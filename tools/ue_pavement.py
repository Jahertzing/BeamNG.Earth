#!/usr/bin/env python3
"""The ground-level pavement of a cell as one mesh, from the surveyed curbs where they exist: the outline is the area the
curbs close around the roads' fitted centres (flood), the fitted ribbons cover what the survey cannot see (under a deck, a
junction's open mouth), the spec's ribbon what it does not cover. The mesh is the terrain's own triangles clipped to the
outline, lifted LIFT_M: it cannot pierce or float off the ground, whatever the outline's shape.

    Earth.UE\\.venv\\Scripts\\python tools\\ue_pavement.py --net <roadnet dir> --dir <export dir> --cell=-2,-1

Writes <kind road>_<i>_<j>.glb over the builder's: its asphalt, dirt and edge decals out, its markings and gutters kept.
"""
import argparse
import json
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402

import numpy as np  # noqa: E402
import shapely  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from scipy import ndimage  # noqa: E402
from skimage import measure  # noqa: E402

import ue_survey  # noqa: E402

PX = 0.25
CELL = 2048.0
MARGIN_M = 64.0                 # m of roads and curbs round the cell
GRID_M = 2.0                    # the terrain's lattice
LIFT_M = 0.03                   # m over the terrain
CORRIDOR_M = 1.5                # m past a fitted ribbon the flood may go
BLOCK_M = 64.0                  # m: the outline is cut to blocks of this side before the triangles are clipped
GAP_M = 30.0                    # m: gaps in a curb line closed up to this
RIBBON_SHARE = 0.5              # share of a closed region the ribbons must cover for it to be pavement
OPEN_PX = 4                     # px: thinner strips opened away (0.25 m each way)
MIN_ISLAND_M2 = 6.0             # m2: smaller separate bits of pavement left out
MIN_HOLE_M2 = 3.0               # m2: smaller holes in it filled
UNSURVEYED_MATERIAL = {"paved": "road_asphalt_2lane", "cobble": "road_asphalt_2lane", "gravel": "m_dirt_road",
                       "dirt": "m_dirt_road"}
DROPPED = ("road_asphalt", "m_dirt_road", "m_gm_concrete_plain", "ECA_road_edge_dirt", "italy_cobblestone")
MATERIALS = ["road_asphalt_2lane", "m_dirt_road", "bne_bridge_concrete"]


def material_of(spec):
    if spec["kind"] == "paved" and spec["surf"] == "concrete":
        return 2
    return MATERIALS.index(UNSURVEYED_MATERIAL.get(spec["kind"], "road_asphalt_2lane"))


def edges_of(p):
    """(left edge (n, 2), right edge (n, 2), measured (n,)) of piece p along its stations: the fit where it has one, else
    the spec's width about its line, eased over the change."""
    w = float(p["spec"]["width"] or 6.0)
    ab = p["ab"]
    measured = np.isfinite(ab[:, 0])
    a = np.where(measured, ab[:, 0], -w / 2)
    b = np.where(measured, ab[:, 1], w / 2)
    if len(a) > 4:
        a = ndimage.gaussian_filter1d(a, 1.5, mode="nearest")
        b = ndimage.gaussian_filter1d(b, 1.5, mode="nearest")
    return p["st"] + p["nrm"] * b[:, None], p["st"] + p["nrm"] * a[:, None], measured


def junction_disks(pieces):
    """[(xy, r)] where three or more roads end at one point."""
    ends = {}
    for p in pieces:
        if p["kind"] != "ground":
            continue
        for q in (p["xy"][0], p["xy"][-1]):
            ends.setdefault((round(q[0] / 1.0), round(q[1] / 1.0)), []).append((p["fid"], float(p["spec"]["width"] or 6.0), q))
    out = []
    for got in ends.values():
        if len({g[0] for g in got}) >= 3:
            out.append((got[0][2], max(18.0, 1.3 * max(g[1] for g in got) + 8.0)))
    return out


def draw_polys(shape, polys, x0, y0, value=255):
    img = Image.new("L", shape[::-1], 0)
    d = ImageDraw.Draw(img)
    for poly in polys:
        d.polygon([tuple(v) for v in (poly - (x0, y0)) / PX], fill=value)
    return np.asarray(img)


