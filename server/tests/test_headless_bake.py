"""tools/headless_bake.py: its server's command (the launcher's options, never the game's port), the mod's pinning and
rebase hint, when the car moves on, the heightmap's height, a drive against a stand-in server (teleport, rebases onto
the planned anchors, terrain-follow, poses) and its server's start and stop."""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import headless_bake as hb  # noqa: E402
import modsim  # noqa: E402
import protocol  # noqa: E402
import route_bake as rb  # noqa: E402

DEG_N = 1 / 111195.0        # degrees of latitude per metre


def opts(**kw):
    o = dict(speed=hb.SPEED, roads_q=hb.ROADS_Q, backlog_q=hb.BACKLOG_Q, edge_m=hb.EDGE_M, max_wait=hb.MAX_WAIT_S,
             ground_wait=hb.GROUND_WAIT_S)
    o.update(kw)
    return argparse.Namespace(**o)


class ServerCommandTest(unittest.TestCase):
    def test_the_launchers_options_then_the_throttles(self):
        import launcher
        with tempfile.TemporaryDirectory() as user:
            cmd = hb.server_command(user, 0, "/c", win=True)
            self.assertEqual(cmd[1:5], [os.path.join(hb.ROOT, "launcher.py"), "--server", "--user-dir", user])
            n = len(launcher.RECOMMENDED)
            self.assertEqual(cmd[5:5 + n], launcher.RECOMMENDED, "the options every revisit key holds")
            self.assertEqual(cmd[5 + n:9 + n], launcher.setting_flags(user))
            self.assertEqual(cmd[9 + n:11 + n], ["--cache-dir", "/c"])
            self.assertEqual(cmd[cmd.index("--port") + 1], "0")
            for a, b in zip(hb.THROTTLE[::2], hb.THROTTLE[1::2]):
                self.assertEqual(cmd[cmd.index(a) + 1], b)
            self.assertEqual(cmd[-2:], ["--nice", "0"], "Windows: the idle class, not raised to below normal")
            self.assertEqual(hb.server_command(user, 0, "/c", win=False)[-4:], ["--nice", "19", "--sched", "idle"])

    def test_never_the_games_port(self):
        with self.assertRaises(ValueError):
            hb.server_command("/u", hb.GAME_PORT, "/c")
        with self.assertRaises(SystemExit):
            hb.main(["--connect", str(hb.GAME_PORT)])

    def test_alone_the_machines_share_below_normal(self):
        with tempfile.TemporaryDirectory() as user:
            far, road = hb.alone_workers(ram_gb=64.0)
            cmd = hb.server_command(user, 0, "/c", win=True, mode="alone")
            self.assertEqual(cmd[cmd.index("--workers") + 1], str(far))
            self.assertEqual(cmd[cmd.index("--road-processes") + 1], str(road))
            for a, b in zip(hb.QUIET[::2], hb.QUIET[1::2]):
                self.assertEqual(cmd[cmd.index(a) + 1], b, "never the game's cores, no map tiles")
            self.assertEqual(cmd[-2:], ["--nice", "0"], "Windows: the class is start_server's (below normal)")
            self.assertEqual(hb.server_command(user, 0, "/c", win=False, mode="alone")[-2:], ["--nice", "10"])
            cmd = hb.server_command(user, 0, "/c", win=True, mode="alone", workers=3, road_processes=2)
            self.assertEqual(cmd[cmd.index("--workers") + 1], "3", "--jobs: the workers divided")
            self.assertEqual(cmd[cmd.index("--road-processes") + 1], "2")
            cmd = hb.server_command(user, 0, "/c", win=True, mode="game", workers=2)
            self.assertEqual(cmd[cmd.index("--workers") + 1], "2", "--workers given: over the throttle's")
            self.assertEqual(cmd[cmd.index("--road-processes") + 1], "1", "... the rest stays the throttle's")
            self.assertEqual(cmd[-2:], ["--nice", "0"])
        with self.assertRaises(ValueError):
            hb.server_command("/u", 0, "/c", mode="auto")

    def test_servers_and_workers_from_the_memory_free(self):
        # the PC with the game closed: 43 GB free (2026-10-28 20:50), 24 CPUs
        jobs, far, road, note = hb.plan_servers(8, 8, ram_gb=43.0, cpus=24)
        self.assertEqual((jobs, far, road), (3, 3, 4), "three servers with every useful worker, not five of 1 + 1")
        self.assertIn("3 servers in the 43 GB", note)
        self.assertEqual(hb.plan_servers(4, 8, ram_gb=43.0, cpus=24)[:3], (3, 3, 4),
                         "four wanted: 4 x (1 + 4) = 20 workers, 3 x (3 + 4) = 21: three")
        self.assertEqual(hb.plan_servers(4, 8, ram_gb=48.0, cpus=24)[:3], (4, 3, 4), "48 GB: four full servers")
        self.assertEqual(hb.plan_servers(2, 8, ram_gb=43.0, cpus=24), (2, 3, 4, None))
        self.assertEqual(hb.plan_servers(8, 2, ram_gb=64.0, cpus=24), (2, 3, 4, None), "at most one per leg")
        self.assertEqual(hb.plan_servers(8, 8, ram_gb=200.0, cpus=12), (4, 3, 4, None), "CPUS_PER_JOB")
        self.assertEqual(hb.plan_servers(8, 8, ram_gb=5.0, cpus=24)[:3], (1, 1, 2), "too little memory: the least")
        self.assertEqual(hb.plan_servers(2, 2, ram_gb=1e9, cpus=24), (2, 3, 4, None))
        self.assertEqual(hb.alone_workers(ram_gb=64.0), (3, 4))
        self.assertEqual(hb.alone_workers(ram_gb=8.0), (1, 2), "8 GB: the least")
        self.assertIsNone(hb.workers_for(6.0))
        self.assertEqual(hb.workers_for(7.5), (1, 2))
        self.assertEqual(hb.workers_for(9.0), (1, 4), "the road processes first")
        self.assertEqual(hb.workers_for(12.0), (3, 4))

    def test_auto_is_alone_unless_the_game_runs(self):
        self.assertEqual(hb.resolve_mode("game"), "game")
        self.assertEqual(hb.resolve_mode("alone"), "alone")
        with self.assertRaises(ValueError):
            hb.resolve_mode("fast")
        with unittest.mock.patch.object(hb, "game_running", return_value=True):
            self.assertEqual(hb.resolve_mode("auto"), "game")
        with unittest.mock.patch.object(hb, "game_running", return_value=False):
            self.assertEqual(hb.resolve_mode("auto"), "alone")
        self.assertIn(hb.game_running(), (True, False), "this machine: told either way, never raises")


