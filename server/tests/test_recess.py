"""Recessed windows (facadekit.recess): the openings cut out of the walls, the glass set back, the reveals between."""
import importlib.util
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import buildings as bld  # noqa: E402
import facadekit as fk  # noqa: E402

LAT, LON = 40.1, -104.9
HERE = os.path.dirname(os.path.abspath(__file__))


def rect(x, y, w, h):
    return np.array([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], float)


def houses(n=40):
    """Frederick-style detached houses (siding / stucco, pitched roofs), each with its front door, and a driveway and
    garage door where the wall is long enough (doors())."""
    out = []
    for k in range(n):
        w, dp = 8.0 + (k * 37 % 9), 7.5 + (k * 13 % 5)
        spec = bld.plan(rect(k * 30.0, 0.0, w, dp), {"class": "house", "num_floors": 1 + k % 2}, 0, LAT, LON)
        if not spec.wall:
            continue
        road = (np.array([(k * 30.0 - 50.0, -12.0), (k * 30.0 + 80.0, -12.0)]), 4.0)
        bld.doors([(spec, 0.0, 0.0)], [road], 0.0, 0.0, [])
        out.append(spec)
    return out


def sheets(per_band=4):
    """Plaster sheet buildings of each band, some with a front door or a shopfront ground floor."""
    got = {b: [] for b in fk.PLASTER_BANDS}
    k = 0
    while min(len(v) for v in got.values()) < per_band and k < 4000:
        cls = ("apartments", "residential", "commercial", "")[k % 4]
        w = 14.0 + (k * 7 % 23)
        spec = bld.plan(rect(k * 60.0, 0.0, w, 12.0 + k % 7), {"class": cls, "num_floors": 2 + k % 5}, 1, LAT, LON)
        k += 1
        sb = fk.sheet_band(spec)
        if sb is None or len(got[sb]) >= per_band:
            continue
        if len(got[sb]) % 2 == 1:
            spec.roof, spec.door = "flat", (0, w * (0.3 + 0.1 * (k % 4)))
        elif len(got[sb]) == 2:
            spec.shop = True
        got[sb].append(spec)
    return [s for v in got.values() for s in v]


class _Rec:
    """A stand-in for bld.Mesh: keeps _walls' quads."""

    def quads(self, P, UV, mid, mats):
        self.got = (np.asarray(P, np.float64).reshape(-1, 4, 3), np.asarray(UV, np.float64).reshape(-1, 4, 2),
                    np.asarray(list(mid), np.int64))


def walls(spec, kit=True, z0=-1.0):
    """_walls' quads (Q (q, 4, 3), U (q, 4, 2), slot (q,)) for the building."""
    r = _Rec()
    bld._walls(r, spec, bld.drawn_ring(spec), z0, spec.height, bld._Looks(spec.seed), kit=kit)
    return r.got


def frame(Q):
    """Each quad as a wall rect: start a, along d, out n, length L, za, zb."""
    a = Q[:, 0, :2]
    e = Q[:, 1, :2] - a
    L = np.hypot(e[:, 0], e[:, 1])
    d = e / L[:, None]
    return a, d, np.stack([d[:, 1], -d[:, 0]], 1), L, Q[:, 0, 2], Q[:, 2, 2]


def normals(Q):
    nn = np.cross(Q[:, 1] - Q[:, 0], Q[:, 2] - Q[:, 0])
    return nn / np.linalg.norm(nn, axis=1)[:, None]


