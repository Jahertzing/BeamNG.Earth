"""Road grading by fixed regions instead of by anchor (2026-10-29; docs/research/2026-10-28-ground-pipeline-plan.html,
stage 1a).

A grading.Grader plans the roads of every map cell in its own ENU frame. Each anchor used to have its own, so every
anchor that saw a place planned its roads again (a 512² block near I-25 / 6th Ave: 8-9 s cold per anchor), and two
anchors could grade the same ground differently (anchors 2 and 3 of the Frederick route: up to 2.4 m at two spots, so
the ground could change under a car at a rebase). tools/world_ground_proto.py measured both.

RegionGrader is a drop-in for a Grader (apply, open_tunnels, planned, warm) that grades every point with the Grader of
the fixed region it lies in: regions are REGION_DEG x REGION_DEG squares of latitude and longitude, each with a Grader
in the ENU frame at its centre. Any anchor's blocks, road tiles and far tiles then read the same plans, made once per
place (and shared between processes and kept for later visits by plancache, which keys them by the Grader's frame:
now the region's). Within BAND_M of a region edge a point is graded by both regions and blended (bilinear at corners),
so an edge never shows as a step. The same block as before is within ~2 cm of an anchor's own grading near the region
centre (the frames' small tilt). BNE_REGION_GRADING=off brings back the Grader per anchor.
"""
import math
import os
import threading
from collections import OrderedDict

import numpy as np

import geodesy
import mapcells

ON = os.environ.get("BNE_REGION_GRADING", "on") != "off"
REGION_DEG = 0.1            # region edge (degrees of latitude and of longitude): ~11 x 8.5 km at 40° N
BAND_M = 40.0               # m across a region edge where the two regions' gradings are blended
MAX_REGIONS = 6             # Graders kept (least recently used dropped): a block touches 1-4, the lookahead a few more
M_PER_DEG = 111320.0


def region_of(lat, lon):
    """(i, j) of the region holding (lat, lon)."""
    return int(math.floor(lat / REGION_DEG)), int(math.floor(lon / REGION_DEG))


def region_center(i, j):
    return (i + 0.5) * REGION_DEG, (j + 0.5) * REGION_DEG


def _axis_weights(f, m_per_unit):
    """For coordinates f in region units (lat / REGION_DEG or lon / REGION_DEG): the own region index, the
    neighbour's (own - 1, own + 1 or own where there is no blend) and the own region's weight (0.5 at the edge,
    1 at BAND_M / 2 inside it)."""
    own = np.floor(f)
    u = (f - own) * m_per_unit             # m from the region's low edge
    span = m_per_unit
    half = BAND_M / 2
    w = np.ones_like(f)
    nb = own.copy()
    low = u < half
    high = (span - u) < half
    w[low] = 0.5 + u[low] / BAND_M
    nb[low] = own[low] - 1
    w[high] = 0.5 + (span - u[high]) / BAND_M
    nb[high] = own[high] + 1
    return own.astype(np.int64), nb.astype(np.int64), w


class _Stats:
    """The plan caches' counters of every region's Grader (blockworker's worker summary reads g.shared.stats)."""

    def __init__(self, rg):
        self._rg = rg

    @property
    def stats(self):
        out = {}
        for g in self._rg.graders():
            if g.shared is not None:
                for k, v in g.shared.stats.items():
                    out[k] = out.get(k, 0) + v
        return out


