#!/usr/bin/env python3
"""The baked kinds' dependencies (server/bakedeps.py): merge a bake run's records into a manifest, compare it with the
committed one, accept it.

  python3 tools/bake_deps.py merge build/headless_bake/<stamp> [...]     the runs' deps-*.json -> <stamp>/bake_deps.json
  python3 tools/bake_deps.py diff build/headless_bake/<stamp>/bake_deps.json   against server/bake_deps.json
  python3 tools/bake_deps.py accept build/headless_bake/<stamp>/bake_deps.json  copy it to server/bake_deps.json (commit it)
  python3 tools/bake_deps.py check                                        the committed manifest's functions exist

A kind's key (revisit.code_key(kind)) changes when its manifest entry changes, so accepting a manifest with new functions
for a kind rebuilds that kind once at the next bake; the game's server must run the same commit as the bake, as always.
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
import bakedeps  # noqa: E402


def records_in(run_dirs):
    """The deps-*.json records under the runs' deps/ folders (and any bake_deps.json given)."""
    recs = []
    for d in run_dirs:
        paths = [d] if d.endswith(".json") else sorted(glob.glob(os.path.join(d, "**", "deps-*.json"), recursive=True))
        for p in paths:
            try:
                with open(p, encoding="utf-8") as f:
                    recs.append(json.load(f).get("kinds") or {})
            except (OSError, ValueError):
                print(f"unreadable: {p}", file=sys.stderr)
    return recs


def cmd_merge(a):
    recs = records_in(a.paths)
    if not recs:
        print("no deps-*.json records found", file=sys.stderr)
        return 2
    kinds = bakedeps.merge(recs)
    out = a.out or os.path.join(a.paths[0], "bake_deps.json")
    bakedeps.write_manifest(kinds, out)
    for k, v in kinds.items():
        print(f"  {k:10s} {len(v['functions']):5d} functions, {len(v['files'])} files")
    print(f"written: {out} ({len(recs)} records)")
    return report_diff(bakedeps.read_manifest(), kinds)


def report_diff(old, new):
    d = bakedeps.diff(old, new)
    if not d:
        print("the same as server/bake_deps.json" if old else "server/bake_deps.json: none yet (whole-code keys)")
        return 0
    for kind, (fa, fr, da, dr) in d.items():
        print(f"  {kind}: +{len(fa)} -{len(fr)} functions, +{len(da)} -{len(dr)} files"
              + (f" (new: {', '.join(fa[:6])}{', ...' if len(fa) > 6 else ''})" if fa else ""))
    print("accept it (tools/bake_deps.py accept <file>) and commit server/bake_deps.json: those kinds rebuild once")
    return 0


def cmd_diff(a):
    return report_diff(bakedeps.read_manifest(), bakedeps.read_manifest(a.paths[0]))


def cmd_accept(a):
    kinds = bakedeps.read_manifest(a.paths[0])
    if not kinds:
        print(f"{a.paths[0]}: no kinds", file=sys.stderr)
        return 2
    bad = bakedeps.missing_functions(kinds)
    if bad:
        for kind, name in bad[:20]:
            print(f"  {kind}: {name} does not exist", file=sys.stderr)
        print("not accepted: functions missing (a module changed since the record)", file=sys.stderr)
        return 1
    bakedeps.write_manifest(kinds, bakedeps.MANIFEST)
    print(f"written: {bakedeps.MANIFEST}; commit it with the code it was recorded on")
    return 0


def cmd_check(a):
    kinds = bakedeps.read_manifest()
    if not kinds:
        print("server/bake_deps.json: none (every kind on the whole-code key)")
        return 0
    bad = bakedeps.missing_functions(kinds)
    empty = [k for k, v in kinds.items() if not v["functions"]]
    for kind, name in bad[:20]:
        print(f"  {kind}: {name} does not exist")
    for k in empty:
        print(f"  {k}: no functions")
    print(f"{len(kinds)} kinds, {sum(len(v['functions']) for v in kinds.values())} functions; "
          f"{'OK' if not bad and not empty else 'FAIL'}")
    return 1 if bad or empty else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("merge", "diff", "accept", "check"))
    ap.add_argument("paths", nargs="*", help="run folders (merge) or a bake_deps.json (diff, accept)")
    ap.add_argument("--out", default=None, help="merge: write the manifest here")
    a = ap.parse_args(argv)
    if a.command in ("diff", "accept") and not a.paths:
        ap.error(f"{a.command}: a bake_deps.json")
    if a.command == "merge" and not a.paths:
        ap.error("merge: run folders")
    return {"merge": cmd_merge, "diff": cmd_diff, "accept": cmd_accept, "check": cmd_check}[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
