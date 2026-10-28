"""Turn pockets and width tapers (roads.pocket_profiles, widen_off / widen_edges, and their use in lane_marks, extras,
build_tile, roadmesh.ribbons, surface._road_segments and grading.widen_plan; feature turn-pockets)."""
import json
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import grading  # noqa: E402
import roadmesh  # noqa: E402
import roads  # noqa: E402
import surface  # noqa: E402
from test_roads import FRAME, LAT, LON, M_LAT, M_LON, features, flat  # noqa: E402
from test_roads import rules as test_roads_rules  # noqa: E402
from test_turn_lanes import LON_US, tagged  # noqa: E402


def road(lines, classes, names, widths=(), lanes=None, rows=None, ids=None):
    """Features of LINE roads with names, width rules ([(i, m)]), OSM lane counts and regional tags (rows)."""
    f = features(lines, classes, widths=widths, ids=ids)
    f.attrs["name"] = np.array(names)
    if lanes is not None:
        f.attrs["osm_lanes"] = np.array([np.nan if v is None else float(v) for v in lanes])
    if rows is not None:
        f = tagged(f, rows)
    return f


def profiles(*cells):
    return roads.pocket_profiles(list(cells))


def edges_at(wd, s):
    el, er = roads.widen_edges(wd, np.atleast_1d(np.asarray(s, np.float64)))
    return float(el[0]), float(er[0])


