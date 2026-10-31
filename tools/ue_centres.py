#!/usr/bin/env python3
"""Each road piece against the surveyed curbs: every STATION_M along its centreline, one ray across it, the pavement between
neighbouring lines fitted along the road (fit_piece). stations.pkl keeps, per piece, the stations, their left normals and
ab (n, 2): the pavement's two edges in m along the normal (nan: not measured there).

    Earth.UE\\.venv\\Scripts\\python tools\\ue_centres.py --dir C:/beamng-earth/Earth.UE/export/roadnet
"""
import argparse
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

import geodesy  # noqa: E402
import ue_fit  # noqa: E402
import ue_survey  # noqa: E402

STATION_M = 4.0
STEP_M = 0.125
CELL_M = 2048.0
MARGIN_M = 48.0
NONE_COST = {"service": 1.4, "track": 0.5, "unknown": 1.4}      # the "no pavement measured here" state, by class
NONE_DEFAULT = 2.5
WIDTH_COST_CAP = 0.6            # a service road's pavement around the line is never dearer than this for its width ...
SERVICE_MAX_W = 16.0            # ... up to this wide
ROAD_COST_CAP = 1.5             # the same for a road
ROAD_MAX_W = 30.0
SERVICE_LIKE = {"service", "unknown"}
HALF_COST = 2.0                 # a pavement with one surveyed side, its width the spec's
MIN_W, MAX_W = 2.5, 42.0        # m a pavement between two lines
FILL_M = 72.0                   # m a gap (a junction's mouth, under a deck) is bridged from its two sides
REACH_M = 34.0
SIBLING_M = 42.0                # m: a divided road's other carriageway looked for this far
PAINTED_MEDIAN_M = 5.0          # m: a gap this narrow between carriageways with no curb in it is paved
NO_SIBLING = {"service", "track", "unknown", "raceway"}


def stations(xy, step=STATION_M):
    """(points (n, 2), left normals (n, 2), m along) every step along polyline xy, its ends in."""
    d = np.hypot(*np.diff(xy, axis=0).T)
    keep = np.r_[True, d > 1e-6]
    xy = xy[keep]
    if len(xy) < 2:
        return np.zeros((0, 2)), np.zeros((0, 2)), np.zeros(0)
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    n = max(2, int(round(s[-1] / step)) + 1)
    t = np.linspace(0.0, s[-1], n)
    p = np.c_[np.interp(t, s, xy[:, 0]), np.interp(t, s, xy[:, 1])]
    # the direction over about 8 m: a map vertex's kink is not the road's
    a = np.c_[np.interp(np.maximum(t - 4.0, 0), s, xy[:, 0]), np.interp(np.maximum(t - 4.0, 0), s, xy[:, 1])]
    b = np.c_[np.interp(np.minimum(t + 4.0, s[-1]), s, xy[:, 0]), np.interp(np.minimum(t + 4.0, s[-1]), s, xy[:, 1])]
    u = b - a
    u /= np.maximum(np.hypot(*u.T), 1e-9)[:, None]
    return p, np.c_[-u[:, 1], u[:, 0]], t


def candidates(ts, hits, w0):
    """[(a, b, half)] pavements at a station: pairs of neighbouring lines on the ray ts, or one line and the spec's width."""
    at = ts[hits]
    if not len(at):
        return []
    runs = [at[0]]
    for t in at[1:]:
        if t - runs[-1] > 0.6:
            runs.append(t)
        else:
            runs[-1] = (runs[-1] + t) / 2
    out = [(a, b, 0) for a, b in zip(runs[:-1], runs[1:]) if MIN_W <= b - a <= MAX_W]
    left = [t for t in runs if t < 0]
    right = [t for t in runs if t > 0]
    if right:
        out.append((right[0] - w0, right[0], 1))
    if left:
        out.append((left[-1], left[-1] + w0, 1))
    return out


def siblings(pieces):
    """{(piece, station): lateral m} the other carriageway of a divided road beside each station: the nearest one-way road
    running the other way within SIBLING_M, left of the normal when positive. With no surveyed median between them both
    carriageways see one pavement, and both would be moved onto its middle."""
    P, T, O, K = [], [], [], []
    for k, p in enumerate(pieces):
        sp = p["spec"]
        if sp["oneway"] and not sp["link"] and sp["cls"] not in NO_SIBLING and len(p["st"]) and p["kind"] != "tunnel":
            d = float(sp["dir"] or 1)
            P.append(p["st"])
            T.append(np.c_[p["nrm"][:, 1], -p["nrm"][:, 0]] * d)
            O.append(np.full(len(p["st"]), k))
            K.append(np.arange(len(p["st"])))
    if not P:
        return {}
    P, T, O, K = np.concatenate(P), np.concatenate(T), np.concatenate(O), np.concatenate(K)
    near = cKDTree(P).query_ball_point(P, SIBLING_M)
    out = {}
    for i, got in enumerate(near):
        got = np.array(got)
        got = got[O[got] != O[i]]
        if not len(got):
            continue
        got = got[(T[got] @ T[i]) < -0.9]
        if not len(got):
            continue
        p = pieces[int(O[i])]
        lat = (P[got] - P[i]) @ p["nrm"][K[i]]
        lat = lat[np.abs(lat) >= 2.0]
        if len(lat):
            out[(int(O[i]), int(K[i]))] = float(lat[np.abs(lat).argmin()])
    return out


