"""streetlife: the driveways' parking spots, mailboxes, cluster boxes and carts, the mapped bus stops, the downtown
extras; deterministic, clear of the street furniture, and every group in the mod's catalog."""
import re
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import furniture  # noqa: E402
import parking  # noqa: E402
import roads  # noqa: E402
import streetlife as sl  # noqa: E402

TREES = Path(__file__).resolve().parents[2] / "mod" / "beamng_earth" / "lua" / "ge" / "extensions" / "beamngEarth" / \
    "trees.lua"
BOX = (0.0, 0.0, 256.0, 256.0)


def flat(xy):
    return np.zeros(len(np.asarray(xy).reshape(-1, 2)))


def ll(shift=(0.0, 0.0)):
    """A frame-free to_ll: metres as micro-degrees around 39.7 N (shift: another anchor's frame)."""
    def f(xy):
        xy = np.asarray(xy, np.float64).reshape(-1, 2) + np.asarray(shift)
        return 39.7 + xy[:, 1] / 111000.0, -104.9 + xy[:, 0] / 85000.0
    return f


def drives_along(n=12, x0=20.0, gap=18.0, length=14.0):
    """Driveways of houses north of an east-west street (its north edge at y 104): from the house (y 104 + length) to
    the road's edge."""
    return [{"w": 3.2, "n": [[x0 + k * gap, 104.0 + length, 0.0], [x0 + k * gap, 104.0, 0.0]]} for k in range(n)]


def sidewalk():
    return [{"fid": "s", "pts": np.array([(-50.0, 105.6, 0.0), (400.0, 105.6, 0.0)]), "side": 1, "w": 1.5}]


def street_line(urban=True, cls="residential", x=(-100.0, 500.0)):
    """The east-west street the drives meet: its north edge at y 104."""
    return [(np.array([(x[0], 100.0), (x[1], 100.0)]), 4.0, cls, urban)]


class DrivewayTest(unittest.TestCase):
    def test_country_road_a_mailbox_at_every_drive(self):
        rows = sl.driveway_rows(drives_along(), [], BOX, flat, ll(), us=True, town=True, lines=street_line(urban=False))
        mb = [r for r in rows if r[0] == sl.G_MAILBOX]
        self.assertEqual(len(mb), 12)
        for r in mb:        # beside the drive, just in from the road's edge, its door (+x) to the road (-y): yaw 270
            self.assertAlmostEqual(r[2], 104.0 + sl.MAILBOX_IN_M, places=2)
            self.assertAlmostEqual(r[4], 270.0, places=1)
            dx = min(abs(r[1] - (20.0 + k * 18.0)) for k in range(12))
            self.assertAlmostEqual(dx, 1.6 + sl.MAILBOX_SIDE_M, places=2)
        self.assertFalse([r for r in rows if r[0] == sl.G_CBU])

    def test_sparse_town_street_mailboxes_dense_one_a_cluster_box(self):
        sparse = sl.driveway_rows(drives_along(4, gap=40.0), [], BOX, flat, ll(), lines=street_line())
        self.assertEqual(len([r for r in sparse if r[0] == sl.G_MAILBOX]), 4)
        dense = sl.driveway_rows(drives_along(20, gap=8.0), [], BOX, flat, ll(), lines=street_line())
        self.assertFalse([r for r in dense if r[0] == sl.G_MAILBOX])
        self.assertTrue(1 <= len([r for r in dense if r[0] == sl.G_CBU]) <= 2)

    def test_sidewalk_streets_get_a_cluster_box_not_mailboxes(self):
        rows = sl.driveway_rows(drives_along(20, gap=8.0), sidewalk(), BOX, flat, ll(), us=True, town=True,
                                lines=street_line())
        self.assertFalse([r for r in rows if r[0] == sl.G_MAILBOX])
        cbu = [r for r in rows if r[0] == sl.G_CBU]
        self.assertTrue(1 <= len(cbu) <= 2, cbu)
        for r in cbu:       # behind the sidewalk (its outer edge 106.35)
            self.assertGreater(r[2], 106.35)

    def test_an_alley_gets_no_mail(self):
        rows = sl.driveway_rows(drives_along(20, gap=8.0), [], BOX, flat, ll(), lines=street_line(cls="service"))
        self.assertFalse([r for r in rows if r[0] in (sl.G_MAILBOX, sl.G_CBU)])

    def test_nothing_at_the_curb_past_a_dead_end(self):
        rows = sl.driveway_rows(drives_along(4, gap=40.0), [], BOX, flat, ll(),
                                lines=street_line(urban=False, x=(-100.0, 60.0)))
        xs = sorted(r[1] for r in rows if r[0] == sl.G_MAILBOX)
        self.assertEqual(len(xs), 2)            # (the drives at x 20 and 60; 100 and 140 are past the road's end)
        self.assertTrue(all(x < 65.0 for x in xs))

    def test_outside_the_us_no_mailboxes_or_carts(self):
        rows = sl.driveway_rows(drives_along(), [], BOX, flat, ll(), us=False, town=True)
        self.assertEqual(rows, [])

    def test_carts_and_spots(self):
        spots = []
        rows = sl.driveway_rows(drives_along(60, gap=4.0), [], (0, 0, 400, 400), flat, ll(), us=True, spots=spots)
        carts = [r for r in rows if r[0] in (sl.G_CART_TRASH, sl.G_CART_RECYCLE)]
        self.assertTrue(2 <= len(carts) <= 30, len(carts))
        self.assertEqual(len([r for r in carts if r[0] == sl.G_CART_TRASH]),
                         len([r for r in carts if r[0] == sl.G_CART_RECYCLE]))
        self.assertTrue(20 <= len(spots) <= 60, len(spots))
        for s in spots:     # on the drive, nosing in (north: 90 deg), clear of the road's edge and the sidewalk
            self.assertEqual(len(s), 5)
            self.assertEqual(s[4], 1)
            self.assertAlmostEqual(s[3], 90.0, places=1)
            self.assertGreaterEqual(s[1] - sl.SPOT_LEN_M / 2, 104.0 + sl.NO_WALK_SETBACK_M - 1e-6)
            self.assertLessEqual(s[1] + sl.SPOT_LEN_M / 2, 118.0 - sl.HOUSE_GAP_M + 1e-6)

    def test_a_short_drive_has_no_room_for_a_car(self):
        spots = []
        sl.driveway_rows(drives_along(40, gap=6.0, length=7.0), [], (0, 0, 400, 400), flat, ll(), spots=spots)
        self.assertEqual(spots, [])

    def test_the_same_whatever_the_anchor(self):
        """Hash keys in degrees: another anchor's frame (the same places shifted) gives the same choices."""
        a = sl.driveway_rows(drives_along(40, gap=6.0), [], (0, 0, 400, 400), flat, ll(), spots=[])
        shift = np.array([1234.5, -876.25])
        moved = [{"w": d["w"], "n": [[p[0] - shift[0], p[1] - shift[1], 0.0] for p in d["n"]]} for d in drives_along(40, gap=6.0)]
        b = sl.driveway_rows(moved, [], (-1234.5, 876.25, -834.5, 1276.25), flat, ll(shift), spots=[])
        self.assertEqual([r[0] for r in a], [r[0] for r in b])
        for ra, rb in zip(a, b):
            self.assertAlmostEqual(ra[1] - shift[0], rb[1], places=1)

    def test_only_the_tiles_own(self):
        rows = sl.driveway_rows(drives_along(), [], (0, 0, 100, 256), flat, ll())
        self.assertTrue(rows and all(r[1] < 100 for r in rows))