class TaperTest(unittest.TestCase):
    """(b): the wider of two segments of one road starts from the other's width at their joint."""

    def two(self, wa=8.6, wb=11.9, names=("Main", "Main"), extra=()):
        lines = [[(-100, 0), (0, 0)], [(0, 0), (150, 0)]] + [ln for ln, _c in extra]
        return road(lines, ["primary", "primary"] + [c for _ln, c in extra], list(names) + ["Cross"] * len(extra),
                    widths=[(0, wa), (1, wb)])

    def test_wider_tapers_from_the_narrower(self):
        f = self.two()
        sp = roads.road_specs(f, None)
        p = profiles(f)
        self.assertEqual(set(p), {"id001"})                   # the wider one only
        wd = p["id001"]
        dw = sp[1].width - sp[0].width
        el, er = edges_at(wd, 0.0)
        self.assertAlmostEqual(el, -dw / 2, places=6)         # both edges: the narrower's at the joint
        self.assertAlmostEqual(er, -dw / 2, places=6)
        self.assertAlmostEqual(edges_at(wd, roads.POCKET_TAPER_M / 2)[0], -dw / 4, places=6)
        self.assertEqual(edges_at(wd, roads.POCKET_TAPER_M + 1.0), (0.0, 0.0))
        self.assertEqual(edges_at(wd, 149.0), (0.0, 0.0))

    def test_not_across_a_junction_or_a_name_change_or_a_small_step(self):
        cross = ([(0, -60), (0, 0), (0, 60)], "primary")       # a cross street of its rank through the joint
        self.assertEqual(profiles(self.two(extra=[cross])), {})
        ends = ([(0, -60), (0, 0)], "trunk")                   # ... or a higher one ending there
        self.assertEqual(profiles(self.two(extra=[ends])), {})
        self.assertIn("id001", profiles(self.two(extra=[([(0, 0), (0, 40)], "service")])))   # a driveway: it goes on
        side = ([(0, -60), (0, 0), (0, 60)], "residential")
        self.assertEqual(profiles(self.two(extra=[side])), {})  # (a width of no known lane: the junction's step)
        self.assertEqual(profiles(self.two(names=("Main", "Other"))), {})
        self.assertEqual(profiles(self.two(wb=8.9)), {})
        bend = road([[(-100, 0), (0, 0)], [(0, 0), (60, 80)]], ["primary"] * 2, ["Main"] * 2,
                    widths=[(0, 8.6), (1, 11.9)])
        self.assertEqual(profiles(bend), {})                   # (a turn, not straight on)

    def test_short_segment_tapers_within_itself(self):
        f = road([[(-100, 0), (0, 0)], [(0, 0), (12, 0)]], ["primary"] * 2, ["Main"] * 2, widths=[(0, 8.6), (1, 11.9)])
        wd = profiles(f)["id001"]
        self.assertLess(edges_at(wd, 0.0)[0], -1.0)
        self.assertAlmostEqual(edges_at(wd, wd.L)[0], 0.0, places=6)

    def test_pocket_way_opens_its_turn_lane(self):
        """A pocket mapped as its own way (Colorado's): the left-turn lane opens, the rest shift over; the edges match
        the narrower segment's at the joint."""
        rows = [{"lanes:forward": "1", "lanes:backward": "1"},
                {"lanes:forward": "2", "lanes:backward": "1", "turn:lanes:forward": "left|through"}]
        f = road([[(-100, 0), (0, 0)], [(0, 0), (150, 0)]], ["primary"] * 2, ["Main"] * 2, lanes=[2, 3], rows=rows)
        sp = roads.road_specs(f, None)
        dw = sp[1].width - sp[0].width
        self.assertGreater(dw, 2.5)
        wd = profiles(f)["id001"]
        self.assertEqual(sorted({c[0] for c in wd.comps}), ["ins", "mid"])
        el, er = edges_at(wd, 0.0)
        self.assertAlmostEqual(el, -dw / 2, places=6)
        self.assertAlmostEqual(er, -dw / 2, places=6)
        lay = roads.lane_layout(sp[1])
        (d, n, g0, g1), = [g for g in lay["groups"] if g[0] > 0]
        # at the joint the pocket (the forward lane next to the centre line) is closed: its two edges meet
        s0 = np.zeros(1)
        lw = (g1 - g0) / n
        inner = roads.widen_off(wd, s0, g1 - lw)[0]
        outer = roads.widen_off(wd, s0, g1)[0]
        self.assertAlmostEqual(inner, outer, places=6)
        self.assertAlmostEqual(outer, 0.0, places=6)           # where the narrower one's centre line is
        far = np.array([100.0])
        self.assertAlmostEqual(roads.widen_off(wd, far, g1)[0], g1)

    def test_pocket_way_from_a_side_street_not_into_its_junction(self):
        """The pocket way starts at a side street (it opens there); where its turn arrows end (the junction it leads
        to) it keeps its width, whatever the road beyond."""
        rows = [{"lanes:forward": "1", "lanes:backward": "1"},
                {"lanes:forward": "2", "lanes:backward": "1", "turn:lanes:forward": "left|through"},
                {"lanes:forward": "1", "lanes:backward": "1"}]
        lines = [[(-100, 0), (0, 0)], [(0, 0), (150, 0)], [(150, 0), (300, 0)], [(0, -60), (0, 0), (0, 60)]]
        f = road(lines, ["primary"] * 3 + ["residential"], ["Main"] * 3 + ["Side"], lanes=[2, 3, 2, None],
                 rows=rows + [{}])
        wd = profiles(f)["id001"]
        self.assertLess(edges_at(wd, 0.0)[0], -1.0)            # opens from the side street ...
        self.assertEqual(edges_at(wd, wd.L), (0.0, 0.0))       # ... not narrowed into the junction at x = 150