def split_median(cands, ts, w0):
    """Candidates at a station with the other carriageway at lateral ts: a pavement holding both is cut to this one's: at
    the middle where what lies between them is narrow enough to be a painted median, else the spec's width from the outer
    curb (a median the survey does not have)."""
    out = []
    for a, b, half in cands:
        if a < ts < b:
            mid = (a + b) / 2
            gap = (b - a) - 2 * w0
            if gap <= PAINTED_MEDIAN_M:
                inner = mid
            else:
                inner = a + w0 if ts > 0 else b - w0
            a, b = (a, inner) if ts > 0 else (inner, b)
            if b - a < MIN_W:
                continue
        out.append((a, b, half))
    return out


def fit_piece(cands, spec):
    """The pavement (a, b) along a piece's stations (Viterbi: near the centreline, near the spec's width, steady along the
    road), (n, 2) nan where it is not measured, and whether each is two surveyed lines."""
    w0 = float(spec["width"] or 8.0)
    none = NONE_COST.get(spec["cls"], NONE_DEFAULT)
    n = len(cands)
    prev, back = None, []
    for c in cands:
        states = [(np.nan, np.nan, 2)] + c
        A = np.array([s[0] for s in states])
        B = np.array([s[1] for s in states])
        half = np.array([s[2] for s in states])
        d0 = np.maximum(np.maximum(A, -B), 0.0)
        # (a street mapped as a service road is 12 m of curbed pavement: a width off the spec is capped, the survey wins; a
        # ramp is never capped: its spec is close, and round it the lines close on the junction's whole channel)
        wcost = 0.25 * np.abs(B - A - w0)
        if spec["link"]:
            pass
        elif spec["cls"] in SERVICE_LIKE:
            wcost = np.where(B - A <= SERVICE_MAX_W, np.minimum(wcost, WIDTH_COST_CAP), wcost)
        else:
            wcost = np.where(B - A <= ROAD_MAX_W, np.minimum(wcost, ROAD_COST_CAP), wcost)
        cost = d0 + wcost + HALF_COST * (half == 1)
        cost[0] = none
        if prev is None:
            total, bp = cost, np.zeros(len(states), np.int64)
        else:
            Pa, Pb, pc = prev
            tr = 0.7 * (np.abs(A[:, None] - Pa[None]) + np.abs(B[:, None] - Pb[None]))
            tr[np.isnan(tr)] = 0.5
            tr[np.isnan(A)[:, None] & np.isnan(Pa)[None]] = 0.0
            sum_ = pc[None] + tr
            bp = sum_.argmin(1)
            total = cost + sum_[np.arange(len(states)), bp]
        back.append((A, B, half, bp))
        prev = (A, B, total)
    k = int(prev[2].argmin())
    ab, kind = np.full((n, 2), np.nan), np.zeros(n, np.int64)
    for i in range(n - 1, -1, -1):
        A, B, half, bp = back[i]
        ab[i] = A[k], B[k]
        kind[i] = half[k]
        k = int(bp[k])
    return ab, kind == 0


