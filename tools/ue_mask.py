#!/usr/bin/env python3
"""The drawn world as rasters on disk, for anything that places things and must keep off the road: per cell the distance to
the pavement and flags for sidewalk, deck and building, read from the export's own meshes.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_mask.py --dir <export dir>         (writes <export dir>/mask/mask_<i>_<j>.npz, about 2 min)

A file: sd (int16, 1/8 m a step, the distance to the pavement's edge, negative inside it) and flags (uint8: 1 sidewalk, 2 deck,
4 building, 1 m round it included), row 0 south, column 0 west, 0.5 m a pixel, the first pixel's corner 32 m south and west of
the cell's corner (cell i, j starts at frame x = 2048 i, y = 2048 j). Neighbouring cells overlap by those 32 m.

    import ue_mask
    m = ue_mask.Mask("<export dir>/mask")
    sd, flags = m.at(xy)       # xy (n, 2) frame m (x east, y north): sd m, inf off the export; only numpy is needed
"""
import argparse
import concurrent.futures
import glob
import os

import numpy as np

CELL = 2048.0
PX = 0.5
MARGIN_M = 32.0
SD_Q = 8.0
WALK, DECK, BUILDING = 1, 2, 4


class Mask:
    def __init__(self, folder):
        self.dir = folder
        self.cells = {}

    def cell(self, k):
        if k not in self.cells:
            path = os.path.join(self.dir, f"mask_{k[0]}_{k[1]}.npz")
            if os.path.exists(path):
                with np.load(path) as d:
                    self.cells[k] = (d["sd"], d["flags"])
            else:
                self.cells[k] = None
        return self.cells[k]

    def at(self, xy):
        xy = np.asarray(xy, float).reshape(-1, 2)
        sd, fl = np.full(len(xy), np.inf), np.zeros(len(xy), np.uint8)
        c = np.floor(xy / CELL).astype(np.int64)
        for k in {tuple(v) for v in c.tolist()}:
            got = self.cell(k)
            if got is None:
                continue
            a = np.flatnonzero((c == k).all(1))
            q = np.clip(((xy[a] - (k[0] * CELL - MARGIN_M, k[1] * CELL - MARGIN_M)) / PX).astype(np.int64), 0, got[0].shape[0] - 1)
            sd[a] = got[0][q[:, 1], q[:, 0]] / SD_Q
            fl[a] = got[1][q[:, 1], q[:, 0]]
        return sd, fl


def write_cell(job):
    import ue_furniture
    export, i, j, out = job
    _, sd, fl = ue_furniture.raster_cell((export, i, j))
    np.savez_compressed(os.path.join(out, f"mask_{i}_{j}.npz"), sd=sd, flags=fl)


def main():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    out = os.path.join(a.dir, "mask")
    os.makedirs(out, exist_ok=True)
    cells = [tuple(int(v) for v in os.path.basename(f)[7:-4].split("_")) for f in glob.glob(os.path.join(a.dir, "ground_*.glb"))]
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as pool:
        list(pool.map(write_cell, [(a.dir, i, j, out) for i, j in cells]))
    print(len(cells), "cells in", out)


if __name__ == "__main__":
    main()
