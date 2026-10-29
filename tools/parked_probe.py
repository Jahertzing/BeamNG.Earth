#!/usr/bin/env python3
"""Count parked cars appearing and disappearing in view (the car-lot flicker, Jeff's drive 2026-10-28): every --every s
for --minutes, the stock parked cars' places and active state; a car that jumped more than 5 m, or went active or
inactive, while it was (or now is) within VIEW_M in front of the camera counts as a flicker. Read-only.

  python tools/parked_probe.py --minutes 2            # stand by a lot; turn the camera round now and then

Needs the game's MCP server (tools/mcp.py).
"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp  # noqa: E402

VIEW_M, VIEW_DEG = 600, 70

READ = r"""
local gp = gameplay_parking
local cam = core_camera
local cp, cf = cam.getPosition(), cam.getForward()
local cars = {}
for _, id in ipairs(gp and gp.getParkedCarsList and gp.getParkedCarsList() or {}) do
  local o = getObjectByID(id)
  if o then
    local p = o:getPosition()
    cars[#cars + 1] = { id = id, x = p.x, y = p.y, active = o:getActive() }
  end
end
return jsonEncode({ cx = cp.x, cy = cp.y, fx = cf.x, fy = cf.y, cars = cars,
                    state = gp and gp.getState and gp.getState() or false })
"""


def in_view(x, y, s):
    dx, dy = x - s["cx"], y - s["cy"]
    d = math.hypot(dx, dy)
    if d > VIEW_M:
        return False
    if d < 40:
        return True
    fl = math.hypot(s["fx"], s["fy"]) or 1
    return (dx * s["fx"] + dy * s["fy"]) / fl >= math.cos(math.radians(VIEW_DEG)) * d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--minutes", type=float, default=2.0)
    ap.add_argument("--every", type=float, default=0.5)
    a = ap.parse_args(argv)
    prev, flick, moves, n = {}, 0, 0, 0
    end = time.time() + a.minutes * 60
    while time.time() < end:
        s = json.loads(mcp.call("run_lua", {"code": READ}))
        n += 1
        for c in s.get("cars", []):
            p = prev.get(c["id"])
            if p:
                jumped = math.hypot(c["x"] - p["x"], c["y"] - p["y"]) > 5
                toggled = c["active"] != p["active"]
                if jumped or toggled:
                    moves += 1
                    if in_view(p["x"], p["y"], s) or in_view(c["x"], c["y"], s):
                        flick += 1
                        print(f"in view: car {c['id']} {'moved' if jumped else 'shown' if c['active'] else 'hidden'}",
                              flush=True)
            prev[c["id"]] = c
        time.sleep(a.every)
    print(f"{n} samples, {len(prev)} parked cars: {moves} moves, {flick} of them in view")


if __name__ == "__main__":
    main()
