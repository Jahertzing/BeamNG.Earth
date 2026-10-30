#!/usr/bin/env python3
"""Where the grass, shrubs and trees of a tools/ue_export.py folder go, read from its own ground: the heights and the
aerial photo in ground.glb, with roads, decks, paths and buildings kept clear. Trees and shrubs sit where the photo
shows dark vegetation, so they stand where the real ones do. Writes scatter.json (frame metres) beside the manifest.

    .venv\\Scripts\\python tools\\ue_scatter.py --out C:/beamng-earth/Earth.UE/export/slice01
"""
import argparse
import concurrent.futures
import io
import json
import os
import struct

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

CLEAR_KINDS = ("road", "deck", "walks", "bld", "tunnel", "rail")
CLEAR_M = 2.0               # ground kept clear round them
GRASS_NEAR_M = 0.8          # spacing within NEAR_ROAD_M of a road ...
GRASS_FAR_M = 3.0           # ... and beyond
NEAR_ROAD_M = 120.0
BRIGHT_SOIL = 200.0         # photo luminance from here on is bare ground
FULL_COVER = 120.0          # ... and below this, full grass
TREE_MIN_M2, SHRUB_MIN_M2 = 14.0, 2.0
GRASS = ("grass_medium_01", "grass_medium_02", "grass_bermuda_01", "weed_plant_02")
GRASS_SHARE = (0.5, 0.3, 0.15, 0.05)
SHRUBS = ("shrub_02", "shrub_03", "shrub_04")
TREES = ("island_tree_02", "tree_small_02")


def glb(path):
    with open(path, "rb") as f:
        b = f.read()
    n = struct.unpack("<I", b[12:16])[0]
    return json.loads(b[20:20 + n]), b, 20 + n + 8


def accessor(j, b, base, i, dtype, width):
    a = j["accessors"][i]
    v = j["bufferViews"][a["bufferView"]]
    return np.frombuffer(b, dtype, a["count"] * width, base + v["byteOffset"]).reshape(-1, width)


def mesh_arrays(path):
    """(P in frame axes, relative to the file's origin, T) of a .glb of tools/ue_export.py."""
    j, b, base = glb(path)
    prims = j["meshes"][0]["primitives"]
    P = accessor(j, b, base, prims[0]["attributes"]["POSITION"], np.float32, 3)
    T = np.concatenate([accessor(j, b, base, p["indices"], np.uint32, 1) for p in prims]).reshape(-1, 3)
    return np.stack([P[:, 0], -P[:, 2], P[:, 1]], axis=1), T


GRASS_SCALE = (1.4, 2.4)    # the models are small tufts: this makes them read from a car


