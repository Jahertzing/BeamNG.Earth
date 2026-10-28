"""tools/aibrain.py and aibrain_fit.py: the outside brain's IDM and MOBIL, its levels, the UDP round trip, the fit."""
import json
import math
import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import aibrain as ab  # noqa: E402

W = 3.7


def car(speed=30.0, lane=2, lanes=3, near=(), limit=31.3, err=0.0, heading=0.0, cid=7, inputs=None):
    return {"id": cid, "speed": speed, "len": 4.5, "inputs": inputs,
            "road": {"limit": limit, "oneWay": True, "lanes": lanes, "lane": lane, "laneWidth": W, "laneErr": err,
                     "heading": heading},
            "near": list(near)}


def other(fwd, lane_offset=0, v=30.0, same=True, cid=9):
    """A neighbour fwd m ahead (negative: behind), lane_offset lanes to the LEFT."""
    return {"id": cid, "fwd": fwd, "left": lane_offset * W, "vFwd": v, "vLeft": 0.0, "same": same, "len": 4.5}


class IdmTest(unittest.TestCase):
    def test_free_road_accelerates_to_v0(self):
        m = ab.IDM(v0=30)
        self.assertAlmostEqual(m.accel(0), 1.5)
        self.assertAlmostEqual(m.accel(30), 0.0)
        self.assertLess(m.accel(33), 0)

    def test_close_slower_leader_brakes(self):
        m = ab.IDM(v0=30)
        self.assertLess(m.accel(30, gap=15, dv=10), -3)
        self.assertGreater(m.accel(30, gap=200, dv=0), -0.5)


class NeighbourTest(unittest.TestCase):
    def test_lane_from_lateral_offset(self):
        c = car(near=[other(30, 0, cid=1), other(20, 1, cid=2), other(-10, -1, cid=3), other(50, 0, same=False, cid=4)])
        lead, follow = ab.neighbours(c, 2)
        self.assertEqual(lead["id"], 1)
        self.assertIsNone(follow)
        self.assertEqual(ab.neighbours(c, 1)[0]["id"], 2)       # one lane LEFT = lane 1
        self.assertEqual(ab.neighbours(c, 3)[1]["id"], 3)       # one lane right, behind: the follower there
        self.assertAlmostEqual(ab.gap_to(c, other(30)), 25.5)
        self.assertEqual(ab.gap_to(c, None), math.inf)

    def test_personality_is_stable_and_bounded(self):
        self.assertEqual(ab.personality(12), ab.personality(12))
        vals = [ab.personality(i) for i in range(200)]
        self.assertTrue(all(0.92 <= v <= 1.08 for v in vals))
        self.assertGreater(max(vals) - min(vals), 0.1)


class MobilTest(unittest.TestCase):
    def setUp(self):
        self.p = ab.IdmMobil()

    def test_free_road_keeps_right(self):
        self.assertEqual(self.p.act(car(lane=2), {})["lane"], 1)        # bias to the kerb lane on an empty road
        self.assertEqual(self.p.act(car(lane=3), {})["lane"], 0)        # already rightmost
        self.assertEqual(self.p.act(car(lane=2), {"driveOnLeft": True})["lane"], -1)

    def test_passes_a_slow_leader_on_the_left(self):
        c = car(lane=3, near=[other(25, 0, v=20)])
        self.assertEqual(self.p.act(c, {})["lane"], -1)

    def test_no_change_into_a_car_alongside(self):
        c = car(lane=3, near=[other(25, 0, v=20), other(0.5, 1, v=30, cid=2)])
        self.assertEqual(self.p.act(c, {})["lane"], 0)

    def test_no_change_that_makes_the_new_follower_brake_hard(self):
        c = car(speed=20, lane=3, near=[other(20, 0, v=15), other(-6, 1, v=33, cid=2)])
        self.assertEqual(self.p.act(c, {})["lane"], 0)

    def test_speed_is_capped_by_the_desired_speed_and_slows_behind_a_leader(self):
        free = self.p.act(car(speed=31.3, lane=3), {})["speed"]
        self.assertLessEqual(free, 31.3 * 1.08 + 1e-9)
        behind = self.p.act(car(speed=30, lane=3, lanes=1, near=[other(20, 0, v=15)]), {})["speed"]
        self.assertLess(behind, 25)

    def test_off_the_graph_hands_speed_back(self):
        c = car()
        c["road"] = None
        self.assertEqual(self.p.act(c, {}), {"lane": 0, "speed": None})


