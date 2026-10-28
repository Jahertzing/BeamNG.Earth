"""The skyline: tall buildings out to ~40 km (the user, 2026-10-28: see Denver's towers while driving toward it).

The near tiers stop at buildings.FAR_DROP_M (1.9 km: the road tiles' full buildings within 640 m, the far tiles' lite
mesh beyond), while the far terrain is drawn to 100 km and more: a city's towers rose out of the ground only in the
last two kilometres. This tier draws the tall buildings of every z12 Web-Mercator cell (mapcells, ~7.5 km at 40 N)
within SEND_M of the car, from the far tiles' own specs (buildings.tile_buildings: the same heights, roofs, facade
materials and tints, so a tower changes only in detail when the near tiers take it over), in the anchor's ENU
frame with the Earth's curvature (a building 30 km out stands ~70 m below the tangent plane, as the far terrain).

What is drawn (two tiers per cell, both keyed on the cell and nothing else, so a cell's content is a pure function of
its key, its tier, the Overture release and this code; docs/ROADMAP.md "Principle: deterministic generation"):
  * "tall": cells whose square comes within TALL_M of the car: Overture's buildings of >= 20 m or >= 6 floors
    (overture.LAYERS "tall_buildings": the filter runs in the fetch plan, so a row group without such a building costs
    its footer statistics only, and one with them its geometry only after its bbox and height columns said so);
  * "towers": the rest, out to SEND_M: >= 50 m or >= 15 floors ("towers"). A 20 m building 15 km away is under 2 px
    of a 1440p screen, and a metro's row groups (~20k buildings, ~2.5 MB each) nearly all hold one: the tall filter over
    40 km would fetch a whole metro (Denver ~100 MB, New York far more); the towers filter skips most of its suburbs.
Measured (Denver, 2026-10-28, a fresh cache): downtown's cell 12/853/1554 "tall" 618 buildings + 3,919 parts, 29 + 6 MB,
~4 s; "towers" 137 + 604 parts, 22 + 6 MB (DuckDB reads whole row groups' column chunks: downtown's 6 of 9 hold a match);
a suburb's "towers" 0 buildings for ~2 MB (the files' footers, shared by every cell of the file).

Footprints are simplified to MAX_CORNERS corners (their oriented box when that changes the area too much), each wall
is one quad in its facade's tint (mesh()), roofs flat or the near tiers' pitched box.

Hidden under the near tiers, exactly (none drawn twice): every building belongs to the anchor's 256 m road tile that
holds its footprint's centroid (roads.TILE, buildings.tile_buildings' rule), so a skyline tile is cut into one mesh per
such square ("parts", keyed "tx_ty" like the road and far building tiles) and the mod hides a part while that square's
road tile or far building tile shows its buildings (roads.lua: the same test that hides a far tile under its road
tile). Hundreds of parts for a city cell would cost as many objects and draw calls at 30 km, so each skyline tile also
has the whole cell's buildings merged ("shapes", split under buildings.MAX_MESH_VERTS): the mod draws the merged mesh
while the car is farther than PARTS_M (mod) from the cell and no part of it is covered, the parts only near it.

Sent nearest first (a cell ahead of the car's heading counting as nearer), one build at a time on the far building
pool and only while no far building tile is building (the near tiers come first), cached in the revisit cache like the
far tiles (blockworker.skyline_tile). Messages: skyline_tile {anchorId, key "x_y", path, n, tier}, skyline_drop
{anchorId, key}.
"""
import json
import logging
import math
import os

import numpy as np

import buildings
import mapcells
import staging

log = logging.getLogger("earthd.skyline")

