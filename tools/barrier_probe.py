#!/usr/bin/env python3
"""Where the highway barriers (server/barriers.py) stand on real roads, from the cached map data (offline): builds the
road tiles round each place, prints what each tile got (kind, metres, how many mapped) and how long the barriers took,
and draws each place from above (roads grey, barriers by kind) as a PNG. Also counts OpenStreetMap's barrier lines
(Overture infrastructure, subtype barrier) by class in each place's map cells.

    python tools/barrier_probe.py --out C:\\beamng-earth\\barriers\\out              the I-25 places (PLACES)
    python tools/barrier_probe.py --at 39.9151,-104.9874 --tiles 2 --out /tmp/bar
"""
import argparse
import collections
import json
import os
import queue
import sys
import time

os.environ.setdefault("BNE_REVISIT", "off")         # (build them, do not keep them)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

# I-25 from Frederick through Denver to Castle Rock
PLACES = {
    "frederick_co52": (40.0899, -104.9856),
    "e470_nw_pkwy": (39.9960, -104.9860),
    "thornton_120th": (39.9151, -104.9874),
    "thornton_84th": (39.8505, -104.9873),
    "mousetrap_i70": (39.7781, -105.0119),
    "downtown_speer": (39.7444, -105.0166),
    "colorado_blvd": (39.6942, -104.9401),
    "dtc_belleview": (39.6244, -104.8967),
    "c470_lincoln": (39.5636, -104.8788),
    "castle_rock": (39.3900, -104.8660),
}
COLOURS = {"jersey": (230, 230, 225), "wbeam": (80, 200, 255), "cable": (255, 200, 0), "noise": (230, 120, 60)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--at", default=None, help="lat,lon (default: PLACES)")
    ap.add_argument("--tiles", type=int, default=1, help="road tiles from -n to n on each axis (256 m each)")
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/beamng-earth"))
    ap.add_argument("--out", default="barrier_probe")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    places = {"at": tuple(float(v) for v in a.at.split(","))} if a.at else PLACES

    import barriers
    import blockworker
    import dem as demmod
    import roads as roadsmod
    blockworker.init(a.cache_dir, True, True, True, True, True, queue.Queue(), 30, True, True, gas_stations=False)
    got = []            # the current tile's barriers (tile_barriers wrapped)
    secs = []
    real = barriers.tile_barriers

    def spy(*args, **kw):
        t0 = time.perf_counter()
        out = real(*args, **kw)
        secs.append(time.perf_counter() - t0)
        got.extend(out)
        return out
    barriers.tile_barriers = spy
    summary = {}
    for name, (lat, lon) in places.items():
        zoom = demmod.zoom_for(lat, 2.5)
        tdir = os.path.join(a.out, "tiles", name)
        os.makedirs(tdir, exist_ok=True)
        per_tile, all_b, all_roads, errors = {}, [], [], []
        frame = None
        for tx in range(-a.tiles, a.tiles + 1):
            for ty in range(-a.tiles, a.tiles + 1):
                got.clear()
                t0 = time.perf_counter()
                try:
                    blockworker.roads_tile(1, lat, lon, zoom, tx, ty, tdir, "/temp/x", True, 0.0, True, False, True,
                                           None, False, 0.0, "na:sparse")
                except Exception as e:
                    errors.append(f"{tx},{ty}: {type(e).__name__}: {e}")
                    continue
                dt = time.perf_counter() - t0
                c = collections.Counter()
                for b in got:
                    L = float(sum(((b["line"][1:] - b["line"][:-1]) ** 2).sum(1) ** 0.5))
                    c[b["kind"] + (" mapped" if b.get("mapped") else "")] += round(L)
                per_tile[f"{tx},{ty}"] = {"metres": dict(c), "tile_s": round(dt, 2),
                                          "barriers_s": round(secs[-1], 3) if secs else None}
                all_b.extend(got)
        # the roads and the mapped barrier classes of the place's cells
        try:
            import geodesy
            frame = geodesy.EnuFrame(lat, lon, 0.0)
            md = blockworker._s.md
            keys = sorted({k for tx in range(-a.tiles, a.tiles + 1) for ty in range(-a.tiles, a.tiles + 1)
                           for k in roadsmod.cells_of_tile(frame, tx, ty, 50.0)})
            cls_count = collections.Counter()
            noise_tags = collections.Counter()
            for k in keys:
                cell = md.cell(k, layers=("infrastructure", "roads"), kind="demand")
                inf = cell.layers.get("infrastructure")
                if inf is not None and len(inf):
                    sub = inf.attrs.get("subtype")
                    cl = inf.attrs.get("class")
                    tags = inf.attrs.get("tags")
                    for i in range(len(inf)):
                        if sub is not None and str(sub[i]) == "barrier":
                            cls_count[str(cl[i])] += 1
                            if tags is not None and "noise" in str(tags[i]):
                                noise_tags[str(tags[i])[:120]] += 1
                rd = cell.layers.get("roads")
                if rd is not None and len(rd):
                    cl = rd.attrs.get("class")
                    for i in range(len(rd)):
                        if str(cl[i]) in ("motorway", "trunk", "primary", "secondary"):
                            for part in rd.parts(i):
                                for ring in part:
                                    e, n, _ = frame.geodetic_to_enu(ring[:, 1], ring[:, 0], 0.0)
                                    all_roads.append((str(cl[i]), list(zip(e, n))))
        except Exception as e:
            errors.append(f"cells: {type(e).__name__}: {e}")
            cls_count, noise_tags = collections.Counter(), collections.Counter()
        total = collections.Counter()
        for t in per_tile.values():
            total.update(t["metres"])
        summary[name] = {"lat": lat, "lon": lon, "metres": dict(total), "tiles": per_tile,
                         "osm_barrier_classes": dict(cls_count), "noise_tags": dict(noise_tags.most_common(8)),
                         "errors": errors}
        print(f"{name}: {dict(total)}  OSM barrier classes {dict(cls_count)}  errors {len(errors)}", flush=True)
        _draw(os.path.join(a.out, f"{name}.png"), all_b, all_roads, a.tiles)
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    print("barrier time per tile: avg %.3f s, max %.3f s over %d tiles" % (sum(secs) / max(len(secs), 1),
                                                                          max(secs or [0]), len(secs)))


def _draw(path, bars, roads_, n):
    from PIL import Image, ImageDraw
    import roads as roadsmod
    x0, y0, _, _ = roadsmod.tile_bbox(-n, -n)
    _, _, x1, y1 = roadsmod.tile_bbox(n, n)
    S = 2.0                     # px per m
    W, H = int((x1 - x0) * S), int((y1 - y0) * S)
    im = Image.new("RGB", (W, H), (25, 30, 25))
    d = ImageDraw.Draw(im)

    def px(x, y):
        return ((x - x0) * S, (y1 - y) * S)
    widths = {"motorway": 12, "trunk": 10, "primary": 8, "secondary": 6}
    for cls, pts in roads_:
        d.line([px(x, y) for x, y in pts], fill=(70, 70, 75), width=int(widths.get(cls, 4) * S))
    for b in bars:
        d.line([px(x, y) for x, y in b["line"]], fill=COLOURS.get(b["kind"], (255, 0, 255)), width=3)
    y = 6
    for k, c in COLOURS.items():
        d.rectangle([6, y, 26, y + 10], fill=c)
        d.text((32, y), k, fill=(255, 255, 255))
        y += 16
    im.save(path)


if __name__ == "__main__":
    main()