class PocketTest(unittest.TestCase):
    """(a): an exclusive turn lane the lane count leaves out gets a pocket at its downstream end."""

    def one(self, row, lanes=2, length=200.0):
        return road([[(0, 0), (length, 0)]], ["primary"], ["Main"], lanes=[lanes], rows=[row])

    def test_left_pocket_forward_at_the_end(self):
        f = self.one({"lanes:forward": "1", "lanes:backward": "1", "turn:lanes:forward": "left|through"})
        sp = roads.road_specs(f, None)[0]
        lay = roads.lane_layout(sp)
        lw = (lay["hi"] - lay["lo"]) / 2
        wd = profiles(f)["id000"]
        L = wd.L
        self.assertAlmostEqual(L, 200.0, delta=0.5)
        self.assertEqual(edges_at(wd, 0.0), (0.0, 0.0))
        el, er = edges_at(wd, L - 1.0)
        self.assertAlmostEqual(el, lw, places=6)               # toward the centre line: the other way's side grows
        self.assertEqual(er, 0.0)
        self.assertAlmostEqual(edges_at(wd, L - roads.POCKET_M)[0], lw, places=6)
        self.assertAlmostEqual(edges_at(wd, L - roads.POCKET_M - roads.POCKET_TAPER_M / 2)[0], lw / 2, places=6)
        self.assertEqual(edges_at(wd, L - roads.POCKET_M - roads.POCKET_TAPER_M - 1.0), (0.0, 0.0))
        self.assertEqual(len(wd.added), 1)

    def test_backward_pocket_at_the_start(self):
        f = self.one({"lanes:forward": "1", "lanes:backward": "1", "turn:lanes:backward": "left|through"})
        wd = profiles(f)["id000"]
        el, er = edges_at(wd, 1.0)
        self.assertEqual(el, 0.0)
        self.assertGreater(er, 2.5)                            # toward the centre line from the left side: right edge
        self.assertEqual(edges_at(wd, wd.L - 1.0), (0.0, 0.0))

    def test_right_bay_toward_the_curb(self):
        f = self.one({"lanes:forward": "1", "lanes:backward": "1", "turn:lanes:forward": "through|right"})
        wd = profiles(f)["id000"]
        el, er = edges_at(wd, wd.L - 1.0)
        self.assertEqual(el, 0.0)
        self.assertGreater(er, 2.5)

    def test_short_way_opens_within_itself(self):
        f = self.one({"lanes:forward": "1", "lanes:backward": "1", "turn:lanes:forward": "left|through"}, length=40.0)
        wd = profiles(f)["id000"]
        T = roads.POCKET_SHORT_TAPER * wd.L
        self.assertEqual(edges_at(wd, 0.0), (0.0, 0.0))                     # (no step where its way starts)
        self.assertGreater(edges_at(wd, T / 2)[0], 1.0)
        self.assertGreater(edges_at(wd, T + 0.5)[0], 2.5)
        self.assertGreater(edges_at(wd, wd.L)[0], 2.5)
        f = self.one({"lanes:forward": "1", "lanes:backward": "1", "turn:lanes:backward": "left|through"}, length=40.0)
        wd = profiles(f)["id000"]
        self.assertGreater(edges_at(wd, 0.0)[1], 2.5)                       # (backward: from its start)
        self.assertEqual(edges_at(wd, wd.L), (0.0, 0.0))

    def test_lanes_counted_or_centre_turn_lane_no_pocket(self):
        counted = self.one({"lanes:forward": "2", "lanes:backward": "1", "turn:lanes:forward": "left|through"}, lanes=3)
        self.assertEqual(profiles(counted), {})                # (the turn lane is in the width already)
        ctl = self.one({"lanes:forward": "1", "lanes:backward": "1", "lanes:both_ways": "1",
                        "turn:lanes:forward": "left|through"}, lanes=3)
        self.assertEqual(profiles(ctl), {})

    def test_marks_follow_the_pocket(self):
        f = self.one({"lanes:forward": "1", "lanes:backward": "1", "turn:lanes:forward": "left|through"})
        sp = roads.road_specs(f, None)[0]
        wd = profiles(f)["id000"]
        ring = f.parts(0)[0][0]
        lonlat, s = roads.widen_nodes(wd, ring, roads.widen_s(wd, ring))
        e = (lonlat[:, 0] - LON) * M_LON
        n = (lonlat[:, 1] - LAT) * M_LAT
        pts = np.c_[e, n, np.full(len(e), 10.0)]
        plain = roads.lane_marks(sp, pts, LON_US)
        marks = roads.lane_marks(sp, pts, LON_US, wid=(wd, s))
        lay = roads.lane_layout(sp)
        lw = (lay["hi"] - lay["lo"]) / 2
        yellow = [ln for lk, w, ln in marks if lk["m"] == "line_yellow"]
        self.assertEqual(len(yellow), 2)
        for ln in yellow:                                      # the centre line moved over by the pocket at the end
            self.assertAlmostEqual(float(ln[-1, 1]) - float(ln[0, 1]), lw, delta=1e-6)
        lines = [ln for lk, w, ln in marks if lk is roads.LANE_LINE]
        self.assertEqual(len(lines), 1)                        # the pocket's own lane line ...
        self.assertGreater(float(lines[0][0, 0]), 200.0 - roads.POCKET_M - roads.POCKET_TAPER_M)
        self.assertAlmostEqual(float(lines[0][-1, 1]), float(yellow[0][0, 1]) + roads.CENTRE["gap"], delta=0.3)
        arrows = [ln for lk, w, ln in marks if lk.get("arrow")]
        self.assertTrue(arrows)                                # ... and both lanes' arrows (none before: 2 turns)
        self.assertFalse([1 for lk, w, ln in plain if lk.get("arrow")])
        left = [a for a in arrows if a[:, 1].mean() > 0.2]
        self.assertTrue(left)                                  # the left arrow in the pocket (left of the old centre)
        self.assertTrue(all(abs(a[:, 1].mean() - (float(yellow[0][-1, 1]) - lw / 2)) < 1.6 for a in left))


