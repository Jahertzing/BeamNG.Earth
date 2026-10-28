"""tools/route_bake.py: the route anchors a drive re-centres on (the mod's rebase rule and routepins.lua's pinning),
shared between routes and both directions, and the files it writes for the mod and the server."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import route_bake as rb  # noqa: E402

# a rough I-25 from Frederick to Castle Rock, and a route that leaves it for downtown streets
I25 = [(40.0992, -104.9372), (40.0990, -104.9870), (39.9150, -104.9870), (39.8000, -104.9880), (39.7500, -105.0150),
       (39.7000, -104.9900), (39.6200, -104.8950), (39.5200, -104.8700), (39.3722, -104.8561)]
DOWNTOWN = I25[:4] + [(39.7650, -105.0000), (39.7530, -105.0002), (39.7400, -104.9900), (39.7000, -104.9900)] + I25[6:]
DEG_N = 1 / 111195.0        # degrees of latitude per metre


class AnchorTest(unittest.TestCase):
    def test_snap_as_the_mod(self):
        # test_core_stubbed.lua: the default start 36.596718, -118.169991 snaps to 36.6, -118.17
        self.assertEqual(rb.snap_anchor(36.596718, -118.169991), (36.6, -118.17))
        self.assertEqual(rb.snap_anchor(40.0992, -104.9372), (40.1, -104.94))
        self.assertEqual(rb.snap_anchor(88.5, 12.345), (88.5, 12.345))

    def test_anchors_along_a_route(self):
        anchors, order = rb.plan_anchors({"i25": I25}, 15000.0)
        seq = order["i25"]
        self.assertEqual(seq[0], 0)
        self.assertEqual(anchors[0], rb.snap_anchor(*I25[0]), "the start's grid point")
        for i, j in zip(seq, seq[1:]):      # (one rebase distance apart, give or take the grid and the bends)
            self.assertTrue(10000 < rb.horizontal(anchors[i], anchors[j]) < 16500)
        self.assertTrue(all(rb.snap_anchor(*a) == a for a in anchors), "every anchor a grid point")

    def test_routes_and_directions_share_anchors(self):
        anchors, order = rb.plan_anchors({"i25": I25, "downtown": DOWNTOWN}, 15000.0)
        self.assertEqual(order["downtown"], order["i25"], "downtown is never far from I-25: the same anchors")
        # the pinned-anchor rule: the frame is the nearest planned anchor's, so both directions use the same anchors
        # in opposite order (the distance rule alone gave the back drive one more, at its end)
        self.assertEqual(order["i25<"], order["i25"][::-1], "back: the same anchors, reversed")
        self.assertEqual(set(order["i25"]), set(range(len(anchors))), "every planned anchor is driven")
        self.assertNotIn(-1, order["i25"])
        # a drive that starts elsewhere on the route (Thornton) lands on the planned anchors too
        mid = rb.densify(I25)[300:]
        pin_m = 15000.0 * rb.PIN_SHARE
        seq = rb.walk(mid, anchors, 15000.0, rb.PREFETCH_MARGIN, pin_m, add=False)
        self.assertNotIn(-1, seq)
        seq = rb.walk_pinned(mid, anchors, pin_m, add=False)
        self.assertNotIn(-1, seq)
        self.assertEqual(seq, [i for i in order["i25"] if i in seq], "the same anchors from there on")

    def test_the_pinned_walk_switches_once_the_next_anchor_is_within_the_pin(self):
        # A and B 14.7 km apart on a meridian; the frame moves to B where B is within the pin distance (6.75 km) and
        # 300 m nearer than A: 7.95 km from A, not at the 15 km distance; the same place driving back
        A, B = (40.0, -105.0), (40.0 + 14700 * DEG_N, -105.0)
        anchors = [A, B]
        dense = rb.densify([A, B], 10.0)
        seq = rb.walk_pinned(dense, anchors, 6750.0, 300.0, 15000.0, 300.0)
        self.assertEqual(seq, [0, 1])
        at = next(i for i, p in enumerate(dense) if rb.horizontal(B, p) <= 6750.0)
        self.assertEqual(rb.walk_pinned(dense[:at], anchors, 6750.0, 300.0, 15000.0, 300.0), [0], "not before")
        self.assertEqual(rb.walk_pinned(dense[::-1], anchors, 6750.0, 300.0, 15000.0, 300.0), [1, 0])
        # anchors 2 km apart: the switch is 150 m past the midpoint (the hysteresis), in either direction
        C = (40.0 + 2000 * DEG_N, -105.0)
        dense = rb.densify([A, C], 10.0)
        seq, i = [0], None
        for k in range(2, len(dense)):
            if rb.walk_pinned(dense[:k], [A, C], 6750.0, 300.0) == [0, 1]:
                i = k - 1
                break
        self.assertIsNotNone(i)
        self.assertAlmostEqual(rb.horizontal(A, dense[i]), 1150.0, delta=15.0)
        # no planned anchor near: the distance rule, the grid point added as before
        far = rb.densify([A, (40.0 + 16000 * DEG_N, -105.0)], 50.0)
        anchors = [A]
        seq = rb.walk_pinned(far, anchors, 6750.0, 300.0, 15000.0, 300.0)
        self.assertEqual(seq, [0, 1])
        self.assertEqual(anchors[1], rb.snap_anchor(*far[int(14700 / 50)]), "the grid point at 14.7 km")
        self.assertEqual(rb.walk_pinned(far, [A], 6750.0, 300.0, 15000.0, 300.0, add=False), [0, -1])

    def test_a_lattice_covers_the_box_and_scan_lines_cross_it(self):
        box = (39.9, -105.1, 40.0, -105.0)      # ~11 km by 8.5 km
        pin_m = 6750.0
        grid = rb.lattice(box, pin_m)
        self.assertGreater(len(grid), 1)
        for i in range(11):
            for j in range(11):
                p = (box[0] + (box[2] - box[0]) * i / 10, box[1] + (box[3] - box[1]) * j / 10)
                self.assertLessEqual(min(rb.horizontal(a, p) for a in grid), pin_m, p)
        self.assertLess(len(rb.lattice(box, 20000.0)), len(grid), "a longer pin: fewer anchors")
        lines = rb.scan_lines(box, 1000.0)
        self.assertEqual(len(lines), 11, "11.1 km north to south, a line every km")
        self.assertEqual(list(lines)[:3], ["scan_00", "scan_01", "scan_02"])
        self.assertEqual((lines["scan_00"][0][1], lines["scan_00"][1][1]), (-105.1, -105.0), "eastward first")
        self.assertEqual((lines["scan_01"][0][1], lines["scan_01"][1][1]), (-105.0, -105.1), "then back west")
        self.assertTrue(all(box[0] < ln[0][0] < box[2] for ln in lines.values()))
        self.assertEqual(rb.parse_box("39.9,-105.1,40.0,-105.0"), box)
        with self.assertRaises(ValueError):
            rb.parse_box("1,2,3")

    def test_plan_area_writes_the_lattice_and_scan_routes(self):
        with tempfile.TemporaryDirectory() as d:
            pts = os.path.join(d, "pts.json")
            with open(pts, "w") as f:
                json.dump({"i25": I25}, f)
            args = ["--cache-dir", os.path.join(d, "cache"), "--world-dir", os.path.join(d, "world"),
                    "--user-dir", os.path.join(d, "user"), "--rebase-m", "15000"]
            rc = rb.main(["plan", "--routes", "i25", "--points", pts, "--area", "40.05,-105.0,40.1,-104.9",
                          "--area-name", "frederick", "--line-m", "2000"] + args)
            self.assertEqual(rc, 0)
            data = rb.read_json(os.path.join(d, "world", "bake", "anchors.json"))
            routes = rb.read_json(os.path.join(d, "world", "bake", "routes.json"))
            area = data["areas"]["frederick"]
            self.assertEqual(area["box"], [40.05, -105.0, 40.1, -104.9])
            self.assertTrue(area["anchors"], "lattice anchors added after the route's")
            self.assertTrue(all(i >= 1 for i in area["anchors"]))
            self.assertEqual(area["lines"], [n for n in routes if n.startswith("scan_")])
            self.assertEqual(len(area["lines"]), 3, "5.6 km north to south, lines 1, 3 and 5 km up")
            for n in area["lines"]:
                self.assertTrue(data["routes"][n]["scan"])
                self.assertNotIn(-1, data["routes"][n]["anchors"], "every scan line in planned frames")
                self.assertEqual(len(routes[n]), 2)
            self.assertEqual(data["routes"]["i25"]["anchors"][0], 0)
            mod = rb.read_json(os.path.join(d, "user", *rb.MOD_FILE))
            self.assertEqual(mod["anchors"], data["anchors"], "the mod pins to the lattice too")
            rc = rb.main(["plan", "--routes", "", "--area", "40.05,-105.0,40.1,-104.9"] + args)
            self.assertEqual(rc, 0, "an area alone")
            self.assertEqual(rb.main(["plan", "--routes", ""] + args), 2, "nothing to plan")

    def test_plan_writes_both_copies(self):
        with tempfile.TemporaryDirectory() as d:
            pts = os.path.join(d, "pts.json")
            with open(pts, "w") as f:
                json.dump({"i25": I25, "downtown": DOWNTOWN}, f)
            args = ["--cache-dir", os.path.join(d, "cache"), "--world-dir", os.path.join(d, "world"),
                    "--user-dir", os.path.join(d, "user")]
            self.assertEqual(rb.main(["plan", "--points", pts, "--rebase-m", "15000"] + args), 0)
            with open(os.path.join(d, "user", *rb.MOD_FILE)) as f:
                mod = json.load(f)
            with open(os.path.join(d, "world", "bake", "anchors.json")) as f:     # (the world folder's: kept)
                server = json.load(f)
            self.assertEqual(mod, server)
            self.assertEqual(mod["pinM"], 15000 * rb.PIN_SHARE)
            self.assertEqual(set(mod["routes"]), {"i25", "downtown"})
            import cache
            import revisit
            self.assertFalse(os.path.exists(os.path.join(d, "cache", "bake")))
            cache.set_world(os.path.join(d, "world"), os.path.join(d, "cache"), env=False)    # (the server's)
            try:
                self.assertEqual(revisit.pinned_anchors(os.path.join(d, "cache")),
                                 {(round(a[0], 7), round(a[1], 7)) for a in mod["anchors"]})
            finally:
                cache.set_world(None, None, env=False)


class DriveTest(unittest.TestCase):
    """drive: which tool drives each leg, from where (no game: the legs' commands recorded)."""

    def bake(self, d, extra, legs):
        pts = os.path.join(d, "pts.json")
        with open(pts, "w") as f:
            json.dump({"i25": I25, "downtown": DOWNTOWN}, f)
        args = ["--cache-dir", os.path.join(d, "cache"), "--world-dir", os.path.join(d, "world"),
                "--user-dir", os.path.join(d, "user"), "--out", os.path.join(d, "runs")]
        self.assertEqual(rb.main(["plan", "--points", pts, "--rebase-m", "15000"] + args), 0)
        cmds = []

        class Done:
            def __init__(self, rc):
                self.returncode = rc

        def leg(cmd):
            cmds.append(cmd)
            rc, summary = legs(cmd)
            out = cmd[cmd.index("--out") + 1]
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "summary.json"), "w") as f:
                json.dump(summary, f)
            return Done(rc)
        real = rb._drive_leg
        rb._drive_leg = leg
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = rb.main(["drive"] + extra + args)
        finally:
            rb._drive_leg = real
        return rc, cmds

    def test_the_ghost_drives_each_planned_route_whole(self):
        with tempfile.TemporaryDirectory() as d:
            rc, cmds = self.bake(d, ["--both-ways"], lambda cmd: (0, {"complete": True, "km": 93.0}))
            self.assertEqual(rc, 0)
            self.assertEqual([os.path.basename(c[1]) for c in cmds], ["ghost_drive.py"] * 4)
            self.assertEqual([(c[c.index("--route") + 1], "--reverse" in c) for c in cmds],
                             [("downtown", False), ("downtown", True), ("i25", False), ("i25", True)])
            self.assertEqual(cmds[0][cmds[0].index("--routes-file") + 1],
                             os.path.join(d, "world", "bake", "routes.json"))
            with open(os.path.join(d, "world", "bake", rb.MANIFEST)) as f:
                run = json.load(f)["runs"][-1]
            self.assertTrue(run["complete"])
            self.assertEqual(run["legs"][0]["summary"]["km"], 93.0)

    def test_mark_keeps_a_complete_bake_after_a_code_change(self):
        """mark: a bake made before revisit.py's generations stays in use after an update."""
        import revisit
        with tempfile.TemporaryDirectory() as d:
            rc, _ = self.bake(d, [], lambda cmd: (0, {"complete": True, "km": 93.0}))
            self.assertEqual(rc, 0)
            bd = os.path.join(d, "world", "bake")
            with open(os.path.join(bd, "anchors.json")) as f:
                anchors = json.load(f)["anchors"]
            args = ["--cache-dir", os.path.join(d, "cache"), "--world-dir", os.path.join(d, "world")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rb.main(["mark"] + args), 0)
            gd = os.path.join(bd, revisit.GENS_SUBDIR, revisit.code_key())
            self.assertEqual(len([f for f in os.listdir(gd) if f != "started"]), len(anchors))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rb.main(["status"] + args), 0, "gens/ is not a kind")
            self.assertNotIn(revisit.GENS_SUBDIR, rb.sizes(bd))

    def test_a_ghost_drive_that_did_not_finish_fails_the_bake(self):
        with tempfile.TemporaryDirectory() as d:
            rc, cmds = self.bake(d, [], lambda cmd: (1, {"complete": False}))
            self.assertEqual((rc, len(cmds)), (1, 1), "stopped after the first")

    def test_an_ai_leg_after_one_that_did_not_arrive_starts_at_its_own_start(self):
        """(the PC bake 2026-10-28: downtown_0 crashed in Frederick and downtown_1 stood at the wreck for 70 minutes)"""
        with tempfile.TemporaryDirectory() as d:
            res = iter([(0, {"remaining_m": 31000}), (0, {"remaining_m": 20}), (0, {"remaining_m": 20})])
            rc, cmds = self.bake(d, ["--driver", "ai", "--routes", "downtown", "--keep-going"], lambda cmd: next(res))
            self.assertEqual([os.path.basename(c[1]) for c in cmds], ["autodrive_perf.py"] * 2)
            self.assertIn("--from", cmds[0])
            self.assertIn("--from", cmds[1], "the first leg did not arrive: the second from Union Station")
            self.assertEqual(cmds[1][cmds[1].index("--from") + 1], "39.753,-105.0002")
            res = iter([(0, {"remaining_m": 20}), (0, {"remaining_m": 20})])
            rc, cmds = self.bake(d, ["--driver", "ai", "--routes", "downtown"], lambda cmd: next(res))
            self.assertNotIn("--from", cmds[1], "arrived: on from where the car is")


if __name__ == "__main__":
    unittest.main()
