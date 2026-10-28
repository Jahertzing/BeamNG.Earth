"""Grass cards past the 3D grass (the user, 2026-10-28: "im looking miles ahead so like 200m is NOTHING").

The game's 3D grass (the mod's GroundCover, groundcover.lua) only grows on the physics block, ~300 m at most, and the
ground tiles' grass layer (surface.grass_mask, satmat.lua) is flat. Between them, out to SEND_M, grassy land gets a
render-only mesh per 256 m road tile square: crossed vertical cards of our own grass clump texture
(tools/make_grass_card.py: grey blades, so the colour is all the vertex colour's), each card coloured from the ground
photo under it and lit as the ground (its normals point up), so the band of cards takes the field's own colour and
gives it height instead of a flat photo. Placed where the grass mask is (grassy land cover, roads and pads cut out), on
a jittered grid hashed from world position (the same cards whichever anchor), sunk SINK_M into the ground (the far
ground tiles round off the DEM by up to a metre: better half a card hidden than one floating).

Sent as `grfar_tile` / `grfar_drop` by a vegetation.Tracker (kind "grfar"); the mod draws them as the far building
tiles (roads.lua onFarTile kind 'G'), never hidden. Not under the 3D grass: tiles whose square comes within SKIP_M of
the player are not built.
"""
import json
import math
import os

import numpy as np

import staging

VERSION = 1
SEND_M = float(os.environ.get("BNE_GRASSCARD_SEND_M", "1000"))     # tiles whose square comes this close are built
DROP_M = SEND_M + 300.0
SKIP_M = 128.0              # ... but not those this close (Chebyshev): the 3D grass is there
PER_POSE = 2                # tiles building at once
MATERIAL = "bngEarthGrassCard"      # levels/beamng_earth/art/shapes/beamngEarth/grasscards.materials.json
SPACING_M = 3.0             # the candidate grid (one card each at most)
KEEP = 0.5                  # share of the candidates kept where the grass mask is full
HEIGHT_M = (0.6, 1.1)       # card height, uniform (in game 2026-10-28, 0.5-0.9 m: barely seen past ~300 m)
ASPECT = 2.0                # card width / height (the texture's)
SINK_M = 0.15
MASK_PX = 128               # the grass mask over the tile (2 m texels)
# WorldCover class -> card weight: surface.GRASS_WEIGHT without cropland (in game 2026-10-28 an irrigated field's
# cards took its soil rows' colour: reddish-brown speckles on green)
CARD_WEIGHT = {30: 1.0, 100: 0.8, 20: 0.6, 10: 0.3}
PHOTO_BLUR_PX = 2           # box blur radius of the photo the colours come from (20 m: no single rows or roofs)
PHOTO_PX = 64               # the photo the cards take their colour from (4 m texels)
BRIGHT = 1.25               # vertex colour = photo colour x this (the blades' texture averages ~0.78 grey)
FALLBACK_RGB = (118, 112, 84)   # no photo at hand: dry grass
MAX_CARDS = 8000            # a tile's most (16-bit vertex indices: 8 vertices a card)


def _hash01(ix, iy, seed):
    """Deterministic uniform [0, 1) per integer grid cell (arrays)."""
    h = (ix.astype(np.int64) * 73856093) ^ (iy.astype(np.int64) * 19349663) ^ (seed * 83492791)
    h = (h ^ (h >> 13)) * 1274126177
    h = h ^ (h >> 16)
    return (h & 0xFFFFFF).astype(np.float64) / float(0x1000000)


def candidates(x0, y0, size, origin_xy=(0.0, 0.0)):
    """Jittered grid points in [x0, x0 + size]² (frame m): (x, y, keep-hash, size-hash, yaw-hash), the grid and its
    hashes laid on world-ish cells (origin_xy: the anchor's offset, so a place gets the same cards from any anchor
    nearby when the caller passes one)."""
    ox, oy = origin_xy
    i0 = math.floor((x0 + ox) / SPACING_M)
    j0 = math.floor((y0 + oy) / SPACING_M)
    n = int(math.ceil(size / SPACING_M)) + 1
    ii, jj = np.meshgrid(np.arange(i0, i0 + n), np.arange(j0, j0 + n))
    ii, jj = ii.ravel(), jj.ravel()
    x = (ii + _hash01(ii, jj, 1)) * SPACING_M - ox
    y = (jj + _hash01(ii, jj, 2)) * SPACING_M - oy
    inside = (x >= x0) & (x < x0 + size) & (y >= y0) & (y < y0 + size)
    return (x[inside], y[inside], _hash01(ii, jj, 3)[inside], _hash01(ii, jj, 4)[inside],
            _hash01(ii, jj, 5)[inside])


def sample(img, x, y, x0, y0, size):
    """img (h, w[, c]) over [x0, x0 + size]² (row 0 north) at the points (nearest texel)."""
    h, w = img.shape[:2]
    c = np.clip(((x - x0) / size * w).astype(np.int64), 0, w - 1)
    r = np.clip(((y0 + size - y) / size * h).astype(np.int64), 0, h - 1)
    return img[r, c]


