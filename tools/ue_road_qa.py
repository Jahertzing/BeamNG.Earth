#!/usr/bin/env python3
"""Box-wide realism checks of an export, no Unreal needed, each fault with its place, worst first. Run after the pavement and
furniture passes:

    Earth.UE\\.venv\\Scripts\\python tools\\ue_road_qa.py --dir <export dir> --net <roadnet dir> --lamps <lamps.csv> --out <folder>

Checks: lanes     lane widths outside LANE_LIMITS on the roads that get lane lines
        posts     sign and signal posts (the collision meshes) on the pavement or under KERB_M from it
        lamps     lamps and poles on the pavement, in a building, floating or buried (ue_furniture_lint's numbers)
        paint     paint off the pavement, per cell
        walks     sidewalk over the pavement, per cell
        plants    trees and bushes on the pavement
        buildings a building's footprint over the pavement
Writes qa.json: {"summary", "faults": {check: [...]}}; the exit code is the count of checks over their limits.
"""
import argparse
import collections
import concurrent.futures
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402

import ue_furniture_lint as lint  # noqa: E402
import ue_road_eval  # noqa: E402
import ue_survey  # noqa: E402

CELL = lint.CELL
PX = lint.PX
LANE_LIMITS = (2.7, 4.0)
LANE_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary"}
KERB_M = 0.3
LIMITS = {"lanes_km": 1.0, "posts_on_pavement": 0, "lamps_on_pavement": 0, "paint_off_share": 0.02, "walk_over_share": 0.01, "plants_on_pavement": 0, "buildings_over_m2": 200}
PAINT = ("line_", "crossing", "roadmarkings")


