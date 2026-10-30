#!/usr/bin/env python3
"""AI traffic on a motorway drive at several amounts (the I-25 traffic tuning, 2026-10-28): for each mode the traffic is
set up again at the start once the car is there (off: none; light / normal / busy: navgraph.lua modeAmount of the
game's own amount), the car is put on the start and drives itself to the goal (tools/autodrive_perf.py: fps, frame times, CPU), and every TRAFFIC_S the traffic is
sampled: active cars, how many near the player, off the road, their speeds (the pace spread), the AI graph's status
and the rebases. One mode may get a short rebase distance (--rebase-in) so a rebase happens during it.

  python tools/traffic_bench.py --from 40.090,-104.9806 --to 39.74,-104.99 --minutes 12 --pace --out bench

Writes <out>/<mode>/ (autodrive_perf's samples.jsonl, summary.json) plus traffic.jsonl, and <out>/report.json. The
game's MCP server must be on and the companion server running. Restores the rebase distance and leaves the traffic
at the last mode's amount.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mcp  # noqa: E402

TRAFFIC_S = 10.0
SKIP_S = 90.0               # (the teleport, the settle, the snap onto the road: autodrive_perf --from)
MODES = {"off": 0, "light": 0.5, "normal": 1, "busy": 1.5}      # (navgraph.lua MODES, modeAmount)
LIGHT_MIN, BUSY_MAX, BUSY_MORE = 3, 24, 4
FAST_MS, SPREAD, SPREAD_MAX = 20, 0.06, 0.12                   # (navgraph.lua paceLua)


def mode_amount(mode, auto):
    f = MODES[mode]
    if f <= 0:
        return 0
    if f == 1 or not auto or auto < 1:
        return -1
    n = int(auto * f + 0.5)
    if f < 1:
        return min(auto, max(LIGHT_MIN, n))
    return min(BUSY_MAX, max(auto + BUSY_MORE, n))


SETUP_LUA = r"""
if not gameplay_traffic then extensions.load('core_multiSpawn', 'gameplay_traffic', 'gameplay_walk') end
local gt = gameplay_traffic
local okW, nav0 = pcall(require, 'beamngEarth/navgraph')
if okW and nav0.unwatch then nav0.unwatch() end     -- (deleting the cars must not make the mod set them up again)
pcall(gt.deleteVehicles)
local ok, auto = pcall(gt.getIdealSpawnAmount, nil, true)
-- what the mod would set up for this mode (navgraph.plannedAmount: the CPU floor included), when it can say
local okN, nav = pcall(require, 'beamngEarth/navgraph')
local amount, own = nil, nil
if okN and nav.plannedAmount then
  local okP, n, a = pcall(nav.plannedAmount, '%s')
  if okP then amount, own = n, a end
end
return jsonEncode({ auto = ok and auto or nil, amount = amount, own = own })
"""

START_LUA = r"""
local gt = gameplay_traffic
local ok, err = pcall(gt.setupTraffic, %d, {})
return jsonEncode({ ok = ok, err = err and tostring(err) or nil })
"""

# each traffic car its pace (as navgraph.lua paceLua; for a game whose mod is older than that)
PACE_LUA = r"""
local code = [==[do
local f, fast = %%.4f, %.2f
local function scale(md)
  if not (md and md.graph) or md._bnePace == f then return end
  for a, links in pairs(md.graph) do
    for _, e in pairs(links) do
      if e.inNode == a and e.speedLimit then
        e._bneLimit = e._bneLimit or e.speedLimit
        if e._bneLimit >= fast then e.speedLimit = e._bneLimit * f end
      end
    end
  end
  md._bnePace = f
end
mapmgr._bnePace = scale
if not mapmgr._bneSetMap then
  mapmgr._bneSetMap = mapmgr.setMap
  mapmgr.setMap = function(...) local r = mapmgr._bneSetMap(...) pcall(mapmgr._bnePace, mapmgr.mapData) return r end
end
pcall(scale, mapmgr.mapData)
end]==]
local n, me = 0, be:getPlayerVehicleID(0)
for id in pairs(gameplay_traffic.getTrafficData() or {}) do
  local o = getObjectByID(id)
  if o and id ~= me then
    local z = (math.random() + math.random() + math.random() - 1.5) * 2
    local f = math.max(1 - %.3f, math.min(1 + %.3f, 1 + z * %.3f))
    o:queueLuaCommand(string.format(code, f))
    n = n + 1
  end
