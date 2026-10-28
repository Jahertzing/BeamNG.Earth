#!/usr/bin/env python3
"""Street life seen from above (server streetlife.py): the road tiles around a few places on the Front Range drive built
offline from the cached map data, with their buildings, driveways, street furniture, street-life props and parking
spots, as PNGs (asphalt dark grey, sidewalks light grey, buildings tan, driveways mid grey; parking spots: curb blue,
lot / aisle cyan, driveway magenta; mailboxes black, cluster boxes orange, carts green, bus stops red, downtown extras
yellow, other furniture white). The ground is flat. Prints each place's counts per group and its build time with and
without street life (BNE_STREETLIFE's switch).

    python tools/streetlife_probe.py --out C:/beamng-earth/streetlife/out
    python tools/streetlife_probe.py --at 39.5480,-104.9700 --name highlands_ranch --half 250
"""
import argparse
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import numpy as np  # noqa: E402

PLACES = {
    "frederick_old_town": (40.0995, -104.9370),         # The Drive's start: an old town grid
    "frederick_subdivision": (40.1086, -104.9625),      # newer subdivisions west of Old Town
    "thornton_128th": (39.92848, -104.96248),
    "denver_capitol_hill": (39.7330, -104.9790),
    "denver_downtown_16th": (39.7475, -104.9945),
    "highlands_ranch": (39.5480, -104.9700),
    "castle_rock_downtown": (39.3722, -104.8561),
}
PX_M = 2.0
COLOURS = {"mailbox": (10, 10, 10), "mailcbu": (255, 140, 0), "cart_trash": (40, 200, 60), "cart_recycle": (40, 200, 60),
           "bussign": (230, 30, 30), "busshelter": (230, 30, 30), "uspsbox": (255, 230, 0), "bikerack": (255, 230, 0),
           "planter": (255, 230, 0)}


def build(md, frame, tiles, urban, widths, on):
    import buildings
    import parking
    import roads
    import streetlife
    streetlife.ENABLED = on
    out = {"chunks": [], "walks": [], "props": [], "park": [], "rings": [], "drives": []}
    t0 = time.perf_counter()
    zero = lambda la, lo: np.zeros(np.shape(la))        # noqa: E731
    for tx, ty in tiles:
        keys = roads.cells_of_tile(frame, tx, ty)
        cells = [md.cell(k, layers=("roads",), kind="demand").layers.get("roads") for k in keys]
        walks, props, lines, park = [], [], [], []
        items = buildings.road_tile_items(frame, tx, ty, md, zero)
        town = buildings.coverage(items) < buildings.POLES_COVERAGE
        chunks = roads.build_tile(frame, tx, ty, cells, zero, urban, walks=walks, widths=widths, props=props,
                                  lines=lines, wires=[], town=town, park=park, mph=True, lamp_region="na:sparse")
        x0, y0, x1, y1 = roads.tile_bbox(tx, ty)
        drives = []
        props.extend(buildings.doors(items, lines, x0, y0, drives))
        if streetlife.ENABLED:
            props.extend(streetlife.tile_rows(frame, md, roads.cells_of_tile(frame, tx, ty, margin=0.0), drives, walks,
                                              lines, (x0, y0, x1, y1), lambda x, y: np.zeros(np.shape(x)), us=True,
                                              town=bool(town), props=props, spots=park,
                                              blockers=[buildings.drawn_ring(spec) + np.array([x0, y0])
                                                        for spec, *_ in items]))
        drives.extend(buildings.garage_drives(items, lines, x0, y0))
        park = parking.clear_of(park, props, drives)
        park = parking.tile_lot_spots(frame, tx, ty, md, zero, items, park)
        out["chunks"] += chunks
        out["walks"] += walks
        out["props"] += props
        out["park"] += park
        out["drives"] += drives
        out["rings"] += [buildings.drawn_ring(spec) + np.array([x0, y0]) for spec, *_ in items]
    out["dt"] = time.perf_counter() - t0
    return out


