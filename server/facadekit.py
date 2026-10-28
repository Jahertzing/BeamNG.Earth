"""Facade details in 3D for the near buildings (buildings.build, full detail only; 2026-10-28, the user's "better
buildings" list): window trims and shutters, a door frame, a garage door where the driveway meets the house, a
shopfront awning, balconies on 3-6 storey apartments, a stone wainscot along the bottom of the walls.

The walls stay as they are (textured quads: the window sheets' bands, or a plain material with laid-over sash windows,
buildings._walls); the kit puts thin pieces in front of them: a window's sill, head and jambs stand out and catch the
light. The walls get depth of their own (depth(), run on buildings._walls' quads): the windows set into the walls
(recess(): the openings cut out of the wall quads, the glass set back, reveals round it; houses' windows and the
plaster sheet's lower floors), the shopfronts' glass set back (shopfronts()), aluminium fins and floor ledges on the
curtain walls (fins()). Each piece is one or two quads (a front and a top), not a box: a house's
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

# Recessed windows (recess(), 2026-10-28: "real depth and shadow up close"): the window's opening cut out of its wall,
# the glass set back behind the wall's face and the opening's four sides (two jambs, the head's soffit, the sill)
# closing the gap, so a facade has depth and shadow up close. Houses (spec.wall): the native sash window quad moved
# from WIN_OUT_M proud of the wall to RECESS_HOUSE_M behind it, the reveals in the white trim. Plaster sheet buildings
# (spec.facade on buildings.FACADE): each window drawn in the sheet (SHEET_OPENINGS) cut out of its floor's quad and
# set back RECESS_SHEET_M, the reveals the sheet's own plain wall (SHEET_PLAIN_U) so they take the building's tint.
# The glass keeps its material slot and UVs: the night maps (tools/build_night_windows.py) light the same windows.
RECESS = True                       # False: the windows laid on the walls as before (the kernel's quads untouched)
RECESS_HOUSE_M = 0.08               # m a house's glass sits behind its wall's face
RECESS_SHEET_M = 0.15               # ... a plaster sheet building's (masonry: deeper reveals)
RECESS_MIN_W = 0.25                 # m: an opening cut narrower than this by its quad's end (a wall's corner, a door's
                                    # plain strip) stays painted on the wall
RECESS_MAX_Z = 8.0                  # m over the building's floors' base: sheet windows recessed this high at most on its
                                    # side and back walls, the floors seen up close. Each opening is ~6 quads more (its
                                    # glass, 4 reveals, the wall between): downtown Denver's road tile (39.747, -104.992)
                                    # had 44.6k near vertices flat, 67.9k at 8 m, 94.9k at 12 m, 118k at 30 m, 130k
                                    # uncapped (2026-10-28). Houses (3 floors at most) are not capped.
RECESS_STREET_MAX_Z = 30.0          # ... on its street wall (street_edge(): the one you drive past and look up at; well
                                    # under the tile's kit budget, buildings.KIT_FILL)
# Shopfronts set back (shopfronts(), 2026-10-28, "street level is what you see most from the car"): the metal sheet's
# shop band (buildings.BAND_SHOP: two bays of a sign board, a glazed front with its mullions, a concrete bulkhead under
# it) has its glazing cut out and set back; the bulkhead and the pillars between the bays stay on the face.
RECESS_SHOP = True                  # False: the shopfronts flat as before
RECESS_SHOP_M = 0.25                # m the shop glass sits behind the wall's face
SHOP_MIN_H = 0.8                    # m: glass left lower than this under a sign or awning stays flat
SHOP_CLEAR_M = 0.05                 # m kept under a sign box or awning
# each bay's glazing (x0, y0, x1, y1 px of the 1024 sheet, image top first; its frame and mullions with it), measured
# from t_highrise_metal_b / _02_b (the same layout): pillars at x 0-12, 500-522, 1012-1024, the bulkhead from y 737
SHOP_OPENINGS_PX = ((12, 579, 500, 736), (522, 579, 1012, 736))
SHOP_OPENINGS = np.array([(x0 / 1024, x1 / 1024, 1.0 - y1 / 1024, 1.0 - y0 / 1024) for x0, y0, x1, y1 in SHOP_OPENINGS_PX],
                         np.float64)
SHOP_REVEAL_UV = (100 / 1024, 110 / 1024, 1.0 - 760 / 1024, 1.0 - 742 / 1024)     # the bulkhead's concrete (unlit at night)
# Curtain walls' aluminium (fins()): the metal sheet's office bands (buildings.BAND_OFFICE: 5 bays, BAND_OFFICE2: 4)
# have their thick mullions on the bay edges (measured: band 0's 15 px wide centred on k * 204.8, band 1's on k * 256)
FINS = True                         # False: the curtain walls flat as before
FIN_W, FIN_D = 0.07, 0.12           # m: a fin's width, how far it stands out
FIN_END_M = 0.3                     # m: no fin this close to a wall's corner
LEDGE_H, LEDGE_D = 0.2, 0.08        # m: a floor line's spandrel ledge, its height and depth
OFFICE_BANDS = ((0, 5), (1, 4))
FIN_UV = (202 / 1024, 208 / 1024, 1.0 - 230 / 1024, 1.0 - 20 / 1024)   # band 0's bay-edge mullion (unlit at night)
WAINSCOT_TOL = 0.1                  # m an opening may reach down behind a stone base band (a house's sill: 5 cm)
DOOR_CLEAR_M = 0.05                 # m kept between an opening and a door / garage door frame
# The plaster sheet's window openings (x0, y0, x1, y1 px of 1024, image top first) per band (buildings.BAND_TALL,
# BAND_PANEL, BAND_SASH, BAND_BRICK), the glass inside each painted frame, measured from the image: the same rects
# tools/build_night_windows.py OPENINGS lights at night (a test holds them equal). Band 2's bays 1 and 4 (None) are
# open black windows buildings.py never uses. Band 0's bays are uneven: its third window crosses a bay edge.
SHEET_OPENINGS_PX = (
    ((80, 26, 198, 236), (330, 26, 446, 236), (578, 26, 988, 236)),
    ((52, 306, 202, 460), (308, 306, 458, 460), (566, 306, 716, 460), (822, 306, 972, 460)),
    ((60, 566, 146, 716), None, (466, 566, 554, 716), (670, 566, 756, 716), None),
    ((46, 814, 154, 946), (252, 814, 360, 946), (458, 814, 566, 946), (662, 814, 770, 946), (866, 814, 974, 946)),
)
# ... as (u0, u1, v0, v1) (Collada v up)
SHEET_OPENINGS = tuple(np.array([(x0 / 1024, x1 / 1024, 1.0 - y1 / 1024, 1.0 - y0 / 1024)
                                 for x0, y0, x1, y1 in (c for c in band if c is not None)], np.float64)
                       for band in SHEET_OPENINGS_PX)
# u of a column of plain wall in each band, clear of windows, frames and vents (the reveals sample it, ~13 px wide;
# _plain_strip's bay edge falls inside band 0's second window)
SHEET_PLAIN_U = (226 / 1024, 228 / 1024, 205 / 1024, 205 / 1024)
PLASTER_BANDS = ((0, 3), (1, 4), (2, 5), (3, 5))     # (band, bays a tile) of the plaster sheet (buildings.BAND_*)


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


def has_wainscot(spec):
    """wainscot()'s choice: the building has a stone base band."""
    return not (spec.floors < 2 or spec.shop or spec.plain or spec.windows is False
                or B._hash01(spec.seed, "wainscot") >= WAINSCOT_SHARE)


