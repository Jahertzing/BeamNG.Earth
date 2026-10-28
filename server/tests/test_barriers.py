"""Highway barriers (barriers.py): concrete median barriers, cable barriers, W-beam guardrails on embankments and
before bridges, the US's parapets as guardrails, OpenStreetMap's mapped barriers and noise walls, and their mesh."""
import json
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import barriers  # noqa: E402
import mapgeom  # noqa: E402
import roadmesh  # noqa: E402
import roads  # noqa: E402
from test_roads import FRAME, LAT, LON, M_LAT, M_LON, features, rules  # noqa: E402

BOX = (0.0, -128.0, 256.0, 128.0)       # the tile (x0, y0, x1, y1)


def spec(cls="motorway", width=11.8, urban=False, oneway=True, d=1, link=None):
    return roads.Spec(width, "paved", urban, cls, oneway, 2.5, d, None, None, None, None, None, link)


def road(fid, y, way=1, x=(-600.0, 900.0), z=10.0, **kw):
    """A straight road along x at y, its geometry drawn towards +x (way 1) or -x (-1); a one-way road (the default)
    travels along its geometry."""
    xs = np.linspace(x[0], x[1], 16)
    pts = np.c_[xs, np.full(len(xs), y), np.full(len(xs), z)]
    kw.setdefault("d", 1 if kw.get("oneway", True) else 0)
    return (fid, spec(**kw), pts if way > 0 else pts[::-1].copy(), LON)


def flat(la, lo):
    return np.full(np.shape(la), 10.0)


def kinds(bs):
    return sorted(b["kind"] for b in bs)


