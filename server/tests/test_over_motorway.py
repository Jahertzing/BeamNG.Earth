"""buildings.over_motorway: a footprint across a motorway's carriageway is left out in every tier (Jeff's drive
2026-10-29, "big wall in road": DRCOG's roofprint of a light rail station's walkway over I-25 stood as a 19 m wall
across the lanes); one beside the shoulder, or across another class of road, stays."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import buildings as bld  # noqa: E402
from test_roads import FRAME, LAT, LON, M_LAT, M_LON, features, rules  # noqa: E402

MOTORWAY_W = 19.0       # m: I-25's carriageway drawn with its CDOT lanes and shoulders


class Feats:
    """A buildings layer of rectangles (east, north, width, depth) in metres around (LAT, LON), placed as
    test_roads.features places its roads."""

    def __init__(self, ids, rects, **attrs):
        self.ids = np.array(ids, object)
        self.kind = np.full(len(ids), 3, np.uint8)
        self.rings = [np.array([(LON + e / M_LON, LAT + n / M_LAT) for e, n in
                                [(x, y), (x + w, y), (x + w, y + d), (x, y + d), (x, y)]]) for x, y, w, d in rects]
        self.attrs = {k: np.array(v, object if isinstance(v[0], str) else np.float64) for k, v in attrs.items()}

    def parts(self, i):
        return [[np.asarray(self.rings[i], np.float64)]]


def ground(la, lo):
    return np.zeros(np.shape(la))


def tile(b, road_layers, part_layers=()):
    """The ids of tile_buildings' items (by their centres) over the tile [-128, 128)²."""
    ways = bld.motorway_ways(FRAME, road_layers, -128.0, -128.0)
    items = bld.tile_buildings(FRAME, -128.0, -128.0, 128.0, 128.0, [b], ground, part_layers=part_layers,
                               motorways=ways)
    out = set()
    for spec, _, _ in items:
        c = np.asarray(spec.ring).mean(0) - 128.0
        out.add(next(i for i, r in zip(b.ids, b.rings) if np.hypot(*(c - _centre(r))) < 3.0))
    return out


def _centre(ring):
    return np.array([((ring[:-1, 0] - LON) * M_LON).mean(), ((ring[:-1, 1] - LAT) * M_LAT).mean()])


