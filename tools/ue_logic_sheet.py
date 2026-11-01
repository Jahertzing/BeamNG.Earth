#!/usr/bin/env python3
"""A contact sheet of the worst places logic.json found: each on the aerial with our pavement edge and our paint.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_logic_sheet.py --export <export dir> --png <file> [--check lane_step,overlap] [--skip 0]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

from PIL import Image, ImageDraw  # noqa: E402

import ue_road_sheet as sheet  # noqa: E402

W, H = 6, 4


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", default="C:/beamng-earth/Earth.UE/export/frederick-r5")
    ap.add_argument("--png", required=True)
    ap.add_argument("--check", default=None)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--m", type=float, default=110.0, help="m a tile shows")
    ap.add_argument("--size", default="6,4")
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.split(","))
    with open(os.path.join(a.export, "logic.json")) as f:
        found = json.load(f)["findings"]
    if a.check:
        want = a.check.split(",")
        found = [f_ for f_ in found if f_["check"] in want]
    picked, near = [], []
    for f_ in found:
        x, y = f_["at"]
        if any(abs(x - q[0]) < 60 and abs(y - q[1]) < 60 for q in near):
            continue
        near.append((x, y))
        picked.append(f_)
    picked = picked[a.skip:a.skip + w * h]
    sheet.TILE_M = a.m
    sheet.TILE_PX = 360
    out = Image.new("RGB", (w * sheet.TILE_PX, h * sheet.TILE_PX))
    for k, f_ in enumerate(picked):
        x, y = f_["at"]
        try:
            img = sheet.tile((0, float(x), float(y), 0.0), a.export, a.export, [])
        except Exception as e:
            print("tile", x, y, type(e).__name__, e)
            continue
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 359, 13], fill=(0, 0, 0))
        d.text((3, 1), f"{f_['check']} {f_.get('step', f_.get('lanes', ''))} {str(f_.get('name', ''))[:18]} {x:.0f},{y:.0f}", fill=(255, 255, 0))
        out.paste(img, ((k % w) * sheet.TILE_PX, (k // w) * sheet.TILE_PX))
    out.save(a.png)
    print(len(picked))


if __name__ == "__main__":
    main()
