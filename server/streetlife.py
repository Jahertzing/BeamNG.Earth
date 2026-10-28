"""Street life (2026-10-28, the Front Range drive: "make the drive from Frederick to Castle Rock feel lived-in"): the
small things along a street that say people live there, as Forest props (trees.lua rows, the street furniture's form)
placed per road tile, deterministic and pre-buildable (no streaming):

  - parking spots in house driveways for the stock parked cars (parking.lua: they had curbs, lots and aisles only, and
    a Colorado subdivision parks in its driveways), or (static_cars, not yet on) cars standing there as static props
  - a curbside mailbox at the end of each driveway on a street without a sidewalk (rural and estate lots: the US
    Postal Service's curbside delivery), a cluster box unit (CBU) every few dozen houses on subdivision streets with
    sidewalks (USPS centralized delivery: every subdivision platted since the 1970s-80s, all of Frederick's,
    Highlands Ranch's and Castle Rock's newer ones)
  - trash and recycling carts out at the end of some driveways (collection day somewhere every weekday)
  - mapped bus stops (OpenStreetMap's highway=bus_stop, Overture infrastructure transit/bus_stop): the stop's sign at
    the curb, a shelter where it is mapped with one (shelter=yes) and a bench where mapped (bench=yes)
  - downtown: the blue USPS collection boxes at corners, bike racks and planters along the main streets

Every placement hashes a key that does not depend on the anchor (a driveway's mouth in degrees; a road's id and
station), so a rebase or another anchor puts the same things in the same places. Pure: the callers give the geometry
(roads.build_tile's lines, walks and driveways) and the ground.
"""
import json
import math
import os

import numpy as np

import furniture
from furniture import _h01, _yaw

ENABLED = os.environ.get("BNE_STREETLIFE", "on").lower() not in ("off", "0", "false", "no")
# (prop groups, trees.lua CATALOG; every one in trees.lua STREET_LIFE, the streetLife setting)
G_CAR = "drivecar"
G_MAILBOX, G_CBU, G_USPS = "mailbox", "mailcbu", "uspsbox"
G_CART_TRASH, G_CART_RECYCLE = "cart_trash", "cart_recycle"
G_BUS_SIGN, G_BUS_SHELTER = "bussign", "busshelter"
G_BIKE_RACK, G_PLANTER = "bikerack", "planter"
GROUPS = (G_CAR, G_MAILBOX, G_CBU, G_USPS, G_CART_TRASH, G_CART_RECYCLE, G_BUS_SIGN, G_BUS_SHELTER, G_BIKE_RACK,
          G_PLANTER)

# The driveway cars: BeamNG's simple_traffic shapes (vehicles/simple_traffic/traffic_<model>.dae, the light cars the
# stock traffic and parked-car systems spawn), as trees.lua CATALOG drivecar variants; each row's height is its model's
# (trees.lua M.pick: the variant whose height matches, at scale 1), its rank picks among equal heights.
# model: (length, height m, share). Shares: the US fleet in a Colorado subdivision's driveways, SUVs and crossovers
# first, then pickups, sedans, minivans (Experian's 2024 registrations: light trucks ~80 % of new vehicles).
CARS = {
    "midsize": (4.85, 1.45, 0.14), "fullsize": (5.15, 1.50, 0.06), "pessima": (4.70, 1.44, 0.07),
    "covet": (4.15, 1.40, 0.04), "etk800": (4.90, 1.45, 0.03), "vivace": (4.30, 1.47, 0.04),
    "bastion": (5.05, 1.42, 0.02), "legran": (4.95, 1.42, 0.03), "lansdale": (5.15, 1.75, 0.10),
    "roamer": (5.10, 1.85, 0.14), "pickup": (5.60, 1.90, 0.16), "bx": (4.40, 1.30, 0.02),
    "wendover": (4.75, 1.40, 0.02), "sunburst2": (4.60, 1.45, 0.05), "van": (5.40, 2.05, 0.03),
    "etki": (4.65, 1.40, 0.01),
}
CAR_W_M = 1.9
SPOT_LEN_M = 6.0            # a stock parking spot's length (parking.lua SPOT_SCL)
HOUSE_GAP_M = 0.9           # m between the garage door (the drive's house end) and the first car's bumper
CAR_GAP_M = 0.8             # ... and between two cars in line
CAR_SHARE = {True: 0.62, False: 0.45}   # houses with a car in the driveway: a town (suburb) / a dense core
SECOND_SHARE = 0.38         # ... of those, a second car behind it (where it fits)
NOSE_IN_SHARE = 0.78        # cars nose in toward the garage (the rest backed in)
NO_WALK_SETBACK_M = 1.5     # m the cars stay off the road's edge where there is no sidewalk
WALK_SETBACK_M = 0.6        # ... and off the sidewalk's outer edge

