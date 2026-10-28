"""Named pre-load regions (tools/regions.json) and their geometry, for tools/preload.py: circles round a place,
polygons, corridors (a road's centreline buffered by a width), boxes and unions of them, each part with a tier (how
much of it to fetch, and in which order: towns first, then the roads, then the fill between). Pure, no network.

A region is flattened into its parts. part.dist_m(lat, lon) is <= 0 inside and the distance to its edge outside
(numpy arrays in, out); Region.tiles(z) gives the zoom-z tiles that reach into the parts with their tier's priority,
so every layer of the pre-load (elevation, imagery, map cells) is enumerated the same way.
"""
import json
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(HERE, "regions.json")
EARTH_R = 6371008.8
EARTH_CIRC = 40075016.686
# The tiers when a regions file names none: town (every layer, the finest imagery too), road (the corridors between
# them), fill (the rest of the area); a lower priority is fetched first. A tier's settings (TIER_DEFAULTS where it
# leaves one out): dem, the finest DEM zoom (None: dem.FINE_ZOOM, the block's); exports, the export sources' zooms
# (17: the base photo; 18: the regional sources' finer one; [] none); usgs, USGS's zooms over it; mapdata, the z14
# cells: true (every layer), "roads" (roads and connectors), false
TIER_DEFAULTS = {"priority": 9, "dem": None, "exports": [17], "usgs": [16, 15], "mapdata": True}
DEFAULT_TIERS = {
    "town": {"priority": 1, "exports": [17, 18]},
    "road": {"priority": 2},
    "fill": {"priority": 3},
}


class RegionError(ValueError):
    pass


# --- geometry (a local sinusoidal projection about the part's mean longitude: metres, < 0.5 % off over 200 km) ------
def _xy(lat, lon, lon0):
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    return EARTH_R * np.radians(lon - lon0) * np.cos(np.radians(lat)), EARTH_R * np.radians(lat)


def _seg_dist(px, py, ax, ay, bx, by):
    """Distance of points (px, py) to the segments a-b: [points, segments] -> min over segments. Pure."""
    px, py = px[:, None], py[:, None]
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / np.where(ll > 0, ll, 1.0), 0.0, 1.0)
    return np.hypot(px - (ax + t * dx), py - (ay + t * dy)).min(axis=1)


def _chunks(n, size=4096):
    for i in range(0, n, size):
        yield slice(i, min(n, i + size))


class Part:
    """One shape of a region. kind: circle, polygon, corridor or bbox; tier: a key of the tiers."""

    def __init__(self, name, kind, tier, points, radius_m=0.0):
        if not points:
            raise RegionError(f"{name}: no points")
        self.name, self.kind, self.tier = name, kind, tier
        self.points = [(float(a), float(b)) for a, b in points]
        for la, lo in self.points:
            if not (-85 <= la <= 85 and -180 <= lo <= 180):
                raise RegionError(f"{name}: bad point {la}, {lo}")
        self.radius_m = float(radius_m)
        self.lon0 = sum(p[1] for p in self.points) / len(self.points)
        lats = [p[0] for p in self.points]
        lons = [p[1] for p in self.points]
        pad_lat = math.degrees(self.radius_m / EARTH_R)
        pad_lon = pad_lat / max(0.05, math.cos(math.radians(max(abs(min(lats)), abs(max(lats))) + pad_lat)))
        self.bounds = (min(lats) - pad_lat, min(lons) - pad_lon, max(lats) + pad_lat, max(lons) + pad_lon)
        x, y = _xy(lats, lons, self.lon0)
        self._x, self._y = np.asarray(x, float), np.asarray(y, float)
        if kind == "polygon" and len(self.points) < 3:
            raise RegionError(f"{name}: a polygon needs 3 points")

    @property
    def anchor(self):
        """Where its tiles count their distance from (the centre; a corridor's start)."""
        if self.kind in ("circle", "corridor"):
            return self.points[0]
        s, w, n, e = self.bounds
        return ((s + n) / 2, (w + e) / 2)

    def area_km2(self):
        """Rough area (a corridor's length x width, a polygon's shoelace)."""
        if self.kind == "circle":
            return math.pi * self.radius_m ** 2 / 1e6
        if self.kind == "corridor":
            ln = float(np.hypot(np.diff(self._x), np.diff(self._y)).sum())
            return (ln * 2 * self.radius_m + math.pi * self.radius_m ** 2) / 1e6
        x, y = self._x, self._y
        return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2e6

    def dist_m(self, lat, lon):
        """<= 0 inside, else the distance to the part (metres); numpy arrays."""
        px, py = _xy(lat, lon, self.lon0)
        px, py = np.atleast_1d(px).ravel(), np.atleast_1d(py).ravel()
        out = np.empty(px.shape)
        if self.kind == "circle":
            return np.hypot(px - self._x[0], py - self._y[0]) - self.radius_m
        if self.kind == "corridor":
            if len(self._x) == 1:
                return np.hypot(px - self._x[0], py - self._y[0]) - self.radius_m
            ax, ay, bx, by = self._x[:-1], self._y[:-1], self._x[1:], self._y[1:]
            for sl in _chunks(len(px)):
                out[sl] = _seg_dist(px[sl], py[sl], ax, ay, bx, by)
            return out - self.radius_m
        # polygon / bbox: even-odd rule inside, the distance to the ring outside
        ax, ay = self._x, self._y
        bx, by = np.roll(ax, -1), np.roll(ay, -1)
        for sl in _chunks(len(px)):
            qx, qy = px[sl][:, None], py[sl][:, None]
            crosses = ((ay > qy) != (by > qy)) & (qx < (bx - ax) * (qy - ay) / np.where(by != ay, by - ay, 1e-12) + ax)
            inside = (crosses.sum(axis=1) % 2) == 1
            d = _seg_dist(px[sl], py[sl], ax, ay, bx, by)
            out[sl] = np.where(inside, -d, d)
        return out

    def contains(self, lat, lon):
        return bool(self.dist_m([lat], [lon])[0] <= 0)


