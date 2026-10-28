"""OpenStreetMap road tags (lane count, width, sidewalks, speed limit, lighting, smoothness...) for a map cell's
roads, joined to Overture's road segments by their OpenStreetMap way id (the user, 2026-10-22: "map data sources that
have more info about the roads, lane count, road types").

Overture's transportation theme has no lane count (removed in 2024, not back in schema v2.0.0) and few widths, but
98.6 % of its segments come from OpenStreetMap and keep the way id (sources[1].record_id "w<id>@<version>"). OSM US's
Layercake publishes every OSM highway as one GeoParquet file (https://layercake.openstreetmap.us/layers/highways/,
ODbL, ~29 GB, 2,467 row groups, spatially clustered, rebuilt about weekly), with the tags as columns and a bbox struct.
Like overture.py, only what a cell needs is read, through rangeread (netfetch's limits, the block cache), with DuckDB
reading the blocks: the footer once (9 MB), the bbox columns of the row groups whose statistics touch the cell, then
the tag columns of the row groups that hold a matching way (no geometry). The tag values are parsed in the query
(lanes "2;3" -> 2, width "7 m" / "23'" -> metres, maxspeed "30 mph" -> km/h).

The file is rewritten in place: a version is pinned by its ETag (the cache directory; every range request carries
If-Match, rangeread.Changed when it moved on), and the next version is taken on the next start (version()).

A layer of its own is not needed: mapdata joins the tags into the roads layer when it builds a cell (all processes
read the same cell file, so a road's lanes and width agree everywhere). Unreachable: the cell keeps no tags and the
roads use their width guesses (roads.carriageway), as before.
"""
import json
import logging
import math
import os
import re
import threading
import time

import numpy as np

import netfetch
import overture
import rangeread

log = logging.getLogger("earthd.mapdata")

URL = "https://data.openstreetmap.us/layercake/highways.parquet"
HOST = "data.openstreetmap.us"
VERSION_TTL_S = 7 * 86400       # a pinned version is checked again after this long (the file changes ~weekly)
RETRY_S = 3600                  # the source unreachable: asked again after this long (offline, blocked, gone)
PATH = "osm/highways.parquet"   # (the path DuckDB reads it under)

# The query: OSM way id and the parsed tags (NULL where absent or not understood).
_NUM = r"'^\s*([0-9]+(?:\.[0-9]+)?)'"
COLUMNS = {
    "lanes": f"TRY_CAST(regexp_extract(lanes, '^\\s*([0-9]+)', 1) AS INTEGER)",
    "width": (f"TRY_CAST(regexp_extract(width, {_NUM}, 1) AS DOUBLE) * "
              "CASE WHEN width LIKE '%ft%' OR width LIKE '%''%' THEN 0.3048 ELSE 1.0 END"),
    "maxspeed": (f"TRY_CAST(regexp_extract(maxspeed, {_NUM}, 1) AS DOUBLE) * "
                 "CASE WHEN maxspeed LIKE '%mph%' THEN 1.609344 ELSE 1.0 END"),
    "sidewalk": "sidewalk",
    "lit": "lit",
    "smoothness": "smoothness",
    "osm_surface": "surface",
    "osm_oneway": "oneway",
    "osm_layer": "TRY_CAST(layer AS INTEGER)",
    # for the segments Overture took from a relation (no way id: Samara's Novo-Sadovaya, "r3851280@53", drawn 10 m wide
    # instead of its 6 lanes, the user 2026-10-24): the way of the same name around them (join)
    "name": "name",
    # a road not built yet (GitHub #184: a trunk road under construction, opening 2026-12-31, drawn as a finished road;
    # Overture calls highway=construction "unknown"): only these two values, every other highway NULL
    "osm_highway": "CASE WHEN highway IN ('construction', 'proposed') THEN highway END",
    "bx0": "bbox.xmin", "by0": "bbox.ymin", "bx1": "bbox.xmax", "by1": "bbox.ymax",
}
USED = {"id", "bbox", "lanes", "width", "maxspeed", "sidewalk", "lit", "smoothness", "surface", "oneway", "layer", "name"}
FLOATS = ("lanes", "width", "maxspeed", "osm_layer")
TEXTS = ("sidewalk", "lit", "smoothness", "osm_surface", "osm_oneway", "osm_highway")
UNBUILT = ("construction", "proposed")      # osm_highway's values: not drawn (roads.unbuilt)
NAME_MATCH_M = 25.0     # m a segment's middle may lie outside the bbox of the same-named way it takes the tags of


