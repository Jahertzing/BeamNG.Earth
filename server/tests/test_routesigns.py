import gzip
import json
import math
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import geodesy  # noqa: E402
import guidesigns as gs  # noqa: E402
import roads  # noqa: E402
import routesigns as rs  # noqa: E402

LON93 = -105.2
DLAT_M = 1.0 / 111_195.0


def _routes():
    """CO 93 running north 33 km from 39.70 (a node every ~222 m) with a mile marker 5 mapped at 39.75; CO 72 across
    it at 39.85; a pass near its north end; the town of Golden (a box 39.84-39.88) round the junction."""
    ways, nodes = [], []
    nid = [1000]

    def ids(n):
        out = list(range(nid[0], nid[0] + n))
        nid[0] += n
        return out
    pts = [[round(39.70 + i * 0.002, 6), LON93] for i in range(151)]
    n93 = ids(len(pts))
    mid = 75
    ways.append({"id": 1, "tags": {"highway": "primary", "ref": "CO 93"}, "nodes": n93[:mid + 1], "geom": pts[:mid + 1]})
    ways.append({"id": 2, "tags": {"highway": "primary", "ref": "CO 93"}, "nodes": n93[mid:], "geom": pts[mid:]})
    p72 = [[pts[mid][0], round(-105.3 + i * 0.002, 6)] for i in range(101)]
    n72 = ids(len(p72))
    p72[50], n72[50] = pts[mid], n93[mid]
    ways.append({"id": 3, "tags": {"highway": "secondary", "ref": "CO 72"}, "nodes": n72, "geom": p72})
    nodes.append({"id": 9, "lat": 39.75, "lon": LON93, "tags": {"highway": "milestone", "distance": "5"}})
    nodes.append({"id": n93[140], "lat": pts[140][0], "lon": LON93,
                  "tags": {"mountain_pass": "yes", "name": "Loveland Pass", "ele": "3655"}})
    nodes.append({"id": 77, "lat": 39.86, "lon": LON93, "tags": {"place": "town", "name": "Golden", "ele": "1730"}})
    box = [[39.84, -105.25], [39.84, -105.15], [39.88, -105.15], [39.88, -105.25], [39.84, -105.25]]
    towns = [{"id": 5, "tags": {"name": "Golden", "admin_level": "8"}, "rings": [{"role": "outer", "pts": box}]}]
    return {"v": 1, "kind": "routes", "region": "test", "ways": ways, "nodes": nodes, "towns": towns}


