#!/usr/bin/env python3
"""Pre-build a route so it drives without building anything (the user, 2026-10-28: "no streaming, of assets at least"):
every terrain block, base photo, road and building tile, skyline and far tile along it built once and kept on disk,
in frames that a later drive lands on again.

  python3 tools/route_bake.py plan                     both routes' anchors: written for the mod and the server
  python3 tools/route_bake.py drive --out DIR          the bake: the car drives each route itself (game running)
  python3 tools/route_bake.py status                   the anchors, the bake's size per kind, stale after an update?
  python3 tools/route_bake.py clean                    remove baked items the last complete drive did not use

How (revisit.py, blockcache.py, the mod's routepins.lua):
  plan    the road route of each ROUTES entry (routing.plan: OSRM), and the anchors a drive along it re-centres on: the
          mod's rebase rule (a new anchor once the car is rebaseDistance - PREFETCH_MARGIN from the last, horizontally)
          walked along the route, each new anchor the planned one within PIN of the car (another route's: the routes
          share I-25) else the grid point (geodesy.snapAnchor). Written to the mod's settings folder (routepins.lua
          pins teleports and rebases near one to it) and to <world>/bake/anchors.json (what is built in one of them is
          kept in <world>/bake, never cut back or cleared; --world-dir). PIN is under half the anchor spacing, so a drive either way along
          a route, or one that set off from a town on it, lands on the same anchors.
  drive   for each route: tools/autodrive_perf.py over its legs (teleport to the start, the car's own AI on the GPS
          route, the frame times sampled), the server keeping everything it builds. The first drive after a code
          change builds; the next one is the benchmark (its summary: fps, 1% lows, hitches). Needs the game with the
          MCP server on and the companion server running (launcher.py).
  clean   a code change makes every baked item unreachable (revisit's key holds the code); after a complete drive
          the items it did not touch (older than its start) are removed.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, ROOT)

# The routes (the user, 2026-10-28): Frederick to Castle Rock through downtown Denver, and straight down I-25.
ROUTES = {
    "downtown": {"name": "Frederick to Castle Rock through downtown Denver",
                 "points": [("Frederick", 40.0992, -104.9372), ("Union Station", 39.7530, -105.0002),
                            ("Castle Rock", 39.3722, -104.8561)]},
    "i25": {"name": "Frederick to Castle Rock straight down I-25",
            "points": [("Frederick", 40.0992, -104.9372), ("Castle Rock", 39.3722, -104.8561)]},
}
MOD_FILE = ("settings", "beamngEarth_route_anchors.json")      # routepins.lua M.FILE
PREFETCH_MARGIN = 300.0     # rebase.lua: the next anchor is asked for this much before the rebase distance
DEFAULT_DISTANCE = 15000.0  # rebase.lua DEFAULT_DISTANCE (the mod's setting rebaseDistance)
PIN_SHARE = 0.45            # PIN = this share of the rebase distance (under half the anchor spacing)
MAX_PIN_M = 9000.0          # routepins.lua MAX_PIN_M
SNAP_DEG = 0.01             # geodesy.lua SNAP_DEG / SNAP_MAX_LAT
SNAP_MAX_LAT = 85.0
STEP_M = 50.0               # the route walked in steps of this
MANIFEST = "manifest.json"


# --- the anchors (no network) ---------------------------------------------------------------------------------------
def snap_anchor(lat, lon):
    """geodesy.lua snapAnchor: the anchor grid point of (lat, lon)."""
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


def horizontal(a, b):
    """The horizontal distance (m) between (lat, lon) a and b in a's frame (as the mod measures from its anchor)."""
    import geodesy
    e, n, _ = geodesy.EnuFrame(a[0], a[1]).geodetic_to_enu(b[0], b[1], 0.0)
    return math.hypot(float(e), float(n))


def pin(p, anchors, pin_m):
    """routepins.lua pin: the anchor nearest to p within pin_m, or None."""
    best = None
    for a in anchors:
        if abs(a[0] - p[0]) < 0.2:
            d = horizontal(a, p)
            if d <= pin_m and (best is None or d < best[0]):
                best = (d, a)
    return None if best is None else best[1]


def densify(points, step=STEP_M):
    """The polyline [(lat, lon)] with a point at least every step metres."""
    out = [tuple(points[0])]
    for a, b in zip(points, points[1:]):
        d = horizontal(a, b)
        n = max(1, int(math.ceil(d / step)))
        out += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(1, n + 1)]
    return out


