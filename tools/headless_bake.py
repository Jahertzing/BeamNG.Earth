#!/usr/bin/env python3
"""The route bake without the game: a world server of its own, at idle priority, driven along each baked route by a
scripted client that sends what the mod sends on a drive. The user, 2026-10-28: why does the bake need the game at
all? Could it run while he plays? It can: nothing the bake keeps is made in the game. The terrain blocks, base photos
(with their packed copy for the game's terrain texture cache: earthd writes it, bc7.py), road, building and far tiles
are all built by earthd. Their revisit keys hold only the server's code and BNE_* switches, its options and the
anchor, block or tile (the road tiles' left-hand / mph / lamp rules come from the anchor, on the server). The far field
counts as shown once its swap group is sent (no ack from the mod), and load_progress says how far the ground, the road
tiles and the far field round the car are. The game adds only the frame times: the ghost drive (ghost_drive.py) stays
the benchmark.

  python3 tools/headless_bake.py                         every planned route (route_bake.py plan), one way
  python3 tools/headless_bake.py --routes i25 --both-ways
  python3 tools/headless_bake.py --connect 47620         a server already running (tests; never the game's)

The server: launcher.py --server with the launcher's own options (RECOMMENDED, the mod settings' setting_flags, the
remembered --cache-dir) and this process's environment, so its keys are the game server's when both start from the
same folder and shell; then --port 0 (a free port: never the game's GAME_PORT), --workers 1 and --road-processes 1,
--load-boost off (it never lends itself the game's cores), --map-tiles off. Windows: the idle priority class (its worker
processes inherit it; --nice 0, or it would raise itself to below normal); elsewhere --nice 19 --sched idle. It shares
the game's user folder (each server touches its session marker every minute and sweeps only folders idle for ten), the
download cache and the world folder (revisit writes a temp file and renames it). It ends with this tool: stopped in a
finally, and launcher.watch_parent (BNGEARTH_PARENT) ends it if this process is killed.

The client (the mod's rules from server/tests/modsim.py, line-by-line ports of follow.lua and rebase.lua): hello; per
route a teleport to its start (the anchor pinned as routepins.lua does: the planned anchor within PIN_SHARE of the
rebase distance, else the grid point); then the car moved along routes.json at --speed, a pose every POSE_S (its height
read from the committed block's heightmap: the horizon far field's eye), the terrain-follow and rebase requests as the
mod makes them. It moves on only when the place is loaded: no anchor request pending (a rebase's, once ready, does
not count: it commits past the rebase distance), the next block ready before the car nears its block's edge, the ground
final, at most --roads-q road tiles and --backlog-q far tiles still to come round the car. After --max-wait s at one
spot it moves on anyway (counted in the summary). Writes <out>/<route>/samples.jsonl and summary.json, and a run in the
bake's manifest.json (route_bake.py status / clean read it).
"""
import argparse
import json
import math
import os
import re
import select
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, os.path.join(ROOT, "server", "tests"), os.path.join(ROOT, "server"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import ghost_drive as gd  # noqa: E402  (Route, load_route)
import modsim  # noqa: E402
import route_bake as rb  # noqa: E402

GAME_PORT = 47613           # earthd's default: the game's server (launcher.SERVER_PORT)
IS_WIN = os.name == "nt"
TICK_S = 0.1                # the client's frame
POSE_S = 0.5                # core.lua POSE_INTERVAL
SPEED = 27.0                # m/s along the road (the ghost drive's)
ROADS_Q = 1                 # road tiles round the car still to come, at most, to move on
BACKLOG_Q = 4               # far tiles likewise (the ghost's --backlog-q)
EDGE_M = 160.0              # the next block must be ready before the car is this near its block's edge
MAX_WAIT_S = 180.0
# the ground's "not final" (earthd ground_progress: the block's base photo waiting to be drawn again with lazy imagery or
# analysis regions still to come) waited for this long at most: the server itself gives up after GROUND_WAIT_S (90 s),
# and on the first headless runs nothing came in those 90 s (2026-10-28: two stops of 91 s by 11.5 km)
GROUND_WAIT_S = 20.0
GAVE_UP_M = 1000.0          # after giving up at a spot, no standing again for this far
START_S = 600.0             # the server's start (its caches cut back, the airports read) at most
FOLLOW_RETRY_S = 2.0        # after a terrain_error
CAR_Z = 0.5                 # the car's position over the ground (the pose's z)
SAMPLE_S = 1.0              # a samples.jsonl line this often
LISTEN_RE = re.compile(r"earthd listening on 127\.0\.0\.1:(\d+)")
MOD_VERSION = re.search(r"MOD_VERSION = '([^']+)'", (modsim.MOD_DIR / "core.lua").read_text()).group(1)
THROTTLE = ["--workers", "1", "--road-processes", "1", "--load-boost", "off", "--map-tiles", "off"]


# --- the server -----------------------------------------------------------------------------------------------------
def server_command(user_dir, port=0, cache_dir=None, win=IS_WIN):
    """launcher.start_server's command for user_dir (its options and the mod settings' flags, the remembered cache
    folder), then this tool's: a free port, few workers, idle priority."""
    if port == GAME_PORT:
        raise ValueError(f"port {GAME_PORT} is the game's server")
    import launcher
    if cache_dir is None:
        cache_dir = launcher.cache_dir_arg(None)
    cmd = [sys.executable, os.path.join(ROOT, "launcher.py"), "--server", "--user-dir", user_dir]
    cmd += launcher.RECOMMENDED + launcher.setting_flags(user_dir)
    if cache_dir:
        cmd += ["--cache-dir", cache_dir]
    cmd += ["--port", str(int(port))] + THROTTLE
    cmd += ["--nice", "0"] if win else ["--nice", "19", "--sched", "idle"]
    return cmd


def start_server(cmd, log_path, timeout=START_S):
    """(process, port): cmd started at idle priority, its output to log_path, once it listens."""
    env = dict(os.environ, BNGEARTH_PARENT=str(os.getpid()), PYTHONUNBUFFERED="1")
    kw = {"cwd": ROOT, "env": env}
    if IS_WIN:
        kw["creationflags"] = subprocess.IDLE_PRIORITY_CLASS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    out = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, **kw)
    out.close()
    end = time.monotonic() + timeout
    try:
        while time.monotonic() < end:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                m = LISTEN_RE.search(f.read())
            if m:
                port = int(m.group(1))
                if port == GAME_PORT:       # (never: --port 0; a guard against a changed command)
                    raise RuntimeError(f"the server took the game's port {GAME_PORT}")
                return proc, port
            if proc.poll() is not None:
                raise RuntimeError(f"the server ended (exit {proc.returncode}): see {log_path}")
            time.sleep(0.5)
        raise RuntimeError(f"the server did not listen within {timeout:.0f} s: see {log_path}")
    except BaseException:
        stop_server(proc)
        raise


