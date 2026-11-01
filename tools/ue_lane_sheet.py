#!/usr/bin/env python3
"""Top-down pictures of road logic faults: the export's photo, paint and pavement (ue_topdown) with roadnet's lanes on it, arrows of
travel in cyan and the fault at the middle.

    python tools/ue_lane_sheet.py --dir <export dir> --net <roadnet dir> --at x,y;x,y;... --png out.png [--m 180] [--cols 3]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import ue_topdown  # noqa: E402


def lanes_near(links, c, r):
    out = []
    for lk in links:
        p = np.array(lk["points"])[:, :2]
        if lk["level"] != 0 or len(p) < 2 or np.hypot(*(p - c).T).min() > r * 1.6:
            continue
        d = np.gradient(p, axis=0)
        n = np.c_[-d[:, 1], d[:, 0]] / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-6)
        for o in lk["lanes"]:
            out.append((p + n * float(o), lk))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--at", required=True)
    ap.add_argument("--png", required=True)
    ap.add_argument("--m", type=float, default=180.0)
    ap.add_argument("--cols", type=int, default=3)
    a = ap.parse_args()
    with open(os.path.join(a.net, "roadnet.json")) as f:
        links = json.load(f)["links"]
    spots = [tuple(float(v) for v in s.split(",")) for s in a.at.split(";")]
    px = a.m / 600.0
    tiles = []
    tmp = a.png + ".tile.png"
    for k, (x, y) in enumerate(spots):
        box = (x - a.m / 2, y - a.m / 2, x + a.m / 2, y + a.m / 2)
        sys.argv = ["ue_topdown", "--dir", a.dir, "--box=" + ",".join(str(v) for v in box), "--png", tmp, "--px", str(px)]
        ue_topdown.main()
        im = Image.open(tmp).convert("RGB")
        d = ImageDraw.Draw(im)
        for line, lk in lanes_near(links, np.array([x, y]), a.m):
            col = (0, 255, 255) if lk["control"] is None else (0, 255, 0) if lk["control"] == "signal" else (255, 160, 200)
            q = [((u - box[0]) / px, (box[3] - v) / px) for u, v in line]
            d.line(q, fill=col, width=1)
            if len(q) >= 2:
                e = q[-1]
                d.ellipse((e[0] - 2, e[1] - 2, e[0] + 2, e[1] + 2), outline=col)
        d.text((4, 4), f"{k}: {x:.0f},{y:.0f}", fill=(255, 255, 0))
        tiles.append(im)
    os.remove(tmp)
    w, h = tiles[0].size
    rows = (len(tiles) + a.cols - 1) // a.cols
    sheet = Image.new("RGB", (w * a.cols, h * rows))
    for k, im in enumerate(tiles):
        sheet.paste(im, ((k % a.cols) * w, (k // a.cols) * h))
    sheet.save(a.png)


if __name__ == "__main__":
    main()
