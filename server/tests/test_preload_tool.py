"""tools/preload.py's plan (no network): the tiles of a disc nearest first, the DEM rings with the ground filter's z12
tiles, the lazy export sources' parents (z17 and a finer source's z18) only inside their areas, the map cells."""
import importlib.util
import math
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
import dem as demmod  # noqa: E402
import imagery  # noqa: E402
import mapcells  # noqa: E402

_spec = importlib.util.spec_from_file_location("preload", HERE.parents[2] / "tools" / "preload.py")
preload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preload)

FREDERICK = (40.0992, -104.9372)
BALTIMORE = (39.2834, -76.5934)


def dist_m(lat, lon, z, x, y):
    n = 2 ** z
    cx, cy = (float(v) for v in mapcells.tile_xy(lat, lon, z))
    return math.hypot(x + 0.5 - cx, y + 0.5 - cy) * preload._tile_m(lat, z)


class PlanTest(unittest.TestCase):
    def test_disc_nearest_first(self):
        tiles = preload.tiles_in_disc(*FREDERICK, 3000.0, 15)
        d = [dist_m(*FREDERICK, 15, x, y) for x, y in tiles]
        self.assertEqual(d, sorted(d))
        self.assertLess(max(d), 3000.0 + preload._tile_m(FREDERICK[0], 15))
        self.assertIn(tuple(int(v) for v in mapcells.tile_xy(*FREDERICK, 15)), tiles[:1])
        self.assertEqual(preload.tiles_in_disc(*FREDERICK, 0.0, 15), [])

    def test_dem_rings(self):
        keys = preload.plan_dem(*FREDERICK, 1000.0, 2000.0, 20000.0)
        self.assertEqual(len(keys), len(set(keys)))
        zooms = {k[0] for k in keys}
        self.assertEqual(max(zooms), demmod.zoom_for(FREDERICK[0], 1.25, demmod.FINE_ZOOM))
        self.assertTrue({10, 11, 12} <= zooms)
        fz = max(zooms)
        sh = fz - demmod.GLOBAL_ZOOM
        for z, x, y in keys:
            if z == fz:                     # the ground filter's z12 tile of every fine tile
                self.assertIn((demmod.GLOBAL_ZOOM, x >> sh, y >> sh), keys)

    def test_exports_inside_their_areas(self):
        chain = [imagery.SOURCES[n] for n in imagery.DEFAULT_CHAIN]
        plan = dict((src.name, keys) for src, keys in preload.plan_exports(*FREDERICK, chain, 2000.0, 1000.0))
        town = plan["co_frederick"]
        self.assertEqual({k[1] for k in town}, {17, 18})
        for src, z, x, y in town:
            self.assertEqual((x % 4, y % 4), (0, 0), "a parent's first child")
            self.assertTrue(imagery.in_bounds(src, z, x, y))
        self.assertIn("naip", plan)
        self.assertNotIn("md6in", plan, "Maryland's: not asked in Colorado")
        far = dict((src.name, keys) for src, keys in preload.plan_exports(*BALTIMORE, chain, 2000.0, 1000.0))
        self.assertNotIn("co_frederick", far)
        self.assertIn("md6in", far)
        skipped = dict(preload.plan_exports(*FREDERICK, chain, 2000.0, 1000.0, skip={"naip"}))
        self.assertNotIn(imagery.SOURCES["naip"], skipped)

    def test_in_box(self):
        src = imagery.SOURCES["co_frederick"]
        x, y = (int(v) // 4 * 4 for v in mapcells.tile_xy(*FREDERICK, 17))
        self.assertTrue(preload.in_box(src.bounds, (src, 17, x, y)))
        x, y = (int(v) // 4 * 4 for v in mapcells.tile_xy(39.9, -104.9372, 17))
        self.assertFalse(preload.in_box(src.bounds, (src, 17, x, y)))

    def test_cells(self):
        cells = preload.plan_cells(*FREDERICK, 3000.0)
        self.assertIn(mapcells.cell_of(*FREDERICK), cells[:1])
        self.assertTrue(all(c.z == mapcells.ZOOM for c in cells))


if __name__ == "__main__":
    unittest.main()
