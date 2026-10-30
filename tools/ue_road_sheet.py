#!/usr/bin/env python3
"""The 2D test of the road generator, no Unreal needed: every cell of an export scored against the surveyed curbs, every
junction scored on its own, and a contact sheet of the worst ones on the aerial photo (red: our pavement edge, cyan: the
surveyed curb, white and yellow: our markings).

    Earth.UE\\.venv\\Scripts\\python tools\\ue_road_sheet.py --dir <pavement dir> --ground <export dir> --net <roadnet dir>

Writes score.json and worst.png in --dir. Bigger is better: edge F at 1 m is the headline number.
"""
import argparse
import concurrent.futures
import json
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from scipy import ndimage  # noqa: E402

import ue_road_eval  # noqa: E402
import ue_survey  # noqa: E402

PX = 0.25
CELL = 2048.0
TOL_M = 1.0
ZONE_M = 6.0
NEAR_M = 12.0
WINDOW_M = 36.0                 # m round a junction for its own score
TILE_M = 72.0                   # m a contact sheet tile shows
TILE_PX = 360
SHEET = (6, 4)
_w = {}


def init_worker(net):
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    with open(os.path.join(net, "survey.pkl"), "rb") as f:
        _w["lines"] = pickle.load(f)


def cell_maps(job):
    """(cell counts, junction rows) of cell i, j."""
    i, j, pave_dir, ground_dir, jn = job
    x0, y0 = i * CELL, j * CELL
    n = int(CELL / PX)
    mask = np.zeros((n, n), bool)
    for kind, d in (("road", pave_dir), ("deck", ground_dir)):
        path = os.path.join(d, f"{kind}_{i}_{j}.glb")
        if not os.path.exists(path):
            continue
        for prim in ue_survey.read_glb(path):
            if prim["name"].startswith(ue_road_eval.SURFACE):
                mask = ue_road_eval.fill_tris(mask, prim["P"][:, :2] + (x0, y0), prim["T"], x0, y0)
    survey = ue_survey.Raster(_w["lines"], (x0, y0, x0 + CELL, y0 + CELL)).mask
    outline = mask & ~ndimage.binary_erosion(mask, iterations=1)
    d_survey = ndimage.distance_transform_edt(~survey) * PX
    d_drawn = ndimage.distance_transform_edt(~outline) * PX
    near = survey & (ndimage.distance_transform_edt(~mask) * PX <= NEAR_M)
    zone = outline & (d_survey <= ZONE_M)
    counts = {}
    for tol in (0.5, 1.0, 2.0):
        counts[str(tol)] = [int((d_survey[zone] <= tol).sum()), int(zone.sum()), int((d_drawn[near] <= tol).sum()), int(near.sum())]
    rows = []
    w = int(WINDOW_M / PX)
    for jid, x, y in jn:
        c, r = int((x - x0) / PX), int((y - y0) / PX)
        a, b, e, f = max(r - w, 0), min(r + w, n), max(c - w, 0), min(c + w, n)
        z, s = zone[a:b, e:f], near[a:b, e:f]
        if z.sum() < 200 or s.sum() < 200:
            continue
        p = float((d_survey[a:b, e:f][z] <= TOL_M).mean())
        q = float((d_drawn[a:b, e:f][s] <= TOL_M).mean())
        rows.append((jid, round(x, 1), round(y, 1), round(2 * p * q / max(p + q, 1e-9), 3), round(p, 3), round(q, 3)))
    return (i, j), counts, rows


