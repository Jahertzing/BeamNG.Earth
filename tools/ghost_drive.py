#!/usr/bin/env python3
"""The bake's drive without a driver: the player's car set along the route's road line step by step (no AI, no
traffic to meet, no turn to miss), moving on only once the world around it has loaded. The autodrive bake stopped at
three crash spots (the PC, 2026-10-28: a left turn in Frederick the AI ran wide over a kerb, traffic boxing it in at the
railway on I-25, a guide sign at 39.9884,-104.9873); the builder: "is there a way to bake without the autodrive
(teleport along the anchors and wait for the backlog)?" This is that.

  python3 tools/ghost_drive.py --routes-file <world>/bake/routes.json --route downtown --out DIR [--reverse]

Each tick (TICK_S) one MCP call (the chunk GHOST_LUA, global BNGE_GHOST): the route's point at distance s and one
HEAD_M further, through the mod's current anchor (beamngEarth_core._state().anchor, so the rebases, pinned to the
planned anchors by routepins.lua, happen as on a drive), the car set on the surface there (a ray down from just over
the car: on a bridge it stays on the deck, under one it stays under) with the velocity of the drive along the road (the
mod's lookahead and the server's prefetch read the car's velocity: the same blocks and tiles as a drive at that
speed). The car is moved without turning it (the mod's own setCarAt: the cluster by its reference node), and re-set
with spawn.safeTeleport, no reset, facing the road (core.lua and edgeguard.lua's call) when the road turned TURN_DEG
from it or it is not upright. Steps of a few tens of metres: never the drop hold's jump (drophold.lua JUMP_M).

It moves on (s += speed * TICK_S) only when the place is loaded: no anchor request pending, ground under the next
point, the mod's road / sidewalk / building meshes to make at most --roads-q, the server's backlog at most
--backlog-q, the road tiles ready --lead-m ahead along the road (roads.ahead); meanwhile the car stands at s, velocity 0.
After --max-wait s standing it moves on anyway (said in the summary: a place that never loads is a finding, not a stop).
Writes <out>/samples.jsonl (a line per tick) and <out>/summary.json; prints the summary. Exit 0 at the route's end.
Needs the game with the MCP server on and the companion server running, like autodrive_perf.py.
"""
import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import autodrive_perf as ad  # noqa: E402  (parent_gone, hitch causes)
import mcp  # noqa: E402

TICK_S = 0.5            # s between steps
HEAD_M = 30.0           # m: the heading from the point this far on along the route
SPEED = 27.0            # m/s (~60 mph): the bake's speed
ROADS_Q = 8             # the mod's road / sidewalk / building meshes still to make (roads.status().walksQueued)
BACKLOG_Q = 4           # the server's backlog (core's serverBacklog)
LEAD_M = 400.0          # m of road tiles ready ahead
MAX_WAIT_S = 120.0      # s standing at one place, then on anyway
ARRIVE_S = 60.0         # s at most for the start's teleport to commit (a new anchor)
MAX_FAILS = 120         # steps failed in a row (the game gone): the drive stops
EARTH_R = 6371008.8

