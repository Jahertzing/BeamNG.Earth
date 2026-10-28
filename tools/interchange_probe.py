#!/usr/bin/env python3
"""How the stacked ramps of a big interchange come out (the Mousetrap, I-25/I-70 in Denver, 2026-10-28: up to four
levels), offline from the cached map data: solves every bridge chain round each place as the road tiles do
(roads.BridgeProfiles over the graded ground) and checks what a car would meet:

  cross    two decks crossing: the upper one's underside clears the lower one's surface by MIN_BRIDGE_HEADROOM_M
  stack    a deck over another along it (their surfaces overlapping in plan): the same, or one surface (< FLUSH_M)
  under    a ground road (or railway) under a deck: the same headroom (not at an abutment's junction at grade)
  order    the map's level order kept: a higher level's deck never under a lower one's where they cross
  grade    no deck steeper than STEEP (a ramp's 6-7 %), nor a change of grade over KINK per 4 m step
  step     a deck's end and the deck or ground road joined there at one height (< STEP_M)

Prints one line per place and every failure; draws each place from above (decks by height, failures as red dots) and
the long sections of its decks as PNGs in --out.

    python tools/interchange_probe.py --out C:\\beamng-earth\\interchanges\\out          the route's interchanges
    python tools/interchange_probe.py --at 39.7787,-104.9935 --radius 500 --out /tmp/ic
"""
import argparse
import json
import math
import os
import sys
import time

os.environ.setdefault("BNE_REVISIT", "off")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
import numpy as np  # noqa: E402

# I-25's big interchanges from Frederick through Denver to Castle Rock (lat, lon, radius m)
PLACES = {
    "i25_e470": (40.0002, -104.9821, 700),
    "i25_us36_i270": (39.8165, -104.9903, 800),
    "mousetrap_i70": (39.7800, -104.9894, 600),
    "i25_6th_ave": (39.7256, -105.0005, 600),
    "i25_i225": (39.6412, -104.8985, 700),
    "i25_c470_e470": (39.5620, -104.8735, 900),
}
FLUSH_M = 0.25      # m: two decks closer than this in height are one surface (side by side, or a gore)
STEEP = 0.075       # a deck's grade over this (8 m sampled) is a failure
KINK = 0.04         # a change of grade over this between two 4 m steps
STEP_M = 0.15       # m: a height step at a deck's end
VERBOSE = False
END_SKIP_M = 25.0   # m off a shared end where decks overlap in plan by construction (a fork, a merge)


def arcs(xy):
    return np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy[:, :2], axis=0).T))]


def seg_cross(p, q):
    """[(arc on p, arc on q, x, y)] where the polylines p and q cross (not counting touching ends)."""
    out = []
    sp, sq = arcs(p), arcs(q)
    for i in range(len(p) - 1):
        a, b = p[i], p[i + 1]
        for j in range(len(q) - 1):
            c, d = q[j], q[j + 1]
            r, s = b - a, d - c
            den = r[0] * s[1] - r[1] * s[0]
            if abs(den) < 1e-12:
                continue
            t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
            u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
            if 0 <= t <= 1 and 0 <= u <= 1:
                out.append((sp[i] + t * (sp[i + 1] - sp[i]), sq[j] + u * (sq[j + 1] - sq[j]),
                            a[0] + t * r[0], a[1] + t * r[1]))
    return out


def near_end(s, L, m=END_SKIP_M):
    return s < m or L - s < m


