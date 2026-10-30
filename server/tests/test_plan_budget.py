"""grading.plan_budget: the cell plans every Grader of a process holds within PLANS_MB, the least recently used
forgotten (Grader._forget_cell) and planned again when needed; the bake profile's smaller grading caches."""
import os
import sys
import threading
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import budget  # noqa: E402
import grading  # noqa: E402
import regiongrade  # noqa: E402


def plan(n=10):
    xy = np.zeros((n, 2), np.float64)
    xy[:, 0] = np.arange(n, dtype=np.float64)
    return grading.Plan(xy, np.zeros(n, np.float64), 4.0)


class FakeGrader:
    def __init__(self):
        self.lock = threading.Lock()
        self.cells = {}
        self.forgotten = []

    def _forget_cell(self, key):
        self.forgotten.append(key)
        self.cells.pop(key, None)


class PlanBudgetTest(unittest.TestCase):
    def test_least_recently_used_cells_go_first(self):
        b = grading.PlanBudget()
        g1, g2 = FakeGrader(), FakeGrader()
        with mock.patch.object(grading, "PLANS_MB", 1):      # 1 MiB
            kb = 300 * 1024
            b.note(g1, "a", kb)
            b.note(g1, "b", kb)
            b.note(g2, "c", kb)
            self.assertEqual((g1.forgotten, g2.forgotten, b.evicted), ([], [], 0))
            b.touch(g1, "a")                                 # a used again: b is now the oldest
            b.note(g2, "d", kb)                              # 1200 KB > 1024: the oldest goes
            self.assertEqual(g1.forgotten, ["b"])
            self.assertEqual(g2.forgotten, [])
            self.assertEqual(b.bytes, 3 * kb)
            b.note(g1, "a", kb)                              # noted again: its size replaced, not added
            self.assertEqual(b.bytes, 3 * kb)
            b.drop(g2)                                       # g2 let go: its cells leave the budget
            self.assertEqual(b.bytes, kb)
            self.assertEqual(sorted(k for _, k in b.lru), ["a"])
            del g1
            b.note(g2, "e", 2 * kb)                          # over the budget, but the oldest's grader is gone: skipped
            self.assertNotIn("a", g2.forgotten)
            self.assertEqual(b.evicted, 1)

    def test_a_grader_forgets_a_cell_and_plans_it_again(self):
        import geodesy
        key = (14, 3413, 6217)
        # (the plans are its planning region's planner's since upstream 1.7.2: grading.PlanStore)
        g = grading.Grader(geodesy.EnuFrame(40.0, -105.0), None, lambda la, lo: 0.0).store.planner_of_cell(key)
        p = plan()
        g.cells[key] = [("f1", p)]
        g.keeps[key] = []
        g.covers[key] = []
        g.opens[key] = []
        g._plan_sets = {((str(key),), False): ([p], None), ((str((1, 2, 3)),), False): ([], None)}
        self.assertTrue(g.planned(key))
        g._forget_cell(key)
        self.assertFalse(g.planned(key), "the warm loop plans it again (from the shared cache)")
        self.assertNotIn(key, g.cells)
        self.assertEqual(list(g._plan_sets), [((str((1, 2, 3)),), False)], "the merged sets holding it went too")
        self.assertEqual(grading.plans_nbytes([("f", p)], [(p.xy, 1.0)], [], [(p.xy, None)]),
                         p.xy.nbytes + p.z.nbytes + 2 * p.xy.nbytes)
        # _plan_cell notes a kept cell into the budget, _cell_plans touches it
        budget_keep = grading.plan_budget
        try:
            grading.plan_budget = grading.PlanBudget()
            g2 = grading.Grader(geodesy.EnuFrame(40.0, -105.0), None, lambda la, lo: 0.0).store.planner_of_cell(key)
            with mock.patch.object(g2, "_make_cell", return_value=([("f", p)], [], [], [])):
                got = g2._cell_plans(key)
            self.assertEqual(len(got), 1)
            self.assertEqual(grading.plan_budget.bytes, grading.plans_nbytes([("f", p)]))
            self.assertIn((id(g2), key), grading.plan_budget.lru)
            g2._cell_plans(key)                              # a hit: touched, nothing added
            self.assertEqual(grading.plan_budget.bytes, grading.plans_nbytes([("f", p)]))
        finally:
            grading.plan_budget = budget_keep

    def test_the_bake_profile_tightens_the_grading_caches(self):
        keep = (grading.PLANS_MB, grading.PLAN_SETS_MAX, grading.ENU_CACHE_MAX, regiongrade.MAX_REGIONS)
        try:
            with mock.patch.dict(os.environ, {"BNGE_BUDGET": "bake"}):
                budget.apply()
                self.assertLessEqual(grading.PLANS_MB, budget.BAKE_PLANS_MB)
                self.assertLessEqual(grading.PLAN_SETS_MAX, budget.BAKE_PLAN_SETS)
                self.assertLessEqual(grading.ENU_CACHE_MAX, budget.BAKE_ENU_CELLS)
                self.assertLessEqual(regiongrade.MAX_REGIONS, budget.BAKE_REGIONS)
        finally:
            grading.PLANS_MB, grading.PLAN_SETS_MAX, grading.ENU_CACHE_MAX, regiongrade.MAX_REGIONS = keep

    def test_a_dropped_region_grader_leaves_the_budget(self):
        budget_keep = grading.plan_budget
        try:
            grading.plan_budget = grading.PlanBudget()
            made = []

            def make(frame):
                g = FakeGrader()
                g.frame = frame
                made.append(g)
                return g
            rg = regiongrade.RegionGrader(make)
            with mock.patch.object(regiongrade, "MAX_REGIONS", 2):
                g1 = rg.grader(400, -1050)
                grading.plan_budget.note(g1, (14, 1, 1), 1000)
                rg.grader(401, -1050)
                rg.grader(402, -1050)                        # the third: g1 dropped
            self.assertEqual(grading.plan_budget.bytes, 0, "g1's cells left with it")
        finally:
            grading.plan_budget = budget_keep


if __name__ == "__main__":
    unittest.main()