class PartsTest(unittest.TestCase):
    """A segment Overture merged from several OSM ways (osm_parts: osmtags.join_parts)."""

    def merged(self, parts, lanes, row):
        f = road([[(0, 0), (200, 0)]], ["primary"], ["Main"], lanes=[lanes], rows=[row])
        f.attrs["osm_parts"] = np.array([json.dumps(parts)])
        return f

    def test_pocket_over_its_way_share(self):
        """The approach (2 lanes) and the pocket way (its last 30 %, 3 lanes, left|through forward) merged: the pocket
        over the pocket way's 60 m, its taper on the approach; the pocket way's extra lane is the pocket, no step."""
        f = self.merged([[0, 0.7, 2], [0.7, 1, 3]], 2,
                        {"lanes:forward": "1", "lanes:backward": "1", "turn:lanes:forward": "left|through"})
        wd = profiles(f)["id000"]
        lw = edges_at(wd, wd.L)[0]
        self.assertGreater(lw, 2.5)
        self.assertAlmostEqual(edges_at(wd, wd.L * 0.7 + 1.0)[0], lw)                      # (full from its way on)
        self.assertAlmostEqual(edges_at(wd, wd.L * 0.7 - roads.POCKET_TAPER_M / 2)[0], lw / 2, places=6)
        self.assertEqual(edges_at(wd, wd.L * 0.7 - roads.POCKET_TAPER_M - 1.0), (0.0, 0.0))
        self.assertEqual([c[0] for c in wd.comps], ["ins"])                                 # (no (d) step)

    def test_lanes_by_part(self):
        """Two ways of 2 and 4 lanes merged (no turn lanes): the 4-lane half wider by two lanes, opening over the taper
        inside it."""
        f = self.merged([[0, 0.5, 2], [0.5, 1, 4]], 2, {})
        wd = profiles(f)["id000"]
        lw = roads._lane_width(roads.road_specs(f, None)[0])
        self.assertEqual(edges_at(wd, 50.0), (0.0, 0.0))
        el, er = edges_at(wd, 100.0 + roads.POCKET_TAPER_M + 1.0)
        self.assertAlmostEqual(el + er, 2 * lw, places=6)
        self.assertAlmostEqual(el, er, places=6)
        self.assertAlmostEqual(sum(edges_at(wd, 100.0 + roads.POCKET_TAPER_M / 2)), lw, places=6)

    def marks(self, f, wd):
        sp = roads.road_specs(f, None)[0]
        ring = f.parts(0)[0][0]
        lonlat, s = roads.widen_nodes(wd, ring, roads.widen_s(wd, ring))
        pts = np.c_[(lonlat[:, 0] - LON) * M_LON, (lonlat[:, 1] - LAT) * M_LAT, np.full(len(s), 10.0)]
        return sp, s, roads.extras(sp, pts, LON_US, wid=(wd, s))

    def test_each_part_draws_its_own_lanes(self):
        """2 lanes, then 4: the 4-lane part's two lane lines start in its taper where the new lanes are 2/3 wide; the
        centre line goes on through."""
        f = self.merged([[0, 0.5, 2], [0.5, 1, 4]], 2, {})
        wd = profiles(f)["id000"]
        self.assertEqual([p[2] for p in wd.parts], [2, 4])
        (sa, sb, kn, kw), = wd.ptapers
        self.assertEqual((kn, kw), (0, 1))
        sp, s, ex = self.marks(f, wd)
        lines = [ln for lk, w, ln in ex if lk is roads.LANE_LINE]
        self.assertEqual(len(lines), 2)
        start = sa + (sb - sa) * roads.POCKET_LINE_SHARE
        for ln in lines:
            self.assertGreater(ln[0, 0], start - roads.POCKET_STEP_M - 0.5)
            self.assertLess(ln[0, 0], start + roads.POCKET_STEP_M + 0.5)
            self.assertAlmostEqual(ln[-1, 0], 200.0, delta=0.5)
        yellow = [ln for lk, w, ln in ex if lk["m"] == "line_yellow"]
        self.assertEqual(len(yellow), 2)
        self.assertTrue(all(ln[0, 0] < 1.0 and ln[-1, 0] > 199.0 for ln in yellow))

    def test_arrows_in_the_end_parts_lanes(self):
        """The last way's turn lanes (3 forward) fit its part's lanes (4: 3 forward, 1 back), not the segment's (2)."""
        f = self.merged([[0, 0.5, 2], [0.5, 1, 4]], 2,
                        {"lanes:forward": "1", "lanes:backward": "1", "turn:lanes:forward": "left|through|through"})
        wd = profiles(f)["id000"]
        sp, s, ex = self.marks(f, wd)
        arrows = [ln for lk, w, ln in ex if lk.get("arrow")]
        self.assertTrue(arrows)
        lw = roads._lane_width(roads._part_spec(sp, 4, wd.parts[1][3], 3))
        left = [a for a in arrows if len(a) == 2 and abs(a[1, 1] - a[0, 1]) > 1.0]  # (the left arrow's bent stroke)
        self.assertTrue(left)
        self.assertTrue(all(a[:, 1].mean() > -lw for a in left))           # (the leftmost forward lane: by the centre)

    def test_one_way_segments_and_unknown_lanes_unchanged(self):
        self.assertEqual(profiles(self.merged([[0, 0.5, None], [0.5, 1, 2]], 2, {})), {})
        f = road([[(0, 0), (200, 0)]], ["primary"], ["Main"], lanes=[2], rows=[{}])
        f.attrs["osm_parts"] = np.array([""])
        self.assertEqual(profiles(f), {})
        self.assertIsNone(roads.osm_parts(f, 0))


