"""The ungraded far tiles' geometry is keyed by the modules that build it (blockworker.FAR_GEOMETRY_MODULES,
revisit.code_key(modules)): the modules a build runs are all listed (traced), a change to another module keeps the
key, the graded levels keep the whole code's key, and such keys skip the baked generations."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import blockworker  # noqa: E402
import farfield as ff  # noqa: E402
import geodesy  # noqa: E402
import revisit  # noqa: E402
from test_farfield import FakeDem, LAT0, LON0  # noqa: E402

SERVER = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FarGeometryKeyTest(unittest.TestCase):
    def setUp(self):
        self._s = blockworker._s
        blockworker._s = mock.Mock(airports=None, sea=None)
        self.addCleanup(setattr, blockworker, "_s", self._s)

    def test_the_build_runs_only_the_listed_modules(self):
        ran = set()

        def prof(frame, event, arg):
            if event == "call":
                f = frame.f_code.co_filename
                if os.path.normcase(os.path.dirname(os.path.abspath(f))) == SERVER:
                    ran.add(os.path.splitext(os.path.basename(f))[0])
        layout = ff.Layout(64, 512, True, (256, 256, 256, 512))
        frame = geodesy.EnuFrame(LAT0, LON0)
        for level in (1, 2, 4):
            tile = ff.Tile(level, 0, -1)
            sys.setprofile(prof)
            try:
                ff.build_tile(frame, FakeDem(), tile, ff.zoom_for_level(layout, LAT0, level), 1 / 64, layout=layout)
            finally:
                sys.setprofile(None)
        self.assertTrue(ran, "the trace saw the build")
        self.assertLessEqual(ran, set(blockworker.FAR_GEOMETRY_MODULES),
                             f"modules the build ran that its key does not hash: {ran - set(blockworker.FAR_GEOMETRY_MODULES)}")

    def test_every_import_is_listed_or_io(self):
        # the server modules the listed ones import, transitively: each is hashed, or an allowed I/O helper
        import ast
        have = {os.path.splitext(n)[0] for n in os.listdir(SERVER) if n.endswith(".py")}
        listed, io = set(blockworker.FAR_GEOMETRY_MODULES), set(blockworker.FAR_GEOMETRY_IO)
        todo, seen, bad = list(listed), set(), set()
        while todo:
            m = todo.pop()
            if m in seen:
                continue
            seen.add(m)
            with open(os.path.join(SERVER, m + ".py"), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for n in ast.walk(tree):
                names = ([a.name for a in n.names] if isinstance(n, ast.Import) else
                         [n.module] if isinstance(n, ast.ImportFrom) and n.module and n.level == 0 else [])
                for name in names:
                    top = name.split(".")[0]
                    if top not in have or top in io:
                        continue
                    if top not in listed:
                        bad.add(f"{m} imports {top}")
                    todo.append(top)
        self.assertFalse(bad, f"imported but neither hashed nor allowed: {sorted(bad)}")

    def test_key_hashes_only_its_modules(self):
        mods = blockworker.far_geometry_modules(2)
        self.assertIn("farfield", mods)
        self.assertTrue(any(m.startswith("src:") and "def _far_geometry" in m for m in mods))
        read = []
        real_open = open

        def spy(path, *a, **kw):
            read.append(os.path.basename(path))
            return real_open(path, *a, **kw)
        revisit._mod_codes.clear()
        with mock.patch("builtins.open", spy):
            k = revisit.code_key(mods)
        self.assertNotEqual(k, revisit.code_key())
        py = {n for n in read if n.endswith(".py")}
        self.assertEqual(py, {m + ".py" for m in mods if not m.startswith("src:")}, "roads.py, grading.py ... not read")
        self.assertEqual(revisit.code_key(mods), k, "stable")

    def test_graded_levels_keep_the_whole_code(self):
        self.assertIsNone(blockworker.far_geometry_modules(0))
        self.assertIsNone(blockworker.far_geometry_modules(-1))

    def test_switched_on_parts_join_the_key(self):
        blockworker._s = mock.Mock(airports=object(), sea=object())
        mods = blockworker.far_geometry_modules(1)
        self.assertIn("airportgrade", mods)
        self.assertIn("seacarve", mods)

    def test_module_keys_skip_the_generations(self):
        k = revisit.key("fargeo", 39.7, -105.0, (2, 0, 0), modules=("farfield",))
        self.assertIn(k, revisit._stable)
        self.assertNotIn(revisit.key("fargeo", 39.7, -105.0, (0, 0, 0)), revisit._stable)


if __name__ == "__main__":
    unittest.main()
