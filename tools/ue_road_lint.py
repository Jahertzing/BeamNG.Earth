#!/usr/bin/env python3
"""What the edge score does not see, cell by cell, no Unreal needed: paint off the pavement, sidewalks over the asphalt or
standing off it, and drawn pavement with no road in it. Writes lint_<i>_<j>.png with the faults coloured on the photo.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_road_lint.py --dir <export dir> --cell=0,-1
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from scipy import ndimage  # noqa: E402

import ue_road_eval  # noqa: E402
import ue_survey  # noqa: E402

PX = 0.25
CELL = 2048.0
PAINT = ("line_", "crossing", "roadmarkings")


def mask_of(path, x0, y0, n, want=None, skip=None):
    out = np.zeros((n, n), bool)
    if not os.path.exists(path):
        return out
    for prim in ue_survey.read_glb(path):
        if (want and not prim["name"].startswith(want)) or (skip and prim["name"].startswith(skip)):
            continue
        out = ue_road_eval.fill_tris(out, prim["P"][:, :2] + (x0, y0), prim["T"], x0, y0)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick-r4")
    ap.add_argument("--cell", default="0,-1")
    ap.add_argument("--png", default=None)
    a = ap.parse_args()
    i, j = (int(v) for v in a.cell.split(","))
    x0, y0 = i * CELL, j * CELL
    n = int(CELL / PX)
    road = os.path.join(a.dir, f"road_{i}_{j}.glb")
    pave = mask_of(road, x0, y0, n, want=ue_road_eval.SURFACE) | mask_of(os.path.join(a.dir, f"deck_{i}_{j}.glb"), x0, y0, n)
    paint = mask_of(road, x0, y0, n, want=PAINT)
    walk = mask_of(os.path.join(a.dir, f"walks_{i}_{j}.glb"), x0, y0, n)
    d_out = ndimage.distance_transform_edt(~pave) * PX
    off = paint & ~pave
    over = walk & ndimage.binary_erosion(pave, iterations=4)         # (a metre in from the pavement's edge)
    near = walk & (d_out > 0)
    print(json.dumps({"cell": [i, j], "paint_m2": int(paint.sum() * PX * PX), "paint_off_pavement_share": round(float(off.sum()) / max(float(paint.sum()), 1.0), 4),
                      "walk_m2": int(walk.sum() * PX * PX), "walk_over_pavement_share": round(float(over.sum()) / max(float(walk.sum()), 1.0), 4),
                      "walk_gap_to_pavement_p50_m": round(float(np.median(d_out[near])), 2) if near.any() else None,
                      "walk_gap_p90_m": round(float(np.percentile(d_out[near], 90)), 2) if near.any() else None}))
    if a.png:
        photo = ue_road_eval.ground_image(os.path.join(a.dir, f"ground_{i}_{j}.glb")).resize((n // 2, n // 2), Image.BILINEAR)
        rgb = np.asarray(photo)[::-1].copy()

        def put(m, col):
            rgb[ndimage.binary_dilation(m[::2, ::2], iterations=1)] = col
        put(pave & ~ndimage.binary_erosion(pave), (255, 40, 40))
        put(walk & ~ndimage.binary_erosion(walk), (120, 200, 255))
        put(off, (255, 0, 255))
        put(over, (255, 255, 0))
        Image.fromarray(rgb[::-1]).save(a.png)


if __name__ == "__main__":
    main()
