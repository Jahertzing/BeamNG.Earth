"""tools/preload.py's derive lane (no network): what it plans (z12 colour-match / de-shadow regions round the parts with
map data, trees and road widths per full cell, only the kinds that are on), the lane itself (manifest, resume, a
failure left for the next run), the estimate's rows, and the producers' build hooks and world-folder reads
(colormatch.ColorMatch.build, deshadow.Deshadow.build, vegetation.Vegetation, roadwidth.RoadWidths.store)."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
import cache  # noqa: E402
import colormatch  # noqa: E402
import deshadow  # noqa: E402
import mapcells  # noqa: E402
import roadwidth  # noqa: E402
import vegetation  # noqa: E402

_spec = importlib.util.spec_from_file_location("preload", HERE.parents[2] / "tools" / "preload.py")
preload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preload)
regions = preload.regmod

FREDERICK = (40.0992, -104.9372)


class FakeNet:
    def policy(self, host):
        import netfetch
        return netfetch.HostPolicy()

    def host(self, name):
        return type("H", (), {"day": {"requests": 0}})()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache")
        self.world = os.path.join(self.tmp.name, "world")
        os.makedirs(self.cache)
        self.addCleanup(cache.set_world, None, None, env=False)


class PlanTest(Case):
    def region(self):
        town = regions.circle("town", *FREDERICK, 1.0, "town")
        low = regions.circle("low", 40.6, -104.0, 3.0, "low")
        tiers = dict(regions.DEFAULT_TIERS, low={"priority": 5, "mapdata": False, "exports": []})
        return regions.Region("t", [town, low], tiers=tiers, far_km=2.0)

    def test_plan(self):
        reg = self.region()
        cells = preload.plan_cells(reg)
        items = preload.plan_derive(reg, cells, {"cm", "ds", "veg", "rw"})
        kinds = {}
        for (k, key), _ in items:
            kinds.setdefault(k, set()).add(tuple(key))
        self.assertEqual(kinds["cm"], kinds["ds"])
        self.assertTrue(all(key[0] == 12 for key in kinds["cm"]))
        x12, y12 = (int(v) for v in mapcells.tile_xy(*FREDERICK, 12))
        self.assertIn((12, x12, y12), kinds["cm"])
        x12l, y12l = (int(v) for v in mapcells.tile_xy(40.6, -104.0, 12))
        self.assertNotIn((12, x12l, y12l), kinds["cm"], "a tier without map data: no regions")
        full = {tuple(c) for c, _, ly in cells if ly is preload.CELL_LAYERS}
        self.assertEqual(kinds["veg"], full)
        self.assertEqual(kinds["rw"], full)
        only = preload.plan_derive(reg, cells, {"veg"})
        self.assertEqual({k for (k, _), _ in only}, {"veg"}, "only the kinds that are on")
        self.assertEqual(preload.plan_derive(reg, cells, set()), [])


class LaneTest(Case):
    def test_manifest_resume_and_failures(self):
        built, lock = [], threading.Lock()

        def ok(key):
            with lock:
                built.append(key)
            return True

        def bad(key):
            raise RuntimeError("no imagery")
        items = [(("cm", (12, i, 1)), 1) for i in range(5)] + [(("veg", mapcells.CellKey(14, 7, i)), 2)
                                                                 for i in range(3)]
        man = preload.Manifest(os.path.join(self.tmp.name, "r.done"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run = preload.derive_all(FakeNet(), items, {"cm": ok, "veg": bad}, man, workers=2, nice=0)
        self.assertEqual(sorted(built), [(12, i, 1) for i in range(5)])
        ln = run.lanes["derive"]
        self.assertEqual((ln.total, ln.fetched, ln.failed), (8, 5, 3))
        self.assertEqual(man.done, {preload.derive_tok("cm", (12, i, 1)) for i in range(5)},
                         "a failure is left for the next run")
        man.close()
        man2 = preload.Manifest(os.path.join(self.tmp.name, "r.done"))
        built.clear()
        with contextlib.redirect_stdout(out):
            run = preload.derive_all(FakeNet(), items, {"cm": ok, "veg": ok}, man2, workers=1, nice=0)
        self.assertEqual(sorted(built), [mapcells.CellKey(14, 7, i) for i in range(3)], "resumed: only the rest")
        man2.close()

    def test_estimate_rows(self):
        items = [(("cm", (12, i, 1)), 1) for i in range(4)] + [(("rw", mapcells.CellKey(14, 1, i)), 1)
                                                                 for i in range(10)]
        man = preload.Manifest(None)
        man.add(preload.derive_tok("cm", (12, 0, 1)))
        have = {"rw": lambda k: k.y < 4}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            est = preload.estimate_derive(items, man, have, 2)
        txt = out.getvalue()
        self.assertIn(preload.DERIVE_NAMES["cm"], txt)
        sec_cm, size_cm = preload.EST_CPU["cm"]
        sec_rw, size_rw = preload.EST_CPU["rw"]
        self.assertAlmostEqual(est["hours"], (3 * sec_cm + 6 * sec_rw) / 2 / 3600)
        self.assertAlmostEqual(est["wrote"], 3 * size_cm + 6 * size_rw)
        self.assertEqual(preload.estimate_derive([], man, {}, 2), {"wrote": 0.0, "hours": 0.0})


class HooksTest(Case):
    def serve(self):
        cache.set_world(self.world, self.cache, env=False)

    def test_colormatch_and_deshadow_build_and_world_reads(self):
        cm = colormatch.ColorMatch(self.world, "s2", region_z=12)
        self.addCleanup(cm.close)
        calls = []
        cm._compute_field = lambda rk: (calls.append(rk), (colormatch.EMPTY, {}))[1]
        self.assertTrue(cm.build((5, 6)))
        self.assertFalse(cm.build((5, 6)), "on disk: not again")
        self.assertEqual(calls, [(5, 6)])
        # the server's, rooted at the cache, reads the world's copy
        self.serve()
        srv = colormatch.ColorMatch(self.cache, "s2", region_z=12)
        self.addCleanup(srv.close)
        self.assertFalse(srv._on_disk((5, 6)), "the world's no-seam marker")
        self.assertIsNone(srv._on_disk((5, 7)))
        cache.set_world(None, None, env=False)

        ds = deshadow.Deshadow(self.world, None, "full")
        self.addCleanup(ds.close)
        ds._compute = lambda rk: (dict(version=deshadow.VERSION, ok=False, cast=False, reason="test"), None)
        self.assertTrue(ds.build((5, 6)))
        self.assertFalse(ds.build((5, 6)))
        self.serve()
        srv = deshadow.Deshadow(self.cache, None, "full")
        self.addCleanup(srv.close)
        with srv._lock:
            self.assertEqual(srv._load_params((5, 6))["reason"], "test")

    def test_vegetation_reads_the_world(self):
        md = type("MD", (), {"release": "r1"})()
        veg = vegetation.Vegetation(md, self.world)
        p = veg._path(10, 20)
        os.makedirs(os.path.dirname(p))
        fields = ("lon", "lat", "height", "radius", "kind", "group", "rank", "yaw", "scale")
        np.savez_compressed(p, **{n: np.arange(3, dtype=np.float32) for n in fields})
        self.serve()
        srv = vegetation.Vegetation(md, self.cache)
        self.assertTrue(srv.ready((14, 10, 20)))
        srv._build = lambda x, y: self.fail("built again")
        self.assertEqual(len(srv.cell((14, 10, 20))), 3)
        self.assertFalse(srv.ready((14, 10, 21)))

    def test_road_widths_store_and_world_reads(self):
        key = mapcells.CellKey(14, 3412, 6198)
        lon0, lat0, lon1, lat1 = key.bounds()
        lo, la = (lon0 + lon1) / 2, (lat0 + lat1) / 2
        d = 0.0003
        ring = [(lo, la), (lo + d, la), (lo + d, la + d), (lo, la + d), (lo, la)]

        class Feats:
            lonlat = np.array(ring, np.float64)
            coord_off = np.array([0, len(ring)], np.int64)

            def __len__(self):
                return 1
        rw = roadwidth.RoadWidths(lambda k: Feats(), roadwidth.store_dir(self.world, type("M", (), {"release": "r"})()))
        self.assertTrue(rw.store(key))
        self.assertFalse(rw.store(key), "kept: not again")
        broken = roadwidth.RoadWidths(lambda k: 1 / 0, roadwidth.store_dir(self.world, type("M", (), {"release": "x"})()))
        with self.assertRaises(RuntimeError):
            broken.store(key)
        self.serve()
        path = os.path.join(roadwidth.store_dir(self.cache, type("M", (), {"release": "r"})()), "14", "3412", "6198.npy")
        self.assertTrue(roadwidth.complete_store(path))
        segs = roadwidth._read_segs(path)
        self.assertEqual(segs.shape, (4, 4))
        self.assertIsNotNone(roadwidth._read_index(path, len(segs)))
        self.assertFalse(os.path.exists(path), "read from the world, nothing in the cache")


if __name__ == "__main__":
    unittest.main()
