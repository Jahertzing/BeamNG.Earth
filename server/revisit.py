"""The revisit cache: what was built for an anchor kept on disk by the anchor's place, so a place visited again (a game
started where it was left, a teleport back) loads it instead of building it again. Teleport anchors sit on a grid
(the mod's geodesy.snapAnchor), so the same place gets the same frame, and what was built in it fits again.

One file per item: a zip (deflated at level 1: ~5 times smaller, ~8 ms a city road tile to read back) of named blobs
and meta.json; the callers take the session's names out and put them back. Cut back at the server's start like the
other caches (cache.prune_dir: the least recently used first).

Only a complete build is kept: one during which nothing it may have read failed or was not there yet (a download
that failed, a map cell that could not be read, land cover or land use not cached yet: those builds leave things out
and would be kept so). fault() marks that, process wide; a build is kept when faults() did not change during it.

The key holds the server's code (code_key: a hash of its source files and native module), so any change to how things
are built starts afresh.

A baked route's items (tools/route_bake.py) are pinned: an item built in one of the route's anchors (PIN_SUBDIR's
anchors.json, the anchors the mod pins its teleports and rebases to: routepins.lua) goes to PIN_SUBDIR, which is
never cut back, so a whole route (several GB) stays on disk. Its anchor is the key's first two parts (lat, lon), as
every anchor's kind has them. load() looks there first. A code change makes them unreachable as any other; the bake
tool builds the route again and removes what that run did not use. PIN_SUBDIR lives in the world folder (cache.py "The
world folder": <world>/bake, never cut back or cleared) when there is one, else in the cache folder (pin_dir).

Generations: a code change no longer throws a baked route away. The bake's own server (tools/headless_bake.py,
BNE_REVISIT_GENS=bake) marks each anchor it stores or reads pinned items for, under its code (PIN_SUBDIR/gens/<code>/).
Another server, missing an item of an anchor its own code never baked, takes the whole anchor from the newest
generation that did (one generation per anchor and process: a road tile and the ground carved for it come from the
same code). So after an update the route stays baked as it was, and the next bake run replaces it anchor by anchor
(Jeff, 2026-10-28: "why does it have to keep restarting from scratch")."""
import hashlib
import json
import os
import re
import threading
import time
import zipfile

import cache

ON = os.environ.get("BNE_REVISIT", "on") != "off"
SKIP = {k for k in os.environ.get("BNE_REVISIT_SKIP", "").split(",") if k}     # kinds left out (tests: "roads,far")
MAX_MB = float(os.environ.get("BNE_REVISIT_MB", "2000"))      # the cache's size, cut back at start (earthd)
SUBDIR = "revisit"
PIN_SUBDIR = "bake"         # a baked route's items: never cut back (route_bake.py removes the stale ones)
ANCHORS = "anchors.json"    # in PIN_SUBDIR: {"anchors": [[lat, lon], ...]} the baked route's anchors
CACHE_DIR = None            # the server's cache folder (blockworker._State, earthd.Server): stores without one at hand
PIN_DIR = None              # the baked route's folder; None: pin_dir's (the world folder's PIN_SUBDIR, else the cache's)
GENS = os.environ.get("BNE_REVISIT_GENS", "on")     # on: older baked generations as a fallback; bake: marks (the
                                                    # bake's server, which builds anew); off: neither
GENS_SUBDIR = "gens"        # in PIN_SUBDIR: <code>/<lat>_<lon> an anchor that code baked, <code>/started its first mark
MAX_GENS = 8                # the older generations looked at

_faults = [0]
_code = []
_lock = threading.Lock()
_at = {}                    # key -> its anchor (lat, lon), from key(): whether store() pins it
_parts = {}                 # key -> (kind, parts), from key(): the same item's key under an older generation's code
_gen_of = {}                # (pin dir, anchor) -> the code this process takes the anchor's items from (sticky)
_marked = set()             # (pin dir, code, anchor) marked already
_pins = {"stamp": None, "set": frozenset()}


def fault():
    """Something a build may read failed or was not there yet: builds running now are not kept."""
    _faults[0] += 1


def faults():
    return _faults[0]


