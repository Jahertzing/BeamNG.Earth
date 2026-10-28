"""landmarks: curved roofs (dome, onion, spire) on their footprint and on building parts, open-bowl stadiums, the
landmark table (by OSM id, by a point; kept over DRCOG's roofprints) and its placed pieces (a dome, the Castle Rock
Star)."""
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buildings as bld  # noqa: E402
import geodesy  # noqa: E402
import landmarks  # noqa: E402

GROUND = staticmethod(lambda la, lo: np.zeros(np.shape(la)))


def disc(cx, cy, r, n=32):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.stack([cx + r * np.cos(t), cy + r * np.sin(t)], 1)


def rect(x, y, w, d):
    return np.array([(x, y), (x + w, y), (x + w, y + d), (x, y + d)], np.float64)


def normals(P, T):
    n = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    return n, P[T].mean(1)


class Feats:
    def __init__(self, ids, rings, **attrs):
        self.ids = np.array(ids, object)
        self.kind = np.full(len(ids), 3, np.uint8)
        self.rings = rings
        self.attrs = {k: np.array(v, object if isinstance(v[0], str) else np.float64) for k, v in attrs.items()}

    def parts(self, i):
        return [[np.asarray(self.rings[i], np.float64)]]


class TableCase(unittest.TestCase):
    """A test with its own landmark table (landmarks.TABLE pointed at a temporary file)."""

    def use_table(self, t):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "landmarks.json")
        with open(p, "w") as f:
            json.dump(t, f)
        old = landmarks.TABLE
        landmarks.TABLE, landmarks._table = p, None
        self.addCleanup(lambda: (setattr(landmarks, "TABLE", old), setattr(landmarks, "_table", None)))


