"""tools/preload.py and tools/regions.py (no network): the region geometry (circles, corridors, polygons, unions and
their tiers), the tiles a region reaches, the named regions file, the plan (DEM rings with the ground filter's z12
tiles, export parents with their candidate sources by box, coverage and zoom), the coverage probe's classification,
and the run's export lanes (a parent moves on past a source's edge or a dead national host) with the manifest a
second run resumes from."""
import importlib.util
import math
import os
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
import dem as demmod  # noqa: E402
import imagery  # noqa: E402
import mapcells  # noqa: E402
import netfetch  # noqa: E402

_spec = importlib.util.spec_from_file_location("preload", HERE.parents[2] / "tools" / "preload.py")
preload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preload)
regions = preload.regmod

FREDERICK = (40.0992, -104.9372)
LONGMONT = (40.1672, -105.1019)
BALTIMORE = (39.2834, -76.5934)


def metres(a, b):
    """Great-circle distance (m) between (lat, lon) points."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * regions.EARTH_R * math.asin(math.sqrt(h))


def offset(p, north_m, east_m):
    return (p[0] + math.degrees(north_m / regions.EARTH_R),
            p[1] + math.degrees(east_m / regions.EARTH_R) / math.cos(math.radians(p[0])))


class GeometryTest(unittest.TestCase):
    def test_circle(self):
        c = regions.circle("c", *LONGMONT, 5.0)
        self.assertTrue(c.contains(*LONGMONT))
        self.assertTrue(c.contains(*offset(LONGMONT, 4900, 0)))
        self.assertFalse(c.contains(*offset(LONGMONT, 0, 5100)))
        d = c.dist_m(*zip(offset(LONGMONT, 8000, 0)))[0]
        self.assertAlmostEqual(d, 3000.0, delta=20.0)

    def test_corridor(self):
        a, b = (39.0, -104.85), (39.5, -104.85)            # a straight road north, 3 km wide
        r = regions.corridor("road", [a, b], 3.0)
        mid = (39.25, -104.85)
        self.assertTrue(r.contains(*mid))
        self.assertTrue(r.contains(*offset(mid, 0, 1400)))
        self.assertFalse(r.contains(*offset(mid, 0, 1600)))
        self.assertTrue(r.contains(*offset(b, 1400, 0)), "round the end")
        self.assertFalse(r.contains(*offset(b, 1600, 0)))
        self.assertAlmostEqual(r.dist_m([mid[0]], [offset(mid, 0, 4500)[1]])[0], 3000.0, delta=20.0)
        bent = regions.corridor("bent", [a, b, (39.5, -104.5)], 2.0)
        self.assertTrue(bent.contains(39.5, -104.7), "along the second segment")
        self.assertFalse(bent.contains(39.25, -104.6), "not the corner's inside")

    def test_polygon_and_box(self):
        tri = regions.polygon("t", [(39.0, -105.0), (39.0, -104.0), (40.0, -104.5)])
        self.assertTrue(tri.contains(39.3, -104.5))
        self.assertFalse(tri.contains(39.9, -104.9))
        self.assertFalse(tri.contains(38.9, -104.5))
        self.assertLess(tri.dist_m([39.3], [-104.5])[0], 0)
        self.assertAlmostEqual(tri.dist_m([38.9], [-104.5])[0], 0.1 * math.pi / 180 * regions.EARTH_R, delta=50.0)
        box = regions.bbox("b", 39.0, -105.0, 39.5, -104.0)
        self.assertTrue(box.contains(39.25, -104.5))
        self.assertFalse(box.contains(39.6, -104.5))
        self.assertAlmostEqual(box.area_km2(), 55.6 * 86.2, delta=150.0)

    def test_tiles_match_brute_force(self):
        reg = regions.Region("r", [regions.corridor("road", [(40.0, -105.1), (40.1, -105.0)], 2.0),
                                   regions.circle("town", *FREDERICK, 2.0)])
        z = 15
        got = set(reg.tiles(z))
        n = 2 ** z
        x0, y1 = (int(v) for v in regions.tile_xy(39.9, -105.25, z))
        x1, y0 = (int(v) for v in regions.tile_xy(40.25, -104.8, z))
        want = set()
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                la, lo = (float(v[0]) for v in regions.tile_center(z, [x], [y]))
                half = regions.EARTH_CIRC * math.cos(math.radians(la)) / n * 0.7072
                if min(float(p.dist_m([la], [lo])[0]) for p in reg.parts) <= half:
                    want.add((x, y))
        self.assertEqual(got, want)
        self.assertIn(tuple(int(v) for v in regions.tile_xy(*FREDERICK, z)), got)

    def test_priority_and_order(self):
        town = regions.circle("town", *FREDERICK, 1.0, "town")
        fill = regions.circle("fill", *FREDERICK, 4.0, "fill")
        reg = regions.Region("r", [fill, town], home=FREDERICK)
        t = reg.tiles(16)
        centre = tuple(int(v) for v in regions.tile_xy(*FREDERICK, 16))
        self.assertEqual(t[centre], 1, "the town's priority where both reach")
        self.assertEqual(set(t.values()), {1, 3})
        order = reg.ordered(16, t)
        pr = [t[k] for k in order]
        self.assertEqual(pr, sorted(pr), "towns first")
        self.assertEqual(order[0], centre, "nearest the home point first")
        self.assertEqual(set(reg.tiles(16, where=lambda p: p.tier == "town")), {k for k, v in t.items() if v == 1})


class RegionsFileTest(unittest.TestCase):
    def setUp(self):
        self.data = regions.load()

    def test_every_region_loads(self):
        for name in regions.names(self.data):
            r = regions.region(self.data, name)
            self.assertTrue(r.parts, name)
            self.assertGreater(r.area_km2(), 0, name)

    def test_front_range(self):
        r = regions.region(self.data, "front-range")
        def inside(p, tier=None):
            return any(q.contains(*p) for q in r.parts if tier is None or q.tier == tier)
        for p in (LONGMONT, (40.015, -105.27), (39.7392, -104.9903), (39.3722, -104.8561), (38.8339, -104.8214),
                  FREDERICK):
            self.assertTrue(inside(p, "town"), p)
        for p in ((39.20, -104.87), (39.972, -105.20), (39.88, -105.23)):    # I-25 at Larkspur, US-36, CO-93
            self.assertTrue(inside(p, "road"), p)
        self.assertTrue(inside((39.90, -104.75)), "the fill: the metro's north-east")
        self.assertFalse(inside((39.0, -104.4)), "the plains east of Colorado Springs")
        self.assertFalse(inside((39.7, -105.6)), "the mountains")
        self.assertEqual(r.home, FREDERICK)

    def test_bad_regions(self):
        bad = {"regions": {"a": {"union": ["b"]}, "b": {"union": ["a"]}, "c": {"circle": [40, -105], "tier": "x"},
                           "d": {"nothing": 1}, "e": {"union": ["missing"]}}}
        for n in "acde":
            with self.assertRaises(regions.RegionError, msg=n):
                regions.region(bad, n)


class PlanTest(unittest.TestCase):
    chain = [imagery.SOURCES[n] for n in imagery.DEFAULT_CHAIN]

    def test_dem(self):
        reg = preload.disc_region(*FREDERICK, 2.0, 1.0, far_km=20.0)
        keys = [k for k, _ in preload.plan_dem(reg)]
        self.assertEqual(len(keys), len(set(keys)))
        fz = demmod.zoom_for(FREDERICK[0], 1.25, demmod.FINE_ZOOM)
        zooms = {k[0] for k in keys}
        self.assertEqual(max(zooms), fz)
        self.assertTrue({10, 11, 12, 13} <= zooms)
        sh = fz - demmod.GLOBAL_ZOOM
        for z, x, y in keys:
            if z == fz:                     # the ground filter's z12 tile of every fine tile
                self.assertIn((demmod.GLOBAL_ZOOM, x >> sh, y >> sh), keys)
        self.assertIn((fz, *(int(v) for v in mapcells.tile_xy(*FREDERICK, fz))), keys)

    def test_exports(self):
        reg = preload.disc_region(*FREDERICK, 2.0, 1.0)
        plan = preload.plan_exports(reg, self.chain)
        zs = {k[0] for k, _, _ in plan}
        self.assertEqual(zs, {17, 18})
        for (z, x, y), prio, cands in plan:
            self.assertEqual((x % 4, y % 4), (0, 0), "a parent's first child")
            self.assertEqual(cands[0].name, "co_frederick", "the town's photo covers its centre")
            if z == 17:
                self.assertEqual([c.name for c in cands[-2:]], ["naipusda", "naip"], "NAIP last")
                self.assertNotIn("md6in", [c.name for c in cands], "Maryland's: not asked in Colorado")
            else:
                self.assertTrue(all(c.max_zoom >= 18 and c.bounds for c in cands), "z18: regional only")
        n18 = sum(1 for k, _, _ in plan if k[0] == 18)
        self.assertLess(n18, 4 * 2 * 9, "z18 only over the 1 km town part")
        skipped = preload.plan_exports(reg, self.chain, skip={"naip"})
        self.assertFalse(any(c.name == "naip" for _, _, cs in skipped for c in cs))
        far = preload.plan_exports(preload.disc_region(*BALTIMORE, 1.0), self.chain)
        self.assertTrue(all(cs[0].name == "md6in" for k, _, cs in far if k[0] == 17))

    def test_exports_follow_coverage(self):
        reg = preload.disc_region(*FREDERICK, 1.0, 0.0)
        plan = preload.plan_exports(reg, self.chain)
        cov = {"co_frederick": {preload.parent15(*k): False for k, _, _ in plan}}
        plan2 = preload.plan_exports(reg, self.chain, coverage=cov)
        self.assertTrue(plan2)
        for _, _, cands in plan2:
            self.assertNotIn("co_frederick", [c.name for c in cands], "no data there: not asked")
            self.assertEqual(cands[0].name, "naipusda")

    def test_front_range_sources(self):
        """Each Front Range town's centre gets its own county's photo first."""
        want = {(40.1672, -105.1019): "co_boulder_county", (39.868, -104.972): "co_thornton",
                (39.7294, -104.8319): "co_arapahoe", (39.3722, -104.8561): "co_douglas",
                (39.8028, -105.0875): "co_jeffco", (39.7392, -104.9903): "co_lakewood",
                (38.8339, -104.8214): "co_springs"}
        for p, name in want.items():
            reg = preload.disc_region(*p, 0.3, 0.0)
            plan = preload.plan_exports(reg, self.chain)
            self.assertIn(name, [c.name for c in plan[0][2]], p)
            for src in plan[0][2]:
                if src.bounds:
                    self.assertTrue(imagery.in_bounds(src, *plan[0][0]))

    def test_cells(self):
        cells = preload.plan_cells(preload.disc_region(*FREDERICK, 3.0))
        self.assertEqual(cells[0][0], mapcells.cell_of(*FREDERICK))
        self.assertTrue(all(c.z == mapcells.ZOOM for c, _ in cells))

    def test_classify_probe(self):
        a = np.full((64, 64, 3), 253, np.uint8)             # white past the county line ...
        a[:, :20] = np.random.default_rng(2).integers(40, 200, (64, 20, 3))      # ... photo in the west
        a[48:, 48:] = 0                                    # black corner
        rows = preload.classify_probe(a)
        self.assertEqual([r[0] for r in rows], [True] * 4)
        self.assertEqual([r[1] for r in rows], [True] * 4, "a quarter of the block is photo")
        self.assertEqual([r[2] for r in rows] + [r[3] for r in rows], [False] * 8)


# --- the run, with fakes for the imagery cache and the network ----------------------------------------------------
class FakeNet:
    def __init__(self, down=()):
        self.down = set(down)
        self.policies = {}

    def available(self, host):
        return host not in self.down

    def policy(self, host):
        return netfetch.HostPolicy(rate=100.0, max_conc=2, daily_budget=None)

    def host(self, name):
        return type("H", (), {"day": {"requests": 0}})()


class FakeImagery:
    """_paths / _download like imagery.Imagery's export sources: has(src, x, y) -> data for that child?"""

    def __init__(self, root, has):
        self.root, self.has = root, has
        self._local = threading.local()
        self.asked = []
        self.lock = threading.Lock()

    def _paths(self, src, z, x, y):
        p = os.path.join(self.root, src.name, str(z), str(x), f"{y}.jpg")
        return p, p + ".404"

    def _download(self, src, z, x, y):
        with self.lock:
            self.asked.append((src.name, z, x, y))
        if src.name in getattr(self, "fail", ()):
            raise imagery.ImageryError(f"{src.name}: down")
        x0, y0 = x & ~3, y & ~3
        for j in range(4):
            for i in range(4):
                path, miss = self._paths(src, z, x0 + i, y0 + j)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path if self.has(src, x0 + i, y0 + j) else miss, "wb").close()


