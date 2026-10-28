"""Landmarks (Jeff, 2026-10-28: a beautiful drive from Frederick to downtown Denver and on to Castle Rock): the shapes
the generic buildings could not draw, and a table of the drive's landmarks.

  * Curved roofs (buildings.plan, OSM roof:shape through Overture's roof_shape): "dome", "onion" and "conical" (and a
    steep "pyramidal" on a near-square footprint: a spire) were drawn as gables (the State Capitol's gold dome came out
    a gable roof). Each is now a surface of revolution over the footprint itself: its outline scaled towards its
    centroid along a profile (a quarter circle, an onion's bulb, a straight cone), smooth (shared vertices), from the
    eaves to the mapped top. A building part keeps its own curved roof (a dome mapped as a part on its drum).
  * Open-bowl stadiums: a stadium mapped as one building over its field was a solid box. A footprint of class stadium
    (or a landmark set to it) of STADIUM_MIN_M2 or more is drawn as a bowl: the outer wall, an upper and a lower tier of
    raked stands with the club level's glass between them, the field wall, the field open to the sky (the photo and the
    terrain under it), and a roof canopy over the upper stands where the landmark table asks for one.
  * The landmark table (landmarks.json): per building, matched by its OSM id (Overture's src_record, "w123@4") or by a
    point its footprint holds (which also finds DRCOG's roofprints), attributes set over the map's (height, roof shape
    and height, class, the dome's gold, the facade's granite, a stadium's seat colour); and pieces placed by latitude and
    longitude where no footprint carries them (a dome on its drum, a spire, the Castle Rock Star). The buildings
    matched by OSM id keep their OSM outline and parts over DRCOG's roofprints (drcog.merge_buildings).

Pure functions of the specs, the table and a ground-height function, as buildings.py.
"""
import json
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = os.path.join(HERE, "landmarks.json")

CURVED = ("dome", "onion", "cone", "barrel")
# OSM roof:shape -> the curved roof drawn ("round": a barrel vault along the oriented box, the Wells Fargo Center's
# "cash register" top and Union Station's arched roofs)
SHAPES = {"dome": "dome", "onion": "onion", "conical": "cone", "cone": "cone", "spire": "cone", "round": "barrel",
          "round_skillion": "barrel"}
# the roof's rise, x the footprint's equivalent radius (sqrt(area / pi)), when not mapped (roof:height); a barrel's x
# its half width
RISE = {"dome": 1.0, "onion": 1.7, "cone": 2.5, "barrel": 0.6}
BARREL_STEPS = 12
BIG_DOME_R = 25.0           # m: a dome this wide without a mapped rise is an arena's shallow roof ...
SHALLOW_RISE = 0.18         # ... rising this share of its radius
BIG_SPIRE_R = 12.0          # m: a cone / onion this wide without a mapped rise is a dome's height at most
SPIRE_ASPECT = 1.3          # a mapped pyramidal roof on a footprint at most this elongated (long / short side) ...
SPIRE_STEEP = 1.2           # ... rising more than this x its half width: a spire (a cone over the square)
SMALL_CURVED_M2 = 150.0     # m2: a curved-roofed footprint this small has plain stone walls (a lantern, a spire's base)
DOME_STEPS = 10             # profile rings of a dome (the eaves to the top)
DENSIFY_M = 4.0             # m: the outline's edges cut into pieces at most this long (a smooth curved surface)
DENSIFY_MAX = 96            # ... up to this many corners

# a dome's and an onion's profiles: (scale of the outline towards its centroid, share of the rise)
_DOME = tuple((math.cos(t), math.sin(t)) for t in np.linspace(0.0, math.pi / 2, DOME_STEPS + 1).tolist())
_ONION = ((1.0, 0.0), (1.10, 0.10), (1.16, 0.22), (1.12, 0.34), (0.98, 0.46), (0.76, 0.58), (0.52, 0.70),
          (0.32, 0.80), (0.17, 0.89), (0.07, 0.96), (0.0, 1.0))
_CONE = ((1.0, 0.0), (0.0, 1.0))
PROFILES = {"dome": _DOME, "onion": _ONION, "cone": _CONE}

