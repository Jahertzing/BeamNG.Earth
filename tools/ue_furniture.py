#!/usr/bin/env python3
"""Street lamps, wooden poles and their wires for the Unreal world, placed last, on the finished pavement, sidewalks,
buildings and ground of an export: what stands beside a road is measured from the road as it is drawn, not from the map's
ribbon, so nothing stands in a lane whatever moved the curbs.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_furniture.py --dir <export dir> [--net <roadnet dir>] --lamps <lamps.csv> [--apply]

What stands where:
- OpenStreetMap's mapped street lamps and power poles first, as mapped, only pushed off the pavement (the map's points are
  a few metres out; 13% of the lamps fall on the drawn road) and their arms turned to the road.
- Mapped poles a span apart are joined into lines, with wires.
- Lamps where the map has none only where they are as good as sure: signals, a through road's built-up junctions, the old
  towns' corners, a ramp's ends. Nothing along a block and nothing on a subdivision's own streets: a lamp that isn't there reads worse than one
  missing (the owner's street has none; rule-made lamps on residential streets had no evidence behind them).
- Poles where the map has none: country roads, one side, a rural span; the old towns' alleys; a built-up street only with
  mapped poles along it: the power in the area's subdivisions is buried.
- Lattice towers and conductors along the mapped high voltage lines (tower_<i>_<j>.glb, towercol_<i>_<j>.glb).

--apply writes wire_<i>_<j>.glb over the builders' and puts them in the export's manifest. lamps.csv is EarthSky's: kind, x, y,
z, yaw, scale (0 lamp post, 1 wooden pole, 2 lamp head, 3 the arm of a lamp on a pole).
"""
import argparse
import collections
import concurrent.futures
import hashlib
import json
import math
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

import furniture  # noqa: E402
import ue_furniture_lint as lint  # noqa: E402
import ue_roadnet_out  # noqa: E402

CELL = lint.CELL
PX = lint.PX
MARGIN_M = lint.MARGIN_M
SD_Q = 8.0                      # the stored distance's steps a metre
F_WALK, F_DECK, F_BLD = 1, 2, 4
POST, POLE, HEAD, ARM = 0, 1, 2, 3
OLD_TOWNS = ((-400.0, 1050.0, 450.0, 2000.0), (-400.0, -450.0, 500.0, 350.0), (-400.0, -2050.0, 100.0, -1300.0),
             (-10100.0, -6100.0, -9250.0, -5100.0))     # frame m: Firestone, Frederick, Dacono, Erie: the alley grids of the aerial
ALLEY_W_M = 7.0
ALLEY_MIN_M = 50.0
MAJOR = {"primary", "secondary", "tertiary", "trunk"}
LAMP_SCALE_LOW = 0.85           # 25 ft posts, the arterials' 30+ ft
MOTORWAY_LAMP_SCALE = furniture.HIGHWAY_LAMP_H["na"] / furniture.LAMP_H[furniture.G_LAMP]
STREETS = MAJOR | {"motorway", "residential", "unclassified", "living_street"}
POLE_CLASSES = {"secondary", "tertiary", "unclassified", "residential"}
POLE_SHARE = 0.6                # share of the country's roads with a line along them
RURAL_SPAN_M = 85.0             # m (the area's mapped lines: 250-300 ft)
TOWN_SPAN_M = 48.0
MAPPED_SPAN_M = 130.0           # m: mapped poles this close can be one line
POLE_NEAR_M = 22.0              # m from a road a mapped pole counts as along it
EVIDENCE_POLES = 2              # mapped poles along a built-up street that say it has overhead power
SLIDE_M = (0.0, 3.0, -3.0, 6.0, -6.0, 10.0, -10.0)
LAMP_OFF_M = (0.75, 1.1, 1.5, 2.0, 2.4, 2.9, 3.4, 4.0, 4.8)     # m from the curb to a lamp's foot: first free of the walk
POLE_OFF_M = (4.5, 5.5, 3.5, 6.5, 2.5, 7.5)
LAMP_CLEAR_M = 0.6              # m the foot stays off the pavement
POLE_CLEAR_M = 1.0
PUSH_MAX_M = 25.0               # m a mapped point may be pushed
RAY_MAX_M = 40.0
TURN_MAX_DEG = 35.0             # a road goes on through a junction within this turn
furniture.WIRE_SEG_M = 10.0
furniture.WIRE_VERTS_MAX = 400000


def h01(*vals):
    return int.from_bytes(hashlib.blake2b(repr(vals).encode(), digest_size=8).digest(), "little") / 2.0 ** 64


def yaw_of(v):
    return math.degrees(math.atan2(v[1], v[0])) % 360.0