class Plan(unittest.TestCase):
    def setUp(self):
        self.items = rs.plan(_routes())

    def kinds(self, k, **kw):
        return [i for i in self.items if i["k"] == k and all(i.get(a) == b for a, b in kw.items())]

    def test_mile_markers_pinned_to_the_mapped_one(self):
        north = {i["n"]: i for i in self.kinds("rmile", r=["CO", "93"]) if abs(gs._turn(0.0, i["brg"])) < 5}
        south = {i["n"]: i for i in self.kinds("rmile", r=["CO", "93"]) if abs(gs._turn(180.0, i["brg"])) < 5}
        self.assertEqual(sorted(north), sorted(south))                  # both sides of a two-lane road
        self.assertAlmostEqual(north[5]["lat"], 39.75, delta=20 * DLAT_M)
        self.assertAlmostEqual((north[6]["lat"] - north[5]["lat"]) / DLAT_M, gs.MILE_M, delta=20.0)
        self.assertEqual(sorted(north), list(range(2, 23)))           # (39.70 is mile 1.55, 40.0 mile 22.3)

    def test_unpinned_counts_from_the_south_or_west_end(self):
        d = _routes()
        d["nodes"] = [n for n in d["nodes"] if n["id"] != 9]
        items = rs.plan(d)
        n93 = [i for i in items if i["k"] == "rmile" and i["r"] == ["CO", "93"] and abs(i["brg"]) < 5]
        m1 = min(n93, key=lambda i: i["n"])
        self.assertEqual(m1["n"], 1)
        self.assertAlmostEqual((m1["lat"] - 39.70) / DLAT_M, gs.MILE_M, delta=20.0)
        e72 = [i for i in items if i["k"] == "rmile" and i["r"] == ["CO", "72"] and abs(i["brg"] - 90) < 5]
        m1 = min(e72, key=lambda i: i["n"])
        self.assertLess(abs(m1["lon"] - -105.3), abs(m1["lon"] - -105.1))  # (an east-west route: from the west)

    def test_junction_shields_and_jct(self):
        past = self.kinds("rshield", jn=True)
        got = {(tuple(i["sh"][0]), round(i["brg"]) % 360) for i in past}
        self.assertIn((("CO", "93", "NORTH"), 0), got)
        self.assertIn((("CO", "93", "SOUTH"), 180), got)
        self.assertIn((("CO", "72", "EAST"), 90), got)
        n = [i for i in past if i["sh"][0][2] == "NORTH"][0]
        self.assertAlmostEqual((n["lat"] - 39.85) / DLAT_M, rs.REASSURE_M, delta=5.0)
        jct = {(tuple(i["sh"][0][:2]), round(i["brg"]) % 360) for i in self.kinds("jct")}
        self.assertEqual(jct, {(("CO", "72"), 0), (("CO", "72"), 180), (("CO", "93"), 90), (("CO", "93"), 270)})
        j = [i for i in self.kinds("jct") if round(i["brg"]) == 0][0]
        self.assertAlmostEqual((39.85 - j["lat"]) / DLAT_M, rs.JCT_M, delta=5.0)    # ahead of it, northbound

    def test_pass_summit(self):
        p = self.kinds("pass")
        self.assertEqual(len(p), 2)
        self.assertEqual((p[0]["name"], p[0]["ft"], p[0]["divide"]), ("Loveland Pass", 11991, True))

    def test_city_limits(self):
        c = self.kinds("city")
        got = sorted((round(i["lat"], 3), round(i["lon"], 3), round(i["brg"]) % 360) for i in c)
        self.assertEqual(got, [(39.84, LON93, 0), (39.85, -105.25, 90), (39.85, -105.15, 270), (39.88, LON93, 180)])
        self.assertEqual({(i["name"], i["label"], i["ft"]) for i in c}, {("Golden", "TOWN LIMIT", 5676)})

    def test_city_corner_clipped_no_sign(self):
        d = _routes()
        # a sliver of town the road runs through for ~100 m only
        d["towns"].append({"id": 6, "tags": {"name": "Sliver", "border_type": "city"}, "rings": [{"role": "outer", "pts": [
            [39.7203, -105.21], [39.7203, -105.19], [39.7212, -105.19], [39.7212, -105.21], [39.7203, -105.21]]}]})
        self.assertFalse([i for i in rs.plan(d) if i["k"] == "city" and i["name"] == "Sliver"])

    def test_concurrency_signs_the_lowest(self):
        d = _routes()
        d["ways"][1]["tags"]["ref"] = "US 287;CO 93"
        items = rs.plan(d)
        on2 = [i for i in items if i["k"] == "rmile" and i["lat"] > 39.86]
        self.assertTrue(on2)
        self.assertEqual({tuple(i["r"]) for i in on2}, {("US", "287")})

    def test_state_line_clip(self):
        d = _routes()
        d["region"] = "colorado"
        rs.STATES["colorado"], keep = (39.80, -110.0, 41.0, -100.0), rs.STATES["colorado"]
        try:
            n93 = [i for i in rs.plan(d) if i["k"] == "rmile" and i["r"] == ["CO", "93"] and abs(i["brg"]) < 5]
        finally:
            rs.STATES["colorado"] = keep
        self.assertTrue(n93)
        self.assertGreater(min(i["lat"] for i in n93), 39.80)

    def test_faces(self):
        for face, size in (rs.city_face("Castle Rock", "TOWN LIMIT", 6224), rs.pass_face("Loveland Pass", 11990, True),
                           rs.route_shield_face([("CO", "93", "NORTH")]),
                           rs.route_shield_face([("US", "287", ""), ("CO", "52", "")], jct=True)):
            x0, y0, x1, y1 = face.bounds()
            self.assertAlmostEqual(x1 - x0, size[0], delta=0.05)
            self.assertLessEqual(y1, size[1] + 0.02)
            self.assertGreaterEqual(y0, -0.01)


class Files(unittest.TestCase):
    def test_routes_file_and_exit_miles(self):
        import test_guidesigns as tg
        with tempfile.TemporaryDirectory() as d:
            with gzip.open(os.path.join(d, "a.json.gz"), "wt") as f:
                json.dump(_routes(), f)
            p = gs.Plans((d,))
            got = p.near(39.70, -105.30, 40.0, -105.10)
            self.assertTrue({"rmile", "rshield", "jct", "pass", "city"} <= {i["k"] for i in got})
            # an exits file's freeway with its exit-numbered markers, and the same road as a route: its exits' markers
            net = tg._net()
            r = {"v": 1, "kind": "routes", "region": "test", "nodes": [], "towns": [],
                 "ways": [dict(net["ways"][0], tags={"highway": "motorway", "ref": "US 36", "oneway": "yes"})]}
            with gzip.open(os.path.join(d, "b.json.gz"), "wt") as f:
                json.dump(net, f)
            with gzip.open(os.path.join(d, "c.json.gz"), "wt") as f:
                json.dump(r, f)
            p = gs.Plans((d,))
            got = p.near(tg._lat(0), tg.LON0 - 0.01, tg._lat(7500), tg.LON0 + 0.01)    # (the exits' markers' reach)
            self.assertTrue([i for i in got if i["k"] == "mile"])
            self.assertFalse([i for i in got if i["k"] == "rmile"])


    def test_plan_cached(self):
        import revisit
        keep = revisit.CACHE_DIR
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            with gzip.open(os.path.join(d, "a.json.gz"), "wt") as f:
                json.dump(_routes(), f)
            revisit.CACHE_DIR = c
            try:
                n = len(gs.Plans((d,)).near(39.70, -105.30, 40.0, -105.10))
                self.assertTrue(os.listdir(os.path.join(c, "hwysigns")))
                self.assertEqual(len(gs.Plans((d,)).near(39.70, -105.30, 40.0, -105.10)), n)
            finally:
                revisit.CACHE_DIR = keep


