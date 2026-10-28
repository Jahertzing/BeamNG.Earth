"""Lane sources (feature lane-sources): the player's overrides (laneoverrides.py), CDOT's highway geometrics (cdot.py),
OpenStreetMap's lanes checked against DRCOG's pavement and lanes from it (roads._pavement_lanes), ramps' lanes."""
import json
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cdot  # noqa: E402
import laneoverrides  # noqa: E402
import mapgeom  # noqa: E402
import roads  # noqa: E402
from test_roads import M_LAT, M_LON, LAT, LON, features, rules  # noqa: E402

FT = cdot.FT


def stretch(line, **kw):
    """A CDOT stretch GeoJSON feature along line [(east m, north m)...] (digitized in increasing milepoints)."""
    props = {"ROUTE": "052A", "REFPT": 10.0, "ENDREFPT": 11.0, "ISDIVIDED": "N", "PRITHRULNQTY": 1, "SECTHRULNQTY": 1,
             "THRULNWD": 12, "PRIAUXLNQTY": 0, "SECAUXLNQTY": 0, "PRILTURNQTY": 0, "SECLTURNQTY": 0,
             "MEDIAN": "54  Channelized - Painted", "MEDIANWD": 5, "PRIOUTSHLDWD": 8, "SECOUTSHLDWD": 8,
             "PRIINSHLDWD": 0, "SECINSHLDWD": 0, "MAL": "None", "MALNQTY": 0, "MALALIGN": "None"}
    props.update(kw)
    return {"geometry": {"type": "LineString", "coordinates": [[LON + e / M_LON, LAT + n / M_LAT] for e, n in line]},
            "properties": props}


def layer(lines, classes, names=None, oneway=(), osm_lanes=None, subclass=()):
    f = features(lines, classes)
    n = len(lines)
    f.attrs["name"] = np.array(names or [""] * n)
    if oneway:
        f.rules["oneway"] = rules([(i, 1.0) for i in oneway], "float")
    if subclass:
        f.rules["subclass"] = rules([(i, "link") for i in subclass])
        f.attrs["subclass"] = np.array(["link" if i in subclass else "" for i in range(n)])
    if osm_lanes is not None:
        f.attrs["osm_lanes"] = mapgeom.float_column([None if v is None else float(v) for v in osm_lanes])
    return f


def with_cdot(f, stretches):
    cdot.join(f, cdot.parse(stretches))
    return f


