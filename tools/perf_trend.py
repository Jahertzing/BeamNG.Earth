#!/usr/bin/env python3
"""The performance log's trend across drives, with a frame-time budget: the mod's perflog lines in the world server's
log (perflog.lua: `perf minute` every 60 s with the fps, 1 % low, worst frame and hitch count; `perf hitch` for a
frame of 100 ms or more, with the mod's and the engine's parts), one table per drive and a verdict, so a ghost drive
or Jeff's drive can be compared with the last one instead of read line by line.

  python tools/perf_trend.py LOG [LOG ...]                   the drives in the logs (server.log, server.1.log, ...),
                                                             oldest first, a line each
  python tools/perf_trend.py LOG --html out.html             a page: the table and each drive's fps / 1 % low per
                                                             minute with its hitches
  python tools/perf_trend.py LOG --json out.json
  python tools/perf_trend.py LOG --budget                    exit 1 when the newest drive breaks the budget or is
                                                             worse than the one before (a gate after a ghost drive)
  python tools/perf_trend.py LOG --last N                    only the newest N drives

The budget (driving minutes only: the car over MOVING_MPH, so a car standing at a teleport while the world loads
does not count): --min-fps 60 mean fps, --min-low 40 median 1 % low, --max-hitches 1.0 per minute,
--max-worst 500 ms. "Worse than the drive before": fps down WORSE_FPS, 1 % low down WORSE_LOW, or hitches per
minute up WORSE_HITCH and by WORSE_HITCH_MIN. A drive is the perf lines between two `earthd listening` lines, or
a gap of GAP_MIN minutes in them; drives under MIN_MINUTES minutes are left out.
"""
import argparse
import html
import json
import os
import re
import statistics
import sys
from collections import Counter

STAMP = re.compile(r"^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)")
MINUTE = re.compile(r"perf minute (\d+) fps, 1% low (\d+), worst (\d+) ms, (\d+) hitches(?: \(worst: (.*?)\))?"
                    r"(?:, (\d+) stutters)?")
HITCH = re.compile(r"perf hitch (\d+) ms(.*)$")
# the minute line's pacing (perflog 2026-10-29): frames within the refresh's budget, at two refreshes or more, jitter
PACING = re.compile(r"pacing: (\d+)% within ([\d.]+) ms, (\d+)% at 2\+ refreshes, jitter ([\d.]+) ms")
PARTS = re.compile(r"stutter parts: ([^|]+)")                 # "roads 4 (38 ms), trees 2 (12 ms)"
PART = re.compile(r"([A-Za-z+_.]+) (\d+) \((\d+) ms\)")
WHERE = re.compile(r"at (-?\d+\.\d+),(-?\d+\.\d+), (\d+) mph")
MORE = re.compile(r"\((\d+) more hitches since the last line\)")
LISTEN = "earthd listening on"
GAP_MIN = 15
MIN_MINUTES = 3
MOVING_MPH = 5
WORSE_FPS, WORSE_LOW, WORSE_HITCH, WORSE_HITCH_MIN = 0.10, 0.15, 0.5, 0.5
ENGINE_PARTS = ("Ticking", "Lua", "LuaGC", "Render", "RenderShadows", "RenderReflections", "PhysicsAndVLUA",
                "FPSLimiter")
OUTSIDE = "mostly outside"


