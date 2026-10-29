"""The ring of far tiles around the drawn block gets the block's roof fill (earthd.Server.building_fill /
building_faces): only the exact level and the next, only with buildings and map data, the photo kept on failure."""
import os
import sys
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import earthd  # noqa: E402


def server(buildings=True, mapdata=True, exact=-1):
    return types.SimpleNamespace(buildings=buildings, mapdata=object() if mapdata else None,
                                 layout=types.SimpleNamespace(exact_level=exact))


class RoofFillTest(unittest.TestCase):
    def test_levels(self):
        s = server()
        fill = [earthd.Server.building_fill(s, lvl) for lvl in (-1, 0, 1, 2)]
        self.assertEqual(fill, [True, True, False, False])

    def test_needs_buildings_and_map_data(self):
        self.assertFalse(earthd.Server.building_fill(server(buildings=False), -1))
        self.assertFalse(earthd.Server.building_fill(server(mapdata=False), -1))

    def test_switch(self):
        with mock.patch.object(earthd, "FAR_ROOF_FILL", False):
            self.assertFalse(earthd.Server.building_fill(server(), -1))

    def test_faces_filled_and_kept_on_failure(self):
        s = server()
        s.building_fill = lambda lvl: earthd.Server.building_fill(s, lvl)
        tile = types.SimpleNamespace(level=-1, bbox=(0.0, 0.0, 128.0, 128.0), size=128.0, key="-1_0_0")
        anchor = types.SimpleNamespace(frame=None)
        rgb = np.zeros((16, 16, 3), np.uint8)
        mask = np.zeros((16, 16), bool)
        with mock.patch.object(earthd.buildingsmod, "photo_mask", return_value=mask), \
                mock.patch.object(earthd.buildingsmod, "fill_photo", return_value=rgb + 7) as fill:
            out = earthd.Server.building_faces(s, anchor, tile, 16, rgb)
        self.assertEqual(int(out[0, 0, 0]), 7)
        self.assertEqual(fill.call_count, 1)
        with mock.patch.object(earthd.buildingsmod, "photo_mask", side_effect=OSError("no cell")):
            out = earthd.Server.building_faces(s, anchor, tile, 16, rgb)
        self.assertIs(out, rgb)
        tile.level = 1
        self.assertIs(earthd.Server.building_faces(s, anchor, tile, 16, rgb), rgb, "beyond the ring: as it was")


if __name__ == "__main__":
    unittest.main()
