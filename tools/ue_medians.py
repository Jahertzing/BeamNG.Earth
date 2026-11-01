#!/usr/bin/env python3
"""Medians and traffic islands raised to a curb: a strip of ground the pavement closes round, too narrow to be anything else,
stood flush with the road (a real one at Puritan Way is a raised concrete nose). A narrow one is all concrete; a wider one is
a curb round its planting, the ground left in the middle. On the terrain's triangles, WALK_UP_M over the pavement with a
curb's face, in walks_<i>_<j>.glb. Run after ue_driveways, once per pavement pass, before ue_mask: it adds to the walks it finds.

    python tools/ue_medians.py --dir <export dir> [--jobs 6] [--count]
"""
import argparse
import concurrent.futures
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402
import shapely  # noqa: E402
from scipy import ndimage  # noqa: E402

import ue_furniture  # noqa: E402
import ue_mask  # noqa: E402
import ue_pavement  # noqa: E402
import ue_settle  # noqa: E402
import ue_survey  # noqa: E402

CELL = ue_mask.CELL
PX = ue_mask.PX
HALF_WIDE_M = 4.0               # m from the middle of a strip to the pavement: no wider, or it is a block of its own
MIN_M2 = 6.0
MAX_M2 = 2500.0
MAX_WALK_SHARE = 0.3            # of a strip that is already sidewalk
SOLID_M = 0.9                   # m from the middle of a strip to its edge: no wider, and it is concrete all through
CURB_M = 0.75                   # m of a wider strip's edge that is curb
STEP_M = 0.6                    # m of the raster's stairs smoothed off an outline
TUCK_M = 0.35                   # m an island runs on over the pavement's edge: it hides that edge's wander


def islands(export, i, j, above=0.0, walk_share=MAX_WALK_SHARE):
    """(cell raster, 0.5 m a pixel, row 0 south, of the strips to raise, (count, m2), rows x, y, m2, half width, kind). above: only
    strips wider than this half width."""
    _, sd16, fl = ue_furniture.raster_cell((export, i, j))
    sd = sd16 / ue_mask.SD_Q
    free = (sd > 0) & ((fl & (ue_furniture.F_BLD | ue_furniture.F_DECK)) == 0)
    lab, n = ndimage.label(sd > 0)
    if not n:
        return None, (0, 0.0), []
    ids = np.arange(1, n + 1)
    size = ndimage.sum_labels(np.ones(lab.shape), lab, ids) * PX * PX
    top = ndimage.maximum(sd, lab, ids)
    walk = ndimage.sum_labels((fl & ue_furniture.F_WALK) > 0, lab, ids) * PX * PX
    bad = ndimage.sum_labels(~free & (sd > 0), lab, ids)
    edge = np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    good = (top <= HALF_WIDE_M) & (top > above) & (size >= MIN_M2) & (size <= MAX_M2) & (walk <= walk_share * size) & (bad == 0)
    good[edge[edge > 0] - 1] = False
    a = int(ue_mask.MARGIN_M / PX)
    m = int(CELL / PX)
    keep = np.r_[False, good][lab][a:a + m, a:a + m]
    if not keep.any():
        return None, (0, 0.0), []
    inside = lab[a:a + m, a:a + m]
    who = np.unique(inside[keep])
    com = ndimage.center_of_mass(keep, inside, who)
    rows = [[round(c[1] * PX + i * CELL, 1), round(c[0] * PX + j * CELL, 1), round(float(size[k - 1])), round(float(top[k - 1]), 1),
             "solid" if top[k - 1] <= SOLID_M else "curbed"] for k, c in zip(who, com)]
    return keep, (len(who), float(keep.sum()) * PX * PX), rows


def smooth(poly):
    """The outline with its raster steps taken off, tucked a little under the pavement's edge."""
    p = poly.simplify(TUCK_M).buffer(STEP_M).buffer(-STEP_M)
    q = p.buffer(-STEP_M).buffer(STEP_M)
    if not q.is_empty and q.area >= 0.8 * p.area:
        p = q
    return p.buffer(TUCK_M)


