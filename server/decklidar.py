"""Bridge decks at the heights USGS lidar measured them (2026-10-28, Denver's Mousetrap, I-25 / I-70: four levels of
ramps). The map only orders stacked roads (OpenStreetMap's layer: 1 over 0, 2 over 1); roads.deck_profile makes their
heights from the abutments, the clearance over what each deck crosses and the grades a deck may climb. Where a long
viaduct meets a ramp near its far abutment that cannot hold (I-70 east of I-25 came 4.4 m over the ramp under it, its
last 20 m at 15 %), and every level is stacked a full DECK_ROAD_CLEAR (8.35 m) over the one below where life has
~7 m.

3DEP's lidar saw the decks: its surface model (Dsm, the Planetary Computer's 3dep-lidar-dsm, the highest return in
each 2 m pixel, per map cell as lidarheights keeps the buildings' heights) is the deck's surface where nothing stands
over it. (Its height above ground, which the buildings read, will not do: its ground filter took I-70's long viaduct
for ground and read 0 m over it.) Along each deck part, every DECK_STEP point: the median of three samples across its
middle half (a centre line a few metres off the real deck), then spikes off a running median dropped (trucks, sign
gantries, light poles), and points under a deck of a higher level dropped (the lidar sees the top one), or crossing
another of the same level (which is on top unknown).

roads.deck_profile takes what is left as each part's profile (gaps filled, its abutments kept at the graded ground)
and as floor and cap within OBS_TOL_M, and asks the measured parts only LIDAR_ROAD_CLEAR over what they cross: the
structure as built. The rest of its rules (grades, vertical curves, deck over deck) hold as before. Outside 3DEP's
coverage, for parts it saw too little of (MIN_SHARE) and with BNE_DECK_LIDAR=off: the decks as before.
"""
import logging
import math
import os

import numpy as np

log = logging.getLogger("earthd.decklidar")

ON = os.environ.get("BNE_DECK_LIDAR", "on") != "off"
VERSION = 1             # in the revisit / plan caches' keys with the bridges (a change makes the decks again)
SIDE_SHARE = 0.25       # samples this share of the deck's width either side of its centre line (and on it)
RUN_N = 5               # stations in the running median (DECK_STEP apart: 16 m)
SPIKE_UP_M = 1.0        # a station this far over the running median: a vehicle, a gantry, a pole
SPIKE_DOWN_M = 1.5      # ... this far under: a pixel off the deck's edge
OCC_MARGIN_M = 1.5      # m past another deck's half width a station counts as under or across it
END_FREE_M = 20.0       # m off a shared end where joined parts overlap (a fork, a merge): not an occlusion
MIN_SHARE = 0.3         # of a part's stations measured, at least, for the lidar to shape it ...
MIN_N = 4               # ... and this many
GAP_FILL_M = 80.0       # m: a gap between measured stations this short is filled straight between them
OBS_TOL_M = 0.3         # m the deck may leave the measurement for its curves and grades
END_BLEND_M = 30.0      # m from an abutment over which the measurement gives way to the graded ground's height
LIDAR_HEADROOM_M = 4.6  # m under a measured deck's girders (CDOT's 16.5 ft standard is 5.03 m; older ones 14.5 ft)


DSM_COLLECTION = "3dep-lidar-dsm"
DSM_BASE_M = -500.0     # m: the stored mosaic's zero (uint16 steps of DSM_STEP_M over it; 0 = no pixel)
DSM_STEP_M = 0.05