# Racetracks (GitHub #143): Overture's transportation theme has no highway=raceway (Daytona's oval and road course, the
# Nordschleife: nothing), so the cell's raceways come from the same Layercake file with their geometry: ways that are
# highway=raceway, read only from the row groups whose highway column has one (raceways()).
RACEWAY_COLUMNS = ("id", "highway", "name", "surface", "width", "lanes", "layer", "bridge", "tunnel", "oneway")


# Regional tables (tools/osm_tag_table.py, from Geofabrik extracts): the highway tags Layercake does not carry, which
# turn lanes, uneven lane splits, centre turn lanes, on-street parking and per-side sidewalks need (feature
# turn-lanes). A road cell inside a table's box gets one text column per key ("osm_" + the key with ':' as '_', ""
# where the way has none) and is kept under the table's tag (mapdata.MapData.path); a cell outside every table is
# built exactly as before (no columns).
REGIONAL_DIR = "osmregions"
REGIONAL_KEYS = ("turn:lanes", "turn:lanes:forward", "turn:lanes:backward", "turn:lanes:both_ways",
                 "lanes:forward", "lanes:backward", "lanes:both_ways", "centre_turn_lane",
                 "placement", "placement:forward", "placement:backward",
                 "width:lanes", "width:lanes:forward", "width:lanes:backward",
                 "parking:left", "parking:right", "parking:both",
                 "parking:lane:left", "parking:lane:right", "parking:lane:both",
                 "parking:left:orientation", "parking:right:orientation", "parking:both:orientation",
                 "sidewalk:left", "sidewalk:right", "sidewalk:both",
                 "cycleway", "cycleway:left", "cycleway:right", "cycleway:both")


def regional_column(key):
    """The attrs column of a regional key: "turn:lanes:forward" -> "osm_turn_lanes_forward"."""
    return "osm_" + key.replace(":", "_")


class Regional:
    """The regional tables in <raw>/osmregions/*.npz (tools/osm_tag_table.py), read when first asked for. covering():
    the table a cell lies in, tags(): its ways' tags."""

    def __init__(self, raw_dir):
        self.dir = os.path.join(raw_dir, REGIONAL_DIR)
        self._lock = threading.Lock()
        self._index = None          # [(tag, bbox, path)] sorted by tag
        self._tables = {}           # tag -> (ids, row offsets, keys, values)

    def index(self):
        with self._lock:
            if self._index is None:
                idx = []
                try:
                    names = sorted(n for n in os.listdir(self.dir) if n.endswith(".npz"))
                except OSError:
                    names = []
                for n in names:
                    path = os.path.join(self.dir, n)
                    try:
                        with np.load(path, allow_pickle=False) as z:
                            idx.append((str(z["tag"]), tuple(float(v) for v in z["bbox"]), path))
                    except Exception as e:      # a broken table is left out
                        log.warning("osmtags: regional table %s unreadable: %s: %s", n, type(e).__name__, e)
                self._index = sorted(idx)
            return self._index

    def covering(self, bounds):
        """The tag of the table whose box holds the cell's bounds (lon0, lat0, lon1, lat1) wholly, the first by tag
        when several do; None outside every table."""
        lon0, lat0, lon1, lat1 = bounds
        for tag, (x0, y0, x1, y1), _ in self.index():
            if x0 <= lon0 and lon1 <= x1 and y0 <= lat0 and lat1 <= y1:
                return tag
        return None

    def _table(self, tag):
        with self._lock:
            t = self._tables.get(tag)
        if t is not None:
            return t
        path = next(p for g, _, p in self.index() if g == tag)
        with np.load(path, allow_pickle=False) as z:
            ids, row, key, val, keys = z["ids"], z["row"], z["key"], z["val"], [str(k) for k in z["keys"]]
        off = np.searchsorted(row, np.arange(len(ids) + 1))
        t = (ids, off, key, val, keys)
        with self._lock:
            self._tables[tag] = t
        return t

    def tags(self, tag, way_ids):
        """{way id: {key: value}} of the given ways (those the table has)."""
        ids, off, key, val, keys = self._table(tag)
        want = np.array(sorted({int(w) for w in way_ids if w is not None}), np.int64)
        if not len(want) or not len(ids):
            return {}
        k = np.searchsorted(ids, want)
        k = np.minimum(k, len(ids) - 1)
        hit = ids[k] == want
        out = {}
        for w, r in zip(want[hit].tolist(), k[hit].tolist()):
            out[w] = {keys[int(key[j])]: str(val[j]) for j in range(int(off[r]), int(off[r + 1]))}
        return out


