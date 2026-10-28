"""routing: the GPS's whole-route planning (OSRM response handling, simplification)."""
import json
import sys
import unittest
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import routing  # noqa: E402


class RoutingTest(unittest.TestCase):
    def test_simplify_keeps_corners(self):
        line = [(39.0, -76.0 + i * 1e-5) for i in range(50)] + [(39.0 + i * 1e-5, -76.0 + 49e-5) for i in range(1, 50)]
        s = routing.simplify(line)
        self.assertEqual(len(s), 3, "a straight run, a corner, a straight run")
        self.assertEqual(s[0], line[0])
        self.assertEqual(s[-1], line[-1])

    def test_parse(self):
        data = {"code": "Ok", "routes": [{"distance": 1234.5, "duration": 99.0,
                                         "geometry": {"coordinates": [[-76.59, 39.28], [-76.60, 39.28], [-76.60, 39.29]]}}]}
        r = routing.parse(data)
        self.assertEqual(r["points"][0], [39.28, -76.59])
        self.assertEqual((r["dist"], r["dur"]), (1234.5, 99.0))
        with self.assertRaises(ValueError):
            routing.parse({"code": "NoRoute", "routes": []})

    def test_resample_even_steps(self):
        line = [(39.0, -76.0), (39.0, -75.9)]              # ~8.6 km east, one straight segment
        r = routing.resample(line, max_points=20)
        self.assertEqual(r[0][2], 0.0)
        self.assertAlmostEqual(r[-1][0], line[-1][0], places=6)
        self.assertAlmostEqual(r[-1][1], line[-1][1], places=6)
        for a, b in zip(r, r[1:]):
            self.assertGreater(b[2], a[2])

    def test_resample_short_line_one_point(self):
        self.assertEqual(routing.resample([(39.0, -76.0)]), [(39.0, -76.0, 0.0)])
        self.assertEqual(routing.resample([]), [])

    def test_url(self):
        u = routing.url(39.28, -76.59, 38.9, -77.03)
        self.assertIn("-76.590000,39.280000;-77.030000,38.900000", u)
        self.assertTrue(u.startswith("https://routing.openstreetmap.de/"))
        self.assertNotIn("bearings", u)
        self.assertTrue(routing.url(39.28, -76.59, 38.9, -77.03, 370).endswith("&bearings=10,60;"))

    def test_url_via(self):
        # The Drive: Frederick -> Denver Union Station -> Castle Rock, the waypoint between the ends, in order
        u = routing.url(40.1, -104.94, 39.37, -104.86, via=[[39.7527, -105.0], ("bad", 1), [95, 0]])
        self.assertIn("-104.940000,40.100000;-105.000000,39.752700;-104.860000,39.370000?", u)
        # one bearing per coordinate, only the start's set
        self.assertTrue(routing.url(40.1, -104.94, 39.37, -104.86, 180, [[39.7527, -105.0]]).endswith(
            "&bearings=180,60;;"))
        self.assertEqual(len(routing.clean_via([[1, 2]] * 20)), routing.MAX_VIA)
        self.assertEqual(routing.clean_via(None), [])

    def test_url_exclude(self):
        self.assertTrue(routing.url(40.1, -104.94, 39.37, -104.86, exclude="toll").endswith("&exclude=toll"))
        self.assertNotIn("exclude", routing.url(40.1, -104.94, 39.37, -104.86, exclude="bogus"))

    def test_plan_exclude_falls_back_without_it(self):
        import netfetch
        seen = []

        class Net:
            def get(self, u, kind=None):
                seen.append(u)
                if "exclude" in u:
                    raise netfetch.FetchFailed(f"{u}: HTTP 400", u, status=400)
                body = json.dumps({"code": "Ok", "routes": [{"distance": 9.0, "duration": 1.0, "geometry": {
                    "coordinates": [[-104.94, 40.1], [-104.86, 39.37]]}}]}).encode()
                return type("R", (), {"body": body})()
        r = routing.plan(40.1, -104.94, 39.37, -104.86, Net(), None, None, "toll")
        self.assertEqual(r["dist"], 9.0)
        self.assertIn("exclude=toll", seen[0])
        self.assertNotIn("exclude", seen[-1])

    def test_plan_passes_the_via_on(self):
        seen = []

        class Net:
            def get(self, u, kind=None):
                seen.append(u)
                body = json.dumps({"code": "Ok", "routes": [{"distance": 9.0, "duration": 1.0, "geometry": {
                    "coordinates": [[-104.94, 40.1], [-105.0, 39.75], [-104.86, 39.37]]}}]}).encode()
                return type("R", (), {"body": body})()
        r = routing.plan(40.1, -104.94, 39.37, -104.86, Net(), 90, [[39.7527, -105.0]])
        self.assertEqual(len(r["points"]), 3)
        self.assertIn(";-105.000000,39.752700;", seen[0])


if __name__ == "__main__":
    unittest.main()


class RouteProfileSmoothTest(unittest.TestCase):
    def test_spikes_of_a_sample_or_two_go_the_climb_stays(self):
        # (the ground under a bridge, an embankment's bump: the map's chart drew them as peaks, the user 2026-10-27)
        import earthd
        climb = np.linspace(10.0, 40.0, 40)
        z = climb.copy()
        z[10] += 15.0           # a bump one sample long
        z[25:27] -= 12.0        # a valley under a bridge, two samples
        got = earthd.route_profile_smooth(z)
        self.assertLess(float(np.abs(got - climb)[2:-2].max()), 2.0)     # (of 12-15 m spikes)
        self.assertEqual(len(got), len(z))
        self.assertTrue(np.allclose(earthd.route_profile_smooth([5.0, 6.0]), [5.0, 6.0]))
