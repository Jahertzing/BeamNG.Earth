"""The facade kit (server/facadekit.py): window trims, shutters, door frames, garage doors, awnings, balconies and
stone bases on the near buildings."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import buildings as bld  # noqa: E402
import facadekit as fk  # noqa: E402

LAT, LON = 40.1, -104.9


def rect(x, y, w, h):
    return np.array([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], float)


def house(k=0, w=12.0, d=9.0):
    return bld.plan(rect(k * 30.0, 0.0, w, d), {"class": "house"}, 0, LAT, LON)


def verts(items, **kw):
    m = bld.build(items, **kw)
    return 0 if m is None else len(m[0])


def kit_mesh(spec, gmin=0.0, gmax=0.0):
    m = bld.Mesh()
    ring = bld.drawn_ring(spec)
    z_eave = bld.base_z(spec, gmin, gmax) + spec.height
    fk.add(m, spec, ring, gmin, gmax, z_eave, bld._Looks(spec.seed))
    return m.arrays() if m.n else None


class KitTest(unittest.TestCase):
    def test_house_trims_round_its_windows(self):
        spec = house()
        self.assertTrue(spec.wall)
        P, UV, T, M, C = kit_mesh(spec)
        ring = bld.drawn_ring(spec)
        z_eave = spec.height
        slots = [(c, zf) for i in range(len(ring)) for c, zf in bld.house_window_slots(
            spec, ring[i], (ring[(i + 1) % 4] - ring[i]) / np.linalg.norm(ring[(i + 1) % 4] - ring[i]),
            np.array([0.0, 0.0]), np.linalg.norm(ring[(i + 1) % 4] - ring[i]), z_eave, i)]
        self.assertTrue(slots)
        sills = {round(zf, 3) for _, zf in slots}
        self.assertTrue(sills <= {round(float(z), 3) for z in P[:, 2]})      # a sill top at every sill height
        self.assertGreaterEqual(len(P), 24 * len(slots))                      # sill, head, two jambs a window

    def test_kit_faces_out_and_up(self):
        for k in range(8):
            spec = house(k)
            P, UV, T, M, C = kit_mesh(spec)
            n = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
            n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
            mid = P[T].mean(1)
            c = np.append(bld.drawn_ring(spec).mean(0), 0.0)
            flat = np.abs(n[:, 2]) > 0.99
            self.assertTrue((n[flat, 2] > 0).all(), "tops face up")
            side = ~flat
            self.assertTrue((((mid - c)[side, :2] * n[side, :2]).sum(1) > 0).all(), "fronts face out")

    def test_garage_door_where_the_driveway_meets_the_house(self):
        spec = house(w=14.0)
        road = (np.array([(-50.0, -12.0), (80.0, -12.0)]), 4.0)
        drives = []
        bld.doors([(spec, 0.0, 0.0)], [road], 0.0, 0.0, drives)
        self.assertTrue(drives)
        self.assertIsNotNone(spec.drive)
        edge, along = spec.drive
        L = float(np.linalg.norm(np.diff(bld.drawn_ring(spec)[[edge, (edge + 1) % 4]], axis=0)))
        s0, s1 = fk.garage_span(spec, edge, L)
        self.assertIn(round(s1 - s0, 2), (fk.GARAGE_SINGLE_M, fk.GARAGE_DOUBLE_M))
        ring = bld.drawn_ring(spec)
        a = ring[edge]
        d = (ring[(edge + 1) % 4] - a) / L
        for c, zf in bld.house_window_slots(spec, a, d, np.array([d[1], -d[0]]), L, spec.height, edge):
            s = float(np.dot(c - a, d))
            if zf < spec.floor_h:                                       # no ground-floor window over the door
                self.assertTrue(s + bld.WIN_W / 2 <= s0 - 0.3 or s - bld.WIN_W / 2 >= s1 + 0.3)
        siding = bld.MATERIALS.index(bld.SIDING)
        P, UV, T, M, C = kit_mesh(spec)
        door = P[T[M == siding]].reshape(-1, 3)
        self.assertAlmostEqual(float(door[:, 2].max()), fk.GARAGE_H, places=6)

    def test_balconies_on_apartments(self):
        found = 0
        for k in range(30):
            spec = bld.plan(rect(k * 40.0, 0.0, 30.0, 14.0), {"class": "apartments", "num_floors": 4}, 0, LAT, LON)
            spec.door = (0, 15.0)
            got = kit_mesh(spec)
            slab = bld.MATERIALS.index(bld.CONCRETE)
            if got is None or not (got[3] == slab).any():
                continue
            found += 1
            P, UV, T, M, C = got
            z = sorted({round(float(v), 2) for v in P[T[M == slab]].reshape(-1, 3)[:, 2]})
            base = spec.height - spec.floors * spec.floor_h
            for f in range(1, spec.floors):                             # a slab's top at each upper floor
                self.assertIn(round(base + f * spec.floor_h, 2), z)
            self.assertLessEqual(float(P[:, 1].min()), -fk.BALCONY_DEPTH + 1e-6)     # out from the south wall
        self.assertGreater(found, 5)
        self.assertLess(found, 25)                                      # (a share of them: BALCONY_SHARE)

    def test_lite_and_far_have_none(self):
        spec = house()
        self.assertEqual(verts([(spec, 0.0, 0.0)], lite=True), verts([(spec, 0.0, 0.0)], lite=True, kit=False))
        self.assertGreater(verts([(spec, 0.0, 0.0)]), verts([(spec, 0.0, 0.0)], kit=False))

    def test_budget_leaves_the_kit_off_not_the_buildings(self):
        items = [(house(k), 0.0, 0.0) for k in range(40)]
        full = bld.build_parts(items, max_verts=2000)
        plain = bld.build_parts([(s, a, b) for s, a, b in items], max_verts=2000)
        capped = bld.build_parts(items, max_verts=2000, max_parts=2)
        n_full = sum(len(p[0]) for p in full)
        n_capped = sum(len(p[0]) for p in capped)
        self.assertEqual(n_full, sum(len(p[0]) for p in plain))
        self.assertLess(n_capped, n_full)
        nokit = sum(verts([it], kit=False) for it in items)
        self.assertGreaterEqual(n_capped, nokit)                       # every building still there

    def test_deterministic(self):
        a = bld.build([(house(3), 0.0, 1.0)])
        b = bld.build([(house(3), 0.0, 1.0)])
        for x, y in zip(a, b):
            if isinstance(x, np.ndarray):
                np.testing.assert_array_equal(x, y)


if __name__ == "__main__":
    unittest.main()
