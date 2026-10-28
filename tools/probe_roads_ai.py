#!/usr/bin/env python3
"""The roads, the drawn carriageways and the AI road graph around a point, without the game or the server: for "why
did the AI drive off the road here" questions.

  python3 tools/probe_roads_ai.py 40.09874,-104.94170 [--box 300] [--track 40.09867,-104.9400 ...]

For the roads within --box m (a square around the point): class, name, one-way, lanes, spec.width, the driving width
the AI is given (navgraph.driving_width) and the turn pocket / taper profile along it (roads.pocket_profiles: the
widths the road tiles draw); the bridge decks there (roads.BridgeProfiles on the DEM: no grading, which needs the
server's road plans); the AI graph's edges (navgraph.build: lanes, one-way, half width, the grade the mod's tooSteep
cuts at) and their nodes; every AI lane centre less than --margin m inside the drawn pavement (carriageways and decks);
and per --track point (lat,lon; default: the point itself) the nearest drawn carriageway edges and AI edges. Reads the
download cache (--cache-dir, default ~/.cache/beamng-earth) like the server's road processes (measured widths,
DRCOG's pavement, land cover); a missing cell is fetched. The frame is the point's own (--anchor lat,lon for the
game's: the samples' x, y).
"""
import argparse
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
import numpy as np  # noqa: E402

import dem as demmod  # noqa: E402
import geodesy  # noqa: E402
import mapcells  # noqa: E402
import mapdata as mapdatamod  # noqa: E402
import mapgeom  # noqa: E402
import navgraph  # noqa: E402
import netfetch  # noqa: E402
import roads  # noqa: E402
import roadwidth  # noqa: E402

MAX_GRADE = 0.45            # the mod's navgraph.lua M.MAX_GRADE: a steeper edge is left out of the game's graph


def latlon(s):
    a, b = s.split(",")
    return float(a), float(b)


def seg_dist(p, line):
    """(distance, index of the segment, t along it, signed side: + left of travel) of point p to polyline line."""
    a, b = line[:-1, :2], line[1:, :2]
    d = b - a
    L2 = np.maximum((d * d).sum(1), 1e-12)
    t = np.clip(((p - a) * d).sum(1) / L2, 0, 1)
    q = a + t[:, None] * d
    dist = np.hypot(*(p - q).T)
    k = int(dist.argmin())
    side = float(np.sign(d[k, 0] * (p[1] - a[k, 1]) - d[k, 1] * (p[0] - a[k, 0])))
    return float(dist[k]), k, float(t[k]), side


def pavement(p, drawn):
    """[(m inside (+) / outside (-) its edge, fid, spec, m off its centre, half width, side)] of point p per drawn
    road of drawn [(fid, spec, centreline (k, 2), roads.widen_edges of it or None)], the most inside first."""
    rows = []
    for fid, spec, line, edges in drawn:
        d, k, t, side = seg_dist(p, line)
        half = spec.width / 2
        if edges is not None:
            el = np.asarray(edges[0 if side > 0 else 1])
            half += float(el[k] * (1 - t) + el[k + 1] * t)
        rows.append((half - d, fid, spec, d, half, side))
    return sorted(rows, key=lambda r: -r[0])


