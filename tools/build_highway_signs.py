#!/usr/bin/env python3
"""A region's highway sign data (server/guidesigns.py) from OpenStreetMap through the Overpass API: its motorways,
trunk carriageways and ramps (with their ref, maxspeed, lanes and destination tags) and the motorway_junction nodes
(their exit numbers), written to server/hwysigns/<region>.json.gz. The server turns it into exit guide signs,
gantries, shields, mile markers and speed limits when it builds road tiles; nothing is fetched while driving.

    python tools/build_highway_signs.py --region front-range
    python tools/build_highway_signs.py --bbox 39.3,-105.2,40.3,-104.6 --name my-area
    python tools/build_highway_signs.py --region front-range --check      (the plan's counts, no download)
    python tools/build_highway_signs.py --region colorado-statewide --name colorado --strip-deg 0.5

A big box is fetched in strips of latitude (--strip-deg; each strip's answer kept under build/hwysigns-cache, so a
rerun after a busy server picks up where it stopped), merged by element id. The sign plan loads every file in
server/hwysigns (the biggest first; a later file's sign where an earlier one has it is dropped), so a region's file
may overlap the whole state's.

OpenStreetMap data (c) OpenStreetMap contributors, ODbL 1.0.
"""
import argparse
import gzip
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, HERE)

OUT_DIR = os.path.join(ROOT, "server", "hwysigns")
ENDPOINTS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
             "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
             "https://overpass.private.coffee/api/interpreter")
CACHE_DIR = os.path.join(ROOT, "build", "hwysigns-cache")
KEEP = ("highway", "ref", "oneway", "maxspeed", "lanes", "destination", "destination:ref", "destination:street",
        "destination:symbol", "destination:to", "destination:ref:to", "junction:ref", "exit_to", "name", "toll",
        "noref")
QUERY = """[out:json][timeout:{timeout}][maxsize:{maxsize}];
(
  way["highway"~"^(motorway|motorway_link|trunk|trunk_link)$"]({s},{w},{n},{e});
  node["highway"="motorway_junction"]({s},{w},{n},{e});
);
out body geom qt;
"""


def _context():
    try:
        import truststore          # (Windows: the system's certificate store, as the server)
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(bbox, endpoints=ENDPOINTS, timeout=900, maxsize=1 << 30, query=QUERY):
    """Overpass' JSON answer for the bbox (south, west, north, east), trying each endpoint in turn."""
    s, w, n, e = bbox
    body = urllib.parse.urlencode({"data": query.format(s=s, w=w, n=n, e=e, timeout=timeout,
                                                        maxsize=maxsize)}).encode()
    last = None
    for url in endpoints:
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=body, headers={
                    "User-Agent": "BeamNG.Earth highway signs (tools/build_highway_signs.py)"})
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=timeout + 60, context=_context()) as r:
                    data = json.loads(r.read().decode("utf-8"))
                print(f"  {url}: {len(data.get('elements', []))} elements in {time.time() - t0:.0f} s")
                return data
            except Exception as ex:     # (busy: 429 / 504; try again, then the next endpoint)
                last = ex
                print(f"  {url}: {type(ex).__name__}: {ex}")
                time.sleep(10 * (attempt + 1))
    raise SystemExit(f"Overpass failed: {last}")


