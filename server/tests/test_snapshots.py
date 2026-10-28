"""snapshots.py: a drive snapshot kept for good, with the game's and the server's log lines of its moment."""
import datetime
import json
import logging
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import snapshots  # noqa: E402

T0 = 1793227000.0       # the game session's start (unix s)


def game_log(path, start, lines):
    stamp = datetime.datetime.fromtimestamp(start).astimezone()
    head = ("  0.00246|D|logging| -- Log started - v 0.40.3.0 - x64 - build 290897 - "
            + stamp.strftime("%Y-%m-%d %H:%M:%S %z") + " -----\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head)
        for at, text in lines:
            f.write(f"{at:9.5f}|I|GELua.test| {text}\n" if at is not None else text + "\n")


class SnapshotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user = os.path.join(self.tmp.name, "user")
        self.home = os.path.join(self.tmp.name, "snapshots")
        self.src = os.path.join(self.user, "beamngEarth", "snapshots", "20261028_164512_1")
        os.makedirs(self.src)
        self.t = T0 + 1000
        with open(os.path.join(self.src, "snapshot.json"), "w") as f:
            json.dump({"kind": "snapshot", "unix": self.t, "userDir": self.user, "screenshot": "screenshot.jpg"}, f)
        with open(os.path.join(self.src, "screenshot.jpg"), "wb") as f:
            f.write(b"\xff\xd8jpeg")
        game_log(os.path.join(self.user, "beamng.log"), T0, [
            (900.0, "long before"), (950.0, "a minute before"), (999.5, "just before"),
            (None, "  a stack trace line"), (1004.0, "just after"), (1010.0, "too late")])
        game_log(os.path.join(self.user, "beamng.1.log"), T0 - 5000, [(6000.0, "the session before")])

    def tearDown(self):
        self.tmp.cleanup()

    def test_game_log_window_picks_the_session_and_the_window(self):
        lines = snapshots.game_log_window(self.user, self.t)
        text = "\n".join(lines)
        for want in ("a minute before", "just before", "a stack trace line", "just after"):
            self.assertIn(want, text)
        for not_want in ("long before", "too late", "session before"):
            self.assertNotIn(not_want, text)
        self.assertEqual(snapshots.game_log_window(self.user, T0 - 9000), [])

    def test_server_log_window(self):
        path = os.path.join(self.tmp.name, "server.log")

        def stamp(t):
            d = datetime.datetime.fromtimestamp(t)
            return d.strftime("%Y-%m-%d %H:%M:%S") + ",%03d" % int((t % 1) * 1000)
        with open(path, "w") as f:
            f.write(f"{stamp(self.t - 120)} INFO earthd: old\n{stamp(self.t - 2)} INFO earthd: near\n"
                    "  continued\n" f"{stamp(self.t + 30)} INFO earthd: later\n")
        lines = snapshots.server_log_window(path, self.t)
        self.assertEqual([x.split(": ")[-1] for x in lines], ["near", "  continued"])

    def test_ingest_copies_and_adds_logs_once(self):
        dest = snapshots.ingest(self.src, dest_root=self.home, server_lines=["srv line"], build={"commit": "abc"})
        self.assertEqual(dest, os.path.join(self.home, "20261028_164512_1"))
        for f in ("snapshot.json", "screenshot.jpg", "beamng.log.txt", "earthd.log.txt", "build.json"):
            self.assertTrue(os.path.isfile(os.path.join(dest, f)), f)
        with open(os.path.join(dest, "beamng.log.txt")) as f:
            self.assertIn("just before", f.read())
        # the note added in the game later: copied again; the catalog's review is never touched
        with open(os.path.join(dest, "review.json"), "w") as f:
            f.write('{"notes": "mine"}')
        time.sleep(0.01)
        with open(os.path.join(self.src, "snapshot.json"), "w") as f:
            json.dump({"kind": "snapshot", "unix": self.t, "userDir": self.user, "note": "a note, longer file"}, f)
        with open(os.path.join(self.src, "review.json"), "w") as f:
            f.write('{"notes": "not this one"}')
        snapshots.ingest(self.src, dest_root=self.home, server_lines=["other"])
        with open(os.path.join(dest, "snapshot.json")) as f:
            self.assertEqual(json.load(f)["note"], "a note, longer file")
        with open(os.path.join(dest, "review.json")) as f:
            self.assertEqual(json.load(f)["notes"], "mine")
        with open(os.path.join(dest, "earthd.log.txt")) as f:
            self.assertEqual(f.read(), "srv line\n")

    def test_too_recent_waits_for_its_logs_then_scan_adds_them(self):
        with open(os.path.join(self.src, "snapshot.json"), "w") as f:
            json.dump({"unix": time.time() + 100, "userDir": self.user}, f)
        dest = snapshots.ingest(self.src, dest_root=self.home)
        self.assertFalse(os.path.exists(os.path.join(dest, "beamng.log.txt")))
        self.assertTrue(os.path.exists(os.path.join(dest, "snapshot.json")))
        with open(os.path.join(self.src, "snapshot.json"), "w") as f:
            json.dump({"unix": self.t, "userDir": self.user}, f)
        os.utime(os.path.join(self.src, "snapshot.json"), (time.time() + 5, time.time() + 5))
        self.assertEqual(snapshots.scan([self.user], dest_root=self.home), ["20261028_164512_1"])
        self.assertTrue(os.path.exists(os.path.join(dest, "beamng.log.txt")))
        self.assertEqual(snapshots.scan([self.user], dest_root=self.home), [])

    def test_not_a_snapshot(self):
        bad = os.path.join(self.tmp.name, "elsewhere", "x")
        os.makedirs(bad)
        self.assertIsNone(snapshots.ingest(bad, dest_root=self.home))
        self.assertIsNone(snapshots.ingest_message({"folder": bad}, dest_root=self.home))

    def test_ring_and_message(self):
        ring = snapshots.LogRing(10)
        lg = logging.getLogger("test_snapshots")
        lg.addHandler(ring)
        lg.setLevel(logging.INFO)
        try:
            lg.info("from the server")
        finally:
            lg.removeHandler(ring)
        now = time.time()
        self.assertEqual(len(ring.window(now)), 1)
        self.assertEqual(ring.window(now + 600), [])
        with open(os.path.join(self.src, "snapshot.json"), "w") as f:
            json.dump({"unix": now - 4.5, "userDir": self.user}, f)
        dest = snapshots.ingest_message({"folder": self.src.replace("\\", "/") + "/", "unix": now - 4.5}, ring=ring,
                                        build={"commit": "abc"}, dest_root=self.home)
        with open(os.path.join(dest, "earthd.log.txt")) as f:
            self.assertIn("from the server", f.read())

    def test_build_info_of_this_checkout(self):
        b = snapshots.build_info(server_version="9.9")
        self.assertEqual(b["serverVersion"], "9.9")
        if "commit" in b:
            self.assertEqual(len(b["commit"]), 40)
            self.assertIn("dirty", b)


if __name__ == "__main__":
    unittest.main()
