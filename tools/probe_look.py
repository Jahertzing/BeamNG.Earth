#!/usr/bin/env python3
"""Look probe: the same views under several renderer variants (haze, sky, exposure, grade), screenshotted side
by side, so the look can be tuned from pictures instead of guesses.

    python3 tools/probe_look.py dump --out DIR                       the renderer state (look.lua dump) only
    python3 tools/probe_look.py run --set tools/look_bench.json --variants tools/look_variants.json --out DIR
        [--places id,id] [--hours 9,17.5] [--perf 5]

run: tools/benchmark.py's machinery (Bench: refuses while the car moves, teleports, waits until the place is
loaded, fixes the sky clock and a clear sky, restores everything at the end, also on Ctrl-C) with
tools/look.lua on top: for each place, each hour (--hours, default the set's) and each view, every variant is
applied, settled and shot, then reset (look.lua puts every field it wrote back). Writes DIR/dump.json, the
images DIR/<place>__<hour>__<view>__<variant>.jpg, DIR/run.json and DIR/index.html (one row per view, one
column per variant).

Variants file: [{"name": "base"}, {"name": "mie4", "sky": {"atmoMieScattering": [0.016, 0.016, 0.016]}}, ...]
(look.lua apply(): sky, level, objects, consts, lua; "undo": lua run after the shot, for a variant whose lua
changed a module's own state).

--perf S: after each variant settles, a perfmon window of S seconds (fps, p95 ms; tools/benchmark.py's) too, in
run.json's rows and under each picture in index.html: what a variant costs (the 2026-10-28 night lights).
"""
import argparse
import html
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import benchmark as bm  # noqa: E402

LOOK_LUA = HERE / "look.lua"
VARIANT_SETTLE_S = 2.5      # after a variant: the sky LUTs re-bake and auto exposure adapts


def look(game, fn, arg=None):
    code = f"return BNGE_LOOK.{fn}({bm.lua_long(json.dumps(arg)) if arg is not None else ''})"
    return json.loads(game.lua(code, timeout=60) or "null")


def write_index(out, rows, variants):
    cols = [v["name"] for v in variants]
    h = ["<!doctype html><meta charset=utf-8><title>look probe</title><style>body{font:13px sans-serif;"
         "background:#111;color:#ddd}img{width:100%}td{vertical-align:top;padding:2px}</style><table>",
         "<tr><th></th>" + "".join(f"<th>{html.escape(c)}</th>" for c in cols) + "</tr>"]
    for r in rows:
        h.append(f"<tr><td>{html.escape(r['label'])}</td>")
        for c in cols:
            f = r["shots"].get(c)
            p = r.get("perf", {}).get(c)
            cap = f"<br>{p.get('fps')} fps, p95 {p.get('p95')} ms" if p else ""
            h.append(f"<td><a href='{f}'><img src='{f}'></a>{cap}</td>" if f else "<td>-</td>")
        h.append("</tr>")
    h.append("</table>")
    (out / "index.html").write_text("\n".join(h))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("dump", "run"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--set", default=str(HERE / "look_bench.json"))
    ap.add_argument("--variants", default=str(HERE / "look_variants.json"))
    ap.add_argument("--places", default=None)
    ap.add_argument("--hours", default=None, help="comma-separated local hours, overriding the set's")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--perf", type=float, default=0.0, help="seconds of frame times per variant (0: none)")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    game = bm.Game()
    if game.lua(LOOK_LUA.read_text()) != "ok":
        sys.exit("could not load tools/look.lua")
    (out / "dump.json").write_text(json.dumps(look(game, "dump"), indent=1))
    print("dump ->", out / "dump.json", flush=True)
    if a.cmd == "dump":
        return

    variants = json.loads(Path(a.variants).read_text())
    places = bm.plan(bm.load_set(a.set), "all", a.places.split(",") if a.places else None)
    hours = [float(x) for x in a.hours.split(",")] if a.hours else None
    opts = bm.options(force=a.force)
    b = bm.Bench(game, opts)
    b.load_chunk()
    info = b.call("info")
    bad = bm.check_info(info, a.force)
    if bad:
        sys.exit("not now: " + "; ".join(bad))
    saved, home = info["saved"], {"lat": info["player"]["lat"], "lon": info["player"]["lon"]}
    (out / "state.json").write_text(json.dumps({"saved": saved, "home": home}))
    rows, rec = [], {"places": [], "variants": variants}
    hide_ui = "local v = rawget(_G, 'ui_visibility') if v and v.set then v.set(%s) end return 'ok'"
    try:
        b.call("zeroInputs")
        game.lua(hide_ui % "false")             # the game's UI (HUD, apps, banners) out of the pictures
        for pl in places:
            print(f"{pl['id']} ...", flush=True)
            views = pl["views"]
            pl = dict(pl, views=[])            # Bench.place: teleport, wait, time, weather; the views are ours
            pr = b.place(pl, str(out))
            rec["places"].append(pr)
            if pr.get("error"):
                print("  FAILED:", pr["error"], flush=True)
                continue
            for hour in hours or [pl["time"]["hour"]]:
                t = dict(pl["time"], hour=hour, time=round((hour / 24.0 - 0.5) % 1.0, 6))
                b.call("setTime", t)
                for v in views:
                    r = b.call("view", v)
                    if not r.get("ok"):
                        print("  view failed:", v["name"], r.get("error"), flush=True)
                        continue
                    time.sleep(opts.settle)
                    b.wait_ready(time.monotonic(), opts.view_timeout, need_anchor=False)
                    row = {"label": f"{pl['id']} {hour:g}h {v['name']}", "shots": {}, "perf": {}}
                    for var in variants:
                        res = look(game, "apply", {k: var[k] for k in ("sky", "level", "objects", "consts", "lua")
                                                   if k in var})
                        if res and not res.get("ok"):
                            print(f"  {var['name']}: {res.get('errors')}", flush=True)
                        time.sleep(var.get("settle", VARIANT_SETTLE_S))
                        if a.perf > 0:
                            b.call("perfStart")
                            time.sleep(a.perf)
                            p = b.call("perfRead")
                            row["perf"][var["name"]] = {k: p.get(k) for k in ("fps", "p50", "p95", "maxMs", "focused")}
                        stem = f"{pl['id']}__{hour:g}h__{v['name']}__{var['name']}"
                        dest = b.screenshot(str(out / stem))
                        row["shots"][var["name"]] = os.path.basename(dest)
                        look(game, "reset")
                        if var.get("undo"):         # a variant that changed a module's state in its lua
                            look(game, "apply", {"lua": var["undo"]})
                    rows.append(row)
                    rec.setdefault("rows", []).append(row)
                    write_index(out, rows, variants)
    finally:
        try:
            game.lua(hide_ui % "true")
        except Exception as e:      # noqa: BLE001
            print("UI back failed:", e)
        try:
            look(game, "reset")
        except Exception as e:      # noqa: BLE001 (the restore below still runs)
            print("look reset failed:", e)
        rec["restore"] = b.restore(saved, home)
        (out / "run.json").write_text(json.dumps(rec, indent=1, default=str))
        print("restored" if rec["restore"]["ok"] else "RESTORE PROBLEMS: " + "; ".join(rec["restore"]["errors"]))


if __name__ == "__main__":
    main()
