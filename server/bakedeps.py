"""What each kind of baked item is made by, recorded during a bake and keyed on (2026-10-29; Jeff: a change should
rebake only what it touches).

The revisit cache's key held a hash of every server source file, so any server change made every baked item unreachable.
Instead, while a bake builds, each process watches which Python functions run (sys.monitoring, call events only) and
which data files under server/ are opened (an audit hook) between building(kind)'s start and end, and unions them per
kind: the kind's dependencies, {kind: {"functions": ["module:qualname", ...], "files": ["name", ...]}}. A run writes
each process's union to RECORD_DIR (BNGE_BAKE_DEPS_DIR, deps-<pid>.json); tools/bake_deps.py merges the runs into
MANIFEST (server/bake_deps.json), committed like code.

revisit.code_key(kind) then hashes, for a kind in the manifest, the source of each listed function (its def's text from
the module's syntax tree: a comment or an unrelated function in the same file changes nothing), the module-level
statements of the modules those functions live in (constants, tables), the listed data files, the native module and the
BNE_ switches. The game's server reads the same committed file and computes the same key, so what the bake stored is
what the game finds. A kind without an entry keeps the whole-code key, as before. BNE_REVISIT_DEPS=off: every kind
back to the whole-code key (a BNE_ switch: in the key, so the game and the bake must agree, as they do today).

Sound because an item's bytes depend only on the code that ran and the data it read, and the record holds all of it
for the route baked. A build that runs a function not yet listed adds it (the next merge); that kind's key then differs
once. Recording stops by itself in a process once every kind's set has been still for STABLE_BUILDS builds.
"""
import ast
import atexit
import hashlib
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "bake_deps.json")
VERSION = 1
RECORD_DIR = os.environ.get("BNGE_BAKE_DEPS_DIR")          # record when set (the headless bake's servers)
ON = os.environ.get("BNE_REVISIT_DEPS", "on") != "off"      # keys from the manifest
STABLE_BUILDS = 200         # recording stops once every kind's set has not grown for this many builds
WRITE_EVERY_S = 60.0        # a recording process writes its file this often (and at exit)
TOOL_ID = 4                 # sys.monitoring tool id (0-5 are free for scripts; 4 is unused by the profilers here)
DATA_SUFFIXES = (".json", ".npz", ".npy", ".png", ".gz", ".csv", ".txt", ".bin", ".dds")

_lock = threading.Lock()
_local = threading.local()      # .kinds: the kinds this thread is building (a stack)
_deps = {}                      # kind -> {"functions": set, "files": set}
_builds = {}                    # kind -> builds since its set last grew
_recording = [False]
_written_at = [0.0]
_manifest = []                  # [dict] once read


# --- recording ------------------------------------------------------------------------------------------------------
def recording():
    return _recording[0]


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


_HERE_N = _norm(HERE) + os.sep


def function_name(code):
    """"module:qualname" of a code object in server/, the enclosing def for a lambda or a generator expression (their
    text is inside it); None for code outside server/ or at module level."""
    fn = code.co_filename
    if not fn or not fn.endswith(".py") or os.path.dirname(_norm(fn)) + os.sep != _HERE_N:    # (server/*.py alone)
        return None
    module = os.path.splitext(os.path.basename(fn))[0]
    qual = code.co_qualname
    parts = [p for p in qual.split(".") if p != "<locals>"]
    while parts and parts[-1].startswith("<"):           # <lambda>, <genexpr>, <listcomp>: the def that holds it
        parts.pop()
    if not parts:
        return None
    return f"{module}:{'.'.join(parts)}"


def _on_start(code, offset):
    kinds = getattr(_local, "kinds", None)
    if not kinds:
        return
    name = function_name(code)
    if name is None:
        return
    for kind in kinds:
        d = _deps[kind]
        if name not in d["functions"]:
            d["functions"].add(name)
            _builds[kind] = 0


