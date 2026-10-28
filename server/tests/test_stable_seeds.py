"""A building's look does not depend on the anchor it is built under (2026-10-28: plan() hashed its seed from the
ring's centroid in the anchor's tile frame and the town / dense flags came from the road tile it fell in, so the same
house's walls, porch, colours and windows changed with the anchor): the same footprints built under two anchors give
the same Spec choices and the same facade, translated."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import buildings as bld  # noqa: E402
import geodesy  # noqa: E402
import mapgeom  # noqa: E402

LAT, LON = 40.1003, -104.9417          # Frederick
CHOICES = ("seed", "facade", "wall", "band", "roof", "roof_mat", "floors", "floor_h", "height", "porch", "cls",
           "windows", "shop", "plain", "region")


def neighbourhood(n=7):
    """A block of houses and a few bigger buildings round (LAT, LON): heights mostly unknown (predicted: the town
    flag matters), as Features (lon / lat, as the map cells give them)."""
    f = geodesy.EnuFrame(LAT, LON)
    coords, coord_off, cls, hts, names = [], [0], [], [], []
    k = 0
    for i in range(n):
        for j in range(n):
            big = (i + j) % 9 == 0
            # (sizes as the map's are: never an exact half bay, where the wall kernel's round(length / bay) is a coin
            # flipped by a frame's float noise)
            w, d = (30.37, 22.21) if big else (11.13 + (i * 3 + j) % 5 + 0.071 * i, 8.61 + (i + 2 * j) % 4 + 0.043 * j)
            x, y = -60.0 + i * 21.0 + (j % 3), -60.0 + j * 19.0 + (i % 2)
            e = np.array([x, x + w, x + w, x, x])
            nn = np.array([y, y, y + d, y + d, y])
            la, lo, _ = f.enu_to_geodetic(e, nn, 0.0)
            coords.extend(zip(lo, la))
            coord_off.append(len(coords))
            cls.append("commercial" if big else "house")
            hts.append(np.nan if (i + j) % 3 else 6.5)
            names.append(f"08b2{k:012x}")
            k += 1
    m = len(cls)
    return mapgeom.Features(layer="buildings", ids=np.array(names),
                            kind=np.full(m, mapgeom.POLYGON, np.uint8), part_off=np.arange(m + 1, dtype=np.int64),
                            ring_off=np.arange(m + 1, dtype=np.int64), coord_off=np.array(coord_off, np.int64),
                            lonlat=np.array(coords, np.float64),
                            attrs={"class": np.array(cls, object), "height": np.array(hts, np.float64)})


def built(frame, layer):
    """{building (its centre's lon / lat, rounded): (spec, its tile-local ring's frame offset)} of every road tile the
    block falls in, built under `frame`."""
    out = {}
    lo, la = layer.lonlat[:, 0], layer.lonlat[:, 1]
    e, n, _ = frame.geodetic_to_enu(la, lo, np.zeros(len(la)))
    tiles = {(int(np.floor(x / 256.0)), int(np.floor(y / 256.0))) for x, y in zip(e, n)}
    for tx, ty in sorted(tiles):
        x0, y0 = tx * 256.0, ty * 256.0
        for spec, gmin, gmax in bld.tile_buildings(frame, x0, y0, x0 + 256.0, y0 + 256.0, [layer],
                                                   lambda la_, lo_: np.zeros(np.shape(la_))):
            c = spec.ring.mean(0) + [x0, y0]
            cla, clo, _ = frame.enu_to_geodetic(float(c[0]), float(c[1]), 0.0)
            out[(round(float(cla), 5), round(float(clo), 5))] = spec
    return out


class StableSeedTest(unittest.TestCase):
    def check(self, layer):
        A = geodesy.EnuFrame(40.10, -104.94)                    # the game's Frederick anchor
        B = geodesy.EnuFrame(40.0992, -104.9372)                # (a teleport's: ~240 m away)
        C = geodesy.EnuFrame(40.112, -104.955)                  # (another, 1.9 km away)
        a = built(A, layer)
        self.assertGreater(len(a), 40)
        for other in (B, C):
            b = built(other, layer)
            self.assertEqual(set(a), set(b), "the same buildings")
            for key, sa in a.items():
                sb = b[key]
                for f in CHOICES:
                    va, vb = getattr(sa, f), getattr(sb, f)
                    if isinstance(va, float):
                        self.assertAlmostEqual(va, vb, places=6, msg=f"{key} {f}")
                    else:
                        self.assertEqual(va, vb, f"{key} {f}")
                ma, mb = bld.build([(sa, 0.0, 0.0)]), bld.build([(sb, 0.0, 0.0)])
                self.assertEqual(len(ma[0]), len(mb[0]), f"{key}: the facade's vertices")
                np.testing.assert_array_equal(ma[3], mb[3])
                np.testing.assert_allclose(ma[1], mb[1], atol=2e-3)            # (the frames turn by ~1e-4 rad)
                np.testing.assert_allclose(ma[0] - ma[0].mean(0), mb[0] - mb[0].mean(0), atol=0.02)

    def test_same_look_under_any_anchor(self):
        self.check(neighbourhood())

    def test_coverage_is_world_anchored(self):
        """world_coverage: the share of each z17 square the footprints cover, from the map's lon / lat alone."""
        layer = neighbourhood()
        cov = bld.world_coverage([layer, layer])            # (a building in two cells' layers: counted once)
        self.assertEqual(cov, bld.world_coverage([layer]))
        self.assertTrue(all(0.0 < v < 1.0 for v in cov.values()))
        lo, la, ar = bld._footprints(layer)
        side = 40075016.686 * np.cos(np.radians(la)) / (1 << bld.Z_COVER)
        self.assertAlmostEqual(sum(cov.values()), float((ar / side ** 2).sum()), places=9)

    def test_plan_seed_from_the_key(self):
        """plan() with a seed key: the same choices wherever the ring stands in its frame."""
        ring = np.array([(0.0, 0.0), (12.0, 0.0), (12.0, 9.0), (0.0, 9.0)])
        at = {"class": "house", "seed_key": "08b2abc", "cover": 0.1}
        s1 = bld.plan(ring, dict(at), 0, LAT, LON)
        s2 = bld.plan(ring + [137.25, -411.5], dict(at), 0, LAT, LON)
        for f in CHOICES:
            self.assertEqual(getattr(s1, f), getattr(s2, f), f)
        s3 = bld.plan(ring, {"class": "house"}, 0, LAT, LON)
        s4 = bld.plan(ring + [137.25, -411.5], {"class": "house"}, 0, LAT, LON)
        self.assertNotEqual(s3.seed, s4.seed)                   # (no key: the ring's centroid, as before)


if __name__ == "__main__":
    unittest.main()
