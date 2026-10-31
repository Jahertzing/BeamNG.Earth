#!/usr/bin/env python3
"""Where the lamps and poles stand against the drawn world, no Unreal needed: every lamp post and wooden pole of a lamps.csv
measured on the export's pavement, sidewalks, buildings and ground. Reads the export's meshes at 0.5 m a pixel, a process a
cell.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_furniture_lint.py --dir <export dir> --lamps <lamps.csv> --out <folder> [--cells 0,-1;1,-1]

Writes faults.json (each fault with its place) and prints the counts. Faults:
    in_pavement     the foot on the drawn pavement
    on_kerb         the foot under KERB_CLEAR_M from the pavement's edge, not on it (a mirror hits it)
    in_building     the foot inside a building's footprint
    floating        the foot over FLOAT_M above the ground under it
    buried          the foot over FLOAT_M below it
    far_from_road   a lamp over FAR_M from any pavement (a pole line out in a field is fine)
    arm_away        the lamp's head further from the pavement than its foot
"""
import argparse
import concurrent.futures
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402

import ue_road_eval  # noqa: E402
import ue_survey  # noqa: E402

PX = 0.5
CELL = 2048.0
MARGIN_M = 32.0                 # m read round a cell, so a foot at its edge sees the kerb across it
KERB_CLEAR_M = 0.45             # m a pole's foot stays off the pavement's edge (MUTCD, AASHTO: 18 in. from the curb's face)
FLOAT_M = 0.35
FAR_M = 15.0
ARM_M = 2.7                     # m: the post's head out from its foot along the row's yaw
POST, POLE, HEAD, ARM = 0, 1, 2, 3


def mask_of(path, x0, y0, n, want=None, skip=None):
    out = np.zeros((n, n), bool)
    if not os.path.exists(path):
        return out
    org = None
    for prim in ue_survey.read_glb(path):
        if (want and not prim["name"].startswith(want)) or (skip and prim["name"].startswith(skip)):
            continue
        org = (x0, y0)
        out = fill(out, prim["P"][:, :2], prim["T"], org)
    return out


def fill(mask, xy, T, org):
    from PIL import Image, ImageDraw
    img = Image.fromarray(mask.astype(np.uint8) * 255)
    d = ImageDraw.Draw(img)
    uv = (xy - org) / PX
    tri = uv[T]
    n = mask.shape[0]
    keep = (tri[..., 0].max(1) >= 0) & (tri[..., 1].max(1) >= 0) & (tri[..., 0].min(1) < n) & (tri[..., 1].min(1) < n)
    for t in tri[keep]:
        d.polygon([tuple(v) for v in t], fill=255)
    return np.asarray(img) > 0


def cell_masks(export, i, j):
    """Masks over the cell and MARGIN_M round it (row 0 = its south edge); the neighbours' meshes are read too."""
    n = int((CELL + 2 * MARGIN_M) / PX)
    ox, oy = i * CELL - MARGIN_M, j * CELL - MARGIN_M
    pave, deck, walk, bld = (np.zeros((n, n), bool) for _ in range(4))
    for a in range(i - 1, i + 2):
        for b in range(j - 1, j + 2):
            org = np.array([ox - a * CELL, oy - b * CELL])
            road = os.path.join(export, f"road_{a}_{b}.glb")
            if os.path.exists(road):
                for prim in ue_survey.read_glb(road):
                    if prim["name"].startswith(ue_road_eval.SURFACE):
                        pave = fill(pave, prim["P"][:, :2], prim["T"], org)
            for kind, m in (("deck", deck), ("walks", walk), ("bldcol", bld)):
                path = os.path.join(export, f"{kind}_{a}_{b}.glb")
                if not os.path.exists(path):
                    continue
                for prim in ue_survey.read_glb(path):
                    if kind == "walks" and prim["name"].startswith("curbs"):
                        continue
                    m[:] = fill(m, prim["P"][:, :2], prim["T"], org)
    return pave, deck, walk, bld, (ox, oy)


