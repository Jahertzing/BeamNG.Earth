"""Physically plausible ground from the lidar's bare earth (2026-10-29; the "Ground Pipeline Plan", section 10).

Jeff, after a drive: "overall ground is incredibly bumpy in some areas, like perhaps there's room for us to interpret
the data less literally, and instead do what makes actual physical sense." 3DEP's bare earth at ~0.5-1 m keeps what
a car should never meet as terrain: single-pixel spikes and pits (missed vegetation, vehicles, returns off water),
crop furrows and ruts, the rough edges of construction, every pebble of relief a smoother survey would not see. The
road grading already designs the road surfaces; this makes the ground around them plausible too:

  1. spikes and pits out: a pixel more than SPIKE_M off its 3x3 median takes the median;
  2. the micro relief damped: the ground is a smooth surface (a Gaussian of SIGMA_M, three box passes) plus what is
     left of the relief under it, KEEP of it on gentle ground, all of it on slopes over STEEP_FULL_DEG (embankments,
     road cuts, cliffs, rock: real breaklines keep their edges), blended between STEEP_FROM_DEG and that.

Landforms wider than a few SIGMA_M are untouched; the photo and the terrain materials' detail maps carry the texture
the heights lose. dem.Dem applies it to its lidar tiles (zoom PLAUSIBLE_MIN_ZOOM and deeper) with MARGIN_PX of the
neighbouring tiles round each, so tiles agree at their seams. On by default (Jeff asked for it); BNE_PLAUSIBLE=off
switches it off for an A/B. Its tiles are mirrored apart (dem's mirror path names it).

Measured offline (tools/plausible_probe.py, six Front Range places, z16 lidar, 2026-10-30): the vertical g a car feels
at 30 mph on the 2 m block lattice over gentle ground, p99, went from 0.2-2.3 g to 0.07-0.8 g (Frederick's fields
0.21 -> 0.07, Erie 2.27 -> 0.82, Castle Rock 1.98 -> 0.76); the ground moved 2-12 cm on average.
"""
import math
import os

import numpy as np

ON = os.environ.get("BNE_PLAUSIBLE", "on") != "off"
VERSION = 2                 # (the decoded mirror's path: a change to the filter is a new mirror)
MIN_ZOOM = 14               # lidar tiles this deep and deeper (pixels <= ~2 m)
SPIKE_M = 0.4               # m off the 3x3 median: a spike or a pit
SIGMA_M = 4.0               # m: the smooth surface's Gaussian
KEEP = 0.15                 # share of the relief under SIGMA_M kept on gentle ground
STEEP_FROM_DEG = 22.0       # slopes from this ...
STEEP_FULL_DEG = 32.0       # ... to this keep all of it (a 1:2 fill is 26.6 degrees)
MARGIN_PX = 32              # px of the neighbours each tile is filtered with


def _box(a, r, axis):
    """Mean over 2r + 1 samples along axis (edges held), by cumulative sums."""
    if r <= 0:
        return a
    pad = [(0, 0), (0, 0)]
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(a.dtype)


def blur(a, sigma_px):
    """Gaussian blur of a (three box passes per axis: within a few % of a Gaussian of sigma_px)."""
    w = math.sqrt(4.0 * sigma_px * sigma_px + 1.0)
    r = max(0, int(round((w - 1.0) / 2.0)))
    for _ in range(3):
        a = _box(_box(a, r, 0), r, 1)
    return a


def median3(a):
    """The 3x3 median of a (edges held)."""
    p = np.pad(a, 1, mode="edge")
    n, m = a.shape
    stack = np.stack([p[i:i + n, j:j + m] for i in range(3) for j in range(3)])
    return np.partition(stack, 4, axis=0)[4].astype(a.dtype)       # (the 5th of 9: np.median's sort, ~3x faster)


def plausible(a, px_m):
    """a (elevations [rows, cols], m; pixels px_m apart) made plausible ground (the module's steps). Exact 0 (the sea,
    lidar's no-data) stays 0 and is left out of the smoothing around it. Pure."""
    a = np.asarray(a, np.float32)
    sea = a == 0.0
    med = median3(a)
    b = np.where(np.abs(a - med) > SPIKE_M, med, a)
    b = np.where(sea, a, b)
    if sea.any():                       # (the sea's 0 never pulls the shore down: smoothed with the shore's own)
        w = blur((~sea).astype(np.float32), SIGMA_M / px_m)
        g = blur(np.where(sea, 0.0, b).astype(np.float32), SIGMA_M / px_m) / np.maximum(w, 1e-3)
    else:
        g = blur(b, SIGMA_M / px_m)
    gy, gx = np.gradient(g, px_m)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    steep = np.clip((slope - STEEP_FROM_DEG) / (STEEP_FULL_DEG - STEEP_FROM_DEG), 0.0, 1.0)
    keep = KEEP + (1.0 - KEEP) * steep
    out = g + (b - g) * keep
    return np.where(sea, a, out).astype(np.float32)
