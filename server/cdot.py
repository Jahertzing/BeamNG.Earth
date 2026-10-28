"""CDOT's highway geometrics (Colorado): through lanes, auxiliary and managed lanes, turn lanes, shoulders and lane
widths of every state and US highway and interstate, joined onto the road features they lie under (feature
lane-sources, 2026-10-28; on by default in Colorado: --cdot on, BNE_CDOT=1 in every server process).

OpenStreetMap's lane counts on Colorado's highways are patchy (CO-52 through Frederick: lanes=2 on a road whose
pavement is 13-18 m; I-25's express lanes unmapped or one lane), and the pavement width alone cannot tell a shoulder or
a turn lane from a lane. CDOT surveys its whole network yearly ("Highways: Geometrics", the open data service's layer
12: dtdapps.codot.gov Webapps/open_data_sde MapServer/12): per stretch of route (ROUTE "025A", reference points REFPT
..ENDREFPT, the line digitized in increasing milepoint order, CDOT's "primary" direction) the through lanes each way
(PRI/SECTHRULNQTY), their width (THRULNWD, ft), the auxiliary lanes between interchanges (PRI/SECAUXLNQTY), the left
turn lanes (PRI/SECLTURNQTY), the median (MEDIAN: "54  Channelized - Painted", "52  Channelized - Raised Curb", "55
HOV Reversible", ...), the outer and inner shoulders (PRIOUTSHLDWD, PRIINSHLDWD, ft) and the managed lanes (MAL
"Exclusive", MALNQTY lanes, MALALIGN "Both": one express lane each way beside the general lanes, "Median": a reversible
roadway in the median).

What it gives (match()): per road feature that lies along a CDOT line (each of its samples within MATCH_M of it and
running with it within MATCH_DEG; a carriageway of a divided highway within MATCH_ONEWAY_M), the lanes as roads.py
draws them: cdot_lanes_f / cdot_lanes_b (along / against the feature's geometry; a one-way carriageway its own
direction's only, its auxiliary lanes included), cdot_lanes_c (a painted median with left turn lanes: a two-way centre
turn lane), cdot_lane_w (m), cdot_shoulder (m, each side of the drawn lanes) and cdot_express (a managed lanes
carriageway: OSM names them "... Express Lane(s)" or tags them reversible; its lanes are MALNQTY). Kept as float
columns of the roads layer (mapdata: "roads.<regional tag>.cdot7"), credited as "CDOT Highway Geometrics".

Requests: the layer's ArcGIS REST query (GeoJSON, 2,000 features a page) per z14 map cell, through netfetch (host
dtdapps.codot.gov), cached per cell under <raw>/cdot/geometrics/ (refreshed with TAG when CDOT's yearly update is
wanted). Licence: CDOT's open data (public; credited anyway: this fork is for fun, the user's choice).
"""
import json
import logging
import math
import os
import threading
import urllib.parse
from collections import OrderedDict

import numpy as np

import staging

log = logging.getLogger("earthd.mapdata")

ENV = "BNE_CDOT"
TAG = "cdot7"
HOST = "dtdapps.codot.gov"
GEOMETRICS = f"https://{HOST}/server/rest/services/Webapps/open_data_sde/MapServer/12/query"
EXTENT = (-109.06, 36.99, -102.04, 41.01)      # Colorado
PAGE = 2000
DATASET, LICENSE = "CDOT Highway Geometrics", "CDOT open data"
FT = 0.3048
FIELDS = ("ROUTE", "REFPT", "ENDREFPT", "ISDIVIDED", "PRITHRULNQTY", "SECTHRULNQTY", "THRULNWD", "PRIAUXLNQTY",
          "SECAUXLNQTY", "PRILTURNQTY", "SECLTURNQTY", "MEDIAN", "MEDIANWD", "PRIOUTSHLDWD", "SECOUTSHLDWD",
          "PRIINSHLDWD", "SECINSHLDWD", "MAL", "MALNQTY", "MALALIGN")
NUMERIC = ("REFPT", "ENDREFPT", "PRITHRULNQTY", "SECTHRULNQTY", "THRULNWD", "PRIAUXLNQTY", "SECAUXLNQTY",
           "PRILTURNQTY", "SECLTURNQTY", "MEDIANWD", "PRIOUTSHLDWD", "SECOUTSHLDWD", "PRIINSHLDWD", "SECINSHLDWD",
           "MALNQTY")
