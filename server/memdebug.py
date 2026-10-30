"""Who holds the memory: the large numpy arrays alive in this process grouped by the chain of objects that refer to them
(BNGE_MEM_HOLDERS=1, with the BNGE_TRACEMALLOC reports). tracemalloc names the line that allocated an array (np.load,
for the map data's cell layers, 2026-10-30: 3.5 GB in the server, far past the layer cache's cap) but not who keeps it;
this walks the garbage collector's object graph once and reports, for the largest arrays, the holder chain:
"Features.lonlat < dict[roads] < CellData.layers < list < BridgeProfiles.cells". Slow (a few seconds in a process with
millions of objects): a debug switch, every HOLDERS_S at most."""
import gc
import logging
import os
import time

log = logging.getLogger("earthd.memdebug")

ON = os.environ.get("BNGE_MEM_HOLDERS", "") not in ("", "0", "off", "no")
HOLDERS_S = 300.0       # s between reports
MIN_MB = 0.5            # arrays smaller than this are not followed
TOP = 10                # chains reported
DEPTH = 6               # objects up the chain


def _name_in(container, obj):
    """Where obj sits in container: dict key (as [key] or an attribute when the dict is an object's __dict__), a
    list / tuple / set slot."""
    if isinstance(container, dict):
        for k, v in list(container.items()):
            if v is obj:
                return f"[{k!r}]" if not isinstance(k, str) else f"[{k}]"
        return "[?]"
    if isinstance(container, (list, tuple)):
        return "[]"
    if isinstance(container, (set, frozenset)):
        return "{}"
    return ""


def _owner_of_dict(d, referrers):
    """The object whose __dict__ is d, among d's referrers, or None."""
    for r in referrers:
        if getattr(r, "__dict__", None) is d:
            return r
    return None


def chain(obj, depth=DEPTH, skip=()):
    """The holder chain of obj as "Type.attr < Type2[key] < ...", innermost holder first, up to depth objects; skip:
    objects (ids) that are the walk's own (its lists). Frames and the walk's locals are left out."""
    parts = []
    cur = obj
    seen = {id(obj)} | set(skip)
    for _ in range(depth):
        refs = [r for r in gc.get_referrers(cur) if id(r) not in seen and type(r).__name__ not in ("frame", "cell")]
        if not refs:
            break
        r = refs[0]
        seen.add(id(r))
        if isinstance(r, dict):
            owner = _owner_of_dict(r, gc.get_referrers(r))
            name = _name_in(r, cur)
            if owner is not None:
                seen.add(id(owner))
                parts.append(f"{type(owner).__name__}.{name[1:-1]}" if name.startswith("[") else type(owner).__name__)
                cur = owner
                continue
            parts.append(f"dict{name}")
            cur = r
            continue
        parts.append(f"{type(r).__name__}{_name_in(r, cur)}")
        cur = r
    return " < ".join(parts) if parts else "(unreferenced)"


def _items(container):
    if isinstance(container, dict):
        return [(f"[{k}]" if isinstance(k, str) else f"[{k!r}]", v) for k, v in list(container.items())]
    return [("[]", v) for v in list(container)]


def _large_arrays(min_mb, into=2):
    """[(array, holder, name)]: the large owning arrays reachable from the collector's objects. numpy arrays are not
    tracked by the collector, nor are dicts, lists and tuples holding nothing but untracked values (CPython untracks
    them), so the walk looks into every tracked dict, list, tuple and instance __dict__, and `into` levels down through
    untracked containers found there. The holder is the tracked object the array was reached from (an instance for
    its __dict__: "Features.lonlat"), name the path inside it."""
    import numpy as np
    floor = min_mb * 1e6
    found = {}

    def walk(items, holder, prefix, depth):
        for name, v in items:
            if isinstance(v, np.ndarray):
                if v.base is None and v.nbytes >= floor and id(v) not in found:
                    found[id(v)] = (v, holder, prefix + name)
            elif depth > 0 and isinstance(v, (dict, list, tuple)) and not gc.is_tracked(v):
                walk(_items(v), holder, prefix + name, depth - 1)
    for o in gc.get_objects():
        if isinstance(o, (dict, list, tuple)):
            walk(_items(o), o, "", into)
        else:
            d = getattr(o, "__dict__", None)
            if type(d) is dict and d:
                walk([("." + k, v) for k, v in list(d.items())], o, "", into)
    return list(found.values())


def holders(min_mb=MIN_MB, top=TOP, depth=DEPTH, limit=400):
    """[(MB, count, chain)] of this process's large owning arrays (a view's base counts, not the view), the limit
    largest followed, grouped by holder chain (the holder and its path, then the holder's holders), the top chains by
    bytes."""
    arrays = _large_arrays(min_mb)
    arrays.sort(key=lambda t: -t[0].nbytes)
    arrays = arrays[:limit]
    skip = (id(arrays),) + tuple(id(t) for t in arrays)
    by = {}
    for a, holder, name in arrays:
        if isinstance(holder, dict):
            owner = _owner_of_dict(holder, gc.get_referrers(holder))
            if owner is not None:
                first = f"{type(owner).__name__}.{name[1:]}" if name.startswith("[") else type(owner).__name__ + name
                first = first.replace("]", "", 1) if name.startswith("[") else first
                rest = chain(owner, depth - 1, skip + (id(holder),))
            else:
                first = f"dict{name}"
                rest = chain(holder, depth - 1, skip)
        elif isinstance(holder, (list, tuple)):
            first = f"{type(holder).__name__}{name}"
            rest = chain(holder, depth - 1, skip)
        else:
            first = f"{type(holder).__name__}{name}"
            rest = chain(holder, depth - 1, skip)
        c = first if rest == "(unreferenced)" else f"{first} < {rest}"
        mb, n = by.get(c, (0.0, 0))
        by[c] = (mb + a.nbytes / 1e6, n + 1)
    return sorted(((mb, n, c) for c, (mb, n) in by.items()), key=lambda t: -t[0])[:top]


def report(prefix=""):
    """The holders as log lines (INFO), with the walk's own time."""
    t0 = time.perf_counter()
    try:
        rows = holders()
    except Exception as e:          # (diagnostics only)
        log.info("%smemory holders: %s: %s", prefix, type(e).__name__, e)
        return
    log.info("%smemory holders (%.1f s): %s", prefix, time.perf_counter() - t0,
             "; ".join(f"{mb:.0f} MB in {n} arrays: {c}" for mb, n, c in rows) or "none over the size floor")


class Reporter:
    """report() every HOLDERS_S from a trace loop (call tick() each round)."""

    def __init__(self, prefix=""):
        self.prefix = prefix
        self.last = time.monotonic()

    def tick(self):
        if not ON:
            return
        now = time.monotonic()
        if now - self.last >= HOLDERS_S:
            self.last = now
            report(self.prefix)
