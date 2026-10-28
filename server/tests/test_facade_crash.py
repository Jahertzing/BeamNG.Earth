"""2026-10-28: road tiles failed in the game (IndexError in facadekit._cut, every build of the tile, its buildings gone):
a house all of whose windows were left out (blank walls) or kept laid on reached _cut with no opening. And the
safety net: a facade kit failure takes that building's kit off, never the tile."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import buildings as bld  # noqa: E402
import facadekit as fk  # noqa: E402
import test_housefronts as HF  # noqa: E402
import test_recess as TR  # noqa: E402


def houses():
    return [s for s in HF.houses(30) if s.wall and s.windows is not False]


class FacadeCrashTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(fk, k) for k in ("RECESS", "HOUSE_VARIETY", "house_windows", "_clear", "depth")}
        fk.RECESS = fk.HOUSE_VARIETY = True

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(fk, k, v)

    def test_all_windows_on_blank_walls(self):
        """Every wall blank: no opening at all reaches _cut; the native sashes are left out, the walls whole."""
        fk.house_windows = lambda spec, ring, edge, z_eave: []
        for spec in houses()[:10]:
            Q, U, M = TR.walls(spec)                    # (raised IndexError before)
            self.assertFalse((M == 2).any(), "a blank wall's sash left")
            Qb, Ub, Mb = TR.walls(spec, kit=False)
            np.testing.assert_array_equal(np.sort(Q[M == 0].reshape(-1, 12), 0), np.sort(Qb[Mb == 0].reshape(-1, 12), 0))

    def test_every_opening_rejected(self):
        """_clear rejects every window (all by a door / garage / stone band): the native sashes stay as they were."""
        fk._clear = lambda spec, ring, F, base, hq, hs0, hs1, hz0: np.zeros(len(hq), bool)
        def standard(spec, ring, edge, z_eave):         # (no blank walls: every window rejected, none left out)
            a, d, n, L = fk._wall(ring, edge)
            return [fk.Win(float(np.dot(c - a, d)), zf) for c, zf in bld.house_window_slots(spec, a, d, n, L, z_eave, edge)]
        fk.house_windows = standard
        for spec in houses()[:10]:
            for x, y in zip(TR.walls(spec), TR.walls(spec, kit=False)):
                np.testing.assert_array_equal(x, y)

    def test_blank_walls_and_the_rest_rejected(self):
        """Some walls blank, every other window rejected: the blank walls' sashes left out, the others kept."""
        real = self.saved["house_windows"]
        fk.house_windows = lambda spec, ring, edge, z_eave: [] if edge % 2 else real(spec, ring, edge, z_eave)
        fk._clear = lambda spec, ring, F, base, hq, hs0, hs1, hz0: np.zeros(len(hq), bool)
        seen = 0
        for spec in houses()[:15]:
            Q, U, M = TR.walls(spec)
            Qb, Ub, Mb = TR.walls(spec, kit=False)
            self.assertLessEqual(int((M == 2).sum()), int((Mb == 2).sum()))
            seen += int((M == 2).sum()) < int((Mb == 2).sum())
        self.assertGreater(seen, 0)

    def test_cut_with_no_openings(self):
        Q, U, M = TR.walls(houses()[0], kit=False)
        F = fk._Frame(Q.ravel().tolist(), U.ravel().tolist(), M.tolist())
        e = np.zeros(0)
        P2, U2, M2 = fk._cut(F, np.zeros(0, np.int64), e, e, e, e, 0.08, drop=[int(np.flatnonzero(M == 2)[0])])
        self.assertEqual(len(M2), len(M) - 1)
        P2, U2, M2 = fk._cut(F, np.zeros(0, np.int64), e, e, e, e, 0.08)
        np.testing.assert_array_equal(np.asarray(P2), Q.ravel())

    def test_kit_failure_keeps_the_building(self):
        """facadekit raising for a building: build_parts logs one warning and builds it without the kit; every
        building is still in the tile."""
        items = [(s, 0.0, 0.2) for s in houses()[:6]]
        nokit = sum(len(bld.build([it], kit=False)[0]) for it in items)
        ok = sum(len(p[0]) for p in bld.build_parts(items, with_lod=True, max_parts=4))

        def boom(*a, **kw):
            raise IndexError("boolean index did not match (forced)")
        fk.depth = boom
        with self.assertLogs("earthd.buildings", level="WARNING") as cm:
            parts = bld.build_parts(items, with_lod=True, max_parts=4)
        self.assertEqual(len(cm.records), len(items))
        self.assertIn("facade kit failed", cm.output[0])
        self.assertIn("IndexError", cm.output[0])
        self.assertEqual(sum(len(p[0]) for p in parts), nokit)
        self.assertGreater(ok, nokit)
        self.assertTrue(all(p[6] is not None for p in parts), "the lite LOD still there")


if __name__ == "__main__":
    unittest.main()
