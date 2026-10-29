#!/usr/bin/env python3
"""Static checks over the Python and the mod's Lua, before a branch is handed over (tools/gate.py runs it first).
The three bug classes that reached the game in the week of 2026-10-28 are each a static finding: core.lua at LuaJIT's
200-locals limit (a compile error), a function reading the nil global of a local declared further down (the mod's
test_scoping, which tools/lua_tests.py runs), and blockworker.py defining skyline_tile twice (a duplicate definition).

  python tools/lint.py                 everything
  python tools/lint.py --python        ruff (pyproject.toml [tool.ruff]: pyflakes rules, undefined names, syntax) over
                                       server/, tools/ and the root scripts, plus duplicate definitions in a module or
                                       class (a later def silently replaces an earlier one that may be in use)
  python tools/lint.py --lua           every mod/**/*.lua compiled under LuaJIT (syntax, the 200-locals limit), the
                                       main chunk's local count against the limit, and duplicate function definitions
                                       in a file
  --files F...                         only these files (the gate passes the diff's)

Prints one line per finding (path:line: what) and a PASS / FAIL line per check. Exit status 1 on any finding. A
main chunk within ten locals of the limit is a warning (printed, not failing): core.lua sits at 200 today.
"""
import argparse
import ast
import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY_DIRS = ("server", "tools")
LUA_ROOT = os.path.join(ROOT, "mod")
LOCALS_LIMIT = 200          # LuaJIT: "main function has more than 200 local variables"
LOCALS_WARN = 190           # a chunk this close to the limit is a finding: the next local breaks the file
LUA_DEF = re.compile(r"^(?:local\s+function\s+([A-Za-z_][\w]*)|function\s+([A-Za-z_][\w.:]*)|"
                     r"([A-Za-z_][\w.]*)\s*=\s*function)\b")
LUA_LOCAL = re.compile(r"^local\s+(?:function\s+)?([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)*)")
SKIP_DECOS = ("property", "setter", "getter", "deleter", "overload", "register", "dispatch")


def py_files(files=None):
    if files:
        return [f for f in files if f.endswith(".py")]
    out = sorted(glob.glob(os.path.join(ROOT, "*.py")))
    for d in PY_DIRS:
        out += sorted(glob.glob(os.path.join(ROOT, d, "**", "*.py"), recursive=True))
    return [f for f in out if os.sep + "build" + os.sep not in f]


def lua_files(files=None):
    if files:
        return [f for f in files if f.endswith(".lua")]
    return sorted(glob.glob(os.path.join(LUA_ROOT, "**", "*.lua"), recursive=True))


def rel(p):
    try:
        return os.path.relpath(p, ROOT)
    except ValueError:
        return p


# --- Python ------------------------------------------------------------------------------------------------------

def ruff(files):
    """ruff over files; the findings as lines, or None when ruff is not installed."""
    try:
        import ruff  # noqa: F401  (the package: its binary comes with it)
    except ImportError:
        return None
    cmd = [sys.executable, "-m", "ruff", "check", "--config", os.path.join(ROOT, "pyproject.toml"),
           "--output-format", "concise", "--no-cache"] + [rel(f) for f in files]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace")
    lines = [ln for ln in p.stdout.splitlines() if ln and not ln.startswith(("Found ", "[*]", "All checks"))]
    if p.returncode not in (0, 1):
        lines.append("ruff failed: " + (p.stderr.strip() or p.stdout.strip()))
    return lines


def duplicate_defs(src, path="<file>"):
    """Duplicate def / class names in one scope (module or class body): the later one silently replaces the
    earlier. A property's setter, an overload and a name assigned (not defined) before its def are not findings."""
    try:
        tree = ast.parse(src, path)
    except SyntaxError as e:
        return [f"{path}:{e.lineno}: syntax error: {e.msg}"]
    out = []

    def scope(body, where):
        seen = {}
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                decos = " ".join(ast.unparse(d) for d in n.decorator_list)
                if any(s in decos for s in SKIP_DECOS):
                    continue
                if n.name in seen:
                    out.append(f"{path}:{n.lineno}: {where}{n.name} defined again (first at line {seen[n.name]})")
                seen[n.name] = n.lineno
                if isinstance(n, ast.ClassDef):
                    scope(n.body, f"{n.name}.")
    scope(tree.body, "")
    return out