class LegsTest(unittest.TestCase):
    def test_legs_shared_out(self):
        legs = hb.legs_of(["i25", "downtown"], True)
        self.assertEqual(legs, [("i25", False, None), ("i25", True, None), ("downtown", False, None),
                                ("downtown", True, None)])
        self.assertEqual(hb.legs_of(["i25"], False), [("i25", False, None)])
        self.assertEqual([hb.leg_label(*lg) for lg in legs], ["i25", "i25_back", "downtown", "downtown_back"])
        self.assertEqual(hb.parse_legs("i25,downtown_back, i25_back"), [("i25", False, None), ("downtown", True, None),
                                                                        ("i25", True, None)])
        self.assertEqual(hb.share_out(legs, 2), [[("i25", False, None), ("downtown", False, None)],
                                                 [("i25", True, None), ("downtown", True, None)]])
        self.assertEqual(hb.share_out(legs, 1), [legs])
        self.assertEqual(len(hb.share_out(legs[:1], 3)), 1, "no empty jobs")

    def test_split_legs_round_trip(self):
        legs = hb.legs_of(["i25"], True, split=2)
        self.assertEqual(legs, [("i25", False, (0, 2)), ("i25", False, (1, 2)), ("i25", True, (0, 2)),
                                ("i25", True, (1, 2))])
        labels = [hb.leg_label(*lg) for lg in legs]
        self.assertEqual(labels, ["i25_1of2", "i25_2of2", "i25_back_1of2", "i25_back_2of2"])
        self.assertEqual(hb.parse_legs(",".join(labels)), legs)
        self.assertEqual(hb.parse_legs("scan_03_2of4"), [("scan_03", False, (1, 4))])
        route = gd_route([(40.0, -105.0), (40.0 + 10000 * DEG_N, -105.0)])
        a, b, c = (hb.stretch(route, i, 3) for i in range(3))
        self.assertAlmostEqual(a.length, 10000 / 3 + 1000, delta=20, msg="the first reaches 1 km into the second")
        self.assertAlmostEqual(b.length, 10000 / 3 + 2000, delta=20, msg="the middle one into both")
        self.assertAlmostEqual(c.length, 10000 / 3 + 1000, delta=20)
        self.assertEqual(a.point(0.0), route.point(0.0))
        self.assertAlmostEqual(c.point(c.length)[0], route.point(route.length)[0], places=6)
        self.assertAlmostEqual(b.point(0.0)[0], route.point(10000 / 3 - 1000)[0], places=6)
        self.assertIs(hb.stretch(route, 0, 1), route)
        # the longest stretches first, each to the job with the least so far
        legs = hb.legs_of(["long", "short"], True, split=2)
        L = {hb.leg_label(*lg): (90000.0 if lg[0] == "long" else 30000.0) / 2 + 1000.0 for lg in legs}
        groups = hb.share_out(legs, 3, L)
        self.assertEqual(len(groups), 3)
        loads = sorted(sum(L[hb.leg_label(*lg)] for lg in g) for g in groups)
        self.assertLess(loads[-1] - loads[0], 47000.0, loads)
        self.assertEqual([lg[0] for lg in groups[0]][:1], ["long"], "the longest goes first")
        self.assertEqual(hb.share_out(legs, 3, None), [legs[0::3], legs[1::3], legs[2::3]], "no lengths: round robin")

    def test_a_childs_command_carries_the_options(self):
        a = argparse.Namespace(speed=80.0, roads_q=1, backlog_q=4, edge_m=160.0, max_wait=180.0, ground_wait=20.0,
                               cache_dir="/c", world_dir=None, user_dir="/u", rebase_m=None)
        cmd = hb.child_command(a, [("i25", False, None), ("downtown", True, (0, 2))], "/out/x", "alone", 3, 2)
        self.assertEqual(cmd[:2], [sys.executable, os.path.abspath(hb.__file__)])
        self.assertIn("--child", cmd)
        self.assertEqual(cmd[cmd.index("--legs") + 1], "i25,downtown_back_1of2")
        self.assertEqual(cmd[cmd.index("--out") + 1], "/out/x")
        self.assertEqual(cmd[cmd.index("--mode") + 1], "alone")
        self.assertEqual(cmd[cmd.index("--workers") + 1:cmd.index("--workers") + 4], ["3", "--road-processes", "2"])
        self.assertEqual(cmd[cmd.index("--speed") + 1], "80.0")
        self.assertEqual(cmd[cmd.index("--cache-dir") + 1], "/c")
        self.assertEqual(cmd[cmd.index("--user-dir") + 1], "/u")
        self.assertNotIn("--world-dir", cmd)
        self.assertNotIn("--rebase-m", cmd)

    def test_where_the_time_went(self):
        self.assertEqual(hb.cause_of(["anchor request pending (teleport)"]), "anchor")
        self.assertEqual(hb.cause_of(["terrain block (128, 0) not ready, 100 m from the edge", "far tiles 1/9"]),
                         "block")
        self.assertEqual(hb.cause_of(["ground not final", "road tiles 0/36"]), "ground", "the first reason's")
        self.assertEqual(hb.cause_of(["road tiles 0/36"]), "roads")
        self.assertEqual(hb.cause_of(["far tiles 3/400"]), "far")
        s = {"seconds_by": {"moving": 1055.2, "anchor": 2.0, "block": 0.0, "ground": 29.0, "roads": 180.0,
                            "far": 211.0}}
        self.assertEqual(hb.by_text(s), "moving 1055 s, far 211 s, roads 180 s, ground 29 s, anchor 2 s")
        self.assertEqual(hb.by_text({}), "moving 0 s")
        self.assertTrue(hb.leg_report("i25", dict(s, km=28.7, route_km=93.2, seconds=1476.0, rebases=2, prepares=40,
                                                  waits={"count": 9, "seconds": 420.0, "gave_up": 0}))
                        .startswith("i25: 28.7 of 93.2 km in 1476 s (moving 1055 s, far 211 s"))


