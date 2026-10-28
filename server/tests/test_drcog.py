"""The DRCOG planimetrics source (server/drcog.py): roofprints merged with Overture's buildings, road widths between
the edges of pavement, the map data's cells with it on."""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import drcog  # noqa: E402
import mapgeom as mg  # noqa: E402
import mapfixtures as mf  # noqa: E402
import overture  # noqa: E402
from test_mapdata import Sources, SmallBlocks, needs_duckdb  # noqa: E402

LON0, LAT0 = -104.94, 40.10
KX, KY = 111320.0 * np.cos(np.radians(LAT0)), 110540.0


def ll(x, y):
    return LON0 + x / KX, LAT0 + y / KY


def box(x, y, w, d):
    return np.array([ll(x, y), ll(x + w, y), ll(x + w, y + d), ll(x, y + d), ll(x, y)])


def overture_layer(boxes, classes, heights):
    ids = [f"0{i:031d}" for i in range(len(boxes))]
    f = mg.build("buildings", mg.text_column(ids), [(mg.POLYGON, [[b]]) for b in boxes])
    spec = overture.LAYERS["buildings"]
    n = len(boxes)
    for c in spec["text"]:
        f.attrs[c] = mg.text_column([""] * n)
    for c in spec["float"]:
        f.attrs[c] = mg.float_column([None] * n)
    for c in spec["bool"]:
        f.attrs[c] = mg.bool_column([False] * n)
    f.attrs["class"] = mg.text_column(classes)
    f.attrs["height"] = mg.float_column(heights)
    f.attrs["src_dataset"] = mg.text_column(["OpenStreetMap"] * n)
    f.attrs["roof_shape"] = mg.text_column(["gabled"] + [""] * (n - 1))
    f.attrs["cx"] = np.array([b[:, 0].mean() for b in boxes])
    f.attrs["cy"] = np.array([b[:, 1].mean() for b in boxes])
    return f


def roofprints(boxes, heights_ft, types):
    coords = np.concatenate(boxes)
    off = np.r_[0, np.cumsum([len(b) for b in boxes])]
    return {"oid": np.arange(len(boxes), dtype=np.int64) * 16 + 16, "bid": np.array([str(i) for i in range(len(boxes))]),
            "height": np.array([h * drcog.FT if h is not None else np.nan for h in heights_ft]),
            "ground": np.full(len(boxes), 1500.0), "type": np.array(types), "coords": coords, "off": off}


class MergeTest(unittest.TestCase):
    def test_roofprints_replace_what_they_match(self):
        ov = overture_layer([box(0, 0, 10, 8), box(30, 0, 10, 8), box(60, 0, 10, 8)], ["house", "garage", ""],
                            [7.0, None, 5.0])
        # a roofprint over the first (shifted 1 m), one over the third, one where Overture has none
        rp = roofprints([box(1, 0.5, 10, 8), box(60.5, 0, 11, 8), box(90, 0, 12, 12)], [24.0, None, 40.0],
                        ["Residential", "Commercial", "Industrial"])
        out, n = drcog.merge_buildings(ov, rp)
        self.assertEqual(n, 3)
        self.assertEqual(len(out), 4)                   # the garage (no roofprint) stays
        ids = list(out.ids)
        self.assertEqual(ids, sorted(ids))
        self.assertIn("00000000000000000000000000000001", ids)
        self.assertNotIn("00000000000000000000000000000000", ids)
        r0 = ids.index("zdrcog-000000000016")
        self.assertAlmostEqual(float(out.attrs["height"][r0]), 24.0 * drcog.FT, places=4)
        self.assertEqual(out.attrs["class"][r0], "house")             # the replaced building's attributes
        self.assertEqual(out.attrs["roof_shape"][r0], "gabled")
        self.assertEqual(out.attrs["src_dataset"][r0], drcog.DATASET)
        r1 = ids.index("zdrcog-000000000032")
        self.assertAlmostEqual(float(out.attrs["height"][r1]), 5.0, places=4)   # no DRCOG height: Overture's
        r2 = ids.index("zdrcog-000000000048")
        self.assertEqual(out.attrs["class"][r2], "industrial")        # no donor: DRCOG's type
        ring = out.parts(r2)[0][0]
        self.assertAlmostEqual((ring[:, 0].max() - ring[:, 0].min()) * KX, 12.0, delta=0.01)

    def test_ruins_left_out_and_determinism(self):
        ov = overture_layer([box(0, 0, 10, 8)], ["house"], [7.0])
        rp = roofprints([box(0, 0, 10, 8), box(40, 0, 5, 5)], [20.0, 3.0], ["Residential", "Foundation/Ruin"])
        a, n = drcog.merge_buildings(ov, rp)
        b, _ = drcog.merge_buildings(ov, rp)
        self.assertEqual(n, 1)
        np.testing.assert_array_equal(a.lonlat, b.lonlat)
        self.assertEqual(list(a.ids), list(b.ids))

    def test_covers(self):
        self.assertTrue(drcog.covers((-104.95, 40.09, -104.93, 40.11)))
        self.assertFalse(drcog.covers((-76.6, 39.2, -76.5, 39.3)))


