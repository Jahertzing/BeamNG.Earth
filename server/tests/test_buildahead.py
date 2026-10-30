"""buildahead.py: the build-ahead cursor (the near tier built live ahead of the car along the route or heading): the
line's sampling, the mod's anchor rules along it (distance rule, pinned-anchor rule, planned anchors), the road tiles
and terrain blocks predicted, the cursor's horizon, ready front, rate and job limits; and earthd's wiring: the route
message's own points, route_anchors to the mod, the pose tick starting jobs, load_progress "ahead"."""
import asyncio
import math
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import buildahead as ba  # noqa: E402
import geodesy  # noqa: E402
import roads as roadsmod  # noqa: E402

A = (39.90, -104.99)            # Denver's north side: a straight line of route north from here
M_LAT = 1 / 111195.0            # degrees of latitude per metre


def north(start, km, step_m=250.0):
    """A route due north from start, km long, a point every step_m."""
    n = int(km * 1000 / step_m)
    return [[start[0] + i * step_m * M_LAT, start[1]] for i in range(n + 1)]


class LineTest(unittest.TestCase):
    def test_resample_walks_the_line_every_step(self):
        la, lo, s = ba.resample(north(A, 2.0), 128.0)
        self.assertAlmostEqual(s[1] - s[0], 128.0)
        self.assertAlmostEqual(s[-1], 2000.0, delta=2.0)
        self.assertEqual(len(s), 16 + 1, "15 full steps, then the end")
        self.assertAlmostEqual(float(la[-1]), A[0] + 2000.0 * M_LAT, places=5)
        la2, lo2, s2 = ba.resample(north(A, 20.0), 128.0, max_m=3000.0)
        self.assertAlmostEqual(s2[-1], 3000.0, delta=1.0)
        self.assertEqual(len(ba.resample([[1.0, 2.0]], 128.0)[2]), 1)

    def test_snap_anchor_is_the_mods_grid(self):
        self.assertEqual(ba.snap_anchor(39.9049, -104.9951), (39.9, -105.0))
        self.assertEqual(ba.snap_anchor(39.9051, -104.9949), (39.91, -104.99))

    def test_heading_line_runs_from_the_car(self):
        fr = geodesy.EnuFrame(*A)
        pts = ba.heading_line(fr, 100.0, 200.0, 0.0, 1.0, 1000.0, 250.0)
        self.assertEqual(len(pts), 5)
        e, n, _ = fr.geodetic_to_enu(pts[-1][0], pts[-1][1], 0.0)
        self.assertAlmostEqual(float(e), 100.0, delta=0.5)
        self.assertAlmostEqual(float(n), 1200.0, delta=0.5)


class FramesTest(unittest.TestCase):
    def test_distance_rule_lands_on_the_grid_at_the_rebase_distance(self):
        la, lo, s = ba.resample(north(A, 32.0), 250.0)
        anchors, idx, planned = ba.frames(la, lo, A, 15000.0, pins=(), pinned_rule=False)
        self.assertEqual(anchors[0], A)
        first = int(np.argmax(idx != 0))
        self.assertAlmostEqual(s[first], 14700.0, delta=250.0, msg="a new anchor at distance - PREFETCH_MARGIN")
        self.assertEqual(anchors[1], ba.snap_anchor(float(la[first]), float(lo[first])))
        self.assertEqual(planned, anchors[1:], "the anchors landed on are planned for the mod")
        self.assertEqual(len(anchors), 3, "two re-centres in 32 km")
        self.assertTrue(all(idx[i] <= idx[i + 1] for i in range(len(idx) - 1)), "frames only move forward")

    def test_pinned_rule_moves_to_the_planned_anchor_as_soon_as_it_is_nearer(self):
        la, lo, s = ba.resample(north(A, 32.0), 250.0)
        anchors, idx, planned = ba.frames(la, lo, A, 15000.0, pins=(), pinned_rule=True)
        anchors0, idx0, planned0 = ba.frames(la, lo, A, 15000.0, pins=(), pinned_rule=False)
        self.assertEqual(planned, planned0, "the same anchors (the distance rule placed them) ...")
        first = int(np.argmax(idx != 0))
        d_next = ba.metres(anchors[1], (float(la[first]), float(lo[first])))
        d_cur = ba.metres(A, (float(la[first]), float(lo[first])))
        self.assertLessEqual(d_next, ba.pin_m(15000.0), "... entered once the next one is within the pin distance")
        self.assertGreaterEqual(d_cur - d_next, ba.PIN_HYST_M)
        self.assertLess(s[first], 9000.0, "about 7.95 km out, not 14.7")

    def test_a_known_pin_is_landed_on_and_never_planned_again(self):
        la, lo, s = ba.resample(north(A, 20.0), 250.0)
        pin = (A[0] + 14000.0 * M_LAT + 0.004, A[1] + 0.006)     # a bake's anchor near where the car would re-centre
        anchors, idx, planned = ba.frames(la, lo, A, 15000.0, pins=[pin], pinned_rule=True)
        self.assertIn((round(pin[0], 7), round(pin[1], 7)), anchors)
        self.assertEqual(planned, [], "known to the mod already")

    def test_empty_and_short_lines(self):
        anchors, idx, planned = ba.frames([], [], A)
        self.assertEqual((anchors, len(idx), planned), ([A], 0, []))
        la, lo, s = ba.resample(north(A, 3.0), 250.0)
        anchors, idx, planned = ba.frames(la, lo, A)
        self.assertEqual(len(anchors), 1)
        self.assertTrue(np.all(idx == 0))


