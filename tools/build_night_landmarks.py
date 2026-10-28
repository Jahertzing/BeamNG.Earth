"""build_night_landmarks.py: the night's landmarks (nightglow.lua shows them after dusk), beside the building materials
(mod/.../art/shapes/beamngEarth/):

  bne_city_glow.dae    a city's sky glow: a quad 2 wide (x -1..1) and 1 high (z 0..1) facing +y and -y, material
                       bne_city_glow (additive); the mod scales it to the city and turns it to the camera
                       (nightglow.lua). (A cylinder, then a dome first: from outside both had hard edges.)
  bne_city_glow_e.png  its light: a half-disc standing on the quad's foot, full at the foot's middle and fading to
                       nothing at its rim ((1 - d^2)^3 of the distance d from there) and in at its foot (FOOT_V)
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
STAR_H = 14.0           # m (photos: about the height of the flagpole beside it, ~3 storeys)
STAR_BULBS = 100        # the town's count
BULB_M = 0.45
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
