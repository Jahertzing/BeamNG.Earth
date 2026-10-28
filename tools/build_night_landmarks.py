"""build_night_landmarks.py: the night's landmarks (nightglow.lua shows them after dusk), beside the building materials
(mod/.../art/shapes/beamngEarth/):

  bne_city_glow.dae    a city's sky glow: a quad 2 wide (x -1..1) and 1 high (z 0..1) facing +y and -y, material
                       bne_city_glow (additive); the mod scales it to the city and turns it to the camera
                       (nightglow.lua). (A cylinder, then a dome first: from outside both had hard edges.)
  bne_city_glow_e.png  its light: a half-disc standing on the quad's foot, full at the foot's middle and fading to
                       nothing at its rim ((1 - d^2)^3 of the distance d from there) and in at its foot (FOOT_V)
  bne_star.dae         the Castle Rock star (the town's star on top of the Rock, lit every winter since 1936, facing
                       I-25 to the west): STAR_BULBS bulbs round a five-pointed star STAR_H m high, in the y-z plane
                       (facing +x and -x), its foot at the origin, material bne_star_bulb. In camera detail levels
                       (build_lamp_glow.make_lod): the bulbs grow with distance (STAR_BULB_M, each kept while it spans
                       BULB_MIN_PX), then a solid star (from 1.4 km), larger again far off (STAR_FAR_SCALE, each kept
                       while it is STAR_MIN_PX high). One size of 0.45 m bulbs was under a pixel from I-25 1.5 km
                       away, and the 2026-10-28 night shots showed no star at all.

    python3 tools/build_night_landmarks.py
"""
import math
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, HERE)
import dae  # noqa: E402
import build_lamp_glow as lod  # noqa: E402

OUT = os.path.join(HERE, "..", "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth")
STAR_H = 14.0           # m (photos: about the height of the flagpole beside it, ~3 storeys)
STAR_BULBS = 100        # the town's count
STAR_BULB_M = (0.45, 0.8, 1.4, 2.4)     # m: the bulbs' size by detail level, nearest first
BULB_MIN_PX = 2.0
STAR_FAR_SCALE = (1.0, 2.0, 4.0)        # then the solid star, at this many times its size
STAR_MIN_PX = 6.0
FOOT_V = 0.12            # the glow's foot fades in over this share of its height


def city_glow():
    P = [(-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 1.0), (-1.0, 0.0, 1.0)]
    UV = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    T = [(0, 1, 2), (0, 2, 3), (0, 2, 1), (0, 3, 2)]          # both faces
    dae.write_grid_dae(os.path.join(OUT, "bne_city_glow.dae"), np.ravel(P), np.ravel(UV), np.ravel(T),
                       material="bne_city_glow")
    n = 256
    u = (np.arange(n) + 0.5) / n
    v = (np.arange(n // 2) + 0.5) / (n // 2)
    V, U = np.meshgrid(v[::-1], u, indexing="ij")    # image row 0 = the top (v = 1)
    d2 = (2.0 * U - 1.0) ** 2 + V ** 2
    fall = np.clip(1.0 - d2, 0.0, 1.0) ** 3
    foot = np.clip(V / FOOT_V, 0.0, 1.0)
    fall = fall * foot * foot * (3.0 - 2.0 * foot)      # (a soft foot too: no edge where the ground is lower)
    col = np.array([1.0, 0.72, 0.42])                # sodium and warm LED light scattered in the air
    img = fall[:, :, None] * col[None, None, :]
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


def star_points():
    """(10, 2) points (y, z): the star's tips and inner corners, tip up, its foot at z = 0."""
    R = STAR_H / (1.0 + math.cos(math.radians(36)))      # tip up; the lowest tips at -R cos 36
    r = R * 0.382
    pts = []
    for k in range(10):
        a = math.radians(90 + 36 * k)
        rr = R if k % 2 == 0 else r
        pts.append((rr * math.cos(a), rr * math.sin(a) + R * math.cos(math.radians(36))))
    return np.array(pts)


def _mesh(P, UV, T):
    P = np.array(P, float)
    return P, np.tile([1.0, 0.0, 0.0], (len(P), 1)), np.array(UV, float), np.array(T, np.int64)


def star_bulbs(b):
    P, UV, T = [], [], []
    h = b / 2
    for y, z in star_outline():
        i = len(P)
        P += [(0.0, y - h, z - h), (0.0, y + h, z - h), (0.0, y + h, z + h), (0.0, y - h, z + h)]
        UV += [(0, 0), (1, 0), (1, 1), (0, 1)]
        T += [(i, i + 1, i + 2), (i, i + 2, i + 3), (i, i + 2, i + 1), (i, i + 3, i + 2)]
    return _mesh(P, UV, T)


def star_solid(scale):
    """The star filled, scale times its size, its foot still at the origin: a fan from its middle."""
    pts = star_points() * scale
    cz = STAR_H * scale / (1.0 + math.cos(math.radians(36))) * math.cos(math.radians(36))
    P = [(0.0, 0.0, cz)] + [(0.0, y, z) for y, z in pts]
    UV = [(0.5, 0.5)] * len(P)
    T = []
    for k in range(10):
        a, c = 1 + k, 1 + (k + 1) % 10
        T += [(0, a, c), (0, c, a)]
    return _mesh(P, UV, T)


def star_sizes():
    """The detail sizes (the engine's, build_lamp_glow's measure) down to which each level is drawn, falling: each
    while it spans its pixels, the last to the engine's smallest."""
    far = STAR_FAR_SCALE[-1]
    R = math.hypot(STAR_H * far / 2, STAR_H * far * math.sin(math.radians(72)) / (1.0 + math.cos(math.radians(36))))
    ends = [b * lod.SCREEN_PX / BULB_MIN_PX for b in STAR_BULB_M]
    ends += [s * STAR_H * lod.SCREEN_PX / STAR_MIN_PX for s in STAR_FAR_SCALE[:-1]]
    return [R * lod.ENGINE_PX / d for d in ends] + [lod.LAST_PX]


def star():
    meshes = [star_bulbs(b) for b in STAR_BULB_M] + [star_solid(s) for s in STAR_FAR_SCALE]
    sizes = star_sizes()
    lod.make_lod("bne_star.dae", "bne_star_bulb", "1 0.96 0.86 1", meshes, sizes)


def main():
    city_glow()
    star()
    for n in ("bne_city_glow.dae", "bne_city_glow_e.png", "bne_star.dae"):
        p = os.path.join(OUT, n)
        print(os.path.normpath(p), os.path.getsize(p), "bytes")


if __name__ == "__main__":
    main()