def sheet_band(spec):
    """The plaster sheet band (band, bays a tile) of a building whose walls are the sheet (recess()), or None."""
    if spec.wall or spec.plain or not spec.band or B.LOOKS.get(spec.facade, (None,))[0] != B.FACADE:
        return None
    k = tuple(spec.band[:2])
    return k if k in PLASTER_BANDS else None


def street_edge(spec):
    """The edge of a building's street wall (doors(): its front door's, else its shopfront sign's or big box
    storefront's), or None."""
    if spec.door:
        return int(spec.door[0])
    for e in (spec.sign, spec.front):
        if e is not None:
            return int(e)
    return None


def _on_wall(ring, edge, a, d):
    """Which quads (their start a (k, 2), along d (k, 2)) lie on wall `edge` of the ring: (mask, their start's s
    along that wall)."""
    wl = _wall(ring, edge) if edge is not None and 0 <= edge < len(ring) else None
    if wl is None:
        return np.zeros(len(a), bool), np.zeros(len(a))
    ea, ed, en, _ = wl
    rel = a - ea
    return (np.abs(rel @ en) < 1e-3) & (d @ ed > 0.999), rel @ ed


_TEXEL = np.array([(0.002, 0.995), (0.012, 0.995), (0.012, 0.999), (0.002, 0.999)])   # the trims' plain white texel


