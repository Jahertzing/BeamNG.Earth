#!/usr/bin/env python3
"""DRCOG's surveyed edges of pavement as frame-metre lines and rasters, for the road tools (drcog.py: the cells cached in
the world folder; no downloads)."""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import drcog  # noqa: E402
import mapcells  # noqa: E402
import netfetch  # noqa: E402

PX = 0.25


def lines_in(frame, box):
    """The surveyed lines [(k, 2) frame m] of the map cells meeting box (x0, y0, x1, y1), each once."""
    x0, y0, x1, y1 = box
    corners = [frame.enu_to_geodetic(x, y, 0.0) for x in (x0, x1) for y in (y0, y1)]
    lat, lon = [float(c[0]) for c in corners], [float(c[1]) for c in corners]
    src = drcog.Drcog(os.path.join(os.environ["BNGE_WORLD_DIR"], "mapdata", "raw"), netfetch.Fetcher(offline=True))
    a = mapcells.cell_of(min(lat), min(lon))
    b = mapcells.cell_of(max(lat), max(lon))
    out, seen, missing = [], set(), 0
    for x in range(min(a.x, b.x), max(a.x, b.x) + 1):
        for y in range(min(a.y, b.y), max(a.y, b.y) + 1):
            try:
                e = src.edges(type(a)(a.z, x, y))
            except Exception:
                missing += 1
                continue
            c, off = e["coords"], e["off"]
            if not len(c):
                continue
            E, N, _ = frame.geodetic_to_enu(c[:, 1], c[:, 0], np.zeros(len(c)))
            E, N = np.asarray(E), np.asarray(N)
            for s, t in zip(off[:-1], off[1:]):
                key = (s and c[s].tobytes()) or c[0].tobytes(), c[t - 1].tobytes(), int(t - s)   # (a line in two cells: once)
                if key in seen:
                    continue
                seen.add(key)
                if E[s:t].max() >= x0 and E[s:t].min() <= x1 and N[s:t].max() >= y0 and N[s:t].min() <= y1:
                    out.append(np.c_[E[s:t], N[s:t]])
    return out, missing


def read_glb(path):
    """[{name, P (n, 3) frame axes, T (m, 3), UV (n, 2), C (n, 4)}], one per primitive of a .glb tools/ue_export.py wrote."""
    with open(path, "rb") as f:
        data = f.read()
    n = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20:20 + n])
    binary = data[20 + n + 8:]
    kinds = {5126: "<f4", 5125: "<u4", 5121: "u1"}
    widths = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

    def acc(i):
        a = doc["accessors"][i]
        v = doc["bufferViews"][a["bufferView"]]
        arr = np.frombuffer(binary, kinds[a["componentType"]], a["count"] * widths[a["type"]], v.get("byteOffset", 0))
        return arr.reshape(a["count"], -1)
    out = []
    for prim in doc["meshes"][0]["primitives"]:
        at = prim["attributes"]
        P = acc(at["POSITION"]).astype(np.float64)
        uv = acc(at["TEXCOORD_0"]).astype(np.float64) if "TEXCOORD_0" in at else np.zeros((len(P), 2))
        uv[:, 1] = 1.0 - uv[:, 1]
        col = acc(at["COLOR_0"]) if "COLOR_0" in at else np.full((len(P), 4), 255, np.uint8)
        out.append({"name": doc["materials"][prim["material"]]["name"], "P": np.c_[P[:, 0], -P[:, 2], P[:, 1]],
                    "T": acc(prim["indices"]).reshape(-1, 3).astype(np.int64), "UV": uv, "C": col})
    return out


class Raster:
    """Lines drawn into a window (x0, y0, x1, y1) at PX, row 0 at y0, lines one pixel wide."""

    def __init__(self, lines, box, px=PX):
        self.x0, self.y0 = box[0], box[1]
        self.px = px
        w, h = int(round((box[2] - box[0]) / px)), int(round((box[3] - box[1]) / px))
        img = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(img)
        for ln in lines:
            if ln[:, 0].max() < box[0] or ln[:, 0].min() > box[2] or ln[:, 1].max() < box[1] or ln[:, 1].min() > box[3]:
                continue
            d.line([tuple(v) for v in (ln - (self.x0, self.y0)) / px], fill=255, width=1)
        self.mask = np.asarray(img) > 0

    def at(self, xy):
        c = ((xy[..., 0] - self.x0) / self.px).astype(np.int64)
        r = ((xy[..., 1] - self.y0) / self.px).astype(np.int64)
        ok = (r >= 0) & (c >= 0) & (r < self.mask.shape[0]) & (c < self.mask.shape[1])
        out = np.zeros(c.shape, bool)
        out[ok] = self.mask[r[ok], c[ok]]
        return out
