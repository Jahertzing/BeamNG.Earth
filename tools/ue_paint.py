#!/usr/bin/env python3
"""Lane paint that reads as one road. The builders paint each map segment on its own, its own width, lane count and centre, so
lines stopped at a node and started again a metre or six to the side, and ran on through junctions. Here a road is a corridor
through its nodes, and its paint comes from one lane plan along it:

    the drawn pavement's edges are measured at every station, smoothed along the road;
    the lanes are what the links say each way, or what fits at a real width; a change of lanes shorter than MIN_RUN_M is flicker;
    centre, lane lines and edge lines are offsets along the road that ease over a taper, never a step;
    nothing is painted through a junction: paint stops at the mouth, where the stop bar is. A road going straight past a T with no
    control of its own keeps its lines past it.

The builders' long lines along these roads go, in trunk to tertiary, and any lengthwise paint inside a junction; stop bars,
crosswalks, arrows and gores stay. Motorways and ramps keep theirs. Run after ue_dashes, before ue_paint_lift.

    python tools/ue_paint.py --dir <export dir> [--net <roadnet dir>] [--out <dir>] [--near x,y,r]
"""
import argparse
import collections
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

import markings  # noqa: E402,F401
import ue_furniture  # noqa: E402
import ue_settle  # noqa: E402

CELL = 2048.0
STEP_M = 2.0
PAINTED = {"trunk", "primary", "secondary", "tertiary"}
STREETS = PAINTED | {"motorway", "residential", "unclassified", "living_street"}
TURN_DEG = 35.0                 # a road goes on through a node within this turn
LANE_MIN_M, LANE_MAX_M = 3.0, 3.7
RAY_M = 24.0
EDGE_WINDOW_M = 40.0            # m of road the measured edges are medianed over (a driveway's mouth is not the road's edge)
EDGE_SIGMA_M = 12.0             # m: and smoothed
TAPER_SIGMA_M = 14.0            # m: a change of lanes eases out over about four times this
MIN_RUN_M = 45.0                # m: a stretch of another lane count shorter than this is the map's flicker
EDGE_CLEAR_M = 0.25             # m the outer line stays inside the pavement
MIN_CHAIN_M = 24.0
MOUTH_M = 2.0                   # m past the crossing road's edge paint stops
MOUTH_MAX_M = 30.0
CENTRE_W = 0.12
CENTRE_GAP = 0.12
LANE_W = 0.12
EDGE_W = 0.15
DASH = (3.05, 9.15)             # m paint, m gap (MUTCD 3B.04)
EDGE_CLASSES = {"trunk", "primary", "secondary"}
EDGE_MIN_SHOULDER_M = 0.5       # m of pavement outside the lanes before an edge line is drawn
ALONG_M = 15.0                  # m from our stations a builder's line is along a repainted road
PARTNER_M = 22.0                # m: the other carriageway of a divided road is looked for this near
CROSS_LEN_M = 6.0               # m: builders' paint parts longer than this along a road are its lines


def turn_of(p):
    u = np.diff(p, axis=0)
    a = np.unwrap(np.arctan2(u[:, 1], u[:, 0]))
    return float(np.abs(np.diff(a)).sum()) if len(a) > 1 else 0.0


def leave(p, k=4):
    u = p[min(k, len(p) - 1)] - p[0]
    return u / max(float(np.linalg.norm(u)), 1e-6)


class Seg:
    """A piece of a road between two nodes: both ways on one line, or one way."""

    def __init__(self, a, b, xy, cls, name, oneway, width):
        self.a, self.b, self.xy, self.cls, self.name, self.oneway, self.width = a, b, xy, cls, name, oneway, width
        self.fwd, self.back, self.gap = 1, 0 if oneway else 1, 0.0
        self.control = {a: None, b: None}
        self.used = False


