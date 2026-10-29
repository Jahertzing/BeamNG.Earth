#!/usr/bin/env python3
"""The warm lap: fill the D3D12 pipeline cache along the Frederick to Castle Rock route after a game or driver update,
before Jeff's first drive (2026-10-29: the first drive after the 0.40.4 update froze four times for 4.6-8.8 s on I-25
south of Erie, each a pipeline built on first use; the builder's re-drive had none).

The game hops along the route (teleports HOPS apart), waits at each for the far field, the roads and the trees to be
in, in the day look and then the night one (lamps, lit windows, headlights have shaders of their own), so every
material the route shows gets drawn once and its pipelines land in <user>/temp/shaders/pipelinecache.d3d12.db. Nothing
is driven: pipelines are per material and look, not per metre. ~2 min a hop, ~15 min for the route.

  python tools\\warm_lap.py                     the route's hops, day and night
  python tools\\warm_lap.py --hops 3 --night-only

Needs the game running with the world server and its MCP server on (Options > Advanced). Prints the cache files'
growth at the end (pipelinecache.d3d12.db, shaders.d3d12.db): no growth means the pipelines were there already.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mcp  # noqa: E402

# the route's hops (lat, lon, name): Old Town Frederick, I-25 at Erie, Thornton, Union Station, I-25 at Hampden,
# Lone Tree, Castle Pines, Castle Rock (thedrive.lua START / DEST and the places between)
ROUTE = [
    (40.09953, -104.93700, "Frederick"),
    (40.03631, -104.98075, "I-25 at Erie"),
    (39.90000, -104.98800, "Thornton"),
    (39.75292, -104.99975, "Union Station"),
    (39.65000, -104.99200, "I-25 at Hampden"),
    (39.56000, -104.88200, "Lone Tree"),
    (39.47000, -104.87000, "Castle Pines"),
    (39.37180, -104.86002, "Castle Rock"),
]
SETTLE_S = 75           # at most this long for the world to be in at a hop
LOOK_S = 8              # drawn this long in each look once it is in
POLL_S = 2.0


def lua(code):
    return mcp.call("run_lua", {"code": code})


def user_dir():
    out = lua("return FS:getUserPath()")
    return out.strip().strip('"') if out else None


def cache_sizes(udir):
    sizes = {}
    for name in ("pipelinecache.d3d12.db", "shaders.d3d12.db"):
        p = os.path.join(udir, "temp", "shaders", name)
        sizes[name] = os.path.getsize(p) if os.path.isfile(p) else 0
    return sizes


STATUS_LUA = """
local far = require('beamngEarth/farfield').stats()
local roads = require('beamngEarth/roads').status()
local trees = require('beamngEarth/trees').status()
local st = beamngEarth_core._state and beamngEarth_core._state() or {}
return jsonEncode({ far = { done = far.done, queued = far.queued }, roads = { queued = (roads.queued or 0) + (roads.walksQueued or 0) },
  trees = { pending = trees.pending }, serverBacklog = st.serverBacklog })
"""


def status():
    out = lua(STATUS_LUA)
    try:
        return json.loads(out)
    except (TypeError, ValueError):
        return {}


def wait_in(seconds):
    """Until the far field is done and the road and tree queues are empty, or seconds pass. Returns how long it took."""
    t0 = time.time()
    while time.time() - t0 < seconds:
        s = status()
        far = s.get("far") or {}
        roads = s.get("roads") or {}
        trees = s.get("trees") or {}
        quiet = far.get("done") and (far.get("queued") or 0) == 0 and (roads.get("queued") or 0) == 0 \
            and (trees.get("pending") or 0) == 0 and (s.get("serverBacklog") or 0) == 0
        if quiet and time.time() - t0 > 5:
            return time.time() - t0
        time.sleep(POLL_S)
    return time.time() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hops", type=int, default=len(ROUTE), help="hops along the route (default: all)")
    ap.add_argument("--night-only", action="store_true")
    ap.add_argument("--day-only", action="store_true")
    ap.add_argument("--settle", type=float, default=SETTLE_S)
    args = ap.parse_args()
    looks = ["night"] if args.night_only else (["golden"] if args.day_only else ["golden", "night"])
    udir = user_dir()
    before = cache_sizes(udir) if udir else {}
    mcp.quiet_ui()
    n = max(1, min(args.hops, len(ROUTE)))
    step = (len(ROUTE) - 1) / max(1, n - 1) if n > 1 else 0
    hops = [ROUTE[round(i * step)] for i in range(n)]
    t_all = time.time()
    for lat, lon, name in hops:
        t0 = time.time()
        lua(f"beamngEarth_core.teleport({lat}, {lon}, {json.dumps(name)})")
        took = wait_in(args.settle)
        for look in looks:
            lua(f"return require('beamngEarth/thedrive').skyNow({json.dumps(look)})")
            time.sleep(LOOK_S)
        print(f"{name:16s} in after {took:5.1f} s, looks {', '.join(looks)}, hop {time.time() - t0:5.1f} s", flush=True)
    after = cache_sizes(udir) if udir else {}
    print(f"warm lap: {n} hops in {(time.time() - t_all) / 60:.1f} min")
    for k in before:
        d = after.get(k, 0) - before[k]
        print(f"  {k}: {before[k] / 1e6:.1f} -> {after.get(k, 0) / 1e6:.1f} MB ({'+' if d >= 0 else ''}{d / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