def outline_mask(pieces, lines, box, lots=()):
    """(pavement mask, material label raster, surveyed share) of window box, row 0 at its y0. lots: the mapped parking
    lots' rings, asphalt from edge to edge."""
    x0, y0, x1, y1 = box
    n = int(round((x1 - x0) / PX)), int(round((y1 - y0) / PX))
    shape = n[1], n[0]
    fitted, spec_ribbons = [], []
    label = Image.new("L", shape[::-1], 0)
    dl = ImageDraw.Draw(label)
    seeds = []
    for p in pieces:
        if p["kind"] != "ground" or len(p["st"]) < 2:
            continue
        L, R, measured = edges_of(p)
        ring = np.vstack([L, R[::-1]])
        dl.polygon([tuple(v) for v in (ring - (x0, y0)) / PX], fill=material_of(p["spec"]) + 1)
        for run in ue_export_runs(measured):
            r = np.vstack([L[run], R[run][::-1]])
            fitted.append(r)
        for run in ue_export_runs(~measured):
            spec_ribbons.append(np.vstack([L[run], R[run][::-1]]))
        two = p["two"] & measured
        if two.any():
            mid = p["st"][two] + p["nrm"][two] * p["ab"][two].mean(1)[:, None]
            seeds.append(mid)
    fit = draw_polys(shape, fitted, x0, y0) > 0
    spec = draw_polys(shape, spec_ribbons, x0, y0) > 0
    corridor = ndimage.binary_dilation(fit, iterations=int(CORRIDOR_M / PX))
    disks = Image.new("L", shape[::-1], 0)
    dd = ImageDraw.Draw(disks)
    for q, r in junction_disks(pieces):
        c = (q - (x0, y0)) / PX
        dd.ellipse([c[0] - r / PX, c[1] - r / PX, c[0] + r / PX, c[1] + r / PX], fill=255)
    corridor |= np.asarray(disks) > 0
    barrier = ue_survey.Raster(lines + closing_lines(lines, fit, box), box).mask
    barrier = ndimage.binary_dilation(barrier, structure=np.ones((2, 2), bool))
    lab, count = ndimage.label(corridor & ~barrier)
    keep = np.zeros(count + 1, bool)
    if seeds:
        s = np.concatenate(seeds)
        c = ((s[:, 0] - x0) / PX).astype(np.int64)
        r = ((s[:, 1] - y0) / PX).astype(np.int64)
        ok = (r >= 0) & (c >= 0) & (r < shape[0]) & (c < shape[1])
        keep[lab[r[ok], c[ok]]] = True
        keep[0] = False
    # a region that is mostly outside the ribbons is the strip past a curb a stray seed fell in, and a strip that leaked
    # through a gap in the curb is too thin to be a road: opened away
    inside = ndimage.sum_labels(fit, lab, np.arange(count + 1))
    size = np.maximum(np.bincount(lab.ravel(), minlength=count + 1), 1)
    keep &= inside / size >= RIBBON_SHARE
    flood = ndimage.binary_opening(keep[lab], iterations=OPEN_PX)
    flood = ndimage.binary_dilation(flood, iterations=2)    # (back over the line and its dilation)
    mask = flood | fit | spec
    if len(lots):
        near = [r for r in lots if r[:, 0].max() >= x0 and r[:, 0].min() <= x1 and r[:, 1].max() >= y0 and r[:, 1].min() <= y1]
        if near:
            lot = draw_polys(shape, near, x0, y0) > 0
            mask |= lot
            if (lot & (np.asarray(label) == 0)).any():
                label = Image.fromarray(np.where(lot & (np.asarray(label) == 0), 1, np.asarray(label)).astype(np.uint8))
    mask = ndimage.binary_closing(mask, iterations=2)
    # a label for every pixel of it: the nearest ribbon's
    lb = np.asarray(label)
    idx = ndimage.distance_transform_edt(lb == 0, return_distances=False, return_indices=True)
    return mask, lb[idx[0], idx[1]], float(flood.sum()) / max(float(mask.sum()), 1.0)


