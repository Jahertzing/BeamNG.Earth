"""Facade details in 3D for the near buildings (buildings.build, full detail only; 2026-10-28, the user's "better
buildings" list): window trims and shutters, a door frame, a garage door where the driveway meets the house, a
shopfront awning, balconies on 3-6 storey apartments, a stone wainscot along the bottom of the walls.

The walls stay as they are (textured quads: the window sheets' bands, or a plain material with laid-over sash windows,
buildings._walls); the kit puts thin pieces in front of them: no hole is cut, a window is not recessed, but its sill,
head and jambs stand out and catch the light. Each piece is one or two quads (a front and a top), not a box: a house's
trims cost ~30 vertices a window, and a road tile's four building meshes (roads.BLD_PARTS_MAX x buildings.MAX_MESH_VERTS)
hold the town; buildings.build_parts leaves the kit off a building whose kit would not fit (it never drops a
building). The far tiles' lite mesh and the near tiles' coarse detail (buildings.BLD_LOD_PX) have none.

Every choice is hashed from the building's seed (buildings._hash01), so a building gets the same details on every
build (docs/ROADMAP.md "Principle: deterministic generation").
"""
import math

import numpy as np

import buildings as B

TRIM = "bne_bld_fac_white"          # white trim (sills, heads, jambs, door frames)
TRIM_W = 0.09                       # m: a jamb's width, a head / sill's overhang past the window each side
SILL_OUT, SILL_H = 0.09, 0.06       # m a sill stands out, its thickness
HEAD_OUT, HEAD_H = 0.06, 0.14       # ... a window head's
JAMB_OUT = 0.04
SHUTTER_SHARE = 0.3                 # pitched-roof houses with shutters (hashed)
SHUTTER_TINTS = ((0.10, 0.10, 0.10), (0.12, 0.22, 0.14), (0.12, 0.16, 0.30), (0.36, 0.10, 0.10), (0.92, 0.92, 0.90))
SHUTTER_OUT = 0.05
DOOR_W, DOOR_H = 1.0, 2.2           # the stock door's opening (buildings.doors' rows: 1.0 x 2.2)
FRAME_W, FRAME_OUT = 0.14, 0.07
GARAGE_SINGLE_M, GARAGE_DOUBLE_M = 2.7, 4.9     # garage door widths (US residential)
GARAGE_H = 2.2
GARAGE_DOUBLE_SHARE = 0.55          # of the houses with a front wall >= GARAGE_DOUBLE_WALL_M (hashed)
GARAGE_DOUBLE_WALL_M = 11.0
GARAGE_OUT = 0.03
AWNING_SHARE = 0.5                  # shopfronts with an awning (hashed)
AWNING_OUT, AWNING_DROP, AWNING_VALANCE = 1.3, 0.45, 0.25
BALCONY_SHARE = 0.5                 # 3-6 storey apartments with balconies (hashed) ...
BALCONY_FLOORS = (3, 6)
APARTMENT_CLASSES = {"apartments", "residential", "dormitory", ""}
BALCONY_DEPTH, BALCONY_SLAB, RAIL_H = 1.2, 0.15, 1.0
BALCONY_STEEL = "bne_bridge_steel"
WAINSCOT_SHARE = 0.3                # buildings of 2+ storeys with a stone base band (hashed) ...
WAINSCOT_H, WAINSCOT_OUT = 0.9, 0.04


def _front(m, p, q, z0, z1, mat):
    """A vertical quad from p to q (xy, outside to the right of p -> q), z0..z1, in mat's plain colour."""
    uv = (0.002, 0.995), (0.012, 0.995), (0.012, 0.999), (0.002, 0.999)
    m.quad((p[0], p[1], z0), (q[0], q[1], z0), (q[0], q[1], z1), (p[0], p[1], z1), *uv, mat)


def _top(m, p, q, dout, z, mat):
    """A horizontal quad over p -> q (the wall's line) out to dout (a vector), at z, facing up."""
    uv = (0.002, 0.995), (0.012, 0.995), (0.012, 0.999), (0.002, 0.999)
    m.quad((p[0], p[1], z), (p[0] + dout[0], p[1] + dout[1], z), (q[0] + dout[0], q[1] + dout[1], z),
           (q[0], q[1], z), *uv, mat)


def _ledge(m, a, d, out, s0, s1, z0, z1, depth, mat):
    """A ledge on the wall (a: the wall's start, d: along it, out: its outward unit normal) from s0 to s1 m along,
    z0..z1, standing out `depth`: its front and its top."""
    p = a + d * s0 + out * depth
    q = a + d * s1 + out * depth
    _front(m, p, q, z0, z1, mat)
    _top(m, a + d * s0, a + d * s1, out * depth, z1, mat)


def _wall(ring, i):
    a, b = np.asarray(ring[i], np.float64), np.asarray(ring[(i + 1) % len(ring)], np.float64)
    L = float(math.hypot(*(b - a)))
    if L < 1e-6:
        return None
    d = (b - a) / L
    return a, d, np.array([d[1], -d[0]]), L


