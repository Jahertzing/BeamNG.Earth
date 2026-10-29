#!/usr/bin/env python3
"""How a car rides the motorways of I-25's interchanges, offline from the cached data (2026-10-29): the drivable
surface along every motorway part (the graded ground, lifted as the physics block is, and the solved bridge decks,
as tools/interchange_probe.py solves them), and the vertical acceleration a car at SPEED_MS feels over it.

  a(s) = v² · (z(s + h) - 2 z(s) + z(s - h)) / h²       h = BASE_M (about a wheelbase), in g

Reported per place: the share of metres over 0.1 g / 0.3 g / 0.6 g, the worst spikes, and the same over each joint
between a deck and the road it lands on (where the short ramps were), along the 60 m around it.

--smooth applies a vertical-curve smoother to every line first (the profile solver's prototype, solver()):
the least change of height that keeps |a| under the class's limit, with the deck ends and junctions where they are;
it reports how far it had to move the surface.

    python tools/ride_probe.py [--only mousetrap_i70] [--smooth]
"""
import argparse
import math
import os
import sys
import time

os.environ.setdefault("BNE_REVISIT", "off")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import numpy as np  # noqa: E402

from interchange_probe import PLACES as INTERCHANGES  # noqa: E402

# the interchanges, and single deck-end joints the stage 2 solver is checked on (the bridges thread's I-25 ride
# survey, 2026-10-29: /mnt/project-files/ride-survey/i25_cloud_2026-10-29.txt)
PLACES = dict(INTERCHANGES)
PLACES.update({
    "i25_20th_st": (39.76083, -105.00413, 300),       # 56 g: an 11 m motorway stub (3a02ea23) between decks, its
                                                       # middle 7 m spiked up by the overlapping approach fills
    "i25_deck_ff3f9f6e": (39.74145, -105.01395, 300),  # 18 g at deck ff3f9f6e's end
})

SPEED_MS = 29.0             # 65 mph
BASE_M = 3.0
STEP_M = 1.0
G = 9.81
LIMIT_G = {"motorway": 0.05, "trunk": 0.08}     # the smoother's target: a 70 mph crest's K of ~75 m per % is ~0.012 g
JOINT_M = 30.0
DECK_ONLY = False           # --deck-only: across a joint only the deck moves (the ground road as graded)


def accel(z, step=STEP_M, h=BASE_M, v=SPEED_MS):
    """Vertical acceleration (g) along a profile sampled every step m (NaN within h of the ends)."""
    k = max(1, int(round(h / step)))
    out = np.full(len(z), np.nan)
    if len(z) > 2 * k:
        hh = k * step
        out[k:-k] = v * v * (z[2 * k:] - 2 * z[k:-k] + z[:-2 * k]) / (hh * hh) / G
    return out


def resample_line(xy, z, step=STEP_M):
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy[:, :2], axis=0).T))]
    if s[-1] < 2 * step:
        return None, None
    t = np.arange(0.0, s[-1], step)
    return np.stack([np.interp(t, s, xy[:, 0]), np.interp(t, s, xy[:, 1])], 1), np.interp(t, s, z)


def _solve_penta(w, lam, b):
    """x minimising sum w (x - b/w)² + sum lam_i (x[i] - 2 x[i+1] + x[i+2])²: the symmetric pentadiagonal system
    (W + D2' L D2) x = b solved by banded Cholesky (n small: a road part)."""
    n = len(w)
    d0 = w.astype(np.float64).copy()
    d1 = np.zeros(n)
    d2 = np.zeros(n)
    for i in range(n - 2):          # D2 row i = [1, -2, 1] at i, i+1, i+2
        l_ = lam[i]
        d0[i] += l_
        d0[i + 1] += 4 * l_
        d0[i + 2] += l_
        d1[i] += -2 * l_
        d1[i + 1] += -2 * l_
        d2[i] += l_
    # banded Cholesky A = L L': L[i, i] = a[i], L[i, i-1] = p[i], L[i, i-2] = q[i]
    a = np.zeros(n)
    p = np.zeros(n)
    q = np.zeros(n)
    for i in range(n):
        if i >= 2:
            q[i] = d2[i - 2] / a[i - 2]
        if i >= 1:
            p[i] = (d1[i - 1] - (q[i] * p[i - 1] if i >= 2 else 0.0)) / a[i - 1]
        a[i] = math.sqrt(max(d0[i] - p[i] * p[i] - q[i] * q[i], 1e-12))
    y = np.zeros(n)
    for i in range(n):
        y[i] = (b[i] - (p[i] * y[i - 1] if i >= 1 else 0.0) - (q[i] * y[i - 2] if i >= 2 else 0.0)) / a[i]
    x = np.zeros(n)
    for i in range(n - 1, -1, -1):
        x[i] = (y[i] - (p[i + 1] * x[i + 1] if i + 1 < n else 0.0) - (q[i + 2] * x[i + 2] if i + 2 < n else 0.0)) / a[i]
    return x