class MedianTest(unittest.TestCase):
    def test_narrow_median_one_concrete_barrier_halfway(self):
        """I-25 through Denver: two 11.8 m carriageways 18 m apart (6.2 m between their edges): one F-shape barrier on
        the median's middle line, the whole tile long, standing JERSEY_H over the road."""
        bs = barriers.tile_barriers(FRAME, [road("a", 9.0, 1), road("b", -9.0, -1)], flat, BOX)
        self.assertEqual(kinds(bs), ["jersey"], bs)
        b = bs[0]
        self.assertLess(np.abs(b["line"][:, 1]).max(), 0.05)
        self.assertAlmostEqual(float(b["line"][:, 0].min()), 0.0, delta=0.01)
        self.assertAlmostEqual(float(b["line"][:, 0].max()), 256.0, delta=0.01)
        self.assertTrue(np.allclose(b["z"], 10.0, atol=0.01))
        self.assertLess(float(b["foot"].max()), 10.0)
        self.assertEqual(b["ends"], (False, False), "it goes on into the next tiles")

    def test_wide_median_cable_barrier_opposite_ways_only(self):
        bs = barriers.tile_barriers(FRAME, [road("a", 20.0, 1), road("b", -20.0, -1)], flat, BOX)
        self.assertEqual(kinds(bs), ["cable"])
        self.assertLess(np.abs(bs[0]["line"][:, 1]).max(), 0.05, "on the median's middle line")
        same = barriers.tile_barriers(FRAME, [road("a", 20.0, 1), road("b", -20.0, 1)], flat, BOX)
        self.assertEqual(kinds(same), [], "two carriageways the same way 28 m apart: no cable between them")
        two = barriers.tile_barriers(FRAME, [road("a", 20.0, 1), road("b", -20.0, -1, d=-1)], flat, BOX)
        self.assertEqual(kinds(two), [], "drawn the other way but travelling along +x too (dir -1): the same way")

    def test_managed_lanes_drawn_overlapping_meet_at_one_barrier(self):
        """I-25's express lanes in Thornton: a pair with no lane count, drawn 11 m wide with their centrelines 8 m
        apart (3 m overlapping), between the general lanes the same ways: one concrete barrier halfway between the pair,
        none between a pair the same way, nor anything inside a carriageway."""
        rs = [road("gpn", 13.0, 1, width=10.8), road("exn", 4.0, 1, width=11.0), road("exs", -4.0, -1, width=11.0),
              road("gps", -13.0, -1, width=10.8)]
        bs = barriers.tile_barriers(FRAME, rs, flat, BOX)
        self.assertEqual(kinds(bs), ["jersey"], bs)
        self.assertLess(np.abs(bs[0]["line"][:, 1]).max(), 0.05)
        self.assertAlmostEqual(float(np.ptp(bs[0]["line"][:, 0])), 256.0, delta=0.01)
        same = barriers.tile_barriers(FRAME, [road("a", 4.0, 1, width=11.0), road("b", -4.0, 1, width=11.0)], flat, BOX)
        self.assertEqual(same, [], "the same way overlapping: one road")
        one = barriers.tile_barriers(FRAME, [road("a", 1.5, 1, width=11.0), road("b", -1.5, -1, width=11.0)], flat, BOX)
        self.assertEqual(one, [], "centrelines 3 m apart: one road drawn twice")

    def test_twin_medians_side_by_side_one_kept(self):
        raw = [["jersey", np.c_[np.linspace(0, 100, 51), np.full(51, 0.0)], None, None, 1.0, 0, (False, False), "b", 0],
               ["jersey", np.c_[np.linspace(0, 200, 101), np.full(101, 2.0)], None, None, 1.0, 0, (False, False), "a",
                0],
               ["wbeam", np.c_[np.linspace(0, 100, 51), np.full(51, 1.0)], None, None, 1.0, 1, (False, False), "c", 1]]
        for r in raw:
            r[2] = r[3] = np.zeros(len(r[1]))
        out = barriers._one_median(raw)
        self.assertEqual(sorted((r[0], r[7]) for r in out), [("jersey", "a"), ("wbeam", "c")],
                         "b's lies within MEDIAN_TWIN_M of a's (the lower id) all along: cut; a guardrail stays")
        raw[0][1] = np.c_[np.linspace(150, 400, 126), np.full(126, 0.0)]
        out = barriers._one_median(raw)
        b = [r for r in out if r[7] == "b"]
        self.assertEqual(len(b), 1)
        self.assertLessEqual(float(b[0][1][0, 0]), 200.0 + 0.01, "it reaches back to a's end: no gap")
        self.assertGreater(float(b[0][1][0, 0]), 190.0)
        self.assertEqual(b[0][6], (True, False), "its new start a real end")

    def test_very_wide_median_none(self):
        self.assertEqual(barriers.tile_barriers(FRAME, [road("a", 40.0, 1), road("b", -40.0, -1)], flat, BOX), [])

    def test_arterial_trunk_median_none(self):
        """A divided trunk that is an arterial (Santa Fe Drive's signals): a curbed median, no concrete barrier."""
        a = road("a", 9.0, 1, cls="trunk")
        b = road("b", -9.0, -1, cls="trunk")
        art = lambda r: (r[0], r[1]._replace(kmh=60.0), r[2], r[3])     # noqa: E731
        self.assertEqual(kinds(barriers.tile_barriers(FRAME, [a, b], flat, BOX)), ["jersey"], "an expressway's")
        self.assertEqual(barriers.tile_barriers(FRAME, [art(a), art(b)], flat, BOX), [])

    def test_crossing_road_cuts_the_median_barrier(self):
        """A road across the median at grade (a trunk's junction): the barrier stops either side of it."""
        cross = ("c", spec("primary", 8.0, oneway=False, d=0), np.array([[128.0, -60.0, 10.0], [128.0, 60.0, 10.0]]),
                 LON)
        bs = barriers.tile_barriers(FRAME, [road("a", 9.0, 1, cls="trunk"), road("b", -9.0, -1, cls="trunk"), cross],
                                    flat, BOX)
        js = [b for b in bs if b["kind"] == "jersey"]
        self.assertEqual(len(js), 2)
        for b in js:
            self.assertFalse(((b["line"][:, 0] > 122.0) & (b["line"][:, 0] < 134.0)).any())

    def test_twin_bridge_decks_get_one_median_barrier(self):
        """I-25's twin bridges 3 m apart: roadmesh joins their slabs and leaves the parapets out; a barrier on the
        slab between them at deck level, once."""
        def deck(fid, y, z=25.0):
            return {"n": [[-100.0, y, z], [400.0, y, z]], "w": 11.8, "deck": True, "fid": fid, "ctx": True}
        bs = barriers.tile_barriers(FRAME, [], flat, BOX, decks=[deck("d1", 7.4), deck("d2", -7.4)])
        self.assertEqual(kinds(bs), ["jersey"])
        self.assertLess(np.abs(bs[0]["line"][:, 1]).max(), 0.05)
        self.assertTrue(np.allclose(bs[0]["z"], 25.0))
        far = barriers.tile_barriers(FRAME, [], flat, BOX, decks=[deck("d1", 12.0), deck("d2", -12.0)])
        self.assertEqual(far, [], "12 m apart: each deck keeps its parapets")

    def test_split_level_median_too_tall_none(self):
        hi = road("a", 9.0, 1, z=16.0)

        def step(la, lo):
            n = (np.asarray(la) - LAT) * M_LAT
            return np.where(n > 2.0, 16.0, 10.0)
        self.assertEqual([b for b in barriers.tile_barriers(FRAME, [hi, road("b", -9.0, -1)], step, BOX)
                          if b["kind"] == "jersey"], [])


