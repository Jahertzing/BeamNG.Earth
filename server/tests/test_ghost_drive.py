"""tools/ghost_drive.py: the route measured along, when the car moves on, the summary, and its GE chunk under luajit
(mod/tests/test_ghost_drive.lua)."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ghost_drive as gd  # noqa: E402

LUAJIT = shutil.which("luajit")
# 1 km north, then 1 km east (at 40 N)
LINE = [(40.0, -105.0), (40.0 + 1000 / 111195.0, -105.0),
        (40.0 + 1000 / 111195.0, -105.0 + 1000 / (111195.0 * 0.76604))]


def opts(**kw):
    o = dict(roads_q=gd.ROADS_Q, backlog_q=gd.BACKLOG_Q, lead_m=gd.LEAD_M)
    o.update(kw)
    return argparse.Namespace(**o)


class RouteTest(unittest.TestCase):
    def test_measured_along(self):
        r = gd.Route(LINE)
        self.assertAlmostEqual(r.length, 2000, delta=2)
        lat, lon = r.point(500)
        self.assertAlmostEqual(gd.metres(LINE[0], (lat, lon)), 500, delta=1)
        self.assertEqual(r.point(-5), LINE[0])
        self.assertAlmostEqual(r.point(9e9)[1], LINE[2][1], places=9)
        lat, lon = r.point(1500)                    # halfway along the east leg
        self.assertAlmostEqual(gd.metres(LINE[1], (lat, lon)), 500, delta=1)

    def test_repeated_points_are_dropped(self):
        r = gd.Route([LINE[0], LINE[0], LINE[1], LINE[1], LINE[2]])
        self.assertEqual(len(r.pts), 3)
        with self.assertRaises(ValueError):
            gd.Route([LINE[0], LINE[0]])

    def test_the_car_faces_along_the_road_to_its_end(self):
        r = gd.Route(LINE)
        h = r.ahead(100)
        self.assertAlmostEqual(gd.metres(r.point(100), h), gd.HEAD_M, delta=0.5)
        self.assertGreater(h[0], r.point(100)[0], "north on the first leg")
        lat, lon = r.point(r.length - 5)            # the last metres: on past the end, east
        h = r.ahead(r.length - 5)
        self.assertGreater(h[1], lon)
        self.assertAlmostEqual(h[0], lat, places=6)

    def test_routes_file_and_reverse(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "routes.json")
            with open(p, "w") as f:
                json.dump({"i25": [list(x) for x in LINE]}, f)
            self.assertEqual(gd.load_route(p, "i25").pts[0], LINE[0])
            self.assertEqual(gd.load_route(p, "i25", reverse=True).pts[0], LINE[2])
            with self.assertRaises(SystemExit):
                gd.load_route(p, "downtown")


class WaitTest(unittest.TestCase):
    def test_loaded_moves_on(self):
        self.assertEqual(gd.waiting_for({"roads": {"q": 3}, "backlog": 1, "lead": 900}, opts()), [])
        self.assertEqual(gd.waiting_for({}, opts()), [], "nothing known: on (an old mod without roads.ahead)")

    def test_each_reason(self):
        a = opts()
        self.assertIn("anchor request pending (rebase)", gd.waiting_for({"pending": "rebase"}, a))
        self.assertIn("anchor request pending (teleport)", gd.waiting_for({"pending": "teleport", "ready": True}, a))
        self.assertIn("the mod is setting the car down", gd.waiting_for({"placing": True}, a))
        self.assertIn("no ground at the next point", gd.waiting_for({"noground": True}, a))
        self.assertEqual(gd.waiting_for({"roads": {"q": 40}}, a), ["40 road meshes to make"])
        self.assertEqual(gd.waiting_for({"backlog": 12}, a), ["server backlog 12"])
        self.assertEqual(gd.waiting_for({"lead": 96}, a), ["road tiles ready 96 m ahead"])
        self.assertEqual(gd.waiting_for({"err": "no player vehicle"}, a), ["no player vehicle"])
        self.assertEqual(gd.waiting_for({"lead": 96}, opts(lead_m=0)), [], "--lead-m 0: not waited for")

    def test_a_ready_rebase_is_driven_to(self):
        """A rebase's anchor is asked for 300 m before the rebase distance and commits only past it: standing for it
        never ends (the PC's ghost bake 2026-10-28 stood at km 19.2 of I-25 for 16 minutes)."""
        self.assertEqual(gd.waiting_for({"pending": "rebase", "ready": True}, opts()), [])


class SummaryTest(unittest.TestCase):
    def test_numbers(self):
        rows = [{"t": 0, "anchor": 1, "perf": {"frames": 60, "avgMs": 10.0, "p99": 12, "maxMs": 20, "over50": 0},
                 "limit": {"focused": True}},
                {"t": 0.5, "anchor": 1, "turned": True},
                {"t": 1.0, "anchor": 2, "perf": {"frames": 30, "avgMs": 20.0, "p99": 40, "maxMs": 60, "over50": 1},
                 "limit": {"focused": False, "fg": 60}, "hitches": [{"ms": 60, "mod": {"roads": 50}}]}]
        waits = [{"km": 1.0, "s": 4.0, "why": ["x"]}, {"km": 2.0, "s": 120.5, "why": ["y"], "gave_up": True}]
        s = gd.summarize(rows, waits, 2000.0, 2000.0)
        self.assertEqual(s["fps"], round(1000 * 90 / 1200, 1))
        self.assertEqual(s["frame_ms"], {"p99_worst_tick": 40, "max": 60})
        self.assertEqual(s["frames_over"]["over50"], 1)
        self.assertEqual((s["rebases"], s["turned"], s["complete"]), (1, 1, True))
        self.assertEqual(s["waits"]["count"], 2)
        self.assertEqual(s["waits"]["gave_up"], 1)
        self.assertEqual(s["waits"]["longest"][0]["km"], 2.0)
        self.assertEqual(s["hitch_causes"], {"mod.roads": [1, 60]})
        self.assertEqual((s["fps_limit"], s["unfocused_ticks"]), ({"focused": False, "fg": 60}, 1))
        self.assertFalse(gd.summarize(rows, [], 2000.0, 1500.0)["complete"])


class DriveLoopTest(unittest.TestCase):
    """The drive against a fake game: it stands while the place loads, moves on, ends at the route's end."""

    def run_drive(self, answers, **kw):
        sent = []

        def step(cfg):
            sent.append(cfg)
            return dict(next(answers, {"anchor": 1}))
        saved = (gd.lua, gd.step, gd.mcp.quiet_ui, gd.time.sleep, gd.teleport_to)
        gd.lua = lambda code: "ok"
        gd.step = step
        gd.mcp.quiet_ui = lambda: None
        gd.time.sleep = lambda s: None
        gd.teleport_to = lambda lat, lon, log: True
        try:
            with tempfile.TemporaryDirectory() as d:
                a = argparse.Namespace(out=d, turn_deg=20.0, from_km=0.0, to_km=0.0, no_teleport=False,
                                       parent_pipe=False, speed=200.0, max_wait=kw.get("max_wait", 120.0),
                                       route="i25", reverse=False, **vars(opts()))
                rc = gd.drive(a, gd.Route(LINE), log=lambda m: None)
                with open(os.path.join(d, "summary.json")) as f:
                    summary = json.load(f)
        finally:
            gd.lua, gd.step, gd.mcp.quiet_ui, gd.time.sleep, gd.teleport_to = saved
        return rc, summary, sent

    def test_stands_while_loading_then_drives_to_the_end(self):
        answers = iter([{"anchor": 1}, {"pending": "rebase"}, {"pending": "rebase"}, {"roads": {"q": 50}}])
        rc, summary, sent = self.run_drive(answers)
        self.assertEqual(rc, 0)
        self.assertTrue(summary["complete"])
        self.assertTrue(sent[0]["reset"], "the first step faces the road")
        self.assertEqual(sent[0]["v"], 200.0)
        self.assertEqual([c["v"] for c in sent[1:5]], [200.0, 0, 0, 0], "standing while it loads")
        self.assertEqual(sent[1]["lat"], sent[4]["lat"], "at the same place")
        self.assertEqual(sent[5]["v"], 200.0)
        self.assertGreater(sent[5]["lat"], sent[4]["lat"], "then on")
        self.assertEqual(sent[-1]["v"], 0, "parked at the end")
        self.assertEqual(len(sent), 21 + 3 + 1, "21 places 100 m apart, 3 ticks standing, parked")

    def test_a_place_that_never_loads_is_passed_after_max_wait(self):
        never = iter([{"noground": True}] * 10000)
        rc, summary, _ = self.run_drive(never, max_wait=0.0)
        self.assertEqual(rc, 0)
        self.assertTrue(summary["complete"])
        self.assertGreater(summary["waits"]["gave_up"], 5)


@unittest.skipUnless(LUAJIT, "luajit not installed")
class LuaChunkTest(unittest.TestCase):
    def test_chunk_under_luajit(self):
        r = subprocess.run([LUAJIT, os.path.join(ROOT, "mod", "tests", "test_ghost_drive.lua")], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 failed", r.stdout)


if __name__ == "__main__":
    unittest.main()
