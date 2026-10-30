"""The revisit cache (revisit.py): a tile's files kept and written back under another session's names; builds that
read something missing are not kept."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import netfetch  # noqa: E402
import revisit  # noqa: E402


class RevisitTest(unittest.TestCase):
    def test_an_item_comes_back_and_names_swap(self):
        with tempfile.TemporaryDirectory() as d:
            text = json.dumps({"road": {"shape": "/temp/s1/road_3_1_-2.cdae"},
                               "bld": {"shape": "/temp/s1/bld_3_1_-2.cdae"}}, separators=(",", ":"))
            self.assertEqual(revisit.shapes_in(text, "/temp/s1"), ["bld_3_1_-2.cdae", "road_3_1_-2.cdae"])
            mine = [("/temp/s1/", "@V@/"), ("_3_1_-2", "_@A@_1_-2")]
            k = revisit.key("roads", 39.28, -76.56, 1, -2)
            self.assertTrue(revisit.store(d, "roads", k, {revisit.swap("roads_3_1_-2.json", mine):
                                                          revisit.swap(text, mine).encode(), "x.bin": b"mesh"},
                                          {"n": 5}))
            blobs, extra = revisit.load(d, "roads", k)
            self.assertEqual(extra, {"n": 5})
            self.assertEqual(blobs["x.bin"], b"mesh")
            back = [("@V@/", "/temp/s2/"), ("_@A@_1_-2", "_7_1_-2")]
            self.assertIn("roads_@A@_1_-2.json", blobs)
            self.assertEqual(revisit.swap("roads_@A@_1_-2.json", back), "roads_7_1_-2.json")
            tile = json.loads(revisit.swap(blobs["roads_@A@_1_-2.json"].decode(), back))
            self.assertEqual(tile["road"]["shape"], "/temp/s2/road_7_1_-2.cdae")
            self.assertIsNone(revisit.load(d, "roads", revisit.key("roads", 0, 0, 1, -2)))

    def test_shapes_found_with_either_separators(self):
        """A far building tile's JSON (buildings.far_tile: json.dumps' default separators) names its mesh too."""
        data = {"v": 1, "shape": "/temp/s1/bldfar_3_1_-2.cdae", "pos": [256.0, -512.0, 0.0]}
        for text in (json.dumps(data), json.dumps(data, separators=(",", ":"))):
            self.assertEqual(revisit.shapes_in(text, "/temp/s1"), ["bldfar_3_1_-2.cdae"])

    def test_failed_fetches_mark_builds_incomplete_but_missing_data_does_not(self):
        n = revisit.faults()
        netfetch.NotFound("404")                # (no data there: as it should be)
        netfetch.Deferred("dropped")
        self.assertEqual(revisit.faults(), n)
        netfetch.FetchFailed("timed out")
        self.assertEqual(revisit.faults(), n + 1)

    def test_a_cells_road_facts_are_shared(self):
        import roads
        from test_roads import features
        with tempfile.TemporaryDirectory() as d:
            old = revisit.CACHE_DIR
            revisit.CACHE_DIR = d
            try:
                def cell():
                    f = features([[(0, 0), (100, 0)], [(0, 50), (100, 50)]], ["residential", "primary"])
                    f.__dict__["_bnge_cell"] = ("15/1/2", "roads", "v")
                    return f
                a = roads.road_specs(cell())
                ra = [roads.level_ranges(cell(), i) for i in range(2)]
                b_feats = cell()            # (another process: its own decoded cell)
                from unittest import mock
                with mock.patch.object(roads, "_road_specs", side_effect=AssertionError("made again")), \
                        mock.patch.object(roads, "_level_ranges", side_effect=AssertionError("made again")):
                    self.assertEqual(roads.road_specs(b_feats), a)
                    self.assertEqual([roads.level_ranges(b_feats, i) for i in range(2)], ra)
                self.assertEqual(b_feats.__dict__["_road_specs"][(None, None, False)], a)
                self.assertEqual([roads.level_ranges(b_feats, i) for i in range(2)], ra)
                self.assertEqual(b_feats.__dict__.get("_ranges_state"), "done")
            finally:
                revisit.CACHE_DIR = old

    def test_a_baked_routes_items_are_pinned(self):
        """An item built in a baked route's anchor goes to the pinned folder (never cut back), another to the cache;
        one kept before its route was baked moves there when read; both read back."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, revisit.PIN_SUBDIR))
            with open(os.path.join(d, revisit.PIN_SUBDIR, revisit.ANCHORS), "w") as f:
                json.dump({"anchors": [[40.1, -104.94]]}, f)
            on = revisit.key("roads", round(40.1, 7), round(-104.94, 7), 1, -2)
            off = revisit.key("roads", 40.11, -104.94, 1, -2)
            cell = revisit.key("cellfacts", "roads", "14/1/2")
            for k in (on, off, cell):
                self.assertTrue(revisit.store(d, "roads", k, {"a": b"x"}))
            where = lambda k, sub: os.path.exists(os.path.join(d, sub, "roads", k[:2], k + ".zip"))  # noqa: E731
            self.assertTrue(where(on, revisit.PIN_SUBDIR) and not where(on, revisit.SUBDIR))
            self.assertTrue(where(off, revisit.SUBDIR) and not where(off, revisit.PIN_SUBDIR))
            self.assertTrue(where(cell, revisit.SUBDIR), "not an anchor's item: not pinned")
            self.assertEqual(revisit.load(d, "roads", on)[0], {"a": b"x"})
            self.assertEqual(revisit.load(d, "roads", off)[0], {"a": b"x"})
            self.assertTrue(revisit.has(d, "roads", on) and revisit.has(d, "roads", off))
            self.assertFalse(revisit.has(d, "roads", revisit.key("roads", 1.0, 2.0)))
            with open(os.path.join(d, revisit.PIN_SUBDIR, revisit.ANCHORS), "w") as f:
                json.dump({"anchors": [[40.1, -104.94], [40.11, -104.94]]}, f)
            os.utime(os.path.join(d, revisit.PIN_SUBDIR, revisit.ANCHORS), ns=(1, 10 ** 18))
            self.assertEqual(revisit.load(d, "roads", off)[0], {"a": b"x"})
            self.assertTrue(where(off, revisit.PIN_SUBDIR) and not where(off, revisit.SUBDIR), "moved when read")

    def test_a_baked_route_outlives_a_code_change(self):
        """After a code change an anchor's items come from the newest generation that baked it, all of them (a road
        tile and the ground under it from the same code), until a bake run under the new code replaces it."""
        saved = (list(revisit._code), revisit.GENS)

        def as_code(code, gens):
            revisit._code[:] = [code]
            revisit.GENS = gens
            revisit._gen_of.clear()

        try:
            with tempfile.TemporaryDirectory() as d:
                os.makedirs(os.path.join(d, revisit.PIN_SUBDIR))
                with open(os.path.join(d, revisit.PIN_SUBDIR, revisit.ANCHORS), "w") as f:
                    json.dump({"anchors": [[40.1, -104.94], [40.2, -104.94]]}, f)
                x, y = (40.1, -104.94), (40.2, -104.94)
                as_code("a" * 16, "bake")
                for at in (x, y):
                    for kind in ("roads", "block"):
                        self.assertTrue(revisit.store(d, kind, revisit.key(kind, *at, 1), {"v": b"A"}))
                as_code("b" * 16, "on")
                self.assertEqual(revisit.load(d, "roads", revisit.key("roads", *x, 1))[0], {"v": b"A"})
                self.assertIsNone(revisit.load(d, "roads", revisit.key("roads", *x, 2)), "not baked: built")
                self.assertIsNone(revisit.load(d, "roads", revisit.key("block", *x, 1)), "another kind's key")
                as_code("b" * 16, "off")
                self.assertIsNone(revisit.load(d, "roads", revisit.key("roads", *x, 1)))
                as_code("b" * 16, "bake")       # (the next bake run builds anew: x's road tile, not its block yet)
                self.assertIsNone(revisit.load(d, "roads", revisit.key("roads", *x, 1)))
                self.assertTrue(revisit.store(d, "roads", revisit.key("roads", *x, 1), {"v": b"B"}))
                as_code("b" * 16, "on")
                self.assertEqual(revisit.load(d, "roads", revisit.key("roads", *x, 1))[0], {"v": b"B"})
                self.assertIsNone(revisit.load(d, "block", revisit.key("block", *x, 1)),
                                  "never A's block under B's road")
                self.assertEqual(revisit.load(d, "block", revisit.key("block", *y, 1))[0], {"v": b"A"})
                as_code("c" * 16, "on")         # (another change: the newest generation that baked each anchor)
                self.assertEqual(revisit.load(d, "roads", revisit.key("roads", *x, 1))[0], {"v": b"B"})
                self.assertEqual(revisit.load(d, "roads", revisit.key("roads", *y, 1))[0], {"v": b"A"})
                as_code("b" * 16, "on")         # (an anchor's generation is kept for the process: marks after it do not
                y_block = revisit.key("block", *y, 1)   # switch it)
                self.assertEqual(revisit.load(d, "block", y_block)[0], {"v": b"A"})
                revisit.GENS = "bake"
                revisit.store(d, "roads", revisit.key("roads", *y, 1), {"v": b"B"})
                revisit.GENS = "on"
                self.assertEqual(revisit.load(d, "roads", revisit.key("roads", *y, 1))[0], {"v": b"A"})
        finally:
            revisit._code[:], revisit.GENS = saved
            revisit._gen_of.clear()

    def test_sidecar_lines_are_written_compacted_and_dropped(self):
        """store(where=...) appends one line to this process's sidecar; compact() folds older files into one, the
        last line per item, without the items whose zip is gone; a file written after `before` stays as it is."""
        import time
        with tempfile.TemporaryDirectory() as d:
            sig = revisit.sig(sidewalks=True, md={"overture": "2026-09-23.1"})
            ks = [revisit.key("roads", 39.28, -76.56, i) for i in range(3)]
            for i, k in enumerate(ks):
                self.assertTrue(revisit.store(d, "roads", k, {"a": b"x"},
                                              where=revisit.where(39.28, -76.56, 39.28 + i * 0.01, -76.56, 39.29, -76.55,
                                                                  sig)))
            self.assertTrue(revisit.store(d, "far", revisit.key("far", 1), {"a": b"x"}))     # (no where: no line)
            wd = os.path.join(d, revisit.SUBDIR, revisit.WHERE_DIR)
            mine = os.listdir(wd)
            self.assertEqual(len(mine), 1)
            with open(os.path.join(wd, mine[0])) as f:
                rows = [revisit.parse_where(line) for line in f]
            self.assertEqual([r[3] for r in rows], ks)
            self.assertEqual(rows[0][:3], (revisit.code_key(), sig, "roads"))
            self.assertAlmostEqual(rows[2][6], 39.30)
            self.assertIsNone(revisit.parse_where("damaged\tline\n"))
            # an older run's file: two lines for one item (the last wins), one item whose zip is gone
            old = os.path.join(wd, "1_1.tsv")
            with open(os.path.join(wd, mine[0])) as f:
                lines = f.readlines()
            gone = revisit.key("roads", 0, 0, 9)
            with open(old, "w") as f:
                f.writelines(lines + [lines[0], lines[0].replace(ks[0], gone)])
            past = time.time() - 100
            os.utime(old, (past, past))
            self.assertEqual(revisit.compact(d, time.time() - 50), 3)
            self.assertTrue(revisit.COMPACTED.is_set())
            names = sorted(os.listdir(wd))
            self.assertIn(mine[0], names, "this process's own file stays")
            self.assertNotIn("1_1.tsv", names)
            comp = [n for n in names if n.startswith("c")]
            with open(os.path.join(wd, comp[0])) as f:
                kept = [revisit.parse_where(line)[3] for line in f]
            self.assertEqual(sorted(kept), sorted(ks), "the line whose zip is gone is dropped")

    def test_settings_signature(self):
        cur = {"sidewalks": True, "md": {"overture": "2026-09-23.1"}, "tone": 1.0}
        s = revisit.sig(sidewalks=True, md={"overture": "2026-09-23.1"})
        self.assertTrue(revisit.sig_current(s, cur))
        self.assertFalse(revisit.sig_current(s, dict(cur, sidewalks=False)), "a setting changed")
        self.assertFalse(revisit.sig_current(revisit.sig(lanes=1), cur), "a setting the server does not name")
        self.assertTrue(revisit.sig_current("", cur))
        self.assertEqual(revisit.sig(b=1, a=2), revisit.sig(a=2, b=1))

    def test_the_key_holds_the_code(self):
        self.assertEqual(revisit.key("roads", 1, 2), revisit.key("roads", 1, 2))
        self.assertNotEqual(revisit.key("roads", 1, 2), revisit.key("roads", 1, 3))
        self.assertEqual(len(revisit.code_key()), 16)


if __name__ == "__main__":
    unittest.main()