class ItemsTest(unittest.TestCase):
    def test_road_tiles_are_the_trackers_within_send_m(self):
        xs = np.array([0.0, 128.0, 256.0])
        ys = np.zeros(3)
        items = ba.road_items(A, xs, ys, np.array([0.0, 128.0, 256.0]))
        keys = {it.key for it in items}
        self.assertEqual(keys, set(roadsmod.tiles_near(0.0, 0.0, roadsmod.SEND_M)) |
                         set(roadsmod.tiles_near(256.0, 0.0, roadsmod.SEND_M)))
        by = {it.key: it.due for it in items}
        self.assertEqual(by[(0, 0)], 0.0)
        far_east = max(k[0] for k in keys)
        self.assertGreater(by[(far_east, 0)], 0.0, "a tile only the later samples reach is due later")
        self.assertTrue(all(it.kind == "roads" and it.anchor == A for it in items))

    def test_blocks_follow_the_rule_along_the_line(self):
        grid, prepare = 256.0, 300.0

        def follow(px, py, vx, vy, cx, cy):          # a simple follow: the 256 m grid point ahead once out of the box
            if cx is not None and max(abs(px - cx), abs(py - cy)) <= prepare:
                return cx, cy
            return (round((px + vx * 4.0) / grid) * grid, round((py + vy * 4.0) / grid) * grid)
        s = np.arange(0.0, 3000.0 + 1, 128.0)
        xs, ys = np.zeros(len(s)), s.copy()
        items = ba.block_items(A, xs, ys, s, 30.0, follow, start=(0.0, 0.0))
        self.assertTrue(all(it.kind == "block" for it in items))
        self.assertEqual([it.key for it in items], sorted({it.key for it in items}, key=lambda k: k[1]),
                         "each block once, in order")
        self.assertNotIn((0.0, 0.0), [it.key for it in items], "the block the car has is not an item")
        self.assertGreaterEqual(len(items), 4, "a block every ~600 m of straight driving")
        self.assertTrue(all(items[i].due < items[i + 1].due for i in range(len(items) - 1)))
        self.assertEqual(ba.block_items(A, xs[:1], ys[:1], s[:1], 30.0, follow), [])


def follow_simple(px, py, vx, vy, cx, cy):
    if cx is not None and max(abs(px - cx), abs(py - cy)) <= 300.0:
        return cx, cy
    return (round((px + vx * 4.0) / 256.0) * 256.0, round((py + vy * 4.0) / 256.0) * 256.0)