def closing_lines(lines, fit, box):
    """Short lines over the surveyed curbs' gaps: from a line's end to the next line's end within GAP_M, unless that crosses
    a road's fitted ribbon (then the gap is the road's mouth). Without these a corner's flood runs out into the field."""
    x0, y0, x1, y1 = box
    ends = []
    for ln in lines:
        for q in (ln[0], ln[-1]):
            if x0 <= q[0] <= x1 and y0 <= q[1] <= y1:
                ends.append(q)
    if len(ends) < 2:
        return []
    ends = np.array(ends)
    from scipy.spatial import cKDTree
    tree = cKDTree(ends)
    d, nb = tree.query(ends, k=min(4, len(ends)), distance_upper_bound=GAP_M)
    core = ndimage.binary_erosion(fit, iterations=int(1.0 / PX))
    out, seen = [], set()
    for a in range(len(ends)):
        for dist, b in zip(np.atleast_1d(d[a])[1:], np.atleast_1d(nb[a])[1:]):
            if not np.isfinite(dist) or dist < 0.01 or (min(a, int(b)), max(a, int(b))) in seen:
                continue
            seen.add((min(a, int(b)), max(a, int(b))))
            t = np.linspace(0.0, 1.0, max(2, int(dist / PX)))[:, None]
            q = ends[a] + t * (ends[int(b)] - ends[a])
            c = ((q[:, 0] - x0) / PX).astype(np.int64).clip(0, core.shape[1] - 1)
            r = ((q[:, 1] - y0) / PX).astype(np.int64).clip(0, core.shape[0] - 1)
            if core[r, c].mean() < 0.1:
                out.append(np.array([ends[a], ends[int(b)]]))
    return out