# The turn lanes of a segment Overture merged from several ways (its src_ways: the approach and the pocket way after it)
# are the way's at the end its lanes lead to: turn:lanes:forward the last way's, :backward the first's, turn:lanes (one
# way) both (TURN_START: the first's, for a road drawn against its traffic); every other key the segment's own way's.
TURN_END_KEYS = ("turn:lanes", "turn:lanes:forward")
TURN_START_KEYS = ("turn:lanes:backward",)
TURN_START = "osm_turn_lanes_start"


def join_regional(feats, tags):
    """The regional tags (Regional.tags) of each road feature's way as text columns (regional_column; "" where none),
    for every key in REGIONAL_KEYS, and TURN_START. Only a feature's own ways count (segment_ways: a route relation's
    segment its way among its sources; never the same-named way of join(), which may be the other carriageway, its
    forward / backward lanes the wrong way round)."""
    import mapgeom as mg
    n = len(feats)
    sw = [segment_ways(feats, i) for i in range(n)]
    main = [main_way(feats, i, sw[i]) for i in range(n)]
    first = [min(w, key=lambda x: x[1])[0] if w else None for w in sw]
    last = [max(w, key=lambda x: x[2])[0] if w else None for w in sw]
    for k in REGIONAL_KEYS:
        src = last if k in TURN_END_KEYS else first if k in TURN_START_KEYS else main
        feats.attrs[regional_column(k)] = mg.text_column([tags.get(w, {}).get(k, "") if w is not None else ""
                                                          for w in src])
    feats.attrs[TURN_START] = mg.text_column([tags.get(w, {}).get("turn:lanes", "") if w is not None else ""
                                              for w in first])
    return feats


def segment_ways(feats, i):
    """[(way id, from, to)]: the OSM ways of road feature i and the share of it each gives (its src_ways; a cell built
    without them: its first source's way, the whole of it), [] none."""
    col = feats.attrs.get("src_ways")
    out = []
    if col is not None and str(col[i]).startswith("["):
        try:
            recs = json.loads(str(col[i]))
        except ValueError:
            recs = []
        for r in recs if isinstance(recs, list) else ():
            w = way_id(str(r.get("record_id") or "")) if isinstance(r, dict) else None
            if w is None:
                continue
            b = r.get("between")
            try:
                a0, a1 = (float(b[0]), float(b[1])) if b else (0.0, 1.0)
            except (TypeError, ValueError, IndexError):
                a0, a1 = 0.0, 1.0
            out.append((w, a0, a1))
    if not out:
        recs = feats.attrs.get("src_record")
        w = way_id(str(recs[i])) if recs is not None else None
        if w is not None:
            out.append((w, 0.0, 1.0))
    return out


