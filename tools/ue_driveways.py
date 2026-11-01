#!/usr/bin/env python3
"""Curb cuts: a sidewalk's curb runs unbroken past every house, because the map has no driveways. A driveway shows on the aerial as
a pale, neutral strip from the walk to the house, narrower than a lot: where that strip meets the walk the curb is let down to the
pavement. Run after ue_walks. --png draws the finds on the photo.

    python tools/ue_driveways.py --dir <export dir> [--out <dir>] [--cell i,j --png out.png]
"""
import argparse
import concurrent.futures
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from scipy import ndimage  # noqa: E402

import ue_furniture  # noqa: E402
import ue_mask  # noqa: E402
import ue_paint_lift  # noqa: E402
import ue_pavement  # noqa: E402
import ue_road_eval  # noqa: E402
import ue_settle  # noqa: E402
import ue_survey  # noqa: E402

CELL = ue_mask.CELL
PX = ue_mask.PX
N = int(CELL / PX)
LOOK_M = (3.0, 7.5)             # m past the pavement the strip is read
PALE = 16.0                     # luma over the lot's own ground
GREY = 12.0                     # r - b at most over the road's
WIDTH_M = (3.0, 11.0)           # m along the walk a driveway is
CUT_UP_M = 0.03                 # m over the pavement a walk stands at a cut


def finds(export, i, j):
    """(walk pixels at a driveway, walk), cell rasters."""
    _, sd16, fl = ue_furniture.raster_cell((export, i, j))
    a = int(ue_mask.MARGIN_M / PX)
    sd, fl = sd16[a:a + N, a:a + N] / ue_mask.SD_Q, fl[a:a + N, a:a + N]
    walk = (fl & ue_furniture.F_WALK) > 0
    if not walk.any():
        return walk, walk
    photo = np.asarray(ue_road_eval.ground_image(os.path.join(export, f"ground_{i}_{j}.glb")).convert("RGB"), np.float32)[::-1]
    luma = photo @ np.array([0.299, 0.587, 0.114], np.float32)
    warm = photo[..., 0] - photo[..., 2]
    # the ring beyond the walk, each pixel with its nearest walk pixel
    ring = (sd > LOOK_M[0]) & (sd <= LOOK_M[1]) & ((fl & ue_furniture.F_BLD) == 0)
    # (the ground of the lot: a wide blur of the ring, so a pale strip stands out of its own neighbours)
    w = ndimage.uniform_filter(ring.astype(np.float32), 60)
    base = ndimage.uniform_filter(luma * ring, 60) / np.maximum(w, 1e-3)
    warm_road = np.median(warm[sd < -0.5]) if (sd < -0.5).any() else 0.0
    strip = ring & (luma - base >= PALE) & (warm <= warm_road + GREY)
    strip = ndimage.binary_opening(strip, iterations=1)
    # a strip counts when it runs the depth of the ring
    lab, n = ndimage.label(strip)
    if not n:
        return np.zeros_like(walk), walk
    ids = np.arange(1, n + 1)
    lo = ndimage.minimum(sd, lab, ids)
    hi = ndimage.maximum(sd, lab, ids)
    area = ndimage.sum(strip, lab, ids) * PX * PX
    deep = (lo <= LOOK_M[0] + 0.8) & (hi >= LOOK_M[1] - 1.5)
    wide = area / np.maximum(hi - lo, 1.0)
    good = np.r_[False, deep & (wide >= WIDTH_M[0]) & (wide <= WIDTH_M[1])]
    keep = good[lab]
    # carried in to the walk along the shortest way
    near = ndimage.binary_dilation(keep, iterations=int((LOOK_M[0] + 0.5) / PX)) & (sd > 0)
    return near & walk, walk


def cell(job):
    export, out, i, j = job
    path = os.path.join(export, f"walks_{i}_{j}.glb")
    if not os.path.exists(path):
        return 0
    cut, walk = finds(export, i, j)
    if not cut.any():
        return 0
    P, UV, C, T, M, names = ue_settle.read(path)
    q = np.clip((P[:, :2] / PX).astype(np.int64), 0, N - 1)
    grown = ndimage.binary_dilation(cut, iterations=2)
    hit = grown[q[:, 1], q[:, 0]]
    G = ue_survey.read_glb(os.path.join(export, f"ground_{i}_{j}.glb"))[0]["P"]
    n = int(round(len(G) ** 0.5))
    top = ue_paint_lift.terrain_z(G[:, 2].reshape(n, n), P[hit, :2]) + ue_pavement.LIFT_M + CUT_UP_M
    P = P.copy()
    P[hit, 2] = np.minimum(P[hit, 2], top)      # (a height, not a drop: a second run changes nothing)
    ue_settle.write(os.path.join(out, f"walks_{i}_{j}.glb"), f"walks_{i}_{j}", P, UV, C, T, M, names)
    return int(ndimage.label(cut)[1])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--cell", default=None)
    ap.add_argument("--png", default=None)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    if a.png:
        i, j = (int(v) for v in a.cell.split(","))
        cut, walk = finds(a.dir, i, j)
        rgb = np.asarray(ue_road_eval.ground_image(os.path.join(a.dir, f"ground_{i}_{j}.glb")).convert("RGB"))[::-1].copy()
        rgb[walk] = (120, 200, 255)
        rgb[ndimage.binary_dilation(cut, iterations=1)] = (255, 0, 0)
        Image.fromarray(rgb[::-1]).crop((1200, 1200, 2400, 2400)).save(a.png)
        print(ndimage.label(cut)[1])
        return
    out = a.out or a.dir
    os.makedirs(out, exist_ok=True)
    cells = [(int(f[6:-4].split("_")[0]), int(f[6:-4].split("_")[1])) for f in os.listdir(a.dir) if f.startswith("walks_") and f.endswith(".glb")]
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as pool:
        got = list(pool.map(cell, [(a.dir, out, i, j) for i, j in cells]))
    print(json.dumps({"cuts": sum(got)}))


if __name__ == "__main__":
    main()