def _patch(k, u0, u1, v0, v1):
    """k quads' UVs all on one small patch of a sheet (a reveal's or fin's plain colour)."""
    return np.broadcast_to(np.array([(u0, v0), (u1, v0), (u1, v1), (u0, v1)], np.float64), (k, 4, 2))


class _Frame:
    """Kernel quads (P 12 numbers a quad, UV 8, mid the slot) as wall rects: start a, along d, out n, length L,
    za..zb; u = u0 + s us, v = v0 + (z - za) vz (rect: the quads that are such rects)."""

    def __init__(self, P, UV, mid):
        q = self.q = len(mid)
        self.M = (np.frombuffer(bytes(mid), np.uint8) if isinstance(mid, (bytes, bytearray))
                  else np.asarray(mid)).astype(np.int64)
        Q = self.Q = np.asarray(P, np.float64).reshape(q, 4, 3)
        U = self.U = np.asarray(UV, np.float64).reshape(q, 4, 2)
        self.a = a = Q[:, 0, :2]
        e = Q[:, 1, :2] - a
        self.L = L = np.hypot(e[:, 0], e[:, 1])
        self.d = d = e / np.maximum(L, 1e-9)[:, None]
        self.n = np.stack([d[:, 1], -d[:, 0]], 1)
        self.za, self.zb = za, zb = Q[:, 0, 2], Q[:, 2, 2]
        self.rect = ((L > 0.05) & (zb - za > 0.05) & (np.abs(Q[:, 3, :2] - a).max(1) < 1e-6)
                     & (np.abs(Q[:, 2, :2] - Q[:, 1, :2]).max(1) < 1e-6) & (np.abs(Q[:, 1, 2] - za) < 1e-6)
                     & (np.abs(Q[:, 3, 2] - zb) < 1e-6) & (np.abs(U[:, 3, 0] - U[:, 0, 0]) < 1e-9)
                     & (np.abs(U[:, 2, 0] - U[:, 1, 0]) < 1e-9) & (np.abs(U[:, 1, 1] - U[:, 0, 1]) < 1e-9)
                     & (np.abs(U[:, 2, 1] - U[:, 3, 1]) < 1e-9))
        self.u0, self.v0, self.u1 = U[:, 0, 0], U[:, 0, 1], U[:, 1, 0]
        self.us = (self.u1 - self.u0) / np.maximum(L, 1e-9)
        self.vz = (U[:, 3, 1] - self.v0) / np.where(zb - za > 1e-9, zb - za, 1.0)

    def at(self, qi, s, z, w):
        """Positions (k, 3) on quad qi's wall: s along, z, w out from its face."""
        return np.c_[self.a[qi] + self.d[qi] * s[:, None] + self.n[qi] * w[:, None], z]

    def quads(self, qi, corners, uv=None):
        """(P (k, 12), UV (k, 8)) of quads on qi's walls from 4 (s, z, w) corners each (UV the wall's own mapping,
        or given (k, 4, 2))."""
        Pq = np.stack([self.at(qi, s, z, w) for s, z, w in corners], 1)
        if uv is None:
            uv = np.stack([np.c_[self.u0[qi] + s * self.us[qi], self.v0[qi] + (z - self.za[qi]) * self.vz[qi]]
                           for s, z, _ in corners], 1)
        return Pq.reshape(-1, 12), np.asarray(uv).reshape(-1, 8)