class CursorTest(unittest.TestCase):
    def cursor(self, route_km=12.0, pinned=True):
        c = ba.Cursor(follow_simple, road_jobs=2, block_jobs=1, plan_jobs=0)     # (cell plans: PlansAheadTest)
        c.set_route(north(A, route_km), pinned_rule=pinned, rebase_m=15000.0)
        fr = geodesy.EnuFrame(*A)
        return c, fr

    def test_plan_from_the_car_on_the_route(self):
        c, fr = self.cursor()
        items = c.plan(A, fr, 0.0, 0.0, 0.0, 30.0, block=(0.0, 0.0))
        self.assertTrue(items)
        self.assertEqual(items, sorted(items, key=lambda it: (it.due, it.kind != "block")))
        kinds = {it.kind for it in items}
        self.assertEqual(kinds, {"roads", "block"})
        self.assertEqual(len({it.id for it in items}), len(items), "no item twice")
        self.assertFalse(c.off_route)
        self.assertEqual(c.anchors, [A], "12 km: all in the car's frame")
        self.assertEqual(c.planned, [])
        # 3 km up the route: planned from there (the first items are the ones due around 3 km)
        c.plan(A, fr, 0.0, 3000.0, 0.0, 30.0)
        self.assertTrue(3000.0 - 2 * ba.STEP_M <= c.s_car <= 3000.0, c.s_car)   # (the sample before the car's)
        self.assertLess(abs(c.items[0].due - 3000.0), 800.0)

    def test_off_the_route_the_heading_is_the_line(self):
        c, fr = self.cursor()
        c.plan(A, fr, 2000.0, 0.0, 30.0, 0.0)           # 2 km east of the route, heading east
        self.assertTrue(c.off_route)
        self.assertTrue(c.items)
        self.assertLessEqual(max(it.due for it in c.items), ba.HEADING_MAX_M + roadsmod.SEND_M)
        self.assertFalse(c.stale(A, fr, 2100.0, 0.0, 30.0, 0.0))
        self.assertTrue(c.stale(A, fr, 2600.0, 0.0, 30.0, 0.0), "moved HEADING_MOVE_M")
        self.assertTrue(c.stale(A, fr, 2100.0, 0.0, 0.0, 30.0), "turned north")
        self.assertTrue(c.stale(A, fr, 10.0, 500.0, 0.0, 30.0), "back on the route")

    def test_a_long_route_plans_the_anchors_for_the_mod_and_builds_in_their_frames(self):
        c, fr = self.cursor(route_km=30.0)
        c.plan(A, fr, 0.0, 0.0, 0.0, 30.0)
        self.assertEqual(len(c.planned), 1, "one re-centre within MAX_M + REPLAN_M")
        self.assertEqual(c.anchors, [A] + c.planned)
        frames = {it.anchor for it in c.items}
        self.assertEqual(frames, set(c.anchors), "items in both frames")
        far = [it for it in c.items if it.anchor == c.planned[0]]
        self.assertTrue(all(it.due >= 7500.0 for it in far), "the next frame's items come after the pinned switch")
        # a tile in the next frame is keyed in that frame
        it = next(it for it in far if it.kind == "roads")
        fr2 = geodesy.EnuFrame(*it.anchor)
        la, lo, _ = fr.enu_to_geodetic(0.0, it.due, 0.0)
        e, n, _ = fr2.geodetic_to_enu(la, lo, 0.0)
        self.assertLess(roadsmod.tile_distance(it.key[0], it.key[1], float(e), float(n)), roadsmod.SEND_M + 1)

    def test_stale_along_the_route(self):
        c, fr = self.cursor(route_km=30.0)
        self.assertTrue(c.stale(A, fr, 0.0, 0.0, 0.0, 30.0), "no plan yet")
        c.plan(A, fr, 0.0, 0.0, 0.0, 30.0)
        self.assertFalse(c.stale(A, fr, 0.0, 1000.0, 0.0, 30.0))
        self.assertTrue(c.stale(A, fr, 0.0, ba.REPLAN_M + 200.0, 0.0, 30.0), "REPLAN_M further")
        self.assertTrue(c.stale(c.planned[0], fr, 0.0, 1000.0, 0.0, 30.0), "another anchor")
        self.assertAlmostEqual(c.car_s(fr, 0.0, 2000.0), 2000.0, delta=130.0)

    def test_tick_starts_the_items_within_the_horizon_by_the_jobs(self):
        c, fr = self.cursor()
        c.plan(A, fr, 0.0, 0.0, 0.0, 30.0, block=(0.0, 0.0))
        h = c.horizon(30.0)
        self.assertEqual(h, max(ba.MIN_M, 30.0 * (ba.LATENCY_K * ba.LATENCY_S0 + ba.MARGIN_S)))
        self.assertGreater(c.horizon(90.0), h, "faster: farther")
        self.assertLessEqual(c.horizon(300.0), c.max_m)
        start = c.tick(10.0, 0.0, 30.0)
        self.assertEqual(sorted(it.kind for it in start), ["block", "roads", "roads"], "the kinds' jobs")
        self.assertTrue(all(it.due <= h for it in start))
        self.assertEqual(start[0], c.items[0], "nearest first")
        for it in start:
            c.started(it, 10.0)
        self.assertEqual(c.tick(10.5, 0.0, 30.0), [], "all jobs busy")
        c.done_item(start[1], 12.0, "built")
        nxt = c.tick(12.0, 0.0, 30.0)
        self.assertEqual(len(nxt), 1)
        self.assertEqual(nxt[0].kind, "roads")
        self.assertNotIn(nxt[0].id, {it.id for it in start})
        self.assertAlmostEqual(c.stats.latency, ba.LATENCY_S0 + ba.LATENCY_ALPHA * (2.0 - ba.LATENCY_S0))
        self.assertEqual(c.stats.built, 1)

    def test_the_front_and_the_rate(self):
        c, fr = self.cursor()
        c.plan(A, fr, 0.0, 0.0, 0.0, 30.0, block=(0.0, 0.0))
        self.assertEqual(c.front(), c.items[0].due)
        self.assertEqual(c.ready_m(), c.items[0].due)
        now = 0.0
        c.tick(now, 0.0, 30.0)
        # everything due within 2 km done over 40 s of ticks: the front moves to the first item past it
        for it in c.items:
            if it.due <= 2000.0:
                c.done_item(it, now, "cached")
        now = 40.0
        c.tick(now, 0.0, 30.0)
        first_left = next(it for it in c.items if it.due > 2000.0)
        self.assertEqual(c.front(), first_left.due)
        self.assertGreaterEqual(c.stats.busy_s, 40.0 - 1e-9)
        self.assertAlmostEqual(c.stats.advance_m, first_left.due - c.items[0].due)
        self.assertIsNotNone(c.stats.rate)
        self.assertAlmostEqual(c.stats.rate, c.stats.advance_m / c.stats.busy_s)
        self.assertIn("keeps up to", c.status())
        # all done: the front is past the plan's end, nothing pending: not busy
        for it in c.items:
            c.done_item(it, now, "cached")
        c.tick(41.0, 0.0, 30.0)
        busy = c.stats.busy_s
        c.tick(50.0, 0.0, 30.0)
        self.assertEqual(c.stats.busy_s, busy, "nothing queued: not busy")
        self.assertGreater(c.ready_m(), c.items[-1].due - c.s_car)

    def test_skip_marks_the_live_paths_items_done(self):
        c, fr = self.cursor()
        c.plan(A, fr, 0.0, 0.0, 0.0, 30.0, block=(0.0, 0.0))
        first = c.items[0]
        start = c.tick(0.0, 0.0, 30.0, skip=lambda it: it is first)
        self.assertNotIn(first, start)
        self.assertIn(first.id, c.done)
        self.assertEqual(c.stats.cached, 1)

    def test_set_route_none_means_the_heading(self):
        c, fr = self.cursor()
        c.set_route(None)
        self.assertIsNone(c.route)
        c.plan(A, fr, 0.0, 0.0, 0.0, 30.0)
        self.assertTrue(c.items)
        self.assertEqual(c.rebase_m, 15000.0, "kept")
        c.set_route(north(A, 5.0), rebase_m=50000.0)
        self.assertEqual(c.rebase_m, 16000.0, "the mod's MAX_DISTANCE")


