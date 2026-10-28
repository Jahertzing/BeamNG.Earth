"""Road lane and width overrides the player edits (feature lane-sources, 2026-10-28): the first of the lane sources
(roads.py: these, CDOT's highway geometrics, OpenStreetMap's tags checked against DRCOG's pavement, lanes from the
pavement, the class's rule).

The file: <cache-dir>/road_overrides.json (the server's --cache-dir, default ~/.cache/beamng-earth; or the path in
BNE_ROAD_OVERRIDES), read once per process. The server puts its content's hash in BNE_ROAD_OVERRIDES_SHA, so the cached
road tiles and road specs (the revisit cache keys hold every BNE_* value) are built again after an edit; it takes effect
at the next start of the server. Its form:

    {"roads": [
      {"way": 26834512, "lanes": 4},
      {"name": "Highway 52", "bbox": [-104.99, 40.080, -104.92, 40.095],
       "lanes:forward": 1, "lanes:backward": 1, "centre_turn_lane": true, "shoulder": 2.5}
    ]}

An entry matches a road by its OpenStreetMap way id ("way"), or by its name ("name", any case; "ref" is the same:
Overture keeps a route's name, e.g. "Highway 52") with an optional "bbox" [lon0, lat0, lon1, lat1] its middle lies in
and an optional "class" ("primary"...). It sets any of "lanes" (both ways), "lanes:forward" / "lanes:backward" (along /
against the way's direction), "centre_turn_lane" (true / false), "width" (m, the whole carriageway; else the lanes at
the class's lane width plus the shoulders) and "shoulder" (m each side). The first matching entry wins. A bad file is
logged and left out.
"""
import hashlib
import json
import logging
import os
import threading

import numpy as np

log = logging.getLogger("earthd.roads")

ENV_PATH = "BNE_ROAD_OVERRIDES"
ENV_SHA = "BNE_ROAD_OVERRIDES_SHA"
FILE = "road_overrides.json"
KEYS = ("lanes", "lanes:forward", "lanes:backward", "centre_turn_lane", "width", "shoulder")
LANES_RANGE = (0, 12)
WIDTH_RANGE = (2.0, 60.0)

_lock = threading.Lock()
_loaded = {}            # path -> entries


def path(cache_dir=None):
    """The override file's path: BNE_ROAD_OVERRIDES, else <cache_dir>/road_overrides.json."""
    p = os.environ.get(ENV_PATH)
    if p:
        return p
    return os.path.join(cache_dir or os.path.expanduser("~/.cache/beamng-earth"), FILE)


def file_sha(p):
    """The file's content hash (short), "" when there is none."""
    try:
        with open(p, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:16]
    except OSError:
        return ""


def parse(data):
    """The valid entries of the file's JSON (a dict with "roads", or a list). Pure."""
    rows = data.get("roads") if isinstance(data, dict) else data
    out = []
    for e in rows if isinstance(rows, list) else []:
        if not isinstance(e, dict):
            continue
        way = e.get("way")
        name = e.get("name", e.get("ref"))
        if way is None and not name:
            continue
        r = {"way": int(way) if isinstance(way, (int, float, str)) and str(way).strip().isdigit() else None,
             "name": str(name).strip().lower() if name else None,
             "class": str(e["class"]).lower() if e.get("class") else None, "bbox": None}
        bb = e.get("bbox")
        if isinstance(bb, list) and len(bb) == 4:
            try:
                r["bbox"] = tuple(float(v) for v in bb)
            except (TypeError, ValueError):
                continue
        for k in ("lanes", "lanes:forward", "lanes:backward"):
            v = e.get(k)
            if isinstance(v, (int, float)) and LANES_RANGE[0] <= v <= LANES_RANGE[1]:
                r[k] = int(v)
        if isinstance(e.get("centre_turn_lane", e.get("center_turn_lane")), bool):
            r["centre_turn_lane"] = e.get("centre_turn_lane", e.get("center_turn_lane"))
        for k in ("width", "shoulder"):
            v = e.get(k)
            if isinstance(v, (int, float)) and (WIDTH_RANGE[0] <= v <= WIDTH_RANGE[1] if k == "width" else 0 <= v <= 10):
                r[k] = float(v)
        if any(k in r for k in KEYS):
            out.append(r)
    return out


def entries(p=None):
    """The entries of the override file (cached per path), [] without one."""
    p = p or path()
    with _lock:
        if p in _loaded:
            return _loaded[p]
    try:
        with open(p, encoding="utf-8") as f:
            got = parse(json.load(f))
        log.info("road overrides: %d entries from %s", len(got), p)
    except FileNotFoundError:
        got = []
    except (OSError, ValueError) as e:
        log.warning("road overrides %s left out: %s: %s", p, type(e).__name__, e)
        got = []
    with _lock:
        _loaded[p] = got
    return got


def match(entries_, way, name, cls, mid):
    """The first entry for a road (way id or None, name, class, middle (lon, lat)), or None. Pure."""
    nm = (name or "").strip().lower()
    for e in entries_:
        if e["way"] is not None:
            if way != e["way"]:
                continue
        elif e["name"] != nm or not nm:
            continue
        if e["class"] and e["class"] != (cls or "").lower():
            continue
        if e["bbox"] is not None:
            x0, y0, x1, y1 = e["bbox"]
            if not (x0 <= mid[0] <= x1 and y0 <= mid[1] <= y1):
                continue
        return e
    return None


def for_features(feats, entries_=None):
    """Per feature of a roads layer: its override entry or None."""
    import osmtags
    es = entries() if entries_ is None else entries_
    n = len(feats)
    if not es:
        return [None] * n
    recs = feats.attrs.get("src_record")
    names = feats.attrs.get("name")
    cls = feats.attrs.get("class")
    out = []
    for i in range(n):
        parts = feats.parts(i)
        if not parts or not parts[0] or len(parts[0][0]) < 1:
            out.append(None)
            continue
        ll = np.asarray(parts[0][0], np.float64)
        out.append(match(es, osmtags.way_id(str(recs[i])) if recs is not None else None,
                         str(names[i]) if names is not None else "", str(cls[i]) if cls is not None else "",
                         ll[len(ll) // 2]))
    return out
