"""US building heights from 3DEP lidar's height-above-ground (server/lidarheights.py)."""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import lidarheights as LH  # noqa: E402
import mapcells  # noqa: E402
import mapgeom as mg  # noqa: E402
import mapfixtures as mf  # noqa: E402
from test_mapdata import Sources, SmallBlocks, needs_duckdb  # noqa: E402

KEY = mapcells.cell_of(40.0992, -104.9372)


def px_to_lonlat(key, c, r):
    """lon, lat of mosaic pixel (column c, row r) of key's z16 mosaic."""
    n = 1 << (LH.ZOOM - key.z)
    xf, yf = key.x * n + c / 256.0, key.y * n + r / 256.0
    z = 1 << LH.ZOOM
    lon = xf / z * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * yf / z))))
    return lon, lat


def boxes(key, rects, heights=None, srcs=None):
    """A buildings Features of pixel-space rectangles (c0, r0, c1, r1) of the mosaic."""
    geoms = []
    for c0, r0, c1, r1 in rects:
        ring = np.array([px_to_lonlat(key, c, r) for c, r in ((c0, r0), (c1, r0), (c1, r1), (c0, r1), (c0, r0))])
        geoms.append((mg.POLYGON, [[ring]]))
    n = len(rects)
    f = mg.build("buildings", mg.text_column([f"b{i:03d}" for i in range(n)]), geoms)
    f.attrs["height"] = mg.float_column(heights or [None] * n)
    f.attrs["height_src"] = mg.text_column(srcs or [""] * n)
    return f


class HeightsTest(unittest.TestCase):
    def mosaic(self):
        hag = np.zeros((1024, 1024), np.uint8)
        hag[100:120, 100:130] = int(round(9.0 / LH.HAG_STEP_M))          # a 9 m flat roof
        hag[104, 104] = 255                                               # a saturated noise pixel
        hag[106, 110] = int(round(60.0 / LH.HAG_STEP_M))                  # a spike (a bird, a branch)
        hag[300:320, 300:340] = int(round(1.0 / LH.HAG_STEP_M))           # too low: no building there now
        return hag, np.ones((4, 4), bool)

    def test_median_without_spikes(self):
        hag, cov = self.mosaic()
        f = boxes(KEY, [(100, 100, 130, 120), (300, 300, 340, 320), (600, 600, 600.4, 600.4)])
        h = LH.footprint_heights(f, KEY, hag, cov)
        self.assertAlmostEqual(h[0], 9.0, delta=LH.HAG_STEP_M)
        self.assertTrue(np.isnan(h[1]))                                   # under MIN_HEIGHT_M
        self.assertTrue(np.isnan(h[2]))                                   # under MIN_PIXELS

    def test_uncovered_tiles_give_none(self):
        hag, cov = self.mosaic()
        cov[0, 0] = False
        h = LH.footprint_heights(boxes(KEY, [(100, 100, 130, 120)]), KEY, hag, cov)
        self.assertTrue(np.isnan(h[0]))

    def test_apply_keeps_mapped_heights(self):
        f = boxes(KEY, [(0, 0, 10, 10)] * 4, heights=[7.0, 6.0, None, 12.0],
                  srcs=["Microsoft ML Buildings", "OpenStreetMap", "", "USGS Lidar"])
        f, n = LH.apply(f, np.array([11.0, 11.0, 11.0, np.nan]))
        self.assertEqual(n, 2)
        np.testing.assert_allclose(np.asarray(f.attrs["height"], float), [11.0, 6.0, 11.0, 12.0])
        self.assertEqual(list(f.attrs["height_src"]), [LH.DATASET, "OpenStreetMap", LH.DATASET, "USGS Lidar"])

    def test_tile_choice_newest_item(self):
        L = LH.Lidar("unused", None)
        lat, lon = KEY.center()
        items = [("new", (lon - 1, lat - 1, lon + 1, lat + 1), "2020"), ("old", (lon - 1, lat - 1, lon + 1, lat + 1),
                                                                         "2013")]
        got = []

        def tile(item, z, x, y, kind="demand"):
            got.append(item)
            return np.full((256, 256), 20, np.uint8)
        with mock.patch.object(L, "items", return_value=items), mock.patch.object(L, "tile", side_effect=tile):
            m = L._build(KEY, "demand")
        self.assertEqual(set(got), {"new"})
        self.assertTrue(m["covered"].all())
        self.assertEqual(list(m["items"]), ["new"])

    def test_items_sorted_newest_first(self):
        class F:
            def get(self, url, kind="demand"):
                import json
                body = {"features": [{"id": "a", "bbox": [0, 0, 1, 1], "properties": {"datetime": "2013-01-01"}},
                                     {"id": "b", "bbox": [0, 0, 1, 1], "properties": {"datetime": "2019-06-01"}}]}
                return type("R", (), {"body": json.dumps(body).encode()})()
        self.assertEqual([i[0] for i in LH.Lidar("x", F()).items((0, 0, 1, 1))], ["b", "a"])


@needs_duckdb
class MapDataTest(Sources, SmallBlocks, unittest.TestCase):
    def test_off_by_default_and_on_with_the_flag(self):
        md, _ = self.mapdata()
        self.assertIsNone(md.lidar())
        self.assertNotIn(LH.TAG, md.path(mf.CELL, "buildings"))
        with mock.patch.dict(os.environ, {LH.ENV: "1"}):
            md2, _ = self.mapdata(cache=os.path.join(self.tmp, "c2"))
            self.assertIn(f"buildings.{LH.TAG}", md2.path(mf.CELL, "buildings"))
            n = 7
            with mock.patch.object(LH.Lidar, "heights", return_value=np.full(n, 13.0)):
                cd = md2.cell(mf.CELL, ("buildings",))
            b = cd.layers["buildings"]
            i = b.index("bld-00")                       # (no height in the fixture: the lidar's)
            self.assertAlmostEqual(float(b.attrs["height"][i]), 13.0)
            ds = [d["dataset"] for p in cd.provenance for d in p.get("datasets", [])]
            self.assertIn(LH.DATASET, ds)


if __name__ == "__main__":
    unittest.main()
