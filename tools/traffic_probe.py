#!/usr/bin/env python3
"""A read-only look at every AI traffic car in the running game (the traffic fixes of 2026-10-28): speed, distance to
the player, the road under it (ramp, the private ring past the block, its speed), the merge assist's cap, and from the
car's own Lua whether the keep-route wrapper is in and what it would see (ai.lua's mode, its route, its map vs the
car's current one). Changes nothing in the game.

  python tools/traffic_probe.py            # one table
  python tools/traffic_probe.py --json     # the raw rows

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
  if im then local _, m = debug.getupvalue(f, im) r.aiMapSame = m == mapmgr.mapData r.aiMapNil = m == nil end
  if ir then
    local _, route = debug.getupvalue(f, ir)
    r.route = route ~= nil
    if route and route.path then
      r.pathLen = #route.path
      r.planIdx = route.plan and route.plan[1] and route.plan[1].pathidx or nil
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
    local row = { id = id, mph = sp * 2.23694, active = o:getActive(), dist = pp and (p - pp):length() or nil }
    local dir = sp > 1 and vec3(u.x / sp, u.y / sp, 0) or o:getDirectionVector()
    local n1, n2 = map.findBestRoad(p, dir)
    if n1 and n2 and nodes and nodes[n1] then
      local e = nodes[n1].links[n2] or (nodes[n2] and nodes[n2].links[n1])
      if e then
        row.ramp, row.private, row.limitMph = e.ramp or false, e.type == 'private', (e.speedLimit or 0) * 2.23694
        row.oneWay, row.drivability = e.oneWay, e.drivability
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
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
          f"{'same':>5} path")
    for r in sorted(out.get("rows", []), key=lambda r: r.get("dist") or 0):
        road = "off graph" if "limitMph" not in r else \
            f"{'ramp ' if r.get('ramp') else ''}{'ring ' if r.get('private') else ''}{r['limitMph']:.0f}mph d{r.get('drivability')}"
        c = r.get("car") if isinstance(r.get("car"), dict) else {}
        up = f"{c.get('im')}/{c.get('ir')}" if c else "-"
        print(f"{r['id']:>6} {r['mph']:5.1f} {r.get('dist') or 0:6.0f} {road:>16} {str(c.get('wrap', '-')):>5} "
              f"{str(c.get('mode', '-')):>8} {up:>5} {str(c.get('route', '-')):>5} {str(c.get('aiMapSame', '-')):>5} "
              f"{c.get('pathLen', '-')}@{c.get('planIdx', '-')}")


if __name__ == "__main__":
    main()
