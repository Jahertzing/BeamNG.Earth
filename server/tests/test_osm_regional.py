"""Regional OSM tag tables (tools/osm_tag_table.py, osmtags.Regional, mapdata's roads cells inside a table)."""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import mapcells  # noqa: E402
import mapdata  # noqa: E402
import osm_tag_table  # noqa: E402
import osmtags  # noqa: E402
import mapfixtures as mf  # noqa: E402
from test_mapdata import Sources, SmallBlocks, needs_duckdb  # noqa: E402

try:
    import osmium  # noqa: F401
    HAVE_OSMIUM = True
except ImportError:
    HAVE_OSMIUM = False

WAYS = {1: {"turn:lanes": "left|through|through;right", "lanes:forward": "3", "lanes:backward": "2",
            "parking:right": "lane"},
        77: {"centre_turn_lane": "yes"},
        5: {"sidewalk:left": "separate", "cycleway:right": "lane"}}

OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <bounds minlat="39.0" minlon="-77.0" maxlat="40.0" maxlon="-76.0"/>
  <node id="1" lat="39.5" lon="-76.5" version="1"/>
  <node id="2" lat="39.6" lon="-76.5" version="1"/>
  <way id="10" version="1"><nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/><tag k="turn:lanes:forward" v="left|through"/><tag k="lanes" v="4"/></way>
  <way id="11" version="1"><nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="residential"/><tag k="name" v="No extra tags"/></way>
  <way id="12" version="1"><nd ref="1"/><nd ref="2"/>
    <tag k="building" v="yes"/><tag k="parking:right" v="lane"/></way>
  <way id="13" version="1"><nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="secondary"/><tag k="parking:both" v="lane"/><tag k="sidewalk:both" v="yes"/></way>
</osm>
"""


class TableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="osmregional-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.raw = os.path.join(self.tmp, "raw")
        self.dir = os.path.join(self.raw, osmtags.REGIONAL_DIR)

    def write(self, name="testland", box=(-77.0, 39.0, -76.0, 40.0), ways=WAYS, stamp="2026-10-27T21:00:00Z"):
        return osm_tag_table.write_table(os.path.join(self.dir, f"{name}.npz"), name, box, stamp, ways)

    def test_table_round_trip(self):
        tag = self.write()
        self.assertEqual(tag, "testland20261027T210000Z")
        r = osmtags.Regional(self.raw)
        self.assertEqual(r.covering((-76.6, 39.4, -76.5, 39.5)), tag)
        self.assertIsNone(r.covering((-76.1, 39.4, -75.9, 39.5)))           # (not wholly inside)
        self.assertEqual(r.tags(tag, [1, 5, 77, 1234, None]), WAYS)
        self.assertEqual(r.tags(tag, []), {})

    def test_join_columns(self):
        tag = self.write()
        r = osmtags.Regional(self.raw)

        class F:
            attrs = {"src_record": np.array(["w1@3", "w9@1", "r55@2", "w77@1"])}

            def __len__(self):
                return 4
        f = osmtags.join_regional(F(), r.tags(tag, [1, 9, 77]))
        self.assertEqual(set(osmtags.regional_column(k) for k in osmtags.REGIONAL_KEYS) - set(f.attrs),
                         set())
        self.assertEqual(list(f.attrs["osm_turn_lanes"]), ["left|through|through;right", "", "", ""])
        self.assertEqual(list(f.attrs["osm_lanes_forward"]), ["3", "", "", ""])
        self.assertEqual(list(f.attrs["osm_centre_turn_lane"]), ["", "", "", "yes"])
        self.assertEqual(osmtags.regional_column("turn:lanes:both_ways"), "osm_turn_lanes_both_ways")

    def test_first_table_by_tag_wins(self):
        a = self.write("b-newer", stamp="2026-10-28")
        b = self.write("a-older", stamp="2026-01-01")
        self.assertEqual(osmtags.Regional(self.raw).covering((-76.6, 39.4, -76.5, 39.5)), min(a, b))

    @unittest.skipUnless(HAVE_OSMIUM, "pyosmium not installed (pip install osmium)")
    def test_tool_reads_an_extract(self):
        src = os.path.join(self.tmp, "testland-latest.osm")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(OSM_XML)
        osm_tag_table.main(["--pbf", src, "--cache-dir", self.tmp.replace("\\", "/") + "/c",
                            "--bbox=-77,39,-76,40"])
        r = osmtags.Regional(os.path.join(self.tmp, "c", "mapdata", "raw"))
        tag = r.covering((-76.6, 39.4, -76.5, 39.5))
        self.assertTrue(tag.startswith("testland"))
        self.assertEqual(r.tags(tag, [10, 11, 12, 13]),
                         {10: {"turn:lanes:forward": "left|through"},
                          13: {"parking:both": "lane", "sidewalk:both": "yes"}})


@needs_duckdb
class MapDataTest(Sources, SmallBlocks, unittest.TestCase):
    def test_cells_inside_a_table_get_its_tags_under_its_own_name(self):
        cache = os.path.join(self.tmp, "cache")
        md, t = self.mapdata(cache=cache)
        plain = md.path(mf.CELL, "roads")
        lon0, lat0, lon1, lat1 = mf.CELL.bounds()
        tag = osm_tag_table.write_table(os.path.join(cache, "mapdata", "raw", osmtags.REGIONAL_DIR, "fx.npz"), "fx",
                                        (lon0 - 1, lat0 - 1, lon1 + 1, lat1 + 1), "s1", WAYS)
        md2, _ = self.mapdata(cache=cache)
        self.assertEqual(md2.path(mf.CELL, "roads"), plain[:-len(".npz")].replace(
            os.sep.join(["roads.6", ""]), os.sep.join([f"roads.6.{tag}", ""])) + ".npz")
        self.assertEqual(md2.path(mf.CELL, "buildings"), md.path(mf.CELL, "buildings"))     # (roads only)
        far = mapcells.cell_of(10.0, 10.0)
        self.assertEqual(md2.path(far, "roads"), md.path(far, "roads"))                     # outside: as before
        r = md2.cell(mf.CELL, ("roads",)).layers["roads"]
        i = r.index("seg-00")                   # (the fixture's segments are all from way 1)
        self.assertEqual(r.attrs["osm_turn_lanes"][i], "left|through|through;right")
        self.assertEqual(r.attrs["osm_parking_right"][i], "lane")
        self.assertEqual(r.attrs["osm_centre_turn_lane"][i], "")
        r0 = md.cell(far, ("roads",)).layers["roads"]
        self.assertNotIn("osm_turn_lanes", r0.attrs)


if __name__ == "__main__":
    unittest.main()
