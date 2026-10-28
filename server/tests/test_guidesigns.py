import gzip
import json
import math
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import geodesy  # noqa: E402
import guidesigns as gs  # noqa: E402
import roads  # noqa: E402

LAT0, LON0 = 39.85, -104.98
DLAT_M = 1.0 / 111_195.0        # degrees of latitude per m


def _lat(m):
    return LAT0 + m * DLAT_M


def _net():
    """A northbound I-25 carriageway 10 km long (node every 200 m), exit 220 at 3 km and 221 a mile on (their ramps
    leaving to the right, signed US 36 West / Boulder and 120th Ave), an entrance at 5 km, 75 mph."""
    nodes, geom = [], []
    for k in range(51):
        nodes.append(1000 + k)
        geom.append([_lat(200.0 * k), LON0])
    j1, j2 = 1015, 1023               # 3000 m, 4600 m (~1 mile apart: 1609 m)
    geom[23][0] = _lat(3000.0 + 1609.0)
    off = 0.00025
    ways = [{"id": 1, "tags": {"highway": "motorway", "ref": "I 25;US 87", "oneway": "yes", "maxspeed": "75 mph",
                               "lanes": "3"}, "nodes": nodes, "geom": geom},
            {"id": 2, "tags": {"highway": "motorway_link", "destination:ref": "US 36 West", "destination": "Boulder"},
             "nodes": [j1, 2001, 2002], "geom": [geom[15], [_lat(3100), LON0 + off], [_lat(3300), LON0 + 3 * off]]},
            {"id": 3, "tags": {"highway": "motorway_link", "destination": "120th Avenue"},
             "nodes": [j2, 3001, 3002], "geom": [geom[23], [_lat(4700), LON0 + off], [_lat(4900), LON0 + 3 * off]]},
            {"id": 4, "tags": {"highway": "motorway_link"},
             "nodes": [4001, 4002, 1025], "geom": [[_lat(4700), LON0 + 3 * off], [_lat(4900), LON0 + off], geom[25]]}]
    junctions = [{"id": j1, "lat": geom[15][0], "lon": LON0, "tags": {"highway": "motorway_junction", "ref": "220"}},
                 {"id": j2, "lat": geom[23][0], "lon": LON0, "tags": {"highway": "motorway_junction", "ref": "221"}}]
    return {"v": 1, "region": "test", "ways": ways, "junctions": junctions}


