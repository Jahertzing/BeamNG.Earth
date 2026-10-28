"""The world folder (cache.py "The world folder", worldvol.py, tools/world.py): reads look in the world before the
cache, the cache's eviction / clear / cap never touch it, its .404 markers never expire, the pre-load writes into it
(moving what the cache has instead of fetching it again), the baked route lives in it, and tools/world.py migrate
moves a cache's pre-loaded files over (counts, a second run finds nothing, a run cut short resumes)."""
import contextlib
import errno
import importlib.util
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
import cache  # noqa: E402
import dem as demmod  # noqa: E402
import imagery  # noqa: E402
import netfetch  # noqa: E402
import revisit  # noqa: E402
import worldvol  # noqa: E402


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, HERE.parents[2] / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def put(root, rel, data=b"x"):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


class WorldCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache")
        self.world = os.path.join(self.tmp.name, "world")
        os.makedirs(self.cache)
        self.addCleanup(cache.set_world, None, None, env=False)

    def serve(self):
        self.assertEqual(cache.set_world(self.world, self.cache, env=False), os.path.abspath(self.world))


class ResolveTest(WorldCase):
    def test_world_first_then_cache(self):
        self.serve()
        both = put(self.cache, "dem/mapterhorn/15/1/2.webp", b"cache")
        put(self.world, "dem/mapterhorn/15/1/2.webp", b"world")
        only = put(self.cache, "dem/mapterhorn/15/1/3.webp", b"cache")
        self.assertEqual(cache.resolve(both), os.path.join(self.world, "dem", "mapterhorn", "15", "1", "2.webp"))
        self.assertEqual(cache.resolve(only), only)
        self.assertEqual(cache.NONE.read(both), b"world")
        self.assertEqual(cache.NONE.read(only), b"cache")
        self.assertTrue(cache.exists(os.path.join(self.cache, "dem", "mapterhorn", "15", "1", "2.webp")))
        outside = os.path.join(self.tmp.name, "elsewhere", "a.webp")
        self.assertEqual(cache.resolve(outside), outside)
        # a world folder found missing is not looked for again at once; then is (after WORLD_DIR_S)
        late = os.path.join(self.cache, "imagery", "usgs", "16", "5", "6.jpg")
        self.assertEqual(cache.resolve(late), late)
        put(self.world, "imagery/usgs/16/5/6.jpg")
        self.assertEqual(cache.resolve(late), late)
        with mock.patch.object(cache, "WORLD_DIR_S", 0.0):
            self.assertTrue(cache.resolve(late).startswith(self.world))

    def test_off_and_overlap(self):
        p = put(self.world, "dem/mapterhorn/15/1/2.webp")
        c = os.path.join(self.cache, "dem", "mapterhorn", "15", "1", "2.webp")
        self.assertEqual(cache.resolve(c), c)                   # (no world folder set)
        self.assertIsNone(cache.set_world(os.path.join(self.cache, "world"), self.cache, env=False))
        self.assertIsNone(cache.set_world(self.tmp.name, self.cache, env=False))
        self.assertIsNone(cache.world_dir())
        self.serve()
        self.assertEqual(cache.resolve(c), p)
        cache.set_world(None, None, env=False)
        self.assertEqual(cache.resolve(c), c)

    def test_environment_for_the_workers(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            cache.set_world(self.world, self.cache)
            self.assertEqual(os.environ[cache.WORLD_ENV], os.path.abspath(self.world))
            cache.set_world(None, None, env=False)
            cache._world_from_env()
            self.assertEqual(cache.world_dir(), os.path.abspath(self.world))
            cache.set_world(None, None)
            self.assertNotIn(cache.WORLD_ENV, os.environ)

    def test_promote_moves_or_looks(self):
        src = put(self.cache, "imagery/usgs/16/5/6.jpg", b"tile")
        want = os.path.join(self.world, "imagery", "usgs", "16", "5", "6.jpg")
        cache.set_world(self.world, self.cache, promote="look", env=False)
        self.assertEqual(cache.resolve(want), src, "the dry run reads it where it is")
        self.assertTrue(os.path.exists(src))
        cache.set_world(self.world, self.cache, promote=True, env=False)
        self.assertTrue(cache.exists(want))
        self.assertFalse(os.path.exists(src), "moved over")
        with open(want, "rb") as f:
            self.assertEqual(f.read(), b"tile")
        nothing = os.path.join(self.world, "imagery", "usgs", "16", "5", "7.jpg")
        self.assertFalse(cache.exists(nothing))

    def test_move_across_drives(self):
        src = put(self.cache, "a/b.bin", b"payload")
        dst = os.path.join(self.world, "a", "b.bin")
        real = os.replace
        calls = []

        def replace(a, b):
            calls.append(a)
            if a == src:
                raise OSError(errno.EXDEV, "cross-device link")
            return real(a, b)
        with mock.patch.object(cache.os, "replace", replace):
            cache.move_file(src, dst)
        self.assertFalse(os.path.exists(src))
        with open(dst, "rb") as f:
            self.assertEqual(f.read(), b"payload")
        self.assertEqual(len(calls), 2, "tried in place, then the copy put into place")


class NeverPrunedTest(WorldCase):
    def test_eviction_clear_and_cap_skip_the_world(self):
        self.serve()
        worlds = [put(self.world, f"imagery/usgs/16/1/{i}.jpg", b"w" * 1000) for i in range(5)]
        put(self.world, "dem/mapterhorn/15/1/1.404", b"")
        caches = [put(self.cache, f"imagery/usgs/16/2/{i}.jpg", b"c" * 1000) for i in range(5)]
        old = time.time() - 30 * 86400
        for p in worlds + caches:
            os.utime(p, (old, old))
        c = cache.Cache(self.cache, cap_gb=0.000002, start=False)       # 2000 bytes
        c.scan()
        self.assertEqual(c.usage(), (5000, 5), "only the cache counts against the cap")
        # a read of a world tile is untracked; of a cache tile, tracked
        self.assertEqual(c.read(os.path.join(self.cache, "imagery", "usgs", "16", "1", "0.jpg")), b"w" * 1000)
        self.assertEqual(c.usage(), (5000, 5))
        n, _ = c.evict()
        self.assertGreater(n, 0)
        self.assertTrue(all(os.path.exists(p) for p in worlds))
        c.started = time.time() + 10          # (nothing used by "this session": clear takes every file)
        c.clear(wait=0)
        self.assertTrue(all(os.path.exists(p) for p in worlds))
        self.assertTrue(os.path.exists(os.path.join(self.world, "dem", "mapterhorn", "15", "1", "1.404")))
        self.assertFalse(any(os.path.exists(p) for p in caches))
        c.close()


class MarkerTest(WorldCase):
    def test_world_markers_never_expire(self):
        self.serve()
        net = netfetch.Fetcher(offline=False)
        self.addCleanup(net.close)
        url = "https://tiles.mapterhorn.com/15/1/2.webp"
        c = put(self.cache, "dem/mapterhorn/15/1/2.404", b"")
        old = time.time() - 400 * 86400
        os.utime(c, (old, old))
        self.assertFalse(net.miss_alive(c, url), "an old cache marker has expired")
        cache.set_world(self.world, self.cache, env=False)     # (forgets that its folder was missing a moment ago)
        w = put(self.world, "dem/mapterhorn/15/1/2.404", b"")
        os.utime(w, (old, old))
        self.assertTrue(net.miss_alive(c, url), "the world's never does")


class ProducersTest(WorldCase):
    def test_dem_and_imagery_read_the_world(self):
        self.serve()
        net = netfetch.Fetcher(offline=True)
        self.addCleanup(net.close)
        d = demmod.Dem(os.path.join(self.cache, "dem"), fetcher=net)
        put(self.world, "dem/mapterhorn/15/1/2.webp", b"tile")
        self.assertTrue(d.has(15, 1, 2))
        self.assertEqual(d._download(demmod.MAPTERHORN, 15, 1, 2), b"tile")
        img = imagery.Imagery(os.path.join(self.cache, "imagery"), ("usgs",), fetcher=net)
        self.addCleanup(img.close)
        put(self.world, "imagery/usgs/16/3/4.jpg", b"jpg")
        key = (imagery.SOURCES["usgs"], 16, 3, 4)
        self.assertTrue(img._on_disk(key))
        self.assertEqual(img.raw(*key), b"jpg")

    def test_revisit_bake_in_the_world(self):
        self.assertEqual(revisit.pin_dir(self.cache), os.path.join(self.cache, revisit.PIN_SUBDIR))
        self.serve()
        self.assertEqual(revisit.pin_dir(self.cache), os.path.join(os.path.abspath(self.world), revisit.PIN_SUBDIR))
        put(self.world, f"bake/{revisit.ANCHORS}", b'{"anchors": [[40.1, -104.9]]}')
        self.assertEqual(revisit.pinned_anchors(self.cache), {(40.1, -104.9)})


class PreloadIntoWorldTest(WorldCase):
    def test_export_lane_writes_into_the_world_and_takes_the_caches(self):
        preload = load_tool("preload")
        import test_preload_tool as tp
        a = tp.src("county_a")
        keys = [((17, 4 * i, 400), 1, [a]) for i in range(2)]
        # parent 1 is in the cache already (an earlier visit's): moved over, not asked again
        for j in range(4):
            for i in range(4):
                put(self.cache, f"imagery/county_a/17/{4 + i}/{400 + j}.jpg", b"old")
        cache.set_world(self.world, self.cache, promote=True, env=False)
        img = tp.FakeImagery(os.path.join(self.world, "imagery"), lambda s, x, y: True)
        man = preload.Manifest(worldvol.manifest_path(self.world, "test"))
        run = preload.Run(tp.FakeNet())
        preload.export_lanes(run, img, keys, man)
        run.go(every=1e9)
        man.close()
        self.assertEqual(sorted(x for _, _, x, _ in img.asked), [3], "only parent 0 fetched")
        got = {rel for rel, _, _ in worldvol.walk(self.world)}
        for x in range(8):
            self.assertIn(f"imagery/county_a/17/{x}/403.jpg", got)
        self.assertFalse(os.path.exists(os.path.join(self.cache, "imagery", "county_a", "17", "5", "401.jpg")))
        self.assertEqual(worldvol.read_tokens(worldvol.manifest_path(self.world, "test")),
                         {preload.tok("exp", *k) for k, _, _ in keys})

    def test_disk_check(self):
        preload = load_tool("preload")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(preload.check_disk(os.path.join(self.world, "not", "yet"), 1000))
            self.assertFalse(preload.check_disk(self.world, 1e18))
        self.assertIn("does not fit", out.getvalue())


class MigrateTest(WorldCase):
    PRE = ["dem/mapterhorn/15/1/2.webp", "dem/mapterhorn/15/1/3.404", "imagery/usgs/16/3/4.jpg",
           "imagery/co_frederick/17/8/12.jpg", "imagery/co_frederick/17/11/15.jpg", "imagery/co_frederick/17/9/13.404",
           "mapdata/v9/overture/2026-10-21.0/roads/14/5/6.npz", "mapdata/v9/overture/2026-10-21.0/water/14/5/6.npz",
           "mapdata/raw/drcog/2024/edges/14/5/6.npz", "mapdata/raw/overture/2026-10-21.0/theme/f.parquet/0.blk",
           "mapdata/raw/overture/2026-10-21.0/theme/f.parquet/size", "bake/anchors.json", "bake/routes.json",
           "preload/coverage/co_frederick.json"]
    LIVE = ["dem/mapterhorn/15/9/9.webp", "imagery/usgs/16/9/9.jpg", "imagery/co_frederick/17/20/20.jpg",
            "mapdata/v9/overture/2026-10-21.0/roads/14/7/7.npz", "revisit/far/ab/abc.zip", "netstats/today.json",
            "index.tsv", "VERSION", "gravity/egm.bin"]
    TOKENS = ["dem 15/1/2", "dem 15/1/3", "xyz usgs/16/3/4", "exp 17/8/12", "cell 14/5/6", "edges 14/5/6"]

    def setUp(self):
        super().setUp()
        self.tool = load_tool("world")
        self.tool.server_listening = lambda port=0: False
        for rel in self.PRE + self.LIVE:
            put(self.cache, rel, b"data-" + rel.encode())
        put(self.cache, "preload/front-range.done", "\n".join(self.TOKENS).encode() + b"\n")

    def run_tool(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = self.tool.main(list(argv) + ["--world-dir", self.world, "--cache-dir", self.cache])
        return rc, out.getvalue()

    def files(self, root):
        return {rel for rel, _, _ in worldvol.walk(root)}

    def test_dry_run_apply_again(self):
        rc, out = self.run_tool("migrate")
        self.assertEqual(rc, 0, out)
        self.assertIn("Dry run", out)
        self.assertFalse(os.path.exists(self.world), "a dry run writes nothing")
        self.assertIn(f"{len(self.PRE)} files", out)
        rc, out = self.run_tool("migrate", "--apply")
        self.assertEqual(rc, 0, out)
        moved = {r.replace("preload/coverage", "regions/coverage") for r in self.PRE}
        self.assertEqual(self.files(self.world) - {"VERSION", "regions/front-range.done", "regions/migrate.log"},
                         moved)
        self.assertEqual(self.files(self.cache), set(self.LIVE), "live leftovers stay in the cache")
        self.assertEqual(worldvol.read_tokens(worldvol.manifest_path(self.world, "front-range")), set(self.TOKENS))
        with open(os.path.join(self.world, "imagery", "usgs", "16", "3", "4.jpg"), "rb") as f:
            self.assertEqual(f.read(), b"data-imagery/usgs/16/3/4.jpg")
        rc, out = self.run_tool("migrate", "--apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("Moved 0 files", out)
        rc, out = self.run_tool("status")
        self.assertEqual(rc, 0)
        for want in ("front-range", "dem/mapterhorn", "imagery/co_frederick", "mapdata/v9", "bake", "Free on its drive"):
            self.assertIn(want, out)
        rc, out = self.run_tool("verify", "--sample", "0")
        self.assertIn("0 only in the cache", out)

    def test_no_raw_and_refused_while_running(self):
        self.tool.server_listening = lambda port=0: True
        self.tool.default_world_dir = lambda: self.world         # (the server's own folders)
        rc, out = self.run_tool("migrate", "--apply")
        self.assertEqual(rc, 2)
        self.assertIn("listening", out)
        self.tool.server_listening = lambda port=0: False
        rc, out = self.run_tool("migrate", "--apply", "--no-raw")
        self.assertEqual(rc, 0, out)
        self.assertIn("mapdata/raw/overture/2026-10-21.0/theme/f.parquet/0.blk", self.files(self.cache))
        self.assertIn("mapdata/raw/drcog/2024/edges/14/5/6.npz", self.files(self.world), "a cell's own: moved")

    def test_resumes_after_a_cut(self):
        real = cache.move_file
        n = [0]

        def flaky(src, dst):
            n[0] += 1
            if n[0] > 6:
                raise OSError(errno.EIO, "disk went away")
            return real(src, dst)
        with mock.patch.object(cache, "move_file", flaky):
            rc, out = self.run_tool("migrate", "--apply")
        self.assertEqual(rc, 1, out)
        self.assertIn("run it again", out)
        rc, out = self.run_tool("migrate", "--apply")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.files(self.cache), set(self.LIVE))
        self.assertEqual(len(self.files(self.world) & set(self.PRE)), len(self.PRE) - 1)     # (+ the coverage file)

    def test_backup_export_import_region(self):
        self.run_tool("migrate", "--apply")
        put(self.world, "imagery/usgs/16/30/30.jpg", b"another region's")
        bk = os.path.join(self.tmp.name, "backup")
        rc, out = self.run_tool("backup", bk, "--region", "front-range")
        self.assertEqual(rc, 0, out)
        got = self.files(bk)
        self.assertIn("imagery/usgs/16/3/4.jpg", got)
        self.assertIn("regions/front-range.done", got)
        self.assertNotIn("imagery/usgs/16/30/30.jpg", got)
        zp = os.path.join(self.tmp.name, "all.zip")
        rc, out = self.run_tool("export", zp)
        self.assertEqual(rc, 0, out)
        fresh = os.path.join(self.tmp.name, "world2")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.tool.main(["import", zp, "--world-dir", fresh, "--cache-dir", self.cache])
        self.assertEqual(rc, 0)
        self.assertEqual(self.files(fresh), self.files(self.world))


class SettingsPageTest(WorldCase):
    def test_app_paths_world_fields(self):
        """app_paths carries the world folder's path, size (-1 until counted) and regions, in the protocol table's
        types; without a world folder, none of them."""
        import earthd
        import test_protocol_agreement as pa
        put(self.world, "imagery/usgs/16/1/1.jpg", b"x" * 100)
        put(self.world, "regions/front-range.done", b"dem 15/1/2\nxyz usgs/16/1/1\n")
        worldvol.write_meta(self.world, "front-range", title="Front Range", complete=False)
        self.addCleanup(earthd._world_info.clear)
        earthd._world_info.clear()
        self.assertNotIn("worldDir", earthd.app_paths_message(self.cache, None))
        self.serve()
        m = earthd.app_paths_message(self.cache, None)
        self.assertEqual((m["worldBytes"], m["worldFiles"], m["worldRegions"]), (-1, -1, []))
        earthd.scan_world(cache.world_dir())
        m = earthd.app_paths_message(self.cache, None)
        self.assertEqual(m["worldDir"], os.path.abspath(self.world))
        self.assertEqual(m["worldFiles"], 3)            # (the tile, the manifest and its description)
        self.assertGreater(m["worldBytes"], 100)
        self.assertEqual(m["worldRegions"], [{"name": "front-range", "title": "Front Range", "keys": 2,
                                              "last": time.strftime("%Y-%m-%d"), "complete": False}])
        table = pa.SERVER_TO_MOD["app_paths"]
        for k, v in m.items():
            if k != "type":
                self.assertIn(k, table)
                self.assertIsInstance(v, table[k][0])


if __name__ == "__main__":
    unittest.main()