class CurvedRoofTest(unittest.TestCase):
    def check_outward(self, spec, axis_z):
        P, UV, T, M, mats, C = bld.build([(spec, 0.0, 0.0)], kit=False)
        c = landmarks.centroid(spec.ring)
        n, mid = normals(P, T)
        roof = mid[:, 2] > axis_z + 0.01
        out = mid[roof] - np.array([c[0], c[1], axis_z])
        self.assertTrue(((out * n[roof]).sum(1) > 0).all(), "every roof triangle faces out")
        self.assertTrue(np.isfinite(P).all())
        return P, M

    def test_dome_on_a_round_footprint(self):
        spec = bld.plan(disc(0, 0, 10), {"class": "civic", "height": 30.0, "roof_shape": "dome"})
        self.assertEqual(spec.roof, "dome")
        self.assertAlmostEqual(spec.rise, landmarks.radius(bld._area2(spec.ring) / 2), places=6)
        self.assertAlmostEqual(spec.height + spec.rise, 30.0, places=6, msg="the mapped height is the top")
        P, M = self.check_outward(spec, spec.height)
        self.assertAlmostEqual(P[:, 2].max(), 30.0, places=3)

    def test_onion_and_spire_and_mapped_rise(self):
        s = bld.plan(disc(0, 0, 4), {"class": "church", "height": 20.0, "roof_shape": "onion", "roof_height": 7.0})
        self.assertEqual((s.roof, s.rise, s.height), ("onion", 7.0, 13.0))
        self.assertFalse(s.steeple, "no steeple on a church with a mapped onion dome")
        P, _ = self.check_outward(s, s.height)
        r = np.hypot(P[:, 0], P[:, 1])
        self.assertGreater(r[P[:, 2] > s.height + 0.5].max(), 4.0, "the onion's bulb wider than its drum")
        s = bld.plan(disc(0, 0, 3), {"class": "", "height": 20.0, "roof_shape": "conical"})
        self.assertEqual(s.roof, "cone")
        self.check_outward(s, s.height)

    def test_steep_pyramid_on_a_square_tower_is_a_spire(self):
        # the D&F Tower: a square campanile under a steep pyramid
        s = bld.plan(rect(0, 0, 12, 12), {"class": "", "height": 99.0, "roof_shape": "pyramidal", "roof_height": 18.0})
        self.assertEqual(s.roof, "cone")
        self.assertAlmostEqual(s.height, 81.0)
        P, _ = self.check_outward(s, s.height)
        self.assertAlmostEqual(P[:, 2].max(), 99.0, places=3)
        flat = bld.plan(rect(0, 0, 30, 12), {"class": "", "height": 20.0, "roof_shape": "pyramidal", "roof_height": 3.0})
        self.assertEqual(flat.roof, "hipped", "a shallow pyramidal roof stays hipped")

    def test_a_wide_unmapped_dome_is_an_arena_roof(self):
        s = bld.plan(disc(0, 0, 60), {"class": "", "height": 40.0, "roof_shape": "dome"})
        self.assertLess(s.rise, 15.0)
        self.assertGreater(s.height, 25.0)

    def test_walls_follow_a_round_outline(self):
        s = bld.plan(disc(0, 0, 10), {"class": "civic", "height": 30.0, "roof_shape": "dome"})
        self.assertEqual(len(bld.drawn_ring(s)), len(s.ring), "a domed rotunda's walls are round, not its box")

    def test_barrel_vault(self):
        # the Wells Fargo Center's "cash register" top: roof:shape=round, 65.8 x 33.4 m, roof:height 18
        s = bld.plan(rect(0, 0, 65.8, 33.4), {"class": "", "height": 203.0, "roof_shape": "round", "roof_height": 18.0})
        self.assertEqual((s.roof, s.rise, s.height), ("barrel", 18.0, 185.0))
        P, M = self.check_outward(s, s.height)
        self.assertAlmostEqual(P[:, 2].max(), 203.0, places=3)
        self.assertEqual(bld.plan(np.array([(0, 0), (40, 0), (40, 10), (10, 10), (10, 30), (0, 30)], float),
                                  {"class": "", "height": 20.0, "roof_shape": "round"}).roof, "flat",
                         "no vault on an L-shaped outline")

    def test_a_short_cap_stays_under_its_mapped_top(self):
        # the D&F Tower's gold cap: a 2 m part (98-100 m) with a dome roof; a lantern's walls are plain stone
        s = bld.plan(disc(0, 0, 1.2, 12), {"class": "", "height": 2.0, "roof_shape": "dome"})
        self.assertAlmostEqual(s.height + s.rise, 2.0, places=6)
        self.assertTrue(s.plain)
        self.assertTrue(s.facade.startswith(bld.STONE))

    def test_looks(self):
        self.assertEqual(landmarks.look_of({"roof_color": "gold"}), landmarks.GOLD)
        self.assertEqual(landmarks.look_of({"roof_color": "#D4AF37"}), landmarks.GOLD)
        self.assertEqual(landmarks.look_of({"roof_color": "#5E9C83"}), landmarks.COPPER)
        self.assertIsNone(landmarks.look_of({"roof_color": "#808080"}))
        s = bld.plan(disc(0, 0, 8), {"class": "civic", "height": 30.0, "roof_shape": "dome", "roof_color": "gold"})
        self.assertEqual(s.roof_mat, landmarks.GOLD)
        P, UV, T, M, mats, C = bld.build([(s, 0.0, 0.0)], kit=False)
        self.assertIn(bld.MATERIALS.index(bld.GOLD_LEAF), set(M.tolist()))

    def test_lantern(self):
        s = bld.plan(disc(0, 0, 8), {"class": "civic", "height": 30.0, "roof_shape": "dome", "lantern": [1.5, 6.0]})
        P = bld.build([(s, 0.0, 0.0)], kit=False)[0]
        self.assertGreater(P[:, 2].max(), 30.0 + 4.0)

    def test_the_skyline_draws_the_dome(self):
        import skyline
        s = bld.plan(disc(0, 0, 10), {"class": "civic", "height": 30.0, "roof_shape": "dome"})
        m = bld.Mesh()
        bld._roof(m, s, skyline.simplify_ring(s.ring), s.height, bld._Looks(s.seed))
        self.assertAlmostEqual(np.array(m.P).reshape(-1, 3)[:, 2].max(), 30.0, places=3)