MARGIN_DEG = 0.0006         # a cell's query box grown by this (~60 m): a road at its edge still finds its line
MATCH_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary"}     # (links: CDOT's ramps carry no lanes)
MATCH_M = 14.0              # a two-way road's samples within this of the line ...
MATCH_ONEWAY_M = 34.0       # ... a carriageway's (I-25's 55 ft median: each centre line ~15-20 m off CDOT's) ...
MATCH_MOTORWAY_M = 50.0     # ... a motorway's (CDOT's line lies on one carriageway in places: I-25 at Frederick, 42 m)
INTERSTATES = ("025", "070", "076", "225", "270")   # their stretches match motorways and trunks only (not a frontage road)
MATCH_DEG = 20.0            # ... and running with it within this
SAMPLES = tuple(np.linspace(0.05, 0.95, 19))  # where along a feature it is matched: all of them inside the cell's
MIN_SAMPLES = 3             # fetched lines (MARGIN_DEG round it) must, and at least this many (a 7 km segment is in
                            # several cells: each matches its own part)
LANE_W_RANGE = (2.7, 4.3)   # m: a lane width outside this is not believed (the class's)
SHOULDER_MAX_M = 4.0        # m drawn at most (a 14 ft shoulder is 4.3 m of asphalt; the edge strip beyond is the photo)
PAINTED_MEDIANS = ("54", "12")      # MEDIAN codes of a painted median: with left turn lanes, a two-way turn lane
EXPRESS_SHOULDER_M = 0.6     # m beside a managed lanes roadway's lanes (its buffer and barrier)
EXPRESS_INSIDE_M = 3.0       # m nearer CDOT's centre line than the general carriageway beside it ...
EXPRESS_BESIDE_M = 40.0     # ... and this near it: an express roadway
EXPRESS_WORDS = ("express", "hov", "managed lane", "toll lane")
COLUMNS = ("cdot_lanes_f", "cdot_lanes_b", "cdot_lanes_c", "cdot_lane_w", "cdot_shoulder", "cdot_express")
MEM_CELLS = 48


def _cross2(u, v):
    """The z of u x v for 2-D vectors (NumPy 2 refuses np.cross on 2-D vectors)."""
    return float(u[0] * v[1] - u[1] * v[0])


def enabled():
    return os.environ.get(ENV, "1") != "0"


def covers(bounds):
    lon0, lat0, lon1, lat1 = bounds
    x0, y0, x1, y1 = EXTENT
    return lon0 <= x1 and lon1 >= x0 and lat0 <= y1 and lat1 >= y0


class Cdot:
    """The geometrics per map cell (fetched once, cached)."""

    def __init__(self, raw_dir, fetcher):
        self.dir = os.path.join(raw_dir, "cdot", "geometrics")
        self.fetcher = fetcher
        self._lock = threading.Lock()
        self._mem = OrderedDict()

    def _query(self, bounds, kind):
        out, offset = [], 0
        while True:
            q = {"geometry": ",".join(repr(float(v)) for v in bounds), "geometryType": "esriGeometryEnvelope",
                 "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outFields": ",".join(FIELDS),
                 "outSR": "4326", "geometryPrecision": "7", "orderByFields": "OBJECTID", "resultOffset": str(offset),
                 "resultRecordCount": str(PAGE), "f": "geojson"}
            body = self.fetcher.get(f"{GEOMETRICS}?{urllib.parse.urlencode(q)}", kind=kind).body
            d = json.loads(body)
            if d.get("error"):
                raise ValueError(f"CDOT geometrics: {d['error']}")
            got = d.get("features") or []
            out += got
            if len(got) < PAGE:
                return out
            offset += len(got)

    def geometrics(self, key, kind="demand"):
        """{"coords" (n, 2) lon/lat, "off" (k + 1), "route" (k,), "median", "mal", "malalign" (k,) text, and the
        NUMERIC fields (k,) float (NaN unknown)} of the stretches meeting the cell (one record per line part)."""
        path = os.path.join(self.dir, str(key.z), str(key.x), f"{key.y}.npz")
        with self._lock:
            got = self._mem.get(path)
            if got is not None:
                self._mem.move_to_end(path)
                return got
        try:
            with np.load(path, allow_pickle=False) as z:
                got = {k: z[k] for k in z.files}
        except (OSError, ValueError):
            lon0, lat0, lon1, lat1 = key.bounds()
            m = MARGIN_DEG
            got = parse(self._query((lon0 - m, lat0 - m, lon1 + m, lat1 + m), kind))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp.npz"
            np.savez_compressed(tmp, **got)
            staging.replace_cached(tmp, path)
        with self._lock:
            self._mem[path] = got
            while len(self._mem) > MEM_CELLS:
                self._mem.popitem(last=False)
        return got


