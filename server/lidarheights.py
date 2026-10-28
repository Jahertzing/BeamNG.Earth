"""Building heights from the USGS 3D Elevation Program's lidar (US), for the buildings whose height is missing or
machine-learned (--lidar-heights on, the default since 2026-10-28, BNE_LIDAR_HEIGHTS=1 in every server process; the
settings page's "Building heights from USGS lidar" switch, setting lidarHeights, turns it off). Only cells meeting
COVERAGE (the US: 3DEP's extent) ask for it: elsewhere the buildings layer is the one it was, no request, its cached
file kept.

Overture gives a height to ~75 % of a US town's buildings, most of them Microsoft's machine-learned estimates; the rest
get predicted storeys (buildings.plan). 3DEP flew most of the US at 2-20 points a square metre, public domain. Microsoft's
Planetary Computer publishes per-project rasters derived from the point clouds; its "3dep-lidar-hag" collection is the
height above ground (HAG: PDAL's SMRF ground filter, then the nearest ground point's height subtracted) on a 2 m grid.
Those COGs are LERC-compressed (cog.py has no LERC decoder, and no new package is wanted), so the Planetary Computer's
own tile service reads them: Web-Mercator z16 tiles (256 px, ~1.8 m at 40 N, one z14 map cell = 4 x 4 of them), as
8-bit PNGs rescaled 0..HAG_MAX_M (HAG_STEP_M steps; 255 = at or over the top: not believed), ~8 KB a tile. The cell's
mosaic is cached once per cell (<raw>/lidar/hag/), with the items it came from.

A building's height is the median (TOP_PCT) of the HAG pixels inside its footprint after the spikes are dropped (over
SPIKE_K x the median + SPIKE_M: noise points, the 255s of a saturated tile, a tree over the roof), with at least
MIN_PIXELS pixels and the result at least MIN_HEIGHT_M (else none: the building is newer than the flight, or the
footprint lies off the lidar's). Measured in Denver's Capitol Hill (2013 lidar, 2026-10-28) against Overture's own
USGS lidar heights (n = 1,327): median +0.4 m, MAE 2.2 m, 74 % within 2 m; against OpenStreetMap's mapped heights
(n = 1,732): +1.0 m, 3.8 m, 57 %. A high percentile (p90) caught the canopy and the noise: +8 m, MAE 37 m.
It replaces a missing height or a machine-learned one (ML_SOURCES); mapped heights (OpenStreetMap, DRCOG, Overture's
own USGS lidar) stay. Roof shapes are not fitted yet (the work plan's later step).

Licence: 3DEP data are US federal, public domain; credited as "USGS 3DEP Lidar" with the map's datasets.
"""
import io
import json
import logging
import math
import os
import threading

import numpy as np

import staging

log = logging.getLogger("earthd.mapdata")

ENV = "BNE_LIDAR_HEIGHTS"
TAG = "lidar1"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
TILES = "https://planetarycomputer.microsoft.com/api/data/v1/item/tiles/WebMercatorQuad"
COLLECTION = "3dep-lidar-hag"
ZOOM = 16                   # the tiles read: 4 x 4 a z14 map cell
HAG_MAX_M = 127.5           # the PNG's 0..255 spans 0..HAG_MAX_M ...
HAG_STEP_M = HAG_MAX_M / 255.0
TOP_PCT = 50.0
SPIKE_K, SPIKE_M = 1.5, 4.0
MIN_PIXELS = 3
MIN_HEIGHT_M = 2.5
ML_SOURCES = ("Microsoft ML Buildings", "Google Open Buildings", "Esri Community Maps", "")
DATASET, LICENSE = "USGS 3DEP Lidar", "public domain"
# 3DEP's extent, roughly (lon0, lat0, lon1, lat1): the lower 48, Alaska, Hawaii, Puerto Rico and the Virgin Islands
COVERAGE = ((-125.0, 24.4, -66.9, 49.5), (-180.0, 51.0, -129.0, 71.5), (-160.5, 18.8, -154.7, 22.3),
            (-67.3, 17.6, -64.5, 18.6))


def enabled():
    return os.environ.get(ENV) == "1"


def covers(bounds):
    """Does a cell's (lon0, lat0, lon1, lat1) meet COVERAGE?"""
    lon0, lat0, lon1, lat1 = bounds
    return any(lon0 <= x1 and lon1 >= x0 and lat0 <= y1 and lat1 >= y0 for x0, y0, x1, y1 in COVERAGE)


def _contains(b, x, y):
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