def walk(dense, anchors, distance, margin, pin_m, add=True):
    """The anchors (indices into anchors) a drive along dense re-centres on, the mod's rule: the first where it starts,
    a new one once the car is distance - margin from the last; each the planned one within pin_m, else the grid
    point (added to anchors when add)."""
    def land(p):
        a = pin(p, anchors, pin_m) or snap_anchor(*p)
        if a not in anchors:
            if not add:
                return None
            anchors.append(a)
        return a
    cur = land(dense[0])
    seq = [anchors.index(cur) if cur else -1]
    for p in dense[1:]:
        if cur is not None and horizontal(cur, p) >= distance - margin:
            cur = land(p)
            seq.append(anchors.index(cur) if cur else -1)
    return seq


def plan_anchors(routes, distance=DEFAULT_DISTANCE, margin=PREFETCH_MARGIN, pin_m=None, both_ways=True):
    """(anchors [(lat, lon)], {route: [anchor indices in drive order]}) for routes {name: [(lat, lon), ...]}: the mod's
    rebase rule walked along each, in order, the later routes landing on the earlier ones' anchors where they meet;
    both_ways: each route driven back too (name + "<"). Every drive is walked again over the final anchors, which must
    give the same order (a later anchor nearer to an earlier drive's re-centre point: ValueError)."""
    pin_m = min(distance * PIN_SHARE, MAX_PIN_M) if pin_m is None else pin_m
    anchors, order, dense = [], {}, {}
    for name, pts in routes.items():
        dense[name] = densify(pts)
        if both_ways:
            dense[name + "<"] = dense[name][::-1]
    for name, d in dense.items():
        order[name] = walk(d, anchors, distance, margin, pin_m)
    for name, d in dense.items():
        if walk(d, anchors, distance, margin, pin_m, add=False) != order[name]:
            raise ValueError(f"{name}: the anchors planned for the other drives change where it re-centres")
    return anchors, order


# --- the folders ----------------------------------------------------------------------------------------------------
def default_cache_dir():
    try:
        import apppaths
        return apppaths.saved_cache_dir() or apppaths.default_cache_dir()
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".cache", "beamng-earth")


def default_user_dir():
    try:
        import install
        found = install.candidate_user_dirs()
        if found:
            return found[0]
    except Exception:
        pass
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "BeamNG", "BeamNG.drive", "current")


def rebase_distance(user_dir):
    try:
        import bmplaunch
        return float(bmplaunch.mod_setting(user_dir, "rebaseDistance", DEFAULT_DISTANCE))
    except Exception:
        return DEFAULT_DISTANCE


def default_world_dir():
    try:
        import apppaths
        return apppaths.saved_world_dir() or apppaths.default_world_dir()
    except Exception:
        return os.path.join(os.path.expanduser("~"), "beamng-earth", "world")


def bake_dir(a):
    """The baked route's folder: revisit.pin_dir's, in the world folder (--world-dir; "off": the cache's, as before)."""
    import revisit
    return revisit.pin_dir(a.cache_dir, None if a.world_dir == "off" else a.world_dir)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# --- the commands ---------------------------------------------------------------------------------------------------
def route_points(names, offline=None):
    """{route: [(lat, lon)]}: OSRM's road route over each route's points (offline: a JSON of {route: [[lat, lon]]})."""
    if offline:
        data = read_json(offline)
        return {n: [tuple(p) for p in data[n]] for n in names}
    import routing
    out = {}
    for n in names:
        pts = ROUTES[n]["points"]
        path = []
        for (na, la0, lo0), (nb, la1, lo1) in zip(pts, pts[1:]):
            r = routing.plan(la0, lo0, la1, lo1)
            leg = [tuple(p) for p in r["points"]]
            print(f"  {n}: {na} -> {nb}: {r['dist'] / 1000:.1f} km")
            path += leg if not path else leg[1:]
        out[n] = routing.simplify(path, 20.0)
    return out


