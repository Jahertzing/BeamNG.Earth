"""build_night_landmarks.py: the night's landmarks (nightglow.lua shows them after dusk), beside the building materials
(mod/.../art/shapes/beamngEarth/):

  bne_city_glow.dae    a city's sky glow: a dome, radius 1 and 1 high (the mod scales it to the city), both faces,
                       material bne_city_glow (additive), u its distance from the axis (0 over the centre, 1 at the rim).
                       Its light fades to nothing toward the rim (bne_city_glow_e.png), so it has no edge: from outside
                       a soft brightening over the city, from inside the sky brighter overhead. (An open cylinder was
                       first: its wall and rim showed as hard edges across the sky, 2026-10-28.)
  bne_city_glow_e.png  the glow's falloff along u: full over the centre, a smooth fade to nothing at the rim
  bne_star.dae         the Castle Rock star (the town's star on top of the Rock, lit every winter since 1936, facing
                       I-25 to the west): STAR_BULBS bulbs round a five-pointed star STAR_H m high, in the y-z plane
                       (facing +x and -x), its foot at the origin, material bne_star_bulb

    python3 tools/build_night_landmarks.py
"""
import math
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import dae  # noqa: E402

OUT = os.path.join(HERE, "..", "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth")
GLOW_SEGMENTS = 48
GLOW_RINGS = 16
STAR_H = 14.0           # m (photos: about the height of the flagpole beside it, ~3 storeys)
STAR_BULBS = 100        # the town's count
BULB_M = 0.45


def city_glow():
    """The dome: GLOW_RINGS rings of GLOW_SEGMENTS (a quarter circle in profile, so it meets the ground upright under
    the city's edge where its light is gone), a vertex at the top."""
    P, UV, T = [(0.0, 0.0, 1.0)], [(0.0, 0.5)], []
    for i in range(1, GLOW_RINGS + 1):
        r = i / GLOW_RINGS
        z = math.sqrt(max(0.0, 1.0 - r * r))
        for k in range(GLOW_SEGMENTS):
            a = 2.0 * math.pi * k / GLOW_SEGMENTS
            P.append((r * math.cos(a), r * math.sin(a), z))
            UV.append((r, 0.5))
    ring = lambda i, k: 1 + (i - 1) * GLOW_SEGMENTS + k % GLOW_SEGMENTS      # noqa: E731
    for k in range(GLOW_SEGMENTS):
        a, b = ring(1, k), ring(1, k + 1)
        T += [(0, a, b), (0, b, a)]                  # both faces
    for i in range(1, GLOW_RINGS):
        for k in range(GLOW_SEGMENTS):
            a, b, c, d = ring(i, k), ring(i, k + 1), ring(i + 1, k + 1), ring(i + 1, k)
            T += [(a, d, c), (a, c, b), (a, c, d), (a, b, c)]
    dae.write_grid_dae(os.path.join(OUT, "bne_city_glow.dae"), np.ravel(P), np.ravel(UV), np.ravel(T),
                       material="bne_city_glow")
    u = np.linspace(0.0, 1.0, 256)
    fall = (1.0 - u * u) ** 3                        # (smooth: no step anywhere, nothing at the rim)
    col = np.array([1.0, 0.72, 0.42])                # sodium and warm LED light scattered in the air
    img = (fall[None, :, None] * col[None, None, :]).repeat(4, axis=0)
    Image.fromarray(np.clip(img * 255 + 0.5, 0, 255).astype(np.uint8)).save(os.path.join(OUT, "bne_city_glow_e.png"))


def star_outline():
    """(k, 2) points (y, z) round a five-pointed star STAR_H high, its foot at z = 0, evenly spaced along it."""
    R = STAR_H / (1.0 + math.cos(math.radians(36)))      # tip up; the lowest tips at -R cos 36
    r = R * 0.382
    pts = []
    for k in range(10):
        a = math.radians(90 + 36 * k)
        rr = R if k % 2 == 0 else r
        pts.append((rr * math.cos(a), rr * math.sin(a) + R * math.cos(math.radians(36))))
    pts = np.array(pts + [pts[0]])
    seg = np.hypot(*np.diff(pts, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    t = np.linspace(0.0, s[-1], STAR_BULBS, endpoint=False)
    return np.c_[np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])]


def star():
    P, UV, T = [], [], []
    h = BULB_M / 2
    for y, z in star_outline():
        i = len(P)
        P += [(0.0, y - h, z - h), (0.0, y + h, z - h), (0.0, y + h, z + h), (0.0, y - h, z + h)]
        UV += [(0, 0), (1, 0), (1, 1), (0, 1)]
        T += [(i, i + 1, i + 2), (i, i + 2, i + 3), (i, i + 2, i + 1), (i, i + 3, i + 2)]
    dae.write_grid_dae(os.path.join(OUT, "bne_star.dae"), np.ravel(P), np.ravel(UV), np.ravel(T),
                       material="bne_star_bulb")


def main():
    city_glow()
    star()
    for n in ("bne_city_glow.dae", "bne_city_glow_e.png", "bne_star.dae"):
        p = os.path.join(OUT, n)
        print(os.path.normpath(p), os.path.getsize(p), "bytes")


if __name__ == "__main__":
    main()