def stop_server(proc):
    """The server and its worker processes stopped (Windows: the tree; elsewhere its process group)."""
    if proc is None or proc.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            proc.terminate()
    try:
        proc.wait(20)
    except subprocess.TimeoutExpired:
        if not IS_WIN:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        proc.kill()
        proc.wait(10)


# --- the connection -------------------------------------------------------------------------------------------------
class Client:
    """earthd's protocol (protocol.py framing) without blocking: poll() returns what has arrived."""

    def __init__(self, port, timeout=10.0):
        import protocol
        self.protocol = protocol
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.sock.setblocking(False)
        self.dec = protocol.Decoder()

    def send(self, msg):
        data = self.protocol.encode(msg)
        self.sock.setblocking(True)
        try:
            self.sock.sendall(data)
        finally:
            self.sock.setblocking(False)

    def poll(self, timeout=0.0):
        out = []
        end = time.monotonic() + timeout
        while True:
            r, _, _ = select.select([self.sock], [], [], max(0.0, end - time.monotonic()))
            if not r:
                return out
            try:
                data = self.sock.recv(1 << 20)
            except BlockingIOError:
                continue
            if not data:
                raise ConnectionError("the server closed the connection")
            out += self.dec.feed(data)
            if out:
                return out

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# --- the mod's rules ------------------------------------------------------------------------------------------------
def pin_metres(data, distance):
    """routepins.pin's distance: the plan's pinM, within core.lua PIN_SHARE of the rebase distance and MAX_PIN_M."""
    return min(float(data.get("pinM") or 0.0), rb.MAX_PIN_M, rb.PIN_SHARE * distance)