class ServerTest(unittest.TestCase):
    """earthd's wiring, with a Server over stand-ins (no data): the route message's own points, the cursor planned at
    the pose, jobs started through build_ahead_block / build_ahead_roads, route_anchors sent, load_progress ahead."""

    def server(self):
        import earthd
        import surface as sf
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sur = sf.Surface(None)
        self.addCleanup(sur.close)
        md = types.SimpleNamespace(has=lambda key, layer: False, versions=lambda: "v")
        s = earthd.Server(tmp.name, os.path.join(tmp.name, "cache"), imagery=None, cells=64, block=512, surface=sur,
                          mapdata=md)
        self.addCleanup(s.hm_pool.shutdown)
        self.addCleanup(s.tile_pool.shutdown)
        self.addCleanup(s.ahead_pool.shutdown)
        s.dem = None                    # (no elevation profile or corridor download for the route here)
        return s, earthd

    def test_route_points_plan_the_cursor_and_the_pose_starts_jobs(self):
        from test_gasstations import _Writer
        s, earthd = self.server()
        built = []

        def block(anchor, cx, cy):
            built.append(("block", (round(anchor.lat, 7), round(anchor.lon, 7)), (cx, cy), anchor.id))
            return "built"

        def roads(anchor, tx, ty):
            built.append(("roads", (round(anchor.lat, 7), round(anchor.lon, 7)), (tx, ty), anchor.id))
            return "built"
        s.build_ahead_block, s.build_ahead_roads = block, roads
        a = s.new_anchor(*A)
        s.anchors[a.id] = a

        async def go():
            with mock.patch.object(earthd.revisitmod, "ON", True):
                w = _Writer()
                c = earthd.Connection(s, None, w)
                self.assertIsNotNone(c.ahead, "the cursor: mapdata and the revisit cache are on")
                c.ahead.jobs["roads"] = 2                    # (no road processes here: roads would be off)
                c.active = a.id
                c.focus, c.vel = (0.0, 0.0), (0.0, 30.0)
                c.block = earthd.farfield.Block(0.0, 0.0, 512)
                await c.route({"id": 7, "points": north(A, 30.0), "pins": True, "rebaseM": 15000})
                res = next(m for m in w.msgs if m["type"] == "route_result")
                self.assertEqual(res["id"], 7)
                self.assertEqual(len(res["points"]), len(north(A, 30.0)))
                self.assertAlmostEqual(res["dist"], 30000.0, delta=30.0)
                self.assertTrue(c.ahead.items, "planned at once: the anchor is known")
                pins = [m for m in w.msgs if m["type"] == "route_anchors"]
                self.assertEqual(len(pins), 1, "the anchor planned 14.7 km up the route, for the mod")
                self.assertEqual(pins[0]["anchors"], [list(p) for p in c.ahead.planned])
                self.assertEqual(pins[0]["pinM"], 6750)
                # the pose: the tick starts the first items (1 block + 2 road tiles), in phantom anchors
                c.ahead_tick(a.id)
                await asyncio.sleep(0.2)
                self.assertEqual(sorted(b[0] for b in built), ["block", "roads", "roads"])
                self.assertTrue(all(b[1] == A for b in built), "the car's own frame, as a phantom")
                self.assertTrue(all(b[3] >= earthd.PHANTOM_BASE for b in built), "never the mod's anchor id")
                self.assertEqual(c.ahead.stats.built, 3)
                self.assertEqual(c.ahead.active, {})
                # another tick (TICK_S later) starts more; the live path's tiles are skipped
                c.roads.aid = a.id
                nxt = [it for it in c.ahead.items if it.id not in c.ahead.done][0]
                if nxt.kind == "roads":
                    c.roads.sent.add(nxt.key)
                c.ahead.last_tick -= 1.0
                c.ahead_tick(a.id)
                await asyncio.sleep(0.2)
                if nxt.kind == "roads":
                    self.assertIn(nxt.id, c.ahead.done)
                    self.assertNotIn(nxt.key, [b[2] for b in built])
                # load_progress carries what is ready ahead
                c.send_progress(a.id)
                prog = [m for m in w.msgs if m["type"] == "load_progress"][-1]
                self.assertEqual(len(prog["ahead"]), 2)
                self.assertGreaterEqual(prog["ahead"][1], ba.MIN_M)
                return w.msgs
        asyncio.run(go())

    def test_phantom_anchors_and_the_mods_find_each_other(self):
        s, earthd = self.server()
        p = s.phantom_anchor(*A)
        self.assertGreaterEqual(p.id, earthd.PHANTOM_BASE)
        self.assertIs(s.phantom_anchor(A[0], A[1]), p, "one per place")
        self.assertIs(s.anchor_by_id(p.id), p)
        self.assertIsNone(s.anchor_by_id(p.id + 1))
        p.q = 1 / 32
        a = s.new_anchor(*A)
        self.assertEqual(a.q, 1 / 32, "the mod's anchor there takes the phantom's survey")
        self.assertLess(a.id, earthd.PHANTOM_BASE)
        for i in range(6):
            s.phantom_anchor(A[0] + 0.1 * (i + 1), A[1])
        self.assertLessEqual(len(s.phantoms), 4, "only the last few kept")

    def connection(self, s, earthd, on=True):
        from test_gasstations import _Writer

        async def make():
            with mock.patch.object(earthd.revisitmod, "ON", on):
                return earthd.Connection(s, None, _Writer())
        return asyncio.run(make())

    def test_no_cursor_without_the_revisit_cache_or_map_data(self):
        s, earthd = self.server()
        self.assertIsNone(self.connection(s, earthd, on=False).ahead)
        with mock.patch.object(earthd, "BUILD_AHEAD_ON", False):
            self.assertIsNone(self.connection(s, earthd).ahead)
        c = self.connection(s, earthd)
        self.assertIsNotNone(c.ahead)
        self.assertEqual(c.ahead.jobs["roads"], 0, "no road processes: tiles are not cached, so none ahead")

    def test_follow_rule_keeps_the_block_inside_the_prepare_box(self):
        s, earthd = self.server()
        c = self.connection(s, earthd)
        rule = s.follow
        self.assertEqual(c._ahead_follow(10.0, 10.0, 0.0, 30.0, 0.0, 0.0), (0.0, 0.0), "inside: the same block")
        nxt = c._ahead_follow(0.0, rule.prepare + 50.0, 0.0, 30.0, 0.0, 0.0)
        self.assertNotEqual(nxt, (0.0, 0.0))
        self.assertEqual(nxt[0] % rule.grid, 0.0)
        self.assertGreater(nxt[1], 0.0, "ahead of the car")
        first = c._ahead_follow(0.0, 0.0, 0.0, 30.0, None, None)
        self.assertEqual(len(first), 2)


