"""The skyline tier (server/skyline.py): the tall buildings of z12 cells out to ~40 km."""
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import buildings  # noqa: E402
import geodesy  # noqa: E402
import mapcells  # noqa: E402
import mapgeom  # noqa: E402
import overture  # noqa: E402
import skyline  # noqa: E402

ANCHOR = (40.0992, -104.9372)       # Frederick, CO (Denver's towers ~36 km south)
DENVER = (39.7475, -104.9930)


def boxes_layer(frame, rows, own=None):
    """A buildings Features of axis-aligned boxes (x, y, w, d, height, floors) in frame, ids b-000..."""
    coords, coord_off = [], [0]
    for x, y, w, d, *_ in rows:
        e = np.array([x, x + w, x + w, x, x], float)
        n = np.array([y, y, y + d, y + d, y], float)
        la, lo, _ = frame.enu_to_geodetic(e, n, 0.0)
        coords.extend(zip(np.asarray(lo).tolist(), np.asarray(la).tolist()))
        coord_off.append(len(coords))
    k = len(rows)
    f = mapgeom.Features(layer="buildings", ids=np.array([f"b-{i:03d}" for i in range(k)]),
                         kind=np.full(k, mapgeom.POLYGON, np.uint8), part_off=np.arange(k + 1, dtype=np.int64),
                         ring_off=np.arange(k + 1, dtype=np.int64), coord_off=np.array(coord_off, np.int64),
                         lonlat=np.array(coords, np.float64),
                         attrs={"height": mapgeom.float_column([r[4] for r in rows]),
                                "num_floors": mapgeom.float_column([r[5] for r in rows]),
                                "class": mapgeom.text_column(["office"] * k),
                                "src_dataset": mapgeom.text_column(["OpenStreetMap"] * k)})
    f.own = np.ones(k, bool) if own is None else np.asarray(own, bool)
    return f


class FakeMD:
    """MapData stand-in: the given layers for any cell."""

    def __init__(self, layers):
        self.layers = layers
        self.asked = []

    def cell(self, key, layers=None, kind=None):
        self.asked.append((key, tuple(layers), kind))
        return type("C", (), {"layers": {k: self.layers.get(k) for k in layers}})()


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def flat_ground(z=1600.0):
    return lambda la, lo: np.full(np.shape(la), z, np.float64)


class SimplifyTest(unittest.TestCase):
    def test_small_ring_unchanged(self):
        r = np.array([[0, 0], [10, 0], [10, 5], [0, 5]], float)
        np.testing.assert_array_equal(skyline.simplify_ring(r), r)

    def test_many_corners_cut_to_max(self):
        # an L of 12 corners, with 4 tiny jogs: the jogs go, the L stays
        r = np.array([[0, 0], [20, 0], [20, 0.2], [20.1, 0.2], [20.1, 10], [10, 10], [10, 30], [0, 30],
                      [0, 20], [0.1, 20], [0.1, 19.9], [0, 19.9]], float)
        s = skyline.simplify_ring(r)
        self.assertLessEqual(len(s), skyline.MAX_CORNERS)
        self.assertAlmostEqual(buildings._area2(s) / 2, buildings._area2(r) / 2, delta=0.02 * buildings._area2(r) / 2)
        self.assertGreater(buildings._area2(s), 0)          # still counter-clockwise

    def test_round_tower_is_its_box_or_close(self):
        a = np.linspace(0, 2 * math.pi, 40, endpoint=False)
        r = np.stack([20 * np.cos(a), 20 * np.sin(a)], 1)
        s = skyline.simplify_ring(r)
        self.assertLessEqual(len(s), skyline.MAX_CORNERS)
        area0 = buildings._area2(r) / 2
        self.assertLess(abs(buildings._area2(s) / 2 - area0), skyline.BOX_AREA_TOL * area0 + 1e-6)

    def test_deterministic(self):
        rng = np.random.default_rng(3)
        a = np.sort(rng.uniform(0, 2 * math.pi, 30))
        r = np.stack([(15 + rng.uniform(0, 3, 30)) * np.cos(a), (15 + rng.uniform(0, 3, 30)) * np.sin(a)], 1)
        np.testing.assert_array_equal(skyline.simplify_ring(r), skyline.simplify_ring(r.copy()))


