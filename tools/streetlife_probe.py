#!/usr/bin/env python3
"""Street life seen from above (server streetlife.py): the road tiles around a few places on the Front Range drive built
offline from the cached map data, with their buildings, driveways, street furniture, street-life props and parking
spots, as PNGs (asphalt dark grey, sidewalks light grey, buildings tan, driveways mid grey; parking spots: curb blue,
lot / aisle cyan, driveway magenta; mailboxes black, cluster boxes orange, carts green, bus stops red, downtown extras
yellow, other furniture white). The ground is flat. Prints each place's counts per group and its build time with and
without street life (BNE_STREETLIFE's switch).

    python tools/streetlife_probe.py --out C:/beamng-earth/streetlife/out
    python tools/streetlife_probe.py --at 39.5480,-104.9700 --name highlands_ranch --half 250
    python tools/streetlife_probe.py --views tools/streetlife_bench.json     (also the benchmark set: below)

--views writes a tools/benchmark.py set with one place per PLACES entry and, per place, a street-level view of the
prop of each group nearest the place (from VIEW_M in front of it, a three-quarter view: a mailbox's door, a shelter's
open side), so the in-game shots show the props rather than whatever the place's own heading looks at.
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
VIEW_M = 7.0                # m from the prop to the camera
VIEW_SKEW_DEG = 35.0        # ... off the prop's front (a three-quarter view)
VIEW_MAX_M = 260.0          # a prop farther from the place has no view (its tiles may not be exact yet)
VIEW_GROUPS = (("mailbox",), ("mailcbu",), ("cart_trash", "cart_recycle"), ("bussign", "busshelter"),
               ("uspsbox",), ("bikerack", "planter"))
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


def views_of(frame, props, name):
    """The benchmark views (tools/benchmark.py: lat, lon, heading, agl, pitch) of the prop nearest the place (the
    anchor's origin) in each VIEW_GROUPS group."""
    out = []
    for groups in VIEW_GROUPS:
        rows = [r for r in props if r[0] in groups]
        if not rows:
            continue
        r = min(rows, key=lambda r: math.hypot(r[1], r[2]))
        if math.hypot(r[1], r[2]) > VIEW_MAX_M:
            continue
        a = math.radians(r[4] + VIEW_SKEW_DEG)              # (yaw: the prop's front, degrees CCW from east)
        cx, cy = r[1] + VIEW_M * math.cos(a), r[2] + VIEW_M * math.sin(a)
        la, lo, _ = frame.enu_to_geodetic(np.array([cx]), np.array([cy]), 0.0)
        heading = math.degrees(math.atan2(r[1] - cx, r[2] - cy)) % 360.0       # (compass, toward the prop)
        out.append({"name": f"{groups[0]}", "lat": round(float(np.ravel(la)[0]), 7),
                    "lon": round(float(np.ravel(lo)[0]), 7), "heading": round(heading, 1), "agl": 1.7,
                    "pitch": -8, "fov": 60, "note": f"{name}: {r[0]} {math.hypot(r[1], r[2]):.0f} m from the place"})
    return out


def write_views(path, sets):
    import json
    doc = {"version": 1,
           "about": "Street life (server/streetlife.py): the nearest prop of each group at a few places on the Front "
                    "Range drive, from the street (written by tools/streetlife_probe.py --views)",
           "defaults": {"date": "2026-10-20", "hour": 15.0, "weather": "clear",
                        "views": [{"name": "oblique", "agl": 28, "pitch": -40, "fov": 70}]},
           "places": []}
    for name, (lat, lon), views in sets:
        doc["places"].append({"id": f"sl_{name}", "name": name.replace("_", " "), "lat": lat, "lon": lon,
                              "heading": 0, "group": "streetlife", "tags": ["colorado"],
                              "views": [{"name": "oblique", "agl": 28, "pitch": -40, "fov": 70}] + views})
    with open(path, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"benchmark set: {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="C:/beamng-earth/streetlife/out" if os.name == "nt" else "/tmp/streetlife")
    ap.add_argument("--at", default=None, help="lat,lon of one place (else PLACES)")
    ap.add_argument("--name", default="place")
    ap.add_argument("--half", type=float, default=380.0, help="m from the place to the picture's edge")
    ap.add_argument("--views", default=None, help="also write a tools/benchmark.py set of the props' views here")
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
    sets = []
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
        sets.append((name, (lat, lon), views_of(frame, on["props"], name)))
    if a.views:
        write_views(a.views, sets)


if __name__ == "__main__":
    main()
