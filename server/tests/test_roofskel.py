"""Straight-skeleton roofs on the real outline (server/roofskel.py, buildings.roof_skeleton / _skeleton_roof)."""
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import buildings  # noqa: E402
import roofskel  # noqa: E402

RECT = [(0, 0), (10, 0), (10, 6), (0, 6)]
L = [(0, 0), (12, 0), (12, 5), (5, 5), (5, 11), (0, 11)]
T = [(0, 0), (12, 0), (12, 4), (8, 4), (8, 10), (4, 10), (4, 4), (0, 4)]
U = [(0, 0), (12, 0), (12, 9), (9, 9), (9, 3), (3, 3), (3, 9), (0, 9)]


def surface(mesh_tris, pts):
    """The highest z of the triangles (k, 3, 3) over each point (x, y), NaN where none is."""
    out = np.full(len(pts), np.nan)
    for a, b, c in mesh_tris:
        for j, (x, y) in enumerate(pts):
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(d) < 1e-12:
                continue
            l1 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / d
            l2 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / d
            l3 = 1 - l1 - l2
            if min(l1, l2, l3) >= -1e-9:
                z = l1 * a[2] + l2 * b[2] + l3 * c[2]
                out[j] = z if np.isnan(out[j]) else max(out[j], z)
    return out


def spec_for(ring, roof="hipped"):
    s = buildings.Spec(ring=np.array(ring, float), floors=1, floor_h=3.0, height=3.0, roof=roof,
                       roof_mat=f"{buildings.ROOF_SHINGLE}|0", facade=f"{buildings.SIDING}|0", band=None, plain=True, min_h=0.0,
                       cls="house", seed=0.3, steeple=False)
    return s


def roof_tris(spec, z_eave=10.0):
    m = buildings.Mesh()
    buildings._roof(m, spec, buildings.drawn_ring(spec), z_eave, buildings._Looks(spec.seed))
    P, UV, T_, M, C = m.arrays()
    return P[T_], M


class SkeletonTest(unittest.TestCase):
    def check_faces(self, ring, n_faces):
        nodes, nt, faces = roofskel.skeleton(ring)
        self.assertEqual(len(faces), n_faces)
        area = sum(roofskel._area2([nodes[k] for k in f]) for f in faces)
        self.assertAlmostEqual(area, roofskel._area2(ring), places=6)
        for i, f in enumerate(faces):           # each face starts on its own wall and every node at its distance
            self.assertEqual(f[:2], [i, (i + 1) % len(ring)])
        return nodes, nt, faces

    def test_rectangle(self):
        nodes, nt, faces = self.check_faces(RECT, 4)
        self.assertAlmostEqual(max(nt), 3.0)
        self.assertEqual(sorted(len(f) for f in faces), [3, 3, 4, 4])     # two hips, two slopes

    def test_l_shape_valley(self):
        nodes, nt, faces = self.check_faces(L, 6)
        self.assertAlmostEqual(max(nt), 2.5)
        # the valley: from the inner corner (5, 5) to the wings' ridges' meeting point (2.5, 2.5)
        inner = [f for f in faces if 3 in f[:2]]
        self.assertTrue(any(np.allclose(nodes[k], (2.5, 2.5)) for f in inner for k in f[2:]))

    def test_t_and_u(self):
        self.check_faces(T, 8)
        self.check_faces(U, 8)

    def test_gables_on_the_wing_ends(self):
        for ring, ends in ((RECT, [1, 3]), (L, [1, 4]), (T, [1, 4, 7])):
            nodes, nt, faces, gables = roofskel.gable(ring, *roofskel.skeleton(ring))
            self.assertEqual([g[0] for g in gables], ends)
            for i, g in gables:
                self.assertEqual(faces[i], [])
                (ax, ay), (bx, by) = ring[i], ring[(i + 1) % len(ring)]
                x, y = nodes[g]                 # on the end wall's line, at the ridge's height
                self.assertAlmostEqual((bx - ax) * (y - ay) - (by - ay) * (x - ax), 0.0, places=6)

    def test_square_keeps_its_peak(self):
        sq = [(0, 0), (8, 0), (8, 8), (0, 8)]
        nodes, nt, faces = roofskel.skeleton(sq)
        self.assertAlmostEqual(max(nt), 4.0)

    def test_degenerate(self):
        self.assertEqual(roofskel.degenerate([(0, 0), (10, 0), (10, 0.2), (10.1, 6), (0, 6)]), "short edge")
        self.assertEqual(roofskel.degenerate([(0, 0), (10, 0), (0, 0.5)]), "spike")
        self.assertEqual(roofskel.degenerate(RECT[::-1]), "orientation")
        self.assertIsNone(roofskel.skeleton([(0, 0), (10, 0), (10, 0.2), (10.1, 6), (0, 6)]))
        a = np.linspace(0, 2 * math.pi, 40, endpoint=False)
        self.assertEqual(roofskel.degenerate(list(zip(np.cos(a) * 9, np.sin(a) * 9))), "corners")

    def test_deterministic(self):
        rng = np.random.default_rng(7)
        for _ in range(20):
            k = int(rng.integers(5, 11))
            ang = np.sort(rng.uniform(0, 2 * math.pi, k))
            r = rng.uniform(6, 12, k)
            ring = list(zip((r * np.cos(ang)).tolist(), (r * np.sin(ang)).tolist()))
            self.assertEqual(roofskel.skeleton(ring), roofskel.skeleton(list(ring)))

    def test_offset_ring(self):
        out = roofskel.offset_ring(RECT, 0.5)
        np.testing.assert_allclose(out, [(-0.5, -0.5), (10.5, -0.5), (10.5, 6.5), (-0.5, 6.5)])
        self.assertIsNone(roofskel.offset_ring([(0, 0), (10, 0), (10, 6), (5.1, 6), (5, 0.3), (4.9, 6), (0, 6)], 2.0))


