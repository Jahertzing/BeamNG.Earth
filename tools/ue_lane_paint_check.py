#!/usr/bin/env python3
"""Do the cars drive between the lines? Each traffic lane of roadnet.json, sampled along painted roads, against the paint drawn in
the export: a lane centre under ASTRIDE_M from a painted line has its cars straddling it.

    python tools/ue_lane_paint_check.py --dir <export dir> [--net <roadnet dir>] [--out <folder>]
"""
import argparse
import collections
import concurrent.futures
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from scipy import ndimage  # noqa: E402

import ue_settle  # noqa: E402

CELL = 2048.0
PX = 0.25
N = int(CELL / PX)
ASTRIDE_M = 0.9
CLASSES = {"trunk", "primary", "secondary", "tertiary", "motorway"}


def line_mask(path):
    img = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(img)
    P, _, _, T, M, names = ue_settle.read(path)
    keep = [k for k, n in enumerate(names) if n.startswith("line_")]
    if keep:
        for t in T[np.isin(M, keep)]:
            q = P[t, :2] / PX
            if q.max() >= 0 and q.min() < N:
                d.polygon([tuple(v) for v in q], fill=255)
    return np.asarray(img) > 0


def cell(job):
    path, pts = job
    m = line_mask(path)
    if not m.any():
        return []
    d = ndimage.distance_transform_edt(~m) * PX
    q = np.clip((pts[:, :2] / PX).astype(np.int64), 0, N - 1)
    return d[q[:, 1], q[:, 0]].tolist()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--out", default=None)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    with open(os.path.join(a.net, "roadnet.json")) as f:
        net = json.load(f)
    by_cell = collections.defaultdict(list)
    meta = collections.defaultdict(list)
    for lk in net["links"]:
        if lk["level"] != 0 or lk["ramp"] or lk["class"] not in CLASSES or lk.get("control") == "signal":
            continue
        p = np.array(lk["points"])[:, :2]
        L = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
        if L[-1] < 74.0:
            continue
        t = np.arange(30.0, L[-1] - 30.0, 6.0)
        c = np.c_[np.interp(t, L, p[:, 0]), np.interp(t, L, p[:, 1])]
        u = np.gradient(c, axis=0)
        n = np.c_[-u[:, 1], u[:, 0]] / np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-6)
        for o in lk["lanes"]:
            q = c + n * float(o)
            k = np.floor(q / CELL).astype(np.int64)
            for key in {tuple(v) for v in k.tolist()}:
                got = q[(k == key).all(1)]
                by_cell[key].append(got - (key[0] * CELL, key[1] * CELL))
                meta[key].append((lk["class"], lk["id"], len(got)))
    jobs, keys = [], []
    for key, v in by_cell.items():
        path = os.path.join(a.dir, f"road_{key[0]}_{key[1]}.glb")
        if os.path.exists(path):
            jobs.append((path, np.concatenate(v)))
            keys.append(key)
    tot, bad = collections.Counter(), collections.Counter()
    worst = collections.Counter()
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as pool:
        for key, d in zip(keys, pool.map(cell, jobs)):
            if not d:
                continue
            i = 0
            for cls, lid, n in meta[key]:
                part = np.array(d[i:i + n])
                i += n
                tot[cls] += n
                b = int((part < ASTRIDE_M).sum())
                bad[cls] += b
                worst[lid] += b
    print(json.dumps({c: {"km": round(tot[c] * 6 / 1000), "astride_share": round(bad[c] / max(tot[c], 1), 3)} for c in sorted(tot)}))
    print("worst links", worst.most_common(10))
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "lane_paint.json"), "w") as f:
            json.dump({"by_class": {c: [tot[c], bad[c]] for c in tot}, "worst": worst.most_common(200)}, f)


if __name__ == "__main__":
    main()
