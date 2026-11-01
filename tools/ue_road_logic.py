#!/usr/bin/env python3
"""Do the roads make sense? Not how near they are to the survey (ue_road_sheet), but whether a driver could use them: lanes that
meet, lane counts that change for a reason, one-ways that do not meet head on, signals at real intersections, over the lanes
and facing the traffic. Works on roadnet.json and signals.json, no Unreal.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_road_logic.py --net <roadnet dir> --export <export dir> [--png sheet.png]

Writes logic.json in --export: {"counts": {check: n}, "findings": [{check, at, sev, ...}]}, worst first. Checks:
    lane_step    lanes do not line up across a node (m sideways)         lane_jump   the count changes by 2 or more on a through road
    lane_pop     the count changes at a node with no side road            kink        a through road turns sharply at a node with no junction
    dead_end     a painted road stops with nothing to go on to            head_on     one-way roads meet nose to nose
    overlap      two roads of one level drawn on top of each other        narrow      lanes under 2.7 m or over 4.2 m on a painted road
    sig_arms     a signal where there is no intersection                  sig_gap     an approach of a signalled junction with no signal
    sig_double   two signalled junctions too close along one road         sig_unmapped a mapped signal no junction took
    head_facing  a head not facing its approach                           head_lane   a head not over a lane
    head_dist    a head too near or too far from the stop line            arm_pavement a signal pole in the roadway
"""
import argparse
import json
import math
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

PAINTED = {"motorway", "trunk", "primary", "secondary", "tertiary"}
THROUGH_DEG = 35.0              # a turn under this is going straight on
STEP_M = 1.2                    # lanes further apart than this across a node do not meet
KINK_DEG = 25.0
OVERLAP_M = 2.5
OVERLAP_MIN_M = 20.0
LANE_RANGE = (2.7, 4.2)
SIG_DOUBLE_M = 75.0
SIG_MAPPED_M = 45.0
HEAD_DOT = 0.9
HEAD_LANE_M = 1.6
HEAD_DIST = (8.0, 52.0)


def unit(v):
    return v / max(float(np.hypot(*v)), 1e-9)


def end_dir(p, reach=12.0):
    """Unit direction a link arrives at its last point."""
    q = p[::-1, :2]
    d = np.r_[0.0, np.cumsum(np.hypot(*np.diff(q, axis=0).T))]
    a = np.array([np.interp(min(reach, d[-1]), d, q[:, 0]), np.interp(min(reach, d[-1]), d, q[:, 1])])
    return unit(q[0] - a)


def start_dir(p, reach=12.0):
    """Unit direction a link leaves its first point."""
    q = p[:, :2]
    d = np.r_[0.0, np.cumsum(np.hypot(*np.diff(q, axis=0).T))]
    a = np.array([np.interp(min(reach, d[-1]), d, q[:, 0]), np.interp(min(reach, d[-1]), d, q[:, 1])])
    return unit(a - q[0])


def left(u):
    return np.array([-u[1], u[0]])


def turn_deg(u, v):
    return math.degrees(abs(math.atan2(u[0] * v[1] - u[1] * v[0], float(u @ v))))


def lanes_at(xy, u, offs):
    return np.array([xy + left(u) * o for o in offs])


def best_step(a, b):
    """The smallest worst sideways gap of two rows of lanes (kerb first), the shorter row slid along the longer."""
    if len(a) > len(b):
        a, b = b, a
    return min(float(np.hypot(*(a - b[s:s + len(a)]).T).max()) for s in range(len(b) - len(a) + 1))