ZOOM = 12                   # the skyline's cells (mapcells.CellKey at this zoom): ~7.5 km at 40 N, 1 DEM tile at DEM_ZOOM
SEND_M = 40000.0            # cells whose square (anchor frame) comes this close to the car are sent ...
DROP_M = 44000.0            # ... and dropped beyond this
TALL_M = 6000.0             # cells this near get the "tall" tier (>= 20 m), the rest the "towers" tier (>= 50 m) ...
TALL_HYST_M = 1500.0        # ... and keep it until this much farther (no rebuild back and forth at the edge)
LAYERS = {"tall": ("tall_buildings", "tall_building_parts"), "towers": ("towers", "tower_parts")}
TIER_CODE = {"tall": "t", "towers": "w"}    # (in the file names: a tier change loads new files)
PER_POSE = 1                # skyline tiles building at once (low priority: see the module docstring)
HEADING_BIAS = 0.4          # a cell straight ahead counts (1 - this) times as far; moving faster than HEADING_MIN_MS
HEADING_MIN_MS = 5.0
DEM_ZOOM = 12               # the ground under the buildings: the DEM at this zoom (~30 m pixels at 40 N)
SKIRT_M = 15.0              # m the walls reach below the footprint's lowest ground, on top of buildings.SKIRT_LITE_M:
                            # the far terrain's coarse levels lie up to tens of metres off the DEM between their vertices
MAX_CORNERS = 8             # footprints simplified to this many corners at most ...
BOX_AREA_TOL = 0.12         # ... or their oriented box when the simplified ring's area is off by more than this share
DATA_VERSION = 1            # the tile's JSON layout (the mod: roads.lua onSkyTile)


def cell_box(frame, key):
    """(x0, y0, x1, y1) of the z12 cell's square in the anchor frame: its corners and edge midpoints (the cell's edges
    are not straight in the tangent plane far off)."""
    lon0, lat0, lon1, lat1 = key.bounds()
    lons = np.array([lon0, lon1, lon1, lon0, (lon0 + lon1) / 2, lon1, (lon0 + lon1) / 2, lon0])
    lats = np.array([lat0, lat0, lat1, lat1, lat0, (lat0 + lat1) / 2, lat1, (lat0 + lat1) / 2])
    e, n, _ = frame.geodetic_to_enu(lats, lons, np.zeros(len(lats)))
    e, n = np.asarray(e, np.float64), np.asarray(n, np.float64)
    return float(e.min()), float(n.min()), float(e.max()), float(n.max())


def box_distance(box, x, y):
    """Euclidean distance from (x, y) to the box (0 inside)."""
    x0, y0, x1, y1 = box
    dx = max(x0 - x, 0.0, x - x1)
    dy = max(y0 - y, 0.0, y - y1)
    return math.hypot(dx, dy)


def cells_near(frame, x, y, radius):
    """The z12 cells whose square may come within radius of (x, y) (anchor frame): the cells under the lat/lon box
    radius around the point's position, in key order."""
    lat, lon, _ = frame.enu_to_geodetic(x, y, 0.0)
    lat, lon = float(lat), float(lon)
    dlat = radius / 111000.0
    dlon = radius / (111000.0 * max(0.05, math.cos(math.radians(lat))))
    lat0, lat1 = max(-mapcells.MAX_LAT, lat - dlat), min(mapcells.MAX_LAT, lat + dlat)
    return sorted(mapcells.cells_for_bbox(lat0, lon - dlon, lat1, lon + dlon, z=ZOOM))


def tier_for(d, had=None):
    """The tier of a cell at distance d from the car; had: the tier it has (the hysteresis)."""
    if d < TALL_M or (had == "tall" and d < TALL_M + TALL_HYST_M):
        return "tall"
    return "towers"


# ----------------------------------------------------------------------------------------------- footprints

def simplify_ring(ring, max_n=MAX_CORNERS):
    """A counter-clockwise ring (n, 2) (not closed) with at most max_n corners: the corner whose triangle with its
    neighbours is smallest dropped one at a time (Visvalingam), ties by index (deterministic); the ring's oriented box
    when that moves the area by more than BOX_AREA_TOL of it (a round or ragged outline)."""
    p = np.asarray(ring, np.float64)
    if len(p) <= max_n:
        return p
    area0 = buildings._area2(p) / 2
    keep = list(range(len(p)))
    while len(keep) > max_n:
        q = p[keep]
        prev, nxt = np.roll(q, 1, 0), np.roll(q, -1, 0)
        tri = np.abs((q[:, 0] - prev[:, 0]) * (nxt[:, 1] - prev[:, 1]) - (nxt[:, 0] - prev[:, 0]) * (q[:, 1] - prev[:, 1]))
        del keep[int(np.argmin(tri))]
    out = p[keep]
    a = buildings._area2(out) / 2
    if area0 <= 0 or a <= 0 or abs(a - area0) > BOX_AREA_TOL * area0:
        c, u, hl, hw = buildings.oriented_box(p)
        v = np.array([-u[1], u[0]])
        out = np.array([c - u * hl - v * hw, c + u * hl - v * hw, c + u * hl + v * hw, c - u * hl + v * hw])
    return out