class RulesTest(unittest.TestCase):
    def test_pinned_to_the_planned_anchor(self):
        anchors = [(40.0, -105.0), (40.0 + 3000 * DEG_N, -105.0)]
        self.assertEqual(hb.anchor_for(40.0 + 2900 * DEG_N, -105.0, anchors, 450.0), anchors[1])
        self.assertEqual(hb.anchor_for(40.0 + 1500 * DEG_N, -105.0, anchors, 450.0),
                         rb.snap_anchor(40.0 + 1500 * DEG_N, -105.0), "none near: the grid point")
        self.assertEqual(hb.anchor_for(40.0, -105.0, anchors, 0.0), rb.snap_anchor(40.0, -105.0))
        self.assertEqual(hb.pin_metres({"pinM": 6750.0}, 15000.0), 6750.0)
        self.assertEqual(hb.pin_metres({"pinM": 6750.0}, 10000.0), 4500.0, "core.lua PIN_SHARE of the setting")

    def test_the_pinned_anchor_rule(self):
        A, B = (40.0, -105.0), (40.0 + 1200 * DEG_N, -105.0)
        anchors, pin_m = [A, B], 6750.0
        at = lambda m: (40.0 + m * DEG_N, -105.0)     # noqa: E731
        self.assertEqual(hb.pin_want(*at(100), anchors, pin_m, A, 100.0), (False, A), "A is the nearest: stay")
        self.assertEqual(hb.pin_want(*at(700), anchors, pin_m, A, 700.0), (False, B), "B nearer, not by 300 m")
        self.assertEqual(hb.pin_want(*at(760), anchors, pin_m, A, 760.0), (True, B), "B 320 m nearer: move")
        self.assertEqual(hb.pin_want(*at(760), anchors, pin_m, B, 440.0), (False, B), "in B already")
        self.assertEqual(hb.pin_want(*at(760), anchors, pin_m, None, 1e9), (True, B), "no anchor yet: move")
        self.assertEqual(hb.pin_want(*at(20000), anchors, pin_m, A, 20000.0), (None, None), "none near")
        self.assertEqual(hb.pin_want(*at(760), anchors, 0.0, A, 760.0), (None, None), "no pin distance")
        self.assertEqual(hb.rebase_hint(27.0, 0.0, 700.0, 15000.0, 10.0, -20.0, 1024, 1.0, pinned=True),
                         modsim.follow_centre_near(10.0 + 54.0, -20.0, 10.0 + 27.0, -20.0,
                                                   modsim.follow_mode(27.0, 0.0, 1024)[1], 1024),
                         "pinned: the block where the player will be at the answer, within reach")

    def test_rebase_hint_at_the_origin_is_modsims(self):
        for vx, vy, dist in ((27.0, 0.0, 14700.0), (10.0, -20.0, 14800.0), (27.0, 3.0, 15200.0)):
            self.assertEqual(hb.rebase_hint(vx, vy, dist, 15000.0, 0.0, 0.0, 512),
                             modsim.rebase_hint(vx, vy, dist, 15000.0, 512))
        self.assertEqual(hb.rebase_hint(27.0, 0.0, 14700.0, 15000.0, 300.0, -40.0, 512),
                         modsim.follow_target(600.0, -40.0, 27.0, 0.0, 512), "from the player's spot")

    def test_each_reason(self):
        a = opts()
        self.assertEqual(hb.waiting_for({}, 500.0, a), [])
        self.assertTrue(hb.waiting_for({"pending": {"purpose": "rebase"}}, 500.0, a))
        self.assertEqual(hb.waiting_for({"pending": {"purpose": "rebase", "ready": True}}, 500.0, a), [],
                         "a ready rebase commits past the distance: on to it")
        tp = {"cx": 128.0, "cy": 0.0}
        self.assertEqual(hb.waiting_for({"tp": tp}, 500.0, a), [], "far from the edge")
        self.assertTrue(hb.waiting_for({"tp": tp}, 100.0, a))
        self.assertEqual(hb.waiting_for({"tp": dict(tp, ready=True)}, 100.0, a), [])
        pr = {"ground": [1, 1], "roads": [8, 9], "far": [36, 40]}
        self.assertEqual(hb.waiting_for({"progress": pr}, 500.0, a), [])
        self.assertTrue(hb.waiting_for({"progress": dict(pr, ground=[0, 1])}, 500.0, a))
        self.assertEqual(hb.waiting_for({"progress": dict(pr, ground=[0, 1]), "ground_s": a.ground_wait}, 500.0, a),
                         [], "the photo's refresh waited for long enough")
        self.assertTrue(hb.waiting_for({"progress": dict(pr, roads=[5, 9])}, 500.0, a))
        self.assertTrue(hb.waiting_for({"progress": dict(pr, far=[30, 40])}, 500.0, a))


