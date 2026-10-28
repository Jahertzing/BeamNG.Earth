"""House fronts (facadekit.eaves, gutters, veneer): the pitched roofs' soffit and fascia, gutters and downspouts, stone
veneer fronts."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import buildings as bld  # noqa: E402
import facadekit as fk  # noqa: E402

LAT, LON = 40.1, -104.9           # Frederick, Colorado (us_west)


def rect(x, y, w, h):
    return np.array([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], float)


def ell(x, y):
    return np.array([(x, y), (x + 14.0, y), (x + 14.0, y + 6.0), (x + 7.0, y + 6.0), (x + 7.0, y + 12.0),
                     (x, y + 12.0)], float)


def houses(n=60, lat=LAT, lon=LON, shape=rect):
    """Frederick-style houses with their front door, driveway and garage door (doors())."""
    out = []
    for k in range(n):
        ring = rect(k * 30.0, 0.0, 8.0 + (k * 37 % 9), 7.5 + (k * 13 % 5)) if shape is rect else shape(k * 30.0, 0.0)
        spec = bld.plan(ring, {"class": "house", "num_floors": 1 + k % 2}, 0, lat, lon)
        road = (np.array([(k * 30.0 - 50.0, -12.0), (k * 30.0 + 80.0, -12.0)]), 4.0)
        bld.doors([(spec, 0.0, 0.0)], [road], 0.0, 0.0, [])
        out.append(spec)
    return out


class _Rec:
    """A stand-in for bld.Mesh keeping each quad (4 corners) and its material token."""

    def __init__(self):
        self.q = []

    def quad(self, a, b, c, d, uva, uvb, uvc, uvd, mat):
        self.q.append((np.array([a, b, c, d], float), mat))


def normal(Q):
    nn = np.cross(Q[1] - Q[0], Q[2] - Q[0])
    return nn / np.linalg.norm(nn)


def in_convex(p, poly):
    """p (2,) inside the convex polygon poly (k, 2) (either winding)."""
    e = np.roll(poly, -1, 0) - poly
    c = e[:, 0] * (p[1] - poly[:, 1]) - e[:, 1] * (p[0] - poly[:, 0])
    return (c > 1e-9).all() or (c < -1e-9).all()


class HouseFrontTest(unittest.TestCase):
    SW = ("EAVES", "GUTTERS", "VENEER")

    def setUp(self):
        self.was = {k: getattr(fk, k) for k in self.SW}
        for k in self.SW:
            setattr(fk, k, True)

    def tearDown(self):
        for k, v in self.was.items():
            setattr(fk, k, v)

    def test_overhang_closed_from_below(self):
        """Every point of a pitched roof's overhang (between the walls and the eave edge, in plan) has exactly one
        soffit over it facing down, FASCIA_H under the roof; a fascia faces out along every eave edge."""
        rng = np.random.default_rng(5)
        kinds = set()
        for spec in houses(20) + houses(6, shape=ell):
            er = fk.eave_runs(spec, bld.drawn_ring(spec), spec.height)
            if er is None:
                continue
            kinds.add((spec.roof, bld.roof_skeleton(spec) is not None))
            m = _Rec()
            fk.eaves(m, spec, bld.drawn_ring(spec), spec.height, bld._Looks(spec.seed), er)
            soffits = [Q for Q, _ in m.q if normal(Q)[2] < -0.5]
            fascias = [Q for Q, _ in m.q if abs(normal(Q)[2]) < 1e-6]
            self.assertTrue(soffits and fascias)
            ze, runs, rakes, tp = er
            n_edges = sum(len(e) for e, _ in runs)
            self.assertEqual(len(fascias), n_edges + sum(len(f) for _, f in rakes))
            ring = bld.drawn_ring(spec)
            if bld.roof_skeleton(spec) is None:
                c, u, hl, hw = bld.oriented_box(ring)
                v = np.array([-u[1], u[0]])
                for _ in range(300):
                    s, t = rng.uniform(-hl - bld.EAVE_M, hl + bld.EAVE_M), rng.uniform(-hw - bld.EAVE_M, hw + bld.EAVE_M)
                    if abs(s) < hl - 1e-6 and abs(t) < hw - 1e-6:
                        continue                                    # (under the roof, inside the walls)
                    p = c + u * s + v * t
                    hits = sum(in_convex(p, Q[:, :2]) for Q in soffits)
                    self.assertEqual(hits, 1, f"{spec.roof}: ({s:.2f}, {t:.2f}) under {hits} soffits")
            else:                                                   # the skeleton's: the ring between walls and eaves
                eav = np.asarray(bld.roof_skeleton(spec)[0])
                lo, hi = eav.min(0), eav.max(0)
                for _ in range(300):
                    p = rng.uniform(lo, hi)
                    inside_e = bld._inside(eav, p)
                    inside_w = bld._inside(ring, p)
                    if not inside_e or inside_w:
                        continue
                    self.assertEqual(sum(in_convex(p, Q[:, :2]) for Q in soffits), 1)
        self.assertTrue({("gabled", False), ("hipped", False)} <= kinds, kinds)
        self.assertTrue(any(sk for _, sk in kinds), "no skeleton roof among the L-shaped houses")

    def test_veneer_clear_of_windows_doors_garages(self):
        """The stone keeps clear of the front windows (their trims and shutters), the door's frame and the garage
        door's frame; never on a house with a wainscot; about VENEER_SHARE of the region's houses."""
        n = got = piers = 0
        for spec in houses(120):
            if not (spec.wall and spec.windows is not False and spec.roof in ("gabled", "hipped")):
                continue
            n += 1
            ring = bld.drawn_ring(spec)
            m = _Rec()
            pieces = fk.veneer(m, spec, ring, 0.0, spec.height, bld._Looks(spec.seed))
            self.assertEqual(bool(pieces), fk.has_veneer(spec) and bool(pieces))
            if not pieces:
                continue
            got += 1
            self.assertFalse(fk.has_wainscot(spec))
            f = fk.front_edge(spec)
            a, d, out, L = fk._wall(ring, f)
            base = spec.height - spec.floors * spec.floor_h
            shut = bld._hash01(spec.seed, "shutters") < fk.SHUTTER_SHARE
            boxes = []                                              # (s0, s1, z0, z1) the stone must not touch
            for c, zf in bld.house_window_slots(spec, a, d, out, L, spec.height, f):
                s = float((c - a) @ d)
                h = bld.WIN_W / 2 + fk.TRIM_W + (bld.WIN_W / 2 if shut else 0.0)
                boxes.append((s - h, s + h, zf - fk.SILL_H, zf + bld.WIN_H + fk.HEAD_H))
            if spec.door and spec.door[0] == f:
                h = fk.DOOR_W / 2 + fk.FRAME_W * 1.5
                boxes.append((spec.door[1] - h, spec.door[1] + h, -9.0, base + 3.0))
            if spec.drive and spec.drive[0] == f:
                g0, g1 = fk.garage_span(spec, f, L)
                boxes.append((g0 - fk.FRAME_W, g1 + fk.FRAME_W, -9.0, base + fk.GARAGE_H + 0.18))
                piers += sum(1 for z0, z1, s0, s1 in pieces if z1 > base + 1.5)
            for z0, z1, s0, s1 in pieces:
                self.assertTrue(0.0 <= s0 < s1 <= L + 1e-9)
                for b0, b1, c0, c1 in boxes:
                    self.assertFalse(s0 < b1 and s1 > b0 and z0 < c1 and z1 > c0,
                                     f"stone {s0:.2f}..{s1:.2f} x {z0:.2f}..{z1:.2f} on {b0:.2f}..{b1:.2f}")
        self.assertGreater(n, 60)
        self.assertTrue(0.15 * n < got < 0.6 * n, f"{got} of {n}")
        self.assertGreater(piers, 0, "no garage pier")
        eu = [s for s in houses(40, 48.0, 11.0) if fk.has_veneer(s)]    # (Bavaria: none)
        self.assertEqual(eu, [])

    def test_downspouts_clear(self):
        """The front corners' downspouts keep clear of the door's and garage door's frames and the porch; they stand
        on the wall, in front of nothing else of the kit."""
        checked = 0
        for spec in houses(60):
            ring = bld.drawn_ring(spec)
            er = fk.eave_runs(spec, ring, spec.height)
            f = fk.front_edge(spec)
            if er is None or f is None:
                continue
            m = _Rec()
            fk.gutters(m, spec, ring, 0.0, spec.height, bld._Looks(spec.seed), er)
            a, d, out, L = fk._wall(ring, f)
            tall = [Q for Q, _ in m.q if np.ptp(Q[:, 2]) > 1.0]      # the downspouts' runs down the wall
            if not tall:
                continue
            checked += 1
            spans = []
            if spec.door and spec.door[0] == f:
                h = fk.DOOR_W / 2 + fk.FRAME_W * 1.5
                spans.append((spec.door[1] - h, spec.door[1] + h))
            if spec.drive and spec.drive[0] == f:
                g0, g1 = fk.garage_span(spec, f, L)
                spans.append((g0 - fk.FRAME_W, g1 + fk.FRAME_W))
            ps = fk.porch_span(spec, ring)
            if ps and spec.door[0] == f:
                spans.append(ps)
            for Q in tall:
                s = (Q[:, :2] - a) @ d
                w = (Q[:, :2] - a) @ out
                self.assertTrue((w > -1e-6).all() and (w < fk.DOWNSPOUT_W + 1e-6).all())
                for s0, s1 in spans:
                    self.assertFalse(s.min() < s1 and s.max() > s0, "a downspout on a door, garage door or porch")
        self.assertGreater(checked, 20)

    def test_downspout_offset_under_the_eave(self):
        """The downspout's offset runs from the wall up into the gutter's bottom, under the soffit and below the
        fascia's bottom edge all the way (nothing of it through them)."""
        checked = 0
        for spec in houses(40):
            ring = bld.drawn_ring(spec)
            er = fk.eave_runs(spec, ring, spec.height)
            f = fk.front_edge(spec)
            if er is None or f is None:
                continue
            ze, runs, rakes, tp = er
            m = _Rec()
            fk.gutters(m, spec, ring, 0.0, spec.height, bld._Looks(spec.seed), er)
            a, d, out, L = fk._wall(ring, f)
            zg0 = ze - fk.GUTTER_H
            pts = [(float((q[:2] - a) @ out), float(q[2])) for Q, _ in m.q for q in Q
                   if abs(float((q[:2] - a) @ d) - fk.DOWNSPOUT_IN) < 0.2 and 0.01 < (q[:2] - a) @ out < 0.6
                   and q[2] > spec.height - 2.0]
            if not pts:
                continue
            checked += 1
            for w, z in pts:
                if w <= bld.EAVE_M:
                    self.assertLessEqual(z, spec.height - fk.FASCIA_H - w * tp + 1e-6, "through the soffit")
                    self.assertLessEqual(z, ze - fk.FASCIA_H + (bld.EAVE_M - w) * 2.0 + 1e-6)
                else:
                    self.assertLessEqual(z, zg0 + 1e-6, "above the gutter's bottom")
            self.assertAlmostEqual(max(z for w, z in pts if w > bld.EAVE_M), zg0, places=6)
            # (its top between its corners: under the fascia's bottom edge where it passes the eave line)
            top = {}
            for w, z in pts:
                top[round(w, 6)] = max(top.get(round(w, 6), -1e9), z)
            ws = sorted(top)
            self.assertLessEqual(float(np.interp(bld.EAVE_M, ws, [top[w] for w in ws])), ze - fk.FASCIA_H + 1e-6,
                                 "the offset through the fascia")
        self.assertGreater(checked, 10)

    def test_gutters_follow_the_eaves(self):
        """A hipped roof's gutter goes round (one front a side, no end caps); a gabled box roof's two run along the
        eaves with their ends capped; all fronts face out, under the eave edge."""
        seen = set()
        for spec in houses(30):
            ring = bld.drawn_ring(spec)
            er = fk.eave_runs(spec, ring, spec.height)
            if er is None or bld.roof_skeleton(spec) is not None:
                continue
            ze = er[0]
            fk_d = fk.front_edge(spec)
            spec.door = spec.drive = None                           # (no downspouts: the gutters alone)
            spec.porch = None
            m = _Rec()
            fk.GUTTERS = True
            orig = fk.front_edge
            fk.front_edge = lambda s: None
            try:
                fk.gutters(m, spec, ring, 0.0, spec.height, bld._Looks(spec.seed), er)
            finally:
                fk.front_edge = orig
            del fk_d
            side = [Q for Q, _ in m.q if abs(normal(Q)[2]) < 1e-6]
            c = np.append(ring.mean(0), 0.0)
            fronts = [Q for Q in side if np.ptp(((Q - c)[:, :2] * normal(Q)[:2]).sum(1)) < 1e-6
                      and ((Q.mean(0) - c)[:2] @ normal(Q)[:2]) > 0]
            self.assertTrue(all(Q[:, 2].max() <= ze + 1e-6 for Q in side))
            caps = len(side) - len(fronts)
            if spec.roof == "hipped":
                self.assertEqual((len(fronts), caps), (4, 0))
            else:
                self.assertEqual(len(fronts) + caps, 6)
            seen.add(spec.roof)
        self.assertEqual(seen, {"gabled", "hipped"})

    def test_off_and_kit_budget(self):
        """EAVES, GUTTERS and VENEER off: the kit exactly as without these functions; on: more vertices, but none in
        the lite mesh."""
        specs = houses(12)
        on = [bld.build([(s, 0.0, 0.3)]) for s in specs]
        for k in self.SW:
            setattr(fk, k, False)
        off = [bld.build([(s, 0.0, 0.3)]) for s in specs]
        saved = fk.eaves, fk.gutters, fk.veneer
        fk.eaves = fk.gutters = fk.veneer = lambda *a, **kw: []
        try:
            none = [bld.build([(s, 0.0, 0.3)]) for s in specs]
        finally:
            fk.eaves, fk.gutters, fk.veneer = saved
        for x, y in zip(off, none):
            for p, q in zip(x, y):
                if isinstance(p, np.ndarray):
                    np.testing.assert_array_equal(p, q)
        self.assertGreater(sum(len(x[0]) for x in on), sum(len(x[0]) for x in off))
        for k in self.SW:
            setattr(fk, k, True)
        for s in specs:
            self.assertEqual(len(bld.build([(s, 0.0, 0.3)], lite=True)[0]),
                             len(bld.build([(s, 0.0, 0.3)], lite=True, kit=False)[0]))

    def test_deterministic(self):
        for s in houses(6) + houses(3, shape=ell):
            a, b = bld.build([(s, 0.0, 0.2)]), bld.build([(s, 0.0, 0.2)])
            for p, q in zip(a, b):
                if isinstance(p, np.ndarray):
                    np.testing.assert_array_equal(p, q)


if __name__ == "__main__":
    unittest.main()