def parse_lines(lines, source=""):
    """The drives in a log's lines: each {start, end, source, minutes: [...], hitches: [...]}."""
    drives, cur = [], None

    def close():
        nonlocal cur
        if cur and len(cur["minutes"]) >= MIN_MINUTES:
            drives.append(cur)
        cur = None

    last_t = None
    for line in lines:
        m = STAMP.match(line)
        if not m:
            continue
        t = m.group(1) + " " + m.group(2)
        if LISTEN in line:
            close()
            continue
        mm = MINUTE.search(line)
        hh = HITCH.search(line) if not mm else None
        if not mm and not hh:
            continue
        if cur and last_t and minutes_between(last_t, t) > GAP_MIN:
            close()
        if cur is None:
            cur = {"start": t, "end": t, "source": source, "minutes": [], "hitches": []}
        cur["end"], last_t = t, t
        w = WHERE.search(line)
        where = {"lat": float(w.group(1)), "lon": float(w.group(2)), "mph": int(w.group(3))} if w else {}
        if mm:
            minute = {"t": t, "fps": int(mm.group(1)), "low": int(mm.group(2)), "worst": int(mm.group(3)),
                      "hitches": int(mm.group(4)), "cause": (mm.group(5) or "").strip(),
                      "stutters": int(mm.group(6) or 0), **where}
            pc = PACING.search(line)
            if pc:
                minute.update(within=int(pc.group(1)), budget=float(pc.group(2)), two=int(pc.group(3)),
                              jitter=float(pc.group(4)))
            pp = PARTS.search(line)
            if pp:
                minute["parts"] = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in PART.finditer(pp.group(1))}
            cur["minutes"].append(minute)
        else:
            rest = hh.group(2)
            parts = {}
            for name in ("mod", "engine"):
                pm = re.search(rf"\| {name}: ([^|]+)", rest)
                if pm:
                    parts[name] = pm.group(1).strip()
            more = MORE.search(rest)
            cur["hitches"].append({"t": t, "ms": int(hh.group(1)), "mod": parts.get("mod", ""),
                                   "engine": parts.get("engine", ""), "outside": OUTSIDE in rest,
                                   "more": int(more.group(1)) if more else 0, **where})
    close()
    return drives


def minutes_between(a, b):
    import datetime as dt
    fa = dt.datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
    fb = dt.datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
    return (fb - fa).total_seconds() / 60.0


def top_part(text):
    """The engine or mod part named first (the largest: perflog sorts them) in "Ticking 187.9, Lua 14.3"."""
    m = re.match(r"\s*([A-Za-z+_]+) ", text + " ")
    return m.group(1) if m else ""


def metrics(d):
    """A drive's numbers. fps, 1 % low, the worst frame and the hitches a minute are over the driving minutes (the car
    over MOVING_MPH: a teleport's load while the car stands is not a regression); hitches and stutters are the whole
    drive's."""
    mins = d["minutes"]
    driving = [m for m in mins if m.get("mph", MOVING_MPH) >= MOVING_MPH] or mins
    causes = Counter()
    for h in d["hitches"]:
        if h.get("mph", MOVING_MPH) < MOVING_MPH:
            continue
        c = top_part(h["engine"]) or top_part(h["mod"]) or ("outside" if h["outside"] else "?")
        causes[c] += 1 + h["more"]
    paced = [m for m in driving if "within" in m]
    parts = Counter()
    for m in driving:
        for name, (n, _) in (m.get("parts") or {}).items():
            parts[name] += n
    return {
        "start": d["start"], "end": d["end"], "source": d["source"], "minutes": len(mins), "driving": len(driving),
        # pacing (frames within the refresh's budget, at two refreshes or more, the frame-to-frame jitter): the means over
        # the driving minutes that have them; None in a log from before perflog wrote them
        "within": round(statistics.mean(m["within"] for m in paced), 1) if paced else None,
        "two": round(statistics.mean(m["two"] for m in paced), 1) if paced else None,
        "jitter": round(statistics.mean(m["jitter"] for m in paced), 2) if paced else None,
        "budget": paced[0]["budget"] if paced else None,
        "stutters_per_min": round(sum(m["stutters"] for m in driving) / max(1, len(driving)), 2),
        "stutter_parts": parts.most_common(4),
        "fps": round(statistics.mean(m["fps"] for m in driving), 1),
        "low": round(statistics.median(m["low"] for m in driving), 1),
        "low_min": min(m["low"] for m in driving),
        "worst": max(m["worst"] for m in driving),
        "worst_any": max(m["worst"] for m in mins),
        "hitches": sum(m["hitches"] for m in mins),
        "hitches_per_min": round(sum(m["hitches"] for m in driving) / max(1, len(driving)), 2),
        "big_hitches": sum(1 + h["more"] for h in d["hitches"]
                           if h["ms"] >= 250 and h.get("mph", MOVING_MPH) >= MOVING_MPH),
        "stutters": sum(m["stutters"] for m in mins),
        "causes": causes.most_common(4),
    }


def budget_breaches(m, min_fps, min_low, max_hitches, max_worst):
    out = []
    if m["fps"] < min_fps:
        out.append(f"mean fps {m['fps']} under {min_fps}")
    if m["low"] < min_low:
        out.append(f"median 1% low {m['low']} under {min_low}")
    if m["hitches_per_min"] > max_hitches:
        out.append(f"{m['hitches_per_min']} hitches a minute over {max_hitches}")
    if m["worst"] > max_worst:
        out.append(f"worst frame {m['worst']} ms over {max_worst}")
    return out