class HeightmapTest(unittest.TestCase):
    def test_the_blocks_height_under_the_car(self):
        import heightmap
        with tempfile.TemporaryDirectory() as user:
            os.makedirs(os.path.join(user, "temp", "beamngEarth", "s1"))
            yy, xx = np.mgrid[0:512, 0:512]
            u = 1600.0 + 0.5 * xx + 0.25 * yy           # [gy, gx], gy = north
            q, zb, mh, _ = heightmap.write_png(os.path.join(user, "temp", "beamngEarth", "s1", "hm.png"), u)
            t = {"heightmap": "/temp/beamngEarth/s1/hm.png", "zBase": zb, "maxHeight": mh, "squareSize": 2.0,
                 "cx": 128.0, "cy": -256.0}
            hm = hb.read_heightmap(user, t)
            # sample (gx, gy) = (100, 300) is at x = cx - 512 + 200, y = cy - 512 + 600
            z = hb.ground_z(hm, 128.0 - 512 + 200, -256.0 - 512 + 600)
            self.assertAlmostEqual(z, 1600.0 + 50 + 75, delta=0.1)
            self.assertIsNone(hb.read_heightmap(user, dict(t, heightmap="/temp/beamngEarth/s1/gone.png")))
            self.assertIsNone(hb.ground_z(None, 0.0, 0.0))