def _on_open(event, args):
    if event != "open":
        return
    kinds = getattr(_local, "kinds", None)
    if not kinds:
        return
    path = args[0]
    if not isinstance(path, str) or not path.lower().endswith(DATA_SUFFIXES):
        return
    n = _norm(path)
    if not n.startswith(_HERE_N):
        return
    rel = os.path.relpath(n, _norm(HERE)).replace(os.sep, "/")
    for kind in kinds:
        d = _deps[kind]
        if rel not in d["files"]:
            d["files"].add(rel)
            _builds[kind] = 0


def start_recording(record_dir=None):
    """Record this process's builds (building) into record_dir/deps-<pid>.json. Idempotent; False without
    sys.monitoring."""
    record_dir = record_dir or RECORD_DIR
    if _recording[0] or not record_dir or not hasattr(sys, "monitoring"):
        return _recording[0]
    mon = sys.monitoring
    try:
        mon.use_tool_id(TOOL_ID, "bakedeps")
    except ValueError:          # (taken: another tracer in this process)
        return False
    mon.register_callback(TOOL_ID, mon.events.PY_START, _on_start)
    mon.set_events(TOOL_ID, mon.events.PY_START)
    sys.addaudithook(_on_open)      # (never removed: it does nothing with no kind building)
    _recording[0] = True
    _record_dir[0] = record_dir
    atexit.register(write)
    return True


_record_dir = [None]


def stop_recording():
    if not _recording[0]:
        return
    mon = sys.monitoring
    try:
        mon.set_events(TOOL_ID, 0)
        mon.free_tool_id(TOOL_ID)
    except ValueError:
        pass
    _recording[0] = False


class building:
    """with building(kind): what runs inside is recorded as kind's (nested kinds all take it). A no-op when not
    recording. Counts the build for STABLE_BUILDS and writes the record every WRITE_EVERY_S."""

    def __init__(self, kind):
        self.kind = kind

    def __enter__(self):
        if not _recording[0] or not self.kind:
            return self
        with _lock:
            _deps.setdefault(self.kind, {"functions": set(), "files": set()})
            _builds.setdefault(self.kind, 0)
        stack = getattr(_local, "kinds", None)
        if stack is None:
            stack = _local.kinds = []
        stack.append(self.kind)
        return self

    def __exit__(self, *exc):
        if not _recording[0] or not self.kind:
            return False
        stack = getattr(_local, "kinds", None)
        if stack and stack[-1] == self.kind:
            stack.pop()
        with _lock:
            _builds[self.kind] = _builds.get(self.kind, 0) + 1
            still = all(n >= STABLE_BUILDS for n in _builds.values())
        import time
        if still:
            write()
            stop_recording()
        elif time.monotonic() - _written_at[0] >= WRITE_EVERY_S:
            write()
        return False


def snapshot():
    """{kind: {"functions": [sorted], "files": [sorted]}} of this process's record."""
    with _lock:
        return {k: {"functions": sorted(v["functions"]), "files": sorted(v["files"])} for k, v in _deps.items()}


def write(path=None):
    """This process's record to path (default RECORD_DIR/deps-<pid>.json). Never raises."""
    import time
    _written_at[0] = time.monotonic()
    path = path or (os.path.join(_record_dir[0], f"deps-{os.getpid()}.json") if _record_dir[0] else None)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "pid": os.getpid(), "kinds": snapshot()}, f, indent=0, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


