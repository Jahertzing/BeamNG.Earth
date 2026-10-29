"""The signs of the numbered routes off the freeways (the user, 2026-10-28: CO 93, CO 52, the mountain passes): a
mile marker every mile along every Interstate, US and Colorado state highway (where guidesigns' exit numbers give
none: the two-lane highways, the freeways without numbered exits), route shields with their cardinal direction past
each junction with another route and every ten miles, JCT assemblies ahead of those junctions, a summit sign at
each mountain pass, and the city / town limit signs where a highway enters a town.

Where they come from: tools/build_route_signs.py reads every way carrying a route ref, the mapped mile markers
(highway=milestone), the passes, the towns' place nodes and the towns' boundaries (admin_level 8) into
server/hwysigns/<name>-routes.json.gz; guidesigns.Plans hands such a file (its "kind": "routes") to plan() here.

The mileposts: Colorado's run from 0 at a route's southern or western end (its state line for the Interstates and
US routes) and count up north or east. plan() measures each route along its own ways (a shortest path from that
end over the route's ways, the gaps in its mapping bridged), and where OpenStreetMap maps mile markers on the route,
pins the count to them (between two: interpolated; past the last: on at a mile a mile). Pure but for the font.
"""
import heapq
import math
import re

import numpy as np

import guidesigns as gs

MILE_M = gs.MILE_M
STATES = {"colorado": (36.9925, -109.0603, 41.0034, -102.0416)}     # the state lines: a route's mileage starts at them
GAP_M = 15000.0             # a route's mapped pieces this far apart (end to end) are one route
SNAP_M = 40.0               # a mapped mile marker this close to the route is on it
REASSURE_M = 120.0          # m past a junction: the route's shield and cardinal
REASSURE_EVERY_MI = 10      # ... and every this many miles between
JCT_M = 260.0               # m ahead of a junction: its JCT assembly
CITY_STAY_M = 150.0         # a town limit sign where the road then runs at least this far inside (the annexation
CITY_AGAIN_M = 2500.0       # patchwork along I-25), and not where it entered the same town within this
SHIELD_H = 0.61             # a conventional road's route shield (24")
PASS_SNAP_M = 80.0
# the passes on the Continental Divide (their summit signs say so)
DIVIDE = {"loveland pass", "berthoud pass", "hoosier pass", "independence pass", "monarch pass", "wolf creek pass",
          "cottonwood pass", "cameron pass", "rabbit ears pass", "willow creek pass", "muddy pass", "fremont pass",
          "tennessee pass", "milner pass", "spring creek pass", "la manga pass"}
KIND_RANK = {"I": 0, "US": 1, "CO": 2}