def lane_points(a, b, radius, n, step):
    """[(lane index, point)] every step m along each lane's centre of the AI edge a -> b (half width radius, n lanes):
    the stock AI's lanes, one char each of the edge's lanes string ('-' first, left of a -> b), 2 radius / n wide."""
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    L = float(np.hypot(*(b - a)))
    if L < 0.1 or n < 1:
        return []
    u = (b - a) / L
    left = np.array([-u[1], u[0]])
    lane_w = 2 * radius / n
    return [(k, a + u * s + left * (radius - (k + 0.5) * lane_w))
            for k in range(n) for s in np.linspace(0, L, max(2, int(L / step) + 1))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("point", type=latlon, help="lat,lon")
    ap.add_argument("--box", type=float, default=300.0, help="m, side of the square around the point")
    ap.add_argument("--track", nargs="*", type=latlon, default=None, help="lat,lon points to measure")
    ap.add_argument("--anchor", type=latlon, help="the anchor frame's origin (default: the point)")
    ap.add_argument("--no-widths", action="store_true", help="no measured widths (roadwidth / DRCOG)")
    ap.add_argument("--no-urban", action="store_true", help="no land cover (every road rural)")
    ap.add_argument("--no-decks", action="store_true", help="no bridge decks (the graph's nodes on the DEM)")
    ap.add_argument("--zoom", type=int, default=15, help="DEM zoom (the physics ground's)")
    ap.add_argument("--margin", type=float, default=1.0, help="m an AI lane centre should keep inside the pavement")
    ap.add_argument("--step", type=float, default=2.0, help="m between the lane points tested")
    ap.add_argument("--cache-dir", default=os.path.join(os.path.expanduser("~"), ".cache/beamng-earth"))
    args = ap.parse_args()
    la0, lo0 = args.point
    frame = geodesy.EnuFrame(*(args.anchor or args.point))
    net = netfetch.Fetcher()
    md = mapdatamod.MapData(args.cache_dir, fetcher=net)
    roads.SIGNALS_FN = roads.signals_provider(md)
    widths = None
    if not args.no_widths:
        widths = roadwidth.RoadWidths(
            lambda key: md.cell(key, layers=("buildings",), kind="demand").layers.get("buildings"),
            roadwidth.store_dir(args.cache_dir, md)).get
        if md.drcog() is not None:
            widths = roads.pavement_first(md.drcog().widths(None), widths)
    urban = None
    if not args.no_urban:
        import surface as surfacemod
        urban = surfacemod.Surface(md).urban
    e0, n0, _ = frame.geodetic_to_enu(np.array([la0]), np.array([lo0]), 0.0)
    cx, cy = float(e0[0]), float(n0[0])
    h = args.box / 2
    reach = h + navgraph.WINDOW_M * 0 + 200.0
    la, lo, _ = frame.enu_to_geodetic(np.array([cx - reach, cx + reach]), np.array([cy - reach, cy + reach]), 0.0)
    keys = mapcells.cells_for_bbox(float(la.min()), float(lo.min()), float(la.max()), float(lo.max()))
    feats = [md.cell(k, layers=("roads",), kind="demand").layers.get("roads") for k in keys]
    wids = roads.pocket_profiles(feats, urban, widths, False)

    # the roads the road tiles draw here, as their build_tile sees them (pocket nodes added)
    drawn, seen = [], set()
    print(f"roads within {args.box:.0f} m of {la0},{lo0} (frame x, y of the point: {cx:.1f}, {cy:.1f})")
    for f in feats:
        if f is None or len(f) == 0:
            continue
        specs = roads.road_specs(f, urban, widths, False)
        for i, spec in enumerate(specs):
            fid = str(f.ids[i])
            if spec is None or fid in seen:
                continue
            for ring in roads.ground_parts(f, i):
                e, n = mapgeom.enu_xy(f, frame, ring)
                if e.max() < cx - h or e.min() > cx + h or n.max() < cy - h or n.min() > cy + h:
                    continue
                seen.add(fid)
                wd = wids.get(fid)
                rs = None
                if wd is not None:
                    ring, rs = roads.widen_nodes(wd, ring, roads.widen_s(wd, ring))
                    e, n = mapgeom.enu_xy(f, frame, ring)
                p = np.stack([e, n], 1)
                edges = roads.widen_edges(wd, rs) if wd is not None else None
                drawn.append((fid, spec, p, edges))
                name = str(f.attrs["name"][i]) if "name" in f.attrs else ""
                dw = navgraph.driving_width(spec) if spec.cls in navgraph.DRIVE else None
                print(f"  {fid[:12]} {spec.cls:12s} {name!r:24s} oneway={spec.oneway} dir={spec.dir} "
                      f"lanes={spec.lanes} width={spec.width:.2f} drive_w={dw} median={roads.has_median(spec)} "
                      f"pts={len(p)} from ({p[0, 0]:.1f},{p[0, 1]:.1f}) to ({p[-1, 0]:.1f},{p[-1, 1]:.1f})")
                if edges is not None:
                    lft, rgt = np.asarray(edges[0]), np.asarray(edges[1])
                    print(f"      widened: left edge +{lft.min():.2f}..+{lft.max():.2f}, right +{rgt.min():.2f}.."
                          f"+{rgt.max():.2f} (m past width/2)")
                    for k in np.flatnonzero((np.abs(lft) > 0.05) | (np.abs(rgt) > 0.05))[:40]:
                        print(f"        ({p[k, 0]:.1f},{p[k, 1]:.1f}) left +{lft[k]:.2f} right +{rgt[k]:.2f}")

    # the ground: the DEM (no grading, which needs the server's road plans), as a frame height like the graph's
    dem = demmod.Dem(os.path.join(args.cache_dir, "dem"), fetcher=net)

    def elev(la_, lo_):
        return np.asarray(dem.sample(np.asarray(la_, np.float64), np.asarray(lo_, np.float64), args.zoom), np.float64)

    decks, deck_drawn = {}, []
    if not args.no_decks:
        bp = roads.BridgeProfiles(frame, lambda key: md.cell(key, layers=("roads",), kind="demand").layers.get("roads"),
                                  elev)
        print("\nbridge decks (roads.BridgeProfiles on the DEM):")
        for fid, spec, xy, z, _lon in bp.parts(feats, (cx - h, cy - h, cx + h, cy + h)):
            xy = np.asarray(xy, np.float64)
            decks.setdefault(str(fid), []).append((xy[:, :2], np.asarray(z, np.float64)))
            deck_drawn.append((str(fid), spec, xy[:, :2], None))
            print(f"  {str(fid)[:16]} {spec.cls} width={spec.width:.2f} oneway={spec.oneway} "
                  f"({xy[0, 0]:.1f},{xy[0, 1]:.1f}) -> ({xy[-1, 0]:.1f},{xy[-1, 1]:.1f}) "
                  f"z {float(np.min(z)):.2f}..{float(np.max(z)):.2f}")
    g = navgraph.build(frame, feats, elev, (cx, cy), urban, window=h, widths=widths, decks=decks)
    print(f"\nAI graph: {len(g['nodes'])} nodes, {len(g['edges'])} edges in the box (grade: the mod's tooSteep test, "
          f"cut over {MAX_GRADE})")
    for e in g["edges"]:
        a, b = g["nodes"][e[0]], g["nodes"][e[1]]
        run = max(math.hypot(a[0] - b[0], a[1] - b[1]), 1.0)
        grade = abs(a[2] - b[2]) / run
        print(f"  {e[0][:16]:16s} ({a[0]:7.1f},{a[1]:7.1f},{a[2]:7.2f}) -> {e[1][:16]:16s} ({b[0]:7.1f},{b[1]:7.1f},"
              f"{b[2]:7.2f}) oneway={e[2]} lanes={e[3]}/{e[4]} v={e[5]} radius={e[7]} grade={grade:.2f}"
              + ("  CUT" if grade > MAX_GRADE else ""))

    drawn.extend(deck_drawn)                  # (the decks are drawn too, at their own spec's width)

    # every AI lane's centre (the stock AI's lanes: '-' lanes left of the in -> out line, '+' right, each 2 r / n wide)
    # against the drawn carriageways: a car (~1 m half width) on a centre nearer an edge than that is off the pavement
    print(f"\nAI lane centres less than {args.margin} m inside the drawn pavement (every {args.step} m):")
    bad = 0
    for e in g["edges"]:
        a, b = np.array(g["nodes"][e[0]][:2]), np.array(g["nodes"][e[1]][:2])
        worst = None
        for k, q in lane_points(a, b, e[7], e[3] + e[4], args.step):
            m = pavement(q, drawn)[0]
            if worst is None or m[0] < worst[0][0]:
                worst = (m, q, k)
        if worst is not None and worst[0][0] < args.margin:
            bad += 1
            m, q, k = worst
            print(f"  {e[0][:14]}->{e[1][:14]} lane {k + 1}/{e[3] + e[4]} at ({q[0]:.1f},{q[1]:.1f}): {m[0]:+.2f} m "
                  f"inside {m[2].cls} {m[1][:12]} (half width {m[4]:.2f}, {m[3]:.2f} off its centre)")
    print(f"  {bad} of {len(g['edges'])} edges")

    track = args.track if args.track is not None else [args.point]
    print("\ntrack points: nearest drawn carriageway edge and AI edge")
    for tla, tlo in track:
        te, tn, _ = frame.geodetic_to_enu(np.array([tla]), np.array([tlo]), 0.0)
        p = np.array([float(te[0]), float(tn[0])])
        print(f"  {tla:.6f},{tlo:.6f} at ({p[0]:.1f},{p[1]:.1f})")
        rows = [(-r[0],) + r[1:] for r in pavement(p, drawn)]
        for inside, fid, spec, d, half, side in rows[:4]:
            print(f"    drawn {spec.cls:10s} {fid[:12]}: {d:.2f} m off its centre ({'left' if side > 0 else 'right'}),"
                  f" half width {half:.2f} -> {'INSIDE' if inside <= 0 else 'outside'} by {abs(inside):.2f}")
        best = []
        for e in g["edges"]:
            a, b = np.array(g["nodes"][e[0]][:2]), np.array(g["nodes"][e[1]][:2])
            d, _, t, side = seg_dist(p, np.stack([a, b]))
            best.append((d, e, side))
        for d, e, side in sorted(best, key=lambda r: r[0])[:3]:
            lanes = e[3] + e[4]
            lane_w = 2 * e[7] / max(lanes, 1)
            # the stock AI drives its lane's centre: right of the edge's line by (lanes_in + 0.5) lane widths less half
            right_c = -e[7] + (e[4] + 0.5) * lane_w if e[2] == 0 else None
            print(f"    AI edge {e[0][:14]}->{e[1][:14]}: {d:.2f} m off its line "
                  f"({'left' if side > 0 else 'right'}), half width {e[7]}, lanes {e[3]}/{e[4]}"
                  + (f", right lane centre {abs(right_c) if right_c is not None else 0:.2f} m right of it"
                     if right_c is not None else ""))


if __name__ == "__main__":
    main()
