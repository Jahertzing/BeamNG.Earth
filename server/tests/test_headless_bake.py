"""tools/headless_bake.py: its server's command (the launcher's options, never the game's port), the mod's pinning and
rebase hint, when the car moves on, the heightmap's height, a drive against a stand-in server (teleport, rebases onto
the planned anchors, terrain-follow, poses) and its server's start and stop."""
import argparse
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import headless_bake as hb  # noqa: E402
import modsim  # noqa: E402
import protocol  # noqa: E402
import route_bake as rb  # noqa: E402

DEG_N = 1 / 111195.0        # degrees of latitude per metre


def opts(**kw):
    o = dict(speed=hb.SPEED, roads_q=hb.ROADS_Q, backlog_q=hb.BACKLOG_Q, edge_m=hb.EDGE_M, max_wait=hb.MAX_WAIT_S)
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


class RulesTest(unittest.TestCase):
    def test_pinned_to_the_planned_anchor(self):
        anchors = [(40.0, -105.0), (40.0 + 3000 * DEG_N, -105.0)]
        self.assertEqual(hb.anchor_for(40.0 + 2900 * DEG_N, -105.0, anchors, 450.0), anchors[1])
        self.assertEqual(hb.anchor_for(40.0 + 1500 * DEG_N, -105.0, anchors, 450.0),
                         rb.snap_anchor(40.0 + 1500 * DEG_N, -105.0), "none near: the grid point")
        self.assertEqual(hb.anchor_for(40.0, -105.0, anchors, 0.0), rb.snap_anchor(40.0, -105.0))
        self.assertEqual(hb.pin_metres({"pinM": 6750.0}, 15000.0), 6750.0)
        self.assertEqual(hb.pin_metres({"pinM": 6750.0}, 10000.0), 4500.0, "core.lua PIN_SHARE of the setting")

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
        poses = [m for m in srv.got if m["type"] == "pose"]
        self.assertTrue(poses)
        self.assertEqual(poses[-1]["held"], [poses[-1]["anchorId"]])
        self.assertTrue(any(p.get("slotsGone") for p in poses), "pooled names let go")
        self.assertTrue(all(abs(p["z"] - 1600.0) < 1 for p in poses), "the teleport's ground height")
        commits = [m for m in srv.got if m["type"] == "commit_anchor"]
        self.assertEqual([m["anchorId"] for m in commits], list(range(1, len(asked) + 1)))

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


if __name__ == "__main__":
    unittest.main()