class Lettering(unittest.TestCase):
    def test_width_and_quads(self):
        w = gs.text_width("Denver", 0.4)
        self.assertTrue(1.0 < w < 2.5, w)
        q = gs.text_quads("Exit 2A", 0.4, 0.0, 0.0)
        self.assertEqual(len(q), 6)                     # (the space has no quad)
        for x0, y0, x1, y1, u0, v0, u1, v1 in q:
            self.assertLess(x0, x1)
            self.assertLess(y0, y1)
            self.assertTrue(0 <= u0 < u1 <= 1 and 0 <= v0 < v1 <= 1)
        capital = [q for q in gs.text_quads("H", 1.0, 0.0, 0.0)][0]
        self.assertAlmostEqual(capital[3] - capital[1], 1.0 + 2 * 0.0759, delta=0.05)   # cap height 1 + padding

    def test_parse_ref(self):
        self.assertEqual(gs.parse_ref("I 25 South"), ("I", "25", "SOUTH"))
        self.assertEqual(gs.parse_ref("US-36 West"), ("US", "36", "WEST"))
        self.assertEqual(gs.parse_ref("CO 470"), ("CO", "470", ""))
        self.assertEqual(gs.parse_ref("E-470"), ("E", "470", ""))
        self.assertEqual(gs.parse_ref("SH 7"), ("CO", "7", ""))
        self.assertEqual(gs.route_kind("E", "470"), "toll")
        self.assertEqual(gs.parse_ref("E470 Toll South"), ("E", "470", "SOUTH"))
        self.assertEqual(gs.parse_ref("Northwest Parkway Toll West"), ("TOLL", "Northwest Pkwy", "WEST"))
        self.assertEqual(gs.route_kind("TOLL", "Northwest Pkwy"), "toll")
        self.assertEqual(gs.parse_ref("I 25 EXPR"), ("I", "25", ""))

    def test_cardinal(self):
        self.assertEqual(gs.cardinal("I", "25", 5.0), "NORTH")
        self.assertEqual(gs.cardinal("I", "25", 170.0), "SOUTH")
        self.assertEqual(gs.cardinal("I", "70", 95.0), "EAST")
        self.assertEqual(gs.cardinal("US", "36", 300.0), "WEST")
        self.assertEqual(gs.mph_of("75 mph"), 75.0)
        self.assertAlmostEqual(gs.mph_of("100"), 62.14, places=1)

    def test_faces_bounded(self):
        f, (w, h) = gs.guide_face([("I", "25", "SOUTH"), ("US", "87", "SOUTH")], ["Denver", "Colorado Springs"],
                                  "217B", "1 MILE")
        x0, y0, x1, y1 = f.bounds()
        self.assertAlmostEqual(x1 - x0, w, delta=0.01)
        self.assertAlmostEqual(y1 - y0, h, delta=0.01)
        self.assertTrue(3.0 < w < 9.0 and 2.0 < h < 5.0, (w, h))
        for make in (lambda: gs.gore_face("220"), lambda: gs.mile_face(214), lambda: gs.speed_face(75),
                     lambda: gs.reassurance_face([("I", "25", "NORTH")])):
            f, (w, h) = make()
            self.assertTrue(f.polys and f.texts)
            self.assertGreater(w, 0.3)

    def test_triangulate_concave(self):
        tris = gs.triangulate(gs.INTERSTATE)
        self.assertEqual(len(tris), len(gs.INTERSTATE) - 2)
        area = sum(abs(gs._area([gs.INTERSTATE[a], gs.INTERSTATE[b], gs.INTERSTATE[c]])) for a, b, c in tris)
        self.assertAlmostEqual(area, abs(gs._area(gs.INTERSTATE)), places=6)


class Plan(unittest.TestCase):
    def setUp(self):
        self.items = gs.plan(_net())

    def kinds(self, k):
        return [i for i in self.items if i["k"] == k]

    def test_exits(self):
        dirs = self.kinds("exit_dir")
        self.assertEqual(sorted(i["ref"] for i in dirs), ["220", "221"])
        e220 = [i for i in dirs if i["ref"] == "220"][0]
        self.assertEqual(e220["sh"], [["US", "36", "WEST"]])
        self.assertEqual(e220["dst"], ["Boulder"])
        self.assertFalse(e220["left"])
        self.assertLess(abs(e220["brg"]), 1.0)                          # northbound
        self.assertEqual(e220["thr"]["sh"][0][:2], ["I", "25"])         # through: the route's own shield
        self.assertEqual(e220["thr"]["sh"][0][2], "NORTH")
        # exit 221's 1-mile sign would stand before exit 220: left out; its half-mile one stays
        adv = sorted((i["ref"], i["k"]) for i in self.items if i["k"].startswith("adv"))
        self.assertEqual(adv, [("220", "adv_1"), ("220", "adv_half"), ("221", "adv_half")])
        a = [i for i in self.items if i["k"] == "adv_1"][0]
        self.assertAlmostEqual((_lat(3000) - a["lat"]) / DLAT_M, gs.MILE_M, delta=5.0)
        self.assertEqual(len(self.kinds("gore")), 2)

    def test_services_and_bare_ramps(self):
        net = _net()
        net["ways"][1]["tags"] = {"highway": "motorway_link", "destination": "Weigh Station"}
        net["junctions"][0]["tags"].pop("ref", None)
        net["ways"][2]["tags"] = {"highway": "motorway_link"}
        net["junctions"][1]["tags"]["noref"] = "yes"
        items = gs.plan(net)
        dirs = [i for i in items if i["k"] == "exit_dir"]
        self.assertEqual([(i["dst"], i.get("svc")) for i in dirs], [(["Weigh Station"], True)])
        self.assertFalse([i for i in items if i["k"].startswith("adv")])
        self.assertEqual(len([i for i in items if i["k"] == "gore"]), 2)

    def test_entrance(self):
        r = self.kinds("reassure")
        self.assertEqual(len(r), 1)
        self.assertEqual([s[:2] for s in r[0]["sh"]], [["I", "25"], ["US", "87"]])
        self.assertAlmostEqual((r[0]["lat"] - _lat(5000)) / DLAT_M, 320.0, delta=5.0)
        self.assertEqual([i["mph"] for i in self.kinds("speed")], [75])

    def test_mile_markers(self):
        miles = {i["n"]: i for i in self.kinds("mile")}
        self.assertIn(220, miles)
        self.assertIn(221, miles)
        self.assertAlmostEqual((miles[220]["lat"] - _lat(3000)) / DLAT_M, 0.0, delta=25.0)
        self.assertAlmostEqual((miles[222]["lat"] - _lat(4609 + 1609)) / DLAT_M, 0.0, delta=25.0)
        self.assertEqual(sorted(miles), list(range(min(miles), max(miles) + 1)))
        self.assertLessEqual(max(miles), 224)                             # the road ends at 10 km
        self.assertGreaterEqual(min(miles), 218)

    def test_plans_from_files(self):
        with tempfile.TemporaryDirectory() as d:
            with gzip.open(os.path.join(d, "test.json.gz"), "wt") as f:
                json.dump(_net(), f)
            p = gs.Plans((d,))
            got = p.near(_lat(2900), LON0 - 0.01, _lat(3100), LON0 + 0.01)
            self.assertIn("gore", {i["k"] for i in got})
            n = len(got)
            with gzip.open(os.path.join(d, "overlap.json.gz"), "wt") as f:   # a second file over the same road
                json.dump(_net(), f)
            p = gs.Plans((d,))
            self.assertEqual(len(p.near(_lat(2900), LON0 - 0.01, _lat(3100), LON0 + 0.01)), n)


