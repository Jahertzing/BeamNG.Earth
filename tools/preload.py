#!/usr/bin/env python3
"""Pre-load a region into the download cache: the elevation tiles, the imagery and the map data of a disc round a
place, fetched by the server's own code (dem.Dem, imagery.Imagery, mapdata.MapData behind a netfetch.Fetcher with
the host policies and the daily budgets), so the world there loads from disk and at its sharpest: the lazy export
sources (a state's or a town's orthophotos, NAIP) are otherwise only fetched a few hundred metres ahead of the player.

  python3 tools/preload.py --dry-run                         the plan and its size (Frederick, CO, 20 km)
  python3 tools/preload.py --center 40.0992,-104.9372 --radius-km 20
  python3 tools/preload.py --radius-km 5 --only dem,imagery  a smaller disc, no map data
  python3 tools/preload.py --skip naip                       without USGS's slow NAIP service

What it fetches (each nearest first; what is on disk already, or known missing, is skipped, so a run cut short
resumes where it stopped):
  dem      Mapterhorn z16 (the near ground and the physics block: dem.FINE_ZOOM) over --fine-km with the z12 tiles
           the ground filter reads, z15-z13 over --radius-km, z12-z11 over --far-km, z10 over twice that
  imagery  the chain's lazy export sources (co_frederick, NAIP...) at z17 over --fine-km and, for a source finer than
           that (co_frederick: z18), at its finest zoom over --z18-km; a source is asked only where every earlier
           lazy source of the chain has nothing (as imagery.warm does); USGS z16-z15 over --radius-km, z14-z12 over
           --far-km
  mapdata  the z14 cells over --radius-km, every layer (mapdata.ALL_LAYERS), for the pinned Overture release

Run it with the game and the world server closed: the server keeps its own index of the cache (cache.py
index.tsv) and the last process to save it wins. It refuses to run on the server's cache while something
listens on the server's port (--allow-running: anyway). The cache cap is the one the mod set (--cache-cap to
change it); the files this run writes count against it, the least recently used others go when it is full.
"""
import argparse
import math
import os
import signal
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.append(ROOT)
import cache as cachemod  # noqa: E402
import dem as demmod  # noqa: E402
import imagery as imagerymod  # noqa: E402
import mapcells  # noqa: E402
import mapdata as mapdatamod  # noqa: E402
import netfetch  # noqa: E402
import overture  # noqa: E402

FREDERICK = (40.0992, -104.9372)
SERVER_PORT = 47613             # earthd --port's default
EARTH_CIRC = 40075016.686
# Rough sizes and times for the estimate (Frederick, CO, 2026-10-27): bytes written to the cache per item, and seconds
# per request at one at a time (the host policies' max_conc runs that many at once)
EST = {
    "dem": (55e3, 0.15),
    "export": (16 * 24e3, 0.4),        # 16 child JPEGs (quality 92) per 1024 px parent (the town's: 370 KB); ~0.2-0.4 s
    "export_slow": (16 * 45e3, 6.0),   # USGS's NAIP exportImage (summer, busier JPEGs): 2.7-10 s
    "xyz": (30e3, 0.1),
    "cell": (650e3, 4.0),              # all layers of a cell (a few MB of Overture range reads the first time)
}
SLOW_HOSTS = {"imagery.nationalmap.gov"}
WORKERS = {"dem": 16, "xyz": 8, "cell": 4}


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


# --- the plan (no network) ---------------------------------------------------------------------------------------
def _tile_m(lat, z):
    return EARTH_CIRC * math.cos(math.radians(lat)) / 2 ** z