def _sheet_holes(F, cand, O, min_w=RECESS_MIN_W):
    """The openings O ((k, 4) u0 u1 v0 v1 within one tile of the sheet) in each candidate quad, from its own UVs (u
    modulo the tile; clipped at its ends, narrower than min_w left out): (quad, s0, s1, z0, z1) arrays."""
    if not len(cand):
        return (np.zeros(0, np.int64),) + (np.zeros(0),) * 4
    t0 = np.floor(F.u0[cand] + 1e-9)
    K = max(1, int(np.max(np.ceil(F.u1[cand] - 1e-9) - t0)))
    tile = t0[:, None, None] + np.arange(K)[None, :, None]                         # (C, K, 1)
    cu0 = np.maximum(O[None, None, :, 0] + tile, F.u0[cand][:, None, None])
    cu1 = np.minimum(O[None, None, :, 1] + tile, F.u1[cand][:, None, None])
    ci = np.broadcast_to(cand[:, None, None], cu0.shape)
    sel = cu1 > cu0
    ci, cu0, cu1 = ci[sel], cu0[sel], cu1[sel]
    ov = np.broadcast_to(O[None, None, :, 2:], sel.shape + (2,))[sel]
    hs0 = (cu0 - F.u0[ci]) / F.us[ci]
    hs1 = (cu1 - F.u0[ci]) / F.us[ci]
    hz0 = F.za[ci] + (ov[:, 0] - F.v0[ci]) / F.vz[ci]
    hz1 = F.za[ci] + (ov[:, 1] - F.v0[ci]) / F.vz[ci]
    ok = (hs1 - hs0 >= min_w) & (hz0 > F.za[ci] + 1e-6) & (hz1 < F.zb[ci] - 1e-6)
    return ci[ok], hs0[ok], hs1[ok], hz0[ok], hz1[ok]


def _clear(spec, ring, F, base, hq, hs0, hs1, hz0):
    """Which openings keep clear of the front door's frame, the garage door and a stone base band (the kit's pieces
    stand there)."""
    keep = np.ones(len(hq), bool)
    if has_wainscot(spec):
        keep &= hz0 >= base + WAINSCOT_H - WAINSCOT_TOL
    spans = []
    if spec.door:
        edge, along = spec.door
        h = DOOR_W / 2 + FRAME_W * 1.5
        spans.append((edge, along - h, along + h, base + B.STOOP_H + B.PORCH_DECK_M + DOOR_H + 0.2))
    if getattr(spec, "drive", None):
        wl = _wall(ring, spec.drive[0])
        gs = garage_span(spec, spec.drive[0], wl[3]) if wl is not None else None
        if gs is not None:
            spans.append((spec.drive[0], gs[0] - FRAME_W, gs[1] + FRAME_W, base + GARAGE_H + 0.18))
    for edge, s0, s1, ztop in spans:
        on, se = _on_wall(ring, edge, F.a[hq], F.d[hq])
        keep &= ~(on & (se + hs1 > s0 - DOOR_CLEAR_M) & (se + hs0 < s1 + DOOR_CLEAR_M) & (hz0 < ztop + DOOR_CLEAR_M))
    return keep