class CentreLaneTest(unittest.TestCase):
    """(b): a centre turn lane (CDOT's painted median: lanes_c) opening where the road goes on from a narrower piece."""

    def test_centre_lane_opens_from_a_side_street(self):
        rows = [{"lanes:forward": "1", "lanes:backward": "1"},
                {"lanes:forward": "1", "lanes:backward": "1", "lanes:both_ways": "1"}]
        lines = [[(-100, 0), (0, 0)], [(0, 0), (200, 0)], [(0, -60), (0, 0), (0, 60)]]
        f = road(lines, ["primary"] * 2 + ["residential"], ["Main"] * 2 + ["Side"], lanes=[2, 3, None],
                 rows=rows + [{}])
        sp = roads.road_specs(f, None)
        wd = profiles(f)["id001"]
        dw = sp[1].width - sp[0].width
        self.assertEqual(edges_at(wd, 0.0), (-dw / 2, -dw / 2))
        c0, c1 = roads.lane_layout(sp[1])["centre"]
        s0 = np.zeros(1)
        self.assertAlmostEqual(roads.widen_off(wd, s0, c0)[0], roads.widen_off(wd, s0, c1)[0], places=6)  # (closed)
        self.assertAlmostEqual(roads.widen_off(wd, np.array([150.0]), c1)[0], c1)

    def test_not_across_a_junction_of_its_rank(self):
        rows = [{"lanes:forward": "1", "lanes:backward": "1"},
                {"lanes:forward": "1", "lanes:backward": "1", "lanes:both_ways": "1"}]
        lines = [[(-100, 0), (0, 0)], [(0, 0), (200, 0)], [(0, -60), (0, 0), (0, 60)]]
        f = road(lines, ["primary"] * 2 + ["secondary"], ["Main"] * 2 + ["Cross"], lanes=[2, 3, None],
                 rows=rows + [{}])
        wd = profiles(f).get("id001")
        self.assertTrue(wd is None or not wd.comps)             # (no taper; (c): its centre lane's arrow there)