class Dsm:
    """3DEP's digital surface model (the Planetary Computer's 3dep-lidar-dsm: the highest return in each 2 m pixel) per
    z14 map cell, as lidarheights.Lidar keeps the height above ground: Web-Mercator z16 tiles read through its tile
    service as float32 arrays (format=npy), a cell's 4 x 4 cached once as uint16 (DSM_STEP_M over DSM_BASE_M) under
    <raw>/lidar/dsm/. The surface, not the height over a ground the lidar's filter chose: a long viaduct taken for
    ground read 0 m over it (I-70 at the Mousetrap), its deck under the HAG's ground."""

    def __init__(self, raw_dir, fetcher):
        import threading
        self.dir = os.path.join(raw_dir, "lidar", "dsm")
        self.fetcher = fetcher
        self._lock = threading.Lock()
        self._mem = {}

    def items(self, bounds, kind="demand"):
        import json
        import lidarheights
        body = self.fetcher.get(f"{lidarheights.STAC}?collections={DSM_COLLECTION}&bbox="
                                f"{','.join(repr(float(v)) for v in bounds)}&limit=100", kind=kind).body
        out = []
        for f in json.loads(body).get("features") or []:
            p = f.get("properties") or {}
            out.append((str(f["id"]), tuple(float(v) for v in f["bbox"]),
                        str(p.get("datetime") or p.get("start_datetime") or "")))
        return sorted(out, key=lambda t: (t[2], t[0]), reverse=True)

    def tile(self, item, z, x, y, kind="demand"):
        """The DSM of one tile: float32 (256, 256), NaN where it has no pixel."""
        import io
        import lidarheights
        url = f"{lidarheights.TILES}/{z}/{x}/{y}@1x?collection={DSM_COLLECTION}&item={item}&assets=data&format=npy"
        a = np.load(io.BytesIO(self.fetcher.get(url, kind=kind).body), allow_pickle=False)
        return npy_tile(a)

    def mosaic(self, key, kind="demand"):
        """{"z": float32 (1024, 1024) of the z14 cell (row 0 north, NaN: none), "covered": bool (4, 4), "items"}."""
        import cache
        import staging
        import threading
        path = os.path.join(self.dir, str(key.z), str(key.x), f"{key.y}.npz")
        with self._lock:
            got = self._mem.get(path)
        if got is not None:
            return got
        try:
            with np.load(cache.resolve(path), allow_pickle=False) as f:
                raw = {k: f[k] for k in f.files}
        except (OSError, ValueError):
            raw = self._build(key, kind)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp.npz"
            np.savez_compressed(tmp, **raw)
            staging.replace_cached(tmp, path)
        q = raw["q"]
        got = {"z": np.where(q > 0, DSM_BASE_M + q.astype(np.float32) * DSM_STEP_M, np.nan).astype(np.float32),
               "covered": raw["covered"], "items": raw["items"]}
        with self._lock:
            if len(self._mem) > 16:
                self._mem.clear()
            self._mem[path] = got
        return got

    def _build(self, key, kind):
        import lidarheights
        import mapcells
        n = 1 << (lidarheights.ZOOM - key.z)
        items = self.items(key.bounds(), kind)
        q = np.zeros((n * 256, n * 256), np.uint16)
        covered = np.zeros((n, n), bool)
        used = []
        for j in range(n):
            for i in range(n):
                tx, ty = key.x * n + i, key.y * n + j
                lat, lon = mapcells.CellKey(lidarheights.ZOOM, tx, ty).center()
                it = next((it for it in items if lidarheights._contains(it[1], lon, lat)), None)
                if it is None:
                    continue
                z = self.tile(it[0], lidarheights.ZOOM, tx, ty, kind)
                v = np.round((z - DSM_BASE_M) / DSM_STEP_M)
                q[j * 256:(j + 1) * 256, i * 256:(i + 1) * 256] = np.where(
                    np.isfinite(v), np.clip(v, 1, 65535), 0).astype(np.uint16)
                covered[j, i] = True
                if it[0] not in used:
                    used.append(it[0])
        return {"q": q, "covered": covered, "items": np.array(sorted(used) or [""])}


def npy_tile(a):
    """A tile service's npy answer as float32 (256, 256), NaN where masked: (bands, h, w) with the mask as the last
    band (titiler: data then mask, 0 masked), (h, w), or a masked record. Pure."""
    a = np.asarray(a)
    if a.ndim == 3:
        data = a[0].astype(np.float32)
        if a.shape[0] >= 2:
            data = np.where(a[-1] > 0, data, np.nan)
    else:
        data = a.astype(np.float32)
    data = np.where(np.isfinite(data) & (data > -1000.0) & (data < 9000.0), data, np.nan)
    if data.shape != (256, 256):
        raise ValueError(f"lidar dsm tile: {data.shape}")
    return data


def lidar_road_clear():
    import roadmesh
    return LIDAR_HEADROOM_M + roadmesh.DECK_DEPTH + roadmesh.GIRDER_D