class RecessTest(unittest.TestCase):
    SWITCHES = {"RECESS": True, "RECESS_SHOP": False, "FINS": False}     # (the windows alone)

    def setUp(self):
        self.was = {k: getattr(fk, k) for k in self.SWITCHES}
        for k, v in self.SWITCHES.items():
            setattr(fk, k, v)

    def tearDown(self):
        for k, v in self.was.items():
            setattr(fk, k, v)

    def covers(self, spec, D, before, after, rng, slot=0):
        """(i): on every wall quad before (of `slot`), the face pieces after (w = 0) and the glass (w = -D) cover it
        exactly: every sample point in exactly one, the areas equal. The number of wall quads cut."""
        Qb, Ub, Mb = before
        Qa, Ua, Ma = after
        cut = 0
        nA = normals(Qa)
        for i in np.flatnonzero(Mb == slot):
            a, d, n, L, za, zb = (x[0] for x in frame(Qb[i:i + 1]))
            rel = Qa[:, :, :2] - a
            s = rel @ d
            w = rel @ n
            z = Qa[:, :, 2]
            inside = (s.min(1) > -1e-6) & (s.max(1) < L + 1e-6) & (z.min(1) > za - 1e-6) & (z.max(1) < zb + 1e-6)
            facing = nA[:, :2] @ n > 0.999
            face = inside & facing & (np.abs(w).max(1) < 1e-6)
            glass = inside & facing & (np.abs(w + D).max(1) < 1e-6)
            if glass.any():
                cut += 1
            sel = np.flatnonzero(face | glass)
            area = sum((s[j].max() - s[j].min()) * (z[j].max() - z[j].min()) for j in sel)
            self.assertAlmostEqual(area, L * (zb - za), places=5)
            ps, pz = rng.uniform(0, L, 400), rng.uniform(za, zb, 400)
            hits = np.zeros(400, int)
            for j in sel:
                hits += (ps > s[j].min()) & (ps < s[j].max()) & (pz > z[j].min()) & (pz < z[j].max())
            self.assertTrue((hits == 1).all(), f"wall {i}: points covered {sorted(set(hits.tolist()))} times")
        return cut

    def reveals_face_in(self, spec, D, after):
        """Every quad off the wall planes is a reveal facing into its opening (a point just in front of it lies in
        the opening's box), and every one sits between the face and the glass."""
        Qa, Ua, Ma = after
        nA = normals(Qa)
        rev = np.abs(nA[:, 2]) > 0.999
        ring = bld.drawn_ring(spec)
        for i in range(len(ring)):
            w_ = fk._wall(ring, i)
            if w_ is None:
                continue
            a, d, n, L = w_
            side = np.abs(nA[:, :2] @ d) > 0.999
            c = Qa.mean(1)
            on = (np.abs((c[:, :2] - a) @ n + D / 2) < 1e-6)
            rev |= side & on
        self.assertTrue(rev.any())
        c = Qa[rev].mean(1)
        p = c + nA[rev] * 0.01
        ok = np.zeros(len(c), bool)
        for i in range(len(ring)):
            w_ = fk._wall(ring, i)
            if w_ is None:
                continue
            a, d, n, L = w_
            w = (p[:, :2] - a) @ n
            ok |= (w < 0) & (w > -D) & (np.abs((c[:, :2] - a) @ n + D / 2) < 1e-6)
        self.assertTrue(ok.all(), "a reveal faces away from its opening")

    def test_houses(self):
        rng = np.random.default_rng(1)
        specs = houses()
        self.assertGreater(len(specs), 25)
        recessed = drives = 0
        for spec in specs:
            before, after = walls(spec, kit=False), walls(spec)
            D = fk.RECESS_HOUSE_M
            recessed += self.covers(spec, D, before, after, rng)
            self.reveals_face_in(spec, D, after)
            # (ii) the glass: the native windows' own UVs and slot, at house_window_slots moved to -RECESS_HOUSE_M
            Qb, Ub, Mb = before
            Qa, Ua, Ma = after
            win_b = np.flatnonzero(Mb == 2)
            key = lambda Q, U, M, j: (tuple(np.round(U[j].ravel(), 9)), int(M[j]))      # noqa: E731
            ring = bld.drawn_ring(spec)
            moved = 0
            for i in range(len(ring)):
                w_ = fk._wall(ring, i)
                if w_ is None:
                    continue
                a, d, n, L = w_
                for c, zf in bld.house_window_slots(spec, a, d, n, L, spec.height, i):
                    want = c - n * (bld.WIN_OUT_M + fk.RECESS_HOUSE_M)
                    cen = Qa[:, :, :2].mean(1)
                    hit = np.flatnonzero((np.hypot(*(cen - want).T) < 1e-6) & (np.abs(Qa[:, :, 2].min(1) - zf) < 1e-6))
                    stay = np.flatnonzero((np.hypot(*(cen - c).T) < 1e-6) & (np.abs(Qa[:, :, 2].min(1) - zf) < 1e-6))
                    self.assertEqual(len(hit) + len(stay), 1, "each window once, recessed or as it was")
                    j = hit[0] if len(hit) else stay[0]
                    self.assertEqual(Ma[j], 2)
                    self.assertIn(key(Qa, Ua, Ma, j), {key(Qb, Ub, Mb, k) for k in win_b})
                    moved += len(hit)
                    # (iii) none of the recessed ones in a garage door or the front door's frame
                    if len(hit) and spec.door and spec.door[0] == i:
                        s = float((c - a) @ d)
                        h = fk.DOOR_W / 2 + fk.FRAME_W * 1.5
                        self.assertFalse(s + bld.WIN_W / 2 > spec.door[1] - h and s - bld.WIN_W / 2 < spec.door[1] + h
                                         and zf < spec.height - spec.floors * spec.floor_h + 3.0)
                    if len(hit) and spec.drive and spec.drive[0] == i:
                        g0, g1 = fk.garage_span(spec, i, L)
                        s = float((c - a) @ d)
                        self.assertFalse(s + bld.WIN_W / 2 > g0 - fk.FRAME_W and s - bld.WIN_W / 2 < g1 + fk.FRAME_W
                                         and zf < spec.height - spec.floors * spec.floor_h + fk.GARAGE_H + 0.2)
            self.assertEqual(int(((Ma == 2) & np.isclose(Ua[:, 0, 0], bld.WIN_UV[0])).sum()), len(win_b))
            drives += bool(spec.drive)
        self.assertGreater(recessed, 40)
        self.assertGreater(drives, 5)

    def sheet_glass_in_openings(self, spec, after):
        """(ii) for a sheet building: every glass quad's UVs are inside one of its band's openings (u modulo the
        sheet), its v range the opening's exactly; it keeps the wall's slot (0)."""
        Qa, Ua, Ma = after
        a = Qa[:, 0, :2]
        nA = normals(Qa)
        ring = bld.drawn_ring(spec)
        glass = np.zeros(len(Qa), bool)
        for i in range(len(ring)):
            w_ = fk._wall(ring, i)
            if w_ is None:
                continue
            ea, d, n, L = w_
            glass |= (nA[:, :2] @ n > 0.999) & (np.abs((Qa[:, :, :2] - ea) @ n + fk.RECESS_SHEET_M).max(1) < 1e-6)
        O = fk.SHEET_OPENINGS[spec.band[0]]
        for j in np.flatnonzero(glass):
            self.assertEqual(Ma[j], 0)
            u0, u1 = Ua[j, 0, 0], Ua[j, 1, 0]
            t = np.floor(u0 + 1e-9)
            v0, v1 = Ua[j, 0, 1], Ua[j, 3, 1]
            fits = ((O[:, 0] + t <= u0 + 1e-9) & (O[:, 1] + t >= u1 - 1e-9) & np.isclose(O[:, 2], v0)
                    & np.isclose(O[:, 3], v1))
            self.assertTrue(fits.any(), f"glass {j} u {u0:.4f}..{u1:.4f} v {v0:.4f}..{v1:.4f} in no opening")
        del a
        return int(glass.sum())

    def test_sheet_buildings(self):
        rng = np.random.default_rng(2)
        specs = sheets()
        self.assertEqual({fk.sheet_band(s) for s in specs}, set(fk.PLASTER_BANDS))
        for spec in specs:
            before, after = walls(spec, kit=False), walls(spec)
            cut = self.covers(spec, fk.RECESS_SHEET_M, before, after, rng)
            self.assertGreater(cut, 0, f"band {spec.band}")
            self.reveals_face_in(spec, fk.RECESS_SHEET_M, after)
            self.assertGreater(self.sheet_glass_in_openings(spec, after), 0)
            # the shopfront (slot 1) and the door's plain strips as they were
            Qb, Ub, Mb = before
            Qa, Ua, Ma = after
            for keep in (Mb == 1, (Mb == 0) & (Ub[:, 1, 0] - Ub[:, 0, 0] < 0.5 / spec.band[1])):
                for j in np.flatnonzero(keep):
                    self.assertTrue((np.abs(Qa - Qb[j]).max((1, 2)) < 1e-9).any())
            # (iii) no opening in the front door's frame
            if spec.door:
                edge, along = spec.door
                a, d, n, L = fk._wall(bld.drawn_ring(spec), edge)
                h = fk.DOOR_W / 2 + fk.FRAME_W * 1.5
                g = np.flatnonzero(np.abs((Qa[:, :, :2] - a) @ n + fk.RECESS_SHEET_M).max(1) < 1e-6)
                s = (Qa[g, :, :2] - a) @ d
                z = Qa[g, :, 2]
                base = spec.height - spec.floors * spec.floor_h
                bad = (s.max(1) > along - h) & (s.min(1) < along + h) & (z.min(1) < base + fk.DOOR_H + 0.2)
                self.assertFalse(bad.any())

    def test_whole_bay_shift(self):
        """(v) the night lights' _night_shift moves each floor's u by whole bays: the openings follow the UVs."""
        rng = np.random.default_rng(3)
        for spec in sheets(2):
            if len(spec.band) > 2:
                continue
            bpt = spec.band[1]
            Q, U, M = walls(spec, kit=False)
            P, UV = Q.ravel().tolist(), U.copy()
            f = np.floor((Q[:, :, 2].min(1) - (spec.height - spec.floors * spec.floor_h)) / spec.floor_h + 1e-3)
            sel = (M == 0) & (f >= 0)
            UV[sel, :, 0] += ((f[sel] * 7 + 3) % bpt / bpt)[:, None]
            out = fk.recess(spec, bld.drawn_ring(spec), spec.height, P, UV.ravel().tolist(), M.tolist())
            after = (np.asarray(out[0]).reshape(-1, 4, 3), np.asarray(out[1]).reshape(-1, 4, 2), np.asarray(out[2]))
            self.assertGreater(self.covers(spec, fk.RECESS_SHEET_M, (Q, UV, M), after, rng), 0)
            self.assertGreater(self.sheet_glass_in_openings(spec, after), 0)
            self.reveals_face_in(spec, fk.RECESS_SHEET_M, after)

    def test_off_is_as_before(self):
        """(iv) RECESS off: the walls exactly the kernel's (as with kit=False); lite never recessed."""
        for spec in houses(10) + sheets(2):
            items = [(spec, 0.0, 0.5)]
            fk.RECESS = fk.RECESS_SHOP = fk.FINS = False
            off = bld.build(items)
            for x, y in zip(walls(spec), walls(spec, kit=False)):
                np.testing.assert_array_equal(x, y)
            lite_off = bld.build(items, lite=True)
            fk.RECESS = True
            on = bld.build(items)
            lite_on = bld.build(items, lite=True)
            for x, y in zip(lite_off, lite_on):
                if isinstance(x, np.ndarray):
                    np.testing.assert_array_equal(x, y)
            self.assertGreater(len(on[0]), len(off[0]))
            nokit = bld.build(items, kit=False)
            self.assertLessEqual(len(nokit[0]), len(off[0]))

    def test_tall_sheet_capped(self):
        """Sheet windows recessed up to RECESS_STREET_MAX_Z on the street wall (the front door's), RECESS_MAX_Z on the
        others; without a street wall, RECESS_MAX_Z everywhere."""
        self.assertGreater(fk.RECESS_STREET_MAX_Z, fk.RECESS_MAX_Z)
        found = 0
        for k in range(300):
            spec = bld.plan(rect(k * 50.0, 0.0, 30.0, 20.0), {"class": "apartments", "num_floors": 14}, 1, LAT, LON)
            if fk.sheet_band(spec) is None:
                continue
            base = spec.height - spec.floors * spec.floor_h
            ring = bld.drawn_ring(spec)
            for door in (None, (0, 6.0)):
                spec.door = door
                if door:
                    spec.roof = "flat"
                Qa, Ua, Ma = walls(spec)
                nA = normals(Qa)
                rev = np.abs(nA[:, 2]) > 0.999                          # sills and heads
                self.assertTrue(rev.any())
                top = {}
                for i in range(len(ring)):
                    a, d, n, L = fk._wall(ring, i)
                    on = rev & (np.abs((Qa[:, :, :2].mean(1) - a) @ n + fk.RECESS_SHEET_M / 2) < 1e-6) \
                        & (((Qa[:, :, :2].mean(1) - a) @ d) > 0) & (((Qa[:, :, :2].mean(1) - a) @ d) < L)
                    top[i] = float(Qa[on][:, :, 2].max()) - base if on.any() else 0.0
                side = max(v for i, v in top.items() if not door or i != door[0])
                self.assertLessEqual(side, fk.RECESS_MAX_Z + 1e-6)
                if door:
                    self.assertLessEqual(top[0], fk.RECESS_STREET_MAX_Z + 1e-6)
                    self.assertGreater(top[0], fk.RECESS_MAX_Z + 3.0, "the street wall recessed higher")
                    self.assertGreater(top[0], side + 3.0)
            found += 1
            if found >= 3:
                break
        self.assertEqual(found, 3)

    def test_deterministic(self):
        for spec in houses(5) + sheets(1):
            a, b = walls(spec), walls(spec)
            for x, y in zip(a, b):
                np.testing.assert_array_equal(x, y)

    def test_openings_table(self):
        """SHEET_OPENINGS are the night map's lit rects (tools/build_night_windows.py OPENINGS, where that tool is
        present: the night lights' branch), each inside its band, the plain columns clear of them."""
        for k, band in enumerate(fk.SHEET_OPENINGS_PX):
            for c in band:
                if c is None:
                    continue
                x0, y0, x1, y1 = c
                self.assertTrue(k * 256 <= y0 < y1 <= (k + 1) * 256 and 0 <= x0 < x1 <= 1024)
            pu = fk.SHEET_PLAIN_U[k] * 1024
            du = fk.RECESS_SHEET_M / bld.TILE_PX_M * 1024
            for c in band:
                if c is not None:
                    self.assertTrue(pu + du + 8 < c[0] or pu - du - 8 > c[2], f"band {k}'s plain column in a window")
        for k, (bk, bpt) in enumerate(fk.PLASTER_BANDS):
            v0, v1 = bld._band_v((bk, bpt), bk)
            O = fk.SHEET_OPENINGS[k]
            self.assertTrue(((O[:, 2] > v0) & (O[:, 3] < v1)).all())
        tool = os.path.join(HERE, "..", "..", "tools", "build_night_windows.py")
        if not os.path.exists(tool):
            self.skipTest("tools/build_night_windows.py not on this branch")
        spec = importlib.util.spec_from_file_location("build_night_windows", tool)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        night = tuple(tuple(None if c is None else tuple(c[0]) for c in band) for band in mod.OPENINGS)
        self.assertEqual(night, fk.SHEET_OPENINGS_PX)

