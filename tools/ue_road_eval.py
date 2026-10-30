#!/usr/bin/env python3
"""Score an exported cell's road surface against DRCOG's surveyed edges of pavement (2024, stereo-compiled from 3-12 inch
aerials: drcog.py), and draw the overlay on the cell's own ground photo.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_road_eval.py --dir <export dir> --cell -2,-1 [--png overlay.png] [--box x0,y0,x1,y1]

Boundary score: the drawn pavement's edge (the mask's outline) against the surveyed lines. precision: share of the drawn
edge within TOL of a surveyed line. recall: share of the surveyed lines near a road within TOL of a drawn edge.
"""
import argparse
import json
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402  (the world folder, server/ on the path)

import geodesy  # noqa: E402
import ue_survey  # noqa: E402

PX = 0.25                       # m a pixel
CELL = 2048.0
TOLS = (0.5, 1.0, 2.0)
ZONE_M = 6.0                    # m: the drawn edge this near a surveyed line counts for precision
NEAR_M = 12.0                   # m: surveyed lines this close to a drawn road count for recall (not a far parking lot's)
SURFACE = ("road_asphalt", "m_dirt_road", "bne_bridge_asphalt", "m_gm_concrete_plain")


def fill_tris(mask, xy, T, x0, y0):
    """Rasterise triangles into mask (bool, row 0 = y0), PX a pixel: one PIL polygon each."""
    img = Image.fromarray(mask.astype(np.uint8) * 255)
    d = ImageDraw.Draw(img)
    uv = (xy - (x0, y0)) / PX
    tri = uv[T]
    keep = (tri[..., 0].max(1) >= 0) & (tri[..., 1].max(1) >= 0) & (tri[..., 0].min(1) < mask.shape[1]) & \
        (tri[..., 1].min(1) < mask.shape[0])
    for t in tri[keep]:
        d.polygon([tuple(v) for v in t], fill=255)
    return np.asarray(img) > 0


def line_mask(lines, shape, x0, y0):
    img = Image.new("L", shape[::-1], 0)
    d = ImageDraw.Draw(img)
    for ln in lines:
        d.line([tuple(v) for v in (ln - (x0, y0)) / PX], fill=255, width=1)
    return np.asarray(img) > 0


def score(surface, survey):
    """{tol: (precision, recall, F)}, and the drawn outline."""
    outline = surface & ~ndimage.binary_erosion(surface, iterations=1)
    d_survey = ndimage.distance_transform_edt(~survey) * PX
    d_drawn = ndimage.distance_transform_edt(~outline) * PX
    near = survey & (ndimage.distance_transform_edt(~surface) * PX <= NEAR_M)
    zone = outline & (d_survey <= ZONE_M)      # (a road the survey has: a lot's aisle or a gravel road's edge is not scored)
    out = {}
    for tol in TOLS:
        p = float((d_survey[zone] <= tol).mean())
        r = float((d_drawn[near] <= tol).mean())
        out[tol] = (round(p, 3), round(r, 3), round(2 * p * r / max(p + r, 1e-9), 3))
    return out, outline


def ground_image(path):
    with open(path, "rb") as f:
        data = f.read()
    n = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20:20 + n])
    binary = data[20 + n + 8:]
    v = doc["bufferViews"][doc["images"][0]["bufferView"]]
    import io
    return Image.open(io.BytesIO(binary[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]])).convert("RGB")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick")
    ap.add_argument("--cell", default="-2,-1")
    ap.add_argument("--png", default=None)
    ap.add_argument("--box", default=None, help="x0,y0,x1,y1 frame m: the overlay's window")
    ap.add_argument("--marks", action="store_true", help="draw the markings on the overlay too")
    a = ap.parse_args()
    i, j = (int(v) for v in a.cell.split(","))
    x0, y0 = i * CELL, j * CELL
    n = int(CELL / PX)
    frame = geodesy.EnuFrame(*ue_export.FRAME)
    mask = np.zeros((n, n), bool)
    for kind in ("road", "deck"):
        path = os.path.join(a.dir, f"{kind}_{i}_{j}.glb")
        if not os.path.exists(path):
            continue
        for prim in ue_survey.read_glb(path):
            if prim["name"].startswith(SURFACE):
                mask = fill_tris(mask, prim["P"][:, :2] + (x0, y0), prim["T"], x0, y0)
    lines = ue_survey.lines_in(frame, (x0, y0, x0 + CELL, y0 + CELL))[0]
    survey = line_mask(lines, mask.shape, x0, y0)
    s, outline = score(mask, survey)
    print(json.dumps({"cell": [i, j], "drawn_m2": int(mask.sum() * PX * PX), "survey_lines": len(lines),
                      "score(p,r,f)": {str(k): v for k, v in s.items()}}))
    if a.png:
        photo = ground_image(os.path.join(a.dir, f"ground_{i}_{j}.glb")).resize((n, n), Image.BILINEAR)
        rgb = np.asarray(photo)[::-1].copy()    # (row 0 at y0 from here on)
        rgb[outline] = (255, 40, 40)
        rgb[survey] = (0, 255, 255)
        if a.marks:
            for prim in ue_survey.read_glb(os.path.join(a.dir, f"road_{i}_{j}.glb")):
                if prim["name"].startswith(("line_", "crossing", "roadmarkings")):
                    yellow = "yellow" in prim["name"]
                    m = fill_tris(np.zeros((n, n), bool), prim["P"][:, :2] + (x0, y0), prim["T"], x0, y0)
                    rgb[m] = (255, 220, 0) if yellow else (255, 255, 255)
        if a.box:
            bx0, by0, bx1, by1 = (float(v) for v in a.box.split(","))
            c0, c1 = int((bx0 - x0) / PX), int((bx1 - x0) / PX)
            r0, r1 = int((by0 - y0) / PX), int((by1 - y0) / PX)
            rgb = rgb[r0:r1, c0:c1]
        Image.fromarray(rgb[::-1]).save(a.png)


if __name__ == "__main__":
    main()