def _cut(F, hq, hs0, hs1, hz0, hz1, D, glass=None, glass_out=0.0, reveals=None, reveal_slot=None):
    """The quads with the openings (quad hq, s0..s1, z0..z1 on it) cut out: per quad, rows of openings (one z range
    each) and the strips between them, in a row the gaps between its openings; the glass D behind the face (glass:
    quads moved back from glass_out in front of it, else each opening's own piece of its quad); reveals (a function
    (hq, z0, z1) -> 4 UV arrays: left jamb, right jamb, sill, head) from the face back to the glass, in reveal_slot.
    (P, UV, mid) lists, the untouched quads first in their order; None if openings would overlap."""
    o = np.lexsort((hs0, hz0, hq))
    hq, hs0, hs1, hz0, hz1 = hq[o], hs0[o], hs1[o], hz0[o], hz1[o]
    if glass is not None:
        glass = glass[o]
    nh = len(hq)
    first = np.ones(nh, bool)
    first[1:] = (hq[1:] != hq[:-1]) | (np.abs(hz0[1:] - hz0[:-1]) > 1e-6)
    prev1 = np.r_[0.0, hs1[:-1]]
    if (~first & ((hs0 < prev1 - 1e-6) | (np.abs(hz1 - np.r_[0.0, hz1[:-1]]) > 1e-6))).any():
        return None
    last = np.r_[first[1:], True]
    gq = np.r_[hq, hq[last]]
    gs0 = np.r_[np.where(first, 0.0, prev1), hs1[last]]
    gs1 = np.r_[hs0, F.L[hq[last]]]
    gz0, gz1 = np.r_[hz0, hz0[last]], np.r_[hz1, hz1[last]]
    rq, rz0, rz1 = hq[first], hz0[first], hz1[first]
    rfirst = np.ones(len(rq), bool)
    rfirst[1:] = rq[1:] != rq[:-1]
    rprev1 = np.r_[0.0, rz1[:-1]]
    if (~rfirst & (rz0 < rprev1 - 1e-6)).any():
        return None
    rlast = np.r_[rfirst[1:], True]
    sq = np.r_[rq, rq[rlast]]
    sz0 = np.r_[np.where(rfirst, F.za[rq], rprev1), rz1[rlast]]
    sz1 = np.r_[rz0, F.zb[rq[rlast]]]
    pq = np.r_[gq, sq]
    ps0, ps1 = np.r_[gs0, np.zeros(len(sq))], np.r_[gs1, F.L[sq]]
    pz0, pz1 = np.r_[gz0, sz0], np.r_[gz1, sz1]
    big = (ps1 - ps0 > 1e-6) & (pz1 - pz0 > 1e-6)
    pq, ps0, ps1, pz0, pz1 = pq[big], ps0[big], ps1[big], pz0[big], pz1[big]
    zero = np.zeros(len(pq))
    p_, t_ = F.quads(pq, ((ps0, pz0, zero), (ps1, pz0, zero), (ps1, pz1, zero), (ps0, pz1, zero)))
    out_P, out_UV, out_M = [p_], [t_], [F.M[pq]]
    zh, Dh = np.zeros(nh), np.full(nh, -D)
    if glass is not None:                               # a laid-over quad (a house's window), moved back
        g = F.Q[glass].copy()
        g[:, :, :2] -= (F.n[hq] * (glass_out + D))[:, None, :]
        out_P.append(g.reshape(-1, 12))
        out_UV.append(F.U[glass].reshape(-1, 8))
        out_M.append(F.M[glass])
    else:                                               # the sheet's own window, cut from its quad and moved back
        p_, t_ = F.quads(hq, ((hs0, hz0, Dh), (hs1, hz0, Dh), (hs1, hz1, Dh), (hs0, hz1, Dh)))
        out_P.append(p_)
        out_UV.append(t_)
        out_M.append(F.M[hq])
    if reveals is not None and D > 0:
        # left jamb (facing +d), right jamb (-d), sill (up), head (down), from the face back to the glass
        sides = (((hs0, hz0, zh), (hs0, hz0, Dh), (hs0, hz1, Dh), (hs0, hz1, zh)),
                 ((hs1, hz1, zh), (hs1, hz1, Dh), (hs1, hz0, Dh), (hs1, hz0, zh)),
                 ((hs0, hz0, zh), (hs1, hz0, zh), (hs1, hz0, Dh), (hs0, hz0, Dh)),
                 ((hs0, hz1, Dh), (hs1, hz1, Dh), (hs1, hz1, zh), (hs0, hz1, zh)))
        slot = np.full(nh, reveal_slot) if reveal_slot is not None else F.M[hq]
        for corners, uv in zip(sides, reveals(hq, hz0, hz1)):
            p_, t_ = F.quads(hq, corners, uv)
            out_P.append(p_)
            out_UV.append(t_)
            out_M.append(slot)
    cut = np.zeros(F.q, bool)
    cut[hq] = True
    if glass is not None:
        cut[glass] = True
    k = np.flatnonzero(~cut)
    Pn = np.concatenate([F.Q.reshape(-1, 12)[k]] + out_P)
    UVn = np.concatenate([F.U.reshape(-1, 8)[k]] + out_UV)
    Mn = np.concatenate([F.M[k]] + out_M)
    return Pn.ravel().tolist(), UVn.ravel().tolist(), Mn.tolist()