def join_parts(feats, tags):
    """The osm_parts column: per segment Overture merged from several ways (segment_ways), JSON [[from, to, lanes or
    null], ...] in order along it (Layercake's lanes of each way: tags, join's), "" for the others. roads reads it (its
    pocket over the pocket way's share, its width by part where OSM's lanes are the ones in use)."""
    import mapgeom as mg
    out = []
    for i in range(len(feats)):
        ways = sorted(segment_ways(feats, i), key=lambda x: x[1])
        if len(ways) < 2:
            out.append("")
            continue
        parts = []
        for w, a, b in ways:
            n = tags.get(w, {}).get("lanes")
            parts.append([round(a, 6), round(b, 6), int(n) if n is not None else None])
        out.append(json.dumps(parts, separators=(",", ":")))
    feats.attrs["osm_parts"] = mg.text_column(out)
    return feats


def main_way(feats, i, ways=None):
    """The way of road feature i: its first source's (as before), else (a route relation first) the one of its ways
    that gives most of it; None."""
    recs = feats.attrs.get("src_record")
    w = way_id(str(recs[i])) if recs is not None else None
    if w is not None:
        return w
    ways = segment_ways(feats, i) if ways is None else ways
    return max(ways, key=lambda x: (x[2] - x[1], -x[1]))[0] if ways else None


def way_id(record):
    """The OSM way id of an Overture source record ("w1360453999@2" -> 1360453999), else None."""
    m = re.match(r"^w(\d+)", record or "")
    return int(m.group(1)) if m else None


