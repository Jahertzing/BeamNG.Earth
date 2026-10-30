#!/usr/bin/env python3
"""Coarse ground collision for an area export: every ground_<i>_<j>.glb at an 8 m lattice as groundcol_<i>_<j>.glb,
sunk a little so it never lifts a wheel off the road's own exact collision. 100 full 2 m cells would be 200M triangles.

    .venv\\Scripts\\python tools\\ue_groundcol.py --out C:/beamng-earth/Earth.UE/export/frederick
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402
import struct  # noqa: E402

STEP = 4                # samples of the 2 m lattice
SINK_M = 0.2


def read_positions(path):
    with open(path, "rb") as f:
        head = f.read(20)
        n = struct.unpack("<I", head[12:16])[0]
        j = json.loads(f.read(n))
        a = j["accessors"][j["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
        f.seek(20 + n + 8 + j["bufferViews"][a["bufferView"]]["byteOffset"])
        return np.frombuffer(f.read(a["count"] * 12), np.float32).reshape(-1, 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="C:/beamng-earth/Earth.UE/export/frederick")
    a = ap.parse_args()
    path = os.path.join(a.out, "manifest.json")
    with open(path) as f:
        manifest = json.load(f)
    manifest["meshes"] = [e for e in manifest["meshes"] if e["kind"] != "groundcol"]
    added = []
    for e in [m for m in manifest["meshes"] if m["kind"] == "ground"]:
        P = read_positions(os.path.join(a.out, e["file"]))
        n = int(round(len(P) ** 0.5))
        U = P[:, 1].reshape(n, n)[::STEP, ::STEP].astype(np.float64) - SINK_M
        m = U.shape[0]
        X, Y = np.meshgrid(np.arange(m) * 2.0 * STEP, np.arange(m) * 2.0 * STEP)
        Q = np.stack([X.ravel(), Y.ravel(), U.ravel()], axis=1)
        k = np.arange(m - 1)
        c = (k[None, :] + k[:, None] * m).ravel()
        T = np.stack([c, c + 1, c + m, c + 1, c + m + 1, c + m], axis=1).reshape(-1, 3)
        name = "groundcol" + e["file"][len("ground"):-4]
        g = ue_export.Glb()
        g.add(Q, T, None, ["groundcol"])
        g.write(os.path.join(a.out, name + ".glb"), name, (0.0, 0.0, 0.0))
        added.append({"file": name + ".glb", "kind": "groundcol", "collision_only": True, "origin": e["origin"],
                      "tris": int(len(T)), "verts": int(len(Q)), "materials": ["groundcol"]})
    manifest["meshes"] += added
    with open(path, "w") as f:
        json.dump(manifest, f, indent=1)
    print(len(added), "cells,", sum(e["tris"] for e in added), "triangles")


if __name__ == "__main__":
    main()