def blur(img, r):
    """Box blur (2r + 1 texels) of an (h, w, c) image, its edges clamped."""
    if r <= 0:
        return img
    h, w = img.shape[:2]
    p = np.pad(img, ((r, r), (r, r), (0, 0)), mode="edge")
    out = np.zeros_like(img)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            out += p[dy:dy + h, dx:dx + w]
    return out / (2 * r + 1) ** 2


def cards(x, y, z, hsize, yaw, rgb):
    """The mesh of crossed cards at (x, y, z) (their foot), heights hsize, first quad's yaw (rad), colours rgb (n, 3)
    uint8: (positions (8n, 3), uvs (8n, 2), tris (4n, 3), colors (8n, 4) uint8, normals (8n, 3))."""
    n = len(x)
    w = hsize * ASPECT / 2
    P, UV = [], []
    for a in (yaw, yaw + np.pi / 2):
        dx, dy = np.cos(a) * w, np.sin(a) * w
        P += [np.c_[x - dx, y - dy, z], np.c_[x + dx, y + dy, z],
              np.c_[x + dx, y + dy, z + hsize], np.c_[x - dx, y - dy, z + hsize]]
        UV += [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    P = np.stack(P, 1).reshape(-1, 3)                      # card k's 8 vertices together
    UV = np.tile(np.array(UV, np.float64), (n, 1))
    base = (np.arange(n) * 8)[:, None]
    quad = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
    T = (base[:, None, :] + quad[None, :, :]).reshape(-1, 3)
    C = np.repeat(np.c_[rgb, np.full(n, 255, np.uint8)], 8, axis=0).astype(np.uint8)
    N = np.tile(np.array([0.0, 0.0, 1.0]), (8 * n, 1))     # lit as the ground under them
    return P, UV, T, C, N


def build(frame, x0, y0, size, mask, ground, photo=None, origin_xy=(0.0, 0.0)):
    """The cards of a tile square [x0, x0 + size]² of frame: mask (h, w) uint8 grass weight over it (surface.grass_mask,
    row 0 north), ground(lat, lon) the DEM heights (arrays), photo (h, w, 3) uint8 over it or None. Returns cards()'s
    tuple with positions relative to (x0, y0, 0), or None (no card)."""
    x, y, hk, hs, hy = candidates(x0, y0, size, origin_xy)
    wgt = sample(mask, x, y, x0, y0, size).astype(np.float64) / 255.0
    keep = hk < wgt * KEEP
    if not keep.any():
        return None
    x, y, hs, hy = x[keep], y[keep], hs[keep], hy[keep]
    if len(x) > MAX_CARDS:
        order = np.argsort(hk[keep])[:MAX_CARDS]           # the lowest hashes: thinned evenly
        x, y, hs, hy = x[order], y[order], hs[order], hy[order]
    la, lo, _ = frame.enu_to_geodetic(x, y, np.zeros(len(x)))
    la, lo = np.asarray(la, np.float64), np.asarray(lo, np.float64)
    h = np.asarray(ground(la, lo), np.float64)
    z = np.asarray(frame.geodetic_to_enu(la, lo, h)[2], np.float64) - SINK_M
    if photo is not None:
        rgb = np.clip(sample(blur(np.asarray(photo, np.float64), PHOTO_BLUR_PX), x, y, x0, y0, size) * BRIGHT, 0, 255)
    else:
        rgb = np.tile(np.array(FALLBACK_RGB, np.float64), (len(x), 1))
    hsize = HEIGHT_M[0] + hs * (HEIGHT_M[1] - HEIGHT_M[0])
    P, UV, T, C, N = cards(x - x0, y - y0, z, hsize, hy * np.pi, rgb.astype(np.uint8))
    return P, UV, T, C, N


def write_tile(mesh, out_dir, vfs_dir, aid, tx, ty, x0, y0):
    """The tile's shape and JSON in the session folder: (vfs path of the JSON, card count)."""
    import cdae
    P, UV, T, C, N = mesh
    data = {"v": VERSION}
    shape = f"grfar_{aid}_{tx}_{ty}.cdae"
    cdae.write(os.path.join(out_dir, shape), P, T, normals=N, uvs=UV, material=MATERIAL, colors=C,
               object_name="grass")
    data["shape"] = f"{vfs_dir}/{shape}"
    data["pos"] = [x0, y0, 0.0]
    name = f"grfar_{aid}_{tx}_{ty}.json"
    path = os.path.join(out_dir, name)
    tmp = staging.tmp_for(path)
    with open(tmp, "w") as f:
        f.write(json.dumps(data))
    staging.replace(tmp, path)
    return f"{vfs_dir}/{name}", len(P) // 8


def empty_tile(out_dir, vfs_dir, aid, tx, ty):
    """A tile with no cards (the mod keeps it as sent, nothing to draw): (vfs path, 0)."""
    name = f"grfar_{aid}_{tx}_{ty}.json"
    path = os.path.join(out_dir, name)
    tmp = staging.tmp_for(path)
    with open(tmp, "w") as f:
        f.write(json.dumps({"v": VERSION}))
    staging.replace(tmp, path)
    return f"{vfs_dir}/{name}", 0
