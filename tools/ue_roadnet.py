#!/usr/bin/env python3
"""The road network of a box as data: every road piece in the fixed frame with its spec, level and map connectors, and an
audit of the stacked places. No meshes here; tools/ue_pavement.py and the traffic export read net.pkl.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_roadnet.py --km 20 --out C:/beamng-earth/Earth.UE/export/roadnet

A piece is one run of a feature at one level: kind "ground", "bridge" or "tunnel". Two pieces crossing in plan with no
shared connector and the same level are a phantom at-grade crossing: the audit lists them.
"""
import argparse
import json
import os
import pickle
import sys
import time

os.environ.setdefault("BNE_DRCOG", "1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402  (the world folder, server/ on the path)

import numpy as np  # noqa: E402

SPEC_FIELDS = ("width", "kind", "urban", "cls", "oneway", "dir", "kmh", "gap", "median", "lanes", "surf", "link", "lanes_f",
               "lanes_b", "lanes_c", "turns_f", "turns_b", "shoulder", "lsrc", "park", "sides", "walk")


def load_features(cache_dir, box):
    """[(feats)] of the "roads" layer in the cells meeting box (x0, y0, x1, y1 frame m), and the frame, urban and widths."""
    import blockworker
    import geodesy
    import mapcells
    blockworker.init(cache_dir, True, True, True, True, True, None, 40, True, True)
    s = blockworker._s
    frame = geodesy.EnuFrame(*ue_export.FRAME)
    x0, y0, x1, y1 = box
    lat_a, lon_a, _ = frame.enu_to_geodetic(x0, y0, 0.0)
    lat_b, lon_b, _ = frame.enu_to_geodetic(x1, y1, 0.0)
    a, b = mapcells.cell_of(float(lat_a), float(lon_a)), mapcells.cell_of(float(lat_b), float(lon_b))
    cells, keys = [], []
    for x in range(min(a.x, b.x), max(a.x, b.x) + 1):
        for y in range(min(a.y, b.y), max(a.y, b.y) + 1):
            keys.append(type(a)(a.z, x, y))
            f = s.md.cell(keys[-1], layers=("roads",), kind="demand").layers.get("roads")
            if f is not None and len(f):
                cells.append(f)
    return cells, frame, (s.surface.urban if s.surface is not None else None), s.widths, keys, s.md


def connectors_of(feats, i, frame=None):
    """[(connector id, share along feature i, x, y)] of its map connectors, in order (x, y: nan without a frame)."""
    import mapgeom
    t = feats.rules.get("connectors")
    if t is None or not len(t.feature):
        return []
    got = sorted(((str(t.value[j]), float(t.start[j])) for j in t.rows(i)), key=lambda v: v[1])
    ring = next((r for part in feats.parts(i) for r in part if len(r) >= 2), None)
    if ring is None or frame is None:
        return [(c, a, float("nan"), float("nan")) for c, a in got]
    e, n = mapgeom.enu_xy(feats, frame, ring)
    d = np.r_[0.0, np.cumsum(np.hypot(np.diff(e), np.diff(n)))]
    return [(c, a, float(np.interp(a * d[-1], d, e)), float(np.interp(a * d[-1], d, n))) for c, a in got]


def pieces_of(cells, frame, urban, widths):
    """Every road piece, once per feature id."""
    import mapgeom
    import roads
    seen = {}
    pieces = []
    for feats in cells:
        specs = roads.road_specs(feats, urban, widths, False)
        bspecs = roads.bridge_specs(feats)
        tspecs = roads.tunnel_specs(feats)
        for i in range(len(feats)):
            fid = str(feats.ids[i])
            if fid in seen:
                continue
            sp, bsp, tsp = specs[i], bspecs[i], tspecs[i]
            if sp is None and bsp is None and tsp is None:
                continue
            seen[fid] = True
            name = feats.attrs["name"][i] if "name" in feats.attrs else ""
            cons = connectors_of(feats, i, frame)
            ranges = roads.level_ranges(feats, i)
            for kind, parts, spec in (("ground", roads.ground_parts(feats, i), sp),
                                      ("bridge", roads.bridge_parts(feats, i), bsp),
                                      ("tunnel", roads.tunnel_parts(feats, i), tsp)):
                if spec is None:
                    continue
                lv = roads.bridge_levels(feats, i) if kind == "bridge" else None
                for k, ring in enumerate(parts):
                    if len(ring) < 2:
                        continue
                    e, n = mapgeom.enu_xy(feats, frame, ring)
                    level = 0.0 if kind == "ground" else (lv[k] if lv and k < len(lv) else 1.0) if kind == "bridge" else -1.0
                    pieces.append({"fid": fid, "k": k, "kind": kind, "level": float(level), "name": str(name or ""),
                                   "xy": np.c_[e, n], "ll": np.array(ring, np.float64),
                                   "spec": {f: getattr(spec, f, None) for f in SPEC_FIELDS}, "connectors": cons,
                                   "ranges": [(float(a), float(b), str(c)) for a, b, c in ranges]})
    return pieces


def infrastructure(cells_keys, md, frame):
    """{class: points (n, 2)} of the mapped crossings, signals, stop and give-way signs, and the parking lots' rings."""
    want = {"crossing": [], "traffic_signals": [], "stop": [], "give_way": []}
    lots, seen = [], set()
    for key in cells_keys:
        lay = md.cell(key, layers=("infrastructure",), kind="demand").layers.get("infrastructure")
        if lay is None or not len(lay):
            continue
        cls, sub = lay.attrs.get("class"), lay.attrs.get("subtype")
        for i in range(len(lay)):
            fid = str(lay.ids[i])
            if fid in seen:
                continue
            seen.add(fid)
            c, t = str(cls[i]), str(sub[i])
            if c not in want and c != "parking":
                continue
            for part in lay.parts(i):
                for ring in part:
                    e, n, _ = frame.geodetic_to_enu(ring[:, 1], ring[:, 0], 0.0)
                    xy = np.c_[e, n]
                    if c == "parking" and t == "transit" and len(xy) >= 4:
                        lots.append(xy)
                    elif c in want:
                        want[c].append(xy.mean(0))
    return {k: np.array(v).reshape(-1, 2) for k, v in want.items()}, lots


def audit(pieces):
    """{"phantom": at-grade crossings with no connector, "stacks": places with 3+ levels} from the pieces' plan crossings."""
    from shapely import LineString, STRtree
    lines = [LineString(p["xy"]) for p in pieces]
    tree = STRtree(lines)
    a, b = tree.query(lines, predicate="crosses")
    phantom, overs = [], []
    for i, j in zip(a.tolist(), b.tolist()):
        if i >= j:
            continue
        p, q = pieces[i], pieces[j]
        if p["fid"] == q["fid"]:
            continue
        x = lines[i].intersection(lines[j])
        c = x.representative_point()
        at = [round(c.x, 1), round(c.y, 1)]
        shared = {c[0] for c in p["connectors"]} & {c[0] for c in q["connectors"]}
        if p["level"] == q["level"]:
            if not shared:
                phantom.append({"at": at, "a": [p["fid"], p["spec"]["cls"], p["name"]], "b": [q["fid"], q["spec"]["cls"], q["name"]],
                                "level": p["level"]})
        else:
            overs.append({"at": at, "levels": sorted((p["level"], q["level"])), "classes": [p["spec"]["cls"], q["spec"]["cls"]]})
    return {"phantom": phantom, "overs": overs}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--km", type=float, default=20.48)
    ap.add_argument("--box", default=None, help="x0,y0,x1,y1 frame m, in place of --km")
    ap.add_argument("--out", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--cache-dir", default=os.environ["BNGE_WORLD_CACHE"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    half = a.km * 500.0
    box = tuple(float(v) for v in a.box.split(",")) if a.box else (-half, -half, half, half)
    t0 = time.perf_counter()
    cells, frame, urban, widths, keys, md = load_features(os.path.abspath(a.cache_dir), box)
    print(f"{len(cells)} cells, {time.perf_counter() - t0:.0f} s", flush=True)
    pieces = pieces_of(cells, frame, urban, widths)
    print(f"{len(pieces)} pieces, {time.perf_counter() - t0:.0f} s", flush=True)
    found = audit(pieces)
    infra, lots = infrastructure(keys, md, frame)
    print({k: len(v) for k, v in infra.items()}, "lots", len(lots), flush=True)
    with open(os.path.join(a.out, "net.pkl"), "wb") as f:
        pickle.dump({"pieces": pieces, "frame": ue_export.FRAME, "half": half, "box": box, "infra": infra, "lots": lots}, f)
    with open(os.path.join(a.out, "audit.json"), "w") as f:
        json.dump(found, f, indent=0)
    kinds, classes = {}, {}
    for p in pieces:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
        length = float(np.hypot(*np.diff(p["xy"], axis=0).T).sum()) / 1000
        classes[p["spec"]["cls"]] = round(classes.get(p["spec"]["cls"], 0) + length, 1)
    print(json.dumps({"kinds": kinds, "km_by_class": classes, "phantom": len(found["phantom"]), "over": len(found["overs"])}))
    print(f"{time.perf_counter() - t0:.0f} s")


if __name__ == "__main__":
    main()
