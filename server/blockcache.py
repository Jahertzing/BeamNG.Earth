"""Terrain blocks in the revisit cache (revisit.py): a block built in an anchor's place before is written out again
under this session's names instead of being built: the heights, grading, land cover, road paint and base photo were
5-21 s a block in Denver (2026-10-28), a read back is a few hundred ms.

Two items, kept apart because they are final at different times:
  block  the heightmap PNG, surface masks and .ter (and the info the mod gets): kept when the block is complete,
         nothing it read failed or was missing (revisit.faults), its land cover and roads all there (surfaceComplete,
         roadsComplete);
  base   detail mode: the base photo with its packed copy for the game's terrain texture cache: kept when rendered
         (write_base, in a block process or here) with nothing failing meanwhile, and again when rendered anew with
         its NAIP (Connection.base_refresh; the newer one replaces it). A fast player gets the block before its
         photo (_finish_heightmap's baseLater), and NAIP's host can stay down for hours (Server.base_stale): kept
         with the block, the photo never was (the first route bake kept no block at all, 2026-10-28).
A block read back without its photo has the photo rendered as when it is built; one read back with it is checked
like a fresh one (Server._base_kept: base_refresh renders it again once its NAIP is all here)."""
import logging
import os
import threading
import time

import revisit
import staging
import surface as surfacemod
import terraincache

log = logging.getLogger("earthd")

KIND = "block"
BASE_KIND = "base"
_NAMES = ("ter", "layers", "base")      # the info's file names (the session's: put back on a hit)
LOG_REASONS = 5                         # why an item was not kept: logged (INFO) this often per reason and process
_told = {}
_told_lock = threading.Lock()


def _not_kept(what, key, reason):
    with _told_lock:
        n = _told[reason] = _told.get(reason, 0) + 1
    (log.info if n <= LOG_REASONS else log.debug)("revisit: %s %s not kept: %s", what, key, reason)
    return False


def _write(path, data):
    tmp = staging.tmp_for(path)
    with open(tmp, "wb") as f:
        f.write(data)
    staging.replace(tmp, path)


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _dds_path(server, name):
    import earthd
    return os.path.join(terraincache.cache_dir(server.user_dir),
                        terraincache.base_key(f"{server.vfs_dir}/{name}", earthd.BASE_AO_VFS) + ".dds")


def store(server, anchor, cx, cy, k, info, f0):
    """Keep the block just built (info: make_heightmap's; its heights, masks and .ter) under key k when it is complete;
    f0: revisit.faults() before its build. Never raises; True when kept."""
    key = (anchor.id, cx, cy)
    if not revisit.ON or KIND in revisit.SKIP or info is None:
        return False
    if revisit.faults() != f0:
        return _not_kept("block", key, "something it read failed or was missing")
    if info.get("surfaceComplete") is False or info.get("roadsComplete") is False:
        return _not_kept("block", key, "built before its land cover or roads were here")
    if not info.get("ter"):
        return _not_kept("block", key, "no .ter")
    try:
        out = server.out_dir
        blobs = {"hm.png": _read(os.path.join(out, server.hm_name(key))),
                 "block.ter": _read(os.path.join(out, info["ter"]))}
        for i, name in enumerate(info.get("layers") or ()):
            blobs[f"mask{i}.png"] = _read(os.path.join(out, name))
        extra = {n: v for n, v in info.items() if n not in _NAMES and n not in ("seconds", "stages")}
        extra["nLayers"] = len(info.get("layers") or ())
        return revisit.store(server.cache_dir, KIND, k, blobs, extra)
    except Exception as e:          # (not kept rather than no block)
        return _not_kept("block", key, f"{type(e).__name__}: {e}")


def load(server, anchor, cx, cy, k):
    """The info of the block kept under key k (without its base photo: load_base), its files written under this
    session's names; None when there is none (or it cannot be written: then it is built)."""
    t0 = time.perf_counter()
    got = revisit.load(server.cache_dir, KIND, k)
    if got is None:
        return None
    blobs, extra = got
    key = (anchor.id, cx, cy)
    try:
        out = server.out_dir
        n = int(extra.get("nLayers") or 0)
        masks = surfacemod.mask_names(server.hm_name(key)[:-4]) if n else []
        if len(masks) != n:         # (another material list: built again)
            return None
        _write(os.path.join(out, server.hm_name(key)), blobs["hm.png"])
        ter = server.ter_name(key)
        _write(os.path.join(out, ter), blobs["block.ter"])
        for i, name in enumerate(masks):
            _write(os.path.join(out, name), blobs[f"mask{i}.png"])
        info = {n_: v for n_, v in extra.items() if n_ != "nLayers"}
        info["ter"] = ter
        if masks:
            info["layers"] = masks
        info["stages"] = {"revisit": time.perf_counter() - t0}
        info["zoom"] = anchor.hm_zoom
        return info
    except Exception as e:
        log.info("revisit: block %s not read back: %s: %s", key, type(e).__name__, e)
        return None


def store_base(server, anchor, cx, cy, k, name, f0):
    """Keep a base photo just rendered (name in the session folder, its packed copy in the game's cache) under key k;
    f0: revisit.faults() before its render. Never raises; True when kept."""
    key = (anchor.id, cx, cy)
    if not revisit.ON or BASE_KIND in revisit.SKIP or not name:
        return False
    if revisit.faults() != f0:
        return _not_kept("base photo", key, "something it read failed or was missing")
    try:
        blobs = {"base.png": _read(os.path.join(server.out_dir, name)), "base.dds": _read(_dds_path(server, name))}
        return revisit.store(server.cache_dir, BASE_KIND, k, blobs)
    except Exception as e:
        return _not_kept("base photo", key, f"{type(e).__name__}: {e}")


def load_base(server, anchor, cx, cy, k):
    """The base photo kept under key k written as the block's (Server.base_name) with its packed copy in the game's
    cache: its file name, or None."""
    got = revisit.load(server.cache_dir, BASE_KIND, k)
    if got is None:
        return None
    blobs = got[0]
    key = (anchor.id, cx, cy)
    try:
        name = server.base_name(key)
        _write(os.path.join(server.out_dir, name), blobs["base.png"])
        path = _dds_path(server, name)      # (after the photo: the cache entry must be the newer file)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write(path, blobs["base.dds"])
        return name
    except Exception as e:
        log.info("revisit: base photo %s not read back: %s: %s", key, type(e).__name__, e)
        return None