def parse(feats):
    """geometrics' arrays from the query's GeoJSON features (sorted by route and reference point). Pure."""
    recs = []
    for f in feats:
        g = f.get("geometry") or {}
        lines = ([g.get("coordinates")] if g.get("type") == "LineString" else
                 g.get("coordinates") or [] if g.get("type") == "MultiLineString" else [])
        p = f.get("properties") or {}
        for ln in lines:
            if ln and len(ln) >= 2:
                recs.append((str(p.get("ROUTE") or ""), _num(p.get("REFPT")), p, np.asarray(ln, np.float64)[:, :2]))
    recs.sort(key=lambda r: (r[0], r[1] if np.isfinite(r[1]) else -1.0, float(r[3][0, 0]), float(r[3][0, 1])))
    off = np.r_[0, np.cumsum([len(r[3]) for r in recs])].astype(np.int64)
    out = {"coords": np.concatenate([r[3] for r in recs]) if recs else np.zeros((0, 2)), "off": off,
           "route": np.array([r[0] for r in recs], dtype="<U16"),
           "median": np.array([str(r[2].get("MEDIAN") or "") for r in recs], dtype="<U48"),
           "mal": np.array([str(r[2].get("MAL") or "") for r in recs], dtype="<U24"),
           "malalign": np.array([str(r[2].get("MALALIGN") or "") for r in recs], dtype="<U24")}
    for k in NUMERIC:
        out[k] = np.array([_num(r[2].get(k)) for r in recs], np.float64)
    return out


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return x if np.isfinite(x) else float("nan")


def _iv(g, k, j, default=0):
    v = float(g[k][j])
    return int(round(v)) if np.isfinite(v) and v >= 0 else default


def _express(name, oneway_tag):
    n = (name or "").lower()
    return any(w in n for w in EXPRESS_WORDS) or (oneway_tag or "").lower() == "reversible"


def lanes_of(g, j, along, oneway, express):
    """{column: value} of CDOT stretch j for a feature running along (+1) or against (-1) its line; oneway: a one-way
    carriageway (its own direction only); express: a managed lanes roadway. Pure."""
    lw = _iv(g, "THRULNWD", j, 12) * FT
    lw = lw if LANE_W_RANGE[0] <= lw <= LANE_W_RANGE[1] else float("nan")
    pri, sec = _iv(g, "PRITHRULNQTY", j), _iv(g, "SECTHRULNQTY", j)
    apri, asec = _iv(g, "PRIAUXLNQTY", j), _iv(g, "SECAUXLNQTY", j)
    if express:
        n = _iv(g, "MALNQTY", j)
        if n <= 0:
            return None
        # (MALALIGN "Both": n each way, one roadway each side; "Median": a reversible roadway of n, all one way)
        return {"cdot_lanes_f": float(n), "cdot_lanes_b": 0.0, "cdot_lanes_c": 0.0, "cdot_lane_w": lw,
                "cdot_shoulder": EXPRESS_SHOULDER_M, "cdot_express": 1.0}
    if oneway:
        mine, aux = (pri, apri) if along > 0 else (sec, asec)
        if mine <= 0:
            return None
        aux += _iv(g, "PRILTURNQTY" if along > 0 else "SECLTURNQTY", j)    # (its left turn lanes on its median side)
        k = "PRI" if along > 0 else "SEC"
        outer = _num(g[f"{k}OUTSHLDWD"][j]) * FT
        inner = _num(g[f"{k}INSHLDWD"][j]) * FT
        sh = np.nanmean([outer, inner]) if np.isfinite(outer) or np.isfinite(inner) else float("nan")
        return {"cdot_lanes_f": float(mine + aux), "cdot_lanes_b": 0.0, "cdot_lanes_c": 0.0, "cdot_lane_w": lw,
                "cdot_shoulder": float(min(sh, SHOULDER_MAX_M)) if np.isfinite(sh) else float("nan"),
                "cdot_express": 0.0}
    if pri + sec <= 0:
        return None
    # left turn lanes: a painted median with turn bays is drawn as one centre turn lane (a second turn lane of a
    # direction beside it in that direction's lanes); elsewhere (a raised median, none) each in its direction's lanes
    tp, ts = _iv(g, "PRILTURNQTY", j), _iv(g, "SECLTURNQTY", j)
    med = str(g["median"][j]).strip()[:2]
    centre = 1.0 if med in PAINTED_MEDIANS and tp + ts > 0 else 0.0
    if centre:
        tp, ts = max(0, tp - 1), max(0, ts - 1)
    f, b = (pri + apri + tp, sec + asec + ts) if along > 0 else (sec + asec + ts, pri + apri + tp)
    outer = np.nanmean([_num(g["PRIOUTSHLDWD"][j]), _num(g["SECOUTSHLDWD"][j])]) * FT
    return {"cdot_lanes_f": float(f), "cdot_lanes_b": float(b), "cdot_lanes_c": centre, "cdot_lane_w": lw,
            "cdot_shoulder": float(min(outer, SHOULDER_MAX_M)) if np.isfinite(outer) else float("nan"),
            "cdot_express": 0.0}