def lanes(net_dir):
    with open(os.path.join(net_dir, "roadnet.json")) as f:
        links = json.load(f)["links"]
    out = []
    seen = set()
    for lk in links:
        if lk["class"] not in LANE_CLASSES or lk["ramp"]:
            continue
        key = tuple(sorted((lk["from"], lk["to"])))
        if key in seen:
            continue
        seen.add(key)
        lw = float(lk["lane_width"] or 0.0)
        if lw and not LANE_LIMITS[0] <= lw <= LANE_LIMITS[1]:
            p = np.array(lk["points"])[:, :2]
            L = float(np.hypot(*np.diff(p, axis=0).T).sum())
            m = p[len(p) // 2]
            out.append({"link": lk["id"], "class": lk["class"], "name": lk["name"], "lane_width": round(lw, 2), "lanes": len(lk["lanes"]), "width": lk["width"],
                        "length_m": round(L), "xy": [round(float(m[0])), round(float(m[1]))]})
    return sorted(out, key=lambda f: -f["length_m"])


def components(prim):
    """Foot points [(x, y, height)] of the connected pieces of a mesh."""
    P, T = prim["P"], prim["T"]
    # (a box's faces can come unshared: corners that meet are one vertex here)
    P, inv = np.unique(np.round(P, 2), axis=0, return_inverse=True)
    T = inv.reshape(-1)[T]
    n = len(P)
    parent = np.arange(n)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b, c in T:
        ra, rb, rc = find(int(a)), find(int(b)), find(int(c))
        parent[rb] = ra
        parent[rc] = ra
    roots = np.array([find(i) for i in range(n)])
    out = []
    for r in np.unique(roots):
        q = P[roots == r]
        low = q[q[:, 2] < q[:, 2].min() + 0.3]
        out.append((float(low[:, 0].mean()), float(low[:, 1].mean()), float(q[:, 2].max() - q[:, 2].min()), float(np.ptp(q[:, 0])), float(np.ptp(q[:, 1]))))
    return out


def cell_checks(job):
    export, i, j = job
    org = np.array([i * CELL, j * CELL, 0.0])
    pave, deck, walk, bld, o = lint.cell_masks(export, i, j)
    sd = ndimage.distance_transform_edt(~pave) * PX - ndimage.distance_transform_edt(pave) * PX
    n = pave.shape[0]

    def at(m, xy):
        c = np.clip(((np.asarray(xy) - o) / PX).astype(np.int64), 0, n - 1)
        return m[c[:, 1], c[:, 0]]
    # (read in this cell's window only)
    paint = np.zeros((n, n), bool)
    road = os.path.join(export, f"road_{i}_{j}.glb")
    orgp = np.array([o[0] - i * CELL, o[1] - j * CELL])
    for prim in ue_survey.read_glb(road) if os.path.exists(road) else []:
        if prim["name"].startswith(PAINT):
            paint = lint.fill(paint, prim["P"][:, :2], prim["T"], orgp)
    w = ndimage.binary_erosion(pave, iterations=2)
    res = {"cell": [i, j], "paint_m2": float(paint.sum() * PX * PX), "paint_off_m2": float((paint & ~pave & ~deck).sum() * PX * PX),
           "walk_m2": float(walk.sum() * PX * PX), "walk_over_m2": float((walk & w).sum() * PX * PX),
           "bld_over_m2": float((ndimage.binary_erosion(bld, iterations=1) & w & ~deck).sum() * PX * PX), "posts": [], "plants": []}
    for kind in ("signcol", "signalcol"):
        path = os.path.join(export, f"{kind}_{i}_{j}.glb")
        if not os.path.exists(path):
            continue
        for prim in ue_survey.read_glb(path):
            prim["P"] = prim["P"] + org
            cs = components(prim)
            if not cs:
                continue
            xy = np.array([c[:2] for c in cs])
            s, dk = at(sd, xy), at(deck, xy)
            for c, d, k in zip(cs, s, dk):
                if d < KERB_M and not k and max(c[3], c[4]) < 1.5:
                    res["posts"].append({"kind": kind, "xy": [round(c[0], 1), round(c[1], 1)], "dist": round(float(d), 2), "h": round(c[2], 1)})
    path = os.path.join(export, f"scatter_{i}_{j}.npz")
    if os.path.exists(path):
        d = np.load(path)
        key = "xy" if "xy" in d else ("xyz" if "xyz" in d else None)
        if key:
            xy = d[key][:, :2] + (0 if d[key][:, 0].min() < -1 else org[:2])
            s = at(sd, xy)
            res["plants"] = [[round(float(x), 1), round(float(y), 1)] for (x, y), v in zip(xy, s) if v < 0][:200]
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick-r3")
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--lamps", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    cells = sorted((int(os.path.basename(f).split("_")[1]), int(os.path.basename(f).split("_")[2].split(".")[0])) for f in glob.glob(os.path.join(a.dir, "road_*.glb")))
    lane_faults = lanes(a.net)
    rows = []
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as pool:
        for r in pool.map(cell_checks, [(a.dir, i, j) for i, j in cells]):
            rows.append(r)
    paint, off = sum(r["paint_m2"] for r in rows), sum(r["paint_off_m2"] for r in rows)
    walk, over = sum(r["walk_m2"] for r in rows), sum(r["walk_over_m2"] for r in rows)
    posts = [p for r in rows for p in r["posts"]]
    plants = [p for r in rows for p in r["plants"]]
    by_kind = collections.Counter(p["kind"] for p in posts)
    summary = {"lanes_km": round(sum(f["length_m"] for f in lane_faults) / 1000.0, 1), "lanes_links": len(lane_faults), "posts_on_pavement": len(posts), "posts_by_kind": dict(by_kind),
               "paint_off_share": round(off / max(paint, 1.0), 4), "walk_over_share": round(over / max(walk, 1.0), 4), "plants_on_pavement": len(plants),
               "buildings_over_m2": round(sum(r["bld_over_m2"] for r in rows))}
    print(json.dumps(summary, indent=1))
    cells_paint = sorted(rows, key=lambda r: -r["paint_off_m2"])[:8]
    print("worst paint cells", [(r["cell"], round(r["paint_off_m2"])) for r in cells_paint])
    print("worst building cells", [(r["cell"], round(r["bld_over_m2"])) for r in sorted(rows, key=lambda r: -r["bld_over_m2"])[:8]])
    print("worst lane links", lane_faults[:12])
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "qa.json"), "w") as f:
            json.dump({"summary": summary, "faults": {"lanes": lane_faults, "posts": posts, "plants": plants, "cells": [{k: v for k, v in r.items() if k not in ("posts", "plants")} for r in rows]}}, f, separators=(",", ":"))
    return sum(1 for k, v in LIMITS.items() if summary.get(k, 0) > v)


if __name__ == "__main__":
    sys.exit(main())