GOLD = "bne_gold"           # buildings.LOOKS names of the curved roofs' looks
COPPER = "bne_copper"
LOOK_NAMES = {"gold": GOLD, "golden": GOLD, "copper": COPPER, "green": COPPER, "verdigris": COPPER, "teal": COPPER}

STADIUM_CLASSES = ("stadium",)
STADIUM_MIN_M2 = 8000.0     # a stadium footprint this large (a field and its stands) is an open bowl
STADIUM_H = 30.0            # m: its rim when not mapped
STANDS_SHARE = 0.24         # the stands' depth: this share of the footprint's equivalent diameter ...
STANDS_M = (14.0, 60.0)     # ... in this range
FIELD_MIN_R = 0.35          # the field keeps this share of the outline's distance to its centroid at least
FRONT_M = 2.5               # m: the first row over the field
CLUB_M = 5.0                # m: the club level's glass between the tiers
LIP_M = 2.0                 # m: the rim's parapet over the top row ...
RIM_M = 0.6                 # ... this thick
CANOPY_SHARE = 0.4          # of the stands' depth: the canopy's reach in from the rim
CANOPY_UP_M = 6.0           # m over the rim
SEAT_RGB = (0.42, 0.46, 0.52)   # the seats' colour where the table gives none

STAR_ARMS = 5
STAR_INNER = 0.38           # the inner corners' radius, of the outer's
STAR_TUBE_M = 0.35          # m: the lit tube's thickness
STAR_POST_M = 0.3           # m: its posts' thickness


# ------------------------------------------------------------------------------------------- the table

PROTECT_PAD_DEG = 0.004     # (~400 m: a building's point may lie off the cell its footprint is in)

_table = None
_table_mtime = None


def table(path=None):
    """The landmark table: {"buildings": [...], "pieces": [...]} (landmarks.json; {} parts when missing). Kept loaded,
    reloaded when the file changes. Every building entry gets "_ids" (the set of its OSM ids without versions)."""
    global _table, _table_mtime
    p = path or TABLE
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {"buildings": [], "pieces": []}
    if path is None and _table is not None and _table_mtime == mt:
        return _table
    with open(p, encoding="utf-8") as f:
        t = json.load(f)
    t.setdefault("buildings", [])
    t.setdefault("pieces", [])
    for e in t["buildings"]:
        e["_ids"] = {str(i).split("@")[0] for i in e.get("osm", ())}
    if path is None:
        _table, _table_mtime = t, mt
    return t


def osm_key(rec):
    """An Overture src_record ("w123@4") without its version: "w123"; '' for none."""
    return str(rec or "").split("@")[0]


def by_osm(t=None):
    """{OSM id: building entry} of the table."""
    t = t if t is not None else table()
    return {i: e for e in t["buildings"] for i in e["_ids"]}


def protected_ids(t=None):
    """The OSM ids whose buildings keep their own outline and parts over DRCOG's roofprints."""
    return set(by_osm(t))


def protect_tag(bounds, t=None):
    """A short tag of the table's OSM-matched buildings whose point ("at") is in a map cell's bounds (lon0, lat0,
    lon1, lat1; PROTECT_PAD_DEG round them), '' for none: the cell's DRCOG-merged buildings are cached under it
    (mapdata.path), so a change to those entries re-merges only their cells."""
    import hashlib
    t = t if t is not None else table()
    lon0, lat0, lon1, lat1 = bounds
    pad = PROTECT_PAD_DEG
    ids = sorted(i for e in t["buildings"] if e.get("at") and e["_ids"]
                 and lat0 - pad <= float(e["at"][0]) <= lat1 + pad and lon0 - pad <= float(e["at"][1]) <= lon1 + pad
                 for i in e["_ids"])
    if not ids:
        return ""
    return "lm" + hashlib.sha1(",".join(ids).encode()).hexdigest()[:8]