def code_key():
    """A hash of the server's source files and native module (once per process)."""
    with _lock:
        if not _code:
            here = os.path.dirname(os.path.abspath(__file__))
            h = hashlib.sha1()
            for name in sorted(os.listdir(here)):
                # (a packaged app's modules are in its archive, not beside __file__: the build's CODE_ID, their
                # fingerprint, is; without it a Python-only update reused the older build's items. From PR #368)
                if name == "CODE_ID" or name.endswith(".py") or name.startswith("earthfast"):
                    try:
                        with open(os.path.join(here, name), "rb") as f:
                            h.update(name.encode() + b"\0" + f.read())
                    except OSError:
                        pass
            for k in sorted(os.environ):                # (the build switches: BNE_*)
                if k.startswith("BNE_") and k not in ("BNE_REVISIT", "BNE_REVISIT_MB", "BNE_REVISIT_SKIP",
                                                      "BNE_REVISIT_GENS"):
                    h.update(f"{k}={os.environ[k]}".encode())
            _code.append(h.hexdigest()[:16])
        return _code[0]


def _key_with(kind, code, parts):
    return hashlib.sha1(repr((kind, code, parts)).encode()).hexdigest()


def key(kind, *parts):
    """The cache key of an item: its kind, what it was built from (the anchor's lat/lon, the tile, the settings) and
    the code."""
    k = _key_with(kind, code_key(), parts)
    if len(parts) >= 2 and isinstance(parts[0], float) and isinstance(parts[1], float):
        with _lock:
            if len(_at) > 100000:
                _at.clear()
                _parts.clear()
            _at[k] = (parts[0], parts[1])
            _parts[k] = (kind, parts)
    return k


def _anchor_name(at):
    return f"{round(at[0], 7):.7f}_{round(at[1], 7):.7f}"


def mark_anchor(pd, code, at, started=None):
    """Anchor at (lat, lon) baked under code in the baked route's folder pd (started: the generation's time, when it is
    the first mark)."""
    gd = os.path.join(pd, GENS_SUBDIR, code)
    os.makedirs(gd, exist_ok=True)
    first = os.path.join(gd, "started")
    if not os.path.exists(first):
        with open(first, "w") as f:
            f.write(repr(time.time() if started is None else float(started)))
    open(os.path.join(gd, _anchor_name(at)), "a").close()


def _mark(cache_dir, k):
    """The bake's server stored or read a pinned item of k's anchor: the anchor is baked under this code."""
    with _lock:
        at = _at.get(k)
    if at is None:
        return
    pd, code = pin_dir(cache_dir), code_key()
    if (pd, code, at) in _marked:
        return
    try:
        mark_anchor(pd, code, at)
        with _lock:
            _marked.add((pd, code, at))
    except OSError:
        pass


def _gens(pd):
    """The baked generations [code], newest first."""
    root = os.path.join(pd, GENS_SUBDIR)
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(root, name, "started")) as f:
                out.append((float(f.read().strip() or 0), name))
        except (OSError, ValueError):
            continue
    return [c for _, c in sorted(out, reverse=True)]


def _gen_for(cache_dir, at):
    """The code this process takes anchor at's items from: its own when its code baked the anchor or no generation
    did, else the newest generation that did."""
    pd = pin_dir(cache_dir)
    with _lock:
        got = _gen_of.get((pd, at))
    if got is not None:
        return got
    code, name, got = code_key(), _anchor_name(at), code_key()
    gens = _gens(pd)
    if code not in gens or not os.path.exists(os.path.join(pd, GENS_SUBDIR, code, name)):
        for g in gens[:MAX_GENS]:
            if g != code and os.path.exists(os.path.join(pd, GENS_SUBDIR, g, name)):
                got = g
                break
    with _lock:
        if len(_gen_of) > 10000:
            _gen_of.clear()
        _gen_of.setdefault((pd, at), got)
        return _gen_of[(pd, at)]


def pin_dir(cache_dir, world_dir=None):
    """The baked route's folder: PIN_DIR, else PIN_SUBDIR in the world folder (world_dir, else cache.world_dir(): the
    server's and its workers' --world-dir), else in cache_dir."""
    if PIN_DIR:
        return PIN_DIR
    w = world_dir or cache.world_dir()
    return os.path.join(w if w else cache_dir, PIN_SUBDIR)