class BusStopTest(unittest.TestCase):
    def test_sign_at_the_curb_and_the_shelter_behind(self):
        lines = [(np.array([(-100.0, 100.0), (400.0, 100.0)]), 6.0, "secondary", True)]
        stops = [(120.0, 108.0, True, False), (200.0, 93.0, False, True), (50.0, 160.0, True, True)]
        rows = sl.bus_stop_rows(stops, lines, BOX, flat)
        signs = sorted((r for r in rows if r[0] == sl.G_BUS_SIGN), key=lambda r: r[1])
        self.assertEqual(len(signs), 2)                     # (the third: 54 m off the road)
        self.assertAlmostEqual(signs[0][2], 106.0 + sl.BUS_CURB_M, places=2)
        self.assertAlmostEqual(signs[1][2], 94.0 - sl.BUS_CURB_M, places=2)
        self.assertEqual(len([r for r in rows if r[0] == sl.G_BUS_SHELTER]), 1)
        self.assertEqual(len([r for r in rows if r[0] == furniture.G_BENCH]), 1)
        shelter = next(r for r in rows if r[0] == sl.G_BUS_SHELTER)
        self.assertGreater(shelter[2], signs[0][2] + 2.0)

    def test_parked_cars_keep_out_of_the_stop(self):
        sign = [sl.G_BUS_SIGN, 100.0, 50.0, 0.0, 0.0, 1.0, 3.0, 0.0]
        spots = [[x, 48.0, 0.0, 0.0] for x in (80.0, 92.0, 100.0, 108.0, 120.0)]
        kept = parking.clear_of(spots, [sign], [])
        self.assertEqual([s[0] for s in kept], [80.0, 120.0])