class Surface:
    """surface(la, lo) -> the lidar's surface height (Dsm), NaN where 3DEP has no pixel (outside its flights, a mosaic
    tile it did not cover). Each map cell's mosaic is put on the DEM's datum: less DATUM_PCT-th percentile of DSM -
    ground(la, lo) (the raw DEM) over a DATUM_N x DATUM_N grid of it (open ground; buildings and trees stand over it).
    A mosaic that cannot be read raises (the caller leaves the lidar out and does not keep what it made:
    revisit.fault)."""

    def __init__(self, dsm, ground, kind="demand"):
        self.dsm, self.ground, self.kind = dsm, ground, kind
        self.offsets = {}

    def _offset(self, key, z):
        got = self.offsets.get(key)
        if got is None:
            n = z.shape[0]
            idx = (np.arange(DATUM_N) + 0.5) * n / DATUM_N
            r, c = np.meshgrid(idx.astype(np.int64), idx.astype(np.int64), indexing="ij")
            dz = z[r, c].ravel()
            m = 1 << int(round(math.log2(n / 256)))
            fx = key.x * m + (c.ravel() + 0.5) / 256.0
            fy = key.y * m + (r.ravel() + 0.5) / 256.0
            zz = key.z + int(round(math.log2(m)))
            lo = fx / (1 << zz) * 360.0 - 180.0
            la = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * fy / (1 << zz)))))
            ok = np.isfinite(dz)
            got = 0.0
            if ok.sum() >= DATUM_N:
                g = np.asarray(self.ground(la[ok], lo[ok]), np.float64)
                d = dz[ok] - g
                got = float(np.percentile(d[np.isfinite(d)], DATUM_PCT)) if np.isfinite(d).any() else 0.0
            if abs(got) > DATUM_MAX_M:
                got = 0.0
            self.offsets[key] = got
        return got

    def __call__(self, la, lo):
        import lidarheights
        import mapcells
        la = np.asarray(la, np.float64).ravel()
        lo = np.asarray(lo, np.float64).ravel()
        out = np.full(len(la), np.nan)
        if not len(la):
            return out
        z = mapcells.ZOOM
        fx, fy = mapcells.tile_xy(la, lo, lidarheights.ZOOM)
        n = 1 << (lidarheights.ZOOM - z)
        cx, cy = np.floor(fx / n).astype(np.int64), np.floor(fy / n).astype(np.int64)
        for x, y in set(zip(cx.tolist(), cy.tolist())):
            key = mapcells.CellKey(z, x, y)
            if not lidarheights.covers(key.bounds()):
                continue
            m = self.dsm.mosaic(key, self.kind)
            dsm, cov = m["z"], m["covered"]
            sel = (cx == x) & (cy == y)
            px = np.clip(((fx[sel] - x * n) * 256.0).astype(np.int64), 0, dsm.shape[1] - 1)
            py = np.clip(((fy[sel] - y * n) * 256.0).astype(np.int64), 0, dsm.shape[0] - 1)
            v = dsm[py, px].astype(np.float64)
            ok = cov[py // 256, px // 256] & np.isfinite(v)
            out[sel] = np.where(ok, v - self._offset(key, dsm), np.nan)
        return out


DATUM_N = 48            # a cell's datum grid (DATUM_N^2 points)
DATUM_PCT = 10.0        # the percentile of DSM - DEM taken for the ground's
DATUM_MAX_M = 5.0       # m: a larger offset is the data's fault, not the datum's (none taken)


def _run_median(v, n):
    """The running median of v over n samples, NaNs left out (NaN where a window has none). Pure."""
    r = n // 2
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        w = v[max(0, i - r):i + r + 1]
        w = w[np.isfinite(w)]
        if len(w):
            out[i] = float(np.median(w))
    return out


def _dist_to(q, line):
    """m from each of the points q (n, 2) to the polyline line (k, 2), and the arc on it. Pure."""
    P, ab = line[:-1], np.diff(line, axis=0)
    L2 = np.maximum((ab * ab).sum(1), 1e-12)
    t = np.clip(((q[:, None, :] - P[None]) * ab[None]).sum(2) / L2[None], 0.0, 1.0)
    d = np.hypot(*(q[:, None, :] - (P[None] + t[..., None] * ab[None])).transpose(2, 0, 1))
    m = d.argmin(1)
    s = np.r_[0.0, np.cumsum(np.sqrt(L2))]
    return d[np.arange(len(q)), m], s[m] + t[np.arange(len(q)), m] * np.sqrt(L2[m])


def observations(lines, widths, levels, surface_xy):
    """Per deck part (lines: (k, 2) ENU points DECK_STEP apart; widths m; levels: the map's), the lidar's deck surface
    height at each point or NaN; None for a part measured too little (MIN_SHARE, MIN_N). surface_xy(xy (n, 2)) ->
    heights (NaN: none). Pure given surface_xy."""
    pts = [np.asarray(ln, np.float64)[:, :2] for ln in lines]
    lo_b = [p.min(0) for p in pts]
    hi_b = [p.max(0) for p in pts]
    out = []
    for k, p in enumerate(pts):
        if len(p) < 2:
            out.append(None)
            continue
        d = np.gradient(p, axis=0)
        d /= np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)[:, None]
        nrm = np.c_[-d[:, 1], d[:, 0]]
        off = SIDE_SHARE * float(widths[k])
        samples = np.stack([surface_xy(p + o * nrm) for o in (-off, 0.0, off)])
        good = np.isfinite(samples).sum(0) >= 2
        obs = np.full(len(p), np.nan)
        if good.any():
            obs[good] = np.nanmedian(samples[:, good], axis=0)
        # under (or across, at the same level) another deck: the lidar saw that one
        ends = (p[0], p[-1])
        s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
        for j, q in enumerate(pts):
            if j == k or len(q) < 2 or levels[j] < levels[k]:
                continue
            reach = float(widths[j]) / 2 + OCC_MARGIN_M
            if (hi_b[j] + reach < lo_b[k]).any() or (hi_b[k] + reach < lo_b[j]).any():
                continue
            dist, _ = _dist_to(p, q)
            hit = dist < reach
            if levels[j] == levels[k]:
                # joined parts of one level overlap near their shared end (a fork): both one surface there
                shared = [e for e in ends if min(np.hypot(*(q[0] - e)), np.hypot(*(q[-1] - e))) < 1.0]
                for e in shared:
                    hit &= np.hypot(*(p - e).T) > END_FREE_M
                # side by side (one deck's two carriageways): not across it
                if hit.any() and hit.mean() > 0.5:
                    continue
            obs[hit] = np.nan
        med = _run_median(obs, RUN_N)
        with np.errstate(invalid="ignore"):
            bad = (obs - med > SPIKE_UP_M) | (med - obs > SPIKE_DOWN_M)
        obs[bad] = np.nan
        n = int(np.isfinite(obs).sum())
        out.append(obs if n >= MIN_N and n >= MIN_SHARE * len(obs) and s[-1] > 0 else None)
    return out