def draw(got, half, path, title, driveway_spots):
    from PIL import Image, ImageDraw
    import pocket_probe
    pocket_probe.PX_M = PX_M
    pocket_probe.draw(got["chunks"], got["walks"], half, path, title)
    im = Image.open(path)
    dr = ImageDraw.Draw(im)

    def px(xy):
        xy = np.asarray(xy, np.float64)[:, :2]
        return [((x + half) * PX_M, (half - y) * PX_M) for x, y in xy.tolist()]
    for ring in got["rings"]:
        if len(ring) >= 3:
            dr.polygon(px(ring), fill=(196, 176, 140), outline=(120, 100, 70))
    for d in got["drives"]:
        dr.line(px(d["n"]), fill=(130, 130, 130), width=max(1, int(d.get("w", 3.2) * PX_M)))
    for s in got["park"]:
        a = math.radians(s[3])
        u, v = np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])
        c = np.array(s[:2])
        poly = [c + u * 2.2 + v * 0.9, c - u * 2.2 + v * 0.9, c - u * 2.2 - v * 0.9, c + u * 2.2 - v * 0.9]
        col = (220, 40, 220) if (s[0], s[1]) in driveway_spots else ((40, 200, 230) if len(s) > 4 else (40, 90, 230))
        dr.polygon(px(poly), fill=col)
    for r in got["props"]:
        col = COLOURS.get(r[0])
        x, y = (r[1] + half) * PX_M, (half - r[2]) * PX_M
        if col is None:
            dr.point((x, y), fill=(255, 255, 255))
            continue
        k = 3 if r[0] in ("mailcbu", "busshelter", "bussign") else 2
        dr.rectangle((x - k, y - k, x + k, y + k), fill=col)
    im.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="C:/beamng-earth/streetlife/out" if os.name == "nt" else "/tmp/streetlife")
    ap.add_argument("--at", default=None, help="lat,lon of one place (else PLACES)")
    ap.add_argument("--name", default="place")
    ap.add_argument("--half", type=float, default=380.0, help="m from the place to the picture's edge")
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/beamng-earth"))
    a = ap.parse_args()
    import geodesy
    import mapdata
    import roads
    import roadwidth
    import surface
    roads.ROADS_IN_GROUND = False
    md = mapdata.MapData(a.cache_dir)
    sf = surface.Surface(md)
    widths = roadwidth.RoadWidths(
        lambda k: md.cell(k, layers=("buildings",), kind="demand").layers.get("buildings")).get
    places = {a.name: tuple(float(v) for v in a.at.split(","))} if a.at else PLACES
    os.makedirs(a.out, exist_ok=True)
    for name, (lat, lon) in places.items():
        frame = geodesy.EnuFrame(lat, lon)
        r = max(1, int(math.ceil(a.half / roads.TILE)))
        tiles = [(tx, ty) for tx in range(-r, r) for ty in range(-r, r)]
        build(md, frame, tiles, sf.urban, widths, False)        # (warm: the cells once)
        off = build(md, frame, tiles, sf.urban, widths, False)
        on = build(md, frame, tiles, sf.urban, widths, True)
        before = {(s[0], s[1]) for s in off["park"]}
        drive_spots = {(s[0], s[1]) for s in on["park"]} - before
        counts = {}
        for row in on["props"]:
            counts[row[0]] = counts.get(row[0], 0) + 1
        mine = {g: n for g, n in sorted(counts.items()) if g in COLOURS}
        path = os.path.join(a.out, f"{name}.png")
        draw(on, a.half, path, f"{name} ({lat:.5f}, {lon:.5f})", drive_spots)
        print(f"{name}: {len(tiles)} tiles, build {off['dt']:.2f} s without street life, {on['dt']:.2f} s with; "
              f"houses' driveways {len([d for d in on['drives'] if d.get('w') == 3.2])}; parking spots "
              f"{len(off['park'])} -> {len(on['park'])} ({len(drive_spots)} on driveways); street life {mine} -> {path}")


if __name__ == "__main__":
    main()
