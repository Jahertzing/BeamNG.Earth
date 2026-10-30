"""The build-ahead cursor: the near tier built live, ahead of the car, instead of baked (the user, 2026-10-29: no bake
if it can be helped; the far tiers stay, the roads and the ground are what the car needs just in time).

A route bake (tools/route_bake.py, headless_bake.py) drove a simulated car along a route so the server built, into
its revisit cache, exactly the terrain blocks and road tiles the mod asks for along it. This module does the same
thing from inside the live server, a few km ahead of the real car: it samples the GPS route (routing.py's points,
Connection.route) or, without one, the car's heading, works out the anchor frame the mod will be in at each point
(the mod's rebase rules: rebase.lua's distance rule and routepins.lua's pinned-anchor rule, planned anchors the
server also hands to the mod so the frames agree), predicts the terrain blocks the mod's terrain-follow will ask
for there (follow.lua, through earthd.predict_centre) and the road tiles its tracker will want (roads.wanted_tiles),
and orders them by how far along the route they are due. The Connection runs them (earthd Server.build_ahead_block /
build_ahead_roads: built in phantom anchors into the revisit cache, the session copies discarded) a few at a time
while the car is within the horizon of each: the car's own requests then find everything in the cache, as after a
bake.

The horizon (how far ahead items are started) grows with the car's speed and with the measured latency of an item
(queued to done): speed * (LATENCY_K * latency + MARGIN_S), within MIN_M .. MAX_M. The cursor measures how fast the
"ready front" (the route distance up to which everything is built) advances while it has work queued: that rate,
in m of route per second, is the top speed at which the build keeps up with the car (reported in the log as a speed).

Pure: no asyncio, no server state; the Connection calls plan()/tick()/started()/done() and runs the jobs.
"""
import logging
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np

import geodesy
import mapcells
import roads as roadsmod

log = logging.getLogger("earthd.ahead")

ON = os.environ.get("BNE_BUILD_AHEAD", "on").lower() not in ("off", "0", "no", "false")
STEP_M = 128.0          # the route sampled this often (half a road tile: every tile a drive wants is met)
BLOCK_STEP_M = 16.0     # the terrain-follow simulation's step along the route (the mod decides per frame)
MIN_M = 2500.0          # the horizon: items due this far ahead are started whatever the speed ...
MAX_M = 15000.0         # ... and never farther than this (--ahead-km; the corridor's data comes 20 km ahead: routeahead)
MARGIN_S = 45.0         # s of driving beyond the measured latency the horizon covers (a cold cell's plans: 10-90 s)
LATENCY_K = 4.0         # the horizon covers this many times an item's measured latency at the car's speed
LATENCY_S0 = 8.0        # s: the latency assumed before any item is measured (a cold road tile)
LATENCY_ALPHA = 0.2     # the latency's exponential average
HEADING_MAX_M = 4000.0  # m: without a route the cursor looks this far along the heading, no farther
HEADING_DEG = 20.0      # ... and plans again once the car has turned this much ...
HEADING_MOVE_M = 500.0  # ... or moved this far from the plan's origin
OFF_ROUTE_M = 300.0     # m from the route the car counts as off it (the heading instead)
REPLAN_M = 2000.0       # the route plan is made again once the car moved this far along it (it reaches MAX_M + this)
ROAD_JOBS = 2           # road tiles building at once (--ahead-jobs)
BLOCK_JOBS = 1          # blocks building at once
PLAN_JOBS = 2           # map cells planning at once (the road processes; plan_items)
PLAN_LEAD_M = 1500.0    # a cell's plans are due this far before the first tile that needs them
PLAN_REACH_M = 900.0    # m round a sample whose cells a road tile there plans (SEND_M + the grading's neighbours)
SPEED_MIN = 15.0        # m/s: the horizon and the block prediction are for at least this speed
TICK_S = 0.5            # the cursor looks for work at most this often
LOG_S = 30.0            # the status line in the log at most this often
DONE_MAX = 20000        # keys remembered as done (cleared past this; the cache has them anyway)
PIN_SHARE = 0.45        # core.lua PIN_SHARE: the pin distance as a share of the rebase distance
MAX_PIN_M = 9000.0      # routepins.lua MAX_PIN_M
PIN_HYST_M = 300.0      # rebase.lua PIN_HYST_M
DEFAULT_DISTANCE = 15000.0  # rebase.lua DEFAULT_DISTANCE (the mod's rebaseDistance setting; the route message's rebaseM)
PREFETCH_MARGIN = 300.0     # rebase.lua PREFETCH_MARGIN: the next anchor is asked for this far before the distance
SNAP_DEG, SNAP_MAX_LAT = 0.01, 85.0     # geodesy.lua snapAnchor
EARTH_R = 6371008.8


