#!/usr/bin/env python3
"""Checks The Drive's two routes (mod thedrive.lua) against the live router the GPS uses (server routing.py: OSRM,
routing.openstreetmap.de): distance, time, where the start, the waypoints and the end snap to, and the roads driven
(longest stretches first), so a route that leaves I-25 for E-470 or misses Union Station shows.

  python tools/thedrive_check.py            both routes
  python tools/thedrive_check.py --json     the same as JSON

The places are read from the mod's thedrive.lua, so this checks what the game will ask for. One request per route
plus one per point (the router's fair use: about one a second).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import routing  # noqa: E402

LUA = os.path.join(HERE, "..", "mod", "beamng_earth", "lua", "ge", "extensions", "beamngEarth", "thedrive.lua")
UA = "BeamNG.Earth thedrive_check (github.com/TecnaGamer/BeamNG.Earth)"
NEAREST = "https://routing.openstreetmap.de/routed-car/nearest/v1/driving/{lon:.6f},{lat:.6f}"
TOLL = re.compile(r"E[ -]?470|Northwest Parkway|Jefferson Parkway", re.I)    # Denver's toll roads, by name or ref


def places(text):
    """{start, dest, routes: {key: {vias: [(lat, lon, name)], avoid}}} from thedrive.lua's text. Pure."""
    pt = r"\{\s*lat\s*=\s*([-\d.]+),\s*lon\s*=\s*([-\d.]+),\s*name\s*=\s*'([^']*)'\s*\}"
    start = re.search(r"M\.START\s*=\s*" + pt, text)
    dest = re.search(r"M\.DEST\s*=\s*" + pt, text)
    routes = {}
    block = text[text.index("M.ROUTES = {"):]
    block = block[:block.index("\n}\n")]
    for key, body in re.findall(r"\n  (\w+) = \{(.*?)(?=\n  \w+ = \{|$)", block, re.S):
        av = re.search(r"avoid = '(\w+)'", body)
        routes[key] = {"label": re.search(r"label = '([^']*)'", body).group(1),
                       "vias": [(float(a), float(b), n) for a, b, n in re.findall(pt, body)],
                       "avoid": av.group(1) if av else None}
    return {"start": (float(start.group(1)), float(start.group(2)), start.group(3)),
            "dest": (float(dest.group(1)), float(dest.group(2)), dest.group(3)), "routes": routes}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    time.sleep(1.1)
    return data


def roads(route):
    """[(name, km)] of a route with steps, the same road's steps added up, longest first. Pure."""
    km = {}
    for leg in route.get("legs", []):
        for st in leg.get("steps", []):
            name = st.get("ref") or st.get("name") or "(unnamed)"
            km[name] = km.get(name, 0.0) + float(st.get("distance") or 0) / 1000
    return sorted(km.items(), key=lambda kv: -kv[1])


def check(p):
    out = {}
    s, d = p["start"], p["dest"]
    for key, r in p["routes"].items():
        u = routing.url(s[0], s[1], d[0], d[1], via=[(v[0], v[1]) for v in r["vias"]], exclude=r["avoid"])
        u = u.replace("steps=false", "steps=true")
        try:
            data = get(u)
        except urllib.error.HTTPError as e:     # a router without that road class (400), as routing.plan: without it
            if e.code != 400 or not r["avoid"]:
                raise
            data = get(routing.url(s[0], s[1], d[0], d[1], via=[(v[0], v[1]) for v in r["vias"]])
                       .replace("steps=false", "steps=true"))
            data["avoid_unsupported"] = True
        if data.get("code") != "Ok":
            out[key] = {"error": data.get("code"), "message": data.get("message")}
            continue
        rt = data["routes"][0]
        out[key] = {"label": r["label"], "km": round(rt["distance"] / 1000, 1), "minutes": round(rt["duration"] / 60),
                    "avoid": None if data.get("avoid_unsupported") else r["avoid"],
                    "avoid_unsupported": r["avoid"] if data.get("avoid_unsupported") else None,
                    "snapped": [{"name": w.get("name"), "off_m": round(w.get("distance") or 0)} for w in data["waypoints"]],
                    "roads": [(n, round(k, 1)) for n, k in roads(rt)[:8]],
                    "tolls": [n for n, _ in roads(rt) if TOLL.search(n)]}
    pts = [s] + [v for r in p["routes"].values() for v in r["vias"]] + [d]
    out["points"] = []
    for lat, lon, name in pts:
        n = get(NEAREST.format(lat=lat, lon=lon))
        w = (n.get("waypoints") or [{}])[0]
        out["points"].append({"place": name, "road": w.get("name"), "off_m": round(w.get("distance") or 0)})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = check(places(open(LUA, encoding="utf-8").read()))
    if a.json:
        print(json.dumps(res, indent=1))
        return
    for p in res.pop("points"):
        print(f"{p['place']:>14}: on {p['road'] or '(unnamed)'} ({p['off_m']} m off)")
    for key, r in res.items():
        if "error" in r:
            print(f"\n{key}: {r['error']} {r.get('message') or ''}")
            continue
        print(f"\n{key} ({r['label']}): {r['km']} km, {r['minutes']} min" + (f", avoiding {r['avoid']}" if r["avoid"] else "")
              + (f" (the router can't avoid {r['avoid_unsupported']}: routed without)" if r["avoid_unsupported"] else ""))
        print("  tolls:   " + (", ".join(r["tolls"]) if r["tolls"] else "none (no E-470, Northwest Parkway)"))
        print("  snapped: " + ", ".join(f"{w['name'] or '(unnamed)'} ({w['off_m']} m)" for w in r["snapped"]))
        print("  roads:   " + ", ".join(f"{n} {k} km" for n, k in r["roads"]))


if __name__ == "__main__":
    main()
