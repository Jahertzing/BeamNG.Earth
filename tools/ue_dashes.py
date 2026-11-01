#!/usr/bin/env python3
"""Lane lines broken for real: the builders' dashed lines are solid strips whose texture has the gaps (BeamNG's decals), and
Unreal paints a strip by its material alone, so every lane line in the world came out solid and every crosswalk a white slab.
Each strip is cut to its dashes or bars by its own texture coordinate. Run after the pavement pass.

    python tools/ue_dashes.py --dir <export dir> [--out <dir>]
"""
import argparse
import concurrent.futures
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402

import ue_settle  # noqa: E402

# (paint in a repeat of the texture, repeats a texture v): a crosswalk's slab is cut to continental bars, 0.6 m on, 0.6 m off
DASH = {"line_dashed_long": (0.25, 1), "line_dashed_short_yellow": (0.5, 1), "crossing_white": (0.5, 4)}


def clip(poly, lo, hi):
    """poly [(position, uv, colour)...] cut to lo <= v <= hi."""
    for sign, edge in ((1.0, lo), (-1.0, hi)):
        out = []
        for k in range(len(poly)):
            a, b = poly[k], poly[(k + 1) % len(poly)]
            da, db = sign * (a[1][1] - edge), sign * (b[1][1] - edge)
            if da >= 0:
                out.append(a)
            if (da >= 0) != (db >= 0):
                t = da / (da - db)
                out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2]))
        poly = out
        if not poly:
            return poly
    return poly


def cell(job):
    path, out = job
    P, UV, C, T, M, names = ue_settle.read(path)
    cut = [k for k, n in enumerate(names) if n in DASH]
    if not cut:
        return 0
    nP, nUV, nC, nT, nM = [], [], [], [], []
    base = len(P)
    for m in cut:
        share, per = DASH[names[m]]
        for t in T[M == m]:
            vs = UV[t, 1]
            for k in range(math.floor(vs.min() * per), math.floor(vs.max() * per) + 1):
                poly = clip([(P[i], UV[i], C[i]) for i in t], k / per, (k + share) / per)
                if len(poly) < 3:
                    continue
                first = base + len(nP)
                for p, uv, c in poly:
                    nP.append(p)
                    nUV.append(uv)
                    nC.append(c)
                for q in range(1, len(poly) - 1):
                    nT.append((first, first + q, first + q + 1))
                    nM.append(m)
    keep = ~np.isin(M, cut)
    P2 = np.r_[P, np.array(nP).reshape(-1, 3)]
    UV2 = np.r_[UV, np.array(nUV).reshape(-1, 2)]
    C2 = np.r_[C, np.array(nC, C.dtype).reshape(-1, C.shape[1])]
    T2 = np.r_[T[keep], np.array(nT, np.int64).reshape(-1, 3)]
    M2 = np.r_[M[keep], np.array(nM, np.int64)]
    name = os.path.basename(path)[:-4]
    ue_settle.write(os.path.join(out, name + ".glb"), name, P2, UV2, C2, T2, M2, names)
    return len(nT)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    out = a.out or a.dir
    os.makedirs(out, exist_ok=True)
    files = sorted(os.path.join(a.dir, f) for f in os.listdir(a.dir) if f.startswith("road_") and f.endswith(".glb") and (not a.only or f == f"road_{a.only}.glb"))
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as pool:
        got = list(pool.map(cell, [(f, out) for f in files]))
    print(json.dumps({"cells": len(files), "dash_tris": sum(got)}))


if __name__ == "__main__":
    main()
