"""The gate's tools offline: tools/lint.py (duplicate definitions in Python and Lua, the locals warning, the Lua
compile through lupa), tools/lua_tests.py (the bytecode listing test_scoping reads, a test file run), tools/run_tests.py
(the modules a diff selects) and tools/perf_trend.py (the perflog lines, the metrics, the budget, the page). Nothing
here talks to the game or the network. The LuaJIT parts skip without lupa (server/requirements-dev.txt)."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import lint  # noqa: E402
import lua_tests  # noqa: E402
import perf_trend  # noqa: E402
import run_tests  # noqa: E402

try:
    from lupa import luajit21  # noqa: F401
    HAVE_LUAJIT = True
except ImportError:
    HAVE_LUAJIT = False

MINUTE = ("2026-10-28 14:51:45,004 INFO earthd: game: perf minute {fps} fps, 1% low {low}, worst {worst} ms, "
          "{hit} hitches (worst: Lua 966.1), {st} stutters 50-100 ms at 39.74020,-104.98735, {mph} mph, "
          "block 1024,3328 | Lua 204 MB")
HITCH = ("2026-10-28 14:50:45,813 INFO earthd: game: perf hitch 213 ms at 40.03966,-104.98078, 0 mph, block -128,0, "
         "loading after a teleport | mod: sea+trees 13.9 | engine: Ticking 187.9, Lua 14.3 | doing: far field loading"
         " | (2 more hitches since the last line)")


def minute(i, fps=88, low=60, worst=40, hit=0, st=0, mph=50, day="2026-10-28", hour=14):
    line = MINUTE.format(fps=fps, low=low, worst=worst, hit=hit, st=st, mph=mph)
    return line.replace("2026-10-28 14:51:45", f"{day} {hour:02d}:{i:02d}:45")


class LintTest(unittest.TestCase):
    def test_python_duplicate_defs(self):
        src = "def a():\n    pass\n\ndef b():\n    pass\n\ndef a():\n    pass\n\nclass C:\n    def m(self): pass\n" \
              "    def m(self): pass\n    @property\n    def p(self): return 1\n    @p.setter\n    def p(self, v): pass\n"
        f = lint.duplicate_defs(src, "x.py")
        self.assertEqual(len(f), 2, f)
        self.assertIn("x.py:7: a defined again (first at line 1)", f[0])
        self.assertIn("C.m defined again", f[1])
        self.assertEqual(lint.duplicate_defs("x = None\ndef x():\n    pass\n"), [])
        self.assertIn("syntax error", lint.duplicate_defs("def (:\n", "y.py")[0])

    def test_lua_duplicates_and_locals(self):
        src = "local M = {}\nfunction M.a() end\nlocal function h() end\nfunction M.a() end\nM.b = function() end\n" \
              "M.b = function() end\nlocal function h() end\nreturn M\n"
        f, w = lint.lua_findings(src, "m.lua")
        self.assertEqual([x.split(": ")[1].split(" (")[0] for x in f],
                         ["function M.a defined again", "function M.b defined again", "function h defined again"])
        self.assertEqual(w, [])
        self.assertEqual(lint.lua_findings(src, "test_m.lua", test=True)[0], [])     # a test re-stubs on purpose
        many = "\n".join(f"local v{i} = {i}" for i in range(lint.LOCALS_WARN)) + "\nlocal a, b = 1, 2\n"
        f, w = lint.lua_findings(many, "big.lua")
        self.assertEqual(f, [])
        self.assertEqual(len(w), 1)
        self.assertIn(f"{lint.LOCALS_WARN + 2} locals", w[0])

    @unittest.skipUnless(HAVE_LUAJIT, "lupa (LuaJIT) not installed")
    def test_lua_compile(self):
        comp = lint.lua_compiler()
        with tempfile.TemporaryDirectory() as d:
            ok, bad, many = (os.path.join(d, n) for n in ("ok.lua", "bad.lua", "many.lua"))
            Path(ok).write_text("local M = {}\nreturn M\n")
            Path(bad).write_text("local M = {\nreturn M\n")
            Path(many).write_text("\n".join(f"local v{i} = {i}" for i in range(201)) + "\n")
            self.assertIsNone(comp(ok))
            self.assertIn("expected", str(comp(bad)))
            self.assertIn("200 local variables", str(comp(many)))
            f, w = lint.check_lua([ok, bad, many])
            self.assertEqual(len(f), 2)
            self.assertEqual(len(w), 1)


@unittest.skipUnless(HAVE_LUAJIT, "lupa (LuaJIT) not installed")
class LuaTestsTest(unittest.TestCase):
    def test_listing_has_global_reads_and_writes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.lua")
            Path(p).write_text("local M = {}\nfunction M.f() return later + 1 end\nwritten = 2\nlocal later = 1\n"
                               "return M\n")
            text, err = lua_tests.listing(p)
            self.assertIsNone(err)
            self.assertIn('GGET', text)
            self.assertRegex(text, r'GGET.*"later"')
            self.assertRegex(text, r'GSET.*"written"')
            self.assertNotRegex(text, r'"M"')
            text, err = lua_tests.listing(os.path.join(d, "none.lua"))
            self.assertIsNone(text)
            self.assertTrue(err)

    def test_run_one_pass_fail_error(self):
        with tempfile.TemporaryDirectory() as d:
            for name, body in (("pass", "print('a') os.exit(0)"), ("fail", "print('FAIL: x') os.exit(1)"),
                               ("err", "error('boom')"), ("find", "local p = io.popen('find \"" + d.replace("\\", "/")
                                                                  + "\" -name \"*.lua\"') local n = 0 "
                                                                  "for _ in p:lines() do n = n + 1 end p:close() "
                                                                  "print('files ' .. n) os.exit(n >= 4 and 0 or 1)")):
                Path(os.path.join(d, f"test_{name}.lua")).write_text(body + "\n")
            self.assertEqual(lua_tests.run_one(os.path.join(d, "test_pass.lua")), (0, ["a"]))
            self.assertEqual(lua_tests.run_one(os.path.join(d, "test_fail.lua"))[0], 1)
            code, lines = lua_tests.run_one(os.path.join(d, "test_err.lua"))
            self.assertEqual(code, 2)
            self.assertIn("boom", "\n".join(lines))
            code, lines = lua_tests.run_one(os.path.join(d, "test_find.lua"))
            self.assertEqual((code, lines), (0, ["files 4"]))

    def test_the_mods_scoping_test_runs_for_real(self):
        """test_scoping reads `luajit -bl` through io.popen: under the runner it gets a listing with GGET lines."""
        code, lines = lua_tests.run_one(str(ROOT / "mod" / "tests" / "test_scoping.lua"))
        self.assertEqual(code, 0, lines)
        self.assertTrue(any("2 passed, 0 failed" in ln for ln in lines), lines)


class RunTestsSelectionTest(unittest.TestCase):
    TESTS = {
        "test_roads": "import roads\nimport unittest\n",
        "test_gasstations": "import gasstations as gs\n",
        "test_benchmark_tool": "import benchmark as bm\n",
        "test_e2e": "import modsim\nfrom earthd import Server\n",
        "test_apppaths": "import apppaths\n",
    }
    MODS = {
        "roads": "import geodesy\nimport staging\n", "gasstations": "import roads\n", "geodesy": "import math\n",
        "benchmark": "import mcp\n", "modsim": "import rebase\n", "earthd": "import roads\nimport gasstations\n",
        "apppaths": "", "mcp": "",
    }

    def test_imports_of(self):
        self.assertEqual(run_tests.imports_of("import os, sys\nimport numpy as np\nfrom roads import x\n  import a.b"),
                         {"os", "sys", "numpy", "roads", "a"})

    def test_direct_and_importers(self):
        picked, why = run_tests.select_modules(["server/geodesy.py"], self.TESTS, self.MODS)
        # geodesy -> roads (one hop) -> test_roads; gasstations and earthd import roads, a second hop: not taken
        self.assertEqual(picked, ["test_roads"])
        self.assertIn("roads", why["test_roads"])
        picked, _ = run_tests.select_modules(["server/roads.py"], self.TESTS, self.MODS)
        self.assertEqual(picked, ["test_e2e", "test_gasstations", "test_roads"])

    def test_tool_and_test_itself(self):
        picked, _ = run_tests.select_modules(["tools/benchmark.py", "server/tests/test_apppaths.py"], self.TESTS,
                                             self.MODS)
        self.assertEqual(picked, ["test_apppaths", "test_benchmark_tool"])
        picked, _ = run_tests.select_modules(["tools/mcp.py"], self.TESTS, self.MODS)
        self.assertEqual(picked, ["test_benchmark_tool"])

    def test_nothing_relevant(self):
        picked, _ = run_tests.select_modules(["mod/beamng_earth/lua/x.lua", "docs/A.md", "README.md"], self.TESTS,
                                             self.MODS)
        self.assertEqual(picked, [])
        self.assertEqual(run_tests.select_modules([], self.TESTS, self.MODS)[0], [])

    def test_root_script(self):
        picked, _ = run_tests.select_modules(["apppaths.py"], self.TESTS, self.MODS)
        self.assertEqual(picked, ["test_apppaths"])


class LogRotationTest(unittest.TestCase):
    def test_server_log_kept_for_the_trend(self):
        sys.path.insert(0, str(ROOT))
        import launcher
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "server.log")
            launcher.rotate_log(p, keep=3)                                  # nothing there yet: nothing happens
            for i in range(5):
                Path(p).write_text(f"run {i}\n")
                launcher.rotate_log(p, keep=3)
            self.assertFalse(os.path.exists(p))
            self.assertEqual([Path(os.path.join(d, f"server.{i}.log")).read_text() for i in (1, 2, 3)],
                             ["run 4\n", "run 3\n", "run 2\n"])
            self.assertFalse(os.path.exists(os.path.join(d, "server.4.log")))


class PerfTrendTest(unittest.TestCase):
    def lines(self):
        out = ["2026-10-28 13:00:00,000 INFO earthd listening on 127.0.0.1:47613 session x"]
        out += [minute(i, hour=13) for i in range(5)]                       # drive 1: 5 clean minutes
        out.append("2026-10-28 13:30:00,000 INFO earthd listening on 127.0.0.1:47613 session y")
        out += [minute(0, mph=0, fps=40, low=10, hour=14)]                  # standing at the teleport: not driving
        out.append(HITCH.replace("14:50:45", "14:00:50"))
        out += [minute(i, fps=70, low=24, worst=351, hit=3, hour=14) for i in range(1, 5)]
        out.append("2026-10-28 15:00:00,000 INFO earthd: nothing")
        out += [minute(i, hour=16) for i in range(2)]                       # too short: left out
        return out

    def test_parse_and_metrics(self):
        drives = perf_trend.parse_lines(self.lines(), "server.log")
        self.assertEqual(len(drives), 2)
        a, b = (perf_trend.metrics(d) for d in drives)
        self.assertEqual((a["minutes"], a["fps"], a["low"], a["hitches"]), (5, 88.0, 60.0, 0))
        self.assertEqual((b["minutes"], b["driving"]), (5, 4))
        self.assertEqual((b["fps"], b["low"], b["low_min"], b["worst"]), (70.0, 24.0, 24, 351))
        self.assertEqual((b["hitches"], b["hitches_per_min"], b["big_hitches"]), (12, 3.0, 0))
        self.assertEqual(b["causes"], [])                                   # the hitch line came standing (0 mph)
        self.assertEqual(drives[1]["hitches"][0]["mod"], "sea+trees 13.9")
        h = drives[1]["hitches"][0]
        h["mph"] = 30
        self.assertEqual(perf_trend.metrics(drives[1])["causes"], [("Ticking", 3)])     # the line and its 2 more

    def test_budget_and_worse(self):
        drives = perf_trend.parse_lines(self.lines())
        a, b = (perf_trend.metrics(d) for d in drives)
        self.assertEqual(perf_trend.budget_breaches(a, 60, 40, 1.0, 500), [])
        br = perf_trend.budget_breaches(b, 60, 40, 1.0, 500)
        self.assertEqual(len(br), 2)
        self.assertIn("1% low 24", br[0])
        self.assertIn("hitches a minute", br[1])
        w = perf_trend.worse_than(b, a)
        self.assertEqual(len(w), 3)
        self.assertEqual(perf_trend.worse_than(a, b), [])

    def test_gap_splits_a_drive(self):
        lines = [minute(i, hour=10) for i in range(4)] + [minute(i, hour=12) for i in range(4)]
        self.assertEqual(len(perf_trend.parse_lines(lines)), 2)

    def test_html_and_cli(self):
        with tempfile.TemporaryDirectory() as d:
            log, page, js = (os.path.join(d, n) for n in ("server.log", "t.html", "t.json"))
            Path(log).write_text("\n".join(self.lines()) + "\n", encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                rc = self.cli(log, page, js)
            self.assertEqual(rc, 1)                                         # --budget: the newest drive breaks it
            text = Path(page).read_text(encoding="utf-8")
            self.assertIn("<svg", text)
            self.assertIn("hitches a minute", text)
            data = json.loads(Path(js).read_text(encoding="utf-8"))
            self.assertEqual(len(data["drives"]), 2)
            self.assertTrue(data["breaches"])
            self.assertIn("newest drive", out.getvalue())

    def cli(self, log, page, js):
        argv = sys.argv
        sys.argv = ["perf_trend", log, "--html", page, "--json", js, "--budget"]
        try:
            return perf_trend.main()
        finally:
            sys.argv = argv


if __name__ == "__main__":
    unittest.main()