def _samples(P):
    """[(point (2,), unit direction (2,))] at SAMPLES of a polyline P (k, 2) in metres."""
    seg = np.diff(P, axis=0)
    L = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.r_[0.0, np.cumsum(L)]
    out = []
    if cum[-1] < 1.0:
        return out
    for s in SAMPLES:
        t = s * cum[-1]
        k = int(min(np.searchsorted(cum, t, side="right") - 1, len(L) - 1))
        while L[k] < 1e-6 and k > 0:
            k -= 1
        if L[k] < 1e-6:
            continue
        d = seg[k] / L[k]
        out.append((P[k] + d * (t - cum[k]), d))
    return out


def _nearest_on(p, P):
    """(m, point) from point p to polyline P (k, 2): the distance and the nearest point."""
    a, b = P[:-1], P[1:]
    d = b - a
    t = np.clip(((p - a) * d).sum(1) / np.maximum((d * d).sum(1), 1e-9), 0.0, 1.0)
    q = a + d * t[:, None]
    dist = np.hypot(*(p - q).T)
    k = int(np.argmin(dist))
    return float(dist[k]), q[k]


def _managed(g, j):
    return str(g["mal"][j]).lower() not in ("", "none") and _iv(g, "MALNQTY", j) > 0


def match(feats, g):
    """{column: float array (len feats), NaN where no stretch matches} of the road Features feats (lon/lat) against
    geometrics g (Cdot.geometrics). A feature takes the stretch nearest its middle sample. Where the stretch has managed
    lanes, a carriageway is the express roadway when OSM names it so (EXPRESS_WORDS, or tags it reversible) or when it
    runs inside another carriageway of the same direction (within EXPRESS_BESIDE_M of it and nearer the other
    direction's carriageway, or CDOT's line without one: I-25
    north of Denver maps them as unnamed route segments, and in the Gap the unnamed ones are the general lanes); the
    general lanes take the managed ones in when no express roadway runs beside them. Deterministic. Pure."""
    import roads
    n = len(feats)
    out = {c: np.full(n, np.nan) for c in COLUMNS}
    if len(g["off"]) < 2 or n == 0:
        return out
    lat0 = float(np.mean(g["coords"][:, 1]))
    lon0 = float(np.mean(g["coords"][:, 0]))
    kx, ky = 111320.0 * math.cos(math.radians(lat0)), 110540.0
    coords, off = (g["coords"] - [lon0, lat0]) * [kx, ky], g["off"]
    starts = np.repeat(np.arange(len(off) - 1), np.diff(off))
    same = np.r_[starts[1:] == starts[:-1], False][:-1]
    A, B, rec = coords[:-1][same], coords[1:][same], starts[:-1][same]
    D = B - A
    box_lo, box_hi = coords.min(0) - MATCH_MOTORWAY_M, coords.max(0) + MATCH_MOTORWAY_M   # (where the lines speak)
    interstate = np.array([str(g["route"][r])[:3] in INTERSTATES for r in rec], bool)
    L2 = np.maximum((D * D).sum(1), 1e-9)
    Ld = np.sqrt(L2)
    cos_min = math.cos(math.radians(MATCH_DEG))
    cls = feats.attrs.get("class")
    sub = feats.attrs.get("subclass")
    names = feats.attrs.get("name")
    ow_tag = feats.attrs.get("osm_oneway")
    subtype = feats.attrs.get("subtype")
    found = {}          # feature -> (stretch, along, oneway, named express, mid point, offset from the line, polyline)
    for i in range(n):
        c = str(cls[i]) if cls is not None else ""
        if c not in MATCH_CLASSES or (sub is not None and str(sub[i]) == "link"):
            continue
        if subtype is not None and str(subtype[i]) != "road":
            continue
        parts = feats.parts(i)
        if not parts or not parts[0] or len(parts[0][0]) < 2:
            continue
        P = (np.asarray(parts[0][0], np.float64) - [lon0, lat0]) * [kx, ky]
        smp = [(p, u) for p, u in _samples(P) if (p >= box_lo).all() and (p <= box_hi).all()]
        if len(smp) < MIN_SAMPLES:
            continue
        tag = str(ow_tag[i]) if ow_tag is not None else ""
        oneway = roads._rule_value(feats.rules.get("oneway"), i) is not None or             tag.lower() in ("yes", "1", "-1", "true", "reversible")
        reach = (MATCH_MOTORWAY_M if c in ("motorway", "trunk") else MATCH_ONEWAY_M) if oneway else MATCH_M
        interstate_ok = c in ("motorway", "trunk")
        lo, hi = P.min(0) - reach, P.max(0) + reach
        near = np.flatnonzero(((np.maximum(A, B) >= lo) & (np.minimum(A, B) <= hi)).all(1))
        if not len(near):
            continue
        got, along, offs = [], [], []
        for p, u in smp:
            a, d = A[near], D[near]
            t = np.clip(((p - a) * d).sum(1) / L2[near], 0.0, 1.0)
            q = a + d * t[:, None]
            dist = np.hypot(*(p - q).T)
            cosang = (d @ u) / Ld[near]
            ok = (dist <= reach) & (np.abs(cosang) >= cos_min)
            if not interstate_ok:
                ok &= ~interstate[near]
            if not ok.any():
                got = None
                break
            k = int(np.flatnonzero(ok)[np.argmin(dist[ok])])
            got.append(int(rec[near[k]]))
            along.append(1 if cosang[k] > 0 else -1)
            offs.append(_cross2(d[k] / Ld[near[k]], p - q[k]))
        if not got or len(set(along)) != 1:
            continue
        ex = _express(str(names[i]) if names is not None else "", tag)
        mid = len(got) // 2
        found[i] = (got[mid], along[mid], oneway, ex, smp[mid][0], offs[mid], P)
    def offset(pt, route):
        """m (signed) of a point from the nearest of route's lines."""
        on = np.flatnonzero(g["route"][rec] == route)
        a, d = A[on], D[on]
        t = np.clip(((pt - a) * d).sum(1) / L2[on], 0.0, 1.0)
        q = a + d * t[:, None]
        k = int(np.argmin(np.hypot(*(pt - q).T)))
        return _cross2(d[k] / Ld[on[k]], pt - q[k])
    # the express roadways among the carriageways under managed lanes (see the docstring): compared at one place (its
    # middle and the nearest point of the other: CDOT's line is not always the median's middle)
    express = {i for i, f in found.items() if f[3]}
    beside = set()
    for i, (j, al, ow, ex, p, o, P) in found.items():
        if not ow or ex or not _managed(g, j):
            continue
        route = str(g["route"][j])
        for k, (j2, al2, ow2, ex2, p2, o2, P2) in found.items():
            if k == i or not ow2 or al2 != al or str(g["route"][j2]) != route:
                continue
            dist, q2 = _nearest_on(p, P2)
            if dist > EXPRESS_BESIDE_M:
                continue
            # inside: nearer the other direction's carriageway than the general one is there (CDOT's line lies on one
            # carriageway in places: I-25 at 120th Ave); without the other direction's, nearer CDOT's line
            opp = [_nearest_on(p, f[6])[0] for m, f in found.items()
                   if f[2] and f[1] != al and str(g["route"][f[0]]) == route]
            if opp:
                d_me = min(opp)
                d_it = min(_nearest_on(q2, f[6])[0] for m, f in found.items()
                           if f[2] and f[1] != al and str(g["route"][f[0]]) == route)
                inside = d_me + EXPRESS_INSIDE_M <= d_it and d_me <= 3 * EXPRESS_BESIDE_M
            else:
                o2 = offset(q2, route)
                inside = abs(o) + EXPRESS_INSIDE_M <= abs(o2) and o * o2 > 0
            if inside:
                express.add(i)              # (inside a general carriageway of its direction)
                beside.add(k)
                break
    for i, (j, al, ow, ex, p, o, P) in found.items():
        if i in express and not _managed(g, j):
            continue                        # (an express lane CDOT does not list here)
        vals = lanes_of(g, j, al, ow, i in express)
        if vals is None:
            continue
        if i not in express and ow and _managed(g, j) and i not in beside and                 str(g["malalign"][j]).lower().startswith("both") and not any(
                    k in express and found[k][1] == al for k in found):
            vals["cdot_lanes_f"] += _iv(g, "MALNQTY", j)     # (the express lane on the same pavement)
        for k_, v in vals.items():
            out[k_][i] = v
    return out


def join(feats, g):
    """feats with the match columns (float; NaN none) and how many matched. In place."""
    import mapgeom as mg
    cols = match(feats, g)
    for c, v in cols.items():
        feats.attrs[c] = mg.float_column([None if not np.isfinite(x) else float(x) for x in v])
    return feats, int(np.isfinite(cols["cdot_lanes_f"]).sum())
