"""Raised pavement markers (roads.rpm_rows): along a motorway's lane and yellow lines, on the ground under them."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import grading  # noqa: E402
import roadmesh  # noqa: E402
import roads  # noqa: E402
from test_roads import FRAME  # noqa: E402


def flat(h):
    return lambda la, lo: np.full(np.shape(la), h, np.float64)


class RpmTest(unittest.TestCase):
    def test_markers_along_lines(self):
        """Every RPM_STEP_M from RPM_START_M along a lane line, white; amber on a yellow line; none on a white edge
        line; on the graded ground under the markings, along the line."""
        lane = np.array([[0.0, 0.0, 5.0], [100.0, 0.0, 5.0]])
        yellow = np.array([[0.0, 10.0, 5.0], [0.0, 60.0, 5.0]])
        edge = np.array([[0.0, 20.0, 5.0], [100.0, 20.0, 5.0]])
        rows = roads.rpm_rows(FRAME, flat(5.0), [("line_dashed_long", lane), ("line_yellow", yellow),
                                                 ("line_white", edge)])
        white = [r for r in rows if r[0] == "rpm_white"]
        amber = [r for r in rows if r[0] == "rpm_amber"]
        self.assertEqual(len(white), len(np.arange(roads.RPM_START_M, 100.0, roads.RPM_STEP_M)))
        self.assertEqual(len(amber), len(np.arange(roads.RPM_START_M, 50.0, roads.RPM_STEP_M)))
        self.assertAlmostEqual(white[0][1], roads.RPM_START_M, places=1)
        self.assertAlmostEqual(white[1][1] - white[0][1], roads.RPM_STEP_M, places=1)
        self.assertTrue(all(abs(r[2]) < 1e-6 and abs(r[4]) < 1e-6 for r in white))
        self.assertTrue(all(abs(r[4] - 90.0) < 1e-6 for r in amber))
        gz = FRAME.geodetic_to_enu(*FRAME.enu_to_geodetic(np.array([white[0][1]]), np.array([0.0]), np.zeros(1))[:2],
                                   np.array([5.0]))[2][0]
        self.assertAlmostEqual(white[0][3], gz + grading.ROAD_LIFT + roadmesh.LIFT["mark"], places=2)

    def test_on_a_deck_its_own_height(self):
        """A line well over the ground under it (a bridge deck): the markers at the line's own height."""
        deck = np.array([[0.0, 0.0, 30.0], [60.0, 0.0, 30.0]])
        rows = roads.rpm_rows(FRAME, flat(5.0), [("line_dashed_long", deck)])
        self.assertTrue(rows and all(abs(r[3] - (30.0 + roadmesh.LIFT["mark"])) < 0.05 for r in rows))

    def test_short_and_none(self):
        self.assertEqual(roads.rpm_rows(FRAME, flat(0.0), []), [])
        self.assertEqual(roads.rpm_rows(FRAME, flat(0.0), [("line_dashed_long", np.array([[0, 0, 0], [5, 0, 0.0]]))]),
                         [])


if __name__ == "__main__":
    unittest.main()