class LowerLevelsTest(unittest.TestCase):
    def test_lanekeep_aims_at_the_lane_centre(self):
        a = ab.LaneKeep().act(car(err=0.8), {})
        self.assertAlmostEqual(a["left"], 0.8)         # 0.8 m right of the centre: aim 0.8 m left
        self.assertEqual(a["ahead"], 30.0)
        bend = ab.LaneKeep().act(car(heading=0.05), {})
        self.assertAlmostEqual(bend["left"], 1.5)      # the road bends left

    def test_steer_turns_toward_the_lane_and_brakes_when_too_fast(self):
        a = ab.Steer().act(car(err=1.0), {})
        self.assertLess(a["steer"], 0)                 # target to the left: steer left (negative)
        fast = ab.Steer().act(car(speed=40, lanes=1, lane=1), {})
        self.assertEqual(fast["throttle"], 0)
        self.assertGreater(fast["brake"], 0)

    def test_stock_sends_nothing(self):
        self.assertEqual(ab.answer(ab.Stock(), {"seq": 3, "cars": [car()]}), {"seq": 3, "actions": {}})


class AnswerTest(unittest.TestCase):
    def test_one_bad_car_does_not_stop_the_rest(self):
        class Flaky:
            level = "tactical"

            def act(self, c, msg):
                if c["id"] == 1:
                    raise ValueError("boom")
                return {"lane": 0, "speed": 10}
        import io
        err, sys.stderr = sys.stderr, io.StringIO()
        try:
            r = ab.answer(Flaky(), {"seq": 1, "cars": [car(cid=1), car(cid=2)]})
        finally:
            sys.stderr = err
        self.assertEqual(r["actions"], {"2": {"lane": 0, "speed": 10}})

    def test_udp_round_trip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        t = threading.Thread(target=ab.serve, args=(ab.IdmMobil(), "127.0.0.1", port, None, True), daemon=True)
        import io
        out, sys.stdout = sys.stdout, io.StringIO()
        try:
            t.start()
            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            c.settimeout(0.2)
            data = None
            for _ in range(50):     # (the server thread binds first; until then Windows resets the client socket)
                c.sendto(json.dumps({"seq": 11, "level": "tactical", "cars": [car()]}).encode(), ("127.0.0.1", port))
                try:
                    data, _ = c.recvfrom(65536)
                    break
                except (socket.timeout, ConnectionResetError):
                    continue
            c.close()
            reply = json.loads(data)
        finally:
            sys.stdout = out
        self.assertEqual(reply["seq"], 11)
        self.assertEqual(set(reply["actions"]["7"]), {"lane", "speed"})


class FitTest(unittest.TestCase):
    def test_recovers_a_proportional_driver(self):
        import aibrain_fit as fit
        import random
        rnd = random.Random(1)
        lines = []
        for _ in range(400):
            err, v = rnd.uniform(-1.5, 1.5), rnd.uniform(10, 35)
            inputs = {"steer": -0.4 * err, "throttle": max(0.0, 0.05 * (31.3 - v)), "brake": 0.0}
            lines.append(json.dumps({"car": car(speed=v, err=err, inputs=inputs)}))
        lines.append(json.dumps({"car": car(inputs=None)}))           # no inputs: skipped
        X, Y = fit.samples(lines)
        self.assertEqual(len(X), 400)
        m = fit.fit(X, Y, ridge=0.01)
        self.assertGreater(m["r2"]["steer"], 0.99)
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_aibrain_model.json")
        try:
            with open(path, "w") as f:
                json.dump(m, f)
            a = ab.Linear(path).act(car(err=1.0), {})
        finally:
            os.remove(path)
        self.assertAlmostEqual(a["steer"], -0.4, places=2)


if __name__ == "__main__":
    unittest.main()
