#!/usr/bin/env python3
"""Pre-load an area into the download cache: the elevation tiles, the imagery and the map data of a named region
(tools/regions.json: towns, road corridors, the fill between), a place, a route or a disc, fetched by the server's own
code (dem.Dem, imagery.Imagery, mapdata.MapData behind a netfetch.Fetcher with the host policies and the daily
budgets), so the world there loads from disk and at its sharpest: the lazy export sources (a county's or a town's
orthophotos, NAIP) are otherwise only fetched a few hundred metres ahead of the player.

  python3 tools/preload.py --list                                  the named regions
  python3 tools/preload.py --region front-range --dry-run          the plan, its size, time and days
  python3 tools/preload.py --region front-range                    fetch it (Ctrl+C stops; run it again to resume)
  python3 tools/preload.py --place "Longmont, CO" --radius-km 15   round a place (Photon geocoding)
  python3 tools/preload.py --route "Boulder, CO" "Colorado Springs, CO" --width-km 10   along the road route (OSRM)
  python3 tools/preload.py --center 40.0992,-104.9372 --radius-km 20 [--z18-km 5]       a disc

What it fetches over the region's parts (towns first, then the roads, then the fill; nearest the region's home point
first within each; what is on disk already, known missing or in the region's manifest is skipped):
  dem      Mapterhorn at the finest zoom (dem.FINE_ZOOM: the near ground and the physics block) with the z12 tiles the
           ground filter reads, z15-z13 a little round it, z12-z11 over --far-km round it, z10 over twice that
  imagery  the chain's lazy export sources at z17 (one 1024 px export per z15 parent) and, in the towns, the regional
           sources at z18; each parent goes to the first source of the chain that covers it (a coverage probe of
           each regional source: one 64 px export per z13 tile, kept in <cache>/preload/coverage), to the next one
           where that one has gaps (past its county line), and to NAIP where no regional source has it;
           USGS z16-z15 over the region, z14-z12 over --far-km
  mapdata  the z14 cells, every layer (mapdata.ALL_LAYERS), for the pinned Overture release
Every host runs at once, each within its policy (netfetch.POLICIES: rate, requests at once); a host stops for the day
at its soft daily budget (the game needs the rest; --over-budget: on at half rate) and the next run resumes there.
Finished keys go to <cache>/preload/<region>.done (the manifest; --recheck ignores it).

Run it with the game and the world server closed: the server keeps its own index of the cache (cache.py
index.tsv) and the last process to save it wins. It refuses to run on the server's cache while something
listens on the server's port (--allow-running: anyway). The cache cap is the one the mod set (--cache-cap to
change it); the estimate says when the region does not fit.
"""
import argparse
import io
import json
import math
import os
import queue
import re
import signal
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.append(ROOT)
import cache as cachemod  # noqa: E402
import dem as demmod  # noqa: E402
import imagery as imagerymod  # noqa: E402
import mapcells  # noqa: E402
import mapdata as mapdatamod  # noqa: E402
import netfetch  # noqa: E402
import overture  # noqa: E402
import regions as regmod  # noqa: E402

FREDERICK = (40.0992, -104.9372)
SERVER_PORT = 47613             # earthd --port's default
GB = 1e9
# Rough sizes and times for the estimate (Front Range, 2026-10-27/28): bytes downloaded and written to the cache per
# request, and seconds per request one at a time (a host runs min(max_conc, workers) at once, within its rate)
EST = {
    "dem": (55e3, 55e3, 0.15),
    "xyz": (30e3, 30e3, 0.1),
    "export": (380e3, 16 * 25e3, 0.9),          # a county's 1024 px export: 260-490 KB, 0.4-1.7 s; 16 JPEGs q92
    "export_slow": (300e3, 16 * 45e3, 6.0),     # USGS's NAIP exportImage (summer, busier JPEGs): 2.7-14 s
    "cell": (1.0e6, 650e3, 4.0),                # all layers of a cell (Overture range reads, the first time)
}
SLOW_HOSTS = {"imagery.nationalmap.gov", "apps.geo.fpac.usda.gov"}
WORKERS = {"dem": 16, "xyz": 8, "cell": 4}
MID_PAD = 0.25                  # the DEM's z15-z13 reach this share of far_km round the region
PROBE_Z, PROBE_PX = 13, 64      # coverage: one 64 px export per z13 tile, 16 px per z15 parent
PROBE_SHARE = 0.05              # a z15 parent with this share of texels neither white nor black has data


def default_cache_dir():
    """The server's own default (earthd --cache-dir): the folder chosen on the settings page, else ~/.cache."""
    try:
        import apppaths
        return apppaths.saved_cache_dir() or apppaths.default_cache_dir()
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".cache", "beamng-earth")


