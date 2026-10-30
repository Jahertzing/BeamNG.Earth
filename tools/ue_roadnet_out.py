#!/usr/bin/env python3
"""roadnet.json: the corrected network for traffic and anything else that drives on it, in frame metres (x east, y north,
z up, origin 40.0992, -104.9372).

    Earth.UE\\.venv\\Scripts\\python tools\\ue_roadnet_out.py --net <roadnet dir> --export <export dir>

{"frame", "links": [{id, from, to, points [[x, y, z], ...], class, ramp, level (0 ground, 1+ deck, -1 tunnel), name, oneway,
speed (m/s), width (m), lanes: [offsets m left of the points, kerb lane first], lane_width, turns: [per lane, kerb first] or
null, control ("signal", "stop", "give_way", null) at its end, control_source ("mapped", "rule", null), surface}], "nodes":
{id: {xy, junction, arms}}, "junctions": [{id, nodes, control, xy}]}

Links join only at nodes: a node is one of the map's connectors (or the point a road changes level), so two roads crossing
in plan never connect. A node's "junction" groups the nodes of one intersection (a divided road's crossing is several).
"""
import argparse
import json
import math
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

import geodesy  # noqa: E402
import roads  # noqa: E402
import ue_fit  # noqa: E402
import ue_survey  # noqa: E402

CELL = 2048.0
SKIP_CLASSES = {"track", "raceway"}
SPEED_DEFAULT = {"motorway": 33.5, "trunk": 29.0, "primary": 24.5, "secondary": 20.0, "tertiary": 15.5, "unclassified": 15.5,
                 "residential": 11.0, "living_street": 7.0, "service": 7.0, "unknown": 11.0}
RANK = {"motorway": 9, "trunk": 8, "primary": 7, "secondary": 6, "tertiary": 5, "unclassified": 4, "residential": 3,
        "living_street": 2, "service": 1, "unknown": 1}
SIGNAL_NODE_M = 36.0            # m from a mapped signal a node is that signal's
SIGN_NODE_M = 22.0              # m from a mapped stop sign the end of a link is its
CLUSTER_M = 34.0                # m: nodes joined by a link this short are one junction


class Heights:
    """The drawn ground and decks, read from the export's cells on demand."""

    def __init__(self, export):
        self.dir = export
        self.z = {}
        self.deck = {}

    def grid(self, i, j):
        k = (i, j)
        if k not in self.z:
            path = os.path.join(self.dir, f"ground_{i}_{j}.glb")
            if os.path.exists(path):
                P = ue_survey.read_glb(path)[0]["P"]
                n = int(round(len(P) ** 0.5))
                self.z[k] = P[:, 2].reshape(n, n)
            else:
                self.z[k] = None
        return self.z[k]

    def ground(self, xy):
        """Bilinear heights at (n, 2), nan outside the export."""
        out = np.full(len(xy), np.nan)
        c = np.floor(xy / CELL).astype(np.int64)
        for k in {tuple(v) for v in c.tolist()}:
            z = self.grid(*k)
            if z is None:
                continue
            at = np.flatnonzero((c == k).all(1))
            f = (xy[at] - (k[0] * CELL, k[1] * CELL)) / 2.0
            a = np.clip(f.astype(np.int64), 0, z.shape[0] - 2)
            t = f - a
            out[at] = (z[a[:, 1], a[:, 0]] * (1 - t[:, 0]) * (1 - t[:, 1]) + z[a[:, 1], a[:, 0] + 1] * t[:, 0] * (1 - t[:, 1]) +
                       z[a[:, 1] + 1, a[:, 0]] * (1 - t[:, 0]) * t[:, 1] + z[a[:, 1] + 1, a[:, 0] + 1] * t[:, 0] * t[:, 1])
        return out

    def decks(self, xy):
        """A tree over the deck surface's vertices near (n, 2): (tree, z)."""
        P, Z = [], []
        for k in {tuple(v) for v in np.floor(xy / CELL).astype(np.int64).tolist()}:
            if k not in self.deck:
                path = os.path.join(self.dir, f"deck_{k[0]}_{k[1]}.glb")
                got = None
                if os.path.exists(path):
                    prims = [p for p in ue_survey.read_glb(path) if "asphalt" in p["name"]]
                    if prims:
                        got = np.concatenate([p["P"] for p in prims]) + (k[0] * CELL, k[1] * CELL, 0.0)
                self.deck[k] = got
            if self.deck[k] is not None:
                P.append(self.deck[k][:, :2])
                Z.append(self.deck[k][:, 2])
        if not P:
            return None, None
        return cKDTree(np.concatenate(P)), np.concatenate(Z)


def spec_of(sp, width):
    d = dict(sp)
    d["width"] = width
    d.setdefault("walk", 2.5)
    d = {k: v for k, v in d.items() if k in roads.Spec._fields}
    for f in roads.Spec._fields:
        d.setdefault(f, None)
    d["walk"] = d["walk"] or 2.5
    d["dir"] = d["dir"] or 0
    return roads.Spec(**d)