# --- the manifest and its merge -------------------------------------------------------------------------------------
def read_manifest(path=MANIFEST):
    """{kind: {"functions": [...], "files": [...]}} of the committed manifest; {} when there is none or it is broken."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        kinds = data.get("kinds") or {}
        return {k: {"functions": sorted(set(v.get("functions") or ())), "files": sorted(set(v.get("files") or ()))}
                for k, v in kinds.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def manifest():
    """The committed manifest, read once per process (ON; {} with BNE_REVISIT_DEPS=off)."""
    with _lock:
        if not _manifest:
            _manifest.append(read_manifest() if ON else {})
        return _manifest[0]


def merge(records):
    """The union of records ([{kind: {"functions", "files"}}]) per kind, sorted."""
    out = {}
    for rec in records:
        for kind, v in (rec or {}).items():
            d = out.setdefault(kind, {"functions": set(), "files": set()})
            d["functions"].update(v.get("functions") or ())
            d["files"].update(v.get("files") or ())
    return {k: {"functions": sorted(v["functions"]), "files": sorted(v["files"])} for k, v in sorted(out.items())}


def write_manifest(kinds, path=MANIFEST):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"version": VERSION, "kinds": kinds}, f, indent=1, sort_keys=True)
        f.write("\n")


def diff(old, new):
    """{kind: (added functions, removed functions, added files, removed files)} between two manifests, kinds with a
    difference only."""
    out = {}
    for kind in sorted(set(old) | set(new)):
        a, b = old.get(kind) or {"functions": [], "files": []}, new.get(kind) or {"functions": [], "files": []}
        fa, fb = set(a["functions"]), set(b["functions"])
        da, db = set(a["files"]), set(b["files"])
        if fa != fb or da != db:
            out[kind] = (sorted(fb - fa), sorted(fa - fb), sorted(db - da), sorted(da - db))
    return out


# --- the key --------------------------------------------------------------------------------------------------------
_sources = {}       # module -> (functions {qualname: source}, module-level source) or None (no such module)


def module_sources(module, here=HERE):
    """({qualname: the def's source}, the module-level statements' source) of server/<module>.py, or None."""
    with _lock:
        if module in _sources:
            return _sources[module]
    path = os.path.join(here, module + ".py")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        tree = ast.parse(text)
    except (OSError, SyntaxError, ValueError):
        with _lock:
            _sources[module] = None
        return None
    funcs = {}
    top = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                funcs[name] = ast.get_source_segment(text, child) or ""
                walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
    walk(tree, "")
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top.append(ast.get_source_segment(text, node) or "")
        elif isinstance(node, ast.ClassDef):          # a class's own statements (attributes), not its methods
            for sub in node.body:
                if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    top.append(ast.get_source_segment(text, sub) or "")
    got = (funcs, "\n".join(top))
    with _lock:
        _sources[module] = got
    return got


def kind_hash(deps, here=HERE, extra=b""):
    """The hash of a kind's dependencies: each function's source (missing: marked), each module's module-level
    statements, each data file's bytes, the native module and CODE_ID, plus extra (the BNE_ switches)."""
    h = hashlib.sha1(b"deps\0")
    modules = sorted({name.split(":", 1)[0] for name in deps.get("functions") or ()})
    for name in sorted(deps.get("functions") or ()):
        module, qual = name.split(":", 1)
        src = module_sources(module, here)
        text = (src[0].get(qual) if src else None)
        h.update(name.encode() + b"\0" + (text.encode("utf-8") if text is not None else b"<missing>") + b"\0")
    for module in modules:
        src = module_sources(module, here)
        h.update(module.encode() + b"\0" + (src[1].encode("utf-8") if src else b"<missing>") + b"\0")
    for rel in sorted(deps.get("files") or ()):
        try:
            with open(os.path.join(here, rel), "rb") as f:
                h.update(rel.encode() + b"\0" + f.read() + b"\0")
        except OSError:
            h.update(rel.encode() + b"\0<missing>\0")
    try:
        for name in sorted(os.listdir(here)):
            if name == "CODE_ID" or name.startswith("earthfast"):
                with open(os.path.join(here, name), "rb") as f:
                    h.update(name.encode() + b"\0" + f.read())
    except OSError:
        pass
    h.update(extra)
    return h.hexdigest()[:16]


def missing_functions(kinds, here=HERE):
    """[(kind, name)] of listed functions that no longer exist (the gate's check)."""
    out = []
    for kind, v in sorted(kinds.items()):
        for name in v.get("functions") or ():
            module, qual = name.split(":", 1)
            src = module_sources(module, here)
            if src is None or qual not in src[0]:
                out.append((kind, name))
    return out