class Lidar:
    """The HAG mosaic per z14 map cell (fetched once, cached) and the heights of its buildings."""

    def __init__(self, raw_dir, fetcher):
        self.dir = os.path.join(raw_dir, "lidar", "hag")
        self.fetcher = fetcher
        self._lock = threading.Lock()
        self._mem = {}

    def items(self, bounds, kind="demand"):
        """[(id, bbox, datetime)] of the HAG items meeting bounds, newest first (then by id)."""
        body = self.fetcher.get(f"{STAC}?collections={COLLECTION}&bbox={','.join(repr(float(v)) for v in bounds)}"
                                f"&limit=100", kind=kind).body
        out = []
        for f in json.loads(body).get("features") or []:
            p = f.get("properties") or {}
            out.append((str(f["id"]), tuple(float(v) for v in f["bbox"]),
                        str(p.get("datetime") or p.get("start_datetime") or "")))
        return sorted(out, key=lambda t: (t[2], t[0]), reverse=True)

    def tile(self, item, z, x, y, kind="demand"):
        """The HAG of one tile (uint8 (256, 256), HAG_STEP_M steps)."""
        from PIL import Image
        url = (f"{TILES}/{z}/{x}/{y}@1x?collection={COLLECTION}&item={item}&assets=data&format=png"
               f"&rescale=0,{HAG_MAX_M}")
        im = Image.open(io.BytesIO(self.fetcher.get(url, kind=kind).body))
        a = np.asarray(im.convert("L"), np.uint8)
        if a.shape != (256, 256):
            raise ValueError(f"lidar tile {z}/{x}/{y}: {a.shape}")
        return a

    def mosaic(self, key, kind="demand"):
        """{"hag": uint8 (4*256, 4*256) of the z14 cell (row 0 north), "covered": bool (4, 4), "items": [ids]}."""
        path = os.path.join(self.dir, str(key.z), str(key.x), f"{key.y}.npz")
        with self._lock:
            got = self._mem.get(path)
        if got is not None:
            return got
        try:
            with np.load(path, allow_pickle=False) as z:
                got = {k: z[k] for k in z.files}
        except (OSError, ValueError):
            got = self._build(key, kind)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp.npz"
            np.savez_compressed(tmp, **got)
            staging.replace_cached(tmp, path)
        with self._lock:
            if len(self._mem) > 32:
                self._mem.clear()
            self._mem[path] = got
        return got

    def _build(self, key, kind):
        import mapcells
        s = ZOOM - key.z
        n = 1 << s
        items = self.items(key.bounds(), kind)
        hag = np.zeros((n * 256, n * 256), np.uint8)
        covered = np.zeros((n, n), bool)
        used = []
        for j in range(n):
            for i in range(n):
                tx, ty = key.x * n + i, key.y * n + j
                t = mapcells.CellKey(ZOOM, tx, ty)
                lat, lon = t.center()
                it = next((it for it in items if _contains(it[1], lon, lat)), None)
                if it is None:
                    continue
                hag[j * 256:(j + 1) * 256, i * 256:(i + 1) * 256] = self.tile(it[0], ZOOM, tx, ty, kind)
                covered[j, i] = True
                if it[0] not in used:
                    used.append(it[0])
        return {"hag": hag, "covered": covered, "items": np.array(sorted(used) or [""])}

    def heights(self, feats, key, kind="demand"):
        """Per building of feats (a cell's buildings, lon/lat): its lidar height (m) or NaN."""
        m = self.mosaic(key, kind)
        return footprint_heights(feats, key, m["hag"], m["covered"])


def footprint_heights(feats, key, hag, covered):
    """footprint_heights of every feature's outer ring over the cell's mosaic. Pure."""
    from PIL import Image, ImageDraw
    import mapcells
    s = ZOOM - key.z
    n = 1 << s
    out = np.full(len(feats), np.nan)
    size = hag.shape[0]
    for k in range(len(feats)):
        parts = feats.parts(k)
        if not parts or not parts[0] or len(parts[0][0]) < 4:
            continue
        ring = np.asarray(parts[0][0], np.float64)
        fx, fy = mapcells.tile_xy(ring[:, 1], ring[:, 0], ZOOM)
        px = (np.asarray(fx) - key.x * n) * 256.0
        py = (np.asarray(fy) - key.y * n) * 256.0
        c0, r0 = int(math.floor(px.min())), int(math.floor(py.min()))
        c1, r1 = int(math.ceil(px.max())) + 1, int(math.ceil(py.max())) + 1
        c0, r0, c1, r1 = max(c0, 0), max(r0, 0), min(c1, size), min(r1, size)
        if c1 <= c0 or r1 <= r0:
            continue
        cov = covered[np.clip(np.arange(r0, r1) // 256, 0, n - 1)][:, np.clip(np.arange(c0, c1) // 256, 0, n - 1)]
        im = Image.new("L", (c1 - c0, r1 - r0), 0)
        ImageDraw.Draw(im).polygon([(float(x - c0), float(y - r0)) for x, y in zip(px, py)], fill=1)
        mask = (np.asarray(im) > 0) & cov
        v = hag[r0:r1, c0:c1][mask]
        v = v[v < 255].astype(np.float64) * HAG_STEP_M
        if len(v) < MIN_PIXELS:
            continue
        v = v[v <= np.median(v) * SPIKE_K + SPIKE_M]
        h = float(np.percentile(v, TOP_PCT))
        if h >= MIN_HEIGHT_M:
            out[k] = h
    return out


def apply(feats, heights):
    """feats with each lidar height (finite) where its height is missing or machine-learned (ML_SOURCES), its
    height_src "USGS 3DEP Lidar": (feats, how many). In place."""
    import mapgeom as mg
    h = np.asarray(feats.attrs["height"], np.float64).copy()
    src = [str(v) for v in feats.attrs.get("height_src", [""] * len(feats))]
    n = 0
    for k in range(len(feats)):
        if not np.isfinite(heights[k]):
            continue
        if np.isfinite(h[k]) and h[k] > 0 and src[k] not in ML_SOURCES:
            continue                    # (a mapped or measured height stays)
        h[k] = heights[k]
        src[k] = DATASET
        n += 1
    feats.attrs["height"] = mg.float_column(h.tolist())
    feats.attrs["height_src"] = mg.text_column(src)
    return feats, n
