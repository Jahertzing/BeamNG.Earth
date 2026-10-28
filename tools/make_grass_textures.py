#!/usr/bin/env python3
"""Grass ground textures for the imagery tiles' grass layer (2026-10-28): mod/beamng_earth/art/beamngEarth/
detail_grass.dds and detail_grass_nm.dds

The 3D grass (groundcover.lua) only grows on the physics block, ~300 m at most; past it the ground tiles' material
gives grassy land (the server's grass mask, surface.grass_mask) a second layer over the same photo with these as
its detail colour and detail normal maps on UV1 (satmat.lua GRASS_FIELDS). One layer carries one detail scale, so
one texture holds every scale and the GPU's mip selection picks the one that fits the distance: one repeat is
REPEAT_M (32 m) over N texels (3 cm each), with tufts (5-40 cm: the fine mips, the first ~100-300 m), clumps
(0.5-3 m: to ~1 km) and patches (4-16 m: the coarse mips, for kilometres). Mean exactly 0.5 grey: the shader
adds 2 * detail - 1 to the photo (make_detail_texture.py), so the photo's colour stays and only its texture changes.
The normal map has the tufts and clumps as bumps (the low sun catches them), flat at the coarse mips.

Procedural (no third-party content): periodic band-limited noise (FFT, so it tiles seamlessly). Stored uncompressed
(R8G8B8A8): DXT1's 5:6:5 endpoints tint near-grey grain green/magenta.

  python3 tools/make_grass_textures.py [out_dir]
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_detail_texture import encode   # noqa: E402  (the same DDS layout as detail_ground.dds)

OUT_DIR = os.path.join(HERE, "..", "mod", "beamng_earth", "art", "beamngEarth")
N = 1024
REPEAT_M = 32.0
# (cycles per metre from, to, std of the colour around 0.5, std of the normal map's height in metres)
BANDS = (
    (2.5, 20.0, 0.022, 0.006),     # tufts and the gaps between them
    (0.33, 2.0, 0.022, 0.05),      # clumps
    (1 / 16, 0.25, 0.028, 0.0),    # patches (mown / grazed / dry), colour only
)


def band(rng, lo, hi):
    """Periodic noise (N x N, std 1) with its energy between lo and hi cycles per metre."""
    f = np.fft.fftfreq(N) * N / REPEAT_M
    r = np.hypot(*np.meshgrid(f, f))
    shape = (r >= lo) & (r <= hi)
    spec = np.fft.fft2(rng.standard_normal((N, N))) * shape / np.maximum(r, 1e-6) ** 0.5
    a = np.real(np.fft.ifft2(spec))
    a -= a.mean()
    return a / a.std()


def textures(seed=11):
    """(colour (N, N) around 0.5, height (N, N) in metres)."""
    rng = np.random.default_rng(seed)
    colour = np.zeros((N, N))
    height = np.zeros((N, N))
    for lo, hi, std, hw in BANDS:
        b = band(rng, lo, hi)
        if lo >= 2.0:
            # tufts: bright crowns, darker narrow gaps between them (shadowed soil)
            b = np.where(b > 0, b * 0.8, b * 1.3)
            b -= b.mean()
            b /= b.std()
        colour += std * b
        height += hw * b
    colour = 0.5 + colour - colour.mean()
    return colour, height


def colour_levels(img):
    out, cur = [], img
    while True:
        out.append(cur)
        if cur.shape[0] == 1:
            return out
        cur = cur.reshape(cur.shape[0] // 2, 2, cur.shape[1] // 2, 2).mean((1, 3))


def normals(height):
    """Tangent-space normals (N, N, 3) of a periodic height field (m), texel REPEAT_M / N."""
    t = REPEAT_M / N
    dx = (np.roll(height, -1, 1) - np.roll(height, 1, 1)) / (2 * t)
    dy = (np.roll(height, -1, 0) - np.roll(height, 1, 0)) / (2 * t)
    n = np.stack([-dx, dy, np.ones_like(dx)], -1)       # row 0 is +v
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def normal_levels(n):
    out, cur = [], n
    while True:
        out.append(cur)
        if cur.shape[0] == 1:
            return out
        cur = cur.reshape(cur.shape[0] // 2, 2, cur.shape[1] // 2, 2, 3).mean((1, 3))
        cur = cur / np.linalg.norm(cur, axis=-1, keepdims=True)


def encode_rgb(levels_):
    """R8G8B8A8 DDS of (h, w, 3) levels in 0..1 (encode()'s header, its body per channel)."""
    import struct
    head = encode([lv[..., 0] for lv in levels_])[:128]
    body = []
    for lv in levels_:
        c = np.clip(np.rint(lv * 255), 0, 255).astype(np.uint8)
        body.append(np.concatenate([c, np.full(c.shape[:2] + (1,), 255, np.uint8)], -1).tobytes())
    assert struct.unpack_from("<I", head, 28)[0] == len(levels_)
    return head + b"".join(body)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT_DIR
    os.makedirs(out, exist_ok=True)
    colour, height = textures()
    lv = colour_levels(colour)
    with open(os.path.join(out, "detail_grass.dds"), "wb") as f:
        f.write(encode(lv))
    nl = normal_levels(normals(height))
    with open(os.path.join(out, "detail_grass_nm.dds"), "wb") as f:
        f.write(encode_rgb([n * 0.5 + 0.5 for n in nl]))
    print("detail_grass.dds:", len(lv), "levels; stds", [round(float(x.std()), 4) for x in lv[:11]])
    print("detail_grass_nm.dds: mean tilt (deg) per level",
          [round(float(np.degrees(np.arccos(np.clip(n[..., 2], -1, 1))).mean()), 1) for n in nl[:11]])


if __name__ == "__main__":
    main()