class BuildingRoofTest(unittest.TestCase):
    def test_rectangle_equals_the_box_roof(self):
        """The skeleton roof of a rectangle is the box roof (buildings._roof's hipped and gabled ones), point for
        point."""
        pts = [(x, y) for x in np.linspace(-0.3, 10.3, 23) for y in np.linspace(-0.3, 6.3, 15)]
        for roof in ("hipped", "gabled"):
            box = spec_for(RECT, roof)
            self.assertIsNone(buildings.roof_skeleton(box))         # box-like: the box path
            old, _ = roof_tris(box)
            sk = spec_for(RECT, roof)
            eaves = roofskel.offset_ring(RECT, buildings.EAVE_M)
            nodes, nt, faces = roofskel.skeleton(eaves)
            gables = []
            if roof == "gabled":
                nodes, nt, faces, gables = roofskel.gable(eaves, nodes, nt, faces)
            sk.skel = (eaves, nodes, nt, faces, gables)               # (forced: the skeleton path)
            new, _ = roof_tris(sk)
            a = surface([t for t in old], pts)
            b = surface([t for t in new], pts)
            np.testing.assert_allclose(a, b, atol=1e-6, err_msg=roof)

    def test_l_house_walls_and_roof_follow_the_outline(self):
        s = spec_for(L, "gabled")
        sk = buildings.roof_skeleton(s)
        self.assertIsNotNone(sk)
        np.testing.assert_array_equal(buildings.drawn_ring(s), s.ring)      # the walls: the outline, not its box
        tris, M = roof_tris(s)
        top = float(tris[:, :, 2].max())
        tp = math.tan(math.radians(buildings.roof_pitch(s)))
        ze = 10.0 - buildings.EAVE_M * tp
        self.assertAlmostEqual(top, ze + (2.5 + buildings.EAVE_M) * tp, places=6)     # the wings' ridge
        # nothing over the yard of the L (the box roof covered it)
        self.assertTrue(np.isnan(surface(list(tris), [(9.0, 8.0)])[0]))
        fac = buildings.MATERIALS.index(buildings.SIDING)
        self.assertEqual(int((M == fac).sum()), 2)                  # the two gable walls

    def test_box_like_and_fallbacks(self):
        self.assertTrue(buildings._box_like(np.array([(0, 0), (10, 0.1), (10, 6), (0, 6)], float)))
        self.assertFalse(buildings._box_like(np.array([(0, 0), (10, 1.5), (10, 6), (0, 6)], float)))
        tiny = spec_for([(0, 0), (10, 0), (10, 0.2), (10.1, 6), (0, 6)])
        self.assertIsNone(buildings.roof_skeleton(tiny))
        self.assertEqual(len(buildings.drawn_ring(tiny)), 4)       # its oriented box
        church = spec_for(L)
        church.cls = "church"
        self.assertIsNone(buildings.roof_skeleton(church))
        flat = spec_for(L, "flat")
        self.assertIsNone(buildings.roof_skeleton(flat))

    def test_built_house_deterministic(self):
        def mesh():
            s = spec_for(T, "gabled")
            return buildings.build([(s, 100.0, 100.5)])
        a, b = mesh(), mesh()
        for x, y in zip(a, b):
            if isinstance(x, np.ndarray):
                np.testing.assert_array_equal(x, y)


if __name__ == "__main__":
    unittest.main()
