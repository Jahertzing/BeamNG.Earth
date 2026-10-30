#!/usr/bin/env python3
"""A stacked interchange checked as a structure, no Unreal needed: the road pieces of a net.pkl (tools/ue_roadnet.py) drawn in
plan coloured by level, every plan crossing listed with the two levels, and the problems a real road system cannot have:

    phantom     two roads crossing in plan at one level with no shared connector
    level jump  a road's end joined to a road two or more levels away with no ramp between
    stray end   a deck that ends in mid air: its end meets no other road and no ground road

    Earth.UE\\.venv\\Scripts\\python tools\\ue_levels.py --net <dir> --png levels.png [--box x0,y0,x1,y1]
"""
import argparse
import json
import os
import pickle

import numpy as np
from PIL import Image, ImageDraw

COLOURS = {-1: (120, 120, 255), 0: (150, 150, 150), 1: (60, 200, 90), 2: (240, 200, 40), 3: (250, 130, 30), 4: (235, 50, 50)}
END_NEAR_M = 2.0                # m: road ends this close meet
HIDE = ("service", "track")


def crossings(pieces):
    from shapely import LineString, STRtree
    lines = [LineString(p["xy"]) for p in pieces]
    tree = STRtree(lines)
    a, b = tree.query(lines, predicate="crosses")
    out = []
    for i, j in zip(a.tolist(), b.tolist()):
        if i < j and pieces[i]["fid"] != pieces[j]["fid"]:
            x = lines[i].intersection(lines[j]).representative_point()
            out.append((i, j, (x.x, x.y)))
    return out


def check(pieces):
    ends = {}
    for k, p in enumerate(pieces):
        for at in (0, -1):
            ends.setdefault((round(p["xy"][at][0] / END_NEAR_M), round(p["xy"][at][1] / END_NEAR_M)), []).append(k)
    def met(k, at):
        q = pieces[k]["xy"][at]
        key = (round(q[0] / END_NEAR_M), round(q[1] / END_NEAR_M))
        got = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                got += [v for v in ends.get((key[0] + dx, key[1] + dy), ()) if v != k]
        return got
    stray, jumps = [], []
    for k, p in enumerate(pieces):
        if p["kind"] != "bridge":
            continue
        for at in (0, -1):
            others = met(k, at)
            if not others:
                stray.append({"fid": p["fid"], "name": p["name"], "cls": p["spec"]["cls"], "at": [round(v, 1) for v in p["xy"][at]],
                              "level": p["level"]})
            elif all(abs(pieces[v]["level"] - p["level"]) >= 2 for v in others):
                jumps.append({"fid": p["fid"], "name": p["name"], "at": [round(v, 1) for v in p["xy"][at]], "level": p["level"],
                              "others": sorted({pieces[v]["level"] for v in others})})
    phantom = []
    overs = {}
    for i, j, at in crossings(pieces):
        p, q = pieces[i], pieces[j]
        if p["level"] == q["level"]:
            if not {c[0] for c in p["connectors"]} & {c[0] for c in q["connectors"]}:
                phantom.append({"at": [round(v, 1) for v in at], "level": p["level"], "a": [p["spec"]["cls"], p["name"]],
                                "b": [q["spec"]["cls"], q["name"]]})
        else:
            k = tuple(sorted((p["level"], q["level"])))
            overs[str(k)] = overs.get(str(k), 0) + 1
    return {"crossings_by_levels": overs, "phantom": phantom, "stray_deck_ends": stray, "level_jumps": jumps}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--png", default=None)
    ap.add_argument("--box", default=None)
    ap.add_argument("--px", type=float, default=1.0, help="m a pixel")
    a = ap.parse_args()
    with open(os.path.join(a.net, "net.pkl"), "rb") as f:
        net = pickle.load(f)
    pieces = net["pieces"]
    box = tuple(float(v) for v in a.box.split(",")) if a.box else net.get("box")
    if box:
        pieces = [p for p in pieces if p["xy"][:, 0].max() >= box[0] and p["xy"][:, 0].min() <= box[2]
                  and p["xy"][:, 1].max() >= box[1] and p["xy"][:, 1].min() <= box[3]]
    found = check(pieces)
    levels = {}
    for p in pieces:
        k = str(p["level"])
        levels[k] = levels.get(k, 0) + 1
    print(json.dumps({"pieces": len(pieces), "levels": levels, "crossings": found["crossings_by_levels"],
                      "phantom": len(found["phantom"]), "stray_deck_ends": len(found["stray_deck_ends"]),
                      "level_jumps": len(found["level_jumps"])}))
    for k in ("phantom", "stray_deck_ends", "level_jumps"):
        for row in found[k][:12]:
            print(k, json.dumps(row))
    if a.png:
        x0, y0, x1, y1 = box
        w, h = int((x1 - x0) / a.px), int((y1 - y0) / a.px)
        img = Image.new("RGB", (w, h), (18, 18, 22))
        d = ImageDraw.Draw(img)

        def uv(xy):
            return [((q[0] - x0) / a.px, (y1 - q[1]) / a.px) for q in xy]
        for p in sorted(pieces, key=lambda v: v["level"]):
            if p["spec"]["cls"] in HIDE:
                continue
            d.line(uv(p["xy"]), fill=COLOURS.get(int(min(p["level"], 4)), (255, 255, 255)),
                   width=max(1, int(float(p["spec"]["width"] or 6) / a.px)))
        for row in found["stray_deck_ends"]:
            c = uv([row["at"]])[0]
            d.ellipse([c[0] - 7, c[1] - 7, c[0] + 7, c[1] + 7], outline=(255, 0, 255), width=2)
        for row in found["phantom"]:
            c = uv([row["at"]])[0]
            d.rectangle([c[0] - 7, c[1] - 7, c[0] + 7, c[1] + 7], outline=(255, 255, 255), width=2)
        img.save(a.png)


if __name__ == "__main__":
    main()