# --- the mod's anchor rules ----------------------------------------------------------------------------------------
def snap_anchor(lat, lon):
    """geodesy.lua snapAnchor: the anchor grid point of (lat, lon) (a rebase or teleport lands on it without a pin)."""
    if abs(lat) > SNAP_MAX_LAT:
        return lat, lon
    la = math.floor(lat / SNAP_DEG + 0.5) * SNAP_DEG
    c = math.cos(math.radians(la))
    k = 1
    while c * k * 2 <= 1.0 + 1e-12 and k < 128:
        k *= 2
    step = SNAP_DEG * k
    lo = math.floor(lon / step + 0.5) * step
    if lo >= 180:
        lo -= 360
    elif lo < -180:
        lo += 360
    return round(la, 6), round(lo, 6)


def pin_m(rebase_m):
    """The pin distance for a rebase distance (core.lua: PIN_SHARE * rebase.distance, routepins MAX_PIN_M at most)."""
    return min(PIN_SHARE * float(rebase_m), MAX_PIN_M)


def metres(a, b):
    """The distance (m) between (lat, lon) a and b (equirectangular: the steps of a route line)."""
    la = math.radians((a[0] + b[0]) / 2)
    dn = math.radians(b[0] - a[0]) * EARTH_R
    de = math.radians(b[1] - a[1]) * EARTH_R * math.cos(la)
    return math.hypot(dn, de)


