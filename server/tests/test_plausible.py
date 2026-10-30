"""plausible: spikes and pits out, the relief under a few metres damped on gentle ground, slopes (embankments, cliffs)
kept, the sea's 0 untouched, landforms unchanged, tiles agreeing at their seams with the margin dem gives them."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import plausible  # noqa: E402

PX = 0.6        # m per pixel (a z17 lidar tile at 40 degrees)


def field(n=256, seed=1):
    """A gentle hill (landform) plus rough micro relief (0.15 m furrows and noise)."""
    y, x = np.mgrid[0:n, 0:n] * PX
    hill = 5.0 * np.exp(-((x - 80) ** 2 + (y - 80) ** 2) / (2 * 40.0 ** 2))
    rng = np.random.default_rng(seed)
    rough = 0.15 * np.sin(x * 2 * np.pi / 2.4) + 0.05 * rng.standard_normal((n, n))
    return (1600.0 + hill + rough).astype(np.float32), (1600.0 + hill).astype(np.float32)


class PlausibleTest(unittest.TestCase):
    def test_micro_relief_damped_landform_kept(self):
        a, landform = field()
        out = plausible.plausible(a, PX)
        before = np.abs(a - landform)[20:-20, 20:-20].mean()
        after = np.abs(out - landform)[20:-20, 20:-20].mean()
        self.assertLess(after, before * 0.4, "the furrows and noise mostly gone")
        self.assertLess(np.abs(out - landform)[20:-20, 20:-20].max(), 0.35, "the hill where it was")

    def test_spike_and_pit_out(self):
        a = np.full((64, 64), 1600.0, np.float32)
        a[20, 20] = 1603.0
        a[40, 40] = 1597.0
        out = plausible.plausible(a, PX)
        self.assertLess(abs(out[20, 20] - 1600.0), 0.05)
        self.assertLess(abs(out[40, 40] - 1600.0), 0.05)

    def test_steep_face_kept(self):
        # a 1:1 road cut (45 degrees): its edge and its grade stay as they are
        y, x = np.mgrid[0:128, 0:128] * PX
        a = (1600.0 + np.clip(x - 30.0, 0.0, 20.0)).astype(np.float32)
        out = plausible.plausible(a, PX)
        mid = (x > 38) & (x < 42)
        self.assertLess(np.abs(out - a)[mid].max(), 0.05, "the face itself unchanged")

    def test_sea_untouched(self):
        a, _ = field(64)
        a[:, :20] = 0.0
        out = plausible.plausible(a, PX)
        self.assertTrue((out[:, :20] == 0.0).all())
        self.assertGreater(out[:, 25:].min(), 1500.0, "the shore not pulled down by the sea's 0")

    def test_tiles_agree_with_their_margin(self):
        # (dem.Dem._plausible: each tile filtered with MARGIN_PX of its neighbours): a tile's interior as the whole
        a, _ = field(300)
        whole = plausible.plausible(a, PX)
        m = plausible.MARGIN_PX
        t0, t1 = 100, 200
        part = plausible.plausible(a[t0 - m:t1 + m, t0 - m:t1 + m], PX)[m:-m, m:-m]
        np.testing.assert_allclose(part, whole[t0:t1, t0:t1], atol=2e-3)


if __name__ == "__main__":
    unittest.main()