def segments(links):
    """Seg for every drawn street piece, a two-way road's two links made one."""
    pairs = {}
    for lk in links:
        if lk["level"] != 0 or lk["ramp"] or lk["class"] not in STREETS:
            continue
        p = np.array(lk["points"])[:, :2]
        if len(p) < 2:
            continue
        key = (lk["from"], lk["to"]) if lk["oneway"] else tuple(sorted((lk["from"], lk["to"])))
        s = pairs.get(key)
        if s is None:
            a, b = lk["from"], lk["to"]
            if not lk["oneway"] and a != key[0]:
                a, b, p = b, a, p[::-1]
            s = pairs[key] = Seg(a, b, p, lk["class"], lk.get("name") or "", bool(lk["oneway"]), float(lk["width"]))
        n = max(1, len(lk["lanes"]))
        if lk["from"] == s.a:
            s.fwd = n
        else:
            s.back = n
        s.control[lk["to"]] = lk["control"]
        if not lk["oneway"] and len(lk["lanes"]):
            s.gap = max(s.gap, 2.0 * min(abs(float(v)) for v in lk["lanes"]) - float(lk["lane_width"] or 3.5))
    return list(pairs.values())


def chains_of(segs):
    at = collections.defaultdict(list)
    for s in segs:
        at[s.a].append(s)
        at[s.b].append(s)

    def out_of(s, node):
        return s.xy if s.a == node else s.xy[::-1]

    def next_seg(cur, node, u):
        best, score = None, math.cos(math.radians(TURN_DEG))
        for o in at[node]:
            if o is cur or o.used or o.oneway != cur.oneway or o.cls not in PAINTED:
                continue
            if o.oneway and o.a != node:
                continue
            c = float(leave(out_of(o, node)) @ u) + (0.25 if o.name and o.name == cur.name else 0.0)
            if c > score:
                best, score = o, c
        return best
    chains = []
    for s0 in sorted((s for s in segs if s.cls in PAINTED), key=lambda s: min(len(at[s.a]), len(at[s.b]))):
        if s0.used:
            continue
        s0.used = True
        run = [(s0, False)]
        for fwd in (True, False):
            if not fwd and s0.oneway:
                break
            cur, node = s0, s0.b if fwd else s0.a
            while True:
                o = next_seg(cur, node, -leave(out_of(cur, node)))
                if o is None:
                    break
                o.used = True
                back = o.a != node
                if fwd:
                    run.append((o, back))
                else:
                    run.insert(0, (o, not back))
                cur, node = o, (o.b if o.a == node else o.a)
        chains.append(run)
    return chains, at


def mouths(net, at):
    """node -> m its junction's paint stays back from it, and the stop reason: where a chain ends or has control."""
    out = {}
    jx = {j["id"]: j for j in net["junctions"]}
    for k, nd in net["nodes"].items():
        arms = at.get(k, ())
        j = nd.get("junction")
        if j in jx:
            arms = [s for q in jx[j]["nodes"] for s in at.get(q, ())]
        if len(arms) >= 3:
            out[k] = min(max(s.width for s in arms) / 2.0 + MOUTH_M, MOUTH_MAX_M)
    return out


def corridor(run):
    """The chain as stations: xy (n, 2), arc s, tangents, left normals, per station Seg index and lanes each way (along it, against it)."""
    pts, idx, fwd, back = [], [], [], []
    for k, (seg, flip) in enumerate(run):
        p = seg.xy[::-1] if flip else seg.xy
        if pts:
            p = p[1:] if len(p) > 1 else p
        pts.append(p)
        idx += [k] * len(p)
        f, b = (seg.back, seg.fwd) if flip else (seg.fwd, seg.back)
        fwd += [f] * len(p)
        back += [b] * len(p)
    P = np.concatenate(pts)
    arc = np.r_[0.0, np.cumsum(np.hypot(*np.diff(P, axis=0).T))]
    keep = np.r_[True, np.diff(arc) > 1e-6]
    P, arc, idx, fwd, back = P[keep], arc[keep], np.array(idx)[keep], np.array(fwd)[keep], np.array(back)[keep]
    if arc[-1] < MIN_CHAIN_M:
        return None
    s = np.arange(0.0, arc[-1], STEP_M)
    X = np.c_[np.interp(s, arc, P[:, 0]), np.interp(s, arc, P[:, 1])]
    pick = np.clip(np.searchsorted(arc, s, side="right") - 1, 0, len(arc) - 1)
    X = ndimage.gaussian_filter1d(X, 2.0, axis=0, mode="nearest")
    t = np.gradient(X, axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-6)
    return {"s": s, "X": X, "t": t, "n": np.c_[-t[:, 1], t[:, 0]], "seg": idx[pick], "f": fwd[pick].astype(float), "b": back[pick].astype(float), "arc": arc}


