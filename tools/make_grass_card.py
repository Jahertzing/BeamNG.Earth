#!/usr/bin/env python3
"""The grass cards' texture (server/grasscards.py, 2026-10-28): mod/beamng_earth/levels/beamng_earth/art/shapes/
beamngEarth/grasscard_b.color.dds and grasscard_o.data.dds

One clump of grass blades on a card twice as wide as tall (grasscards.ASPECT), row 0 the blades' tips (the card's v
is up). Grey (darker at the foot, as the ground shadows it): the colour is the vertex colour's, from the ground photo
under the card. The colour is carried past the blades' edges (the mips then blend no dark rim in).

The cards are alpha-tested (grasscards.materials.json alphaRef) and seen mostly far off, at the coarse mips. A plain
box-filtered opacity mip thins out (the blades' average falls under alphaRef and the clump vanishes with distance), so
each level's opacity is scaled until the share of texels over alphaRef matches the full-size one (coverage-preserving
mips, as in Castaño's "Computing alpha mipmaps").

Procedural (no third-party content).

  python3 tools/make_grass_card.py [out_dir]
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_detail_texture import encode           # noqa: E402  (one-channel levels, R8G8B8A8)
from make_grass_textures import encode_rgb       # noqa: E402  (three-channel levels)

OUT_DIR = os.path.join(HERE, "..", "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth")
W, H = 512, 256
SS = 4                      # supersampling of the blades' edges
BLADES = 140
ALPHA_REF = 80              # grasscards.materials.json's alphaRef (0-255)
FOOT = 0.72                 # the colour at the blades' foot (1 at their tips)


def blades(seed=7):
    """(opacity (H, W) 0..1, colour (H, W) 0..1): BLADES tapered, curved blades from the card's foot."""
    rng = np.random.default_rng(seed)
    h, w = H * SS, W * SS
    cover = np.zeros((h, w))
    shade = np.zeros((h, w))
    rows = np.arange(h)
    cols = np.arange(w)[None, :]
    for _ in range(BLADES):
        x0 = rng.uniform(0.06, 0.94) * w
        top = rng.uniform(0.35, 0.98)                   # the blade's height, a share of the card's
        lean = rng.normal(0.0, 0.12) * w * top          # its tip's offset from the foot
        width = rng.uniform(0.008, 0.016) * w
        tone = rng.uniform(0.85, 1.0)
        t = np.clip((h - 1 - rows) / (top * h), 0.0, None)   # 0 at the foot, 1 at the tip
        on = t <= 1.0
        xc = x0 + lean * t ** 2
        half = width * np.clip(1.0 - t, 0.0, 1.0) ** 0.8 / 2 + 0.5
        inside = (np.abs(cols - xc[:, None]) <= half[:, None]) & on[:, None]
        c = (FOOT + (1.0 - FOOT) * np.clip(t, 0, 1)) * tone
        cover = np.maximum(cover, inside)
        shade = np.where(inside, c[:, None], shade)
    cover = cover.reshape(H, SS, W, SS).mean((1, 3))
    covered = shade.reshape(H, SS, W, SS)
    weight = (covered > 0).reshape(H, SS, W, SS).sum((1, 3))
    colour = np.where(weight > 0, covered.sum((1, 3)) / np.maximum(weight, 1), 0.0)
    return cover, colour


def bleed(colour, cover):
    """The blades' mean colour in the texels no blade covers: the mips blend in no dark rim."""
    mean = float(colour[cover > 0].mean())
    out = colour.copy()
    hole = cover <= 0
    out[hole] = mean
    return out


def levels(img):
    """The full mip chain (down to 1 x 1: a side at 1 stays 1)."""
    out, cur = [img], img
    while cur.shape != (1, 1):
        if cur.shape[0] > 1:
            cur = cur.reshape(cur.shape[0] // 2, 2, cur.shape[1]).mean(1)
        if cur.shape[1] > 1:
            cur = cur.reshape(cur.shape[0], cur.shape[1] // 2, 2).mean(2)
        out.append(cur)
    return out


def coverage(a, ref):
    return float((a > ref).mean())


def opacity_levels(alpha, ref=ALPHA_REF / 255.0):
    """Box-filtered levels, each scaled so its share of texels over ref matches level 0's."""
    lv = levels(alpha)
    want = coverage(lv[0], ref)
    out = [lv[0]]
    for a in lv[1:]:
        lo, hi = 0.0, 8.0
        for _ in range(30):
            mid = (lo + hi) / 2
            if coverage(np.clip(a * mid, 0, 1), ref) < want:
                lo = mid
            else:
                hi = mid
        out.append(np.clip(a * hi, 0, 1))
    return out


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT_DIR
    os.makedirs(out, exist_ok=True)
    cover, colour = blades()
    colour = bleed(colour, cover)
    ol = opacity_levels(cover)
    cl = levels(colour)
    with open(os.path.join(out, "grasscard_o.data.dds"), "wb") as f:
        f.write(encode(ol))
    with open(os.path.join(out, "grasscard_b.color.dds"), "wb") as f:
        f.write(encode_rgb([np.repeat(c[..., None], 3, -1) for c in cl]))
    ref = ALPHA_REF / 255.0
    print("coverage per level", [round(coverage(a, ref), 3) for a in ol])
    print("blade colour mean", round(float(colour[cover > ref].mean()), 3))


if __name__ == "__main__":
    main()
