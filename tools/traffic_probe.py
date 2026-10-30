#!/usr/bin/env python3
"""A read-only look at every AI traffic car in the running game (the traffic fixes of 2026-10-28): speed, distance to
the player, the road under it (ramp, the private ring past the block, its speed), the merge assist's cap, and from the
car's own Lua whether the keep-route wrapper is in and what it would see (ai.lua's mode, its route, its map vs the
car's current one). Changes nothing in the game.

  python tools/traffic_probe.py            # one table
  python tools/traffic_probe.py --json     # the raw rows
  python tools/traffic_probe.py --watch 2 --minutes 5 --out probe.jsonl
      # a row per car every 2 s, then the moments that looked wrong by kind and road: hard braking at speed,
      # wheelspin, sideways on its road, rolling backwards, blocked (the stock reverse trigger), crash manoeuvre

Needs the game's MCP server (tools/mcp.py).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp  # noqa: E402

# GE: ask each traffic car for its side (answers land in BNGE_PROBE next frames)
ASK = r"""
BNGE_PROBE = {}
local tr = gameplay_traffic
if not (tr and tr.getTrafficData) then return 'no traffic' end
local car = [==[
local r = { wrap = mapmgr._bneKeepSetMap ~= nil, pace = mapmgr._bneSetMap ~= nil, mode = ai and ai.mode or 'none',
  hasDebug = debug ~= nil and debug.getupvalue ~= nil }
local f = ai and ai.updateGFX
if type(f) == 'function' and r.hasDebug then
  local im, ir
  for i = 1, 255 do
    local n = debug.getupvalue(f, i)
    if not n then break end
    if n == 'mapData' then im = i elseif n == 'currentRoute' then ir = i end
  end
  r.im, r.ir = im, ir
  -- what the stock AI is doing: its last pedals, junction and blocked state, its crash manoeuvre
  for i = 1, 255 do
    local n, v = debug.getupvalue(f, i)
    if not n then break end
    if n == 'lastCommand' and type(v) == 'table' then
      r.throttle, r.brake, r.steer = v.throttle, v.brake, v.steering
    elseif n == 'trafficStates' and type(v) == 'table' then
      local tsi = v.intersection or {}
      if tsi.node then r.junction = { action = tsi.action, waiting = tsi.waiting and true or false } end
      r.blockedS = v.block and v.block.timer or nil
    elseif n == 'internalState' and type(v) == 'table' then
      r.road = v.road
      r.crash = v.crash and v.crash.maneuver or nil
    end
  end
  if im then local _, m = debug.getupvalue(f, im) r.aiMapSame = m == mapmgr.mapData r.aiMapNil = m == nil end
  if ir then
    local _, route = debug.getupvalue(f, ir)
    r.route = route ~= nil
    if route and route.path then
      r.pathLen = #route.path
      r.planIdx = route.plan and route.plan[1] and route.plan[1].pathidx or nil
      r.targetMph = route.plan and route.plan.targetSpeed and route.plan.targetSpeed * 2.23694 or nil
    end
  end
end
obj:queueGameEngineLua(string.format('BNGE_PROBE[%d] = %q', objectId, jsonEncode(r)))
]==]
local n = 0
for id in pairs(tr.getTrafficData() or {}) do
  local o = getObjectByID(id)
  if o then o:queueLuaCommand(car) n = n + 1 end