def node_arcs(run):
    """(arc of each node along the chain, node id, the seg before, the seg after)."""
    out = []
    arc = 0.0
    prev = None
    for seg, flip in run:
        p = seg.xy
        first, last = (seg.b, seg.a) if flip else (seg.a, seg.b)
        out.append((arc, first, prev, seg, flip))
        arc += float(np.hypot(*np.diff(p, axis=0).T).sum())
        prev = seg
    out.append((arc, last, prev, None, False))
    return out


def edges(w, c):
    """(left, right) m from the line to the drawn pavement's edge at each station; nan where the line is off the pavement."""
    t = np.arange(0.0, RAY_M, 0.25)
    out = []
    for side in (1.0, -1.0):
        Q = c["X"][:, None, :] + c["n"][:, None, :] * side * t[None, :, None]
        sd, _ = w.at(Q.reshape(-1, 2))
        sd = sd.reshape(len(c["X"]), len(t))
        off = sd > 0
        first = np.argmax(off, axis=1).astype(float) * 0.25
        first[~off.any(1)] = np.nan
        first[sd[:, 0] > 0.5] = np.nan
        out.append(first)
    return out


def steady(n, step):
    """A lane count along the road with runs shorter than MIN_RUN_M taken back to their longer neighbour."""
    n = n.copy()
    for _ in range(3):
        cuts = np.r_[0, np.flatnonzero(np.diff(n)) + 1, len(n)]
        runs = list(zip(cuts[:-1], cuts[1:]))
        changed = False
        for k, (a, b) in enumerate(runs):
            if (b - a) * step >= MIN_RUN_M or len(runs) == 1:
                continue
            left = (runs[k - 1][1] - runs[k - 1][0], n[runs[k - 1][0]]) if k else (0, None)
            right = (runs[k + 1][1] - runs[k + 1][0], n[runs[k + 1][0]]) if k + 1 < len(runs) else (0, None)
            v = left[1] if left[0] >= right[0] else right[1]
            if v is not None:
                n[a:b] = v
                changed = True
        if not changed:
            break
    return n


def shared_left(w, c, opposite):
    """m to the left of the line where this carriageway's half of a shared pavement ends, per station, nan with no other half: a
    divided road on one flush pavement has its two lines near each other, or crossed, and each would take the whole of it. The
    boundary is half way to the other line, whichever side the map has it: traffic keeps right of it."""
    tree, tan = opposite
    out = np.full(len(c["X"]), np.nan)
    d, k = tree.query(c["X"], k=48, distance_upper_bound=PARTNER_M)
    for row in range(len(c["X"])):
        best = None
        for dd, q in zip(d[row], k[row]):
            if q >= len(tan):
                break
            if not np.isfinite(dd) or float(tan[q] @ c["t"][row]) > -0.7:
                continue
            best = q
            break
        if best is None:
            continue
        v = tree.data[best] - c["X"][row]
        if v @ c["t"][row] > 12.0 or v @ c["t"][row] < -12.0:
            continue
        lat = float(v @ c["n"][row])
        sd, _ = w.at((c["X"][row] + c["n"][row] * lat / 2.0)[None])
        if sd[0] < 0:
            out[row] = lat / 2.0
    return out