def scatter_cell(job):
    out_dir, entry, meshes, seed, budget = job
    rng = np.random.default_rng(seed)
    x0, y0 = entry["origin"][:2]

    j, b, base = glb(os.path.join(out_dir, entry["file"]))
    P = accessor(j, b, base, j["meshes"][0]["primitives"][0]["attributes"]["POSITION"], np.float32, 3)
    n = int(round(len(P) ** 0.5))
    U = P[:, 1].reshape(n, n).astype(np.float64)            # [gy, gx], gy north
    view = j["bufferViews"][j["images"][0]["bufferView"]]
    photo = np.asarray(Image.open(io.BytesIO(b[base + view["byteOffset"]:base + view["byteOffset"] + view["byteLength"]]))
                       .convert("RGB"), np.float32)[::-1]    # rows north-up after the flip: [py, px], py north
    span = (n - 1) * 2.0
    ppm = photo.shape[0] / span

    # what must stay clear, on the 2 m lattice
    clear = Image.new("L", (n, n), 0)
    draw = ImageDraw.Draw(clear)
    for e in meshes:
        if e["kind"] not in CLEAR_KINDS:
            continue
        Q, T = mesh_arrays(os.path.join(out_dir, e["file"]))
        gx = (Q[:, 0] + e["origin"][0] - x0) / 2.0
        gy = (Q[:, 1] + e["origin"][1] - y0) / 2.0
        for t in T:
            draw.polygon([(gx[t[0]], gy[t[0]]), (gx[t[1]], gy[t[1]]), (gx[t[2]], gy[t[2]])], fill=255)
    clear = np.asarray(clear) > 0
    road_dist = ndimage.distance_transform_edt(~clear) * 2.0
    keep_clear = road_dist < CLEAR_M

    lum = photo.mean(axis=2)
    r, g, bl = photo[:, :, 0], photo[:, :, 1], photo[:, :, 2]

    def at(grid, x, y, scale):
        ix = np.clip((x * scale).astype(int), 0, grid.shape[1] - 1)
        iy = np.clip((y * scale).astype(int), 0, grid.shape[0] - 1)
        return grid[iy, ix]

    def height(x, y):
        gx, gy = np.clip(x / 2.0, 0, n - 1.001), np.clip(y / 2.0, 0, n - 1.001)
        i, k = gx.astype(int), gy.astype(int)
        fx, fy = gx - i, gy - k
        return (U[k, i] * (1 - fx) * (1 - fy) + U[k, i + 1] * fx * (1 - fy) + U[k + 1, i] * (1 - fx) * fy
                + U[k + 1, i + 1] * fx * fy)

    def jittered(spacing):
        c = np.arange(spacing / 2, span, spacing)
        x, y = np.meshgrid(c, c)
        return (x.ravel() + rng.uniform(-spacing / 2, spacing / 2, x.size),
                y.ravel() + rng.uniform(-spacing / 2, spacing / 2, x.size))

    out = {}

    def add(name, x, y, heights=None, scale=None):
        z = height(x, y)
        s = rng.uniform(0.8, 1.35, len(x)) if scale is None else scale
        rows = np.stack([x + x0, y + y0, z, rng.uniform(0, 360, len(x)), s], axis=1)
        if heights is not None:
            rows = np.column_stack([rows, heights])
        out.setdefault(name, []).extend(np.round(rows, 3).tolist())

    # dark vegetation in the photo: trees and shrubs where the real ones are
    dark = (lum < 88) & (g >= r * 0.92) & (g >= bl)
    dark = ndimage.binary_opening(dark, iterations=1)
    labels, count = ndimage.label(dark)
    area = ndimage.sum_labels(np.ones_like(lum), labels, np.arange(1, count + 1)) / ppm ** 2
    centres = np.array(ndimage.center_of_mass(dark, labels, np.arange(1, count + 1))).reshape(-1, 2) / ppm
    cx, cy = centres[:, 1], centres[:, 0]
    free = ~at(keep_clear, cx, cy, 0.5)
    trees = free & (area >= TREE_MIN_M2) & (area < 900.0)
    shrubs = free & (area >= SHRUB_MIN_M2) & (area < TREE_MIN_M2)
    kind = rng.integers(0, len(TREES), trees.sum())
    for k, name in enumerate(TREES):
        m = kind == k
        d = 2.0 * np.sqrt(area[trees][m] / np.pi)
        add(name, cx[trees][m], cy[trees][m], heights=np.clip(d * 1.3, 4.0, 18.0), scale=np.ones(m.sum()))
    kind = rng.integers(0, len(SHRUBS), shrubs.sum())
    for k, name in enumerate(SHRUBS):
        m = kind == k
        d = 2.0 * np.sqrt(area[shrubs][m] / np.pi)
        add(name, cx[shrubs][m], cy[shrubs][m], heights=np.clip(d * 0.8, 0.6, 2.5), scale=np.ones(m.sum()))

    # grass: thick near the roads, thinner beyond, none on bare ground
    xn, yn = jittered(GRASS_NEAR_M)
    k = at(road_dist, xn, yn, 0.5) <= NEAR_ROAD_M
    xf, yf = jittered(GRASS_FAR_M)
    kf = at(road_dist, xf, yf, 0.5) > NEAR_ROAD_M
    x, y = np.r_[xn[k], xf[kf]], np.r_[yn[k], yf[kf]]
    cover = np.clip((BRIGHT_SOIL - at(lum, x, y, ppm)) / (BRIGHT_SOIL - FULL_COVER), 0.0, 1.0)
    k = (rng.uniform(0, 1, len(x)) < cover) & ~at(keep_clear, x, y, 0.5)
    x, y = x[k], y[k]
    if budget and len(x) > budget:          # the nearest the roads stay
        order = np.argsort(at(road_dist, x, y, 0.5) + rng.uniform(0, 10, len(x)))[:budget]
        x, y = x[order], y[order]
    pick = rng.choice(len(GRASS), len(x), p=GRASS_SHARE)
    for i, name in enumerate(GRASS):
        add(name, x[pick == i], y[pick == i], scale=rng.uniform(*GRASS_SCALE, (pick == i).sum()))

    tail = entry["file"][len("ground"):-4]      # "_-2_-1" for a cell, "" for a single block
    name = "scatter%s.f32" % tail
    with open(os.path.join(out_dir, name), "wb") as f:
        for plant, rows in out.items():
            r = np.asarray(rows, np.float32).reshape(len(rows), -1)
            if r.shape[1] == 5:                 # (x, y, z, yaw, scale, height m or 0)
                r = np.column_stack([r, np.zeros(len(r), np.float32)])
            label = plant.encode()
            f.write(struct.pack("<II", len(label), len(r)) + label + r.astype(np.float32).tobytes())
    return name, {k: len(v) for k, v in out.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="C:/beamng-earth/Earth.UE/export/slice01")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--budget", type=int, default=0, help="grass clumps at most, per cell (0: no limit)")
    a = ap.parse_args()
    with open(os.path.join(a.out, "manifest.json")) as f:
        manifest = json.load(f)
    jobs = []
    for k, e in enumerate(m for m in manifest["meshes"] if m["kind"] == "ground"):
        same = [m for m in manifest["meshes"] if m["origin"][:2] == e["origin"][:2] or m["file"].endswith(e["file"][6:])]
        if e["file"] == "ground.glb":
            same = manifest["meshes"]
        jobs.append((a.out, e, same, a.seed + k, a.budget))
    totals = {}
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as pool:
        for k, (name, counts) in enumerate(pool.map(scatter_cell, jobs), 1):
            for plant, c in counts.items():
                totals[plant] = totals.get(plant, 0) + c
            print(k, "of", len(jobs), name, sum(counts.values()), flush=True)
    print(totals, "total", sum(totals.values()))


if __name__ == "__main__":
    main()