def resample(points, step=STEP_M, max_m=None):
    """points [[lat, lon], ...] -> (lats, lons, s) arrays: the line sampled every step metres along its length (its
    first point first, its last point last), stopping at max_m when given."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if not pts:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    if len(pts) == 1:
        return np.array([pts[0][0]]), np.array([pts[0][1]]), np.zeros(1)
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + metres(a, b))
    length = cum[-1] if max_m is None else min(cum[-1], float(max_m))
    n = int(math.floor(length / step))
    s = np.arange(n + 1, dtype=np.float64) * step
    if length - s[-1] > 1.0:
        s = np.append(s, length)
    cum = np.asarray(cum)
    la = np.interp(s, cum, [p[0] for p in pts])
    lo = np.interp(s, cum, [p[1] for p in pts])
    return la, lo, s


def heading_line(frame, x, y, ux, uy, length=HEADING_MAX_M, step=STEP_M):
    """The straight line from (x, y) of frame along the unit heading (ux, uy): [(lat, lon), ...] every step metres."""
    d = np.arange(0.0, length + step / 2, step)
    la, lo, _ = frame.enu_to_geodetic(x + ux * d, y + uy * d, 0.0)
    return list(zip(np.asarray(la, dtype=float).tolist(), np.asarray(lo, dtype=float).tolist()))


def frames(lats, lons, cur, rebase_m=DEFAULT_DISTANCE, pins=(), pinned_rule=True, hyst=PIN_HYST_M,
           margin=PREFETCH_MARGIN):
    """The anchor the mod is in at each sample of the line (lats, lons), starting in anchor cur (lat, lon):
    -> (anchors [(lat, lon)], index into anchors per sample, the anchors planned here (not in pins)).
    pins: the planned anchors the mod knows (a bake's routepins file, and those this server sent it): a rebase lands
    on the nearest within pin_m (core.lua rebaseAnchor), else on the snapped grid point at the car. The distance rule
    (rebase.lua: a new anchor once the car is rebase_m - margin from the current one) is walked first and plans the
    anchors it lands on; with pinned_rule (the mod applies routepins.want to the anchors we send it) the line is
    walked again over all of them: the frame moves to a planned anchor as soon as it is hyst nearer than the current
    one, wherever the car came from (tools/route_bake.py plan_anchors / walk_pinned do the same for a bake)."""
    pm = pin_m(rebase_m)
    cur = (round(float(cur[0]), 7), round(float(cur[1]), 7))
    known = [(round(float(a[0]), 7), round(float(a[1]), 7)) for a in pins]
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    n = len(lats)
    if n == 0:
        return [cur], np.zeros(0, dtype=np.int64), []
    mid = float(np.mean(lats))
    cache = {}

    def dist_to(a):             # the samples' horizontal distance from anchor a, in a's frame (as the mod measures)
        d = cache.get(a)
        if d is None:
            e, no, _ = geodesy.EnuFrame(a[0], a[1]).geodetic_to_enu(lats, lons, 0.0)
            d = cache[a] = np.hypot(np.asarray(e, dtype=float), np.asarray(no, dtype=float))
        return d

    def nearest_pin(i, cands):
        best = None
        for a in cands:
            if abs(a[0] - mid) > 0.3:       # (frames are only good nearby)
                continue
            d = float(dist_to(a)[i])
            if d <= pm and (best is None or d < best[0]):
                best = (d, a)
        return best

    def walk(cands, pinned):
        anchors, idx, planned, ci = [cur], np.zeros(n, dtype=np.int64), [], 0
        for i in range(n):
            d_cur = float(dist_to(anchors[ci])[i])
            np_ = nearest_pin(i, cands + planned)
            if pinned and np_ is not None and np_[1] != anchors[ci] and d_cur - np_[0] >= hyst:
                ci = _index(anchors, np_[1])
            elif d_cur >= rebase_m - margin:
                if np_ is not None and np_[1] != anchors[ci]:
                    a = np_[1]
                else:
                    a = snap_anchor(float(lats[i]), float(lons[i]))
                    a = (round(a[0], 7), round(a[1], 7))
                    if a not in planned and a not in cands and a != cur:
                        planned.append(a)
                ci = _index(anchors, a)
            idx[i] = ci
        return anchors, idx, planned

    anchors, idx, planned = walk(known, False)
    if pinned_rule:
        anchors, idx, more = walk(known + planned, True)
        planned += [a for a in more if a not in planned]
    return anchors, idx, planned


def _index(anchors, a):
    if a not in anchors:
        anchors.append(a)
    return anchors.index(a)


# --- the items -----------------------------------------------------------------------------------------------------
@dataclass(eq=False)
class Item:
    kind: str                   # "block" | "roads"
    anchor: tuple               # (lat, lon) of its frame
    key: tuple                  # block: (cx, cy); roads: (tx, ty)
    due: float                  # m along the line where the car first wants it
    queued: float = 0.0         # monotonic time it was started

    @property
    def id(self):
        return (self.kind, self.anchor, self.key)


def road_items(anchor, xs, ys, s, send_m=None):
    """The road tiles the mod's tracker wants along the samples (xs, ys) (in anchor's frame, due at s): roads.tiles_near
    within SEND_M of each (what wanted_tiles asks for standing still; the lead at speed is along the line anyway)."""
    send_m = roadsmod.SEND_M if send_m is None else send_m
    out = {}
    for x, y, d in zip(xs, ys, s):
        for k in roadsmod.tiles_near(float(x), float(y), send_m):
            if k not in out:
                out[k] = Item("roads", anchor, (int(k[0]), int(k[1])), float(d))
    return list(out.values())


def plan_items(anchor, lats, lons, s, reach_m=PLAN_REACH_M, lead_m=PLAN_LEAD_M):
    """The map cells the road tiles along the samples (lats, lons, due at s) have planned (grading's cell plans, the
    cold start's cost: one process planned a cell while every other tile waited on its lock): each cell once, due
    lead_m before the first sample that reaches it. Keyed by the mapcells key (regions plan them: the frame is only
    where the job runs)."""
    out = {}
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    for la, lo, d in zip(lats, lons, s):
        dlat = reach_m / 111320.0
        dlon = reach_m / (111320.0 * max(0.05, math.cos(math.radians(float(la)))))
        for k in mapcells.cells_for_bbox(float(la) - dlat, float(lo) - dlon, float(la) + dlat, float(lo) + dlon):
            kk = (int(k.z), int(k.x), int(k.y))
            if kk not in out:
                out[kk] = Item("plans", anchor, kk, max(0.0, float(d) - lead_m))
    return list(out.values())


def block_items(anchor, xs, ys, s, speed, follow, start=None, step=BLOCK_STEP_M):
    """The terrain blocks the mod's terrain-follow asks for along the samples (xs, ys) of anchor's frame at speed
    (m/s): the line walked in steps of step m with the car's velocity along it, follow(px, py, vx, vy, cx, cy) telling
    the centre the mod would have after that frame (earthd: the prepare box and predict_centre; cx, cy None at the
    frame's start). start: the block the car has now in this frame (the current one; None: follow's first)."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    s = np.asarray(s, dtype=float)
    out = []
    if len(xs) < 2:
        return out
    seen = set()
    c = tuple(start) if start is not None else None
    if c is not None:
        seen.add(c)
    v = max(float(speed), SPEED_MIN)
    total = float(s[-1] - s[0])
    n = max(1, int(math.ceil(total / step)))
    ss = s[0] + np.arange(n + 1) * (total / n)
    px = np.interp(ss, s, xs)
    py = np.interp(ss, s, ys)
    for i in range(len(ss)):
        j = min(i + 1, len(ss) - 1)
        dx, dy = px[j] - px[i], py[j] - py[i]
        if j == i:
            dx, dy = px[i] - px[i - 1], py[i] - py[i - 1]
        d = math.hypot(dx, dy)
        if d <= 0:
            continue
        vx, vy = v * dx / d, v * dy / d
        nc = follow(float(px[i]), float(py[i]), vx, vy, None if c is None else c[0], None if c is None else c[1])
        if nc is None:
            continue
        nc = (float(nc[0]), float(nc[1]))
        if nc != c:
            c = nc
            if c not in seen:
                seen.add(c)
                out.append(Item("block", anchor, c, float(ss[i])))
    return out


# --- the cursor ----------------------------------------------------------------------------------------------------
@dataclass
class Stats:
    built: int = 0
    cached: int = 0
    failed: int = 0
    latency: float = LATENCY_S0       # s per built item, queued to done (exponential average)
    busy_s: float = 0.0               # s the cursor had work queued beyond its ready front
    advance_m: float = 0.0            # m the ready front advanced in that time
    by_kind: dict = field(default_factory=dict)

    @property
    def rate(self):
        """m of route made ready per second while the cursor had work: the top speed the build keeps up with. None
        before it has worked long enough to tell."""
        if self.busy_s < 20.0 or self.advance_m <= 0:
            return None
        return self.advance_m / self.busy_s


class Cursor:
    """One connection's build-ahead: plan() lays the items out along the route (or heading) from the car, tick() says
    which to start now, started()/done() account them. follow: block_items' rule. pins: the planned anchors the mod
    knows (routepins; this cursor's planned ones are added as it sends them)."""

    def __init__(self, follow, road_jobs=ROAD_JOBS, block_jobs=BLOCK_JOBS, max_m=MAX_M, pins=(), plan_jobs=None):
        self.follow = follow
        self.jobs = {"roads": road_jobs, "block": block_jobs, "plans": PLAN_JOBS if plan_jobs is None else plan_jobs}
        self.max_m = float(max_m)
        self.pins = {(round(float(a[0]), 7), round(float(a[1]), 7)) for a in pins}
        self.route = None           # [(lat, lon), ...] the GPS route from the car, or None (the heading)
        self._line = None           # its resample (lats, lons, s)
        self.rebase_m = DEFAULT_DISTANCE
        self.pinned_rule = False    # the mod applies the pinned-anchor rule to the anchors we send (route "pins")
        self.items = []             # the plan, by due
        self.done = set()           # item ids built (or cached, or failed) this session
        self.active = {}            # id -> Item in flight
        self.stats = Stats()
        self.s_car = 0.0            # the car along the plan's line (m)
        self.plan_at = None         # (anchor, lat, lon, heading, s_car) the plan was made for
        self.planned = []           # anchors planned for the mod (frames' planned), in order
        self.anchors = []           # the plan's frames
        self.off_route = False
        self.last_tick = 0.0
        self.last_log = 0.0
        self.front_prev = None      # (time, ready front) at the last tick, for the rate
        self.speed = 0.0

    # --- the line ---------------------------------------------------------------------------------------------
    def set_route(self, points, pinned_rule=False, rebase_m=None):
        """A GPS route from the car ([[lat, lon], ...], None: none): planned again at the next tick."""
        self.route = [(float(p[0]), float(p[1])) for p in points] if points and len(points) >= 2 else None
        self._line = resample(self.route, STEP_M) if self.route else None     # (sampled once: locate() per tick)
        self.pinned_rule = bool(pinned_rule)
        if rebase_m:
            self.rebase_m = max(2000.0, min(16000.0, float(rebase_m)))
        self.plan_at = None
        self.items = []
        self.planned = []

    def plan(self, anchor, frame, x, y, vx, vy, block=None):
        """The items along the line from the car ((x, y) in frame of anchor (lat, lon), moving (vx, vy)): the route's
        from the car's place on it (within OFF_ROUTE_M), else the heading's. block: the car's terrain block (cx, cy)."""
        v = math.hypot(vx, vy)
        speed = max(v, SPEED_MIN)
        la, lo, _ = frame.enu_to_geodetic(x, y, 0.0)
        car = (float(la), float(lo))
        heading = math.atan2(vy, vx) if v > 1.0 else (self.plan_at[3] if self.plan_at else 0.0)
        line, s0, self.off_route = None, 0.0, False
        if self.route:
            la_s, lo_s, s_s = self._line
            e, n, _ = frame.geodetic_to_enu(la_s, lo_s, 0.0)
            d = np.hypot(np.asarray(e, float) - x, np.asarray(n, float) - y)
            i = int(np.argmin(d))
            if d[i] <= OFF_ROUTE_M + STEP_M / 2:
                i = max(0, i - 1)
                s0 = float(s_s[i])
                keep = (s_s >= s0) & (s_s <= s0 + self.max_m + REPLAN_M)
                line = (la_s[keep], lo_s[keep], s_s[keep])
            else:
                self.off_route = True
        if line is None:            # the heading: a straight line from the car
            ux, uy = (vx / v, vy / v) if v > 1.0 else (math.cos(heading), math.sin(heading))
            pts = heading_line(frame, x, y, ux, uy, min(HEADING_MAX_M, self.max_m))
            la_s, lo_s, s_s = resample(pts, STEP_M)
            line = (la_s, lo_s, s_s)
        la_s, lo_s, s_s = line
        anchors, idx, planned = frames(la_s, lo_s, anchor, self.rebase_m, self.pins | set(self.planned),
                                       self.pinned_rule)
        for a in planned:
            if a not in self.planned:
                self.planned.append(a)
        self.anchors = anchors
        items = []
        for ai, a in enumerate(anchors):
            sel = idx == ai
            if not np.any(sel):
                continue
            fr = geodesy.EnuFrame(a[0], a[1])
            e, n, _ = fr.geodetic_to_enu(la_s[sel], lo_s[sel], 0.0)
            xs, ys, ss = np.asarray(e, float), np.asarray(n, float), s_s[sel]
            if self.jobs.get("roads", 0) > 0:        # (no road processes: tiles are not cached, none ahead)
                items += road_items(a, xs, ys, ss)
            start = block if (ai == 0 and block is not None) else None
            items += block_items(a, xs, ys, ss, speed, self.follow, start)
            if self.jobs.get("plans", 0) > 0:
                items += plan_items(a, la_s[sel], lo_s[sel], ss)
        items.sort(key=lambda it: (it.due, {"plans": 0, "block": 1, "roads": 2}[it.kind]))
        ids = set()
        self.items = []
        for it in items:
            if it.id in ids:
                continue
            ids.add(it.id)
            self.items.append(it)
        self.s_car = s0
        self.speed = v
        self.plan_at = (anchor, car, (x, y), heading, s0)
        self.front_prev = None
        return self.items

    def stale(self, anchor, frame, x, y, vx, vy):
        """Whether plan() should run again for the car here: no plan, another anchor, a new route, the car off (or
        back on) the line, REPLAN_M further along it, or (heading) turned HEADING_DEG / moved HEADING_MOVE_M."""
        if self.plan_at is None or self.plan_at[0] != anchor:
            return True
        v = math.hypot(vx, vy)
        if self.route and not self.off_route:
            s = self.locate(frame, x, y)
            if s is None:
                return True
            return s - self.plan_at[4] >= REPLAN_M
        if self.route and self.off_route:        # back on the route?
            if self.locate(frame, x, y) is not None:
                return True
        px, py = self.plan_at[2]
        if math.hypot(x - px, y - py) >= HEADING_MOVE_M:
            return True
        if v > 1.0:
            turn = abs((math.atan2(vy, vx) - self.plan_at[3] + math.pi) % (2 * math.pi) - math.pi)
            if math.degrees(turn) >= HEADING_DEG:
                return True
        return False

    def locate(self, frame, x, y):
        """The car's distance along the route (m) when within OFF_ROUTE_M of it, else None."""
        if not self.route:
            return None
        la_s, lo_s, s_s = self._line
        e, n, _ = frame.geodetic_to_enu(la_s, lo_s, 0.0)
        d = np.hypot(np.asarray(e, float) - x, np.asarray(n, float) - y)
        i = int(np.argmin(d))
        return float(s_s[i]) if d[i] <= OFF_ROUTE_M + STEP_M / 2 else None

    def car_s(self, frame, x, y):
        """The car's distance along the plan's line: on the route, locate(); along the heading, its projection from the
        plan's origin; None when there is no plan."""
        if self.plan_at is None:
            return None
        if self.route and not self.off_route:
            return self.locate(frame, x, y)
        px, py = self.plan_at[2]
        h = self.plan_at[3]
        return max(0.0, (x - px) * math.cos(h) + (y - py) * math.sin(h))

    # --- the work ----------------------------------------------------------------------------------------------
    def horizon(self, speed=None):
        """How far ahead (m along the line) items are started for a car at speed (the plan's when None)."""
        v = max(self.speed if speed is None else speed, SPEED_MIN)
        return min(self.max_m, max(MIN_M, v * (LATENCY_K * self.stats.latency + MARGIN_S)))

    def front(self):
        """The ready front: m along the line up to which every item the car needs is done (the first undone block or
        tile's due; the plan's end when all are). Cell plans are a means, not a tier: they do not hold the front."""
        for it in self.items:
            if it.kind != "plans" and it.id not in self.done:
                return it.due
        return self.items[-1].due + STEP_M if self.items else self.s_car

    def ready_m(self):
        """m of line ready ahead of the car."""
        return max(0.0, self.front() - self.s_car)

    def tick(self, now, s_car=None, speed=None, skip=None):
        """The items to start now: the first undone, not in flight ones due within the horizon of the car (at s_car
        along the line; speed m/s), as many as the kinds' free jobs; skip(item) -> True for one the live path is on.
        Accounts the busy time and the front's advance for the rate."""
        if s_car is not None:
            self.s_car = float(s_car)
        if speed is not None:
            self.speed = float(speed)
        h = self.horizon()
        limit = self.s_car + h
        front = self.front()
        pending = any(it.id not in self.done and it.due <= limit for it in self.items)
        if self.front_prev is not None:
            t0, f0, was_busy = self.front_prev
            if was_busy and now > t0:
                self.stats.busy_s += now - t0
                self.stats.advance_m += max(0.0, front - f0)
        busy = pending
        self.front_prev = (now, front, busy)
        self.last_tick = now
        free = {k: n - sum(1 for it in self.active.values() if it.kind == k) for k, n in self.jobs.items()}
        out = []
        for it in self.items:
            if it.due > limit:
                break
            if it.id in self.done or it.id in self.active or free.get(it.kind, 0) <= 0:
                continue
            if skip is not None and skip(it):     # the live path has it (or is on it): nothing to do here
                self.done.add(it.id)
                self.stats.cached += 1
                continue
            free[it.kind] -= 1
            out.append(it)
        return out

    def started(self, item, now):
        item.queued = now
        self.active[item.id] = item

    def done_item(self, item, now, how):
        """how: "built" | "cached" | "empty" | "failed" (an exception: counted, not retried this plan)."""
        self.active.pop(item.id, None)
        self.done.add(item.id)
        if len(self.done) > DONE_MAX:
            self.done = {it.id for it in self.items if it.id in self.done}
        st = self.stats
        st.by_kind[item.kind] = st.by_kind.get(item.kind, 0) + 1
        if how == "built":
            st.built += 1
            if item.queued:
                st.latency += LATENCY_ALPHA * ((now - item.queued) - st.latency)
        elif how == "failed":
            st.failed += 1
        else:
            st.cached += 1

    def status(self):
        """One line for the log: what is ready ahead, the horizon, the measured latency and rate."""
        st = self.stats
        line = (f"{self.ready_m() / 1000:.1f} km ready of {self.horizon() / 1000:.1f} km ahead "
                f"({'off the route, ' if self.off_route else ''}{self.speed:.0f} m/s, {len(self.items)} items planned, "
                f"{len(self.active)} building, {st.built} built, {st.cached} cached, {st.failed} failed; "
                f"by kind {dict(sorted(st.by_kind.items()))}; latency {st.latency:.1f} s")
        r = st.rate
        if r is not None:
            line += f"; build rate {r * 60 / 1000:.2f} km/min = keeps up to {r * 2.23694:.0f} mph"
        return line

    def should_log(self, now):
        if now - self.last_log >= LOG_S:
            self.last_log = now
            return True
        return False
