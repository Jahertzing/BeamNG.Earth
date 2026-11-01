#!/usr/bin/env python3
"""Road paint put on the pavement: the builders laid it at their own lift, about the height of ue_pavement's surface, so lines
fought with it or lay under it (lane dashes on I-25 not drawn at all, solid lines drawn in broken pieces). Each paint vertex on
the ground goes to the drawn terrain under it plus the pavement's lift and PAINT_UP_M; paint on a deck rises by PAINT_UP_M.
Run after ue_dashes.

    python tools/ue_paint_lift.py --dir <export dir> [--out <dir>] [--only road_-2_-1]
"""
import argparse
import concurrent.futures
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402

import ue_pavement  # noqa: E402
import ue_settle  # noqa: E402
import ue_survey  # noqa: E402

PAINT_UP_M = 0.025              # m over the pavement
ON_GROUND_M = 0.6               # m: paint this near the terrain is the terrain's; higher, it is on a deck
GRID_M = 2.0


def terrain_z(z, xy):
    """The terrain's height at xy (cell m): z (n, n) on GRID_M, each square's two triangles as ue_export_area's ground."""
    n = z.shape[0] - 1
    f = np.clip(xy / GRID_M, 0.0, n - 1e-6)
    c = f.astype(np.int64)
    t = f - c
    z00, z10 = z[c[:, 1], c[:, 0]], z[c[:, 1], c[:, 0] + 1]
    z01, z11 = z[c[:, 1] + 1, c[:, 0]], z[c[:, 1] + 1, c[:, 0] + 1]
    low = t.sum(1) <= 1.0
    return np.where(low, z00 + (z10 - z00) * t[:, 0] + (z01 - z00) * t[:, 1],
                    z11 + (z01 - z11) * (1 - t[:, 0]) + (z10 - z11) * (1 - t[:, 1]))


def cell(job):
    path, ground, out = job
    P, UV, C, T, M, names = ue_settle.read(path)
    paint = [k for k, n in enumerate(names) if n.startswith(ue_pavement.PAINT)]
    if not paint:
        return 0
    G = ue_survey.read_glb(ground)[0]["P"]
    n = int(round(len(G) ** 0.5))
    z = G[:, 2].reshape(n, n)
    used = np.unique(T[np.isin(M, paint)])
    gz = terrain_z(z, P[used, :2])
    on = np.abs(P[used, 2] - gz) <= ON_GROUND_M
    P = P.copy()
    P[used, 2] = np.where(on, gz + ue_pavement.LIFT_M + PAINT_UP_M, P[used, 2] + PAINT_UP_M)
    name = os.path.basename(path)[:-4]
    ue_settle.write(os.path.join(out, name + ".glb"), name, P, UV, C, T, M, names)
    return len(used)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    out = a.out or a.dir
    os.makedirs(out, exist_ok=True)
    jobs = []
    for f in sorted(os.listdir(a.dir)):
        if f.startswith("road_") and f.endswith(".glb") and (not a.only or f[:-4] == a.only):
            jobs.append((os.path.join(a.dir, f), os.path.join(a.dir, "ground_" + f[5:]), out))
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as pool:
        got = list(pool.map(cell, jobs))
    print(json.dumps({"cells": len(got), "paint_vertices": sum(got)}))


if __name__ == "__main__":
    main()