def _ft(ele):
    """OSM ele (m, or "11990 ft") in feet, or None."""
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(ft|feet|')?", str(ele or ""))
    if not m:
        return None
    v = float(m.group(1))
    return int(round(v if m.group(2) else v * 3.28084))


def route_refs(tags):
    """A way's routes [(network, number)]: I / US / CO with a number (business routes, express lanes: none)."""
    out = []
    for r in gs._split(tags.get("ref")):
        net, num, _ = gs.parse_ref(r)
        if net in ("I", "US", "CO", "SH") and re.match(r"^\d+[A-Z]?$", num or ""):
            key = ("CO" if net == "SH" else net, num)
            if key not in out:
                out.append(key)
    return out


def _rank(route):
    n = re.match(r"\d+", route[1])
    return (KIND_RANK.get(route[0], 3), int(n.group(0)) if n else 0, route[1])


def _ns(route, pts):
    """Does the route run north-south (its mileposts counting north) rather than west-east: odd Interstates and US
    routes; a state highway by its extent."""
    n = re.match(r"\d+", route[1])
    if route[0] in ("I", "US") and n:
        return int(n.group(0)) % 2 == 1
    la = pts[:, 0]
    lo = pts[:, 1]
    return (la.max() - la.min()) * 111.2 >= (lo.max() - lo.min()) * 111.2 * math.cos(math.radians(float(la.mean())))


def _card(ns, up):
    """The cardinal direction of travel with the mileposts counting up (up) or down."""
    return ("NORTH" if up else "SOUTH") if ns else ("EAST" if up else "WEST")


class Route:
    """One route's ways as a graph of their nodes: its mileage (m from its start) at each node, and the mile."""

    def __init__(self, key, ways, coord, clip=None, stones=()):
        self.key = key
        self.ways = ways                        # [way dict] (directed along their travel when one-way)
        self.adj = {}                           # node -> [(node, way index, m)]
        for wi, w in enumerate(ways):
            ns = w["nodes"]
            for a, b in zip(ns, ns[1:]):
                if clip is not None and not (_in(clip, coord[a]) and _in(clip, coord[b])):
                    continue
                d = gs._dist(coord[a], coord[b])
                self.adj.setdefault(a, []).append((b, wi, d))
                self.adj.setdefault(b, []).append((a, wi, d))
        self.coord = coord
        self.dist = {}
        if not self.adj:
            return
        pts = np.array([coord[n] for n in self.adj])
        self.ns = _ns(key, pts)
        ends = [n for n, e in self.adj.items() if len({b for b, _, _ in e}) == 1]
        self._bridge(ends)
        cand = ends or list(self.adj)
        start = min(cand, key=lambda n: coord[n][0] if self.ns else coord[n][1])
        self.dist = self._dijkstra(start)
        self._calibrate(stones)

    def _bridge(self, ends):
        """Join the route's mapped pieces: each piece's ends to the nearest end of every other piece within GAP_M."""
        comp, k = {}, 0
        for n in self.adj:
            if n in comp:
                continue
            stack = [n]
            comp[n] = k
            while stack:
                u = stack.pop()
                for v, _, _ in self.adj[u]:
                    if v not in comp:
                        comp[v] = k
                        stack.append(v)
            k += 1
        if k <= 1 or not ends:
            return
        E = np.array([self.coord[n] for n in ends])
        kx = math.cos(math.radians(float(E[:, 0].mean()))) * 111_195.0
        xy = np.column_stack([E[:, 1] * kx, E[:, 0] * 111_195.0])
        cs = np.array([comp[n] for n in ends])
        D = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
        D[cs[:, None] == cs[None, :]] = np.inf
        # every piece within GAP_M of another joined to it; then the rest joined end to end, nearest first (a
        # Kruskal tree over the pieces: a route's concurrency with a road mapped without its ref, US 6 along I-70)
        parent = list(range(k))

        def root(c):
            while parent[c] != c:
                parent[c] = parent[parent[c]]
                c = parent[c]
            return c
        order = np.argsort(D, axis=None)
        for flat in order:
            i, j = divmod(int(flat), len(ends))
            d = float(D[i, j])
            if not math.isfinite(d):
                break
            if i > j:
                continue
            ri, rj = root(cs[i]), root(cs[j])
            if ri == rj and d > GAP_M:
                continue
            parent[ri] = rj
            self.adj[ends[i]].append((ends[j], -1, d * 1.15))
            self.adj[ends[j]].append((ends[i], -1, d * 1.15))

    def _dijkstra(self, start):
        dist = {start: 0.0}
        q = [(0.0, start)]
        while q:
            d, u = heapq.heappop(q)
            if d > dist.get(u, math.inf):
                continue
            for v, _, w in self.adj[u]:
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    heapq.heappush(q, (nd, v))
        return dist

    def _calibrate(self, stones):
        """The mile at each mileage from the mapped mile markers [(lat, lon, mile)] on the route."""
        marks = []
        if stones:
            nodes = list(self.dist)
            P = np.array([self.coord[n] for n in nodes])
            kx = math.cos(math.radians(float(P[:, 0].mean()))) * 111_195.0
            for la, lo, mile in stones:
                d = np.hypot((P[:, 0] - la) * 111_195.0, (P[:, 1] - lo) * kx)
                j = int(np.argmin(d))
                if d[j] <= SNAP_M:
                    marks.append((self.dist[nodes[j]], float(mile)))
        marks.sort()
        good = []
        for s, m in marks:          # (counting up a mile a mile, give or take: else a mis-tagged marker)
            if not good or (s - good[-1][0] > 200.0 and 0.6 <= (m - good[-1][1]) / ((s - good[-1][0]) / MILE_M)
                            <= 1.5):
                good.append((s, m))
        if len(good) == 1 and len(marks) > 2:
            good = []
        self.marks = good

    def mile(self, s):
        g = self.marks
        if not g:
            return s / MILE_M
        if s <= g[0][0]:
            return g[0][1] + (s - g[0][0]) / MILE_M
        if s >= g[-1][0]:
            return g[-1][1] + (s - g[-1][0]) / MILE_M
        for (s0, m0), (s1, m1) in zip(g, g[1:]):
            if s <= s1:
                return m0 + (m1 - m0) * (s - s0) / max(1.0, s1 - s0)
        return g[-1][1]

    def walk(self, n, first, reach):
        """From node n along the route through its neighbour first, out to reach m: ((lat, lon), bearing of travel
        away from n), or (None, None) where the route ends (or turns back to n) sooner."""
        prev, cur, went = n, first, 0.0
        while True:
            seg = gs._dist(self.coord[prev], self.coord[cur])
            if went + seg >= reach:
                t = (reach - went) / max(seg, 1e-6)
                a, b = self.coord[prev], self.coord[cur]
                return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), gs._brg(a, b)
            went += seg
            b0 = gs._brg(self.coord[prev], self.coord[cur])
            nxt = [(abs(gs._turn(b0, gs._brg(self.coord[cur], self.coord[v]))), v)
                   for v, wi, _ in self.adj.get(cur, ()) if v != prev and wi >= 0 and self._may(cur, v, wi)]
            nxt = [t for t in nxt if t[0] < 75.0]
            if not nxt:
                return None, None
            prev, cur = cur, min(nxt)[1]

    def _may(self, a, b, wi):
        """May traffic go from a to b on way wi (one-way ways: only along their travel)?"""
        w = self.ways[wi]
        if not w["oneway"]:
            return True
        ns = w["nodes"]
        for i in range(len(ns) - 1):
            if ns[i] == a and ns[i + 1] == b:
                return True
        return False