def _owned_rows(f):
    """Rows of a layer this cell builds (mapdata `own`: the cell holding the bbox centre), all rows without the flag."""
    own = getattr(f, "own", None)
    return np.ones(len(f.kind), bool) if own is None else np.asarray(own, bool)


def part_keys(frame, f, tile=None):
    """{(tx, ty)} of the anchor's road tiles (roads.TILE) that may hold an owned building's centroid (its first ring's
    mean, give or take buildings.CENTRE_PREFILTER_M): the parts to build (tile_buildings decides exactly)."""
    import roads
    tile = tile or roads.TILE
    if f is None or len(f.kind) == 0:
        return set()
    cx, cy = f.outer_centres(frame)
    ok = _owned_rows(f) & np.isfinite(cx) & np.isfinite(cy)
    m = buildings.CENTRE_PREFILTER_M
    out = set()
    for dx in (-m, m):
        for dy in (-m, m):
            out.update(zip(np.floor((cx[ok] + dx) / tile).astype(np.int64).tolist(),
                           np.floor((cy[ok] + dy) / tile).astype(np.int64).tolist()))
    return out


def tile_items(frame, f, parts, elev, tx, ty):
    """[(Spec, gmin, gmax)] of the cell's owned buildings in the anchor's road tile (tx, ty) (rings relative to its
    corner), simplified, their walls SKIRT_M deeper."""
    import roads
    x0, y0, x1, y1 = roads.tile_bbox(tx, ty)
    own = _owned_rows(f)
    skip = frozenset(str(i) for i in np.asarray(f.ids)[~own].tolist()) if (~own).any() else frozenset()
    items = buildings.tile_buildings(frame, x0, y0, x1, y1, [f], elev,
                                     part_layers=[parts] if parts is not None else (), skip=skip)
    out = []
    for spec, gmin, gmax in items:
        spec.ring = simplify_ring(spec.ring)
        spec.gring = None
        out.append((spec, gmin - SKIRT_M, gmax))
    return out


def mesh(items):
    """The skyline's mesh of [(Spec, gmin, gmax)] (rings relative to one origin): build()'s arrays, or None. Each wall
    one quad from the skirt to the eave in its band's plain strip (buildings._plain_strip whole: the facade's tint, no
    windows: a floor's windows are ~1 px from 2 km, and a quad per floor and wall, the lite mesh's, made downtown
    Denver's cell 540k vertices); the roof as the near tiers draw it."""
    m = buildings.Mesh()
    for spec, gmin, gmax in items:
        if spec.cls == "toll_booth" or spec.cls in buildings.BRIDGE_CLASSES:
            continue
        mat_idx = buildings._Looks(spec.seed)
        ring = buildings.drawn_ring(spec)
        mh = spec.min_h or 0.0
        base = buildings.base_z(spec, gmin, gmax)
        z0 = gmin - buildings.SKIRT_LITE_M if mh <= 0.5 else base + mh
        z_eave = base + mh + spec.height
        fac = mat_idx[spec.facade]
        if spec.band:
            bpt = spec.band[1]
            v0, v1 = buildings._band_v(spec.band, spec.band[0])
        else:
            bpt, (v0, v1) = 1, (0.002, 0.995)
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            buildings._plain_strip(m, a, b, z0, z_eave, bpt, buildings.TILE_PX_M / bpt, v0, v1, fac, whole=True)
        if mh > 0.5:                                  # a part over another / a street: its underside
            m.poly([(p[0], p[1], z0) for p in ring[::-1]], [(0.002, 0.995)] * len(ring), fac)
        buildings._roof(m, spec, ring, z_eave, mat_idx)
    if m.n == 0:
        return None
    P, UV, T, M, C = m.arrays()
    return P, UV, T, M, buildings.MATERIALS, C


