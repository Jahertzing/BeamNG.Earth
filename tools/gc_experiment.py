#!/usr/bin/env python3
"""How long LuaJIT's atomic GC step takes against the length of its cycle, offline (2026-10-29: LuaGC frames of
103-193 ms on Jeff's drive, the game making 200-270 MB of garbage a minute on a 210-250 MB heap). The collector is
incremental but for one atomic step a cycle, which re-walks every table written since the cycle marked it; a cycle that
spans 10-30 s of frames finds most of the live heap written. The plan's fix is a pacer in the mod: time-budgeted
collectgarbage('step') calls in frames with room, so a cycle ends in a second or two. This measures what that buys,
with the same LuaJIT the Lua tests run under (lupa's luajit21), before the mod gets it.

  python tools/gc_experiment.py                 the default heap (~200 MB live) and churn, every variant
  python tools/gc_experiment.py --mb 120 --frames 3000

A live heap of --mb (tables of a few fields, some nested, as the mod's and the game's), then --frames frames: each
allocates --garbage-kb of short-lived tables and strings and writes a field in --touch live tables (the game's
per-frame writes), then runs the variant's GC policy:
  engine     one collectgarbage('step', 0) a frame (a small step, as an engine stepping once a frame)
  auto       no explicit steps: the allocator's own stepping (LuaJIT's default pause 200 / stepmul 200)
  pacer-N    collectgarbage('step', 0) calls until N ms of this frame went to the collector (the mod's pacer)
Per variant: the cycles seen, their length in frames, the collector's ms a frame (mean, max: the atomic step), the
longest step, the heap's range. Prints a table; --json writes it.
"""
import argparse
import json
import sys
import time

LUA = r"""
local live, N = {}, 0
local M = {}
function M.build(mb)
  local bytes0 = collectgarbage('count')
  local i = 0
  while collectgarbage('count') - bytes0 < mb * 1024 do
    i = i + 1
    local t = { id = i, x = i * 0.5, y = -i, name = 'node' .. i, links = { i + 1, i + 2 }, hot = 0 }
    if i % 3 == 0 then t.sub = { a = i, b = 'edge' .. i, c = { i } } end
    live[i] = t
  end
  N = i
  collectgarbage('collect')
  return N, collectgarbage('count') / 1024
end
local seed = 12345
local function rnd(n) seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n + 1 end
-- a frame's garbage (kb) and its writes into the live heap (touch tables)
function M.frame(garbageKb, touch)
  local made = 0
  local keep = {}
  while made < garbageKb do
    local s = { tostring(made), made * 2, { made }, 'str' .. made }
    keep[#keep % 64 + 1] = s          -- (a few survive the frame, as a frame's own tables do)
    made = made + 0.2                  -- (~200 bytes a round)
  end
  for _ = 1, touch do
    local t = live[rnd(N)]
    t.hot = t.hot + 1
  end
  return keep
end
function M.count() return collectgarbage('count') end
return M
"""


def run_variant(lua, mod, name, frames, garbage_kb, touch, budget_ms):
    gc = lua.globals().collectgarbage
    clock = time.perf_counter
    gc("restart")
    gc("collect")
    gc("setpause", 200)
    gc("setstepmul", 200)
    if name != "auto":
        gc("stop")          # (explicit steps only: 'stop' turns the allocator's stepping off; 'step' still works)
    per_frame, steps, cycles = [], [], []
    cycle_start = 0
    heap_lo, heap_hi = 1e18, 0
    for f in range(frames):
        mod.frame(garbage_kb, touch)
        t0 = clock()
        if name == "engine":
            done = gc("step", 0)
            steps.append(clock() - t0)
            if done:
                cycles.append(f - cycle_start)
                cycle_start = f
        elif name.startswith("pacer"):
            budget = budget_ms / 1000.0
            while True:
                s0 = clock()
                done = gc("step", 0)
                s1 = clock()
                steps.append(s1 - s0)
                if done:
                    cycles.append(f - cycle_start)
                    cycle_start = f
                    break           # (one cycle's end a frame at most: the next cycle starts next frame)
                if s1 - t0 >= budget:
                    break
        else:               # auto: the allocator stepped during mod.frame; its time is in the frame, measured whole
            pass
        per_frame.append(clock() - t0)
        h = mod.count()
        heap_lo, heap_hi = min(heap_lo, h), max(heap_hi, h)
    if name == "auto":
        # the collector's time is inside the frames: the frame times' spikes stand for the atomic steps
        per_frame = []
        for f in range(frames):
            t0 = clock()
            mod.frame(garbage_kb, touch)
            per_frame.append(clock() - t0)
            h = mod.count()
            heap_lo, heap_hi = min(heap_lo, h), max(heap_hi, h)
        steps = per_frame
    gc("restart")
    pf = sorted(per_frame)
    st = sorted(steps)
    return {
        "variant": name, "frames": frames, "cycles": len(cycles),
        "cycleFrames": round(sum(cycles) / len(cycles), 1) if cycles else None,
        "gcMsMean": round(1000 * sum(per_frame) / len(per_frame), 3),
        "gcMsP99": round(1000 * pf[int(0.99 * (len(pf) - 1))], 2),
        "gcMsMax": round(1000 * pf[-1], 2),
        "stepMsMax": round(1000 * st[-1], 2) if st else None,
        "stepMsP99": round(1000 * st[int(0.99 * (len(st) - 1))], 2) if st else None,
        "heapMb": [round(heap_lo / 1024), round(heap_hi / 1024)],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mb", type=float, default=200, help="live heap to build (MB)")
    ap.add_argument("--frames", type=int, default=2500, help="frames per variant (85 a second)")
    ap.add_argument("--garbage-kb", type=float, default=50, help="garbage a frame (50 KB = 250 MB a minute at 85 fps)")
    ap.add_argument("--touch", type=int, default=20000, help="live tables written each frame")
    ap.add_argument("--variants", default="engine,auto,pacer-0.5,pacer-1,pacer-2")
    ap.add_argument("--json")
    args = ap.parse_args()
    try:
        import lupa.luajit21 as lupa_mod
    except ImportError:
        import lupa as lupa_mod
    lua = lupa_mod.LuaRuntime(unpack_returned_tuples=True)
    print(f"LuaJIT: {lua.globals().jit.version if lua.globals().jit else lua.lua_version}", flush=True)
    mod = lua.execute(LUA)
    t0 = time.perf_counter()
    n, mb = mod.build(args.mb)
    print(f"live heap: {n} tables, {mb:.0f} MB, built in {time.perf_counter() - t0:.1f} s", flush=True)
    results = []
    for v in args.variants.split(","):
        v = v.strip()
        budget = float(v.split("-", 1)[1]) if v.startswith("pacer") else 0
        t1 = time.perf_counter()
        r = run_variant(lua, mod, v, args.frames, args.garbage_kb, args.touch, budget)
        r["wallS"] = round(time.perf_counter() - t1, 1)
        results.append(r)
        print(f"{v:10s} cycles {r['cycles']:4d} of {str(r['cycleFrames']):>6s} frames | gc ms/frame mean {r['gcMsMean']:6.3f} "
              f"p99 {r['gcMsP99']:6.2f} max {r['gcMsMax']:7.2f} | longest step {r['stepMsMax']} ms | heap {r['heapMb'][0]}-{r['heapMb'][1]} MB",
              flush=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"args": vars(args), "results": results}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