def worse_than(m, prev):
    out = []
    if prev["fps"] and m["fps"] < prev["fps"] * (1 - WORSE_FPS):
        out.append(f"fps {m['fps']} from {prev['fps']}")
    if prev["low"] and m["low"] < prev["low"] * (1 - WORSE_LOW):
        out.append(f"1% low {m['low']} from {prev['low']}")
    if m["hitches_per_min"] > prev["hitches_per_min"] * (1 + WORSE_HITCH) \
            and m["hitches_per_min"] - prev["hitches_per_min"] >= WORSE_HITCH_MIN:
        out.append(f"hitches a minute {m['hitches_per_min']} from {prev['hitches_per_min']}")
    return out


def fmt_pacing(m):
    if m.get("within") is None:
        return "-"
    return f"{m['within']:.0f}% in {m['budget']:.1f} ms, {m['two']:.0f}% 2+, jitter {m['jitter']:.1f}"


def table(rows):
    head = f"{'start':19} {'min':>4} {'drv':>4} {'fps':>6} {'1%low':>6} {'lowmin':>6} {'worst':>6} {'hit/min':>7} " \
           f"{'>=250':>5} {'stut/min':>8}  causes | stutter parts | pacing"
    lines = [head]
    for m in rows:
        causes = ", ".join(f"{c} {n}" for c, n in m["causes"])
        parts = ", ".join(f"{c} {n}" for c, n in m.get("stutter_parts", []))
        lines.append(f"{m['start']:19} {m['minutes']:4} {m['driving']:4} {m['fps']:6.1f} {m['low']:6.1f} "
                     f"{m['low_min']:6} {m['worst']:6} {m['hitches_per_min']:7.2f} {m['big_hitches']:5} "
                     f"{m.get('stutters_per_min', 0):8.2f}  {causes} | {parts or '-'} | {fmt_pacing(m)}")
    return "\n".join(lines)


def svg(d, w=720, h=120):
    """fps and 1 % low per minute as two lines, hitches as ticks along the bottom."""
    mins = d["minutes"]
    if not mins:
        return ""
    n = len(mins)
    top = max(90, max(m["fps"] for m in mins))

    def x(i):
        return 40 + (w - 50) * i / max(1, n - 1)

    def y(v):
        return 10 + (h - 30) * (1 - v / top)
    fps = " ".join(f"{x(i):.1f},{y(m['fps']):.1f}" for i, m in enumerate(mins))
    low = " ".join(f"{x(i):.1f},{y(m['low']):.1f}" for i, m in enumerate(mins))
    ticks = "".join('<rect x="%.1f" y="%d" width="3" height="%d" class="hit"/>'
                    % (x(i) - 1.5, h - 18, min(12, 2 + m["hitches"])) for i, m in enumerate(mins) if m["hitches"])
    grid = "".join(f'<line x1="40" x2="{w - 10}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                   f'<text x="36" y="{y(v) + 4:.1f}" class="lbl">{v}</text>' for v in (30, 60, 90) if v <= top)
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">{grid}'
            f'<polyline points="{fps}" class="fps"/><polyline points="{low}" class="low"/>{ticks}</svg>')