def points_in_tile(frame, x0, y0, x1, y1, t=None, margin=0.0):
    """[(building entry, (x, y) relative to (x0, y0))] of the table's point-matched buildings ("at") in the tile."""
    t = t if t is not None else table()
    out = []
    for e in t["buildings"]:
        at = e.get("at")
        if not at:
            continue
        x, y, _ = frame.geodetic_to_enu(float(at[0]), float(at[1]), 0.0)
        x, y = float(x), float(y)
        if x0 - margin <= x < x1 + margin and y0 - margin <= y < y1 + margin:
            out.append((e, (x - x0, y - y0)))
    return out


def apply(attrs, entry):
    """A building's (or part's) attributes with the entry's "set" over them (a new dict)."""
    s = entry.get("set") or {}
    out = dict(attrs)
    for k, v in s.items():
        out[k] = tuple(v) if isinstance(v, list) else v
    out["landmark"] = entry.get("name") or True
    return out


# ------------------------------------------------------------------------------------------- curved roofs

def curved_shape(roof_given, attrs, ring_area, hl, hw):
    """The curved roof ("dome", "onion", "cone") a footprint's roof:shape asks for, or None. A mapped pyramidal roof
    is a spire (a cone over the footprint) on a near-square footprint whose mapped rise is steep."""
    s = SHAPES.get(roof_given)
    if s is not None:
        return s
    if roof_given == "pyramidal":
        rh = attrs.get("roof_height")
        if rh is not None and np.isfinite(rh) and hw > 0.5 and hl <= SPIRE_ASPECT * hw and rh > SPIRE_STEEP * hw:
            return "cone"
    return None


def radius(area):
    return math.sqrt(max(area, 1e-6) / math.pi)


def default_rise(shape, area, hw=None):
    """The rise of a curved roof without a mapped roof:height (a barrel's from its half width hw)."""
    if shape == "barrel":
        return RISE["barrel"] * float(hw if hw is not None else radius(area))
    r = radius(area)
    if shape == "dome" and r > BIG_DOME_R:
        return SHALLOW_RISE * r
    if shape in ("cone", "onion") and r > BIG_SPIRE_R:
        return r
    return RISE[shape] * r


def look_of(attrs):
    """The buildings.LOOKS name for a curved roof from the table's roof_look or the map's roof colour / material, or
    None (the default: buildings.plan's metal roof)."""
    for k in ("roof_look", "roof_color", "roof_material"):
        v = str(attrs.get(k) or "").strip().lower()
        if not v:
            continue
        if v in LOOK_NAMES:
            return LOOK_NAMES[v]
        if v.startswith("#") and len(v) in (4, 7):
            try:
                h = v[1:] if len(v) == 7 else "".join(c * 2 for c in v[1:])
                r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
            except ValueError:
                continue
            if r > 0.6 and g > 0.45 and b < 0.45 * r and r - b > 0.3:
                return GOLD
            if g > r + 0.08 and g >= b - 0.05:
                return COPPER
    return None


def centroid(ring):
    """The area centroid of a ring (n, 2)."""
    p = np.asarray(ring, np.float64)
    q = np.roll(p, -1, 0)
    cr = p[:, 0] * q[:, 1] - q[:, 0] * p[:, 1]
    a = cr.sum() / 2
    if abs(a) < 1e-9:
        return p.mean(0)
    return np.array([((p[:, 0] + q[:, 0]) * cr).sum(), ((p[:, 1] + q[:, 1]) * cr).sum()]) / (6 * a)


def densify(ring, step=DENSIFY_M, max_n=DENSIFY_MAX):
    """The ring with its long edges cut into pieces at most `step` long (fewer pieces when it would pass max_n)."""
    p = np.asarray(ring, np.float64)[:, :2]
    n = len(p)
    L = np.hypot(*(np.roll(p, -1, 0) - p).T)
    total = float(L.sum())
    if total <= 0:
        return p
    step = max(step, total / max_n)
    out = []
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        k = max(1, int(math.ceil(L[i] / step)))
        for j in range(k):
            out.append(a + (b - a) * (j / k))
    return np.array(out)


