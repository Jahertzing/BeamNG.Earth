#!/usr/bin/env python3
"""The gate: one command before a branch is handed to the builder, and the builder's check on a candidate before it
merges and relaunches the game. Static checks (tools/lint.py), the mod's Lua tests (tools/lua_tests.py, LuaJIT in
Python) and the Python tests the diff touches (tools/run_tests.py --changed), in that order, each a step with its
time, then a summary and build/gate.json. Nothing here needs the game or the network.

  python tools/gate.py                       lint, Lua tests, the Python tests for the diff against origin/main
  python tools/gate.py --base main           the diff against another base
  python tools/gate.py --full                lint, Lua tests, the whole Python suite (the nightly: ~8 min at --par 6)
  python tools/gate.py --skip lua,python     leave steps out (lint, lua, python)
  python tools/gate.py --install             pip install server/requirements-dev.txt first (lupa, ruff, psutil)

Exit status 1 when a step failed. The Python step's timeout and parallelism are run_tests.py's (--par, --timeout).
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "build")
STEPS = ("lint", "lua", "python")


def git(*args):
    p = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True, text=True, errors="replace")
    return p.stdout.strip() if p.returncode == 0 else ""


def run_step(name, cmd):
    print(f"\n=== {name}: {' '.join(os.path.relpath(c, ROOT) if os.path.isabs(c) else c for c in cmd)}", flush=True)
    t = time.time()
    p = subprocess.run(cmd, cwd=ROOT)
    return {"step": name, "ok": p.returncode == 0, "rc": p.returncode, "secs": round(time.time() - t, 1)}


def main():
    ap = argparse.ArgumentParser(description="Lint, Lua tests and the affected Python tests, one command.")
    ap.add_argument("--base", default="origin/main", help="the diff's base for --changed (default origin/main)")
    ap.add_argument("--full", action="store_true", help="the whole Python suite instead of the diff's modules")
    ap.add_argument("--skip", default="", help="steps to leave out: lint, lua, python (comma separated)")
    ap.add_argument("--par", type=int, default=6, help="run_tests.py --par")
    ap.add_argument("--timeout", type=int, default=1500, help="run_tests.py --timeout")
    ap.add_argument("--install", action="store_true", help="pip install the dev requirements first")
    a = ap.parse_args()
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    bad = skip - set(STEPS)
    if bad:
        raise SystemExit(f"--skip: unknown step(s) {', '.join(sorted(bad))}; the steps are {', '.join(STEPS)}")
    py = sys.executable
    if a.install:
        r = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                            os.path.join(ROOT, "server", "requirements-dev.txt")], cwd=ROOT)
        if r.returncode:
            raise SystemExit("pip install of server/requirements-dev.txt failed")
    head = git("rev-parse", "--short", "HEAD")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    print(f"gate: {branch} at {head}, base {a.base}{' (full)' if a.full else ''}", flush=True)
    results = []
    if "lint" not in skip:
        results.append(run_step("lint", [py, os.path.join(HERE, "lint.py")]))
    if "lua" not in skip:
        results.append(run_step("lua", [py, os.path.join(HERE, "lua_tests.py")]))
    if "python" not in skip:
        cmd = [py, os.path.join(HERE, "run_tests.py"), "--par", str(a.par), "--timeout", str(a.timeout)]
        if not a.full:
            cmd += ["--changed", a.base]
        results.append(run_step("python", cmd))
    print("\n=== gate summary")
    for r in results:
        print(f"  {r['step']:7} {'OK' if r['ok'] else 'FAIL':5} {r['secs']:7.1f} s")
    ok = all(r["ok"] for r in results)
    print(f"gate: {'PASS' if ok else 'FAIL'} ({branch} at {head})", flush=True)
    os.makedirs(BUILD, exist_ok=True)
    with open(os.path.join(BUILD, "gate.json"), "w", encoding="utf-8") as f:
        json.dump({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "branch": branch, "head": head, "base": a.base,
                   "full": a.full, "ok": ok, "steps": results}, f, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
