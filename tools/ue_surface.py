#!/usr/bin/env python3
"""What each road is paved with, read off the export's own aerial: concrete, asphalt, faded asphalt or gravel. Per road piece
the median colour of its driving width along its stations (cars and paint are too few to move a median), compared with the
piece's class of neighbours' dark asphalt. Writes surface.pkl {(fid, k): name} for tools/ue_pavement.py.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_surface.py --net <roadnet dir> --dir <export dir>
"""
import argparse
import collections
import json
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import numpy as np  # noqa: E402

import ue_road_eval  # noqa: E402

CELL = 2048.0
MIN_STATIONS = 6
CONCRETE_LUMA = 168.0           # I-25's slab is 180; dark asphalt 90-120; a town's old asphalt 130-160
FADED_LUMA = 138.0
GRAVEL_WARM = 45.0              # r - b past the photo's warm cast (pavement 2-35, dirt roads 24-55)
GRAVEL_KINDS = ("gravel", "dirt")


class Photos:
    def __init__(self, export):
        self.dir, self.im = export, {}

    def sample(self, xy):
        out = np.full((len(xy), 3), np.nan)
        c = np.floor(xy / CELL).astype(np.int64)
        for k in {tuple(v) for v in c.tolist()}:
            if k not in self.im:
                path = os.path.join(self.dir, f"ground_{k[0]}_{k[1]}.glb")
                self.im[k] = np.asarray(ue_road_eval.ground_image(path).convert("RGB")) if os.path.exists(path) else None
            im = self.im[k]
            if im is None:
                continue
            a = np.flatnonzero((c == k).all(1))
            n = im.shape[0]
            f = (xy[a] - (k[0] * CELL, k[1] * CELL)) / CELL * n
            out[a] = im[np.clip(n - 1 - f[:, 1].astype(np.int64), 0, n - 1), np.clip(f[:, 0].astype(np.int64), 0, n - 1)]
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick-r3")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    with open(os.path.join(a.net, "fit.pkl"), "rb") as f:
        fit = pickle.load(f)
    pieces = fit["pieces"] if isinstance(fit, dict) and "pieces" in fit else None
    if pieces is None:
        with open(os.path.join(a.net, "stations.pkl"), "rb") as f:
            pieces = pickle.load(f)
        pieces = pieces["pieces"] if isinstance(pieces, dict) else pieces
    ph = Photos(a.dir)
    out, seen = {}, collections.defaultdict(list)
    for p in pieces:
        if p["kind"] != "ground" or len(p.get("st", ())) < MIN_STATIONS:
            continue
        cls = p["spec"]["cls"]
        if cls in ("service", "track", "raceway"):
            continue
        st, nrm, ab = p["st"], p["nrm"], p.get("ab")
        w = float(p["spec"]["width"] or 6.0)
        if ab is not None and len(ab) == len(st) and np.isfinite(ab).any():
            lo = np.where(np.isfinite(ab[:, 0]), ab[:, 0], -w / 2)
            hi = np.where(np.isfinite(ab[:, 1]), ab[:, 1], w / 2)
        else:
            lo, hi = np.full(len(st), -w / 2), np.full(len(st), w / 2)
        rows = []
        for t in (0.3, 0.5, 0.7):
            rows.append(ph.sample(st + nrm * (lo + (hi - lo) * t)[:, None]))
        rgb = np.concatenate(rows)
        rgb = rgb[np.isfinite(rgb[:, 0])]
        if len(rgb) < MIN_STATIONS:
            continue
        m = np.median(rgb, axis=0)
        luma = float(m @ (0.299, 0.587, 0.114))
        warm = float(m[0] - m[2])
        if p["spec"]["kind"] in GRAVEL_KINDS or warm > GRAVEL_WARM:
            name = "gravel"
        elif luma >= CONCRETE_LUMA:
            name = "concrete"
        elif luma >= FADED_LUMA:
            name = "faded"
        else:
            name = "asphalt"
        out[(p["fid"], p["k"])] = name
        seen[(cls, name)].append(float(np.hypot(*np.diff(p["xy"], axis=0).T).sum()) / 1000.0)
        seen[("luma", cls)].append(luma)
        seen[("warm", p["spec"]["kind"] + " " + cls)].append(warm)
    table = {f"{k[0]} {k[1]}": round(sum(v), 1) for k, v in sorted(seen.items()) if k[0] not in ("luma", "warm")}
    print(json.dumps(table, indent=0))
    for k, v in sorted(seen.items()):
        if k[0] in ("luma", "warm"):
            print(k[0], k[1], "p10/50/90", np.percentile(v, [10, 50, 90]).round(), len(v))
    with open(a.out or os.path.join(a.net, "surface.pkl"), "wb") as f:
        pickle.dump(out, f)


if __name__ == "__main__":
    main()