def tile(row, pave_dir, ground_dir, lines):
    """A contact sheet tile round a junction."""
    jid, x, y, f = row[:4]
    i, j = int(x // CELL), int(y // CELL)
    x0, y0 = i * CELL, j * CELL
    photo = ue_road_eval.ground_image(os.path.join(ground_dir, f"ground_{i}_{j}.glb"))
    k = photo.size[0] / CELL
    box = ((x - x0 - TILE_M / 2) * k, photo.size[1] - (y - y0 + TILE_M / 2) * k,
           (x - x0 + TILE_M / 2) * k, photo.size[1] - (y - y0 - TILE_M / 2) * k)
    img = photo.crop(tuple(int(v) for v in box)).resize((TILE_PX, TILE_PX), Image.BILINEAR)
    d = ImageDraw.Draw(img)
    s = TILE_PX / TILE_M

    def xy(p):
        return (p[..., 0] - (x - TILE_M / 2)) * s, TILE_PX - (p[..., 1] - (y - TILE_M / 2)) * s
    for prim in ue_survey.read_glb(os.path.join(pave_dir, f"road_{i}_{j}.glb")):
        P = prim["P"][:, :2] + (x0, y0)
        keep = (np.abs(P[prim["T"]] - (x, y)).max(axis=(1, 2)) < TILE_M / 2 + 4)
        T = prim["T"][keep]
        if not len(T):
            continue
        if prim["name"].startswith(ue_road_eval.SURFACE):
            tri = np.sort(T[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
            edge, count = np.unique(tri, axis=0, return_counts=True)
            for a, b in edge[count == 1]:
                u, v = xy(P[[a, b]])
                d.line([(u[0], v[0]), (u[1], v[1])], fill=(255, 40, 40), width=1)
        elif prim["name"].startswith(("line_", "crossing", "roadmarkings")):
            col = (255, 220, 0) if "yellow" in prim["name"] else (255, 255, 255)
            for t in T:
                u, v = xy(P[t])
                d.polygon(list(zip(u.tolist(), v.tolist())), fill=col)
    for ln in lines:
        if np.abs(ln - (x, y)).max(axis=1).min() < TILE_M:
            u, v = xy(ln)
            d.line(list(zip(u.tolist(), v.tolist())), fill=(0, 255, 255), width=1)
    d.rectangle([0, 0, 150, 13], fill=(0, 0, 0))
    d.text((3, 1), f"j{jid} F {f:.2f}  {x:.0f},{y:.0f}", fill=(255, 255, 255))
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick-r-pave")
    ap.add_argument("--ground", default="C:/beamng-earth/Earth.UE/export/frederick-r")
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args()
    t0 = time.perf_counter()
    with open(os.path.join(a.net, "roadnet.json")) as f:
        net = json.load(f)
    by_cell = {}
    for j in net["junctions"]:
        if len(j["nodes"]) >= 1:
            by_cell.setdefault((int(j["xy"][0] // CELL), int(j["xy"][1] // CELL)), []).append((j["id"], j["xy"][0], j["xy"][1]))
    cells = [tuple(int(v) for v in f[5:-4].split("_")) for f in os.listdir(a.dir) if f.startswith("road_") and f.endswith(".glb")]
    jobs = [(i, j, a.dir, a.ground, by_cell.get((i, j), [])) for i, j in cells]
    totals, rows, per_cell = {}, [], {}
    with concurrent.futures.ProcessPoolExecutor(a.jobs, initializer=init_worker, initargs=(a.net,)) as pool:
        for key, counts, got in pool.map(cell_maps, jobs):
            rows += got
            for tol, v in counts.items():
                t = totals.setdefault(tol, [0, 0, 0, 0])
                for k in range(4):
                    t[k] += v[k]
            v = counts["1.0"]
            per_cell[f"{key[0]},{key[1]}"] = round(2 * (v[0] / max(v[1], 1)) * (v[2] / max(v[3], 1)) /
                                                   max(v[0] / max(v[1], 1) + v[2] / max(v[3], 1), 1e-9), 3)
    score = {}
    for tol, v in totals.items():
        p, r = v[0] / max(v[1], 1), v[2] / max(v[3], 1)
        score[tol] = {"precision": round(p, 3), "recall": round(r, 3), "F": round(2 * p * r / max(p + r, 1e-9), 3)}
    rows.sort(key=lambda v: v[3])
    fs = np.array([r[3] for r in rows]) if rows else np.zeros(1)
    doc = {"edge": score, "junctions_scored": len(rows), "junction_F_median": round(float(np.median(fs)), 3),
           "junctions_F_over_0.9": round(float((fs >= 0.9).mean()), 3), "junctions_F_over_0.75": round(float((fs >= 0.75).mean()), 3),
           "cells": per_cell, "worst": rows[:60]}
    with open(os.path.join(a.dir, "score.json"), "w") as f:
        json.dump(doc, f, indent=0)
    print(json.dumps({k: v for k, v in doc.items() if k not in ("cells", "worst")}), f"{time.perf_counter() - t0:.0f} s", flush=True)
    with open(os.path.join(a.net, "survey.pkl"), "rb") as f:
        lines = pickle.load(f)
    W, H = SHEET
    sheet = Image.new("RGB", (W * TILE_PX, H * TILE_PX))
    for k, row in enumerate(rows[:W * H]):
        sheet.paste(tile(row, a.dir, a.ground, lines), ((k % W) * TILE_PX, (k // W) * TILE_PX))
    sheet.save(os.path.join(a.dir, "worst.png"))


if __name__ == "__main__":
    main()