def solver(z, limit_g, pins=(), step=STEP_M, v=SPEED_MS, iters=12):
    """The profile solver's prototype: z moved as little as it can (least squares) so that the curvature a car at v
    feels stays under limit_g, the samples at pins (indices: deck ends, junctions) held. Reweighted smoothing: a
    light curvature penalty everywhere, raised 8x per pass wherever the curvature is still over the limit (IRLS),
    so only the bumps move. Returns the new profile."""
    z0 = np.asarray(z, np.float64)
    n = len(z0)
    if n < 5:
        return z0.copy()
    cmax = limit_g * G * step * step / (v * v)      # the largest allowed z[i] - 2 z[i+1] + z[i+2]
    w = np.ones(n)
    pins = [int(p) for p in pins if 0 <= p < n]
    w[pins] = 1e6
    lam = np.full(n - 2, 1.0)
    x = z0.copy()
    for _ in range(iters):
        x = _solve_penta(w, lam, w * z0)
        d2 = np.abs(np.diff(x, 2))
        over = d2 > cmax
        if not over.any():
            break
        grow = over.copy()                  # (and their neighbours)
        for d in range(1, 5):
            grow[d:] |= over[:-d]
            grow[:-d] |= over[d:]
        lam[grow] *= 8.0
    return x


def lines_of(name, la, lo, radius, cache_dir):
    """[(label, kind 'ground' | 'deck', cls, xy (n, 2), z (n,))]: the motorway parts' drivable surface round a place."""
    import geodesy
    import grading
    import mapcells
    import mapdata
    import mapgeom
    import netfetch
    import roads
    import dem as demmod
    import decklidar
    net = netfetch.Fetcher(offline=True)
    md = mapdata.MapData(cache_dir, fetcher=net)
    dem = demmod.Dem(os.path.join(cache_dir, "dem"), fetcher=net)
    frame = geodesy.EnuFrame(la, lo)
    sample = lambda a, b: dem.sample(a, b, grading.PROFILE_ZOOM)  # noqa: E731
    import profilesolve
    import regiongrade

    def make(fr):
        g = grading.Grader(fr, md, sample)
        g.surface = decklidar.surface_for(md, dem)
        return g
    # with BNE_JOINT_SOLVE=on the servers' grading (region graders, the bridge ends' correction layer); else one
    # Grader in the place's frame as before
    grader = regiongrade.RegionGrader(make) if profilesolve.ON else make(frame)

    def elev(a, b):
        return grader.apply(a, b, sample(a, b), grading.ROAD_LIFT)
    cell_fn = lambda k: md.cell(k, layers=("roads",), kind="demand").layers.get("roads")  # noqa: E731
    bp = roads.BridgeProfiles(frame, cell_fn, elev)
    bp.surface = decklidar.surface_for(md, dem)
    box = (-radius, -radius, radius, radius)
    pla, plo, _ = frame.enu_to_geodetic(np.array([-radius, radius, -radius, radius]),
                                        np.array([-radius, -radius, radius, radius]), 0.0)
    keys = mapcells.cells_for_points(pla, plo)
    cells = [cell_fn(k) for k in keys]
    out, seen = [], set()
    for fid, spec, xy, zz, _ in bp.parts(cells, box):
        if spec.cls not in LIMIT_G:
            continue
        key = (fid, len(xy), round(float(xy[0][0]), 1))
        if key in seen:
            continue
        seen.add(key)
        out.append((f"deck {str(fid)[:8]}", "deck", spec.cls, np.asarray(xy)[:, :2], np.asarray(zz, np.float64)))
    for feats in cells:
        if feats is None:
            continue
        rs = roads.road_specs(feats)
        for i in range(len(feats)):
            if rs[i] is None or rs[i].cls not in LIMIT_G:
                continue
            for ring in roads.ground_parts(feats, i):
                e, n = mapgeom.enu_xy(feats, frame, ring)
                if not ((np.abs(e) < radius).any() and (np.abs(n) < radius).any()):
                    continue
                xy = np.stack([e, n], 1)
                key = (str(feats.ids[i]), len(xy), round(float(xy[0][0]), 1))
                if key in seen:
                    continue
                seen.add(key)
                rxy, _ = resample_line(xy, np.zeros(len(xy)))
                if rxy is None:
                    continue
                a, b, _ = frame.enu_to_geodetic(rxy[:, 0], rxy[:, 1], 0.0)
                # the drivable surface: the graded ground in the frame's z (surface point; heightmap.surface's rule)
                h = elev(np.asarray(a), np.asarray(b))
                u = frame.geodetic_to_enu(np.asarray(a), np.asarray(b), h)[2]
                out.append((f"ground {str(feats.ids[i])[:8]} {str(feats.attrs['name'][i])[:20]}", "ground",
                            rs[i].cls, rxy, np.asarray(u, np.float64)))
    return out


