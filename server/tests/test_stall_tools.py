"""The stall tools of 2026-10-29 (tools/nsys_report.py, tools/stallwatch.py, tools/warm_lap.py): their pure parts, so
the builder's first real run does not trip over a typo. nsys_report on an empty SQLite and on a synthetic one with the
table shapes it looks for; stallwatch's module lookup; warm_lap's hop selection. No game, no nsys, no admin."""
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools")
sys.path.insert(0, TOOLS)

import nsys_report  # noqa: E402


class NsysReportTest(unittest.TestCase):
    def test_empty_capture_says_what_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.sqlite")
            sqlite3.connect(p).close()
            rep = nsys_report.summarize(p, os.path.join(d, "x.summary.json"))
            self.assertEqual(rep["tables"], {})
            self.assertEqual(len(rep["missing"]), 3)
            self.assertTrue(os.path.isfile(os.path.join(d, "x.summary.json")))

    def test_synthetic_capture(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lap.sqlite")
            con = sqlite3.connect(p)
            con.execute("CREATE TABLE StringIds (id INTEGER, value TEXT)")
            con.executemany("INSERT INTO StringIds VALUES (?, ?)", [(1, "Present"), (2, "CreateGraphicsPipelineState")])
            con.execute("CREATE TABLE DX12_API (start INTEGER, end INTEGER, nameId INTEGER, globalTid INTEGER)")
            ms = 1_000_000
            con.executemany("INSERT INTO DX12_API VALUES (?, ?, ?, ?)",
                            [(0, 2 * ms, 1, 7), (10 * ms, 60 * ms, 1, 7), (100 * ms, 230 * ms, 2, 7), (300 * ms, 301 * ms, 1, 7)])
            con.execute("CREATE TABLE WDDM_QUEUE_PACKET (start INTEGER, end INTEGER, packetType INTEGER, context INTEGER)")
            con.executemany("INSERT INTO WDDM_QUEUE_PACKET VALUES (?, ?, ?, ?)",
                            [(0, 8 * ms, 1, 3), (20 * ms, 75 * ms, 1, 3), (80 * ms, 81 * ms, 2, 3)])
            con.execute("CREATE TABLE SAMPLING_SAMPLES (start INTEGER, globalTid INTEGER, ip INTEGER)")
            con.executemany("INSERT INTO SAMPLING_SAMPLES VALUES (?, ?, ?)", [(i, 7 if i % 3 else 9, 0x1000 + i) for i in range(30)])
            con.commit()
            con.close()
            rep = nsys_report.summarize(p)
            self.assertEqual(rep["missing"], [])
            api = rep["api_calls"]["DX12_API"]
            self.assertEqual(api["over10ms"], 2)
            self.assertEqual(api["longest"][0]["call"], "CreateGraphicsPipelineState")
            self.assertEqual(api["longest"][0]["ms"], 130.0)
            self.assertEqual([c["call"] for c in api["byCallOver10ms"]], ["CreateGraphicsPipelineState", "Present"])
            gpu = rep["gpu_packets"]["WDDM_QUEUE_PACKET"]
            self.assertEqual((gpu["over10ms"], gpu["longest"][0]["ms"]), (1, 55.0))
            self.assertEqual(rep["sampling"]["threads"][0], {"tid": 7, "samples": 20})


@unittest.skipUnless(sys.platform == "win32", "stallwatch loads kernel32 / dbghelp at import")
class StallwatchTest(unittest.TestCase):
    def test_module_lookup(self):
        import stallwatch
        mods = stallwatch.Modules.__new__(stallwatch.Modules)
        mods.mods = sorted([(0x7ff000000000, 0x7ff000100000, "ntdll.dll"), (0x140000000, 0x142000000, "BeamNG.drive.x64.exe"),
                            (0x7ff800000000, 0x7ff800400000, "nvwgf2umx.dll")])
        self.assertEqual(mods.of(0x140001234), ("BeamNG.drive.x64.exe", 0x1234))
        self.assertEqual(mods.of(0x7ff000000010), ("ntdll.dll", 0x10))
        self.assertEqual(mods.of(0x7ff8003fffff), ("nvwgf2umx.dll", 0x3fffff))
        self.assertEqual(mods.of(0x1), ("?", 0x1))
        self.assertEqual(mods.of(0x142000000), ("?", 0x142000000))       # the end is exclusive
        self.assertEqual(stallwatch.Sampler.fmt([("a.dll", 16), ("b.exe", 255)]), ["a.dll+0x10", "b.exe+0xff"])
        self.assertEqual(stallwatch.Sampler.fmt(None), [])
        self.assertEqual(__import__("ctypes").sizeof(stallwatch.CONTEXT), 1232)


class WarmLapTest(unittest.TestCase):
    def test_hops_along_the_route(self):
        import warm_lap
        route = warm_lap.ROUTE
        self.assertEqual(route[0][2], "Frederick")
        self.assertEqual(route[-1][2], "Castle Rock")
        n = 3
        step = (len(route) - 1) / (n - 1)
        hops = [route[round(i * step)] for i in range(n)]
        self.assertEqual([h[2] for h in hops], ["Frederick", "I-25 at Hampden", "Castle Rock"])
        self.assertIn("farfield", warm_lap.STATUS_LUA)
        self.assertIn("jsonEncode", warm_lap.STATUS_LUA)


if __name__ == "__main__":
    unittest.main()