def _surface(m, ring, c, z0, rise, profile, mat, uv_m=6.0):
    """A surface of revolution: the ring (n, 2) scaled towards c along the profile [(scale, share of rise)], from z0
    up by rise, closed at the top by a fan to the apex (the profile's last scale 0). Shared vertices (smooth
    shading); the ring's first column repeated at its end (the texture's seam)."""
    ring = np.asarray(ring, np.float64)
    n = len(ring)
    rel = ring - c
    # u: along the outline (m at the eaves); v: up the profile (its arc length at the eaves' mean radius)
    seg = np.hypot(*(np.roll(ring, -1, 0) - ring).T)
    u = np.concatenate([[0.0], np.cumsum(seg)]) / uv_m
    r_mean = float(np.hypot(*rel.T).mean())
    prof = list(profile)
    closed = prof[-1][0] <= 1e-6
    rows = prof[:-1] if closed else prof
    v, acc = [0.0], 0.0
    for (s0, t0), (s1, t1) in zip(prof[:-1], prof[1:]):
        acc += math.hypot((s1 - s0) * r_mean, (t1 - t0) * rise) / uv_m
        v.append(acc)
    i0 = m.n
    col = tuple(mat[1])
    for k, (s, t) in enumerate(rows):
        z = z0 + rise * t
        for j in range(n + 1):
            q = c + rel[j % n] * s
            m.P += (float(q[0]), float(q[1]), float(z))
            m.UV += (float(u[j]), float(v[k]))
            m.C += col
    m.n += len(rows) * (n + 1)
    W = n + 1
    for k in range(len(rows) - 1):
        for j in range(n):
            a, b = i0 + k * W + j, i0 + k * W + j + 1
            d, e = a + W, b + W
            m.T += (a, b, e, a, e, d)
            m.M += (mat[0], mat[0])
    if closed:
        apex = m.n
        m.P += (float(c[0]), float(c[1]), float(z0 + rise))
        m.UV += (float(u[-1]) / 2, float(v[-1]))
        m.C += col
        m.n += 1
        top = i0 + (len(rows) - 1) * W
        for j in range(n):
            m.T += (top + j, top + j + 1, apex)
            m.M.append(mat[0])


def curved_roof(m, spec, ring, z_eave, mat_idx):
    """A curved spec's roof (spec.roof in CURVED) over its drawn ring, spec.rise high (buildings._roof)."""
    ring = np.asarray(ring, np.float64)
    if spec.roof == "barrel":
        _barrel(m, spec, ring, z_eave, mat_idx)
        return
    c = centroid(ring)
    rise = float(spec.rise or default_rise(spec.roof, _area(ring)))
    _surface(m, densify(ring), c, z_eave, rise, PROFILES[spec.roof], mat_idx[spec.roof_mat])
    if getattr(spec, "lantern", None):
        lr, lh = spec.lantern
        _lantern(m, c, z_eave + rise * (0.97 if spec.roof == "dome" else 1.0), lr, lh, mat_idx.__getitem__, spec.roof_mat,
                 spec.facade)


def _barrel(m, spec, ring, z_eave, mat_idx):
    """A barrel vault over the ring's oriented box: a half ellipse across its width, spec.rise high, along its length
    (smooth); each end closed by a half-ellipse wall in the facade's colour."""
    import buildings
    c, u, hl, hw = buildings.oriented_box(ring)
    v = np.array([-u[1], u[0]])
    rise = float(spec.rise or default_rise("barrel", 0.0, hw))
    rm, fac = mat_idx[spec.roof_mat], mat_idx[spec.facade]
    th = np.linspace(0.0, math.pi, BARREL_STEPS + 1)
    t, z = -np.cos(th) * hw, np.sin(th) * rise          # across the width (from -v to +v), up
    arc = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(t), np.diff(z)))]) / 6.0
    i0 = m.n
    for s in (-hl, hl):
        for k in range(len(th)):
            p = c + u * s + v * t[k]
            m.P += (float(p[0]), float(p[1]), float(z_eave + z[k]))
            m.UV += (float(s / 6.0), float(arc[k]))
            m.C += tuple(rm[1])
    m.n += 2 * len(th)
    W = len(th)
    for k in range(W - 1):
        a, b = i0 + k, i0 + k + 1               # at -hl
        m.T += (a, b + W, b, a, a + W, b + W)
        m.M += (rm[0], rm[0])
    for s, sgn in ((-hl, -1), (hl, 1)):         # the ends: fans from the eave line's middle, facing -u / +u
        cz = (float((c + u * s)[0]), float((c + u * s)[1]), float(z_eave))
        for k in range(W - 1):
            p, q = c + u * s + v * t[k], c + u * s + v * t[k + 1]
            a = (float(p[0]), float(p[1]), float(z_eave + z[k]))
            b = (float(q[0]), float(q[1]), float(z_eave + z[k + 1]))
            if sgn < 0:
                m.tri(cz, a, b, (0.002, 0.995), (0.012, 0.995), (0.007, 0.999), fac)
            else:
                m.tri(cz, b, a, (0.002, 0.995), (0.012, 0.995), (0.007, 0.999), fac)


