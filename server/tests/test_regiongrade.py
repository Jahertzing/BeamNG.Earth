"""regiongrade: every point graded by its fixed region's Grader, whichever anchor asks; blended across region edges
(no step); a real Grader's heights through it; open tunnel sections once; cell lookahead routed to the regions it
touches; the kept regions bounded."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import geodesy  # noqa: E402
import grading  # noqa: E402
import regiongrade  # noqa: E402
from test_grading import FakeMD  # noqa: E402
from test_roads import features, FRAME, LAT, LON  # noqa: E402


class FakeGrader:
    """apply: base + an offset of its own region (its frame's centre), so the tests see which region graded a point."""

    def __init__(self, frame, log):
        self.frame, self.log = frame, log
        self.offset = round(frame.lat0 * 10) * 100.0 + round(frame.lon0 * 10)
        self.planned_keys = set()
        log.append(self)

    def apply(self, lat, lon, base, lift=0.0, roads_only=False, planned_only=False):
        return np.asarray(base, np.float64) + self.offset

    def open_tunnels(self, lat, lon):
        return [(np.array([1.0, 2.0]), np.array([3.0, 4.0]), "spec")]

    def planned(self, key):
        return key in self.planned_keys

    def warm(self, key):
        self.planned_keys.add(key)


class RegionGraderTest(unittest.TestCase):
    def setUp(self):
        self.made = []
        self.rg = regiongrade.RegionGrader(lambda f: FakeGrader(f, self.made))

    def test_a_point_is_graded_by_its_region_alone(self):
        i, j = regiongrade.region_of(LAT, LON)
        la0, lo0 = regiongrade.region_center(i, j)
        out = self.rg.apply(np.array([la0, la0 + 0.03]), np.array([lo0, lo0 - 0.02]), np.zeros(2))
        self.assertEqual(len(self.made), 1, "one region, one Grader")
        np.testing.assert_allclose(out, self.made[0].offset)
        self.assertAlmostEqual(self.made[0].frame.lat0, la0)

    def test_the_same_grader_for_any_anchor(self):
        # (the drop-in for the Grader per anchor: the region's answer does not depend on who asks)
        a = self.rg.apply(np.array([LAT]), np.array([LON]), np.zeros(1))
        b = self.rg.apply(np.array([LAT]), np.array([LON]), np.zeros(1))
        self.assertEqual(len(self.made), 1)
        self.assertEqual(float(a[0]), float(b[0]))

    def test_blended_across_an_edge_without_a_step(self):
        edge = (regiongrade.region_of(LAT, LON)[0] + 1) * regiongrade.REGION_DEG     # a latitude edge
        d = np.linspace(-60.0, 60.0, 241) / regiongrade.M_PER_DEG
        out = self.rg.apply(edge + d, np.full(len(d), LON), np.zeros(len(d)))
        lo, hi = sorted(g.offset for g in self.made)
        self.assertEqual(len(self.made), 2)
        np.testing.assert_allclose(out[0], lo)          # 60 m south: the south region alone
        np.testing.assert_allclose(out[-1], hi)         # 60 m north: the north region alone
        self.assertAlmostEqual(float(out[120]), (lo + hi) / 2, delta=1e-6, msg="half and half on the edge")
        step = np.abs(np.diff(out)).max()
        self.assertLess(step, (hi - lo) / (regiongrade.BAND_M / 0.5) * 1.01, "a ramp over the band, no step")

    def test_corner_weights_sum_to_one(self):
        i, j = regiongrade.region_of(LAT, LON)
        la, lo = (i + 1) * regiongrade.REGION_DEG, (j + 1) * regiongrade.REGION_DEG
        rng = np.random.default_rng(1)
        lat = la + rng.uniform(-30, 30, 500) / regiongrade.M_PER_DEG
        lon = lo + rng.uniform(-30, 30, 500) / regiongrade.M_PER_DEG
        ri, rj, w = self.rg._weights(lat, lon)
        np.testing.assert_allclose(w.sum(1), 1.0)
        self.assertTrue((w >= 0).all())
        out = self.rg.apply(lat, lon, np.zeros(500))
        self.assertEqual(len(self.made), 4, "the four regions at the corner")
        offs = [g.offset for g in self.made]
        self.assertTrue(((out >= min(offs) - 1e-9) & (out <= max(offs) + 1e-9)).all())

    def test_real_grader_heights_unchanged_inside_a_region(self):
        # a Grader in the region's frame grades as one in an anchor's frame (FRAME, ~km away): within a few mm here
        f = features([[(-300, 20), (300, 20)]], ["residential"])
        dem = lambda la, lo: np.full(np.shape(la), 10.0)     # noqa: E731
        rg = regiongrade.RegionGrader(lambda fr: grading.Grader(fr, FakeMD(f), dem))
        own = grading.Grader(FRAME, FakeMD(f), dem)
        la, lo, _ = FRAME.enu_to_geodetic(np.array([0.0, 0.0, 100.0]), np.array([20.0, 200.0, 21.0]), 0.0)
        np.testing.assert_allclose(rg.apply(la, lo, np.zeros(3)), own.apply(la, lo, np.zeros(3)), atol=0.005)

    def test_open_tunnels_once(self):
        edge = (regiongrade.region_of(LAT, LON)[0] + 1) * regiongrade.REGION_DEG
        got = self.rg.open_tunnels(np.array([edge - 1e-5, edge + 1e-5]), np.array([LON, LON]))
        self.assertEqual(len(self.made), 2)
        self.assertEqual(len(got), 1, "the same section from both regions, once")

    def test_lookahead_routed_to_the_regions_a_cell_touches(self):
        import mapcells
        key = mapcells.cell_of(LAT, LON)
        self.assertFalse(self.rg.planned(key))
        self.rg.warm(key)
        self.assertTrue(self.rg.planned(key))
        want = {regiongrade.region_of(la, lo) for la, lo in [(LAT, LON)]}
        have = {regiongrade.region_of(g.frame.lat0, g.frame.lon0) for g in self.made}
        self.assertTrue(want <= have)

    def test_regions_kept_bounded(self):
        for k in range(regiongrade.MAX_REGIONS + 3):
            self.rg.grader(k, 0)
        self.assertEqual(len(self.rg.graders()), regiongrade.MAX_REGIONS)

    def test_frame_is_the_region_centre(self):
        g = self.rg.grader(397, -1050)
        self.assertIsInstance(g.frame, geodesy.EnuFrame)
        self.assertAlmostEqual(g.frame.lat0, 39.75)
        self.assertAlmostEqual(g.frame.lon0, -104.95)


if __name__ == "__main__":
    unittest.main()