def check_cell(job):
    export, i, j, rows = job
    pave, deck, walk, bld, org = cell_masks(export, i, j)
    sd = ndimage.distance_transform_edt(~pave) * PX - ndimage.distance_transform_edt(pave) * PX
    n = pave.shape[0]

    def at(m, xy):
        c = np.clip(((xy - org) / PX).astype(np.int64), 0, n - 1)
        return m[c[:, 1], c[:, 0]]
    import ue_roadnet_out
    h = ue_roadnet_out.Heights(export)
    R = np.array(rows, float)
    xy = R[:, 1:3]
    yaw = np.radians(R[:, 4])
    head = xy + np.c_[np.cos(yaw), np.sin(yaw)] * ARM_M
    d = at(sd, xy)
    z0 = h.ground(xy)
    return {"cell": [i, j], "rows": R.tolist(), "dist": d.tolist(), "dist_head": at(sd, head).tolist(), "on_walk": at(walk, xy).tolist(),
            "on_deck": at(deck, xy).tolist(), "in_bld": at(bld, xy).tolist(), "dz": (R[:, 3] - z0).tolist()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick-r4")
    ap.add_argument("--lamps", default="C:/beamng-earth/Earth.UE/game/Plugins/EarthSky/Data/lamps.csv")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cells", default=None)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    by_cell = {}
    with open(a.lamps) as f:
        for row in csv.reader(f):
            r = [float(v) for v in row]
            if int(r[0]) in (POST, POLE):
                by_cell.setdefault((int(r[1] // CELL), int(r[2] // CELL)), []).append(r)
    want = None if not a.cells else {tuple(int(v) for v in c.split(",")) for c in a.cells.split(";")}
    jobs = [(a.dir, i, j, v) for (i, j), v in sorted(by_cell.items()) if (want is None or (i, j) in want) and
            os.path.exists(os.path.join(a.dir, f"road_{i}_{j}.glb"))]
    faults, n_rows = [], {POST: 0, POLE: 0}
    counts = {}
    hist = {POST: [], POLE: []}
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as pool:
        for r in pool.map(check_cell, jobs):
            for row, d, dh, w, dk, b, dz in zip(r["rows"], r["dist"], r["dist_head"], r["on_walk"], r["on_deck"], r["in_bld"], r["dz"]):
                kind = int(row[0])
                n_rows[kind] += 1
                hist[kind].append(d)
                why = []
                if d <= 0 and not dk:
                    why.append("in_pavement")
                elif d < KERB_CLEAR_M and not dk:
                    why.append("on_kerb")
                if b:
                    why.append("in_building")
                if np.isfinite(dz) and not dk:
                    if dz > FLOAT_M:
                        why.append("floating")
                    elif dz < -FLOAT_M:
                        why.append("buried")
                if kind == POST and d > FAR_M:
                    why.append("far_from_road")
                if kind == POST and d > 0 and dh > d + 0.5:
                    why.append("arm_away")
                for w_ in why:
                    counts[(kind, w_)] = counts.get((kind, w_), 0) + 1
                if why:
                    faults.append({"kind": "post" if kind == POST else "pole", "xy": [row[1], row[2]], "z": row[3], "yaw": row[4], "dist": round(d, 2),
                                   "dz": round(dz, 2) if np.isfinite(dz) else None, "why": why, "on_walk": bool(w)})
    report = {"rows": {"post": n_rows[POST], "pole": n_rows[POLE]}}
    for kind, name in ((POST, "post"), (POLE, "pole")):
        report[name] = {w: c for (k, w), c in sorted(counts.items()) if k == kind}
        if hist[kind]:
            report[name]["dist_pct"] = {p: round(float(np.percentile(hist[kind], p)), 2) for p in (1, 5, 25, 50, 75, 95, 99)}
    print(json.dumps(report, indent=1))
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "furniture_faults.json"), "w") as f:
            json.dump({"report": report, "faults": faults}, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