def shop_specs(n=6):
    """Shop ground floors (the metal sheet's shop band) on plaster and glass buildings, their sign on the first wall;
    and big boxes (a storefront band laid over a plain wall)."""
    out, k = [], 0
    while len(out) < n and k < 400:
        cls = ("commercial", "retail", "office", "retail")[k % 4]
        nf = (2, 3, 25, 1)[k % 4]
        spec = bld.plan(rect(k * 60.0, 0.0, 18.0 + k % 9, 14.0), {"class": cls, "num_floors": nf}, 1, LAT, LON)
        k += 1
        if spec.wall or spec.plain or not spec.band:
            continue
        spec.roof, spec.shop, spec.sign, spec.door = "flat", True, 0, None
        out.append(spec)
    boxes, k = [], 0
    while len(boxes) < max(2, n // 3) and k < 400:
        spec = bld.plan(rect(k * 90.0, 0.0, 60.0 + k % 7, 40.0), {"class": "retail"}, 0, LAT, LON)
        k += 1
        if spec.windows is False and spec.wall and spec.roof == "flat":
            spec.front = spec.sign = 0
            boxes.append(spec)
    return out, boxes


def towers(n=6):
    """Glass curtain-wall towers (the metal sheet's office bands), a front door on the first wall of every other."""
    out, k = [], 0
    while len(out) < n and k < 400:
        spec = bld.plan(rect(k * 60.0, 0.0, 24.0 + k % 11, 20.0), {"class": "office", "num_floors": 22}, 1, LAT, LON)
        k += 1
        if bld.LOOKS.get(spec.facade, (None,))[0] == bld.OFFICE and tuple(spec.band[:2]) in fk.OFFICE_BANDS:
            spec.door = (0, 8.0) if len(out) % 2 == 0 else None
            out.append(spec)
    return out


def on_plane(Q, ring, w):
    """Which quads lie (all corners) w out from one of the ring's walls, facing out from it: (mask, the wall's index
    per quad, s of the corners along it)."""
    nA = normals(Q)
    hit = np.zeros(len(Q), bool)
    edge = np.full(len(Q), -1)
    S = np.zeros(Q.shape[:2])
    for i in range(len(ring)):
        w_ = fk._wall(ring, i)
        if w_ is None:
            continue
        a, d, n, L = w_
        rel = Q[:, :, :2] - a
        m = (nA[:, :2] @ n > 0.999) & (np.abs(rel @ n - w).max(1) < 1e-6) & ~hit
        hit |= m
        edge[m] = i
        S[m] = (rel @ d)[m]
    return hit, edge, S


class ShopFinTest(RecessTest):
    """Shopfronts set back and curtain walls' fins (facadekit.shopfronts, fins)."""
    SWITCHES = {"RECESS": False, "RECESS_SHOP": True, "FINS": True}

    # (the window tests are RecessTest's own)
    test_houses = test_sheet_buildings = test_whole_bay_shift = test_tall_sheet_capped = None
    test_off_is_as_before = test_openings_table = None

    def test_shopfronts(self):
        """The shop glass set back RECESS_SHOP_M: watertight like the windows, its UVs inside the sheet's measured
        glazing (u modulo the sheet; under a sign or awning its top lower), its slot (1) kept, the reveals facing in; a
        big box's storefront cut into its wall first."""
        fk.FINS = False                                     # (a tower's fin caps are horizontal too)
        rng = np.random.default_rng(4)
        shops, boxes = shop_specs(12)
        self.assertTrue(shops and boxes)
        D = fk.RECESS_SHOP_M
        clipped = 0
        for spec in shops + boxes:
            before, after = walls(spec, kit=False), walls(spec)
            if spec in boxes:
                self.assertGreater(self.covers(spec, D, before, after, rng, slot=0), 0, "the storefront's wall")
            else:
                self.assertGreater(self.covers(spec, D, before, after, rng, slot=1), 0, "the shop floor")
                self.covers(spec, D, before, after, rng, slot=0)       # (the floors above whole)
            self.reveals_face_in(spec, D, after)
            Qa, Ua, Ma = after
            ring = bld.drawn_ring(spec)
            glass, edge, _ = on_plane(Qa, ring, -D)
            self.assertTrue(glass.any())
            O = fk.SHOP_OPENINGS
            for j in np.flatnonzero(glass):
                self.assertEqual(Ma[j], 1)
                u0, u1, v0, v1 = Ua[j, 0, 0], Ua[j, 1, 0], Ua[j, 0, 1], Ua[j, 3, 1]
                t = np.floor(u0 + 1e-9)
                fits = ((O[:, 0] + t <= u0 + 1e-9) & (O[:, 1] + t >= u1 - 1e-9) & np.isclose(O[:, 2], v0)
                        & (O[:, 3] >= v1 - 1e-9) & (v1 > v0))
                self.assertTrue(fits.any(), f"shop glass u {u0:.4f}..{u1:.4f} v {v0:.4f}..{v1:.4f}")
                if spec in shops and edge[j] == spec.sign:
                    base = spec.height - spec.floors * spec.floor_h
                    z_top = spec.height - (spec.floors - 1) * spec.floor_h if spec.floors > 1 else spec.height
                    lim = z_top - 0.15 - bld.SIGN_H
                    if fk.has_awning(spec):
                        lim = min(lim, base + bld.GROUND_FLOOR_SHOP_M - bld.SIGN_H - 0.25)
                    self.assertLessEqual(float(Qa[j, :, 2].max()), lim + 1e-6, "glass under the sign / awning")
                    clipped += bool(v1 < O[:, 3].min() - 1e-6)
        self.assertGreater(clipped, 0, "no shop glass reached its sign / awning")

    def test_fins_on_the_mullions(self):
        """Fins on the sheet's bay edges (u x bays a whole number), ledges at floor lines, up to RECESS_STREET_MAX_Z on
        the street wall and RECESS_MAX_Z on the others; all of them facing out, up or down."""
        fk.RECESS_SHOP = False                              # (the fins alone: a tower's shop floor stays flat)
        specs = towers()
        self.assertGreaterEqual(len(specs), 4)
        street_higher = 0
        for spec in specs:
            bpt = spec.band[1]
            Qb, Ub, Mb = walls(spec, kit=False)
            Qa, Ua, Ma = walls(spec)
            ring = bld.drawn_ring(spec)
            base = spec.height - spec.floors * spec.floor_h
            fins, edge, S = on_plane(Qa, ring, fk.FIN_D)
            fins &= np.isclose(S.max(1) - S.min(1), fk.FIN_W)
            self.assertTrue(fins.any())
            hb, eb, Sb = on_plane(Qb, ring, 0.0)
            tops = {}
            for j in np.flatnonzero(fins):
                s = S[j].mean()
                z0 = float(Qa[j, :, 2].min())
                own = np.flatnonzero(hb & (eb == edge[j]) & (Mb == 0) & (np.abs(Qb[:, 0, 2] - z0) < 1e-6))
                self.assertTrue(len(own))
                k = own[0]
                u = Ub[k, 0, 0] + (s - Sb[k, 0]) / (Sb[k, 1] - Sb[k, 0]) * (Ub[k, 1, 0] - Ub[k, 0, 0])
                self.assertAlmostEqual(u * bpt, round(u * bpt), places=6, msg="a fin off the mullion line")
                tops[edge[j]] = max(tops.get(edge[j], -1e9), float(Qa[j, :, 2].max()) - base)
            st = fk.street_edge(spec)
            for e, top in tops.items():
                self.assertLessEqual(top, (fk.RECESS_STREET_MAX_Z if e == st else fk.RECESS_MAX_Z) + 1e-6)
            if st is not None and st in tops and max(v for e, v in tops.items() if e != st) + 3.0 < tops[st]:
                street_higher += 1
            ledges, _, _ = on_plane(Qa, ring, fk.LEDGE_D)
            ledges &= np.isclose(Qa[:, 2, 2] - Qa[:, 0, 2], fk.LEDGE_H)
            self.assertTrue(ledges.any())
            lines = np.unique(Qb[Mb == 0][:, 0, 2])
            for j in np.flatnonzero(ledges):
                self.assertLess(np.abs(lines - Qa[j, :, 2].mean()).min(), 1e-6, "a ledge off a floor line")
            extra = Qa[len(Qb):]                        # (fins() appends: the kernel's quads first, untouched)
            np.testing.assert_array_equal(Qa[:len(Qb)], Qb)
            nA = normals(extra)
            c = Qb[:, :, :2].reshape(-1, 2).mean(0)
            front = np.abs(nA[:, 2]) < 0.5
            mid = extra[:, :, :2].mean(1)
            facing = ((mid - c) * nA[:, :2]).sum(1)
            fronts = np.abs(on_plane(extra, ring, fk.FIN_D)[0] | on_plane(extra, ring, fk.LEDGE_D)[0])
            self.assertTrue((facing[front & fronts] > 0).all(), "a fin's or ledge's front faces in")
        self.assertGreater(street_higher, 0, "no street wall had its fins higher")

    def test_switches_off(self):
        """RECESS_SHOP and FINS off: the shop floors and curtain walls exactly the kernel's."""
        shops, boxes = shop_specs(3)
        for spec in shops + boxes + towers(3):
            fk.RECESS_SHOP = fk.FINS = False
            for x, y in zip(walls(spec), walls(spec, kit=False)):
                np.testing.assert_array_equal(x, y)
            fk.RECESS_SHOP = fk.FINS = True
            self.assertGreater(len(walls(spec)[0]), len(walls(spec, kit=False)[0]))

    def test_deterministic(self):
        shops, boxes = shop_specs(2)
        for spec in shops + boxes + towers(2):
            for x, y in zip(walls(spec), walls(spec)):
                np.testing.assert_array_equal(x, y)


if __name__ == "__main__":
    unittest.main()
