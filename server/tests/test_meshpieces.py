"""Colliding meshes in pieces (server/meshpieces.py, roadmesh.write_pieces, sidewalks / buildings collision pieces,
roads.write_tile's deck2.. entries and shape_files' names): the split keeps every triangle once with only the vertices
a piece uses, small meshes stay one file, and a tile's big deck comes as several units."""
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import meshpieces  # noqa: E402
import roadmesh  # noqa: E402
import roads  # noqa: E402
import cdae  # noqa: E402


def strip(n_quads, width=8.0, step=2.0):
    """A road strip of n_quads quads along +x: (P, T, UV, mats, M) as roadmesh.write takes it."""
    xs = np.arange(n_quads + 1) * step
    P = np.array([[x, y, 0.0] for x in xs for y in (-width / 2, width / 2)])
    T = []
    for i in range(n_quads):
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        T.append([a, c, b])
        T.append([b, c, d])
    T = np.array(T)
    UV = np.column_stack([P[:, 0] / 10.0, (P[:, 1] + width / 2) / width])
    M = np.zeros(len(T), np.int64)
    return P, T, UV, ["bne_asphalt"], M


class SplitTest(unittest.TestCase):
    def test_small_mesh_is_one_piece(self):
        P, T, UV, mats, M = strip(10)
        pieces = meshpieces.split(T, 400, 12)
        self.assertEqual(len(pieces), 1)
        (lo, hi), ids, Ti = pieces[0]
        self.assertEqual((lo, hi), (0, 20))
        self.assertEqual(len(ids), 22)
        np.testing.assert_array_equal(ids[Ti], T)                   # renumbered back: the same triangles

    def test_big_mesh_in_even_pieces_with_their_own_vertices(self):
        P, T, UV, mats, M = strip(500)                               # 1000 triangles
        pieces = meshpieces.split(T, 400, 12)
        self.assertEqual(len(pieces), 3)
        self.assertEqual([hi - lo for (lo, hi), _, _ in pieces], [333, 334, 333])
        seen = 0
        for (lo, hi), ids, Ti in pieces:
            np.testing.assert_array_equal(ids[Ti], T[lo:hi])
            self.assertLessEqual(len(ids), (hi - lo) // 2 * 2 + 4)   # a strip: ~one vertex a triangle, not all 1002
            seen += hi - lo
        self.assertEqual(seen, 1000)
        self.assertEqual(len(meshpieces.split(T, 10, 4)), 4)          # the cap: 4 pieces of 250
        self.assertEqual(meshpieces.split(np.zeros((0, 3), int), 10, 4), [])

    def test_piece_paths(self):
        self.assertEqual(meshpieces.piece_path("/x/deck_1_2_3.cdae", 1), "/x/deck_1_2_3.cdae")
        self.assertEqual(meshpieces.piece_path("/x/deck_1_2_3.cdae", 2), "/x/deck_1_2_3_2.cdae")


class WritePiecesTest(unittest.TestCase):
    def test_roadmesh_pieces_written_and_readable(self):
        with tempfile.TemporaryDirectory() as d:
            names = roadmesh.write_pieces(os.path.join(d, "deck_1_0_0.cdae"), strip(500), 400, 12)
            self.assertEqual(names, ["deck_1_0_0.cdae", "deck_1_0_0_2.cdae", "deck_1_0_0_3.cdae"])
            sizes = []
            for n in names:
                p = os.path.join(d, n)
                self.assertTrue(os.path.isfile(p))
                self.assertEqual(cdae.read_version(p), 0x007C001F)          # a shape the engine reads
                sizes.append(os.path.getsize(p))
            self.assertLess(max(sizes), os.path.getsize(os.path.join(d, "whole.cdae"))
                            if roadmesh.write(os.path.join(d, "whole.cdae"), strip(500)) else 0)   # each piece smaller than the whole
            self.assertEqual(roadmesh.write_pieces(os.path.join(d, "small.cdae"), strip(10), 400, 12), ["small.cdae"])

    def test_write_tile_names_the_pieces(self):
        with tempfile.TemporaryDirectory() as d:
            chunks = [{"m": "x", "w": 1, "tl": 1, "p": 1, "n": [[0, 0, 0], [1, 0, 0]]}]
            roads.write_tile(d, "/vfs", 1, 0, 0, chunks, None, None, strip(500))
            import json
            data = json.load(open(os.path.join(d, "roads_1_0_0.json"), encoding="utf-8"))
            self.assertEqual(data["deck"]["shape"], "/vfs/deck_1_0_0.cdae")
            self.assertEqual(data["deck2"]["shape"], "/vfs/deck_1_0_0_2.cdae")
            self.assertEqual(data["deck3"]["shape"], "/vfs/deck_1_0_0_3.cdae")
            self.assertNotIn("deck4", data)
            files = roads.shape_files(1, 0, 0)
            self.assertEqual(files["deck2"], "deck_1_0_0_2.cdae")
            self.assertEqual(files["bldcol12"], "bldcol_1_0_0_12.cdae")
            self.assertNotIn("deck13", files)
            self.assertNotIn("road2", files)


if __name__ == "__main__":
    unittest.main()