class CdotTest(unittest.TestCase):
    def test_two_way_highway_lanes_turn_lane_and_shoulders(self):
        # CO-52 through Frederick (CDOT 2025): one through lane each way, a painted median with left turn bays, 8 ft
        # shoulders; OpenStreetMap said lanes=2 and DRCOG's pavement 17 m, drawn as two 8 m lanes before
        f = with_cdot(layer([[(0, 2), (400, 2)]], ["primary"], osm_lanes=[2]),
                      [stretch([(-50, 0), (500, 0)], PRILTURNQTY=1)])
        sp = roads.road_specs(f)[0]
        self.assertEqual((sp.lanes_f, sp.lanes_b, sp.lanes_c, sp.lsrc), (1, 1, 1, "cdot"))
        self.assertAlmostEqual(sp.shoulder, 8 * FT)
        self.assertAlmostEqual(sp.width, 3 * 12 * FT + 2 * 8 * FT, places=6)
        lay = roads.lane_layout(sp)
        self.assertEqual([(d, n) for d, n, _, _ in lay["groups"]], [(1, 1), (-1, 1)])
        self.assertIsNotNone(lay["centre"])
        self.assertAlmostEqual(lay["hi"] - lay["lo"], 3 * 12 * FT, places=6)     # (the shoulders beside the lanes)

    def test_against_the_line_swaps_the_directions_and_aux_lanes_count(self):
        f = with_cdot(layer([[(400, 2), (0, 2)]], ["primary"]),
                      [stretch([(-50, 0), (500, 0)], PRITHRULNQTY=2, SECTHRULNQTY=1, PRIAUXLNQTY=1,
                               MEDIAN="52  Channelized - Raised Curb", SECLTURNQTY=2)])
        sp = roads.road_specs(f)[0]
        self.assertEqual((sp.lanes_f, sp.lanes_b, sp.lanes_c), (1 + 2, 3, 0))     # (a raised median: turn lanes in)

    def test_divided_carriageways_take_their_direction(self):
        # I-25 at Frederick: 3 + 3, a 55 ft median, 10 ft inner and 16 ft outer shoulders; CDOT's line nearer one side
        s = stretch([(-100, 0), (600, 0)], ROUTE="025A", ISDIVIDED="Y", PRITHRULNQTY=3, SECTHRULNQTY=3,
                    MEDIAN="21  Depressed", MEDIANWD=55, PRIOUTSHLDWD=16, SECOUTSHLDWD=16, PRIINSHLDWD=10,
                    SECINSHLDWD=10)
        f = with_cdot(layer([[(0, -12), (500, -12)], [(500, 42), (0, 42)]], ["motorway", "motorway"], oneway=(0, 1)),
                      [s])
        sp = roads.road_specs(f)
        self.assertEqual([x.lanes for x in sp], [3, 3])
        self.assertTrue(all(x.lsrc == "cdot" for x in sp))
        self.assertAlmostEqual(sp[0].width, 3 * 12 * FT + 2 * 13 * FT, places=6)

    def test_frontage_road_does_not_take_the_interstates_lanes(self):
        s = stretch([(-100, 0), (600, 0)], ROUTE="025A", ISDIVIDED="Y", PRITHRULNQTY=3, SECTHRULNQTY=3)
        f = with_cdot(layer([[(0, 30), (500, 30)]], ["secondary"], oneway=(0,)), [s])
        self.assertIsNone(roads.road_specs(f)[0].lsrc)

    def test_express_roadway_beside_the_general_lanes(self):
        # I-25 north of Denver: one express lane each way inside the general lanes, mapped as unnamed segments
        s = stretch([(-100, 0), (600, 0)], ROUTE="025A", ISDIVIDED="Y", PRITHRULNQTY=3, SECTHRULNQTY=3,
                    MEDIAN="14  Level", MAL="Exclusive", MALNQTY=1, MALALIGN="Both")
        lines = [[(0, -20), (500, -20)], [(0, -8), (500, -8)], [(500, 8), (0, 8)], [(500, 20), (0, 20)]]
        f = with_cdot(layer(lines, ["motorway"] * 4, names=["Dwight D. Eisenhower Highway", "", "",
                                                             "Dwight D. Eisenhower Highway"], oneway=(0, 1, 2, 3)), [s])
        sp = roads.road_specs(f)
        self.assertEqual([x.lanes for x in sp], [3, 1, 1, 3])
        self.assertEqual([float(v) for v in f.attrs["cdot_express"]], [0.0, 1.0, 1.0, 0.0])
        # one named "Express Lane" (the Gap) is express whatever its place; without an express roadway the general
        # carriageway takes the managed lane in
        f2 = with_cdot(layer([[(0, -14), (500, -14)]], ["motorway"], names=["South Gap Express Lane"], oneway=(0,)), [s])
        self.assertEqual(roads.road_specs(f2)[0].lanes, 1)
        f3 = with_cdot(layer([[(0, -14), (500, -14)]], ["motorway"], oneway=(0,)), [s])
        self.assertEqual(roads.road_specs(f3)[0].lanes, 4)

    def test_long_segment_matches_its_part_in_the_cell(self):
        # a 7 km Overture segment: only its samples where the cell's lines are count
        f = with_cdot(layer([[(-3000, 2), (4000, 2)]], ["primary"]), [stretch([(0, 0), (900, 0)], PRITHRULNQTY=2,
                                                                             SECTHRULNQTY=2)])
        self.assertEqual(roads.road_specs(f)[0].lanes, 4)

    def test_off_the_line_or_across_it_no_match(self):
        s = stretch([(-50, 0), (500, 0)])
        f = with_cdot(layer([[(0, 40), (400, 40)], [(200, -200), (200, 200)]], ["primary", "primary"]), [s])
        self.assertEqual([x.lsrc for x in roads.road_specs(f)], [None, None])

    def test_parse_sorted_and_deterministic(self):
        a = stretch([(0, 0), (100, 0)], REFPT=12.0)
        b = stretch([(100, 0), (200, 0)], REFPT=11.0)
        g1, g2 = cdot.parse([a, b]), cdot.parse([b, a])
        self.assertEqual(list(g1["REFPT"]), [11.0, 12.0])
        for k in g1:
            np.testing.assert_array_equal(g1[k], g2[k])

    def test_covers(self):
        self.assertTrue(cdot.covers((-104.99, 40.08, -104.98, 40.09)))
        self.assertFalse(cdot.covers((-76.6, 39.28, -76.59, 39.29)))