class ShippedTableTest(unittest.TestCase):
    def test_the_table_loads_and_names_real_looks(self):
        t = landmarks.table(landmarks.TABLE)
        self.assertTrue(t["buildings"])
        for e in t["buildings"]:
            for k in ("facade_look", "roof_look"):
                if k in e.get("set", {}):
                    self.assertIn(e["set"][k], bld.LOOKS, e["name"])
        for p in t["pieces"]:
            self.assertIn(p["kind"], ("dome", "spire", "star"))


class PartsTest(TableCase):
    def setUp(self):
        self.f = geodesy.EnuFrame(39.7393, -104.9848)
        self.use_table({"buildings": []})

    def ll(self, ring):
        ring = np.vstack([ring, ring[:1]])
        la, lo, _ = self.f.enu_to_geodetic(ring[:, 0], ring[:, 1], 0.0)
        return np.stack([lo, la], 1)

    def capitol(self):
        b = Feats(["B"], [self.ll(rect(60, 60, 100, 70))], **{"class": ["government"], "height": [83.0],
                                                             "has_parts": [1.0], "src_record": ["w1@3"]})
        parts = Feats(["p1", "p2"], [self.ll(rect(60, 60, 100, 70)), self.ll(disc(110, 95, 12))],
                      building_id=["B", "B"], height=[25.0, 83.0], min_height=[np.nan, 25.0],
                      num_floors=[np.nan, np.nan], min_floor=[np.nan, np.nan], roof_shape=["flat", "dome"],
                      roof_height=[np.nan, 14.0], roof_color=["", "gold"], src_record=["w2@1", "w3@1"])
        return b, parts

    def test_a_part_keeps_its_dome(self):
        b, parts = self.capitol()
        items = bld.tile_buildings(self.f, 0, 0, 256, 256, [b], GROUND, part_layers=[parts])
        roofs = sorted(s.roof for s, _, _ in items)
        self.assertEqual(roofs, ["dome", "flat"])
        dome = next(s for s, _, _ in items if s.roof == "dome")
        self.assertEqual(dome.roof_mat, landmarks.GOLD)
        self.assertAlmostEqual(dome.min_h + dome.height + dome.rise, 83.0, places=5)

    def test_table_by_osm_id_and_by_point(self):
        la, lo, _ = self.f.enu_to_geodetic(110.0, 95.0, 0.0)
        self.use_table({"buildings": [
            {"name": "Capitol", "osm": ["w1"], "set": {"facade_look": "bne_granite"}},
            {"name": "Dome", "at": [float(la), float(lo)], "part": True,
             "set": {"roof_shape": "dome", "roof_height": 16.0, "roof_look": "bne_gold", "lantern": [2, 7]}}]})
        b, parts = self.capitol()
        parts.attrs["roof_shape"] = np.array(["flat", "flat"], object)     # (the map without its dome)
        items = bld.tile_buildings(self.f, 0, 0, 256, 256, [b], GROUND, part_layers=[parts])
        dome = next(s for s, _, _ in items if s.roof == "dome")
        self.assertEqual((dome.rise, dome.roof_mat, dome.lantern), (16.0, "bne_gold", (2, 7)))
        self.assertTrue(all(s.facade == "bne_granite" for s, _, _ in items), "the building's look on its parts")

    def test_capitol_dome_stack_from_osm_parts(self):
        # w34008043 with its parts as OpenStreetMap maps them (2026-10-28): the dome part from the ground to 70 m
        # inside the drums (to 44 and 55 m), the lantern 70-83 m, both gold
        self.use_table({"buildings": [{"osm": ["w1"], "set": {"facade_look": "bne_granite"}},
                                      {"osm": ["w30"], "set": {"roof_height": 13.0}}]})
        b = Feats(["B"], [self.ll(rect(60, 60, 100, 100))], **{"class": ["public"], "height": [23.0],
                                                              "has_parts": [1.0], "src_record": ["w1@3"]})
        parts = Feats(["p1", "p2", "p3", "p4", "p5"],
                      [self.ll(rect(60, 60, 100, 100)), self.ll(disc(110, 110, 14.3)), self.ll(disc(110, 110, 10.9)),
                       self.ll(disc(110, 110, 8.4)), self.ll(disc(110, 110, 2.1, 16))],
                      building_id=["B"] * 5, height=[25.0, 44.0, 55.0, 70.0, 83.0],
                      min_height=[np.nan, np.nan, np.nan, np.nan, 70.0], num_floors=[np.nan] * 5,
                      min_floor=[np.nan] * 5, roof_shape=["hipped", "flat", "flat", "dome", "dome"],
                      roof_height=[np.nan] * 5, roof_color=["", "", "", "#D7AF00", "#D7AF00"],
                      src_record=["w10@1", "w20@1", "w25@1", "w30@1", "w40@1"])
        items = bld.tile_buildings(self.f, 0, 0, 256, 256, [b], GROUND, part_layers=[parts])
        domes = sorted((s.min_h + s.height, s.rise, s.roof_mat) for s, _, _ in items if s.roof == "dome")
        self.assertEqual(len(domes), 2)
        self.assertAlmostEqual(domes[0][0] + domes[0][1], 70.0, places=5)
        self.assertEqual(domes[0][1], 13.0)
        self.assertEqual({d[2] for d in domes}, {landmarks.GOLD})
        P = bld.build(items, kit=False)[0]
        self.assertAlmostEqual(P[:, 2].max(), 83.0, places=2)

    def test_table_drop(self):
        self.use_table({"buildings": [{"osm": ["w9"], "drop": True}]})
        b = Feats(["A", "B"], [self.ll(rect(10, 10, 20, 20)), self.ll(rect(50, 10, 20, 20))],
                  **{"class": ["", ""], "height": [9.0, 9.0], "src_record": ["w8@1", "w9@2"]})
        items = bld.tile_buildings(self.f, 0, 0, 256, 256, [b], GROUND)
        self.assertEqual(len(items), 1)


