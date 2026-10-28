"""The world folder's layout (cache.py "The world folder"): what tools/preload.py fetched, kept for good beside the
download cache, read before it (cache.resolve) and never evicted, capped or cleared.

  <world>/VERSION                 the layout version (VERSION)
  <world>/regions/<slug>.done     a pre-load's manifest: the keys it finished, one per line (tools/preload.py tok())
  <world>/regions/<slug>.json     what was pre-loaded there: the region's name, title, bounds, the last run's dates
  <world>/regions/coverage/       the pre-load's coverage probes of the regional imagery sources
  <world>/bake/                   revisit.py's baked routes (anchors.json, routes.json, their pinned items)
  <world>/dem/ imagery/ mapdata/ ...   the cache's own layout for the files it holds

Here: the folder's version, its regions, and which files a set of manifests says are pre-loaded (Keys, classify): the
moves of tools/world.py migrate and the per-region backups pick files by it."""
import json
import os
import time

VERSION = 1
VERSION_FILE = "VERSION"
REGIONS = "regions"
COVERAGE = os.path.join(REGIONS, "coverage")
BAKE = "bake"
DEM_SOURCE = "mapterhorn"           # dem.MAPTERHORN["name"]: the tiles the pre-load fetches


def ensure(world_dir):
    """The folder made, its VERSION written when missing. Returns its version (an int; another layout: not ours)."""
    os.makedirs(world_dir, exist_ok=True)
    path = os.path.join(world_dir, VERSION_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except FileNotFoundError:
        text = ""
    if not text:
        tmp = path + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"{VERSION}\n")
        os.replace(tmp, path)
        return VERSION
    try:
        return int(text.split()[0])
    except ValueError:
        return -1


def manifest_path(world_dir, slug):
    return os.path.join(world_dir, REGIONS, f"{slug}.done")


def meta_path(world_dir, slug):
    return os.path.join(world_dir, REGIONS, f"{slug}.json")


def read_tokens(path):
    try:
        with open(path, encoding="utf-8") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except OSError:
        return set()


def read_meta(world_dir, slug):
    try:
        with open(meta_path(world_dir, slug), encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_meta(world_dir, slug, **kw):
    """Merge kw into the region's <slug>.json (atomically)."""
    d = read_meta(world_dir, slug)
    d.update(kw)
    path = meta_path(world_dir, slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, path)


def regions(world_dir):
    """[{slug, path, tokens, kinds {kind: count}, mtime, meta}] of the world's manifests, by slug."""
    d = os.path.join(world_dir, REGIONS)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".done"))
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(d, n)
        toks = read_tokens(p)
        kinds = {}
        for t in toks:
            k = t.split(" ", 1)[0]
            kinds[k] = kinds.get(k, 0) + 1
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            mtime = None
        out.append({"slug": n[:-5], "path": p, "tokens": toks, "kinds": kinds, "mtime": mtime,
                    "meta": read_meta(world_dir, n[:-5])})
    return out


def summary(world_dir):
    """One line for the server's log: the folder and its regions (no walk of its files)."""
    if not world_dir:
        return "no world folder"
    if not os.path.isdir(world_dir):
        return f"world folder {world_dir} (empty: tools/preload.py fills it, tools/world.py migrate moves the cache's)"
    rs = regions(world_dir)
    last = max((r["mtime"] for r in rs if r["mtime"]), default=None)
    return (f"world folder {world_dir}: {len(rs)} pre-loaded region{'' if len(rs) == 1 else 's'}"
            + (f" ({', '.join(r['slug'] for r in rs)}; last {time.strftime('%Y-%m-%d', time.localtime(last))})"
               if rs else ""))


