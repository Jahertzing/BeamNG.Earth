"""Grass cards past the 3D grass (grasscards.py; the user, 2026-10-28: "im looking miles ahead so like 200m is
NOTHING"): the world-locked jittered grid, cards only where the grass mask is, on the ground (sunk a little),
coloured from the photo, the tile files, and the tracker that sends them."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import cdae  # noqa: E402
import geodesy  # noqa: E402
import grasscards as gc  # noqa: E402
import surface as sf  # noqa: E402
import vegetation  # noqa: E402


def _load(path):
    with open(path) as f:
        return json.load(f)


def flat(h):
    return lambda la, lo: np.full(np.shape(la), float(h))


class CandidatesTest(unittest.TestCase):
    def test_inside_the_square_and_deterministic(self):
        x, y, hk, hs, hy = gc.candidates(0.0, 0.0, 256.0)
        self.assertTrue(((x >= 0) & (x < 256) & (y >= 0) & (y < 256)).all())
        n = (256 / gc.SPACING_M) ** 2
        self.assertAlmostEqual(len(x) / n, 1.0, delta=0.05, msg="about one per grid cell")
        for h in (hk, hs, hy):
            self.assertTrue(((h >= 0) & (h < 1)).all())
        x2, y2, *_ = gc.candidates(0.0, 0.0, 256.0)
        self.assertTrue(np.array_equal(x, x2) and np.array_equal(y, y2))

    def test_world_locked(self):
        """The same place from two anchors 1000 m apart: the same cards (origin_xy = the anchor's offset)."""
        a = gc.candidates(1000.0, 0.0, 256.0)
        b = gc.candidates(0.0, 0.0, 256.0, origin_xy=(1000.0, 0.0))
        self.assertEqual(len(a[0]), len(b[0]))
        self.assertTrue(np.allclose(a[0], b[0] + 1000.0) and np.allclose(a[1], b[1]))
        self.assertTrue(np.array_equal(a[2], b[2]))

    def test_neighbours_do_not_overlap(self):
        a = gc.candidates(0.0, 0.0, 256.0)
        b = gc.candidates(256.0, 0.0, 256.0)
        pa = set(zip(np.round(a[0], 6), np.round(a[1], 6)))
        pb = set(zip(np.round(b[0], 6), np.round(b[1], 6)))
        self.assertFalse(pa & pb)


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.frame = geodesy.EnuFrame(40.0755, -104.8862)
        self.mask = np.zeros((gc.MASK_PX, gc.MASK_PX), np.uint8)
        self.mask[:, : gc.MASK_PX // 2] = 255                 # grass on the west half

    def test_cards_where_the_grass_is(self):
        P, UV, T, C, N = gc.build(self.frame, 0.0, 0.0, 256.0, self.mask, flat(1500.0))
        n = len(P) // 8
        self.assertEqual(P.shape, (8 * n, 3))
        self.assertEqual(T.shape, (4 * n, 3))
        self.assertEqual(C.shape, (8 * n, 4))
        self.assertEqual(UV.shape, (8 * n, 2))
        self.assertTrue((T.max() < len(P)) and T.min() == 0)
        foot = P[0::8]                                          # each card's first vertex: a foot corner
        centre = (P[0::8] + P[1::8]) / 2
        self.assertTrue((centre[:, 0] < 128.0 + 1e-6).all(), "only on the grassy (west) half")
        expect = n / ((128 / gc.SPACING_M) * (256 / gc.SPACING_M))
        self.assertAlmostEqual(expect, gc.KEEP, delta=0.05)
        self.assertTrue((N[:, 2] == 1.0).all(), "lit as the ground")
        self.assertTrue((C[:, :3] == gc.FALLBACK_RGB).all(), "no photo: the fallback colour")
        self.assertTrue((C[:, 3] == 255).all())
        z = self.frame.geodetic_to_enu(*self.frame.enu_to_geodetic(centre[:, 0], centre[:, 1], 0.0)[:2], 1500.0)[2]
        self.assertTrue(np.allclose(foot[:, 2], np.asarray(z) - gc.SINK_M, atol=0.05), "sunk SINK_M into the ground")
        h = P[2::8, 2] - P[1::8, 2]
        self.assertTrue(((h >= gc.HEIGHT_M[0] - 1e-6) & (h <= gc.HEIGHT_M[1] + 1e-6)).all())

    def test_photo_colours(self):
        photo = np.zeros((gc.PHOTO_PX, gc.PHOTO_PX, 3), np.uint8)
        photo[:] = (80, 100, 40)
        photo[: gc.PHOTO_PX // 2] = (120, 110, 60)            # north half: drier
        P, UV, T, C, N = gc.build(self.frame, 0.0, 0.0, 256.0, self.mask, flat(0.0), photo)
        centre_y = (P[0::8, 1] + P[1::8, 1]) / 2
        cols = C[0::8, :3].astype(int)
        south = cols[centre_y < 120]
        north = cols[centre_y > 136]
        self.assertTrue(len(south) and len(north))
        self.assertTrue((south == np.round(np.array((80, 100, 40)) * gc.BRIGHT).astype(int)).all(axis=1).mean() > 0.95)
        self.assertTrue((north == np.minimum(255, (np.array((120, 110, 60)) * gc.BRIGHT)).astype(int)).all(axis=1).mean() > 0.95)

    def test_no_grass_no_cards(self):
        self.assertIsNone(gc.build(self.frame, 0.0, 0.0, 256.0, np.zeros_like(self.mask), flat(0.0)))

    def test_capped(self):
        full = np.full_like(self.mask, 255)
        old = gc.MAX_CARDS
        gc.MAX_CARDS = 500
        try:
            P = gc.build(self.frame, 0.0, 0.0, 256.0, full, flat(0.0))[0]
        finally:
            gc.MAX_CARDS = old
        self.assertEqual(len(P) // 8, 500)
        cx = (P[0::8, 0] + P[1::8, 0]) / 2
        self.assertTrue(cx.min() < 40 and cx.max() > 216, "thinned evenly, not cut off")
        n = len(gc.build(self.frame, 0.0, 0.0, 256.0, full, flat(0.0))[0]) // 8
        self.assertLessEqual(n * 8, 65535, "a full tile within the 16-bit vertex budget")


class FilesTest(unittest.TestCase):
    def test_tile_files(self):
        frame = geodesy.EnuFrame(40.0, -105.0)
        mask = np.full((gc.MASK_PX, gc.MASK_PX), 255, np.uint8)
        mesh = gc.build(frame, 256.0, -512.0, 256.0, mask, flat(10.0))
        with tempfile.TemporaryDirectory() as d:
            path, n = gc.write_tile(mesh, d, "/temp/bne", 3, 1, -2, 256.0, -512.0)
            self.assertEqual(path, "/temp/bne/grfar_3_1_-2.json")
            self.assertEqual(n, len(mesh[0]) // 8)
            data = _load((os.path.join(d, "grfar_3_1_-2.json")))
            self.assertEqual(data["shape"], "/temp/bne/grfar_3_1_-2.cdae")
            self.assertEqual(data["pos"], [256.0, -512.0, 0.0])
            shape = cdae.read(os.path.join(d, "grfar_3_1_-2.cdae"))
            self.assertEqual([m["name"] for m in shape["materials"]], [gc.MATERIAL])
            path, n = gc.empty_tile(d, "/temp/bne", 3, 2, -2)
            self.assertEqual(n, 0)
            self.assertNotIn("shape", _load((os.path.join(d, "grfar_3_2_-2.json"))))

    def test_material_and_textures_in_the_mod(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mod", "beamng_earth")
        mats = _load((os.path.join(root, "levels", "beamng_earth", "art", "shapes", "beamngEarth",
                                           "grasscards.materials.json")))
        m = mats[gc.MATERIAL]
        self.assertTrue(m["Stages"][0]["vertColor"] and m["alphaTest"] and m["doubleSided"])
        for k in ("baseColorMap", "opacityMap"):
            self.assertTrue(os.path.exists(os.path.join(root, m["Stages"][0][k].lstrip("/"))), k)


class GrassMaskExTest(unittest.TestCase):
    def test_complete_flag(self):
        frame = geodesy.EnuFrame(40.0, -105.0)

        class Fake:
            gas_pads = False

            def __init__(self, cls, complete):
                self.cls, self.complete = cls, complete

            def classes(self, lat, lon):
                return np.full(np.shape(lat), self.cls, np.uint8), self.complete

            def roads(self, lat, lon):
                return [], True

        self.assertEqual(sf.grass_mask_ex(frame, 0.0, 0.0, 256.0, 32, None), (None, True))
        self.assertEqual(sf.grass_mask_ex(frame, 0.0, 0.0, 256.0, 32, Fake(30, False)), (None, False))
        self.assertEqual(sf.grass_mask_ex(frame, 0.0, 0.0, 256.0, 32, Fake(60, True)), (None, True))
        m, ok = sf.grass_mask_ex(frame, 0.0, 0.0, 256.0, 32, Fake(30, True))
        self.assertTrue(ok and m is not None and m.shape == (32, 32))


class TrackerTest(unittest.TestCase):
    def test_not_under_the_3d_grass(self):
        """The Tracker's wanted tiles: out to SEND_M, none whose square comes within SKIP_M."""
        import roads as roadsmod
        tr = vegetation.Tracker(gc.SEND_M, gc.DROP_M, gc.PER_POSE, "grfar", skip_m=gc.SKIP_M)
        near = set(roadsmod.tiles_near(128.0, 128.0, gc.SKIP_M))
        far = set(tr._near(128.0, 128.0, gc.SEND_M))
        self.assertIn((0, 0), near)
        self.assertTrue(near < far)
        self.assertIn((4, 0), far - near)


class WorkerTest(unittest.TestCase):
    """blockworker.grass_cards_tile: the tile over road tile square (tx, ty), from the process's surface and DEM."""

    def run_tile(self, cls, complete=True):
        import blockworker

        class Surf:
            gas_pads = False

            def classes(self, lat, lon):
                return np.full(np.shape(lat), cls, np.uint8), complete

            def roads(self, lat, lon):
                return [], True

        class Dem:
            def sample(self, la, lo, zoom):
                return np.full(np.shape(la), 1600.0)

        class S:
            surface, dem, airports, sea = Surf(), Dem(), None, None

        old = blockworker._s
        blockworker._s = S()
        try:
            with tempfile.TemporaryDirectory() as d:
                got = blockworker.grass_cards_tile(4, 40.0, -105.0, 14, 2, -1, d, "/temp/bne")
                files = sorted(os.listdir(d))
        finally:
            blockworker._s = old
        return got, files

    def test_grassland(self):
        got, files = self.run_tile(30)
        self.assertEqual(got[0], "/temp/bne/grfar_4_2_-1.json")
        self.assertGreater(got[1], 1000)
        self.assertEqual(files, ["grfar_4_2_-1.cdae", "grfar_4_2_-1.json"])

    def test_bare_or_not_cached(self):
        self.assertEqual(self.run_tile(60), (("/temp/bne/grfar_4_2_-1.json", 0), ["grfar_4_2_-1.json"]))
        self.assertEqual(self.run_tile(30, complete=False), (None, []))


if __name__ == "__main__":
    unittest.main()