class World:
    """The export's pavement distance and flags at any point, from the cells' rasters."""

    def __init__(self, export, cells, jobs):
        self.cells = {}
        with concurrent.futures.ProcessPoolExecutor(jobs) as pool:
            for k, sd, fl in pool.map(raster_cell, [(export, i, j) for i, j in cells]):
                self.cells[k] = (sd, fl)
        self.n = next(iter(self.cells.values()))[0].shape[0]
        self.h = ue_roadnet_out.Heights(export)

    def at(self, xy):
        """(distance to the pavement m, inside it negative; inf off the export), flags at (n, 2)."""
        xy = np.asarray(xy, float).reshape(-1, 2)
        sd, fl = np.full(len(xy), np.inf), np.zeros(len(xy), np.uint8)
        c = np.floor(xy / CELL).astype(np.int64)
        for k in {tuple(v) for v in c.tolist()}:
            got = self.cells.get(k)
            if got is None:
                continue
            a = np.flatnonzero((c == k).all(1))
            q = ((xy[a] - (k[0] * CELL - MARGIN_M, k[1] * CELL - MARGIN_M)) / PX).astype(np.int64)
            q = np.clip(q, 0, self.n - 1)
            sd[a] = got[0][q[:, 1], q[:, 0]] / SD_Q
            fl[a] = got[1][q[:, 1], q[:, 0]]
        return sd, fl

    def away(self, p):
        """The unit vector up the distance at p (away from the pavement), by a ring of samples."""
        ang = np.arange(16) * math.pi / 8
        v = np.c_[np.cos(ang), np.sin(ang)]
        for r in (1.0, 2.5, 5.0):
            s, _ = self.at(p + v * r)
            s = np.where(np.isfinite(s), s, -1e3)
            g = (v * s[:, None]).sum(0)
            if np.linalg.norm(g) > 1e-3:
                return g / np.linalg.norm(g)
        return np.array([1.0, 0.0])


def raster_cell(job):
    export, i, j = job
    pave, deck, walk, bld, _ = lint.cell_masks(export, i, j)
    sd = ndimage.distance_transform_edt(~pave) * PX - ndimage.distance_transform_edt(pave) * PX
    # (a building's mesh is its collision box: a metre round it is kept clear too)
    bld = ndimage.binary_dilation(bld, iterations=2)
    fl = (walk * F_WALK + deck * F_DECK + bld * F_BLD).astype(np.uint8)
    return (i, j), np.clip(sd * SD_Q, -32000, 32000).astype(np.int16), fl


