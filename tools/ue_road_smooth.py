#!/usr/bin/env python3
"""How smooth the built roads are to drive, no Unreal needed: along every road link of roadnet.json the height profile is
resampled every STEP_M and its grade change between neighbouring steps measured. A big change in a few metres is a bump
or a lip a car feels, at a junction or a deck's end above all.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_road_smooth.py --net <roadnet dir>
"""
import argparse
import json
import os

import numpy as np

STEP_M = 4.0
BUMP = 0.04                     # grade change between steps counted as a bump (4 %: a sharp lip at 25 m/s)
HIDE = ("service", "track", "unknown")


def profile(points):
    """(s, z) of a link's points every STEP_M."""
    p = np.array(points)
    d = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p[:, :2], axis=0).T))]
    if d[-1] < 3 * STEP_M:
        return None, None
    s = np.arange(0.0, d[-1], STEP_M)
    return s, np.interp(s, d, p[:, 2])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    a = ap.parse_args()
    with open(os.path.join(a.net, "roadnet.json")) as f:
        net = json.load(f)
    rows, km, bumps = [], {}, {}
    for lk in net["links"]:
        if lk["class"] in HIDE:
            continue
        s, z = profile(lk["points"])
        if s is None:
            continue
        g = np.diff(z) / STEP_M
        dg = np.abs(np.diff(g))
        key = (lk["class"], "deck" if lk["level"] > 0 else "tunnel" if lk["level"] < 0 else "ground")
        km[key] = km.get(key, 0.0) + float(s[-1]) / 1000
        bumps[key] = bumps.get(key, 0) + int((dg > BUMP).sum())
        if len(dg) and dg.max() > BUMP:
            k = int(dg.argmax())
            q = np.array(lk["points"])
            d = np.r_[0.0, np.cumsum(np.hypot(*np.diff(q[:, :2], axis=0).T))]
            rows.append((round(float(dg.max()), 3), lk["class"], lk["level"], lk["name"][:22], round(float(np.interp(s[k + 1], d, q[:, 0]))),
                         round(float(np.interp(s[k + 1], d, q[:, 1])))))
    for k in sorted(km, key=str):
        print(f"{k[0]:14s} {k[1]:7s} {km[k]:7.1f} km  bumps over {BUMP:.0%}: {bumps[k]:5d}  per km {bumps[k] / max(km[k], 0.01):5.2f}")
    rows.sort(reverse=True)
    for r in rows[:14]:
        print(r)


if __name__ == "__main__":
    main()