def _laid_over(F, slot):
    """The quads of `slot` laid WIN_OUT_M in front of the wall quad (slot 0) they follow in the kernel's order (a
    house's windows, a big box's storefront): (them, their wall quads, s0, s1, z0, z1 on it)."""
    owner = np.maximum.accumulate(np.where(F.M == 0, np.arange(F.q), -1))
    w = np.flatnonzero((F.M == slot) & (owner >= 0))
    o = owner[w]
    c = F.Q[w, :, :2].mean(1) - F.a[o]
    hs0 = ((F.Q[w, 0, :2] - F.a[o]) * F.d[o]).sum(1)
    hs1 = ((F.Q[w, 1, :2] - F.a[o]) * F.d[o]).sum(1)
    hz0, hz1 = F.Q[w, 0, 2], F.Q[w, 2, 2]
    ok = F.rect[o] & (F.M[o] == 0) & (np.abs((c * F.n[o]).sum(1) - B.WIN_OUT_M) < 1e-3) & (hs0 > 1e-6) \
        & (hs1 > hs0 + 0.1) & (hs1 < F.L[o] - 1e-6) & (hz0 > F.za[o] + 1e-6) & (hz1 < F.zb[o] - 1e-6)
    return w[ok], o[ok], hs0[ok], hs1[ok], hz0[ok], hz1[ok]


def recess(spec, ring, z_eave, P, UV, mid):
    """buildings._walls' kernel quads (P 12 numbers a quad, UV 8, mid the material slot: 0 the wall, 1 the
    shopfront, 2 a house's window) with the windows set into the walls (RECESS_*): each opening cut out of its wall
    quad (the wall split into the rects round the openings, their UVs the quad's own affine mapping), the glass moved
    back behind the face and four reveals from the face back to the glass. Run after the quads are final
    (buildings._garage_windows_out; a shift of the sheet's u by whole bays, as the night lights' is, is fine: the
    openings are looked up from each quad's own UVs, u modulo the tile). Openings near a door, garage door or stone
    base band stay as they were; so does a building any of whose openings would overlap (never seen: the kernel's
    are disjoint). (P, UV, mid) as lists, the untouched quads first in their order, then the walls' pieces, the
    glass, the reveals."""
    if not RECESS or not len(mid) or spec.cls in B.BRIDGE_CLASSES or spec.deck:
        return P, UV, mid
    sb = None
    if not spec.wall:
        sb = sheet_band(spec)
        if sb is None:
            return P, UV, mid
    elif spec.windows is False:
        return P, UV, mid
    F = _Frame(P, UV, mid)
    base = z_eave - spec.floors * spec.floor_h
    if spec.wall:
        # houses: each window (slot 2) WIN_OUT_M in front of its wall's quad
        glass, hq, hs0, hs1, hz0, hz1 = _laid_over(F, 2)
        D = RECESS_HOUSE_M
    else:
        # plaster sheet buildings: the sheet's windows in each of its quads (not a door's or plinth's plain strip)
        bk, bpt = sb
        cap = base + np.where(_on_wall(ring, street_edge(spec), F.a, F.d)[0], RECESS_STREET_MAX_Z, RECESS_MAX_Z)
        cand = np.flatnonzero(F.rect & (F.M == 0) & (F.u1 - F.u0 >= 0.5 / bpt) & (F.za < cap))
        hq, hs0, hs1, hz0, hz1 = _sheet_holes(F, cand, SHEET_OPENINGS[bk])
        ok = hz1 <= cap[hq]
        hq, hs0, hs1, hz0, hz1 = hq[ok], hs0[ok], hs1[ok], hz0[ok], hz1[ok]
        glass = None
        D = RECESS_SHEET_M
    if not len(hq):
        return P, UV, mid
    keep = _clear(spec, ring, F, base, hq, hs0, hs1, hz0)
    hq, hs0, hs1, hz0, hz1 = hq[keep], hs0[keep], hs1[keep], hz0[keep], hz1[keep]
    if glass is not None:
        glass = glass[keep]
    if not len(hq):
        return P, UV, mid
    if spec.wall:
        def reveals(qi, z0, z1):                        # the white trim
            return [np.broadcast_to(_TEXEL, (len(qi), 4, 2))] * 4
        slot = 2
    else:
        def reveals(qi, z0, z1):                        # the sheet's plain wall beside / over / under the window
            k = len(qi)
            pu = SHEET_PLAIN_U[sb[0]]
            du = D / B.TILE_PX_M
            va, vb = F.v0[qi] + (z0 - F.za[qi]) * F.vz[qi], F.v0[qi] + (z1 - F.za[qi]) * F.vz[qi]
            dv = D * F.vz[qi]
            U0, U1, Um, Up = np.full(k, pu), np.full(k, pu + du), np.full(k, pu - du / 2), np.full(k, pu + du / 2)
            return [np.stack([np.c_[U0, va], np.c_[U1, va], np.c_[U1, vb], np.c_[U0, vb]], 1),
                    np.stack([np.c_[U0, vb], np.c_[U1, vb], np.c_[U1, va], np.c_[U0, va]], 1),
                    np.stack([np.c_[Um, va], np.c_[Up, va], np.c_[Up, va - dv], np.c_[Um, va - dv]], 1),
                    np.stack([np.c_[Um, vb + dv], np.c_[Up, vb + dv], np.c_[Up, vb], np.c_[Um, vb]], 1)]
        slot = None
    out = _cut(F, hq, hs0, hs1, hz0, hz1, D, glass, B.WIN_OUT_M, reveals, slot)
    return (P, UV, mid) if out is None else out