def mapped_infra(path):
    """{"street_lamp": (n, 2), "power_pole": (n, 2), "power_tower": (n, 2)}, read once from the map's infrastructure layer."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    import ue_roadnet
    cells, frame, urban, widths, keys, md = ue_roadnet.load_features(os.path.abspath(os.environ["BNGE_WORLD_CACHE"]), (-10240, -10240, 10240, 10240))
    want = {"street_lamp": [], "power_pole": [], "power_tower": []}
    lines = []
    seen = set()
    for key in keys:
        lay = md.cell(key, layers=("infrastructure",), kind="demand").layers.get("infrastructure")
        if lay is None or not len(lay):
            continue
        cls = lay.attrs.get("class")
        for i in range(len(lay)):
            fid, c = str(lay.ids[i]), str(cls[i])
            if (c not in want and c != "power_line") or fid in seen:
                continue
            seen.add(fid)
            for part in lay.parts(i):
                for ring in part:
                    e, n, _ = frame.geodetic_to_enu(ring[:, 1], ring[:, 0], 0.0)
                    if c == "power_line":
                        lines.append(np.c_[e, n])
                    else:
                        want[c].append(np.c_[e, n].mean(0))
    out = {k: np.array(v).reshape(-1, 2) for k, v in want.items()}
    out["power_line"] = lines
    with open(path, "wb") as f:
        pickle.dump(out, f)
    return out


def chains_of(links):
    """Roads through their junctions: [{"xy": (n, 2), "cls": per point, "w": per point, "oneway", "name", "ramp"}]. A road goes on
    at a junction along the leg nearest straight ahead, a one-way only head to tail."""
    segs, seen = [], set()
    for lk in links:
        if lk["level"] != 0 or lk["class"] not in STREETS:
            continue
        key = (lk["from"], lk["to"]) if lk["oneway"] else tuple(sorted((lk["from"], lk["to"])))
        if key in seen:
            continue
        seen.add(key)
        p = np.array(lk["points"])[:, :2]
        if len(p) >= 2 and float(np.hypot(*np.diff(p, axis=0).T).sum()) > 1.0:
            segs.append({"a": lk["from"], "b": lk["to"], "xy": p, "cls": lk["class"], "w": float(lk["width"]), "oneway": lk["oneway"],
                         "name": lk.get("name") or "", "ramp": bool(lk["ramp"]), "used": False})
    at = collections.defaultdict(list)
    for s in segs:
        at[s["a"]].append(s)
        at[s["b"]].append(s)

    def leaving(s, node):
        p = s["xy"] if s["a"] == node else s["xy"][::-1]
        n = min(len(p) - 1, 3)
        u = p[n] - p[0]
        return u / max(np.linalg.norm(u), 1e-6)

    def next_seg(cur, node, u):
        best, score = None, math.cos(math.radians(TURN_MAX_DEG))
        for o in at[node]:
            if o is cur or o["used"] or o["ramp"] != cur["ramp"] or o["oneway"] != cur["oneway"]:
                continue
            if o["oneway"] and o["a"] != node:
                continue
            c = float(leaving(o, node) @ u)
            if o["name"] == cur["name"] and o["name"]:
                c += 0.3
            if c > score:
                best, score = o, c
        return best
    chains = []
    # (ends first, so a loop-free road is walked once from one end)
    order = sorted(segs, key=lambda s: min(len(at[s["a"]]), len(at[s["b"]])))
    for s0 in order:
        if s0["used"]:
            continue
        s0["used"] = True
        parts = [(s0, False)]
        for fwd in (True, False) if not s0["oneway"] else (True,):
            cur, node, flip = s0, s0["b"] if fwd else s0["a"], False
            while True:
                p = cur["xy"] if (cur["b"] == node) else cur["xy"][::-1]
                u = p[-1] - p[max(0, len(p) - 4)]
                u /= max(np.linalg.norm(u), 1e-6)
                o = next_seg(cur, node, u)
                if o is None:
                    break
                o["used"] = True
                other = o["b"] if o["a"] == node else o["a"]
                back = o["a"] != node
                if fwd:
                    parts.append((o, back))
                else:
                    parts.insert(0, (o, not back))
                cur, node = o, other
        xy, cls, w = [], [], []
        for s, back in parts:
            p = s["xy"][::-1] if back else s["xy"]
            if xy:
                p = p[1:]
            xy.append(p)
            cls += [s["cls"]] * len(p)
            w += [s["w"]] * len(p)
        chains.append({"xy": np.concatenate(xy), "cls": cls, "w": np.array(w), "oneway": s0["oneway"], "ramp": s0["ramp"], "name": s0["name"],
                       "nodes": [s0["a"]]})
    return chains, at


def stations(xy, spacing, phase):
    """(arc m, points, unit tangents) every spacing m along xy."""
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    at = np.arange(phase, s[-1], spacing)
    if not len(at):
        return at, np.zeros((0, 2)), np.zeros((0, 2))
    P = np.c_[np.interp(at, s, xy[:, 0]), np.interp(at, s, xy[:, 1])]
    Q = np.c_[np.interp(at + 1.0, s, xy[:, 0]), np.interp(at + 1.0, s, xy[:, 1])] - np.c_[np.interp(at - 1.0, s, xy[:, 0]), np.interp(at - 1.0, s, xy[:, 1])]
    return at, P, Q / np.maximum(np.linalg.norm(Q, axis=1, keepdims=True), 1e-6)


def curb_of(w, p, n, half):
    """m from the centre point p along n to where the pavement ends, the spec's half width where the line is off it."""
    t = np.arange(0.0, RAY_MAX_M, 0.25)
    sd, _ = w.at(p + n * t[:, None])
    if sd[0] > 0:
        return half
    out = np.flatnonzero(sd > 0)
    return float(t[out[0]]) if len(out) else None


def foot_ok(w, q, u, clear, walk_ok=False):
    """Is q free: off the pavement, a building and a deck, and on no driveway a step either way along the road (u)?"""
    pts = np.array([q, q + u * 1.5, q - u * 1.5])
    sd, fl = w.at(pts)
    if not np.isfinite(sd[0]) or sd[0] < clear or sd[1:].min() < 0.3:
        return False
    if fl[0] & (F_BLD | F_DECK):
        return False
    return walk_ok or not fl[0] & F_WALK


def spot(w, p, u, n, half, offs, clear, slide=SLIDE_M):
    """A free foot beside the road at p: curb, then out along offs; slid along the road when there is none. (foot, away unit)."""
    for da in slide:
        c = p + u * da
        e = curb_of(w, c, n, half)
        if e is None:
            continue
        for off in offs:
            q = c + n * (e + off)
            if foot_ok(w, q, u, clear):
                return q, n
    return None


class Places:
    """The lamps and poles placed so far."""

    def __init__(self):
        self.posts, self.poles, self.pole_lamps = [], [], []
        self.lamp_xy = []
        self.tree = None

    def near(self, q, r):
        """Any lamp within r of q."""
        if self.lamp_xy:
            a = np.array(self.lamp_xy)
            return float(np.hypot(*(a - q).T).min()) < r
        return False

    def post(self, q, away, scale):
        self.posts.append((float(q[0]), float(q[1]), yaw_of(-away), scale))
        self.lamp_xy.append((float(q[0]), float(q[1])))

    def pole_lamp(self, q, away):
        self.pole_lamps.append((float(q[0]), float(q[1]), yaw_of(away)))
        self.lamp_xy.append((float(q[0]), float(q[1])))


