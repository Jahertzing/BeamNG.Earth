#!/usr/bin/env python3
"""The mod's Lua tests (mod/tests/test_*.lua) on a machine without a luajit binary: LuaJIT 2.1 inside Python (the
lupa package, server/requirements-dev.txt) runs each file as `luajit mod/tests/test_x.lua` would. The 71 test files
(3,000 checks) had never run on the Windows PC, and test_scoping.lua, the one that catches a function reading the nil
global of a local declared further down, silently passed there: it reads `luajit -bl` through io.popen, which found
no luajit.

  python tools/lua_tests.py                    every mod/tests/test_*.lua, several at a time
  python tools/lua_tests.py test_navgraph      just these (names or paths)
  python tools/lua_tests.py --listing FILE     the GGET / GSET lines of a Lua file's bytecode (what test_scoping reads)

  --par N          files at a time (default 4)
  --timeout S      a file still running after S seconds is killed (default 120)
  --luajit         use the luajit binary on PATH instead (the same runs; for a machine that has one)

Each file runs in a process of its own (`--one FILE`: a fresh LuaJIT state, a hang kills one file, not the run), with
the globals a luajit run has: arg[0] the file's path (testlib.lua finds the mod's modules from it), os.exit ending the
file with its code, and io.popen answered in-process for the two commands the tests use: `find <root> -name "*.lua"`
(the files) and `luajit -bl <file>` (the bytecode listing: only its GGET and GSET lines, which is all test_scoping
reads; from jit.util, the opcodes found at run time, so a LuaJIT version change cannot shift them). Anything else goes
to the real io.popen. A file passes when it ends with os.exit(0) (testlib's T.done) and printed no FAIL line.
Exit status 1 when a file did not pass, except the files in tools/quarantine.txt (known failures, printed as KNOWN).
"""
import argparse
import concurrent.futures as cf
import glob
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TESTS = os.path.join(ROOT, "mod", "tests")
EXIT_MARK = "__lua_tests_exit:"

# Lua side: os.exit as an error the runner reads, print collected, io.popen's two commands answered here.
PRELUDE = r'''
local __py_popen, __py_print = ...
arg = { [0] = ARG0 }
os.exit = function(code)
  if code == true then code = 0 elseif code == false then code = 1 end
  error('__lua_tests_exit:' .. tostring(tonumber(code) or 0), 0)
end
print = function(...)
  local t = {}
  for i = 1, select('#', ...) do t[i] = tostring((select(i, ...))) end
  __py_print(table.concat(t, '\t'))
end
local real_popen = io.popen
io.popen = function(cmd, mode)
  local text = __py_popen(cmd)
  if text == nil then return real_popen(cmd, mode) end
  local lines = {}
  for line in tostring(text):gmatch('[^\n]+') do lines[#lines + 1] = line end
  return {
    lines = function() local i = 0; return function() i = i + 1; return lines[i] end end,
    read = function(_, fmt) return table.concat(lines, '\n') end,
    close = function() return true end,
  }
end
local ok, err = xpcall(function() dofile(ARG0) end, function(e)
  local s = tostring(e)
  if s:sub(1, #'__lua_tests_exit:') == '__lua_tests_exit:' then return s end
  return debug.traceback(s, 2)
end)
if ok then return 'ok' end
return tostring(err)
'''

# The bytecode listing: GGET / GSET lines in luajit -bl's shape ("0012    GGET     1   0      ; "name"").
LISTING = r'''
local path = ...
local ju = require('jit.util')
local bit = require('bit')
local function op_at(src, pc) return bit.band(ju.funcbc(assert(loadstring(src)), pc), 0xff) end
local GGET, GSET = op_at('return x', 1), op_at('x = 1', 2)
local f, err = loadfile(path)
if not f then return nil, err end
local out = {}
local function walk(fn)
  local pc = 1
  while true do
    local ins = ju.funcbc(fn, pc)
    if not ins then break end
    local op = bit.band(ins, 0xff)
    if op == GGET or op == GSET then
      local a, d = bit.band(bit.rshift(ins, 8), 0xff), bit.rshift(ins, 16)
      local k = ju.funck(fn, -d - 1)
      out[#out + 1] = string.format('%04d    %s     %d   %d      ; "%s"', pc, op == GGET and 'GGET' or 'GSET', a, d,
        tostring(k))
    end
    pc = pc + 1
  end
  local n = -1
  while true do
    local k = ju.funck(fn, n)
    if k == nil then break end
    if type(k) == 'proto' then walk(k) end
    n = n - 1
  end
end
walk(f)
return table.concat(out, '\n')
'''


def runtime():
    try:
        from lupa import luajit21
    except ImportError as e:
        raise SystemExit("lua_tests: LuaJIT is needed: pip install -r server/requirements-dev.txt (lupa), "
                         f"or put luajit on PATH and pass --luajit ({e})")
    return luajit21.LuaRuntime(unpack_returned_tuples=True, register_eval=False, register_builtins=False)


def listing(path, L=None):
    """The GGET / GSET lines of path's bytecode (compiled, not run), as luajit -bl prints them; None with the compile
    error when the file does not compile (a syntax error, LuaJIT's 200-locals limit)."""
    L = L or runtime()
    fn = L.execute("return function(...)\n" + LISTING + "\nend")
    r = fn(path.replace("\\", "/"))
    if isinstance(r, tuple):            # (nil, error) from loadfile
        return (None, str(r[1])) if r[0] is None else (str(r[0]), None)
    return (str(r), None) if r is not None else (None, "no listing")


def find_files(root):
    out = []
    for d, _, names in os.walk(root):
        for n in sorted(names):
            if n.endswith(".lua"):
                out.append(os.path.join(d, n).replace("\\", "/"))
    return out


