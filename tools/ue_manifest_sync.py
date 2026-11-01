#!/usr/bin/env python3
"""The export's manifest brought up to date with its meshes after the passes that rewrite them (ue_pavement): each entry's
materials, triangle and vertex counts as the file has them.

    python tools/ue_manifest_sync.py --dir <export dir>
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402,F401

import ue_survey  # noqa: E402

KINDS = ("road", "walks")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    a = ap.parse_args()
    path = os.path.join(a.dir, "manifest.json")
    with open(path) as f:
        doc = json.load(f)
    n = 0
    gone = [e["file"] for e in doc["meshes"] if not os.path.exists(os.path.join(a.dir, e["file"]))]
    doc["meshes"] = [e for e in doc["meshes"] if e["file"] not in gone]     # (walkcol: ue_pavement's walks collide themselves)
    have = {e["file"] for e in doc["meshes"]}
    for kind in KINDS:          # (ue_medians can make a cell's first walks)
        for f in sorted(os.listdir(a.dir)):
            if f.startswith(kind + "_") and f.endswith(".glb") and f not in have:
                i, j = (int(v) for v in f[len(kind) + 1:-4].split("_"))
                doc["meshes"].append({"file": f, "kind": kind, "collision_only": False, "origin": [i * 2048.0, j * 2048.0, 0.0]})
    for e in doc["meshes"]:
        if e["kind"] in KINDS:
            prims = ue_survey.read_glb(os.path.join(a.dir, e["file"]))
            e["materials"] = [p["name"] for p in prims]
            e["tris"] = sum(len(p["T"]) for p in prims)
            e["verts"] = len(prims[0]["P"])
            n += 1
    with open(path, "w") as f:
        json.dump(doc, f, indent=1)
    print(n, "entries,", len(gone), "gone")


if __name__ == "__main__":
    main()