class StadiumTest(TableCase):
    def setUp(self):
        self.use_table({"buildings": []})

    def oval(self):
        t = np.linspace(0, 2 * math.pi, 40, endpoint=False)
        return np.stack([130 * np.cos(t), 100 * np.sin(t)], 1)

    def test_open_bowl(self):
        s = bld.plan(self.oval(), {"class": "stadium", "height": 45.0})
        self.assertEqual(s.cls, "stadium_bowl")
        P, UV, T, M, mats, C = bld.build([(s, 1600.0, 1602.0)])
        # the field is open: nothing over the centre
        tri = P[T]
        span_x = (tri[:, :, 0].min(1) < 0) & (tri[:, :, 0].max(1) > 0)
        span_y = (tri[:, :, 1].min(1) < 0) & (tri[:, :, 1].max(1) > 0)
        self.assertFalse((span_x & span_y).any(), "no triangle over the field's centre")
        self.assertAlmostEqual(P[:, 2].max(), 1602.0 + 45.0, places=3)
        # the stands face up and in
        n, mid = normals(P, T)
        stands = M == bld.MATERIALS.index(bld.PANEL)
        self.assertTrue(stands.any())
        self.assertTrue((n[stands][:, 2] > 0).all())
        inward = ((mid[stands][:, :2] * n[stands][:, :2]).sum(1) < 0)
        self.assertTrue(inward.all(), "the stands look down to the field")
        self.assertEqual(bld.collider_boxes([(s, 1600.0, 1602.0)], 0, 0), [], "the field is driven on")
        col = bld.collision_mesh([(s, 1600.0, 1602.0)])
        cp, ct = col[0], col[2]
        tri = cp[ct]
        self.assertFalse((((tri[:, :, 0].min(1) < 0) & (tri[:, :, 0].max(1) > 0)) &
                          ((tri[:, :, 1].min(1) < 0) & (tri[:, :, 1].max(1) > 0))).any(), "no lid over the field")

    def test_seats_and_canopy_from_the_table(self):
        s = bld.plan(self.oval(), {"class": "", "height": 45.0, "stadium": True, "canopy": True,
                                   "seat_rgb": (0.9, 0.4, 0.1), "landmark": "Empower Field"})
        self.assertEqual(s.cls, "stadium_bowl")
        P, UV, T, M, mats, C = bld.build([(s, 0.0, 0.0)])
        self.assertGreater(P[:, 2].max(), 45.0 + landmarks.CANOPY_UP_M - 0.01)
        stands = M == bld.MATERIALS.index(bld.PANEL)
        self.assertEqual(tuple(C[T[stands][0, 0]][:3]), tuple(bld._Looks(s.seed)[s.stands][1][:3]))

    def test_small_or_roofed_stadium_stays_a_building(self):
        self.assertNotEqual(bld.plan(rect(0, 0, 60, 60), {"class": "stadium"}).cls, "stadium_bowl")
        self.assertNotEqual(bld.plan(self.oval(), {"class": "stadium", "roof_shape": "dome"}).cls, "stadium_bowl")