def check(parts, levels, grounds, ground_z, under_d):
    """Failures [(kind, x, y, text)] of the solved decks parts [(fid, spec, xy, z)] (levels: their map levels)."""
    import roads
    fails = []
    need = roads.MIN_BRIDGE_HEADROOM_M + under_d - 0.05
    xyz = [(np.asarray(xy, float)[:, :2], np.asarray(z, float)) for _, _, xy, z in parts]
    S = [arcs(xy) for xy, _ in xyz]
    ends = np.array([p for xy, _ in xyz for p in (xy[0], xy[-1])]).reshape(-1, 2)
    box = [(xy.min(0), xy.max(0)) for xy, _ in xyz]
    # grades and kinks
    for k, (xy, z) in enumerate(xyz):
        if S[k][-1] < 8:
            continue
        t = np.arange(0, S[k][-1], 4.0)
        zz = np.interp(t, S[k], z)
        g = np.diff(zz[::2]) / 8.0 if len(zz) > 2 else np.array([0.0])
        if np.abs(g).max() > STEEP:
            i = int(np.argmax(np.abs(g))) * 2
            p = np.interp(t[i], S[k], xy[:, 0]), np.interp(t[i], S[k], xy[:, 1])
            fails.append(("grade", *p, f"{parts[k][0][:8]} grade {np.abs(g).max() * 100:.1f} %"))
        g4 = np.diff(zz) / 4.0
        if len(g4) > 1 and np.abs(np.diff(g4)).max() > KINK:
            i = int(np.argmax(np.abs(np.diff(g4)))) + 1
            p = np.interp(t[i], S[k], xy[:, 0]), np.interp(t[i], S[k], xy[:, 1])
            fails.append(("kink", *p, f"{parts[k][0][:8]} grade change {np.abs(np.diff(g4)).max() * 100:.1f} % in 4 m"))
    # deck over deck
    for i in range(len(xyz)):
        for j in range(i + 1, len(xyz)):
            wi, wj = parts[i][1].width, parts[j][1].width
            reach = (wi + wj) / 2
            if (box[i][1] + reach < box[j][0]).any() or (box[j][1] + reach < box[i][0]).any():
                continue
            (pi, zi), (pj, zj) = xyz[i], xyz[j]
            for si, sj, x, y in seg_cross(pi, pj):
                if near_end(si, S[i][-1], 2.0) and near_end(sj, S[j][-1], 2.0):
                    continue
                a, b = float(np.interp(si, S[i], zi)), float(np.interp(sj, S[j], zj))
                hi, lo = (i, j) if a > b else (j, i)
                dz = abs(a - b)
                if VERBOSE:
                    print(f"      x {parts[hi][0][:8]} L{levels[hi]:g} {max(a, b):.1f} over {parts[lo][0][:8]} "
                          f"L{levels[lo]:g} {min(a, b):.1f}: {dz:.2f} m at ({x:.0f}, {y:.0f})")
                if dz < need and dz > FLUSH_M:
                    fails.append(("cross", x, y, f"{parts[hi][0][:8]} L{levels[hi]:g} over {parts[lo][0][:8]} "
                                                 f"L{levels[lo]:g}: {dz:.2f} m"))
                elif dz <= FLUSH_M and levels[i] != levels[j]:
                    fails.append(("cross", x, y, f"{parts[i][0][:8]} L{levels[i]:g} through {parts[j][0][:8]} "
                                                 f"L{levels[j]:g}: {dz:.2f} m"))
                if levels[hi] < levels[lo] and dz > FLUSH_M:
                    fails.append(("order", x, y, f"{parts[hi][0][:8]} L{levels[hi]:g} over L{levels[lo]:g} "
                                                 f"{parts[lo][0][:8]}"))
            # along: sample i every 8 m, nearest point of j
            t = np.arange(0, S[i][-1], 8.0)
            q = np.c_[np.interp(t, S[i], pi[:, 0]), np.interp(t, S[i], pi[:, 1])]
            P, ab = pj[:-1], np.diff(pj, axis=0)
            L2 = np.maximum((ab * ab).sum(1), 1e-12)
            tt = np.clip(((q[:, None, :] - P[None]) * ab[None]).sum(2) / L2[None], 0, 1)
            d = np.hypot(*(q[:, None, :] - (P[None] + tt[..., None] * ab[None])).transpose(2, 0, 1))
            m = d.argmin(1)
            dd = d[np.arange(len(q)), m]
            worst = None
            for a_ in np.flatnonzero(dd < reach - 0.5):
                sj = S[j][m[a_]] + tt[a_, m[a_]] * (S[j][m[a_] + 1] - S[j][m[a_]])
                if near_end(t[a_], S[i][-1]) or near_end(sj, S[j][-1]):
                    if (np.hypot(*(ends - q[a_]).T) < END_SKIP_M + reach).any():
                        continue
                dz = abs(float(np.interp(t[a_], S[i], zi)) - float(np.interp(sj, S[j], zj)))
                if FLUSH_M < dz < need and (worst is None or dz < worst[0]):
                    worst = (dz, q[a_])
            if worst is not None:
                fails.append(("stack", *worst[1], f"{parts[i][0][:8]} L{levels[i]:g} / {parts[j][0][:8]} "
                                                  f"L{levels[j]:g} overlap {worst[0]:.2f} m apart"))
    # ground roads under decks
    for gname, gl in grounds:
        g = np.asarray(gl, float)[:, :2]
        if len(g) < 2:
            continue
        gs = arcs(g)
        for k, (xy, z) in enumerate(xyz):
            if (g.max(0) < box[k][0]).any() or (g.min(0) > box[k][1]).any():
                continue
            for sk, sg, x, y in seg_cross(xy, g):
                if near_end(sk, S[k][-1], roads.AT_GRADE_REACH_M) or near_end(sg, gs[-1], 1.0):
                    continue
                dz = float(np.interp(sk, S[k], z)) - float(ground_z(np.array([[x, y]]))[0])
                if dz < need:
                    fails.append(("under", x, y, f"{parts[k][0][:8]} L{levels[k]:g} {dz:.2f} m over {gname}"))
    return fails