def push_off(w, p, clear, walk_ok=True):
    """p, moved to the nearest free ground with no road over it, or None."""
    sd, fl = w.at(p[None])
    if not np.isfinite(sd[0]):
        return None
    if sd[0] >= clear and not fl[0] & (F_BLD | F_DECK):
        return p
    ang = np.arange(32) * math.pi / 16
    v = np.c_[np.cos(ang), np.sin(ang)]
    for r in np.arange(0.5, PUSH_MAX_M, 0.5):
        q = p + v * r
        s, f = w.at(q)
        ok = np.isfinite(s) & (s >= clear) & ((f & (F_BLD | F_DECK)) == 0)
        if not walk_ok:
            ok &= (f & F_WALK) == 0
        if ok.any():
            return q[np.argmax(ok)]
    return None


def mapped_lamps(w, pts, places, motor):
    moved = dropped = 0
    for p in pts:
        q = push_off(w, p, furniture.LAMP_CURB_M)
        if q is None:
            dropped += 1
            continue
        moved += int(np.hypot(*(q - p)) > 0.01)
        scale = MOTORWAY_LAMP_SCALE if motor.query(q)[0] < 30.0 else 1.0
        places.post(q, w.away(q), scale)
    return moved, dropped


def mapped_lines(w, pts):
    """Mapped poles joined into lines: [[(x, y, away (2,))...]]: the nearest neighbours a span apart, two to a pole, no sharp
    turn; a pole with no neighbour stands alone."""
    q = []
    for p in pts:
        r = push_off(w, p, POLE_CLEAR_M)
        if r is not None:
            q.append(r)
    if not q:
        return []
    q = np.array(q)
    tree = cKDTree(q)
    pairs = sorted(tree.query_pairs(MAPPED_SPAN_M), key=lambda ab: np.hypot(*(q[ab[0]] - q[ab[1]])))
    adj = collections.defaultdict(list)
    comp = list(range(len(q)))

    def find(a):
        while comp[a] != a:
            comp[a] = comp[comp[a]]
            a = comp[a]
        return a
    for a, b in pairs:
        if len(adj[a]) >= 2 or len(adj[b]) >= 2 or find(a) == find(b):
            continue
        bad = False
        for m, o in ((a, b), (b, a)):
            for c in adj[m]:
                u, v = q[m] - q[c], q[o] - q[m]
                if float(u @ v) / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-6) < 0.5:
                    bad = True
        if bad:
            continue
        adj[a].append(b)
        adj[b].append(a)
        comp[find(a)] = find(b)
    lines, seen = [], set()
    for a in range(len(q)):
        if a in seen or len(adj[a]) == 2:
            continue
        run, prev, cur = [], None, a
        while cur is not None:
            seen.add(cur)
            run.append(q[cur])
            nxt = [c for c in adj[cur] if c != prev]
            prev, cur = cur, (nxt[0] if nxt else None)
        lines.append(run)
    return lines


def run_rows(run):
    """[(x, y, yaw)] of a line of poles, their cross-arms across it."""
    out = []
    p = np.array(run)
    for k in range(len(p)):
        a, b = p[max(k - 1, 0)], p[min(k + 1, len(p) - 1)]
        d = b - a
        if np.linalg.norm(d) < 1e-6:
            d = np.array([1.0, 0.0])
        out.append((float(p[k, 0]), float(p[k, 1]), yaw_of((d[1], -d[0]))))
    return out


