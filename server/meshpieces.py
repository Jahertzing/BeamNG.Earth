"""Collision meshes in pieces the frame governor can spread (2026-10-29: a road tile's bridge deck or railway collider
registered in one 53-112 ms step on the game's main thread, the engine's collision set-up costing ~50 us a triangle,
and a building collision mesh took 1.3 s; a 350 KB far tile without collision loads in 3 ms). A mesh of more than
max_tris triangles is cut into runs of consecutive triangles of about equal size (the builders add geometry stretch
by stretch along the road, so a run is one stretch), each piece its own shape with only the vertices it uses, at most
max_pieces pieces (the last takes the rest). The mod (roads.lua) makes each piece a unit of its own: "deck",
"deck2", ... and the governor spreads them over frames."""
import os

import numpy as np


def split(tris, max_tris, max_pieces):
    """[((lo, hi), vertex ids (k,), tris of the piece renumbered (hi - lo, 3))] for triangles (m, 3): one piece over
    everything when m <= max_tris, else ceil(m / max_tris) pieces of about equal size, max_pieces at most."""
    T = np.asarray(tris, np.int64).reshape(-1, 3)
    m = len(T)
    if m == 0:
        return []
    n = max(1, min(int(max_pieces), -(-m // int(max_tris))))
    bounds = np.linspace(0, m, n + 1).round().astype(int)
    out = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        if hi <= lo:
            continue
        sub = T[lo:hi]
        ids, inv = np.unique(sub, return_inverse=True)
        out.append(((int(lo), int(hi)), ids, inv.reshape(sub.shape)))
    return out


def piece_path(path, i):
    """The i-th piece's file: path itself for 1, else <stem>_<i><ext> beside it (shape_files names them so)."""
    if i == 1:
        return path
    stem, ext = os.path.splitext(path)
    return f"{stem}_{i}{ext}"