def cmd_plan(a):
    names = a.routes.split(",")
    dist = a.rebase_m or rebase_distance(a.user_dir)
    pts = route_points(names, a.points)
    anchors, order = plan_anchors(pts, dist)
    pin_m = min(dist * PIN_SHARE, MAX_PIN_M)
    data = {"version": 1, "pinM": pin_m, "rebaseDistance": dist, "anchors": [list(x) for x in anchors],
            "routes": {n: {"name": ROUTES[n]["name"], "anchors": order[n], "back": order[n + "<"],
                           "km": round(sum(horizontal(p, q) for p, q in zip(pts[n], pts[n][1:])) / 1000, 1)}
                       for n in names},
            "planned": time.strftime("%Y-%m-%d %H:%M:%S")}
    write_json(os.path.join(a.user_dir, *MOD_FILE), data)
    write_json(os.path.join(bake_dir(a), "anchors.json"), data)
    write_json(os.path.join(bake_dir(a), "routes.json"), {n: [list(p) for p in pts[n]] for n in names})
    print(f"{len(anchors)} anchors (rebase {dist / 1000:g} km, pinned within {pin_m / 1000:.2f} km):")
    for i, x in enumerate(anchors):
        used = [n for n in order if i in order[n]]
        print(f"  {i:2d}  {x[0]:.6f}, {x[1]:.6f}  {' '.join(used)}")
    for n in names:
        print(f"  {n} ({data['routes'][n]['km']} km): anchors {order[n]}, back {order[n + '<']}")
    print(f"written: {os.path.join(a.user_dir, *MOD_FILE)}")
    print(f"         {os.path.join(bake_dir(a), 'anchors.json')}")
    return 0


def sizes(folder):
    """{kind: (files, bytes)} under folder."""
    out = {}
    if not os.path.isdir(folder):
        return out
    for kind in sorted(os.listdir(folder)):
        p = os.path.join(folder, kind)
        if not os.path.isdir(p):
            continue
        n = b = 0
        for d, _, files in os.walk(p):
            for f in files:
                if f.endswith(".zip"):
                    n += 1
                    b += os.path.getsize(os.path.join(d, f))
        out[kind] = (n, b)
    return out


def cmd_status(a):
    import revisit
    bd = bake_dir(a)
    data = read_json(os.path.join(bd, "anchors.json"))
    if not data:
        print("no route planned (route_bake.py plan)")
        return 1
    print(f"{len(data['anchors'])} anchors, planned {data.get('planned')}:")
    for n, r in data["routes"].items():
        print(f"  {n}: {r['name']} ({r['km']} km), anchors {r['anchors']}")
    mod = read_json(os.path.join(a.user_dir, *MOD_FILE))
    if not mod or mod.get("anchors") != data["anchors"]:
        print(f"  the mod's copy ({os.path.join(a.user_dir, *MOD_FILE)}) is missing or differs: run plan again")
    total = 0
    for kind, (n, b) in sizes(bd).items():
        total += b
        print(f"  {kind:10s} {n:7d} items {b / 1e9:7.2f} GB")
    print(f"  total {total / 1e9:.2f} GB in {bd}")
    man = read_json(os.path.join(bd, MANIFEST), {})
    runs = man.get("runs") or []
    last = next((r for r in reversed(runs) if r.get("complete")), None)
    if last is None:
        print("  not baked yet (route_bake.py drive)")
    elif last.get("code") != revisit.code_key():
        print(f"  STALE: baked {last.get('ended')} with other server code; drive again to re-bake, then clean")
    else:
        print(f"  baked {last.get('ended')} with this code")
    return 0