class Tile(unittest.TestCase):
    def setUp(self):
        self.frame = geodesy.EnuFrame(_lat(3000), LON0, 0.0)
        net = _net()
        main = net["ways"][0]["geom"]
        e, n, _ = self.frame.geodetic_to_enu(np.array([p[0] for p in main]), np.array([p[1] for p in main]), 0.0)
        mline = np.stack([e, n, np.zeros(len(e))], axis=1)
        lk = net["ways"][1]["geom"]
        le, ln, _ = self.frame.geodetic_to_enu(np.array([p[0] for p in lk]), np.array([p[1] for p in lk]), 0.0)
        lline = np.stack([le, ln, np.zeros(len(le))], axis=1)
        self.roads = [("m", roads.Spec(16.0, "paved", False, "motorway", True, 2.5, 1, median=40.0, lanes=3), mline,
                       LON0),
                      ("l", roads.Spec(6.0, "paved", False, "motorway", True, 2.5, 1, link=True), lline, LON0)]
        items = gs.plan(net)
        e, n, _ = self.frame.geodetic_to_enu(np.array([i["lat"] for i in items]), np.array([i["lon"] for i in items]),
                                             np.zeros(len(items)))
        self.items = [(i, (float(a), float(b))) for i, a, b in zip(items, e, n)]

    def test_gantry_and_gore(self):
        box = (-128.0, -128.0, 128.0, 128.0)
        out = gs.tile(self.frame, self.roads, box, items=self.items)
        P, T, UV, C, M = out["mesh"]
        P = P + np.array([box[0], box[1], 0.0])                          # (the tile's corner: the frame)
        self.assertEqual(len(T), len(M))
        used = set(M.tolist())
        for m in (gs.M_PANEL, gs.M_WHITE, gs.M_TEXT, gs.M_STEEL, gs.M_PAINT):
            self.assertIn(m, used)
        # the gantry's panels hang over the road (5.5 m clearance), the truss spans to the median leg
        text = P[np.unique(T[M == gs.M_TEXT])]
        high = text[text[:, 2] > 4.5]                                     # (the gore's EXIT sign: low, on its posts)
        self.assertTrue(len(high))
        self.assertGreater(high[:, 2].min(), gs.CLEARANCE_M - 0.1)
        self.assertLess(high[:, 0].min(), 0.0)                            # the through panel over the left lanes
        cP, cT, boxes = out["col"]
        self.assertGreaterEqual(len(boxes), 2)                            # two legs at least, and the gore sign's posts
        legs_x = sorted({round(b[0] + (b[3] - b[0]) / 2) for b in boxes})
        self.assertTrue(any(-12 < x < -10 for x in legs_x), legs_x)       # left leg: 8 + 3 m left (a wide median)
        self.assertTrue(any(8 < x < 14 for x in legs_x), legs_x)          # right leg: 8 + 3 m
        paint = P[np.unique(T[M == gs.M_PAINT])]
        self.assertGreater(paint[:, 0].min(), 7.0)                        # chevrons right of the motorway's edge

    def test_gore_sign_beside_the_continuing_carriageway(self):
        """The motorway's ways split at the exit (a lane dropped into it): the junction ends one way and starts the next.
        The EXIT sign stands in the gore right of the continuing lanes, not in them (I-25 at 39.9884,-104.9873, where
        the gore was measured to the end of the way ending there: the bake's autodrive drove into the sign)."""
        _, spec, mline, lon = self.roads[0]
        i = int(np.argmin(np.abs(mline[:, 1])))                            # the junction: y = 0
        four = roads.Spec(19.0, "paved", False, "motorway", True, 2.5, 1, median=40.0, lanes=4)
        split = [("a_ends_here", four, mline[:i + 1], lon), ("b_goes_on", spec, mline[i:], lon), self.roads[1]]
        gore = [x for x in self.items if x[0]["k"] == "gore"][:1]
        for rs in (self.roads, split):
            box = (-128.0, -128.0, 128.0, 128.0)
            out = gs.tile(self.frame, rs, box, items=gore)
            P, T, _, _, M = out["mesh"]
            P = P + np.array([box[0], box[1], 0.0])
            text = P[np.unique(T[M == gs.M_TEXT])]                            # the gore's EXIT sign
            self.assertTrue(len(text))
            self.assertGreater(text[:, 0].min(), 8.0, "right of the continuing carriageway's edge")
            self.assertGreater(text[:, 1].min(), 30.0, "where the ramp has left, not at the junction")

    def test_gantry_legs_off_other_roads(self):
        m = gs.match(self.roads, np.array([0.0, 0.0]), np.array([0.0, 1.0]))
        panels = gs._guide_panels({"k": "adv_1", "sh": [["I", "25", "NORTH"]], "dst": ["Denver"]}, m, True)
        line = self.roads[0][2] + np.array([12.0, 0.0, 0.0])          # a road where the right leg stood (11 m)
        cd = gs.Clear(self.roads + [("c", roads.Spec(6.0, "paved", False, "secondary", False, 2.0, 0), line, LON0)])
        col = gs.Boxes()
        self.assertIsNotNone(gs.overhead(gs.Mesh(), col, m, panels, clear=cd))
        legs = [(b[0] + b[3]) / 2 for b in col.aabbs]
        self.assertTrue(all(not 8.4 < x < 15.6 for x in legs), legs)      # moved past it
        wide = gs.Clear(self.roads + [("w", roads.Spec(40.0, "paved", False, "secondary", False, 2.0, 0), line,
                                       LON0)])
        self.assertIsNone(gs.overhead(gs.Mesh(), gs.Boxes(), m, panels, clear=wide))  # nowhere: no gantry

    def test_text_faces_viewer(self):
        out = gs.tile(self.frame, self.roads, (-128.0, -128.0, 128.0, 128.0), items=self.items)
        P, T, _, _, M = out["mesh"]
        t = T[M == gs.M_TEXT]
        n = np.cross(P[t[:, 1]] - P[t[:, 0]], P[t[:, 2]] - P[t[:, 0]])
        self.assertTrue((n[:, 1] < 0).all())                               # toward the northbound traffic (-y)

    def test_mile_and_delineators(self):
        mile = [x for x in self.items if x[0]["k"] == "mile" and x[0]["n"] == 221][0]
        x, y = mile[1]
        box = (x - 50, y - 50, x + 50, y + 50)
        out = gs.tile(self.frame, self.roads, box, items=self.items)
        P, T, _, _, M = out["mesh"]
        self.assertIn(gs.M_REFLECT, set(M.tolist()))
        self.assertIn(gs.M_AMBER, set(M.tolist()))
        self.assertIsNone(out["col"])                                      # (mile posts and delineators give way)

    def test_barriers_take_the_delineators(self):
        box = (-128.0, 1500.0, 128.0, 1756.0)
        bar = [{"kind": "wbeam", "line": [[8.6, y] for y in np.arange(1400, 1900, 2.0)], "fid": "m", "side": -1}]
        a = gs.tile(self.frame, self.roads, box, items=[])["mesh"]
        b = gs.tile(self.frame, self.roads, box, items=[], barriers=bar)["mesh"]
        self.assertGreater((a[4] == gs.M_REFLECT).sum(), 0)
        self.assertEqual((b[4] == gs.M_REFLECT).sum(), 0)
        self.assertEqual((a[4] == gs.M_AMBER).sum(), (b[4] == gs.M_AMBER).sum())

    def test_billboards(self):
        box = (-128.0, 5800.0, 128.0, 9000.0)
        rows = gs.billboards(self.roads, box, self.frame, lambda la, lo: np.zeros(len(la), bool), None, [])
        self.assertTrue(rows)
        self.assertEqual(len(rows) % 2, 0)
        for r in rows[::2]:
            self.assertEqual(r[0], "billboard")
            self.assertTrue(8 + 22 <= r[1] <= 8 + 40, r)
            self.assertAlmostEqual(r[4], 0.0, delta=1.0)                     # local +x east: its face (-y) south
        self.assertEqual(gs.billboards(self.roads, box, self.frame, lambda la, lo: np.ones(len(la), bool), None, []),
                         [])

    def test_no_highway_nothing(self):
        r = [("s", roads.Spec(10.0, "paved", True, "residential", False), self.roads[0][2], LON0)]
        out = gs.tile(self.frame, r, (-128.0, -128.0, 128.0, 128.0), items=self.items)
        self.assertIsNone(out["mesh"])