class OsmTags:
    """Tags by OSM way id per map cell, from the pinned Layercake version."""

    def __init__(self, raw_dir, fetcher, url=URL):
        self.raw_dir, self.fetcher, self.url = raw_dir, fetcher, url
        self._lock = threading.Lock()
        self._remote = None         # (etag, RemoteFile)
        self._checked = 0.0
        self._reader = overture.Reader(raw_dir, fetcher)
        self._meta = None           # (RemoteFile, row groups) of the version in use: _groups

    def version(self, kind="demand"):
        """(etag, RemoteFile) of the version in use: the one pinned on disk while fresh, else the server's."""
        with self._lock:
            if self._remote is not None and time.time() - self._checked < VERSION_TTL_S:
                return self._remote
            pin = os.path.join(self.raw_dir, "osmtags", "pinned")
            etag = size = None
            try:
                with open(pin) as f:
                    etag, size, when = f.read().split("\n")[:3]
                    size, when = int(size), float(when)
                if time.time() - when > VERSION_TTL_S:
                    etag = None
            except (OSError, ValueError):
                etag = None
            if etag is None:
                down = os.path.join(self.raw_dir, "osmtags", "unreachable")
                try:
                    recent = time.time() - os.path.getmtime(down) < RETRY_S
                except OSError:
                    recent = False
                if recent:
                    raise rangeread.RangeError(f"{self.url}: unreachable at the last try")
                try:
                    r = self.fetcher.get(self.url, kind=kind, headers={"Range": "bytes=0-15"})
                    etag = r.headers.get("etag", "").strip()
                    m = rangeread.CONTENT_RANGE.match(r.headers.get("content-range", ""))
                    if not etag or not m or m.group(3) == "*":
                        raise rangeread.RangeError(f"{self.url}: no ETag or size")
                except Exception:
                    os.makedirs(os.path.dirname(down), exist_ok=True)
                    open(down, "w").close()
                    raise
                size = int(m.group(3))
                os.makedirs(os.path.dirname(pin), exist_ok=True)
                with open(pin + ".tmp", "w") as f:
                    f.write(f"{etag}\n{size}\n{time.time()}\n")
                os.replace(pin + ".tmp", pin)
            d = os.path.join(self.raw_dir, "osmtags", re.sub(r"[^A-Za-z0-9]+", "_", etag).strip("_"))
            self._remote = (etag, rangeread.RemoteFile([self.url], size, d, self.fetcher, if_match=etag))
            self._checked = time.time()
            return self._remote

    def _forget(self):
        with self._lock:
            self._remote = None
            try:
                os.remove(os.path.join(self.raw_dir, "osmtags", "pinned"))
            except OSError:
                pass

    def cell(self, bounds, kind="demand"):
        """({way id: {column: value}} of the OSM highways whose bbox touches bounds (lon0, lat0, lon1, lat1), [the
        highway=raceway ways among them: {"id", "coords" (k, 2) lon/lat, tags...}]): one pass for both. A version that
        changed under the reads is dropped and the cell asked once more on the new one."""
        for attempt in (0, 1):
            etag, remote = self.version(kind)
            try:
                return self._cell(remote, bounds, kind)
            except rangeread.Changed:
                if attempt:
                    raise
                log.info("osmtags: %s changed (was %s), taking the new version", self.url, etag)
                self._forget()
        return {}, []

    def _groups(self, con, uri, remote, kind):
        """{row group: {"first", "n", "bbox": {leaf: (min, max)}, "cols": {column: [byte ranges]}}} of the file,
        parsed once per version (0.6 s of every cell's two queries, 2026-10-22: Central Park's first block waited
        90 s for its cells)."""
        with self._lock:
            got = self._meta
        if got is not None and got[0] is remote:
            return got[1]
        tail = remote.read(max(0, remote.size - overture.FOOTER_GUESS), remote.size, kind)
        n = int.from_bytes(tail[-8:-4], "little")
        if n + 8 > len(tail):
            remote.read(remote.size - n - 8, remote.size, kind)
        meta = con.execute(f"""SELECT row_group_id, row_group_num_rows, path_in_schema, stats_min, stats_max,
            dictionary_page_offset, data_page_offset, total_compressed_size FROM parquet_metadata({uri})
            ORDER BY row_group_id, column_id""").fetchall()
        groups, first = {}, 0
        for rg, nrows, col, smin, smax, dict_off, data_off, size in meta:
            g = groups.get(rg)
            if g is None:
                g = groups[rg] = {"first": first, "n": nrows, "bbox": {}, "cols": {}}
                first += nrows
            offs = [o for o in (dict_off, data_off) if o]
            if not offs:
                continue
            top, leaf = col.split(", ")[0], col.split(", ")[-1]
            g["cols"].setdefault(top, []).append((min(offs), min(offs) + size))
            if top == "bbox":
                g["bbox"][leaf] = (smin, smax)
        with self._lock:
            self._meta = (remote, groups)
        return groups

    @staticmethod
    def _candidates(groups, bounds):
        lon0, lat0, lon1, lat1 = bounds

        def stat(g, leaf, i, default):
            try:
                return float(g["bbox"][leaf][i])
            except (KeyError, TypeError, ValueError):
                return default
        return [rg for rg, g in sorted(groups.items())
                if stat(g, "xmin", 0, -1e9) <= lon1 and stat(g, "xmax", 1, 1e9) >= lon0
                and stat(g, "ymin", 0, -1e9) <= lat1 and stat(g, "ymax", 1, 1e9) >= lat0]

    def _cell(self, remote, bounds, kind):
        lon0, lat0, lon1, lat1 = bounds
        rd = self._reader
        files = {PATH: remote}
        con, proto, errors = rd._connect(files, kind)
        try:
            uri = f"'{proto}://{PATH}'"
            groups = self._groups(con, uri, remote, kind)
            cand = self._candidates(groups, bounds)
            if not cand:
                return {}, []
            want = {c for rg in cand for c in groups[rg]["cols"] if c in USED or c == "highway"}
            rd._fetch(files, {PATH: [r for rg in cand for c, rs in groups[rg]["cols"].items() if c in want
                                     for r in rs]}, kind)
            only = " OR ".join(f"file_row_number BETWEEN {groups[rg]['first']} AND "
                               f"{groups[rg]['first'] + groups[rg]['n'] - 1}" for rg in cand)
            box = (f"bbox.xmin <= {lon1!r} AND bbox.xmax >= {lon0!r} AND bbox.ymin <= {lat1!r} "
                   f"AND bbox.ymax >= {lat0!r}")
            cols = ", ".join(f"{expr} AS \"{name}\"" for name, expr in COLUMNS.items())
            # only the ways with a tag the roads use (and the raceways): join() treats a way without one as one not
            # here, and building a dict per way of every highway in the cell was most of this thread's CPU
            # (osmtags, 14 CPU-s of a Deck's teleport, 2026-10-23)
            some = " OR ".join([f"{c} IS NOT NULL" for c in sorted(USED - {"id", "bbox"})]
                               + [f"highway IN ({', '.join(repr(v) for v in UNBUILT)})"])
            rows = con.execute(f"""SELECT id, highway = 'raceway', file_row_number, {cols}
                FROM read_parquet({uri}, file_row_number = true)
                WHERE ({only}) AND {box} AND (highway = 'raceway' OR {some})""").fetchall()
            race = [r[2] for r in rows if r[1]]
            got = []
            if race:            # (rare: the raceways' own columns and geometry, of their row groups only)
                hit = sorted({rg for r in race for rg in cand
                              if groups[rg]["first"] <= r < groups[rg]["first"] + groups[rg]["n"]})
                rd._fetch(files, {PATH: [r for rg in hit for c in RACEWAY_COLUMNS + ("geometry",)
                                         for r in groups[rg]["cols"].get(c, [])]}, kind)
                try:            # (the geometry as its WKB bytes: no spatial extension needed)
                    con.execute("SET enable_geoparquet_conversion = false")
                except Exception:
                    pass
                rcols = ", ".join(f'"{c}"' for c in RACEWAY_COLUMNS)
                rows_in = ", ".join(str(int(r)) for r in race)
                got = con.execute(f"""SELECT {rcols}, geometry FROM read_parquet({uri}, file_row_number = true)
                    WHERE file_row_number IN ({rows_in})""").fetchall()
        except Exception:
            for e in errors:
                if isinstance(e, rangeread.Changed):
                    raise e
            raise
        finally:
            con.close()
        names = list(COLUMNS)
        tags = {int(r[0]): {k: v for k, v in zip(names, r[3:]) if v is not None} for r in rows}
        ways = []
        for row in got:
            coords = _linestring(bytes(row[-1]))
            if coords is None or len(coords) < 2:
                continue
            d = {c: v for c, v in zip(RACEWAY_COLUMNS, row[:-1]) if v is not None}
            d["coords"] = coords
            ways.append(d)
        return tags, ways

