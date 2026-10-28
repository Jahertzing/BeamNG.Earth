#!/usr/bin/env python3
"""A regional table of the OpenStreetMap highway tags OSM US's Layercake file does not carry (server/osmtags.py
REGIONAL_KEYS: turn:lanes, lanes:forward / backward / both_ways, centre_turn_lane, placement, width:lanes,
parking:*, sidewalk:*, cycleway:*), built from a Geofabrik extract (https://download.geofabrik.de/, ODbL), for the
road cells inside it (osmtags.Regional: joined by way id like Layercake's tags; cells outside every table are built
exactly as before).

    pip install osmium                      # (pyosmium: not a server requirement, this tool only)
    python tools/osm_tag_table.py --pbf colorado-latest.osm.pbf
    python tools/osm_tag_table.py --download https://download.geofabrik.de/north-america/us/colorado-latest.osm.pbf

One pass over the extract's ways (no node locations: ~1 min and little memory for a US state). The table goes to
<cache>/mapdata/raw/osmregions/<region>.npz: the extract's bounding box (its header's, or --bbox), its replication
timestamp (the table's tag: a road cell built with it is kept under its own name, mapdata.MapData.path, so a newer
extract rebuilds the cells it covers), the way ids that have one of the keys (sorted) and one (row, key, value) entry
per tag. The server picks tables up when it starts.
"""
import argparse
import os
import re
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import osmtags  # noqa: E402


def read_extract(path, keys=osmtags.REGIONAL_KEYS):
    """(header bbox (lon0, lat0, lon1, lat1) or None, timestamp text, {way id: {key: value}}) of the highways of an
    .osm.pbf / .osm file with one of `keys` (exact keys)."""
    import osmium
    want = set(keys)
    hdr = osmium.io.Reader(path, osmium.osm.osm_entity_bits.NOTHING).header()
    box = None
    try:
        b = hdr.box()
        if b.valid():
            box = (b.bottom_left.lon, b.bottom_left.lat, b.top_right.lon, b.top_right.lat)
    except Exception:
        box = None
    stamp = hdr.get("osmosis_replication_timestamp") or hdr.get("timestamp") or ""
    out = {}
    for w in osmium.FileProcessor(path, osmium.osm.WAY):
        tags = w.tags
        if "highway" not in tags:
            continue
        got = {t.k: t.v for t in tags if t.k in want}
        if got:
            out[int(w.id)] = got
    return box, stamp, out


def write_table(path, region, box, stamp, ways, keys=osmtags.REGIONAL_KEYS):
    """The table file (osmtags.Regional reads it), written atomically."""
    ids = np.array(sorted(ways), np.int64)
    kidx = {k: i for i, k in enumerate(keys)}
    rows, ks, vals = [], [], []
    for r, w in enumerate(ids.tolist()):
        for k, v in sorted(ways[w].items(), key=lambda kv: kidx[kv[0]]):
            rows.append(r)
            ks.append(kidx[k])
            vals.append(v)
    tag = re.sub(r"[^A-Za-z0-9]+", "", f"{region}{stamp}")[:48] or region
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, region=np.array(region), tag=np.array(tag), stamp=np.array(stamp),
                        bbox=np.array(box, np.float64), keys=np.array(list(keys)), ids=ids,
                        row=np.array(rows, np.int32), key=np.array(ks, np.int16),
                        val=np.array(vals, dtype=f"<U{max([1] + [len(v) for v in vals])}"))
    os.replace(tmp, path)
    return tag


def download(url, dest):
    """url saved to dest (a .part file renamed at the end); prints its size first."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "BeamNG.Earth osm_tag_table"})
    with urllib.request.urlopen(req, timeout=60) as r:
        size = int(r.headers.get("Content-Length") or 0)
        print(f"downloading {url} ({size / 1e6:.0f} MB) to {dest}")
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest + ".part", "wb") as f:
            got = 0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                got += len(b)
    os.replace(dest + ".part", dest)
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pbf", help="a Geofabrik .osm.pbf (or any .osm / .osm.pbf extract)")
    src.add_argument("--download", metavar="URL", help="download this extract first (into the cache's osmregions)")
    ap.add_argument("--region", help="the table's name (default: the file's, without -latest.osm.pbf)")
    ap.add_argument("--bbox", help="lon0,lat0,lon1,lat1 (as --bbox=-105,37,-102,41): the area the table covers "
                                   "(default: the extract's header box)")
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/beamng-earth"))
    a = ap.parse_args(argv)
    out_dir = os.path.join(a.cache_dir, "mapdata", "raw", osmtags.REGIONAL_DIR)
    path = a.pbf or download(a.download, os.path.join(out_dir, "src", a.download.rsplit("/", 1)[-1]))
    region = a.region or re.sub(r"(-latest)?\.osm(\.pbf)?$", "", os.path.basename(path))
    t0 = time.perf_counter()
    box, stamp, ways = read_extract(path)
    if a.bbox:
        box = tuple(float(v) for v in a.bbox.split(","))
    if box is None:
        ap.error("the extract has no bounding box in its header: give --bbox")
    dest = os.path.join(out_dir, f"{region}.npz")
    tag = write_table(dest, region, box, stamp, ways)
    n = sum(len(t) for t in ways.values())
    print(f"{region}: {len(ways)} highway ways with {n} tags, bbox {box}, stamp {stamp or '-'}, tag {tag}; "
          f"{time.perf_counter() - t0:.1f} s -> {dest}")


if __name__ == "__main__":
    main()