end
return tostring(n)
"""

# GE: the cars from the game's side, joined with their answers
READ = r"""
local tr = gameplay_traffic
local me = be:getPlayerVehicleID(0)
local pv = me and me >= 0 and getObjectByID(me) or nil
local pp = pv and pv:getPosition() or nil
local nav = require('beamngEarth/navgraph')
local nodes = nav.graphNodes and nav.graphNodes() or nil
local rows = {}
for id in pairs(tr and tr.getTrafficData and tr.getTrafficData() or {}) do
  local o = getObjectByID(id)
  if o then
    local p, u = o:getPosition(), o:getVelocity()
    local sp = math.sqrt(u.x * u.x + u.y * u.y)
    local row = { id = id, mph = sp * 2.23694, active = o:getActive(), dist = pp and (p - pp):length() or nil,
                  x = p.x, y = p.y }
    local dir = sp > 1 and vec3(u.x / sp, u.y / sp, 0) or o:getDirectionVector()
    local fwd = o:getDirectionVector()
    local fl = math.sqrt(fwd.x * fwd.x + fwd.y * fwd.y)
    if sp > 1 and fl > 1e-6 then row.backward = (u.x * fwd.x + u.y * fwd.y) / (sp * fl) < -0.5 end
    local n1, n2 = map.findBestRoad(p, dir)
    if n1 and n2 and nodes and nodes[n1] then
      local e = nodes[n1].links[n2] or (nodes[n2] and nodes[n2].links[n1])
      if e then
        row.ramp, row.private, row.limitMph = e.ramp or false, e.type == 'private', (e.speedLimit or 0) * 2.23694
        row.oneWay, row.drivability = e.oneWay, e.drivability
        -- the car's nose against its road's line (90: across the road)
        local na, nb = nodes[e.inNode or n1] or nodes[n1], nodes[e.outNode or n2] or nodes[n2]
        local ex, ey = nb.pos.x - na.pos.x, nb.pos.y - na.pos.y
        local el = math.sqrt(ex * ex + ey * ey)
        if el > 1e-6 and fl > 1e-6 then
          row.angle = math.deg(math.acos(math.min(1, math.abs((ex * fwd.x + ey * fwd.y) / (el * fl)))))
        end
      end
    end
    local ans = BNGE_PROBE and BNGE_PROBE[id]
    if ans then local ok, t = pcall(jsonDecode, ans) row.car = ok and t or ans end
    rows[#rows + 1] = row
  end
end
return jsonEncode({ rows = rows, status = nav.status() })
"""


def run(lua):
    return mcp.call("run_lua", {"code": lua})


def flags(r):
    """What looks wrong in a car's row: brake (full brake above 10 mph), spin (throttle on, not moving), sideways (nose
    60+ degrees off its road while moving), reverse, stuck (blocked 4 s+), crash (its crash manoeuvre), giveway."""
    c = r.get("car") if isinstance(r.get("car"), dict) else {}
    out = []
    if (c.get("brake") or 0) > 0.8 and r["mph"] > 10:
        out.append("brake")
    if (c.get("throttle") or 0) > 0.6 and r["mph"] < 3:
        out.append("spin")
    if (r.get("angle") or 0) > 60 and r["mph"] > 2:
        out.append("sideways")
    if r.get("backward"):
        out.append("reverse")
    if (c.get("blockedS") or 0) > 4:
        out.append("stuck")
    if c.get("crash"):
        out.append("crash")
    if (c.get("junction") or {}).get("waiting"):
        out.append("giveway")
    return out


# the population's list of sudden stops (population.lua WATCH_STOPS)
STOPS = "return jsonEncode(require('beamngEarth/population').state.stops or {})"


def snapshot():
    run(ASK)
    time.sleep(0.5)
    return json.loads(run(READ))


def road_of(r):
    if "drivability" not in r:
        return "off graph"
    return "ramp" if r.get("ramp") else ("ring" if r.get("private") else f"d{r.get('drivability')}")


def watch(every, minutes, path):
    """A row per car every `every` s for `minutes` into path (JSONL); then the flagged moments by kind and road."""
    import collections
    end = time.time() + minutes * 60
    counts, where = collections.Counter(), collections.Counter()
    n = 0
    run("require('beamngEarth/population').WATCH_STOPS = true")
    with open(path, "a", encoding="utf-8") as f:
        while time.time() < end:
            t0 = time.time()
            try:
                out = snapshot()
            except Exception as e:      # (the game busy for a moment: the next one)
                print(f"probe failed: {type(e).__name__}: {e}")
                time.sleep(every)
                continue
            for r in out.get("rows", []):
                r["t"], r["flags"] = t0, flags(r)
                f.write(json.dumps(r) + chr(10))
                n += 1
                for k in r["flags"]:
                    counts[k] += 1
                    where[(k, road_of(r))] += 1
            time.sleep(max(0.0, every - (time.time() - t0)))
    print(f"{n} car rows in {path}")
    stops = json.loads(run(STOPS))
    with open(path + ".stops.json", "w", encoding="utf-8") as f:
        json.dump(stops, f, indent=1)
    print(f"{len(stops)} sudden stops in {path}.stops.json")
    for k, v in counts.most_common():
        print(f"  {k:9} {v:5}  " + ", ".join(f"{road} {c}" for (kk, road), c in where.most_common() if kk == k))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--watch", type=float, help="seconds between snapshots: watch instead of one table")
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--out", default="traffic_probe.jsonl")
    a = ap.parse_args(argv)
    if a.watch:
        watch(a.watch, a.minutes, a.out)
        return
    n = run(ASK)
    time.sleep(1.0)
    out = json.loads(run(READ))
    if a.json:
        print(json.dumps(out, indent=1))
        return
    st = out.get("status") or {}
    print(f"cars asked: {n}; navgraph: applies {st.get('applies')}, routes kept {st.get('routesKept')} "
          f"dropped {st.get('routesDropped')} {st.get('routeDrops')}, merging {st.get('merging')}, "
          f"cut/ahead respawns {st.get('cutRespawns')}/{st.get('aheadRespawns')}")
    print(f"{'id':>6} {'mph':>5} {'dist':>6} {'road':>16} {'wrap':>5} {'mode':>8} {'up':>5} {'route':>5} "
          f"{'same':>5} {'path':>6} {'tgt':>4} {'thr':>4} {'brk':>4} {'ang':>4} flags")
    for r in sorted(out.get("rows", []), key=lambda r: r.get("dist") or 0):
        road = "off graph" if "limitMph" not in r else \
            f"{'ramp ' if r.get('ramp') else ''}{'ring ' if r.get('private') else ''}{r['limitMph']:.0f}mph d{r.get('drivability')}"
        c = r.get("car") if isinstance(r.get("car"), dict) else {}
        up = f"{c.get('im')}/{c.get('ir')}" if c else "-"
        print(f"{r['id']:>6} {r['mph']:5.1f} {r.get('dist') or 0:6.0f} {road:>16} {str(c.get('wrap', '-')):>5} "
              f"{str(c.get('mode', '-')):>8} {up:>5} {str(c.get('route', '-')):>5} {str(c.get('aiMapSame', '-')):>5} "
              f"{str(c.get('pathLen', '-')) + '@' + str(c.get('planIdx', '-')):>6} "
              f"{(c.get('targetMph') or 0):4.0f} {(c.get('throttle') or 0):4.1f} {(c.get('brake') or 0):4.1f} "
              f"{(r.get('angle') or 0):4.0f} {' '.join(flags(r))}")


if __name__ == "__main__":
    main()
