"""The surveyed fit (tools/ue_centres.py) put back into the road builders: each feature's vertices moved onto the middle of
its pavement and its width set to the measured one, before anything is built from them. Lane markings, sidewalks, decks,
grading and the road specs all read the features, so they follow without any change of their own.

    corrections(pieces) -> {"moves": {vertex key: (d lon, d lat)}, "widths": {fid: m}}; written once by save().
    install(path): in a worker, after blockworker.init: map cells come out corrected.
"""
import pickle

import numpy as np
from scipy import ndimage

MIN_MEASURED = 0.3              # share of a piece's stations measured for it to be moved
SIGMA_STATIONS = 5.0            # the correction smoothed along the road: about 20 m (a pocket's widening is not a shift)
END_BLEND_M = 30.0              # m over which a road's end takes its junction's shared move
MAX_MOVE_M = 7.0
WIDTH_MIN_STATIONS = 8          # stations of two surveyed lines for a measured width
WIDTH_CLASSES_OUT = {"track", "raceway"}
CROSSWALK_NEAR_M = 15.0         # m from a mapped crossing a painted one stays
SIGNAL_NEAR_M = 45.0            # m from a mapped signal
M_LAT = 111132.0


def _key(ll):
    return float(round(float(ll[0]), 7)), float(round(float(ll[1]), 7))


def _piece_moves(p):
    """V (n, 2): the smoothed move at each station of piece p (frame m), or None."""
    ab = p["ab"]
    ok = np.isfinite(ab[:, 0])
    if len(ok) < 3 or ok.mean() < MIN_MEASURED:
        return None
    t = p["t"]
    m = np.interp(t, t[ok], ab[ok].mean(1))
    m = ndimage.gaussian_filter1d(m, SIGMA_STATIONS, mode="nearest")
    m = np.clip(m, -MAX_MOVE_M, MAX_MOVE_M)
    return p["nrm"] * m[:, None]


def corrections(pieces, infra=None):
    moves = {}
    for p in pieces:
        if p["kind"] != "tunnel" and len(p["st"]):
            v = _piece_moves(p)
            if v is not None:
                moves[id(p)] = v
    # a junction's roads all end on one point: one move for all of them
    nodes = {}
    for p in pieces:
        v = moves.get(id(p))
        if v is None:
            continue
        for end, q in ((0, p["xy"][0]), (-1, p["xy"][-1])):
            nodes.setdefault((round(q[0]), round(q[1])), []).append(v[end])
    node_v = {k: np.mean(vs, axis=0) for k, vs in nodes.items()}
    out, widths = {}, {}
    w_vals = {}
    for p in pieces:
        v = moves.get(id(p))
        if v is not None:
            t = p["t"]
            for end, q, d in ((0, p["xy"][0], t), (-1, p["xy"][-1], t[-1] - t)):
                nv = node_v.get((round(q[0]), round(q[1])))
                if nv is not None:
                    w = np.clip(1.0 - d / END_BLEND_M, 0.0, 1.0)
                    v = v + w[:, None] * (nv - v[end])
            d = np.hypot(*np.diff(p["xy"], axis=0).T)
            s = np.r_[0.0, np.cumsum(d)]
            s *= t[-1] / max(s[-1], 1e-9)
            vx, vy = np.interp(s, t, v[:, 0]), np.interp(s, t, v[:, 1])
            k_lon = 1.0 / (M_LAT * np.cos(np.radians(p["ll"][:, 1])))
            for ll, dx, dy, kl in zip(p["ll"], vx, vy, k_lon):
                out[_key(ll)] = (float(dx * kl), float(dy / M_LAT))
        two = p["two"] & np.isfinite(p["ab"][:, 0]) if len(p["ab"]) else np.zeros(0, bool)
        if two.sum() >= WIDTH_MIN_STATIONS and p["spec"]["cls"] not in WIDTH_CLASSES_OUT:
            w_vals.setdefault(p["fid"], []).append(p["ab"][two, 1] - p["ab"][two, 0])
    for fid, vs in w_vals.items():
        widths[fid] = float(np.median(np.concatenate(vs)))
    return {"moves": out, "widths": widths, "infra": infra or {}}


def save(pieces, path, infra=None):
    with open(path, "wb") as f:
        pickle.dump(corrections(pieces, infra), f)


def _corrected(feats, moves):
    """feats with its vertices moved, once. Whatever was worked out from the old ones is forgotten."""
    if feats is None or feats.__dict__.get("_ue_fit"):
        return feats
    ll = np.array(feats.lonlat, np.float64)
    for k in range(len(ll)):
        d = moves.get(_key(ll[k]))
        if d is not None:
            ll[k, 0] += d[0]
            ll[k, 1] += d[1]
    feats.lonlat = ll
    for name in ("_xy_in", "_boxes", "_parts_by_level", "_road_specs"):
        feats.__dict__.pop(name, None)
    feats.__dict__["_ue_fit"] = True
    return feats


def _mapped_crosswalks_only(infra):
    """junctions.find's crosswalks kept only where the map has a crossing or a signal near (it would paint every corner of
    a town's grid, and the aerials show none on most of them)."""
    import junctions
    from scipy.spatial import cKDTree
    crossing = cKDTree(infra["crossing"])
    signals = cKDTree(infra["traffic_signals"]) if len(infra.get("traffic_signals", ())) else None
    find = junctions.find

    def mapped(*a, **kw):
        got = find(*a, **kw)
        if kw.get("pavement") or not got[2]:
            return got
        keep = []
        for c in got[2]:
            mid = (np.asarray(c["a"][:2], float) + np.asarray(c["b"][:2], float)) / 2
            if crossing.query(mid)[0] <= CROSSWALK_NEAR_M or (signals is not None and signals.query(mid)[0] <= SIGNAL_NEAR_M):
                keep.append(c)
        return (got[0], got[1], keep) + tuple(got[3:])
    junctions.find = mapped


def install(path):
    import blockworker
    import roads
    with open(path, "rb") as f:
        fit = pickle.load(f)
    moves, widths = fit["moves"], fit["widths"]
    md = blockworker._s.md
    cell = md.cell

    def fitted_cell(*a, **kw):
        got = cell(*a, **kw)
        layer = got.layers.get("roads") if hasattr(got, "layers") else None
        if layer is not None and len(layer):
            _corrected(layer, moves)
        return got
    md.cell = fitted_cell

    def widened(fn):
        def wrapped(feats, *a, **kw):
            out = fn(feats, *a, **kw)
            for i, sp in enumerate(out):
                if sp is not None:
                    w = widths.get(str(feats.ids[i]))
                    if w is not None:
                        out[i] = sp._replace(width=w)
            return out
        return wrapped
    # lane tags on a local street or a lot's aisle are not painted lines: a residential street here has no centre line
    painted = roads.CENTRE_CLASSES | roads.LANE_CLASSES
    tags = roads.has_lane_tags
    roads.has_lane_tags = lambda spec: tags(spec) and spec.cls in painted
    infra = fit.get("infra") or {}
    if len(infra.get("crossing", ())):
        _mapped_crosswalks_only(infra)
    roads.road_specs = widened(roads.road_specs)
    roads.bridge_specs = widened(roads.bridge_specs)
    roads.tunnel_specs = widened(roads.tunnel_specs)