def place_roads(w, chains, urban_tree, urban_of, poles_mapped, places, runs):
    """The rule-made poles and their lamps."""
    pole_tree = cKDTree(poles_mapped) if len(poles_mapped) else None
    stats = collections.Counter()
    for ch in chains:
        xy, name = ch["xy"], ch["name"] or f"{xy[0, 0]:.0f},{xy[0, 1]:.0f}"
        mid = len(xy) // 2
        cls = ch["cls"][mid]
        L = float(np.hypot(*np.diff(xy, axis=0).T).sum())
        if ch["oneway"] or cls not in POLE_CLASSES or ch["ramp"] or L < 60.0:
            continue
        urban = urban_of[urban_tree.query(xy[mid])[1]]
        s, P, U = stations(xy, 20.0, 5.0)
        evidence = 0
        side_vote = 0.0
        if pole_tree is not None and len(P):
            got = pole_tree.query_ball_point(P, POLE_NEAR_M)
            ids = {i for g in got for i in g}
            evidence = len(ids)
            for k, g in enumerate(got):
                for i in g:
                    side_vote += float((poles_mapped[i] - P[k]) @ np.array([-U[k, 1], U[k, 0]]))
        if evidence >= 3:
            stats["road_on_mapped_line"] += 1
            continue            # (the mapped line itself stands along it)
        if urban and evidence < EVIDENCE_POLES:
            continue            # buried
        if not urban and h01(name, cls, "line") > POLE_SHARE:
            continue
        span = TOWN_SPAN_M if urban else RURAL_SPAN_M
        side = (1 if side_vote > 0 else -1) if side_vote else (1 if h01(name, "pside") < 0.5 else -1)
        at, P, U = stations(xy, span, h01(name, "pphase") * span)
        run = []

        def flush():
            nonlocal run
            if len(run) > 1 or (run and not urban):
                runs.append(run)
            run = []
        last = None
        for k, (a, p, u) in enumerate(zip(at, P, U)):
            n = np.array([-u[1], u[0]]) * side
            half = float(np.interp(a, np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))], ch["w"])) / 2
            got = spot(w, p, u, n, half, POLE_OFF_M if not urban else (1.2, 1.8, 2.4, 3.0, 3.6), POLE_CLEAR_M)
            if got is None:
                flush()
                last = None
                continue
            q, away = got
            if last is not None and np.hypot(*(q - last)) > span * 1.6:
                flush()
            run.append(q)
            last = q
            if urban and furniture.pole_lamp(cls, k, "na:sparse") and not places.near(q, 40.0):
                places.pole_lamp(q, away)
                stats["pole_lamp"] += 1
        flush()

    return stats


def place_alleys(w, links, runs):
    """The old towns' power runs down their alleys: a line of poles along each."""
    seen = set()
    n_put = 0
    for lk in links:
        key = tuple(sorted((lk["from"], lk["to"])))
        if key in seen or lk["class"] != "service" or lk["level"] != 0 or float(lk["width"]) > ALLEY_W_M:
            continue
        seen.add(key)
        p = np.array(lk["points"])[:, :2]
        L = float(np.hypot(*np.diff(p, axis=0).T).sum())
        m = p.mean(0)
        if L < ALLEY_MIN_M or np.hypot(*(p[-1] - p[0])) < 0.97 * L:
            continue
        if not any(x0 <= m[0] < x1 and y0 <= m[1] < y1 for x0, y0, x1, y1 in OLD_TOWNS):
            continue
        side = 1 if h01(round(float(m[1]) / 200.0), round(float(m[0]) / 200.0), "alley") < 0.5 else -1
        at, P, U = stations(p, TOWN_SPAN_M, 6.0)
        run = []
        for q, u in zip(P, U):
            got = spot(w, q, u, np.array([-u[1], u[0]]) * side, float(lk["width"]) / 2, (0.6, 1.0, 1.5, 2.0), 0.4, (0.0, 2.0, -2.0, 4.0, -4.0))
            if got is not None:
                run.append(got[0])
        if len(run) > 1:
            runs.append(run)
            n_put += len(run)
    return n_put


TOWER_H = 28.0                  # m: the area's 230 kV lattice towers
TOWER_BASE_M = 4.0              # half the base's side
TOWER_TOP_M = 0.7
TOWER_ARMS = ((19.0, 6.5), (23.0, 5.5), (27.0, 4.5))    # (height, half length) of the cross-arms
TOWER_PANELS = 6
CONDUCTOR_DROP_M = 2.2          # m the conductors hang under the arms
CONDUCTOR_SAG = 0.03            # of a span
CONDUCTOR_R = 0.03
TOWER_LINE_SHARE = 0.5          # of a line's supports towers: the line is a tower line


def tower_mesh(m, c, yaw, z0):
    """A lattice tower at c, its arms along yaw, into ue_signals.Mesh m."""
    ca, sa = math.cos(yaw), math.sin(yaw)
    ex, ey = np.array([ca, sa, 0.0]), np.array([-sa, ca, 0.0])

    def corner(z, sx, sy):
        h = TOWER_BASE_M + (TOWER_TOP_M - TOWER_BASE_M) * (max(z, 0.0) / TOWER_H) ** 0.7
        return np.array([c[0], c[1], z0 + z]) + ex * sx * h + ey * sy * h
    zs = np.linspace(0.0, TOWER_H, TOWER_PANELS + 1)
    legs = ((1, 1), (-1, 1), (-1, -1), (1, -1))
    for k, (sx, sy) in enumerate(legs):
        nx, ny = legs[(k + 1) % 4]
        m.tube(corner(-0.3, sx, sy), corner(TOWER_H, sx, sy), 0.14, 0.07, 0, sides=4)
        for a, b in zip(zs[:-1], zs[1:]):
            m.tube(corner(a, sx, sy), corner(b, nx, ny), 0.05, 0.05, 0, sides=3)
            m.tube(corner(a, nx, ny), corner(b, sx, sy), 0.05, 0.05, 0, sides=3)
    for z, half in TOWER_ARMS:
        o = np.array([c[0], c[1], z0 + z])
        m.tube(o - ex * half, o + ex * half, 0.1, 0.1, 0, sides=4)