def joints(lines):
    """[(deck index, ground index, deck end 0|-1, ground end 0|-1)] where a deck's end meets a ground part's end."""
    out = []
    for i, (_, kind, _, xy, _) in enumerate(lines):
        if kind != "deck":
            continue
        for de in (0, -1):
            p = xy[de]
            for j, (_, k2, _, xy2, _) in enumerate(lines):
                if k2 != "ground":
                    continue
                for ge in (0, -1):
                    if np.hypot(*(xy2[ge] - p)) < 1.5:
                        out.append((i, j, de, ge))
    return out


def joint_profile(lines, jt, zs):
    """The 2·JOINT_M of surface across a joint, ground then deck, every STEP_M."""
    i, j, de, ge = jt
    _, _, _, dxy, _ = lines[i]
    _, _, _, gxy, _ = lines[j]
    dz, gz = zs[i], zs[j]
    dxy_r, dz_r = resample_line(dxy, dz)
    if dxy_r is None:
        return None
    n = int(JOINT_M / STEP_M)
    g = gz if ge == -1 else gz[::-1]            # ground towards the joint
    d = dz_r if de == 0 else dz_r[::-1]         # deck away from it
    return np.r_[g[-n:], d[1:n]]


def stats(a):
    a = np.abs(a[np.isfinite(a)])
    if not len(a):
        return None
    return {"m": len(a), ">0.1g": float((a > 0.1).mean()), ">0.3g": float((a > 0.3).mean()),
            ">0.6g": float((a > 0.6).mean()), "max": float(a.max()), "p99": float(np.percentile(a, 99))}


def fmt(st):
    if st is None:
        return "-"
    return (f"{st['m']:6d} m  >0.1g {st['>0.1g'] * 100:5.1f} %  >0.3g {st['>0.3g'] * 100:5.2f} %  "
            f">0.6g {st['>0.6g'] * 100:5.2f} %  p99 {st['p99']:.2f} g  max {st['max']:.2f} g")