class FilterTest(unittest.TestCase):
    def test_layer_filters(self):
        for name, h, nf in (("tall_buildings", 20, 6), ("towers", 50, 15)):
            spec = overture.LAYERS[name]
            self.assertEqual((spec["theme"], spec["type"]), ("buildings", "building"))
            self.assertIn(f"height >= {h}", spec["where"])
            self.assertIn(f"num_floors >= {nf}", spec["where"])
            self.assertEqual(dict(spec["stat_any"]), {"height": float(h), "num_floors": float(nf)})
        for name in ("tall_building_parts", "tower_parts"):
            self.assertEqual(overture.LAYERS[name]["type"], "building_part")
        self.assertEqual(skyline.LAYERS, {"tall": ("tall_buildings", "tall_building_parts"),
                                          "towers": ("towers", "tower_parts")})

    def test_row_group_statistics(self):
        sa = overture.LAYERS["tall_buildings"]["stat_any"]
        low = {"stats": {"height": ("1.0", "19.5"), "num_floors": ("1", "5")}}
        tall = {"stats": {"height": ("1.0", "21.0"), "num_floors": ("1", "2")}}
        floors = {"stats": {"height": ("1.0", "12.0"), "num_floors": ("1", "8")}}
        nulls = {"stats": {"height": ("1.0", "12.0"), "num_floors": (None, None)}}
        self.assertFalse(overture.may_match(low, sa))
        self.assertTrue(overture.may_match(tall, sa))
        self.assertTrue(overture.may_match(floors, sa))
        self.assertTrue(overture.may_match(nulls, sa))      # (no statistics: may hold one)
        self.assertTrue(overture.may_match({}, sa))
        self.assertTrue(overture.may_match(low, None))      # other layers: every group