def windows(m, spec, ring, z_eave, mat_idx):
    """Trims (sill, head, two jambs) round each laid-over sash window of a house (buildings.house_window_slots), and a
    pair of shutters on a share of houses (SHUTTER_SHARE, its tint hashed)."""
    trim = mat_idx[TRIM]
    shutters = B._hash01(spec.seed, "shutters") < SHUTTER_SHARE and spec.roof != "flat"
    sh = None
    if shutters:
        r, g, b = SHUTTER_TINTS[int(B._hash01(spec.seed, "shtint") * len(SHUTTER_TINTS))]
        sh = (mat_idx[TRIM][0], (int(r * 255), int(g * 255), int(b * 255), 255))
    for i in range(len(ring)):
        w = _wall(ring, i)
        if w is None:
            continue
        a, d, out, L = w
        for c, zf in B.house_window_slots(spec, a, d, out, L, z_eave, i):
            s = float(np.dot(c - a, d))
            h0, h1 = s - B.WIN_W / 2, s + B.WIN_W / 2
            _ledge(m, a, d, out, h0 - TRIM_W, h1 + TRIM_W, zf - SILL_H, zf, SILL_OUT, trim)                  # sill
            _ledge(m, a, d, out, h0 - TRIM_W, h1 + TRIM_W, zf + B.WIN_H, zf + B.WIN_H + HEAD_H, HEAD_OUT, trim)  # head
            for e0, e1 in ((h0 - TRIM_W, h0), (h1, h1 + TRIM_W)):                                              # jambs
                _front(m, a + d * e0 + out * JAMB_OUT, a + d * e1 + out * JAMB_OUT, zf, zf + B.WIN_H, trim)
            if sh is not None:
                for e0, e1 in ((h0 - TRIM_W - B.WIN_W / 2, h0 - TRIM_W), (h1 + TRIM_W, h1 + TRIM_W + B.WIN_W / 2)):
                    if e0 > 0.15 and e1 < L - 0.15:
                        _front(m, a + d * e0 + out * SHUTTER_OUT, a + d * e1 + out * SHUTTER_OUT, zf, zf + B.WIN_H, sh)


def door_frame(m, spec, ring, gmax, mat_idx):
    """A frame (two jambs and a head with a top) round the front door the props place (buildings.doors)."""
    if not spec.door:
        return
    edge, along = spec.door
    w = _wall(ring, edge)
    if w is None:
        return
    a, d, out, L = w
    gd = B.door_ground(spec, edge, along, gmax) if spec.stoop else gmax
    base = gd + (B.STOOP_H if spec.stoop else 0.0) + (B.PORCH_DECK_M if spec.porch else 0.0)
    trim = mat_idx[TRIM]
    h0, h1 = along - DOOR_W / 2, along + DOOR_W / 2
    for e0, e1 in ((h0 - FRAME_W, h0), (h1, h1 + FRAME_W)):
        _ledge(m, a, d, out, e0, e1, base, base + DOOR_H, FRAME_OUT, trim)
    _ledge(m, a, d, out, h0 - FRAME_W * 1.5, h1 + FRAME_W * 1.5, base + DOOR_H, base + DOOR_H + 0.2, FRAME_OUT * 1.4,
           trim)


def garage_span(spec, edge, L):
    """(s0, s1) along wall `edge` (of length L) of the house's garage door (spec.drive: where its driveway meets the
    wall), or None."""
    dr = getattr(spec, "drive", None)
    if not dr or dr[0] != edge:
        return None
    width = GARAGE_DOUBLE_M if (L >= GARAGE_DOUBLE_WALL_M and B._hash01(spec.seed, "garage2") < GARAGE_DOUBLE_SHARE) \
        else GARAGE_SINGLE_M
    s = float(np.clip(dr[1], width / 2 + 0.3, L - width / 2 - 0.3))
    if L < width + 0.6:
        return None
    return s - width / 2, s + width / 2


def garage(m, spec, ring, base, mat_idx):
    """The garage door: a panel of white siding (its grooves the door's panels) in a white frame."""
    dr = getattr(spec, "drive", None)
    if not dr:
        return
    w = _wall(ring, dr[0])
    if w is None:
        return
    a, d, out, L = w
    span = garage_span(spec, dr[0], L)
    if span is None:
        return
    s0, s1 = span
    p, q = a + d * s0 + out * GARAGE_OUT, a + d * s1 + out * GARAGE_OUT
    su = 0.55                                       # (one groove a panel: ~0.55 m)
    m.quad((p[0], p[1], base), (q[0], q[1], base), (q[0], q[1], base + GARAGE_H), (p[0], p[1], base + GARAGE_H),
           (0, 0), ((s1 - s0) / su, 0), ((s1 - s0) / su, GARAGE_H / su), (0, GARAGE_H / su), mat_idx[B.SIDING])
    trim = mat_idx[TRIM]
    for e0, e1 in ((s0 - FRAME_W, s0), (s1, s1 + FRAME_W)):
        _ledge(m, a, d, out, e0, e1, base, base + GARAGE_H, FRAME_OUT, trim)
    _ledge(m, a, d, out, s0 - FRAME_W, s1 + FRAME_W, base + GARAGE_H, base + GARAGE_H + 0.18, FRAME_OUT, trim)