def cmd_drive(a):
    import revisit
    bd = bake_dir(a)
    data = read_json(os.path.join(bd, "anchors.json"))
    if not data:
        print("plan first (route_bake.py plan)", file=sys.stderr)
        return 2
    names = a.routes.split(",")
    man = read_json(os.path.join(bd, MANIFEST), {})
    run = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "start_ts": time.time(), "code": revisit.code_key(),
           "routes": names, "complete": False, "legs": []}
    man.setdefault("runs", []).append(run)
    write_json(os.path.join(bd, MANIFEST), man)
    ok = True
    for n in names:
        pts = ROUTES[n]["points"]
        legs = list(zip(pts, pts[1:]))
        if a.both_ways:
            back = list(reversed(pts))
            legs += list(zip(back, back[1:]))
        for i, ((na, la0, lo0), (nb, la1, lo1)) in enumerate(legs):
            out = os.path.join(a.out, f"{n}_{i}_{na}-{nb}".replace(" ", "_"))
            km = horizontal((la0, lo0), (la1, lo1)) / 1000
            cmd = [sys.executable, os.path.join(HERE, "autodrive_perf.py"), "--to", f"{la1},{lo1}", "--out", out,
                   "--minutes", f"{max(20.0, km * 1.6):.0f}"]
            if i == 0:          # (the next legs from where the car arrived)
                cmd += ["--from", f"{la0},{lo0}"]
            if a.ai_mph:
                cmd += ["--ai-mph", str(a.ai_mph)]
            if a.keep_going:
                cmd.append("--keep-going")
            print(f"{n}: {na} -> {nb} ({km:.0f} km): {' '.join(cmd[1:])}", flush=True)
            r = _drive_leg(cmd)
            summary = read_json(os.path.join(out, "summary.json"), {})
            run["legs"].append({"route": n, "from": na, "to": nb, "rc": r.returncode, "out": out,
                                "summary": {k: summary.get(k) for k in ("seconds", "fps", "frame_ms", "frames_over",
                                                                        "miles", "remaining_m", "hitch_causes",
                                                                        "events") if k in summary}})
            write_json(os.path.join(bd, MANIFEST), man)
            if r.returncode != 0:
                ok = False
                if not a.keep_going:
                    break
        if not ok and not a.keep_going:
            break
    run["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
    run["complete"] = ok
    write_json(os.path.join(bd, MANIFEST), man)
    cmd_status(a)
    return 0 if ok else 1


def _drive_leg(cmd):
    """Run one leg's autodrive_perf. It holds the read end of a pipe only this process writes (--parent-pipe), so
    however this tool ends (Ctrl+C, killed by PID) the leg parks the car and turns the mod's autodrive off (the first PC
    bake, killed by PID 2026-10-28, left both running)."""
    p = subprocess.Popen(cmd + ["--parent-pipe"], stdin=subprocess.PIPE)
    try:
        p.wait()
    except KeyboardInterrupt:
        p.stdin.close()
        try:
            p.wait(60)
        except subprocess.TimeoutExpired:
            p.kill()
        raise
    finally:
        if p.stdin and not p.stdin.closed:
            p.stdin.close()
    return p


def cmd_clean(a):
    bd = bake_dir(a)
    man = read_json(os.path.join(bd, MANIFEST), {})
    last = next((r for r in reversed(man.get("runs") or []) if r.get("complete")), None)
    if last is None:
        print("no complete drive yet: nothing removed")
        return 1
    cut = last["start_ts"] - 60.0
    n = b = 0
    for kind, _ in sizes(bd).items():
        for d, _, files in os.walk(os.path.join(bd, kind)):
            for f in files:
                p = os.path.join(d, f)
                if f.endswith(".zip") and os.path.getmtime(p) < cut:
                    b += os.path.getsize(p)
                    n += 1
                    if not a.dry_run:
                        os.remove(p)
    print(f"{'would remove' if a.dry_run else 'removed'} {n} items, {b / 1e9:.2f} GB, older than the drive of "
          f"{last['started']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("plan", "drive", "status", "clean"))
    ap.add_argument("--routes", default=",".join(ROUTES), help=f"comma list of {', '.join(ROUTES)}")
    ap.add_argument("--cache-dir", default=None, help="the server's cache (default: the server's)")
    ap.add_argument("--world-dir", default=None,
                    help="the world folder: the bake lives in its bake/ (default: the server's; off: the cache's)")
    ap.add_argument("--user-dir", default=None, help="the game's user folder (default: the one the game uses)")
    ap.add_argument("--rebase-m", type=float, default=None, help="plan: the rebase distance (default: the mod's)")
    ap.add_argument("--points", default=None, help="plan: the routes' points from this JSON instead of OSRM")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "route_bake"), help="drive: the runs' folder")
    ap.add_argument("--ai-mph", type=float, default=0.0, help="drive: the AI's speed (default: the legal speed)")
    ap.add_argument("--both-ways", action="store_true", help="drive: back again too (bakes the other direction)")
    ap.add_argument("--keep-going", action="store_true", help="drive: on after a crash, a stop or a failed leg")
    ap.add_argument("--dry-run", action="store_true", help="clean: say what would go")
    a = ap.parse_args(argv)
    a.cache_dir = a.cache_dir or default_cache_dir()
    a.world_dir = a.world_dir or default_world_dir()
    a.user_dir = a.user_dir or default_user_dir()
    return {"plan": cmd_plan, "drive": cmd_drive, "status": cmd_status, "clean": cmd_clean}[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