MAILBOX_SIDE_M = 0.55       # m beside the driveway's edge
MAILBOX_IN_M = 0.45         # m in from the road's edge (the USPS: the box's face 6-8 in back of the curb or shoulder)
MAILBOX_H = 1.25            # m: the post and box (the box's floor 41-45 in up)
CBU_CELL_DEG = 0.0012       # a cluster box serves the houses of a ~130 x 100 m cell (8-16 houses) ...
CBU_MIN_HOUSES = 5          # ... with at least this many driveways on sidewalk streets in the tile
CBU_H = 1.75
CART_SHARE = 0.12           # houses with their carts out
CART_H = 1.07               # a 96-gallon cart: 42 in
WALK_NEAR_M = 7.0           # a sidewalk run this near the driveway's mouth (across it): a sidewalk street

BUS_SIGN_H = 3.6
BUS_SHELTER_H = 2.85
BUS_CURB_M = 0.7            # m behind the curb (the road's edge where no sidewalk is drawn)
BUS_SHELTER_BACK_M = 2.4    # m behind the sign
BUS_SNAP_M = 25.0           # a mapped stop this far from a road's edge at most (OSM maps them at the pole or on the way)
BUS_CLASSES = {"primary", "secondary", "tertiary", "trunk", "residential", "unclassified"}

USPS_SPACING_M = 420.0      # downtown: a blue collection box along a main street every this along each side
BIKE_SPACING_M = 160.0
PLANTER_SPACING_M = 55.0
DOWNTOWN_CLASSES = {"primary", "secondary", "tertiary"}
USPS_H, BIKE_RACK_H, PLANTER_H = 1.53, 1.16, 0.8     # (the stock shapes' heights: trees.lua CATALOG)


def _key(ll):
    """A stable hash key of a place (lat, lon degrees), ~1 m."""
    return (round(float(ll[0]), 5), round(float(ll[1]), 5))


def _pick_car(*key):
    r = _h01(*key, "car")
    acc = 0.0
    for model, (_, _, share) in CARS.items():
        acc += share
        if r < acc:
            return model
    return "midsize"