def ue_export_runs(flags):
    """The index arrays of flags' runs of two or more."""
    out, start = [], None
    for i, f in enumerate(flags.tolist() + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start >= 2:
                out.append(np.arange(start, i))
            start = None
    return out


def polygons(mask, x0, y0):
    """Shapely polygons of mask, its outline smoothed at about 0.4 m, in frame m."""
    clean = measure.label(mask, connectivity=1)
    areas = np.bincount(clean.ravel())
    small = areas * PX * PX < MIN_ISLAND_M2
    small[0] = False
    m = mask & ~small[clean]
    holes = measure.label(~m, connectivity=1)
    areas = np.bincount(holes.ravel())
    small = areas * PX * PX < MIN_HOLE_M2
    small[0] = False
    m = m | small[holes]
    f = ndimage.gaussian_filter(np.pad(m, 2).astype(np.float32), 1.6)
    shells, inner = [], []
    for c in measure.find_contours(f, 0.5, positive_orientation="low"):
        if len(c) < 8:
            continue
        ring = shapely.LinearRing(np.c_[(c[:, 1] - 2 + 0.5) * PX + x0, (c[:, 0] - 2 + 0.5) * PX + y0])
        ring = shapely.simplify(ring, 0.04)
        if not ring.is_valid or len(ring.coords) < 4:
            continue
        (shells if ring.is_ccw else inner).append(ring)
    polys = [shapely.Polygon(r) for r in shells]
    if not polys:
        return []
    tree = shapely.STRtree(polys)
    has = {}
    for r in inner:
        p = shapely.Point(r.coords[0])
        got = tree.query(p, predicate="intersects")
        if len(got):
            k = min(got.tolist(), key=lambda v: polys[v].area)
            has.setdefault(k, []).append(r)
    return [shapely.make_valid(shapely.Polygon(shells[k], has.get(k, ()))) for k in range(len(polys))]


def clip_terrain(polys, mask, z, x0, y0, labels):
    """(P (n, 3) cell m, T (m, 3), material per triangle) of the terrain's triangles clipped to polys. mask: the pavement
    over the cell (row 0 at y0), z: (N + 1, N + 1) terrain heights."""
    N = z.shape[0] - 1
    k = int(GRID_M / PX)
    h, w = N * k, N * k
    grow = ndimage.binary_dilation(mask[:h, :w], iterations=3)
    shrink = ndimage.binary_erosion(mask[:h, :w], iterations=3)
    any_ = grow.reshape(N, k, N, k).any((1, 3))
    all_ = shrink.reshape(N, k, N, k).all((1, 3))
    Ps, Ts, Ms, base = [], [], [], 0

    def label_at(x, y):
        c = np.clip(((x - x0) / PX).astype(np.int64), 0, labels.shape[1] - 1)
        r = np.clip(((y - y0) / PX).astype(np.int64), 0, labels.shape[0] - 1)
        return np.maximum(labels[r, c].astype(np.int64) - 1, 0)
    # whole triangles
    rr, cc = np.nonzero(all_)
    if len(rr):
        a = rr * (N + 1) + cc
        T = np.r_[np.c_[a, a + 1, a + N + 1], np.c_[a + 1, a + N + 2, a + N + 1]]
        X, Y = np.meshgrid(x0 + np.arange(N + 1) * GRID_M, y0 + np.arange(N + 1) * GRID_M)
        P = np.c_[X.ravel(), Y.ravel(), z.ravel()]
        used, inv = np.unique(T, return_inverse=True)
        T = inv.reshape(-1, 3)
        P = P[used]
        c = P[T].mean(1)
        Ps.append(P)
        Ts.append(T)
        Ms.append(label_at(c[:, 0], c[:, 1]))
        base = len(P)
    # the outline's squares, block by block
    rr, cc = np.nonzero(any_ & ~all_)
    if len(rr) and polys:
        blocks = {}
        for r, c in zip(rr.tolist(), cc.tolist()):
            blocks.setdefault((int(c * GRID_M // BLOCK_M), int(r * GRID_M // BLOCK_M)), []).append((r, c))
        whole = shapely.union_all(polys)
        for (bi, bj), squares in blocks.items():
            bx, by = x0 + bi * BLOCK_M, y0 + bj * BLOCK_M
            geom = shapely.clip_by_rect(whole, bx - 1, by - 1, bx + BLOCK_M + 1, by + BLOCK_M + 1)
            if geom.is_empty:
                continue
            q = np.array(squares)
            X0, Y0 = x0 + q[:, 1] * GRID_M, y0 + q[:, 0] * GRID_M
            # the terrain's two triangles of a square: (0, 0), (1, 0), (0, 1) and (1, 0), (1, 1), (0, 1)
            G = GRID_M
            tri_a = np.stack([np.c_[X0, Y0], np.c_[X0 + G, Y0], np.c_[X0, Y0 + G], np.c_[X0, Y0]], axis=1)
            tri_b = np.stack([np.c_[X0 + G, Y0], np.c_[X0 + G, Y0 + G], np.c_[X0, Y0 + G], np.c_[X0 + G, Y0]], axis=1)
            tris = shapely.polygons(np.r_[tri_a, tri_b])
            z00 = z[q[:, 0], q[:, 1]]
            z10 = z[q[:, 0], q[:, 1] + 1]
            z01 = z[q[:, 0] + 1, q[:, 1]]
            z11 = z[q[:, 0] + 1, q[:, 1] + 1]
            # each triangle's plane: z at its corner (x0, y0) and slopes
            pz = np.r_[z00, z11 + (z10 - z11) + (z01 - z11)]
            gx = np.r_[(z10 - z00) / G, (z11 - z01) / G]
            gy = np.r_[(z01 - z00) / G, (z11 - z10) / G]
            ox, oy = np.r_[X0, X0], np.r_[Y0, Y0]
            cut = shapely.intersection(tris, geom)
            keep = ~shapely.is_empty(cut)
            cut, own = cut[keep], np.flatnonzero(keep)
            if not len(cut):
                continue
            parts, which = shapely.get_parts(cut, return_index=True)
            poly_ok = shapely.get_type_id(parts) == 3
            parts, which = parts[poly_ok], which[poly_ok]
            cdt = shapely.constrained_delaunay_triangles(parts)
            small, up = shapely.get_parts(cdt, return_index=True)
            xy = shapely.get_coordinates(small)
            xy = xy.reshape(-1, 4, 2)[:, :3]
            src = own[which[up]]
            cross = (xy[:, 1, 0] - xy[:, 0, 0]) * (xy[:, 2, 1] - xy[:, 0, 1]) - (xy[:, 1, 1] - xy[:, 0, 1]) * (xy[:, 2, 0] - xy[:, 0, 0])
            good = np.abs(cross) > 1e-5
            xy, src, cross = xy[good], src[good], cross[good]
            flip = cross < 0
            xy[flip] = xy[flip][:, ::-1]
            zz = pz[src, None] + gx[src, None] * (xy[..., 0] - ox[src, None]) + gy[src, None] * (xy[..., 1] - oy[src, None])
            P = np.concatenate([xy, zz[..., None]], axis=2).reshape(-1, 3)
            T = np.arange(len(P)).reshape(-1, 3) + base
            c = xy.mean(1)
            Ps.append(P)
            Ts.append(T)
            Ms.append(label_at(c[:, 0], c[:, 1]))
            base += len(P)
    if not Ps:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64), np.zeros(0, np.int64)
    P, T, M = np.concatenate(Ps), np.concatenate(Ts), np.concatenate(Ms)
    key = np.round(P * 1000).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.ravel()
    return P[first], inv[T], M


def build_cell(pieces, lines, i, j, in_dir, out_dir, lots=(), road_dir=None):
    t0 = time.perf_counter()
    x0, y0 = i * CELL, j * CELL
    box = (x0 - MARGIN_M, y0 - MARGIN_M, x0 + CELL + MARGIN_M, y0 + CELL + MARGIN_M)
    near = [p for p in pieces if len(p["st"]) and p["st"][:, 0].max() >= box[0] - 30 and p["st"][:, 0].min() <= box[2] + 30
            and p["st"][:, 1].max() >= box[1] - 30 and p["st"][:, 1].min() <= box[3] + 30]
    mask, labels, surveyed = outline_mask(near, lines, box, lots)
    m = int(MARGIN_M / PX)
    n = int(CELL / PX)
    polys = polygons(mask[m:m + n, m:m + n], x0, y0)
    ground = ue_survey.read_glb(os.path.join(in_dir, f"ground_{i}_{j}.glb"))[0]
    N = int(round(len(ground["P"]) ** 0.5)) - 1
    z = ground["P"][:, 2].reshape(N + 1, N + 1)
    P, T, M = clip_terrain(polys, mask[m:m + n, m:m + n], z, x0, y0, labels[m:m + n, m:m + n])
    P[:, 2] += LIFT_M
    P[:, 0] -= x0
    P[:, 1] -= y0
    UV = P[:, :2] / 8.0
    C = np.full((len(P), 4), 255, np.uint8)
    names = list(MATERIALS)
    allP, allT, allUV, allC, allM, base = [P], [T], [UV], [C], [M], len(P)
    path = os.path.join(road_dir or in_dir, f"road_{i}_{j}.glb")
    if os.path.exists(path):
        for prim in ue_survey.read_glb(path):
            if prim["name"].startswith(DROPPED):
                continue
            if prim["name"] not in names:
                names.append(prim["name"])
            used, inv = np.unique(prim["T"], return_inverse=True)
            allP.append(prim["P"][used])
            allT.append(inv.reshape(-1, 3) + base)
            allUV.append(prim["UV"][used])
            allC.append(prim["C"][used])
            allM.append(np.full(len(prim["T"]), names.index(prim["name"])))
            base += len(used)
    g = ue_export.Glb()
    g.add(np.concatenate(allP), np.concatenate(allT), np.concatenate(allUV), names, np.concatenate(allM), np.concatenate(allC))
    os.makedirs(out_dir, exist_ok=True)
    g.write(os.path.join(out_dir, f"road_{i}_{j}.glb"), f"road_{i}_{j}", (0.0, 0.0, 0.0))
    return {"cell": [i, j], "pavement_tris": int(len(T)), "pavement_m2": int(mask[m:m + n, m:m + n].sum() * PX * PX),
            "from_curbs": round(surveyed, 3), "polys": len(polys), "s": round(time.perf_counter() - t0, 1)}


_w = {}


def init_worker(net_dir):
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    with open(os.path.join(net_dir, "stations.pkl"), "rb") as f:
        _w["pieces"] = pickle.load(f)
    with open(os.path.join(net_dir, "survey.pkl"), "rb") as f:
        _w["lines"] = pickle.load(f)
    with open(os.path.join(net_dir, "net.pkl"), "rb") as f:
        _w["lots"] = pickle.load(f).get("lots", ())


def run_cell(job):
    i, j, in_dir, out_dir = job
    return build_cell(_w["pieces"], _w["lines"], i, j, in_dir, out_dir, _w["lots"])


def main():
    import concurrent.futures
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--net", default="C:/beamng-earth/Earth.UE/export/roadnet")
    ap.add_argument("--dir", default="C:/beamng-earth/Earth.UE/export/roads-a")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cell", default=None, help="i,j;i,j (all the cells with a ground mesh in --dir if left out)")
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args()
    if a.cell:
        cells = [tuple(int(v) for v in c.split(",")) for c in a.cell.split(";")]
    else:
        cells = [tuple(int(v) for v in f[7:-4].split("_")) for f in os.listdir(a.dir)
                 if f.startswith("ground_") and f.endswith(".glb")]
    out = a.out or a.dir + "-pave"
    with concurrent.futures.ProcessPoolExecutor(a.jobs, initializer=init_worker, initargs=(a.net,)) as pool:
        for got in pool.map(run_cell, [(i, j, a.dir, out) for i, j in cells]):
            print(json.dumps(got), flush=True)


if __name__ == "__main__":
    main()
