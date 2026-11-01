#!/bin/sh
# What an export needs from the look side before Unreal: sh tools/ue_chain_look.sh <name> <previous export name>.
# The far meshes hardlinked from the previous export (the same frame) with their manifest entries, coarse ground collision,
# the plants, roof tints. Scatter reads the finished roads, so this runs after ue_chain.sh.
set -eo pipefail
NAME=${1:?export name}
PREV=${2:?previous export name}
PY=/c/beamng-earth/Earth.UE/.venv/Scripts/python
E=/c/beamng-earth/Earth.UE/export
W=C:/beamng-earth/Earth.UE/export
OUT=$W/$NAME
TOOLS=/c/beamng-earth/BeamNG.Earth/tools     # (ue_groundcol.py, ue_scatter.py --budget: the working copies there)
T=$(date +%s)

stage() {
    NOW=$(date +%s)
    echo "== $1 ($(((NOW - T) / 60)) min in, $(date +%H:%M:%S))"
}

stage "far meshes"
ln -f "$E/$PREV"/far.glb "$E/$PREV"/far2.glb "$E/$NAME/"
$PY - "$E/$PREV/manifest.json" "$E/$NAME/manifest.json" <<'PY'
import json
import sys
with open(sys.argv[1]) as f:
    prev = json.load(f)
with open(sys.argv[2]) as f:
    doc = json.load(f)
doc["meshes"] = [e for e in doc["meshes"] if e["kind"] != "far"] + [e for e in prev["meshes"] if e["kind"] == "far"]
for k in ("far", "far2"):
    if k in prev:
        doc[k] = prev[k]
with open(sys.argv[2], "w") as f:
    json.dump(doc, f, indent=1)
print(len(doc["meshes"]), "meshes")
PY
stage "ground collision"
$PY $TOOLS/ue_groundcol.py --out "$OUT" | tail -1 | cut -c1-200
stage "scatter"
$PY $TOOLS/ue_scatter.py --out "$OUT" --budget 300000 --jobs 5 | tail -1 | cut -c1-200
stage "roof tints"
$PY /c/beamng-earth/Earth.UE/scripts/roof_tint.py "$OUT" | tail -1 | cut -c1-200
stage "done"
du -sh "$E/$NAME"