def smooth_fill(ab, t):
    """ab with a running median over about 28 m and its gaps up to FILL_M filled along t; nan where it stays unmeasured."""
    ok = np.isfinite(ab[:, 0])
    if not ok.any():
        return ab
    out = ab.copy()
    for c in (0, 1):
        v = np.interp(t, t[ok], ab[ok, c])
        out[:, c] = ndimage.median_filter(v, size=min(7, len(v) | 1), mode="nearest")
    # distance across the gap each station sits in: a wide gap, or past the measured ends, stays unmeasured
    before = np.where(ok, t, -np.inf)
    before = np.maximum.accumulate(before)
    after = np.where(ok, t, np.inf)
    after = np.minimum.accumulate(after[::-1])[::-1]
    out[(after - before) > FILL_M] = np.nan
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/roadnet")
    a = ap.parse_args()
    t0 = time.perf_counter()
    with open(os.path.join(a.dir, "net.pkl"), "rb") as f:
        net = pickle.load(f)
    frame = geodesy.EnuFrame(*ue_export.FRAME)
    b = net.get("box") or (-net["half"], -net["half"], net["half"], net["half"])
    lines, missing = ue_survey.lines_in(frame, (b[0] - 64.0, b[1] - 64.0, b[2] + 64.0, b[3] + 64.0))
    km = sum(float(np.hypot(*np.diff(ln, axis=0).T).sum()) for ln in lines) / 1000
    print(f"{len(lines)} surveyed lines, {km:.0f} km, {missing} cells missing, {time.perf_counter() - t0:.0f} s", flush=True)
    with open(os.path.join(a.dir, "survey.pkl"), "wb") as f:
        pickle.dump(lines, f)
    pieces = net["pieces"]
    all_p, all_n, owner = [], [], []
    for k, p in enumerate(pieces):
        q, nrm, t = stations(p["xy"])
        p["t"], p["st"], p["nrm"] = t, q, nrm
        all_p.append(q)
        all_n.append(nrm)
        owner.append(np.full(len(q), k))
    P, N, O = np.concatenate(all_p), np.concatenate(all_n), np.concatenate(owner)
    ts = np.arange(-int(REACH_M / STEP_M), int(REACH_M / STEP_M) + 1) * STEP_M
    W0 = np.array([float(p["spec"]["width"] or 8.0) for p in pieces])[O]
    sib = siblings(pieces)
    print(f"{len(sib)} stations beside another carriageway, {time.perf_counter() - t0:.0f} s", flush=True)
    cands = [None] * len(P)
    c = np.floor(P / CELL_M).astype(np.int64)
    code = c[:, 0] * 100000 + c[:, 1]
    for u in np.unique(code):
        idx = np.flatnonzero(code == u)
        i, j = c[idx[0]]
        box = (i * CELL_M - MARGIN_M, j * CELL_M - MARGIN_M, (i + 1) * CELL_M + MARGIN_M, (j + 1) * CELL_M + MARGIN_M)
        ras = ue_survey.Raster(lines, box)
        ras.mask = ndimage.binary_dilation(ras.mask, structure=np.ones((2, 2), bool))
        for chunk in np.array_split(idx, max(1, len(idx) // 4000)):
            rays = P[chunk, None, :] + N[chunk, None, :] * ts[None, :, None]
            hit = ras.at(rays)
            for q, h in zip(chunk.tolist(), hit):
                cands[q] = candidates(ts, h, W0[q]) if h.any() else []
    print(f"rays done, {time.perf_counter() - t0:.0f} s", flush=True)
    off = 0
    for k, (p, q) in enumerate(zip(pieces, all_p)):
        n = len(q)
        if n:
            here = cands[off:off + n]
            for i in range(n):
                ts = sib.get((k, i))
                if ts is not None:
                    here[i] = split_median(here[i], ts, float(p["spec"]["width"] or 8.0))
            ab, two = fit_piece(here, p["spec"])
            p["two"] = two
            p["ab"] = smooth_fill(ab, p["t"])
        else:
            p["two"], p["ab"] = np.zeros(0, bool), np.zeros((0, 2))
        off += n
    with open(os.path.join(a.dir, "stations.pkl"), "wb") as f:
        pickle.dump(pieces, f)
    ue_fit.save(pieces, os.path.join(a.dir, "fit.pkl"), net.get("infra"))
    rows = {}
    for p in pieces:
        if p["kind"] != "ground" or not len(p["ab"]):
            continue
        s = rows.setdefault(p["spec"]["cls"] + ("/link" if p["spec"]["link"] else ""),
                            {"n": 0, "two": 0, "w": [], "sw": [], "off": []})
        ok = np.isfinite(p["ab"][:, 0])
        s["n"] += len(ok)
        s["two"] += int(p["two"].sum())
        if ok.any():
            w, o = p["ab"][ok, 1] - p["ab"][ok, 0], p["ab"][ok].mean(1)
            s["w"].append(w)
            s["sw"].append(np.full(len(w), float(p["spec"]["width"] or 0)))
            s["off"].append(o)
    for k in sorted(rows, key=lambda v: -rows[v]["n"]):
        s = rows[k]
        if not s["w"]:
            print(f"{k:22s} stations {s['n']:7d} measured 0")
            continue
        w, sw, o = np.concatenate(s["w"]), np.concatenate(s["sw"]), np.concatenate(s["off"])
        print(f"{k:22s} stations {s['n']:7d} two-line {s['two'] / s['n']:.2f} kept {len(w) / s['n']:.2f} "
              f"width med {np.median(w):5.1f} (spec {np.median(sw):5.1f}) |w-spec| p50 {np.median(abs(w - sw)):4.1f} "
              f"p90 {np.percentile(abs(w - sw), 90):4.1f}  |offset| p50 {np.median(abs(o)):4.2f} "
              f"p90 {np.percentile(abs(o), 90):4.2f}")
    print(f"{time.perf_counter() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
