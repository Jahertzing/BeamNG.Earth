"""Bridge decks at the lidar's heights (decklidar.py; Denver's Mousetrap, 2026-10-28)."""
import os
import sys
from types import SimpleNamespace
from unittest import TestCase

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decklidar
import mapcells
import roads


def line(x0, y0, x1, y1, step=4.0):
    n = max(2, int(round(np.hypot(x1 - x0, y1 - y0) / step)) + 1)
    t = np.linspace(0.0, 1.0, n)
    return np.c_[x0 + (x1 - x0) * t, y0 + (y1 - y0) * t]


class ObservationsTest(TestCase):
    def test_follows_the_surface_and_drops_spikes(self):
        ln = line(0, 0, 200, 0)

        def surface(xy):
            z = 10.0 + 0.03 * xy[:, 0]
            z[np.abs(xy[:, 0] - 100.0) < 3.0] += 3.0         # a truck
            return z
        obs = decklidar.observations([ln], [10.0], [1.0], surface)[0]
        self.assertIsNotNone(obs)
        ok = np.isfinite(obs)
        np.testing.assert_allclose(obs[ok], 10.0 + 0.03 * ln[ok, 0], atol=1e-6)
        self.assertFalse(ok[np.abs(ln[:, 0] - 100.0) < 3.0].any())

    def test_under_a_higher_deck_is_not_measured(self):
        low, high = line(0, 0, 200, 0), line(100, -100, 100, 100)

        def surface(xy):                    # the lidar sees the upper deck where it is over the lower one
            return np.where(np.abs(xy[:, 0] - 100.0) < 6.0, 20.0, 10.0)
        obs = decklidar.observations([low, high], [10.0, 10.0], [1.0, 2.0], surface)
        under = np.abs(low[:, 0] - 100.0) < 5.0 + decklidar.OCC_MARGIN_M
        self.assertTrue(np.isnan(obs[0][under]).all())
        np.testing.assert_allclose(obs[0][~under & np.isfinite(obs[0])], 10.0)
        self.assertTrue(np.isfinite(obs[1][np.abs(high[:, 1]) < 4.0]).all())    # the upper one is seen over it

    def test_little_coverage_is_none(self):
        ln = line(0, 0, 200, 0)
        obs = decklidar.observations([ln], [10.0], [1.0], lambda xy: np.where(xy[:, 0] < 20.0, 5.0, np.nan))
        self.assertIsNone(obs[0])

    def test_filled_gaps_and_ends(self):
        s = np.arange(0.0, 201.0, 4.0)
        base = np.zeros_like(s)
        obs = np.full_like(s, np.nan)
        obs[(s > 40) & (s < 80)] = 8.0
        obs[(s > 120) & (s < 160)] = 12.0
        z, ok = decklidar.filled(s, obs, base)
        self.assertTrue((z[(s > 80) & (s < 120)] > 8.0).all() and (z[(s > 80) & (s < 120)] < 12.0).all())
        self.assertAlmostEqual(z[0], 8.0 * (1 - min(1.0, 44.0 / decklidar.END_BLEND_M)), places=6)
        self.assertTrue(ok[(s > 40) & (s < 80)].all() and not ok[s < 40].any() and not ok[s > 160].any())
        self.assertFalse(ok[(s > 80) & (s < 120)].any())   # (a 44 m gap: not held)


class SurfaceTest(TestCase):
    def test_reads_the_mosaic_on_the_dems_datum(self):
        key = mapcells.cell_of(39.78, -104.99)
        z = np.full((1024, 1024), 1503.0, np.float32)    # the ground, 3 m over the DEM's datum ...
        z[500:520, :] = 1545.0                          # ... a deck
        z[:, :10] = np.nan                              # no pixel
        covered = np.ones((4, 4), bool)
        covered[3, 3] = False
        dsm = SimpleNamespace(mosaic=lambda k, kind="demand": {"z": z, "covered": covered})
        sf = decklidar.Surface(dsm, lambda la, lo: np.full(len(la), 1500.0))
        lon0, lat0, lon1, lat1 = key.bounds()
        fy = key.y * 4 + 510 / 256.0
        la_deck = float(np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * fy / (1 << 16))))))
        la = np.array([(lat0 + lat1) / 2 + 3e-3, la_deck, lat1 - 1e-6, lat0 + 1e-6])
        lo = np.array([(lon0 + lon1) / 2, (lon0 + lon1) / 2, lon0 + 1e-7, lon1 - 1e-6])
        got = sf(la, lo)
        self.assertAlmostEqual(got[0], 1500.0, places=3)
        self.assertAlmostEqual(got[1], 1542.0, places=3)
        self.assertTrue(np.isnan(got[2]))               # no pixel
        self.assertTrue(np.isnan(got[3]))               # uncovered tile (south-east)

    def test_npy_tile(self):
        a = np.zeros((2, 256, 256), np.float32)
        a[0] = 1600.0
        a[1, :, 128:] = 255
        t = decklidar.npy_tile(a)
        self.assertTrue(np.isnan(t[:, :128]).all())
        self.assertTrue((t[:, 128:] == 1600.0).all())


