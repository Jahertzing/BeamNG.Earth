#!/usr/bin/env python3
"""How bumpy the open ground is, raw lidar vs plausible.py's filter, offline from cached DEM tiles.

For each place: its zoom-z Mapterhorn tile and the 8 round it (from <dem dir>/mapterhorn/<z>/<x>/<y>.webp; nothing
is downloaded), decoded and hole-filled as dem.Dem does, then plausible.plausible over the 3x3 mosaic. Measured on
the middle tile:

  ride g      the vertical acceleration a car feels driving straight across the ground at SPEED_MPH, over a BASE_M
              wheelbase (second difference along rows and columns): p50 / p99 and the share over 0.3 g
  change      how far the filter moved the ground: mean, p99, max |out - raw| (landforms must stay)
  steep kept  the same on slopes over plausible.STEEP_FULL_DEG (embankments, cuts: should be ~0)

  python tools/plausible_probe.py --dem C:/beamng-earth/world/dem [--z 16] [--png out_dir] [lat,lon[,name] ...]

With --png, a hillshade of raw | plausible per place. Pure numpy beside earthfast; a few seconds a place."""
import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import dem as demmod  # noqa: E402
import plausible  # noqa: E402

SPEED_MPH = 30.0
BASE_M = 3.0
PLACES = {
    "frederick_fields": (40.105, -104.955),
    "erie": (40.035, -105.035),
    "thornton": (39.925, -104.975),
    "denver_20th": (39.7608, -105.0041),
    "c470": (39.56, -104.875),
    "castle_rock": (39.385, -104.86),
}


def tile_xy(lat, lon, z):
    n = 2 ** z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def px_m(z, y):
    n = 2 ** z
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / n))))
    return 40075016.686 * math.cos(math.radians(lat)) / n / demmod.TILE


def mosaic(root, z, x, y):
    T = demmod.TILE
    big = np.zeros((3 * T, 3 * T), np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            p = os.path.join(root, "mapterhorn", str(z), str(x + dx), f"{y + dy}.webp")
            with open(p, "rb") as f:
                a = demmod.decode_terrarium(f.read(), T)
            a = demmod.fill_holes(a, demmod.hole_px(z, y + dy))
            big[(dy + 1) * T:(dy + 2) * T, (dx + 1) * T:(dx + 2) * T] = a
    return big


def ride_g(a, px, speed_mph=SPEED_MPH, base_m=BASE_M):
    """|vertical acceleration| in g along rows and columns (both directions pooled)."""
    k = max(1, int(round(base_m / px)))
    b = k * px
    v2 = (speed_mph * 0.44704) ** 2
    out = []
    for arr in (a, a.T):
        d2 = (arr[:, 2 * k:] - 2 * arr[:, k:-k] + arr[:, :-2 * k]) / (b * b)
        out.append(np.abs(d2).ravel() * v2 / 9.81)
    return np.concatenate(out)


def slope_deg(a, px):
    gy, gx = np.gradient(a, px)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def hillshade(a, px):
    gy, gx = np.gradient(a, px)
    az, alt = math.radians(315), math.radians(45)
    sl = np.arctan(np.hypot(gx, gy) * 2.0)
    asp = np.arctan2(-gx, gy)
    h = np.sin(alt) * np.cos(sl) + np.cos(alt) * np.sin(sl) * np.cos(az - asp)
    return (np.clip(h, 0, 1) * 255).astype(np.uint8)


def stats(g):
    return {"p50": round(float(np.percentile(g, 50)), 3), "p99": round(float(np.percentile(g, 99)), 3),
            "over_0.3g_pct": round(float((g > 0.3).mean() * 100), 2)}


def probe(root, name, lat, lon, z, png=None):
    x, y = tile_xy(lat, lon, z)
    px = px_m(z, y)
    raw = mosaic(root, z, x, y)
    out = plausible.plausible(raw, px)
    T = demmod.TILE
    c = (slice(T, 2 * T), slice(T, 2 * T))
    r, o = raw[c], out[c]
    d = np.abs(o - r)
    steep = slope_deg(r, px) > plausible.STEEP_FULL_DEG
    res = {"place": name, "lat": lat, "lon": lon, "tile": [z, x, y], "px_m": round(px, 3),
           "ride_raw": stats(ride_g(r, px)), "ride_plausible": stats(ride_g(o, px)),
           "ride65_raw": stats(ride_g(r, px, 65.0)), "ride65_plausible": stats(ride_g(o, px, 65.0)),
           "change_m": {"mean": round(float(d.mean()), 3), "p99": round(float(np.percentile(d, 99)), 3),
                        "max": round(float(d.max()), 3)},
           "steep_share_pct": round(float(steep.mean() * 100), 2),
           "steep_change_p99_m": round(float(np.percentile(d[steep], 99)), 3) if steep.any() else None}
    if png:
        from PIL import Image
        os.makedirs(png, exist_ok=True)
        im = np.concatenate([hillshade(r, px), np.full((T, 8), 255, np.uint8), hillshade(o, px)], axis=1)
        Image.fromarray(im).save(os.path.join(png, f"{name}.png"))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dem", required=True, help="the DEM cache folder (holds mapterhorn/)")
    ap.add_argument("--z", type=int, default=16)
    ap.add_argument("--png", help="folder for raw | plausible hillshades")
    ap.add_argument("places", nargs="*", help="lat,lon[,name] (default: the Front Range set)")
    a = ap.parse_args()
    places = PLACES
    if a.places:
        places = {}
        for i, s in enumerate(a.places):
            p = s.split(",")
            places[p[2] if len(p) > 2 else f"p{i}"] = (float(p[0]), float(p[1]))
    for name, (lat, lon) in places.items():
        try:
            print(json.dumps(probe(a.dem, name, lat, lon, a.z, a.png)), flush=True)
        except FileNotFoundError as e:
            print(json.dumps({"place": name, "missing": str(e)}), flush=True)


if __name__ == "__main__":
    main()
