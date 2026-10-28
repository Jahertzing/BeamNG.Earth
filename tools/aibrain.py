#!/usr/bin/env python3
"""The outside brain for BeamNG.Earth's AI cars (docs/AI_BRAIN.md): listens on UDP for the mod's car states
(beamngEarth/aibrain.lua) and answers with actions, 10 times a second.

  python tools/aibrain.py                          # IDM + MOBIL highway driver, tactical level (the default)
  python tools/aibrain.py --policy lanekeep        # target level: a point on the lane centre ahead + IDM speed
  python tools/aibrain.py --policy steer           # raw level: wheel and pedals from the same, a P controller
  python tools/aibrain.py --policy stock --record runs/i25.jsonl   # watch the stock AI, record its inputs
  python tools/aibrain.py --policy linear:runs/model.json         # raw level: a model fitted by aibrain_fit.py
  python tools/aibrain.py --policy mypkg.mymod:MyPolicy           # your own (a class with level and act())

Then in the game's console (~):
  extensions.load('beamngEarth_aibrain'); beamngEarth_aibrain.start({ level = 'tactical' })
The level there must be the policy's (printed at start). Each datagram from the game:
  {"seq", "t", "level", "driveOnLeft", "cars": [{"id", "speed", "len", "inputs": {"steer", "throttle", "brake"},
    "road": {"limit", "oneWay", "lanes", "lane" (1 = leftmost), "laneWidth", "laneErr" (m right of the lane centre),
             "heading" (sine of the road's direction to the left of the car's)},
    "near": [{"id", "fwd", "left", "vFwd", "vLeft", "same", "len", "player"}]}]}
and the answer: {"seq", "actions": {"<id>": action}}, action per level:
  tactical {"lane": -1 | 0 | 1, "speed": m/s or null}   target {"ahead": m, "left": m, "speed": m/s}
  raw {"steer": -1..1 (positive right), "throttle": 0..1, "brake": 0..1}   observe: none
"""
import argparse
import importlib
import json
import math
import os
import socket
import sys
import time

HOST, PORT = "127.0.0.1", 47620


# ---- IDM: the Intelligent Driver Model (Treiber, Hennecke, Helbing 2000), a car's acceleration behind a leader ----
class IDM:
    def __init__(self, v0=31.0, T=1.4, a=1.5, b=2.0, s0=2.5, delta=4):
        self.v0, self.T, self.a, self.b, self.s0, self.delta = v0, T, a, b, s0, delta

    def accel(self, v, gap=math.inf, dv=0.0, v0=None):
        """m/s^2 at speed v (m/s) with a bumper-to-bumper gap (m) to a leader dv m/s slower (inf: no leader)."""
        v0 = max(0.1, v0 if v0 is not None else self.v0)
        free = 1 - (max(0.0, v) / v0) ** self.delta
        if not math.isfinite(gap):
            return self.a * free
        s_star = self.s0 + max(0.0, v * self.T + v * dv / (2 * math.sqrt(self.a * self.b)))
        return self.a * (free - (s_star / max(gap, 0.1)) ** 2)


def lane_of(n, my_lane, width):
    """The lane (1 = leftmost) of neighbour n, from its offset to my left."""
    if not width:
        return my_lane
    return my_lane - int(round(n["left"] / width))


def neighbours(car, lane):
    """(leader, follower) in `lane` going my way: the nearest ahead and behind, as near[] entries (or None)."""
    road = car.get("road") or {}
    width, mine = road.get("laneWidth"), road.get("lane") or 0
    lead = follow = None
    for n in car.get("near", ()):
        if not n.get("same") or lane_of(n, mine, width) != lane:
            continue
        if n["fwd"] > 0 and (lead is None or n["fwd"] < lead["fwd"]):
            lead = n
        elif n["fwd"] <= 0 and (follow is None or n["fwd"] > follow["fwd"]):
            follow = n
    return lead, follow


def gap_to(car, other, ahead=True):
    """Bumper-to-bumper gap (m) between car and other (a near[] entry), inf for None."""
    if other is None:
        return math.inf
    half = (car.get("len") or 4.5) / 2 + (other.get("len") or 4.5) / 2
    return max(0.0, (other["fwd"] if ahead else -other["fwd"]) - half)


def personality(car_id, spread=0.08):
    """A car's own desired-speed factor, the same every run for one id (1 +- spread)."""
    h = (int(car_id) * 2654435761) % 2 ** 32 / 2 ** 32
    return 1 - spread + 2 * spread * h