def ends_steps(parts, grounds, ground_z):
    """Height steps where a deck's end meets another deck's end or a ground road's end."""
    out = []
    pts = []
    for fid, _, xy, z in parts:
        xy = np.asarray(xy, float)
        for e in (0, -1):
            pts.append((xy[e, :2], float(z[e]), fid))
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            if np.hypot(*(pts[a][0] - pts[b][0])) < 0.6 and abs(pts[a][1] - pts[b][1]) > STEP_M:
                out.append(("step", *pts[a][0], f"{pts[a][2][:8]}/{pts[b][2][:8]} joint {abs(pts[a][1] - pts[b][1]):.2f} m"))
    for gname, gl in grounds:
        g = np.asarray(gl, float)[:, :2]
        for e in (0, -1):
            for p, z, fid in pts:
                if np.hypot(*(g[e] - p)) < 0.6:
                    gz = float(ground_z(g[e:e + 1] if e == 0 else g[-1:])[0])
                    if abs(gz - z) > STEP_M:
                        out.append(("step", *p, f"{fid[:8]} abutment {z - gz:+.2f} m to {gname}"))
    return out


def draw(path, parts, levels, grounds, fails, radius):
    from PIL import Image, ImageDraw
    W = 1400
    sc = W / (2 * radius)
    img = Image.new("RGB", (W, W), (24, 24, 24))
    dr = ImageDraw.Draw(img)
    T = lambda x, y: (W / 2 + x * sc, W / 2 - y * sc)  # noqa: E731
    for _, gl in grounds:
        g = np.asarray(gl, float)
        if len(g) > 1:
            dr.line([T(*p[:2]) for p in g], fill=(90, 90, 90), width=2)
    zall = np.concatenate([np.asarray(z) for *_, z in parts]) if parts else np.zeros(1)
    z0, z1 = float(np.nanmin(zall)), float(np.nanmax(zall))
    order = sorted(range(len(parts)), key=lambda k: float(np.nanmean(parts[k][3])))
    for k in order:
        _, spec, xy, z = parts[k]
        xy = np.asarray(xy)
        for a in range(len(xy) - 1):
            u = (float(z[a]) - z0) / max(z1 - z0, 1e-6)
            col = (int(40 + 215 * u), int(120 + 100 * (1 - abs(u - 0.5) * 2)), int(255 - 215 * u))
            dr.line([T(*xy[a, :2]), T(*xy[a + 1, :2])], fill=col, width=max(2, int(spec.width * sc)))
        mid = xy[len(xy) // 2]
        dr.text(T(*mid[:2]), f"L{levels[k]:g}", fill=(255, 255, 255))
    for kind, x, y, _ in fails:
        cx, cy = T(x, y)
        dr.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=(255, 40, 40), width=3)
    dr.text((10, 10), f"decks {z0:.1f}-{z1:.1f} m (blue low, red high); grey: ground roads; red rings: failures",
            fill=(255, 255, 255))
    img.save(path)