def server_listening(port=SERVER_PORT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "area"


# --- the area --------------------------------------------------------------------------------------------------------
def disc_region(lat, lon, radius_km, z18_km=None, far_km=80.0):
    """The old disc: road tier (z17) over radius_km, town tier (z18 too) over z18_km (default: the whole disc)."""
    z18 = radius_km if z18_km is None else z18_km
    parts = [regmod.circle("disc", lat, lon, radius_km, "road")]
    if z18 > 0:
        parts.insert(0, regmod.circle("disc-z18", lat, lon, min(z18, radius_km), "town"))
    return regmod.Region(f"disc-{lat:.4f},{lon:.4f}-{radius_km:g}km", parts, far_km=far_km, home=(lat, lon))


def geocode_one(net, text):
    import geocode
    hits = geocode.search(text, net=net)
    if not hits:
        raise SystemExit(f"no place found for {text!r}")
    h = hits[0]
    print(f"  {text!r}: {h['name']}{', ' + h['detail'] if h.get('detail') else ''} ({h['lat']:.4f}, {h['lon']:.4f})")
    return h["lat"], h["lon"]


def route_region(net, a, b, width_km, tier, far_km):
    import routing
    la0, lo0 = geocode_one(net, a)
    la1, lo1 = geocode_one(net, b)
    r = routing.plan(la0, lo0, la1, lo1, net=net)
    pts = routing.simplify([tuple(p) for p in r["points"]], 150.0)
    print(f"  route: {r['dist'] / 1000:.0f} km, {len(pts)} points")
    return regmod.Region(f"route-{slug(a)}-{slug(b)}-{width_km:g}km",
                         [regmod.corridor("route", pts, width_km, tier)], far_km=far_km, home=(la0, lo0))


# --- the plan (no network) -------------------------------------------------------------------------------------------
def plan_dem(region, fine_zoom=None):
    """DEM keys (z, x, y) with their priority: the coarse rings first (few; the horizon), then the fine zoom with the
    z12 tiles the ground filter reads, by tier and nearest the home point."""
    fz = fine_zoom if fine_zoom is not None else demmod.zoom_for(region.home[0], 1.25, demmod.FINE_ZOOM)
    far, mid = region.far_m, MID_PAD * region.far_m
    keys = {}
    for z, pad in ((10, 2 * far), (11, far), (12, far)) + tuple((z, mid) for z in range(13, fz)):
        for x, y in region.ordered(z, region.tiles(z, pad)):
            keys.setdefault((z, x, y), 0)
    fine = region.tiles(fz)
    for x, y in region.ordered(fz, fine):
        if demmod.Dem.ground_filter and fz > demmod.GLOBAL_ZOOM:
            sh = fz - demmod.GLOBAL_ZOOM
            keys.setdefault((demmod.GLOBAL_ZOOM, x >> sh, y >> sh), fine[(x, y)])
        keys.setdefault((fz, x, y), fine[(x, y)])
    return list(keys.items())


def plan_xyz(region, names=("usgs",), skip=()):
    """[(source, [((source, z, x, y), priority)])]: z16-z15 over the region, z14-z12 over far_km round it."""
    out = []
    for name in names:
        if name in skip:
            continue
        src = imagerymod.SOURCES[name]
        keys = []
        for z, pad in ((14, region.far_m), (13, region.far_m), (12, region.far_m), (15, 0.0), (16, 0.0)):
            if src.min_zoom <= z <= src.max_zoom:
                t = region.tiles(z, pad)
                keys += [((src, z, x, y), t[(x, y)]) for x, y in region.ordered(z, t)
                         if imagerymod.in_bounds(src, z, x, y)]
        out.append((src, keys))
    return out


def plan_cells(region):
    t = region.tiles(mapcells.ZOOM)
    return [(mapcells.CellKey(mapcells.ZOOM, x, y), t[(x, y)]) for x, y in region.ordered(mapcells.ZOOM, t)]


def export_sources(chain, skip=()):
    return [s for s in chain if s.lazy and s.export and s.name not in skip]


def parent15(z, x, y):
    """The z15 tile holding the zoom-z tile x, y (z >= 15)."""
    return x >> (z - 15), y >> (z - 15)


def plan_exports(region, chain, coverage=None, skip=()):
    """The export parents, each as (z, x, y) of its first child (as imagery.warm makes them) at z17 over the region and
    z18 over its z18 tiers, with its priority and its candidate sources in chain order: the regional ones whose box
    holds it, whose max_zoom reaches z and whose coverage (a {source name: {(x15, y15): bool}}, None or a missing
    entry: by the box) says data, then the national ones (no box; z17 only). [(key, priority, [sources])]."""
    srcs = export_sources(chain, skip)
    coverage = coverage or {}
    out = []
    z18_tiers = {t for t, v in region.tiers.items() if isinstance(v, dict) and v.get("z18")}
    for z, where in ((17, None), (18, lambda p: p.tier in z18_tiers)):
        k = 2                                       # export=2: a parent of 4 x 4 tiles
        par = region.tiles(z - k, where=where)
        for px, py in region.ordered(z - k, par):
            key = (z, px << k, py << k)
            cands = []
            for s in srcs:
                if s.max_zoom < z or z < s.min_zoom or s.export != k:
                    continue
                if s.bounds:
                    if not imagerymod.in_bounds(s, *key):
                        continue
                    cov = coverage.get(s.name)
                    if cov is not None and cov.get(parent15(*key)) is False:
                        continue
                elif z > 17:
                    continue
                cands.append(s)
            if cands:
                out.append((key, par[(px, py)], cands))
    return out


# --- coverage probe --------------------------------------------------------------------------------------------------
def classify_probe(rgb, cells=4):
    """[[bool]] per (row, column) block of a probe image: has data (texels neither white nor black). Pure."""
    a = np.asarray(rgb)
    data = (np.minimum(np.minimum(a[..., 0], a[..., 1]), a[..., 2]) < imagerymod.NODATA_WHITE_MIN) & \
           (np.maximum(np.maximum(a[..., 0], a[..., 1]), a[..., 2]) > imagerymod.NODATA_MAX)
    h, w = data.shape
    bh, bw = h // cells, w // cells
    return [[bool(data[j * bh:(j + 1) * bh, i * bw:(i + 1) * bw].mean() > PROBE_SHARE) for i in range(cells)]
            for j in range(cells)]


def coverage_path(cache_dir, src):
    return os.path.join(cache_dir, "preload", "coverage", f"{src.name}.json")


def load_coverage(cache_dir, src):
    """{(x15, y15): bool} probed before for this source (and this URL), else {}."""
    try:
        with open(coverage_path(cache_dir, src), encoding="utf-8") as f:
            d = json.load(f)
        if d.get("url") != src.url:
            return {}
        return {tuple(int(v) for v in k.split(",")): bool(v) for k, v in d.get("z15", {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def save_coverage(cache_dir, src, cov):
    p = coverage_path(cache_dir, src)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"url": src.url, "z15": {f"{x},{y}": v for (x, y), v in sorted(cov.items())}}, f)
    os.replace(tmp, p)


def probe_coverage(net, cache_dir, region, chain, skip=(), stop=None, write=True):
    """{source name: {(x15, y15): bool}} for the regional export sources whose box meets the region: what is not
    known yet is probed (PROBE_PX px exports of the z13 tiles, the host's own rate). Unknown parents (a failed
    probe) stay out (by the box)."""
    from PIL import Image
    srcs = [s for s in export_sources(chain, skip) if s.bounds]
    z13 = region.tiles(PROBE_Z)
    out = {}

    def one(src):
        cov = load_coverage(cache_dir, src)
        todo = [(x, y) for x, y in z13 if imagerymod.in_bounds(src, PROBE_Z, x, y)
                and (x << 2, y << 2) not in cov]
        if not todo:
            return src, cov, 0, 0
        failed = 0
        lock = threading.Lock()

        def probe(t):
            nonlocal failed
            if stop is not None and stop.is_set():
                return
            x, y = t
            mb = imagerymod.mercator_bbox(PROBE_Z, x, y)
            url = src.url.format(bbox=",".join(f"{v:.3f}" for v in mb), bbox4326=imagerymod.lat_lon_bbox(mb),
                                 size=PROBE_PX)
            try:
                r = net.get(url, kind="demand")
                rows = classify_probe(np.asarray(Image.open(io.BytesIO(r.body)).convert("RGB")))
            except netfetch.NotFound:
                rows = [[False] * 4 for _ in range(4)]
            except Exception:
                with lock:
                    failed += 1
                return
            with lock:
                for j in range(4):
                    for i in range(4):
                        cov[((x << 2) + i, (y << 2) + j)] = rows[j][i]

        conc = max(1, net.policy(netfetch.host_of(src.url)).max_conc or 1)
        with ThreadPoolExecutor(conc) as ex:
            list(ex.map(probe, todo))
        if write:
            save_coverage(cache_dir, src, cov)
        return src, cov, len(todo), failed

    with ThreadPoolExecutor(max(1, len(srcs))) as ex:
        for src, cov, n, failed in ex.map(one, srcs):
            out[src.name] = cov
            if n:
                yes = sum(cov.values())
                print(f"  coverage {src.name}: probed {n} z13 tiles ({failed} failed): data in {yes} of {len(cov)} "
                      "z15 parents", flush=True)
    return out


# --- the manifest ----------------------------------------------------------------------------------------------------
class Manifest:
    """The keys a region's pre-load has finished (fetched, already on disk or known missing), one per line, appended as
    they finish: a run cut short resumes without asking them again."""

    def __init__(self, path, read=True):
        self.path = path
        self.done = set()
        self._lock = threading.Lock()
        self._f = None
        if read and path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.done = {ln.strip() for ln in f if ln.strip()}

    def __contains__(self, token):
        return token in self.done

    def add(self, token):
        if self.path is None:
            self.done.add(token)
            return
        with self._lock:
            if token in self.done:
                return
            self.done.add(token)
            if self._f is None:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                self._f = open(self.path, "a", encoding="utf-8")
            self._f.write(token + "\n")
            self._f.flush()

    def close(self):
        with self._lock:
            if self._f is not None:
                self._f.close()
                self._f = None


def tok(kind, *key):
    return f"{kind} " + "/".join(str(v) for v in key)


# --- what is on disk -------------------------------------------------------------------------------------------------
def export_state(img, src, key):
    """(done, gaps) of an export parent at src: done when its last child (written last) is on disk or known missing;
    gaps when any child is known missing (past the source's edge: the next source fills in)."""
    z, x, y = key
    last = (1 << src.export) - 1
    path, miss = img._paths(src, z, x | last, y | last)
    if not (os.path.exists(path) or os.path.exists(miss)):
        return False, False
    for j in range(1 << src.export):
        for i in range(1 << src.export):
            if os.path.exists(img._paths(src, z, x + i, y + j)[1]):
                return True, True
    return True, False


# --- the run ---------------------------------------------------------------------------------------------------------
class Lane:
    """One host's queue of work (a few threads, nearest / most important first); work(item) -> True (fetched), False
    (nothing to do), or raises."""

    def __init__(self, name, host, workers, work, run):
        self.name, self.host, self.workers, self.work, self.run = name, host, max(1, workers), work, run
        self.q = queue.PriorityQueue()
        self.total = self.done = self.fetched = self.failed = self.deferred = 0
        self.paused = ""
        self.errors = []

    def put(self, prio, item):
        with self.run.lock:
            self.total += 1
            self.run.pending += 1
            self.run.seq += 1
            seq = self.run.seq
        self.q.put((prio, seq, item))

    def loop(self):
        r = self.run
        while not r.stop.is_set():
            try:
                prio, _, item = self.q.get(timeout=0.3)
            except queue.Empty:
                if r.finished():
                    return
                continue
            fetched = failed = deferred = False
            try:
                if self.paused:
                    deferred = True
                else:
                    res = self.work(item, prio)
                    deferred = res == "deferred"
                    fetched = res is True
            except Exception as e:  # noqa: BLE001 (one key's failure: counted, the run goes on)
                failed = True
                with r.lock:
                    if len(self.errors) < 3:
                        self.errors.append(f"{type(e).__name__}: {e}"[:200])
                        print(f"    {self.name}: {self.errors[-1]}", flush=True)
            with r.lock:
                self.done += 1
                self.fetched += fetched
                self.failed += failed
                self.deferred += deferred
                r.pending -= 1

    def status(self):
        s = f"{self.name} {self.done}/{self.total}"
        extra = [f"{self.fetched} new"] if self.fetched else []
        if self.failed:
            extra.append(f"{self.failed} failed")
        if self.deferred:
            extra.append(f"{self.deferred} for tomorrow")
        return s + (f" ({', '.join(extra)})" if extra else "") + (f" [{self.paused}]" if self.paused else "")


class Run:
    def __init__(self, net, over_budget=False, stop=None):
        self.net, self.over_budget = net, over_budget
        self.lock = threading.Lock()
        self.stop = stop if stop is not None else threading.Event()
        self.pending = 0
        self.seq = 0
        self.lanes = {}
        self.threads = []
        self.started = False

    def lane(self, name, host, workers, work):
        """The lane of that name (made the first time; one made while the run goes gets its threads at once)."""
        with self.lock:
            ln = self.lanes.get(name)
            if ln is None:
                ln = self.lanes[name] = Lane(name, host, workers, work, self)
                if self.started:
                    self._start(ln)
            return ln

    def _start(self, ln):
        for i in range(ln.workers):
            t = threading.Thread(target=ln.loop, name=f"preload-{ln.name}-{i}", daemon=True)
            self.threads.append(t)
            t.start()

    def finished(self):
        with self.lock:
            return self.pending == 0

    def budget_ok(self, lane):
        """False (and the lane paused for today) once its host is at its soft daily budget (or its hard one)."""
        if lane.paused:
            return False
        p = self.net.policy(lane.host)
        used = self.net.host(lane.host).day["requests"]
        if p.daily_hard is not None and used >= p.daily_hard:
            lane.paused = f"daily limit {p.daily_hard} reached: resume tomorrow"
        elif p.daily_budget is not None and used >= p.daily_budget and not self.over_budget:
            lane.paused = f"daily budget {p.daily_budget} reached: resume tomorrow (or --over-budget)"
        if lane.paused:
            print(f"  {lane.name}: {lane.paused}", flush=True)
        return not lane.paused

    def go(self, every=10.0):
        t0 = last = time.monotonic()
        with self.lock:
            self.started = True
            for ln in self.lanes.values():
                self._start(ln)
        while any(t.is_alive() for t in list(self.threads)):
            time.sleep(0.5)
            now = time.monotonic()
            if now - last >= every:
                last = now
                self.print_status(now - t0)
        self.print_status(time.monotonic() - t0)

    def print_status(self, dt):
        with self.lock:
            busy = [ln.status() for ln in self.lanes.values() if ln.total]
        print(f"  [{dt / 60:5.1f} min] " + "; ".join(busy), flush=True)


def run_all(net, img, dem, md, plan, manifest, only, over_budget=False, stop=None):
    """Fetch the plan: every host's lane at once. Returns the Run (its lanes' counts)."""
    run = Run(net, over_budget, stop)
    if "dem" in only:
        host = netfetch.host_of(demmod.MAPTERHORN["url"])

        def dem_one(k, prio):
            t = tok("dem", *k)
            if t in manifest:
                return False
            if dem.has(*k):
                manifest.add(t)
                return False
            if not run.budget_ok(run.lanes["dem"]):
                return "deferred"
            ok = dem._download(demmod.MAPTERHORN, *k, kind="demand") is not None
            manifest.add(t)
            return ok
        ln = run.lane("dem", host, WORKERS["dem"], dem_one)
        for k, prio in plan["dem"]:
            if tok("dem", *k) not in manifest:
                ln.put(prio, k)
    if "imagery" in only:
        for src, keys in plan["xyz"]:
            host = netfetch.host_of(src.url)

            def xyz_one(k, prio, name=src.name):
                t = tok("xyz", k[0].name, *k[1:])
                if t in manifest:
                    return False
                path, miss = img._paths(*k)
                if os.path.exists(path) or os.path.exists(miss):
                    manifest.add(t)
                    return False
                if not run.budget_ok(run.lanes[name]):
                    return "deferred"
                ok = img.raw(*k, kind="demand") is not None
                manifest.add(t)
                return ok
            ln = run.lane(src.name, host, WORKERS["xyz"], xyz_one)
            for k, prio in keys:
                if tok("xyz", k[0].name, *k[1:]) not in manifest:
                    ln.put(prio, k)
        export_lanes(run, img, plan["exports"], manifest)
    if md is not None and "mapdata" in only:
        def cell_one(key, prio):
            t = tok("cell", key.z, key.x, key.y)
            if t in manifest:
                return False
            todo = tuple(ly for ly in mapdatamod.ALL_LAYERS if not md.has(key, ly))
            if todo:
                md.cell(key, layers=todo, kind="demand")
            manifest.add(t)
            return bool(todo)
        ln = run.lane("map cells", "overture", WORKERS["cell"], cell_one)
        for key, prio in plan["cells"]:
            if tok("cell", key.z, key.x, key.y) not in manifest:
                ln.put(prio, key)
    run.go()
    return run


def export_lanes(run, img, exports, manifest):
    """One lane per export source; a parent starts at its first candidate source and moves to the next where that
    one has gaps (past its edge), fails (a national source: USDA's host down, then USGS's) or has nothing."""
    net = run.net

    def lane_of(src):
        host = netfetch.host_of(src.url)
        conc = max(1, net.policy(host).max_conc or 1)
        return run.lane(src.name, host, conc, work)

    def forward(item, prio):
        key, cands, i = item
        if i + 1 < len(cands):
            lane_of(cands[i + 1]).put(prio, (key, cands, i + 1))
            return
        manifest.add(tok("exp", *key))             # every candidate asked: what there is, is there

    def work(item, prio):
        key, cands, i = item
        src = cands[i]
        lane = run.lanes[src.name]
        done, gaps = export_state(img, src, key)
        if not done:
            if not net.available(lane.host):
                if not src.bounds:                  # a national source down: the next one
                    forward(item, prio)
                    return False
                raise netfetch.HostUnavailable(f"{lane.host} is not answering", host=lane.host)
            if not run.budget_ok(lane):
                return "deferred"
            z, x, y = key
            last = (1 << src.export) - 1
            img._local.kind = "demand"
            try:
                img._download(src, z, x | last, y | last)
            except imagerymod.ImageryError:
                if not src.bounds:
                    forward(item, prio)
                    return False
                raise
            done, gaps = export_state(img, src, key)
            fetched = True
        else:
            fetched = False
        if gaps or not done:
            forward(item, prio)
        else:
            manifest.add(tok("exp", *key))
        return fetched

    for s in {s.name: s for _, _, cands in exports for s in cands}.values():
        lane_of(s)                                  # every candidate's lane (and threads) from the start
    for key, prio, cands in exports:
        if tok("exp", *key) in manifest:
            continue
        lane_of(cands[0]).put(prio, (key, cands, 0))


# --- the estimate ----------------------------------------------------------------------------------------------------
def host_rate(net, host, workers, sec):
    """Requests a second a lane gets: its workers (at most the host's max_conc) over the seconds per request, within
    the host's rate."""
    p = net.policy(host)
    conc = min(workers, p.max_conc or workers)
    return min(p.rate or 1e9, conc / sec)


def estimate(region, net, img, dem, md, plan, manifest, only, release):
    """Rows of the estimate (and prints them): per lane needed / to fetch, MB down / written, hours, days of budget."""
    rows = []

    def row(name, host, needed, todo, est, workers):
        down, wrote, sec = est
        rate = host_rate(net, host, workers, sec)
        p = net.policy(host)
        hours = todo / rate / 3600 if todo else 0.0
        budget = p.daily_hard if p.daily_budget is None else p.daily_budget
        days = math.ceil(todo / budget) if (budget and todo) else (1 if todo else 0)
        rows.append(dict(name=name, host=host, needed=needed, todo=todo, down=todo * down, wrote=todo * wrote,
                         hours=hours, days=days, budget=budget, rate=rate))

    if "dem" in only:
        todo = [k for k, _ in plan["dem"] if tok("dem", *k) not in manifest and not dem.has(*k)]
        row("DEM (Mapterhorn)", "tiles.mapterhorn.com", len(plan["dem"]), len(todo), EST["dem"], WORKERS["dem"])
    if "imagery" in only:
        for src, keys in plan["xyz"]:
            todo = [k for k, _ in keys if tok("xyz", src.name, *k[1:]) not in manifest
                    and not any(os.path.exists(p) for p in img._paths(*k))]
            row(f"{src.name} z12-z16 (XYZ)", netfetch.host_of(src.url), len(keys), len(todo), EST["xyz"],
                WORKERS["xyz"])
        per = {}
        for key, prio, cands in plan["exports"]:
            # a parent no regional photo covers: NAIP, from USDA's host else USGS's (counted at the slower one)
            src = cands[0] if cands[0].bounds else cands[-1]
            d = per.setdefault(src.name, {"src": src, "n": 0, "todo": 0, "z": set()})
            d["n"] += 1
            d["z"].add(key[0])
            if tok("exp", *key) not in manifest and not export_state(img, src, key)[0]:
                d["todo"] += 1
        for name, d in per.items():
            host = netfetch.host_of(d["src"].url)
            slow = host in SLOW_HOSTS
            row(f"{name} z{'/z'.join(map(str, sorted(d['z'])))}" + (" (USDA, else USGS: no regional photo)" if not d["src"].bounds
                                                                     else ""),
                host, d["n"], d["todo"], EST["export_slow" if slow else "export"],
                max(1, net.policy(host).max_conc or 1))
    if md is not None and "mapdata" in only:
        todo = [c for c, _ in plan["cells"] if tok("cell", c.z, c.x, c.y) not in manifest
                and not all(md.has(c, ly) for ly in mapdatamod.ALL_LAYERS)]
        row(f"map cells z14 (Overture {release})", "overturemaps-us-west-2.s3.us-west-2.amazonaws.com",
            len(plan["cells"]), len(todo), EST["cell"], WORKERS["cell"])

    print(f"  {'':40s} {'needed':>7s} {'to fetch':>8s} {'MB down':>8s} {'MB disk':>8s} {'hours':>6s} {'req/s':>6s} "
          f"{'budget/day':>10s} {'days':>4s}")
    for r in rows:
        print(f"  {r['name'][:40]:40s} {r['needed']:7d} {r['todo']:8d} {r['down'] / 1e6:8.0f} {r['wrote'] / 1e6:8.0f} "
              f"{r['hours']:6.1f} {r['rate']:6.1f} {r['budget'] if r['budget'] else '-':>10} {r['days']:4d}")
    down = sum(r["down"] for r in rows)
    wrote = sum(r["wrote"] for r in rows)
    # every host at once: the slowest lane sets the time; a lane past its budget waits for the next UTC day
    longest = max((r["hours"] for r in rows), default=0.0)
    days = max((r["days"] for r in rows), default=0)
    total_req = sum(r["todo"] for r in rows)
    print(f"  {'total':40s} {sum(r['needed'] for r in rows):7d} {total_req:8d} {down / 1e6:8.0f} {wrote / 1e6:8.0f} "
          f"{longest:6.1f}   (hosts in parallel: the slowest sets the time)")
    slow = max(rows, key=lambda r: r["hours"]) if rows else None
    if slow and slow["hours"] > 0:
        print(f"  slowest: {slow['name']} at {slow['rate']:.1f} requests/s ({slow['host']})")
    if days > 1:
        late = [r["name"] for r in rows if r["days"] > 1]
        print(f"  ! {days} days: past a host's daily budget its lane stops until the next UTC day ({', '.join(late)});"
              " run it again each day (or --over-budget: on at half rate)")
    return dict(rows=rows, down=down, wrote=wrote, hours=longest, days=days, requests=total_req)


def check_cap(cache_dir, cap_gb, wrote, dry_run):
    """Print whether the cache (what is there plus what this run writes) fits the cap; True when it does."""
    c = cachemod.Cache(cache_dir, cap_gb, start=False)
    used = sum(v[0] or 0 for v in c._entries.values())     # the sizes in its index (no walk)
    cap = c.cap
    need = used + wrote
    if not cap:
        print(f"  cache: {used / GB:.1f} GB used, unlimited cap; after this run ~{need / GB:.1f} GB")
        return True
    print(f"  cache: {used / GB:.1f} GB used of a {cap / GB:.0f} GB cap; after this run ~{need / GB:.1f} GB")
    if need > 0.9 * cap:
        want = math.ceil(need / 0.85 / GB / 5) * 5
        hint = (f"raise the cap to {want} GB or more (the mod's settings page: 5-50 GB; --cache-cap {want} here)"
                if want <= 50 else f"it needs ~{want} GB, past the settings page's 50 GB: start the server with "
                                    f"--cache-cap {want}, or pre-load a smaller region")
        print(f"  ! does not fit: the least recently used files would go again; {hint}", flush=True)
        return False
    return True


# --- main ------------------------------------------------------------------------------------------------------------
def list_regions(data):
    print("Named regions (tools/regions.json):")
    for n in regmod.names(data):
        try:
            r = regmod.region(data, n)
        except regmod.RegionError as e:
            print(f"  {n:24s} (bad: {e})")
            continue
        kinds = sorted({p.kind for p in r.parts})
        print(f"  {n:24s} {len(r.parts):3d} part{'s' if len(r.parts) != 1 else ' '} ~{r.area_km2():7.0f} km2  "
              f"{'+'.join(kinds):24s} {r.title}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    where = ap.add_mutually_exclusive_group()
    where.add_argument("--region", help="a named region of the regions file (--list)")
    where.add_argument("--place", help="a place name (geocoded with Photon), with --radius-km")
    where.add_argument("--route", nargs=2, metavar=("FROM", "TO"), help="two places: the road route between them "
                                                                        "(OSRM), with --width-km")
    where.add_argument("--center", help="lat,lon: a disc with --radius-km (default: Frederick, CO)")
    ap.add_argument("--list", action="store_true", help="list the named regions")
    ap.add_argument("--regions", default=regmod.DEFAULT_FILE, help="the regions file")
    ap.add_argument("--radius-km", type=float, default=20.0, help="--place / --center: the disc")
    ap.add_argument("--z18-km", type=float, default=None,
                    help="--place / --center: the regional sources' z18 photos over this (default: the whole disc)")
    ap.add_argument("--width-km", type=float, default=10.0, help="--route: the corridor's whole width")
    ap.add_argument("--tier", default=None, help="--route's tier (default road: z17), --place's z18 part's")
    ap.add_argument("--far-km", type=float, default=None, help="the coarse DEM and USGS rings round the area "
                                                               "(default: the region's, else 40; a disc's 80)")
    ap.add_argument("--only", default="dem,imagery,mapdata", help="comma-separated: dem, imagery, mapdata")
    ap.add_argument("--skip", default="", help="imagery sources to leave out, comma-separated (e.g. naip,naipusda)")
    ap.add_argument("--release", default=overture.RELEASE, help="Overture release (default: the pinned one)")
    ap.add_argument("--cache-dir", default=None, help="the download cache (default: the server's)")
    ap.add_argument("--cache-cap", type=float, default=None, metavar="GB",
                    help="the cache's cap (default: the one the mod set, else cache.DEFAULT_CAP_GB; 0 = unlimited)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and its size, fetch nothing (but the "
                                                           "coverage probe, a few hundred small images)")
    ap.add_argument("--no-probe", action="store_true", help="no coverage probe: each regional source by its box")
    ap.add_argument("--over-budget", action="store_true", help="past a host's soft daily budget: on at half rate")
    ap.add_argument("--recheck", action="store_true", help="ignore the manifest (check every key on disk)")
    ap.add_argument("--allow-running", action="store_true",
                    help="run even while a world server listens on its port (its cache index may lose this run's)")
    a = ap.parse_args(argv)
    data = regmod.load(a.regions)
    if a.list:
        list_regions(data)
        return 0
    only = {s.strip() for s in a.only.split(",") if s.strip()}
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    cache_dir = os.path.abspath(a.cache_dir or default_cache_dir())
    if not a.dry_run and not a.allow_running and server_listening() \
            and os.path.normcase(cache_dir) == os.path.normcase(os.path.abspath(default_cache_dir())):
        print(f"A world server is listening on 127.0.0.1:{SERVER_PORT} and uses {cache_dir}: close the game and the "
              "server first (or --cache-dir elsewhere, or --allow-running).", file=sys.stderr)
        return 2

    # (a dry run keeps out of the day's request counts: a running server saves its own over them)
    net = netfetch.Fetcher(stats_dir=None if a.dry_run else os.path.join(cache_dir, "netstats"))
    if a.region:
        region = regmod.region(data, a.region)
    elif a.place:
        la, lo = geocode_one(net, a.place)
        region = disc_region(la, lo, a.radius_km, a.z18_km, a.far_km or 40.0)
        region.name = f"place-{slug(a.place)}-{a.radius_km:g}km"
    elif a.route:
        region = route_region(net, *a.route, a.width_km, a.tier or "road", a.far_km or 40.0)
    else:
        lat, lon = (float(v) for v in (a.center or f"{FREDERICK[0]},{FREDERICK[1]}").split(","))
        region = disc_region(lat, lon, a.radius_km, a.z18_km, a.far_km or 80.0)
    if a.far_km is not None:
        region.far_m = a.far_km * 1000.0

    disk = None if a.dry_run else cachemod.Cache(cache_dir, a.cache_cap)
    dem = demmod.Dem(os.path.join(cache_dir, "dem"), fetcher=net)
    img = imagerymod.Imagery(os.path.join(cache_dir, "imagery"), imagerymod.DEFAULT_CHAIN, fetcher=net)
    md = mapdatamod.MapData(cache_dir, fetcher=net, release=a.release) if "mapdata" in only else None
    if disk is not None:
        dem.disk = img.disk = disk
    manifest = Manifest(None if a.dry_run else os.path.join(cache_dir, "preload", f"{slug(region.name)}.done"),
                        read=not a.recheck)
    if a.dry_run and not a.recheck:
        manifest.done = Manifest(os.path.join(cache_dir, "preload", f"{slug(region.name)}.done")).done

    s, w, n, e = region.bounds
    print(f"Pre-load {region.name}{': ' + region.title if region.title else ''}\n  {len(region.parts)} parts, "
          f"~{region.area_km2():.0f} km2 (overlaps counted twice), {s:.2f}..{n:.2f} N {w:.2f}..{e:.2f} E, coarse "
          f"rings {region.far_m / 1000:g} km round it, into {cache_dir}"
          + (f"; manifest: {len(manifest.done)} keys done" if manifest.done else ""))
    stop = threading.Event()
    chain = [imagerymod.SOURCES[nm] for nm in imagerymod.DEFAULT_CHAIN]
    coverage = None
    if "imagery" in only and not a.no_probe:
        coverage = probe_coverage(net, cache_dir, region, chain, skip, stop)
    plan = {
        "dem": plan_dem(region) if "dem" in only else [],
        "xyz": plan_xyz(region, skip=skip) if "imagery" in only else [],
        "exports": plan_exports(region, chain, coverage, skip) if "imagery" in only else [],
        "cells": plan_cells(region) if md is not None else [],
    }
    est = estimate(region, net, img, dem, md, plan, manifest, only, a.release)
    check_cap(cache_dir, a.cache_cap, est["wrote"], a.dry_run)
    if a.dry_run:
        net.close()
        if md is not None:
            md.close()
        return 0

    def on_int(*_):
        print("\nstopping (what is fetched stays; run it again to resume)...", flush=True)
        stop.set()
    signal.signal(signal.SIGINT, on_int)
    t0 = time.monotonic()
    run = None
    try:
        run = run_all(net, img, dem, md, plan, manifest, only, a.over_budget, stop)
    finally:
        manifest.close()
        if md is not None:
            md.close()
        net.close()
        disk.close()
    left = sum(ln.total - ln.done + ln.deferred + ln.failed for ln in run.lanes.values()) if run else 0
    print(f"done in {(time.monotonic() - t0) / 60:.1f} min{' (stopped)' if stop.is_set() else ''}"
          + (f"; {left} keys left (failed, stopped or past a budget): run it again to resume" if left else
             "; everything there"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
