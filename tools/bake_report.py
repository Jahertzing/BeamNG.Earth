#!/usr/bin/env python3
"""What a drive built and what it read back (the route bake, tools/route_bake.py): the server log's terrain blocks
(built, or read back from the revisit cache: the "revisit" stage, blockcache.py), base photos rendered, anchors and
their far fields, between two times; with the drive summaries of autodrive_perf.py runs beside it.

  python3 tools/bake_report.py --log earthd.log [--log earthd.1.log] [--since "2026-10-28 10:45"] [--until ...]
                               [--runs DIR ...]

A baked drive should show terrain blocks read back, no base photos rendered and fast anchors; a cold one the build
times the bake saves.
"""
import argparse
import glob
import json
import os
import re
import sys

STAMP = re.compile(r"^(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)")
TERRAIN = re.compile(r"terrain \((\d+), (-?[\d.]+), (-?[\d.]+)\) z\d+ in ([\d.]+) s \(([^)]*)\)")
BASE = re.compile(r"base photo \((\d+), (-?[\d.]+), (-?[\d.]+)\) r\d+ in ([\d.]+) s")
ANCHOR = re.compile(r"anchor (\d+) \((-?[\d.]+), (-?[\d.]+)\) ready in ([\d.]+) s")
FAR = re.compile(r"anchor (\d+) far field done: (\d+) tiles \((\d+) sent, (\d+) swaps in ([\d.]+) s")
# a worker process's CPU seconds by job (blockworker, -v; cumulative: its last line counts)
WORKER_CPU = re.compile(r"worker (\d+) cpu: (.*)")
JOB = re.compile(r"(\w+) (\d+) jobs ([\d.]+) s")


def stages(text):
    """{stage: seconds} of a terrain line's "(heights 1.20 grading 0.30 ...)"."""
    parts = text.split()
    out = {}
    for k, v in zip(parts[::2], parts[1::2]):
        try:
            out[k] = float(v)
        except ValueError:
            pass
    return out


def scan(paths, since=None, until=None):
    """The log lines' events between since and until (strings comparable with the lines' stamps)."""
    ev = {"read": [], "built": [], "base": [], "anchors": [], "far": [], "cpu": {}}
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = STAMP.match(line)
                t = m.group(1).replace("T", " ") if m else None
                if t is not None and ((since and t < since) or (until and t > until)):
                    continue
                if (m := TERRAIN.search(line)):
                    st = stages(m.group(5))
                    ev["read" if "revisit" in st else "built"].append((t, float(m.group(4)), st))
                elif (m := BASE.search(line)):
                    ev["base"].append((t, float(m.group(4))))
                elif (m := ANCHOR.search(line)):
                    ev["anchors"].append((t, int(m.group(1)), float(m.group(2)), float(m.group(3)),
                                          float(m.group(4))))
                elif (m := FAR.search(line)):
                    ev["far"].append((t, int(m.group(1)), int(m.group(2)), float(m.group(5))))
                elif (m := WORKER_CPU.search(line)):
                    ev["cpu"][int(m.group(1))] = m.group(2)
    return ev


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else 0.0


def report(ev, runs=()):
    out = []
    read, built = ev["read"], ev["built"]
    n = len(read) + len(built)
    out.append(f"terrain blocks: {n} ({len(read)} read back, {len(built)} built)")
    if built:
        s = [b[1] for b in built]
        out.append(f"  built: median {pct(s, 0.5):.2f} s, p90 {pct(s, 0.9):.2f} s, max {max(s):.2f} s, "
                   f"total {sum(s):.0f} s")
        tot = {}
        for _, _, st in built:
            for k, v in st.items():
                tot[k] = tot.get(k, 0.0) + v
        out.append("  built, by stage (s): " + ", ".join(f"{k} {v:.0f}" for k, v in
                                                       sorted(tot.items(), key=lambda kv: -kv[1])[:8]))
    if read:
        s = [r[1] for r in read]
        out.append(f"  read back: median {pct(s, 0.5):.2f} s, max {max(s):.2f} s")
    if ev["base"]:
        s = [b[1] for b in ev["base"]]
        out.append(f"base photos rendered (INFO lines only: slower than SLOW_BASE_S): {len(s)}, median "
                   f"{pct(s, 0.5):.2f} s, max {max(s):.2f} s")
    if ev["anchors"]:
        out.append(f"anchors: {len(ev['anchors'])}")
        for t, aid, la, lo, s in ev["anchors"]:
            out.append(f"  {t} anchor {aid} ({la:.5f}, {lo:.5f}) ready in {s:.2f} s")
    if ev["far"]:
        s = [f[3] for f in ev["far"]]
        out.append(f"far fields done: {len(s)}, median {pct(s, 0.5):.1f} s, max {max(s):.1f} s")
    if ev.get("cpu"):
        kinds = {}
        for text in ev["cpu"].values():
            for name, n, sec in JOB.findall(text.split(";")[0]):
                k0, s0 = kinds.get(name, (0, 0.0))
                kinds[name] = (k0 + int(n), s0 + float(sec))
        out.append(f"worker CPU by job ({len(ev['cpu'])} processes, -v lines): " + ", ".join(
            f"{k} {n} in {s:.0f} s ({s / max(n, 1):.2f} s each)" for k, (n, s) in
            sorted(kinds.items(), key=lambda kv: -kv[1][1])))
    for d in runs:
        for p in sorted(glob.glob(os.path.join(d, "**", "summary.json"), recursive=True)):
            try:
                with open(p, encoding="utf-8") as f:
                    s = json.load(f)
            except (OSError, ValueError):
                continue
            fm = s.get("frame_ms") or {}
            fo = s.get("frames_over") or {}
            causes = ", ".join(f"{k} x{v[0]} (worst {v[1]:.0f} ms)" for k, v in list((s.get("hitch_causes") or {})
                                                                                    .items())[:4])
            if "km" in s:           # a ghost drive (ghost_drive.py): distance in km, the waits for the world to load
                w = s.get("waits") or {}
                out.append(f"{os.path.relpath(os.path.dirname(p), d)}: {s.get('km')} of {s.get('route_km')} km in "
                           f"{s.get('seconds')} s, {s.get('fps')} fps, worst p99 {fm.get('p99_worst_tick')} ms, max "
                           f"{fm.get('max')} ms, >50 ms {fo.get('over50')}, >100 ms {fo.get('over100')}; waited "
                           f"{w.get('count')}x {w.get('seconds')} s ({w.get('gave_up')} gave up); hitches: "
                           f"{causes or 'none'}")
                continue
            out.append(f"{os.path.relpath(os.path.dirname(p), d)}: {s.get('miles')} mi in {s.get('seconds')} s, "
                       f"{s.get('fps')} fps, p99 {fm.get('p99')} ms, max {fm.get('max')} ms, >50 ms "
                       f"{fo.get('over50')}, >100 ms {fo.get('over100')}; hitches: {causes or 'none'}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", action="append", required=True, help="a server log (glob ok; repeat for more)")
    ap.add_argument("--since", default=None, help="'YYYY-MM-DD HH:MM[:SS]' (the log's own clock)")
    ap.add_argument("--until", default=None)
    ap.add_argument("--runs", action="append", default=[], help="a folder of autodrive_perf / ghost_drive runs (summary.json)")
    a = ap.parse_args(argv)
    paths = sorted({p for g in a.log for p in glob.glob(g)})
    if not paths:
        print("no log found", file=sys.stderr)
        return 2
    print(report(scan(paths, a.since, a.until), a.runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
