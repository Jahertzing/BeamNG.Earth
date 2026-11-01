#!/usr/bin/env python3
"""Signs stood on the finished pavement: the builders put a sign's post from the road's spec, and a junction's real curb
return runs wider than that, so posts came out on the asphalt. Every post (the sign collision's small boxes) on the
pavement is moved, with what hangs on it, to the nearest free ground, at that ground's height. Gantries stay.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_settle.py --dir <export dir> [--out <dir>]

Rewrites sign_<i>_<j>.glb and signcol_<i>_<j>.glb, the same meshes moved. Run after the pavement pass, once: it moves what it
finds, so a rerun works from the builders' own (--src <export>-builder, tools/ue_chain.sh keeps them there).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402
from scipy.sparse import coo_matrix  # noqa: E402
from scipy.sparse.csgraph import connected_components  # noqa: E402

import ue_furniture  # noqa: E402
import ue_survey  # noqa: E402

CELL = 2048.0
CLEAR_M = 0.5                   # m a post's foot stays off the pavement
POST_MAX_M = 1.5                # m: a box wider than this is no post (a gantry's leg foot, a wall)
PART_NEAR_M = 0.4               # m from a post to a part's box that hangs on it
PART_MAX_M = 6.0                # m: a bigger part is no post's
TOUCH_M = 0.08                  # m: parts this close hang together
SIGN_MAX_M = 5.0                # m: what hangs on a post is no wider than this


def read(path):
    """(P, UV, C, T, M, names): one vertex array and the triangles with their material, as the file's primitives share them."""
    prims = ue_survey.read_glb(path)
    names = [p["name"] for p in prims]
    T = np.concatenate([p["T"] for p in prims])
    M = np.concatenate([np.full(len(p["T"]), k, np.int64) for k, p in enumerate(prims)])
    return prims[0]["P"], prims[0]["UV"], prims[0]["C"], T, M, names


def write(path, name, P, UV, C, T, M, names):
    g = ue_export.Glb()
    g.add(P, T, UV, names, M, C)
    g.write(path, name, (0.0, 0.0, 0.0))