class DowntownTest(unittest.TestCase):
    def street(self, fid, cls="secondary"):
        pts = np.array([(-300.0, 50.0, 0.0), (900.0, 50.0, 0.0)])
        r = (fid, roads.Spec(14.0, "paved", True, cls, False), pts, 0.0)
        walks = []
        for side in (1, -1):
            line = roads.offset_polyline(pts, side * (7.0 + 2.0))
            walks.append({"fid": fid, "pts": line, "side": side, "w": 4.0, "caps": (False, False)})
        return r, walks

    def test_a_dense_core_only(self):
        r, walks = self.street("d1")
        self.assertEqual(sl.downtown_rows([r], walks, (-300, 0, 900, 256), us=True, town=True), [])
        rows = sl.downtown_rows([r], walks, (-300, 0, 900, 256), us=True, town=False)
        groups = {g for g, *_ in rows}
        self.assertIn(sl.G_USPS, groups)
        self.assertIn(sl.G_BIKE_RACK, groups)
        for g, x, y, *_ in rows:
            self.assertTrue(41.0 <= y <= 59.0 and not 43.0 < y < 57.0, (g, y))     # on the sidewalks
        self.assertFalse({g for g, *_ in sl.downtown_rows([r], walks, (-300, 0, 900, 256), us=False, town=False)}
                         & {sl.G_USPS})

    def test_planters_where_no_street_trees(self):
        found = {}
        for k in range(40):
            fid = f"d{k}"
            trees = furniture._h01(fid, "trees") < furniture.STREET_TREE_SHARE
            r, walks = self.street(fid)
            rows = sl.downtown_rows([r], walks, (-300, 0, 900, 256), town=False)
            found.setdefault(trees, set()).update(g for g, *_ in rows)
        self.assertNotIn(sl.G_PLANTER, found[True])
        self.assertIn(sl.G_PLANTER, found[False])


class ClearTest(unittest.TestCase):
    def test_off_roads_and_out_of_buildings(self):
        rows = [[sl.G_MAILBOX, x, y, 0, 0, 1, 1.25, 0] for x, y in ((10.0, 101.0), (10.0, 104.6), (10.0, 120.0))]
        self.assertEqual([r[2] for r in sl.off_roads(rows, street_line())], [104.6, 120.0])
        ring = np.array([(0.0, 115.0), (20.0, 115.0), (20.0, 130.0), (0.0, 130.0)])
        self.assertEqual([r[2] for r in sl.out_of(rows, [ring])], [101.0, 104.6])

    def test_a_bus_stop_at_a_junction_moves_off_the_crossing_street(self):
        lines = street_line(cls="secondary") + [(np.array([(100.0, 0.0), (100.0, 250.0)]), 5.0, "secondary", True)]
        rows = sl.bus_stop_rows([(101.0, 106.0, False, False)], lines, BOX, flat)
        self.assertEqual(len(rows), 1)
        self.assertTrue(sl._Segs(lines).clear([(rows[0][1], rows[0][2])], 0.5)[0])

    def test_clear_of_props(self):
        rows = [[sl.G_MAILBOX, 10.0, 10.0, 0, 0, 1, 1.25, 0], [sl.G_MAILBOX, 20.0, 10.0, 0, 0, 1, 1.25, 0]]
        props = [["hydrant", 10.5, 10.2, 0, 0, 1, 0.9, 0]]
        self.assertEqual([r[1] for r in sl.clear_of(rows, props)], [20.0])
        self.assertEqual(sl.clear_of(rows, []), rows)


class CatalogTest(unittest.TestCase):
    def test_every_group_shown_and_switched_by_the_setting(self):
        src = TREES.read_text(encoding="utf-8")
        m = re.search(r"M\.STREET_LIFE = \{([^}]*)\}", src)
        self.assertIsNotNone(m)
        listed = set(re.findall(r"'(\w+)'", m.group(1)))
        placed = set(sl.GROUPS) - {sl.G_CAR}       # (driveway cars: static_cars, not on yet)
        self.assertEqual(listed, placed)
        for g in placed:
            self.assertRegex(src, rf"M\.CATALOG\.{g} = ")
        # the rows' heights are the catalog's: scale 1
        heights = {sl.G_MAILBOX: sl.MAILBOX_H, sl.G_CBU: sl.CBU_H, sl.G_CART_TRASH: sl.CART_H,
                   sl.G_CART_RECYCLE: sl.CART_H, sl.G_BUS_SIGN: sl.BUS_SIGN_H, sl.G_BUS_SHELTER: sl.BUS_SHELTER_H}
        for g, h in heights.items():
            line = re.search(rf"M\.CATALOG\.{g} = (.*)", src).group(1)
            for v in re.findall(r"', ([\d.]+) \}", line):
                self.assertAlmostEqual(float(v), h, places=3, msg=g)

    def test_own_models_built(self):
        shapes = TREES.parents[4] / "levels" / "beamng_earth" / "art" / "shapes" / "beamngEarth"
        for name in ("bne_mailbox", "bne_mailbox_b", "bne_mailbox_cbu", "bne_cart_trash", "bne_cart_recycle"):
            self.assertTrue((shapes / f"{name}.dae").exists(), name)
        self.assertTrue((shapes / "street_props.materials.json").exists())


if __name__ == "__main__":
    unittest.main()