def link_check(net, inside):
    links = net["links"]
    P = [np.array(lk["points"]) for lk in links]
    into, out = {}, {}
    for lk in links:
        into.setdefault(lk["to"], []).append(lk["id"])
        out.setdefault(lk["from"], []).append(lk["id"])
    found = []

    def add(check, at, sev, **kw):
        if inside(at):
            found.append({"check": check, "at": [round(float(at[0]), 1), round(float(at[1]), 1)], "sev": round(float(sev), 2), **kw})
    for lk in links:
        cls, a = lk["class"], lk["id"]
        pa = P[a]
        if cls in PAINTED and not lk["ramp"]:
            lw = float(lk["lane_width"] or 3.6)
            L = float(np.hypot(*np.diff(pa[:, :2], axis=0).T).sum())
            if L > 40 and not LANE_RANGE[0] <= lw <= LANE_RANGE[1]:
                add("narrow", pa[len(pa) // 2], abs(lw - 3.5), link=a, name=lk["name"], lane_w=lw, lanes=len(lk["lanes"]), width=lk["width"])
        node = lk["to"]
        outs = [links[b] for b in out.get(node, ()) if links[b]["to"] != lk["from"] or links[b]["oneway"]]
        u = end_dir(pa)
        E = pa[-1, :2]
        arms = {k: 1 for k in out.get(node, ())}
        arms.update({k: 1 for k in into.get(node, ())})
        if not outs:
            if cls in PAINTED and not lk["ramp"] and not lk["oneway"]:
                add("dead_end", E, 3.0, link=a, name=lk["name"], cls=cls)
            elif lk["oneway"] and cls in PAINTED | {"residential", "unclassified"} and len(into.get(node, ())) >= 2:
                add("head_on", E, 6.0, link=a, name=lk["name"], cls=cls)
            continue
        through = None
        for b in outs:
            v = start_dir(P[b["id"]])
            t = turn_deg(u, v)
            same = (b["name"] == lk["name"] and lk["name"] != "")
            score = t - (20 if same else 0)
            if t < THROUGH_DEG + (20 if same else 0) and (through is None or score < through[0]):
                through = (score, b, t, v)
        if through is None or cls not in PAINTED or lk["ramp"]:
            continue
        b, t, v = through[1], through[2], through[3]
        if b["ramp"] or b["level"] != lk["level"] or b["class"] not in PAINTED:
            continue
        la, lb = lanes_at(E, u, lk["lanes"]), lanes_at(P[b["id"]][0, :2], v, b["lanes"])
        step = best_step(la, lb)
        na, nb = len(lk["lanes"]), len(b["lanes"])
        side_road = len([k for k in arms if links[k]["name"] != lk["name"]]) > 0
        if step > STEP_M:
            add("lane_step", E, step, link=a, to=b["id"], name=lk["name"], cls=cls, step=round(step, 2), lanes=[na, nb], widths=[lk["width"], b["width"]])
        if abs(na - nb) >= 2:
            add("lane_jump", E, 2.0 * abs(na - nb), link=a, to=b["id"], name=lk["name"], cls=cls, lanes=[na, nb])
        elif na != nb and not side_road:
            add("lane_pop", E, 1.5, link=a, to=b["id"], name=lk["name"], cls=cls, lanes=[na, nb])
        if t > KINK_DEG and not side_road:
            add("kink", E, t / 10, link=a, to=b["id"], name=lk["name"], cls=cls, deg=round(t, 1))
    return found, into, out


def overlaps(net, inside):
    from shapely import LineString, STRtree
    keep = [lk for lk in net["links"] if lk["class"] in PAINTED | {"residential", "unclassified"}]
    geo = [LineString(np.array(lk["points"])[:, :2]) for lk in keep]
    tree = STRtree(geo)
    a, b = tree.query(geo, predicate="dwithin", distance=OVERLAP_M)
    out, seen = [], set()
    for i, j in zip(a.tolist(), b.tolist()):
        if i >= j:
            continue
        p, q = keep[i], keep[j]
        if p["level"] != q["level"] or p["from"] == q["to"] and p["to"] == q["from"]:
            continue
        if {p["from"], p["to"]} & {q["from"], q["to"]} and geo[i].hausdorff_distance(geo[j]) > 60:
            continue
        share = geo[i].intersection(geo[j].buffer(OVERLAP_M)).length
        if share < OVERLAP_MIN_M:
            continue
        key = (min(i, j), max(i, j))
        if key in seen:
            continue
        seen.add(key)
        c = geo[i].intersection(geo[j].buffer(OVERLAP_M)).representative_point()
        if inside((c.x, c.y)):
            out.append({"check": "overlap", "at": [round(c.x, 1), round(c.y, 1)], "sev": round(min(share / 10, 8), 2), "a": p["id"], "b": q["id"],
                        "names": [p["name"], q["name"]], "m": round(share, 1)})
    return out


def signal_check(net, heads, mapped, into, out, inside):
    links = net["links"]
    P = {lk["id"]: np.array(lk["points"]) for lk in links}
    jun = {j["id"]: j for j in net["junctions"]}
    found = []

    def add(check, at, sev, **kw):
        if inside(at):
            found.append({"check": check, "at": [round(float(at[0]), 1), round(float(at[1]), 1)], "sev": round(float(sev), 2), **kw})
    signal = [j for j in net["junctions"] if j["control"] == "signal"]
    node_j = {k: j["id"] for j in signal for k in j["nodes"]}
    for j in signal:
        nodes = set(j["nodes"])
        ins = [links[k] for n in nodes for k in into.get(n, ()) if links[k]["from"] not in nodes]
        names = {lk["name"] or f"#{lk['class']}" for lk in ins if lk["class"] not in ("service",)}
        if len(ins) < 3 or len(names) < 2:
            add("sig_arms", j["xy"], 6.0, junction=j["id"], approaches=len(ins), names=sorted(names)[:4])
        for lk in ins:
            if lk["control"] != "signal" and lk["class"] not in ("service", "track") and not lk["ramp"]:
                add("sig_gap", P[lk["id"]][-1], 4.0, junction=j["id"], link=lk["id"], name=lk["name"], cls=lk["class"])
    xy = np.array([j["xy"] for j in signal]).reshape(-1, 2)
    for a in range(len(signal)):
        for b in range(a + 1, len(signal)):
            d = float(np.hypot(*(xy[a] - xy[b])))
            if d < SIG_DOUBLE_M:
                add("sig_double", (xy[a] + xy[b]) / 2, 4.0, a=signal[a]["id"], b=signal[b]["id"], m=round(d, 1))
    for q in mapped:
        if not len(xy) or float(np.hypot(*(xy - q).T).min()) > SIG_MAPPED_M:
            add("sig_unmapped", q, 2.0)
    for h in heads:
        lk = links[h["link"]]
        p = P[h["link"]]
        u = end_dir(p)
        f = -np.array(h["facing"][:2])
        red = np.array(h["lens"]["red"][:2])
        if float(f @ u) < HEAD_DOT:
            add("head_facing", red, 5.0, link=h["link"], dot=round(float(f @ u), 2))
        E = p[-1, :2]
        along = float((red - E) @ u)
        if not HEAD_DIST[0] <= along <= HEAD_DIST[1]:
            add("head_dist", red, 3.0, link=h["link"], m=round(along, 1), name=lk["name"])
        side = float((red - E) @ left(u))
        if min(abs(side - o) for o in lk["lanes"]) > HEAD_LANE_M:
            add("head_lane", red, 3.0, link=h["link"], side=round(side, 1), lanes=lk["lanes"], name=lk["name"])
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--export", default="C:/beamng-earth/Earth.UE/export/frederick-r5")
    ap.add_argument("--edge", type=float, default=10100.0, help="m from the origin: findings past this are the export's edge")
    a = ap.parse_args()
    with open(os.path.join(a.net, "roadnet.json")) as f:
        net = json.load(f)
    with open(os.path.join(a.export, "signals.json")) as f:
        heads = json.load(f)["heads"]
    with open(os.path.join(a.net, "net.pkl"), "rb") as f:
        mapped = pickle.load(f).get("infra", {}).get("traffic_signals", np.zeros((0, 2)))

    def inside(q):
        return abs(q[0]) < a.edge and abs(q[1]) < a.edge
    found, into, out = link_check(net, inside)
    found += overlaps(net, inside)
    found += signal_check(net, heads, mapped, into, out, inside)
    found.sort(key=lambda v: -v["sev"])
    counts = {}
    for f_ in found:
        counts[f_["check"]] = counts.get(f_["check"], 0) + 1
    with open(os.path.join(a.export, "logic.json"), "w") as f:
        json.dump({"counts": counts, "findings": found}, f, indent=0)
    print(json.dumps(counts, indent=0))
    seen = {}
    for f_ in found:
        n = seen[f_["check"]] = seen.get(f_["check"], 0) + 1
        if n <= 3:
            print(json.dumps(f_)[:220])


if __name__ == "__main__":
    main()