class GuardrailTest(unittest.TestCase):
    def test_embankment_gets_a_guardrail_facing_the_road(self):
        """The ground falls 3 m beyond the left edge (y > 8): a W-beam there, its face EDGE_OFF_M off the edge, the
        traffic on its right (face -1 of a line running with the road), none on the flat right side."""
        def bank(la, lo):
            n = (np.asarray(la) - LAT) * M_LAT
            return 10.0 - np.clip(n - 7.5, 0.0, 3.0)
        bs = barriers.tile_barriers(FRAME, [road("a", 0.0, 1, oneway=False, cls="trunk", width=12.0)], bank, BOX)
        self.assertEqual(kinds(bs), ["wbeam"])
        b = bs[0]
        self.assertTrue(np.allclose(b["line"][:, 1], 6.0 + barriers.EDGE_OFF_M, atol=0.05))
        self.assertEqual((b["side"], b["face"]), (1, -1))
        self.assertTrue(np.allclose(b["z"], 10.0, atol=0.05))

    def test_before_a_bridge(self):
        """A carriageway ending at x = 200 with no ground road going on (its bridge): guardrail on its last GUARD_END_M,
        its far end real (a terminal), the end at the bridge not; a road going on: none."""
        a = road("a", 0.0, 1, x=(-600.0, 200.0), oneway=False, cls="trunk", width=12.0)
        bs = barriers.tile_barriers(FRAME, [a], flat, BOX)
        self.assertEqual(kinds(bs), ["wbeam", "wbeam"])
        for b in bs:
            self.assertGreater(float(b["line"][:, 0].min()), 200.0 - barriers.GUARD_END_M - 2.5)
            self.assertAlmostEqual(float(b["line"][:, 0].max()), 200.0, delta=0.5)
            self.assertEqual(b["ends"], (True, False))
        on = ("b", a[1], np.array([[200.0, 0.0, 10.0], [900.0, 0.0, 10.0]]), LON)
        self.assertEqual(barriers.tile_barriers(FRAME, [a, on], flat, BOX), [])

    def test_ramp_leaving_keeps_the_guardrail_off_it(self):
        def bank(la, lo):
            n = (np.asarray(la) - LAT) * M_LAT
            return 10.0 - np.clip(-n - 7.5, 0.0, 3.0)
        main = road("a", 0.0, 1, oneway=False, cls="trunk", width=12.0)
        ramp = ("r", spec(width=6.0, link=True), np.array([[60.0, -6.0, 10.0], [140.0, -12.0, 10.0],
                                                          [400.0, -60.0, 10.0]]), LON)
        bs = [b for b in barriers.tile_barriers(FRAME, [main, ramp], bank, BOX) if b["fid"] == "a"]
        self.assertTrue(bs)
        for b in bs:
            self.assertFalse(((b["line"][:, 0] > 60.0) & (b["line"][:, 0] < 120.0)).any(), "not across the ramp")

    def test_mapped_guardrail_replaces_the_procedural_one(self):
        def bank(la, lo):
            n = (np.asarray(la) - LAT) * M_LAT
            return 10.0 - np.clip(n - 7.5, 0.0, 3.0)
        r = road("a", 0.0, 1, oneway=False, cls="trunk", width=12.0)
        osm = [("wbeam", np.array([[-100.0, 7.2], [400.0, 7.2]]), None, "osm1")]
        bs = barriers.tile_barriers(FRAME, [r], bank, BOX, mapped=osm)
        self.assertEqual(len(bs), 1)
        self.assertEqual(bs[0]["fid"], "osm1")
        self.assertEqual(bs[0]["face"], -1, "its traffic on its right (the road at y 0)")

    def test_mapped_barrier_on_a_carriageway_left_out(self):
        osm = [("jersey", np.array([[-100.0, 2.0], [400.0, 2.0]]), None, "osm1"),
               ("noise", np.array([[-100.0, 25.0], [400.0, 25.0]]), 5.0, "osm2")]
        r = road("a", 0.0, 1, oneway=False, cls="primary", width=12.0)
        bs = barriers.tile_barriers(FRAME, [r], flat, BOX, mapped=osm)
        self.assertEqual(kinds(bs), ["noise"])
        self.assertEqual(bs[0]["h"], 5.0)

    def test_mapped_guardrail_on_a_bridge_left_out(self):
        """OpenStreetMap maps a bridge's rails along its deck: their ground is the valley under it, none drawn there."""
        deck = {"n": [[100.0, 0.0, 25.0], [180.0, 0.0, 25.0]], "w": 12.0, "deck": True}
        osm = [("wbeam", np.array([[60.0, 7.2], [220.0, 7.2]]), None, "osm1")]
        bs = barriers.tile_barriers(FRAME, [road("a", 0.0, 1, oneway=False, cls="primary", width=12.0)], flat, BOX,
                                    decks=[deck], mapped=osm)
        self.assertTrue(bs)
        for b in bs:
            self.assertFalse(((b["line"][:, 0] > 98.0) & (b["line"][:, 0] < 182.0)).any())

    def test_us_parapets_become_guardrails_in_build_tile(self):
        """A rural road along a 4 m drop: Alpine stone parapets elsewhere, W-beam guardrails in the US."""
        f = features([[(-600, 0), (900, 0)]], ["secondary"])

        def drop(la, lo):
            n = (np.asarray(la) - LAT) * M_LAT
            return 10.0 - np.clip(n - 5.0, 0.0, 4.0) * 2.0
        rural = lambda la, lo: np.zeros(np.shape(la), bool)        # noqa: E731
        walls, out = [], []
        roads.build_tile(FRAME, 0, 0, [f], drop, rural, walls=walls, barriers=out, barrier_lines=[])
        self.assertTrue(any(w.get("parapet") for w in walls), "elsewhere: stone parapets")
        self.assertEqual(out, [])
        walls, out = [], []
        roads.build_tile(FRAME, 0, 0, [f], drop, rural, walls=walls, barriers=out, barrier_lines=[],
                         lamp_region="na:sparse")
        self.assertFalse(any(w.get("parapet") for w in walls))
        self.assertIn("wbeam", [b["kind"] for b in out])

    def test_parapets_as_guardrails(self):
        w = {"base": np.c_[np.arange(0.0, 60.0, 2.0), np.full(30, 4.2)], "toward": np.tile([0.0, -1.0], (30, 1)),
             "road_z": np.full(30, 10.0), "parapet": True}
        bs = barriers.tile_barriers(FRAME, [], flat, BOX, parapets=[w])
        self.assertEqual(kinds(bs), ["wbeam"])
        self.assertTrue(np.allclose(bs[0]["line"][:, 1], 4.0 + barriers.EDGE_OFF_M, atol=0.01))
        self.assertEqual(bs[0]["face"], -1)

    def test_deterministic_and_seamless_between_tiles(self):
        rs = [road("a", 9.0, 1), road("b", -9.0, -1)]
        a = barriers.tile_barriers(FRAME, rs, flat, BOX)
        b = barriers.tile_barriers(FRAME, list(reversed(rs)), flat, BOX)
        self.assertEqual(len(a), len(b))
        self.assertTrue(np.allclose(a[0]["line"], b[0]["line"]))
        nxt = barriers.tile_barriers(FRAME, rs, flat, (256.0, -128.0, 512.0, 128.0))
        self.assertTrue(np.allclose(a[0]["line"][-1], nxt[0]["line"][0], atol=0.01), "meets the next tile's")