class Streets(unittest.TestCase):
    """Street name signs at a town crossroads: East Colfax Avenue (14 m, primary) across Grant Street (9 m)."""

    def _roads(self, urban=True, grant="Grant Street"):
        c = (100.0, 100.0, 0.0)
        colfax = roads.Spec(14.0, "paved", urban, "primary", False, 2.5, 0)
        res = roads.Spec(9.0, "paved", urban, "residential", False, 2.0, 0)
        rs = [("cw", colfax, np.array([(0.0, 100.0, 0.0), c]), LON0), ("ce", colfax, np.array([c, (256.0, 100.0, 0.0)]),
                                                                          LON0),
              ("gs", res, np.array([(100.0, 0.0, 0.0), c]), LON0), ("gn", res, np.array([c, (100.0, 256.0, 0.0)]),
                                                                       LON0)]
        names = {"cw": "East Colfax Avenue", "ce": "East Colfax Avenue", "gs": grant, "gn": grant}
        return rs, names

    def test_street_name(self):
        self.assertEqual(gs.street_name("East Colfax Avenue"), "E Colfax Ave")
        self.assertEqual(gs.street_name("Park Avenue West"), "Park Ave W")
        self.assertEqual(gs.street_name("Broadway"), "Broadway")
        self.assertEqual(gs.street_name("Calle Ñandú"), "Calle Nandu")
        self.assertEqual(gs.street_name(None), "")

    def test_crossroads(self):
        rs, names = self._roads()
        mesh, col = gs.Mesh(), gs.Boxes()
        n = gs.street_signs(mesh, col, rs, (0, 0, 256, 256), names)
        self.assertEqual(n, 2)                                      # a wide street: two corners
        self.assertEqual(len(col.aabbs), 2)
        P = np.asarray(mesh.P)
        for a in col.aabbs:                                         # each post on a corner, off both carriageways
            x, y = (a[0] + a[3]) / 2, (a[1] + a[4]) / 2
            self.assertGreater(abs(y - 100.0), 7.0 + 1.0)
            self.assertGreater(abs(x - 100.0), 4.5 + 1.0)
        text = [P[t[0]] for t, m in zip(mesh.T, mesh.M) if m == gs.M_LETTER]
        self.assertTrue(text)
        self.assertGreater(min(p[2] for p in text), gs.BLADE_TOP_M)
        self.assertLess(max(p[2] for p in text), gs.BLADE_TOP_M + 1.1)
        a = col.aabbs[0]
        px, py = (a[0] + a[3]) / 2, (a[1] + a[4]) / 2
        mine = [p for p in text if abs(p[0] - px) < 2 and abs(p[1] - py) < 2]
        lower = np.array([p for p in mine if p[2] < gs.BLADE_TOP_M + 0.17 + 2 * gs.BLADE_CAP[0] + 0.03])     # Colfax's blade (the widest, below)
        upper = np.array([p for p in mine if p[2] > gs.BLADE_TOP_M + 0.17 + 2 * gs.BLADE_CAP[0] + 0.03])
        self.assertGreater(np.ptp(lower[:, 0]), 5 * np.ptp(lower[:, 1]))          # along Colfax (east-west)
        self.assertGreater(np.ptp(upper[:, 1]), 5 * np.ptp(upper[:, 0]))          # along Grant
        avoid = [((a[0] + a[3]) / 2, (a[1] + a[4]) / 2) for a in col.aabbs]
        mesh2, col2 = gs.Mesh(), gs.Boxes()
        self.assertEqual(gs.street_signs(mesh2, col2, rs, (0, 0, 256, 256), names, avoid=avoid), 2)
        self.assertTrue(all(a not in col.aabbs for a in col2.aabbs))      # other corners

    def test_none(self):
        rs, names = self._roads(urban=False)
        self.assertEqual(gs.street_signs(gs.Mesh(), gs.Boxes(), rs, (0, 0, 256, 256), names), 0)   # (out of town)
        rs, names = self._roads(grant="East Colfax Avenue")
        self.assertEqual(gs.street_signs(gs.Mesh(), gs.Boxes(), rs, (0, 0, 256, 256), names), 0)   # (one name)
        rs, names = self._roads()
        self.assertEqual(gs.street_signs(gs.Mesh(), gs.Boxes(), rs, (0, 0, 50, 50), names), 0)     # (another tile's)

    def test_tile(self):
        rs, names = self._roads()
        t = gs.tile(None, rs, (0, 0, 256, 256), names=names)
        self.assertIsNotNone(t["mesh"])
        self.assertEqual(len(t["col"][2]), 2)
        self.assertIsNone(gs.tile(None, rs, (0, 0, 256, 256), names=names, mph=False)["mesh"])
        self.assertIsNone(gs.tile(None, rs, (0, 0, 256, 256))["mesh"])