def check_python(files):
    findings = []
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            findings += duplicate_defs(fh.read(), rel(f))
    r = ruff(files)
    if r is None:
        findings.append("ruff is not installed: pip install -r server/requirements-dev.txt")
    else:
        findings += r
    return findings


# --- Lua ---------------------------------------------------------------------------------------------------------

def lua_compiler():
    """A function (path) -> error string or None, from lupa's LuaJIT; None when lupa is missing."""
    try:
        from lupa import luajit21
    except ImportError:
        return None
    L = luajit21.LuaRuntime(register_eval=False, register_builtins=False)
    loadfile = L.eval("function(p) local f, e = loadfile(p); if f then return nil end; return e end")

    def compile_(path):
        return loadfile(path.replace("\\", "/"))
    return compile_


def lua_findings(src, path="<file>", test=False):
    """Pure checks on a Lua file's text: duplicate function definitions (the later replaces the earlier; not in a
    test, which re-stubs on purpose) as findings, and the main chunk's local count near LuaJIT's limit as a warning
    (the file still compiles; the next local breaks it). Returns (findings, warnings)."""
    out, warn = [], []
    locals_n, defs = 0, {}
    for i, line in enumerate(src.splitlines(), 1):
        m = LUA_LOCAL.match(line)
        if m:
            locals_n += len(m.group(1).split(","))
        m = LUA_DEF.match(line)
        if m and not test:
            name = m.group(1) or m.group(2) or m.group(3)
            if name in defs:
                out.append(f"{path}:{i}: function {name} defined again (first at line {defs[name]})")
            else:
                defs[name] = i
    if locals_n >= LOCALS_WARN:
        warn.append(f"{path}:1: {locals_n} locals in the main chunk (LuaJIT's limit is {LOCALS_LIMIT}: "
                    f"the next one is a compile error; make some module fields)")
    return out, warn


def is_test(path):
    return (os.sep + "tests" + os.sep) in path or "/tests/" in path


def check_lua(files):
    """(findings, warnings) over the Lua files."""
    findings, warnings = [], []
    compile_ = lua_compiler()
    if compile_ is None:
        findings.append("LuaJIT (lupa) is not installed: pip install -r server/requirements-dev.txt; "
                        "the Lua files were not compiled")
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        a, b = lua_findings(src, rel(f), is_test(f))
        findings += a
        warnings += b
        if compile_ is not None:
            err = compile_(f)
            if err:
                findings.append(f"{rel(f)}: does not compile: {err}")
    return findings, warnings


def main():
    ap = argparse.ArgumentParser(description="Static checks: ruff and duplicate defs (Python), compile, locals and "
                                             "duplicate functions (Lua).")
    ap.add_argument("--python", action="store_true")
    ap.add_argument("--lua", action="store_true")
    ap.add_argument("--files", nargs="*", help="only these files")
    a = ap.parse_args()
    if not a.python and not a.lua:
        a.python = a.lua = True
    files = [os.path.abspath(f) for f in a.files] if a.files else None
    bad = 0
    if a.python:
        py = py_files(files)
        f = check_python(py) if py else []
        print("\n".join(f))
        print(f"python: {'PASS' if not f else 'FAIL'} ({len(py)} files, {len(f)} findings)", flush=True)
        bad += len(f)
    if a.lua:
        lua = lua_files(files)
        f, w = check_lua(lua) if lua else ([], [])
        print("\n".join(f + ["warning: " + x for x in w]))
        print(f"lua: {'PASS' if not f else 'FAIL'} ({len(lua)} files, {len(f)} findings, {len(w)} warnings)",
              flush=True)
        bad += len(f)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
