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

    def test_photo_rows_averaged(self):
        """A field's alternating 4 m rows (green crop, brown soil): the cards take their mean, not speckles of each."""
        photo = np.zeros((gc.PHOTO_PX, gc.PHOTO_PX, 3), np.uint8)
        photo[:, 0::2] = (60, 120, 40)
        photo[:, 1::2] = (130, 90, 60)
        P, UV, T, C, N = gc.build(self.frame, 0.0, 0.0, 256.0, self.mask, flat(0.0), photo)
        cx = (P[0::8, 0] + P[1::8, 0]) / 2
        cols = C[0::8, :3][(cx > 8) & (cx < 120)].astype(float)
        self.assertLess(cols.std(axis=0).max(), 0.1 * 255 * gc.BRIGHT / 3, "no speckles")

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
            # its SUB x SUB squares (drawn near the player): every square of a full tile, at its corner
            self.assertEqual(data["subSize"], 64.0)
            self.assertEqual(len(data["subs"]), gc.SUB * gc.SUB)
            self.assertEqual(data["subs"][0], {"shape": "/temp/bne/grfar_3_1_-2_00.cdae", "pos": [256.0, -512.0, 0.0]})
            self.assertEqual(data["subs"][-1]["pos"], [256.0 + 192.0, -512.0 + 192.0, 0.0])
            for e in data["subs"]:
                self.assertTrue(os.path.exists(os.path.join(d, os.path.basename(e["shape"]))))
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


class SplitTest(unittest.TestCase):
    def test_squares_hold_every_card_once(self):
        """split: each card in the square of its foot, positions relative to that square, triangles re-indexed."""
        frame = geodesy.EnuFrame(40.0, -105.0)
        mask = np.full((gc.MASK_PX, gc.MASK_PX), 255, np.uint8)
        mask[:, gc.MASK_PX // 2:] = 0                       # (x half 128-256 m bare: squares i 2, 3 left out)
        mesh = gc.build(frame, 0.0, 0.0, 256.0, mask, flat(5.0))
        parts = gc.split(mesh, 256.0)
        n = len(mesh[0]) // 8
        self.assertEqual(sum(len(m[0]) // 8 for _, _, m in parts), n)
        self.assertEqual(sorted({i for i, _, _ in parts}), [0, 1])
        P = mesh[0].reshape(n, 8, 3)
        foot = P[:, :2, :2].mean(1)
        for i, j, (p, uv, t, c, nm) in parts:
            k = len(p) // 8
            self.assertEqual(len(t), 4 * k)
            self.assertEqual(int(t.min()), 0)
            self.assertEqual(int(t.max()), 8 * k - 1)
            f = p.reshape(k, 8, 3)[:, :2, :2].mean(1)
            self.assertTrue(((f >= -1e-9) & (f < 64.0 + 1e-9)).all(), (i, j))
            self.assertEqual(len(uv), len(p))
            self.assertEqual(len(c), len(p))
            self.assertEqual(len(nm), len(p))
            # the same cards: shifted back they are among the tile's
            back = f + np.array([i * 64.0, j * 64.0])
            self.assertTrue(np.isin(np.round(back[:, 0], 6), np.round(foot[:, 0], 6)).all())
            # triangles pick the right vertices: triangle 0 of the square's card 0 is its own first three
            self.assertEqual(t[0].tolist(), [0, 1, 2])


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
    def test_the_tiles_round_the_car_too(self):
        """The Tracker's wanted tiles: out to SEND_M, the car's own too (the mod hides the squares under it; left out
        before 2026-10-29: a hole round the car after every teleport and rebase)."""
        tr = vegetation.Tracker(gc.SEND_M, gc.DROP_M, gc.PER_POSE, "grfar", skip_m=gc.SKIP_M)
        self.assertFalse(tr.skip_m)
        far = set(tr._near(128.0, 128.0, gc.SEND_M))
        self.assertIn((0, 0), far)
        self.assertIn((4, 0), far)


class SubsTest(unittest.TestCase):
    """The near tiles' squares again (grsub): only once the tile itself was sent, their shapes pooled."""

    def test_grass_sub_tile(self):
        import earthd
        frame = geodesy.EnuFrame(40.0, -105.0)
        mask = np.full((gc.MASK_PX, gc.MASK_PX), 255, np.uint8)
        mesh = gc.build(frame, 0.0, 0.0, 256.0, mask, flat(10.0))
        with tempfile.TemporaryDirectory() as d:
            gc.write_tile(mesh, d, "/temp/bne", 3, 1, -2, 0.0, 0.0)
            srv = type("S", (), {"out_dir": d, "vfs_dir": "/temp/bne"})()
            self.assertIsNone(earthd.Server.grass_sub_tile(srv, 3, 1, -2, set()), "not sent yet")
            self.assertEqual(earthd.Server.grass_sub_tile(srv, 3, 1, -2, {(1, -2)}),
                             ("/temp/bne/grfar_3_1_-2.json", gc.SUB * gc.SUB))
            self.assertIsNone(earthd.Server.grass_sub_tile(srv, 3, 2, -2, {(2, -2)}), "no file")

    def test_squares_pooled(self):
        from concurrent.futures import Future
        frame = geodesy.EnuFrame(40.0, -105.0)
        mask = np.full((gc.MASK_PX, gc.MASK_PX), 255, np.uint8)
        mesh = gc.build(frame, 0.0, 0.0, 256.0, mask, flat(10.0))
        with tempfile.TemporaryDirectory() as d:
            gc.write_tile(mesh, d, "/temp/bne", 3, 1, -2, 0.0, 0.0)
            asked, sent = [], []

            def pool(conn, files):
                asked.append(files)
                return {k: [f"/temp/bne/slot_{i:05d}.cdae", i] for i, k in enumerate(files)}

            conn = type("C", (), {"s": type("S", (), {"out_dir": d, "pool_shapes": staticmethod(pool)})(),
                                  "send": lambda self, m: sent.append(m)})()
            tr = vegetation.Tracker(gc.SUB_SEND_M, gc.SUB_DROP_M, gc.SUB_PER_POSE, "grsub")
            tr.aid = 3
            fut = Future()
            fut.set_result(("/temp/bne/grfar_3_1_-2.json", gc.SUB * gc.SUB))
            tr._built(conn, 3, (1, -2), fut)
            self.assertEqual(len(asked), 1)
            self.assertEqual(asked[0]["s0"], "grfar_3_1_-2_00.cdae")
            self.assertEqual(len(asked[0]), gc.SUB * gc.SUB)
            m = sent[0]
            self.assertEqual((m["type"], m["key"], m["n"]), ("grsub_tile", "1_-2", gc.SUB * gc.SUB))
            self.assertEqual(len(m["data"]["subs"]), gc.SUB * gc.SUB)
            self.assertEqual(m["slots"]["s0"], ["/temp/bne/slot_00000.cdae", 0])


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
        self.assertEqual(files[:2], ["grfar_4_2_-1.cdae", "grfar_4_2_-1.json"])
        self.assertEqual(len(files), 2 + gc.SUB * gc.SUB)

    def test_no_cards_on_cropland(self):
        self.assertEqual(self.run_tile(40)[0][1], 0)

    def test_bare_or_not_cached(self):
        self.assertEqual(self.run_tile(60), (("/temp/bne/grfar_4_2_-1.json", 0), ["grfar_4_2_-1.json"]))
        self.assertEqual(self.run_tile(30, complete=False), (None, []))


if __name__ == "__main__":
    unittest.main()
