"""Bridge ends solved with their roads as one line (2026-10-29; docs/research/2026-10-28-ground-pipeline-plan.html,
stage 2).

tools/ride_probe.py measured the vertical acceleration a car at 65 mph feels over I-25's interchanges: p99 0.12-0.33 g
along the roads, but 0.5-1.5 g across the joints where a deck meets its road (the "short ramps" at bridge ends). The
deck (roads.BridgeProfiles) and the road leading onto it (the grading) are solved apart and meet at one height but at
two grades, often a step of 0.1-0.2 m apart. Solved as one line, each joint came to ~0.05 g, moving the surface 1.2 m
at most. Easing each side alone toward a shared grade made them worse (feature/joint-ease, not merged).

A correction layer on top of the grading, as Unreal stacks a landscape's edit layers:
  1. decks are solved on the graded ground as before (BridgeProfiles reads it through raw(): no corrections);
  2. at every abutment a ground road goes on from, the deck's first JOINT_M and the road's last JOINT_M are solved
     as one line (joint_solve: the least change that keeps the curvature a car at the class's speed feels under
     LIMIT_G, the line held from JOINT_M out on both sides);
  3. the deck's half goes into the deck (BridgeProfiles), the road's half into the ground under the road's core
     (JointLayer.delta, added by regiongrade.RegionGrader.apply): every consumer of the graded ground (the block, the
     road meshes, the near far tiles) sees it, and nothing the decks were solved from changes, so nothing is circular.
Every process solves the same joints from the same data, so the halves meet.

BNE_JOINT_SOLVE=on switches it on (off by default until the in-game check).
"""
import contextlib
import math
import os
import threading

import numpy as np

ON = os.environ.get("BNE_JOINT_SOLVE", "off") == "on"
JOINT_M = 30.0                  # m solved on each side of a joint
STEP_M = 1.0
LIMIT_G = 0.05                  # vertical acceleration aimed at (a 70 mph crest by the book: ~0.012 g)
SPEED_MS = {"motorway": 29.0, "trunk": 27.0, "primary": 22.0, "secondary": 18.0}   # the speed each class is solved for
DEFAULT_SPEED_MS = 15.0
TAPER_M = 5.0                   # m past the road's core over which its correction fades out
END_M = 1.5                     # m: a ground road's end this near a deck's end is the road onto it
G = 9.81
ITERS = 12

_tl = threading.local()


@contextlib.contextmanager
def raw():
    """The graded ground without the joint corrections while inside (BridgeProfiles solves its decks on it)."""
    old = getattr(_tl, "raw", False)
    _tl.raw = True
    try:
        yield
    finally:
        _tl.raw = old


def is_raw():
    return getattr(_tl, "raw", False)


def speed_of(cls):
    return SPEED_MS.get(cls, DEFAULT_SPEED_MS)


def _solve_penta(w, lam, b):
    """x minimising sum w (x - b / w)² + sum lam_i (x[i] - 2 x[i+1] + x[i+2])²: (W + D2' L D2) x = b, a symmetric
    pentadiagonal system, by banded Cholesky (a joint: ~60 samples)."""
    n = len(w)
    d0 = np.asarray(w, np.float64).copy()
    d1 = np.zeros(n)
    d2 = np.zeros(n)
    for i in range(n - 2):              # D2 row i = [1, -2, 1] at i, i+1, i+2
        lv = lam[i]
        d0[i] += lv
        d0[i + 1] += 4 * lv
        d0[i + 2] += lv
        d1[i] -= 2 * lv
        d1[i + 1] -= 2 * lv
        d2[i] += lv
    a, p, q = np.zeros(n), np.zeros(n), np.zeros(n)     # L[i, i], L[i, i-1], L[i, i-2]
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


def limited(z, step, speed, held, limit_g=LIMIT_G, iters=ITERS):
    """z (every step m) moved as little as it can so that a car at speed feels at most limit_g of its curvature;
    held: {index: height} kept. Reweighted least squares: a light curvature penalty everywhere, raised 8x per pass
    (with 4 neighbours) wherever it is still over."""
    z = np.asarray(z, np.float64)
    n = len(z)
    if n < 5:
        return z.copy()
    cmax = limit_g * G * step * step / (speed * speed)      # the largest z[i] - 2 z[i+1] + z[i+2] allowed
    w = np.ones(n)
    tgt = z.copy()
    for i, v in held.items():
        if 0 <= i < n:
            w[i], tgt[i] = 1e6, v
    lam = np.ones(n - 2)
    x = tgt
    for _ in range(iters):
        x = _solve_penta(w, lam, w * tgt)
        over = np.abs(np.diff(x, 2)) > cmax
        if not over.any():
            break
        grow = over.copy()
        for d in range(1, 5):
            grow[d:] |= over[:-d]
            grow[:-d] |= over[d:]
        lam[grow] *= 8.0
    return x


def _arc(xy):
    return np.r_[0.0, np.cumsum(np.hypot(*np.diff(np.asarray(xy, np.float64)[:, :2], axis=0).T))]


def _along(xy, s_from_end, at_end):
    """Points of polyline xy at the distances s_from_end (m) from its end (at_end) or start, measured along it."""
    xy = np.asarray(xy, np.float64)[:, :2]
    s = _arc(xy)
    t = s[-1] - s_from_end if at_end else s_from_end
    t = np.clip(t, 0.0, s[-1])
    return np.stack([np.interp(t, s, xy[:, 0]), np.interp(t, s, xy[:, 1])], 1)