GHOST_LUA = r"""
local G = {}
_G.BNGE_GHOST = G
G.TURN_COS = math.cos(math.rad(%(turn)r))
G.UP_MIN = 0.8          -- the car's up vector this upright at least, else it is set again
G.LIFT = 0.5            -- m over the surface the car is set at
local function ext(n) return rawget(_G, n) end

-- the load state: anchor, pending request, server backlog, far field and road meshes, the frame times
local function state(st, perf)
  local out = { anchor = st.anchorId, pending = st.pending and (st.pending.purpose or true) or nil,
    backlog = st.serverBacklog }
  local okF, ff = pcall(require, 'beamngEarth/farfield')
  if okF and type(ff) == 'table' and ff.stats then
    local ok, s = pcall(ff.stats)
    if ok and s then out.far = { queued = s.queued, groups = s.groups, done = s.done } end
  end
  local roads = package.loaded['beamngEarth/roads']
  if roads and roads.status then
    local ok, s = pcall(roads.status)
    if ok and s then out.roads = { q = s.walksQueued, queued = s.queued, tiles = s.tiles, quietS = s.quietS } end
  end
  if perf then
    local pm = ext('beamngEarth_perfmon')
    if not pm then extensions.load('beamngEarth_perfmon'); pm = ext('beamngEarth_perfmon') end
    if pm then
      if pm.tagHarness then pm.tagHarness() end
      local p = pm.snapshot() or {}
      out.perf = { frames = p.frames, avgMs = p.avgMs, p99 = p.p99, maxMs = p.maxMs, over50 = p.over50,
        over100 = p.over100 }
    end
    local core = ext('beamngEarth_core')
    out.hitches = core and core.hitches and core.hitches() or nil
    local okL, lim = pcall(function() return Engine.getCurrentFpsLimitReason() end)
    if okL and type(lim) == 'table' then
      out.limit = { focused = lim.windowFocused, vsync = lim.vsync, fpsCap = lim.fpsCap,
        fg = lim.foregroundActive and lim.foregroundCap or nil, bg = lim.backgroundActive and lim.backgroundCap or nil }
    end
  end
  return out
end

-- cfg {lat, lon, hlat, hlon, v (m/s along the road), perf}: the car at (lat, lon) facing (hlat, hlon); no lat: the
-- state alone
function G.step(cfg)
  cfg = type(cfg) == 'string' and jsonDecode(cfg) or (cfg or {})
  local core = ext('beamngEarth_core')
  if not core or not core._state then return jsonEncode({ err = 'the mod is not running' }) end
  local st = core._state()
  local out = state(st, cfg.perf)
  local veh = getPlayerVehicle(0)
  if not veh then out.err = 'no player vehicle'; return jsonEncode(out) end
  if out.pending or not cfg.lat or not st.anchor then return jsonEncode(out) end
  local a = st.anchor
  local x, y = a:geodeticToEnu(cfg.lat, cfg.lon, 0)
  local hx, hy = a:geodeticToEnu(cfg.hlat, cfg.hlon, 0)
  local p = veh:getPosition()
  local z = be:getSurfaceHeightBelow(vec3(x, y, p.z + 4))
  if not z or z < -1e6 then z = be:getSurfaceHeightBelow(vec3(x, y, p.z + 60)) end   -- (a climb)
  if not z or z < -1e6 then out.noground = true; return jsonEncode(out) end
  local dx, dy = hx - x, hy - y
  local L = math.sqrt(dx * dx + dy * dy)
  if L < 0.01 then dx, dy, L = 0, 1, 1 end
  local dir = vec3(dx / L, dy / L, 0)
  local fwd, up = veh:getDirectionVector(), veh:getDirectionVectorUp()
  local f2 = math.sqrt(fwd.x * fwd.x + fwd.y * fwd.y)
  local cosd = f2 > 1e-3 and (fwd.x * dir.x + fwd.y * dir.y) / f2 or -1
  local ref = veh:getRefNodeId()
  if cfg.reset or cosd < G.TURN_COS or up.z < G.UP_MIN then
    spawn.safeTeleport(veh, vec3(x, y, z + G.LIFT), quatFromDir(dir, vec3(0, 0, 1)), nil, nil, false, nil, false)
    out.turned = true
  else
    local rel = veh:getNodePosition(ref)
    veh:setClusterPosRelRot(ref, x + rel.x, y + rel.y, z + G.LIFT + rel.z, 0, 0, 0, 1)
  end
  local v = tonumber(cfg.v) or 0
  veh:applyClusterVelocityScaleAdd(ref, 0, dir.x * v, dir.y * v, 0)
  out.x, out.y, out.z = x, y, z
  local roads = package.loaded['beamngEarth/roads']
  if roads and roads.ahead then        -- how far along the road its tiles are in (autodrive_perf's lead)
    local ok, list = pcall(roads.ahead, x, y, dir.x, dir.y, cfg.leadM or 1000)
    if ok and list then
      local road = nil
      for _, e in ipairs(list) do
        if not road and not (e.arrived and e.road) then road = e.d end
      end
      out.lead = road or (cfg.leadM or 1000)
    end
  end
  return jsonEncode(out)
end
return 'ok'
"""


