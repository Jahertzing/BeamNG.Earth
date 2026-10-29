#!/usr/bin/env python3
"""How a car rides the whole drive (I-25 from Frederick through Denver to Castle Rock, 2026-10-29), offline from the
cached data: tools/ride_probe.py's drivable surface (graded ground, solved decks) of every motorway and trunk part in
discs every SPACING_M along the route, and the worst vertical jolts a car at 65 mph feels on them, ranked.

Each spot is a part's worst point over SPIKE_G (one per part and disc, then one per SPOT_M), with what it is on (a
deck, the ground road, or the ground road within JOINT_M of a deck's end) and the part's name.

    python tools/ride_survey.py [--jobs 4] [--from-km 0 --to-km 90] [--out ride_survey.json] [--online]
"""
import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

os.environ.setdefault("BNE_REVISIT", "off")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import numpy as np  # noqa: E402

# I-25 north to south (lat, lon): Frederick / Firestone, E-470, US-36, the Mousetrap, Speer, 6th Ave, Broadway,
# Colorado Blvd, I-225, C-470, Lone Tree, Castle Pines, Castle Rock
ROUTE = [(40.1230, -104.9840), (40.0600, -104.9850), (39.9822, -104.9890), (39.9000, -104.9880), (39.8180, -104.9860),
         (39.7800, -104.9894), (39.7600, -104.9990), (39.7373, -105.0143), (39.7256, -105.0005),
         (39.7050, -104.9880), (39.6800, -104.9420), (39.6600, -104.9100), (39.6412, -104.8985),
         (39.6000, -104.8820), (39.5620, -104.8735), (39.5200, -104.8700), (39.4600, -104.8800),
         (39.4100, -104.8650), (39.3700, -104.8600)]
SPACING_M = 1200.0
RADIUS_M = 800.0
SPIKE_G = 0.6
SPOT_M = 25.0
JOINT_M = 30.0


def _metres(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((b[0] - a[0]) * 111320.0, (b[1] - a[1]) * 111320.0 * k)


def discs(route=ROUTE, spacing=SPACING_M):
    """[(km along the route, lat, lon)] every spacing m along the route's line."""
    seg = [_metres(a, b) for a, b in zip(route[:-1], route[1:])]
    s = np.r_[0.0, np.cumsum(seg)]
    out = []
    for d in np.arange(0.0, s[-1] + 1e-6, spacing):
        i = min(int(np.searchsorted(s, d, side="right")) - 1, len(seg) - 1)
        t = (d - s[i]) / max(seg[i], 1e-9)
        a, b = route[i], route[i + 1]
        out.append((d / 1000.0, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def survey_disc(args):
    """The spikes [(g, label, kind, cls, lat, lon, near a deck end)] of the motorway parts of one disc."""
    km, la, lo, radius, cache_dir, online = args
    import ride_probe as rp
    import geodesy
    if online:                          # (ride_probe reads the cache only: fetch what is missing)
        import netfetch
        if not getattr(netfetch.Fetcher, "_survey", False):
            base = netfetch.Fetcher

            class Online(base):
                _survey = True

                def __init__(self, *a, **kw):
                    kw.pop("offline", None)
                    super().__init__(*a, **kw)
            netfetch.Fetcher = Online
    try:
        lines = rp.lines_of(f"km{km:.1f}", la, lo, radius, cache_dir)
    except Exception as ex:             # (a cell that will not load: the disc is reported as such)
        return km, None, f"{type(ex).__name__}: {ex}"
    frame = geodesy.EnuFrame(la, lo)
    ends = np.array([p for ln in lines if ln[1] == "deck" for p in (ln[3][0], ln[3][-1])]).reshape(-1, 2)
    out = []
    for label, kind, cls, xy, z in lines:
        if kind == "deck":
            xy, z = rp.resample_line(xy, z)
            if xy is None:
                continue
        a = np.abs(rp.accel(z))
        inside = np.hypot(*xy.T) <= radius
        a = np.where(inside, a, np.nan)
        if not np.isfinite(a).any() or np.nanmax(a) < SPIKE_G:
            continue
        i = int(np.nanargmax(a))
        joint = bool(len(ends) and np.hypot(*(ends - xy[i]).T).min() <= JOINT_M)
        p_la, p_lo, _ = frame.enu_to_geodetic(xy[i, 0], xy[i, 1], 0.0)
        out.append((float(a[i]), label, kind, cls, float(p_la), float(p_lo), joint))
    return km, out, None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/beamng-earth"))
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--from-km", type=float, default=0.0)
    ap.add_argument("--to-km", type=float, default=1e9)
    ap.add_argument("--out", default="ride_survey.json")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--online", action="store_true", help="fetch the data missing from the cache")
    a = ap.parse_args()
    import apppaths
    import cache
    cache.set_world(apppaths.saved_world_dir() or apppaths.default_world_dir(), a.cache_dir)
    todo = [(km, la, lo, RADIUS_M, a.cache_dir, a.online) for km, la, lo in discs() if a.from_km <= km <= a.to_km]
    t0 = time.time()
    spots, failed = [], []
    # (a fresh process a disc: one downtown's caches held ~4 GB, and processes kept them from disc to disc)
    with ProcessPoolExecutor(a.jobs, max_tasks_per_child=1) as ex:
        for km, got, err in ex.map(survey_disc, todo):
            if got is None:
                failed.append((km, err))
                print(f"  km {km:5.1f}: failed: {err}", flush=True)
                continue
            spots += [(km,) + g for g in got]
            print(f"  km {km:5.1f}: {len(got)} spikes over {SPIKE_G} g ({time.time() - t0:.0f} s)", flush=True)
    spots.sort(key=lambda r: -r[1])
    kept = []
    for r in spots:                     # one per SPOT_M (the discs overlap)
        if all(_metres((r[5], r[6]), (k[5], k[6])) > SPOT_M for k in kept):
            kept.append(r)
    with open(a.out, "w") as f:
        json.dump({"spots": [dict(zip(("km", "g", "part", "kind", "cls", "lat", "lon", "joint"), r)) for r in kept],
                   "failed": failed, "discs": len(todo)}, f, indent=1)
    print(f"{len(todo)} discs, {len(kept)} spots over {SPIKE_G} g, {len(failed)} discs failed "
          f"({time.time() - t0:.0f} s); the worst:")
    for km, g, label, kind, cls, la, lo, joint in kept[:a.top]:
        print(f"  {g:6.2f} g  km {km:5.1f}  {label:40s} {cls:9s} {'deck end' if joint else '':8s} {la:.6f},{lo:.6f}")


if __name__ == "__main__":
    main()
