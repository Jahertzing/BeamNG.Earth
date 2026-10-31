#!/usr/bin/env python3
"""Sidewalks only where the aerial has one. The builders give every road in built-up land a sidewalk and the pavement pass a
curb to go with it, so Highway 52 and the frontage roads ran between curbs through open fields. A sidewalk shows as a pale
strip at the road's edge, against the ground further out: band by band (BLOCK_M along the road) what is not paler than that
ground is taken out of walks_<i>_<j>.glb, curb with it. Run after the pavement pass, before ue_mask and what stands beside the roads.

    python tools/ue_walks.py --dir <export dir> [--out <dir>]
"""
import argparse
import concurrent.futures
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

import ue_furniture  # noqa: E402
import ue_mask  # noqa: E402
import ue_road_eval  # noqa: E402
import ue_settle  # noqa: E402

CELL = ue_mask.CELL
PX = ue_mask.PX
N = int(CELL / PX)
BLOCK_M = 24.0
PALER = 8.0                     # luma the strip is over the ground beyond it
NEAR = ((0.0, 2.0), (2.0, 4.0))     # m off the pavement: an attached walk, one behind a tree lawn
FAR = (6.0, 10.0)
JOIN_M = 12.0                   # m of unproved walk between proved ones kept (a tree's shadow, a driveway)
MIN_RUN_M2 = 16.0
THROUGH = {"trunk", "primary", "secondary", "tertiary"}
ARTERIAL_NEAR_M = 24.0          # m from a through road's line a walk counts as along it
BUILT_NEAR_M = 35.0             # m to a building that gives a through road's walk a reason
NET = "C:/beamng-earth/Earth.UE/export/roadnet"


_tree = []


def arterials():
    if not _tree:
        with open(os.path.join(NET, "roadnet.json")) as f:
            links = json.load(f)["links"]
        pts = []
        for lk in links:
            if lk["class"] in THROUGH and lk["level"] == 0:
                p = np.array(lk["points"])[:, :2]
                L = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
                t = np.arange(0.0, max(L[-1], 1.0), 8.0)
                pts.append(np.c_[np.interp(t, L, p[:, 0]), np.interp(t, L, p[:, 1])])
        _tree.append(cKDTree(np.concatenate(pts)))
    return _tree[0]


def keep_mask(export, i, j):
    """(cell raster of the walk to keep, of the walk there is), 0.5 m a pixel, row 0 south."""
    _, sd16, fl = ue_furniture.raster_cell((export, i, j))
    a = int(ue_mask.MARGIN_M / PX)
    sd, fl = sd16[a:a + N, a:a + N] / ue_mask.SD_Q, fl[a:a + N, a:a + N]
    walk = (fl & ue_furniture.F_WALK) > 0
    if not walk.any():
        return walk, walk
    photo = np.asarray(ue_road_eval.ground_image(os.path.join(export, f"ground_{i}_{j}.glb")).convert("RGB"), np.float32)[::-1]
    luma = photo @ np.array([0.299, 0.587, 0.114], np.float32)
    cut = np.ones(walk.shape, bool)
    step = int(BLOCK_M / PX)
    cut[::step, :] = False
    cut[:, ::step] = False
    chunks, n = ndimage.label(walk & cut)
    if not n:
        return walk, walk
    who = chunks[tuple(ndimage.distance_transform_edt(chunks == 0, return_distances=False, return_indices=True))]
    ids = np.arange(1, n + 1)
    free = (fl & (ue_furniture.F_BLD | ue_furniture.F_DECK)) == 0

    def median_in(lo, hi):
        m = (sd > lo) & (sd <= hi) & free
        out = np.full(n + 1, np.nan)
        out[1:] = ndimage.median(luma, np.where(m, who, 0), ids)
        return out
    far = median_in(*FAR)
    near = np.nanmax([median_in(*b) for b in NEAR], axis=0)
    good = np.where(np.isfinite(far) & np.isfinite(near), near - far >= PALER, True)
    good[0] = False
    # (a gravel shoulder is as pale as a walk: on a through road a sidewalk serves something built beside it)
    tree = arterials()
    com = np.array(ndimage.center_of_mass(walk, chunks, ids)).reshape(-1, 2)
    xy = np.c_[com[:, 1] * PX + i * CELL, com[:, 0] * PX + j * CELL]
    through = tree.query(xy)[0] <= ARTERIAL_NEAR_M
    bd = ndimage.distance_transform_edt((fl & ue_furniture.F_BLD) == 0) * PX
    c = np.clip(com.astype(np.int64), 0, N - 1)
    good[1:] &= ~through | (bd[c[:, 0], c[:, 1]] <= BUILT_NEAR_M)
    proved = good[chunks]
    keep = walk & ndimage.binary_dilation(proved, iterations=int(JOIN_M / PX))
    lab, c = ndimage.label(ndimage.binary_dilation(keep, iterations=2))
    size = ndimage.sum(keep, lab, np.arange(1, c + 1)) * PX * PX
    keep &= np.r_[False, size >= MIN_RUN_M2][lab]
    return keep, walk


def cell(job):
    export, out_dir, i, j = job
    path = os.path.join(export, f"walks_{i}_{j}.glb")
    if not os.path.exists(path):
        return 0.0, 0.0
    keep, walk = keep_mask(export, i, j)
    kept = float(keep.sum()) * PX * PX
    had = float(walk.sum()) * PX * PX
    grown = ndimage.binary_dilation(keep, iterations=4)
    P, UV, C, T, M, names = ue_settle.read(path)
    c = P[T].mean(1)[:, :2]
    q = np.clip((c / PX).astype(np.int64), 0, N - 1)
    on = grown[q[:, 1], q[:, 0]]
    if len(T) and not on.all():
        if on.any():
            ue_settle.write(os.path.join(out_dir, f"walks_{i}_{j}.glb"), f"walks_{i}_{j}", P, UV, C, T[on], M[on], names)
        else:
            os.remove(path)
    return had, kept


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    out_dir = a.out or a.dir
    os.makedirs(out_dir, exist_ok=True)
    cells = [(int(f[6:-4].split("_")[0]), int(f[6:-4].split("_")[1])) for f in os.listdir(a.dir) if f.startswith("walks_") and f.endswith(".glb")]
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as pool:
        got = list(pool.map(cell, [(a.dir, out_dir, i, j) for i, j in cells]))
    print(json.dumps({"walk_km2": round(sum(g[0] for g in got) / 1e6, 3), "kept_km2": round(sum(g[1] for g in got) / 1e6, 3)}))


if __name__ == "__main__":
    main()