class ToolTest(unittest.TestCase):
    def test_headless_options(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
        import headless_bake as hb
        with tempfile.TemporaryDirectory() as user:
            cmd = hb.server_command(user, 0, "/c", win=True, ahead=False)
            self.assertEqual(cmd[cmd.index("--build-ahead") + 1], "off")
            self.assertNotIn("--build-ahead", hb.server_command(user, 0, "/c", win=True))
        a = types.SimpleNamespace(speed=30, roads_q=1, backlog_q=1, edge_m=1, max_wait=1, ground_wait=1, ahead="off",
                                  cache_dir=None, world_dir=None, user_dir=None, rebase_m=None, profile=False, near=None,
                                  no_record_deps=False, power=None, fly=200, cold=False, keep_cold=False,
                                  trace_frames=1, mem_holders=False)
        cmd = hb.child_command(a, [("i25", False, None)], "/out", "alone", 2, 2)
        self.assertEqual(cmd[cmd.index("--ahead") + 1], "off")
        self.assertNotIn("--cold", cmd)


if __name__ == "__main__":
    unittest.main()


class ColdRunTest(unittest.TestCase):
    """headless_bake --cold: the server's revisit cache in a throwaway subdir, an empty pins folder, removed after."""

    def test_cold_env_and_cleanup(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
        import headless_bake as hb
        with tempfile.TemporaryDirectory() as tmp:
            env, sub = hb.cold_env(os.path.join(tmp, "cache"), os.path.join(tmp, "out"), stamp="x")
            self.assertEqual(env["BNE_REVISIT_SUBDIR"], "revisit-cold-x")
            self.assertEqual(sub, os.path.join(tmp, "cache", "revisit-cold-x"))
            self.assertTrue(os.path.isdir(env["BNE_REVISIT_PIN_DIR"]), "the empty baked-route folder exists")
            os.makedirs(os.path.join(sub, "roads"))
            said = []
            hb.cold_done(sub, keep=True, say=lambda *a, **k: said.append(a))
            self.assertTrue(os.path.isdir(sub))
            hb.cold_done(sub, keep=False, say=lambda *a, **k: said.append(a))
            self.assertFalse(os.path.exists(sub))
        a = types.SimpleNamespace(speed=30, roads_q=1, backlog_q=1, edge_m=1, max_wait=1, ground_wait=1, ahead="on",
                                  cache_dir=None, world_dir=None, user_dir=None, rebase_m=None, profile=False, near=None,
                                  no_record_deps=False, power=None, fly=200, cold=True, keep_cold=False,
                                  trace_frames=1, mem_holders=False)
        cmd = hb.child_command(a, [("i25", False, None)], "/out", "alone", 2, 2)
        self.assertIn("--cold", cmd)
        self.assertNotIn("--keep-cold", cmd)

    def test_revisit_reads_the_switches(self):
        import importlib
        import revisit
        with mock.patch.dict(os.environ, {"BNE_REVISIT_SUBDIR": "revisit-cold-t", "BNE_REVISIT_PIN_DIR": "/p"}):
            importlib.reload(revisit)
            self.assertEqual(revisit.SUBDIR, "revisit-cold-t")
            self.assertEqual(revisit.PIN_DIR, "/p")
            self.assertEqual(revisit.pin_dir("/c"), "/p")
        importlib.reload(revisit)
        self.assertEqual(revisit.SUBDIR, "revisit")


class PlansAheadTest(unittest.TestCase):
    """The map cells the road tiles need, planned ahead of them (plan_items; earthd build_ahead_plans through
    blockworker.plan_cells): due before their tiles, never holding the ready front."""

    def test_plan_items_cover_the_cells_round_the_line_once_due_early(self):
        la, lo, s = ba.resample(north(A, 3.0), 128.0)
        items = ba.plan_items(A, la, lo, s)
        self.assertTrue(items)
        self.assertTrue(all(it.kind == "plans" and len(it.key) == 3 and it.key[0] == 14 for it in items))
        self.assertEqual(len({it.key for it in items}), len(items), "each cell once")
        self.assertEqual(min(it.due for it in items), 0.0, "the first cells are due at once")
        far = max(items, key=lambda it: it.due)
        self.assertLessEqual(far.due, 3000.0 - ba.PLAN_LEAD_M + 1.0, "PLAN_LEAD_M before the sample that reaches it")
        import mapcells
        here = mapcells.cell_of(*A)
        self.assertIn((here.z, here.x, here.y), {it.key for it in items})

    def test_plans_come_first_and_do_not_hold_the_front(self):
        c = ba.Cursor(follow_simple, road_jobs=2, block_jobs=1, plan_jobs=2)
        c.set_route(north(A, 12.0), pinned_rule=True, rebase_m=15000.0)
        fr = geodesy.EnuFrame(*A)
        c.plan(A, fr, 0.0, 0.0, 0.0, 30.0, block=(0.0, 0.0))
        kinds = [it.kind for it in c.items]
        self.assertIn("plans", kinds)
        self.assertEqual(kinds[0], "plans", "the cells under the car first")
        start = c.tick(0.0, 0.0, 30.0)
        self.assertEqual(sorted(it.kind for it in start), ["block", "plans", "plans", "roads", "roads"])
        front0 = c.front()
        for it in c.items:
            if it.kind == "plans":
                c.done_item(it, 1.0, "built")
        self.assertEqual(c.front(), front0, "plans done: the front is the first block or tile still")
        first_tile = next(it for it in c.items if it.kind != "plans")
        self.assertEqual(c.front(), first_tile.due)
        self.assertIn("by kind", c.status())
        c2 = ba.Cursor(follow_simple, road_jobs=0, plan_jobs=0)
        c2.set_route(north(A, 3.0))
        c2.plan(A, fr, 0.0, 0.0, 0.0, 30.0)
        self.assertEqual({it.kind for it in c2.items}, {"block"}, "no road processes: blocks alone")

    def test_server_plans_through_the_road_pool(self):
        import earthd
        a = types.SimpleNamespace(id=1000001, lat=A[0], lon=A[1])
        calls = []

        class Pool:
            def submit(self, fn, *args):
                calls.append((fn, args))
                return types.SimpleNamespace(result=lambda: 1 if len(calls) == 1 else 0)
        s = types.SimpleNamespace(road_pool=Pool())
        self.assertEqual(earthd.Server.build_ahead_plans(s, a, (14, 3413, 6217)), "built")
        self.assertEqual(earthd.Server.build_ahead_plans(s, a, (14, 3413, 6218)), "cached")
        import blockworker
        self.assertIs(calls[0][0], blockworker.plan_cells)
        self.assertEqual(calls[0][1], (1000001, A[0], A[1], [(14, 3413, 6217)]))
        s = types.SimpleNamespace(road_pool=None)
        self.assertEqual(earthd.Server.build_ahead_plans(s, a, (14, 3413, 6217)), "empty")

    def test_worker_plan_cells_warms_the_unplanned(self):
        import blockworker
        planned, warmed = {(14, 1, 1)}, []

        class G:
            def planned(self, key):
                return tuple(key) in planned

            def warm(self, key):
                warmed.append(tuple(key))
        with mock.patch.object(blockworker, "_s", types.SimpleNamespace(anchor=lambda aid, la, lo: (G(), None))):
            n = blockworker.plan_cells(5, A[0], A[1], [(14, 1, 1), (14, 1, 2)])
        self.assertEqual((n, warmed), (1, [(14, 1, 2)]))
        with mock.patch.object(blockworker, "_s", types.SimpleNamespace(anchor=lambda aid, la, lo: (None, None))):
            self.assertEqual(blockworker.plan_cells(5, A[0], A[1], [(14, 1, 1)]), 0)


class RouteOrderTest(unittest.TestCase):
    def test_the_route_result_waits_for_its_elevation_at_most_route_elev_s(self):
        """The cursor and the corridor download get the line before the elevation profile (a cold DEM fetch for the
        profile held both for a whole run)."""
        import earthd
        from test_gasstations import _Writer
        import surface as sf
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sur = sf.Surface(None)
        self.addCleanup(sur.close)
        md = types.SimpleNamespace(has=lambda key, layer: False, versions=lambda: "v")
        s = earthd.Server(tmp.name, os.path.join(tmp.name, "cache"), imagery=None, cells=64, block=512, surface=sur,
                          mapdata=md)
        self.addCleanup(s.hm_pool.shutdown)
        self.addCleanup(s.tile_pool.shutdown)
        self.addCleanup(s.ahead_pool.shutdown)
        order = []

        async def go():
            with mock.patch.object(earthd.revisitmod, "ON", True), mock.patch.object(earthd, "ROUTE_ELEV_S", 0.2):
                w = _Writer()
                c = earthd.Connection(s, None, w)
                c.route_download = lambda version, points: order.append("download")
                c.ahead_plan = lambda: order.append("plan")
                s.dem = object()                      # (an elevation profile would be made ...)

                def slow(points):                     # ... but its DEM read hangs
                    import time as t
                    t.sleep(1.0)
                    return [[0.0, 1.0]]
                c._route_elevation = slow
                await c.route({"id": 3, "points": north(A, 2.0), "pins": True})
                return w.msgs
        msgs = asyncio.run(go())
        self.assertEqual(order, ["download", "plan"], "the line's users run before the profile")
        res = [m for m in msgs if m["type"] == "route_result"][0]
        self.assertNotIn("elev", res, "the profile timed out: the route goes without it")
        self.assertEqual(len(res["points"]), len(north(A, 2.0)))


class OptionsTest(unittest.TestCase):
    def test_the_build_ahead_options_reach_the_module(self):
        """--build-ahead off / --ahead-km / --ahead-jobs set module globals in main_async: a name assigned there without
        a global declaration is a local, and the flag is silently ignored (every "ahead off" run of 2026-10-29 had it
        on)."""
        import earthd
        code = earthd.main_async.__code__
        for name in ("BUILD_AHEAD_ON", "BUILD_AHEAD_MAX_M", "BUILD_AHEAD_JOBS", "ROUTE_AHEAD_MAX_M", "PROGRESS_S"):
            self.assertIn(name, code.co_names, f"{name} is assigned in main_async")
            self.assertNotIn(name, code.co_varnames, f"{name} must be declared global in main_async")


class DriveStartTest(unittest.TestCase):
    def test_the_drive_sends_its_leg_as_the_route_after_the_teleport(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
        import headless_bake as hb
        import ghost_drive as gd
        sent = []
        c = types.SimpleNamespace(send=sent.append)
        route = gd.Route(north(A, 1.0))
        a = types.SimpleNamespace(speed=30.0, fly=30.0, ahead="on")
        d = hb.Drive(c, route, [], 0.0, 15000.0, 512, None, a, next_id=1, out=None, say=lambda *x, **k: None)
        d.start(0.0)
        self.assertEqual([m["type"] for m in sent], ["prepare_anchor", "route"])
        r = sent[1]
        self.assertEqual((len(r["points"]), r["pins"], r["rebaseM"]), (len(route.pts), True, 15000.0))
        self.assertEqual(r["points"][0], [route.pts[0][0], route.pts[0][1]])
        self.assertNotEqual(r["id"], sent[0]["requestId"])
        # --ahead off: the teleport alone
        sent.clear()
        a.ahead = "off"
        d.start(0.0)
        self.assertEqual([m["type"] for m in sent], ["prepare_anchor"])
        # the server's planned anchors join the pins the drive follows
        d.handle({"type": "route_anchors", "anchors": [[39.9, -105.0]], "pinM": 6750}, 1.0)
        self.assertEqual((d.anchors, d.pin_m), ([(39.9, -105.0)], 6750.0))

    def test_trace_frames_and_callers(self):
        import blockworker
        with mock.patch.dict(os.environ, {"BNGE_TRACEMALLOC": "12"}):
            self.assertEqual(blockworker.trace_frames(), 12)
        with mock.patch.dict(os.environ, {"BNGE_TRACEMALLOC": "1"}):
            self.assertEqual(blockworker.trace_frames(), 1)
        with mock.patch.dict(os.environ, {"BNGE_TRACEMALLOC": "yes"}):
            self.assertEqual(blockworker.trace_frames(), 1)
        import tracemalloc
        was = tracemalloc.is_tracing()
        tracemalloc.start(4)
        try:
            keep = [bytearray(200000) for _ in range(5)]
            snap = tracemalloc.take_snapshot()
            top = snap.statistics("lineno")[:1]
            lines = blockworker.trace_callers(snap, top)
            self.assertEqual(len(lines), 1)
            self.assertIn("MB via test_buildahead.py:", lines[0])
            self.assertIn(" < ", lines[0], "the callers follow the allocating line")
            del keep
        finally:
            tracemalloc.stop()
            if was:
                tracemalloc.start(1)


class NeighbourMemoTest(unittest.TestCase):
    """gradesep.neighbours_hook remembers the eight neighbour layers weakly: a layer the cache let go is asked again,
    one still alive is not; strong references chained every cell's layer to the whole drive's (2026-10-30)."""

    def test_neighbours_are_held_weakly_and_asked_again_when_gone(self):
        import gc
        import gradesep
        import mapcells
        import mapgeom
        import numpy as np

        def feats(tag):
            n = np.zeros(0, np.int64)
            f = mapgeom.Features("roads", np.array([], dtype="U1"), np.zeros(0, np.uint8), n, n, n,
                                 np.zeros((0, 2), np.float64))
            f.__dict__["_tag"] = tag
            return f
        asked = []
        alive = {}

        class MD:
            def cell(self, k, layers, kind):
                asked.append(str(k))
                f = alive.get(str(k)) or feats(str(k))
                alive[str(k)] = f
                return types.SimpleNamespace(layers={"roads": f})
        key = mapcells.CellKey(14, 3413, 6217)
        bounds, get = gradesep.neighbours_hook(MD(), key)
        self.assertEqual(bounds, key.bounds())
        got = get()
        self.assertEqual(len(got), 8)
        self.assertEqual(len(asked), 8)
        self.assertEqual(get(), got, "all alive: remembered, nothing asked")
        self.assertEqual(len(asked), 8)
        gone = str(got[3].__dict__["_tag"])
        del alive[gone]
        got = None
        gc.collect()
        again = get()
        self.assertEqual(len(again), 8)
        self.assertGreater(len(asked), 8, "a neighbour let go: asked again (from the cache)")
        self.assertIn(gone, asked[8:])

    def test_mem_holders_names_the_chain(self):
        import memdebug
        import numpy as np

        class Holder:
            pass
        h = Holder()
        h.big = {"roads": np.zeros(400000, np.float64)}         # 3.2 MB under Holder.big["roads"]
        rows = memdebug.holders(min_mb=1.0, top=5)
        chains = [c for _, _, c in rows]
        self.assertTrue(any("Holder.big[roads]" in c for c in chains), chains)
        del h
        with mock.patch.object(memdebug, "ON", True), mock.patch.object(memdebug, "HOLDERS_S", 0.0):
            r = memdebug.Reporter("x ")
            r.last = 0.0
            with self.assertLogs("earthd.memdebug", level="INFO") as cm:
                r.tick()
            self.assertTrue(any("memory holders" in m for m in cm.output))

    def test_headless_tracing_options(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
        import headless_bake as hb
        self.assertEqual(hb.TRACE_FRAMES, 1, "deep traces slowed every build ~10x: opt-in only")
        a = types.SimpleNamespace(speed=30, roads_q=1, backlog_q=1, edge_m=1, max_wait=1, ground_wait=1, ahead="on",
                                  cache_dir=None, world_dir=None, user_dir=None, rebase_m=None, profile=True, near=None,
                                  no_record_deps=False, power=None, fly=200, cold=False, keep_cold=False,
                                  trace_frames=8, mem_holders=True)
        cmd = hb.child_command(a, [("i25", False, None)], "/out", "alone", 2, 2)
        self.assertEqual(cmd[cmd.index("--trace-frames") + 1], "8")
        self.assertIn("--mem-holders", cmd)