def _area(ring):
    p = np.asarray(ring, np.float64)
    q = np.roll(p, -1, 0)
    return abs(float((p[:, 0] * q[:, 1] - q[:, 0] * p[:, 1]).sum())) / 2


def circle(c, r, n=32):
    t = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
    return np.stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)], 1)


def _cylinder(m, ring, z0, z1, mat, uv_m=4.0):
    """Smooth walls round a ring (counter-clockwise) from z0 to z1, facing out."""
    ring = np.asarray(ring, np.float64)
    n = len(ring)
    seg = np.hypot(*(np.roll(ring, -1, 0) - ring).T)
    u = np.concatenate([[0.0], np.cumsum(seg)]) / uv_m
    i0 = m.n
    col = tuple(mat[1])
    for z, vv in ((z0, 0.0), (z1, (z1 - z0) / uv_m)):
        for j in range(n + 1):
            q = ring[j % n]
            m.P += (float(q[0]), float(q[1]), float(z))
            m.UV += (float(u[j]), float(vv))
            m.C += col
    m.n += 2 * (n + 1)
    W = n + 1
    for j in range(n):
        a, b = i0 + j, i0 + j + 1
        m.T += (a, b, b + W, a, b + W, a + W)
        m.M += (mat[0], mat[0])


def _lantern(m, c, z, r, h, look, roof_look, wall_look):
    """A dome's lantern: a small drum and its own little dome on the top (look(name) -> material token)."""
    ring = circle(c, r, 16)
    _cylinder(m, ring, z - 0.5, z + h * 0.6, look(wall_look))
    _surface(m, ring, np.asarray(c, np.float64), z + h * 0.6, h * 0.4, _DOME, look(roof_look))


# ------------------------------------------------------------------------------------------- open-bowl stadiums

def is_bowl(cls, area, roof_given, attrs):
    """Is the footprint an open-bowl stadium (drawn by stadium())?"""
    if roof_given in SHAPES or roof_given in ("flat", "gabled", "hipped") and not attrs.get("landmark"):
        return False                        # (a mapped roof: a covered stadium or an arena, a building)
    return (cls in STADIUM_CLASSES or attrs.get("stadium")) and area >= STADIUM_MIN_M2


def _inset(ring, c, d, keep=FIELD_MIN_R):
    """The ring with each corner moved d towards c (at most to `keep` of its distance)."""
    rel = ring - c
    r = np.hypot(*rel.T)
    s = np.maximum(keep, 1.0 - d / np.maximum(r, 1e-6))
    return c + rel * s[:, None]


def bowl_rings(ring):
    """The bowl's rings: (outer, mid, inner, centroid, stands depth)."""
    ring = densify(np.asarray(ring, np.float64), 8.0, 128)
    c = centroid(ring)
    D = float(np.clip(STANDS_SHARE * 2 * radius(_area(ring)), *STANDS_M))
    return ring, _inset(ring, c, D / 2), _inset(ring, c, D), c, D


def _band(m, a, b, za, zb, mat, uv_m=4.0):
    """Quads between two rings of the same corners (a at za, b at zb): faces up / in for a counter-clockwise a
    outside b."""
    n = len(a)
    for i in range(n):
        j = (i + 1) % n
        p, q = a[i], a[j]
        r, s = b[j], b[i]
        w = float(math.hypot(*(q - p))) / uv_m
        d = float(math.hypot(*(s - p))) / uv_m
        m.quad((p[0], p[1], za), (q[0], q[1], za), (r[0], r[1], zb), (s[0], s[1], zb),
               (0.0, 0.0), (w, 0.0), (w, d), (0.0, d), mat)