# --- the route (pure) -----------------------------------------------------------------------------------------------
class Route:
    """A road line [(lat, lon)] measured along: point(s), the (lat, lon) s metres from its start."""

    def __init__(self, points):
        pts = [tuple(map(float, p)) for p in points]
        self.pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
        if len(self.pts) < 2:
            raise ValueError("a route needs two points at least")
        self.cum = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
            self.cum.append(self.cum[-1] + metres(a, b))
        self.length = self.cum[-1]

    def point(self, s):
        s = min(max(s, 0.0), self.length)
        lo, hi = 0, len(self.cum) - 1          # the segment holding s
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= s:
                lo = mid
            else:
                hi = mid
        seg = self.cum[hi] - self.cum[lo]
        t = (s - self.cum[lo]) / seg if seg > 0 else 0.0
        a, b = self.pts[lo], self.pts[hi]
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t

    def ahead(self, s, d=HEAD_M):
        """The point the car at s faces: d further along; in the last d metres, as far past the end as d back."""
        if s + d <= self.length:
            return self.point(s + d)
        lat, lon = self.point(s)
        plat, plon = self.point(s - d)
        return lat + (lat - plat), lon + (lon - plon)


def metres(a, b):
    """The distance (m) between (lat, lon) a and b (equirectangular: steps of a route line)."""
    la = math.radians((a[0] + b[0]) / 2)
    dn = math.radians(b[0] - a[0]) * EARTH_R
    de = math.radians(b[1] - a[1]) * EARTH_R * math.cos(la)
    return math.hypot(dn, de)


def load_route(path, name, reverse=False):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if name not in data:
        raise SystemExit(f"{path}: no route {name!r} (has {', '.join(data)})")
    pts = data[name]
    return Route(pts[::-1] if reverse else pts)


def waiting_for(r, a):
    """Why the car does not move on yet (empty: it does). r = BNGE_GHOST.step's answer, a = the options."""
    why = []
    if r.get("err"):
        why.append(r["err"])
    if r.get("pending"):
        why.append(f"anchor request pending ({r['pending']})")
    if r.get("noground"):
        why.append("no ground at the next point")
    q = (r.get("roads") or {}).get("q") or 0
    if q > a.roads_q:
        why.append(f"{q} road meshes to make")
    if (r.get("backlog") or 0) > a.backlog_q:
        why.append(f"server backlog {r['backlog']}")
    if r.get("lead") is not None and r["lead"] < a.lead_m:
        why.append(f"road tiles ready {r['lead']:.0f} m ahead")
    return why


def summarize(rows, waits, route_m, s_end):
    """The drive's numbers: time, distance, frames (weighted by their count), the waits."""
    perf = [r["perf"] for r in rows if r.get("perf") and r["perf"].get("frames")]
    frames = sum(p["frames"] for p in perf)
    ms = sum((p.get("avgMs") or 0) * p["frames"] for p in perf)
    lims = [r["limit"] for r in rows if r.get("limit")]
    out = {
        "seconds": round(rows[-1]["t"] - rows[0]["t"], 1) if len(rows) > 1 else 0,
        "km": round(s_end / 1000, 2), "route_km": round(route_m / 1000, 2),
        "complete": s_end >= route_m - 1,
        "frames": frames, "fps": round(1000 * frames / ms, 1) if ms else None,
        "frame_ms": {"p99_worst_tick": max((p.get("p99") or 0) for p in perf) if perf else None,
                     "max": max((p.get("maxMs") or 0) for p in perf) if perf else None},
        "frames_over": {k: sum(p.get(k) or 0 for p in perf) for k in ("over50", "over100")},
        "hitch_causes": ad._causes(rows),
        "rebases": sum(1 for a, b in zip(rows, rows[1:]) if a.get("anchor") != b.get("anchor")),
        "turned": sum(1 for r in rows if r.get("turned")),
        "waits": {"count": len(waits), "seconds": round(sum(w["s"] for w in waits), 1),
                  "gave_up": sum(1 for w in waits if w.get("gave_up")),
                  "longest": sorted(waits, key=lambda w: -w["s"])[:10]},
        # the game's frame cap (a drive at 59.7 fps against 88-90 in the benches, the PC 2026-10-28)
        "fps_limit": lims[-1] if lims else None,
        "unfocused_ticks": sum(1 for x in lims if x.get("focused") is False),
    }
    return out