class StandIn:
    """A server that answers the drive's requests at once (anchor_ready / terrain_ready / load_progress), the roads
    round the car taking LOAD_S after each teleport or rebase."""
    LOAD_S = 0.6

    def __init__(self):
        self.lsock = socket.socket()
        self.lsock.bind(("127.0.0.1", 0))
        self.lsock.listen(1)
        self.port = self.lsock.getsockname()[1]
        self.got = []
        self.anchors = 0
        self.loaded_at = 0.0
        self.serial = 0
        self.stop = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def send(self, m):
        self.conn.sendall(protocol.encode(m))

    def terrain(self, cx, cy):
        return {"cx": modsim.snap(cx, 512), "cy": modsim.snap(cy, 512), "heightmap": "/temp/beamngEarth/x/hm.png",
                "size": 512, "squareSize": 2.0, "zBase": 0.0, "maxHeight": 10.0, "q": 0.01}

    def run(self):
        self.conn, _ = self.lsock.accept()
        dec = protocol.Decoder()
        while not self.stop:
            try:
                data = self.conn.recv(65536)
            except OSError:
                return
            if not data:
                return
            for m in dec.feed(data):
                self.got.append(m)
                try:
                    self.answer(m)
                except OSError:         # (the drive closed the connection)
                    return

    def answer(self, m):
        t = m["type"]
        if t == "hello":
            self.send({"type": "hello", "sessionId": "s1", "blockSize": 512, "nearVisual": "detail"})
        elif t == "prepare_anchor":
            self.anchors += 1
            self.loaded_at = time.monotonic() + self.LOAD_S
            self.send({"type": "anchor_ready", "requestId": m["requestId"], "anchorId": self.anchors, "lat": m["lat"],
                       "lon": m["lon"], "groundZ": 1600.0, "terrain": self.terrain(m["cx"], m["cy"])})
        elif t == "prepare_terrain":
            self.send(dict(self.terrain(m["cx"], m["cy"]), type="terrain_ready", requestId=m["requestId"],
                           anchorId=m["anchorId"]))
        elif t == "pose":
            n = 3 if time.monotonic() >= self.loaded_at else 1
            self.send({"type": "load_progress", "anchorId": m["anchorId"], "ground": [1, 1], "roads": [n, 3],
                       "trees": [0, 0], "far": [20, 20]})
            self.serial += 1
            self.send({"type": "far_tile", "anchorId": m["anchorId"], "key": "k", "level": 3, "shape": "x",
                       "slotSerial": self.serial, "material": "bngEarthFar_ab12_00007"})

    def close(self):
        self.stop = True
        for s in (getattr(self, "conn", None), self.lsock):
            if s is not None:
                s.close()