def anchor_for(lat, lon, anchors, pin_m):
    """core.lua rebaseAnchor / teleportTo: the planned anchor nearest within pin_m, else the grid point."""
    a = rb.pin((lat, lon), anchors, pin_m) if pin_m > 0 and anchors else None
    return tuple(a) if a is not None else rb.snap_anchor(lat, lon)


def rebase_hint(vx, vy, dist, distance, dx, dy, size, latency=None):
    """core.lua rebaseHint with the player's spot (dx, dy) in the new frame (modsim.rebase_hint has it at the
    origin)."""
    rem = max(0.0, distance - dist)
    if rem <= 0 and modsim.rebase_table(size)["KEEP_EDGE"] > 0:
        lead = modsim.rebase_ahead_lead(latency)
        lat = lead - modsim.REBASE["LEAD_EXTRA_S"]
        return modsim.follow_centre_near(dx + vx * lead, dy + vy * lead, dx + vx * lat, dy + vy * lat,
                                         modsim.follow_mode(vx, vy, size)[1], size)
    speed = math.hypot(vx, vy)
    px = py = 0.0
    if speed > 1:
        px, py = vx / speed * rem, vy / speed * rem
    return modsim.follow_target(dx + px, dy + py, vx, vy, size)


def waiting_for(st, edge, a):
    """Why the car does not move on yet (empty: it does). st: the drive's state, edge: the car's distance to its
    block's ground edge (None: no block), a: the options."""
    why = []
    p = st.get("pending")
    if p and not (p["purpose"] == "rebase" and p.get("ready")):
        why.append(f"anchor request pending ({p['purpose']})")
    tp = st.get("tp")
    if tp and not tp.get("ready") and edge is not None and edge < a.edge_m:
        why.append(f"terrain block ({tp['cx']:.0f}, {tp['cy']:.0f}) not ready, {edge:.0f} m from the edge")
    pr = st.get("progress") or {}
    g, r, f = pr.get("ground") or (1, 1), pr.get("roads") or (0, 0), pr.get("far") or (0, 0)
    if g[0] < g[1] and (st.get("ground_s") or 0.0) < a.ground_wait:
        why.append("ground not final")
    if r[1] - r[0] > a.roads_q:
        why.append(f"road tiles {r[0]}/{r[1]}")
    if f[1] - f[0] > a.backlog_q:
        why.append(f"far tiles {f[0]}/{f[1]}")
    return why


def read_heightmap(user_dir, terrain):
    """(qgrid, zBase, maxHeight, square, cx, cy) of a terrain message's block from its PNG in the session folder
    (rows flipped back: row 0 = south), or None."""
    try:
        import numpy as np
        from PIL import Image
        path = os.path.join(user_dir, *terrain["heightmap"].strip("/").split("/"))
        with Image.open(path) as im:
            q = np.array(im, dtype=np.uint16)[::-1]
        return (q, float(terrain["zBase"]), float(terrain["maxHeight"]), float(terrain.get("squareSize") or 2.0),
                float(terrain["cx"]), float(terrain["cy"]))
    except Exception:
        return None


def ground_z(hm, x, y):
    if hm is None:
        return None
    import heightmap
    q, zb, mh, sq, cx, cy = hm
    return heightmap.height_at(q, zb, mh, cx, cy, x, y, sq)


