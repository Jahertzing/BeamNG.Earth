#!/usr/bin/env python3
"""The world folder: what tools/preload.py fetched, kept for good beside the download cache (server/cache.py "The world
folder", server/worldvol.py its layout). The server reads it before the cache and never evicts, caps or clears it.

  python3 tools/world.py status                     regions, size per layer and source, coverage, last pre-load, free disk
  python3 tools/world.py migrate                    what moving the cache's pre-loaded files over would do (dry run)
  python3 tools/world.py migrate --apply            move them (game and server closed)
  python3 tools/world.py backup D:/bnge-world       copy the folder (or --region NAME's files) to another drive
  python3 tools/world.py export D:/front-range.zip  the same as one zip file
  python3 tools/world.py import D:/bnge-world       copy a backup (folder or zip) in; what is there stays
  python3 tools/world.py verify                     spot-check files decode, manifests against the files

migrate moves from the cache: the pre-loads' manifests (<cache>/preload/*.done -> <world>/regions/) and coverage
probes, the baked route (<cache>/bake -> <world>/bake), and every file those manifests say was pre-loaded (the DEM
tiles, the imagery tiles and export parents, the map cells' layer files and their DRCOG / lidar / CDOT inputs, and,
unless --no-raw, the map sources' range blocks under mapdata/raw: shared between cells, needed to build them again
after an update). A file already in the world stays there (the cache's copy is left for its eviction). One drive:
os.replace, nothing copied; another drive: copied, then removed. Run it again to resume; a finished one finds nothing.
"""
import argparse
import json
import os
import random
import shutil
import socket
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.append(ROOT)
import cache as cachemod  # noqa: E402
import worldvol  # noqa: E402

SERVER_PORT = 47613             # earthd --port's default
GB = 1e9
DATA_TOPS = ("dem", "imagery", "mapdata")       # the cache's folders migrate looks through
SAMPLE = 200                    # verify: files decoded
TILE_KM = 40075.016686          # the equator's length: a z14 cell's side is TILE_KM / 2^14 * cos(lat)


def default_world_dir():
    try:
        import apppaths
        return apppaths.saved_world_dir() or apppaths.default_world_dir()
    except Exception:
        return os.path.join(os.path.expanduser("~"), "beamng-earth", "world")


def default_cache_dir():
    try:
        import apppaths
        return apppaths.saved_cache_dir() or apppaths.default_cache_dir()
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".cache", "beamng-earth")