def _wall(m, ring, z0, z1, mat, inward=False, uv_m=4.0):
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        if inward:
            a, b = b, a
        w = float(math.hypot(*(b - a))) / uv_m
        m.quad((a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1),
               (0.0, z0 / uv_m), (w, z0 / uv_m), (w, z1 / uv_m), (0.0, z1 / uv_m), mat)


def stadium(m, spec, gmin, gmax, look, lite=False, z0=None):
    """An open bowl (spec.cls "stadium_bowl"): look(name) -> material token (buildings._Looks, or one token for all:
    the collision mesh). Outer wall from z0 (the skirt) to the rim; the upper tier from under the rim down to the club
    level, its glass, the lower tier down to the front row, the field wall; a canopy where spec.canopy."""
    outer, mid, inner, c, D = bowl_rings(spec.ring)
    base = gmin
    top = gmax + float(spec.height)
    z0 = base - 3.0 if z0 is None else z0
    front = base + FRONT_M
    span = max(top - LIP_M - front - CLUB_M, 4.0)
    mid_lo = front + 0.38 * span
    mid_hi = mid_lo + CLUB_M
    wall, seats, glass, roof = look(spec.facade), look(spec.stands), look(spec.club), look(spec.roof_mat)
    rim = _inset(outer, c, RIM_M)
    _wall(m, outer, z0, top, wall)
    _band(m, outer, rim, top, top, wall)                        # the rim's top
    _wall(m, rim, top - LIP_M, top, wall, inward=True)         # the parapet's inside
    _band(m, rim, mid, top - LIP_M, mid_hi, seats)              # upper tier
    _wall(m, mid, mid_lo, mid_hi, glass, inward=True)           # club level
    _band(m, mid, inner, mid_lo, front, seats)                  # lower tier
    _wall(m, inner, base - 1.0, front, wall, inward=True)       # field wall
    if spec.canopy and not lite:
        reach = _inset(outer, c, CANOPY_SHARE * D)
        zc = top + CANOPY_UP_M
        _band(m, outer, reach, zc, zc, roof)
        _band(m, reach, outer, zc - 0.4, zc - 0.4, roof)       # (its underside: the rings' order swapped faces down)
        _wall(m, outer, top, zc, wall)                          # the canopy's back over the rim
        _wall(m, reach, zc - 0.4, zc, roof, inward=True)


# ------------------------------------------------------------------------------------------- placed pieces

def pieces_in_tile(frame, x0, y0, x1, y1, t=None):
    """[(piece, (x, y) relative to (x0, y0))] of the table's pieces whose point is in the tile."""
    t = t if t is not None else table()
    out = []
    for p in t["pieces"]:
        at = p.get("at")
        if not at:
            continue
        x, y, _ = frame.geodetic_to_enu(float(at[0]), float(at[1]), 0.0)
        x, y = float(x), float(y)
        if x0 <= x < x1 and y0 <= y < y1:
            out.append((p, (x - x0, y - y0)))
    return out


def _star_ring(c, r, yaw):
    """The star's outline: STAR_ARMS points of radius r, standing upright in the plane through c along yaw (degrees
    from east, counter-clockwise: the star faces yaw + 90): [(x, y, height over its centre)]."""
    d = np.array([math.cos(math.radians(yaw)), math.sin(math.radians(yaw))])
    out = []
    for k in range(2 * STAR_ARMS):
        rr = r if k % 2 == 0 else r * STAR_INNER
        a = math.pi / 2 + k * math.pi / STAR_ARMS
        p = c + d * (rr * math.cos(a))
        out.append((float(p[0]), float(p[1]), rr * math.sin(a)))
    return out