# --- the drive ------------------------------------------------------------------------------------------------------
class Drive:
    """One route driven through the client c. a: the options (speed, roads_q, backlog_q, edge_m, max_wait)."""

    def __init__(self, c, route, anchors, pin_m, distance, size, user_dir, a, next_id=1, out=None, say=print):
        import geodesy
        self.geodesy = geodesy
        self.c, self.route, self.anchors, self.pin_m = c, route, anchors, pin_m
        self.distance, self.size, self.user_dir, self.a = distance, size, user_dir, a
        self.say, self.out = say, out
        self.next_id = next_id
        self.st = {"pending": None, "tp": None, "progress": None}
        self.frame = self.aid = self.block = None
        self.hm = {}                # (anchor id, cx, cy) -> read_heightmap
        self.z = 0.0
        self.t_lat = self.a_lat = None
        self.retry_at = self.follow_retry = 0.0
        self.slots_gone = []
        self.stats = {"rebases": 0, "prepares": 0, "commits": 0, "anchor_errors": 0, "terrain_errors": 0,
                      "discards": 0, "anchors": []}
        self.waits = []

    def rid(self):
        self.next_id += 1
        return self.next_id

    # messages
    def handle(self, m, now):
        t = m.get("type")
        st = self.st
        p, tp = st["pending"], st["tp"]
        if t == "anchor_ready" and p and m.get("requestId") == p["requestId"]:
            p["ready"], p["msg"] = True, m
            self.a_lat = now - p["sent"]
            hm = read_heightmap(self.user_dir, m["terrain"])
            if hm is not None:
                self.hm[(m["anchorId"], hm[4], hm[5])] = hm
            if m.get("groundZ") is not None and p["purpose"] == "teleport":
                self.z = float(m["groundZ"])
        elif t == "anchor_error" and p and m.get("requestId") == p["requestId"]:
            self.stats["anchor_errors"] += 1
            self.say(f"  anchor error ({p['purpose']}): {m.get('error')}")
            if p["purpose"] == "teleport":
                p["retry"] = now + modsim.REBASE["RETRY_S"]
            else:
                st["pending"] = None
                self.retry_at = now + modsim.REBASE["RETRY_S"]
        elif t == "terrain_ready" and tp and m.get("requestId") == tp["requestId"]:
            tp["ready"], tp["msg"] = True, m
            self.t_lat = now - tp["sent"]
            hm = read_heightmap(self.user_dir, m)
            if hm is not None:
                self.hm[(m["anchorId"], hm[4], hm[5])] = hm
        elif t == "terrain_error" and tp and m.get("requestId") == tp["requestId"]:
            self.stats["terrain_errors"] += 1
            st["tp"] = None
            self.follow_retry = now + FOLLOW_RETRY_S
        elif t == "load_progress":
            st["progress"] = m
        elif t == "far_tile" and m.get("slotSerial") is not None and isinstance(m.get("material"), str):
            # nothing is drawn here: the pooled name is let go at once (the mod reports it when the tile's object goes)
            try:
                self.slots_gone.append([int(m["material"].rsplit("_", 1)[1]), m["slotSerial"]])
            except ValueError:
                pass

    def drain(self, now, timeout=0.0):
        for m in self.c.poll(timeout):
            self.handle(m, now)

    # requests
    def teleport(self, lat, lon, now):
        aLat, aLon = anchor_for(lat, lon, self.anchors, self.pin_m)
        px, py, _ = self.geodesy.EnuFrame(aLat, aLon).geodetic_to_enu(lat, lon, 0.0)
        px, py = float(px), float(py)
        cx, cy = modsim.follow_target(px, py, 0.0, 0.0, self.size)
        self.st["pending"] = {"requestId": self.rid(), "purpose": "teleport", "sent": now, "lat": aLat, "lon": aLon,
                              "cx": cx, "cy": cy, "px": px, "py": py}
        self.send_anchor(self.st["pending"])

    def send_anchor(self, p):
        msg = {"type": "prepare_anchor", "requestId": p["requestId"], "lat": p["lat"], "lon": p["lon"], "cx": p["cx"],
               "cy": p["cy"], "purpose": p["purpose"]}
        if p.get("px") is not None:
            msg["px"], msg["py"] = p["px"], p["py"]
        self.c.send(msg)

    def commit(self, m):
        self.frame = self.geodesy.EnuFrame(m["lat"], m["lon"])
        self.aid = m["anchorId"]
        self.block = (m["terrain"]["cx"], m["terrain"]["cy"])
        self.st["tp"] = None
        self.st["pending"] = None
        self.c.send({"type": "commit_anchor", "anchorId": self.aid})
        self.c.send({"type": "commit_terrain", "anchorId": self.aid, "cx": self.block[0], "cy": self.block[1]})
        self.stats["anchors"].append([round(m["lat"], 6), round(m["lon"], 6)])
        for k in [k for k in self.hm if k[0] != self.aid]:
            del self.hm[k]

    def enu(self, frame, lat, lon):
        e, n, _ = frame.geodetic_to_enu(lat, lon, 0.0)
        return float(e), float(n)

    def step(self, s, now):
        """One frame of the mod's rules with the car at s (moving along the road at --speed). Returns the car's
        distance to its block's ground edge (None before the first anchor)."""
        st, size = self.st, self.size
        p = st["pending"]
        if p and p["purpose"] == "teleport":
            if p.get("ready"):
                self.commit(p["msg"])
            elif p.get("retry") and now >= p["retry"]:
                p["retry"], p["sent"] = None, now
                p["requestId"] = self.rid()
                self.send_anchor(p)
            if self.frame is None or st["pending"]:
                return None
        lat, lon = self.route.point(s)
        hlat, hlon = self.route.ahead(s)
        x, y = self.enu(self.frame, lat, lon)
        hx, hy = self.enu(self.frame, hlat, hlon)
        d = math.hypot(hx - x, hy - y) or 1.0
        vx, vy = self.a.speed * (hx - x) / d, self.a.speed * (hy - y) / d
        # origin rebase first (core.lua updateWorld)
        p = st["pending"]
        stale = edge = None
        if p and p.get("ready"):
            m = p["msg"]
            nx, ny = self.enu(self.geodesy.EnuFrame(m["lat"], m["lon"]), lat, lon)
            stale = modsim.cheb(nx, ny, m["terrain"]["cx"], m["terrain"]["cy"])
            gx0, gy0, gx1, gy1 = modsim.ground_box(m["terrain"]["cx"], m["terrain"]["cy"], size)
            edge = min(nx - gx0, gx1 - nx, ny - gy0, gy1 - ny)
        dist = math.hypot(x, y)
        act = modsim.rebase_decide(dist, p, stale, self.distance, now, self.retry_at, edge=edge, size=size)
        if act == "prefetch":
            aLat, aLon = anchor_for(lat, lon, self.anchors, self.pin_m)
            dx, dy = self.enu(self.geodesy.EnuFrame(aLat, aLon), lat, lon)
            cx, cy = rebase_hint(vx, vy, dist, self.distance, dx, dy, size, self.a_lat)
            st["pending"] = {"requestId": self.rid(), "purpose": "rebase", "sent": now, "lat": aLat, "lon": aLon,
                             "cx": cx, "cy": cy}
            self.send_anchor(st["pending"])
        elif act == "commit":
            self.commit(p["msg"])
            self.stats["rebases"] += 1
            x, y = self.enu(self.frame, lat, lon)
            hx, hy = self.enu(self.frame, hlat, hlon)
            d = math.hypot(hx - x, hy - y) or 1.0
            vx, vy = self.a.speed * (hx - x) / d, self.a.speed * (hy - y) / d
        elif act == "discard":
            st["pending"] = None
            self.stats["discards"] += 1
        if act != "commit":         # terrain-follow (mesh mode: no commit gating)
            b, tp = self.block, st["tp"]
            fa = modsim.follow_decide(x, y, vx, vy, b[0], b[1], tp, now, self.follow_retry, None, self.t_lat, size)
            if fa and fa[0] == "prepare":
                st["tp"] = {"requestId": self.rid(), "cx": fa[1], "cy": fa[2], "sent": now}
                self.stats["prepares"] += 1
                self.c.send({"type": "prepare_terrain", "requestId": st["tp"]["requestId"], "anchorId": self.aid,
                             "cx": fa[1], "cy": fa[2]})
            elif fa and fa[0] == "commit":
                self.block, st["tp"] = (tp["cx"], tp["cy"]), None
                self.stats["commits"] += 1
                self.c.send({"type": "commit_terrain", "anchorId": self.aid, "cx": tp["cx"], "cy": tp["cy"]})
            elif fa and fa[0] == "discard":
                st["tp"] = None
        z = ground_z(self.hm.get((self.aid, float(self.block[0]), float(self.block[1]))), x, y)
        if z is not None:
            self.z = z + CAR_Z
        self.pos = (x, y, vx, vy, lat, lon)
        gx0, gy0, gx1, gy1 = modsim.ground_box(*self.block, size)
        return min(x - gx0, gx1 - x, y - gy0, gy1 - y)

    def pose(self):
        x, y, vx, vy, lat, lon = self.pos
        msg = {"type": "pose", "anchorId": self.aid, "x": x, "y": y, "z": self.z, "vx": vx, "vy": vy, "lat": lat,
               "lon": lon, "speed": self.a.speed, "follow": True, "held": [self.aid], "sessions": []}
        if self.slots_gone:
            msg["slotsGone"], self.slots_gone = self.slots_gone, []
        self.c.send(msg)

    def run(self):
        """Drive the route; returns its summary."""
        t0 = time.monotonic()
        now = 0.0
        s, last = 0.0, 0.0
        pose_at = sample_at = 0.0
        wait = None             # the current wait: {"at_km": km, "t0": now, "why": [...]}
        free_until = -1.0       # gave up at a spot: on without standing until here (m)
        ground_t0 = None        # since when the ground is not final
        lat0, lon0 = self.route.point(0.0)
        self.teleport(lat0, lon0, now)
        samples = open(os.path.join(self.out, "samples.jsonl"), "w", encoding="utf-8") if self.out else None
        try:
            while True:
                self.drain(now, max(0.0, t0 + now + TICK_S - time.monotonic()))
                prev, now = now, time.monotonic() - t0
                dt = min(now - prev, 0.5)
                edge = self.step(s, now)
                if self.frame is None or self.st["pending"] and self.st["pending"]["purpose"] == "teleport":
                    why = ["anchor request pending (teleport)"]
                else:
                    g = (self.st.get("progress") or {}).get("ground") or (1, 1)
                    if g[0] < g[1]:
                        ground_t0 = now if ground_t0 is None else ground_t0
                        self.st["ground_s"] = now - ground_t0
                    else:
                        ground_t0, self.st["ground_s"] = None, 0.0
                    why = waiting_for(self.st, edge, self.a)
                    if now >= pose_at:
                        pose_at = now + POSE_S
                        self.pose()
                gave_up = False
                if why and s < free_until and not (self.frame is None or self.st["pending"]
                                                   and self.st["pending"]["purpose"] == "teleport"):
                    why = []            # (on past a spot that does not load: a finding, not a stop)
                if why:
                    if wait is None:
                        wait = {"at_km": round(s / 1000, 2), "t0": now, "why": why}
                    if now - wait["t0"] >= self.a.max_wait:
                        gave_up = True
                if wait is not None and (not why or gave_up):
                    self.waits.append({"at_km": wait["at_km"], "s": round(now - wait["t0"], 1), "why": wait["why"],
                                       "gave_up": gave_up, "last": why})
                    if gave_up:
                        free_until = s + GAVE_UP_M
                        self.say(f"  gave up waiting at {wait['at_km']} km after {now - wait['t0']:.0f} s: "
                                 f"{'; '.join(why)}")
                    wait = None
                if s >= self.route.length and (not why or gave_up):
                    break
                if not why or gave_up:
                    s = min(self.route.length, s + self.a.speed * dt)
                if now >= sample_at:
                    sample_at = now + SAMPLE_S
                    pr = self.st.get("progress") or {}
                    row = {"t": round(now, 1), "km": round(s / 1000, 3), "anchor": self.aid, "why": why,
                           "progress": {k: pr.get(k) for k in ("ground", "roads", "far")}}
                    if samples:
                        samples.write(json.dumps(row) + "\n")
                    if now - last >= 10.0:
                        last = now
                        self.say(f"  {s / 1000:6.2f} / {self.route.length / 1000:.2f} km, anchor {self.aid}"
                                 f"{': ' + '; '.join(why) if why else ''}", flush=True)
        finally:
            if samples:
                samples.close()
        return self.summary(now, s)

    def summary(self, seconds, s):
        return {"seconds": round(seconds, 1), "km": round(s / 1000, 2), "route_km": round(self.route.length / 1000, 2),
                "complete": s >= self.route.length - 1, **{k: v for k, v in self.stats.items()},
                "waits": {"count": len(self.waits), "seconds": round(sum(w["s"] for w in self.waits), 1),
                          "gave_up": sum(1 for w in self.waits if w["gave_up"]),
                          "longest": sorted(self.waits, key=lambda w: -w["s"])[:10]}}


