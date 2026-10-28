"""DRCOG Regional Planimetric Data 2024 (Denver metro): building roofprints and edges of pavement, an optional regional
source (off by default: --drcog on, the environment's BNE_DRCOG=1 in every server process).

The Denver Regional Council of Governments stereo-compiles the region's buildings from its 2024 aerial photography
(DRAPP, 3-12 inch): every roofprint at its eaves with its height (the highest point: a pitched roof's ridge) and
ground elevation in feet and a building type, ~1.5 million of them over -105.40..-104.37, 39.31..40.21; and the
edges of pavement (the curb's flowline). Licence (checked 2026-10-28): the layers' item info says "provided for free
public download from DRCOG's Regional Data Catalog", whose terms (https://data.drcog.org/about) are CC BY 3.0 with
the credit "DRCOG Regional Data Catalog"; the Town of Frederick's open data (its planimetrics come from the same
project) may be copied, distributed and adapted with attribution (https://frederickco.gov/769/Terms-of-Use).

What it changes (mapdata.MapData, a cell whose box meets EXTENT):
  * buildings: the cell's DRCOG roofprints (each roof level a footprint of its own, at its own height), plus the
    Overture buildings no roofprint conflicts with (neither's centroid inside the other): a building the photos have
    is drawn from them, one they miss (outside the capture area, built since) stays; a roofprint takes the
    attributes (class, name, roof shape, floors, facade) of the Overture building it replaces, its height from
    DRCOG. Kept under its own layer name (mapdata: "buildings.drcog2024") and credited as a dataset of the cell
    ("DRCOG Planimetrics 2024", CC-BY-3.0: credits.py lists it with the map's sources);
  * road widths (roads._measured_widths, with --road-widths on): the distance between the nearest edges of pavement
    either side of the road's centreline, the median of samples along it (widths()); the buildings' measure where
    the edges give none.

Requests: the layers' ArcGIS REST query (GeoJSON, 2,000 features a page) per z14 cell, through netfetch (host
gis.drcog.org), cached per cell under <raw>/drcog/2024/ (the data is a fixed release).
"""
import collections
import json
import logging
import math
import os
import threading
import time
import urllib.parse

import numpy as np

import cache
import staging

log = logging.getLogger("earthd.mapdata")

ENV = "BNE_DRCOG"
RELEASE = "2024"
TAG = "drcog2024"
HOST = "gis.drcog.org"
BASE = "https://gis.drcog.org/server/rest/services/RDC"
ROOFPRINTS = f"{BASE}/PLANIMETRICS_2024_BUILDING_ROOFPRINTS_TOTAL/MapServer/0/query"
EDGES = f"{BASE}/PLANIMETRICS_2024_EDGE_PAVEMENT_LINE_TOTAL/MapServer/0/query"
EXTENT = (-105.4019, 39.3086, -104.3702, 40.2129)     # the roofprints' extent (query returnExtentOnly, 2026-10-28)
PAGE = 2000
DATASET, LICENSE = "DRCOG Planimetrics 2024", "CC-BY-3.0"
FT = 0.3048
# cells kept decoded in memory, per kind (a least-recently-used cache each): the whole dict was cleared past 64 cells of
# both kinds together, so on a long Denver drive the road widths' edges were read from disk again and again
MEM_CELLS = {"roofprints": 24, "edges": 64}
MIN_HEIGHT_M = 1.5          # a roofprint's height under this is not believed (the building's Overture height, or none)
# DRCOG's building types -> Overture-like classes (buildings.plan) where no Overture building gives one
TYPE_CLASS = {"Residential": "residential", "Commercial": "commercial", "Industrial": "industrial",
              "Medical": "hospital", "Public": "civic", "Garage/Shed": "garage", "Parking Structure": "parking",
              "Tank": "storage_tank", "Miscellaneous": ""}
SKIP_TYPES = {"Foundation/Ruin"}
# widths: samples along the road, how far across to look, what counts
WIDTH_SAMPLES = (0.25, 0.5, 0.75)
WIDTH_REACH_M = 25.0
WIDTH_RANGE_M = (3.0, 40.0)
WIDTH_MIN_SAMPLES = 2
EDGES_RETRY_S = 60.0        # a cell whose edges could not be fetched: asked again after this (not by every road in it)