class DriveTest(unittest.TestCase):
    def test_a_drive_onto_the_planned_anchors(self):
        line = [(40.0, -105.0), (40.0 + 2400 * DEG_N, -105.0)]
        dist = 900.0
        anchors, order = rb.plan_anchors({"t": line}, dist, both_ways=False)
        data = {"pinM": dist * rb.PIN_SHARE, "anchors": anchors}
        srv = StandIn()
        self.addCleanup(srv.close)
        c = hb.Client(srv.port)
        self.addCleanup(c.close)
        self.assertEqual(hb.hello(c, 10)["blockSize"], 512)
        said = []
        d = hb.Drive(c, gd_route(line), anchors, hb.pin_metres(data, dist), dist, 512, "/nonexistent",
                     opts(speed=250.0, max_wait=20.0), say=lambda *x, **k: said.append(x))
        s = d.run()
        self.assertTrue(s["complete"], s)
        asked = [(m["lat"], m["lon"]) for m in srv.got if m["type"] == "prepare_anchor"]
        self.assertEqual([m["purpose"] for m in srv.got if m["type"] == "prepare_anchor"],
                         ["teleport"] + ["rebase"] * (len(asked) - 1))
        self.assertEqual(asked, [tuple(anchors[i]) for i in order["t"]], "the planned anchors, in order")
        self.assertEqual(s["rebases"], len(order["t"]) - 1)
        self.assertGreater(s["prepares"], 0, "terrain-follow asked for blocks")
        self.assertGreaterEqual(s["waits"]["count"], 1, "stood while the roads loaded")
        self.assertEqual(s["waits"]["gave_up"], 0)
        by = s["seconds_by"]
        self.assertEqual(set(by), set(hb.CAUSES))
        self.assertAlmostEqual(sum(by.values()), s["seconds"], delta=1.0, msg="every second has a cause")
        self.assertGreater(by["moving"], 0.0)
        self.assertGreater(by["roads"] + by["anchor"], 0.0, "the stand-in's roads take LOAD_S after each anchor")
        self.assertEqual(s["speed"], 250.0)
        poses = [m for m in srv.got if m["type"] == "pose"]
        self.assertTrue(poses)
        self.assertEqual(poses[-1]["held"], [poses[-1]["anchorId"]])
        self.assertTrue(any(p.get("slotsGone") for p in poses), "pooled names let go")
        self.assertTrue(all(abs(p["z"] - 1600.0) < 1 for p in poses), "the teleport's ground height")
        commits = [m for m in srv.got if m["type"] == "commit_anchor"]
        self.assertEqual([m["anchorId"] for m in commits], list(range(1, len(asked) + 1)))

    def test_a_drive_moves_to_the_nearer_planned_anchor(self):
        # two planned anchors 1200 m apart, the rebase distance 15 km: the distance rule never fires on a 2.4 km
        # drive; the pinned-anchor rule moves the frame to B 150 m past the midpoint (B 300 m nearer)
        A, B = (40.0, -105.0), (40.0 + 1200 * DEG_N, -105.0)
        line = [A, (40.0 + 2400 * DEG_N, -105.0)]
        srv = StandIn()
        self.addCleanup(srv.close)
        c = hb.Client(srv.port)
        self.addCleanup(c.close)
        hb.hello(c, 10)
        d = hb.Drive(c, gd_route(line), [A, B], 6750.0, 15000.0, 1024, "/nonexistent",
                     opts(speed=100.0, max_wait=20.0), say=lambda *x, **k: None)
        s = d.run()
        self.assertTrue(s["complete"], s)
        asked = [(m["lat"], m["lon"], m["purpose"]) for m in srv.got if m["type"] == "prepare_anchor"]
        self.assertEqual(asked, [(A[0], A[1], "teleport"), (B[0], B[1], "rebase")])
        self.assertEqual((s["rebases"], s["pinned"], s["discards"]), (1, 1, 0))
        rebase = next(m for m in srv.got if m["type"] == "prepare_anchor" and m["purpose"] == "rebase")
        self.assertLess(abs(rebase["cx"]), 3000, "the hint: a block near the player, not 15 km ahead")
        self.assertEqual(s["anchors"], [[A[0], A[1]], [round(B[0], 6), B[1]]])

    def test_on_past_a_spot_that_never_loads(self):
        line = [(40.0, -105.0), (40.0 + 600 * DEG_N, -105.0)]
        srv = StandIn()
        srv.LOAD_S = 1e9
        self.addCleanup(srv.close)
        c = hb.Client(srv.port)
        self.addCleanup(c.close)
        hb.hello(c, 10)
        d = hb.Drive(c, gd_route(line), [], 0.0, 15000.0, 512, "/nonexistent", opts(speed=300.0, max_wait=1.0),
                     say=lambda *x, **k: None)
        s = d.run()
        self.assertTrue(s["complete"], s)
        self.assertEqual(s["waits"]["gave_up"], 1, "once, then on (GAVE_UP_M)")


def gd_route(line):
    import ghost_drive
    return ghost_drive.Route(line)


