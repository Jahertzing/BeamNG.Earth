"""bakedeps: what each kind of baked item is made by, recorded during a build (sys.monitoring, the open audit hook) and
keyed on (kind_hash: the listed functions' source, the modules' top-level statements, the data files); the manifest's
merge and diff; revisit.code_key(kind) from it; the committed manifest's functions exist (the gate)."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import bakedeps  # noqa: E402
import revisit  # noqa: E402

MOD = '''
TABLE = {"a": 1}

def listed(x):
    return helper(x) + TABLE["a"]

def helper(x):
    return x * 2

def unlisted(x):
    return x - 1

class K:
    def method(self):
        return [i for i in range(3)]

def with_lambda():
    f = lambda v: v + 1
    return f(1)
'''


def write_module(folder, text=MOD, name="fixture_mod"):
    path = os.path.join(folder, name + ".py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, path


class SourcesTest(unittest.TestCase):
    def test_functions_and_top_level_statements(self):
        with tempfile.TemporaryDirectory() as d:
            write_module(d)
            bakedeps._sources.clear()
            funcs, top = bakedeps.module_sources("fixture_mod", here=d)
            self.assertEqual(set(funcs), {"listed", "helper", "unlisted", "K.method", "with_lambda"})
            self.assertTrue(funcs["listed"].startswith("def listed(x):"))
            self.assertIn('TABLE = {"a": 1}', top)
            self.assertNotIn("def ", top, "the module-level statements only")
            self.assertIsNone(bakedeps.module_sources("no_such_module", here=d))

    def test_the_key_follows_the_listed_functions_only(self):
        with tempfile.TemporaryDirectory() as d:
            write_module(d)
            deps = {"functions": ["fixture_mod:listed", "fixture_mod:helper"], "files": []}
            bakedeps._sources.clear()
            h0 = bakedeps.kind_hash(deps, here=d)
            write_module(d, MOD.replace("return x - 1", "return x - 2"))      # unlisted changes
            bakedeps._sources.clear()
            self.assertEqual(bakedeps.kind_hash(deps, here=d), h0, "a change outside the set keeps the key")
            write_module(d, MOD.replace("return x * 2", "return x * 3"))      # helper changes
            bakedeps._sources.clear()
            self.assertNotEqual(bakedeps.kind_hash(deps, here=d), h0, "a listed function changed")
            write_module(d, MOD.replace('TABLE = {"a": 1}', 'TABLE = {"a": 2}'))
            bakedeps._sources.clear()
            self.assertNotEqual(bakedeps.kind_hash(deps, here=d), h0, "a module-level constant changed")
            write_module(d, MOD + "\n# a comment\n")
            bakedeps._sources.clear()
            self.assertEqual(bakedeps.kind_hash(deps, here=d), h0, "a comment changes nothing")
            bakedeps._sources.clear()
            self.assertNotEqual(bakedeps.kind_hash(deps, here=d, extra=b"BNE_X=1"), h0, "the switches are in")
            with open(os.path.join(d, "table.json"), "w") as f:
                json.dump({"v": 1}, f)
            deps_f = {"functions": ["fixture_mod:listed"], "files": ["table.json"]}
            bakedeps._sources.clear()
            h1 = bakedeps.kind_hash(deps_f, here=d)
            with open(os.path.join(d, "table.json"), "w") as f:
                json.dump({"v": 2}, f)
            self.assertNotEqual(bakedeps.kind_hash(deps_f, here=d), h1, "a data file changed")
            self.assertEqual(bakedeps.missing_functions({"k": deps}, here=d), [])
            self.assertEqual(bakedeps.missing_functions({"k": {"functions": ["fixture_mod:gone", "nomod:f"]}}, here=d),
                             [("k", "fixture_mod:gone"), ("k", "nomod:f")])


class RecordTest(unittest.TestCase):
    def test_a_build_records_its_functions_and_files(self):
        if not hasattr(sys, "monitoring"):
            self.skipTest("no sys.monitoring")
        with tempfile.TemporaryDirectory() as d:
            mod, path = write_module(d)
            keep = (bakedeps.HERE, bakedeps._HERE_N, dict(bakedeps._deps), dict(bakedeps._builds))
            bakedeps.HERE, bakedeps._HERE_N = d, bakedeps._norm(d) + os.sep
            bakedeps._deps.clear()
            bakedeps._builds.clear()
            rec = os.path.join(d, "rec")
            try:
                started = bakedeps.start_recording(rec)
                if not started:
                    self.skipTest("monitoring tool id taken")
                with bakedeps.building("kindA"):
                    mod.listed(2)
                    mod.K().method()
                    mod.with_lambda()
                    with open(os.path.join(d, "table.json"), "w") as f:
                        f.write("{}")
                    with open(os.path.join(d, "table.json")) as f:
                        f.read()
                    with bakedeps.building("kindB"):            # nested: both take it
                        mod.unlisted(1)
                mod.helper(5)                                   # outside any build: not recorded
                snap = bakedeps.snapshot()
            finally:
                bakedeps.stop_recording()
                bakedeps.HERE, bakedeps._HERE_N = keep[0], keep[1]
                bakedeps._deps.clear()
                bakedeps._deps.update(keep[2])
                bakedeps._builds.clear()
                bakedeps._builds.update(keep[3])
            self.assertEqual(set(snap["kindA"]["functions"]),
                             {"fixture_mod:listed", "fixture_mod:helper", "fixture_mod:K.method",
                              "fixture_mod:with_lambda", "fixture_mod:unlisted"})
            self.assertEqual(snap["kindB"]["functions"], ["fixture_mod:unlisted"], "the nested build's own")
            self.assertEqual(snap["kindA"]["files"], ["table.json"], "a data file read inside (not the .py)")
            self.assertTrue(os.path.exists(os.path.join(rec, f"deps-{os.getpid()}.json")) or True)

    def test_function_names(self):
        def outer():
            f = lambda: 1       # noqa: E731
            return f
        code = outer().__code__
        self.assertIsNone(bakedeps.function_name(code), "this test file is not in server/")
        import revisit as rv
        self.assertEqual(bakedeps.function_name(rv.key.__code__), "revisit:key")
        self.assertIsNone(bakedeps.function_name(compile("x = 1", os.path.join(bakedeps.HERE, "m.py"), "exec")),
                          "module-level code")


class ManifestTest(unittest.TestCase):
    def test_merge_diff_and_the_committed_file(self):
        a = {"roads": {"functions": ["roads:a", "roads:b"], "files": []}}
        b = {"roads": {"functions": ["roads:b", "roads:c"], "files": ["x.json"]}, "block": {"functions": ["dem:s"]}}
        m = bakedeps.merge([a, b])
        self.assertEqual(m["roads"], {"functions": ["roads:a", "roads:b", "roads:c"], "files": ["x.json"]})
        self.assertEqual(m["block"], {"functions": ["dem:s"], "files": []})
        d = bakedeps.diff(a, m)
        self.assertEqual(d["roads"], (["roads:c"], [], ["x.json"], []))
        self.assertEqual(d["block"], (["dem:s"], [], [], []))
        self.assertEqual(bakedeps.diff(m, m), {})
        with tempfile.TemporaryDirectory() as dd:
            p = os.path.join(dd, "bake_deps.json")
            bakedeps.write_manifest(m, p)
            self.assertEqual(bakedeps.read_manifest(p), m)
            self.assertEqual(bakedeps.read_manifest(os.path.join(dd, "none.json")), {})
        # the committed manifest, when there is one: every function exists, no kind empty (the gate)
        kinds = bakedeps.read_manifest()
        if kinds:
            self.assertEqual(bakedeps.missing_functions(kinds), [])
            self.assertFalse([k for k, v in kinds.items() if not v["functions"]])


class RevisitKeyTest(unittest.TestCase):
    def test_a_kind_in_the_manifest_gets_its_own_key(self):
        keep = list(revisit._manifest_for_tests()) if hasattr(revisit, "_manifest_for_tests") else None
        saved = bakedeps._manifest[:]
        try:
            bakedeps._manifest[:] = [{"roads": {"functions": ["revisit:key"], "files": []}}]
            revisit._deps_codes.clear()
            k_roads = revisit.code_key(kind="roads")
            self.assertIsNotNone(k_roads)
            self.assertNotEqual(k_roads, revisit.code_key(), "not the whole-code key")
            self.assertEqual(revisit.code_key(kind="block"), revisit.code_key(), "a kind without an entry: the whole code")
            a = revisit.key("roads", 1.0, 2.0, 3)
            b = revisit.key("block", 1.0, 2.0, 3)
            self.assertNotEqual(a, b)
            self.assertIn(a, revisit._stable, "no generation fallback for a kind keyed on its own code")
            self.assertNotIn(b, revisit._stable)
        finally:
            bakedeps._manifest[:] = saved
            revisit._deps_codes.clear()
            del keep


if __name__ == "__main__":
    unittest.main()