class NoiseWallTest(unittest.TestCase):
    HOMES = [[np.array([[-300.0, 40.0], [600.0, 40.0], [600.0, 300.0], [-300.0, 300.0]])]]    # north of the road

    def test_noise_wall_along_the_homes(self):
        """Thornton: homes north of I-25's northbound carriageway: a noise wall NOISE_OFF_M off its outer edge, none in
        the median nor on the south side."""
        rs = [road("a", 9.0, 1), road("b", -9.0, -1)]
        bs = barriers.tile_barriers(FRAME, rs, flat, BOX, homes=self.HOMES)
        noise = [b for b in bs if b["kind"] == "noise"]
        self.assertEqual(len(noise), 1)
        self.assertTrue(np.allclose(noise[0]["line"][:, 1], 9.0 + 5.9 + barriers.NOISE_OFF_M, atol=0.05))
        self.assertEqual(noise[0]["h"], barriers.NOISE_PROC_H_M)
        self.assertEqual(kinds(barriers.tile_barriers(FRAME, rs, flat, BOX, homes=[])), ["jersey"])

    def test_mapped_noise_wall_or_a_ramp_beside_none(self):
        rs = [road("a", 9.0, 1), road("b", -9.0, -1)]
        osm = [("noise", np.array([[-300.0, 30.0], [600.0, 30.0]]), 5.0, "osm1")]
        bs = barriers.tile_barriers(FRAME, rs, flat, BOX, homes=self.HOMES, mapped=osm)
        self.assertEqual([b["fid"] for b in bs if b["kind"] == "noise"], ["osm1"])
        ramp = ("r", spec(width=6.0, link=True), np.array([[-300.0, 22.0, 10.0], [600.0, 22.0, 10.0]]), LON)
        bs = barriers.tile_barriers(FRAME, rs + [ramp], flat, BOX, homes=self.HOMES)
        self.assertEqual([b for b in bs if b["kind"] == "noise"], [])

    def test_noise_wall_along_a_subdivisions_streets(self):
        """No residential land use mapped (most of the Front Range) but a subdivision's street 50 m off the edge: a
        noise wall on that side; a street crossing under the motorway alone: none."""
        rs = [road("a", 9.0, 1), road("b", -9.0, -1)]
        street = ("s", spec("residential", 6.0, urban=True, oneway=False, d=0),
                  np.array([[-300.0, 70.0, 10.0], [600.0, 70.0, 10.0]]), LON)
        bs = barriers.tile_barriers(FRAME, rs + [street], flat, BOX)
        noise = [b for b in bs if b["kind"] == "noise"]
        self.assertEqual(len(noise), 1)
        self.assertGreater(float(noise[0]["line"][:, 1].min()), 0.0, "on the street's side")
        across = ("s", spec("residential", 6.0, urban=True, oneway=False, d=0),
                  np.array([[128.0, -300.0, 4.0], [128.0, 300.0, 4.0]]), LON)
        bs = barriers.tile_barriers(FRAME, rs + [across], flat, BOX)
        self.assertEqual([b for b in bs if b["kind"] == "noise"], [])

    def test_residential_parts(self):
        f = features([[(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)], [(200, 0), (300, 0), (300, 100), (200, 0)]],
                     ["residential", "farmland"], subtypes=["residential", "agriculture"])
        f.layer = "land_use"
        parts = barriers.residential_parts(FRAME, [f, f])
        self.assertEqual(len(parts), 1)
        self.assertEqual(list(barriers._in_parts(parts, np.array([[50.0, 50.0], [250.0, 20.0]]))), [True, False])