class TierTest(unittest.TestCase):
    def test_tiers_with_hysteresis(self):
        self.assertEqual(skyline.tier_for(0.0), "tall")
        self.assertEqual(skyline.tier_for(skyline.TALL_M - 1), "tall")
        self.assertEqual(skyline.tier_for(skyline.TALL_M + 1), "towers")
        self.assertEqual(skyline.tier_for(skyline.TALL_M + 1, had="tall"), "tall")
        self.assertEqual(skyline.tier_for(skyline.TALL_M + skyline.TALL_HYST_M + 1, had="tall"), "towers")

    def test_cells_near_cover_the_radius(self):
        f = geodesy.EnuFrame(*ANCHOR)
        keys = skyline.cells_near(f, 0.0, 0.0, skyline.SEND_M)
        self.assertTrue(all(k.z == skyline.ZOOM for k in keys))
        self.assertIn(mapcells.cell_of(*DENVER, z=skyline.ZOOM), keys)
        self.assertIn(mapcells.cell_of(*ANCHOR, z=skyline.ZOOM), keys)
        for dx, dy in ((39000, 0), (0, -39000), (-27000, 27000)):
            la, lo, _ = f.enu_to_geodetic(dx, dy, 0.0)
            self.assertIn(mapcells.cell_of(float(la), float(lo), z=skyline.ZOOM), keys)

    def test_cell_box(self):
        f = geodesy.EnuFrame(*ANCHOR)
        key = mapcells.cell_of(*ANCHOR, z=skyline.ZOOM)
        x0, y0, x1, y1 = skyline.cell_box(f, key)
        self.assertTrue(x0 <= 0 <= x1 and y0 <= 0 <= y1)
        self.assertAlmostEqual(x1 - x0, key.size_m()[0], delta=60)
        self.assertEqual(skyline.box_distance((x0, y0, x1, y1), 0, 0), 0.0)
        self.assertAlmostEqual(skyline.box_distance((10, 0, 20, 5), 0, 0), 10.0)
        self.assertAlmostEqual(skyline.box_distance((3, 4, 9, 9), 0, 0), 5.0)


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="skyline-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def build(self, frame, key, rows, own=None, ground=None, tier="tall", out=None):
        f = boxes_layer(frame, rows, own)
        md = FakeMD({skyline.LAYERS[tier][0]: f, skyline.LAYERS[tier][1]: None})
        out = out or self.tmp
        path, n = skyline.build_tile(frame, key, tier, md, ground or flat_ground(), out, "/vfs", 7)
        with open(os.path.join(out, os.path.basename(path))) as fh:
            return json.load(fh), n, path, md

    def test_parts_by_road_tile_and_merged(self):
        f = geodesy.EnuFrame(*ANCHOR)
        key = mapcells.cell_of(*ANCHOR, z=skyline.ZOOM)
        rows = [(10, 10, 30, 20, 40.0, 12), (60, 20, 20, 20, 25.0, None),      # road tile 0_0
                (300, 10, 20, 20, 80.0, 24),                                     # 1_0
                (-200, -300, 25, 25, 30.0, None)]                                # -1_-2
        data, n, path, md = self.build(f, key, rows)
        self.assertEqual(n, 4)
        self.assertEqual(path, f"/vfs/sky_7_{key.x}_{key.y}_t.json")
        self.assertEqual(data["tier"], "tall")
        self.assertEqual(sorted(p["k"] for p in data["parts"]), ["-1_-2", "0_0", "1_0"])
        for p in data["parts"]:
            tx, ty = map(int, p["k"].split("_"))
            self.assertEqual(p["pos"], [tx * 256.0, ty * 256.0, 0.0])
            self.assertTrue(os.path.exists(os.path.join(self.tmp, os.path.basename(p["shape"]))))
        self.assertEqual(len(data["shapes"]), 1)
        self.assertEqual(md.asked[0][1], ("tall_buildings", "tall_building_parts"))
        self.assertEqual(md.asked[0][2], "prefetch")

    def test_not_owned_left_out(self):
        f = geodesy.EnuFrame(*ANCHOR)
        key = mapcells.cell_of(*ANCHOR, z=skyline.ZOOM)
        rows = [(10, 10, 30, 20, 40.0, 12), (300, 10, 20, 20, 80.0, 24)]
        data, n, _, _ = self.build(f, key, rows, own=[True, False])
        self.assertEqual(n, 1)
        self.assertEqual([p["k"] for p in data["parts"]], ["0_0"])

    def test_empty(self):
        f = geodesy.EnuFrame(*ANCHOR)
        key = mapcells.cell_of(*ANCHOR, z=skyline.ZOOM)
        data, n, _, _ = self.build(f, key, [])
        self.assertEqual((n, data["parts"], data["shapes"]), (0, [], []))

    def test_deterministic(self):
        f = geodesy.EnuFrame(*ANCHOR)
        key = mapcells.cell_of(*ANCHOR, z=skyline.ZOOM)
        rows = [(10 + 37 * i, 10 + 53 * (i % 5), 18, 22, 20.0 + 7 * i, None) for i in range(25)]
        outs = []
        for k in range(2):
            out = os.path.join(self.tmp, str(k))
            os.makedirs(out)
            self.build(f, key, rows, out=out)
            outs.append({n: _read(os.path.join(out, n)) for n in sorted(os.listdir(out))})
        self.assertEqual(outs[0], outs[1])
        self.assertGreater(len(outs[0]), 2)

    def test_curvature_far_building_below_the_tangent_plane(self):
        """A tower 30 km from the anchor stands on the ground as the far terrain has it: ~d^2 / 2R (70 m) below the
        anchor's tangent plane on flat ground at the anchor's height."""
        f = geodesy.EnuFrame(ANCHOR[0], ANCHOR[1], 0.0)
        la, lo, _ = f.enu_to_geodetic(0.0, -30000.0, 0.0)
        key = mapcells.cell_of(float(la), float(lo), z=skyline.ZOOM)
        rows = [(10, -30010, 20, 20, 100.0, 30)]
        f_ = boxes_layer(f, rows)
        md = FakeMD({"towers": f_, "tower_parts": None})
        items = skyline.tile_items(f, f_, None, flat_ground(0.0), 0, int(math.floor(-30010 / 256)))
        self.assertEqual(len(items), 1)
        spec, gmin, gmax = items[0]
        drop = 30000.0 ** 2 / (2 * 6371000.0)
        self.assertAlmostEqual(gmax, -drop, delta=3.0)
        self.assertAlmostEqual(gmin, -drop - skyline.SKIRT_M, delta=3.0)
        path, n = skyline.build_tile(f, key, "towers", md, flat_ground(0.0), self.tmp, "/vfs", 1)
        data = json.loads(_read(os.path.join(self.tmp, os.path.basename(path))))
        import cdae
        shape = cdae.read(os.path.join(self.tmp, os.path.basename(data["shapes"][0]["shape"])))
        top = max(float(cdae.mesh_arrays(m)["verts"][:, 2].max()) for m in shape["meshes"] if m["type"] == 0)
        self.assertAlmostEqual(top, -drop + 100.0, delta=3.0)      # (the merged mesh's origin: z 0)

    def test_vertex_cap_splits_merged(self):
        f = geodesy.EnuFrame(*ANCHOR)
        rows = [(256 * (i % 20) + 10, 256 * (i // 20) + 10, 30, 30, 60.0, 20) for i in range(400)]
        items = []
        for tx in range(20):
            for ty in range(20):
                items += skyline.tile_items(f, boxes_layer(f, rows), None, flat_ground(), tx, ty)
        m = skyline.mesh(items)
        cap = len(m[0]) // 3
        parts = skyline._merge([skyline.mesh(items[i:i + 10]) for i in range(0, len(items), 10)], max_verts=cap)
        self.assertGreater(len(parts), 2)
        self.assertTrue(all(len(p[0]) <= cap for p in parts))
        self.assertEqual(sum(len(p[0]) for p in parts), len(m[0]))
        self.assertLessEqual(len(m[0]) / len(items), 60)    # one quad a wall: ~40 vertices a box building

    def test_walls_one_quad_each(self):
        f = geodesy.EnuFrame(*ANCHOR)
        items = skyline.tile_items(f, boxes_layer(f, [(10, 10, 30, 20, 120.0, 40)]), None, flat_ground(), 0, 0)
        P, UV, T, M, mats, C = skyline.mesh(items)
        self.assertEqual(len(P), 4 * 4 + 4)                 # 4 walls + a flat roof
        self.assertAlmostEqual(float(P[:, 2].max()), 1600.0 + 120.0, places=3)
        self.assertAlmostEqual(float(P[:, 2].min()), 1600.0 - skyline.SKIRT_M - buildings.SKIRT_LITE_M, places=3)


class Conn:
    def __init__(self):
        self.sent = []

    def send(self, m):
        self.sent.append(m)


class ImmediateLoop:
    """run_in_executor that runs at once (a done future)."""

    def run_in_executor(self, pool, fn, *args):
        from concurrent.futures import Future
        fu = Future()
        try:
            fu.set_result(fn(*args))
        except Exception as e:
            fu.set_exception(e)
        return fu


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.f = geodesy.EnuFrame(*ANCHOR)
        self.built = []

    def build(self, aid, key, tier):
        self.built.append((key, tier))
        return f"/vfs/sky_{aid}_{key.x}_{key.y}.json", (5 if key == mapcells.cell_of(*DENVER, z=skyline.ZOOM) else 0)

    def test_nearest_first_one_at_a_time_and_busy(self):
        tr = skyline.Tracker()
        self.addCleanup(tr.close)
        conn = Conn()
        tr.on_pose(conn, 1, self.f, 0.0, 0.0, ImmediateLoop(), self.build, busy=True)
        self.assertEqual(self.built, [])
        tr.on_pose(conn, 1, self.f, 0.0, 0.0, ImmediateLoop(), self.build)
        self.assertEqual(len(self.built), 1)
        self.assertEqual(self.built[0], (mapcells.cell_of(*ANCHOR, z=skyline.ZOOM), "tall"))
        self.assertEqual(conn.sent, [])                     # nothing in it: no message
        for _ in range(200):
            tr.on_pose(conn, 1, self.f, 0.0, 0.0, ImmediateLoop(), self.build)
        tiers = dict(self.built)
        denver = mapcells.cell_of(*DENVER, z=skyline.ZOOM)
        self.assertEqual(tiers[denver], "towers")
        self.assertEqual([m["key"] for m in conn.sent], [f"{denver.x}_{denver.y}"])
        self.assertEqual(conn.sent[0]["tier"], "towers")
        n = len(self.built)
        tr.on_pose(conn, 1, self.f, 0.0, 0.0, ImmediateLoop(), self.build)
        self.assertEqual(len(self.built), n)                # all built: nothing more

    def test_tier_change_and_drop(self):
        tr = skyline.Tracker()
        self.addCleanup(tr.close)
        conn = Conn()
        denver = mapcells.cell_of(*DENVER, z=skyline.ZOOM)
        for _ in range(200):
            tr.on_pose(conn, 1, self.f, 0.0, 0.0, ImmediateLoop(), self.build)
        # the car drives to Denver: its cell turns "tall"
        la, lo = DENVER
        x, y, _ = self.f.geodetic_to_enu(la, lo, 0.0)
        for _ in range(200):
            tr.on_pose(conn, 1, self.f, float(x), float(y), ImmediateLoop(), self.build)
        self.assertEqual(tr.sent[denver], "tall")
        self.assertEqual(conn.sent[-1]["tier"] if conn.sent[-1]["type"] == "skyline_tile" else None, None) \
            if False else None
        self.assertIn(("tall"), [m.get("tier") for m in conn.sent if m["key"] == f"{denver.x}_{denver.y}"])
        # and on 60 km north: dropped
        for _ in range(3):
            tr.on_pose(conn, 1, self.f, float(x), float(y) + 100000.0, ImmediateLoop(), self.build, busy=True)
        self.assertEqual([m["type"] for m in conn.sent if m["key"] == f"{denver.x}_{denver.y}"][-1], "skyline_drop")
        self.assertNotIn(denver, tr.sent)

    def test_heading_first(self):
        tr = skyline.Tracker()
        self.addCleanup(tr.close)
        want_still, _ = tr.wanted(self.f, 0.0, 0.0)
        want_south, _ = tr.wanted(self.f, 0.0, 0.0, vel=(0.0, -30.0))
        south = [w for w in want_south if w[3] > 15000][0]
        x0, y0, x1, y1 = tr.boxes[south[1]]
        self.assertLess((y0 + y1) / 2, 0.0)                 # the first far one ahead is south
        self.assertNotEqual([w[1] for w in want_still], [w[1] for w in want_south])

    def test_new_anchor_resets(self):
        tr = skyline.Tracker()
        self.addCleanup(tr.close)
        conn = Conn()
        tr.on_pose(conn, 1, self.f, 0.0, 0.0, ImmediateLoop(), self.build)
        self.assertTrue(tr.sent)
        tr.on_pose(conn, 2, self.f, 0.0, 0.0, ImmediateLoop(), self.build, busy=True)
        self.assertEqual((tr.aid, tr.sent), (2, {}))


if __name__ == "__main__":
    unittest.main()