def run_one(path):
    """One test file in this process. Returns (code, lines): code 0 passed, 1 failed (FAIL lines or os.exit(1)),
    2 a Lua error (the traceback in lines)."""
    L = runtime()
    lines = []
    path = os.path.abspath(path).replace("\\", "/")

    def popen(cmd):
        cmd = str(cmd)
        if cmd.lstrip().startswith("find "):
            root = cmd.split('"')[1] if '"' in cmd else cmd.split()[1]
            return "\n".join(find_files(root))
        if "luajit" in cmd and "-bl" in cmd:
            target = cmd.split('"')[1] if '"' in cmd else cmd.split()[-1]
            text, err = listing(target, L)
            return text if text is not None else ""
        return None

    def collect(s):
        lines.append(str(s))

    L.globals()["ARG0"] = path
    fn = L.execute("return function(...)\n" + PRELUDE + "\nend")
    res = fn(popen, collect)
    res = res[0] if isinstance(res, tuple) else res
    res = str(res)
    if res == "ok":
        code = 0
    elif res.startswith(EXIT_MARK):
        code = int(float(res[len(EXIT_MARK):]) or 0)
        code = 0 if code == 0 else 1
    else:
        lines.append(res)
        code = 2
    if code == 0 and any(ln.startswith("FAIL") for ln in lines):
        code = 1
    return code, lines


def run_one_luajit(path, timeout):
    p = subprocess.run(["luajit", path], cwd=os.path.dirname(path), capture_output=True, text=True,
                       errors="replace", timeout=timeout)
    lines = (p.stdout + p.stderr).splitlines()
    return (0 if p.returncode == 0 else (2 if p.returncode > 1 else 1)), lines


def quarantined(path=os.path.join(HERE, "quarantine.txt")):
    """{test module: reason} from tools/quarantine.txt: known failures that do not fail the run."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and ":" in line:
                    name, why = line.split(":", 1)
                    out[name.strip()] = why.strip()
    except OSError:
        pass
    return out


def run_files(files, par, timeout, use_luajit, known=None):
    t0 = time.time()
    results = {}
    known = known if known is not None else quarantined()

    def job(f):
        t = time.time()
        if use_luajit:
            try:
                code, lines = run_one_luajit(f, timeout)
            except subprocess.TimeoutExpired:
                code, lines = 3, ["timed out"]
        else:
            try:
                # (the test's own folder as the working directory, as `cd mod/tests && luajit test_x.lua` has it:
                # a few tests put '../beamng_earth/lua/...' on package.path themselves)
                p = subprocess.run([sys.executable, "-B", __file__, "--one", f], capture_output=True, text=True,
                                   errors="replace", timeout=timeout, cwd=os.path.dirname(f))
                code, lines = p.returncode, (p.stdout + p.stderr).splitlines()
            except subprocess.TimeoutExpired:
                code, lines = 3, ["timed out after %d s" % timeout]
        return f, code, lines, time.time() - t

    with cf.ThreadPoolExecutor(max(1, par)) as ex:
        for f, code, lines, dt in ex.map(job, files):
            name = os.path.basename(f)[:-4]
            word = {0: "OK", 1: "FAIL", 2: "ERROR", 3: "HANG"}.get(code, "FAIL")
            if code != 0 and name in known:
                word = "KNOWN"
            summary = next((ln for ln in reversed(lines) if " passed, " in ln), "")
            print(f"{name:28} {word:5} {dt:5.1f}s  {summary}", flush=True)
            if code != 0:
                for ln in lines:
                    if ln.startswith("FAIL") or code >= 2 or " passed, " in ln:
                        print("    " + ln, flush=True)
                if name in known:
                    print(f"    (quarantined: {known[name]})", flush=True)
            results[name] = code
    bad = sorted(n for n, c in results.items() if c != 0 and n not in known)
    allowed = sorted(n for n, c in results.items() if c != 0 and n in known)
    print(f"lua tests: {len(results) - len(bad) - len(allowed)} of {len(results)} files passed in "
          f"{time.time() - t0:.1f} s" + (": " + ", ".join(bad) + " failed" if bad else "")
          + (f"; known failures: {', '.join(allowed)} (tools/quarantine.txt)" if allowed else ""), flush=True)
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description="Run the mod's Lua tests under LuaJIT (lupa), one process per file.")
    ap.add_argument("tests", nargs="*", help="test names or paths (default: every mod/tests/test_*.lua)")
    ap.add_argument("--par", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--luajit", action="store_true", help="use the luajit binary on PATH")
    ap.add_argument("--one", help="(internal) run one file in this process")
    ap.add_argument("--listing", help="print the GGET / GSET bytecode lines of a Lua file")
    a = ap.parse_args()
    if a.one:
        code, lines = run_one(a.one)
        sys.stdout.write("\n".join(lines) + ("\n" if lines else ""))
        return code
    if a.listing:
        text, err = listing(os.path.abspath(a.listing))
        if text is None:
            print(f"does not compile: {err}")
            return 2
        print(text)
        return 0
    if a.luajit and not shutil.which("luajit"):
        raise SystemExit("--luajit: no luajit on PATH")
    files = []
    for t in a.tests or sorted(glob.glob(os.path.join(TESTS, "test_*.lua"))):
        if os.path.isfile(t):
            files.append(os.path.abspath(t))
        else:
            name = t if t.endswith(".lua") else t + ".lua"
            p = os.path.join(TESTS, name if name.startswith("test_") else "test_" + name)
            if not os.path.isfile(p):
                raise SystemExit(f"no such test: {t}")
            files.append(p)
    if not a.luajit:
        runtime()           # (lupa missing: say so before starting anything)
    return run_files(files, a.par, a.timeout, a.luajit)


if __name__ == "__main__":
    sys.exit(main())