class PiecesTest(TableCase):
    def test_star_and_dome(self):
        f = geodesy.EnuFrame(39.3767, -104.8636)
        self.use_table({"buildings": [], "pieces": [
            {"name": "Castle Rock Star", "kind": "star", "at": [39.3767, -104.8636], "size_m": 30, "yaw_deg": 0},
            {"name": "dome", "kind": "dome", "at": [39.3770, -104.8630], "base_m": 25, "r": 9, "drum_h": 8,
             "rise": 11, "look": "bne_gold", "lantern": [1.6, 6]}]})
        mesh = landmarks.pieces_mesh(f, -128, -128, 128, 128, lambda la, lo: np.full(np.shape(la), 1900.0),
                                     bld._Looks(0.5))
        P, UV, T, M, mats, C = mesh
        self.assertIn(bld.MATERIALS.index(bld.GLOW), set(M.tolist()))
        self.assertIn(bld.MATERIALS.index(bld.GOLD_LEAF), set(M.tolist()))
        glow = P[T[M == bld.MATERIALS.index(bld.GLOW)].ravel()]
        self.assertAlmostEqual(np.ptp(glow[:, 2]), 30 * (1 + math.sin(math.radians(54))) / 2, delta=0.5)
        self.assertGreater(glow[:, 2].min(), 1890.0, "over the rock (the frame's up, a little under 1900)")
        self.assertIsNone(landmarks.pieces_mesh(f, 500, 500, 756, 756, lambda la, lo: np.zeros(np.shape(la)),
                                                bld._Looks(0.5)))

    def test_tube_faces_out(self):
        m = bld.Mesh()
        landmarks._tube(m, (0, 0, 0), (10, 3, 2), 0.4, bld._Looks(0.5)["bne_glow"])
        P, UV, T, M, C = m.arrays()
        n, mid = normals(P, T)
        a = np.array([10, 3, 2]) / np.linalg.norm([10, 3, 2])
        off = mid - np.outer(mid @ a, a)
        self.assertTrue(((off * n).sum(1) > 0).all())


class DrcogTest(TableCase):
    def test_landmark_kept_over_the_roofprints(self):
        import drcog
        import mapgeom as mg
        self.use_table({"buildings": [{"name": "Capitol", "osm": ["w5"], "at": [0.5, 0.5]}]})

        def sq(x, y, w):
            return np.array([(x, y), (x + w, y), (x + w, y + w), (x, y + w), (x, y)], np.float64)
        feats = mg.build("buildings", mg.text_column(["a", "b"]), [(mg.POLYGON, [[sq(0, 0, 1)]]),
                                                                   (mg.POLYGON, [[sq(3, 0, 1)]])])
        feats.attrs["src_record"] = mg.text_column(["w5@2", "w6@1"])
        roof = {"off": np.array([0, 5, 10]), "coords": np.concatenate([sq(0.1, 0.1, 0.8), sq(3.1, 0.1, 0.8)]),
                "type": np.array(["Public", "Commercial"]), "height": np.array([30.0, 12.0]),
                "oid": np.array([16, 32])}
        out, n = drcog.merge_buildings(feats, roof)
        self.assertEqual(n, 1, "the roofprint over the landmark left out")
        self.assertEqual(sorted(str(i) for i in out.ids), ["a", "zdrcog-000000000032"])
        self.assertTrue(landmarks.protect_tag((0.0, 0.0, 1.0, 1.0)).startswith("lm"))
        self.assertEqual(landmarks.protect_tag((10.0, 10.0, 11.0, 11.0)), "")


if __name__ == "__main__":
    unittest.main()