def enabled():
    return os.environ.get(ENV) == "1"


def covers(bounds):
    lon0, lat0, lon1, lat1 = bounds
    x0, y0, x1, y1 = EXTENT
    return lon0 <= x1 and lon1 >= x0 and lat0 <= y1 and lat1 >= y0


def _pip(x, y, ring):
    """Is (x, y) inside the ring (k, 2) (even-odd)?"""
    xs, ys = ring[:, 0], ring[:, 1]
    xj, yj = np.roll(xs, 1), np.roll(ys, 1)
    c = ((ys > y) != (yj > y)) & (x < (xj - xs) * (y - ys) / np.where(yj - ys == 0, 1e-30, yj - ys) + xs)
    return bool(np.count_nonzero(c) % 2)


def _ring_centre(ring):
    r = ring[:-1] if len(ring) > 1 and np.allclose(ring[0], ring[-1]) else ring
    return float(r[:, 0].mean()), float(r[:, 1].mean())


class Drcog:
    """The roofprints and edges of pavement per map cell (fetched once, cached)."""

    def __init__(self, raw_dir, fetcher):
        self.dir = os.path.join(raw_dir, "drcog", RELEASE)
        self.fetcher = fetcher
        self._lock = threading.Lock()
        self._mem = {}
        self._edges_failed = {}         # cell key -> time.monotonic() to ask for its edges again

    # --- fetching ---------------------------------------------------------------------------------------------
    def _query(self, url, bounds, fields, kind):
        """Every GeoJSON feature of the layer meeting bounds (lon0, lat0, lon1, lat1), page by page in OBJECTID order."""
        out, offset = [], 0
        while True:
            q = {"geometry": ",".join(repr(float(v)) for v in bounds), "geometryType": "esriGeometryEnvelope",
                 "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outFields": ",".join(fields),
                 "outSR": "4326", "geometryPrecision": "7", "orderByFields": "OBJECTID", "resultOffset": str(offset),
                 "resultRecordCount": str(PAGE), "f": "geojson"}
            body = self.fetcher.get(f"{url}?{urllib.parse.urlencode(q)}", kind=kind).body
            got = json.loads(body).get("features") or []
            out += got
            if len(got) < PAGE:
                return out
            offset += len(got)

    def _cached(self, name, key, build):
        path = os.path.join(self.dir, name, str(key.z), str(key.x), f"{key.y}.npz")
        with self._lock:
            mem = self._mem.setdefault(name, collections.OrderedDict())     # (kind -> path -> arrays: LRU, MEM_CELLS)
            got = mem.get(path)
            if got is not None:
                mem.move_to_end(path)
        if got is not None:
            return got
        try:
            with np.load(cache.resolve(path), allow_pickle=False) as z:
                got = {k: z[k] for k in z.files}
        except (OSError, ValueError):
            got = build()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp.npz"
            np.savez_compressed(tmp, **got)
            staging.replace_cached(tmp, path)
        with self._lock:
            mem[path] = got
            mem.move_to_end(path)
            while len(mem) > MEM_CELLS.get(name, 32):
                mem.popitem(last=False)
        return got

    def roofprints(self, key, kind="demand"):
        """{"oid", "bid", "height" (m, NaN), "ground" (m), "type", "coords" (n, 2) lon/lat, "off" (k + 1)} of the
        cell's roofprints, one record per polygon's outer ring (sorted by OBJECTID)."""
        def build():
            feats = self._query(ROOFPRINTS, key.bounds(), ("OBJECTID", "Building_ID", "Bldg_Height",
                                                           "Ground_Elevation", "Bldg_Type"), kind)
            oid, bid, h, g, typ, coords, off = [], [], [], [], [], [], [0]
            for f in sorted(feats, key=lambda f: int(f.get("id") or f["properties"].get("OBJECTID") or 0)):
                p = f.get("properties") or {}
                geom = f.get("geometry") or {}
                polys = ([geom["coordinates"]] if geom.get("type") == "Polygon" else
                         geom.get("coordinates") or [] if geom.get("type") == "MultiPolygon" else [])
                for k, poly in enumerate(polys):
                    if not poly or len(poly[0]) < 4:
                        continue
                    ring = np.asarray(poly[0], np.float64)[:, :2]
                    oid.append(int(p.get("OBJECTID") or f.get("id") or 0) * 16 + k)
                    bid.append(str(p.get("Building_ID") or ""))
                    hv = p.get("Bldg_Height")
                    h.append(float(hv) * FT if hv not in (None, "") else np.nan)
                    gv = p.get("Ground_Elevation")
                    g.append(float(gv) * FT if gv not in (None, "") else np.nan)
                    typ.append(str(p.get("Bldg_Type") or ""))
                    coords.append(ring)
                    off.append(off[-1] + len(ring))
            return {"oid": np.array(oid, np.int64), "bid": np.array(bid, dtype="<U32"),
                    "height": np.array(h, np.float64), "ground": np.array(g, np.float64),
                    "type": np.array(typ, dtype="<U32"),
                    "coords": np.concatenate(coords) if coords else np.zeros((0, 2)), "off": np.array(off, np.int64)}
        return self._cached("roofprints", key, build)

    def edges(self, key, kind="demand"):
        """{"coords" (n, 2) lon/lat, "off" (k + 1)} of the cell's edge of pavement lines."""
        def build():
            feats = self._query(EDGES, key.bounds(), ("OBJECTID", "Curb"), kind)
            coords, off = [], [0]
            for f in sorted(feats, key=lambda f: int(f.get("id") or 0)):
                geom = f.get("geometry") or {}
                lines = ([geom["coordinates"]] if geom.get("type") == "LineString" else
                         geom.get("coordinates") or [] if geom.get("type") == "MultiLineString" else [])
                for ln in lines:
                    if len(ln) >= 2:
                        coords.append(np.asarray(ln, np.float64)[:, :2])
                        off.append(off[-1] + len(ln))
            return {"coords": np.concatenate(coords) if coords else np.zeros((0, 2)), "off": np.array(off, np.int64)}
        return self._cached("edges", key, build)

    # --- buildings ----------------------------------------------------------------------------------------------
    def merge(self, feats, key, kind="demand"):
        """(the cell's buildings Features with its roofprints in, the roofprints drawn): see merge_buildings."""
        return merge_buildings(feats, self.roofprints(key, kind))

    # --- road widths --------------------------------------------------------------------------------------------
    def widths(self, fallback=None, kind="demand"):
        """A widths(fid, lonlat) function for roads._measured_widths: the edges' width, else fallback's (the
        buildings' measure: roadwidth.RoadWidths.get)."""
        import mapcells
        memo, lock = {}, threading.Lock()

        def measure(fid, lonlat):
            """(width or the fallback's, final): not final when the cell's edges could not be had (asked again)."""
            fb = fallback(fid, lonlat) if fallback is not None else None
            ll = np.asarray(lonlat, np.float64)
            if len(ll) < 2:
                return fb, True
            mid = ll[len(ll) // 2]
            if not covers((mid[0], mid[1], mid[0], mid[1])):
                return fb, True
            key = mapcells.cell_of(float(mid[1]), float(mid[0]))
            with self._lock:
                retry_at = self._edges_failed.get(key)
            if retry_at is not None and time.monotonic() < retry_at:
                import revisit
                revisit.fault()             # (as the failed fetch did: a build that goes on without it is not kept)
                return fb, False
            try:
                e = self.edges(key, kind)
            except Exception as ex:         # (the buildings' measure, as without the source)
                log.debug("drcog edges: %s: %s", type(ex).__name__, ex)
                with self._lock:            # (not every road of the cell asking again: 2,922 requests for 9
                    self._edges_failed[key] = time.monotonic() + EDGES_RETRY_S     # downtown road tiles offline)
                return fb, False
            with self._lock:
                self._edges_failed.pop(key, None)
            w = measure_width(ll, e["coords"], e["off"], edge_index(e))
            if w is None:
                return fb, True
            return (w, fb[1] if fb is not None else 2.5), True

        def get(fid, lonlat):
            # decided once per road per run, as the buildings' measure (roadwidth.RoadWidths.get): every consumer
            # (the ground photos' painted roads, road tiles, the road graph) asks for the same roads again
            with lock:
                if fid in memo:
                    return memo[fid]
            got, final = measure(fid, lonlat)
            if not final:                   # (the edges once they come)
                return got
            with lock:
                return memo.setdefault(fid, got)
        return get


def merge_buildings(feats, roof):
    """(Features, n roofprints): the roofprints as buildings (ids "zdrcog-<oid>", after every Overture id), each with
    the attributes of the Overture building it replaces (one whose centroid lies in it, or that holds its centroid)
    and its own height, with the Overture buildings no roofprint conflicts with, in id order. Pure."""
    import mapgeom as mg
    import overture
    spec = overture.LAYERS["buildings"]
    n_o = len(feats)
    rings_o = []
    for i in range(n_o):
        parts = feats.parts(i)
        rings_o.append(np.asarray(parts[0][0], np.float64) if parts and parts[0] else np.zeros((0, 2)))
    cen_o = np.array([_ring_centre(r) if len(r) >= 3 else (np.nan, np.nan) for r in rings_o]).reshape(-1, 2)
    off, coords = roof["off"], roof["coords"]
    keep_r = [j for j in range(len(off) - 1) if str(roof["type"][j]) not in SKIP_TYPES]
    rings_r = {j: coords[off[j]:off[j + 1]] for j in keep_r}
    cen_r = {j: _ring_centre(r) for j, r in rings_r.items()}
    # a grid of the Overture centroids (~ 50 m) for the candidates of each roofprint
    cell = 0.0005
    grid = {}
    for i, (x, y) in enumerate(cen_o.tolist()):
        if np.isfinite(x):
            grid.setdefault((int(math.floor(x / cell)), int(math.floor(y / cell))), []).append(i)
    replaced = set()
    donor = {}
    # the landmark table's buildings (landmarks.json, by OSM id) keep their own outline and parts: a roofprint over one
    # is left out (the State Capitol's dome is an OSM part; DRCOG has its roof levels, no dome)
    import landmarks
    prot = landmarks.protected_ids()
    recs = feats.attrs.get("src_record")
    prot_rows = {i for i in range(n_o) if prot and recs is not None and landmarks.osm_key(recs[i]) in prot}
    dropped = set()
    for j in keep_r:
        r = rings_r[j]
        x0, y0 = r.min(0)
        x1, y1 = r.max(0)
        cands = set()
        for gx in range(int(math.floor(x0 / cell)) - 1, int(math.floor(x1 / cell)) + 2):
            for gy in range(int(math.floor(y0 / cell)) - 1, int(math.floor(y1 / cell)) + 2):
                cands.update(grid.get((gx, gy), ()))
        cx, cy = cen_r[j]
        for i in sorted(cands):
            ro = rings_o[i]
            if len(ro) < 3:
                continue
            ox, oy = cen_o[i]
            if (x0 <= ox <= x1 and y0 <= oy <= y1 and _pip(ox, oy, r)) or _pip(cx, cy, ro):
                if i in prot_rows:
                    dropped.add(j)
                replaced.add((j, i))
    keep_r = [j for j in keep_r if j not in dropped]
    kept = set(keep_r)
    for j, i in sorted(replaced):
        if j in kept:
            donor.setdefault(j, i)
    replaced = {i for j, i in replaced if j in kept}
    keep_o = [i for i in range(n_o) if i not in replaced]
    ids = [str(feats.ids[i]) for i in keep_o] + [f"zdrcog-{int(roof['oid'][j]):012d}" for j in keep_r]
    geoms = [(feats.kind[i], [[np.asarray(rg, np.float64) for rg in p] for p in feats.parts(i)]) for i in keep_o]
    geoms += [(mg.POLYGON, [[rings_r[j]]]) for j in keep_r]
    order = sorted(range(len(ids)), key=lambda k: ids[k])
    out = mg.build("buildings", mg.text_column([ids[k] for k in order]), [geoms[k] for k in order])
    out.clipped = np.zeros(len(ids), bool)
    nk = len(keep_o)
    # the Overture row of each output (a roofprint's: the building it replaces, or None)
    rows = [keep_o[k] if k < nk else donor.get(keep_r[k - nk]) for k in range(len(ids))]

    def column(name, default):
        a = feats.attrs.get(name)
        return [a[r] if (a is not None and r is not None) else default for r in rows]
    cols = {c: [str(v) for v in column(c, "")] for c in spec.get("text", ())}
    cols.update({c: [float(v) for v in column(c, np.nan)] for c in spec.get("float", ())})
    cols.update({c: [bool(v) for v in column(c, False)] for c in spec.get("bool", ())})
    for m_, j in enumerate(keep_r):                 # the roofprints' own: height, source, no parts, a class if none
        k = nk + m_
        h = float(roof["height"][j])
        if np.isfinite(h) and h >= MIN_HEIGHT_M:
            cols["height"][k] = h
            cols["height_src"][k] = DATASET
        cols["min_height"][k] = np.nan
        cols["has_parts"][k] = False
        cols["src_dataset"][k] = DATASET
        cols["src_record"][k] = f"oid{int(roof['oid'][j]) // 16}"
        if rows[k] is None:
            cols["class"][k] = TYPE_CLASS.get(str(roof["type"][j]), "")
    for c in spec.get("text", ()):
        out.attrs[c] = mg.text_column([cols[c][k] for k in order])
    for c in spec.get("float", ()):
        out.attrs[c] = mg.float_column([None if not np.isfinite(cols[c][k]) else cols[c][k] for k in order])
    for c in spec.get("bool", ()):
        out.attrs[c] = mg.bool_column([cols[c][k] for k in order])
    # (mapdata's `own`: Overture's bbox centre; a roofprint's ring centre is near enough to its bbox's)
    cx = [float(feats.attrs["cx"][i]) if "cx" in feats.attrs else cen_o[i][0] for i in keep_o] +         [cen_r[j][0] for j in keep_r]
    cy = [float(feats.attrs["cy"][i]) if "cy" in feats.attrs else cen_o[i][1] for i in keep_o] +         [cen_r[j][1] for j in keep_r]
    out.attrs["cx"] = np.array([cx[k] for k in order], np.float64)
    out.attrs["cy"] = np.array([cy[k] for k in order], np.float64)
    return out, len(keep_r)


def _segments(coords, off):
    """(a, b) (m, 2) the start and end of every segment of the lines coords / off (no segment across two lines)."""
    if len(coords) < 2:
        return np.zeros((0, 2)), np.zeros((0, 2))
    starts = np.repeat(np.arange(len(off) - 1), np.diff(off))
    same = np.r_[starts[1:] == starts[:-1], False]          # segment k -> k + 1 within one line
    return coords[:-1][same[:-1]], coords[1:][same[:-1]]


# The edges of a cell on a lon / lat grid, so a road's width is measured against the segments near it only: against all
# of a Denver cell's (~10^5) it cost ~5-10 ms a road, the first painted ground photo or road tile of a downtown cell
# measuring thousands of roads (Jeff's drive into Denver 2026-10-28: 15-21 s of "roads" in a block's base photo).
INDEX_CELL_DEG = 0.0005     # ~43 m east-west, ~55 m north-south at 40 N
_GRID_OFF = 1 << 20         # (grid indices made non-negative for the packed key)


class EdgeIndex:
    """A cell's edge-of-pavement segments (lon / lat) by grid square: near(box) -> the indices of every segment whose
    bounding box meets the box's squares (a superset of those that meet the box)."""

    def __init__(self, coords, off):
        self.a, self.b = _segments(np.asarray(coords, np.float64), np.asarray(off, np.int64))
        lo = np.floor(np.minimum(self.a, self.b) / INDEX_CELL_DEG).astype(np.int64)
        hi = np.floor(np.maximum(self.a, self.b) / INDEX_CELL_DEG).astype(np.int64)
        nx, ny = hi[:, 0] - lo[:, 0] + 1, hi[:, 1] - lo[:, 1] + 1
        cnt = nx * ny
        seg = np.repeat(np.arange(len(self.a)), cnt)
        k = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)    # each square of a segment's box
        gx = np.repeat(lo[:, 0], cnt) + k % np.repeat(nx, cnt)
        gy = np.repeat(lo[:, 1], cnt) + k // np.repeat(nx, cnt)
        key = self._key(gx, gy)
        order = np.argsort(key, kind="stable")
        self.keys, self.segs = key[order], seg[order]

    @staticmethod
    def _key(gx, gy):
        return (np.asarray(gx, np.int64) + _GRID_OFF) * (2 * _GRID_OFF) + (np.asarray(gy, np.int64) + _GRID_OFF)

    def near(self, x0, y0, x1, y1):
        cx0, cx1 = int(math.floor(x0 / INDEX_CELL_DEG)), int(math.floor(x1 / INDEX_CELL_DEG))
        cy0, cy1 = int(math.floor(y0 / INDEX_CELL_DEG)), int(math.floor(y1 / INDEX_CELL_DEG))
        parts = []
        for gx in range(cx0, cx1 + 1):          # (a column's squares are one run of the sorted keys)
            i = np.searchsorted(self.keys, self._key(gx, cy0), side="left")
            j = np.searchsorted(self.keys, self._key(gx, cy1), side="right")
            if j > i:
                parts.append(self.segs[i:j])
        if not parts:
            return np.zeros(0, np.int64)
        return np.unique(np.concatenate(parts))


