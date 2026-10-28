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
    def setUp(self):
        self.was = fk.RECESS
        fk.RECESS = True

    def tearDown(self):
        fk.RECESS = self.was

    def covers(self, spec, D, before, after, rng):
        """(i): on every wall quad before, the face pieces after (w = 0) and the glass (w = -D) cover it exactly: every
        sample point in exactly one, the areas equal. The number of wall quads cut."""
        Qb, Ub, Mb = before
        Qa, Ua, Ma = after
        cut = 0
        nA = normals(Qa)
        for i in np.flatnonzero(Mb == 0):
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
            fk.RECESS = False
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


if __name__ == "__main__":
    unittest.main()