def parts_of(P, T):
    """Part label per vertex: triangles joined, corners that meet counted as one vertex."""
    _, inv = np.unique(np.round(P, 2), axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    t = inv[T]
    n = int(inv.max()) + 1
    i, j = np.r_[t[:, 0], t[:, 1]], np.r_[t[:, 1], t[:, 2]]
    _, lab = connected_components(coo_matrix((np.ones(len(i), bool), (i, j)), shape=(n, n)), directed=False)
    return lab[inv]


def boxes(P, lab):
    """(label, x0, y0, x1, y1, z0, z1) of each part."""
    order = np.argsort(lab)
    sl, sp = lab[order], P[order]
    cuts = np.flatnonzero(np.diff(sl)) + 1
    out = []
    for a, b in zip(np.r_[0, cuts], np.r_[cuts, len(sl)]):
        q = sp[a:b]
        out.append((int(sl[a]), *q[:, :2].min(0), *q[:, :2].max(0), q[:, 2].min(), q[:, 2].max()))
    return out


def hanging(B, seeds):
    """The parts of a post's sign: seeds, and every small part touching one of them, in 3D, and so on. A blade's letters are parts
    of their own, so those past the post's own box stayed behind when only the post's neighbours moved ("Ri" in a house wall)."""
    small = (B[:, 2] - B[:, 0] < PART_MAX_M) & (B[:, 3] - B[:, 1] < PART_MAX_M)
    done = set(int(k) for k in seeds)
    todo = list(done)
    box = np.array([B[todo, 0].min(), B[todo, 1].min(), B[todo, 2].max(), B[todo, 3].max()]) if todo else None
    while todo:
        a = B[todo.pop()]
        hit = small & (B[:, 0] - TOUCH_M <= a[2]) & (B[:, 2] + TOUCH_M >= a[0]) & (B[:, 1] - TOUCH_M <= a[3]) & (B[:, 3] + TOUCH_M >= a[1]) & \
            (B[:, 4] - TOUCH_M <= a[5]) & (B[:, 5] + TOUCH_M >= a[4])
        for k in np.flatnonzero(hit):
            k = int(k)
            if k in done:
                continue
            grown = np.array([min(box[0], B[k, 0]), min(box[1], B[k, 1]), max(box[2], B[k, 2]), max(box[3], B[k, 3])])
            if max(grown[2] - grown[0], grown[3] - grown[1]) > SIGN_MAX_M:
                continue        # (the walk has reached the next sign, or a gantry)
            box = grown
            done.add(k)
            todo.append(k)
    return sorted(done)


def settle_cell(w, export, out_dir, i, j):
    """export: where the unsettled meshes are."""
    col_path = os.path.join(export, f"signcol_{i}_{j}.glb")
    sign_path = os.path.join(export, f"sign_{i}_{j}.glb")
    if not os.path.exists(col_path) or not os.path.exists(sign_path):
        return 0, 0
    org = np.array([i * CELL, j * CELL, 0.0])
    cP, cUV, cC, cT, cM, cN = read(col_path)
    if not len(cT):
        return 0, 0
    sP, sUV, sC, sT, sM, sN = read(sign_path)
    clab = parts_of(cP, cT)
    slab = parts_of(sP, sT)
    sbox = boxes(sP, slab)
    sB = np.array([b[1:5] for b in sbox])
    sB3 = np.array([b[1:7] for b in sbox])
    moved, stuck = 0, 0
    shifted = set()
    cP, sP = cP.copy(), sP.copy()
    for lab, x0, y0, x1, y1, z0, z1 in boxes(cP, clab):
        if max(x1 - x0, y1 - y0) > POST_MAX_M:
            continue
        foot = np.array([(x0 + x1) / 2, (y0 + y1) / 2]) + org[:2]
        sd, fl = w.at(foot[None])
        if not np.isfinite(sd[0]) or sd[0] >= CLEAR_M or fl[0] & ue_furniture.F_DECK:
            continue
        to = ue_furniture.push_off(w, foot, CLEAR_M, walk_ok=False)
        if to is None:
            to = ue_furniture.push_off(w, foot, CLEAR_M, walk_ok=True)
        if to is None:
            stuck += 1
            continue
        z = w.h.ground(np.array([foot, to]))
        dz = float(z[1] - z[0]) if np.isfinite(z).all() else 0.0
        d = np.array([to[0] - foot[0], to[1] - foot[1], dz])
        cP[clab == lab] += d
        f = foot - org[:2]
        near = (sB[:, 0] - PART_NEAR_M <= f[0]) & (sB[:, 2] + PART_NEAR_M >= f[0]) & (sB[:, 1] - PART_NEAR_M <= f[1]) & (sB[:, 3] + PART_NEAR_M >= f[1]) & \
            (sB[:, 2] - sB[:, 0] < PART_MAX_M) & (sB[:, 3] - sB[:, 1] < PART_MAX_M)
        # (each part once: two posts of one sign, or posts that touch, move as the first one does, the second's box with them)
        for k in hanging(sB3, np.flatnonzero(near)):
            if k not in shifted:
                shifted.add(k)
                sP[slab == sbox[k][0]] += d
        moved += 1
    if moved:
        write(os.path.join(out_dir, f"sign_{i}_{j}.glb"), f"sign_{i}_{j}", sP, sUV, sC, sT, sM, sN)
        write(os.path.join(out_dir, f"signcol_{i}_{j}.glb"), f"signcol_{i}_{j}", cP, cUV, cC, cT, cM, cN)
    return moved, stuck


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick-r3")
    ap.add_argument("--out", default=None)
    ap.add_argument("--src", default=None, help="where the unsettled sign meshes are (the builders' own), if not --dir")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    out_dir = a.out or a.dir
    os.makedirs(out_dir, exist_ok=True)
    cells = sorted((int(f.split("_")[1]), int(f.split("_")[2].split(".")[0])) for f in os.listdir(a.dir) if f.startswith("ground_") and f.endswith(".glb"))
    w = ue_furniture.World(a.dir, cells, a.jobs)
    total = [0, 0]
    for i, j in cells:
        m, s = settle_cell(w, a.src or a.dir, out_dir, i, j)
        total[0] += m
        total[1] += s
    print(json.dumps({"posts_moved": total[0], "stuck": total[1]}))


if __name__ == "__main__":
    main()