class PavementTest(unittest.TestCase):
    def sp(self, **kw):
        base = dict(width=17.0, kind="paved", urban=False, cls="primary", oneway=False)
        base.update(kw)
        return roads.Spec(**base)

    def test_osm_lanes_kept_within_one_lane(self):
        s = roads._pavement_lanes(self.sp(width=14.0, lanes=2))
        self.assertEqual((s.lanes, s.lsrc), (2, "osm"))

    def test_pavement_wins_past_one_lane(self):
        s = roads._pavement_lanes(self.sp(width=21.0, lanes=2, lanes_f=1, lanes_b=1))
        self.assertEqual((s.lanes, s.lanes_f, s.lsrc), (4, None, "pavement"))

    def test_lanes_from_the_pavement_where_osm_has_none(self):
        self.assertEqual(roads._pavement_lanes(self.sp(width=15.0)).lanes, 4)
        self.assertIsNone(roads._pavement_lanes(self.sp(width=15.0, cls="residential")).lanes)   # (unmarked)
        self.assertIsNone(roads._pavement_lanes(self.sp(width=40.0)).lanes)    # (more than PAVE_MAX_LANES: not taken)

    def test_pavement_first_marks_its_widths(self):
        get = roads.pavement_first(lambda fid, ll: (12.0, 2.5) if fid == "a" else None, lambda fid, ll: (9.0, 3.0))
        self.assertEqual(get("a", None), (12.0, 3.0, "pavement"))
        self.assertEqual(get("b", None), (9.0, 3.0))


class RampTest(unittest.TestCase):
    def test_two_lane_ramp_has_its_width_and_shoulders(self):
        f = layer([[(0, 0), (100, 0)]] * 2, ["motorway"] * 2, oneway=(0, 1), subclass=(0, 1), osm_lanes=[2, 1])
        sp = roads.road_specs(f)
        self.assertEqual((sp[0].lanes, sp[0].width), (2, roads.LINK_M[2]))
        self.assertEqual((sp[1].lanes, sp[1].width), (1, roads.LINK_M[1]))
        lay = roads.lane_layout(sp[0])
        self.assertEqual(lay["groups"][0][1], 2)
        self.assertAlmostEqual(lay["hi"] - lay["lo"], 2 * roads.LINK_LANE_M, places=6)

    def test_ramp_lanes_from_the_pavement(self):
        f = layer([[(0, 0), (100, 0)]] * 2, ["motorway"] * 2, oneway=(0, 1), subclass=(0, 1))
        pave = {"id000": (9.5, 2.5, "pavement"), "id001": (14.0, 2.5, "pavement")}
        sp = roads.road_specs(f, None, lambda fid, ll: pave.get(fid))
        self.assertEqual((sp[0].lanes, sp[0].lsrc), (2, "pavement"))
        self.assertIsNone(sp[1].lsrc)               # (3 lanes from a ramp's pavement: a gore measured, not taken)
        self.assertEqual(roads.link_width(3), roads.LINK_M[2] + roads.LINK_LANE_M)


class OverrideTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(laneoverrides._loaded.clear)

    def write(self, data):
        p = os.path.join(self.tmp, f"o{len(os.listdir(self.tmp))}.json")
        with open(p, "w") as fh:
            json.dump(data, fh)
        return p

    def test_by_name_and_box_over_cdot(self):
        p = self.write({"roads": [{"name": "Highway 52", "bbox": [LON - 0.01, LAT - 0.01, LON + 0.01, LAT + 0.01],
                                   "lanes:forward": 2, "lanes:backward": 2, "centre_turn_lane": True,
                                   "shoulder": 2.5}]})
        f = with_cdot(layer([[(0, 2), (400, 2)], [(0, 2000), (400, 2000)]], ["primary", "primary"],
                            names=["Highway 52", "Highway 52"]), [stretch([(-50, 0), (500, 0)])])
        with mock.patch.dict(os.environ, {laneoverrides.ENV_PATH: p}):
            sp = roads.road_specs(f)
        self.assertEqual((sp[0].lanes_f, sp[0].lanes_b, sp[0].lanes_c, sp[0].lsrc), (2, 2, 1, "override"))
        self.assertAlmostEqual(sp[0].width, 5 * roads.LANE_W["primary"] + 5.0)
        self.assertNotEqual(sp[1].lsrc, "override")           # (outside the box)

    def test_by_way_id_lanes_and_width(self):
        p = self.write([{"way": 123, "lanes": 3, "width": 12.0}, {"way": 999}])
        f = layer([[(0, 0), (100, 0)]], ["secondary"])
        f.attrs["src_record"] = np.array(["w123@4"])
        with mock.patch.dict(os.environ, {laneoverrides.ENV_PATH: p}):
            sp = roads.road_specs(f)[0]
        self.assertEqual((sp.lanes, sp.width, sp.lsrc), (3, 12.0, "override"))

    def test_bad_file_left_out(self):
        p = os.path.join(self.tmp, "bad.json")
        with open(p, "w") as fh:
            fh.write("{nope")
        self.assertEqual(laneoverrides.entries(p), [])
        self.assertEqual(laneoverrides.entries(os.path.join(self.tmp, "missing.json")), [])
        self.assertEqual(laneoverrides.file_sha(os.path.join(self.tmp, "missing.json")), "")
        self.assertEqual(len(laneoverrides.file_sha(p)), 16)


if __name__ == "__main__":
    unittest.main()