# --- the command ----------------------------------------------------------------------------------------------------
def hello(c, timeout=120.0):
    core = modsim.CORE
    c.send({"type": "hello", "protocol": int(core["PROTOCOL"]), "format": int(core["FORMAT"]),
            "modVersion": MOD_VERSION, "sessions": [], "vegFlat": True})
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for m in c.poll(1.0):
            if m.get("type") == "hello":
                return m
    raise RuntimeError("no hello from the server")


def bne_env():
    return {k: v for k, v in sorted(os.environ.items()) if k.startswith("BNE_")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--routes", default=None, help="comma list of the planned routes (default: all in routes.json)")
    ap.add_argument("--both-ways", action="store_true", help="each route back again too")
    ap.add_argument("--speed", type=float, default=SPEED, help="m/s along the road")
    ap.add_argument("--roads-q", type=int, default=ROADS_Q)
    ap.add_argument("--backlog-q", type=int, default=BACKLOG_Q)
    ap.add_argument("--edge-m", type=float, default=EDGE_M)
    ap.add_argument("--max-wait", type=float, default=MAX_WAIT_S)
    ap.add_argument("--ground-wait", type=float, default=GROUND_WAIT_S,
                    help="s to wait for the ground's final photo at most (the server's own limit is 90)")
    ap.add_argument("--connect", type=int, default=None, help="drive a server already listening on this port")
    ap.add_argument("--cache-dir", default=None, help="the server's cache (default: the launcher's)")
    ap.add_argument("--world-dir", default=None, help="the world folder (default: the server's)")
    ap.add_argument("--user-dir", default=None, help="the game's user folder (default: the one the game uses)")
    ap.add_argument("--rebase-m", type=float, default=None, help="the rebase distance (default: the mod's setting)")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "headless_bake"))
    a = ap.parse_args(argv)
    if a.connect == GAME_PORT:
        ap.error(f"port {GAME_PORT} is the game's server: this tool drives a server of its own")
    a.world_dir = a.world_dir or rb.default_world_dir()
    a.user_dir = a.user_dir or rb.default_user_dir()
    import revisit
    bd = rb.bake_dir(argparse.Namespace(cache_dir=a.cache_dir or rb.default_cache_dir(), world_dir=a.world_dir))
    data = rb.read_json(os.path.join(bd, "anchors.json"))
    routes_file = os.path.join(bd, "routes.json")
    lines = rb.read_json(routes_file)
    if not data or not lines:
        print(f"no plan in {bd}: route_bake.py plan first", file=sys.stderr)
        return 2
    names = a.routes.split(",") if a.routes else list(lines)
    distance = a.rebase_m or rb.rebase_distance(a.user_dir)
    if data.get("rebaseDistance") and abs(float(data["rebaseDistance"]) - distance) > 1:
        print(f"warning: the plan is for rebases every {float(data['rebaseDistance']) / 1000:g} km, the mod's setting "
              f"is {distance / 1000:g} km: plan again (route_bake.py plan)", file=sys.stderr)
    anchors = [tuple(map(float, x)) for x in data.get("anchors") or ()]
    pin_m = pin_metres(data, distance)
    code = revisit.code_key()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(a.out, stamp)
    os.makedirs(out, exist_ok=True)
    man_path = os.path.join(bd, rb.MANIFEST)
    man = rb.read_json(man_path, {})
    prior = next((r.get("code") for r in reversed(man.get("runs") or []) if r.get("code")), None)
    print(f"headless bake: {', '.join(names)}{' both ways' if a.both_ways else ''} at {a.speed:g} m/s; "
          f"code {code}, BNE env {bne_env() or 'none'}; bake {bd}")
    if prior and prior != code:
        print(f"note: the last bake run had code {prior}: a server/*.py change or other BNE_* switches since",
              flush=True)
    run = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "start_ts": time.time(), "code": code, "routes": names,
           "driver": "headless", "complete": False, "legs": []}
    man.setdefault("runs", []).append(run)
    rb.write_json(man_path, man)
    proc = c = None
    ok = True
    try:
        if a.connect:
            port = a.connect
        else:
            cmd = server_command(a.user_dir, 0, a.cache_dir)
            print("server: " + " ".join(cmd[1:]), flush=True)
            proc, port = start_server(cmd, os.path.join(out, "server.log"))
            print(f"server listening on {port} (log {os.path.join(out, 'server.log')})", flush=True)
        c = Client(port)
        h = hello(c)
        size = int(h.get("blockSize") or 1024)
        print(f"session {h.get('sessionId')}, block {size}, near visual {h.get('nearVisual')}", flush=True)
        next_id = 1
        for n in names:
            if n not in lines:
                print(f"{n}: not in {routes_file}", file=sys.stderr)
                ok = False
                continue
            for back in ((False, True) if a.both_ways else (False,)):
                route = gd.load_route(routes_file, n, back)
                leg = f"{n}{'_back' if back else ''}"
                lo = os.path.join(out, leg)
                os.makedirs(lo, exist_ok=True)
                print(f"{leg}: {route.length / 1000:.1f} km", flush=True)
                d = Drive(c, route, anchors, pin_m, distance, size, a.user_dir, a, next_id, lo)
                summary = d.run()
                next_id = d.next_id
                rb.write_json(os.path.join(lo, "summary.json"), summary)
                run["legs"].append({"route": n, "back": back, "driver": "headless", "rc": 0, "out": lo,
                                    "summary": summary})
                rb.write_json(man_path, man)
                w = summary["waits"]
                print(f"{leg}: {summary['km']} of {summary['route_km']} km in {summary['seconds']:.0f} s; "
                      f"{summary['rebases']} rebases, {summary['prepares']} blocks asked; waited {w['count']}x "
                      f"{w['seconds']:.0f} s ({w['gave_up']} gave up)", flush=True)
                ok = ok and summary["complete"]
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        ok = False
    except (RuntimeError, ConnectionError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        ok = False
    finally:
        if c is not None:
            c.close()
        stop_server(proc)
        run["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        run["complete"] = ok
        rb.write_json(man_path, man)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