def _walk_index(walks):
    """(points (n, 2), their run's half width (n,), outer normal side unused) of every sidewalk run's vertices, densified
    to ~2 m so a point-to-run distance is a nearest-vertex lookup."""
    pts, half = [], []
    for wk in walks or ():
        if wk.get("median") or "pts" not in wk:
            continue
        p = np.asarray(wk["pts"], np.float64)[:, :2]
        if len(p) < 2:
            continue
        seg = np.diff(p, axis=0)
        L = np.hypot(*seg.T)
        for a, d, l in zip(p[:-1], seg, L):
            n = max(1, int(l // 2.0))
            t = np.arange(n)[:, None] / n
            pts.append(a + d * t)
            half.append(np.full(n, wk.get("w", 2.0) / 2))
        pts.append(p[-1:])
        half.append(np.full(1, wk.get("w", 2.0) / 2))
    if not pts:
        return np.zeros((0, 2)), np.zeros(0)
    return np.concatenate(pts), np.concatenate(half)


def _row(group, q, z, yaw, h, rank=0.0):
    return [group, round(float(q[0]), 2), round(float(q[1]), 2), round(float(z), 2), round(float(yaw) % 360.0, 1), 1.0,
            round(float(h), 2), round(float(rank), 3)]


def driveway_rows(drives, walks, box, z_at, to_ll, us=True, town=True, spots=None, static_cars=False, car_dz=None):
    """Rows of the driveways' mailboxes and carts and the subdivisions' cluster boxes. drives: buildings.doors'
    driveway chunks of the tile's houses ("n": points from the house's front to the road's edge); walks: the tile's
    sidewalk runs (dicts with pts, w); box: the tile (x0, y0, x1, y1); z_at(xy (k, 2)) -> the drawn ground's heights;
    to_ll(xy (k, 2)) -> (lat, lon) arrays (the anchor-free hash keys); us: the US (mailboxes and carts as the USPS and
    US towns have them); town: a suburb or town tile (buildings.coverage under POLES_COVERAGE), not a dense core.
    spots (a list) gets parking.py's spot rows [x, y, z, heading, 1] for the stock parked cars on the drives (one or two,
    from the garage door out, nosing in; kind 1: no street tracking), static_cars: drivecar rows instead (car_dz: model ->
    m its shape's origin stands above the ground)."""
    x0, y0, x1, y1 = box
    wp, wh = _walk_index(walks)
    car_dz = car_dz or {}
    places = []                     # (q (2,), group, yaw, height, rank, dz)
    spot_at = []                    # (q (2,), heading)
    cbu_cand = {}                   # cell -> [(hash, q, yaw)]
    for d in drives or ():
        n = np.asarray(d.get("n") or (), np.float64)
        if len(n) < 2:
            continue
        p0, p1 = n[0, :2], n[-1, :2]
        v = p0 - p1
        L = float(np.hypot(*v))
        if L < 3.0:
            continue
        u = v / L                                   # toward the house
        s = np.array([-u[1], u[0]])                 # across the drive
        half_w = float(d.get("w", 3.2)) / 2
        lat, lon = (float(a[0]) for a in to_ll(p1[None]))
        k = _key((lat, lon))
        # a sidewalk across the mouth: the nearest sidewalk vertex within WALK_NEAR_M, in front of the house
        outer = None
        if len(wp):
            rel = wp - p1[None]
            along = rel @ u
            across = np.abs(rel @ s)
            ok = (along > -1.0) & (along < WALK_NEAR_M) & (across < 6.0)
            if ok.any():
                i = np.flatnonzero(ok)[int(np.argmin(np.hypot(*rel[ok].T)))]
                outer = float(along[i] + wh[i])
        setback = (outer + WALK_SETBACK_M) if outer is not None else NO_WALK_SETBACK_M
        # cars, from the garage door out
        if _h01(k, "has") < CAR_SHARE[bool(town)]:
            room = L - setback - HOUSE_GAP_M
            at = HOUSE_GAP_M
            for j in range(2):
                model = _pick_car(k, j)
                ln, h, _ = CARS[model]
                if spots is not None and not static_cars:
                    ln = SPOT_LEN_M
                if room < ln or (j == 1 and _h01(k, "second") >= SECOND_SHARE):
                    break
                c = p0 - u * (at + ln / 2)
                if static_cars:
                    nose = u if _h01(k, j, "nose") < NOSE_IN_SHARE else -u
                    places.append((c, G_CAR, _yaw(nose[0], nose[1]), h, _h01(k, j, "rank"), car_dz.get(model, 0.0)))
                elif spots is not None:
                    spot_at.append((c, _yaw(u[0], u[1])))
                at += ln + CAR_GAP_M
                room -= ln + CAR_GAP_M
        if not us:
            continue
        side = 1.0 if _h01(k, "mside") < 0.5 else -1.0
        if outer is None:
            # a curbside mailbox beside the drive, its door to the road
            q = p1 + u * MAILBOX_IN_M + s * side * (half_w + MAILBOX_SIDE_M)
            places.append((q, G_MAILBOX, _yaw(-u[0], -u[1]), MAILBOX_H, _h01(k, "mb"), 0.0))
        elif town:
            cell = (math.floor(lat / CBU_CELL_DEG), math.floor(lon / CBU_CELL_DEG))
            q = p1 + u * (outer + 0.6) + s * side * (half_w + 1.6)
            cbu_cand.setdefault(cell, []).append((_h01(k, "cbu"), q, _yaw(-u[0], -u[1])))
        if _h01(k, "cart") < CART_SHARE:
            # the trash and recycling carts at the end of the drive, on its side away from the mailbox
            for j, g in enumerate((G_CART_TRASH, G_CART_RECYCLE)):
                q = p1 + u * 0.7 - s * side * (half_w - 0.45 - 0.75 * j)
                places.append((q, g, _yaw(-u[0], -u[1]) + 8.0 * (_h01(k, j, "cy") - 0.5), CART_H, 0.0, 0.0))
    for cell, cand in cbu_cand.items():
        if len(cand) >= CBU_MIN_HOUSES:
            _, q, yaw = min(cand, key=lambda c: c[0])
            places.append((q, G_CBU, yaw, CBU_H, 0.0, 0.0))
    if spot_at:
        S = np.array([q for q, _ in spot_at], np.float64)
        Z = np.asarray(z_at(S), np.float64)
        for (q, hd), z in zip(spot_at, Z):
            if x0 <= q[0] < x1 and y0 <= q[1] < y1 and math.isfinite(z):
                spots.append([round(float(q[0]), 2), round(float(q[1]), 2), round(float(z), 2), round(hd % 360.0, 1), 1])
    if not places:
        return []
    Q = np.array([p[0] for p in places], np.float64)
    inside = (Q[:, 0] >= x0) & (Q[:, 0] < x1) & (Q[:, 1] >= y0) & (Q[:, 1] < y1)
    Z = np.asarray(z_at(Q), np.float64)
    return [_row(g, q, z + dz, yaw, h, rank) for (q, g, yaw, h, rank, dz), z, ok in zip(places, Z, inside) if ok]


def _tags(v):
    if isinstance(v, dict):
        return v
    try:
        t = json.loads(v) if v else {}
    except (TypeError, ValueError):
        return {}
    return t if isinstance(t, dict) else {}


def mapped_bus_stops(frame, infra_list):
    """[(x, y, shelter, bench)] of OpenStreetMap's bus stops (Overture infrastructure class bus_stop) in infra_list (the
    map cells' infrastructure Features; duplicates once), in the anchor frame."""
    seen, out = set(), []
    for f in infra_list or ():
        if f is None or len(f) == 0:
            continue
        cls = f.attrs.get("class")
        if cls is None:
            continue
        tags = f.attrs.get("tags")
        for i in np.flatnonzero(np.asarray(cls).astype(str) == "bus_stop"):
            fid = str(f.ids[i])
            if fid in seen:
                continue
            seen.add(fid)
            a = f.coord_off[f.ring_off[f.part_off[i]]]
            b = f.coord_off[f.ring_off[f.part_off[i + 1]]]
            lo, la = f.lonlat[a:b].mean(axis=0)
            t = _tags(tags[i]) if tags is not None else {}
            e, n_, _ = frame.geodetic_to_enu(np.array([la]), np.array([lo]), 0.0)
            out.append((float(np.asarray(e)[0]), float(np.asarray(n_)[0]), t.get("shelter") == "yes",
                        t.get("bench") == "yes"))
    return out


def bus_stops_for(md, frame, keys):
    """mapped_bus_stops of the map cells keys (md: mapdata.MapData); [] when they cannot be read."""
    try:
        infra = [md.cell(k, layers=("infrastructure",), kind="demand").layers.get("infrastructure") for k in keys]
    except Exception:
        return []
    return mapped_bus_stops(frame, infra)


def bus_stop_rows(stops, lines, box, z_at, left_hand=False):
    """Rows of the bus stops (mapped_bus_stops) in the tile box: the sign at the back of the curb of the nearest
    street (lines: build_tile's [(centreline (k, 2+), half width, class, urban)]), facing the oncoming traffic; a
    shelter behind it where mapped, else a bench where mapped."""
    x0, y0, x1, y1 = box
    out = []
    segs = []
    for ln in lines or ():
        if len(ln) > 2 and ln[2] not in BUS_CLASSES:
            continue
        p = np.asarray(ln[0], np.float64)[:, :2]
        for a, b in zip(p[:-1], p[1:]):
            segs.append((a, b, float(ln[1])))
    if not segs:
        return out
    A = np.array([s[0] for s in segs])
    B = np.array([s[1] for s in segs])
    H = np.array([s[2] for s in segs])
    D = B - A
    L2 = np.maximum((D * D).sum(1), 1e-12)
    places = []
    for x, y, shelter, bench in stops:
        if not (x0 <= x < x1 and y0 <= y < y1):
            continue
        q = np.array([x, y])
        t = np.clip(((q - A) * D).sum(1) / L2, 0, 1)
        near = A + t[:, None] * D
        dist = np.hypot(*(q - near).T) - H
        k = int(np.argmin(dist))
        if dist[k] > BUS_SNAP_M:
            continue
        d = D[k] / math.sqrt(L2[k])
        n = np.array([-d[1], d[0]])
        side = 1.0 if float((q - near[k]) @ n) >= 0 else -1.0
        out_n = n * side                            # away from the road, on the stop's side
        p = near[k] + out_n * (H[k] + BUS_CURB_M)
        # the traffic on this side drives along d on the right (side -1) in right-hand countries
        flow = d if (side < 0) != bool(left_hand) else -d
        # (the stock sign's plate stands out from its pole along its local +x, faces along y: +x to the road shows it
        # to the traffic both ways; the shelter, 3.9 m along its y, its open side +x to the road)
        places.append((p, G_BUS_SIGN, _yaw(-out_n[0], -out_n[1]), BUS_SIGN_H))
        if shelter:
            places.append((p + out_n * BUS_SHELTER_BACK_M - flow * 3.0, G_BUS_SHELTER,
                           _yaw(-out_n[0], -out_n[1]), BUS_SHELTER_H))
        elif bench:
            places.append((p + out_n * 1.4 - flow * 2.0, furniture.G_BENCH,
                           _yaw(-out_n[0], -out_n[1]) + furniture.BENCH_YAW, 0.9))
    if not places:
        return out
    Z = np.asarray(z_at(np.array([p[0] for p in places])), np.float64)
    return [_row(g, q, z, yaw, h) for (q, g, yaw, h), z in zip(places, Z)]


def downtown_rows(roads, walks, box, us=True, town=True, curb_h=0.15):
    """Rows of a dense core's (not town) main streets' extras along their sidewalks: blue collection boxes (us: the
    US), bike racks, and planters on the streets without a row of street trees. roads: build_tile's (fid, spec,
    centreline (k, 3), lon); walks: its sidewalk runs (dicts with fid, pts, side, w)."""
    if town:
        return []
    x0, y0, x1, y1 = box
    by_fid = {fid: (spec, pts) for fid, spec, pts, _ in roads}
    rows = []
    for wk in walks or ():
        fid = wk.get("fid")
        if fid is None or "pts" not in wk or fid not in by_fid or wk.get("median"):
            continue
        spec, line = by_fid[fid]
        if not spec.urban or spec.cls not in DOWNTOWN_CLASSES:
            continue
        run = np.asarray(wk["pts"], np.float64)
        if len(run) < 2:
            continue
        side, w = wk["side"], wk.get("w", 2.5)

        def put(group, spacing, phase, curb_off, height, yaw_fn, clear=furniture.END_CLEAR_M):
            for p, out in furniture._stations(run, line, spacing, phase, clear, side):
                q = p[:2] - out * (w / 2 - curb_off)
                if x0 <= q[0] < x1 and y0 <= q[1] < y1:
                    rows.append(_row(group, q, p[2] + curb_h, yaw_fn(out), height))
        if us:      # near a corner (the run's start), against the curb
            put(G_USPS, USPS_SPACING_M, _h01(fid, side, "usps") * USPS_SPACING_M, 0.55, USPS_H,
                lambda out: _yaw(out[0], out[1]), clear=furniture.END_CLEAR_M + 2.0)
        put(G_BIKE_RACK, BIKE_SPACING_M, _h01(fid, side, "bike") * BIKE_SPACING_M, 0.8, BIKE_RACK_H,
            lambda out: _yaw(-out[1], out[0]))
        if w >= 3.5 and not (spec.cls in furniture.STREET_TREE_CLASSES
                             and _h01(fid, "trees") < furniture.STREET_TREE_SHARE):
            put(G_PLANTER, PLANTER_SPACING_M, _h01(fid, side, "planter") * PLANTER_SPACING_M, 0.9, PLANTER_H,
                lambda out: _yaw(-out[1], out[0]), clear=furniture.END_CLEAR_M + 6.0)
    return rows


CLEAR_M = 1.2               # m a street-life prop keeps from the street furniture already placed (doors, fences, lamps,
                            # hydrants, trees)


def clear_of(rows, props, r=CLEAR_M):
    """rows without those within r of a props row (x, y at [1], [2]); props rows' own groups are not compared."""
    if not rows or not props:
        return rows
    P = np.array([(p[1], p[2]) for p in props if len(p) > 2], np.float64)
    if not len(P):
        return rows
    out = []
    grid = {}
    for i, (x, y) in enumerate(P):
        grid.setdefault((int(x // 4), int(y // 4)), []).append(i)
    for row in rows:
        gx, gy = int(row[1] // 4), int(row[2] // 4)
        near = [i for dx in (-1, 0, 1) for dy in (-1, 0, 1) for i in grid.get((gx + dx, gy + dy), ())]
        if near and (np.hypot(P[near, 0] - row[1], P[near, 1] - row[2]) < r).any():
            continue
        out.append(row)
    return out


BUS_STOP_CLEAR_M = 12.0     # m of curb kept free of parked cars each side of a bus stop's sign (a bus stop zone: 60-80 ft)


def tile_rows(frame, md, keys, drives, walks, lines, box, ground, us, town, left_hand=False, props=(), spots=None):
    """The driveway and bus stop rows of a road tile (driveway_rows, bus_stop_rows), clear of the tile's props. frame:
    the anchor frame; md, keys: the map data and the tile's cells (the mapped bus stops); drives: the houses' driveway
    chunks (buildings.doors'); ground(x (n,), y (n,)) -> the drawn ground (NaN off the block: left out); spots (a list)
    gets the driveways' parking spots."""
    def z_at(xy):
        xy = np.asarray(xy, np.float64).reshape(-1, 2)
        return np.asarray(ground(xy[:, 0], xy[:, 1]), np.float64)

    def to_ll(xy):
        la, lo, _ = frame.enu_to_geodetic(xy[:, 0], xy[:, 1], 0.0)
        return np.asarray(la, np.float64).reshape(-1), np.asarray(lo, np.float64).reshape(-1)
    rows = driveway_rows(drives, walks, box, z_at, to_ll, us=us, town=town, spots=spots)
    if md is not None and lines:
        rows += bus_stop_rows(bus_stops_for(md, frame, keys), lines, box, z_at, left_hand)
    rows = [r for r in rows if math.isfinite(r[3])]
    return clear_of(rows, props)