# --- the drive ------------------------------------------------------------------------------------------------------
def lua(code):
    return mcp.call("run_lua", {"code": code})


def step(cfg):
    return json.loads(lua("return BNGE_GHOST.step(%s)" % lua_long(json.dumps(cfg))))


def lua_long(text):
    n = 0
    while ("]" + "=" * n + "]") in text:
        n += 1
    return "[" + "=" * n + "[" + text + "]" + "=" * n + "]"


def teleport_to(lat, lon, log):
    """The mod's teleport to the start (not in the recent places); waits for the new anchor."""
    before = lua("return tostring(beamngEarth_core._state().anchorId)").strip()
    r = lua("return tostring(beamngEarth_core.teleport(%r, %r, 'ghost_drive start', {record = false}))" % (lat, lon))
    log(f"teleport to {lat:.5f},{lon:.5f}: {r.strip()}")
    end = time.time() + ARRIVE_S
    while time.time() < end:
        try:
            if lua("return tostring(beamngEarth_core._state().anchorId)").strip() != before:
                return True
        except (Exception, SystemExit):
            pass
        time.sleep(1)
    log(f"the teleport did not commit within {ARRIVE_S:.0f} s: on from where the car is")
    return False


def drive(a, route, log=print):
    os.makedirs(a.out, exist_ok=True)
    mcp.quiet_ui()
    if lua(GHOST_LUA % {"turn": a.turn_deg}).strip() != "ok":
        raise SystemExit("could not load the ghost drive's chunk")
    lua("input.event('throttle', 0, 2) input.event('brake', 0, 2) input.event('steering', 0, 2) return 1")
    lat0, lon0 = route.point(a.from_km * 1000)
    if not a.no_teleport:
        teleport_to(lat0, lon0, log)
    gone = ad.parent_gone() if a.parent_pipe else None
    s, rows, waits = a.from_km * 1000, [], []
    wait = None                 # {"s", "since", "why"} while standing
    reset, t0, n, fails = True, time.time(), 0, 0
    lat, lon, hlat, hlon = lat0, lon0, *route.ahead(s)
    stop_at = route.length if not a.to_km else min(route.length, a.to_km * 1000)
    rc = 0
    with open(os.path.join(a.out, "samples.jsonl"), "w") as f:
        while True:
            tick = time.time()
            if gone is not None and gone.is_set():
                log("the calling tool is gone: stopping")
                rc = 3
                break
            lat, lon = route.point(s)
            hlat, hlon = route.ahead(s)
            moving = wait is None and s < stop_at
            try:
                r = step({"lat": lat, "lon": lon, "hlat": hlat, "hlon": hlon, "v": a.speed if moving else 0,
                          "perf": n % max(1, round(1 / TICK_S)) == 0, "reset": reset, "leadM": a.lead_m + 200})
            except (Exception, SystemExit) as e:      # a busy frame, a reloaded GE: the next tick (the chunk again)
                log(f"step: {e}")
                fails += 1
                if fails >= MAX_FAILS:
                    log(f"{fails} steps in a row failed: the game is gone? stopping")
                    rc = 2
                    break
                if "BNGE_GHOST" in str(e):
                    try:
                        lua(GHOST_LUA % {"turn": a.turn_deg})
                    except (Exception, SystemExit):
                        pass
                time.sleep(TICK_S)
                continue
            reset, fails = False, 0
            n += 1
            r.update({"t": round(tick - t0, 2), "s": round(s, 1), "lat": round(lat, 6), "lon": round(lon, 6)})
            why = waiting_for(r, a)
            r["why"] = why or None
            rows.append(r)
            f.write(json.dumps(r) + "\n")
            f.flush()
            if why:
                if wait is None:
                    wait = {"s": s, "since": tick, "why": why}
                elif tick - wait["since"] > a.max_wait:
                    waits.append(_wait_row(wait, tick, why, gave_up=True))
                    log(f"km {s / 1000:.2f}: on after {a.max_wait:.0f} s waiting for {'; '.join(why)}")
                    wait = None
                    if s >= stop_at:
                        break
                    reset = bool(r.get("noground"))
                    s = min(stop_at, s + a.speed * TICK_S)
            else:
                if wait is not None:
                    w = _wait_row(wait, tick, wait["why"])
                    if w["s"] >= 2:
                        waits.append(w)
                    wait = None
                if s >= stop_at:
                    break
                s = min(stop_at, s + a.speed * TICK_S)
            if n % 120 == 0:
                log(f"km {s / 1000:.1f} of {stop_at / 1000:.1f}, anchor {r.get('anchor')}, "
                    f"{len(waits)} waits {sum(w['s'] for w in waits):.0f} s")
            time.sleep(max(0.0, TICK_S - (time.time() - tick)))
    try:                                        # parked where it ended
        step({"lat": lat, "lon": lon, "hlat": hlat, "hlon": hlon, "v": 0})
        lua("input.event('parkingbrake', 1, 2) return 1")
    except (Exception, SystemExit):
        pass
    summary = summarize(rows, waits, stop_at - a.from_km * 1000, s - a.from_km * 1000) if rows else {"ticks": 0}
    summary["route"], summary["reverse"] = a.route, a.reverse
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    log(json.dumps(summary, indent=1))
    if rc == 0 and not summary.get("complete"):
        rc = 1
    return rc