def plan(w, c, oneway, opposite=None):
    """The offsets along the road: centre, right edge of the lanes, left edge, lane width, counts, pavement edges."""
    left, right = edges(w, c)
    win = int(EDGE_WINDOW_M / STEP_M) | 1
    if oneway and opposite is not None:
        cap = shared_left(w, c, opposite)
        left = np.where(np.isfinite(cap), cap, left)

    def smooth(v, fallback):
        v = np.where(np.isfinite(v), v, np.nan)
        if not np.isfinite(v).any():
            return np.full(len(v), fallback)
        i = np.arange(len(v))
        ok = np.isfinite(v)
        v = np.interp(i, i[ok], v[ok])
        v = ndimage.median_filter(v, size=win, mode="nearest")
        return ndimage.gaussian_filter1d(v, EDGE_SIGMA_M / STEP_M, mode="nearest")
    half = 3.6 * (c["f"] + c["b"]).mean() / 2.0
    # (a line on the wrong side of the middle has a negative left: the median keeps it)
    L = smooth(left, half)
    R = smooth(right, half)
    W = L + R
    n = steady(np.maximum(c["f"] + c["b"], 1.0), STEP_M)
    f = steady(c["f"], STEP_M)
    b = steady(c["b"], STEP_M)
    # (no more lanes than the pavement holds at a real width; two-way stays two)
    fit = np.floor(W / LANE_MIN_M)
    over = (f + b) - fit
    for k in np.flatnonzero(over > 0):
        while f[k] + b[k] > max(fit[k], 2.0 if not oneway else 1.0):
            if f[k] >= b[k] and f[k] > 1:
                f[k] -= 1
            elif b[k] > (0 if oneway else 1):
                b[k] -= 1
            else:
                break
    f, b = steady(f, STEP_M), steady(b, STEP_M)
    total = np.maximum(f + b, 1.0)
    lw = np.clip((W - 0.6) / total, LANE_MIN_M, LANE_MAX_M)
    lw = ndimage.gaussian_filter1d(lw, EDGE_SIGMA_M / STEP_M, mode="nearest")
    sig = TAPER_SIGMA_M / STEP_M
    F = ndimage.gaussian_filter1d(f * lw, sig, mode="nearest")
    B = ndimage.gaussian_filter1d(b * lw, sig, mode="nearest")
    mid = (L - R) / 2.0                     # the pavement's middle, left of the line
    # (right lanes stack from the centre down, left ones up; the whole sits in the middle of the pavement)
    centre = mid + (F - B) / 2.0
    centre = ndimage.gaussian_filter1d(centre, EDGE_SIGMA_M / STEP_M, mode="nearest")
    return {"centre": centre, "F": F, "B": B, "lw": lw, "f": f, "b": b, "L": L, "R": R}


def stops(run, c, mouth):
    """Stations where paint is cut: at a junction this road is stopped at, or crosses."""
    cut = np.zeros(len(c["s"]), bool)
    for arc, node, before, after, _ in node_arcs(run):
        r = mouth.get(node)
        if r is None:
            continue
        controlled = any(s is not None and s.control.get(node) for s in (before, after))
        if not controlled and before is not None and after is not None:
            continue            # (straight on past a side road)
        cut |= np.abs(c["s"] - arc) < r
    return cut


class Strips:
    """Paint as ribbons, by cell and material."""

    def __init__(self):
        self.parts = collections.defaultdict(list)

    def add(self, mat, line, width):
        """line (n, 2) a run of stations: a ribbon each segment in the cell of its middle."""
        if len(line) < 2:
            return
        t = np.gradient(line, axis=0)
        n = np.c_[-t[:, 1], t[:, 0]] / np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-6)
        a, b = line + n * width / 2, line - n * width / 2
        mid = (line[:-1] + line[1:]) / 2
        cell = np.floor(mid / CELL).astype(np.int64)
        for k in range(len(mid)):
            self.parts[(int(cell[k, 0]), int(cell[k, 1]), mat)].append((a[k], b[k], a[k + 1], b[k + 1]))


def runs_of(mask):
    cuts = np.flatnonzero(np.diff(np.r_[0, mask.astype(np.int8), 0]))
    return list(zip(cuts[::2], cuts[1::2]))