class ServerProcessTest(unittest.TestCase):
    def test_started_found_and_stopped(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "server.log")
            child = "import time; print('earthd listening on 127.0.0.1:47999 session x'); time.sleep(60)"
            proc, port = hb.start_server([sys.executable, "-c", child], log, timeout=20)
            self.assertEqual(port, 47999)
            self.assertIsNone(proc.poll())
            hb.stop_server(proc)
            self.assertIsNotNone(proc.poll())
            hb.stop_server(proc)        # (twice: harmless)

    def test_a_server_on_the_games_port_is_stopped(self):
        with tempfile.TemporaryDirectory() as d:
            child = f"import time; print('earthd listening on 127.0.0.1:{hb.GAME_PORT} session x'); time.sleep(60)"
            with self.assertRaises(RuntimeError):
                hb.start_server([sys.executable, "-c", child], os.path.join(d, "server.log"), timeout=20)

    def test_a_server_that_ends(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                hb.start_server([sys.executable, "-c", "raise SystemExit(3)"], os.path.join(d, "s.log"), timeout=20)


class CommandTest(unittest.TestCase):
    """main against a stand-in server: the plan read, the legs driven, the run in the manifest with each leg's
    seconds by cause; --jobs children (child_command patched to a script that writes their summaries) waited for and
    read back."""

    def plan(self, d, line, dist):
        anchors, order = rb.plan_anchors({"t": line}, dist, both_ways=False)
        bd = os.path.join(d, "world", "bake")
        os.makedirs(bd)
        rb.write_json(os.path.join(bd, "anchors.json"),
                      {"version": 1, "pinM": dist * rb.PIN_SHARE, "rebaseDistance": dist, "anchors": anchors,
                       "routes": {"t": {"name": "test", "anchors": order["t"], "km": 2.4}}})
        rb.write_json(os.path.join(bd, "routes.json"), {"t": [list(p) for p in line]})
        return bd

    def args(self, d):
        return ["--world-dir", os.path.join(d, "world"), "--cache-dir", os.path.join(d, "cache"),
                "--user-dir", os.path.join(d, "user"), "--out", os.path.join(d, "out")]

    def test_a_run_against_a_server(self):
        line = [(40.0, -105.0), (40.0 + 2400 * DEG_N, -105.0)]
        with tempfile.TemporaryDirectory() as d:
            bd = self.plan(d, line, 900.0)
            srv = StandIn()
            self.addCleanup(srv.close)
            rc = hb.main(["--connect", str(srv.port), "--rebase-m", "900", "--speed", "250", "--max-wait", "20",
                          "--mode", "game", "--jobs", "2"] + self.args(d))
            self.assertEqual(rc, 0)
            man = rb.read_json(os.path.join(bd, rb.MANIFEST))
            run = man["runs"][-1]
            self.assertTrue(run["complete"])
            self.assertEqual((run["driver"], run["mode"], run["speed"], run["jobs"]), ("headless", "game", 250.0, 1),
                             "--connect: one job, whatever --jobs says")
            self.assertEqual([lg["route"] for lg in run["legs"]], ["t"])
            s = run["legs"][0]["summary"]
            self.assertTrue(s["complete"])
            self.assertIn("seconds_by", s)
            out = os.listdir(os.path.join(d, "out"))
            self.assertEqual(len(out), 1, "one stamped run folder")
            self.assertTrue(os.path.exists(os.path.join(d, "out", out[0], "t", "summary.json")))
            self.assertTrue(os.path.exists(os.path.join(d, "out", out[0], "t", "samples.jsonl")))

    def test_split_stretches_against_a_server(self):
        line = [(40.0, -105.0), (40.0 + 2400 * DEG_N, -105.0)]
        with tempfile.TemporaryDirectory() as d:
            bd = self.plan(d, line, 900.0)
            srv = StandIn()
            self.addCleanup(srv.close)
            rc = hb.main(["--connect", str(srv.port), "--rebase-m", "900", "--speed", "250", "--max-wait", "20",
                          "--mode", "game", "--split", "2"] + self.args(d))
            self.assertEqual(rc, 0)
            run = rb.read_json(os.path.join(bd, rb.MANIFEST))["runs"][-1]
            self.assertEqual([(lg["route"], lg["part"]) for lg in run["legs"]], [("t", [0, 2]), ("t", [1, 2])])
            self.assertTrue(all(lg["summary"]["complete"] for lg in run["legs"]))
            self.assertAlmostEqual(run["legs"][0]["summary"]["route_km"], 2.2, delta=0.05, msg="1.2 km + 1 km overlap")
            out = os.listdir(os.path.join(d, "out"))[0]
            self.assertEqual(sorted(os.listdir(os.path.join(d, "out", out))), ["t_1of2", "t_2of2"])

    def test_jobs_children_read_back(self):
        line = [(40.0, -105.0), (40.0 + 2400 * DEG_N, -105.0)]
        with tempfile.TemporaryDirectory() as d:
            bd = self.plan(d, line, 900.0)
            cmds = []

            def fake_child(a, legs, out, mode, workers, road_processes):
                cmds.append((legs, mode, workers, road_processes))
                script = ("import json, os, sys\n"
                          "for leg in sys.argv[2].split(','):\n"
                          "    os.makedirs(os.path.join(sys.argv[1], leg), exist_ok=True)\n"
                          "    s = {'seconds': 5.0, 'km': 2.4, 'route_km': 2.4, 'complete': leg != 't_back',\n"
                          "         'rebases': 1, 'prepares': 2, 'seconds_by': {'moving': 5.0},\n"
                          "         'waits': {'count': 0, 'seconds': 0.0, 'gave_up': 0}}\n"
                          "    json.dump(s, open(os.path.join(sys.argv[1], leg, 'summary.json'), 'w'))\n")
                return [sys.executable, "-c", script, out, ",".join(hb.leg_label(*lg) for lg in legs)]
            with unittest.mock.patch.object(hb, "child_command", fake_child):
                rc = hb.main(["--rebase-m", "900", "--mode", "alone", "--jobs", "2", "--both-ways"] + self.args(d))
            self.assertEqual(rc, 1, "a leg that did not finish (t_back) fails the run")
            self.assertEqual([lg for lg, _, _, _ in cmds], [[("t", False, None)], [("t", True, None)]], "a job per leg")
            far, road = hb.plan_servers(2, 2)[1:3]
            self.assertEqual({(m, w, r) for _, m, w, r in cmds}, {("alone", far, road)}, "the plan's workers")
            run = rb.read_json(os.path.join(bd, rb.MANIFEST))["runs"][-1]
            self.assertFalse(run["complete"])
            self.assertEqual(run["jobs"], 2)
            self.assertEqual([(lg["route"], lg["back"], lg["rc"], lg["summary"]["complete"]) for lg in run["legs"]],
                             [("t", False, 0, True), ("t", True, 0, False)])


class ProfileTest(unittest.TestCase):
    """--profile: the server's -v, the workers' CPU lines summed by job kind (the last, cumulative line of each
    process), the sampler over a process tree, and the summary line."""
    LOG = ("2026-10-28 10:20:00,000 DEBUG earthd: worker 100 cpu: roads_tile 10 jobs 20.0 s\n"
           "2026-10-28 10:20:30,000 DEBUG earthd: worker 100 cpu: roads_tile 25 jobs 61.5 s; shared road plans read 5\n"
           "2026-10-28 10:20:31,000 DEBUG earthd: worker 200 cpu: far_geometry 300 jobs 12.0 s, far_tile 300 jobs 30.0 s\n"
           "2026-10-28 10:20:32,000 DEBUG earthd: worker 300 cpu: build 8 jobs 9.5 s, base_photo 8 jobs 20.0 s\n"
           "2026-10-28 10:20:33,000 INFO earthd: terrain (1, 0.0, 0.0) z16 in 1.0 s (heights 0.5 grading 0.1)\n")

    def test_v_only_with_profile(self):
        with tempfile.TemporaryDirectory() as user:
            self.assertNotIn("-v", hb.server_command(user, 0, "/c", win=True))
            self.assertEqual(hb.server_command(user, 0, "/c", win=True, profile=True)[-1], "-v")
        a = argparse.Namespace(speed=80.0, roads_q=1, backlog_q=4, edge_m=160.0, max_wait=180.0, ground_wait=20.0,
                               cache_dir=None, world_dir=None, user_dir=None, rebase_m=None, profile=True)
        self.assertEqual(hb.child_command(a, [("i25", False, None)], "/o", "alone", 1, 1)[-1], "--profile")
        a.profile = False
        self.assertNotIn("--profile", hb.child_command(a, [("i25", False, None)], "/o", "alone", 1, 1))

    def test_cpu_by_kind_takes_each_workers_last_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "server.log")
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.LOG)
            kinds, roles = hb.cpu_by_kind(p)
            self.assertEqual(kinds, {"roads_tile": (25, 61.5), "far_geometry": (300, 12.0), "far_tile": (300, 30.0),
                                     "build": (8, 9.5), "base_photo": (8, 20.0)})
            self.assertEqual(roles, {100: "roads", 200: "far", 300: "block"})
            self.assertEqual(hb.cpu_by_kind(os.path.join(d, "none.log")), ({}, {}))

    def test_sampler_over_a_process_tree(self):
        try:
            import psutil  # noqa: F401
        except ImportError:
            self.skipTest("psutil not installed")
        child = ("import subprocess, sys, time\n"
                 "p = subprocess.Popen([sys.executable, '-c', 't=time.time()\\nwhile time.time()-t<2: pass'] if False else"
                 " [sys.executable, '-c', 'import time\\nt=time.time()\\nwhile time.time()-t<2: pass'])\n"
                 "t = time.time()\n"
                 "while time.time() - t < 2: pass\n"
                 "p.wait()\n")
        proc = subprocess.Popen([sys.executable, "-c", child])
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cpu.jsonl")
            s = hb.CpuSampler(proc.pid, path, every=0.2, say=lambda *x, **k: None).start()
            time.sleep(1.5)
            s.stop()
            with open(os.path.join(d, "server.log"), "w", encoding="utf-8") as f:
                f.write(self.LOG)
            p = s.summary(os.path.join(d, "server.log"))
            self.assertGreaterEqual(len(p["by_pid"]), 1)
            self.assertIn(proc.pid, p["by_pid"], "the root process")
            self.assertGreater(p["cpu_s"], 0.3, "two busy loops over 1.5 s")
            self.assertEqual(p["by_kind"]["roads_tile"], [25, 61.5])
            self.assertIn("server", p["by_role"], "the busiest process without jobs")
            with open(path, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            self.assertGreaterEqual(len(rows), 3)
            self.assertIn(str(proc.pid), rows[-1]["cpu"])
            text = hb.profile_text(p)
            self.assertTrue(text.startswith("profile: ") and "cores used" in text and "roads_tile 25 in 62 s" in text,
                            text)
        proc.wait(20)

    def test_summary_without_samples(self):
        s = hb.CpuSampler(1, "/nonexistent/cpu.jsonl", say=lambda *x, **k: None)
        s.psutil = None
        p = s.summary(None)
        self.assertEqual((p["cpu_s"], p["by_pid"], p["by_role"], p["by_kind"]), (0.0, {}, {}, {}))
        self.assertTrue(hb.profile_text(p).startswith("profile: 0.00 cores used"))


if __name__ == "__main__":
    unittest.main()