def lanes_of(sp):
    """{+1: ([offsets left of the geometry, kerb first], lane width), -1: ...} by direction along the geometry."""
    lay = roads.lane_layout(sp)
    if lay is None:
        o = (sp.dir or 1) if sp.oneway else None
        if o:
            return {o: ([0.0], sp.width)}
        return {1: ([-sp.width / 4], sp.width / 2), -1: ([sp.width / 4], sp.width / 2)}
    out = {}
    for d, n, lo, hi in lay["groups"]:
        w = (hi - lo) / max(n, 1)
        centres = [lo + (k + 0.5) * w for k in range(n)]
        out[int(d)] = (centres if d > 0 else centres[::-1], w)     # (right of travel is low offsets along, high against)
    return out


def split_at(xy, nodes):
    """Polyline xy cut at nodes [(id, x, y)] lying on it: [(id or None, ...)] pieces [(from id, to id, points)]."""
    d = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    marks = []
    for cid, x, y in nodes:
        a, b = xy[:-1], xy[1:]
        ab = b - a
        t = np.clip(((np.array([x, y]) - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-9), 0, 1)
        q = a + t[:, None] * ab
        r = np.hypot(*(q - (x, y)).T)
        k = int(r.argmin())
        if r[k] <= 3.0:
            marks.append((float(d[k] + t[k] * math.hypot(*ab[k])), cid))
    marks.sort()
    return d, marks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--export", default="C:/beamng-earth/Earth.UE/export/frederick-r")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    t0 = time.perf_counter()
    with open(os.path.join(a.net, "stations.pkl"), "rb") as f:
        pieces = pickle.load(f)
    with open(os.path.join(a.net, "fit.pkl"), "rb") as f:
        fit = pickle.load(f)
    with open(os.path.join(a.net, "net.pkl"), "rb") as f:
        infra = pickle.load(f).get("infra", {})
    frame = geodesy.EnuFrame(*ue_export.FRAME)
    heights = Heights(a.export)
    moves, widths = fit["moves"], fit["widths"]
    links, nodes = [], {}

    def node(nid, xy):
        n = nodes.setdefault(nid, {"xy": [round(float(xy[0]), 2), round(float(xy[1]), 2)], "ends": []})
        return n

    for p in pieces:
        sp0 = p["spec"]
        if sp0["cls"] in SKIP_CLASSES or sp0["kind"] not in ("paved", "gravel", "dirt", "cobble"):
            continue
        ll = p["ll"].copy()
        for k in range(len(ll)):
            d = moves.get(ue_fit._key(ll[k]))
            if d is not None:
                ll[k, 0] += d[0]
                ll[k, 1] += d[1]
        e, n, _ = frame.geodetic_to_enu(ll[:, 1], ll[:, 0], 0.0)
        xy = np.c_[np.asarray(e), np.asarray(n)]
        keep = np.r_[True, np.hypot(*np.diff(xy, axis=0).T) > 0.05]
        xy, raw = xy[keep], p["xy"][keep]
        if len(xy) < 2:
            continue
        width = widths.get(p["fid"], float(sp0["width"] or 6.0))
        sp = spec_of(sp0, width)
        lanes = lanes_of(sp)
        # the map's connectors on this piece, by their place on the uncorrected line; the piece's own ends as level joints
        cons = [(c[0], c[2], c[3]) for c in p["connectors"] if np.isfinite(c[2])]
        d_raw, marks = split_at(raw, cons)
        L = d_raw[-1]
        scale = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
        scale = scale * (L / max(scale[-1], 1e-9))      # (arc on the corrected line, in the raw line's metres)
        stops = [(0.0, f"j:{p['fid'][:12]}:{round(raw[0][0])}:{round(raw[0][1])}")] + \
            [m for m in marks if 0.5 < m[0] < L - 0.5] + [(L, f"j:{p['fid'][:12]}:{round(raw[-1][0])}:{round(raw[-1][1])}")]
        for m in marks:                                 # a connector on the end names that end
            if m[0] <= 0.5:
                stops[0] = (0.0, "c" + m[1])
            elif m[0] >= L - 0.5:
                stops[-1] = (L, "c" + m[1])
        stops = [(s, ("c" + c) if not c.startswith(("j:", "c")) else c) for s, c in stops]
        z = heights.ground(xy)
        if p["kind"] == "bridge" and len(xy) >= 2:
            tree, zz = heights.decks(xy)
            chord = np.interp(scale, [scale[0], scale[-1]], [z[0], z[-1]])
            if tree is not None:
                for k, near in enumerate(tree.query_ball_point(xy, 3.5)):
                    if near:
                        v = zz[near]
                        z[k] = float(v[np.abs(v - chord[k] - 2.0).argmin()])
        known = np.isfinite(z)
        if not known.any():             # (past the export's edge)
            continue
        z = np.interp(scale, scale[known], z[known])
        pts = np.c_[xy, z]
        for (s0, n0), (s1, n1) in zip(stops[:-1], stops[1:]):
            if s1 - s0 < 0.3:
                continue
            inner = (scale > s0 + 1e-3) & (scale < s1 - 1e-3)
            q0 = [float(np.interp(s0, scale, pts[:, c])) for c in range(3)]
            q1 = [float(np.interp(s1, scale, pts[:, c])) for c in range(3)]
            seg = np.vstack([q0, pts[inner], q1])
            for d, (offs, lw) in lanes.items():
                if not offs:
                    continue
                turns = sp.turns_f if d > 0 else sp.turns_b
                line = seg if d > 0 else seg[::-1]
                a_id, b_id = (n0, n1) if d > 0 else (n1, n0)
                lid = len(links)
                speed = (sp.kmh / 3.6) if sp.kmh else SPEED_DEFAULT.get(sp.cls, 11.0)
                links.append({"id": lid, "from": a_id, "to": b_id,
                              "points": line.round(2).tolist(), "class": sp.cls, "ramp": bool(sp.link),
                              "level": p["level"], "name": p["name"], "oneway": bool(sp.oneway), "speed": round(speed, 1),
                              "width": round(width, 2), "lanes": [round(-o if d < 0 else o, 2) for o in offs],
                              "lane_width": round(lw, 2), "turns": list(turns) if turns else None, "control": None,
                              "control_source": None, "surface": sp.kind})
                node(a_id, line[0])["ends"].append(("out", lid))
                node(b_id, line[-1])["ends"].append(("in", lid))
    # junctions: nodes tied by a short link
    ids = list(nodes)
    index = {k: i for i, k in enumerate(ids)}
    parent = list(range(len(ids)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for lk in links:
        pts = np.array(lk["points"])
        if np.hypot(*np.diff(pts[:, :2], axis=0).T).sum() <= CLUSTER_M and lk["level"] == 0:
            parent[root(index[lk["from"]])] = root(index[lk["to"]])
    groups = {}
    for k in ids:
        arms = {lk for how, lk in nodes[k]["ends"]}
        if len({links[v]["name"] + links[v]["class"] for v in arms}) >= 2 or len(arms) >= 5:
            groups.setdefault(root(index[k]), []).append(k)
    junctions = []
    xy_all = {k: np.array(nodes[k]["xy"]) for k in ids}
    signals = infra.get("traffic_signals", np.zeros((0, 2)))
    sig_tree = cKDTree(signals) if len(signals) else None
    stops = np.concatenate([infra.get("stop", np.zeros((0, 2))), infra.get("give_way", np.zeros((0, 2)))])
    n_stop = len(infra.get("stop", ()))
    stop_tree = cKDTree(stops) if len(stops) else None
    for g in groups.values():
        c = np.mean([xy_all[k] for k in g], axis=0)
        jid = len(junctions)
        sig = sig_tree is not None and any(sig_tree.query(xy_all[k])[0] <= SIGNAL_NODE_M for k in g)
        for k in g:
            nodes[k]["junction"] = jid
        junctions.append({"id": jid, "nodes": g, "xy": [round(float(c[0]), 2), round(float(c[1]), 2)],
                          "control": "signal" if sig else None})
        into = [lk for k in g for how, lk in nodes[k]["ends"] if how == "in" and links[lk]["from"] not in g]
        if sig:
            for lk in into:
                links[lk]["control"], links[lk]["control_source"] = "signal", "mapped"
            continue
        top = max((RANK.get(links[lk]["class"], 1) for lk in into), default=0)
        n_top = sum(1 for lk in into if RANK.get(links[lk]["class"], 1) == top)
        for lk in into:
            end = np.array(links[lk]["points"][-1][:2])
            if stop_tree is not None:
                d, at = stop_tree.query(end)
                if d <= SIGN_NODE_M:
                    links[lk]["control"] = "stop" if at < n_stop else "give_way"
                    links[lk]["control_source"] = "mapped"
                    continue
            if RANK.get(links[lk]["class"], 1) < top and n_top >= 1:
                links[lk]["control"], links[lk]["control_source"] = "stop", "rule"
    # arms for the nodes
    out_nodes = {k: {"xy": v["xy"], "junction": v.get("junction"), "arms": len({lk for _, lk in v["ends"]})} for k, v in nodes.items()}
    doc = {"frame": {"lat": ue_export.FRAME[0], "lon": ue_export.FRAME[1], "axes": "x east, y north, z up, metres"},
           "links": links, "nodes": out_nodes, "junctions": junctions}
    path = a.out or os.path.join(a.net, "roadnet.json")
    with open(path, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    c = {}
    for lk in links:
        c[lk["control"]] = c.get(lk["control"], 0) + 1
    print(json.dumps({"links": len(links), "nodes": len(nodes), "junctions": len(junctions),
                      "signal_junctions": sum(1 for j in junctions if j["control"]), "control": {str(k): v for k, v in c.items()},
                      "mb": round(os.path.getsize(path) / 1e6, 1),
                      "s": round(time.perf_counter() - t0, 1)}))


if __name__ == "__main__":
    main()
