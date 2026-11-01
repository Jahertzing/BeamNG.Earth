#!/bin/sh
# The export chain, start to finish: sh tools/ue_chain.sh <name> [jobs]. Writes export/<name>, ready for Unreal but for the far
# mesh and the plants (the look thread's). Each stage prints its minutes. Prop rows come with the export: props_<i>_<j>.json.
# Jobs: 10 by default; a pavement job peaks at 2.9 GB, so leave them down if an editor is running.
set -eo pipefail
NAME=${1:?export name}
JOBS=${2:-10}
PY=/c/beamng-earth/Earth.UE/.venv/Scripts/python
E=/c/beamng-earth/Earth.UE/export
W=C:/beamng-earth/Earth.UE/export
OUT=$W/$NAME
export BNE_DRCOG=1
cd "$(dirname "$0")/.."
T=$(date +%s)

stage() {
    NOW=$(date +%s)
    echo "== $1 ($(((NOW - T) / 60)) min in, $(date +%H:%M:%S))"
}

stage "road network and fit"
$PY tools/ue_roadnet.py | tail -1 | cut -c1-200
$PY tools/ue_centres.py | tail -1
stage "builders, $JOBS jobs"
mkdir -p "$E/$NAME"
$PY tools/ue_export_area.py --km 20 --jobs "$JOBS" --fit $W/roadnet/fit.pkl --out "$OUT" | tail -1 | cut -c1-200
stage "roadnet.json and surfaces"
$PY tools/ue_roadnet_out.py --export "$OUT" | tail -1
$PY tools/ue_surface.py --dir "$OUT" | tail -1 | cut -c1-200
stage "pavement, $JOBS jobs"
$PY tools/ue_pavement.py --dir "$OUT" --jobs "$JOBS" | tail -1 | cut -c1-200
stage "finished road and walks into the export"
mkdir -p "$E/$NAME-builder"
mv "$E/$NAME"/road_*.glb "$E/$NAME"/walks_*.glb "$E/$NAME-builder/"
rm -f "$E/$NAME"/walkcol_*.glb
ln "$E/$NAME-pave"/road_*.glb "$E/$NAME-pave"/walks_*.glb "$E/$NAME/"
stage "dashes cut, sidewalks only where the aerial has them"
$PY tools/ue_dashes.py --dir "$OUT" | tail -1
$PY tools/ue_walks.py --dir "$OUT" | tail -1
$PY tools/ue_manifest_sync.py --dir "$OUT"
stage "signals, sign posts, furniture"
$PY tools/ue_signals.py --export "$OUT" --manifest | tail -1
$PY tools/ue_settle.py --dir "$OUT" | tail -1 | cut -c1-200
$PY tools/ue_furniture.py --dir "$OUT" --lamps "$W/_qa/lamps-$NAME.csv" --apply | tail -1 | cut -c1-200
$PY tools/ue_mask.py --dir "$OUT" | tail -1
$PY tools/ue_road_qa.py --dir "$OUT" --out "$W/_qa/$NAME" | tail -14 | cut -c1-200 || true
stage "done"
du -sh "$E/$NAME"
