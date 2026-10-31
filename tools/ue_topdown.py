#!/usr/bin/env python3
"""A top-down review picture of a box of the export, no Unreal needed: the ground photo with the pavement's outline, the paint
as the game draws it, sidewalks, lamps and poles on it, and optionally DRCOG's surveyed lines.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_topdown.py --dir <export dir> --box x0,y0,x1,y1 --png out.png [--px 0.25] [--lamps lamps.csv] [--survey] [--raw]

Colours: pavement outline red, surveyed curbs cyan, sidewalks light blue, solid paint white or yellow as painted, dashes
orange, crosswalks magenta, lamp posts green discs, wood poles brown discs. --raw leaves the photo bare: side by side with the
overlay it shows what the aerial really has.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from scipy import ndimage  # noqa: E402

import geodesy  # noqa: E402
import ue_road_eval  # noqa: E402
import ue_survey  # noqa: E402

CELL = 2048.0
COL = {"line_white": (255, 255, 255), "line_yellow": (255, 220, 0), "line_dashed": (255, 150, 0), "crossing": (255, 0, 255),
       "roadmarkings": (255, 0, 255)}


def cells(box):
    x0, y0, x1, y1 = box
    return [(i, j) for i in range(int(x0 // CELL), int((x1 - 1e-6) // CELL) + 1) for j in range(int(y0 // CELL), int((y1 - 1e-6) // CELL) + 1)]


def photo_of(export, box, px):
    x0, y0, x1, y1 = box
    w, h = int((x1 - x0) / px), int((y1 - y0) / px)
    out = Image.new("RGB", (w, h), (40, 40, 40))
    for i, j in cells(box):
        path = os.path.join(export, f"ground_{i}_{j}.glb")
        if not os.path.exists(path):
            continue
        im = ue_road_eval.ground_image(path)
        n = int(CELL / px)
        im = im.resize((n, n), Image.BILINEAR)
        out.paste(im, (int((i * CELL - x0) / px), int((y1 - (j + 1) * CELL) / px)))
    return out


def draw_tris(d, P, T, x0, y1, px, fill):
    uv = np.c_[(P[:, 0] - x0) / px, (y1 - P[:, 1]) / px]
    for t in uv[T]:
        d.polygon([tuple(v) for v in t], fill=fill)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick-r4")
    ap.add_argument("--box", required=True)
    ap.add_argument("--png", required=True)
    ap.add_argument("--px", type=float, default=0.25)
    ap.add_argument("--lamps", default=None)
    ap.add_argument("--survey", action="store_true")
    ap.add_argument("--raw", action="store_true")
    a = ap.parse_args()
    box = tuple(float(v) for v in a.box.split(","))
    x0, y0, x1, y1 = box
    photo = photo_of(a.dir, box, a.px)
    if a.raw:
        photo.save(a.png)
        return
    w, h = photo.size
    pave = Image.new("L", (w, h), 0)
    dp = ImageDraw.Draw(pave)
    over = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dv = ImageDraw.Draw(over)
    for i, j in cells(box):
        org = np.array([i * CELL, j * CELL, 0.0])
        road = os.path.join(a.dir, f"road_{i}_{j}.glb")
        prims = ue_survey.read_glb(road) if os.path.exists(road) else []
        for prim in prims:
            if prim["name"].startswith(ue_road_eval.SURFACE):
                draw_tris(dp, prim["P"] + org, prim["T"], x0, y1, a.px, 255)
        walks = os.path.join(a.dir, f"walks_{i}_{j}.glb")
        for prim in ue_survey.read_glb(walks) if os.path.exists(walks) else []:
            if not prim["name"].startswith("curbs"):
                draw_tris(dv, prim["P"] + org, prim["T"], x0, y1, a.px, (120, 200, 255, 110))
        for prim in prims:
            for key, col in COL.items():
                if prim["name"].startswith(key):
                    draw_tris(dv, prim["P"] + org, prim["T"], x0, y1, a.px, col + (255,))
                    break
    m = np.asarray(pave) > 0
    edge = m & ~ndimage.binary_erosion(m)
    rgb = np.asarray(photo).copy()
    rgb[edge] = (255, 30, 30)
    out = Image.fromarray(rgb).convert("RGBA")
    out.alpha_composite(over)
    d = ImageDraw.Draw(out)
    if a.survey:
        frame = geodesy.EnuFrame(*ue_export.FRAME)
        lines, _ = ue_survey.lines_in(frame, box)
        for ln in lines:
            d.line([((p[0] - x0) / a.px, (y1 - p[1]) / a.px) for p in ln], fill=(0, 255, 255, 255), width=1)
    if a.lamps:
        with open(a.lamps) as f:
            for row in csv.reader(f):
                k, x, y = int(row[0]), float(row[1]), float(row[2])
                if k in (0, 1) and x0 <= x < x1 and y0 <= y < y1:
                    u, v, r = (x - x0) / a.px, (y1 - y) / a.px, 1.0 / a.px
                    d.ellipse((u - r, v - r, u + r, v + r), fill=(0, 255, 0, 255) if k == 0 else (150, 80, 20, 255), outline=(0, 0, 0, 255))
    out.convert("RGB").save(a.png)


if __name__ == "__main__":
    main()
