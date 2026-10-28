#!/usr/bin/env python3
"""Copies the USGS lidar height-above-ground mosaics (lidarheights.Lidar), or with --dsm the surface model's
(decklidar.Dsm), fetched and cached when missing, of the map cells round the interchanges of
tools/interchange_probe.py to a folder, for working on the deck heights offline where the Planetary Computer is out of
reach. --probe-tile prints what the tile service answers for one DSM tile (shape, type, values) and stops.

    python tools/hag_export.py --out C:\\beamng-earth\\interchanges\\hag [--reach 1500]
    python tools/hag_export.py --dsm --out C:\\beamng-earth\\interchanges\\dsm
"""
import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import numpy as np  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/beamng-earth"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--reach", type=float, default=1500.0, help="m round each place")
    ap.add_argument("--dsm", action="store_true", help="the surface model (decklidar.Dsm), not the height above ground")
    ap.add_argument("--probe-tile", action="store_true")
    a = ap.parse_args()
    import interchange_probe
    import lidarheights
    import mapcells
    import cache
    import netfetch
    raw = os.path.join(a.cache_dir, "mapdata", "raw")       # (mapdata.MapData.raw_dir)
    lid = lidarheights.Lidar(raw, netfetch.Fetcher())
    if a.dsm or a.probe_tile:
        import decklidar
        lid = decklidar.Dsm(raw, netfetch.Fetcher())
    if a.probe_tile:
        import io
        k = mapcells.cell_of(39.7800, -104.9894)
        items = lid.items(k.bounds())
        print("items", items[:3])
        t = mapcells.CellKey(lidarheights.ZOOM, k.x * 4 + 1, k.y * 4 + 1)
        url = (f"{lidarheights.TILES}/{t.z}/{t.x}/{t.y}@1x.npy?collection={decklidar.DSM_COLLECTION}"
               f"&item={items[0][0]}&assets=data")
        body = lid.fetcher.get(url).body
        arr = np.load(io.BytesIO(body), allow_pickle=False)
        print("npy", arr.shape, arr.dtype, [float(np.nanmin(b)) for b in arr.reshape(-1, 256, 256)],
              [float(np.nanmax(b)) for b in arr.reshape(-1, 256, 256)])
        z = decklidar.npy_tile(arr)
        print("tile", z.shape, np.nanmin(z), np.nanmedian(z), np.nanmax(z), "nan", int(np.isnan(z).sum()))
        return
    keys = {}
    for name, (la, lo, _) in interchange_probe.PLACES.items():
        dla, dlo = a.reach / 111000.0, a.reach / (111000.0 * np.cos(np.radians(la)))
        for k in mapcells.cells_for_points(np.array([la - dla, la + dla, la, la]), np.array([lo, lo, lo - dlo, lo + dlo])):
            keys[str(k)] = k
        for k in mapcells.cells_for_points(np.array([la - dla, la + dla]), np.array([lo - dlo, lo + dlo])):
            keys[str(k)] = k
        k0 = mapcells.cell_of(la, lo)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                k = mapcells.CellKey(k0.z, k0.x + dx, k0.y + dy)
                b = k.bounds()
                if b[0] - dlo < lo < b[2] + dlo and b[1] - dla < la < b[3] + dla:
                    keys[str(k)] = k
    for s, k in sorted(keys.items()):
        m = lid.mosaic(k)
        src = cache.resolve(os.path.join(lid.dir, str(k.z), str(k.x), f"{k.y}.npz"))
        dst = os.path.join(a.out, str(k.z), str(k.x), f"{k.y}.npz")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        print(s, "covered", int(m["covered"].sum()), "of", m["covered"].size, "items", list(m["items"]), flush=True)


if __name__ == "__main__":
    main()