def filled(s, obs, base):
    """The measured profile obs (NaN gaps) at arcs s, its gaps up to GAP_FILL_M filled straight between the measured
    points either side, longer gaps and unmeasured ends going over to base (the deck as made without the lidar) offset
    to meet the measurements: (z, measured mask). Pure."""
    s = np.asarray(s, np.float64)
    obs = np.asarray(obs, np.float64)
    base = np.asarray(base, np.float64)
    ok = np.isfinite(obs)
    if not ok.any():
        return base.copy(), ok
    z = base.copy()
    idx = np.flatnonzero(ok)
    z[ok] = obs[ok]
    # inner gaps
    for a, b in zip(idx[:-1], idx[1:]):
        if b - a <= 1:
            continue
        mid = slice(a + 1, b)
        if s[b] - s[a] <= GAP_FILL_M:
            z[mid] = np.interp(s[mid], [s[a], s[b]], [obs[a], obs[b]])
        else:       # base, shifted by the measurements' offsets at each side (linear between)
            da, db = obs[a] - base[a], obs[b] - base[b]
            z[mid] = base[mid] + np.interp(s[mid], [s[a], s[b]], [da, db])
    # ends: base, offset to the nearest measurement, the offset fading over END_BLEND_M
    a, b = idx[0], idx[-1]
    if a > 0:
        u = np.clip((s[a] - s[:a]) / END_BLEND_M, 0.0, 1.0)
        z[:a] = base[:a] + (obs[a] - base[a]) * (1.0 - u)
    if b < len(z) - 1:
        u = np.clip((s[b + 1:] - s[b]) / END_BLEND_M, 0.0, 1.0)
        z[b + 1:] = base[b + 1:] + (obs[b] - base[b]) * (1.0 - u)
    return z, ok


def end_blend(s, z, z0, z1, at_start, at_end):
    """z with an abutment's end (at_start / at_end) brought to its height (z0 / z1: the graded ground), the change
    fading over END_BLEND_M (a smoothstep). Pure."""
    z = np.array(z, np.float64)
    s = np.asarray(s, np.float64)
    for on, d, ze, e in ((at_start, s, z0, 0), (at_end, s[-1] - s, z1, -1)):
        if not on or not math.isfinite(ze):
            continue
        u = np.clip(d / END_BLEND_M, 0.0, 1.0)
        w = 1.0 - u * u * (3.0 - 2.0 * u)
        z += (ze - z[e]) * w
    return z


def surface_for(md, dem):
    """The Surface of a server process (its MapData md and DEM dem: the raw ground at grading.PROFILE_ZOOM, one zoom in
    every process so every one makes the same decks), or None (BNE_DECK_LIDAR=off, no map data or DEM)."""
    if not ON or md is None or dem is None or getattr(md, "raw_dir", None) is None or getattr(md, "fetcher", None) is None:
        return None
    import grading
    dsm = md.__dict__.get("_deck_dsm")
    if dsm is None:
        dsm = md.__dict__.setdefault("_deck_dsm", Dsm(md.raw_dir, md.fetcher))
    return Surface(dsm, lambda la, lo: dem.sample(la, lo, grading.PROFILE_ZOOM))