def _tube(m, p, q, t, mat):
    """A square tube t thick from p to q (3D)."""
    p, q = np.asarray(p, np.float64), np.asarray(q, np.float64)
    a = q - p
    L = float(np.linalg.norm(a))
    if L < 1e-6:
        return
    a /= L
    up = np.array([0.0, 0.0, 1.0]) if abs(a[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    s = np.cross(a, up)
    s /= np.linalg.norm(s)
    w = np.cross(s, a)
    h = t / 2
    k = [s * h + w * h, -s * h + w * h, -s * h - w * h, s * h - w * h]
    for i in range(4):
        e0, e1 = k[i], k[(i + 1) % 4]
        m.quad(tuple(p + e1), tuple(p + e0), tuple(q + e0), tuple(q + e1),
               (0.002, 0.995), (0.012, 0.995), (0.012, 0.999), (0.002, 0.999), mat)


def piece_mesh(m, piece, xy, z, look, lite=False):
    """One placed piece at (x, y) over ground z (the frame's up): look(name) -> material token."""
    kind = piece.get("kind")
    c = np.asarray(xy, np.float64)
    base = z + float(piece.get("base_m", 0.0))
    if kind == "dome":
        r = float(piece["r"])
        ring = circle(c, r, 48 if not lite else 24)
        dh = float(piece.get("drum_h", 0.0))
        if dh > 0:
            _cylinder(m, ring, base - 1.0, base + dh, look(piece.get("drum_look", "bne_bld_fac_grey")))
        roof_look = piece.get("look", GOLD)
        _surface(m, ring, c, base + dh, float(piece.get("rise", r)), PROFILES[piece.get("shape", "dome")],
                 look(roof_look))
        lan = piece.get("lantern")
        if lan:
            _lantern(m, c, base + dh + float(piece.get("rise", r)) * 0.97, float(lan[0]), float(lan[1]), look,
                     roof_look, piece.get("drum_look", "bne_bld_fac_grey"))
    elif kind == "spire":
        r = float(piece["r"])
        n = int(piece.get("sides", 4))
        yaw = math.radians(float(piece.get("yaw_deg", 45.0)))
        t = yaw + np.arange(n) * 2 * math.pi / n
        ring = np.stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)], 1)
        dh = float(piece.get("drum_h", 0.0))
        if dh > 0:
            _cylinder(m, ring, base - 1.0, base + dh, look(piece.get("drum_look", "bne_bld_fac_grey")))
        _surface(m, ring, c, base + dh, float(piece["rise"]), _CONE, look(piece.get("look", "bne_copper")))
    elif kind == "star":
        r = float(piece.get("size_m", 30.0)) / 2
        lift = float(piece.get("lift_m", 2.0))
        cz = base + lift + r
        pts = [(x, y, cz + h) for x, y, h in _star_ring(c, r, float(piece.get("yaw_deg", 0.0)))]
        glow = look(piece.get("look", "bne_glow"))
        for i in range(len(pts)):
            _tube(m, pts[i], pts[(i + 1) % len(pts)], STAR_TUBE_M, glow)
        if not lite:
            frame_ = look("bne_bridge_steel")
            for i in (4, 6):                                # the two lower points: posts down to the rock
                x, y, zz = pts[i]
                _tube(m, (x, y, base - 1.0), (x, y, zz), STAR_POST_M, frame_)
    else:
        return False
    return True


def pieces_mesh(frame, x0, y0, x1, y1, ground, looks, lite=False, t=None):
    """The table's pieces in the tile [x0, x1) x [y0, y1) as one build()-shaped mesh (rings relative to (x0, y0)), or
    None. ground(lats, lons) -> heights over the ellipsoid (arrays); looks: buildings._Looks (looks[name] -> token);
    the heights go into the frame's up (the Earth's curvature) as tile_buildings does."""
    import buildings
    got = pieces_in_tile(frame, x0, y0, x1, y1, t)
    if not got:
        return None
    m = buildings.Mesh()
    for p, (x, y) in got:
        la, lo = float(p["at"][0]), float(p["at"][1])
        g = float(np.asarray(ground(np.array([la]), np.array([lo])), np.float64)[0])
        if not np.isfinite(g):
            continue
        z = float(frame.geodetic_to_enu(la, lo, g)[2])
        piece_mesh(m, p, (x, y), z, looks.__getitem__, lite)
    if m.n == 0:
        return None
    P, UV, T, M, C = m.arrays()
    return P, UV, T, M, buildings.MATERIALS, C