def circle(name, lat, lon, radius_km, tier="town"):
    return Part(name, "circle", tier, [(lat, lon)], radius_km * 1000.0)


def corridor(name, points, width_km, tier="road"):
    return Part(name, "corridor", tier, points, width_km * 500.0)


def polygon(name, points, tier="fill"):
    return Part(name, "polygon", tier, points)


def bbox(name, s, w, n, e, tier="fill"):
    return Part(name, "bbox", tier, [(s, w), (s, e), (n, e), (n, w)])


# --- tiles ---------------------------------------------------------------------------------------------------------
def tile_m(lat, z):
    return EARTH_CIRC * math.cos(math.radians(lat)) / 2 ** z


def tile_xy(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tile_center(z, x, y):
    """(lat, lon) of tile centres; numpy arrays in, out."""
    n = 2.0 ** z
    x, y = np.asarray(x, float) + 0.5, np.asarray(y, float) + 0.5
    return np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * y / n)))), x / n * 360.0 - 180.0


class Region:
    """A named union of parts, with the tiers' settings; far_km: how far round it the coarse rings reach."""

    def __init__(self, name, parts, tiers=None, far_km=40.0, home=None, title=""):
        if not parts:
            raise RegionError(f"{name}: no parts")
        self.name, self.parts, self.title = name, list(parts), title
        self.tiers = dict(DEFAULT_TIERS if tiers is None else tiers)
        for p in self.parts:
            if p.tier not in self.tiers:
                raise RegionError(f"{name}: part {p.name}: unknown tier {p.tier!r}")
        self.far_m = float(far_km) * 1000.0
        self.home = tuple(home) if home else self.parts[0].anchor

    @property
    def bounds(self):
        bs = [p.bounds for p in self.parts]
        return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))

    def area_km2(self):
        return sum(p.area_km2() for p in self.parts)

    def priority(self, tier):
        return int(self.opt(tier, "priority"))

    def opt(self, tier, name):
        """A tier's setting (TIER_DEFAULTS where the tier leaves it out; "z18": true is exports [17, 18])."""
        t = self.tiers.get(tier) or {}
        if name == "exports" and "exports" not in t and "z18" in t:
            return [17, 18] if t["z18"] else [17]
        return t.get(name, TIER_DEFAULTS[name])

    def where(self, name, test):
        """A parts filter for tiles(): the parts whose tier's setting `name` passes test."""
        return lambda p: test(self.opt(p.tier, name))

    def tiles(self, z, pad_m=0.0, where=None):
        """{(x, y): priority} of the zoom-z tiles that reach into a part (its edge within half a tile's diagonal, plus
        pad_m), with the lowest priority among those parts; where(part) picks the parts (default all)."""
        out = {}
        n = 2 ** z
        for p in self.parts:
            if where is not None and not where(p):
                continue
            s, w, nn, e = p.bounds
            mid = (s + nn) / 2
            reach = pad_m + tile_m(mid, z) * 0.75           # half the diagonal (~0.71 tile), and a little
            dlat = math.degrees(reach / EARTH_R)
            dlon = dlat / max(0.05, math.cos(math.radians(mid)))
            x0, y1 = tile_xy(max(-85.0, s - dlat), w - dlon, z)
            x1, y0 = tile_xy(min(85.0, nn + dlat), e + dlon, z)
            xs = np.arange(int(math.floor(x0)), int(math.floor(x1)) + 1)
            ys = np.arange(max(0, int(math.floor(y0))), min(n - 1, int(math.floor(y1))) + 1)
            if not len(xs) or not len(ys):
                continue
            gx, gy = np.meshgrid(xs, ys)
            gx, gy = gx.ravel(), gy.ravel()
            la, lo = tile_center(z, gx, gy)
            half = EARTH_CIRC * np.cos(np.radians(la)) / n * 0.7072 + pad_m
            keep = p.dist_m(la, lo) <= half
            pr = self.priority(p.tier)
            for x, y in zip(gx[keep].tolist(), gy[keep].tolist()):
                k = (x % n, y)
                if out.get(k, 99) > pr:
                    out[k] = pr
        return out

    def ordered(self, z, tiles):
        """The keys of tiles ({(x, y): priority}) by priority, then nearest the home point first."""
        hx, hy = tile_xy(*self.home, z)
        return sorted(tiles, key=lambda k: (tiles[k], (k[0] + 0.5 - hx) ** 2 + (k[1] + 0.5 - hy) ** 2))