class ApproachTest(unittest.TestCase):
    """(c): the approaches to a junction of major roads where OpenStreetMap gives no turn lanes."""

    def setUp(self):
        self.was = roads.POCKET_INFER_ON

    def tearDown(self):
        roads.POCKET_INFER_ON = self.was

    def junction(self, lanes, rows, beyond=None, cross="secondary", regional=True, link=False):
        """Main (id000) from x = -200 to the junction at x = 0, crossed there by Cross (id001); Main going on beyond
        it (id002) with `beyond` lanes when given."""
        lines = [[(-200, 0), (0, 0)], [(0, -80), (0, 0), (0, 80)]]
        classes, names, ln, rw = ["primary", cross], ["Main", "Cross"], [lanes, None], [rows, {}]
        if beyond is not None:
            lines.append([(0, 0), (200, 0)])
            classes.append("primary")
            names.append("Main")
            ln.append(beyond[0])
            rw.append(beyond[1])
        f = road(lines, classes, names, lanes=ln, rows=rw if regional else None)
        if link:
            f.rules["subclass"] = test_roads_rules([(1, "link")])
        return f

    def test_inferred_left_pocket_on_a_four_lane_approach(self):
        f = self.junction(4, {})
        wd = profiles(f)["id000"]
        self.assertGreater(edges_at(wd, wd.L - 1.0)[0], 2.5)    # forward (eastbound) arrives at x = 0: its end
        self.assertEqual(edges_at(wd, 1.0), (0.0, 0.0))
        self.assertEqual([(d, t[0]) for d, t in wd.turns], [(1, ("left",))])
        self.assertEqual(len(wd.added), 1)
        sp = roads.road_specs(f, None)[0]
        ring = f.parts(0)[0][0]
        lonlat, s = roads.widen_nodes(wd, ring, roads.widen_s(wd, ring))
        pts = np.c_[(lonlat[:, 0] - LON) * M_LON, (lonlat[:, 1] - LAT) * M_LAT, np.full(len(s), 10.0)]
        ex = roads.extras(sp, pts, LON_US, wid=(wd, s))
        self.assertTrue([1 for lk, w, ln in ex if lk.get("arrow")])     # (its left arrow in the pocket)

    def test_no_pocket_on_two_lanes_or_without_a_major_road_or_outside_the_tables(self):
        self.assertEqual(profiles(self.junction(2, {})), {})
        self.assertEqual(profiles(self.junction(4, {}, cross="residential")), {})
        self.assertEqual(profiles(self.junction(4, {}, regional=False)), {})
        self.assertEqual(profiles(self.junction(4, {}, link=True)), {})     # (a slip road is no cross street)
        roads.POCKET_INFER_ON = False
        self.assertEqual(profiles(self.junction(4, {})), {})

    def test_a_lane_more_than_goes_on_is_the_turn_lane(self):
        rows = {"lanes:forward": "3", "lanes:backward": "2"}
        f = self.junction(5, rows, beyond=(4, {"lanes:forward": "2", "lanes:backward": "2"}))
        wd = profiles(f)["id000"]
        self.assertFalse(wd.comps)                              # (no widening: it is counted)
        self.assertEqual(dict(wd.turns)[1], (("left",), (), ()))

    def test_centre_turn_lane_gets_its_arrow(self):
        rows = {"lanes:forward": "1", "lanes:backward": "1", "lanes:both_ways": "1"}
        wd = profiles(self.junction(3, rows))["id000"]
        self.assertFalse(wd.comps)
        self.assertIn((1, "C"), wd.turns)