RACEWAY_PAVED = {"", "asphalt", "concrete", "paved", "chipseal"}


def raceway_features(ways):
    """The raceways (raceways()) as road Features: class "raceway", ids "osm-raceway-<way id>" (after every Overture
    GERS id: mapgeom.concat appends them), OSM's surface / width / lanes / layer / bridge / tunnel as Overture's rules
    and the osm_* columns."""
    import mapgeom as mg
    ways = sorted(ways, key=lambda w: int(w["id"]))
    if not ways:
        return mg.empty("roads")
    coords, coord_off, flags, levels, surfaces, widths = [], [0], [], [], [], []
    for i, w in enumerate(ways):
        c = np.asarray(w["coords"], np.float64)
        coords.append(c)
        coord_off.append(coord_off[-1] + len(c))
        if str(w.get("bridge") or "no") not in ("no", ""):
            flags.append((i, "is_bridge", None))
        if str(w.get("tunnel") or "no") not in ("no", ""):
            flags.append((i, "is_tunnel", None))
        try:
            lv = float(w.get("layer"))
            if lv:
                levels.append((i, lv, None))
        except (TypeError, ValueError):
            pass
        sf = str(w.get("surface") or "").lower()
        surfaces.append((i, "paved" if sf in RACEWAY_PAVED else sf, None))
        try:
            wd = float(re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)", str(w.get("width") or "")).group(1))
            widths.append((i, wd, None))
        except (AttributeError, ValueError):
            pass
    n = len(ways)
    lonlat = np.concatenate(coords)
    f = mg.Features("roads", mg.text_column([f"osm-raceway-{int(w['id']):012d}" for w in ways]),
                    np.full(n, mg.LINE, np.uint8), np.arange(n + 1, dtype=np.int64), np.arange(n + 1, dtype=np.int64),
                    np.array(coord_off, np.int64), lonlat)
    f.attrs = {"class": mg.text_column(["raceway"] * n), "subtype": mg.text_column(["road"] * n),
               "subclass": mg.text_column([""] * n), "name": mg.text_column([w.get("name") for w in ways]),
               "src_dataset": mg.text_column(["OpenStreetMap"] * n),
               "src_record": mg.text_column([f"w{int(w['id'])}@0" for w in ways]),
               "cx": np.array([float(c[:, 0].min() + c[:, 0].max()) / 2 for c in coords]),
               "cy": np.array([float(c[:, 1].min() + c[:, 1].max()) / 2 for c in coords])}
    f.rules = {"flags": mg.rule_table(flags), "level": mg.rule_table(levels, "float"),
               "surface": mg.rule_table(surfaces), "width": mg.rule_table(widths, "float")}
    f.clipped = np.zeros(n, bool)
    return f


