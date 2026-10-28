#!/usr/bin/env python3
"""Turn pockets and tapers seen from above (roads.pocket_profiles, feature turn-pockets): the road tiles around a few
places built offline from the cached map data, before (BNE_TURN_POCKETS=off) and after, as PNGs: asphalt dark grey,
sidewalks light grey, corner asphalt grey, white and yellow markings, turn arrows red. The ground is flat (the shapes
only); the carriageway is drawn as its mesh (roads.ROADS_IN_GROUND off: the ground's photo and masks paint the same
edges, surface._road_segments). Prints each place's build time both ways and how many roads are not of one width.

    python tools/pocket_probe.py --out C:/beamng-earth/pockets/out
    python tools/pocket_probe.py --at 39.7400,-104.9874 --name colfax --half 200
"""
import argparse
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import numpy as np  # noqa: E402

PLACES = {      # name -> (lat, lon): Colorado's regional OSM table covers them
    "denver_colfax_broadway": (39.7400, -104.9874),     # (Colfax's segments come from its route relation: no tags)
    "denver_20th_st": (39.75227, -104.99172),           # a left pocket mapped as its own way, from a side street
    "frederick_co52_colorado_blvd": (40.0874, -104.9421),   # CO-52 (CDOT's lanes): centre lane arrows at the junction
    "frederick_co52_step_west": (40.0877, -104.9494),   # ... its centre turn lane opening from a side street
    "frederick_co52_near_i25": (40.0877, -104.9725),
    "thornton_136th_washington": (39.9211, -104.9400),  # a backward left pocket the lane count leaves out
    "thornton_128th": (39.92848, -104.96248),           # pockets mapped as their own ways
}
PX_M = float(os.environ.get("POCKET_PROBE_PX_M", "2.0"))     # pixels a metre


def build(md, frame, tiles, urban, widths):
    import roads
    chunks, walks = [], []
    t0 = time.perf_counter()
    for tx, ty in tiles:
        cells = [md.cell(k, layers=("roads",), kind="demand").layers.get("roads")
                 for k in roads.cells_of_tile(frame, tx, ty)]
        pcells = [md.cell(k, layers=("roads",), kind="demand").layers.get("roads")
                  for k in roads.cells_of_tile(frame, tx, ty, roads.POCKET_REACH_M)]
        w = []
        chunks += roads.build_tile(frame, tx, ty, cells, lambda la, lo: np.zeros(np.shape(la)), urban, walks=w,
                                   widths=widths, pocket_cells=pcells)
        walks += w
    return chunks, walks, time.perf_counter() - t0


def draw(chunks, walks, half, path, title):
    from PIL import Image, ImageDraw
    import roadmesh
    size = int(2 * half * PX_M)
    im = Image.new("RGB", (size, size), (92, 112, 74))
    dr = ImageDraw.Draw(im)

    def px(xy):
        xy = np.asarray(xy, np.float64)[:, :2]
        return [((x + half) * PX_M, (half - y) * PX_M) for x, y in xy.tolist()]
    for wk in walks:                        # sidewalks under the rest
        if "corner" in wk:
            outer = np.array([o[:2] for o in wk["corner"]["outer"]])
            dr.polygon(px(np.vstack([wk["corner"]["arc"][:, :2], outer[::-1]])), fill=(188, 186, 180))
            continue
        dr.line(px(wk["pts"]), fill=(188, 186, 180), width=max(1, int(wk.get("w", 2.5) * PX_M)))
    for c in chunks:
        if c.get("poly"):
            dr.polygon(px(c["poly"]), fill=(84, 84, 84))
    base = [c for c in chunks if c.get("n") and roadmesh.layer_of(c) == "base"]
    for left, right, _v, _m, _layer, _z in roadmesh.ribbons(base):
        for j in range(len(left) - 1):
            dr.polygon(px([left[j], left[j + 1], right[j + 1], right[j]]), fill=(62, 62, 62))
    for c in chunks:
        if not c.get("n") or roadmesh.layer_of(c) != "mark" or c["m"] == "m_metal_grid_drain":
            continue
        col = (230, 190, 40) if "yellow" in c["m"] else (240, 240, 240)
        if c.get("w") == 0.3 and c["m"] == "line_white" and len(c["n"]) <= 3:
            col = (230, 60, 50)             # (turn arrow strokes)
        dr.line(px(c["n"]), fill=col, width=max(1, int(round(c["w"] * PX_M * 1.5))))
    dr.text((8, 8), title, fill=(255, 255, 255))
    im.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="C:/beamng-earth/pockets/out" if os.name == "nt" else "/tmp/pockets")
    ap.add_argument("--at", default=None, help="lat,lon of one place (else PLACES)")
    ap.add_argument("--name", default="place")
    ap.add_argument("--half", type=float, default=200.0, help="m from the place to the picture's edge")
    ap.add_argument("--before", choices=("off", "noparts", "nolines"), default="off",
                    help="the before picture: no pockets at all, pockets without the merged ways' parts, or the parts "
                         "without their own lane lines")
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
        r = int(math.ceil(a.half / roads.TILE))
        tiles = [(tx, ty) for tx in range(-r, r) for ty in range(-r, r)]
        got = {}
        parts = roads.osm_parts
        for on in (False, True):
            roads.POCKETS_ON = on or a.before in ("noparts", "nolines")
            roads.osm_parts = parts if on or a.before == "nolines" else (lambda feats, i: None)
            roads.PART_LINES_ON = on or a.before != "nolines"
            roads._POCKET_MEMO.clear()
            build(md, frame, tiles, sf.urban, widths)           # (warm: the cells and specs once)
            chunks, walks, dt = build(md, frame, tiles, sf.urban, widths)
            got[on] = dt
            tag = "after" if on else "before"
            path = os.path.join(a.out, f"{name}_{tag}.png")
            n = sum(1 for c in chunks if c.get("wl") is not None)
            draw(chunks, walks, a.half, path, f"{name} {tag} ({lat:.5f}, {lon:.5f})")
            print(f"{name} {tag}: {len(tiles)} tiles in {dt:.2f} s, {n} widened carriageway chunks -> {path}")
        cells = [md.cell(k, layers=("roads",), kind="demand").layers.get("roads")
                 for k in roads.cells_of_tile(frame, -r, -r) + roads.cells_of_tile(frame, r - 1, r - 1)]
        prof = roads.pocket_profiles(cells, sf.urban, widths)
        kinds = {}
        for wd in prof.values():
            k = "+".join(sorted({c[0] for c in wd.comps})) + (" (pocket added)" if wd.added else "")
            kinds[k] = kinds.get(k, 0) + 1
        print(f"{name}: build {got[False]:.2f} s before, {got[True]:.2f} s after; profiles in its cells: {kinds}")


if __name__ == "__main__":
    main()