class Tile(unittest.TestCase):
    """CO 93 as a two-lane road (10 m) through a tile north of the junction, in the frame of 39.85, -105.2."""

    def setUp(self):
        self.frame = geodesy.EnuFrame(39.85, LON93, 0.0)
        self.line = np.array([(0.0, y, 0.0) for y in np.arange(-4000.0, 4001.0, 50.0)])
        self.roads = [("r93", roads.Spec(10.0, "paved", False, "primary", False, 2.0, 0), self.line, LON93)]
        items = rs.plan(_routes())
        e, n, _ = self.frame.geodetic_to_enu(np.array([i["lat"] for i in items]), np.array([i["lon"] for i in items]),
                                             np.zeros(len(items)))
        self.items = [(i, (float(a), float(b))) for i, a, b in zip(items, e, n)]

    def _one(self, k, north=True):
        return [(i, xy) for i, xy in self.items if i["k"] == k and abs(xy[0]) < 1.0
                and (abs(i["brg"]) < 5 or abs(i["brg"] - 360) < 5) == north]

    def test_markers_on_the_right(self):
        (it, (x, y)), = [m for m in self._one("rmile") if m[0]["r"] == ["CO", "93"] and m[0]["n"] == 13]
        box = (-128.0, y - 64.0, 128.0, y + 64.0)
        out = gs.tile(self.frame, self.roads, box, items=self.items)
        P, T, _, _, M = out["mesh"]
        P = P + np.array([box[0], box[1], 0.0])
        t = T[M == gs.M_LETTER]
        self.assertTrue(len(t))
        a = P[t[:, 0]]
        nrm = np.cross(P[t[:, 1]] - a, P[t[:, 2]] - a)
        east, west = a[:, 0] > 5.0, a[:, 0] < -5.0
        self.assertEqual(int(east.sum() + west.sum()), len(t))         # beside the road, not on it
        self.assertTrue(east.any() and west.any())                     # a marker each side of a two-lane road
        self.assertTrue((nrm[east, 1] < 0).all())                      # the east one facing the northbound drivers
        self.assertTrue((nrm[west, 1] > 0).all())

    def test_city_sign(self):
        (it, (x, y)), = self._one("city", north=False)                # entering Golden southbound at 39.88
        box = (-128.0, y - 64.0, 128.0, y + 64.0)
        out = gs.tile(self.frame, self.roads, box, items=[(it, (x, y))])
        P, T, _, _, M = out["mesh"]
        P = P + np.array([box[0], box[1], 0.0])
        text = P[np.unique(T[M == gs.M_LETTER])]
        self.assertLess(text[:, 0].max(), -5.0)                        # the southbound side: west
        self.assertGreater(text[:, 2].min(), 1.7)
        self.assertIsNotNone(out["col"])                                # its posts

    def _mile13(self, roads, ground=None):
        (it, (x, y)), = [m for m in self._one("rmile") if m[0]["r"] == ["CO", "93"] and m[0]["n"] == 13]
        box = (-128.0, y - 64.0, 128.0, y + 64.0)
        out = gs.tile(self.frame, roads, box, items=[(it, (x, y))], ground=ground)
        if out["mesh"] is None:
            return None
        P, T, _, _, M = out["mesh"]
        return P + np.array([box[0], box[1], 0.0])

    def test_kept_off_other_roads(self):
        line = self.line + np.array([10.0, 0.0, 0.0])        # a frontage road beside: 6-14 m east
        front = ("fr", roads.Spec(8.0, "paved", False, "secondary", False, 2.0, 0), line, LON93)
        P = self._mile13(self.roads + [front])
        self.assertTrue(P is None or not ((P[:, 0] > 5.0) & (P[:, 0] < 14.6)).any())    # none on it
        line = self.line + np.array([14.0, 0.0, 0.0])        # 10-18 m east: the marker fits short of it
        front = ("fr", roads.Spec(8.0, "paved", False, "secondary", False, 2.0, 0), line, LON93)
        P = self._mile13(self.roads + [front])
        east = P[P[:, 0] > 0]
        self.assertTrue(len(east))
        self.assertLess(east[:, 0].max(), 10.0 - gs.CLEAR_M + 1e-6)

    def test_none_under_a_bridge(self):
        high = [("r93", self.roads[0][1], self.line + np.array([0.0, 0.0, 8.0]), LON93)]
        self.assertIsNone(self._mile13(high, ground=lambda x, y: np.zeros(len(x))))
        self.assertIsNotNone(self._mile13(self.roads, ground=lambda x, y: np.zeros(len(x))))

    def test_metric_country_no_route_shields(self):
        (it, (x, y)), = [m for m in self._one("rmile") if m[0]["r"] == ["CO", "93"] and m[0]["n"] == 13]
        box = (-128.0, y - 64.0, 128.0, y + 64.0)
        self.assertIsNone(gs.tile(self.frame, self.roads, box, items=self.items, mph=False)["mesh"])