def _in(box, p):
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def _oneway(tags):
    return tags.get("oneway") in ("yes", "1", "true", "-1") or tags.get("junction") == "roundabout" or \
        tags.get("highway") == "motorway"


def _ways(data):
    """The data's ways, directed along their travel where one-way: [{id, tags, nodes, geom, oneway, routes}] and
    {node: (lat, lon)}."""
    ways, coord = [], {}
    for w in data.get("ways", ()):
        tags = w.get("tags", {})
        if tags.get("highway", "").endswith("_link"):
            continue
        nodes, geom = list(w["nodes"]), [tuple(p) for p in w["geom"]]
        if len(nodes) < 2 or len(nodes) != len(geom):
            continue
        if tags.get("oneway") == "-1":
            nodes, geom = nodes[::-1], geom[::-1]
        for n, p in zip(nodes, geom):
            coord[n] = p
        ways.append({"id": int(w["id"]), "tags": tags, "nodes": nodes, "geom": geom, "oneway": _oneway(tags),
                     "routes": route_refs(tags)})
    return ways, coord


def _stones(data, routes):
    """The mapped mile markers: {route: [(lat, lon, mile)]} (by their ref, else the one route they stand beside)."""
    out = {}
    for n in data.get("nodes", ()):
        t = n.get("tags", {})
        if t.get("highway") != "milestone" or t.get("railway"):
            continue
        m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(mi|mile|miles)?\s*$", str(t.get("distance") or t.get("name")
                                                                            or ""))
        if not m:
            continue
        refs = route_refs(t)
        for r in refs or routes:
            out.setdefault(r, []).append((n["lat"], n["lon"], float(m.group(1))))
    return out