def src(name, bounds=(39.0, -106.0, 41.0, -104.0)):
    return replace(imagery.SOURCES["co_frederick"], name=name, url=f"https://{name}.example/x?{{bbox}}",
                   bounds=bounds)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a, self.b = src("county_a"), src("county_b")
        self.usda = replace(imagery.SOURCES["naipusda"], name="usda", url="https://usda.example/x?{bbox}")
        self.usgs = replace(imagery.SOURCES["naip"], name="usgs_naip", url="https://usgsnaip.example/x?{bbox}")
        self.keys = [((17, 4 * i, 400), 1, [self.a, self.b, self.usda, self.usgs]) for i in range(3)]

    def go(self, img, net, manifest):
        run = preload.Run(net)
        preload.export_lanes(run, img, self.keys, manifest)
        run.go(every=1e9)
        return run

    def test_parent_moves_on_past_an_edge(self):
        # county_a has the west half of parent 0 only, nothing of parent 1, all of parent 2; county_b has the rest
        def has(s, x, y):
            if s.name == "county_a":
                return x < 2 or x >= 8
            return s.name == "county_b"
        img = FakeImagery(self.tmp.name, has)
        man = preload.Manifest(os.path.join(self.tmp.name, "r.done"))
        run = self.go(img, FakeNet(), man)
        asked = sorted((n, x) for n, z, x, y in img.asked)
        self.assertEqual(asked, [("county_a", 3), ("county_a", 7), ("county_a", 11), ("county_b", 3), ("county_b", 7)])
        self.assertEqual({t for t in man.done}, {preload.tok("exp", *k) for k, _, _ in self.keys})
        self.assertEqual(sum(ln.failed for ln in run.lanes.values()), 0)
        man.close()
        # a second run: everything in the manifest, nothing asked again
        img2 = FakeImagery(self.tmp.name, has)
        man2 = preload.Manifest(os.path.join(self.tmp.name, "r.done"))
        self.assertEqual(len(man2.done), 3)
        self.go(img2, FakeNet(), man2)
        self.assertEqual(img2.asked, [])
        # without the manifest: on disk already, still nothing asked
        img3 = FakeImagery(self.tmp.name, has)
        self.go(img3, FakeNet(), preload.Manifest(None))
        self.assertEqual(img3.asked, [])

    def test_national_fallback_past_a_dead_host(self):
        img = FakeImagery(self.tmp.name, lambda s, x, y: s.name == "usgs_naip")
        img.fail = {"usda"}
        man = preload.Manifest(None)
        run = self.go(img, FakeNet(), man)
        names = [n for n, *_ in img.asked]
        self.assertEqual(names.count("usgs_naip"), 3, "USGS's NAIP after USDA's failed")
        self.assertEqual(sum(ln.failed for ln in run.lanes.values()), 0)
        self.assertEqual(len(man.done), 3)
        img2 = FakeImagery(self.tmp.name, lambda s, x, y: True)
        run = self.go(img2, FakeNet(down={"county_a.example"}), preload.Manifest(None))
        self.assertEqual(run.lanes["county_a"].failed, 0, "on disk (known missing): not asked")

    def test_regional_host_down_fails_without_falling_back(self):
        img = FakeImagery(self.tmp.name, lambda s, x, y: True)
        man = preload.Manifest(None)
        run = self.go(img, FakeNet(down={"county_a.example"}), man)
        self.assertEqual(run.lanes["county_a"].failed, 3, "left for the next run, not NAIP'd")
        self.assertEqual(img.asked, [])
        self.assertEqual(man.done, set())

    def test_budget_pauses_a_lane(self):
        class Spent(FakeNet):
            def policy(self, host):
                return netfetch.HostPolicy(rate=100.0, max_conc=1, daily_budget=10)

            def host(self, name):
                return type("H", (), {"day": {"requests": 10}})()
        img = FakeImagery(self.tmp.name, lambda s, x, y: True)
        man = preload.Manifest(None)
        run = self.go(img, Spent(), man)
        self.assertEqual(img.asked, [])
        self.assertEqual(run.lanes["county_a"].deferred, 3)
        self.assertIn("tomorrow", run.lanes["county_a"].paused)
        self.assertEqual(man.done, set())
        run = preload.Run(Spent(), over_budget=True)
        preload.export_lanes(run, img, self.keys, man)
        run.go(every=1e9)
        self.assertEqual(len(img.asked), 3, "--over-budget: on")

    def test_manifest_file(self):
        p = os.path.join(self.tmp.name, "sub", "m.done")
        m = preload.Manifest(p)
        m.add("dem 16/1/2")
        m.add("dem 16/1/2")
        m.add("cell 14/3/4")
        m.close()
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read().split(), ["dem", "16/1/2", "cell", "14/3/4"])
        self.assertIn("cell 14/3/4", preload.Manifest(p))
        self.assertNotIn("cell 14/3/4", preload.Manifest(p, read=False))


if __name__ == "__main__":
    unittest.main()
