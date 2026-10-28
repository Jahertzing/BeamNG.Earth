"""tools/route_bake.py: the route anchors a drive re-centres on (the mod's rebase rule and routepins.lua's pinning),
shared between routes and both directions, and the files it writes for the mod and the server."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import route_bake as rb  # noqa: E402

# a rough I-25 from Frederick to Castle Rock, and a route that leaves it for downtown streets
I25 = [(40.0992, -104.9372), (40.0990, -104.9870), (39.9150, -104.9870), (39.8000, -104.9880), (39.7500, -105.0150),
       (39.7000, -104.9900), (39.6200, -104.8950), (39.5200, -104.8700), (39.3722, -104.8561)]
DOWNTOWN = I25[:4] + [(39.7650, -105.0000), (39.7530, -105.0002), (39.7400, -104.9900), (39.7000, -104.9900)] + I25[6:]


class AnchorTest(unittest.TestCase):
    def test_snap_as_the_mod(self):
        # test_core_stubbed.lua: the default start 36.596718, -118.169991 snaps to 36.6, -118.17
        self.assertEqual(rb.snap_anchor(36.596718, -118.169991), (36.6, -118.17))
        self.assertEqual(rb.snap_anchor(40.0992, -104.9372), (40.1, -104.94))
        self.assertEqual(rb.snap_anchor(88.5, 12.345), (88.5, 12.345))

    def test_anchors_along_a_route(self):
        anchors, order = rb.plan_anchors({"i25": I25}, 15000.0)
        seq = order["i25"]
        self.assertEqual(seq[0], 0)
        self.assertEqual(anchors[0], rb.snap_anchor(*I25[0]), "the start's grid point")
        for i, j in zip(seq, seq[1:]):      # (one rebase distance apart, give or take the grid and the bends)
            self.assertTrue(10000 < rb.horizontal(anchors[i], anchors[j]) < 16500)
        self.assertTrue(all(rb.snap_anchor(*a) == a for a in anchors), "every anchor a grid point")

    def test_routes_and_directions_share_anchors(self):
        anchors, order = rb.plan_anchors({"i25": I25, "downtown": DOWNTOWN}, 15000.0)
        self.assertEqual(order["downtown"], order["i25"], "downtown is never far from I-25: the same anchors")
        self.assertEqual(set(order["i25<"]) - set(order["i25"]), {len(anchors) - 1}, "back: one more, at the end")
        # a drive that starts elsewhere on the route (Thornton) lands on the planned anchors too
        mid = rb.densify(I25)[300:]
        seq = rb.walk(mid, anchors, 15000.0, rb.PREFETCH_MARGIN, 15000.0 * rb.PIN_SHARE, add=False)
        self.assertNotIn(-1, seq)

    def test_plan_writes_both_copies(self):
        with tempfile.TemporaryDirectory() as d:
            pts = os.path.join(d, "pts.json")
            with open(pts, "w") as f:
                json.dump({"i25": I25, "downtown": DOWNTOWN}, f)
            args = ["--cache-dir", os.path.join(d, "cache"), "--world-dir", os.path.join(d, "world"),
                    "--user-dir", os.path.join(d, "user")]
            self.assertEqual(rb.main(["plan", "--points", pts, "--rebase-m", "15000"] + args), 0)
            with open(os.path.join(d, "user", *rb.MOD_FILE)) as f:
                mod = json.load(f)
            with open(os.path.join(d, "world", "bake", "anchors.json")) as f:     # (the world folder's: kept)
                server = json.load(f)
            self.assertEqual(mod, server)
            self.assertEqual(mod["pinM"], 15000 * rb.PIN_SHARE)
            self.assertEqual(set(mod["routes"]), {"i25", "downtown"})
            import cache
            import revisit
            self.assertFalse(os.path.exists(os.path.join(d, "cache", "bake")))
            cache.set_world(os.path.join(d, "world"), os.path.join(d, "cache"), env=False)    # (the server's)
            try:
                self.assertEqual(revisit.pinned_anchors(os.path.join(d, "cache")),
                                 {(round(a[0], 7), round(a[1], 7)) for a in mod["anchors"]})
            finally:
                cache.set_world(None, None, env=False)


if __name__ == "__main__":
    unittest.main()
