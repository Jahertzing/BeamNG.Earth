"""Whole-route planning for the world map's GPS (the user, 2026-10-14: "have the server plan out the whole route so
it can work with much longer distances").

The game's own road graph (navgraph.py) only covers ~1 km around the player, so the route itself comes from OSRM
on OpenStreetMap data, the FOSSGIS-hosted instance (https://routing.openstreetmap.de; fair use: a valid
User-Agent, at most ~1 request a second, no bulk use; netfetch's routing.openstreetmap.de policy). One request per
destination (and per re-route when the player leaves the route); the geometry is simplified and sent to the mod,
which draws it on the map and steers the stock route arrows along it on its local graph (gps.lua).
"""
import json
import math

import netfetch

OSRM_URL = "https://routing.openstreetmap.de/routed-car/route/v1/driving/{coords}?overview=full&geometries=geojson&steps=false"
SIMPLIFY_M = 4.0
MAX_POINTS = 6000
ATTRIBUTION = "Routing: OSRM (FOSSGIS), data (c) OpenStreetMap contributors (ODbL)"


BEARING_RANGE = 60          # degrees either side of the car's heading the start may take
MAX_VIA = 8                 # waypoints a route may pass through on the way (The Drive: Union Station)
# road classes a route may leave out (OSRM's car profile: exclude=toll|motorway|ferry); The Drive's "straight down
# I-25" asks to leave out the toll roads (routing.openstreetmap.de answers 400 InvalidValue to any exclude, so plan
# routes without it; its plain Frederick to Castle Rock route stays on I-25, tools/thedrive_check.py)
EXCLUDE = ("toll", "motorway", "ferry")


def clean_via(via):
    """The waypoints of a route request as [(lat, lon)]: valid pairs only, at most MAX_VIA. Pure."""
    out = []
    for p in via or []:
        try:
            lat, lon = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out.append((lat, lon))
    return out[:MAX_VIA]


def url(lat0, lon0, lat1, lon1, bearing=None, via=None, exclude=None):
    """bearing: the car's heading (compass degrees): the route starts on a road the car can follow that way.
    via: [(lat, lon)] the route passes through in order between the start and the end. exclude: a road class
    (EXCLUDE) the route keeps off."""
    pts = [(lat0, lon0)] + clean_via(via) + [(lat1, lon1)]
    u = OSRM_URL.format(coords=";".join(f"{float(lon):.6f},{float(lat):.6f}" for lat, lon in pts))
    if bearing is not None:            # one entry per coordinate, only the start's set
        u += f"&bearings={int(bearing) % 360},{BEARING_RANGE}" + ";" * (len(pts) - 1)
    if exclude in EXCLUDE:
        u += f"&exclude={exclude}"
    return u


def _xy(lat, lon, lat_ref):
    m = 111320.0
    return lon * m * math.cos(math.radians(lat_ref)), lat * m


def simplify(points, tol_m=SIMPLIFY_M):
    """points [(lat, lon)] with the vertices closer than tol_m to their neighbours' line dropped (Ramer-Douglas-
    Peucker, iterative). Pure."""
    n = len(points)
    if n < 3:
        return list(points)
    lat_ref = points[0][0]
    xy = [_xy(la, lo, lat_ref) for la, lo in points]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            if L2 > 0:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                qx, qy = ax + t * dx, ay + t * dy
            else:
                qx, qy = ax, ay
            d = (px - qx) ** 2 + (py - qy) ** 2
            if d > best:
                best, bi = d, i
        if bi > 0 and best > tol_m * tol_m:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(points, keep) if k]


def resample(points, max_points=120, min_step_m=50.0):
    """points [(lat, lon)] (the route, however sparse its vertices are) at even distance steps, for an elevation
    profile: [(lat, lon, dist_m)], the first at 0 and the last at the route's whole length, at most max_points,
    step >= min_step_m. simplify() only keeps vertices at bends, so a long straight climb would else be two points
    and no profile at all (dem.sample wants points spread along it instead). Pure."""
    n = len(points)
    if n == 0:
        return []
    if n == 1:
        return [(points[0][0], points[0][1], 0.0)]
    lat_ref = points[0][0]
    xy = [_xy(la, lo, lat_ref) for la, lo in points]
    seg = [math.hypot(xy[i + 1][0] - xy[i][0], xy[i + 1][1] - xy[i][1]) for i in range(n - 1)]
    total = sum(seg)
    if total <= 0:
        return [(points[0][0], points[0][1], 0.0)]
    step = max(min_step_m, total / max(1, max_points - 1))
    out = []
    i, done, s = 0, 0.0, 0.0
    while s <= total:
        while i < n - 2 and done + seg[i] < s:
            done += seg[i]
            i += 1
        f = max(0.0, min(1.0, (s - done) / seg[i])) if seg[i] > 0 else 0.0
        la = points[i][0] + f * (points[i + 1][0] - points[i][0])
        lo = points[i][1] + f * (points[i + 1][1] - points[i][1])
        out.append((la, lo, s))
        s += step
    if out[-1][2] < total - 1e-6:
        out.append((points[-1][0], points[-1][1], total))
    return out


def parse(data):
    """{points: [[lat, lon], ...], dist (m), dur (s)} of an OSRM route response, or raises ValueError. Pure."""
    if not isinstance(data, dict) or data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(f"no route ({(data or {}).get('code') if isinstance(data, dict) else 'bad answer'})")
    r = data["routes"][0]
    coords = r["geometry"]["coordinates"]
    pts = [(float(c[1]), float(c[0])) for c in coords]
    pts = simplify(pts)
    tol = SIMPLIFY_M
    while len(pts) > MAX_POINTS:            # very long routes: coarser
        tol *= 2
        pts = simplify(pts, tol)
    return {"points": [[round(a, 6), round(b, 6)] for a, b in pts], "dist": float(r.get("distance") or 0),
            "dur": float(r.get("duration") or 0)}


def plan(lat0, lon0, lat1, lon1, net=None, bearing=None, via=None, exclude=None):
    """The road route from (lat0, lon0) to (lat1, lon1) (blocking), through the via points in order, keeping off the
    exclude road class if the router can (else without), starting the way bearing points if it can (else without it).
    Raises netfetch.FetchError or ValueError."""
    net = net if net is not None else netfetch.default_fetcher()
    if exclude in EXCLUDE:
        try:
            return _plan(lat0, lon0, lat1, lon1, net, bearing, via, exclude)
        except (ValueError, netfetch.FetchFailed) as e:    # (a router without that class: 400 InvalidValue)
            if isinstance(e, netfetch.FetchFailed) and e.status != 400:
                raise
    return _plan(lat0, lon0, lat1, lon1, net, bearing, via, None)


def _plan(lat0, lon0, lat1, lon1, net, bearing, via, exclude):
    if bearing is not None:
        try:
            return parse(json.loads(net.get(url(lat0, lon0, lat1, lon1, bearing, via, exclude), kind="demand")
                                    .body.decode("utf-8")))
        except (ValueError, netfetch.FetchFailed) as e:   # no road that way near the start (OSRM: 400 NoSegment,
            if isinstance(e, netfetch.FetchFailed) and e.status != 400:      # a car across a field): any direction
                raise
    return parse(json.loads(net.get(url(lat0, lon0, lat1, lon1, via=via, exclude=exclude), kind="demand")
                            .body.decode("utf-8")))