def edge_index(edges):
    """The EdgeIndex of a cell's edges dict (Drcog.edges), made once and kept in it (in memory only)."""
    got = edges.get("_index")
    if got is None:
        got = edges["_index"] = EdgeIndex(edges["coords"], edges["off"])
    return got


def measure_width(ll, coords, off, index=None):
    """The carriageway width (m) of a road (centreline ll (k, 2) lon/lat) between the nearest edge of pavement lines
    either side of it: at WIDTH_SAMPLES of its length, the nearest crossing each side within WIDTH_REACH_M of a line
    square to it; the median of the samples where both sides have one (WIDTH_MIN_SAMPLES at least), else None.
    index (the lines' EdgeIndex): only the segments near the road are looked at (the same result). Pure."""
    if len(coords) == 0 or len(ll) < 2:
        return None
    lat0 = float(ll[:, 1].mean())
    kx, ky = 111320.0 * math.cos(math.radians(lat0)), 110540.0
    lon0 = float(ll[:, 0].mean())
    P = np.c_[(ll[:, 0] - lon0) * kx, (ll[:, 1] - lat0) * ky]
    if index is not None:       # a crossing within the reach of a sample lies within the reach of the road's box
        rx, ry = (WIDTH_REACH_M + 1.0) / kx, (WIDTH_REACH_M + 1.0) / ky
        near = index.near(ll[:, 0].min() - rx, ll[:, 1].min() - ry, ll[:, 0].max() + rx, ll[:, 1].max() + ry)
        a, b = index.a[near], index.b[near]
    else:
        a, b = _segments(np.asarray(coords, np.float64), np.asarray(off, np.int64))
    A = np.c_[(a[:, 0] - lon0) * kx, (a[:, 1] - lat0) * ky]
    B = np.c_[(b[:, 0] - lon0) * kx, (b[:, 1] - lat0) * ky]
    seg = np.diff(P, axis=0)
    L = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.r_[0.0, np.cumsum(L)]
    if cum[-1] < 4.0:
        return None
    got = []
    for f in WIDTH_SAMPLES:
        s = f * cum[-1]
        k = int(min(np.searchsorted(cum, s, side="right") - 1, len(L) - 1))
        if L[k] < 1e-6:
            continue
        t = seg[k] / L[k]
        p = P[k] + t * (s - cum[k])
        n = np.array([-t[1], t[0]])
        # crossings of the line p + u n (|u| <= reach) with every edge segment
        d = B - A
        den = n[0] * d[:, 1] - n[1] * d[:, 0]
        ok = np.abs(den) > 1e-9
        w = A - p
        u = np.where(ok, (w[:, 0] * d[:, 1] - w[:, 1] * d[:, 0]) / np.where(ok, den, 1.0), np.nan)
        v = np.where(ok, (w[:, 0] * n[1] - w[:, 1] * n[0]) / np.where(ok, den, 1.0), np.nan)
        hit = ok & (v >= 0) & (v <= 1) & (np.abs(u) <= WIDTH_REACH_M)
        left, right = u[hit & (u > 0.3)], u[hit & (u < -0.3)]
        if len(left) and len(right):
            got.append(float(left.min() - right.max()))
    got = [g for g in got if WIDTH_RANGE_M[0] <= g <= WIDTH_RANGE_M[1]]
    if len(got) < WIDTH_MIN_SAMPLES:
        return None
    return float(np.median(got))
