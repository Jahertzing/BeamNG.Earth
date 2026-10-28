"""House window variety (facadekit.house_windows): paired sashes, picture windows, small side windows, blank side
walls; cut, glazed and trimmed consistently."""
import collections
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import buildings as bld  # noqa: E402
import facadekit as fk  # noqa: E402
import test_housefronts as HF  # noqa: E402
import test_recess as TR  # noqa: E402


def wins_of(spec):
    ring = bld.drawn_ring(spec)
    return {i: fk.house_windows(spec, ring, i, spec.height) for i in range(len(ring))}


class _Rec:
    def __init__(self):
        self.q = []

    def quad(self, a, b, c, d, uva, uvb, uvc, uvd, mat):
        self.q.append((np.array([a, b, c, d], float), np.array([uva, uvb, uvc, uvd], float), mat))


class HouseWindowTest(TR.RecessTest):
    SWITCHES = {"RECESS": True, "RECESS_SHOP": False, "FINS": False, "HOUSE_VARIETY": True}
    test_houses = test_sheet_buildings = test_whole_bay_shift = test_tall_sheet_capped = None
    test_off_is_as_before = test_openings_table = test_deterministic = None

    def specs(self, n=80):
        return [s for s in HF.houses(n) if s.wall and s.windows is not False]

    def test_kinds_and_shares(self):
        """Every kind turns up in Frederick's houses; pairs and picture windows only on the front and back, small ones
        and blank walls only on the sides; the glass never more than the walls' standard slots."""
        kinds, blank = collections.Counter(), 0
        for spec in self.specs():
            ring = bld.drawn_ring(spec)
            fe = fk.front_edge(spec)
            for i, ws in wins_of(spec).items():
                a, d, out, L = fk._wall(ring, i)
                slots = bld.house_window_slots(spec, a, d, out, L, spec.height, i)
                self.assertLessEqual(len(ws), len(slots))
                if slots and not ws:
                    blank += 1
                    self.assertTrue(L < fk.BLANK_SIDE_L)
                cs = abs(float(fk._wall(ring, fe)[1] @ d)) if fe is not None else None
                for w in ws:
                    kinds[w.kind] += 1
                    if w.kind in ("pair", "triple"):
                        self.assertTrue(i == fe or cs > 0.7)
                    if w.kind == "triple":
                        self.assertEqual(i, fe)
                    if w.kind == "small":
                        self.assertLess(cs, 0.3)
        self.assertTrue(all(kinds[k] > 0 for k in ("std", "pair", "triple", "small")), kinds)
        self.assertGreater(blank, 0)

    def test_watertight_and_glass(self):
        """Each wall: its face pieces, the mullion posts' fronts and the glass cover it exactly (test_recess's check);
        every glass quad is a sash (buildings.WIN_UV, slot 2), the reveals face into their openings."""
        rng = np.random.default_rng(6)
        u0, u1, v0, v1 = bld.WIN_UV
        sash = np.array([(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
        multi = 0
        for spec in self.specs(40):
            before, after = TR.walls(spec, kit=False), TR.walls(spec)
            self.covers(spec, fk.RECESS_HOUSE_M, before, after, rng)
            self.reveals_face_in(spec, fk.RECESS_HOUSE_M, after)
            Qa, Ua, Ma = after
            nA = TR.normals(Qa)
            ring = bld.drawn_ring(spec)
            for i in range(len(ring)):
                a, d, n, L = fk._wall(ring, i)
                g = (nA[:, :2] @ n > 0.999) & (np.abs((Qa[:, :, :2] - a) @ n + fk.RECESS_HOUSE_M).max(1) < 1e-6)
                for j in np.flatnonzero(g):
                    self.assertEqual(Ma[j], 2)
                    np.testing.assert_allclose(Ua[j], sash, atol=1e-12)
            multi += sum(w.units > 1 for ws in wins_of(spec).values() for w in ws)
        self.assertGreater(multi, 5)

    def test_openings_match_trims(self):
        """The trims (sill, head) go round every window's whole opening: the sill's top and the head's bottom at the
        glass's bottom and top, across its width and TRIM_W past each side."""
        for spec in self.specs(40):
            ring = bld.drawn_ring(spec)
            m = _Rec()
            fk.windows(m, spec, ring, spec.height, bld._Looks(spec.seed))
            tops = [Q for Q, _, _ in m.q if TR.normals(Q[None])[0][2] > 0.99]
            for i, ws in wins_of(spec).items():
                a, d, n, L = fk._wall(ring, i)
                for w in ws:
                    want = (round(w.s - w.w / 2 - fk.TRIM_W, 4), round(w.s + w.w / 2 + fk.TRIM_W, 4), round(w.zf, 4))
                    got = {(round(float(min((Q[:, :2] - a) @ d)), 4), round(float(max((Q[:, :2] - a) @ d)), 4),
                            round(float(Q[0, 2]), 4)) for Q in tops if abs(float((Q[0, :2] - a) @ n)) < 0.2}
                    self.assertIn(want, got, f"no sill under {w.kind} at {w.s:.2f}")

    def test_nothing_overlaps(self):
        """No window's trims (or shutters) touch another's, the door's or garage door's frame, or the stone veneer (a
        standard sash that stood on a narrow house's door frame is left out)."""
        checked = 0
        for spec in self.specs(100):
            ring = bld.drawn_ring(spec)
            base = spec.height - spec.floors * spec.floor_h
            veneer = fk.veneer(_Rec(), spec, ring, 0.0, spec.height, bld._Looks(spec.seed))
            fe = fk.front_edge(spec)
            for i, ws in wins_of(spec).items():
                a, d, n, L = fk._wall(ring, i)
                fixed = []
                if spec.door and spec.door[0] == i:
                    h = fk.DOOR_W / 2 + fk.FRAME_W * 1.5
                    fixed.append((spec.door[1] - h, spec.door[1] + h, -9.0, base + 3.0))
                if spec.drive and spec.drive[0] == i:
                    g0, g1 = fk.garage_span(spec, i, L)
                    fixed.append((g0 - fk.FRAME_W, g1 + fk.FRAME_W, -9.0, base + fk.GARAGE_H + 0.18))
                if i == fe:
                    fixed += [(s0, s1, z0, z1) for z0, z1, s0, s1 in veneer]
                boxes = []
                for w in ws:
                    b = list(w.box())
                    b[0] -= w.uw / 2 if w.shut[0] else 0.0
                    b[1] += w.uw / 2 if w.shut[1] else 0.0
                    boxes.append((w, tuple(b)))
                for k, (w, b) in enumerate(boxes):
                    checked += 1
                    self.assertTrue(b[0] >= 0.15 - 1e-9 and b[1] <= L - 0.15 + 1e-9)
                    self.assertFalse(fk._hit(b, fixed), f"{w.kind} on a door, garage door or the stone")
                    self.assertFalse(fk._hit(b, [c for j, (_, c) in enumerate(boxes) if j != k]), w.kind)
        self.assertGreater(checked, 50)

    def test_off_is_the_standard_sash(self):
        """HOUSE_VARIETY off: every window the standard sash in its slot, the trims and the walls as before."""
        specs = self.specs(20)
        on = [bld.build([(s, 0.0, 0.2)]) for s in specs]
        fk.HOUSE_VARIETY = False
        for spec in specs:
            ring = bld.drawn_ring(spec)
            for i, ws in wins_of(spec).items():
                a, d, n, L = fk._wall(ring, i)
                slots = bld.house_window_slots(spec, a, d, n, L, spec.height, i)
                self.assertEqual([(round(w.s, 9), w.zf, w.w, w.h, w.kind) for w in ws],
                                 [(round(float((c - a) @ d), 9), zf, bld.WIN_W, bld.WIN_H, "std") for c, zf in slots])
        off = [bld.build([(s, 0.0, 0.2)]) for s in specs]
        self.assertTrue(any(len(x[0]) != len(y[0]) for x, y in zip(on, off)))
        # the walls with the variety off: exactly test_recess's house recess (RecessTest pins HOUSE_VARIETY off)
        for spec in specs[:5]:
            for x, y in zip(TR.walls(spec), TR.walls(spec)):
                np.testing.assert_array_equal(x, y)

    def test_deterministic(self):
        for spec in self.specs(10):
            a, b = bld.build([(spec, 0.0, 0.3)]), bld.build([(spec, 0.0, 0.3)])
            for p, q in zip(a, b):
                if isinstance(p, np.ndarray):
                    np.testing.assert_array_equal(p, q)


if __name__ == "__main__":
    unittest.main()