def paint_chain(w, run, mouth, st, stat, opposite):
    c = corridor(run)
    if c is None:
        return False
    oneway = run[0][0].oneway
    if oneway and turn_of(c["X"][::5]) > 5.0 and c["s"][-1] < 400.0:
        return False            # (a roundabout's ring)
    p = plan(w, c, oneway, opposite if oneway else None)
    X, n = c["X"], c["n"]
    cut = stops(run, c, mouth)
    live = ~cut
    cls = run[len(run) // 2][0].cls
    on = np.isfinite(w.at(X)[0])
    live &= on

    def at(off):
        return X + n * off[:, None]
    for a, b in runs_of(live):
        if (b - a) * STEP_M < MIN_CHAIN_M / 2:
            continue
        rng = slice(a, b)
        o = p["centre"][rng]
        F, B = p["F"][rng], p["B"][rng]
        lw = p["lw"][rng]
        XX, nn = X[rng], n[rng]

        def line(off):
            return XX + nn * off[:, None]
        if not oneway:
            for sd in (1, -1):
                st.add("line_yellow", line(o + sd * (CENTRE_GAP + CENTRE_W) / 2), CENTRE_W)
        # lane lines, kept to where the lanes are there
        for k in range(1, int(max(p["f"][rng].max(), 1))):
            have = F >= (k + 0.9) * lw
            for u, v in runs_of(have):
                dash(st, line(o - k * lw)[u:v], c["s"][a + u:a + v])
        for k in range(1, int(max(p["b"][rng].max(), 1))):
            have = B >= (k + 0.9) * lw
            for u, v in runs_of(have):
                dash(st, line(o + k * lw)[u:v], c["s"][a + u:a + v])
        right_off, left_off = o - F, o + B
        low, high = -p["R"][rng] + EDGE_CLEAR_M, p["L"][rng] - EDGE_CLEAR_M
        if oneway:              # (a carriageway's left edge, the yellow that keeps traffic off the other half, always)
            st.add("line_yellow", line(np.minimum(left_off, high)), EDGE_W)
        if cls in EDGE_CLASSES:
            sides = [(right_off, p["R"][rng] + right_off)]
            if not oneway:
                sides.append((left_off, p["L"][rng] - left_off))
            for off, room in sides:
                for u, v in runs_of(room >= EDGE_MIN_SHOULDER_M):
                    if (v - u) * STEP_M >= 20.0:
                        st.add("line_white", line(np.clip(off, low, high))[u:v], EDGE_W)
    stat["km"] += float(c["s"][-1]) / 1000.0
    stat["chains"] += 1
    return c


def dash(st, line, s):
    """Dashes along a run of stations, on the road's own arc, so a line stays in step past a gap."""
    if len(line) < 2:
        return
    period = DASH[0] + DASH[1]
    k0, k1 = int(s[0] // period), int(s[-1] // period) + 1
    for k in range(k0, k1):
        a, b = k * period, k * period + DASH[0]
        if b <= s[0] or a >= s[-1]:
            continue
        t = np.linspace(max(a, s[0]), min(b, s[-1]), 3)
        st.add("line_white", np.c_[np.interp(t, s, line[:, 0]), np.interp(t, s, line[:, 1])], LANE_W)


def rewrite(export, out, cell, strips, tree, cut_tree, cut_r, heights):
    """The cell's road mesh: builders' lengthwise paint along the painted roads and in the junctions out, ours in."""
    i, j = cell
    path = os.path.join(export, f"road_{i}_{j}.glb")
    if not os.path.exists(path):
        return 0, 0
    P, UV, C, T, M, names = ue_settle.read(path)
    org = np.array([i * CELL, j * CELL])
    names = list(names)
    paint = [k for k, n in enumerate(names) if n.startswith("line_")]
    drop = np.zeros(len(T), bool)
    if paint:
        pm = np.isin(M, paint)
        lab = ue_settle.parts_of(P, T[pm])
        tl = lab[T[pm][:, 0]]
        c = P[T[pm]].mean(1)[:, :2] + org
        order = np.argsort(tl)
        cuts = np.flatnonzero(np.diff(tl[order])) + 1
        where = np.flatnonzero(pm)
        for g in np.split(order, cuts):
            q = c[g]
            span = q.max(0) - q.min(0)
            long_ = float(np.hypot(*span)) >= CROSS_LEN_M
            mid = q.mean(0)
            along = tree.query(mid)[0] <= ALONG_M
            dashed = names[int(M[where[g[0]]])].startswith("line_dashed")
            if (long_ or dashed) and along:
                drop[where[g]] = True
            else:
                d, k = cut_tree.query(mid)
                if d < cut_r[k] and (float(np.hypot(*span)) >= 2.5):
                    drop[where[g]] = True
    P2, UV2, C2, T2, M2 = [P], [UV], [C], [T[~drop]], [M[~drop]]
    n = len(P)
    tris = 0
    for mat in ("line_white", "line_yellow"):
        got = strips.parts.get((i, j, mat))
        if not got:
            continue
        if mat not in names:
            names.append(mat)
        m = names.index(mat)
        q = np.array(got)
        k = len(q)
        pts = q.reshape(-1, 2) - org
        z = heights(pts + org)                  # (ue_paint_lift settles it on the drawn terrain)
        P2.append(np.c_[pts, z])
        UV2.append(np.zeros((len(pts), 2)))
        C2.append(np.full((len(pts), C.shape[1]), 255, C.dtype))
        base = n + 4 * np.arange(k)[:, None]
        T2.append(np.r_[base + (0, 1, 2), base + (2, 1, 3)])
        M2.append(np.full(2 * k, m, np.int64))
        n += len(pts)
        tris += 2 * k
    ue_settle.write(os.path.join(out, f"road_{i}_{j}.glb"), f"road_{i}_{j}", np.concatenate(P2), np.concatenate(UV2), np.concatenate(C2), np.concatenate(T2), np.concatenate(M2), names)
    return tris, int(drop.sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--out", default=None)
    ap.add_argument("--near", default=None, help="x,y,r: only chains near here, for a look")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    out = a.out or a.dir
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(a.net, "roadnet.json")) as f:
        net = json.load(f)
    cells = sorted((int(f.split("_")[1]), int(f.split("_")[2].split(".")[0])) for f in os.listdir(a.dir) if f.startswith("road_") and f.endswith(".glb"))
    near = tuple(float(v) for v in a.near.split(",")) if a.near else None
    if near:
        cells = [c for c in cells if abs(c[0] * CELL + 1024 - near[0]) < 1024 + near[2] and abs(c[1] * CELL + 1024 - near[1]) < 1024 + near[2]]
    w = ue_furniture.World(a.dir, cells, a.jobs)
    segs = segments(net["links"])
    chains, at = chains_of(segs)
    mouth = mouths(net, at)
    st = Strips()
    stat = collections.Counter()
    stations = []
    one = [(c["X"], c["t"]) for c in (corridor(r) for r in chains if r[0][0].oneway) if c is not None]
    opposite = (cKDTree(np.concatenate([v[0] for v in one])), np.concatenate([v[1] for v in one])) if one else None
    for run in chains:
        if near and min(float(np.hypot(*(s.xy - near[:2]).T).min()) for s, _ in run) > near[2]:
            continue
        c = paint_chain(w, run, mouth, st, stat, opposite)
        if c:
            stations.append(c["X"])
    stops_xy, stops_r = [], []
    for k, r in mouth.items():
        if len({id(s) for s in at.get(k, ())}) >= 4 or any(s.control.get(k) == "signal" for s in at.get(k, ())):
            xy = next((s.xy[0] if s.a == k else s.xy[-1]) for s in at[k])
            stops_xy.append(xy)
            stops_r.append(r)
    tree = cKDTree(np.concatenate(stations))
    cut_tree, cut_r = cKDTree(np.array(stops_xy)), np.array(stops_r)
    done = [0, 0]
    for c in cells:
        got = rewrite(a.dir, out, c, st, tree, cut_tree, cut_r, w.h.ground)
        done[0] += got[0]
        done[1] += got[1]
    print(json.dumps({"km": round(stat["km"]), "chains": stat["chains"], "tris": done[0], "builder_tris_dropped": done[1]}))


if __name__ == "__main__":
    main()