# --- the regions file ----------------------------------------------------------------------------------------------
def load(path=DEFAULT_FILE):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("regions"), dict):
        raise RegionError(f"{path}: no \"regions\" object")
    return data


def names(data):
    return [k for k in data["regions"] if not k.startswith("_")]


def _parts(data, name, spec, tier, seen):
    if isinstance(spec, str):
        if spec in seen:
            raise RegionError(f"{name}: {spec} includes itself")
        if spec not in data["regions"]:
            raise RegionError(f"{name}: no region {spec!r}")
        return _parts(data, spec, data["regions"][spec], tier, seen | {spec})
    if not isinstance(spec, dict):
        raise RegionError(f"{name}: a region is an object or a name")
    t = spec.get("tier", tier)
    if "union" in spec:
        out = []
        for i, sub in enumerate(spec["union"]):
            sub_name = sub if isinstance(sub, str) else sub.get("title", f"{name}[{i}]") if isinstance(sub, dict) \
                else f"{name}[{i}]"
            out += _parts(data, sub_name, sub, t, seen)
        return out
    if "circle" in spec:
        return [circle(name, *spec["circle"], spec.get("radius_km", 5.0), t or "town")]
    if "corridor" in spec:
        return [corridor(name, spec["corridor"], spec.get("width_km", 3.0), t or "road")]
    if "polygon" in spec:
        return [polygon(name, spec["polygon"], t or "fill")]
    if "bbox" in spec:
        return [bbox(name, *spec["bbox"], tier=t or "fill")]
    raise RegionError(f"{name}: one of circle, corridor, polygon, bbox, union")


def region(data, name):
    """The named region of a loaded regions file, flattened."""
    if name not in data["regions"]:
        raise RegionError(f"no region {name!r} (--list shows them)")
    spec = data["regions"][name]
    tiers = data.get("tiers") or DEFAULT_TIERS
    parts = _parts(data, name, spec, None, {name})
    return Region(name, parts, tiers, far_km=spec.get("far_km", data.get("far_km", 40.0)) if isinstance(spec, dict)
                  else 40.0, home=spec.get("home") if isinstance(spec, dict) else None,
                  title=spec.get("title", "") if isinstance(spec, dict) else "")
