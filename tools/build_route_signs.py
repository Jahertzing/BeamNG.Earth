#!/usr/bin/env python3
"""A region's numbered-route sign data (server/routesigns.py) from OpenStreetMap through the Overpass API: every way
that carries an Interstate, US or Colorado state highway ref (motorways down to the two-lane state highways, CO 93,
CO 52 and the mountain passes' roads), the mile marker nodes (highway=milestone) mapped along them, the passes
(mountain_pass=yes), the towns' place nodes (their elevations) and the towns' boundaries (admin_level 8), written to
server/hwysigns/<name>-routes.json.gz. The server turns it into mile markers, route shields and junction assemblies
on the highways without numbered exits, pass summit signs and city limit signs when it builds road tiles; nothing
is fetched while driving.

    python tools/build_route_signs.py --region colorado-statewide --name colorado
    python tools/build_route_signs.py --region colorado-statewide --name colorado --check

Fetched in strips of latitude like tools/build_highway_signs.py (each strip's answer cached under
build/hwysigns-cache). OpenStreetMap data (c) OpenStreetMap contributors, ODbL 1.0.
"""
import argparse
import gzip
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, HERE)

import build_highway_signs as bhs    # noqa: E402  (its Overpass fetch, strips and writer)

QUERY = """[out:json][timeout:{timeout}][maxsize:{maxsize}];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential)$"]["ref"~"(^|;) *(CO|SH|US|I)[ -]?[0-9]"]({s},{w},{n},{e});
  node["highway"="milestone"]({s},{w},{n},{e});
  node["mountain_pass"="yes"]({s},{w},{n},{e});
  node["place"~"^(city|town|village)$"]({s},{w},{n},{e});
);
out body geom qt;
rel["boundary"="administrative"]["admin_level"="8"]({s},{w},{n},{e});
out body geom qt;
"""
# the consolidated city-counties (Denver, Broomfield): their boundaries are admin_level 6, the whole state's at once
COUNTY_CITIES_QUERY = """[out:json][timeout:{timeout}][maxsize:{maxsize}];
rel["boundary"="administrative"]["admin_level"="6"]["name"~"^(City and County of )?(Denver|Broomfield)( County)?$"]({s},{w},{n},{e});
out body geom qt;
"""
WAY_KEEP = ("highway", "ref", "oneway", "maxspeed", "lanes", "name", "junction", "bridge", "tunnel")
NODE_KEEP = ("highway", "distance", "ref", "name", "ele", "mountain_pass", "place", "population", "railway")
REL_KEEP = ("name", "border_type", "admin_level", "official_name", "place", "population")
SIMPLIFY_M = 6.0        # a boundary ring's Douglas-Peucker tolerance


def _xy(ring):
    lat0 = sum(p[0] for p in ring) / len(ring)
    k = math.cos(math.radians(lat0)) * 111_195.0
    return [((p[1]) * k, p[0] * 111_195.0) for p in ring]


def simplify(ring, tol=SIMPLIFY_M):
    """Douglas-Peucker on a closed ring of [lat, lon] (tol m), the ring kept closed."""
    if len(ring) < 8:
        return ring
    xy = _xy(ring)
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            d = abs(dx * (ay - py) - dy * (ax - px)) / L if L > 1e-9 else math.hypot(px - ax, py - ay)
            if d > best:
                best, bi = d, i
        if bi >= 0 and best > tol:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    out = [p for p, k in zip(ring, keep) if k]
    return out if len(out) >= 4 else ring


def rings(members):
    """A boundary relation's members (out geom) joined into closed rings: [(role, [[lat, lon], ...])]."""
    parts = {"outer": [], "inner": []}
    for m in members:
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        role = "inner" if m.get("role") == "inner" else "outer"
        parts[role].append([(round(p["lat"], 7), round(p["lon"], 7)) for p in m["geometry"]])
    out = []
    for role, segs in parts.items():
        segs = [s for s in segs if len(s) >= 2]
        while segs:
            cur = segs.pop(0)
            grown = True
            while cur[0] != cur[-1] and grown:
                grown = False
                for i, s in enumerate(segs):
                    if s[0] == cur[-1]:
                        cur = cur + s[1:]
                    elif s[-1] == cur[-1]:
                        cur = cur + s[::-1][1:]
                    elif s[-1] == cur[0]:
                        cur = s + cur[1:]
                    elif s[0] == cur[0]:
                        cur = s[::-1] + cur[1:]
                    else:
                        continue
                    segs.pop(i)
                    grown = True
                    break
            if cur[0] == cur[-1] and len(cur) >= 4:
                out.append((role, [[round(a, 6), round(b, 6)] for a, b in simplify(cur)]))
    return out