class RegionGrader:
    """grading.Grader's interface over fixed regions. make(frame) returns a configured Grader for a frame."""

    def __init__(self, make):
        self.make = make
        self._regions = OrderedDict()       # (i, j) -> Grader
        self._lock = threading.Lock()
        self.shared = _Stats(self)

    def graders(self):
        with self._lock:
            return list(self._regions.values())

    def grader(self, i, j):
        """The Grader of region (i, j), made at first use."""
        k = (int(i), int(j))
        with self._lock:
            g = self._regions.get(k)
            if g is not None:
                self._regions.move_to_end(k)
                return g
        g = self.make(geodesy.EnuFrame(*region_center(*k)))
        with self._lock:
            g = self._regions.setdefault(k, g)
            self._regions.move_to_end(k)
            while len(self._regions) > MAX_REGIONS:
                self._regions.popitem(last=False)
            return g

    def _weights(self, lat, lon):
        """Up to four (region i, region j, weight) per point: arrays ri (n, 4), rj (n, 4), w (n, 4)."""
        mlat = REGION_DEG * M_PER_DEG
        mlon = REGION_DEG * M_PER_DEG * max(0.05, math.cos(math.radians(float(np.mean(lat)))))
        oi, ni, wi = _axis_weights(lat / REGION_DEG, mlat)
        oj, nj, wj = _axis_weights(lon / REGION_DEG, mlon)
        ri = np.stack([oi, oi, ni, ni], 1)
        rj = np.stack([oj, nj, oj, nj], 1)
        w = np.stack([wi * wj, wi * (1 - wj), (1 - wi) * wj, (1 - wi) * (1 - wj)], 1)
        return ri, rj, w

    def apply(self, lat, lon, base, lift=0.0, roads_only=False, planned_only=False):
        """grading.Grader.apply: base graded under the roads, each point by its region (blended near the edges)."""
        lat = np.asarray(lat, np.float64)
        lon = np.asarray(lon, np.float64)
        base = np.asarray(base, np.float64)
        shape = base.shape
        if base.size == 0:
            return base
        la, lo, b = lat.ravel(), lon.ravel(), base.ravel()
        ri, rj, w = self._weights(la, lo)
        out = np.zeros(len(b))
        used = w > 0
        for i, j in sorted(set(zip(ri[used].tolist(), rj[used].tolist()))):
            sel = (ri == i) & (rj == j) & used         # (n, 4): this region's slots
            pts = sel.any(1)
            wt = (w * sel).sum(1)[pts]
            z = self.grader(i, j).apply(la[pts], lo[pts], b[pts], lift, roads_only, planned_only)
            out[pts] += wt * np.asarray(z, np.float64).ravel()
        return out.reshape(shape)

    def open_tunnels(self, lat, lon):
        """grading.Grader.open_tunnels over the regions the points touch, each section once."""
        lat = np.asarray(lat, np.float64).ravel()
        lon = np.asarray(lon, np.float64).ravel()
        if lat.size == 0:
            return []
        ri, rj, w = self._weights(lat, lon)
        out, seen = [], set()
        for i, j in sorted(set(zip(ri[w > 0].tolist(), rj[w > 0].tolist()))):
            pts = ((ri == i) & (rj == j) & (w > 0)).any(1)
            for la, lo, spec in self.grader(i, j).open_tunnels(lat[pts], lon[pts]):
                k = (round(float(la[0]), 6), round(float(lo[0]), 6), round(float(la[-1]), 6),
                     round(float(lo[-1]), 6), len(la))
                if k not in seen:
                    seen.add(k)
                    out.append((la, lo, spec))
        return out

    def _cell_regions(self, key):
        """The regions a map cell's roads are graded in: those its box (+ the grading's reach) touches."""
        import grading as gradingmod
        lon0, lat0, lon1, lat1 = mapcells.CellKey(*key).bounds()
        pad = (gradingmod.T_MAX + 40.0 + BAND_M) / M_PER_DEG
        padlon = pad / max(0.05, math.cos(math.radians((lat0 + lat1) / 2)))
        i0, j0 = region_of(lat0 - pad, lon0 - padlon)
        i1, j1 = region_of(lat1 + pad, lon1 + padlon)
        return [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]

    def planned(self, key):
        return all(self.grader(i, j).planned(key) for i, j in self._cell_regions(key))

    def warm(self, key):
        for i, j in self._cell_regions(key):
            self.grader(i, j).warm(key)