def plan(data):
    """The route sign items of a routes file's dict: [{"k": kind, "lat", "lon", "brg": travel bearing, ...}]:
    "rmile" (n: the mile), "rshield" (sh: [[network, number, cardinal]]), "jct" (sh: [[network, number, ""]]),
    "pass" (name, ft, divide) and "city" (name, label, ft)."""
    ways, coord = _ways(data)
    by_route = {}
    for wi, w in enumerate(ways):
        for r in w["routes"]:
            by_route.setdefault(r, []).append(w)
    clip = STATES.get(str(data.get("region", "")).lower())
    stones = _stones(data, list(by_route))
    items = []
    routes = {}
    for key in sorted(by_route, key=_rank):
        rt = Route(key, by_route[key], coord, clip, stones.get(key, ()))
        if rt.dist:
            routes[key] = rt
    # mile markers: on each way, of its first route (a concurrency signs the lowest: I, then US, then CO); not on the
    # Interstates (their exit numbers are their mileposts: guidesigns' markers)
    for key, rt in routes.items():
        if key[0] == "I":
            continue
        for wi, w in enumerate(rt.ways):
            if sorted(w["routes"], key=_rank)[0] != key:
                continue
            ns = w["nodes"]
            for a, b in zip(ns, ns[1:]):
                if a not in rt.dist or b not in rt.dist:
                    continue
                ma, mb = rt.mile(rt.dist[a]), rt.mile(rt.dist[b])
                lo, hi = min(ma, mb), max(ma, mb)
                if hi - lo > 3.0:           # (a gap bridged: no run of markers along it)
                    continue
                for M in range(int(math.floor(lo)) + 1, int(math.floor(hi)) + 1):
                    if M <= 0:
                        continue
                    t = (M - ma) / (mb - ma)
                    pa, pb = coord[a], coord[b]
                    p = (pa[0] + (pb[0] - pa[0]) * t, pa[1] + (pb[1] - pa[1]) * t)
                    brg = gs._brg(pa, pb)
                    for bb in ((brg,) if w["oneway"] else (brg, (brg + 180.0) % 360.0)):
                        items.append({"k": "rmile", "lat": p[0], "lon": p[1], "brg": bb, "n": M,
                                      "r": list(key), "mw": w["tags"].get("highway") == "motorway"})
                    # between the junctions, a shield now and then (the freeways': past their entrances)
                    if M % REASSURE_EVERY_MI == 5 and w["tags"].get("highway") != "motorway":
                        for bb, up in ((brg, mb > ma), ((brg + 180.0) % 360.0, mb < ma)):
                            if w["oneway"] and bb != brg:
                                continue
                            items.append({"k": "rshield", "lat": p[0], "lon": p[1], "brg": bb,
                                          "sh": [[key[0], key[1], _card(rt.ns, up)]], "jn": False})
    # junctions of two routes: the shields past, the JCT assembly ahead
    node_routes = {}
    for key, rt in routes.items():
        for n in rt.adj:
            node_routes.setdefault(n, set()).add(key)
    for n, rs in node_routes.items():
        if len(rs) < 2:
            continue
        for key in rs:
            rt = routes[key]
            mine = {wi for _, wi, _ in rt.adj[n] if wi >= 0}
            others = sorted({r for r in rs if r != key and not any(r in rt.ways[wi]["routes"] for wi in mine)},
                            key=_rank)
            if not others:
                continue                    # (a concurrency's ends are its routes' shared ways' ends)
            for v, wi, _ in rt.adj[n]:
                if wi < 0 or rt.ways[wi]["tags"].get("highway") == "motorway":
                    continue
                if rt._may(n, v, wi):
                    p, b = rt.walk(n, v, REASSURE_M)
                    if p is not None:
                        up = rt.dist.get(v, 0.0) > rt.dist.get(n, 0.0)
                        items.append({"k": "rshield", "lat": p[0], "lon": p[1], "brg": b,
                                      "sh": [[key[0], key[1], _card(rt.ns, up)]], "jn": True})
                if rt._may(v, n, wi):
                    p, b = _walk_back(rt, n, v, JCT_M)
                    if p is not None:
                        items.append({"k": "jct", "lat": p[0], "lon": p[1], "brg": b,
                                      "sh": [[o[0], o[1], ""] for o in others[:2]]})
    items = _dedupe(items, "rshield", 600.0, lambda it: tuple(map(tuple, it["sh"])))
    items = _dedupe(items, "jct", 300.0, lambda it: tuple(map(tuple, it["sh"])))
    items = _dedupe(items, "rmile", 250.0, lambda it: it["n"])
    items += passes(data, ways, coord)
    items += city_limits(data, ways, coord)
    return items


def _walk_back(rt, n, v, reach):
    """The point reach m before n on the way in from v's side: (lat, lon) and the bearing toward n."""
    p, b = rt.walk(n, v, reach)
    if p is None:
        return None, None
    return p, (b + 180.0) % 360.0