class NonFiniteGroundTest(unittest.TestCase):
    def test_nan_ground_leaves_those_points_out(self):
        """A NaN ground height under part of a barrier (Castle Rock's tiles on Jeff's PC, 2026-10-28: a NaN vertex in
        the tunnel mesh failed the tile): no non-finite value in any barrier nor in the mesh, the rest kept."""
        def holey(la, lo):
            e = (np.asarray(la, np.float64) * 0 + (np.asarray(lo) - LON) * M_LON)
            return np.where((e > 100.0) & (e < 140.0), np.nan, np.where(np.abs((np.asarray(la) - LAT) * M_LAT) > 17.0,
                                                                          5.0, 10.0))
        rs = [road("a", 9.0, 1), road("b", -9.0, -1)]
        bs = barriers.tile_barriers(FRAME, rs, holey, BOX)
        self.assertTrue(bs)
        for b in bs:
            for k in ("line", "z", "foot"):
                self.assertTrue(np.isfinite(b[k]).all(), (b["kind"], k))
        js = [b for b in bs if b["kind"] == "jersey"]
        self.assertTrue(js)
        for b in js:
            self.assertFalse(((b["line"][:, 0] > 102.0) & (b["line"][:, 0] < 138.0)).any())
        self.assertTrue([b for b in bs if b["kind"] == "wbeam"], "the embankments' guardrails kept")
        P, T, UV, M = [], [], [], []
        bad = dict(bs[0], z=np.full(len(bs[0]["z"]), np.nan))
        barriers.mesh_into(bs + [bad], (0.0, 0.0, 0.0), P, T, UV, M, 0)
        self.assertTrue(np.isfinite(np.concatenate(P)).all())


