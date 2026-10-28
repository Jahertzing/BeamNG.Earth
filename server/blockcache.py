"""Terrain blocks in the revisit cache (revisit.py): a block built in an anchor's place before (its heightmap PNG,
surface masks, .ter and, in detail mode, its base photo with the photo's packed copy for the game's terrain texture
cache) is written out again under this session's names instead of being built: the heights, grading, land cover,
road paint and base photo were 5-21 s a block in Denver (2026-10-28), a read back is a few hundred ms.

Kept only when the block is complete: nothing it read failed or was missing (revisit.faults), its land cover and
roads were all there (surfaceComplete, roadsComplete) and, in detail mode, its base photo came with it, final (not
sent later to a fast player, not waiting on lazy imagery: Server.base_stale)."""
import logging
import os
import time

import revisit
import staging
import surface as surfacemod
import terraincache

log = logging.getLogger("earthd")

KIND = "block"
_NAMES = ("ter", "layers", "base")      # the info's file names (the session's: put back on a hit)


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
    """Keep the block just built (info: make_heightmap's) under key k when it is complete; f0: revisit.faults()
    before its build. Never raises; True when kept."""
    if not revisit.ON or KIND in revisit.SKIP or info is None or revisit.faults() != f0:
        return False
    if info.get("surfaceComplete") is False or info.get("roadsComplete") is False or not info.get("ter"):
        return False
    key = (anchor.id, cx, cy)
    detail = server.near_visual == "detail" and server.imagery is not None
    if detail and (not info.get("base") or info["base"] != server.base_name(key) or key in server.base_stale
                   or info.get("stages", {}).get("baseLater")):
        return False
    try:
        out = server.out_dir
        blobs = {"hm.png": _read(os.path.join(out, server.hm_name(key))),
                 "block.ter": _read(os.path.join(out, info["ter"]))}
        for i, name in enumerate(info.get("layers") or ()):
            blobs[f"mask{i}.png"] = _read(os.path.join(out, name))
        if detail:
            blobs["base.png"] = _read(os.path.join(out, info["base"]))
            blobs["base.dds"] = _read(_dds_path(server, info["base"]))
        extra = {n: v for n, v in info.items() if n not in _NAMES and n not in ("seconds", "stages")}
        extra["nLayers"] = len(info.get("layers") or ())
        extra["hasBase"] = bool(detail)
        return revisit.store(server.cache_dir, KIND, k, blobs, extra)
    except Exception as e:          # (not kept rather than no block)
        log.debug("revisit: block %s not kept: %s: %s", key, type(e).__name__, e)
        return False


def load(server, anchor, cx, cy, k):
    """The info of the block kept under key k, its files written under this session's names; None when there is none
    (or it cannot be written: then it is built)."""
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
        info = {n_: v for n_, v in extra.items() if n_ not in ("nLayers", "hasBase")}
        info["ter"] = ter
        if masks:
            info["layers"] = masks
        if extra.get("hasBase"):
            base = server.base_name(key)
            _write(os.path.join(out, base), blobs["base.png"])
            path = _dds_path(server, base)     # (after the photo: the cache entry must be the newer file)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _write(path, blobs["base.dds"])
            info["base"] = base
            server.base_pending[key] = frozenset()
            server.base_stale.discard(key)
        info["stages"] = {"revisit": time.perf_counter() - t0}
        info["zoom"] = anchor.hm_zoom
        return info
    except Exception as e:
        log.info("revisit: block %s not read back: %s: %s", key, type(e).__name__, e)
        return None