def _dedupe(items, kind, within, key):
    """items of kind kept within m apart for the same key and way of travel (the first kept)."""
    out, kept = [], {}
    cell = within / 111_195.0
    for it in items:
        if it["k"] == kind:
            k = key(it)
            p = (it["lat"], it["lon"])
            ci, cj = int(p[0] // cell), int(p[1] // cell)
            near = [q for di in (-1, 0, 1) for dj in (-1, 0, 1) for q in kept.get((k, ci + di, cj + dj), ())]
            if any(gs._dist(p, q) < within and abs(gs._turn(b, it["brg"])) < 45 for q, b in near):
                continue
            kept.setdefault((k, ci, cj), []).append((p, it["brg"]))
        out.append(it)
    return out


def passes(data, ways, coord):
    """A summit sign each way at each mapped pass on a route's road (name, elevation, the Divide)."""
    pas = [n for n in data.get("nodes", ()) if n.get("tags", {}).get("mountain_pass") == "yes"
           and n["tags"].get("name")]
    if not pas:
        return []
    at = {}
    for w in ways:
        for i, n in enumerate(w["nodes"]):
            at.setdefault(n, []).append((w, i))
    P = np.array([p for w in ways for p in w["geom"]])
    W = [(w, i) for w in ways for i in range(len(w["geom"]))]
    items = []
    for pn in pas:
        hits = at.get(pn["id"], ())
        best = None
        if hits:
            best = (0.0, hits[0][0], hits[0][1])
        else:                       # (a pass mapped beside its road)
            d = np.hypot((P[:, 0] - pn["lat"]) * 111_195.0,
                         (P[:, 1] - pn["lon"]) * 111_195.0 * math.cos(math.radians(pn["lat"])))
            j = int(np.argmin(d))
            if d[j] < PASS_SNAP_M:
                best = (float(d[j]), *W[j])
        if best is None:
            continue
        _, w, i = best
        g = w["geom"]
        a, b = g[max(0, i - 1)], g[min(len(g) - 1, i + 1)]
        if a == b:
            continue
        brg = gs._brg(a, b)
        name = pn["tags"]["name"]
        base = {"k": "pass", "lat": g[i][0], "lon": g[i][1], "name": name, "ft": _ft(pn["tags"].get("ele")),
                "divide": name.strip().lower() in DIVIDE}
        for bb in ((brg,) if w["oneway"] else (brg, (brg + 180.0) % 360.0)):
            items.append(dict(base, brg=bb))
    return items


def _ring_xy(pts, kx):
    a = np.asarray(pts, float)
    return np.column_stack([a[:, 1] * kx, a[:, 0] * 111_195.0])


def _signed_area(xy):
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float(np.sum(x[:-1] * y[1:] - x[1:] * y[:-1]))


def town_label(tags, places):
    """CITY LIMIT / TOWN LIMIT by the boundary's border_type; else a big one (OpenStreetMap's place tags go by size:
    Thornton's "town") a city; else by its place tag (the boundary's, its place node's)."""
    bt = (tags.get("border_type") or "").lower()
    if "town" in bt or "village" in bt:
        return "TOWN LIMIT"
    if "city" in bt or "county" in bt:
        return "CITY LIMIT"
    node = places.get(town_name(tags).lower(), {})
    try:
        if int(re.sub(r"[^0-9]", "", str(node.get("population") or "")) or 0) >= CITY_POP:
            return "CITY LIMIT"
    except ValueError:
        pass
    pl = tags.get("place") or node.get("place")
    return "TOWN LIMIT" if pl in ("town", "village") else "CITY LIMIT"


def town_name(tags):
    """A boundary's name on its sign ("City and County of Denver": Denver)."""
    return re.sub(r"^(city and county|city|town|county) of\s+", "", tags.get("name", ""), flags=re.I).strip()


CITY_POP = 25000            # a municipality this big with no border_type: a city

CELL_DEG = 0.005


def city_limits(data, ways, coord):
    """A town limit sign where a route's road enters a town (its admin_level 8 boundary): at the crossing, facing the
    traffic coming in; not where the road runs on inside it for less than CITY_STAY_M, nor again within
    CITY_AGAIN_M of the last entry into the same town that way."""
    towns = data.get("towns") or []
    if not towns or not ways:
        return []
    places = {}
    for n in data.get("nodes", ()):
        t = n.get("tags", {})
        if t.get("place") and t.get("name"):
            places.setdefault(t["name"].lower(), {"place": t["place"], "ft": _ft(t.get("ele")),
                                                  "population": t.get("population"), "lat": n["lat"],
                                                  "lon": n["lon"]})
    lat0 = float(np.mean([w["geom"][0][0] for w in ways]))
    kx = math.cos(math.radians(lat0)) * 111_195.0
    # the towns' ring edges, oriented with the town on their left, in a grid of CELL_DEG
    E0, E1, T = [], [], []
    for ti, tw in enumerate(towns):
        for r in tw["rings"]:
            xy = _ring_xy(r["pts"], kx)
            if len(xy) < 4:
                continue
            ccw = _signed_area(xy) > 0
            if (r["role"] == "inner") == ccw:
                xy = xy[::-1]
            E0.append(xy[:-1])
            E1.append(xy[1:])
            T.append(np.full(len(xy) - 1, ti))
    if not E0:
        return []
    E0, E1, T = np.concatenate(E0), np.concatenate(E1), np.concatenate(T)
    cs = CELL_DEG * 111_195.0
    grid = {}
    for i in range(len(E0)):
        _cells(grid, E0[i], E1[i], cs, i)
    # the roads' edges (both ways where two-way), their crossings; a short edge looks in its middle's cell only (the
    # ring edges are in their cells' neighbours too)
    items = []
    for w in ways:
        g = _ring_xy(w["geom"], kx)
        mid = (g[:-1] + g[1:]) / 2
        L = np.hypot(*(g[1:] - g[:-1]).T)
        cx = np.floor(mid / cs).astype(np.int64)
        for k in range(len(g) - 1):
            if L[k] <= cs * 0.5:
                cand = grid.get((int(cx[k, 0]), int(cx[k, 1])))
            else:
                got = set()
                for c in _cells(None, g[k], g[k + 1], cs, None, dilate=False):
                    got.update(grid.get(c, ()))
                cand = list(got)
            if not cand:
                continue
            idx = np.unique(np.asarray(cand, np.int64))
            hit = _cross(g[k], g[k + 1], E0[idx], E1[idx])
            for t, j, entering in hit:
                ti = int(T[idx[j]])
                for fwd in ((True,) if w["oneway"] else (True, False)):
                    if entering != fwd:
                        continue
                    items.append((w, k, t, fwd, ti))
    # keep the entries the road then stays in, one per town and way of travel within CITY_AGAIN_M
    out = []
    kept = {}
    for w, k, t, fwd, ti in items:
        a, b = w["geom"][k], w["geom"][k + 1]
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        brg = gs._brg(a, b) if fwd else gs._brg(b, a)
        if not _stays(w, k, t, fwd, ti, E0, E1, T, kx):
            continue
        tw = towns[ti]
        if any(gs._dist(p, q) < CITY_AGAIN_M and abs(gs._turn(bb, brg)) < 60 for q, bb in kept.get(ti, ())):
            continue
        kept.setdefault(ti, []).append((p, brg))
        name = town_name(tw["tags"])
        pl = places.get(name.lower(), {})
        out.append({"k": "city", "lat": p[0], "lon": p[1], "brg": brg, "name": name,
                    "label": town_label(tw["tags"], places), "ft": pl.get("ft"),
                    "mw": w["tags"].get("highway") == "motorway"})
    return out


def _cells(grid, a, b, cs, i, dilate=True):
    """The grid cells the segment a-b runs through (sampled every half cell), and their neighbours (dilate); with
    i, i added to each."""
    L = float(np.hypot(*(b - a)))
    n = max(1, int(L / (cs * 0.5)) + 1)
    got = set()
    for s in range(n + 1):
        p = a + (b - a) * (s / n)
        c = (int(math.floor(p[0] / cs)), int(math.floor(p[1] / cs)))
        if not dilate:
            got.add(c)
            continue
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                got.add((c[0] + dx, c[1] + dy))
    if i is not None:
        for c in got:
            grid.setdefault(c, []).append(i)
    return got


def _cross(p, q, A, B):
    """Where segment p-q crosses the edges A-B (n, 2): [(t along p-q, edge index, entering: crossing to the edge's
    left)]."""
    r = q - p
    s = B - A
    den = r[0] * s[:, 1] - r[1] * s[:, 0]
    ok = np.abs(den) > 1e-9
    ap = A - p
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (ap[:, 0] * s[:, 1] - ap[:, 1] * s[:, 0]) / den
        u = (ap[:, 0] * r[1] - ap[:, 1] * r[0]) / den
    hit = ok & (t >= 0) & (t < 1) & (u >= 0) & (u < 1)
    # crossing to the edge's left: the road's direction r has a positive component on the edge's left normal
    return [(float(t[j]), int(j), bool(s[j, 0] * r[1] - s[j, 1] * r[0] > 0)) for j in np.nonzero(hit)[0]]


def _stays(w, k, t, fwd, ti, E0, E1, T, kx):
    """Does the road run on at least CITY_STAY_M (along its way) after the entry before it leaves town ti?"""
    g = _ring_xy(w["geom"], kx)
    mine = np.nonzero(T == ti)[0]
    A, B = E0[mine], E1[mine]
    p0 = g[k] + (g[k + 1] - g[k]) * t
    went = 0.0
    seq = list(range(k + 1, len(g))) if fwd else list(range(k, -1, -1))
    cur = p0
    for j in seq:
        nxt = g[j]
        hits = [h for h in _cross(cur, nxt, A, B) if h[0] > 1e-6]
        if hits:
            tt = min(h[0] for h in hits)
            return went + tt * float(np.hypot(*(nxt - cur))) >= CITY_STAY_M
        went += float(np.hypot(*(nxt - cur)))
        if went >= CITY_STAY_M:
            return True
        cur = nxt
    return True                     # (the way ends inside: the next way runs on in town)


# ------------------------------------------------------------------------------------------------------------- faces

def route_shield_face(shields, jct=False):
    """A conventional road's route marker assembly: JCT over the shields, or each shield under its cardinal plate;
    bottom middle at the origin."""
    out = Face()
    parts = []
    for net, num, card in shields[:2]:
        sf, sw = gs.shield(net, num, SHIELD_H)
        g = Face()
        g.add(sf, -sw / 2, 0)
        h, w = SHIELD_H, sw
        if card and not jct:
            cf, (cw, ch) = _plate(card, 0.14, SHIELD_H)
            g.add(cf, 0, h + 0.05)
            h, w = h + ch + 0.05, max(w, cw)
        parts.append((g, w, h))
    if not parts:
        return out, (0.0, 0.0)
    W = sum(p[1] for p in parts) + 0.12 * (len(parts) - 1)
    x = -W / 2
    H = 0.0
    for g, w, h in parts:
        out.add(g, x + w / 2, 0)
        x += w + 0.12
        H = max(H, h)
    if jct:
        jf, (jw, jh) = _plate("JCT", 0.16, max(W, SHIELD_H))
        out.add(jf, 0, H + 0.05)
        H += jh + 0.05
        W = max(W, jw)
    return out, (W, H)


Face = gs.Face


def _plate(text, cap, width):
    """A black-on-white plate (a cardinal direction, JCT) width wide; bottom middle at the origin."""
    f = Face()
    W = max(width, gs.text_width(text, cap) + 0.12)
    H = round(cap * 2.0, 3)
    f.poly(gs.rounded_rect(-W / 2, 0, W / 2, H, 0.03), gs.WHITE, 0)
    f.text_c(text, cap, 0.0, (H - cap) / 2, "k", 2)
    return f, (W, H)


def _green(lines, pad=0.22, gap=0.14, min_w=1.2):
    """A green panel of white lines [(text, cap)] from the top; bottom middle at the origin."""
    f = Face()
    W = max([min_w] + [gs.text_width(s, c) + 2 * pad for s, c in lines])
    H = pad * 2 + sum(c for _, c in lines) + gap * (len(lines) - 1)
    f.framed(gs.rounded_rect(-W / 2, 0, W / 2, H, 0.10), gs.rounded_rect(-W / 2 + 0.03, 0.03, W / 2 - 0.03, H - 0.03, 0.08),
             gs.WHITE, gs.GREEN, 0)
    y = H - pad
    for s, c in lines:
        y -= c
        f.text_c(s, c, 0.0, y, "w", 2)
        y -= gap
    return f, (W, H)


def _clean(text):
    glyphs = gs.font()["glyphs"]
    out = []
    import unicodedata
    for ch in str(text):
        if ch != " " and ch not in glyphs:
            ch = unicodedata.normalize("NFKD", ch)[0]
        if ch == " " or ch in glyphs:
            out.append(ch)
    return "".join(out).strip()


def city_face(name, label, ft=None, big=False):
    """A city limit sign (I-1 style, Colorado's elevation under it): the name, CITY LIMIT, ELEV n."""
    c = 0.25 if big else 0.20
    lines = [(_clean(name), c), (label, c * 0.7)]
    if ft:
        lines.append((f"ELEV {ft:,}", c * 0.7))
    return _green(lines, min_w=1.5 if big else 1.2)


def pass_face(name, ft=None, divide=False):
    """A pass summit sign: its name, ELEV n FT, CONTINENTAL DIVIDE."""
    lines = [(_clean(name).upper(), 0.25)]
    if ft:
        lines.append((f"ELEV {ft:,} FT", 0.20))
    if divide:
        lines.append(("CONTINENTAL DIVIDE", 0.16))
    return _green(lines, pad=0.26, gap=0.16, min_w=2.0)


# --------------------------------------------------------------------------------------------------------------- tile

ROUTE_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential"}
KINDS = ("rmile", "rshield", "jct", "pass", "city")


def match(roads, xy, bvec, max_d=25.0):
    """guidesigns.match over every road class a route runs on (no ramps): the road the item stands by."""
    best = None
    for fid, spec, line, _ in roads:
        if spec.cls not in ROUTE_CLASSES or spec.link or len(line) < 2:
            continue
        d, s, pt, tan = gs._project(line, xy)
        if d > max_d + spec.width / 2:
            continue
        sgn = spec.dir if spec.dir else (1 if float(np.dot(tan, bvec)) >= 0 else -1)
        t = tan * sgn
        if float(np.dot(t, bvec)) < 0.7:
            continue
        if best is None or d < best["d"]:
            best = {"d": d, "fid": fid, "spec": spec, "line": line, "s": s, "pt": pt, "t": t, "sgn": sgn,
                    "hw": spec.width / 2, "lanes": gs._lanes(spec)}
    return best


def build(mesh, col, roads, it, xy, ground=None, left_hand=False, clear=None):
    """One route item into the tile's sign mesh, beside the road it stands by; its xy, or None (no road)."""
    b = np.array([math.sin(math.radians(it["brg"])), math.cos(math.radians(it["brg"]))])
    m = match(roads, xy, b)
    if m is None:
        return None
    k = it["k"]
    fast = m["spec"].cls in gs.HIGHWAY_MAIN
    if k == "rmile":
        face, size = gs.mile_face(it["n"])
        gs._side_sign(mesh, col, m, face, size, ground, left_hand, clear=clear, off=1.8 if fast else 1.2,
                      bottom=1.2,
                      collide=False)
    elif k in ("rshield", "jct"):
        face, size = route_shield_face([tuple(s) for s in it["sh"]], jct=k == "jct")
        if not size[0]:
            return None
        gs._side_sign(mesh, col, m, face, size, ground, left_hand, clear=clear, off=POST_OFF(fast), bottom=1.8)
    elif k == "pass":
        face, size = pass_face(it["name"], it.get("ft"), it.get("divide"))
        gs._side_sign(mesh, col, m, face, size, ground, left_hand, clear=clear, off=POST_OFF(fast) + 0.5,
                      bottom=1.6)
    elif k == "city":
        face, size = city_face(it["name"], it["label"], it.get("ft"), big=fast)
        gs._side_sign(mesh, col, m, face, size, ground, left_hand, clear=clear, off=POST_OFF(fast), bottom=1.8)
    return xy


def POST_OFF(fast):
    """m from the edge to a sign's near edge: a freeway's (clear of its barriers) or a two-lane road's."""
    return gs.POST_OFF_M if fast else 1.5