def awning(m, spec, ring, base, mat_idx):
    """A sloped awning over a shopfront (spec.sign: its street wall), its colour the sign band's (hashed)."""
    if spec.sign is None or not spec.shop or spec.plain or B._hash01(spec.seed, "awning") >= AWNING_SHARE:
        return
    w = _wall(ring, spec.sign)
    if w is None or w[3] < 4.0:
        return
    a, d, out, L = w
    z = base + B.GROUND_FLOOR_SHOP_M - B.SIGN_H - 0.25
    tint = B.SIGN_TINTS[int(B._hash01(spec.seed, "awning tint") * len(B.SIGN_TINTS))]
    mat = (mat_idx[TRIM][0], tuple(int(v * 255) for v in tint) + (255,))
    p0, q0 = a + d * (L * 0.08), a + d * (L * 0.92)
    p1, q1 = p0 + out * AWNING_OUT, q0 + out * AWNING_OUT
    uv = (0.002, 0.995), (0.012, 0.995), (0.012, 0.999), (0.002, 0.999)
    m.quad((p1[0], p1[1], z - AWNING_DROP), (q1[0], q1[1], z - AWNING_DROP), (q0[0], q0[1], z), (p0[0], p0[1], z),
           *uv, mat)                                                                                # the slope
    m.quad((q1[0], q1[1], z - AWNING_DROP), (p1[0], p1[1], z - AWNING_DROP), (p0[0], p0[1], z), (q0[0], q0[1], z),
           *uv, mat)                                                                                # (its underside)
    _front(m, p1, q1, z - AWNING_DROP - AWNING_VALANCE, z - AWNING_DROP, mat)                     # the valance


def balconies(m, spec, ring, z_eave, mat_idx):
    """Balconies on every other bay of an apartment block's street wall (spec.door's), from the second floor up: a
    concrete slab and a steel railing (front and sides)."""
    lo, hi = BALCONY_FLOORS
    if not spec.door or not (lo <= spec.floors <= hi) or (spec.cls or "") not in APARTMENT_CLASSES or spec.wall \
            or spec.plain or not spec.band or B._hash01(spec.seed, "balcony") >= BALCONY_SHARE:
        return
    w = _wall(ring, spec.door[0])
    if w is None:
        return
    a, d, out, L = w
    bay = B.TILE_PX_M / spec.band[1]
    nb = int(round(L / bay))
    if nb < 3:
        return
    bw = L / nb
    base = z_eave - spec.floors * spec.floor_h
    slab = mat_idx["bne_precast"]
    rail = mat_idx[BALCONY_STEEL]
    for f in range(1, spec.floors):
        z = base + f * spec.floor_h
        for k in range(1, nb - 1, 2):
            s0, s1 = k * bw + 0.15 * bw, (k + 1) * bw - 0.15 * bw
            _ledge(m, a, d, out, s0, s1, z - BALCONY_SLAB, z, BALCONY_DEPTH, slab)
            p, q = a + d * s0 + out * BALCONY_DEPTH, a + d * s1 + out * BALCONY_DEPTH
            _front(m, p, q, z, z + RAIL_H, rail)
            for e, sgn in ((s0, -1), (s1, 1)):          # the sides, facing out along the wall
                w0, w1 = a + d * e, a + d * e + out * BALCONY_DEPTH
                if sgn < 0:
                    _front(m, w0, w1, z, z + RAIL_H, rail)
                else:
                    _front(m, w1, w0, z, z + RAIL_H, rail)


def wainscot(m, spec, ring, gmin, base, mat_idx):
    """A stone band along the bottom of every wall (WAINSCOT_H over the floors' base), standing out a little."""
    if spec.floors < 2 or spec.shop or spec.plain or spec.windows is False \
            or B._hash01(spec.seed, "wainscot") >= WAINSCOT_SHARE:
        return
    tints = B.WALL_TINTS[B.STONE]
    mat = mat_idx[f"{B.STONE}|{int(B._hash01(spec.seed, 'wsc') * len(tints))}"]
    for i in range(len(ring)):
        w = _wall(ring, i)
        if w is None or w[3] < 0.5:
            continue
        a, d, out, L = w
        _ledge(m, a, d, out, 0.0, L, gmin - 0.3, base + WAINSCOT_H, WAINSCOT_OUT, mat)


def add(m, spec, ring, gmin, gmax, z_eave, mat_idx):
    """All of a building's kit (its drawn ring, its ground, its eave height)."""
    if spec.cls in B.BRIDGE_CLASSES or (spec.min_h or 0.0) > 0.5 or spec.deck:
        return
    base = z_eave - spec.floors * spec.floor_h
    if spec.wall and spec.windows is not False:
        windows(m, spec, ring, z_eave, mat_idx)
    door_frame(m, spec, ring, gmax, mat_idx)
    garage(m, spec, ring, base, mat_idx)
    awning(m, spec, ring, base, mat_idx)
    balconies(m, spec, ring, z_eave, mat_idx)
    wainscot(m, spec, ring, gmin, base, mat_idx)