def joint_solve(approach_z, deck_z, speed, step=STEP_M, joint_m=JOINT_M):
    """The road's profile toward a joint (approach_z, every step m, the joint last) and the deck's away from it
    (deck_z, the joint first) solved as one line: (new approach_z, new deck_z). The samples joint_m or more from the
    joint keep their heights (a deck shorter than that: its far end); the joint itself may move (the two met at
    two heights: their mean, smoothed)."""
    za = np.asarray(approach_z, np.float64)
    zd = np.asarray(deck_z, np.float64)
    na, nd = len(za), len(zd)
    if na < 3 or nd < 3:
        return za.copy(), zd.copy()
    joint = 0.5 * (za[-1] + zd[0])
    p = np.r_[za[:-1], joint, zd[1:]]
    k = int(round(joint_m / step))
    held = {i: p[i] for i in range(0, max(1, na - 1 - k) + 1)}
    j0 = na - 1
    for i in range(j0 + min(k, nd - 1), len(p)):
        held[i] = p[i]
    out = limited(p, step, speed, held)
    return out[:na], out[j0:]


class JointLayer:
    """The road halves of the joint solves (BridgeProfiles.approach) put into the roads' grading plans: the layer is a
    grading.Grader's plan_adjust (called with the grader and its plan set; version() keys the remembered sets), adding each correction to the plan of its own road (by its id) along its centre
    line, so grade_points blends it with the roads around as any plan (a height added round the line touched the
    roads beside and under it: a 5.75 g spike on US-6 at the Mousetrap). bp: a roads.BridgeProfiles on the graded
    ground (read through raw()), cell_fn: the roads Features of a map cell."""

    NEAR_M = 2.5        # m: a plan vertex this near a correction's line takes it

    def __init__(self, bp, cell_fn):
        self.bp, self.cell_fn = bp, cell_fn
        self._n = 0

    def version(self):
        """Changes when corrections are added (the graders' plan sets are remembered by it); -1 inside raw()."""
        if is_raw():
            return -1
        with self.bp.lock:
            return len(self.bp.approach)

    def ensure(self, lat, lon):
        """The chains round the points solved (their joints' corrections made)."""
        import mapcells
        lat = np.asarray(lat, np.float64).ravel()
        lon = np.asarray(lon, np.float64).ravel()
        if not len(lat):
            return
        f = self.bp.frame
        x, y, _ = f.geodetic_to_enu(lat, lon, 0.0)
        x, y = np.asarray(x), np.asarray(y)
        pad = (2 * JOINT_M + 50.0) / 111320.0
        keys = mapcells.cells_for_points(lat, lon, pad, pad / max(0.05, math.cos(math.radians(float(lat.mean())))))
        box = (float(x.min()) - 2 * JOINT_M, float(y.min()) - 2 * JOINT_M, float(x.max()) + 2 * JOINT_M,
               float(y.max()) + 2 * JOINT_M)
        with raw():
            self.bp.parts([self.cell_fn(k) for k in keys], box)

    def __call__(self, grader, plans):
        return self.adjust(grader, plans)

    def adjust(self, grader, plans):
        """plans with the corrections of their roads added along them (a copy of each plan changed)."""
        if is_raw():
            return plans
        import grading
        with self.bp.lock:
            corr = list(self.bp.approach.values())
        if not corr:
            return plans
        by_rid = {}
        for xa, dz, _half, fid in corr:
            by_rid.setdefault(grading.road_id(fid), []).append((xa, dz))
        out = []
        src, dst = grader.frame, self.bp.frame
        for p in plans:
            cs = by_rid.get(getattr(p, "rid", -1))
            if not cs:
                out.append(p)
                continue
            la, lo, _ = src.enu_to_geodetic(p.xy[:, 0], p.xy[:, 1], 0.0)
            e, n, _ = dst.geodetic_to_enu(np.asarray(la), np.asarray(lo), 0.0)
            pts = np.stack([np.asarray(e), np.asarray(n)], 1)
            add = np.zeros(len(pts))
            for xa, dz in cs:
                a, b = xa[:-1], xa[1:]
                ab = b - a
                L2 = np.maximum((ab * ab).sum(1), 1e-12)
                t = np.clip(((pts[:, None, :] - a[None]) * ab[None]).sum(2) / L2[None], 0.0, 1.0)
                proj = a[None] + t[..., None] * ab[None]
                d = np.hypot(*(pts[:, None, :] - proj).transpose(2, 0, 1))
                seg = d.argmin(1)
                ok = d[np.arange(len(pts)), seg] <= self.NEAR_M
                tt = t[np.arange(len(pts)), seg]
                add += np.where(ok, dz[:-1][seg] * (1 - tt) + dz[1:][seg] * tt, 0.0)
            if not add.any():
                out.append(p)
                continue
            q = grading.Plan(p.xy, np.asarray(p.z, np.float64) + add, p.half, p.abut, p.cut, p.road_half)
            q.rid, q.max_wall, q.bank = p.rid, p.max_wall, p.bank
            out.append(q)
        return out