class Write(unittest.TestCase):
    def test_write_tile(self):
        frame = geodesy.EnuFrame(_lat(3000), LON0, 0.0)
        t = Tile()
        t.setUp()
        x0, y0, x1, y1 = roads.tile_bbox(0, 0)
        signs = gs.tile(frame, t.roads, (x0, y0, x1, y1), items=t.items)
        with tempfile.TemporaryDirectory() as d:
            vfs, n = roads.write_tile(d, "/vfs", 1, 0, 0, [], signs=signs)
            with open(os.path.join(d, os.path.basename(vfs))) as f:
                data = json.load(f)
            if signs["mesh"] is not None:
                self.assertTrue(os.path.exists(os.path.join(d, "sign_1_0_0.cdae")))
                self.assertEqual(data["sign"]["shape"], "/vfs/sign_1_0_0.cdae")
            if signs["col"] is not None:
                self.assertIn("signcol", data["colliders"])
                with open(os.path.join(d, "signcol_1_0_0.cdae"), "rb") as f:
                    body = f.read()
                self.assertNotIn(b"bngEarthDae", body, "a material the level has (no engine error per collider)")
                self.assertIn(b"bngEarthFar_v3", body)
        self.assertIn("sign", roads.shape_files(1, 0, 0))


if __name__ == "__main__":
    unittest.main()