class MappedTest(unittest.TestCase):
    def test_mapped_lines(self):
        f = features([[(0, 10), (100, 10)], [(0, 30), (100, 30)], [(0, 50), (100, 50)], [(0, 70), (100, 70)]],
                     ["guard_rail", "wall", "wall", "fence"], subtypes=["barrier"] * 4)
        f.layer = "infrastructure"
        f.attrs["tags"] = np.array([None, json.dumps({"wall": "noise_barrier"}), json.dumps({"wall": "brick"}),
                                    json.dumps({"noise_barrier": "yes"})], object)
        f.attrs["height"] = np.array([np.nan, 6.0, np.nan, 40.0])
        got = barriers.mapped_lines(FRAME, [f, f])
        self.assertEqual([(k, h) for k, _xy, h, _id in got], [("wbeam", None), ("noise", 6.0), ("noise", None)])
        self.assertAlmostEqual(float(got[0][1][0, 1]), 10.0, delta=0.05)


class MeshTest(unittest.TestCase):
    def bars(self):
        jersey = {"kind": "jersey", "line": np.array([[0.0, 0.0], [50.0, 0.0]]), "z": np.array([10.0, 10.0]),
                  "foot": np.array([9.7, 9.7]), "h": barriers.JERSEY_H, "face": 0, "ends": (False, False),
                  "fid": "a", "side": 0}
        wbeam = dict(jersey, kind="wbeam", line=np.array([[0.0, 8.0], [40.0, 8.0]]), h=barriers.WBEAM_TOP, face=-1,
                     ends=(True, False), foot=np.array([9.1, 9.1]))
        cable = dict(jersey, kind="cable", line=np.array([[0.0, -20.0], [40.0, -20.0]]), h=barriers.CABLE_POST_H)
        noise = dict(jersey, kind="noise", line=np.array([[0.0, 30.0], [40.0, 30.0]]), h=4.5)
        return jersey, wbeam, cable, noise

    def test_tunnel_mesh_with_barriers(self):
        jersey, wbeam, cable, noise = self.bars()
        P, T, UV, mats, M = roadmesh.tunnel([], (0.0, 0.0, 0.0), (), [jersey, wbeam, cable, noise])
        self.assertIn("bne_guardrail_steel", mats)
        self.assertIn("bne_noise_wall", mats)
        self.assertEqual(len(T), len(M))
        self.assertTrue(np.isfinite(P).all())
        self.assertTrue(np.isfinite(UV).all())
        self.assertLess(int(T.max()), len(P))
        conc = P[np.unique(T[np.array(mats)[M] == roadmesh.TUNNEL_SIDE])]
        self.assertAlmostEqual(float(conc[:, 2].max()), 10.0 + barriers.JERSEY_H, places=3)
        self.assertAlmostEqual(float(np.abs(conc[:, 1]).max()), barriers.JERSEY_BASE / 2, places=3)
        nw = P[np.unique(T[np.array(mats)[M] == roadmesh.NOISE_WALL])]
        self.assertAlmostEqual(float(nw[:, 2].max()), 10.0 + 4.5 + barriers.PILASTER_OVER, places=3)

    def test_jersey_faces_out(self):
        jersey = self.bars()[0]
        P, T, UV, M = [], [], [], []
        barriers.mesh_into([jersey], (0.0, 0.0, 0.0), P, T, UV, M, 0)
        P, T = np.concatenate(P), np.concatenate(T[:-2])         # (the end caps are two-sided)
        n = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
        c = P[T].mean(1)
        out = (n[:, 1] * c[:, 1] > -1e-9) | (n[:, 2] > 0)
        self.assertTrue(out.all())

    def test_wbeam_terminal_and_posts(self):
        """A real end turns the rail down into the ground; posts stand behind the rail, away from the traffic."""
        wbeam = self.bars()[1]
        P, T, UV, M = [], [], [], []
        barriers.mesh_into([wbeam], (0.0, 0.0, 0.0), P, T, UV, M, 0)
        P = np.concatenate(P)
        near_end = P[P[:, 0] < 0.01]
        self.assertLess(float(near_end[:, 2].max()), 10.1, "the end is down at the ground")
        far = P[(P[:, 0] > 39.99)]
        self.assertAlmostEqual(float(far[:, 2].max()), 10.0 + barriers.WBEAM_TOP, places=2)
        self.assertGreater(float(P[:, 1].max()), 8.0 + barriers.WBEAM_BLOCK, "posts behind (face -1: the road at -y)")
        self.assertLess(float(P[:, 1].min()), 8.0 + 1e-6)

    def test_build_tile_wires_them(self):
        f = features([[(-600, 9), (900, 9)], [(900, -9), (-600, -9)]], ["motorway", "motorway"])
        f.rules["oneway"] = rules([(0, 1.0), (1, 1.0)], "float")
        out = []
        roads.build_tile(FRAME, 0, 0, [f], flat, barriers=out, barrier_lines=[])
        self.assertIn("jersey", [b["kind"] for b in out])


if __name__ == "__main__":
    unittest.main()