def server_listening(port=SERVER_PORT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def servers_folders(a):
    """A world server listens and the folders are its own (the defaults: a copy elsewhere is fair game)."""
    def same(x, y):
        return os.path.normcase(os.path.abspath(x)) == os.path.normcase(os.path.abspath(y))
    return server_listening(a.port) and (same(a.world_dir, default_world_dir()) or same(a.cache_dir,
                                                                                      default_cache_dir()))


def gb(b):
    return f"{b / GB:.2f} GB"


def free_bytes(path):
    d = path
    while d and not os.path.isdir(d):
        nd = os.path.dirname(d)
        d = nd if nd != d else None
    try:
        return shutil.disk_usage(d or path).free
    except OSError:
        return None


def osp(root, rel):
    return os.path.join(root, *rel.split("/"))


def region_keys(world_dir, cache_dir=None, only=None):
    """worldvol.Keys of the world's manifests (and the cache's not moved yet), only: one region's slug."""
    toks = set()
    for d in [os.path.join(world_dir, worldvol.REGIONS)] + ([os.path.join(cache_dir, "preload")] if cache_dir else []):
        try:
            names = [n for n in os.listdir(d) if n.endswith(".done")]
        except OSError:
            continue
        for n in names:
            if only is None or n[:-5] == only:
                toks |= worldvol.read_tokens(os.path.join(d, n))
    return worldvol.Keys(toks)


# --- status ------------------------------------------------------------------------------------------------------------
def cells_km2(cells):
    """The area of a set of z14 cells (km2)."""
    import math
    total = 0.0
    for z, x, y in cells:
        if z != 14:
            continue
        n = math.pi - 2.0 * math.pi * (y + 0.5) / 2 ** z
        lat = math.degrees(math.atan(math.sinh(n)))
        side = TILE_KM / 2 ** z * math.cos(math.radians(lat))
        total += side * side
    return total


def cmd_status(a):
    w = a.world_dir
    print(f"World folder {w}" + ("" if os.path.isdir(w) else " (not there yet: tools/preload.py fills it, "
                                                             "tools/world.py migrate moves the cache's)"))
    if not os.path.isdir(w):
        return 0
    try:
        with open(os.path.join(w, worldvol.VERSION_FILE)) as f:
            print(f"  layout {f.read().strip()}")
    except OSError:
        print("  no VERSION file")
    rs = worldvol.regions(w)
    print(f"Regions ({len(rs)}):")
    for r in rs:
        m = r["meta"]
        k = worldvol.Keys(r["tokens"])
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"])) if r["mtime"] else "?"
        state = "" if not m else (" complete" if m.get("complete") else f" unfinished ({m.get('left', '?')} keys left)"
                                  if "complete" in m else "")
        print(f"  {r['slug']:32s} last {when}{state}{(': ' + m['title']) if m.get('title') else ''}")
        print(f"    {len(r['tokens'])} keys: " + ", ".join(f"{n} {c}" for n, c in sorted(r["kinds"].items())))
        zs = sorted({z for z, _, _ in k.dem})
        print(f"    coverage: map cells ~{cells_km2(k.cells):.0f} km2, DEM z{zs[0]}-z{zs[-1]}" if zs else
              f"    coverage: map cells ~{cells_km2(k.cells):.0f} km2")
    sizes, files, total, n = {}, {}, 0, 0
    for rel, size, _ in worldvol.walk(w):
        ly = worldvol.layer_of(rel)
        sizes[ly] = sizes.get(ly, 0) + size
        files[ly] = files.get(ly, 0) + 1
        total += size
        n += 1
    print("Size per layer / source:")
    for ly in sorted(sizes):
        print(f"  {ly:40s} {files[ly]:9d} files {gb(sizes[ly]):>11s}")
    print(f"  {'total':40s} {n:9d} files {gb(total):>11s}")
    last = max((r["mtime"] for r in rs if r["mtime"]), default=None)
    print("Last pre-load: " + (time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "none"))
    fb = free_bytes(w)
    print(f"Free on its drive: {gb(fb) if fb is not None else '?'}")
    return 0


# --- migrate -----------------------------------------------------------------------------------------------------------
def plan_migrate(world_dir, cache_dir, raw=True):
    """[(label, source path, target path, size)] of what migrate moves, and [(label, path)] of the files already in
    the world (left in the cache)."""
    moves, there = [], []

    def add(label, src, dst, size):
        if os.path.exists(dst):
            there.append((label, src))
        else:
            moves.append((label, src, dst, size))
    pre = os.path.join(cache_dir, "preload")
    for rel, size, _ in worldvol.walk(pre):
        if rel.endswith(".done") and "/" not in rel:
            continue                                # (the manifests: merged, below)
        if rel.startswith("coverage/") and rel.endswith(".json"):
            add("regions/coverage", osp(pre, rel), osp(world_dir, worldvol.COVERAGE.replace(os.sep, "/") + "/"
                                                       + rel.split("/", 1)[1]), size)
    for rel, size, _ in worldvol.walk(os.path.join(cache_dir, "bake")):
        if not rel.endswith(".tmp"):
            add("bake", osp(os.path.join(cache_dir, "bake"), rel), osp(os.path.join(world_dir, worldvol.BAKE), rel),
                size)
    keys = region_keys(world_dir, cache_dir)
    exports = worldvol.export_levels()
    for top in DATA_TOPS:
        for rel, size, _ in worldvol.walk(os.path.join(cache_dir, top)):
            rel = f"{top}/{rel}"
            label = worldvol.classify(rel, keys, exports, raw=raw)
            if label:
                add(label, osp(cache_dir, rel), osp(world_dir, rel), size)
    return moves, there, keys


def merge_manifests(world_dir, cache_dir, apply):
    """The cache's <cache>/preload/*.done into <world>/regions/ (a region in both: the union). [(slug, keys)]."""
    pre = os.path.join(cache_dir, "preload")
    try:
        names = sorted(n for n in os.listdir(pre) if n.endswith(".done"))
    except OSError:
        return []
    out = []
    for n in names:
        src = os.path.join(pre, n)
        toks = worldvol.read_tokens(src)
        dst = worldvol.manifest_path(world_dir, n[:-5])
        out.append((n[:-5], len(toks)))
        if not apply:
            continue
        have = worldvol.read_tokens(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not have:
            cachemod.move_file(src, dst)
            continue
        tmp = dst + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(t + "\n" for t in sorted(have | toks))
        os.replace(tmp, dst)
        os.remove(src)
    return out


def cmd_migrate(a):
    w, c = a.world_dir, a.cache_dir
    if cachemod.set_world(w, c, env=False) is None:
        print(f"The world folder {w} and the cache {c} overlap: pick a world folder outside the cache (--world-dir).",
              file=sys.stderr)
        return 2
    cachemod.set_world(None, None, env=False)
    if a.apply and servers_folders(a) and not a.allow_running:
        print(f"A world server is listening on 127.0.0.1:{a.port}: close the game and the server first.",
              file=sys.stderr)
        return 2
    if not os.path.isdir(c):
        print(f"No cache at {c}.", file=sys.stderr)
        return 2
    print(f"{'Moving' if a.apply else 'Dry run: would move'} the pre-loaded files of {c}\n  into {w}")
    mans = merge_manifests(w, c, apply=False)
    for slug, n in mans:
        print(f"  manifest {slug}: {n} keys -> {worldvol.manifest_path(w, slug)}")
    t0 = time.monotonic()
    moves, there, keys = plan_migrate(w, c, raw=not a.no_raw)
    print(f"  manifests' keys: dem {len(keys.dem)}, xyz {len(keys.xyz)}, export parents {len(keys.exp)}, cells "
          f"{len(keys.cells)}, edges {len(keys.edges)} (looked through the cache in {time.monotonic() - t0:.0f} s)")
    by = {}
    for label, _, _, size in moves:
        n, b = by.get(label, (0, 0))
        by[label] = (n + 1, b + size)
    for label in sorted(by):
        print(f"  {label:40s} {by[label][0]:9d} files {gb(by[label][1]):>11s}")
    total = sum(m[3] for m in moves)
    print(f"  {'total':40s} {len(moves):9d} files {gb(total):>11s}")
    if there:
        print(f"  {len(there)} files already in the world folder: left in the cache (its eviction takes them)")
    same = moves and os.path.splitdrive(os.path.abspath(w))[0].lower() == os.path.splitdrive(os.path.abspath(c))[0].lower()
    if moves and not same:
        fb = free_bytes(w)
        print(f"  another drive: copied, then removed ({gb(fb) if fb is not None else '?'} free there)")
        if fb is not None and total > fb:
            print("  ! does not fit", file=sys.stderr)
            return 2
    if not a.apply:
        print("Nothing moved (a dry run): --apply to move them, with the game and the server closed.")
        return 0
    if worldvol.ensure(w) != worldvol.VERSION:
        print(f"{w} holds another layout (its {worldvol.VERSION_FILE}): left alone.", file=sys.stderr)
        return 2
    merge_manifests(w, c, apply=True)
    moved = failed = bad = 0
    mb = 0
    t0 = time.monotonic()
    for i, (label, src, dst, size) in enumerate(moves):
        try:
            cachemod.move_file(src, dst)
        except FileNotFoundError:
            continue                                # (gone meanwhile)
        except OSError as e:
            failed += 1
            if failed <= 10:
                print(f"  ! {src}: {e}", file=sys.stderr)
            continue
        try:
            if os.path.getsize(dst) != size:
                bad += 1
        except OSError:
            bad += 1
        moved += 1
        mb += size
        if (i + 1) % 20000 == 0:
            print(f"  {i + 1}/{len(moves)} ({gb(mb)}, {time.monotonic() - t0:.0f} s)", flush=True)
    # what is left: a second look (the moves that failed, and nothing else)
    left, _, _ = plan_migrate(w, c, raw=not a.no_raw)
    ok = failed == 0 and bad == 0 and not left
    print(f"Moved {moved} files ({gb(mb)}) in {time.monotonic() - t0:.0f} s; {failed} failed, {bad} with another size "
          f"at the target; {len(left)} still to move" + (" - run it again" if left else ""))
    with open(os.path.join(w, worldvol.REGIONS, "migrate.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "cache": c, "moved": moved, "bytes": mb,
                            "failed": failed, "bad": bad, "left": len(left), "planned": len(moves)}) + "\n")
    return 0 if ok else 1


# --- backup / export / import ------------------------------------------------------------------------------------------
def selected(world_dir, region):
    """(world-relative path, size, mtime) of the files a backup takes: all, or one region's (its manifest, its
    description, and the files its manifest says it pre-loaded)."""
    if region is None:
        yield from worldvol.walk(world_dir)
        return
    if not os.path.exists(worldvol.manifest_path(world_dir, region)):
        raise SystemExit(f"no region {region!r} in {world_dir} (tools/world.py status)")
    keys = region_keys(world_dir, only=region)
    exports = worldvol.export_levels()
    for rel, size, mtime in worldvol.walk(world_dir):
        if rel in (worldvol.VERSION_FILE, f"{worldvol.REGIONS}/{region}.done", f"{worldvol.REGIONS}/{region}.json") \
                or worldvol.classify(rel, keys, exports, raw=False):
            yield rel, size, mtime


def copy_file(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + f".{os.getpid()}.tmp"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def cmd_backup(a):
    w, dest = a.world_dir, os.path.abspath(a.path)
    if os.path.normcase(dest).startswith(os.path.normcase(os.path.abspath(w)) + os.sep):
        print("The backup cannot go inside the world folder.", file=sys.stderr)
        return 2
    n = skipped = b = 0
    for rel, size, mtime in selected(w, a.region):
        dst = osp(dest, rel)
        try:
            st = os.stat(dst)
            if st.st_size == size and abs(st.st_mtime - mtime) < 2:
                skipped += 1
                continue
        except OSError:
            pass
        copy_file(osp(w, rel), dst)
        n += 1
        b += size
        if n % 20000 == 0:
            print(f"  {n} files ({gb(b)})", flush=True)
    print(f"Backed up {n} files ({gb(b)}) to {dest}; {skipped} already there")
    return 0


def cmd_export(a):
    w, dest = a.world_dir, os.path.abspath(a.path)
    if not dest.lower().endswith(".zip"):
        dest += ".zip"
    tmp = dest + ".part"
    n = b = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:     # (tiles are compressed already)
        for rel, size, _ in selected(w, a.region):
            z.write(osp(w, rel), rel)
            n += 1
            b += size
    os.replace(tmp, dest)
    print(f"Exported {n} files ({gb(b)}) to {dest}")
    return 0


def cmd_import(a):
    w, src = a.world_dir, os.path.abspath(a.path)
    if servers_folders(a) and not a.allow_running:
        print(f"A world server is listening on 127.0.0.1:{a.port}: close the game and the server first.",
              file=sys.stderr)
        return 2
    if worldvol.ensure(w) != worldvol.VERSION:
        print(f"{w} holds another layout (its {worldvol.VERSION_FILE}): left alone.", file=sys.stderr)
        return 2
    n = kept = b = 0
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            for info in z.infolist():
                rel = info.filename
                if info.is_dir() or rel.startswith(("/", "\\")) or ".." in rel.replace("\\", "/").split("/"):
                    continue
                dst = osp(w, rel)
                if rel == worldvol.VERSION_FILE or os.path.exists(dst) and not a.overwrite:
                    kept += 1
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                tmp = dst + f".{os.getpid()}.tmp"
                with z.open(info) as fi, open(tmp, "wb") as fo:
                    shutil.copyfileobj(fi, fo)
                os.replace(tmp, dst)
                n += 1
                b += info.file_size
    elif os.path.isdir(src):
        for rel, size, _ in worldvol.walk(src):
            dst = osp(w, rel)
            if rel == worldvol.VERSION_FILE or os.path.exists(dst) and not a.overwrite:
                kept += 1
                continue
            copy_file(osp(src, rel), dst)
            n += 1
            b += size
    else:
        print(f"{src}: neither a folder nor a zip file", file=sys.stderr)
        return 2
    print(f"Imported {n} files ({gb(b)}) into {w}; {kept} already there (kept{'' if a.overwrite else ': --overwrite'})")
    return 0


# --- verify ------------------------------------------------------------------------------------------------------------
def check_file(path):
    """None when the file decodes, else what is wrong."""
    try:
        if path.endswith(".404"):
            return None if os.path.getsize(path) == 0 else "a marker that is not empty"
        if path.endswith((".webp", ".jpg", ".png")):
            from PIL import Image
            with Image.open(path) as im:
                im.load()
            return None
        if path.endswith(".npz"):
            import numpy as np
            with np.load(path, allow_pickle=False) as z:
                for k in z.files:
                    z[k]
            return None
        if path.endswith(".json"):
            with open(path, encoding="utf-8") as f:
                json.load(f)
            return None
        if path.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                return None if z.testzip() is None else "a damaged member"
        return None if os.path.getsize(path) > 0 or path.endswith(("size", "404")) else "empty"
    except Exception as e:      # noqa: BLE001
        return f"{type(e).__name__}: {e}"[:160]


def cmd_verify(a):
    w, c = a.world_dir, a.cache_dir
    files = [(rel, size) for rel, size, _ in worldvol.walk(w)]
    print(f"{len(files)} files in {w}")
    rnd = random.Random(a.seed)
    sample = rnd.sample(files, min(a.sample, len(files)))
    bad = [(rel, err) for rel, _ in sample for err in [check_file(osp(w, rel))] if err]
    print(f"  decoded {len(sample)} files at random: {len(bad)} bad")
    for rel, err in bad[:20]:
        print(f"    ! {rel}: {err}")
    present = {rel for rel, _ in files}
    cells, edges = set(), set()         # (a cell with any layer file; DRCOG's edges)
    for rel in present:
        if rel.startswith("mapdata/") and rel.endswith(".npz"):
            parts = rel.split("/")
            k = worldvol._zxy(parts)
            if k is not None and parts[1] != "raw":
                cells.add(k)
            elif k is not None and len(parts) > 4 and parts[-4] == "edges":
                edges.add(k)
    exports = worldvol.export_levels()
    exp_srcs = sorted({rel.split("/")[1] for rel in present if rel.startswith("imagery/") and
                       rel.split("/")[1] in exports})
    missing_total = 0
    for r in worldvol.regions(w):
        counts = {}
        for t in r["tokens"]:
            kind, _, rest = t.partition(" ")
            p = rest.split("/")
            try:
                if kind == "dem":
                    base = f"dem/{worldvol.DEM_SOURCE}/{p[0]}/{p[1]}/{p[2]}"
                    rels = [base + ".webp", base + ".404"]
                elif kind == "xyz":
                    base = f"imagery/{p[0]}/{p[1]}/{p[2]}/{p[3]}"
                    rels = [base + ".jpg", base + ".404"]
                elif kind == "exp":
                    z, x, y = int(p[0]), int(p[1]), int(p[2])
                    rels = [f"imagery/{s}/{z}/{x | m}/{(y | m)}{e}" for s in exp_srcs
                            for m in [(1 << exports[s]) - 1] for e in (".jpg", ".404")]
                elif kind in ("cell", "cellroads", "sky"):
                    rels = None
                    hit = (int(p[0]), int(p[1]), int(p[2])) in cells
                elif kind == "edges":
                    rels = None
                    hit = (int(p[0]), int(p[1]), int(p[2])) in edges
                else:
                    continue
            except (ValueError, IndexError, OSError):
                continue
            if rels is not None:
                hit = any(x in present for x in rels)
                where = "world" if hit else ("cache" if c and any(os.path.exists(osp(c, x)) for x in rels) else "none")
            else:
                where = "world" if hit else "none"
            n = counts.setdefault(kind, {"world": 0, "cache": 0, "none": 0})
            n[where] += 1
        print(f"  region {r['slug']}:")
        for kind, n in sorted(counts.items()):
            missing_total += n["cache"]
            print(f"    {kind:10s} {sum(n.values()):8d} keys: {n['world']} in the world, "
                  f"{n['cache']} only in the cache (migrate), {n['none']} without a file (known missing upstream, "
                  f"or not built yet)")
    print("OK" if not bad and not missing_total else
          f"{len(bad)} bad files, {missing_total} pre-loaded keys still in the cache")
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("command", choices=("status", "migrate", "backup", "export", "import", "verify"))
    ap.add_argument("path", nargs="?", help="backup / import: a folder (import: or a zip); export: the zip file")
    ap.add_argument("--world-dir", default=None, help="the world folder (default: the server's)")
    ap.add_argument("--cache-dir", default=None, help="the download cache (default: the server's)")
    ap.add_argument("--apply", action="store_true", help="migrate: move the files (default: a dry run)")
    ap.add_argument("--no-raw", action="store_true",
                    help="migrate: leave the map sources' shared range blocks (mapdata/raw) in the cache")
    ap.add_argument("--region", default=None, help="backup / export: only this region's files (status lists them)")
    ap.add_argument("--overwrite", action="store_true", help="import: replace files the world has")
    ap.add_argument("--sample", type=int, default=SAMPLE, help="verify: how many files to decode")
    ap.add_argument("--seed", type=int, default=None, help="verify: the sample's seed")
    ap.add_argument("--port", type=int, default=SERVER_PORT, help="the world server's port (migrate / import refuse "
                                                                  "while it listens)")
    ap.add_argument("--allow-running", action="store_true", help="migrate / import with the server running")
    a = ap.parse_args(argv)
    a.world_dir = os.path.abspath(a.world_dir or default_world_dir())
    a.cache_dir = os.path.abspath(a.cache_dir or default_cache_dir())
    if a.command in ("backup", "export", "import") and not a.path:
        ap.error(f"{a.command} needs a PATH")
    return {"status": cmd_status, "migrate": cmd_migrate, "backup": cmd_backup, "export": cmd_export,
            "import": cmd_import, "verify": cmd_verify}[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