def run(name, la, lo, radius, cache_dir, smooth):
    t0 = time.time()
    lines = lines_of(name, la, lo, radius, cache_dir)
    js = joints(lines)
    zs = [ln[4] for ln in lines]
    res = {}
    for label, zz in [("today", zs)] + ([("smoothed", None)] if smooth else []):
        if zz is None:          # the solver: every line held at its joints' ends
            pins = {i: set() for i in range(len(lines))}
            for i, j, de, ge in js:
                pins[i].add(0 if de == 0 else len(lines[i][4]) - 1)
                pins[j].add(0 if ge == 0 else len(lines[j][4]) - 1)
            zz, moved = [], []
            for k, ln in enumerate(lines):
                if ln[1] == "deck":
                    xy_r, z_r = resample_line(ln[3], ln[4])
                    if z_r is None:
                        zz.append(ln[4])
                        continue
                    new = solver(z_r, LIMIT_G[ln[2]], pins=(0, len(z_r) - 1))
                    moved.append(np.abs(new - z_r).max())
                    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(ln[3], axis=0).T))]
                    zz.append(np.interp(s, np.arange(len(new)) * STEP_M, new))
                else:
                    new = solver(ln[4], LIMIT_G[ln[2]], pins=pins[k])
                    moved.append(np.abs(new - ln[4]).max())
                    zz.append(new)
            res["moved"] = (float(np.median(moved)) if moved else 0.0, float(np.max(moved)) if moved else 0.0)
        along = []
        for k, ln in enumerate(lines):
            if ln[1] == "deck":
                _, z_r = resample_line(ln[3], zz[k])
                along.append(accel(z_r) if z_r is not None else np.full(0, np.nan))     # (one per line: worst's index)
            else:
                along.append(accel(zz[k]))
        if label == "today":        # where the worst spikes along the parts are
            import geodesy
            frame = geodesy.EnuFrame(la, lo)
            worst = []
            for k, a in enumerate(along):
                if len(a) and np.isfinite(a).any() and np.nanmax(np.abs(a)) > 0.6:
                    i = int(np.nanargmax(np.abs(a)))
                    xy = lines[k][3] if lines[k][1] == "ground" else resample_line(lines[k][3], lines[k][4])[0]
                    p_la, p_lo, _ = frame.enu_to_geodetic(xy[i, 0], xy[i, 1], 0.0)
                    worst.append((float(np.nanmax(np.abs(a))), lines[k][0], float(p_la), float(p_lo)))
            res["worst"] = sorted(worst, reverse=True)[:5]
        jp = [p for p in (joint_profile(lines, jt, zs) for jt in js) if p is not None]
        if label == "smoothed":     # the chain solved across the joint (both sides move; the far ends held)
            n = int(JOINT_M / STEP_M)       # DECK_ONLY: the ground half held, only the deck eases into it
            jp_new = [solver(p, LIMIT_G["motorway"], pins=(tuple(range(n)) if DECK_ONLY else (0,)) + (len(p) - 1,))
                      for p in jp]
            res["joint_moved"] = float(max((np.abs(a - b).max() for a, b in zip(jp, jp_new)), default=0.0))
            jp = jp_new
        across = [accel(p) for p in jp]
        res[label] = (stats(np.concatenate(along)) if any(len(a) for a in along) else None,
                      stats(np.concatenate(across)) if across else None)
    print(f"{name}: {sum(1 for ln in lines if ln[1] == 'ground')} ground parts, "
          f"{sum(1 for ln in lines if ln[1] == 'deck')} decks, {len(js)} deck ends on a road ({time.time() - t0:.0f} s)")
    for label in ("today", "smoothed"):
        if label in res:
            print(f"  {label:8s} along  {fmt(res[label][0])}")
            print(f"  {'':8s} joints {fmt(res[label][1])}")
    for g_, lab, p_la, p_lo in res.get("worst", []):
        print(f"    worst along: {g_:5.2f} g  {lab}  at {p_la:.6f},{p_lo:.6f}")
    if "moved" in res:
        print(f"  the smoother moved the surface by median {res['moved'][0]:.2f} m, at most {res['moved'][1]:.2f} m "
              f"(across the joints at most {res.get('joint_moved', 0.0):.2f} m)")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default=None)
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/beamng-earth"))
    ap.add_argument("--smooth", action="store_true")
    ap.add_argument("--deck-only", action="store_true", help="across the joints only the decks move")
    a = ap.parse_args()
    global DECK_ONLY
    DECK_ONLY = a.deck_only
    import apppaths
    import cache
    cache.set_world(apppaths.saved_world_dir() or apppaths.default_world_dir(), a.cache_dir)
    for name, (la, lo, r) in PLACES.items():
        if a.only and name not in a.only.split(","):
            continue
        run(name, la, lo, r, a.cache_dir, a.smooth)


if __name__ == "__main__":
    main()
