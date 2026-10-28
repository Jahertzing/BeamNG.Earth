"""Masonry trim (facadekit.masonry_sills, belts): stone sills and lintels on the plaster sheet's recessed windows, belt
courses at the floor lines of 2-6 storey blocks."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import buildings as bld  # noqa: E402
import facadekit as fk  # noqa: E402
import test_recess as TR  # noqa: E402

LAT, LON = TR.LAT, TR.LON


def blocks(n=4):
    """Plaster sheet blocks of every band (test_recess.sheets: some with a door or a shopfront) and 3-6 storey
    apartment blocks (balconies on a share), all with flat roofs (a cornice: belt courses)."""
    out = TR.sheets(n)
    k = 0
    while sum(1 for s in out if fk.balcony_bays(s, bld.drawn_ring(s))) < 3 and k < 600:
        spec = bld.plan(TR.rect(k * 50.0, 0.0, 30.0, 14.0), {"class": "apartments", "num_floors": 3 + k % 4}, 1, LAT, LON)
        k += 1
        if fk.sheet_band(spec) is None:
            continue
        spec.door = (0, 15.0)
        if fk.balcony_bays(spec, bld.drawn_ring(spec)):
            out.append(spec)
    # a tall-window (band 0) block of low floors: its lintels reach up to the belt courses (squeezed under them)
    k = 0
    while k < 800:
        spec = bld.plan(TR.rect(k * 50.0, 0.0, 24.0, 14.0), {"class": "commercial", "num_floors": 3}, 1, LAT, LON)
        k += 1
        if fk.sheet_band(spec) == (0, 3):
            spec.floor_h, spec.height = 2.8, 3 * 2.8 + 0.6
            out.append(spec)
            break
    for s in out:
        s.roof = "flat"
        if s.shop:
            s.sign = 0
    return out


def local(Q, ring):
    """Per quad: the wall it lies along (facing out from it or flat), and its corners' s along / w out of it."""
    nA = TR.normals(Q)
    res = []
    for j in range(len(Q)):
        best = None
        for i in range(len(ring)):
            w_ = fk._wall(ring, i)
            if w_ is None:
                continue
            a, d, n, L = w_
            rel = Q[j, :, :2] - a
            w = rel @ n
            if w.min() < -0.3 or w.max() > 0.3 or (rel @ d).min() < -0.5 or (rel @ d).max() > L + 0.5:
                continue
            if abs(nA[j, 2]) < 0.99 and nA[j, :2] @ n < 0.999 and abs(nA[j, :2] @ d) < 0.999:
                continue
            best = (i, rel @ d, w)
            break
        res.append(best)
    return res