class MeshTest(unittest.TestCase):
    def test_ribbon_without_profile_unchanged(self):
        c = {"m": "road_asphalt_2lane", "w": 7.0, "tl": 11.0, "p": 20,
             "n": [[0.0, 0.0, 1.0], [20.0, 3.0, 1.0], [45.0, 3.0, 1.0]]}
        (left, right, v, m, layer, z), = roadmesh.ribbons([c])
        xy, s, _z = roadmesh._resample(np.asarray(c["n"]), roadmesh.DRAPE_STEP)
        nrm = roadmesh._normals(xy) * 3.5
        self.assertTrue(np.array_equal(left, xy + nrm) and np.array_equal(right, xy - nrm))

    def test_ribbon_per_node_widths(self):
        c = {"m": "road_asphalt_2lane", "w": 7.0, "tl": 11.0, "p": 20,
             "n": [[0.0, 0.0, 1.0], [40.0, 0.0, 1.0]], "wl": [3.5, 6.5], "wr": [3.5, 3.5]}
        (left, right, *_), = roadmesh.ribbons([c])
        self.assertAlmostEqual(left[0, 1], 3.5)
        self.assertAlmostEqual(left[-1, 1], 6.5)
        self.assertAlmostEqual(left[len(left) // 2, 1], 5.0)
        self.assertTrue(np.allclose(right[:, 1], -3.5))
        far = roadmesh._reach_past(c, (5.0, -50.0, 30.0, 50.0))
        self.assertEqual(len(far["wl"]), len(far["n"]))


class TileTest(unittest.TestCase):
    def setUp(self):
        self.was = roads.ROADS_IN_GROUND
        roads.ROADS_IN_GROUND = False

    def tearDown(self):
        roads.ROADS_IN_GROUND = self.was

    def cells(self):
        """A taper across the seam of tiles 0 and 1 (x = 256): Main narrows to 8.6 m at x = 250."""
        lines = [[(100, 20), (250, 20)], [(250, 20), (450, 20)]]
        both = road(lines, ["primary"] * 2, ["Main"] * 2, widths=[(0, 8.6), (1, 11.9)])
        only_b = road(lines[1:], ["primary"], ["Main"], widths=[(0, 11.9)], ids=["id001"])
        return both, only_b

    @staticmethod
    def left_edge_at(chunks, x):
        for c in chunks:
            if c["m"].startswith("road_") and c.get("wl") is not None:
                n = np.asarray(c["n"])
                if n[0, 0] <= x <= n[-1, 0]:
                    return float(np.interp(x, n[:, 0], n[:, 1] + np.asarray(c["wl"])))
        return None

    def test_both_tiles_draw_the_same_taper(self):
        both, only_b = self.cells()
        t0 = roads.build_tile(FRAME, 0, 0, [both], flat)
        # tile 1's own cell holds only the wider segment; the cells within reach of it hold both
        t1 = roads.build_tile(FRAME, 1, 0, [only_b], flat, pocket_cells=[only_b, both])
        a, b = self.left_edge_at(t0, 257.0), self.left_edge_at(t1, 257.0)
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertAlmostEqual(a, b, places=2)
        self.assertLess(a, 20 + 11.9 / 2 - 1.0)                # (narrower than its width there: the taper)
        self.assertEqual(roads.pocket_profiles([only_b, both]).keys(), roads.pocket_profiles([both, only_b]).keys())

    def test_roads_of_one_width_unchanged(self):
        f = road([[(-20, 30), (280, 30)]], ["primary"], ["Main"], widths=[(0, 8.6)])
        chunks = roads.build_tile(FRAME, 0, 0, [f], flat)
        self.assertFalse([c for c in chunks if "wl" in c])


class GroundTest(unittest.TestCase):
    def test_photo_segments_follow_the_pocket(self):
        f = road([[(0, 100), (200, 100)]], ["primary"], ["Main"], lanes=[2],
                 rows=[{"lanes:forward": "1", "lanes:backward": "1", "turn:lanes:forward": "left|through"}])
        segs, hws, _k = surface._road_segments(FRAME, -50.0, 0.0, 300.0, 300, 300, [f])
        self.assertGreater(hws.max() - hws.min(), 1.2)          # (1 px a metre: half the pocket's lane)
        plain = road([[(0, 100), (200, 100)]], ["primary"], ["Main"], lanes=[2])
        segs, hws, _k = surface._road_segments(FRAME, -50.0, 0.0, 300.0, 300, 300, [plain])
        self.assertEqual(len(set(hws.tolist())), 1)

    def test_grading_core_out_to_the_pocket(self):
        f = road([[(0, 0), (200, 0)]], ["primary"], ["Main"], lanes=[2],
                 rows=[{"lanes:forward": "1", "lanes:backward": "1", "turn:lanes:forward": "left|through"}])
        wd = profiles(f)["id000"]
        p = grading.Plan(np.array([[0.0, 0.0], [200.0, 0.0]]), np.zeros(2), 4.0 + 0.5, road_half=4.0)
        grading.widen_plan(p, wd)
        self.assertGreater(p.half, 4.0 + 2.5)
        self.assertGreater(p.bbox[3], p.half)


if __name__ == "__main__":
    unittest.main()
