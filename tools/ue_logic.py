#!/usr/bin/env python3
"""Does the road network make sense to a driver? Checks roadnet.json for the things that read as wrong whatever the map says:
lanes that jump sideways or change count where a road goes on through a node, signals that control only some of a junction's
ways in, signals on a road nothing crosses, signalled junctions a few metres apart. (Pavement steps are the road thread's
edge_step check: a link's width is a median, its edges field the truth.)

    Earth.UE\\.venv\\Scripts\\python tools\\ue_logic.py --net <roadnet dir> --out <folder>

Writes logic.json {"summary", "faults": {check: [{"xy", ...}]}}, worst first.
"""
import argparse
import collections
import json
import math
import os

import numpy as np

CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary"}
STREETS = CLASSES | {"residential", "unclassified", "living_street"}
TURN_THROUGH_DEG = 30.0         # a road goes on through a node within this turn
JUMP_M = 0.8                    # m a lane's line may step sideways at a node
NEAR_SIGNALS_M = 45.0           # m between two signalled junctions that are really one


def end_dir(p, at_end):
    q = p[::-1] if at_end else p
    k = min(len(q) - 1, 3)
    u = q[k] - q[0]
    return u / max(float(np.linalg.norm(u)), 1e-6)


def lane_lines(lk, at_end):
    """World points of a link's lanes at one end."""
    p = np.array(lk["points"])[:, :2]
    d = -end_dir(p, True) if at_end else end_dir(p, False)
    n = np.array([-d[1], d[0]])         # (left of travel)
    q = p[-1] if at_end else p[0]
    return np.array([q + n * float(o) for o in lk["lanes"]]).reshape(-1, 2), d


def through_pairs(links):
    """(in link, out link) where a road goes straight on through a node: the straightest way out with the same name or class."""
    by_from = collections.defaultdict(list)
    for lk in links:
        if lk["class"] in STREETS and lk["level"] == 0:
            by_from[lk["from"]].append(lk)
    for a in links:
        if a["class"] not in STREETS or a["level"] != 0:
            continue
        pa = np.array(a["points"])[:, :2]
        if len(pa) < 2:
            continue
        u = -end_dir(pa, True)
        best, score = None, math.cos(math.radians(TURN_THROUGH_DEG))
        for b in by_from.get(a["to"], ()):
            if b["from"] == a["to"] and b["to"] == a["from"]:
                continue
            pb = np.array(b["points"])[:, :2]
            if len(pb) < 2:
                continue
            c = float(end_dir(pb, False) @ u)
            if c > score:
                best, score = b, c
        if best is not None:
            yield a, best


def lanes_check(links, nodes):
    """Only out on the road: across a junction a lane need not line up with itself on the far side."""
    junction = {k for k, v in nodes.items() if v.get("junction") is not None}
    jumps, counts = [], []
    for a, b in through_pairs(links):
        if a["to"] in junction:
            continue
        if not a["lanes"] or not b["lanes"]:
            continue
        A, ua = lane_lines(a, True)
        B, ub = lane_lines(b, False)
        xy = [round(float(a["points"][-1][0]), 1), round(float(a["points"][-1][1]), 1)]
        row = {"xy": xy, "a": a["id"], "b": b["id"], "class": a["class"], "name": a.get("name") or b.get("name") or "", "lanes": [len(a["lanes"]), len(b["lanes"])]}
        n = np.array([-ua[1], ua[0]])
        off_a, off_b = np.sort((A - A.mean(0)) @ n), np.sort((B - A.mean(0)) @ n)
        k = min(len(off_a), len(off_b))
        # (the side the lanes are compared from: the kerb, where a lane added on the left leaves the others in line)
        step = float(np.abs((off_a[:k] - off_a[0]) - (off_b[:k] - off_a[0])).max()) if len(off_a) == len(off_b) else min(
            float(np.abs(off_a[:k] - off_b[:k]).max()), float(np.abs(off_a[-k:] - off_b[-k:]).max()))
        if step > JUMP_M:
            jumps.append(dict(row, step=round(step, 2)))
        if abs(len(a["lanes"]) - len(b["lanes"])) >= 2:
            counts.append(row)
    return sorted(jumps, key=lambda f: -f["step"]), counts


def signals_check(net):
    links = net["links"]
    nodes = net["nodes"]
    out = {"partial": [], "no_cross": [], "close": [], "side_road": []}
    jx = {j["id"]: j for j in net["junctions"]}
    by_j = collections.defaultdict(list)
    for lk in links:
        j = (nodes.get(lk["to"]) or {}).get("junction")
        if j is not None and lk["level"] == 0:
            by_j[j].append(lk)
    sig = []
    for j, inc in by_j.items():
        group = set(jx[j]["nodes"]) if j in jx else set()
        ins = [k for k in inc if k["from"] not in group and k["class"] in STREETS]
        s = [k for k in ins if k["control"] == "signal"]
        if not s:
            continue
        xy = [round(float(v), 1) for v in jx[j]["xy"]] if j in jx else [round(float(v), 1) for v in s[0]["points"][-1][:2]]
        sig.append((xy, j, len(s), len(ins)))
        names = {k["name"] for k in ins if k["name"]}
        if len(s) < len(ins):
            out["partial"].append({"xy": xy, "junction": j, "signal": len(s), "ways_in": len(ins), "without": sorted({k["class"] for k in ins if k["control"] != "signal"}), "names": sorted(names)})
        if len(ins) <= 2:
            out["no_cross"].append({"xy": xy, "junction": j, "ways_in": len(ins), "names": sorted(names)})
    for i in range(len(sig)):
        for k in range(i + 1, len(sig)):
            d = math.hypot(sig[i][0][0] - sig[k][0][0], sig[i][0][1] - sig[k][0][1])
            if d < NEAR_SIGNALS_M:
                out["close"].append({"xy": sig[i][0], "other": sig[k][0], "m": round(d, 1)})
    return out, len(sig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    with open(os.path.join(a.net, "roadnet.json")) as f:
        net = json.load(f)
    jumps, counts = lanes_check(net["links"], net["nodes"])
    sig, n_sig = signals_check(net)
    summary = {"lane_jumps": len(jumps), "lane_count_jumps": len(counts), "signal_junctions": n_sig, **{"signal_" + k: len(v) for k, v in sig.items()}}
    print(json.dumps(summary, indent=1))
    print("by class", collections.Counter(f["class"] for f in jumps).most_common())
    for f in jumps[:10] + counts[:6]:
        print(f)
    for k, v in sig.items():
        print(k, v[:8])
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "logic.json"), "w") as f:
            json.dump({"summary": summary, "faults": {"lane_jumps": jumps, "lane_count_jumps": counts, **sig}}, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