def tower_lines(w, lines, towers, poles, export):
    """The high voltage lines: lattice towers at the supports of a mapped line that the map calls towers, conductors between.
    Writes tower_<i>_<j>.glb and towercol_<i>_<j>.glb; returns their manifest entries and the conductors."""
    import ue_signals
    tt, pt = cKDTree(towers), cKDTree(poles)
    built = {}
    spans = []
    for ln in lines:
        nt = int((tt.query(ln)[0] < 1.0).sum())
        np_ = int((pt.query(ln)[0] < 1.0).sum())
        if nt == 0 or nt < TOWER_LINE_SHARE * (nt + np_):
            continue
        tops = []
        for k, p in enumerate(ln):
            a, b = ln[max(k - 1, 0)], ln[min(k + 1, len(ln) - 1)]
            d = b - a
            yaw = math.atan2(d[0], -d[1])
            q = push_off(w, p, 4.0)
            if q is None:
                tops.append(None)
                continue
            key = (round(float(q[0])), round(float(q[1])))
            if key not in built:
                built[key] = (q, yaw)
            tops.append((q, yaw))
        for a, b in zip(tops[:-1], tops[1:]):
            if a is not None and b is not None and np.hypot(*(a[0] - b[0])) > 5.0:
                spans.append((a, b))
    mesh, col = {}, {}
    zs = {}
    for key, (q, yaw) in built.items():
        z0 = float(w.h.ground(q[None])[0])
        if not np.isfinite(z0):
            continue
        zs[key] = z0
        cell = (int(q[0] // CELL), int(q[1] // CELL))
        tower_mesh(mesh.setdefault(cell, ue_signals.Mesh()), q, yaw, z0)
        h = TOWER_BASE_M * 0.9
        col.setdefault(cell, ue_signals.Mesh()).box((q[0], q[1], z0 + 2.0), (h, 0, 0), (0, h, 0), (0, 0, 2.5), 0)
    entries = []
    for f in os.listdir(export):
        if f.startswith("tower") and f.endswith(".glb"):
            os.remove(os.path.join(export, f))
    for cell, m in mesh.items():
        origin = np.array([cell[0] * CELL, cell[1] * CELL, 0.0])
        for kind, name, mm in (("sign", f"tower_{cell[0]}_{cell[1]}", m), ("signcol", f"towercol_{cell[0]}_{cell[1]}", col[cell])):
            P, T, M = mm.result()
            g = ue_export.Glb()
            g.add(P - origin, T, np.zeros((len(P), 2)), ["bne_gsign_steel"], M, np.full((len(P), 4), 255, np.uint8))
            g.write(os.path.join(export, name + ".glb"), name, (0.0, 0.0, 0.0))
            entries.append({"file": name + ".glb", "kind": kind, "collision_only": kind == "signcol", "origin": origin.tolist(), "tris": int(len(T)),
                            "verts": int(len(P)), "materials": ["bne_gsign_steel"]})
    wires = []
    for (qa, ya), (qb, yb) in spans:
        za, zb = zs.get((round(float(qa[0])), round(float(qa[1])))), zs.get((round(float(qb[0])), round(float(qb[1]))))
        if za is None or zb is None:
            continue
        span = float(np.hypot(*(qa - qb)))
        t = np.linspace(0.0, 1.0, max(3, int(span / 30.0)) + 1)
        for z, half in TOWER_ARMS:
            for sd in (1, -1):
                pa = np.array([qa[0] + math.cos(ya) * sd * half, qa[1] + math.sin(ya) * sd * half, za + z - CONDUCTOR_DROP_M])
                pb = np.array([qb[0] + math.cos(yb) * sd * half, qb[1] + math.sin(yb) * sd * half, zb + z - CONDUCTOR_DROP_M])
                p = pa[None] + (pb - pa)[None] * t[:, None]
                p[:, 2] -= 4.0 * CONDUCTOR_SAG * span * t * (1.0 - t)
                wires.append((p, CONDUCTOR_R))
    return entries, wires, len(built)


def place_junctions(w, links, at, urban_tree, urban_of, places):
    """Where no lamp is mapped: one at a built-up junction with a through road on it (a subdivision's entrance, an arterial's
    crossing), two, diagonally, at a signal."""
    done = collections.Counter()
    seen = set()
    for lk in links:
        for node in (lk["to"],):
            if node in seen:
                continue
            seen.add(node)
            legs = [s for s in at.get(node, ())]
            if len(legs) < 3:
                continue
            c = next(s["xy"][0] if s["a"] == node else s["xy"][-1] for s in legs)
            if not urban_of[urban_tree.query(c)[1]]:
                continue
            ang = []
            for s in legs:
                p = s["xy"] if s["a"] == node else s["xy"][::-1]
                u = p[min(3, len(p) - 1)] - p[0]
                ang.append(math.atan2(u[1], u[0]))
            ang = sorted(ang)
            gaps = [(ang[(k + 1) % len(ang)] - ang[k]) % (2 * math.pi) for k in range(len(ang))]
            corners = [ang[k] + gaps[k] / 2 for k in range(len(ang)) if math.radians(40) < gaps[k]]
            old = any(x0 <= c[0] < x1 and y0 <= c[1] < y1 for x0, y0, x1, y1 in OLD_TOWNS)
            if not old and not any(s["cls"] in MAJOR for s in legs):
                continue        # (a subdivision's own streets: dark but for the lamps the map has)
            major = sum(s["cls"] in MAJOR for s in legs) >= 3
            want = 2 if lk["control"] == "signal" else 1
            r = 30.0
            if places.near(c, r):
                continue
            corners.sort(key=lambda a: h01(round(float(c[0])), round(float(c[1])), round(a, 1)))
            for a in corners[:want + 1]:
                d = np.array([math.cos(a), math.sin(a)])
                e = curb_of(w, c, d, 6.0)
                if e is None:
                    continue
                tang = np.array([-d[1], d[0]])
                for off in LAMP_OFF_M:
                    q = c + d * (e + off)
                    if foot_ok(w, q, tang, LAMP_CLEAR_M) and not places.near(q, 8.0):
                        places.post(q, d, 1.0 if major else LAMP_SCALE_LOW)
                        done["lamp_corner"] += 1
                        want -= 1
                        break
                if want <= 0:
                    break
    return done


def place_ramps(w, chains, places):
    """Partial interchange lighting: a tall post at each end of a ramp, outside it."""
    n_put = 0
    for ch in chains:
        if not ch["ramp"]:
            continue
        xy = ch["xy"]
        arc = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
        L = arc[-1]
        ends = [L * 0.5] if L < 2 * furniture.PARTIAL_END_M + 30.0 else [furniture.PARTIAL_END_M + 15.0, L - furniture.PARTIAL_END_M - 15.0]
        for a in ends:
            p = np.array([np.interp(a, arc, xy[:, 0]), np.interp(a, arc, xy[:, 1])])
            q = np.array([np.interp(a + 1, arc, xy[:, 0]), np.interp(a + 1, arc, xy[:, 1])]) - np.array([np.interp(a - 1, arc, xy[:, 0]), np.interp(a - 1, arc, xy[:, 1])])
            u = q / max(np.linalg.norm(q), 1e-6)
            n = np.array([u[1], -u[0]])          # (the right of travel)
            if places.near(p, 40.0):
                continue
            got = spot(w, p, u, n, float(ch["w"][0]) / 2, (2.5, 3.5, 4.5, 6.0), 2.0)
            if got is not None:
                places.post(got[0], got[1], 1.0)
                n_put += 1
    return n_put


def rows_csv(w, places, runs, loners):
    """lamps.csv's rows, and [(x, y, z, yaw)...] per run for the wires."""
    rows, wired = [], []
    posts = np.array(places.posts).reshape(-1, 4)
    z = w.h.ground(posts[:, :2]) if len(posts) else []
    for (x, y, yaw, scale), zz in zip(places.posts, z):
        if not np.isfinite(zz):
            continue
        rows.append((POST, x, y, float(zz), yaw, scale))
        hx, hy, hz = (v * scale for v in furniture.LAMP_HEADS[furniture.G_LAMP])
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        rows.append((HEAD, x + c * hx - s * hy, y + s * hx + c * hy, float(zz) + hz - furniture.LENS_DROP_M, yaw, 1.0))
    for run in runs:
        rr = run_rows(run)
        zs = w.h.ground(np.array(rr)[:, :2])
        line = []
        for (x, y, yaw), zz in zip(rr, zs):
            if not np.isfinite(zz):
                continue
            rows.append((POLE, x, y, float(zz), yaw, 1.0))
            line.append((x, y, float(zz), yaw))
        if len(line) > 1:
            wired.append(line)
    if places.pole_lamps:
        pl = np.array(places.pole_lamps)
        zs = w.h.ground(pl[:, :2])
        for (x, y, yaw), zz in zip(places.pole_lamps, zs):
            if not np.isfinite(zz):
                continue
            rows.append((ARM, x, y, float(zz) + furniture.POLE_LAMP_Z, yaw, 1.0))
            hx, hy, hz = furniture.LAMP_HEADS[furniture.G_POLELAMP]
            c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
            rows.append((HEAD, x + c * hx - s * hy, y + s * hx + c * hy, float(zz) + furniture.POLE_LAMP_Z + hz - furniture.LENS_DROP_M, yaw, 1.0))
    for (x, y, yaw) in loners:
        zz = w.h.ground(np.array([[x, y]]))[0]
        if np.isfinite(zz):
            rows.append((POLE, x, y, float(zz), yaw, 1.0))
    return rows, wired


def write_wires(export, wired, w, conductors=(), extra=()):
    """wire_<i>_<j>.glb from the lines of poles, a span in the cell of its first pole, none under a deck, and the conductors
    of the tower lines; extra: more manifest entries."""
    by_cell = collections.defaultdict(list)
    for line in conductors:
        by_cell[(int(line[0][0, 0] // CELL), int(line[0][0, 1] // CELL))].append(line)
    for line in wired:
        for a, b in zip(line[:-1], line[1:]):
            mid = np.array([[(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]])
            if w.at(mid)[1][0] & F_DECK:
                continue
            by_cell[(int(a[0] // CELL), int(a[1] // CELL))].extend(furniture.wire_lines([a, b]))
    entries = []
    for f in os.listdir(export):
        if f.startswith("wire_") and f.endswith(".glb"):
            os.remove(os.path.join(export, f))
    for (i, j), lines in by_cell.items():
        origin = np.array([i * CELL, j * CELL, 0.0])
        got = furniture.wire_mesh(lines, origin)
        if got is None:
            continue
        P, T, UV = got
        name = f"wire_{i}_{j}"
        g = ue_export.Glb()
        g.add(P, T, UV, ["bne_wire"], np.zeros(len(T), np.int64), np.full((len(P), 4), 255, np.uint8))
        g.write(os.path.join(export, name + ".glb"), name, (0.0, 0.0, 0.0))
        entries.append({"file": name + ".glb", "kind": "wire", "collision_only": False, "origin": origin.tolist(), "tris": int(len(T)),
                        "verts": int(len(P)), "materials": ["bne_wire"]})
    path = os.path.join(export, "manifest.json")
    with open(path) as f:
        doc = json.load(f)
    doc["meshes"] = [e for e in doc["meshes"] if not e["file"].startswith(("wire_", "tower"))] + entries + list(extra)
    with open(path, "w") as f:
        json.dump(doc, f, indent=1)
    return sum(e["tris"] for e in entries)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/frederick-r3")
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--lamps", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    with open(os.path.join(a.net, "roadnet.json")) as f:
        net = json.load(f)
    with open(os.path.join(a.net, "net.pkl"), "rb") as f:
        pieces = pickle.load(f)["pieces"]
    infra = mapped_infra(os.path.join(a.net, "furniture_infra.pkl"))
    cells = sorted((int(f.split("_")[1]), int(f.split("_")[2].split(".")[0])) for f in os.listdir(a.dir) if f.startswith("ground_") and f.endswith(".glb"))
    w = World(a.dir, cells, a.jobs)
    links = net["links"]
    upts, uflag = [], []
    for p in pieces:
        if p["kind"] == "ground" and p["spec"]["cls"] not in ("service", "track"):
            upts.append(p["xy"])
            uflag.append(np.full(len(p["xy"]), bool(p["spec"].get("urban"))))
    urban_tree, urban_of = cKDTree(np.concatenate(upts)), np.concatenate(uflag)
    motor = cKDTree(np.concatenate([np.array(k["points"])[:, :2] for k in links if k["class"] == "motorway"]))
    chains, at = chains_of(links)
    places = Places()
    moved, dropped = mapped_lamps(w, infra["street_lamp"], places, motor)
    lines = mapped_lines(w, infra["power_pole"])
    runs = [ln for ln in lines if len(ln) > 1]
    loners = [run_rows(ln)[0] for ln in lines if len(ln) == 1]
    stats = place_roads(w, chains, urban_tree, urban_of, infra["power_pole"], places, runs)
    stats["poles_alley"] = place_alleys(w, links, runs)
    stats.update(place_junctions(w, links, at, urban_tree, urban_of, places))
    stats["lamp_ramp"] = place_ramps(w, chains, places)
    rows, wired = rows_csv(w, places, runs, loners)
    os.makedirs(os.path.dirname(a.lamps), exist_ok=True)
    with open(a.lamps, "w") as f:
        for r in rows:
            f.write("%d,%.2f,%.2f,%.2f,%.1f,%.3f\n" % r)
    counts = collections.Counter(r[0] for r in rows)
    print(json.dumps({"chains": len(chains), "mapped_lamps": len(infra["street_lamp"]), "lamps_pushed": moved, "lamps_dropped": dropped, "mapped_lines": len(runs),
                      "loners": len(loners), "posts": counts[POST], "poles": counts[POLE], "heads": counts[HEAD], "arms": counts[ARM], **stats}))
    if a.apply:
        extra, conductors, towers = tower_lines(w, infra["power_line"], infra["power_tower"], infra["power_pole"], a.dir)
        print("towers", towers, "wire tris", write_wires(a.dir, wired, w, conductors, extra))


if __name__ == "__main__":
    main()