def _shifted(mesh, dx, dy):
    """A build() mesh moved by (dx, dy)."""
    P = mesh[0].copy()
    P[:, 0] += dx
    P[:, 1] += dy
    return (P,) + tuple(mesh[1:])


def _merge(meshes, max_verts=buildings.MAX_MESH_VERTS):
    """build() meshes (one origin) joined into as few as fit under max_verts vertices each (whole meshes)."""
    out, cur, n = [], [], 0
    for m in meshes:
        if cur and n + len(m[0]) > max_verts:
            out.append(buildings._concat(cur))
            cur, n = [], 0
        cur.append(m)
        n += len(m[0])
    if cur:
        out.append(buildings._concat(cur))
    return out


def build_tile(frame, key, tier, md, elev, out_dir, vfs_dir, aid, kind="prefetch"):
    """The skyline tile of z12 cell `key` at `tier` for anchor aid, written to the session folder: (vfs path of its
    JSON, buildings) (none drawn: no shape in it). elev(lat, lon): the ground (the DEM). The cell's layers are fetched
    with `kind` (a prefetch the hosts have no room for raises netfetch.Deferred: built again on a later pose)."""
    import roads
    bl, pl = LAYERS[tier]
    cd = md.cell(key, layers=(bl, pl), kind=kind)
    f, parts = cd.layers.get(bl), cd.layers.get(pl)
    box = cell_box(frame, key)
    ox = math.floor((box[0] + box[2]) / 2 / roads.TILE) * roads.TILE
    oy = math.floor((box[1] + box[3]) / 2 / roads.TILE) * roads.TILE
    code = TIER_CODE[tier]
    stem = f"sky_{aid}_{key.x}_{key.y}_{code}"
    data = {"v": DATA_VERSION, "tier": tier, "box": [round(v, 1) for v in box], "parts": [], "shapes": []}
    n = 0
    merged = []
    for tx, ty in sorted(part_keys(frame, f)):
        items = tile_items(frame, f, parts, elev, tx, ty)
        if not items:
            continue
        tm = mesh(items)
        if tm is None:
            continue
        n += len(items)
        x0, y0, _, _ = roads.tile_bbox(tx, ty)
        name = f"{stem}_p{tx}_{ty}.cdae"         # (~40 vertices a building: a 256 m square stays under the cap)
        buildings.write(os.path.join(out_dir, name), tm)
        data["parts"].append({"k": f"{tx}_{ty}", "shape": f"{vfs_dir}/{name}", "pos": [x0, y0, 0.0]})
        merged.append(_shifted(tm, x0 - ox, y0 - oy))
    for k, m in enumerate(_merge(merged)):
        name = f"{stem}_m{k}.cdae"
        buildings.write(os.path.join(out_dir, name), m)
        data["shapes"].append({"shape": f"{vfs_dir}/{name}", "pos": [ox, oy, 0.0]})
    name = f"{stem}.json"
    path = os.path.join(out_dir, name)
    tmp = staging.tmp_for(path)
    with open(tmp, "w") as fh:
        fh.write(json.dumps(data, separators=(",", ":")))
    staging.replace(tmp, path)
    return f"{vfs_dir}/{name}", n


# ----------------------------------------------------------------------------------------------- the tracker