def pinned_anchors(cache_dir):
    """The baked route's anchors {(lat, lon) rounded to 7 places} (pin_dir/ANCHORS; read again when it changes)."""
    path = os.path.join(pin_dir(cache_dir), ANCHORS)
    try:
        st = os.stat(path)
        stamp = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return frozenset()
    with _lock:
        if _pins["stamp"] == stamp:
            return _pins["set"]
    try:
        with open(path, encoding="utf-8") as f:
            got = frozenset((round(float(a[0]), 7), round(float(a[1]), 7)) for a in json.load(f)["anchors"])
    except Exception:
        got = frozenset()
    with _lock:
        _pins["stamp"], _pins["set"] = stamp, got
    return got


def _pinned(cache_dir, k):
    with _lock:
        at = _at.get(k)
    return at is not None and (round(at[0], 7), round(at[1], 7)) in pinned_anchors(cache_dir)


def _path(cache_dir, kind, k, sub=SUBDIR):
    return os.path.join(pin_dir(cache_dir) if sub == PIN_SUBDIR else os.path.join(cache_dir, sub), kind, k[:2],
                        k + ".zip")


def store(cache_dir, kind, k, blobs, extra=None):
    """Keep an item under key k: blobs {name: bytes}, extra JSON data (in PIN_SUBDIR when built in a baked route's
    anchor). Never raises; False when not kept."""
    if not ON or kind in SKIP:
        return False
    pinned = _pinned(cache_dir, k)
    path = _path(cache_dir, kind, k, PIN_SUBDIR if pinned else SUBDIR)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for name, data in blobs.items():
                z.writestr(name, data)
            z.writestr("meta.json", json.dumps({"names": list(blobs), "extra": extra}))
        os.replace(tmp, path)
        if pinned and GENS == "bake":
            _mark(cache_dir, k)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def has(cache_dir, kind, k):
    """Whether an item is kept under key k (never reads it)."""
    if not ON or kind in SKIP:
        return False
    return os.path.exists(_path(cache_dir, kind, k, PIN_SUBDIR)) or os.path.exists(_path(cache_dir, kind, k))


def load(cache_dir, kind, k):
    """({name: bytes}, extra) of the item under key k, or None when there is none (or it cannot be read)."""
    if not ON or kind in SKIP:
        return None
    if GENS == "on" and _pinned(cache_dir, k):
        with _lock:
            at, kp = _at.get(k), _parts.get(k)
        g = _gen_for(cache_dir, at)
        if g != code_key():
            if kp is None or kp[0] != kind:
                return None
            return _read(_path(cache_dir, kind, _key_with(kind, g, kp[1]), PIN_SUBDIR))
    path = _path(cache_dir, kind, k, PIN_SUBDIR)
    if not os.path.exists(path):
        path = _path(cache_dir, kind, k)
        if not os.path.exists(path):
            return None
        if _pinned(cache_dir, k):           # (kept before its route was baked: pinned now)
            pin = _path(cache_dir, kind, k, PIN_SUBDIR)
            try:
                cache.move_file(path, pin)      # (the world folder may be on another drive)
                path = pin
            except OSError:
                pass
    got = _read(path)
    if got is not None and GENS == "bake" and path == _path(cache_dir, kind, k, PIN_SUBDIR):
        _mark(cache_dir, k)                 # (an anchor this code baked before: still this generation's)
    return got


def _read(path):
    if not os.path.exists(path):
        return None
    try:
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read("meta.json"))
            blobs = {name: z.read(name) for name in meta["names"]}
        try:
            os.utime(path, None)            # (used: kept longest when cut back)
        except OSError:
            pass
        return blobs, meta.get("extra")
    except Exception:
        return None


def swap(text, tokens):
    """text with each (a, b) of tokens replaced in turn."""
    for a, b in tokens:
        text = text.replace(a, b)
    return text


# (json.dumps' default separators too: buildings.far_tile writes '"shape": "..."', and its far building tiles were kept
# without their mesh, a revisit's hit pointing at a shape file that was never written)
SHAPE_RE = re.compile(r'"shape":\s*"([^"]+)"')


def shapes_in(text, vfs_dir):
    """The file names of the shapes a tile's JSON points to in vfs_dir."""
    pre = vfs_dir.rstrip("/") + "/"
    return sorted({s[len(pre):] for s in SHAPE_RE.findall(text) if s.startswith(pre) and "/" not in s[len(pre):]})
