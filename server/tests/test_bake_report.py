"""tools/bake_report.py: terrain blocks built or read back (the revisit stage), anchors and far fields from a log."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import bake_report as br  # noqa: E402

LOG = """2026-10-28 10:44:59,001 INFO earthd: terrain (1, 0.0, 0.0) z15 in 9.10 s (heights 2.00 roadPaint 6.00): zBase 1500 maxHeight 300 (Q 1/64)
2026-10-28 10:45:01,002 INFO earthd: terrain (1, 128.0, 0.0) z15 in 8.00 s (heights 2.00 grading 1.00 roadPaint 4.00): zBase 1500 maxHeight 300 (Q 1/64)
2026-10-28 10:45:02,003 INFO earthd: terrain (1, 256.0, 0.0) z15 in 0.21 s (revisit 0.20): zBase 1500 maxHeight 300 (Q 1/64)
2026-10-28 10:45:03,004 INFO earthd: base photo (1, 128.0, 0.0) r0 in 5.40 s: imagery 1.00 buildings 0.40 roads 3.00 rock 0.50, png 0.10, cache 0.40
2026-10-28 10:45:04,005 INFO earthd: anchor 2 (39.97000, -104.99000) ready in 3.20 s, terrain at (0, 0), groundZ 1580.0; dem {}
2026-10-28 10:45:30,006 INFO earthd: anchor 2 far field done: 900 tiles (900 sent, 40 swaps in 26.00 s; build median 12.0 ms)
"""


class ReportTest(unittest.TestCase):
    def test_counts(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "earthd.log")
            with open(p, "w") as f:
                f.write(LOG)
            ev = br.scan([p], since="2026-10-28 10:45")
            self.assertEqual((len(ev["built"]), len(ev["read"]), len(ev["base"])), (1, 1, 1))
            self.assertEqual(ev["built"][0][2], {"heights": 2.0, "grading": 1.0, "roadPaint": 4.0})
            self.assertEqual(ev["anchors"][0][1:4], (2, 39.97, -104.99))
            self.assertEqual(ev["far"][0][2:], (900, 26.0))
            text = br.report(ev)
            self.assertIn("terrain blocks: 2 (1 read back, 1 built)", text)
            self.assertEqual(br.main(["--log", p]), 0)

    def test_drive_lines(self):
        """An autodrive leg's summary and a ghost drive's (km, the waits) both get their line."""
        import json
        with tempfile.TemporaryDirectory() as d:
            for name, s in (("i25_0_ai", {"miles": 10.9, "seconds": 960, "fps": 59.7, "frame_ms": {"p99": 19.1}}),
                            ("i25_ghost", {"km": 93.2, "route_km": 93.2, "seconds": 4100, "fps": 88.0,
                                           "frame_ms": {"p99_worst_tick": 31.0, "max": 70.0},
                                           "frames_over": {"over50": 2, "over100": 0},
                                           "waits": {"count": 12, "seconds": 340.0, "gave_up": 1},
                                           "hitch_causes": {"mod.roads": [2, 70]}})):
                os.makedirs(os.path.join(d, name))
                with open(os.path.join(d, name, "summary.json"), "w") as f:
                    json.dump(s, f)
            text = br.report(br.scan([]), runs=[d])
            self.assertIn("i25_0_ai: 10.9 mi in 960 s, 59.7 fps, p99 19.1 ms", text)
            self.assertIn("i25_ghost: 93.2 of 93.2 km in 4100 s, 88.0 fps, worst p99 31.0 ms, max 70.0 ms, >50 ms 2, "
                          ">100 ms 0; waited 12x 340.0 s (1 gave up); hitches: mod.roads x2 (worst 70 ms)", text)


if __name__ == "__main__":
    unittest.main()