def compact(data, region, bbox):
    ways, nodes, towns = [], [], []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        if el["type"] == "way" and el.get("geometry") and el.get("nodes"):
            geom = [[round(p["lat"], 7), round(p["lon"], 7)] for p in el["geometry"]]
            if len(geom) != len(el["nodes"]):
                continue
            ways.append({"id": el["id"], "tags": {k: v for k, v in tags.items() if k in WAY_KEEP},
                         "nodes": el["nodes"], "geom": geom})
        elif el["type"] == "node" and "lat" in el:
            nodes.append({"id": el["id"], "lat": round(el["lat"], 7), "lon": round(el["lon"], 7),
                          "tags": {k: v for k, v in tags.items() if k in NODE_KEEP}})
        elif el["type"] == "relation" and tags.get("boundary") == "administrative":
            rs = rings(el.get("members", ()))
            if rs and tags.get("name"):
                towns.append({"id": el["id"], "tags": {k: v for k, v in tags.items() if k in REL_KEEP},
                              "rings": [{"role": r, "pts": p} for r, p in rs]})
    for lst in (ways, nodes, towns):
        lst.sort(key=lambda e: e["id"])
    return {"v": 1, "kind": "routes", "region": region, "bbox": list(bbox),
            "stamp": data.get("osm3s", {}).get("timestamp_osm_base", ""),
            "source": "OpenStreetMap contributors (ODbL), Overpass API", "ways": ways, "nodes": nodes,
            "towns": towns}


def summary(d):
    import routesigns
    kinds = {}
    for it in routesigns.plan(d):
        kinds[it["k"]] = kinds.get(it["k"], 0) + 1
    return kinds


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--region", help="a region of tools/regions.json (its bounding box)")
    ap.add_argument("--bbox", help="south,west,north,east")
    ap.add_argument("--name", help="the file's name, <name>-routes.json.gz (default: the region's)")
    ap.add_argument("--pad-km", type=float, default=2.0, help="km added round the region's box")
    ap.add_argument("--strip-deg", type=float, default=0.5, help="degrees of latitude per Overpass request")
    ap.add_argument("--check", action="store_true", help="print the existing file's plan counts")
    a = ap.parse_args()
    if a.region:
        import regions
        r = regions.region(regions.load(), a.region)
        s, w, n, e = r.bounds
        pad = a.pad_km / 111.0
        bbox = (round(s - pad, 4), round(w - pad * 1.3, 4), round(n + pad, 4), round(e + pad * 1.3, 4))
        name = a.name or a.region
    elif a.bbox:
        bbox = tuple(float(v) for v in a.bbox.split(","))
        name = a.name or "bbox"
    else:
        ap.error("--region or --bbox")
    path = os.path.join(bhs.OUT_DIR, f"{name}-routes.json.gz")
    if a.check:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        print(f"{path}: {len(d['ways'])} ways, {len(d['nodes'])} nodes, {len(d['towns'])} towns, "
              f"OSM {d.get('stamp', '?')}: {summary(d)}")
        return
    print(f"Overpass: numbered routes, mile markers, passes and towns in {bbox}")
    data = bhs.fetch_strips(bbox, name + "-routes", a.strip_deg, query=QUERY)
    extra = bhs.fetch(bbox, timeout=300, maxsize=256 << 20, query=COUNTY_CITIES_QUERY)
    seen = {(e["type"], e["id"]) for e in data["elements"]}
    data["elements"] += [e for e in extra.get("elements", []) if (e["type"], e["id"]) not in seen]
    d = compact(data, name, bbox)
    bhs.write(path, d)
    print(f"{path}: {len(d['ways'])} ways, {len(d['nodes'])} nodes, {len(d['towns'])} towns "
          f"({os.path.getsize(path) / 1e6:.1f} MB)")
    try:
        print(f"plan: {summary(d)}")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