class DeckProfileTest(TestCase):
    def test_a_measured_viaduct_keeps_its_height_and_its_abutments(self):
        # a 600 m viaduct between abutments at 0 m whose middle stands 9 m over a road; the lidar measured it at 9 m
        # where the old rule wanted DECK_ROAD_CLEAR (8.35 m + shoulders) and climbed only 6 %
        ln = line(0, 0, 600, 0)
        s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(ln, axis=0).T))]
        real = np.minimum(np.minimum(s * 0.07, (s[-1] - s) * 0.07), 9.0)
        ground = lambda xy: np.zeros(len(xy))  # noqa: E731
        cross = [(0, 300.0, 0.0)]
        obs = real.copy()
        obs[(s > 280) & (s < 320)] = np.nan             # under a gantry: not measured
        zs = roads.deck_profile([ln], ground, cross, levels=[1.0], widths=[12.0], observed=[obs],
                                clear_of=[decklidar.lidar_road_clear()])
        z = zs[0]
        self.assertAlmostEqual(z[0], 0.0, places=3)
        self.assertAlmostEqual(z[-1], 0.0, places=3)
        m = (s > 60) & (s < 540)
        self.assertLess(float(np.abs(z[m] - real[m]).max()), decklidar.OBS_TOL_M + 0.05)
        self.assertGreater(float(np.interp(300.0, s, z)), decklidar.lidar_road_clear() - 0.05)

    def test_without_measurements_as_before(self):
        ln = line(0, 0, 300, 0)
        ground = lambda xy: np.zeros(len(xy))  # noqa: E731
        a = roads.deck_profile([ln], ground, [(0, 150.0, 0.0)], levels=[1.0], widths=[12.0])
        b = roads.deck_profile([ln], ground, [(0, 150.0, 0.0)], levels=[1.0], widths=[12.0], observed=[None])
        np.testing.assert_allclose(a[0], b[0])

    def test_joint_with_an_unmeasured_part_has_no_step(self):
        a, b = line(0, 0, 200, 0), line(200, 0, 400, 0)
        ground = lambda xy: np.zeros(len(xy))  # noqa: E731
        sa = np.r_[0.0, np.cumsum(np.hypot(*np.diff(a, axis=0).T))]
        obs = np.minimum(sa * 0.06, 8.0)
        zs = roads.deck_profile([a, b], ground, [], levels=[1.0, 1.0], widths=[12.0, 12.0], observed=[obs, None])
        self.assertLess(abs(zs[0][-1] - zs[1][0]), 0.02)
        self.assertGreater(zs[0][-1], 6.0)


class AbutmentConsistentTest(TestCase):
    def test_drops_what_no_deck_reaches_from_its_abutment(self):
        s = np.arange(0.0, 101.0, 4.0)
        obs = np.full_like(s, 5.0)
        obs[s < 24] = 9.0                               # another deck over the first 20 m
        got = decklidar.abutment_consistent(s, obs, 0.0, None)
        self.assertTrue(np.isnan(got[s < 24]).all())
        self.assertTrue(np.isfinite(got[s > 60]).all())


class LidarLiftTest(TestCase):
    def setUp(self):
        import grading
        self.g = grading
        self.xy = np.c_[np.arange(0.0, 400.0, 2.0), np.zeros(200)]

    def test_a_fill_the_dem_lacks(self):
        z = np.full(200, 100.0)
        surf = 100.0 + np.clip(np.minimum(self.xy[:, 0] - 50, 350 - self.xy[:, 0]) * 0.05, 0.0, 6.0)
        lift = self.g.lidar_lift(self.xy, z, surf)
        ok = (self.xy[:, 0] > 90) & (self.xy[:, 0] < 310)
        np.testing.assert_allclose(lift[ok], (surf - z)[ok], atol=0.3)     # (its corners rounded)
        self.assertTrue((lift[self.xy[:, 0] < 50] == 0.0).all())     # (no fill: not raised)

    def test_a_dem_dip_at_a_bridge_end_and_a_deck_over_the_road(self):
        z = np.full(200, 100.0)
        z[-4:] = [99.0, 98.5, 98.0, 97.5]               # the DEM dips into the bridge's gap
        surf = np.full(200, 100.0)
        over = np.abs(self.xy[:, 0] - 200) < 20
        surf[over] = 109.0                              # a viaduct over it, not among the occluders
        lift = self.g.lidar_lift(self.xy, z, surf)
        self.assertLess(float(lift[over].max()), 0.05)   # the deck is not the road
        np.testing.assert_allclose(z[-3:] + lift[-3:], 100.0, atol=0.2)
        self.assertLess(float(np.abs(np.diff(z + lift)).max()), 0.6)    # (no step where it starts)

    def test_under_decks_straight_across(self):
        z = np.full(200, 100.0)
        surf = np.full(200, 105.0)
        hidden = np.abs(self.xy[:, 0] - 200) < 40
        surf[hidden] = 120.0
        occ = [(np.array([[200.0, -50.0], [200.0, 50.0]]), 40.0)]
        lift = self.g.lidar_lift(self.xy, z, surf, occ)
        np.testing.assert_allclose(lift, 5.0, atol=0.1)