def _wait_row(wait, now, why, gave_up=False):
    w = {"km": round(wait["s"] / 1000, 2), "s": round(now - wait["since"], 1), "why": why}
    if gave_up:
        w["gave_up"] = True
    return w


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--routes-file", required=True, help="route_bake.py plan's routes.json (<world>/bake/routes.json)")
    ap.add_argument("--route", required=True, help="its route (downtown, i25)")
    ap.add_argument("--reverse", action="store_true", help="from its end to its start")
    ap.add_argument("--out", required=True)
    ap.add_argument("--speed", type=float, default=SPEED, help="m/s along the road while moving")
    ap.add_argument("--from-km", type=float, default=0.0, help="start this far along the route")
    ap.add_argument("--to-km", type=float, default=0.0, help="stop this far along it (0: its end)")
    ap.add_argument("--no-teleport", action="store_true", help="no teleport to the start (the car is there)")
    ap.add_argument("--roads-q", type=int, default=ROADS_Q)
    ap.add_argument("--backlog-q", type=int, default=BACKLOG_Q)
    ap.add_argument("--lead-m", type=float, default=LEAD_M)
    ap.add_argument("--max-wait", type=float, default=MAX_WAIT_S)
    ap.add_argument("--turn-deg", type=float, default=20.0, help="the car turned to the road past this angle")
    ap.add_argument("--parent-pipe", action="store_true",
                    help="stop when standard input ends (a calling tool gone: route_bake.py)")
    a = ap.parse_args(argv)
    route = load_route(a.routes_file, a.route, a.reverse)
    print(f"{a.route}{' back' if a.reverse else ''}: {route.length / 1000:.1f} km at {a.speed:.0f} m/s", flush=True)
    return drive(a, route, log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    sys.exit(main())
