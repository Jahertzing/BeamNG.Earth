"""Terrain blocks in the revisit cache (blockcache.py): a complete block comes back under another session's names with
its base photo and the photo's packed copy; an incomplete one is not kept."""
import itertools
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import blockcache  # noqa: E402
import earthd  # noqa: E402
import revisit  # noqa: E402
import surface as surfacemod  # noqa: E402
import terraincache  # noqa: E402


class FakeServer:
    """The parts of earthd.Server blockcache uses, one session folder each."""
    hm_name = earthd.Server.hm_name
    ter_name = earthd.Server.ter_name
    base_name = earthd.Server.base_name

    def __init__(self, root, sid):
        self.cache_dir = os.path.join(root, "cache")
        self.user_dir = os.path.join(root, "user")
        self.out_dir = os.path.join(root, "temp", sid)
        self.vfs_dir = f"/temp/beamngEarth/{sid}"
        os.makedirs(self.out_dir, exist_ok=True)
        self.near_visual = "detail"
        self.imagery = object()
        self.ter_serial = itertools.count(1)
        self.base_pending = {}
        self.base_stale = set()

    def build(self, anchor, cx, cy):
        """A block's files as make_heightmap writes them, and its info."""
        key = (anchor.id, cx, cy)
        hm = self.hm_name(key)
        ter = self.ter_name(key)
        masks = surfacemod.mask_names(hm[:-4])
        files = {hm: b"heights", ter: b"terrain", self.base_name(key): b"photo"}
        files.update({m: f"mask{i}".encode() for i, m in enumerate(masks)})
        for name, data in files.items():
            with open(os.path.join(self.out_dir, name), "wb") as f:
                f.write(data)
        dds = blockcache._dds_path(self, self.base_name(key))
        os.makedirs(os.path.dirname(dds), exist_ok=True)
        with open(dds, "wb") as f:
            f.write(b"packed")
        return {"cx": cx, "cy": cy, "zBase": 1500.0, "maxHeight": 300.0, "q": 1 / 64, "groundZ": None,
                "layers": masks, "surfaceComplete": True, "ter": ter, "mats": [0, 3], "base": self.base_name(key),
                "stages": {"heights": 1.0}, "seconds": 9.0, "zoom": 15}


class BlockCacheTest(unittest.TestCase):
    def test_a_block_comes_back_under_the_new_session_names(self):
        with tempfile.TemporaryDirectory() as d:
            a = FakeServer(d, "s1")
            anchor = types.SimpleNamespace(id=3, hm_zoom=15)
            info = a.build(anchor, 128, -256)
            k = revisit.key("block", 39.9, -105.0, 128, -256)
            self.assertTrue(blockcache.store(a, anchor, 128, -256, k, info, revisit.faults()))
            b = FakeServer(d, "s2")
            b.base_stale.add((7, 128, -256))
            other = types.SimpleNamespace(id=7, hm_zoom=15)
            got = blockcache.load(b, other, 128, -256, k)
            self.assertIsNotNone(got)
            self.assertEqual(got["ter"], "hm_7_128_-256_1.ter")
            self.assertEqual(got["base"], "hm_7_128_-256_base.png")
            self.assertEqual(got["layers"], surfacemod.mask_names("hm_7_128_-256"))
            self.assertEqual((got["zBase"], got["mats"], got["surfaceComplete"]), (1500.0, [0, 3], True))
            self.assertIn("revisit", got["stages"])
            def read(n):
                with open(os.path.join(b.out_dir, n), "rb") as f:
                    return f.read()
            self.assertEqual(read("hm_7_128_-256.png"), b"heights")
            self.assertEqual(read(got["ter"]), b"terrain")
            self.assertEqual(read(got["base"]), b"photo")
            self.assertEqual(read(got["layers"][1]), b"mask1")
            with open(blockcache._dds_path(b, got["base"]), "rb") as f:
                self.assertEqual(f.read(), b"packed")
            self.assertNotIn((7, 128, -256), b.base_stale)
            self.assertIsNone(blockcache.load(b, other, 0, 0, revisit.key("block", 0)))

    def test_incomplete_blocks_are_not_kept(self):
        with tempfile.TemporaryDirectory() as d:
            a = FakeServer(d, "s1")
            anchor = types.SimpleNamespace(id=3, hm_zoom=15)
            k = revisit.key("block", 1)
            f0 = revisit.faults()
            info = a.build(anchor, 0, 0)
            revisit.fault()                                     # something it read failed
            self.assertFalse(blockcache.store(a, anchor, 0, 0, k, info, f0))
            for change in ({"surfaceComplete": False}, {"roadsComplete": False}, {"base": None},
                           {"stages": {"baseLater": 1.0}}):
                self.assertFalse(blockcache.store(a, anchor, 0, 0, k, dict(info, **change), revisit.faults()), change)
            a.base_stale.add((3, 0, 0))                         # its photo still waits on imagery
            self.assertFalse(blockcache.store(a, anchor, 0, 0, k, info, revisit.faults()))
            self.assertIsNone(revisit.load(a.cache_dir, "block", k))


if __name__ == "__main__":
    unittest.main()