def has_awning(spec):
    """awning()'s choice (its wall's length aside)."""
    return spec.sign is not None and spec.shop and not spec.plain and B._hash01(spec.seed, "awning") < AWNING_SHARE


def shopfronts(spec, ring, z_eave, P, UV, mid):
    """The shopfronts' glass set back RECESS_SHOP_M behind the wall's face (RECESS_SHOP): each bay's glass in the
    metal sheet's shop band (SHOP_OPENINGS, its mullions with it) cut out of the ground floor's shop quad (slot 1),
    its UVs and slot kept (the night glow), the returns, soffit and sill (over the band's own bulkhead) in the band's
    concrete. A big box's storefront (laid WIN_OUT_M over its plain wall) is first cut into the wall, flush, then the
    same. On the sign wall the glass stops under the sign boxes and the awning (both stay on the face)."""
    if not RECESS_SHOP or not len(mid) or spec.cls in B.BRIDGE_CLASSES or spec.deck or spec.plain:
        return P, UV, mid
    if spec.wall and spec.windows is False and spec.front is not None:
        F = _Frame(P, UV, mid)
        glass, hq, hs0, hs1, hz0, hz1 = _laid_over(F, 1)
        if len(hq):
            out = _cut(F, hq, hs0, hs1, hz0, hz1, 0.0, glass, B.WIN_OUT_M)
            if out is not None:
                P, UV, mid = out
    F = _Frame(P, UV, mid)
    bpt = B.BAND_SHOP[1]
    cand = np.flatnonzero(F.rect & (F.M == 1) & (F.u1 - F.u0 >= 0.5 / bpt) & (F.zb - F.za > 2.0))
    hq, hs0, hs1, hz0, hz1 = _sheet_holes(F, cand, SHOP_OPENINGS)
    if not len(hq):
        return P, UV, mid
    base = z_eave - spec.floors * spec.floor_h
    if spec.sign is not None:
        lim = []
        if spec.roof == "flat":                         # buildings.build's sign boxes
            z_top = z_eave - (spec.floors - 1) * spec.floor_h if spec.floors > 1 else z_eave
            lim.append(z_top - 0.15 - B.SIGN_H)
        if has_awning(spec):
            lim.append(base + B.GROUND_FLOOR_SHOP_M - B.SIGN_H - 0.25)
        if lim and not (spec.wall and spec.windows is False):     # (a big box's sign is over its storefront)
            on, _ = _on_wall(ring, spec.sign, F.a[hq], F.d[hq])
            hz1 = np.where(on, np.minimum(hz1, min(lim) - SHOP_CLEAR_M), hz1)
    keep = (hz1 - hz0 >= SHOP_MIN_H) & _clear(spec, ring, F, base, hq, hs0, hs1, hz0)
    hq, hs0, hs1, hz0, hz1 = hq[keep], hs0[keep], hs1[keep], hz0[keep], hz1[keep]
    if not len(hq):
        return P, UV, mid

    def reveals(qi, z0, z1):
        return [_patch(len(qi), *SHOP_REVEAL_UV)] * 4
    out = _cut(F, hq, hs0, hs1, hz0, hz1, RECESS_SHOP_M, None, 0.0, reveals, 1)
    return (P, UV, mid) if out is None else out