def sections(path, parts, levels):
    from PIL import Image, ImageDraw
    n = len(parts)
    if not n:
        return
    W, H = 1400, 140
    img = Image.new("RGB", (W, H * n), (250, 250, 250))
    dr = ImageDraw.Draw(img)
    zall = np.concatenate([np.asarray(z) for *_, z in parts])
    z0, z1 = float(np.nanmin(zall)) - 1, float(np.nanmax(zall)) + 1
    Lmax = max(arcs(np.asarray(xy))[-1] for _, _, xy, _ in parts)
    for k, (fid, spec, xy, z) in enumerate(parts):
        s = arcs(np.asarray(xy))
        y0 = H * k
        pts = [(10 + s[a] / Lmax * (W - 20), y0 + H - 10 - (z[a] - z0) / (z1 - z0) * (H - 20)) for a in range(len(s))]
        dr.line(pts, fill=(30, 60, 200), width=2)
        dr.text((10, y0 + 4), f"{fid[:8]} L{levels[k]:g} {spec.cls}{' link' if spec.link else ''} {s[-1]:.0f} m "
                              f"z {np.min(z):.1f}-{np.max(z):.1f}", fill=(0, 0, 0))
    img.save(path)


def probe(name, la, lo, radius, cache_dir, out, lidar=True):
    import geodesy
    import grading
    import mapcells
    import mapdata
    import mapgeom
    import netfetch
    import roadmesh
    import roads
    import dem as demmod
    t0 = time.time()
    net = netfetch.Fetcher()
    md = mapdata.MapData(cache_dir, fetcher=net)
    dem = demmod.Dem(os.path.join(cache_dir, "dem"), fetcher=net)
    frame = geodesy.EnuFrame(la, lo)
    sample = lambda a, b: dem.sample(a, b, grading.PROFILE_ZOOM)  # noqa: E731
    grader = grading.Grader(frame, md, sample)

    def elev(a, b):
        return grader.apply(a, b, sample(a, b), grading.ROAD_LIFT)
    cell_fn = lambda k: md.cell(k, layers=("roads",), kind="demand").layers.get("roads")  # noqa: E731
    bp = roads.BridgeProfiles(frame, cell_fn, elev)
    if lidar:
        import decklidar
        bp.surface = decklidar.surface_for(md, dem)
    box = (-radius, -radius, radius, radius)
    pts_la, pts_lo, _ = frame.enu_to_geodetic(np.array([-radius, radius, -radius, radius, 0.0]),
                                              np.array([-radius, -radius, radius, radius, 0.0]), 0.0)
    keys = sorted({str(k): k for k in mapcells.cells_for_points(pts_la, pts_lo)}.items())
    lo_k = min(k.x for _, k in keys), min(k.y for _, k in keys)
    hi_k = max(k.x for _, k in keys), max(k.y for _, k in keys)
    z = keys[0][1].z
    cells = [cell_fn(mapcells.CellKey(z, x, y)) for x in range(lo_k[0], hi_k[0] + 1) for y in range(lo_k[1], hi_k[1] + 1)]
    got = bp.parts(cells, box)
    parts, levels, seen = [], [], set()
    lvl = {}
    for feats in cells:
        if feats is None:
            continue
        bs = roads.bridge_specs(feats)
        for i in range(len(feats)):
            if bs[i] is not None:
                for j, v in enumerate(roads.bridge_levels(feats, i)):
                    lvl.setdefault(str(feats.ids[i]), v)
    for fid, spec, xy, zz, _ in got:
        key = (fid, len(xy), round(float(xy[0][0]), 1))
        if key in seen:
            continue
        seen.add(key)
        parts.append((fid, spec, np.asarray(xy), np.asarray(zz)))
        levels.append(lvl.get(fid, 1.0))
    grounds = []
    for feats in cells:
        if feats is None:
            continue
        rs = roads.road_specs(feats)
        for i in range(len(feats)):
            if rs[i] is None:
                continue
            for ring in roads.ground_parts(feats, i):
                e, n = mapgeom.enu_xy(feats, frame, ring)
                if (np.abs(e) < radius + 50).any() and (np.abs(n) < radius + 50).any():
                    grounds.append((f"{str(feats.ids[i])[:8]} {rs[i].cls}{' link' if rs[i].link else ''} "
                                    f"{str(feats.attrs['name'][i])[:24]}".strip(), np.stack([e, n], 1)))
    if lidar and bp.surface is not None and parts:     # how the decks sit against the lidar
        obs, _ = bp._observed([p[2] for p in parts], [p[1].width for p in parts], levels)
        res, n_meas = [], 0
        for k, p in enumerate(parts):
            o = None if obs is None else obs[k]
            if o is None:
                continue
            n_meas += 1
            ok = np.isfinite(o)
            res.extend(np.abs(np.asarray(p[3])[ok] - o[ok]).tolist())
            if VERBOSE:
                print(f"      lidar {p[0][:8]} L{levels[k]:g}: {ok.mean() * 100:.0f} % measured, deck - lidar median "
                      f"{np.median(np.asarray(p[3])[ok] - o[ok]):+.2f} m, worst {np.abs(np.asarray(p[3])[ok] - o[ok]).max():.2f} m")
        if res:
            print(f"    lidar: {n_meas} of {len(parts)} parts measured; |deck - lidar| median {np.median(res):.2f} m, "
                  f"95th {np.percentile(res, 95):.2f} m")
    under_d = roadmesh.DECK_DEPTH + roadmesh.GIRDER_D
    fails = check(parts, levels, grounds, bp._ground, under_d) + ends_steps(parts, grounds, bp._ground)
    fails = [f for f in fails if abs(f[1]) <= radius and abs(f[2]) <= radius]
    kinds = {}
    for f in fails:
        kinds[f[0]] = kinds.get(f[0], 0) + 1
    print(f"{name}: {len(parts)} deck parts, levels {sorted(set(levels))}, "
          f"{sum(arcs(np.asarray(p[2]))[-1] for p in parts) / 1000:.1f} km of deck, {time.time() - t0:.0f} s; "
          f"failures {kinds or 'none'}", flush=True)
    for f in fails:
        la_, lo_, _ = frame.enu_to_geodetic(np.array([f[1]]), np.array([f[2]]), 0.0)
        print(f"    {f[0]:6s} {float(la_[0]):.5f},{float(lo_[0]):.5f}  {f[3]}")
    os.makedirs(out, exist_ok=True)
    draw(os.path.join(out, f"{name}_plan.png"), parts, levels, grounds, fails, radius)
    sections(os.path.join(out, f"{name}_sections.png"), parts, levels)
    return {"place": name, "parts": len(parts), "levels": sorted(set(levels)), "failures": kinds,
            "list": [(f[0], round(f[1], 1), round(f[2], 1), f[3]) for f in fails]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--at", default=None, help="lat,lon (default: PLACES)")
    ap.add_argument("--radius", type=float, default=600.0)
    ap.add_argument("--only", default=None, help="comma-separated PLACES names")
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/beamng-earth"))
    ap.add_argument("--out", default="interchange_probe")
    ap.add_argument("--no-lidar", action="store_true", help="the decks without the lidar's heights (decklidar)")
    ap.add_argument("-v", "--verbose", action="store_true", help="every deck-over-deck crossing")
    a = ap.parse_args()
    global VERBOSE
    VERBOSE = a.verbose
    if a.at:
        la, lo = (float(v) for v in a.at.split(","))
        places = {"at": (la, lo, a.radius)}
    else:
        places = {k: v for k, v in PLACES.items() if not a.only or k in a.only.split(",")}
    res = [probe(k, *v, a.cache_dir, a.out, not a.no_lidar) for k, v in places.items()]
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