def raised(polys):
    """What stands up of each island: all of a narrow one, of a wider one the band CURB_M in from its one smoothed outline."""
    out = []
    for poly in polys:
        p = smooth(poly)
        if p.is_empty:
            continue
        if not p.buffer(-SOLID_M).is_empty:
            p = p.difference(p.buffer(-CURB_M))
        out.extend(shapely.get_parts(shapely.make_valid(p)).tolist())
    return [p for p in out if p.geom_type == "Polygon" and not p.is_empty]


def cell(job):
    export, i, j, count, above = job
    keep, got, rows = islands(export, i, j, above, 1.0 if count else MAX_WALK_SHARE)   # (counted after a pass, its strips are walks)
    if keep is None or count:
        return got, rows
    x0, y0 = i * CELL, j * CELL
    k = int(PX / ue_pavement.PX)
    band = np.repeat(np.repeat(keep, k, axis=0), k, axis=1)
    ground = ue_survey.read_glb(os.path.join(export, f"ground_{i}_{j}.glb"))[0]
    N = int(round(len(ground["P"]) ** 0.5)) - 1
    z = ground["P"][:, 2].reshape(N + 1, N + 1)
    polys = raised(ue_pavement.polygons(band, x0, y0))
    if not polys:
        return got, rows
    near = ndimage.binary_erosion(band, iterations=int((CURB_M + 0.5) / ue_pavement.PX))       # (none of a curbed strip is whole)
    P, T, _ = ue_pavement.clip_terrain(polys, band & ~near, z, x0, y0, np.zeros(band.shape, np.uint8))
    if not len(T):
        return got, rows
    P = P.copy()
    P[:, 2] += ue_pavement.LIFT_M + ue_pavement.WALK_UP_M
    P, T = ue_pavement.skirted(P, T, ue_pavement.WALK_UP_M + 0.05)
    P = P - (x0, y0, 0.0)
    path = os.path.join(export, f"walks_{i}_{j}.glb")
    if os.path.exists(path):
        P0, UV0, C0, T0, M0, names = ue_settle.read(path)
        at = len(P0)
        walk = names.index("sidewalk1") if "sidewalk1" in names else 0
        UV = P[:, :2] / 2.0
        ue_settle.write(path, f"walks_{i}_{j}", np.r_[P0, P], np.r_[UV0, UV], np.r_[C0, np.full((len(P), 4), 255, C0.dtype)],
                        np.r_[T0, T + at], np.r_[M0, np.full(len(T), walk, M0.dtype)], names)
    else:
        g = ue_export.Glb()
        g.add(P, T, P[:, :2] / 2.0, ["sidewalk1"], None, np.full((len(P), 4), 255, np.uint8))
        g.write(path, f"walks_{i}_{j}", (0.0, 0.0, 0.0))
    return got, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick-r6")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--count", action="store_true", help="say how much, change nothing")
    ap.add_argument("--above", type=float, default=0.0, help="only strips wider than this half width (m): a pass that adds to an earlier one")
    ap.add_argument("--csv", default=None, help="the strips' list: x, y, m2, half_width_m, kind")
    a = ap.parse_args()
    cells = [tuple(int(v) for v in f[7:-4].split("_")) for f in os.listdir(a.dir) if f.startswith("ground_") and f.endswith(".glb")]
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as pool:
        got = list(pool.map(cell, [(a.dir, i, j, a.count, a.above) for i, j in cells]))
    if a.csv:
        with open(a.csv, "w") as f:
            print("x,y,m2,half_width_m,kind", file=f)
            for _, rows in got:
                for r in rows:
                    print(",".join(str(v) for v in r), file=f)
    print(json.dumps({"islands": sum(g[0][0] for g in got), "m2": round(sum(g[0][1] for g in got)), "cells": sum(1 for g in got if g[0][0])}))


if __name__ == "__main__":
    main()