class MastArms(unittest.TestCase):
    """A signalled crossroads, East Colfax Avenue (14 m) across Grant Street (9 m), a boom on Grant's northbound
    approach."""

    def setUp(self):
        c = (100.0, 100.0, 0.0)
        colfax = roads.Spec(14.0, "paved", True, "primary", False, 2.5, 0)
        grant = roads.Spec(9.0, "paved", True, "secondary", False, 2.0, 0)
        self.roads = [("cw", colfax, np.array([(0.0, 100.0, 0.0), c]), LON93),
                      ("ce", colfax, np.array([c, (256.0, 100.0, 0.0)]), LON93),
                      ("gs", grant, np.array([(100.0, 0.0, 0.0), c]), LON93),
                      ("gn", grant, np.array([c, (100.0, 256.0, 0.0)]), LON93)]
        self.names = {"cw": "East Colfax Avenue", "ce": "East Colfax Avenue", "gs": "Grant Street",
                      "gn": "Grant Street"}
        # northbound on Grant: the kerb side east (+x), the boom's yaw 0 (its local +x east, +y north)
        self.boom = ["signal", 106.0, 90.0, 0.15, 0.0, 1.0, 9.4, 0.0]

    def test_sign_over_the_lanes(self):
        mesh = gs.Mesh()
        self.assertEqual(gs.mast_arm_signs(mesh, self.roads, (0, 0, 256, 256), self.names, [self.boom]), 1)
        P = np.asarray(mesh.P)
        text = np.array([P[t[0]] for t, m in zip(mesh.T, mesh.M) if m == gs.M_LETTER])
        self.assertTrue(len(text))
        self.assertGreater(text[:, 2].min(), 0.15 + 4.9)                    # under the arm, clear of the traffic
        self.assertLess(text[:, 2].max(), 0.15 + gs.MAST_ARM[-1][1] + 0.1)              # (level, at the arm)
        self.assertLess(text[:, 0].max(), 106.0 - gs.MAST_SIGN_AT[0])          # out along the arm, over the road
        self.assertGreater(text[:, 0].min(), 106.0 - gs.MAST_SIGN_AT[1])       # short of its head
        self.assertLess(abs(text[:, 1].mean() - 90.0), 0.5)
        t = [t for t, m in zip(mesh.T, mesh.M) if m == gs.M_LETTER]
        nrm = [np.cross(P[b] - P[a], P[c] - P[a]) for a, b, c in t]
        self.assertTrue(all(v[1] < 0 for v in nrm))                    # facing the northbound drivers
        self.assertGreater(np.ptp(text[:, 0]), 1.0)                    # "E Colfax Ave" reads across

    def test_lettering_is_geometry(self):
        mesh = gs.Mesh()
        gs.mast_arm_signs(mesh, self.roads, (0, 0, 256, 256), self.names, [self.boom])
        self.assertNotIn(gs.M_TEXT, mesh.M)                             # (no glyph atlas: sharp up close)
        self.assertLess(len(mesh.P), 1500)                              # (a tile holds dozens)

    def test_tile_and_none(self):
        t = gs.tile(None, self.roads, (0, 0, 256, 256), names=self.names, booms=[self.boom])
        n_with = len(t["mesh"][1])
        t0 = gs.tile(None, self.roads, (0, 0, 256, 256), names=self.names)
        self.assertGreater(n_with, len(t0["mesh"][1]))
        names = dict(self.names, gs="East Colfax Avenue", gn="East Colfax Avenue")
        self.assertEqual(gs.mast_arm_signs(gs.Mesh(), self.roads, (0, 0, 256, 256), names, [self.boom]), 0)


if __name__ == "__main__":
    unittest.main()