class IdmMobil:
    """Tactical: IDM picks the speed, MOBIL (Kesting, Treiber, Helbing 2007) the lane change. The stock AI keeps the
    route, junctions, signals and steering. keep_right: US practice, a small bonus for moving right (a "keep right
    except to pass" bias); politeness: how much the new follower's braking counts."""
    level = "tactical"

    def __init__(self, politeness=0.3, threshold=0.2, b_safe=3.0, keep_right=0.3, horizon=1.5, flow=1.0):
        self.idm = IDM()
        self.p, self.th, self.b_safe, self.keep_right = politeness, threshold, b_safe, keep_right
        self.horizon, self.flow = horizon, flow

    def desired(self, car):
        limit = (car.get("road") or {}).get("limit") or self.idm.v0
        return limit * self.flow * personality(car["id"])

    def accel_in(self, car, lane, v0):
        lead, _ = neighbours(car, lane)
        dv = car["speed"] - lead["vFwd"] if lead else 0.0
        return self.idm.accel(car["speed"], gap_to(car, lead), dv, v0)

    def lane_choice(self, car, v0, drive_on_left=False):
        road = car.get("road") or {}
        n, mine = road.get("lanes") or 0, road.get("lane") or 0
        if n < 2 or not road.get("laneWidth"):
            return 0
        here = self.accel_in(car, mine, v0)
        best, best_gain = 0, self.th
        for step in (-1, 1):
            lane = mine + step
            if not 1 <= lane <= n:
                continue
            lead, follow = neighbours(car, lane)
            # safety: the new follower must not brake harder than b_safe, and nobody alongside
            if gap_to(car, lead) < 1.0 or gap_to(car, follow, ahead=False) < 1.0:
                continue
            gain = self.accel_in(car, lane, v0) - here
            if follow is not None:
                v_f = follow["vFwd"]
                a_f_new = self.idm.accel(v_f, gap_to(car, follow, ahead=False), v_f - car["speed"], v_f + 5)
                if a_f_new < -self.b_safe:
                    continue
                gain += self.p * min(0.0, a_f_new)
            toward_kerb = (step == 1) != bool(drive_on_left)
            gain += self.keep_right if toward_kerb else -self.keep_right
            if gain > best_gain:
                best, best_gain = step, gain
        return best

    def act(self, car, msg):
        if not car.get("road"):
            return {"lane": 0, "speed": None}       # off the graph: the stock AI's legal speed
        v0 = self.desired(car)
        a = self.accel_in(car, car["road"].get("lane") or 0, v0)
        speed = max(0.0, min(v0, car["speed"] + a * self.horizon))
        return {"lane": self.lane_choice(car, v0, msg.get("driveOnLeft")), "speed": round(speed, 2)}


class LaneKeep(IdmMobil):
    """Target level: a point on the lane centre one second ahead, IDM's speed. Shows the stock low-level
    controller following a brain's points; no lane changes."""
    level = "target"

    def target(self, car):
        road = car["road"]
        ahead = max(8.0, car["speed"] * 1.0)
        # laneErr m right of the lane centre: the centre is that far to the left, and the road turns away by heading
        return ahead, (road.get("laneErr") or 0.0) + ahead * (road.get("heading") or 0.0)

    def act(self, car, msg):
        if not car.get("road"):
            return {"ahead": 10, "left": 0, "speed": 0}
        ahead, left = self.target(car)
        tac = IdmMobil.act(self, car, msg)
        return {"ahead": round(ahead, 2), "left": round(left, 2), "speed": tac["speed"]}


class Steer(LaneKeep):
    """Raw level: the wheel from the angle to LaneKeep's point, the pedals from the speed error. A plain
    proportional controller: expect a wobble at speed; it is the baseline a learned model has to beat."""
    level = "raw"

    def __init__(self, k_steer=1.2, k_speed=0.25, **kw):
        super().__init__(**kw)
        self.k_steer, self.k_speed = k_steer, k_speed

    def act(self, car, msg):
        if not car.get("road"):
            return {"steer": 0, "throttle": 0, "brake": 1}
        ahead, left = self.target(car)
        steer = max(-1.0, min(1.0, -self.k_steer * math.atan2(left, ahead)))   # target to the left: steer left
        want = IdmMobil.act(self, car, msg)["speed"]
        err = want - car["speed"]
        throttle = max(0.0, min(1.0, self.k_speed * err))
        brake = max(0.0, min(1.0, -self.k_speed * err)) if err < -1.0 else 0.0
        return {"steer": round(steer, 4), "throttle": round(throttle, 3), "brake": round(brake, 3)}


class Stock:
    """Observe: no actions, the stock AI drives; with --record its inputs are the labels to learn from."""
    level = "observe"

    def act(self, car, msg):
        return None