class Tracker:
    """Per connection: which skyline cells the mod has, at which tier (as vegetation.Tracker for the anchor's tiles,
    with global z12 keys). build(aid, key, tier) -> (vfs path, n) or None, on the tracker's own thread."""

    def __init__(self, send_m=SEND_M, drop_m=DROP_M, per_pose=PER_POSE):
        self.send_m, self.drop_m, self.per_pose = send_m, drop_m, per_pose
        self.aid = None
        self.sent = {}              # CellKey -> tier the mod has (or that was built empty: nothing sent)
        self.shown = set()          # CellKeys the mod was sent a tile with shapes for (they get a skyline_drop)
        self.building = {}          # CellKey -> tier in flight
        self.futs = {}
        self.boxes = {}             # CellKey -> its square in the anchor frame (cell_box)
        self.pool = None

    def close(self):
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)

    def _reset(self, aid):
        for fu in self.futs.values():
            fu.cancel()
        self.aid, self.sent, self.shown, self.building, self.futs, self.boxes = aid, {}, set(), {}, {}, {}

    def wanted(self, frame, x, y, vel=None):
        """[(order, key, tier, d)] of the cells within send_m to build (missing or at another tier), best first; and
        the keys the mod has beyond drop_m."""
        fx = fy = 0.0
        if vel is not None:
            sp = math.hypot(vel[0], vel[1])
            if sp >= HEADING_MIN_MS:
                fx, fy = vel[0] / sp, vel[1] / sp
        want, far = [], []
        for key in cells_near(frame, x, y, self.drop_m):
            box = self.boxes.get(key)
            if box is None:
                box = self.boxes[key] = cell_box(frame, key)
            d = box_distance(box, x, y)
            if d > self.drop_m:
                if key in self.sent:
                    far.append(key)
                continue
            if d > self.send_m:
                continue
            tier = tier_for(d, self.sent.get(key))
            if self.sent.get(key) == tier or self.building.get(key) == tier:
                continue
            order = d
            if (fx or fy) and d > 0:
                cx, cy = (box[0] + box[2]) / 2 - x, (box[1] + box[3]) / 2 - y
                r = math.hypot(cx, cy)
                if r > 0:
                    order = d * (1.0 - HEADING_BIAS * max(0.0, (cx * fx + cy * fy) / r))
            want.append((order, key, tier, d))
        want.sort(key=lambda w: (w[0], w[1]))
        # keys the mod has that left the drop radius (cells_near covers drop_m; one the car left far behind is not in it)
        near = set(cells_near(frame, x, y, self.drop_m))
        far += [k for k in self.sent if k not in near]
        return want, sorted(set(far))

    def on_pose(self, conn, aid, frame, x, y, loop, build, vel=None, busy=False):
        """The cells to build started (none while busy: the near tiers' builds come first), those beyond drop_m
        dropped."""
        if aid != self.aid:
            self._reset(aid)
        if self.pool is None:
            from concurrent.futures import ThreadPoolExecutor
            self.pool = ThreadPoolExecutor(self.per_pose, thread_name_prefix="skyline-tile")
        want, far = self.wanted(frame, x, y, vel)
        for key in far:
            self.sent.pop(key, None)
            self.boxes.pop(key, None)
            if key in self.shown:
                self.shown.discard(key)
                conn.send({"type": "skyline_drop", "anchorId": aid, "key": f"{key.x}_{key.y}"})
        if busy:
            return
        for _, key, tier, _ in want[:max(0, self.per_pose - len(self.building))]:
            self.building[key] = tier
            fut = self.futs[key] = loop.run_in_executor(self.pool, build, aid, key, tier)
            fut.add_done_callback(lambda fu, k=key, t=tier, a=aid: self._built(conn, a, k, t, fu))

    def _built(self, conn, aid, key, tier, fut):
        if aid != self.aid:
            return
        if self.building.get(key) == tier:
            del self.building[key]
        self.futs.pop(key, None)
        if fut.cancelled():
            return
        try:
            res = fut.result()
        except Exception as e:          # (a deferred prefetch, a source error: asked again on a later pose)
            log.debug("skyline %s %s: %s: %s", key, tier, type(e).__name__, e)
            return
        if res is None:
            return
        path, n = res
        self.sent[key] = tier
        if n == 0 and key not in self.shown:
            return                      # nothing to draw, nothing drawn before: no message
        m = {"type": "skyline_tile", "anchorId": aid, "key": f"{key.x}_{key.y}", "path": path, "n": n, "tier": tier}
        out_dir = getattr(getattr(conn, "s", None), "out_dir", None)
        data = staging.inline_json(out_dir, path) if out_dir else None
        if data is not None:
            m["data"] = data
            pool = getattr(getattr(conn, "s", None), "pool_shapes", None)
            vfs = path.rsplit("/", 1)[0] + "/"
            if pool is not None and data.get("shapes"):     # the merged meshes at pooled shape paths (matslots.py)
                slots = pool(conn, {f"m{i}": s["shape"][len(vfs):] for i, s in enumerate(data["shapes"])
                                    if s["shape"].startswith(vfs)})
                if slots:
                    m["slots"] = slots
        if n > 0:
            self.shown.add(key)
        else:
            self.shown.discard(key)
        conn.send(m)
