"""Lanes each way, centre turn lanes, parking and turn arrows from the regional OSM tables (roads._osm_lanes,
lane_layout, lane_marks; feature turn-lanes)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import osmtags  # noqa: E402
import roads  # noqa: E402
from test_roads import FRAME, features, flat  # noqa: E402

LON_US = -104.99


def tagged(feats, rows):
    """feats with the regional columns: rows [{key: value}] per feature (as mapdata's join leaves them)."""
    n = len(feats)
    for k in osmtags.REGIONAL_KEYS:
        feats.attrs[osmtags.regional_column(k)] = np.array([r.get(k, "") for r in rows] + [""] * (n - len(rows)))
    return feats


def spec(**kw):
    base = dict(width=roads.carriageway("primary", "paved", True, None, False, kw.get("lanes")), kind="paved",
                urban=True, cls="primary", oneway=False)
    base.update(kw)
    return roads.Spec(**base)


STRAIGHT = np.c_[np.linspace(0, 200, 21), np.zeros(21), np.full(21, 10.0)]


class ParseTest(unittest.TestCase):
    def test_turns(self):
        self.assertEqual(roads.parse_turns("left|through|through;right"),
                         (("left",), ("through",), ("through", "right")))
        self.assertEqual(roads.parse_turns("|none|reverse;left"), ((), (), ("left",)))
        self.assertIsNone(roads.parse_turns(""))

    def test_parking_sides(self):
        f = tagged(features([[(0, 0), (100, 0)]] * 4, ["residential"] * 4),
                   [{"parking:both": "lane"}, {"parking:left": "no", "parking:right": "street_side"},
                    {"parking:lane:right": "parallel"}, {}])
        self.assertEqual([roads.parking_sides(f, i) for i in range(4)], [(1, -1), (-1,), (-1,), None])


class LayoutTest(unittest.TestCase):
    def test_uneven_split_moves_the_centre_line(self):
        sp = spec(lanes=5, lanes_f=3, lanes_b=2, width=5 * 3.3)
        lay = roads.lane_layout(sp)
        (df, nf, f0, f1), (db, nb, b0, b1) = lay["groups"]
        self.assertEqual((df, nf, db, nb), (1, 3, -1, 2))
        self.assertAlmostEqual(f0, lay["lo"])
        self.assertAlmostEqual(f1 - f0, 3 * (b1 - b0) / 2)          # the forward lanes on the right, 3 of 5
        self.assertAlmostEqual(f1, b0)
        self.assertGreater(f1, 0.0)                                 # 3 of 5 forward: the centre line left of the geometry
        left = roads.lane_layout(sp, left_hand=True)
        self.assertAlmostEqual(left["groups"][0][3], lay["hi"])     # (left-hand: the forward lanes on the left)

    def test_missing_direction_takes_the_rest(self):
        lay = roads.lane_layout(spec(lanes=4, lanes_f=1, width=4 * 3.3))
        self.assertEqual([(g[0], g[1]) for g in lay["groups"]], [(1, 1), (-1, 3)])

    def test_centre_turn_lane(self):
        sp = spec(lanes=5, lanes_c=1, width=5 * 3.3)
        lay = roads.lane_layout(sp)
        c0, c1 = lay["centre"]
        self.assertAlmostEqual(c0, -c1)                             # 2 + 1 + 2: the turn lane in the middle
        self.assertAlmostEqual(c1 - c0, (lay["hi"] - lay["lo"]) / 5)
        marks = roads.lane_marks(sp, STRAIGHT, LON_US)
        mats = [lk["m"] for lk, w, ln in marks]
        self.assertEqual(mats.count("line_yellow"), 2)
        self.assertEqual(mats.count("line_dashed_short_yellow"), 2)
        offs = sorted(float(ln[0, 1]) for lk, w, ln in marks if "yellow" in lk["m"])
        # outer solid, inner dashed on each side
        solid = sorted(float(ln[0, 1]) for lk, w, ln in marks if lk["m"] == "line_yellow")
        self.assertEqual([offs[0], offs[-1]], solid)

    def test_parking_lanes_from_the_tags(self):
        sp = spec(cls="residential", lanes=2, park=(-1,), width=2 * roads.LANE_W_DEFAULT + roads.PARKING_M)
        lay = roads.lane_layout(sp)
        self.assertAlmostEqual(lay["lo"], -sp.width / 2 + roads.PARKING_M)
        self.assertAlmostEqual(lay["hi"], sp.width / 2)            # (urban: no edge strip on the unparked side)
        marks = roads.lane_marks(sp, STRAIGHT, LON_US)
        edge = [ln for lk, w, ln in marks if lk is roads.PARK_EDGE]
        self.assertEqual(len(edge), 1)
        self.assertAlmostEqual(float(edge[0][0, 1]), lay["lo"])


class ArrowTest(unittest.TestCase):
    def test_arrows_before_the_downstream_end(self):
        sp = spec(lanes=4, lanes_f=2, lanes_b=2, width=4 * 3.3, turns_f=(("left",), ("through", "right")),
                  turns_b=(("left",), ("through",)))
        marks = roads.lane_marks(sp, STRAIGHT, LON_US)
        strokes = [ln for lk, w, ln in marks if lk.get("arrow")]
        self.assertTrue(strokes)
        fwd = [s for s in strokes if s[0, 1] < 0]                   # forward lanes: right of the geometry
        bwd = [s for s in strokes if s[0, 1] > 0]
        self.assertTrue(all(150 < s[:, 0].mean() < 195 for s in fwd))     # near x = 200 (the forward end)
        self.assertTrue(all(5 < s[:, 0].mean() < 50 for s in bwd))        # near x = 0
        # the forward lanes' leftmost (nearest the centre) turns left: its bent stroke reaches toward +y
        lane0 = [s for s in fwd if s[:, 1].max() > -1.0 * (sp.width / 4) and len(s) == 2 and abs(s[1, 1] - s[0, 1]) > 1]
        self.assertTrue(lane0)

    def test_no_arrows_on_a_short_road_or_straight_lanes_only(self):
        short = np.c_[np.linspace(0, 20, 3), np.zeros(3), np.full(3, 10.0)]
        sp = spec(lanes=2, lanes_f=1, lanes_b=1, width=2 * 3.3, turns_f=(("left",),))
        self.assertFalse([1 for lk, w, ln in roads.lane_marks(sp, short, LON_US) if lk.get("arrow")])
        sp2 = spec(lanes=4, lanes_f=2, lanes_b=2, width=4 * 3.3, turns_f=(("through",), ("through",)))
        self.assertFalse([1 for lk, w, ln in roads.lane_marks(sp2, STRAIGHT, LON_US) if lk.get("arrow")])

    def test_stroke_shapes(self):
        p = np.array([0.0, 0.0, 5.0])
        s = roads.arrow_strokes(p, (1.0, 0.0), ("through",))
        self.assertEqual(len(s), 2)                                 # shaft + head
        s = roads.arrow_strokes(p, (1.0, 0.0), ("left", "through"))
        self.assertEqual(len(s), 4)
        self.assertTrue(all(np.allclose(x[:, 2], 5.0) for x in s))


class SpecsTest(unittest.TestCase):
    def test_osm_lanes_on_the_specs(self):
        lines = [[(0, 0), (200, 0)], [(0, 50), (200, 50)], [(0, 100), (200, 100)]]
        f = features(lines, ["primary", "primary", "residential"])
        f.attrs["osm_lanes"] = np.array([5.0, np.nan, np.nan])
        f = tagged(f, [{"lanes:forward": "3", "lanes:backward": "2", "turn:lanes:forward": "left|through|through"},
                       {}, {"parking:right": "lane", "parking:left": "no"}])
        sps = roads.road_specs(f, None)
        self.assertEqual((sps[0].lanes_f, sps[0].lanes_b, sps[0].turns_f[0]), (3, 2, ("left",)))
        self.assertFalse(roads.has_lane_tags(sps[1]))
        self.assertEqual(sps[2].park, (-1,))

    def test_untagged_roads_unchanged(self):
        """Without the regional columns the specs and markings are the old ones."""
        f = features([[(0, 0), (200, 0)]], ["primary"])
        sp = roads.road_specs(f, None)[0]
        self.assertFalse(roads.has_lane_tags(sp))
        ex = roads.extras(sp, STRAIGHT, LON_US)
        self.assertFalse([1 for lk, w, ln in ex if lk.get("arrow") or lk["m"] == "line_dashed_short_yellow"])

    def test_tile_draws_whole_arrows(self):
        f = features([[(-50, 20), (230, 20)]], ["primary"])
        f.attrs["osm_lanes"] = np.array([4.0])
        f = tagged(f, [{"lanes:forward": "2", "lanes:backward": "2", "turn:lanes:forward": "left|through",
                        "turn:lanes:backward": "left|through"}])
        chunks = roads.build_tile(FRAME, 0, 0, [f], flat)
        arrows = [c for c in chunks if c["m"] == "line_white" and abs(c["w"] - roads.ARROW["w"]) < 1e-6]
        self.assertTrue(arrows)                       # the forward lanes' (before x = 230; the backward ones' end is off it)
        for c in arrows:
            x0, y0 = c["n"][0][:2]
            self.assertTrue(0 <= x0 < 256 and 0 <= y0 < 256)


if __name__ == "__main__":
    unittest.main()