class OverMotorwayTest(unittest.TestCase):
    def motorway(self, cls="motorway", width=MOTORWAY_W):
        """A road along y = 0 across the tile, width m wide (None: its class's own width)."""
        return features([[(-300.0, 0.0), (300.0, 0.0)]], [cls], widths=[(0, width)] if width else [])

    def test_walkway_across_the_motorway_is_left_out(self):
        # the Arapahoe at Village Center walkway's shape: 5 m wide across the lanes, a 19 m stair tower each side
        b = Feats(["walk", "towerless"], [(-2.5, -40.0, 5.0, 80.0), (60.0, -30.0, 4.0, 60.0)],
                  **{"class": ["", ""], "height": [19.2, 6.0]})
        self.assertEqual(tile(b, [self.motorway()]), set())

    def test_elevated_walkway_is_left_out_too(self):
        b = Feats(["walk"], [(-2.5, -40.0, 5.0, 80.0)], **{"class": [""], "height": [12.0], "min_height": [6.5]})
        self.assertEqual(tile(b, [self.motorway()]), set(), "nothing carries it over the lanes")

    def test_buildings_beside_the_shoulder_stay(self):
        hw = MOTORWAY_W / 2
        b = Feats(["clear", "touching", "sliver"],
                  [(-100.0, hw + 2.0, 60.0, 20.0),          # 2 m off the edge
                   (-30.0, hw, 40.0, 20.0),                 # its wall on the edge
                   (20.0, hw - 1.5, 90.0, 25.0)],           # 1.5 m over it (the centre line's own error): 135 m2
                  **{"class": ["", "", ""], "height": [8.0, 8.0, 8.0]})
        self.assertEqual(tile(b, [self.motorway()]), {"clear", "touching", "sliver"})

    def test_links_and_trunks_count_other_roads_do_not(self):
        b = Feats(["walk"], [(-2.5, -40.0, 5.0, 80.0)], **{"class": [""], "height": [10.0]})
        self.assertEqual(tile(b, [self.motorway("trunk", 8.0)]), set(), "a two-lane trunk")
        for cls in ("motorway", "trunk"):       # a ramp: Overture's subclass "link", one lane and its shoulders
            ramp = features([[(-300.0, 0.0), (300.0, 0.0)]], [cls])
            ramp.rules["subclass"] = rules([(0, "link")])
            self.assertEqual(tile(b, [ramp]), set(), f"a {cls} ramp")
        for cls in ("primary", "residential", "footway"):
            self.assertEqual(tile(b, [self.motorway(cls, 8.0)]), {"walk"}, cls)
        self.assertEqual(tile(b, []), {"walk"}, "no roads: as before")

    def test_a_bridge_or_tunnel_range_is_not_ground(self):
        b = Feats(["walk"], [(-2.5, -40.0, 5.0, 80.0)], **{"class": [""], "height": [10.0]})
        for flag in ("is_bridge", "is_tunnel"):
            f = features([[(-300.0, 0.0), (300.0, 0.0)]], ["motorway"], widths=[(0, MOTORWAY_W)],
                         levels=[(0, 1.0 if flag == "is_bridge" else -1.0, 0.4, 0.6)], flags=[(0, flag, 0.4, 0.6)])
            self.assertEqual(tile(b, [f]), {"walk"}, f"under a viaduct / over a tunnel ({flag})")

    def test_the_widest_copy_across_cells_whatever_the_order(self):
        """A road in two cells, one copy without its CDOT lanes (11.8 m against 19 m drawn): the footprint over its
        outer lanes is tested against the 19 m, whichever cell came first."""
        narrow, wide = self.motorway(width=None), self.motorway()
        self.assertLess(bld.motorway_ways(FRAME, [narrow], 0.0, 0.0)[0][1] * 2, 15.0)
        b = Feats(["outer"], [(-25.0, 4.5, 50.0, 3.5)], **{"class": [""], "height": [6.0]})    # 4.5-8 m off centre
        self.assertEqual(tile(b, [narrow]), {"outer"}, "clear of the narrow copy's lanes")
        self.assertEqual(tile(b, [narrow, wide]), set())
        self.assertEqual(tile(b, [wide, narrow]), set())

    def test_a_part_across_the_motorway_goes(self):
        """A building drawn as its parts: the part across the lanes goes, the rest stays."""
        b = Feats(["B"], [(-40.0, 12.0, 80.0, 30.0)], **{"class": [""], "height": [20.0], "has_parts": [1.0]})
        parts = Feats(["p1", "p2"], [(-40.0, 12.0, 80.0, 30.0), (-2.5, -30.0, 5.0, 45.0)], building_id=["B", "B"],
                      height=[20.0, 12.0], min_height=[0.0, 6.0], num_floors=[np.nan, np.nan],
                      min_floor=[np.nan, np.nan])
        ways = bld.motorway_ways(FRAME, [self.motorway()], -128.0, -128.0)
        items = bld.tile_buildings(FRAME, -128.0, -128.0, 128.0, 128.0, [b], ground, part_layers=[parts],
                                   motorways=ways)
        self.assertEqual(len(items), 1)
        self.assertGreater(np.asarray(items[0][0].ring)[:, 1].min() - 128.0, 10.0, "the part beside it")

    def test_overlap_is_a_pure_function(self):
        ring = np.array([(-2.5, -40.0), (2.5, -40.0), (2.5, 40.0), (-2.5, 40.0)])
        ways = [(np.array([[-300.0, 0.0], [300.0, 0.0]]), MOTORWAY_W / 2)]
        a = bld.motorway_overlap(ring, ways)
        self.assertAlmostEqual(a, 5.0 * MOTORWAY_W, delta=5.0)
        self.assertEqual(a, bld.motorway_overlap(ring.copy(), list(ways)))
        self.assertAlmostEqual(bld.motorway_overlap(ring, ways, inset=2.0), 5.0 * (MOTORWAY_W - 4.0), delta=5.0)


if __name__ == "__main__":
    unittest.main()