def _linestring(wkb):
    """(k, 2) lon/lat of a WKB LineString (2D, either byte order), else None."""
    import struct
    if len(wkb) < 9:
        return None
    end = "<" if wkb[0] == 1 else ">"
    typ = struct.unpack_from(end + "I", wkb, 1)[0]
    if typ % 1000 != 2 or typ >= 1000 or typ & 0x20000000:
        return None
    n = struct.unpack_from(end + "I", wkb, 5)[0]
    if 9 + 16 * n > len(wkb):
        return None
    return np.frombuffer(wkb, dtype=end + "f8", count=2 * n, offset=9).reshape(n, 2).astype(np.float64)


def _named_way(feats, i, by_name):
    """The OSM way id of the same-named way (by_name: name -> [(way id, lon0, lat0, lon1, lat1)]) whose bbox, grown by
    NAME_MATCH_M, holds feature i's middle vertex: the smallest such, else None."""
    names = feats.attrs.get("name")
    cands = by_name.get(str(names[i])) if names is not None else None
    if not cands:
        return None
    a = feats.coord_off[feats.ring_off[feats.part_off[i]]]
    b = feats.coord_off[feats.ring_off[feats.part_off[i + 1]]]
    if b <= a:
        return None
    lon, lat = (float(v) for v in feats.lonlat[(a + b - 1) // 2][:2])
    my = NAME_MATCH_M / 111320.0
    mx = my / max(math.cos(math.radians(lat)), 0.1)
    best = None
    for w, x0, y0, x1, y1 in cands:
        if x0 - mx <= lon <= x1 + mx and y0 - my <= lat <= y1 + my:
            area = (x1 - x0) * (y1 - y0)
            if best is None or area < best[0]:
                best = (area, w)
    return best[1] if best else None


def join(feats, tags):
    """The tags of each road feature (its source record's way id) as attrs columns: floats (NaN where unknown) for
    FLOATS, text ('' where unknown) for TEXTS. feats: an Overture roads Features (attrs src_record). A segment without a
    known way (from a relation) takes the tags of the same-named way around it (_named_way), but its oneway: that way may
    be one carriageway of the road the segment draws."""
    import mapgeom as mg
    n = len(feats)
    ids = [main_way(feats, i) for i in range(n)]      # (a route relation's segment: its way among its sources)
    by_name = {}
    for w, t in tags.items():
        if t.get("name") and all(k in t for k in ("bx0", "by0", "bx1", "by1")):
            by_name.setdefault(str(t["name"]), []).append((w, t["bx0"], t["by0"], t["bx1"], t["by1"]))
    named = [w if w in tags else _named_way(feats, i, by_name) for i, w in enumerate(ids)]
    for c in FLOATS:
        feats.attrs["osm_" + c if not c.startswith("osm_") else c] = np.array(
            [float(tags.get(w, {}).get(c, np.nan)) if w is not None else np.nan for w in named], np.float64)
    for c in TEXTS:
        src = ids if c == "osm_oneway" else named
        feats.attrs[c if c.startswith("osm_") else "osm_" + c] = mg.text_column(
            [str(tags.get(w, {}).get(c, "")) if w is not None else "" for w in src])
    return feats