def fins(spec, ring, z_eave, P, UV, mid):
    """Aluminium on a curtain wall (FINS; the metal sheet's office bands): a vertical fin (front, sides, caps) on each
    bay's mullion line (the sheet's bay edges, where its thick mullions are) and a spandrel ledge (front, top,
    underside; its ends run on round a corner) at each floor line, up to RECESS_STREET_MAX_Z on the street wall and
    RECESS_MAX_Z on the others. Pieces in front of the glass (nothing cut), in the facade's own slot sampling the
    sheet's mullion, so they take the glass's tint."""
    if not FINS or not len(mid) or spec.wall or spec.plain or not spec.band \
            or tuple(spec.band[:2]) not in OFFICE_BANDS or B.LOOKS.get(spec.facade, (None,))[0] != B.OFFICE:
        return P, UV, mid
    F = _Frame(P, UV, mid)
    bpt = spec.band[1]
    base = z_eave - spec.floors * spec.floor_h
    st = street_edge(spec)
    hw, fd, lh, ld = FIN_W / 2, FIN_D, LEDGE_H / 2, LEDGE_D
    pts, k = [], 0
    for i in range(len(ring)):
        wl = _wall(ring, i)
        if wl is None or wl[3] < 2.0:
            continue
        ea, ed, en, Lw = wl
        on, se = _on_wall(ring, i, F.a, F.d)
        sel = np.flatnonzero(on & F.rect & (F.M == 0) & (F.u1 - F.u0 >= 0.5 / bpt))
        if not len(sel):
            continue
        cap = base + (RECESS_STREET_MAX_Z if i == st else RECESS_MAX_Z)
        zlo, ztop = float(F.za[sel].min()), min(float(F.zb[sel].max()), cap)
        if ztop - max(zlo, base) < 1.0:
            continue
        j = sel[np.argmin(F.za[sel])]
        kk = np.arange(np.ceil(F.u0[j] * bpt - 1e-9), np.floor(F.u1[j] * bpt + 1e-9) + 1)
        sf = se[j] + (kk / bpt - F.u0[j]) / F.us[j]
        sf = sf[(sf > FIN_END_M) & (sf < Lw - FIN_END_M)]

        def p(s, w):
            return ea + ed * s + en * w
        for s in sf:                                    # front, its sides, the top and bottom caps
            a0, a1, f0, f1 = p(s - hw, 0.0), p(s + hw, 0.0), p(s - hw, fd), p(s + hw, fd)
            pts += [((f0, zlo), (f1, zlo), (f1, ztop), (f0, ztop)), ((a0, zlo), (f0, zlo), (f0, ztop), (a0, ztop)),
                    ((f1, zlo), (a1, zlo), (a1, ztop), (f1, ztop)), ((a0, ztop), (f0, ztop), (f1, ztop), (a1, ztop)),
                    ((a0, zlo), (a1, zlo), (f1, zlo), (f0, zlo))]
        zs = np.unique(np.round(F.za[sel], 6))
        b0, b1 = p(0.0, 0.0), p(Lw, 0.0)
        c0, c1 = p(-ld, ld), p(Lw + ld, ld)
        for z in zs[(zs > zlo + 0.5) & (zs <= ztop - lh)]:      # the floor lines over the lowest
            z0, z1 = z - lh, z + lh
            pts += [((c0, z0), (c1, z0), (c1, z1), (c0, z1)), ((b0, z1), (c0, z1), (c1, z1), (b1, z1)),
                    ((b0, z0), (b1, z0), (c1, z0), (c0, z0))]
    if not pts:
        return P, UV, mid
    Pn = np.array([[(x, y, z) for (x, y), z in qd] for qd in pts], np.float64).reshape(-1, 12)
    UVn = _patch(len(pts), *FIN_UV).reshape(-1, 8)
    return (np.r_[F.Q.reshape(-1, 12), Pn].ravel().tolist(), np.r_[F.U.reshape(-1, 8), UVn].ravel().tolist(),
            np.r_[F.M, np.zeros(len(pts), np.int64)].tolist())


def depth(spec, ring, z_eave, P, UV, mid):
    """All of the walls' depth (buildings._walls, with the kit, never lite): recessed windows, set-back shopfronts,
    curtain walls' fins and ledges."""
    P, UV, mid = recess(spec, ring, z_eave, P, UV, mid)
    P, UV, mid = shopfronts(spec, ring, z_eave, P, UV, mid)
    return fins(spec, ring, z_eave, P, UV, mid)


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