end
return tostring(n)
""" % (FAST_MS, SPREAD_MAX, SPREAD_MAX, SPREAD)

POLL_LUA = r"""
local gt = gameplay_traffic
local out = { state = gt and gt.getState() or 'none', active = 0, near = 0, offroad = 0, speeds = {} }
local me = be:getPlayerVehicleID(0)
local pv = getObjectByID(me)
local pp = pv and pv:getPosition()
if gt and gt.getState() == 'on' then
  for id in pairs(gt.getTrafficData() or {}) do
    local o = getObjectByID(id)
    if o and id ~= me and o:getActive() then
      out.active = out.active + 1
      local p = o:getPosition()
      out.speeds[#out.speeds + 1] = math.floor(o:getVelocity():length() * 2.23694 + 0.5)
      local a, b, d = map.findClosestRoad(p)
      if not a or (tonumber(d) and d > 8) then out.offroad = out.offroad + 1 end
      if pp and (p - pp):length() < 600 then out.near = out.near + 1 end
    end
  end
end
local okN, nav = pcall(function() return require('beamngEarth/navgraph').status() end)
out.nav = okN and nav or nil
local st = beamngEarth_core._state()
out.rebases, out.anchor = st.rebaseCount, tostring(st.anchorId)
out.mph = pv and math.floor(pv:getVelocity():length() * 2.23694 + 0.5) or nil
return jsonEncode(out)
"""


def lua(code):
    return mcp.call("run_lua", {"code": code})


def poller(path, stop):
    with open(path, "w") as f:
        while not stop.is_set():
            try:
                r = json.loads(lua(POLL_LUA))
                r["t"] = time.time()
                f.write(json.dumps(r) + "\n")
                f.flush()
            except (Exception, SystemExit) as e:
                print(f"traffic poll: {e}", file=sys.stderr)
            stop.wait(TRAFFIC_S)


def traffic_summary(path):
    rows = [json.loads(x) for x in open(path) if x.strip()]
    # the teleport and the settle are not the drive: the polls from SKIP_S on (and while the traffic runs, if it does)
    rows = [r for r in rows if r["t"] - rows[0]["t"] >= SKIP_S] if rows else rows
    if any(r.get("state") == "on" for r in rows):
        rows = [r for r in rows if r.get("state") == "on"]
    if not rows:
        return {}
    fast = [s for r in rows for s in r.get("speeds", []) if s >= 50]
    per_poll_sd = [statistics.pstdev([s for s in r["speeds"] if s >= 50]) for r in rows
                   if len([s for s in r.get("speeds", []) if s >= 50]) >= 3]
    nav = [r.get("nav") or {} for r in rows]
    return {
        "polls": len(rows),
        "active_avg": round(sum(r["active"] for r in rows) / len(rows), 1),
        "near_600m_avg": round(sum(r["near"] for r in rows) / len(rows), 1),
        "offroad_max": max(r["offroad"] for r in rows),
        "offroad_polls": sum(1 for r in rows if r["offroad"]),
        "fast_cars_mph": {"n": len(fast), "median": statistics.median(fast) if fast else None,
                          "min": min(fast) if fast else None, "max": max(fast) if fast else None},
        "spread_mph_per_poll": round(statistics.mean(per_poll_sd), 1) if per_poll_sd else None,
        "rebases": rows[-1]["rebases"] - rows[0]["rebases"],
        "nav_applies": (nav[-1].get("applies") or 0) - (nav[0].get("applies") or 0),
        "ahead_respawns": (nav[-1].get("aheadRespawns") or 0) - (nav[0].get("aheadRespawns") or 0),
        "cut_respawns": (nav[-1].get("cutRespawns") or 0) - (nav[0].get("cutRespawns") or 0),
        # (navgraph.lua keepRouteLua: each car's route kept or dropped at a new map)
        "routes_kept": (nav[-1].get("routesKept") or 0) - (nav[0].get("routesKept") or 0),
        "routes_dropped": (nav[-1].get("routesDropped") or 0) - (nav[0].get("routesDropped") or 0),
        # (mergeassist.lua: ramp cars given a speed cap to merge into a gap)
        "merge_assists": (nav[-1].get("mergeAssists") or 0) - (nav[0].get("mergeAssists") or 0),
        # (population.lua, --population: ghosts made real, real made ghosts, ghosts come back, spare ghosts)
        "pop": {k: ((nav[-1].get("population") or {}).get(k) or 0) - ((nav[0].get("population") or {}).get(k) or 0)
                for k in ("promoted", "demoted", "recycled", "spares", "pushes")},
        "pop_ghosts": (nav[-1].get("population") or {}).get("ghosts"),
        "nav_apply_ms_last": nav[-1].get("applyMs"),
        "guard_max_ms": max((n.get("guardMaxMs") or 0) for n in nav),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--settle", type=float, default=45.0)
    ap.add_argument("--modes", default="off,normal,busy")
    ap.add_argument("--pace", action="store_true", help="give each traffic car its pace (navgraph.lua paceLua)")
    ap.add_argument("--population", action="store_true",
                    help="the persistent traffic population (population.lua) instead of recycling the cars ahead")
    ap.add_argument("--rebase-in", default="", help="the mode during which the rebase distance is short (a distance "
                    "shorter than the route pins' spacing rebased ~390 times in 5 min, bench2: better a drive long "
                    "enough for the 15 km one)")
    ap.add_argument("--rebase-m", type=float, default=4000.0)
    ap.add_argument("--snap-kmh", type=float, default=90.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    report = {}
    old_rebase = lua("return tostring(require('beamngEarth/rebase').distance)").strip()
    try:
        for mode in a.modes.split(","):
            d = os.path.join(a.out, mode)
            os.makedirs(d, exist_ok=True)
            setup = json.loads(lua(SETUP_LUA % mode))
            auto = setup.get("own") or setup.get("auto")
            # (the mod's own plan when it has one: the amount a player gets; else the game's pick as before)
            n = setup["amount"] if setup.get("amount") is not None else mode_amount(mode, auto)
            if mode == a.rebase_in:
                lua("require('beamngEarth/rebase').setDistance(%r) return 1" % a.rebase_m)
            stop = threading.Event()
            aid0 = lua("return tostring(beamngEarth_core._state().anchorId)").strip()

            def traffic_later():
                # set up at the start, not before the teleport (bench2: set up at the old place, the cars stayed
                # 'spawning' there or went 'off' at the teleport, and normal and busy drove with none): once the car
                # has arrived (a new anchor, as autodrive_perf) and the new place's graph is in (most of the settle)
                t_end = time.time() + 300
                while not stop.is_set() and time.time() < t_end:
                    try:
                        if lua("return tostring(beamngEarth_core._state().anchorId)").strip() != aid0:
                            break
                    except (Exception, SystemExit):
                        pass
                    stop.wait(2)
                if stop.wait(max(5.0, a.settle - 15)):
                    return
                print(mode, "traffic:", n, "(game's own:", auto, ")", lua(START_LUA % n), flush=True)
                for _ in range(45):
                    if stop.wait(2):
                        return
                    try:
                        if lua("return gameplay_traffic.getState()").strip() == "on":
                            break
                    except (Exception, SystemExit):
                        pass
                print(mode, "traffic state:", lua("return gameplay_traffic.getState()").strip(), flush=True)
                if a.population:
                    print(mode, "population:", lua("return tostring(require('beamngEarth/navgraph').setPopulation(true))"),
                          flush=True)
                if a.pace and not stop.wait(20):
                    print("paced", lua(PACE_LUA), "cars", flush=True)
            th = threading.Thread(target=poller, args=(os.path.join(d, "traffic.jsonl"), stop), daemon=True)
            th.start()
            if n:
                threading.Thread(target=traffic_later, daemon=True).start()
            subprocess.run([sys.executable, os.path.join(HERE, "autodrive_perf.py"), "--from", a.start, "--to", a.to,
                            "--minutes", str(a.minutes), "--settle", str(a.settle), "--snap-kmh", str(a.snap_kmh),
                            "--keep-going", "--out", d], check=False)
            stop.set()
            th.join(15)
            if mode == a.rebase_in:
                lua("require('beamngEarth/rebase').setDistance(%s) return 1" % old_rebase)
            s = {"amount": n, "game_auto": auto, "traffic": traffic_summary(os.path.join(d, "traffic.jsonl"))}
            try:
                s["drive"] = json.load(open(os.path.join(d, "summary.json")))
            except (OSError, ValueError):
                s["drive"] = None
            report[mode] = s
            print(mode, json.dumps(s["traffic"]), flush=True)
    finally:
        lua("require('beamngEarth/rebase').setDistance(%s) return 1" % old_rebase)
        with open(os.path.join(a.out, "report.json"), "w") as f:
            json.dump(report, f, indent=1)
    keep = ("fps", "frame_ms", "frames_over", "cpu_cores", "avg_mph_moving", "events")
    print(json.dumps({m: {"amount": r["amount"], "traffic": r["traffic"],
                          "drive": {k: (r["drive"] or {}).get(k) for k in keep}} for m, r in report.items()}, indent=1))


if __name__ == "__main__":
    main()
