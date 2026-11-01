#!/usr/bin/env python3
"""Traffic signals for the Unreal world: a mast arm and heads for every signalled approach of roadnet.json, US style: the
pole on the far right corner, the arm over the lanes, one head a lane with its yellow edged backplate, facing the drivers who
come in, the cross street's name on the arm and a tall luminaire on the pole, as nearly every signal here has. The meshes go per
cell into signal_<i>_<j>.glb (drawn) and signalcol_<i>_<j>.glb (the poles' collision); signals.json has every lens's place for
whoever lights them (the traffic sim knows the phases), and the luminaires' for ue_furniture.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_signals.py --net <roadnet dir> --export <export dir> [--manifest]

signals.json: {"heads": [{"junction", "link", "node", "lens": {"red": [x, y, z], "yellow": ..., "green": ...}, "facing": [x, y, 0],
"radius"}], "lamps": [[x, y, z, yaw]]}, frame metres. --manifest adds the meshes to the export's manifest.json as sign and signcol.
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402

import guidesigns  # noqa: E402
import ue_furniture  # noqa: E402
import ue_roadnet_out  # noqa: E402

CELL = 2048.0
POLE_R = 0.17                   # m: the mast pole's radius at its foot
POLE_TOP_R = 0.12
ARM_R = 0.09
ARM_Z = 6.1                     # m over the road, at the pole
ARM_RISE = 0.5                  # m the arm rises to its tip
ARM_MAX_M = 18.0
HEAD_W, HEAD_H = 0.34, 1.02     # the three-lens head (12 inch lenses)
PLATE_W, PLATE_H = 0.86, 1.42   # its backplate
HEAD_DEPTH = 0.22
HEAD_BOTTOM_Z = 5.1             # m over the road
LENS_R = 0.15
KERB_CLEAR_M = 1.6              # m from the outer lane's edge to the pole
FAR_DEFAULT_M = (12.0, 48.0)    # m from the stop line to the far side, at least and at most
SLIDE_M = (0.0, 1.5, -1.5, 3.0, -3.0, 4.5, 6.0, 8.0, 10.0, 12.0)    # m along the road the pole may stand off its place
KERB_SEARCH_M = 16.0            # m past the lanes' side to look for the curb
POLE_CLEAR_M = 0.7              # m off the pavement
MATERIALS = ["bne_gsign_steel", "bne_gsign_textk", "bne_signal_red", "bne_signal_yellow", "bne_signal_green", "bne_gsign_amber",
             "bne_gsign_panel", "bne_gsign_letter"]
M_AMBER, M_PANEL, M_LETTER = 5, 6, 7
PLATE_EDGE_M = 0.05             # m of retroreflective yellow round a backplate
POLE_TOP_M = 10.5               # m over the road: the pole, carrying its luminaire
DAVIT_M = 3.2                   # m the luminaire reaches out over the road
DAVIT_RISE_M = 0.5
NAME_CAP_M = 0.2                # m: the mast arm name sign's capitals (MUTCD D3-1, 8 in.)
NAME_MAX_M = 3.6
NAME_FROM_POLE_M = 1.2          # m from the pole to the sign's near end


class Mesh:
    """Boxes, tubes and discs in frame metres."""

    def __init__(self):
        self.P, self.T, self.M = [], [], []
        self.n = 0

    def add(self, P, T, m):
        self.P.append(np.asarray(P, float))
        self.T.append(np.asarray(T, np.int64) + self.n)
        self.M.append(np.full(len(T), m))
        self.n += len(P)

    def tube(self, a, b, ra, rb, m, sides=10):
        a, b = np.asarray(a, float), np.asarray(b, float)
        w = b - a
        w /= np.linalg.norm(w)
        u = np.cross(w, (0.0, 0.0, 1.0) if abs(w[2]) < 0.9 else (1.0, 0.0, 0.0))
        u /= np.linalg.norm(u)
        v = np.cross(w, u)
        ang = np.arange(sides) * 2 * math.pi / sides
        ring = np.cos(ang)[:, None] * u + np.sin(ang)[:, None] * v
        P = np.r_[a + ring * ra, b + ring * rb, [b]]
        T = []
        for k in range(sides):
            j = (k + 1) % sides
            T += [(k, j, sides + k), (j, sides + j, sides + k), (sides + k, sides + j, 2 * sides)]
        self.add(P, T, m)

    def box(self, c, x, y, z, m):
        """A box at c with half axes x, y, z (3-vectors)."""
        c, x, y, z = (np.asarray(v, float) for v in (c, x, y, z))
        P = [c + a * x + b * y + d * z for a in (-1, 1) for b in (-1, 1) for d in (-1, 1)]
        T = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
        self.add(P, T, m)

    def disc(self, c, nrm, r, m, sides=14):
        c, nrm = np.asarray(c, float), np.asarray(nrm, float)
        u = np.array([-nrm[1], nrm[0], 0.0])
        u /= np.linalg.norm(u)
        v = np.cross(nrm, u)
        ang = np.arange(sides) * 2 * math.pi / sides
        P = np.r_[[c], c + r * (np.cos(ang)[:, None] * u + np.sin(ang)[:, None] * v)]
        T = [(0, k + 1, (k + 1) % sides + 1) for k in range(sides)]
        self.add(P, T, m)

    def result(self):
        return np.concatenate(self.P), np.concatenate(self.T), np.concatenate(self.M)


def end_dir(points, reach=15.0):
    p = np.array(points)[:, :2]
    d = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p[::-1], axis=0).T))]
    q = p[::-1]
    a = np.array([np.interp(min(reach, d[-1]), d, q[:, 0]), np.interp(min(reach, d[-1]), d, q[:, 1])])
    u = q[0] - a
    return u / max(np.linalg.norm(u), 1e-6)


def name_sign(m, c, right, up, nrm, name):
    """A green street name sign centred at c on the arm, facing nrm, its text running along right."""
    w = guidesigns.text_width(name, NAME_CAP_M)
    W, H = w + 0.3, NAME_CAP_M * 2.0
    m.box(c, right * W / 2, -nrm * 0.015, up * H / 2, M_PANEL)
    org = c + nrm * 0.018 - right * w / 2 - up * NAME_CAP_M / 2
    for pts, tris in guidesigns.text_tris(name, NAME_CAP_M, 0.0, 0.0):
        q = np.array(pts, float)
        m.add(org + right * q[:, :1] + up * q[:, 1:], np.array(tris, np.int64), M_LETTER)


def cross_name(links, group, name, t):
    """The name of the road that crosses one running along t, the longest spelled."""
    got = [lk.get("name") or "" for k in group for lk in links.get(k, ()) if abs(float(end_dir(lk["points"]) @ t)) < 0.6]
    got = sorted({v.split(";")[0] for v in got if v and v != name}, key=len)
    return got[-1] if got else None


def corner(world, F, t, n, kerb):
    """(F, kerb) with the pole's foot on free ground: the far right corner as the drawn pavement has it, moved along the road
    and out past the curb as little as will do; None when no ground is near. The arm stays square to the lanes."""
    best, score = None, 1e9
    for da in SLIDE_M:
        G = F + t * da
        extra = np.arange(0.0, KERB_SEARCH_M, 0.5)
        Q = G + n[None] * (kerb - extra)[:, None]
        sd, fl = world.at(Q)
        free = np.isfinite(sd) & (sd >= POLE_CLEAR_M) & ((fl & (ue_furniture.F_BLD | ue_furniture.F_DECK)) == 0)
        if not free.any():
            continue
        k = int(np.argmax(free))
        cost = abs(da) * 0.7 + float(extra[k]) + (1.0 if fl[k] & ue_furniture.F_WALK else 0.0)
        if cost < score:
            best, score = (G, kerb - float(extra[k])), cost
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--export", default="C:/beamng-earth/Earth.UE/export/frederick-r4")
    ap.add_argument("--out", default=None)
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    out_dir = a.out or a.export
    with open(os.path.join(a.net, "roadnet.json")) as f:
        net = json.load(f)
    links = net["links"]
    heights = ue_roadnet_out.Heights(a.export)
    cells = sorted((int(f.split("_")[1]), int(f.split("_")[2].split(".")[0])) for f in os.listdir(a.export) if f.startswith("ground_") and f.endswith(".glb"))
    world = ue_furniture.World(a.export, cells, a.jobs)
    by_from = {}
    for lk in links:
        by_from.setdefault(lk["from"], []).append(lk)
    node_junction = {k: v.get("junction") for k, v in net["nodes"].items()}
    junctions = {j["id"]: j for j in net["junctions"]}
    # (a slip lane past the corner has no head of its own: a ramp counts only where a through road stops too)
    main_ends = {lk["to"] for lk in links if lk["control"] == "signal" and not lk["ramp"]}
    mesh, col, heads = {}, {}, []
    lamps = []
    made = 0
    for lk in links:
        if lk["control"] != "signal" or lk["level"] != 0:
            continue
        pts = np.array(lk["points"])
        L = np.hypot(*np.diff(pts[:, :2], axis=0).T).sum()
        if L < 6.0 or (lk["ramp"] and lk["to"] not in main_ends):
            continue
        E = pts[-1, :2]
        t = end_dir(lk["points"])
        n = np.array([-t[1], t[0]])
        jid = node_junction.get(lk["to"])
        far = None
        group = junctions[jid]["nodes"] if jid in junctions else [lk["to"]]
        for k in group:
            for o in by_from.get(k, ()):
                if o["to"] in group or o["level"] != 0:
                    continue
                q = np.array(o["points"])
                u = q[min(len(q) - 1, 4), :2] - q[0, :2]
                u /= max(np.linalg.norm(u), 1e-6)
                if float(u @ t) > 0.8:
                    s = float((q[0, :2] - E) @ t)
                    far = max(far or 0.0, s)
        if far is None:
            wide = [float(o["width"]) for k in group for o in by_from.get(k, ()) if abs(float(end_dir(o["points"]) @ t)) < 0.6]
            far = max(wide or [FAR_DEFAULT_M[0]]) + 6.0
        far = float(np.clip(far + 1.5, *FAR_DEFAULT_M))
        F = E + t * far
        offs = sorted(float(v) for v in lk["lanes"]) or [0.0]
        lw = float(lk["lane_width"] or 3.6)
        kerb = offs[0] - lw / 2 - KERB_CLEAR_M
        got = corner(world, F, t, n, kerb)
        if got is None:
            continue
        F, kerb = got
        P = F + n * kerb
        z0 = float(heights.ground(P[None])[0])
        zr = float(heights.ground(F[None])[0])
        if not np.isfinite(z0) or not np.isfinite(zr):
            continue
        tip = offs[-1] + lw * 0.5 + 0.4
        if tip - kerb > ARM_MAX_M:
            tip = kerb + ARM_MAX_M
        cell = (int(P[0] // CELL), int(P[1] // CELL))
        m = mesh.setdefault(cell, Mesh())
        c = col.setdefault(cell, Mesh())
        top = zr + POLE_TOP_M
        m.tube((P[0], P[1], z0 - 0.3), (P[0], P[1], top), POLE_R, POLE_TOP_R, 0)
        c.box((P[0], P[1], (z0 + top) / 2), (POLE_R, 0, 0), (0, POLE_R, 0), (0, 0, (top - z0) / 2 + 0.3), 0)
        A0 = np.r_[P, zr + ARM_Z]
        A1 = np.r_[F + n * tip, zr + ARM_Z + ARM_RISE]
        m.tube(A0, A1, ARM_R, ARM_R * 0.55, 0)
        L0 = np.r_[P, top - 0.2]
        L1 = np.r_[P + n * DAVIT_M, top + DAVIT_RISE_M]
        m.tube(L0, L1, 0.05, 0.04, 0)
        m.box(L1 + np.r_[n * 0.3, -0.03], np.r_[n * 0.36, 0.0], np.r_[t * 0.16, 0.0], np.array([0.0, 0.0, 0.07]), 0)
        lamps.append([round(float(v), 2) for v in (*(L1[:2] + n * 0.3), L1[2] - 0.12)] + [round(math.degrees(math.atan2(n[1], n[0])) % 360.0, 1)])
        # one head a lane, at least two
        at = offs[:] if len(offs) >= 2 else [offs[0] - lw * 0.25, offs[0] + lw * 0.25]
        at = [v for v in at if v <= tip][-4:]
        nrm = np.r_[-t, 0.0]            # (the lenses face the drivers)
        nv = np.r_[n, 0.0]
        up = np.array([0.0, 0.0, 1.0])
        for k, off in enumerate(at):
            H = F + n * off
            s = (off - kerb) / max(tip - kerb, 1e-6)
            arm_z = zr + ARM_Z + ARM_RISE * s
            bottom = min(zr + HEAD_BOTTOM_Z, arm_z - HEAD_H - 0.12)
            mid = np.r_[H, bottom + HEAD_H / 2] + nrm * 0.12
            m.tube(np.r_[H, bottom + HEAD_H], np.r_[H, arm_z], 0.03, 0.03, 0)
            m.box(mid - nrm * HEAD_DEPTH / 2, nv * HEAD_W / 2, -nrm * HEAD_DEPTH / 2, up * HEAD_H / 2, 1)
            m.box(mid - nrm * (HEAD_DEPTH + 0.015), nv * PLATE_W / 2, -nrm * 0.012, up * PLATE_H / 2, 1)
            m.box(mid - nrm * (HEAD_DEPTH + 0.03), nv * (PLATE_W / 2 + PLATE_EDGE_M), -nrm * 0.008, up * (PLATE_H / 2 + PLATE_EDGE_M), M_AMBER)
            lens = {}
            for j, name in enumerate(("red", "yellow", "green")):
                q = mid + nrm * 0.006 + up * ((1 - j) * HEAD_H / 3.1)
                m.disc(q, nrm, LENS_R, 2 + j)
                lens[name] = [round(float(v), 3) for v in q + nrm * 0.01]
            heads.append({"junction": jid, "link": lk["id"], "node": lk["to"], "lens": lens,
                          "facing": [round(float(v), 4) for v in nrm], "radius": LENS_R})
        name = cross_name(by_from, group, lk.get("name") or "", t)
        if name and guidesigns.text_width(name, NAME_CAP_M) + 0.3 < NAME_MAX_M:
            W = guidesigns.text_width(name, NAME_CAP_M) + 0.3
            first = min(at) if at else tip
            x = kerb + NAME_FROM_POLE_M + W / 2
            if x + W / 2 < first - PLATE_W / 2:
                sz = (x - kerb) / max(tip - kerb, 1e-6)
                name_sign(m, np.r_[F + n * x, zr + ARM_Z + ARM_RISE * sz - 0.02] + nrm * 0.12, -nv, up, nrm, name)
        made += 1
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    for cell, m in mesh.items():
        P, T, M = m.result()
        origin = np.array([cell[0] * CELL, cell[1] * CELL, 0.0])
        for kind, name, (PP, TT, MM), names in (("sign", f"signal_{cell[0]}_{cell[1]}", (P, T, M), MATERIALS),
                                                ("signcol", f"signalcol_{cell[0]}_{cell[1]}", col[cell].result(), ["bne_gsign_steel"])):
            g = ue_export.Glb()
            g.add(PP - origin, TT, np.zeros((len(PP), 2)), names, MM, np.full((len(PP), 4), 255, np.uint8))
            g.write(os.path.join(out_dir, name + ".glb"), name, (0.0, 0.0, 0.0))
            entries.append({"file": name + ".glb", "kind": kind, "collision_only": kind == "signcol", "origin": origin.tolist(),
                            "tris": int(len(TT)), "verts": int(len(PP)), "materials": names})
    with open(os.path.join(out_dir, "signals.json"), "w") as f:
        json.dump({"frame": net["frame"], "heads": heads, "lamps": lamps}, f, separators=(",", ":"))
    if a.manifest:
        path = os.path.join(out_dir, "manifest.json")
        with open(path) as f:
            doc = json.load(f)
        doc["meshes"] = [e for e in doc["meshes"] if not e["file"].startswith("signal")] + entries
        with open(path, "w") as f:
            json.dump(doc, f, indent=1)
    print(json.dumps({"approaches": made, "heads": len(heads), "cells": len(mesh), "junctions": len({h["junction"] for h in heads}),
                      "tris": sum(e["tris"] for e in entries)}))


if __name__ == "__main__":
    main()
