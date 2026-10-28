"""Facade details in 3D for the near buildings (buildings.build, full detail only; 2026-10-28, the user's "better
buildings" list): window trims and shutters, a door frame, a garage door where the driveway meets the house, a
shopfront awning, balconies on 3-6 storey apartments, a stone wainscot along the bottom of the walls.

The walls stay as they are (textured quads: the window sheets' bands, or a plain material with laid-over sash windows,
buildings._walls); the kit puts thin pieces in front of them: a window's sill, head and jambs stand out and catch the
light. The walls get depth of their own (depth(), run on buildings._walls' quads): the windows set into the walls
(recess(): the openings cut out of the wall quads, the glass set back, reveals round it; houses' windows and the
plaster sheet's lower floors), the shopfronts' glass set back (shopfronts()), aluminium fins and floor ledges on the
curtain walls (fins()). Pitched roofs get a soffit and fascia under their overhang, gutters and
downspouts (eaves(), gutters()); a share of the US houses a stone veneer front (veneer()). Each piece is one or two quads (a front and a top), not a box: a house's
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
FIN_W, FIN_D = 0.10, 0.25           # m: a fin's width, how far it stands out (7 x 12 cm hardly read from the street,
                                    # 2026-10-28's shots)
FIN_END_M = 0.3                     # m: no fin this close to a wall's corner
LEDGE_H, LEDGE_D = 0.2, 0.15        # m: a floor line's spandrel ledge, its height and depth
OFFICE_BANDS = ((0, 5), (1, 4))
FIN_UV = (202 / 1024, 208 / 1024, 1.0 - 230 / 1024, 1.0 - 20 / 1024)   # band 0's bay-edge mullion (unlit at night)
# Masonry trim (masonry_sills(), belts(), 2026-10-28: LoDo's and Castle Rock's brick and plaster blocks): on the plaster
# sheet's buildings a stone sill under and a flat lintel over every recessed window (the same caps), and belt courses
# at the floor lines of 2-6 storey blocks with a cornice. In the facade's own slot: on brick the sheet's light grey
# painted sill (a precast / limestone look against the brick, the building's tint over it), on plaster the band's
# own plain plaster (the same colour, its shape catching the light).
MASONRY_TRIM = True                 # False: none
MSILL_OUT, MSILL_T, MSILL_EXT = 0.06, 0.08, 0.06   # m: a sill stands out, its thickness, past the opening each side
LINTEL_H, LINTEL_OUT, LINTEL_EXT = 0.2, 0.03, 0.04
LINTEL_MIN_H = 0.08                 # m: a lintel squeezed under a belt course lower than this is left out
BELT_H, BELT_OUT = 0.18, 0.05       # m: a belt course's height (centred on the floor line), how far it stands out
BELT_FLOORS = (2, 6)
BELT_CLEAR_M = 0.01                 # m between a belt course and a sill / lintel
STONE_UV = (70 / 1024, 140 / 1024, 1.0 - 730 / 1024, 1.0 - 722 / 1024)   # band 2's painted sill: even light grey
BRICK_BAND = 3                      # buildings.BAND_BRICK's band
# House fronts (2026-10-28, Frederick's suburban streets): the pitched roofs' overhang closed (a soffit and a fascia:
# the roof planes are one-sided, the overhang showed the sky from below), gutters and downspouts, and a stone veneer
# across a share of the US houses' fronts (eaves(), gutters(), veneer()).
EAVES = True                        # False: the overhang open as before
FASCIA_H = 0.2                      # m: the fascia board's height; the soffit hangs this far under the roof's plane
FASCIA_TINTS = ((0.95, 0.95, 0.93), (0.95, 0.95, 0.93), (0.95, 0.95, 0.93), (0.30, 0.25, 0.21), (0.24, 0.24, 0.25))
GUTTERS = True                      # False: no gutters or downspouts
GUTTER_D, GUTTER_H = 0.12, 0.13     # m: a gutter's depth out from the fascia, its height under the eave edge
DOWNSPOUT_W = 0.08                  # m square
DOWNSPOUT_IN = 0.1                  # m from the front wall's corner to its centre (clear of a garage door's frame)
VENEER = True                       # False: no stone fronts
VENEER_SHARE = {"us_west": 0.4, "us_plains": 0.3, "us_south": 0.25, "us_east": 0.2}   # of the region's pitched houses
VENEER_H = (0.9, 1.2)               # m over the floors' base (hashed), but under the front windows' sills
VENEER_OUT = 0.04                   # m it stands out from the wall
VENEER_UV_M = (1.8, 0.9)            # m of wall per repeat of the stone texture (t_stone_brick_irregular: 12 courses):
                                    # long thin stones in ~7.5 cm courses, ledgestone (at the walls' 3 m it read as
                                    # big ashlar blocks, 2026-10-28's shots)
VENEER_TINTS = (0, 1, 3, 4)         # buildings.WALL_TINTS[STONE]: limestone, honey, grey, warm grey
VENEER_CLEAR_M = 0.05               # m kept from a door's, garage door's or window's trims
VENEER_PIER_M = 1.4                 # m: a garage door's stone pier at most this wide
# House window variety (house_windows(), 2026-10-28: every window of Frederick's suburbs was the same 1.05 x 1.75 m
# sash): per house, hashed; the glass always the sash's own UV rect (buildings.WIN_UV: the night map's lit cell).
HOUSE_VARIETY = True                # False: every house window the standard sash, as before
PAIR_SHARE = 0.4                    # houses with paired sashes (two units, a mullion between) on front and back walls ...
PAIR_WINDOW_SHARE = 0.6             # ... in this share of those walls' windows
MULLION_W = 0.1                     # m between a multiple window's units
PICTURE_SHARE = 0.4                 # houses with a picture window (three units) on the front's ground floor by the door
PICTURE_UNITS, PICTURE_UNIT_W = 3, 0.8
SMALL_SHARE = 0.5                   # side walls' windows that are small (bathrooms, bedrooms) ...
SMALL_W, SMALL_H, SMALL_SILL = 0.9, 1.2, 1.25   # ... this size, their sill this high over the floor
BLANK_SIDE_L = 9.0                  # m: side walls shorter than this ...
BLANK_SHARE = 0.4                   # ... have no windows on this share of houses
WINDOW_CLEAR_M = 0.05               # m a varied window's trims keep from the door's / garage door's frame, other windows
# A floor too low for the standard sash (its 0.85 m sill + 1.75 m under the eave's 0.2 m: walls under ~2.8 m, e.g. a
# 2.5 m ranch house's) had no windows at all (buildings.house_window_slots); with the variety on it gets low windows
# in the same bays: the sill LOW_SILL over the floor, the head LOW_HEAD_GAP under the eave, LOW_MIN_H tall at least.
LOW_SILL, LOW_HEAD_GAP, LOW_MIN_H = 0.75, 0.2, 1.0
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


class Win:
    """A house window (house_windows): s its centre along the wall, zf its sill, w x h its opening, units sash units
    uw wide with mw mullions between, kind ("std", "pair", "triple", "small"), shut its shutters drawn (left, right),
    s_std / zf_std the standard slot it stands in (buildings.house_window_slots: the native window quad's)."""
    __slots__ = ("s", "zf", "w", "h", "units", "uw", "mw", "kind", "shut", "s_std", "zf_std")

    def __init__(self, s, zf, kind="std", units=1, uw=None, h=None, mw=0.0):
        self.s, self.zf, self.kind, self.units, self.mw = s, zf, kind, units, mw
        self.uw = uw or B.WIN_W
        self.w = units * self.uw + (units - 1) * mw
        self.h = h or B.WIN_H
        self.shut = (False, False)
        self.s_std, self.zf_std = s, zf

    def box(self, extra=0.0):
        """(s0, s1, z0, z1) of the window with its trims (sill, head, jambs), extra more each side."""
        return (self.s - self.w / 2 - TRIM_W - extra, self.s + self.w / 2 + TRIM_W + extra,
                self.zf - SILL_H, self.zf + self.h + HEAD_H)


def low_windows(spec, L, z_eave, edge):
    """[Win] of a house wall's floors too low for the standard sash (house_window_slots leaves them out): low windows
    (kind "low", no native quad: s_std None) in the same bays, skipping the middle bay of an odd ground floor (the
    door's) and the garage door's place, as house_window_slots does."""
    nb = int(L // B.HOUSE_BAY_M)
    if nb < 1 or L < 2.0:
        return []
    gs = garage_span(spec, edge, L) if edge is not None else None
    base = z_eave - spec.floors * spec.floor_h
    door_wall = bool(spec.door) and spec.door[0] == edge
    out = []
    for f in range(spec.floors):
        if base + f * spec.floor_h + B.WIN_SILL + B.WIN_H <= z_eave - 0.2:
            continue                                    # (the standard sash fits there: a slot)
        zf = base + f * spec.floor_h + LOW_SILL
        h = min(B.WIN_H, min(base + (f + 1) * spec.floor_h, z_eave) - LOW_HEAD_GAP - zf)
        if h < LOW_MIN_H:
            continue
        for k in range(nb):
            if f == 0 and nb % 2 == 1 and k == nb // 2 and door_wall:
                continue                                # (the front door's place: on its own wall only)
            s = L * (k + 0.5) / nb
            if f == 0 and gs is not None and s + B.WIN_W / 2 > gs[0] - 0.3 and s - B.WIN_W / 2 < gs[1] + 0.3:
                continue
            w = Win(s, zf, "low", 1, B.WIN_W, h)
            w.s_std = w.zf_std = None
            out.append(w)
    return out


def middle_windows(spec, L, z_eave, edge):
    """[Win] for the ground floor's middle bay of an odd number of bays on a wall that is not the front door's:
    house_window_slots (and the native walls) leave that bay out on every wall, "where a front door may stand", so a
    one-storey house's walls of 3.4-6.8 m (one bay) had no window at all (2026-10-28: a Frederick ranch house's whole
    street side blank). A standard sash there, no native quad (s_std None: recess() cuts it into the wall's quad)."""
    nb = int(L // B.HOUSE_BAY_M)
    if nb < 1 or L < 2.0 or nb % 2 == 0 or (spec.door and spec.door[0] == edge):
        return []
    base = z_eave - spec.floors * spec.floor_h
    zf = base + B.WIN_SILL
    if zf + B.WIN_H > z_eave - 0.2:
        return []                                       # (a low floor: low_windows' bays)
    s = L * (nb // 2 + 0.5) / nb
    gs = garage_span(spec, edge, L) if edge is not None else None
    if gs is not None and s + B.WIN_W / 2 > gs[0] - 0.3 and s - B.WIN_W / 2 < gs[1] + 0.3:
        return []
    w = Win(s, zf)
    w.s_std = w.zf_std = None
    return [w]


def has_shutters(spec):
    return B._hash01(spec.seed, "shutters") < SHUTTER_SHARE and spec.roof != "flat"


def _hit(b, boxes):
    return any(b[0] < c[1] and b[1] > c[0] and b[2] < c[3] and b[3] > c[2] for c in boxes)


def house_windows(spec, ring, edge, z_eave):
    """A house wall's windows [Win] (its drawn ring's edge): its standard sash slots (buildings.house_window_slots),
    and with HOUSE_VARIETY (and RECESS: the windows are cut and glazed by recess()) a house's own mix, hashed: paired
    sashes on the front and back walls (PAIR_SHARE of houses), a triple picture window on the front's ground floor
    beside the door (PICTURE_SHARE), small high windows on the side walls (SMALL_SHARE of their windows) and no
    windows at all on a share of narrow side walls (BLANK_SHARE: tract houses' blank sides). A variant that would touch
    the door's or garage door's frame, another window's trims or the wall's end stays the standard sash. Shutters on
    a share of houses (SHUTTER_SHARE), each side where it fits."""
    wl = _wall(ring, edge)
    if wl is None:
        return []
    a, d, out, L = wl
    wins = [Win(float(np.dot(c - a, d)), zf) for c, zf in B.house_window_slots(spec, a, d, out, L, z_eave, edge)]
    shut = has_shutters(spec)
    vary = HOUSE_VARIETY and RECESS and spec.wall and spec.windows is not False
    if not vary:
        if shut:                                        # (as the trims always did: each side within the wall)
            for w in wins:
                h0, h1 = w.s - w.w / 2 - TRIM_W, w.s + w.w / 2 + TRIM_W
                w.shut = (h0 - w.uw / 2 > 0.15 and h0 < L - 0.15, h1 > 0.15 and h1 + w.uw / 2 < L - 0.15)
        return wins
    wins += low_windows(spec, L, z_eave, edge) + middle_windows(spec, L, z_eave, edge)
    if not wins:
        return []
    base = z_eave - spec.floors * spec.floor_h
    role = None
    fe = front_edge(spec)
    fw = _wall(ring, fe) if fe is not None else None
    if fw is not None:
        cs = abs(float(np.dot(fw[1], d)))
        role = "front" if edge == fe else ("back" if cs > 0.7 else ("side" if cs < 0.3 else None))
    if role == "side" and L < BLANK_SIDE_L and B._hash01(spec.seed, "blank", edge) < BLANK_SHARE:
        return []
    fixed = []                                          # the door's and garage door's frames
    if spec.door and spec.door[0] == edge:
        h = DOOR_W / 2 + FRAME_W * 1.5
        fixed.append((spec.door[1] - h, spec.door[1] + h, -1e9, base + B.STOOP_H + B.PORCH_DECK_M + DOOR_H + 0.2))
    dr = getattr(spec, "drive", None)
    if dr and dr[0] == edge:
        gs = garage_span(spec, edge, L)
        if gs is not None:
            fixed.append((gs[0] - FRAME_W, gs[1] + FRAME_W, -1e9, base + GARAGE_H + 0.18))
    pic = None
    if role == "front" and spec.door and spec.door[0] == edge and B._hash01(spec.seed, "picture") < PICTURE_SHARE:
        ground = [k for k, w in enumerate(wins) if w.zf < base + spec.floor_h and w.kind == "std"]
        if ground:
            pic = min(ground, key=lambda k: abs(wins[k].s - spec.door[1]))
    # a standard sash whose trims stood on the door's frame (a narrow house's door at a fifth of its wall, a window in
    # the bay beside it): left out, the wall plain there (it was drawn over the frame)
    wins = [w for w in wins if not _hit(w.box(), fixed)]
    if pic is not None:
        ground = [k for k, w in enumerate(wins) if w.zf < base + spec.floor_h and w.kind == "std"]
        pic = min(ground, key=lambda k: abs(wins[k].s - spec.door[1])) if ground else None
    pairs = B._hash01(spec.seed, "pairs") < PAIR_SHARE
    for k, w in enumerate(wins):
        cand = None
        if w.kind != "std":                             # (a low window stays one)
            continue
        if k == pic:
            cand = Win(w.s, w.zf, "triple", PICTURE_UNITS, PICTURE_UNIT_W, None, MULLION_W)
        elif role in ("front", "back") and pairs and B._hash01(spec.seed, "pair", edge, k) < PAIR_WINDOW_SHARE:
            cand = Win(w.s, w.zf, "pair", 2, B.WIN_W, None, MULLION_W)
        elif role == "side" and B._hash01(spec.seed, "small", edge, k) < SMALL_SHARE:
            cand = Win(w.s, w.zf - B.WIN_SILL + SMALL_SILL, "small", 1, SMALL_W, SMALL_H)
        if cand is None:
            continue
        b = cand.box(WINDOW_CLEAR_M)
        if b[0] < 0.15 or b[1] > L - 0.15 or _hit(b, fixed) \
                or _hit(b, [o.box() for j, o in enumerate(wins) if j != k]):
            continue
        cand.s_std, cand.zf_std = w.s_std, w.zf_std
        wins[k] = cand
    if shut:                                            # shutters where they touch nothing
        taken = list(fixed) + [w.box() for w in wins]
        for w in wins:
            if w.kind not in ("std", "pair"):
                continue
            h0, h1 = w.s - w.w / 2 - TRIM_W, w.s + w.w / 2 + TRIM_W
            sw = w.uw / 2
            sides = []
            for e0, e1 in ((h0 - sw, h0), (h1, h1 + sw)):
                b = (e0, e1, w.zf, w.zf + w.h)
                ok = e0 > 0.15 and e1 < L - 0.15 and not _hit(b, [t for t in taken if t != w.box()])
                if ok:
                    taken.append(b)
                sides.append(ok)
            w.shut = tuple(sides)
    return wins


def windows(m, spec, ring, z_eave, mat_idx):
    """Trims (sill, head, two jambs) round each of a house's windows (house_windows: the sashes, a multiple window's
    whole opening), and a pair of shutters on a share of houses (SHUTTER_SHARE, its tint hashed), where they fit."""
    trim = mat_idx[TRIM]
    sh = None
    if has_shutters(spec):
        r, g, b = SHUTTER_TINTS[int(B._hash01(spec.seed, "shtint") * len(SHUTTER_TINTS))]
        sh = (mat_idx[TRIM][0], (int(r * 255), int(g * 255), int(b * 255), 255))
    for i in range(len(ring)):
        w = _wall(ring, i)
        if w is None:
            continue
        a, d, out, L = w
        for wn in house_windows(spec, ring, i, z_eave):
            h0, h1 = wn.s - wn.w / 2, wn.s + wn.w / 2
            zf, zt = wn.zf, wn.zf + wn.h
            _ledge(m, a, d, out, h0 - TRIM_W, h1 + TRIM_W, zf - SILL_H, zf, SILL_OUT, trim)                  # sill
            _ledge(m, a, d, out, h0 - TRIM_W, h1 + TRIM_W, zt, zt + HEAD_H, HEAD_OUT, trim)                  # head
            for e0, e1 in ((h0 - TRIM_W, h0), (h1, h1 + TRIM_W)):                                              # jambs
                _front(m, a + d * e0 + out * JAMB_OUT, a + d * e1 + out * JAMB_OUT, zf, zt, trim)
            if sh is not None:
                for ok, (e0, e1) in zip(wn.shut, ((h0 - TRIM_W - wn.uw / 2, h0 - TRIM_W),
                                                  (h1 + TRIM_W, h1 + TRIM_W + wn.uw / 2))):
                    if ok:
                        _front(m, a + d * e0 + out * SHUTTER_OUT, a + d * e1 + out * SHUTTER_OUT, zf, zt, sh)


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


def balcony_bays(spec, ring):
    """balconies()' choice: (its wall's edge, [(s0, s1)] its balconies' spans along it), or None."""
    lo, hi = BALCONY_FLOORS
    if not spec.door or not (lo <= spec.floors <= hi) or (spec.cls or "") not in APARTMENT_CLASSES or spec.wall \
            or spec.plain or not spec.band or B._hash01(spec.seed, "balcony") >= BALCONY_SHARE:
        return None
    w = _wall(ring, spec.door[0])
    if w is None:
        return None
    L = w[3]
    bay = B.TILE_PX_M / spec.band[1]
    nb = int(round(L / bay))
    if nb < 3:
        return None
    bw = L / nb
    return spec.door[0], [(k * bw + 0.15 * bw, (k + 1) * bw - 0.15 * bw) for k in range(1, nb - 1, 2)]


def balconies(m, spec, ring, z_eave, mat_idx):
    """Balconies on every other bay of an apartment block's street wall (spec.door's), from the second floor up: a
    concrete slab and a steel railing (front and sides)."""
    got = balcony_bays(spec, ring)
    if got is None:
        return
    a, d, out, L = _wall(ring, got[0])
    base = z_eave - spec.floors * spec.floor_h
    slab = mat_idx["bne_precast"]
    rail = mat_idx[BALCONY_STEEL]
    for f in range(1, spec.floors):
        z = base + f * spec.floor_h
        for s0, s1 in got[1]:
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


def _pieces(F, hq, hs0, hs1, hz0, hz1):
    """The wall pieces round any openings (quad hq, s0..s1, z0..z1 on it) of whatever heights: per quad, bands between
    every opening's top and bottom, in each the gaps between the openings across it, a piece carried up while the next
    band has the same gap. (quad, s0, s1, z0, z1) arrays, or None if two openings overlap."""
    out = []
    for q in np.unique(hq):
        k = np.flatnonzero(hq == q)
        L = float(F.L[q])
        zs = sorted(set([float(F.za[q]), float(F.zb[q])] + hz0[k].tolist() + hz1[k].tolist()))
        open_ = {}                                      # (s0, s1) -> its z0, carried up
        for zi, zj in zip(zs, zs[1:]):
            if zj - zi < 1e-9:
                continue
            c = k[(hz0[k] <= zi + 1e-9) & (hz1[k] >= zj - 1e-9)]
            gaps, cur = [], 0.0
            for a0, a1 in sorted(zip(hs0[c].tolist(), hs1[c].tolist())):
                if a0 < cur - 1e-6:
                    return None
                if a0 > cur + 1e-9:
                    gaps.append((round(cur, 9), round(a0, 9), cur, a0))
                cur = max(cur, a1)
            if L > cur + 1e-9:
                gaps.append((round(cur, 9), round(L, 9), cur, L))
            keys = {(g[0], g[1]): g for g in gaps}
            for key in list(open_):
                if key not in keys:                     # (that gap ends under this band)
                    g0, z0 = open_.pop(key)
                    out.append((q, g0[2], g0[3], z0, zi))
            for key, g in keys.items():
                if key not in open_:
                    open_[key] = (g, zi)
        for g0, z0 in open_.values():
            out.append((q, g0[2], g0[3], z0, zs[-1]))
    if not out:
        return np.zeros(0, np.int64), *(np.zeros(0),) * 4
    A = np.array(out, np.float64)
    return A[:, 0].astype(np.int64), A[:, 1], A[:, 2], A[:, 3], A[:, 4]


def _cut(F, hq, hs0, hs1, hz0, hz1, D, glass=None, glass_out=0.0, reveals=None, reveal_slot=None, units=None,
         drop=None):
    """The quads with the openings (quad hq, s0..s1, z0..z1 on it) cut out: per quad, rows of openings (one z range
    each) and the strips between them, in a row the gaps between its openings; the glass D behind the face (glass:
    quads moved back from glass_out in front of it, else each opening's own piece of its quad); reveals (a function
    (hq, z0, z1) -> 4 UV arrays: left jamb, right jamb, sill, head) from the face back to the glass, in reveal_slot.
    units (a house's windows, house_windows): per opening (count, unit width, mullion width): its glass the sash
    (buildings.WIN_UV, slot 2) per unit, a white mullion post (front and sides) between units. drop: more quads to
    leave out (the native windows these replace). (P, UV, mid) lists, the untouched quads first in their order; None
    if openings would overlap."""
    o = np.lexsort((hs0, hz0, hq))
    hq, hs0, hs1, hz0, hz1 = hq[o], hs0[o], hs1[o], hz0[o], hz1[o]
    if glass is not None:
        glass = glass[o]
    if units is not None:
        units = tuple(np.asarray(x)[o] for x in units)
    nh = len(hq)
    if not nh:                                          # no opening: the quads less `drop` (a house whose windows are
        cut = np.zeros(F.q, bool)                       # all on blank walls: its native sashes left out, the walls
        if drop is not None and len(drop):              # whole; 2026-10-28 the row split below raised on it, IndexError,
            cut[np.asarray(drop, np.int64)] = True      # and the road tile failed with all its buildings)
        k = np.flatnonzero(~cut)
        return F.Q.reshape(-1, 12)[k].ravel().tolist(), F.U.reshape(-1, 8)[k].ravel().tolist(), F.M[k].tolist()
    first = np.ones(nh, bool)
    first[1:] = (hq[1:] != hq[:-1]) | (np.abs(hz0[1:] - hz0[:-1]) > 1e-6)
    prev1 = np.r_[0.0, hs1[:-1]]
    if (~first & (hs0 < prev1 - 1e-6)).any():
        return None
    general = (~first & (np.abs(hz1 - np.r_[0.0, hz1[:-1]]) > 1e-6)).any()
    last = np.r_[first[1:], True]
    gq = np.r_[hq, hq[last]]
    gs0 = np.r_[np.where(first, 0.0, prev1), hs1[last]]
    gs1 = np.r_[hs0, F.L[hq[last]]]
    gz0, gz1 = np.r_[hz0, hz0[last]], np.r_[hz1, hz1[last]]
    rq, rz0, rz1 = hq[first], hz0[first], hz1[first]
    rfirst = np.ones(len(rq), bool)
    rfirst[1:] = rq[1:] != rq[:-1]
    rprev1 = np.r_[0.0, rz1[:-1]]
    general = general or (~rfirst & (rz0 < rprev1 - 1e-6)).any()
    rlast = np.r_[rfirst[1:], True]
    sq = np.r_[rq, rq[rlast]]
    sz0 = np.r_[np.where(rfirst, F.za[rq], rprev1), rz1[rlast]]
    sz1 = np.r_[rz0, F.zb[rq[rlast]]]
    pq = np.r_[gq, sq]
    ps0, ps1 = np.r_[gs0, np.zeros(len(sq))], np.r_[gs1, F.L[sq]]
    pz0, pz1 = np.r_[gz0, sz0], np.r_[gz1, sz1]
    if general:                                         # rows of different heights in a quad (a house's small window)
        got = _pieces(F, hq, hs0, hs1, hz0, hz1)
        if got is None:
            return None
        pq, ps0, ps1, pz0, pz1 = got
    big = (ps1 - ps0 > 1e-6) & (pz1 - pz0 > 1e-6)
    pq, ps0, ps1, pz0, pz1 = pq[big], ps0[big], ps1[big], pz0[big], pz1[big]
    zero = np.zeros(len(pq))
    p_, t_ = F.quads(pq, ((ps0, pz0, zero), (ps1, pz0, zero), (ps1, pz1, zero), (ps0, pz1, zero)))
    out_P, out_UV, out_M = [p_], [t_], [F.M[pq]]
    zh, Dh = np.zeros(nh), np.full(nh, -D)
    if units is not None:                               # sash units and mullion posts
        nu, uw, mw = units
        nu = nu.astype(np.int64)
        rep_ = np.repeat(np.arange(nh), nu)
        j = np.arange(len(rep_)) - np.repeat(np.cumsum(nu) - nu, nu)
        gs0 = hs0[rep_] + j * (uw[rep_] + mw[rep_])
        gs1 = gs0 + uw[rep_]
        u0, u1, v0, v1 = B.WIN_UV
        k = len(rep_)
        gD = np.full(k, -D)
        p_, t_ = F.quads(hq[rep_], ((gs0, hz0[rep_], gD), (gs1, hz0[rep_], gD), (gs1, hz1[rep_], gD),
                                    (gs0, hz1[rep_], gD)), _patch(k, u0, u1, v0, v1))
        out_P.append(p_)
        out_UV.append(t_)
        out_M.append(np.full(k, 2))
        mm = j > 0                                      # a mullion before every unit but the first
        if mm.any():
            r, m1 = rep_[mm], gs0[mm]
            m0 = m1 - mw[r]
            q_, z0_, z1_ = hq[r], hz0[r], hz1[r]
            km = int(mm.sum())
            z_, dm = np.zeros(km), np.full(km, -D)
            for corners in (((m0, z0_, z_), (m1, z0_, z_), (m1, z1_, z_), (m0, z1_, z_)),          # its front
                            ((m0, z1_, z_), (m0, z1_, dm), (m0, z0_, dm), (m0, z0_, z_)),          # (facing -d)
                            ((m1, z0_, z_), (m1, z0_, dm), (m1, z1_, dm), (m1, z1_, z_))):         # (facing +d)
                p_, t_ = F.quads(q_, corners, np.broadcast_to(_TEXEL, (km, 4, 2)))
                out_P.append(p_)
                out_UV.append(t_)
                out_M.append(np.full(km, 2))
    elif glass is not None:                             # a laid-over quad (a house's window), moved back
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
    if drop is not None and len(drop):
        cut[np.asarray(drop, np.int64)] = True
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


def _house_openings(spec, ring, z_eave, F, base, glass, hq, hs0, hs1, hz0, hz1):
    """recess() for a house with HOUSE_VARIETY: each native sash quad (glass, on wall quad hq, s0..s1, z0..z1) replaced
    by its house_windows() window, cut and glazed unit by unit (a blank wall's left out, the wall whole there); one
    that would touch the door, the garage door or the stone band is the standard sash, and if that would too, the
    native quad stays laid on the wall as before. (P, UV, mid) or None."""
    W = {i: house_windows(spec, ring, i, z_eave) for i in range(len(ring))}
    hole, std, drop = [], [], []
    for j in range(len(glass)):
        q = hq[j]
        e = off = None
        for i in range(len(ring)):
            on, se = _on_wall(ring, i, F.a[q:q + 1], F.d[q:q + 1])
            if on[0]:
                e, off = i, float(se[0])
                break
        if e is None:
            continue
        sc = (hs0[j] + hs1[j]) / 2 + off
        w = next((w for w in W[e] if w.s_std is not None and abs(w.s_std - sc) < 1e-4
                  and abs(w.zf_std - hz0[j]) < 1e-4), None)
        if w is None:
            drop.append(int(glass[j]))                  # (a blank wall: no window there)
            continue
        hole.append((j, q, w.s - w.w / 2 - off, w.s + w.w / 2 - off, w.zf, w.zf + w.h, w.units, w.uw, w.mw))
        std.append((j, q, hs0[j], hs1[j], hz0[j], hz1[j], 1, B.WIN_W, 0.0))
    rows = []
    if hole:
        H, S = np.array(hole, np.float64), np.array(std, np.float64)
        hq_ = H[:, 1].astype(np.int64)
        ok = _clear(spec, ring, F, base, hq_, H[:, 2], H[:, 3], H[:, 4])
        sok = _clear(spec, ring, F, base, hq_, S[:, 2], S[:, 3], S[:, 4])
        rows = [H[i] if ok[i] else S[i] for i in range(len(H)) if ok[i] or sok[i]]
    extra = []                                          # low windows: no native quad, cut into their wall's quad
    for e, ws in W.items():
        lows = [w for w in ws if w.s_std is None]
        if not lows:
            continue
        on, se = _on_wall(ring, e, F.a, F.d)
        qs = np.flatnonzero(on & F.rect & (F.M == 0))
        for w in lows:
            q = next((q for q in qs if se[q] - 1e-6 <= w.s - w.w / 2 and se[q] + F.L[q] + 1e-6 >= w.s + w.w / 2
                      and F.za[q] < w.zf and F.zb[q] > w.zf + w.h), None)
            if q is not None:
                o = float(se[q])
                extra.append((-1, q, w.s - w.w / 2 - o, w.s + w.w / 2 - o, w.zf, w.zf + w.h, w.units, w.uw, w.mw))
    if extra:
        X = np.array(extra, np.float64)
        okx = _clear(spec, ring, F, base, X[:, 1].astype(np.int64), X[:, 2], X[:, 3], X[:, 4])
        rows += [X[i] for i in range(len(X)) if okx[i]]
    if not rows and not drop:
        return None
    R = np.array(rows, np.float64).reshape(-1, 9)
    drop += [int(glass[int(j)]) for j in R[:, 0] if j >= 0]

    def reveals(qi, z0, z1):                            # the white trim
        return [np.broadcast_to(_TEXEL, (len(qi), 4, 2))] * 4
    return _cut(F, R[:, 1].astype(np.int64), R[:, 2], R[:, 3], R[:, 4], R[:, 5], RECESS_HOUSE_M, None, 0.0, reveals, 2,
                (R[:, 6].astype(np.int64), R[:, 7], R[:, 8]), drop)


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
        if HOUSE_VARIETY:
            out = _house_openings(spec, ring, z_eave, F, base, glass, hq, hs0, hs1, hz0, hz1)
            return (P, UV, mid) if out is None else out
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
    if out is not None and sb is not None and MASONRY_TRIM:
        extra = masonry_sills(spec, F, hq, hs0, hs1, hz0, hz1, sb, z_eave)
        if extra is not None:
            out = (out[0] + extra[0], out[1] + extra[1], out[2] + extra[2])
    return (P, UV, mid) if out is None else out


def _faces(F, qi, s0, s1, z0, z1, w1, faces, uv):
    """Faces of boxes on quads qi's walls (arrays: s0..s1 along, z0..z1, from the face out to w1): faces from f(ront),
    t(op), u(nderside), l(eft end, -d), r(ight end, +d). (P (k, 12), UV (k, 8))."""
    k = len(qi)
    zero, W = np.zeros(k), np.full(k, w1)
    corners = {"f": ((s0, z0, W), (s1, z0, W), (s1, z1, W), (s0, z1, W)),
               "t": ((s0, z1, zero), (s0, z1, W), (s1, z1, W), (s1, z1, zero)),
               "u": ((s0, z0, zero), (s1, z0, zero), (s1, z0, W), (s0, z0, W)),
               "l": ((s0, z0, zero), (s0, z0, W), (s0, z1, W), (s0, z1, zero)),
               "r": ((s1, z0, W), (s1, z0, zero), (s1, z1, zero), (s1, z1, W))}
    Ps, Us = [], []
    for f in faces:
        p_, t_ = F.quads(qi, corners[f], uv)
        Ps.append(p_)
        Us.append(t_)
    return np.concatenate(Ps), np.concatenate(Us)


def _trim_uv(spec, sb, k, v=None):
    """k quads' UVs for masonry trim: on brick the sheet's light painted sill, on plaster its plain plaster at v (the
    band's middle if None)."""
    if sb[0] == BRICK_BAND:
        return _patch(k, *STONE_UV)
    pu = SHEET_PLAIN_U[sb[0]]
    if v is None:
        v0, v1 = B._band_v(spec.band, sb[0])
        v = np.full(k, (v0 + v1) / 2)
    e = 4 / 1024
    return np.stack([np.c_[np.full(k, pu), v - e], np.c_[np.full(k, pu + 2 * e), v - e],
                     np.c_[np.full(k, pu + 2 * e), v + e], np.c_[np.full(k, pu), v + e]], 1)


def has_belts(spec):
    """belts()' choice: a 2-6 storey plaster sheet block with a cornice (buildings.build: a flat roof, not plain)."""
    lo, hi = BELT_FLOORS
    return (MASONRY_TRIM and sheet_band(spec) is not None and lo <= spec.floors <= hi and spec.roof == "flat"
            and not spec.plain and spec.cls not in B.BRIDGE_CLASSES and not spec.deck and (spec.min_h or 0.0) <= 0.5)


def masonry_sills(spec, F, hq, hs0, hs1, hz0, hz1, sb, z_eave):
    """A stone sill (its front and top, as the kit's house trims: ~36 vertices an opening with the underside and ends,
    LoDo's tile came near the kit's budget) under and a flat lintel (front, underside) over each recessed
    sheet window (quad hq, s0..s1, z0..z1: the measured opening, so they sit on the painted sill and head), a little
    wider than it, within its quad; no lintel into the cornice, none (or a lower one) into a belt course. (P, UV, mid)
    lists to append, or None."""
    k = len(hq)
    if not k:
        return None
    L = F.L[hq]
    vm = F.v0[hq] + ((hz0 + hz1) / 2 - F.za[hq]) * F.vz[hq]
    base = z_eave - spec.floors * spec.floor_h
    fh = spec.floor_h
    s0, s1 = np.maximum(hs0 - MSILL_EXT, 0.0), np.minimum(hs1 + MSILL_EXT, L)
    sz0 = hz0 - MSILL_T
    top = hz1 + LINTEL_H
    keep_s = np.ones(k, bool)
    if has_belts(spec):                                 # clear of the belt courses at the floor lines
        below = base + np.floor((hz0 - base) / fh + 1e-6) * fh
        keep_s = (sz0 >= below + BELT_H / 2 + BELT_CLEAR_M) | (below <= base + 1e-6)
        above = base + np.ceil((hz1 - base) / fh - 1e-6) * fh
        top = np.where(above < z_eave - 1e-6, np.minimum(top, above - BELT_H / 2 - BELT_CLEAR_M), top)
    if spec.roof == "flat" and not spec.plain and spec.floors <= 6:     # (buildings.build's cornice)
        top = np.where(top > z_eave - B.CORNICE_DOWN_M, -np.inf, top)
    keep_l = top - hz1 >= LINTEL_MIN_H
    uv = _trim_uv(spec, sb, k, vm)
    P_, U_ = [], []
    if keep_s.any():
        p_, t_ = _faces(F, hq[keep_s], s0[keep_s], s1[keep_s], sz0[keep_s], hz0[keep_s], MSILL_OUT, "ft", uv[keep_s])
        P_.append(p_)
        U_.append(t_)
    if keep_l.any():
        l0, l1 = np.maximum(hs0 - LINTEL_EXT, 0.0), np.minimum(hs1 + LINTEL_EXT, L)
        p_, t_ = _faces(F, hq[keep_l], l0[keep_l], l1[keep_l], hz1[keep_l], top[keep_l], LINTEL_OUT, "fu",
                        uv[keep_l])
        P_.append(p_)
        U_.append(t_)
    if not P_:
        return None
    Pn, Un = np.concatenate(P_), np.concatenate(U_)
    return Pn.ravel().tolist(), Un.ravel().tolist(), [0] * len(Pn)


def belts(spec, ring, z_eave, P, UV, mid):
    """Belt courses (MASONRY_TRIM, has_belts): a band BELT_H tall standing out BELT_OUT at each floor line of every
    wall (front, top, underside; mitred round the corners), up to RECESS_STREET_MAX_Z on the street wall and
    RECESS_MAX_Z on the others, under the cornice; none over a shopfront's sign boxes and awning (its sign wall's first
    floor line), broken round the balconies. In the facade's slot (_trim_uv). (P, UV, mid) with them appended."""
    if not len(mid) or not has_belts(spec):
        return P, UV, mid
    sb = sheet_band(spec)
    base = z_eave - spec.floors * spec.floor_h
    st = street_edge(spec)
    bal = balcony_bays(spec, ring)
    h, bo = BELT_H / 2, BELT_OUT
    quads = []
    for i in range(len(ring)):
        wl = _wall(ring, i)
        if wl is None or wl[3] < 1.0:
            continue
        a, d, n, L = wl
        cap = base + (RECESS_STREET_MAX_Z if i == st else RECESS_MAX_Z)
        N, Dv = _out3(n), _out3(d)

        def p(s, w, z):
            q = a + d * s + n * w
            return float(q[0]), float(q[1]), float(z)
        for f in range(1, spec.floors):
            z = base + f * spec.floor_h
            if z + h > cap or z + h > z_eave - B.CORNICE_DOWN_M - 0.05:
                break
            if f == 1 and spec.shop and spec.sign == i:
                continue                                # (the sign boxes and the awning there)
            spans = [(0.0, L)]
            if bal is not None and bal[0] == i:
                for b0, b1 in bal[1]:
                    spans = [x for s0, s1 in spans for x in ((s0, min(s1, b0 - 0.05)), (max(s0, b1 + 0.05), s1))
                             if x[1] - x[0] > 0.05]
            z0, z1 = z - h, z + h
            for s0, s1 in spans:
                e0, e1 = (bo if s0 <= 1e-9 else 0.0), (bo if s1 >= L - 1e-9 else 0.0)    # (round a corner)
                quads.append(([p(s0 - e0, bo, z0), p(s1 + e1, bo, z0), p(s1 + e1, bo, z1), p(s0 - e0, bo, z1)], N))
                quads.append(([p(s0, 0.0, z1), p(s0 - e0, bo, z1), p(s1 + e1, bo, z1), p(s1, 0.0, z1)], _UP))
                quads.append(([p(s0, 0.0, z0), p(s1, 0.0, z0), p(s1 + e1, bo, z0), p(s0 - e0, bo, z0)], _DOWN))
                if not e0:
                    quads.append(([p(s0, 0.0, z0), p(s0, bo, z0), p(s0, bo, z1), p(s0, 0.0, z1)], -Dv))
                if not e1:
                    quads.append(([p(s1, bo, z0), p(s1, 0.0, z0), p(s1, 0.0, z1), p(s1, bo, z1)], Dv))
    if not quads:
        return P, UV, mid
    Pq = []
    for pts, want in quads:                             # (each facing its way)
        q = np.asarray(pts, np.float64)
        if float(np.dot(np.cross(q[1] - q[0], q[2] - q[0]), want)) < 0:
            q = q[::-1]
        Pq.append(q.ravel())
    k = len(Pq)
    Un = _trim_uv(spec, sb, k).reshape(-1, 8)
    M = (np.frombuffer(bytes(mid), np.uint8) if isinstance(mid, (bytes, bytearray)) else np.asarray(mid)).astype(np.int64)
    return (np.r_[np.asarray(P, np.float64), np.concatenate(Pq)].tolist(),
            np.r_[np.asarray(UV, np.float64), Un.ravel()].tolist(), np.r_[M, np.zeros(k, np.int64)].tolist())


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
    """All of the walls' depth (buildings._walls, with the kit, never lite): recessed windows (with masonry sills and
    lintels), set-back shopfronts, belt courses, curtain walls' fins and ledges."""
    P, UV, mid = recess(spec, ring, z_eave, P, UV, mid)
    P, UV, mid = shopfronts(spec, ring, z_eave, P, UV, mid)
    P, UV, mid = belts(spec, ring, z_eave, P, UV, mid)
    return fins(spec, ring, z_eave, P, UV, mid)


def _oq(m, pts, uv, mat, want):
    """A quad (4 (x, y, z)) facing along `want` (its corners reversed if they ran the other way)."""
    p = np.asarray(pts, np.float64)
    nrm = np.cross(p[1] - p[0], p[2] - p[0])
    if float(np.dot(nrm, want)) < 0:
        pts, uv = pts[::-1], uv[::-1]
    m.quad(*[tuple(float(v) for v in q) for q in pts], *uv, mat)


_PLAIN = ((0.002, 0.995), (0.012, 0.995), (0.012, 0.999), (0.002, 0.999))
_UP, _DOWN = np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, -1.0])


def _xyz(p, z):
    return float(p[0]), float(p[1]), float(z)


def _out3(n):
    return np.array([n[0], n[1], 0.0])


def eave_runs(spec, ring, z_eave):
    """A pitched roof's eaves as buildings._roof draws them: (ze (the eave edge's height), runs, rakes, tp). runs:
    [(edges, closed)], each edge (W0, W1, E0, E1): its wall line (at z_eave) and its eave edge (EAVE_M out, at ze),
    in order round the roof. rakes, the gables' overhangs (the roof's planes past a gable wall): [(soffit quads (4
    (x, y, z) on the roof's plane), fascia edges ((x, y, z), (x, y, z), its outward (3,)))]. None for other roofs."""
    if spec.roof not in ("gabled", "hipped") or spec.cls in B.CHURCH_CLASSES or spec.steeple:
        return None
    tp = math.tan(math.radians(B.roof_pitch(spec)))
    ze = z_eave - B.EAVE_M * tp
    sk = B.roof_skeleton(spec)
    if sk is not None:
        eaves_, nodes, nt, faces, gables = sk
        W, E = np.asarray(spec.ring, np.float64)[:, :2], np.asarray(eaves_, np.float64)[:, :2]
        n = len(W)
        if len(E) != n or float(np.hypot(*(E - W).T).max()) > 4 * B.EAVE_M:
            return None
        gab = {int(i): int(g) for i, g in gables}
        edges = [None if i in gab else (W[i], W[(i + 1) % n], E[i], E[(i + 1) % n]) for i in range(n)]
        ns = [_unit(np.array([(E[(i + 1) % n] - E[i])[1], -(E[(i + 1) % n] - E[i])[0]])) for i in range(n)]

        def zf(p, j):                                   # the height of face j's plane over p
            return ze + float(np.dot(E[j] - p, ns[j])) * tp
        rakes = []
        for i, g in gab.items():
            G = np.asarray(nodes[g], np.float64)[:2]
            Gw = G - ns[i] * B.EAVE_M
            j0, j1 = (i - 1) % n, (i + 1) % n
            soff = [[(*W[i], zf(W[i], j0)), (*E[i], zf(E[i], j0)), (*G, zf(G, j0)), (*Gw, zf(Gw, j0))],
                    [(*Gw, zf(Gw, j1)), (*G, zf(G, j1)), (*E[j1], zf(E[j1], j1)), (*W[j1], zf(W[j1], j1))]]
            fas = [((*E[i], zf(E[i], j0)), (*G, zf(G, j0)), _out3(ns[i])),
                   ((*G, zf(G, j1)), (*E[j1], zf(E[j1], j1)), _out3(ns[i]))]
            rakes.append((soff, fas))
        if not gab:
            return ze, [(edges, True)], rakes, tp
        runs, cur = [], []
        start = (max(gab) + 1) % n
        for k in range(n):
            e = edges[(start + k) % n]
            if e is None:
                if cur:
                    runs.append((cur, False))
                cur = []
            else:
                cur.append(e)
        if cur:
            runs.append((cur, False))
        return ze, runs, rakes, tp
    c, u, hl, hw = B.oriented_box(np.asarray(ring, np.float64)[:, :2])
    v = np.array([-u[1], u[0]])
    hl2, hw2 = hl + B.EAVE_M, hw + B.EAVE_M

    def P(s, t):
        return c + u * s + v * t
    if spec.roof == "hipped":
        cw = [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]
        ce = [(-hl2, -hw2), (hl2, -hw2), (hl2, hw2), (-hl2, hw2)]
        edges = [(P(*cw[i]), P(*cw[(i + 1) % 4]), P(*ce[i]), P(*ce[(i + 1) % 4])) for i in range(4)]
        return ze, [(edges, True)], [], tp
    zr = ze + hw2 * tp

    def z(t):
        return ze + (hw2 - abs(t)) * tp
    runs = [([(P(-hl2, -hw), P(hl2, -hw), P(-hl2, -hw2), P(hl2, -hw2))], False),
            ([(P(hl2, hw), P(-hl2, hw), P(hl2, hw2), P(-hl2, hw2))], False)]
    rakes = []
    for sgn in (-1.0, 1.0):
        out = np.array([u[0] * sgn, u[1] * sgn, 0.0])
        soff = [[(*P(sgn * hl, 0.0), zr), (*P(sgn * hl2, 0.0), zr), (*P(sgn * hl2, t * hw), z(hw)),
                 (*P(sgn * hl, t * hw), z(hw))] for t in (-1.0, 1.0)]
        fas = [((*P(sgn * hl2, t * hw2), ze), (*P(sgn * hl2, 0.0), zr), out) for t in (-1.0, 1.0)]
        rakes.append((soff, fas))
    return ze, runs, rakes, tp


def _fascia_mat(spec, mat_idx):
    r, g, b = FASCIA_TINTS[int(B._hash01(spec.seed, "fascia") * len(FASCIA_TINTS))]
    f = mat_idx[TRIM]
    return (f[0], (int(r * 255), int(g * 255), int(b * 255), 255))


def eaves(m, spec, ring, z_eave, mat_idx, er=None):
    """A pitched roof's overhang closed (EAVES): a soffit under it (the roof's plane FASCIA_H lower, from the wall out
    to the eave edge: the roof's planes were open from below, the sky through the overhang) and a fascia board down
    the eave edge, the gables' overhangs (rakes) too; the trim white or a dark tint (hashed)."""
    er = er or eave_runs(spec, ring, z_eave)
    if not EAVES or er is None:
        return
    ze, runs, rakes, tp = er
    fm = _fascia_mat(spec, mat_idx)
    soffit = mat_idx[TRIM]
    fh = FASCIA_H
    for edges, _ in runs:
        for W0, W1, E0, E1 in edges:
            nrm = _out3(E0 - W0)
            _oq(m, [_xyz(W0, z_eave - fh), _xyz(W1, z_eave - fh), _xyz(E1, ze - fh), _xyz(E0, ze - fh)], _PLAIN,
                soffit, _DOWN)
            _oq(m, [_xyz(E0, ze - fh), _xyz(E1, ze - fh), _xyz(E1, ze), _xyz(E0, ze)], _PLAIN, fm, nrm)
    for soff, fas in rakes:
        for q in soff:
            _oq(m, [(x, y, zz - fh) for x, y, zz in q], _PLAIN, soffit, _DOWN)
        for p0, p1, out in fas:
            _oq(m, [(p0[0], p0[1], p0[2] - fh), (p1[0], p1[1], p1[2] - fh), p1, p0], _PLAIN, fm, out)


def _box_faces(m, a, d, n, s0, s1, w0, w1, z0, z1, mat, faces, uv=_PLAIN):
    """Faces of a box on a wall (a: its start, d: along, n: out): s0..s1 along, w0..w1 out, z0..z1; faces: a string of
    f(ront) b(ack) l(eft, -d) r(ight, +d) t(op) u(nderside)."""
    def p(s, w, z):
        q = a + d * s + n * w
        return float(q[0]), float(q[1]), float(z)
    N, D = _out3(n), _out3(d)
    if "f" in faces:
        _oq(m, [p(s0, w1, z0), p(s1, w1, z0), p(s1, w1, z1), p(s0, w1, z1)], uv, mat, N)
    if "b" in faces:
        _oq(m, [p(s0, w0, z0), p(s1, w0, z0), p(s1, w0, z1), p(s0, w0, z1)], uv, mat, -N)
    if "l" in faces:
        _oq(m, [p(s0, w0, z0), p(s0, w1, z0), p(s0, w1, z1), p(s0, w0, z1)], uv, mat, -D)
    if "r" in faces:
        _oq(m, [p(s1, w0, z0), p(s1, w1, z0), p(s1, w1, z1), p(s1, w0, z1)], uv, mat, D)
    if "t" in faces:
        _oq(m, [p(s0, w0, z1), p(s1, w0, z1), p(s1, w1, z1), p(s0, w1, z1)], uv, mat, _UP)
    if "u" in faces:
        _oq(m, [p(s0, w0, z0), p(s1, w0, z0), p(s1, w1, z0), p(s0, w1, z0)], uv, mat, _DOWN)


def front_edge(spec):
    """The wall a house faces the street with: its front door's, else its driveway's, else street_edge()."""
    if spec.door:
        return int(spec.door[0])
    dr = getattr(spec, "drive", None)
    if dr:
        return int(dr[0])
    return street_edge(spec)


def porch_span(spec, ring):
    """(s0, s1) along the front door's wall that buildings._porch covers, or None."""
    if not (spec.porch and spec.door):
        return None
    w_ = _wall(ring, spec.door[0])
    if w_ is None or w_[3] < 4.0:
        return None
    L = w_[3]
    w = min(L * 0.75, 7.5)
    c0 = float(np.clip(spec.door[1], w / 2, L - w / 2))
    return c0 - w / 2 - 0.15, c0 + w / 2 + 0.15


def gutters(m, spec, ring, gmin, z_eave, mat_idx, er=None):
    """A gutter along every horizontal eave (GUTTERS: its front, bottom (both sides) and open ends' caps, mitred round
    a hipped roof's corners) and a downspout at the front wall's two corners (down the wall from under the soffit, out
    to the gutter), none where the porch stands."""
    er = er or eave_runs(spec, ring, z_eave)
    if not GUTTERS or er is None:
        return
    ze, runs, rakes, tp = er
    mat = _fascia_mat(spec, mat_idx)
    zg0, zg1 = ze - GUTTER_H, ze - 0.02
    for edges, closed in runs:
        k = len(edges)
        ns = [np.array([(e[3] - e[2])[1], -(e[3] - e[2])[0]]) / max(float(np.hypot(*(e[3] - e[2]))), 1e-9)
              for e in edges]
        offs = []
        for j in range(k + 1):                          # the offset at each vertex of the run (mitred inside it)
            if (0 < j < k) or (closed and k > 1):
                a_, b_ = ns[(j - 1) % k], ns[j % k]
                offs.append((a_ + b_) / max(1.0 + float(a_ @ b_), 0.2) * GUTTER_D)
            else:
                offs.append(ns[min(j, k - 1)] * GUTTER_D)
        for j, (W0, W1, E0, E1) in enumerate(edges):
            G0, G1 = E0 + offs[j], E1 + offs[j + 1]
            N = _out3(ns[j])
            _oq(m, [_xyz(G0, zg0), _xyz(G1, zg0), _xyz(G1, zg1), _xyz(G0, zg1)], _PLAIN, mat, N)
            bot = [_xyz(E0, zg0), _xyz(E1, zg0), _xyz(G1, zg0), _xyz(G0, zg0)]
            _oq(m, bot, _PLAIN, mat, _DOWN)
            _oq(m, bot, _PLAIN, mat, _UP)
            dd = _out3(E1 - E0)
            if not closed and j == 0:
                _oq(m, [_xyz(E0, zg0), _xyz(G0, zg0), _xyz(G0, zg1), _xyz(E0, zg1)], _PLAIN, mat, -dd)
            if not closed and j == k - 1:
                _oq(m, [_xyz(E1, zg0), _xyz(G1, zg0), _xyz(G1, zg1), _xyz(E1, zg1)], _PLAIN, mat, dd)
    # downspouts at the front wall's corners, on the eave over it
    f = front_edge(spec)
    wl = _wall(ring, f) if f is not None else None
    if wl is None:
        return
    a, d, n, L = wl
    if L < 2.0:
        return
    if not any(abs(float(np.dot(_unit(E1 - E0), d)) - 1.0) < 1e-6 and abs(float(np.dot(W0 - a, n))) < 1e-3
               for edges, _ in runs for W0, W1, E0, E1 in edges):
        return
    ps = porch_span(spec, ring) if spec.door and spec.door[0] == f else None
    hw_ = DOWNSPOUT_W / 2
    # one offset from the wall up into the gutter (a straight sloped tube; three boxes read as a zigzag from below,
    # 2026-10-28's shots): its top under the fascia's bottom edge and the soffit, reaching the gutter's bottom
    w1 = B.EAVE_M + GUTTER_D / 2 + hw_
    k = (zg0 - (ze - FASCIA_H - 0.005)) / max(w1 - B.EAVE_M - 0.005, 0.02)
    za = zg0 - k * w1                                   # the tube's top at the wall
    T = DOWNSPOUT_W                                     # (its height)
    N = _out3(n)

    def p(s, w, z):
        q = a + d * s + n * w
        return float(q[0]), float(q[1]), float(z)
    for s in (DOWNSPOUT_IN, L - DOWNSPOUT_IN):
        if ps is not None and ps[0] - hw_ < s < ps[1] + hw_:
            continue
        s0, s1 = s - hw_, s + hw_
        _box_faces(m, a, d, n, s0, s1, 0.0, DOWNSPOUT_W, gmin - 0.1, za - T / 2, mat, "flr")    # down the wall
        for sx, want in ((s0, -_out3(d)), (s1, _out3(d))):
            _oq(m, [p(sx, 0.0, za - T), p(sx, w1, zg0 - T), p(sx, w1, zg0), p(sx, 0.0, za)], _PLAIN, mat, want)
        _oq(m, [p(s0, 0.0, za), p(s1, 0.0, za), p(s1, w1, zg0), p(s0, w1, zg0)], _PLAIN, mat, _UP)
        _oq(m, [p(s0, 0.0, za - T), p(s1, 0.0, za - T), p(s1, w1, zg0 - T), p(s0, w1, zg0 - T)], _PLAIN, mat, _DOWN)
        _oq(m, [p(s0, w1, zg0 - T), p(s1, w1, zg0 - T), p(s1, w1, zg0), p(s0, w1, zg0)], _PLAIN, mat, N)


def _unit(x):
    return x / max(float(np.hypot(*x)), 1e-9)


def veneer_share(spec):
    return VENEER_SHARE.get(getattr(spec, "region", None) or "", 0.0)


def has_veneer(spec):
    """A pitched-roof house with a stone veneer front (VENEER_SHARE of its region's; never with a wainscot)."""
    return bool(VENEER and spec.wall and spec.windows is not False and spec.roof in ("gabled", "hipped")
            and not has_wainscot(spec) and B._hash01(spec.seed, "veneer") < veneer_share(spec))


def veneer(m, spec, ring, gmin, z_eave, mat_idx):
    """Ledgestone across a house's front wall (has_veneer; a hashed warm tan or grey stone): a band from the ground
    to VENEER_H (hashed, but under the lowest front window's sill trim), round the door's and garage door's frames,
    and full height on the garage door's piers (between its frame and the wall's end, the door's frame or a window,
    VENEER_PIER_M wide at most), standing out VENEER_OUT. (z0, z1, s spans) of its pieces for the tests."""
    if not has_veneer(spec):
        return []
    f = front_edge(spec)
    wl = _wall(ring, f) if f is not None else None
    if wl is None or wl[3] < 3.0:
        return []
    a, d, n, L = wl
    base = z_eave - spec.floors * spec.floor_h
    tints = VENEER_TINTS
    mat = mat_idx[f"{B.STONE}|{tints[int(B._hash01(spec.seed, 'veneer tint') * len(tints))]}"]
    c = VENEER_CLEAR_M
    top = base + VENEER_H[0] + (VENEER_H[1] - VENEER_H[0]) * B._hash01(spec.seed, "veneer h")
    shut = has_shutters(spec)
    vary = HOUSE_VARIETY and RECESS
    wins = []                                           # the front's ground-floor windows: their trims' / shutters' span
    for wn in house_windows(spec, ring, f, z_eave):
        if wn.zf < base + spec.floor_h:
            sl = sr = wn.uw / 2 if shut else 0.0        # (as before: the shutters' room either side)
            if vary:
                sl, sr = (wn.uw / 2 if wn.shut[0] else 0.0), (wn.uw / 2 if wn.shut[1] else 0.0)
            wins.append((wn.s - wn.w / 2 - TRIM_W - sl - c, wn.s + wn.w / 2 + TRIM_W + sr + c))
            top = min(top, wn.zf - SILL_H - 0.02)
    holes = []                                          # (s0, s1) the band leaves out
    if spec.door and spec.door[0] == f:
        h = DOOR_W / 2 + FRAME_W * 1.5 + c
        holes.append((spec.door[1] - h, spec.door[1] + h))
    piers = []
    dr = getattr(spec, "drive", None)
    if dr and dr[0] == f:
        gs = garage_span(spec, f, L)
        if gs is not None:
            g0, g1 = gs[0] - FRAME_W - c, gs[1] + FRAME_W + c
            holes.append((g0, g1))
            stops = sorted([0.0, L] + [x for h_ in holes[:-1] + wins for x in h_])
            left = max([x for x in stops if x <= g0 + 1e-9], default=0.0)
            right = min([x for x in stops if x >= g1 - 1e-9], default=L)
            for p0, p1 in ((max(left, g0 - VENEER_PIER_M), g0), (g1, min(right, g1 + VENEER_PIER_M))):
                if p1 - p0 >= 0.2 and not any(w0 < p1 and w1 > p0 for w0, w1 in wins + holes[:-1]):
                    piers.append((p0, p1))
    pier_top = min(base + spec.floor_h, z_eave - 0.3)
    if top - base < 0.3:
        top = None
    uu, uv_ = VENEER_UV_M
    z0 = gmin - 0.3
    pieces = []
    spans = [(0.0, L)]
    for h0, h1 in sorted(holes + piers):
        nxt = []
        for s0, s1 in spans:
            if h1 <= s0 or h0 >= s1:
                nxt.append((s0, s1))
                continue
            if h0 > s0:
                nxt.append((s0, h0))
            if h1 < s1:
                nxt.append((h1, s1))
        spans = nxt
    for (s0, s1), z1 in ([(sp, top) for sp in spans if sp[1] - sp[0] >= 0.15] if top is not None else []) \
            + [(p, pier_top) for p in piers]:
        uv = ((s0 / uu, z0 / uv_), (s1 / uu, z0 / uv_), (s1 / uu, z1 / uv_), (s0 / uu, z1 / uv_))
        _box_faces(m, a, d, n, s0, s1, 0.0, VENEER_OUT, z0, z1, mat, "flrt", uv)
        pieces.append((z0, z1, s0, s1))
    return pieces


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
    if EAVES or GUTTERS:
        er = eave_runs(spec, ring, z_eave)
        eaves(m, spec, ring, z_eave, mat_idx, er)
        gutters(m, spec, ring, gmin, z_eave, mat_idx, er)
    veneer(m, spec, ring, gmin, z_eave, mat_idx)