# --- which files are pre-loaded ----------------------------------------------------------------------------------------
class Keys:
    """The keys of a set of manifests' tokens, by kind: dem {(z, x, y)}, xyz {(source, z, x, y)}, exp {(z, x, y)} (an
    export parent's first child), cells {(z, x, y)} (the z14 cells and the skyline's z12 / z14 ones), edges."""

    def __init__(self, tokens=()):
        self.dem, self.xyz, self.exp, self.cells, self.edges = set(), set(), set(), set(), set()
        for t in tokens:
            self.add(t)

    def add(self, token):
        kind, _, rest = token.partition(" ")
        parts = rest.split("/")
        try:
            if kind == "dem":
                self.dem.add(tuple(int(v) for v in parts[:3]))
            elif kind == "xyz":
                self.xyz.add((parts[0],) + tuple(int(v) for v in parts[1:4]))
            elif kind == "exp":
                self.exp.add(tuple(int(v) for v in parts[:3]))
            elif kind in ("cell", "cellroads", "sky"):
                self.cells.add(tuple(int(v) for v in parts[:3]))
            elif kind == "edges":
                self.edges.add(tuple(int(v) for v in parts[:3]))
        except (ValueError, IndexError):
            pass

    def __len__(self):
        return len(self.dem) + len(self.xyz) + len(self.exp) + len(self.cells) + len(self.edges)


def _zxy(parts):
    """(z, x, y) of a path's last three parts ("z", "x", "y.ext"), or None."""
    if len(parts) < 3:
        return None
    try:
        return int(parts[-3]), int(parts[-2]), int(parts[-1].split(".", 1)[0])
    except ValueError:
        return None


def export_levels():
    """{imagery source name: its export levels} for the sources fetched as parents (imagery.SOURCES; never guessed:
    without them a migrate would leave the export photos behind)."""
    import imagery
    return {n: s.export for n, s in imagery.SOURCES.items() if s.export}


def classify(rel, keys, exports, raw=True):
    """The layer label ("dem/mapterhorn", "imagery/naip", "mapdata", "mapdata/raw", ...) of a cache-relative path
    ('/'-separated) when keys say it is pre-loaded data, else None. exports: export_levels(). raw: the map sources'
    shared range blocks (mapdata/raw/: not one cell's) count too."""
    parts = rel.split("/")
    top = parts[0]
    name = parts[-1]
    if name.endswith((".tmp", ".tmp.npz", ".lock")):
        return None
    if top == "dem" and len(parts) == 5 and parts[1] == DEM_SOURCE:
        k = _zxy(parts)
        return f"dem/{DEM_SOURCE}" if k in keys.dem else None
    if top == "imagery" and len(parts) == 5:
        src, k = parts[1], _zxy(parts)
        if k is None:
            return None
        if (src,) + k in keys.xyz:
            return f"imagery/{src}"
        e = exports.get(src)
        if e:
            m = (1 << e) - 1
            if (k[0], k[1] & ~m, k[2] & ~m) in keys.exp:
                return f"imagery/{src}"
        return None
    if top == "mapdata":
        if len(parts) > 1 and parts[1] == "raw":
            k = _zxy(parts) if name.endswith(".npz") else None
            if k is not None and (k in keys.cells or k in keys.edges):
                return "mapdata/raw/" + parts[2] if len(parts) > 2 else "mapdata/raw"
            if raw and len(parts) > 2:
                return "mapdata/raw/" + parts[2]
            return None
        k = _zxy(parts) if name.endswith(".npz") else None
        return "mapdata" if k is not None and k in keys.cells else None
    return None


def walk(root, skip_top=()):
    """(cache-relative path with '/', size, mtime) of every file under root (os.scandir), top-level names in skip_top
    left out."""
    stack = [(root, "")]
    while stack:
        d, rel = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for de in it:
                r = f"{rel}/{de.name}" if rel else de.name
                if not rel and de.name in skip_top:
                    continue
                try:
                    if de.is_dir(follow_symlinks=False):
                        stack.append((de.path, r))
                    elif de.is_file(follow_symlinks=False):
                        st = de.stat(follow_symlinks=False)
                        yield r, st.st_size, st.st_mtime
                except OSError:
                    continue


def layer_of(rel):
    """A world file's layer for the status's sizes: dem/<source>, imagery/<source>, mapdata/v<n>, mapdata/raw/<source>,
    bake, regions, else its top folder."""
    parts = rel.split("/")
    if len(parts) == 1:
        return "(files)"
    if parts[0] in ("dem", "imagery") or parts[0] == "mapdata" and parts[1] != "raw":
        return "/".join(parts[:2])
    if parts[0] == "mapdata" and len(parts) > 2:
        return "/".join(parts[:3])
    return parts[0]