class WidthTest(unittest.TestCase):
    def edges(self, half=5.0, length=200.0):
        a = np.array([ll(x, half) for x in np.linspace(0, length, 11)])
        b = np.array([ll(x, -half) for x in np.linspace(0, length, 11)])
        return np.concatenate([a, b]), np.array([0, len(a), len(a) + len(b)])

    def test_width_between_the_edges(self):
        coords, off = self.edges(5.5)
        road = np.array([ll(20, 0.4), ll(180, 0.4)])
        self.assertAlmostEqual(drcog.measure_width(road, coords, off), 11.0, places=3)

    def test_no_edges_or_one_side(self):
        coords, off = self.edges(5.0)
        road = np.array([ll(20, 30), ll(180, 30)])          # far from both: out of reach on one side
        self.assertIsNone(drcog.measure_width(road, coords, off))
        self.assertIsNone(drcog.measure_width(road, np.zeros((0, 2)), np.array([0])))

    def test_widths_function_falls_back(self):
        d = drcog.Drcog("unused", None)
        coords, off = self.edges(4.0)
        with mock.patch.object(d, "edges", return_value={"coords": coords, "off": off}):
            get = d.widths(lambda fid, l: (14.0, 3.0))
            w, walk = get("x", np.array([ll(20, 0), ll(180, 0)]))
            self.assertAlmostEqual(w, 8.0, places=4)
            self.assertEqual(walk, 3.0)
            self.assertEqual(get("y", np.array([ll(20, 60), ll(180, 60)])), (14.0, 3.0))
            self.assertEqual(get("z", np.array([(-76.6, 39.2), (-76.5, 39.2)])), (14.0, 3.0))   # outside the region


@needs_duckdb
class MapDataTest(Sources, SmallBlocks, unittest.TestCase):
    def test_off_by_default_and_on_with_the_flag(self):
        md, _ = self.mapdata()
        self.assertIsNone(md.drcog())
        plain = md.path(mf.CELL, "buildings")
        with mock.patch.dict(os.environ, {drcog.ENV: "1"}), \
                mock.patch.object(drcog, "covers", return_value=True):
            md2, _ = self.mapdata(cache=os.path.join(self.tmp, "c2"))
            self.assertTrue(md2.path(mf.CELL, "buildings").endswith(os.path.join(f"buildings.{drcog.TAG}", "14",
                                                                                str(mf.CELL.x), f"{mf.CELL.y}.npz")))
            self.assertNotIn("drcog", md2.path(mf.CELL, "roads"))
            lon0, lat0, lon1, lat1 = mf.CELL.bounds()
            x, y = lon0 + 0.9 * (lon1 - lon0), lat1 - 0.9 * (lat1 - lat0)
            ring = np.array([(x, y), (x + 1e-4, y), (x + 1e-4, y + 1e-4), (x, y + 1e-4), (x, y)])
            rp = {"oid": np.array([16]), "bid": np.array(["7"]), "height": np.array([12.0]),
                  "ground": np.array([1.0]), "type": np.array(["Commercial"]), "coords": ring, "off": np.array([0, 5])}
            with mock.patch.object(drcog.Drcog, "roofprints", return_value=rp):
                cd = md2.cell(mf.CELL, ("buildings",))
            b = cd.layers["buildings"]
            self.assertEqual(len(b), 8)                 # the fixture's 7 and the new roofprint
            self.assertEqual(b.ids[-1], "zdrcog-000000000016")
            self.assertTrue(b.own[-1])
            ds = [d["dataset"] for p in cd.provenance for d in p.get("datasets", [])]
            self.assertIn(drcog.DATASET, ds)

    def test_roads_come_with_their_edges(self):
        # (fix/road-paint-perf: a cell's edges were first fetched by the ground photo's road widths, 2-3.5 s in it)
        with mock.patch.dict(os.environ, {drcog.ENV: "1"}),                 mock.patch.object(drcog, "covers", return_value=True):
            md, _ = self.mapdata(cache=os.path.join(self.tmp, "c3"))
            got = {"coords": np.zeros((0, 2)), "off": np.array([0])}
            with mock.patch.object(drcog.Drcog, "edges", return_value=got) as edges:
                md.cell(mf.CELL, ("roads",))
                md.layer_arrays(mf.CELL, "roads")
            self.assertEqual(edges.call_count, 1, "once per cell")
            md2, _ = self.mapdata(cache=os.path.join(self.tmp, "c4"))
            with mock.patch.object(drcog.Drcog, "edges", side_effect=OSError("down")) as edges:
                cd = md2.cell(mf.CELL, ("roads",))         # (the roads all the same)
                self.assertGreater(len(cd.layers["roads"]), 0)
                md2.layer_arrays(mf.CELL, "roads")
            self.assertEqual(edges.call_count, 2, "a failed fetch is tried again")


class MemoryTest(unittest.TestCase):
    def test_lru_per_kind(self):
        import mapcells
        d = drcog.Drcog(os.path.join(os.path.dirname(__file__), "no-such-dir"), None)
        built = []

        def build(i):
            def f():
                built.append(i)
                return {"i": np.array([i])}
            return f
        with mock.patch.object(drcog.np, "savez_compressed"), mock.patch.object(drcog.staging, "replace_cached"),                 mock.patch.object(drcog.os, "makedirs"),                 mock.patch.dict(drcog.MEM_CELLS, {"edges": 3, "roofprints": 2}):
            keys = [mapcells.CellKey(14, 3400 + i, 6200) for i in range(5)]
            for i in range(3):
                d._cached("edges", keys[i], build(i))
            for i in range(4):                          # roofprints do not push the edges out
                d._cached("roofprints", keys[i], build(10 + i))
            d._cached("edges", keys[0], build(99))      # (kept: no build; now the most recent)
            d._cached("edges", keys[3], build(3))       # keys[1] goes, the least recent
            d._cached("edges", keys[0], build(98))
            d._cached("edges", keys[1], build(1))
        self.assertEqual(built, [0, 1, 2, 10, 11, 12, 13, 3, 1])
        self.assertEqual(len(d._mem["edges"]), 3)
        self.assertEqual(len(d._mem["roofprints"]), 2)


if __name__ == "__main__":
    unittest.main()