def fetch_strips(bbox, name, strip_deg, endpoints=ENDPOINTS, query=QUERY):
    """fetch over the bbox in strips of latitude strip_deg high, each strip's answer cached; one answer of every
    strip's elements (a way crossing strips comes whole from each: once), its timestamp the oldest strip's."""
    s, w, n, e = bbox
    k = max(1, int(-(-(n - s) // strip_deg)))
    if k == 1:
        return fetch(bbox, endpoints, query=query)
    os.makedirs(CACHE_DIR, exist_ok=True)
    seen, elements, stamps = set(), [], []
    for i in range(k):
        sb = (round(s + (n - s) * i / k, 5), w, round(s + (n - s) * (i + 1) / k, 5), e)
        cache = os.path.join(CACHE_DIR, f"{name}-{k}-{i}.json.gz")
        if os.path.exists(cache):
            with gzip.open(cache, "rt", encoding="utf-8") as f:
                data = json.load(f)
            print(f"  strip {i + 1}/{k} {sb}: cached")
        else:
            print(f"  strip {i + 1}/{k} {sb}")
            data = fetch(sb, endpoints, timeout=300, maxsize=512 << 20, query=query)
            with gzip.open(cache + ".tmp", "wt", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(cache + ".tmp", cache)
        stamps.append(data.get("osm3s", {}).get("timestamp_osm_base", ""))
        for el in data.get("elements", []):
            if (el["type"], el["id"]) not in seen:
                seen.add((el["type"], el["id"]))
                elements.append(el)
    return {"osm3s": {"timestamp_osm_base": min((t for t in stamps if t), default="")}, "elements": elements}


def compact(data, region, bbox):
    """The region file's dict from Overpass' answer: ways (id, tags kept, node ids, [lat, lon] per node) and
    junctions (id, lat, lon, tags kept)."""
    ways, junctions = [], []
    for el in data.get("elements", []):
        tags = {k: v for k, v in (el.get("tags") or {}).items() if k in KEEP}
        if el["type"] == "way" and el.get("geometry") and el.get("nodes"):
            geom = [[round(p["lat"], 7), round(p["lon"], 7)] for p in el["geometry"]]
            if len(geom) != len(el["nodes"]):
                continue
            ways.append({"id": el["id"], "tags": tags, "nodes": el["nodes"], "geom": geom})
        elif el["type"] == "node" and tags.get("highway") == "motorway_junction":
            junctions.append({"id": el["id"], "lat": round(el["lat"], 7), "lon": round(el["lon"], 7), "tags": tags})
    ways.sort(key=lambda w: w["id"])
    junctions.sort(key=lambda j: j["id"])
    return {"v": 1, "region": region, "bbox": list(bbox), "stamp": data.get("osm3s", {}).get("timestamp_osm_base", ""),
            "source": "OpenStreetMap contributors (ODbL), Overpass API", "ways": ways, "junctions": junctions}


def write(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(d, f, separators=(",", ":"))
    os.replace(tmp, path)


def summary(d):
    import guidesigns
    items = guidesigns.plan(d)
    kinds = {}
    for it in items:
        kinds[it["k"]] = kinds.get(it["k"], 0) + 1
    return kinds


def list_exits(d, route):
    """The exit direction signs whose through sign carries route, north to south: number, shields, destinations."""
    import guidesigns
    want = guidesigns.parse_ref(route)[:2]
    rows = []
    for it in guidesigns.plan(d):
        if it["k"] != "exit_dir":
            continue
        thr = [tuple(s[:2]) for s in (it.get("thr") or {}).get("sh", [])]
        if want not in thr:
            continue
        card = [s[2] for s in it["thr"]["sh"] if tuple(s[:2]) == want][0]
        sh = ", ".join(f"{n} {r}{(' ' + c) if c else ''}" for n, r, c in it["sh"])
        rows.append((-it["lat"], f"{card:5} exit {it['ref'] or '-':5} {sh:24} {' / '.join(it['dst'])}"
                                 f"{'  (left)' if it['left'] else ''}  {it['lat']:.4f},{it['lon']:.4f}"))
    for _, r in sorted(rows):
        print(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--region", help="a region of tools/regions.json (its bounding box)")
    ap.add_argument("--bbox", help="south,west,north,east")
    ap.add_argument("--name", help="the file's name (default: the region's)")
    ap.add_argument("--pad-km", type=float, default=5.0, help="km added round the region's box")
    ap.add_argument("--check", action="store_true", help="print the existing file's plan counts")
    ap.add_argument("--strip-deg", type=float, default=0.6, help="degrees of latitude per Overpass request")
    ap.add_argument("--exits", metavar="REF", help="with --check: list the exits signed on this route (e.g. \"I 25\")")
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
    path = os.path.join(OUT_DIR, f"{name}.json.gz")
    if a.check:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        print(f"{path}: {len(d['ways'])} ways, {len(d['junctions'])} junctions, OSM {d.get('stamp', '?')}: {summary(d)}")
        if a.exits:
            list_exits(d, a.exits)
        return
    print(f"Overpass: motorways, trunks and exits in {bbox}")
    d = compact(fetch_strips(bbox, name, a.strip_deg), name, bbox)
    write(path, d)
    print(f"{path}: {len(d['ways'])} ways, {len(d['junctions'])} junctions ({os.path.getsize(path) / 1e6:.1f} MB)")
    print(f"plan: {summary(d)}")


if __name__ == "__main__":
    main()