def tiles_in_disc(lat, lon, radius_m, z):
    """(x, y) of the zoom-z tiles that reach within radius_m of lat/lon, nearest first."""
    if radius_m <= 0:
        return []
    n = 2 ** z
    cx, cy = (float(v) for v in mapcells.tile_xy(lat, lon, z))
    rt = radius_m / _tile_m(lat, z) + 0.71          # + half a tile's diagonal: tiles that reach in
    out = []
    for y in range(max(0, int(cy - rt) - 1), min(n - 1, int(cy + rt) + 1) + 1):
        for x in range(int(cx - rt) - 1, int(cx + rt) + 2):
            d2 = (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2
            if d2 <= rt * rt:
                out.append((d2, x % n, y))
    out.sort()
    return [(x, y) for _, x, y in out]


def plan_dem(lat, lon, fine_m, radius_m, far_m, fine_zoom=None):
    """DEM keys (z, x, y), nearest first per ring: the fine zoom with its z12 ground-filter parents, then coarser."""
    fz = fine_zoom if fine_zoom is not None else demmod.zoom_for(lat, 1.25, demmod.FINE_ZOOM)   # earthd's hm_zoom
    rings = [(fz, fine_m)] + [(z, radius_m) for z in range(fz - 1, 12, -1)] + [(12, far_m), (11, far_m), (10, 2 * far_m)]
    keys = {}
    for z, r in rings:
        for x, y in tiles_in_disc(lat, lon, r, z):
            if demmod.Dem.ground_filter and z > demmod.GLOBAL_ZOOM:
                sh = z - demmod.GLOBAL_ZOOM
                keys.setdefault((demmod.GLOBAL_ZOOM, x >> sh, y >> sh), None)
            keys.setdefault((z, x, y), None)
    return list(keys)


def plan_exports(lat, lon, chain, fine_m, z18_m, skip=()):
    """[(source, keys)] of the chain's lazy export sources, in chain order: a key is (source, z, x, y) of a parent's
    first child (as imagery.warm makes them) at the base photo's zoom (17) over fine_m and at the source's finest zoom
    over z18_m when that is finer."""
    out = []
    for src in chain:
        if not src.lazy or src.name in skip:
            continue
        k = src.export
        keys = []
        zooms = [(min(17, src.max_zoom), fine_m)]
        if src.max_zoom > 17:
            zooms.append((src.max_zoom, z18_m))
        for z, r in zooms:
            if z < src.min_zoom:
                continue
            for px, py in tiles_in_disc(lat, lon, r, z - k):        # the parents (one request each)
                key = (src, z, px << k, py << k)
                if imagerymod.in_bounds(*key):
                    keys.append(key)
        if keys:
            out.append((src, keys))
    return out


def plan_xyz(lat, lon, radius_m, far_m, names=("usgs",), skip=()):
    out = []
    for name in names:
        if name in skip:
            continue
        src = imagerymod.SOURCES[name]
        keys = []
        for z, r in ((16, radius_m), (15, radius_m), (14, far_m), (13, far_m), (12, far_m)):
            if src.min_zoom <= z <= src.max_zoom:
                keys += [(src, z, x, y) for x, y in tiles_in_disc(lat, lon, r, z) if imagerymod.in_bounds(src, z, x, y)]
        out.append((src, keys))
    return out


def plan_cells(lat, lon, radius_m):
    return [mapcells.CellKey(mapcells.ZOOM, x, y) for x, y in tiles_in_disc(lat, lon, radius_m, mapcells.ZOOM)]


def in_box(bounds, key):
    """The export parent's centre inside (south, west, north, east)."""
    src, z, x, y = key
    k = 1 << src.export
    la, lo = mapcells.CellKey(z - src.export, x // k, y // k).center()
    s_, w_, n_, e_ = bounds
    return s_ <= la <= n_ and w_ <= lo <= e_


def export_on_disk(img, key):
    """An export parent's last child (the one written last) on disk or known missing: no network, no coverage probe
    (the dry run's count; the run itself asks imagery's _warm_done, which also passes a parent an earlier lazy source
    already covers)."""
    src, z, x, y = key
    last = (1 << src.export) - 1
    path, miss = img._paths(src, z, x | last, y | last)
    return os.path.exists(path) or os.path.exists(miss)


# --- the run -----------------------------------------------------------------------------------------------------
class Progress:
    def __init__(self, name, total):
        self.name, self.total = name, total
        self.done = self.fetched = self.failed = 0
        self.t0 = self.last = time.monotonic()
        self.lock = threading.Lock()

    def step(self, fetched=False, failed=False):
        with self.lock:
            self.done += 1
            self.fetched += bool(fetched)
            self.failed += bool(failed)
            now = time.monotonic()
            if now - self.last >= 5.0 or self.done == self.total:
                self.last = now
                self.line(now)

    def line(self, now=None):
        dt = (now or time.monotonic()) - self.t0
        rate = self.done / dt if dt > 0 else 0.0
        eta = (self.total - self.done) / rate if rate > 0 else float("nan")
        print(f"  {self.name}: {self.done}/{self.total} ({self.fetched} fetched, {self.failed} failed) "
              f"{dt:5.0f} s, ETA {eta:5.0f} s", flush=True)


def run_pool(name, items, work, workers, stop):
    """work(item) -> True (fetched), False (nothing to do) or raises (failed); items nearest first."""
    if not items:
        return
    p = Progress(name, len(items))
    with ThreadPoolExecutor(workers, thread_name_prefix=f"preload-{name}") as ex:
        futs, it = set(), iter(items)
        while True:
            while len(futs) < workers * 2 and not stop.is_set():
                item = next(it, None)
                if item is None:
                    break
                futs.add(ex.submit(work, item))
            if not futs:
                break
            done = next(as_completed(futs))
            futs.discard(done)
            try:
                p.step(fetched=bool(done.result()))
            except Exception as e:
                p.step(failed=True)
                if p.failed <= 3:
                    print(f"    {name}: {type(e).__name__}: {e}"[:200], flush=True)
    if p.done < p.total:            # (stopped: the last line was not printed)
        p.line()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--center", default=f"{FREDERICK[0]},{FREDERICK[1]}", help="lat,lon (default: Frederick, CO)")
    ap.add_argument("--radius-km", type=float, default=20.0, help="the disc: map data, middle DEM zooms, USGS")
    ap.add_argument("--fine-km", type=float, default=None,
                    help="the finest DEM zoom and the z17 photos over this (default: --radius-km)")
    ap.add_argument("--z18-km", type=float, default=None,
                    help="a source finer than z17 (co_frederick) at its finest zoom over this (default: --radius-km)")
    ap.add_argument("--far-km", type=float, default=80.0, help="the coarse DEM and USGS zooms over this")
    ap.add_argument("--only", default="dem,imagery,mapdata", help="comma-separated: dem, imagery, mapdata")
    ap.add_argument("--skip", default="", help="imagery sources to leave out, comma-separated (e.g. naip,naipusda)")
    ap.add_argument("--release", default=overture.RELEASE, help="Overture release (default: the pinned one)")
    ap.add_argument("--cache-dir", default=None, help="the download cache (default: the server's)")
    ap.add_argument("--cache-cap", type=float, default=None, metavar="GB",
                    help="the cache's cap (default: the one the mod set, else cache.DEFAULT_CAP_GB; 0 = unlimited)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and its size, fetch nothing")
    ap.add_argument("--allow-running", action="store_true",
                    help="run even while a world server listens on its port (its cache index may lose this run's)")
    a = ap.parse_args(argv)
    lat, lon = (float(v) for v in a.center.split(","))
    radius = a.radius_km * 1000.0
    fine = (a.fine_km if a.fine_km is not None else a.radius_km) * 1000.0
    z18 = (a.z18_km if a.z18_km is not None else a.radius_km) * 1000.0
    far = max(a.far_km * 1000.0, radius)
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
    disk = None if a.dry_run else cachemod.Cache(cache_dir, a.cache_cap)
    dem = demmod.Dem(os.path.join(cache_dir, "dem"), fetcher=net)
    img = imagerymod.Imagery(os.path.join(cache_dir, "imagery"), imagerymod.DEFAULT_CHAIN, fetcher=net)
    md = mapdatamod.MapData(cache_dir, fetcher=net, release=a.release) if "mapdata" in only else None
    if disk is not None:
        dem.disk = img.disk = disk

    # --- what is needed, and what of it is on disk already -----------------------------------------------------
    print(f"Pre-load {a.center}: {a.radius_km:g} km (fine {fine / 1000:g} km, z18 {z18 / 1000:g} km, far "
          f"{far / 1000:g} km) into {cache_dir}")
    rows, est_bytes, est_s = [], 0.0, 0.0
    dem_todo = exports = xyz = cells_todo = []
    if "dem" in only:
        keys = plan_dem(lat, lon, fine, radius, far)
        dem_todo = [k for k in keys if not dem.has(*k)]
        b, s = EST["dem"]
        rows.append(("DEM (Mapterhorn)", len(keys), len(dem_todo), len(dem_todo) * b, len(dem_todo) * s / WORKERS["dem"],
                     "sum"))
    if "imagery" in only:
        exports = plan_exports(lat, lon, img.chain, fine, z18, skip)
        boxes = []                          # the earlier regional sources' areas: a later source is not asked there
        for src, keys in exports:
            todo = [k for k in keys if not export_on_disk(img, k) and not any(in_box(b, k) for b in boxes)]
            host = netfetch.host_of(src.url)
            b, s = EST["export_slow" if host in SLOW_HOSTS else "export"]
            conc = net.policy(host).max_conc or 1
            zs = sorted({k[1] for k in keys})
            # a national source (NAIP) is the fallback of the one before it: only one of them ends up fetching
            rows.append((f"{src.name} z{'/z'.join(map(str, zs))} (1024 px exports)" + ("" if src.bounds else " *"),
                         len(keys), len(todo), len(todo) * b, len(todo) * s / conc, "sum" if src.bounds else "alt"))
            if src.bounds:
                boxes.append(src.bounds)
        xyz = plan_xyz(lat, lon, radius, far, skip=skip)
        for src, keys in xyz:
            todo = [k for k in keys if not os.path.exists(img._paths(*k)[0]) and not os.path.exists(img._paths(*k)[1])]
            b, s = EST["xyz"]
            rows.append((f"{src.name} z12-z16 (XYZ)", len(keys), len(todo), len(todo) * b, len(todo) * s / WORKERS["xyz"],
                         "sum"))
    if md is not None:
        cells = plan_cells(lat, lon, radius)
        layers = mapdatamod.ALL_LAYERS
        cells_todo = [c for c in cells if not all(md.has(c, ly) for ly in layers)]
        b, s = EST["cell"]
        rows.append((f"map cells z14 (Overture {a.release})", len(cells), len(cells_todo), len(cells_todo) * b,
                     len(cells_todo) * s / WORKERS["cell"], "sum"))
    print(f"  {'':44s} {'needed':>8s} {'to fetch':>9s} {'~MB':>8s} {'~min':>6s}")
    alt = []
    for name, n, todo, b, s, how in rows:
        if how == "sum":
            est_bytes += b
            est_s += s
        else:
            alt.append((s, b))
        print(f"  {name:44s} {n:8d} {todo:9d} {b / 1e6:8.0f} {s / 60:6.1f}")
    if alt:                                 # the slowest of the fallbacks (the first may be down: it fails at once)
        s, b = max(alt)
        est_s += s
        est_bytes += b
        print("  * fallbacks of each other (NAIP: USDA's service, then USGS's): the total counts the slowest; "
              "--skip naip leaves USGS's slow one out")
    print(f"  {'total (written to the cache; time if run alone)':44s} {'':8s} {'':9s} {est_bytes / 1e6:8.0f} "
          f"{est_s / 60:6.1f}")
    if disk is not None or a.dry_run:
        cap = cachemod.Cache(cache_dir, a.cache_cap, start=False) if a.dry_run else disk
        cap_b = cap._cap
        if cap_b and est_bytes > 0.9 * cap_b:
            print(f"  ! the estimate is near or over the cache cap ({cap_b / 1e9:.0f} GB): raise it (--cache-cap, or "
                  "the mod's settings page) or the oldest of this run's files go again", flush=True)
        elif cap_b:
            print(f"  cache cap {cap_b / 1e9:.0f} GB")
    if a.dry_run:
        net.close()
        if md is not None:
            md.close()
        return 0

    # --- fetch ---------------------------------------------------------------------------------------------------
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: (print("\nstopping (what is fetched stays)...", flush=True), stop.set()))
    t0 = time.monotonic()
    try:
        if dem_todo:
            def dem_one(k):
                if dem.has(*k):
                    return False
                return dem._download(demmod.MAPTERHORN, *k, kind="demand") is not None
            run_pool("DEM", dem_todo, dem_one, WORKERS["dem"], stop)
        for src, keys in xyz:
            def xyz_one(k):
                path, miss = img._paths(*k)
                if os.path.exists(path) or os.path.exists(miss):
                    return False
                return img.raw(*k, kind="demand") is not None
            run_pool(src.name, keys, xyz_one, WORKERS["xyz"], stop)
        for src, keys in exports:
            host = netfetch.host_of(src.url)

            def export_one(k, host=host):
                if img._warm_done(k):           # on disk, known missing, or an earlier source has it
                    return False
                if not net.available(host):     # its breaker open: the rest of this source is skipped
                    raise netfetch.HostUnavailable(f"{host} is not answering", host=host)
                src, z, x, y = k
                last = (1 << src.export) - 1
                img._local.kind = "demand"
                return img._download(src, z, x | last, y | last) is not None
            run_pool(src.name, keys, export_one, max(1, net.policy(host).max_conc or 1), stop)
        if md is not None and cells_todo:
            def cell_one(key):
                todo = tuple(ly for ly in mapdatamod.ALL_LAYERS if not md.has(key, ly))
                if not todo:
                    return False
                md.cell(key, layers=todo, kind="demand")
                return True
            run_pool("map cells", cells_todo, cell_one, WORKERS["cell"], stop)
    finally:
        if md is not None:
            md.close()
        net.close()
        disk.close()
    print(f"done in {(time.monotonic() - t0) / 60:.1f} min{' (stopped)' if stop.is_set() else ''}; "
          "run it again to fill in what failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