class MasonryTest(unittest.TestCase):
    SW = {"RECESS": True, "RECESS_SHOP": True, "FINS": True, "HOUSE_VARIETY": True, "MASONRY_TRIM": True}

    def setUp(self):
        self.was = {k: getattr(fk, k) for k in self.SW}
        for k, v in self.SW.items():
            setattr(fk, k, v)

    def tearDown(self):
        for k, v in self.was.items():
            setattr(fk, k, v)

    def parts(self, spec):
        """(glass openings [(wall, s0, s1, z0, z1)], sill tops, lintel undersides, belt fronts [(wall, s0, s1, z0,
        z1)]) of a building's walls."""
        Q, U, M = TR.walls(spec)
        ring = bld.drawn_ring(spec)
        nA = TR.normals(Q)
        glass, sills, lintels, belt, fronts = [], [], [], [], []
        for j, loc in enumerate(local(Q, ring)):
            if loc is None:
                continue
            i, s, w = loc
            z = Q[j, :, 2]
            row = (i, float(s.min()), float(s.max()), float(z.min()), float(z.max()))
            if nA[j, 2] > 0.99 and abs(w.max() - fk.MSILL_OUT) < 1e-6 and abs(w.min()) < 1e-6:
                sills.append(row)
            elif nA[j, 2] < -0.99 and abs(w.max() - fk.LINTEL_OUT) < 1e-6 and abs(w.min()) < 1e-6:
                lintels.append(row)
            elif abs(nA[j, 2]) < 1e-6 and np.allclose(w, -fk.RECESS_SHEET_M) and M[j] == 0:
                glass.append(row)
            elif abs(nA[j, 2]) < 1e-6 and np.allclose(w, fk.BELT_OUT) and abs(np.ptp(z) - fk.BELT_H) < 1e-6:
                belt.append(row)
            elif abs(nA[j, 2]) < 1e-6 and (np.allclose(w, fk.MSILL_OUT) or np.allclose(w, fk.LINTEL_OUT)):
                fronts.append(row)                      # (a sill's or lintel's front: its whole height)
        self.fronts = fronts
        return glass, sills, lintels, belt

    def test_sills_and_lintels_on_the_openings(self):
        """Every recessed sheet window has a sill under it (its top at the opening's bottom, MSILL_EXT wider each side
        unless its quad ends) and most a lintel over it (its underside at the opening's top); none anywhere else."""
        n_open = n_lint = 0
        for spec in blocks():
            glass, sills, lintels, belt = self.parts(spec)
            if not glass:
                continue
            for i, s0, s1, z0, z1 in glass:
                n_open += 1
                under = [x for x in sills if x[0] == i and abs(x[4] - z0) < 1e-6 and x[1] <= s0 + 1e-6
                         and x[2] >= s1 - 1e-6 and x[1] >= s0 - fk.MSILL_EXT - 1e-6 and x[2] <= s1 + fk.MSILL_EXT + 1e-6]
                if has := [x for x in lintels if x[0] == i and abs(x[3] - z1) < 1e-6 and x[1] <= s0 + 1e-6
                           and x[2] >= s1 - 1e-6]:
                    n_lint += 1
                    self.assertTrue(all(x[2] - x[1] <= s1 - s0 + 2 * fk.LINTEL_EXT + 1e-6 for x in has))
                belts_ok = fk.has_belts(spec)
                self.assertTrue(under or belts_ok, f"no sill under the opening at {s0:.2f} {z0:.2f}")
            for x in sills:
                self.assertTrue(any(g[0] == x[0] and abs(g[3] - x[4]) < 1e-6 and g[1] >= x[1] - 1e-6
                                    and g[2] <= x[2] + 1e-6 for g in glass), "a sill under no opening")
        self.assertGreater(n_open, 50)
        self.assertGreater(n_lint, 0.5 * n_open)

    def test_belts(self):
        """Belt courses on the 2-6 storey blocks, at floor lines, under the caps and the cornice, never over the
        shopfront's sign boxes / awning or through a balcony, never touching a sill or lintel."""
        found = 0
        for spec in blocks():
            glass, sills, lintels, belt = self.parts(spec)
            if not fk.has_belts(spec):
                self.assertEqual(belt, [])
                continue
            base = spec.height - spec.floors * spec.floor_h
            lines = [base + f * spec.floor_h for f in range(1, spec.floors)]
            st = fk.street_edge(spec)
            bal = fk.balcony_bays(spec, bld.drawn_ring(spec))
            for i, s0, s1, z0, z1 in belt:
                found += 1
                zc = (z0 + z1) / 2
                self.assertLess(min(abs(zc - z) for z in lines), 1e-6)
                cap = base + (fk.RECESS_STREET_MAX_Z if i == st else fk.RECESS_MAX_Z)
                self.assertLessEqual(z1, cap + 1e-6)
                self.assertLessEqual(z1, spec.height - bld.CORNICE_DOWN_M)
                if spec.shop and spec.sign == i:
                    self.assertGreater(zc, base + spec.floor_h + 1e-6, "a belt over the sign boxes")
                if bal and bal[0] == i:
                    for b0, b1 in bal[1]:
                        self.assertFalse(s0 < b1 and s1 > b0, "a belt through a balcony")
                for x in sills + lintels + self.fronts:
                    self.assertFalse(x[0] == i and s0 < x[2] and s1 > x[1] and z0 < x[4] - 1e-6 and z1 > x[3] + 1e-6,
                                     "a belt on a sill or lintel")
        self.assertGreater(found, 10)

    def test_trim_uvs(self):
        """The trim samples the sheet's light painted sill on brick, the band's plain plaster elsewhere: never a
        window (the night map lights only the openings)."""
        for spec in blocks(2):
            Qa, Ua, Ma = TR.walls(spec)
            fk.MASONRY_TRIM = False
            Qb, Ub, Mb = TR.walls(spec)
            fk.MASONRY_TRIM = True
            if len(Qa) == len(Qb):
                continue
            # the trim's quads: those not in the walls without it
            extra = {tuple(np.round(q.ravel(), 6)) for q in Qa} - {tuple(np.round(q.ravel(), 6)) for q in Qb}
            idx = [j for j, q in enumerate(Qa) if tuple(np.round(q.ravel(), 6)) in extra]
            uv = Ua[idx].reshape(-1, 2)
            if spec.band[0] == fk.BRICK_BAND:
                u0, u1, v0, v1 = fk.STONE_UV
                self.assertTrue(((uv[:, 0] >= u0 - 1e-9) & (uv[:, 0] <= u1 + 1e-9) & (uv[:, 1] >= v0 - 1e-9)
                                 & (uv[:, 1] <= v1 + 1e-9)).all())
            else:
                pu = fk.SHEET_PLAIN_U[spec.band[0]]
                self.assertTrue(((uv[:, 0] >= pu - 1e-9) & (uv[:, 0] <= pu + 8 / 1024 + 1e-9)).all())
            O = fk.SHEET_OPENINGS[spec.band[0]]
            for u, v in uv:
                self.assertFalse(((O[:, 0] < u % 1.0) & (u % 1.0 < O[:, 1]) & (O[:, 2] < v) & (v < O[:, 3])).any())

    def test_not_on_towers_or_houses(self):
        """Glass towers (their fins) and houses get no masonry trim."""
        specs = TR.towers(3) + [s for s in TR.houses(6)]
        for spec in specs:
            a = TR.walls(spec)
            fk.MASONRY_TRIM = False
            b = TR.walls(spec)
            fk.MASONRY_TRIM = True
            for x, y in zip(a, b):
                np.testing.assert_array_equal(x, y)

    def test_off_is_as_before(self):
        """MASONRY_TRIM off: the walls exactly as without these functions."""
        specs = blocks(2)
        fk.MASONRY_TRIM = False
        off = [TR.walls(s) for s in specs]
        saved = fk.masonry_sills, fk.belts
        fk.masonry_sills = lambda *a: None
        fk.belts = lambda spec, ring, z_eave, P, UV, mid: (P, UV, mid)
        try:
            fk.MASONRY_TRIM = True
            none = [TR.walls(s) for s in specs]
        finally:
            fk.masonry_sills, fk.belts = saved
        for x, y in zip(off, none):
            for p, q in zip(x, y):
                np.testing.assert_array_equal(p, q)
        fk.MASONRY_TRIM = True
        self.assertTrue(any(len(TR.walls(s)[0]) > len(o[0]) for s, o in zip(specs, off)))

    def test_deterministic(self):
        for spec in blocks(1):
            for x, y in zip(TR.walls(spec), TR.walls(spec)):
                np.testing.assert_array_equal(x, y)


if __name__ == "__main__":
    unittest.main()
