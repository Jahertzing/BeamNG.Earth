"""navgraph: the drivable roads as the stock map's graph: connectors shared at junctions, one-way direction,
lanes, speed limits, clipped to the window."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import navgraph  # noqa: E402
import roads  # noqa: E402
from test_roads import FRAME, features, flat, rules  # noqa: E402


def graph(lines, classes, connectors=(), oneway=(), speeds=(), subclass=(), centre=(0.0, 0.0), window=1100.0):
    f = features(lines, classes)
    f.rules["connectors"] = rules(list(connectors))
    f.rules["oneway"] = rules(list(oneway), "float")
    f.rules["max_speed_kmh"] = rules(list(speeds), "float")
    f.rules["subclass"] = rules(list(subclass))
    return navgraph.build(FRAME, [f], flat, centre, window=window)


class GraphTest(unittest.TestCase):
    def test_junction_shares_the_connector(self):
        g = graph([[(0, 0), (100, 0)], [(100, 0), (100, 100)]], ["residential", "residential"],
                  connectors=[(0, "A", 0.0, 0.0), (0, "J", 1.0, 1.0), (1, "J", 0.0, 0.0), (1, "B", 1.0, 1.0)])
        self.assertIn("cJ", g["nodes"])
        links = [e for e in g["edges"] if "cJ" in e[:2]]
        self.assertEqual(len(links), 2, "both roads end at the junction node")
        x, y, z, r = g["nodes"]["cJ"]
        self.assertAlmostEqual(x, 100.0, delta=0.5)
        self.assertAlmostEqual(y, 0.0, delta=0.5)
        self.assertEqual(z, 10.0)
        for e in g["edges"]:
            self.assertEqual((e[3], e[4]), (1, 1), "a two-way residential street: one lane each way")
            self.assertAlmostEqual(e[5], 25 * navgraph.MPH, places=2)

    def test_long_edges_split_and_oneway_against_the_geometry(self):
        g = graph([[(0, 0), (300, 0)]], ["secondary"], connectors=[(0, "A", 0.0, 0.0), (0, "B", 1.0, 1.0)],
                  oneway=[(0, -1.0)], speeds=[(0, 72.0)])
        self.assertGreaterEqual(len(g["edges"]), 4, "300 m in edges of at most MAX_EDGE_M")
        for e in g["edges"]:
            self.assertEqual(e[2], 1)
            self.assertLess(g["nodes"][e[1]][0], g["nodes"][e[0]][0], "one-way -1: drives against the geometry")
            self.assertEqual((e[3], e[4]), (e[3], 0))
            self.assertAlmostEqual(e[5], 20.0, places=2)
        self.assertTrue(any(e[1] == "cA" for e in g["edges"]))

    def test_ramp_edges_are_flagged(self):
        # (the mod's merge assist finds on-ramps by it: edge[8], Overture's subclass "link")
        g = graph([[(0, 0), (100, 0)], [(-80, -40), (0, 0)]], ["motorway", "motorway"],
                  connectors=[(0, "J", 0.0, 0.0), (0, "B", 1.0, 1.0), (1, "R", 0.0, 0.0), (1, "J", 1.0, 1.0)],
                  oneway=[(0, 1.0), (1, 1.0)], subclass=[(1, "link")])
        main = [e for e in g["edges"] if e[1] == "cB" or e[0] == "cJ"]
        ramp = [e for e in g["edges"] if e[1] == "cJ"]
        self.assertTrue(main and ramp)
        self.assertTrue(all(len(e) == 9 for e in g["edges"]))
        self.assertTrue(all(e[8] == 0 for e in main))
        self.assertTrue(all(e[8] == 1 for e in ramp))

    def test_exit_ramps_to_streets_are_less_drivable(self):
        # motorway A -> J -> B (one-way); an exit ramp J -> X to a two-way street S1 - S2; an on-ramp O -> K joining the
        # motorway at K (K on the motorway: J -> K -> B); a ramp from J to another motorway M1 -> M2
        lines = [[(0, 0), (200, 0)], [(200, 0), (400, 0)], [(200, 0), (300, -60)], [(300, -100), (300, 100)],
                 [(150, -80), (200, 0)], [(200, 0), (300, 60)], [(300, 60), (500, 60)]]
        classes = ["motorway", "motorway", "motorway", "residential", "motorway", "motorway", "motorway"]
        connectors = [(0, "A", 0.0, 0.0), (0, "J", 1.0, 1.0), (1, "J", 0.0, 0.0), (1, "B", 1.0, 1.0),
                      (2, "J", 0.0, 0.0), (2, "X", 1.0, 1.0), (3, "S1", 0.0, 0.0), (3, "X", 0.4, 0.4),
                      (3, "S2", 1.0, 1.0), (4, "O", 0.0, 0.0), (4, "J", 1.0, 1.0),
                      (5, "J", 0.0, 0.0), (5, "M1", 1.0, 1.0), (6, "M1", 0.0, 0.0), (6, "M2", 1.0, 1.0)]
        g = graph(lines, classes, connectors=connectors,
                  oneway=[(0, 1.0), (1, 1.0), (2, 1.0), (4, 1.0), (5, 1.0), (6, 1.0)],
                  subclass=[(2, "link"), (4, "link"), (5, "link")])
        def piece(e):       # which feature an edge is on (its shape-point nodes are named s<feature id>_...)
            for nm in e[:2]:
                if nm.startswith("s"):
                    return nm[1:].split("_")[0]
            return None
        exit_edges = [e for e in g["edges"] if piece(e) == "id002"]
        self.assertTrue(exit_edges)
        self.assertTrue(all(e[6] == navgraph.EXIT_DRIVABILITY for e in exit_edges), exit_edges)
        onramp = [e for e in g["edges"] if piece(e) == "id004"]
        connector = [e for e in g["edges"] if piece(e) == "id005"]
        self.assertTrue(onramp and all(e[6] == 1.0 for e in onramp), "an on-ramp keeps the motorway's drivability")
        self.assertTrue(connector and all(e[6] == 1.0 for e in connector), "a motorway-to-motorway ramp too")
        self.assertTrue(all(e[6] == 1.0 for e in g["edges"] if e[8] == 0 and e[0] in ("cA", "cJ")))

    def test_not_drivable_and_window(self):
        g = graph([[(0, 0), (50, 0)], [(0, 10), (50, 10)], [(0, 20), (50, 20)], [(5000, 0), (5100, 0)]],
                  ["footway", "service", "residential", "residential"], subclass=[(1, "parking_aisle")])
        ys = {round(n[1]) for n in g["nodes"].values()}
        self.assertEqual(ys, {20}, "only the residential street in the window")

    def test_even_lanes_and_no_parking_lanes(self):
        import roads
        for cls in ("residential", "tertiary", "secondary", "primary"):
            sp = roads.Spec(roads.carriageway(cls, "paved", True), "paved", True, cls, False)
            w = navgraph.driving_width(sp)
            self.assertLessEqual(w, sp.width)
            lo, li = navgraph._lanes(w, False)
            self.assertEqual(lo, li, cls)

    def test_rural_motorway_two_lanes(self):
        import roads
        for urban, n in ((False, 2), (True, 3)):
            sp = roads.Spec(roads.carriageway("motorway", "paved", urban, None, True), "paved", urban, "motorway", True)
            self.assertEqual(navgraph._lanes(navgraph.driving_width(sp), True), (n, 0), urban)
        ramp = roads.Spec(roads.LINK_M[1], "paved", False, "motorway", True)
        self.assertEqual(navgraph._lanes(navgraph.driving_width(ramp), True), (1, 0), "a ramp: one lane (GitHub #107)")

    def test_osm_lanes_on_the_painted_lanes(self):
        # I-25 (2026-10-28): OpenStreetMap's three to five lanes a way, rural and urban; the AI's lanes as wide as the
        # painted ones (roads.lane_layout), not squeezed into the class rule's 7.4 m / 11 m
        for urban, n in ((False, 3), (True, 4), (True, 5), (False, 2)):
            w = roads.carriageway("motorway", "paved", urban, None, True, n)
            sp = roads.Spec(w, "paved", urban, "motorway", True, lanes=n)
            dw = navgraph.driving_width(sp)
            self.assertAlmostEqual(dw, n * roads.LANE_W["motorway"], places=6, msg=(urban, n))
            self.assertEqual(navgraph._lanes(dw, True, sp.lanes), (n, 0))
            lay = roads.lane_layout(sp)
            self.assertAlmostEqual(dw, lay["hi"] - lay["lo"], places=6, msg="the width the lane lines are painted on")
        # a city street's lanes from OpenStreetMap: the parking lanes stay outside the AI's
        st = roads.Spec(roads.carriageway("residential", "paved", True, None, False, 2), "paved", True, "residential",
                        False, lanes=2)
        self.assertAlmostEqual(navgraph.driving_width(st), 2 * roads.LANE_W_DEFAULT, places=6)
        # no lane count: the class rule as before (a rural Interstate's two lanes)
        plain = roads.Spec(roads.carriageway("motorway", "paved", False, None, True), "paved", False, "motorway", True)
        self.assertAlmostEqual(navgraph.driving_width(plain), roads.MOTORWAY_RURAL_M, places=6)

    def test_motorway_flow_over_the_limit(self):
        f = features([[(0, 0), (400, 0)], [(0, 50), (400, 50)]], ["motorway", "residential"])
        f.rules["connectors"] = rules([(0, "A", 0.0, 0.0), (0, "B", 1.0, 1.0), (1, "C", 0.0, 0.0), (1, "D", 1.0, 1.0)])
        f.rules["oneway"] = rules([(0, 1.0)], "float")
        f.rules["max_speed_kmh"] = rules([], "float")
        f.rules["subclass"] = rules([])
        us = navgraph.build(FRAME, [f], flat, (0.0, 0.0))
        eu = navgraph.build(FRAME, [f], flat, (0.0, 0.0), mph=False)
        mw = [e[5] for e in us["edges"] if e[2]]
        res = [e[5] for e in us["edges"] if not e[2]]
        self.assertTrue(mw and res)
        for v in mw:
            self.assertAlmostEqual(v, (65 + navgraph.FLOW_MPH["motorway"]) * navgraph.MPH, places=2,
                                   msg="mph countries: the motorway's traffic flows over the posted 65")
        for v in res:
            self.assertAlmostEqual(v, 25 * navgraph.MPH, places=2, msg="streets: the limit")
        for e in eu["edges"]:
            if e[2]:
                self.assertAlmostEqual(e[5], 120 / 3.6, places=2, msg="km/h countries: the limit")
        self.assertAlmostEqual(navgraph.road_mph(f, 0, roads.Spec(11.0, "paved", False, "motorway", True)), 65.0,
                               msg="the speed signs keep the posted limit")

    def test_nodes_on_the_curve_of_the_earth(self):
        """10 km from the anchor the ground drawn lies ~7.8 m under the flat plane (the anchor frame's up): the nodes
        too, not at the raw height (the AI's nodes and traffic lights stood over the ground, PR #112's finding)."""
        g = graph([[(0, 10000), (200, 10000)]], ["primary"], centre=(100.0, 10000.0))
        zs = np.array([n[2] for n in g["nodes"].values()])
        self.assertTrue(len(zs))
        drop = 10000.0 ** 2 / (2 * 6371000.0)
        np.testing.assert_allclose(zs, 10.0 - drop, atol=0.15)

    def test_tunnel_stretch_left_out(self):
        f = features([[(0, 0), (300, 0)]], ["primary"], levels=[(0, -1.0, 0.4, 0.7)])
        f.rules["connectors"] = rules([(0, "A", 0.0, 0.0), (0, "B", 1.0, 1.0)])
        g = navgraph.build(FRAME, [f], flat, (0.0, 0.0))
        for e in g["edges"]:
            mid = (g["nodes"][e[0]][0] + g["nodes"][e[1]][0]) / 2
            self.assertFalse(130 < mid < 200, "an edge over the tunnel")
        self.assertTrue(g["edges"])

    def test_nodes_on_a_bridge_take_its_deck(self):
        f = features([[(0, 0), (200, 0)]], ["primary"])
        f.rules["connectors"] = rules([(0, "A", 0.0, 0.0), (0, "B", 1.0, 1.0)])
        decks = {"id000": [(np.array([(50.0, 0.0), (150.0, 0.0)]), np.array([10.0, 14.0]))]}
        g = navgraph.build(FRAME, [f], flat, (0.0, 0.0), decks=decks)
        on = [n for n in g["nodes"].values() if 55 < n[0] < 145]
        off = [n for n in g["nodes"].values() if n[0] < 40 or n[0] > 160]
        self.assertTrue(on and off)
        for n in on:
            self.assertAlmostEqual(n[2], 10.0 + 4.0 * (n[0] - 50.0) / 100.0, delta=0.05)
        self.assertTrue(all(n[2] == 10.0 for n in off), "the ground (flat 10 m) off the bridge")

    def test_lanes_on_a_deck_are_the_drawn_decks(self):
        # 5th Street at Colorado Blvd, Frederick (2026-10-28): the ground measured 19.2 m (four lanes), its culvert deck
        # drawn at the bridge spec's width (no measured widths): the AI's lanes on the deck are the deck's
        f = features([[(0, 0), (200, 0)]], ["tertiary"], levels=[(0, 1.0, 0.4, 0.6)])
        f.rules["connectors"] = rules([(0, "A", 0.0, 0.0), (0, "B", 1.0, 1.0)])
        urban = lambda la, lo: np.ones(np.shape(la), bool)
        g = navgraph.build(FRAME, [f], flat, (0.0, 0.0), urban_fn=urban, widths=lambda fid, ll: (19.2, 2.0, "pavement"))
        deck = navgraph.driving_width(roads.bridge_specs(f)[0]) / 2
        ground = navgraph.driving_width(roads.road_specs(f, urban, lambda fid, ll: (19.2, 2.0, "pavement"))[0]) / 2
        self.assertGreater(ground, deck + 2.0)
        seen = set()
        for e in g["edges"]:
            mid = (g["nodes"][e[0]][0] + g["nodes"][e[1]][0]) / 2
            on = 80 < mid < 120
            self.assertAlmostEqual(e[7], round(deck if on else ground, 2), msg=mid)
            self.assertEqual(e[3:5], list(navgraph._lanes(deck * 2, False)) if on else
                             list(navgraph._lanes(ground * 2, False, roads.road_specs(f, urban, lambda fid, ll: (
                                 19.2, 2.0, "pavement"))[0].lanes)), mid)
            seen.add(on)
        self.assertEqual(seen, {True, False})
        # a deck as wide as the road: as before
        g2 = navgraph.build(FRAME, [f], flat, (0.0, 0.0))
        self.assertEqual({e[7] for e in g2["edges"]}, {round(deck, 2)})



class SignalsTest(unittest.TestCase):
    def test_no_stop_on_a_link_inside_one_intersection(self):
        import roads
        sp = roads.Spec(8.0, "paved", True, "residential", False)
        nodes = {"cJ": [0.0, 0.0, 0.0, 4], "cK": [0.0, 8.0, 0.0, 4], "s1": [0.0, -40.0, 0.0, 2],
                 "cW": [-40.0, 0.0, 0.0, 2], "cE": [40.0, 0.0, 0.0, 2]}
        arms = {"cJ": [("n", sp, np.array([0.0, 1.0]), 0.0, 1), ("s", sp, np.array([0.0, -1.0]), 0.0, 0),
                       ("w", sp, np.array([-1.0, 0.0]), 0.0, 1), ("e", sp, np.array([1.0, 0.0]), 0.0, 0)]}
        approaches = {("cJ", "n", 1): ("cK", True), ("cJ", "s", 0): ("s1", True), ("cJ", "w", 1): ("cW", True),
                      ("cJ", "e", 0): ("cE", True)}
        import furniture
        orig = furniture.junction_controls
        furniture.junction_controls = lambda a, k, link_ok=None, ring=None, signals=None: [(x, furniture.G_STOP) for x in a]     # an all-way stop
        try:
            out = navgraph._signals(arms, approaches, nodes)
        finally:
            furniture.junction_controls = orig
        got = {ap[0] for ap in out[0]["app"]}
        self.assertEqual(got, {"s1", "cW", "cE"}, "the 8 m link from the next connector gets no stop")


class RulesTest(unittest.TestCase):
    def test_default_speeds_by_country(self):
        class F:            # a feature without a mapped limit
            rules = {}
        mw = roads.Spec(11.0, "paved", False, "motorway", True)
        self.assertAlmostEqual(navgraph.road_mph(F, 0, mw), 65.0)
        self.assertAlmostEqual(navgraph.road_mph(F, 0, mw, mph=False) * 1.609344, 120.0)
        town = roads.Spec(9.0, "paved", True, "primary", False)
        self.assertAlmostEqual(navgraph.road_mph(F, 0, town, mph=False), 35.0, msg="in town: the urban cap")
        res = roads.Spec(6.0, "paved", True, "residential", False)
        self.assertAlmostEqual(navgraph.road_mph(F, 0, res, mph=False), 25.0)

    def test_driving_side_and_signals_by_country(self):
        self.assertEqual(navgraph.road_rules("US"), {"rightHandDrive": False, "turnOnRed": True, "redYellow": False,
                                                     "country": "US", "mph": True})
        self.assertFalse(navgraph.road_rules("CH")["mph"])
        self.assertTrue(navgraph.road_rules("GB")["mph"])
        self.assertTrue(navgraph.road_rules("gb")["rightHandDrive"], "the game's name: true = driving on the left")
        self.assertTrue(navgraph.road_rules("GB")["redYellow"])
        self.assertFalse(navgraph.road_rules("DE")["rightHandDrive"])
        self.assertTrue(navgraph.road_rules("JP")["rightHandDrive"])
        self.assertFalse(navgraph.road_rules(None)["rightHandDrive"], "unknown: US rules")


if __name__ == "__main__":
    unittest.main()
