#!/usr/bin/env python3
"""How far roads with no surveyed curb sit off the aerial: along each paved piece the photo's cross-section is averaged over
CHUNK_M of road, and the strip as wide as the road that stands out most from its flanks gives the offset there. A piece's
offset is the median of its chunks, kept only where they agree. Nothing changes; it reports.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_aerial_offset.py --net <roadnet dir> --dir <export dir> [--json <file>]
"""
import argparse
import json
import math
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402

import ue_road_eval  # noqa: E402

CELL = 2048.0
CHUNK_M = 60.0                  # m of road averaged into one cross-section
REACH_M = 14.0                  # m either side of the line searched
STEP_M = 0.5                    # m between samples across
FLANK_M = 3.0                   # m of ground either side of the strip
MIN_LEN_M = 150.0
AGREE_M = 2.5                   # m a piece's chunks may spread (quartile to quartile) and still count
OFF_M = 3.0                     # m off the aerial reported


def luminance(cells, xy, export):
    """Photo brightness at frame points (n, 2), NaN off the box."""
    out = np.full(len(xy), np.nan)
    i = np.floor(xy[:, 0] / CELL).astype(np.int64)
    j = np.floor(xy[:, 1] / CELL).astype(np.int64)
    for a, b in set(zip(i.tolist(), j.tolist())):
        if (a, b) not in cells:
            path = os.path.join(export, f"ground_{a}_{b}.glb")
            cells[(a, b)] = (np.asarray(ue_road_eval.ground_image(path).convert("L"), np.float32)
                             if os.path.exists(path) else None)
        img = cells[(a, b)]
        if img is None:
            continue
        on = (i == a) & (j == b)
        k = img.shape[1] / CELL
        c = np.clip(((xy[on, 0] - a * CELL) * k).astype(np.int64), 0, img.shape[1] - 1)
        r = np.clip((img.shape[0] - 1 - (xy[on, 1] - b * CELL) * k).astype(np.int64), 0, img.shape[0] - 1)
        out[on] = img[r, c]
    return out


def strip_offset(section, width):
    """(offset m, contrast) of the strip of a cross-section most unlike its flanks."""
    wide = max(int(width / STEP_M), 2)
    flank = int(FLANK_M / STEP_M)
    v = np.r_[0.0, np.cumsum(section)]
    mid = len(section) // 2
    best = (0.0, 0.0)
    for a in range(flank, len(section) - wide - flank):
        band = (v[a + wide] - v[a]) / wide
        side = (v[a] - v[a - flank] + v[a + wide + flank] - v[a + wide]) / (2 * flank)
        got = abs(band - side)
        if got > best[1]:
            best = ((a + wide / 2 - mid) * STEP_M, float(got))
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick-r6")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    with open(os.path.join(a.net, "stations.pkl"), "rb") as f:
        pieces = pickle.load(f)
    across = np.arange(-REACH_M, REACH_M + STEP_M / 2, STEP_M)
    cells, found = {}, []
    total, read = 0.0, 0.0
    for p in pieces:
        sp = p["spec"]
        if p["kind"] != "ground" or sp["kind"] != "paved" or len(p["st"]) < 8 or sp["cls"] in ("service", "track", "unknown"):
            continue
        if np.isfinite(p["ab"][:, 0]).mean() > 0.1:
            continue
        length = float(p["t"][-1] - p["t"][0])
        if length < MIN_LEN_M:
            continue
        total += length
        width = float(np.median(p["edge_a"] - p["edge_b"]))
        offs = []
        for a0 in np.arange(float(p["t"][0]), float(p["t"][-1]) - CHUNK_M, CHUNK_M):
            on = (p["t"] >= a0) & (p["t"] < a0 + CHUNK_M)
            if on.sum() < 3:
                continue
            xy = (p["st"][on][:, None, :] + p["nrm"][on][:, None, :] * across[None, :, None]).reshape(-1, 2)
            lum = luminance(cells, xy, a.dir).reshape(on.sum(), len(across))
            if not np.isfinite(lum).all():
                continue
            o, got = strip_offset(np.median(lum, axis=0), abs(width))
            if got >= 12.0:
                offs.append(o)
        if len(offs) < 3:
            continue
        read += length
        q1, q2, q3 = np.percentile(offs, [25, 50, 75])
        if q3 - q1 <= AGREE_M and abs(q2) >= OFF_M:
            mid = p["st"][len(p["st"]) // 2]
            found.append({"name": p.get("name"), "cls": sp["cls"], "m": round(length), "offset": round(float(q2), 1),
                          "at": [round(float(mid[0])), round(float(mid[1]))], "fid": str(p["fid"]), "k": p["k"]})
    found.sort(key=lambda v: -v["m"])
    if a.json:
        with open(a.json, "w") as f:
            json.dump(found, f, indent=0)
    print(json.dumps({"km": round(total / 1000, 1), "read_km": round(read / 1000, 1), "off_km": round(sum(v["m"] for v in found) / 1000, 1),
                      "pieces_off": len(found)}))
    for v in found[:20]:
        print(v)


if __name__ == "__main__":
    main()