def html_page(drives, rows, breaches, worse):
    css = """
:root{--bg:#f6f5f1;--fg:#1d1f1b;--mut:#5d615a;--line:#d9d8d0;--ok:#1f6a3d;--bad:#a83a2c;--fps:#1f6a3d;--low:#b7791f}
@media(prefers-color-scheme:dark){:root{--bg:#17191a;--fg:#e8e6e0;--mut:#a2a59d;--line:#363a3c;--ok:#6cc48f;--bad:#e07a6b;--fps:#6cc48f;--low:#e0ad4f}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:1.5rem max(16px,3vw)}
h1{font-size:1.4rem;margin:0 0 .5rem}h2{font-size:1rem;margin:1.5rem 0 .3rem}
table{border-collapse:collapse;font-size:.9rem;width:100%}th,td{text-align:right;padding:.3rem .5rem;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
th:first-child,td:first-child,td.l,th.l{text-align:left}.bad{color:var(--bad);font-weight:600}.ok{color:var(--ok)}
.fps{fill:none;stroke:var(--fps);stroke-width:1.5}.low{fill:none;stroke:var(--low);stroke-width:1.5}.hit{fill:var(--bad)}
.grid{stroke:var(--line)}.lbl{fill:var(--mut);font-size:9px;text-anchor:end}.mut{color:var(--mut)}.wrap{overflow-x:auto}
"""
    rows_html = ""
    for i, m in enumerate(rows):
        causes = ", ".join(f"{c} {n}" for c, n in m["causes"])
        cls = ' class="bad"' if i == len(rows) - 1 and (breaches or worse) else ""
        parts = ", ".join(f"{c} {n}" for c, n in m.get("stutter_parts", []))
        rows_html += (f"<tr{cls}><td class='l'>{html.escape(m['start'])}</td><td>{m['minutes']}</td><td>{m['driving']}</td>"
                      f"<td>{m['fps']}</td><td>{m['low']}</td><td>{m['low_min']}</td><td>{m['worst']}</td>"
                      f"<td>{m['hitches_per_min']}</td><td>{m['big_hitches']}</td><td>{m.get('stutters_per_min', 0)}</td>"
                      f"<td class='l mut'>{html.escape(causes)}</td><td class='l mut'>{html.escape(parts or '-')}</td>"
                      f"<td class='l mut'>{html.escape(fmt_pacing(m))}</td></tr>")
    verdict = ""
    if rows:
        if breaches or worse:
            verdict = "<p class='bad'>Newest drive: " + html.escape("; ".join(breaches + worse)) + "</p>"
        else:
            verdict = "<p class='ok'>Newest drive within the budget and no worse than the one before.</p>"
    charts = ""
    for d, m in zip(drives, rows):
        charts += (f"<h2>{html.escape(m['start'])} <span class='mut'>{html.escape(os.path.basename(m['source']))}, "
                   f"{m['minutes']} min, {m['hitches']} hitches</span></h2>{svg(d)}")
    return (f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Perf trend</title><style>{css}</style><h1>Performance trend</h1>{verdict}"
            f"<div class='wrap'><table><tr><th class='l'>start</th><th>min</th><th>driving</th><th>fps</th><th>1% low</th>"
            f"<th>low min</th><th>worst ms</th><th>hit/min</th><th>&ge;250 ms</th><th>stut/min</th><th class='l'>causes</th>"
            f"<th class='l'>stutter parts</th><th class='l'>pacing</th></tr>{rows_html}"
            f"</table></div><p class='mut'>Green: fps per minute. Amber: 1 % low. Red ticks: hitches in that minute. "
            f"The numbers are over driving minutes (the car over {MOVING_MPH} mph); the charts show every "
            f"minute.</p>{charts}")


def main():
    ap = argparse.ArgumentParser(description="Per-drive performance metrics from the server log's perflog lines.")
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--html")
    ap.add_argument("--json")
    ap.add_argument("--budget", action="store_true", help="exit 1 when the newest drive breaks the budget or is worse")
    ap.add_argument("--last", type=int, default=0)
    ap.add_argument("--min-fps", type=float, default=60)
    ap.add_argument("--min-low", type=float, default=40)
    ap.add_argument("--max-hitches", type=float, default=1.0, help="per minute")
    ap.add_argument("--max-worst", type=float, default=500, help="ms")
    a = ap.parse_args()
    drives = []
    for p in a.logs:
        with open(p, encoding="utf-8", errors="replace") as f:
            drives += parse_lines(f, p)
    drives.sort(key=lambda d: d["start"])
    if a.last:
        drives = drives[-a.last:]
    rows = [metrics(d) for d in drives]
    if not rows:
        print("no drives: no perf minute lines in the logs")
        return 0
    print(table(rows))
    newest = rows[-1]
    breaches = budget_breaches(newest, a.min_fps, a.min_low, a.max_hitches, a.max_worst)
    worse = worse_than(newest, rows[-2]) if len(rows) > 1 else []
    print(f"newest drive {newest['start']}: " + ("; ".join(breaches + worse) if breaches or worse else
                                                  "within the budget" + (", no worse than the one before"
                                                                         if len(rows) > 1 else "")))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"drives": rows, "breaches": breaches, "worse": worse}, f, indent=1)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(html_page(drives, rows, breaches, worse))
        print(f"wrote {a.html}")
    return 1 if a.budget and (breaches or worse) else 0


if __name__ == "__main__":
    sys.exit(main())