# ---- a learned model: features -> raw controls, fitted by tools/aibrain_fit.py ----
FEATURES = ("speed", "limit", "lane_err", "heading", "gap_ahead", "dv_ahead", "lanes", "lane")


def features(car):
    """The fixed feature vector of an observation (FEATURES order); gap capped at 150 m."""
    road = car.get("road") or {}
    lead, _ = neighbours(car, road.get("lane") or 0)
    gap = min(150.0, gap_to(car, lead))
    dv = car["speed"] - lead["vFwd"] if lead else 0.0
    return [car["speed"], road.get("limit") or 0.0, road.get("laneErr") or 0.0, road.get("heading") or 0.0,
            gap, dv, float(road.get("lanes") or 0), float(road.get("lane") or 0)]


class Linear:
    """Raw level: steer, throttle and brake each a linear function of features() (weights from aibrain_fit.py)."""
    level = "raw"

    def __init__(self, path):
        with open(path) as f:
            m = json.load(f)
        self.w, self.mean, self.std = m["weights"], m["mean"], m["std"]

    def act(self, car, msg):
        if not car.get("road"):
            return {"steer": 0, "throttle": 0, "brake": 1}
        x = [(v - mu) / sd for v, mu, sd in zip(features(car), self.mean, self.std)] + [1.0]
        out = {k: sum(a * b for a, b in zip(w, x)) for k, w in self.w.items()}
        return {"steer": max(-1.0, min(1.0, out["steer"])), "throttle": max(0.0, min(1.0, out["throttle"])),
                "brake": max(0.0, min(1.0, out["brake"]))}


POLICIES = {"idm": IdmMobil, "lanekeep": LaneKeep, "steer": Steer, "stock": Stock}


def load_policy(spec):
    if spec in POLICIES:
        return POLICIES[spec]()
    if spec.startswith("linear:"):
        return Linear(spec[len("linear:"):])
    mod, _, cls = spec.partition(":")
    return getattr(importlib.import_module(mod), cls or "Policy")()


def answer(policy, msg):
    """The reply datagram for one message from the game."""
    actions = {}
    for car in msg.get("cars", ()):
        try:
            a = policy.act(car, msg)
        except Exception as e:      # one bad car must not stop the others (it gets no action: STALE_S rules)
            print(f"policy error on car {car.get('id')}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if a is not None:
            actions[str(car["id"])] = a
    return {"seq": msg.get("seq"), "actions": actions}


def serve(policy, host=HOST, port=PORT, record=None, quiet=False):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        # Windows: a reply to a game that has stopped listening makes the next recvfrom raise (ICMP port unreachable)
        sock.ioctl(socket.SIO_UDP_CONNRESET, False)
    sock.bind((host, port))
    print(f"aibrain: {type(policy).__name__} (level '{policy.level}') on udp {host}:{port}; in the game: "
          f"extensions.load('beamngEarth_aibrain'); beamngEarth_aibrain.start({{ level = '{policy.level}' }})")
    out = open(record, "a", encoding="utf-8") if record else None
    n, t_last, slow = 0, time.time(), 0.0
    try:
        while True:
            try:
                data, addr = sock.recvfrom(65536)
                msg = json.loads(data)
            except (ConnectionResetError, ValueError):
                continue
            t0 = time.perf_counter()
            if msg.get("level") != policy.level and n == 0:
                print(f"warning: the game runs level '{msg.get('level')}', this policy is '{policy.level}'")
            reply = answer(policy, msg)
            sock.sendto(json.dumps(reply, separators=(",", ":")).encode(), addr)
            slow = max(slow, time.perf_counter() - t0)
            if out:
                for car in msg.get("cars", ()):
                    out.write(json.dumps({"t": msg.get("t"), "seq": msg.get("seq"), "level": msg.get("level"),
                                          "car": car, "action": reply["actions"].get(str(car["id"]))},
                                         separators=(",", ":")) + "\n")
            n += 1
            if not quiet and time.time() - t_last >= 5:
                t_last = time.time()
                cars = msg.get("cars", ())
                print(f"{n} ticks, {len(cars)} cars, slowest answer {slow * 1000:.1f} ms")
                slow = 0.0
    except KeyboardInterrupt:
        pass
    finally:
        if out:
            out.close()
        sock.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--policy", default="idm", help="idm | lanekeep | steer | stock | linear:<model.json> | module:Class")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--record", help="append every observation and action to this JSONL file")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.record:
        os.makedirs(os.path.dirname(os.path.abspath(a.record)), exist_ok=True)
    serve(load_policy(a.policy), a.host, a.port, a.record, a.quiet)


if __name__ == "__main__":
    main()
