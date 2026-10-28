"""tools/probe_roads_ai.py's pure parts: the AI's lane centres (lane_points: the stock AI's lanes across an edge's half
width) and how far a point is inside the drawn pavement (pavement, with a turn pocket's widened edge)."""
import sys
import unittest
from collections import namedtuple
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import probe_roads_ai  # noqa: E402

S = namedtuple("S", "width cls")


class LanePoints(unittest.TestCase):
    def test_two_way_lanes_either_side_of_the_line(self):
        pts = probe_roads_ai.lane_points((0, 0), (10, 0), 3.25, 2, 5.0)
        ys = sorted({round(float(q[1]), 3) for _, q in pts})
        self.assertEqual(ys, [-1.625, 1.625])        # '-' lane left (north of an eastward edge), '+' right
        self.assertEqual(len(pts), 2 * 3)

    def test_one_lane_on_the_line(self):
        pts = probe_roads_ai.lane_points((0, 0), (0, 8), 2.0, 1, 4.0)
        self.assertTrue(all(abs(float(q[0])) < 1e-9 for _, q in pts))

    def test_degenerate_edge(self):
        self.assertEqual(probe_roads_ai.lane_points((1, 1), (1, 1.05), 3.0, 2, 2.0), [])


class Pavement(unittest.TestCase):
    def setUp(self):
        self.line = np.array([[0.0, 0.0], [100.0, 0.0]])

    def test_inside_and_outside(self):
        drawn = [("a", S(8.0, "tertiary"), self.line, None)]
        inside, fid, _spec, d, half, side = probe_roads_ai.pavement(np.array([50.0, 3.0]), drawn)[0]
        self.assertAlmostEqual(inside, 1.0)
        self.assertEqual((fid, half, side), ("a", 4.0, 1.0))
        self.assertAlmostEqual(probe_roads_ai.pavement(np.array([50.0, -5.5]), drawn)[0][0], -1.5)

    def test_widened_edge_counts(self):
        # a pocket widening the right edge by 3 m at the far end (roads.widen_edges: left, right per point)
        drawn = [("a", S(8.0, "primary"), self.line, (np.zeros(2), np.array([0.0, 3.0])))]
        self.assertAlmostEqual(probe_roads_ai.pavement(np.array([100.0, -6.0]), drawn)[0][0], 1.0)
        self.assertAlmostEqual(probe_roads_ai.pavement(np.array([0.0, -6.0]), drawn)[0][0], -2.0)

    def test_most_inside_first(self):
        other = np.array([[0.0, 10.0], [100.0, 10.0]])
        drawn = [("a", S(8.0, "tertiary"), self.line, None), ("b", S(6.0, "residential"), other, None)]
        self.assertEqual(probe_roads_ai.pavement(np.array([50.0, 9.0]), drawn)[0][1], "b")


if __name__ == "__main__":
    unittest.main()
