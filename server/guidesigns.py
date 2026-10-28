"""Highway signs (the user, 2026-10-28: "a beautiful fun drive from Frederick to downtown Denver, into Castle Rock"):
the green guide signs of a motorway's exits with their real numbers and destinations, the overhead gantries and
cantilevers that carry them, route shields, mile markers, freeway speed limits (any value: 75 mph too, the stock
Utah set stops at 70), the gore's EXIT sign and painted chevrons, delineator posts with their reflectors, and
billboards.

Where the signs come from: OpenStreetMap's motorway_junction nodes (ref: the exit number, exit_to) and the
destination tags of the ramps and carriageways (destination, destination:ref, destination:street), read for a named
region by tools/build_highway_signs.py (Overpass API) into server/hwysigns/<region>.json.gz, committed with the code:
nothing is fetched while driving. plan() turns a region's ways and junctions into sign items in latitude / longitude
(an exit's advance signs 1 and 1/2 mile ahead of it, its exit direction gantry, its gore; a route shield and the
speed limit after each entrance; a mile marker every mile, the exit numbers being the mileposts). tile() places the
items inside a road tile on the tile's own carriageways (their widths, lanes and median) and builds them:

  "mesh": the drawn signs, render-only (panels, lettering, shields, steel, delineators, gore chevrons)
  "col":  the posts' and gantry legs' collision boxes (cdae collision_only)
  "props": billboard rows for trees.lua (West Coast USA's drag-strip billboards and their ads)

Lettering: one glyph atlas (tools/build_sign_font.py: Overpass SemiBold, the free Highway Gothic look-alike; its
metrics in server/signfont.json), a quad per letter. Colours are vertex colours of one panel material; white parts,
the white legends and the reflectors have their own materials, which glow after dusk (nightlights.lua: the
retroreflective look in headlights). Pure but for reading the region files and the font metrics.
"""
import gzip
import json
import math
import os
import re
import threading
import unicodedata

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REGION_DIR = os.path.join(HERE, "hwysigns")
FONT_FILE = os.path.join(HERE, "signfont.json")
PLAN_V = 1

# materials (guidesigns.materials.json); a mesh's triangles carry an index into this list
MATS = ("bne_gsign_panel", "bne_gsign_white", "bne_gsign_text", "bne_gsign_textk", "bne_gsign_steel",
        "bne_gsign_reflect", "bne_gsign_amber", "bne_gsign_paint")
M_PANEL, M_WHITE, M_TEXT, M_TEXTK, M_STEEL, M_REFLECT, M_AMBER, M_PAINT = range(len(MATS))

GREEN = (0, 98, 65)
BLUE = (0, 56, 130)
RED = (178, 28, 40)
BLACK = (18, 18, 18)
WHITE = (255, 255, 255)
YELLOW = (248, 192, 0)
POST = (196, 198, 200)          # a delineator's post
STEEL = (150, 152, 152)

LAYER_M = 0.012                 # m between a face's layers (panel, border, shield, lettering): toward the viewer
PANEL_DEPTH = 0.05              # m from a panel's face to its back

# MUTCD 2009 freeway overhead sizes (inches as metres: the capitals' heights)
CAP_DEST = 0.50                 # destinations (20" upper case, 15" lower case)
CAP_CARD = 0.45                 # cardinal directions (18")
CAP_TAB = 0.45                  # the exit number tab
CAP_DIST = 0.40                 # "1 MILE", "1/2 MILE" (16")
SHIELD_H = 1.07                 # a route shield (42")
MARGIN = 0.38                   # a guide sign's inner margin
BORDER = 0.05                   # its white border
CORNER = 0.30                   # its corner radius

HIGHWAY_MAIN = {"motorway", "trunk"}
CLEARANCE_M = 5.5               # m from the road to an overhead sign's bottom (17.5 ft + a little)
POST_SIGN_BOTTOM_M = 2.2        # m from the ground to a ground-mounted sign's bottom (7 ft)
POST_OFF_M = 3.0                # m beyond the carriageway's edge: posts and gantry legs (the barriers' rail face at +0.6)
MEDIAN_LEG_GAP_M = (2.0, 12.0)  # the carriageways' edges this far apart: a gantry's left leg on the median's centre line
                                # (on its barrier: the barriers thread's F-shape up to 12 m); wider, beyond the left
                                # edge (POST_OFF_M); narrower or unknown, a cantilever from the right
GORE_SIGN_GAP_M = 6.0           # the gore EXIT sign where the two edges are this far apart
GORE_PAINT_M = (0.0, 4.5)       # the painted gore: the edges' gap between these (m)
CHEVRON_EVERY_M = 7.0
CHEVRON_W = 0.45
DELINEATOR_EVERY_M = 50.0
DELINEATOR_OFF_M = 0.6
BILLBOARD_EVERY_M = 1400.0      # (hashed: a billboard roughly this often each side, outside towns)
BILLBOARD_OFF_M = (22.0, 40.0)
BILLBOARD_CLEAR_M = 350.0       # m kept from a sign gantry
BILLBOARD_ADS = 23              # West Coast USA's dragbillboard_sign01 .. 23 (trees.lua bbad01 ..)
STREET_CLASSES = {"trunk", "primary", "secondary", "tertiary", "residential", "unclassified", "living_street"}
STREET_MAJOR = {"trunk", "primary", "secondary", "tertiary"}
BLADE_CAP = (0.15, 0.10)        # a street name blade's capitals: major streets (6"), the rest (4")
BLADE_TOP_M = 3.15              # m from the ground to the post's top (the lower blade's bottom: MUTCD 7 ft + the stop
                                # or yield sign under it on the same corner elsewhere)
STREET_CLEAR_M = 1.3            # m from the corner's two carriageway edges to the post (the sidewalk's corner)
STREET_WIDE_M = 7.0             # a junction whose widest street is wider than this (both halves) gets a second corner
VERTS_MAX = 60000               # a tile's sign mesh at most (the engine's 16-bit indices)

MILE_M = 1609.344
ROUTE_MILE_CLEAR_M = 1000.0     # a route's mile marker (routesigns) dropped this close to an exit-numbered one


# ---------------------------------------------------------------------------------------------------------- lettering

_font = None
_font_lock = threading.Lock()


def font():
    """The glyph metrics (tools/build_sign_font.py)."""
    global _font
    with _font_lock:
        if _font is None:
            with open(FONT_FILE) as f:
                _font = json.load(f)
        return _font


def text_width(s, cap):
    """m of s's advance at capital height cap (kerned)."""
    f = font()
    g, kern = f["glyphs"], f["kern"]
    w = 0.0
    for i, ch in enumerate(s):
        w += g[ch]["adv"] if ch in g else f["space"]
        if i + 1 < len(s):
            w += kern.get(s[i:i + 2], 0.0)
    return w * cap


def text_quads(s, cap, x, y):
    """The glyph quads of s from pen (x, y) on its baseline: [(x0, y0, x1, y1, u0, v0, u1, v1)] (uv in Collada's
    convention, v up: the atlas' rows from its bottom)."""
    f = font()
    g, kern = f["glyphs"], f["kern"]
    out = []
    pen = x
    for i, ch in enumerate(s):
        gl = g.get(ch)
        if gl is not None:
            b, uv = gl["box"], gl["uv"]
            out.append((pen + b[0] * cap, y + b[1] * cap, pen + b[2] * cap, y + b[3] * cap,
                        uv[0], 1.0 - uv[3], uv[2], 1.0 - uv[1]))
            pen += gl["adv"] * cap
        else:
            pen += f["space"] * cap
        if i + 1 < len(s):
            pen += kern.get(s[i:i + 2], 0.0) * cap
    return out


# --------------------------------------------------------------------------------------------------------- 2D shapes

def rounded_rect(x0, y0, x1, y1, r, seg=5):
    """A rounded rectangle's outline, counter-clockwise."""
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    if r <= 1e-6:
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for k in range(seg + 1):
            a = math.radians(a0 + 90.0 * k / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _area(poly):
    a = 0.0
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        a += x0 * y1 - x1 * y0
    return a / 2


def triangulate(poly):
    """Ear clipping of a simple polygon: index triples counter-clockwise."""
    n = len(poly)
    if n < 3:
        return []
    idx = list(range(n))
    if _area(poly) < 0:
        idx.reverse()
    out = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= -1e-12 and cross(b, c, p) >= -1e-12 and cross(c, a, p) >= -1e-12

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        m = len(idx)
        for k in range(m):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if cross(a, b, c) <= 1e-12:
                continue
            if any(inside(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2))
            del idx[k]
            break
        else:                       # (degenerate: fan the rest)
            break
    for k in range(1, len(idx) - 1):
        out.append((idx[0], idx[k], idx[k + 1]))
    return out


def _scaled(poly, sx, sy, dx, dy):
    return [(dx + x * sx, dy + y * sy) for x, y in poly]


# the Interstate shield's outline (M1-1) in a unit box (0..1), counter-clockwise from the bottom point: the sides
# bulge out below the shoulders and curve in to the point; the top dips in the middle
_IS_SIDE = [(0.5, 0.0), (0.66, 0.05), (0.80, 0.14), (0.91, 0.28), (0.98, 0.45), (1.0, 0.62), (0.99, 0.76),
            (0.96, 0.88), (0.94, 1.0)]
INTERSTATE = _IS_SIDE + [(0.80, 0.975), (0.65, 0.96), (0.5, 0.95), (0.35, 0.96), (0.20, 0.975)] + \
    [(1.0 - x, y) for x, y in reversed(_IS_SIDE[1:])]
# the US route shield (M1-4): the white shield on its black square
_US_SIDE = [(0.5, 0.06), (0.70, 0.12), (0.84, 0.24), (0.90, 0.42), (0.91, 0.60), (0.94, 0.74), (0.90, 0.80),
            (0.86, 0.86), (0.88, 0.94)]
US_SHIELD = _US_SIDE + [(0.74, 0.95), (0.62, 0.91), (0.5, 0.94), (0.38, 0.91), (0.26, 0.95)] + \
    [(1.0 - x, y) for x, y in reversed(_US_SIDE[1:])]
# Colorado's state highway marker: a green rectangle, white mountains and COLORADO over the number
CO_PEAKS = [(0.06, 0.70), (0.22, 0.84), (0.34, 0.76), (0.50, 0.92), (0.64, 0.79), (0.76, 0.86), (0.94, 0.70)]

ARROW_UP = [(-0.10, 0.0), (0.10, 0.0), (0.10, 0.62), (0.30, 0.62), (0.0, 1.0), (-0.30, 0.62), (-0.10, 0.62)]


def arrow(angle_deg, length):
    """An arrow's outline pointing at angle_deg (0 up, 90 right... clockwise), its tail at the origin."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(length * (x * ca + y * sa), length * (-x * sa + y * ca)) for x, y in ARROW_UP]


# ------------------------------------------------------------------------------------------------------------- faces

class Face:
    """A sign's face in its own plane (m: x right, y up, the origin anywhere): filled polygons and lettering in layers
    drawn toward the viewer (0: the panel, then its border, a shield, the lettering on top)."""

    def __init__(self):
        self.polys = []         # (layer, outline, colour or None for white, material)
        self.texts = []         # (layer, quads, ink "w" / "k")

    def poly(self, outline, color, layer):
        mat = M_WHITE if color == WHITE else M_PANEL
        self.polys.append((layer, list(outline), color, mat))

    def rect(self, x0, y0, x1, y1, color, layer):
        self.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], color, layer)

    def text(self, s, cap, x, y, ink="w", layer=3):
        """s with its pen at x on the baseline y; returns its width."""
        self.texts.append((layer, text_quads(s, cap, x, y), ink))
        return text_width(s, cap)

    def text_c(self, s, cap, cx, y, ink="w", layer=3):
        """s centred on cx."""
        w = text_width(s, cap)
        self.text(s, cap, cx - w / 2, y, ink, layer)
        return w

    def add(self, other, dx, dy, dlayer=0):
        for layer, outline, color, mat in other.polys:
            self.polys.append((layer + dlayer, [(x + dx, y + dy) for x, y in outline], color, mat))
        for layer, quads, ink in other.texts:
            self.texts.append((layer + dlayer, [(a + dx, b + dy, c + dx, d + dy, *uv) for a, b, c, d, *uv in quads],
                               ink))

    def bounds(self):
        xs = [x for _, o, _, _ in self.polys for x, _ in o] + [q[i] for _, qs, _ in self.texts for q in qs
                                                                for i in (0, 2)]
        ys = [y for _, o, _, _ in self.polys for _, y in o] + [q[i] for _, qs, _ in self.texts for q in qs
                                                                for i in (1, 3)]
        return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)


def route_kind(network, ref):
    """"I", "US", "CO" (a Colorado state highway), "toll" or "" (a plain panel) of a route."""
    n = (network or "").upper()
    if n in ("I", "IH", "INTERSTATE") or n.startswith("US:I"):
        return "I"
    if n in ("US", "US:US") or n.startswith("US:US"):
        return "US"
    if n in ("CO", "SH", "US:CO", "STATE") or n == "C":
        return "CO"
    if n in ("E", "E-") or "TOLL" in n:
        return "toll"
    return ""


def parse_ref(text):
    """A destination:ref or ref token ("I 25 South", "US-36 West", "CO 470", "E-470") -> (network, number, cardinal
    or "")."""
    t = (text or "").strip()
    card = ""
    m = re.search(r"\b(north|south|east|west)(bound)?\s*$", t, re.I)
    if m:
        card = m.group(1).upper()
        t = t[:m.start()].strip()
    toll = bool(re.search(r"\btoll\b", t, re.I))            # ("E470 Toll", "Northwest Parkway Toll")
    t = re.sub(r"\s*\b(toll|expr|express)\b\s*", " ", t, flags=re.I).strip()
    m = re.match(r"^([A-Za-z]+)[\s-]*([0-9]+[A-Za-z]?)$", t)
    if m:
        net = m.group(1).upper()
        if net in ("C", "SH", "CO", "COLO"):
            net = "CO"
        return net, m.group(2), card
    if toll and t:                  # a toll road without a number: its name on the toll shield
        return "TOLL", street_name(t), card
    return "", t, card


def shield(network, number, h=SHIELD_H):
    """A route shield face of height h, its bottom left at the origin; returns (face, width)."""
    f = Face()
    kind = route_kind(network, number)
    num = str(number)
    if kind == "I":
        w = h * (1.0 if len(num) <= 2 else 1.25)
        f.poly(_scaled(INTERSTATE, w, h, 0, 0), WHITE, 1)
        inner = _scaled(INTERSTATE, w * 0.9, h * 0.9, w * 0.05, h * 0.05)
        f.poly(inner, BLUE, 2)
        f.poly([(w * 0.12, h * 0.74), (w * 0.88, h * 0.74), (w * 0.895, h * 0.885), (w * 0.80, h * 0.875),
                (w * 0.65, h * 0.865), (w * 0.5, h * 0.855), (w * 0.35, h * 0.865), (w * 0.20, h * 0.875),
                (w * 0.105, h * 0.885)], RED, 3)
        cap = h * (0.40 if len(num) <= 2 else 0.36)
        f.text_c(num, cap, w / 2, h * 0.24, "w", 4)
        f.text_c("INTERSTATE", h * 0.06, w / 2, h * 0.78, "w", 4)
    elif kind == "US":
        w = h * (1.0 if len(num) <= 2 else 1.25)
        f.rect(0, 0, w, h, BLACK, 1)
        f.poly(_scaled(US_SHIELD, w, h, 0, 0), WHITE, 2)
        f.text_c(num, h * (0.42 if len(num) <= 2 else 0.37), w / 2, h * 0.30, "k", 3)
    elif kind == "CO":
        w = h * (1.0 if len(num) <= 2 else 1.25)
        f.poly(rounded_rect(0, 0, w, h, h * 0.06), WHITE, 1)
        f.poly(rounded_rect(h * 0.04, h * 0.04, w - h * 0.04, h - h * 0.04, h * 0.04), GREEN, 2)
        peaks = [(0.04 + x * 0.92, y) for x, y in CO_PEAKS]
        f.poly(_scaled([(0.04, 0.62)] + peaks + [(0.96, 0.62)], w, h, 0, 0), WHITE, 3)
        f.text_c("COLORADO", h * 0.075, w / 2, h * 0.655, "k", 4)
        f.text_c(num, h * (0.40 if len(num) <= 2 else 0.34), w / 2, h * 0.14, "w", 4)
    elif kind == "toll":
        label = num if not network or "TOLL" in network.upper() else f"{network}-{num}"
        w = max(h * 1.2, text_width(label, h * 0.32) + h * 0.3)
        f.poly(rounded_rect(0, 0, w, h, h * 0.08), WHITE, 1)
        f.poly(rounded_rect(h * 0.04, h * 0.04, w - h * 0.04, h - h * 0.04, h * 0.06), (0, 92, 150), 2)
        f.rect(h * 0.04, h * 0.62, w - h * 0.04, h * 0.80, (236, 120, 30), 3)
        f.text_c("TOLL", h * 0.12, w / 2, h * 0.65, "w", 4)
        f.text_c(label, h * 0.32, w / 2, h * 0.18, "w", 4)
    else:
        label = f"{network} {num}".strip()
        w = max(h * 0.9, text_width(label, h * 0.36) + h * 0.3)
        f.poly(rounded_rect(0, 0, w, h * 0.8, h * 0.06), WHITE, 1)
        f.text_c(label, h * 0.36, w / 2, h * 0.22, "k", 2)
        h = h * 0.8
    return f, w


def _fit(lines, cap, width_max):
    """The capital height lines fit width_max at (cap at most, 0.7 cap at least: longer lines are cut)."""
    w = max((text_width(s, cap) for s in lines), default=0.0)
    if w <= width_max or w <= 0:
        return cap
    return max(cap * 0.7, cap * width_max / w)


def guide_face(shields, dests, exit_ref=None, bottom=None, arrow_kind=None, exit_left=False, width_max=7.5):
    """A green guide sign: an exit tab ("EXIT 228") on top, a row of route shields with their cardinal directions,
    up to two destination lines, then a bottom line ("1 MILE") or an arrow ("down": over its lane, "up_right" /
    "up_left": an exit's way off). shields: [(network, number, cardinal)]. Returns the face with the main panel's
    bottom middle at the origin, and its (width, height)."""
    f = Face()
    body = Face()
    y = MARGIN
    rows = []                       # (height, kind, payload) from the bottom up
    if bottom:
        rows.append((CAP_DIST, "text", (bottom, CAP_DIST)))
    if arrow_kind == "down":
        rows.append((0.75, "arrow", 180.0))
    dests = [d for d in dests if d][:2]
    cap = _fit(dests, CAP_DEST, width_max - 2 * MARGIN - (1.0 if arrow_kind in ("up_right", "up_left") else 0))
    for d in reversed(dests):
        rows.append((cap, "dest", (d, cap)))
    if shields:
        rows.append((SHIELD_H, "shields", shields[:3]))
    widths = []
    for h, kind, pay in rows:
        if kind == "text" or kind == "dest":
            widths.append(text_width(pay[0], pay[1]))
        elif kind == "shields":
            widths.append(_shield_row(Face(), pay, 0, 0))
        else:
            widths.append(0.8)
    inner_w = max(widths + [1.6])
    side_arrow = arrow_kind in ("up_right", "up_left")
    if side_arrow:
        inner_w += 1.0
    x_text = MARGIN + (1.0 if arrow_kind == "up_left" else 0.0)
    for (h, kind, pay), w in zip(rows, widths):
        cx = x_text + (inner_w - (1.0 if side_arrow else 0.0)) / 2
        if kind in ("text", "dest"):
            body.text_c(pay[0], pay[1], cx, y)
            y += h + 0.30
        elif kind == "shields":
            _shield_row(body, pay, cx - w / 2, y)
            y += h + 0.30
        else:
            body.poly([(cx + x, y + 0.75 + yy) for x, yy in arrow(pay, 0.75)], WHITE, 3)
            y += h + 0.25
    H = y - 0.30 + MARGIN
    W = inner_w + 2 * MARGIN
    if side_arrow:
        ax = (W - MARGIN - 0.45) if arrow_kind == "up_right" else (MARGIN + 0.45)
        body.poly([(ax + x, H / 2 - 0.35 + yy) for x, yy in arrow(45.0 if arrow_kind == "up_right" else -45.0,
                                                                    0.8)], WHITE, 3)
    f.poly(rounded_rect(0, 0, W, H, CORNER), WHITE, 0)
    f.poly(rounded_rect(BORDER, BORDER, W - BORDER, H - BORDER, CORNER - BORDER), GREEN, 1)
    f.add(body, 0, 0)
    if exit_ref:
        label = f"EXIT {exit_ref}"
        tw = text_width(label, CAP_TAB) + 2 * 0.22
        th = CAP_TAB + 2 * 0.2
        tx0 = 0.0 if exit_left else W - tw
        f.poly(rounded_rect(tx0, H - 0.1, tx0 + tw, H + th, 0.15), WHITE, 0)
        f.poly(rounded_rect(tx0 + BORDER, H - 0.1, tx0 + tw - BORDER, H + th - BORDER, 0.12), GREEN, 1)
        f.text(label, CAP_TAB, tx0 + 0.22, H + 0.17)
        H_all = H + th
    else:
        H_all = H
    out = Face()
    out.add(f, -W / 2, 0)
    return out, (W, H_all)


def _shield_row(face, shields, x, y):
    """Shields left to right from (x, y), each with its cardinal direction beside it; returns the row's width."""
    pen = x
    for k, (net, num, card) in enumerate(shields):
        if k:
            pen += 0.35
        sf, sw = shield(net, num)
        face.add(sf, pen, y)
        pen += sw
        if card:
            pen += 0.2
            pen += face.text(card, CAP_CARD, pen, y + SHIELD_H / 2 - CAP_CARD / 2)
    return pen - x


def gore_face(exit_ref):
    """The gore's EXIT sign (E5-1): EXIT over its number and an arrow up and away; its bottom middle at the origin."""
    f = Face()
    lines = ["EXIT"] + ([str(exit_ref)] if exit_ref else [])
    cap = 0.30
    W = max(1.5, max(text_width(s, cap) for s in lines) + 0.9)
    H = 0.25 + len(lines) * (cap + 0.18) + 0.65
    f.poly(rounded_rect(0, 0, W, H, 0.15), WHITE, 0)
    f.poly(rounded_rect(0.04, 0.04, W - 0.04, H - 0.04, 0.12), GREEN, 1)
    y = H - 0.22 - cap
    for s in lines:
        f.text_c(s, cap, W / 2, y)
        y -= cap + 0.18
    f.poly([(W / 2 - 0.25 + x, 0.12 + yy) for x, yy in arrow(45.0, 0.55)], WHITE, 3)
    out = Face()
    out.add(f, -W / 2, 0)
    return out, (W, H)


def mile_face(mile):
    """A mile marker (D10-1/-3): MILE over the number, its digits stacked from three; bottom middle at the origin."""
    f = Face()
    s = str(int(mile))
    digits = [s] if len(s) <= 2 else list(s)
    cap = 0.25
    W = 0.45 if len(s) <= 2 else 0.38
    H = 0.20 + 0.14 + len(digits) * (cap + 0.08) + 0.12
    f.poly(rounded_rect(0, 0, W, H, 0.05), WHITE, 0)
    f.poly(rounded_rect(0.02, 0.02, W - 0.02, H - 0.02, 0.04), GREEN, 1)
    y = H - 0.08 - 0.10
    f.text_c("MILE", 0.10, W / 2, y)
    y -= 0.10 + cap
    for d in digits:
        f.text_c(d, cap if len(d) == 1 else cap * 0.9, W / 2, y)
        y -= cap + 0.08
    out = Face()
    out.add(f, -W / 2, 0)
    return out, (W, H)


def speed_face(mph):
    """SPEED LIMIT nn (R2-1, the freeway size 48" x 60"); bottom middle at the origin."""
    f = Face()
    W, H = 1.22, 1.52
    f.poly(rounded_rect(0, 0, W, H, 0.08), WHITE, 0)
    for a, b, c, d in ((0.03, 0.03, W - 0.03, 0.055), (0.03, H - 0.055, W - 0.03, H - 0.03),
                       (0.03, 0.03, 0.055, H - 0.03), (W - 0.055, 0.03, W - 0.03, H - 0.03)):
        f.rect(a, b, c, d, BLACK, 1)
    f.text_c("SPEED", 0.25, W / 2, H - 0.16 - 0.25, "k", 2)
    f.text_c("LIMIT", 0.25, W / 2, H - 0.16 - 0.25 - 0.12 - 0.25, "k", 2)
    f.text_c(str(int(mph)), 0.48, W / 2, 0.16, "k", 2)
    out = Face()
    out.add(f, -W / 2, 0)
    return out, (W, H)


def cardinal_face(card):
    """The cardinal plate over a reassurance shield (black on white); bottom middle at the origin."""
    f = Face()
    cap = 0.22
    W = max(0.91, text_width(card, cap) + 0.2)
    H = 0.46
    f.poly(rounded_rect(0, 0, W, H, 0.05), WHITE, 0)
    f.text_c(card, cap, W / 2, (H - cap) / 2, "k", 2)
    out = Face()
    out.add(f, -W / 2, 0)
    return out, (W, H)


def reassurance_face(shields):
    """Route shields, each with its cardinal plate over it, side by side; bottom middle at the origin."""
    out = Face()
    parts = []
    for net, num, card in shields[:3]:
        sf, sw = shield(net, num)
        g = Face()
        g.add(sf, -sw / 2, 0)
        h, w = SHIELD_H, sw
        if card:
            cf, (cw, ch) = cardinal_face(card)
            g.add(cf, 0, h + 0.08)
            h, w = h + ch + 0.08, max(w, cw)
        parts.append((g, w, h))
    W = sum(p[1] for p in parts) + 0.25 * max(0, len(parts) - 1)
    x = -W / 2
    H = 0.0
    for g, w, h in parts:
        out.add(g, x + w / 2, 0)
        x += w + 0.25
        H = max(H, h)
    return out, (W, H)


# ------------------------------------------------------------------------------------------------------------- meshes

class Mesh:
    """Triangles with a material each, uvs and vertex colours, in a tile's frame (origin subtracted at the end)."""

    def __init__(self):
        self.P, self.UV, self.C, self.T, self.M = [], [], [], [], []

    def __len__(self):
        return len(self.T)

    def tri_poly(self, pts3, tris, mat, color=WHITE, uvs=None):
        base = len(self.P)
        self.P.extend(pts3)
        self.UV.extend(uvs if uvs is not None else [(0.0, 0.0)] * len(pts3))
        self.C.extend([(*color, 255)] * len(pts3))
        for a, b, c in tris:
            self.T.append((base + a, base + b, base + c))
            self.M.append(mat)

    def quad(self, a, b, c, d, mat, color=WHITE, uvs=None):
        """a b c d counter-clockwise seen from the side drawn."""
        self.tri_poly([a, b, c, d], [(0, 1, 2), (0, 2, 3)], mat, color, uvs)

    def box(self, c0, c1, half_a, half_b, mat, color=STEEL, ends=False):
        """A square-ish tube from c0 to c1 (3D), half_a / half_b its half sizes across: its four sides (and ends)."""
        c0, c1 = np.asarray(c0, float), np.asarray(c1, float)
        d = c1 - c0
        L = float(np.linalg.norm(d))
        if L < 1e-6:
            return
        d /= L
        up = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        a = np.cross(d, up)
        a /= np.linalg.norm(a)
        b = np.cross(a, d)
        corners = [a * half_a + b * half_b, -a * half_a + b * half_b, -a * half_a - b * half_b, a * half_a - b * half_b]
        for k in range(4):
            p, q = corners[k], corners[(k + 1) % 4]
            self.quad(tuple(c0 + q), tuple(c0 + p), tuple(c1 + p), tuple(c1 + q), mat, color)
        if ends:
            self.quad(*[tuple(c1 + p) for p in corners], mat, color)
            self.quad(*[tuple(c0 + p) for p in reversed(corners)], mat, color)

    def result(self, origin):
        """(positions (n, 3), tris (m, 3), uvs (n, 2), colours (n, 4) uint8, tri materials (m,)) with origin
        subtracted, or None when empty."""
        if not self.T:
            return None
        P = np.asarray(self.P, np.float64) - np.asarray(origin, np.float64)
        return (P.astype(np.float32), np.asarray(self.T, np.int64), np.asarray(self.UV, np.float32),
                np.asarray(self.C, np.uint8), np.asarray(self.M, np.int64))


class Boxes:
    """Collision boxes (posts, legs): a collision-only mesh, and each one's bounds (roads.lua's colliders: not made
    solid while the car is inside one)."""

    def __init__(self):
        self.P, self.T, self.aabbs = [], [], []

    def __len__(self):
        return len(self.T)

    def add(self, c0, c1, half):
        m = Mesh()
        m.box(c0, c1, half, half, M_STEEL, ends=True)
        pts = np.asarray(m.P, float)
        if len(pts):
            lo, hi = pts.min(axis=0), pts.max(axis=0)
            self.aabbs.append([round(float(v), 1) for v in (*(lo - 0.5), *(hi + 0.5))])
        base = len(self.P)
        self.P.extend(m.P)
        self.T.extend((a + base, b + base, c + base) for a, b, c in m.T)

    def result(self, origin):
        """(positions (n, 3) less origin, tris (m, 3), boxes [[x0, y0, z0, x1, y1, z1]] in the frame), or None."""
        if not self.T:
            return None
        return (np.asarray(self.P, np.float64) - np.asarray(origin, np.float64)).astype(np.float32), \
            np.asarray(self.T, np.int64), self.aabbs


def draw_face(mesh, face, o, right, normal, back=True):
    """face into mesh: its origin at o (3D), its x along right (unit, horizontal), y up, its layers toward normal
    (unit: toward the viewer); a steel back PANEL_DEPTH behind its panel layer."""
    o, right, normal = np.asarray(o, float), np.asarray(right, float), np.asarray(normal, float)
    up = np.array([0.0, 0.0, 1.0])

    def P(x, y, layer):
        p = o + right * x + up * y + normal * (layer * LAYER_M)
        return (float(p[0]), float(p[1]), float(p[2]))
    for layer, outline, color, mat in face.polys:
        tris = triangulate(outline)
        mesh.tri_poly([P(x, y, layer) for x, y in outline], tris, mat, color)
    for layer, quads, ink in face.texts:
        mat = M_TEXT if ink == "w" else M_TEXTK
        for x0, y0, x1, y1, u0, v0, u1, v1 in quads:
            mesh.quad(P(x0, y0, layer), P(x1, y0, layer), P(x1, y1, layer), P(x0, y1, layer), mat, WHITE,
                      [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
    if back:
        for layer, outline, color, mat in face.polys:
            if layer != 0:
                continue
            pts = [P(x, y, 0) for x, y in outline]
            pts = [tuple(np.asarray(p) - normal * PANEL_DEPTH) for p in pts]
            tris = [(a, c, b) for a, b, c in triangulate(outline)]
            mesh.tri_poly(pts, tris, M_STEEL, STEEL)


def ground_mount(mesh, col, face, size, base, right, normal, ground_z, bottom=POST_SIGN_BOTTOM_M):
    """A face on posts: base (3D: the sign's middle on the ground line), the face's bottom bottom m over the ground;
    two posts under a wide face, one under a narrow one."""
    W, H = size
    base = np.asarray(base, float)
    z0 = ground_z + bottom
    draw_face(mesh, face, (base[0], base[1], z0), right, normal)
    xs = [0.0] if W < 1.3 else [-W * 0.3, W * 0.3]
    for x in xs:
        p = base + np.asarray(right) * x - np.asarray(normal) * (PANEL_DEPTH + 0.09)
        a = (p[0], p[1], ground_z - 0.3)
        b = (p[0], p[1], z0 + H * 0.92)
        mesh.box(a, b, 0.07 if W >= 1.3 else 0.04, 0.07 if W >= 1.3 else 0.04, M_STEEL)
        col.add(a, b, 0.08)


def truss(mesh, a, b, depth=1.0, height=1.2, along=None):
    """A box truss from a to b (3D: its bottom chord's middle line ends), depth across (along `along`, unit), height
    up: four chords, the end frames, verticals and diagonals on its faces every ~1.6 m."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    along = np.asarray(along, float)
    up = np.array([0.0, 0.0, 1.0])
    L = float(np.linalg.norm(b - a))
    n = max(1, int(round(L / 1.6)))
    off = [along * (depth / 2), -along * (depth / 2)]
    for s in off:
        for h in (0.0, height):
            mesh.box(a + s + up * h, b + s + up * h, 0.06, 0.06, M_STEEL)
    for k in range(n + 1):
        p = a + (b - a) * (k / n)
        for s in off:
            mesh.box(p + s, p + s + up * height, 0.035, 0.035, M_STEEL)
        mesh.box(p + off[0] + up * height, p + off[1] + up * height, 0.03, 0.03, M_STEEL)
        if k < n:
            q = a + (b - a) * ((k + 1) / n)
            for s in off:
                lo, hi = (p, q) if k % 2 == 0 else (q, p)
                mesh.box(lo + s, hi + s + up * height, 0.03, 0.03, M_STEEL)


def leg(mesh, col, foot, top_z, along, depth=1.0):
    """A gantry's leg: two columns (its truss' depth apart, along `along`) from foot (3D, the ground) to top_z, braced."""
    foot = np.asarray(foot, float)
    along = np.asarray(along, float)
    cols = [foot + along * (depth / 2), foot - along * (depth / 2)]
    for c in cols:
        mesh.box((c[0], c[1], foot[2] - 0.4), (c[0], c[1], top_z), 0.12, 0.12, M_STEEL)
    zs = np.arange(foot[2] + 1.0, top_z - 0.3, 1.4)
    for k, z in enumerate(zs):
        a, b = cols if k % 2 == 0 else cols[::-1]
        mesh.box((a[0], a[1], z), (b[0], b[1], min(z + 1.4, top_z)), 0.03, 0.03, M_STEEL)
    col.add((foot[0], foot[1], foot[2] - 0.4), (foot[0], foot[1], top_z), max(0.25, depth / 2 + 0.12))


# --------------------------------------------------------------------------------------------------------------- plan

R_EARTH = 6371008.8


def _dist(a, b):
    """m between (lat, lon) a and b (equirectangular: short spans)."""
    la = math.radians((a[0] + b[0]) / 2)
    dx = math.radians(b[1] - a[1]) * math.cos(la) * R_EARTH
    dy = math.radians(b[0] - a[0]) * R_EARTH
    return math.hypot(dx, dy)


def _brg(a, b):
    """Degrees clockwise from north of the way from a to b."""
    la = math.radians((a[0] + b[0]) / 2)
    dx = math.radians(b[1] - a[1]) * math.cos(la)
    dy = math.radians(b[0] - a[0])
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _turn(b0, b1):
    """Degrees from bearing b0 to b1, -180..180 (positive: to the right)."""
    return (b1 - b0 + 540.0) % 360.0 - 180.0


def mph_of(maxspeed):
    """An OSM maxspeed in mph ("75 mph", "65", "100" (km/h without a unit)), or None."""
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(mph)?", maxspeed or "")
    if not m:
        return None
    v = float(m.group(1))
    return v if m.group(2) else v / 1.609344


def cardinal(network, number, brg):
    """A route's cardinal direction travelling at bearing brg: odd Interstates and US routes run north-south, even
    ones east-west; other routes by the bearing's main axis."""
    num = re.match(r"\d+", str(number) or "")
    if route_kind(network, number) in ("I", "US") and num:
        ns = int(num.group(0)) % 2 == 1
    else:
        ns = abs(math.cos(math.radians(brg))) >= abs(math.sin(math.radians(brg)))
    if ns:
        return "NORTH" if math.cos(math.radians(brg)) >= 0 else "SOUTH"
    return "EAST" if math.sin(math.radians(brg)) >= 0 else "WEST"


def _split(v):
    return [t.strip() for t in (v or "").split(";") if t.strip()]


class Net:
    """A region's motorway and trunk ways (tools/build_highway_signs.py's file), directed along their travel."""

    def __init__(self, data):
        self.ways = []
        self.junctions = {int(j["id"]): j for j in data.get("junctions", ())}
        for w in data.get("ways", ()):
            tags = w.get("tags", {})
            hw = tags.get("highway", "")
            link = hw.endswith("_link")
            base = hw[:-5] if link else hw
            if base not in HIGHWAY_MAIN:
                continue
            ow = tags.get("oneway", "")
            if base == "trunk" and not link and ow not in ("yes", "-1", "1", "true"):
                continue                # (an undivided trunk: no freeway signs)
            nodes, geom = list(w["nodes"]), [tuple(p) for p in w["geom"]]
            if ow == "-1":
                nodes, geom = nodes[::-1], geom[::-1]
            if len(nodes) < 2 or len(nodes) != len(geom):
                continue
            self.ways.append({"id": int(w["id"]), "tags": tags, "link": link, "nodes": nodes, "geom": geom,
                              "refs": [] if re.search(r"\bEXPR", tags.get("ref", ""), re.I)     # (express lanes:
                              else [parse_ref(r) for r in _split(tags.get("ref"))]})          # not the route's)
        self.at, self.starts, self.ends, self.coord = {}, {}, {}, {}
        for wi, w in enumerate(self.ways):
            for k, (n, p) in enumerate(zip(w["nodes"], w["geom"])):
                self.at.setdefault(n, []).append((wi, k))
                self.coord[n] = p
            self.starts.setdefault(w["nodes"][0], []).append(wi)
            self.ends.setdefault(w["nodes"][-1], []).append(wi)

    def _main(self, wi):
        return not self.ways[wi]["link"]

    def _same_route(self, wi, wj):
        a, b = self.ways[wi]["refs"], self.ways[wj]["refs"]
        return bool(a and b and a[0][:2] == b[0][:2])

    def path(self, wi, k, down, reach):
        """The main line from node k of way wi, downstream (down) or upstream, out to reach m: [(m, (lat, lon),
        node)] from the start, following the way of the same route where it splits."""
        out = [(0.0, self.ways[wi]["geom"][k], self.ways[wi]["nodes"][k])]
        seen = {wi}
        while True:
            w = self.ways[wi]
            idx = range(k + 1, len(w["nodes"])) if down else range(k - 1, -1, -1)
            for j in idx:
                d = out[-1][0] + _dist(out[-1][1], w["geom"][j])
                out.append((d, w["geom"][j], w["nodes"][j]))
                if d >= reach:
                    return out
            end = w["nodes"][-1] if down else w["nodes"][0]
            nxt = [v for v in (self.starts if down else self.ends).get(end, ()) if self._main(v) and v not in seen]
            if not nxt:
                return out
            nxt.sort(key=lambda v: (not self._same_route(wi, v), self.ways[v]["id"]))
            wi = nxt[0]
            seen.add(wi)
            k = 0 if down else len(self.ways[wi]["nodes"]) - 1

    def dests(self, wi, depth=3):
        """(shields, names) a ramp signs: its destination tags, else the next ramp's (a ramp that splits)."""
        tags = self.ways[wi]["tags"]
        sh = [parse_ref(r) for r in _split(tags.get("destination:ref"))]
        names = _split(tags.get("destination")) or _split(tags.get("destination:street"))
        if not sh and not names and re.search(r"\bEXPR", tags.get("ref", ""), re.I):     # (I 25 EXPR: an express
            sh, names = [parse_ref(r) for r in _split(tags["ref"])], ["Express Lanes"]  # lanes' entrance)
        if not sh and not names and depth > 0:
            for v in self.starts.get(self.ways[wi]["nodes"][-1], ()):
                if self.ways[v]["link"]:
                    s2, n2 = self.dests(v, depth - 1)
                    sh += [s for s in s2 if s not in sh]
                    names += [n for n in n2 if n not in names]
        return sh, names


def _at(path, d):
    """(lat, lon, bearing of travel) at d m along a path (Net.path), the travel being from the path's end to its start
    when up (an upstream path)."""
    for (d0, p0, _), (d1, p1, _) in zip(path, path[1:]):
        if d <= d1:
            t = 0.0 if d1 <= d0 else (d - d0) / (d1 - d0)
            return (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t), _brg(p0, p1)
    return None, None


def _route_shields(refs, brg):
    return [(net, num, cardinal(net, num, brg)) for net, num, _ in refs[:2]]


def plan(data):
    """The sign items of a region (its file's dict): [{"k": kind, "lat", "lon", "brg": travel bearing, ...}]."""
    net = Net(data)
    items = []
    exits = set()           # junction nodes with a ramp leaving the main line
    for node, jn in net.junctions.items():
        if any(net.ways[v]["link"] for v in net.starts.get(node, ())):
            exits.add(node)
    for node in sorted(exits):
        jn = net.junctions[node]
        tags = jn.get("tags", {})
        links = [v for v in net.starts.get(node, ()) if net.ways[v]["link"]]
        ins = [(wi, k) for wi, k in net.at.get(node, ()) if net._main(wi) and k > 0]
        if not ins or not links:
            continue
        wi, k = ins[0]
        up = net.path(wi, k, False, MILE_M + 200)
        if len(up) < 2:
            continue
        outs = [(v, kk) for v, kk in net.at.get(node, ()) if net._main(v) and kk < len(net.ways[v]["nodes"]) - 1]
        main_brg = _brg(up[1][1], up[0][1])
        lw = net.ways[links[0]]
        link_brg = _brg(lw["geom"][0], lw["geom"][min(len(lw["geom"]) - 1, 2)])
        left = _turn(main_brg, link_brg) < 0
        sh, names = [], []
        for v in links:
            s2, n2 = net.dests(v)
            sh += [s for s in s2 if s not in sh]
            names += [n for n in n2 if n not in names]
        if not names and tags.get("exit_to"):
            for t in _split(tags["exit_to"]):
                r = parse_ref(t)
                (sh.append(r) if r[0] else names.append(t))
        if not names and not sh and tags.get("name"):
            names = [tags["name"]]
        if any(re.search(r"\bEXPR", net.ways[v]["tags"].get("ref", ""), re.I) for v in links) and \
                not any("xpress" in n for n in names):
            names = ["Express Lanes"] + names
        ref = None if tags.get("noref") == "yes" else (tags.get("ref") or lw["tags"].get("junction:ref"))
        ref = _split(ref)[0] if ref else None
        if not ref and not sh and not names:        # (nothing to sign: a service road, a ramp without tags)
            items.append({"k": "gore", "lat": jn["lat"], "lon": jn["lon"], "brg": link_brg, "mbrg": main_brg,
                          "ref": None, "left": left})
            continue
        svc = not ref and not sh and all(SERVICES.search(n) for n in names)
        thr = None
        if outs:
            ot = net.ways[outs[0][0]]
            tsh = [parse_ref(r) for r in _split(ot["tags"].get("destination:ref"))]
            tn = _split(ot["tags"].get("destination"))
            if not tsh:
                tsh = _route_shields(ot["refs"], main_brg)
            thr = {"sh": [list(s) for s in tsh[:2]], "dst": tn[:2]}
        base = {"ref": ref, "sh": [list(s) for s in sh[:2]], "dst": names[:2], "left": left}
        if svc:
            base["svc"] = True
        # another exit between a sign and this one: that sign is left out (it would sign past the other exit)
        others = [d for d, _, n in up[1:] if n in exits]
        for d, kind in ((60.0, "exit_dir"), (MILE_M / 2, "adv_half"), (MILE_M, "adv_1")):
            if (svc or any(o < d + 150.0 for o in others)) and kind != "exit_dir":
                continue
            p, b = _at(up, d)
            if p is None:
                continue
            it = dict(base, k=kind, lat=p[0], lon=p[1], brg=(b + 180.0) % 360.0)
            if kind == "exit_dir":
                it["thr"] = thr
            items.append(it)
        items.append({"k": "gore", "lat": jn["lat"], "lon": jn["lon"], "brg": link_brg, "mbrg": main_brg,
                      "ref": ref, "left": left})
    # route shields and the speed limit after each entrance, and where the limit changes
    for wi, w in enumerate(net.ways):
        if not w["link"]:
            continue
        end = w["nodes"][-1]
        on = [(v, k) for v, k in net.at.get(end, ()) if net._main(v) and k < len(net.ways[v]["nodes"]) - 1]
        if not on:
            continue
        v, k = on[0]
        down = net.path(v, k, True, 700.0)
        p, b = _at(down, 320.0)
        if p is None:
            continue
        refs = net.ways[v]["refs"]
        if refs:
            items.append({"k": "reassure", "lat": p[0], "lon": p[1], "brg": b,
                          "sh": [list(s) for s in _route_shields(refs, b)]})
        mph = mph_of(net.ways[v]["tags"].get("maxspeed"))
        p2, b2 = _at(down, 480.0)
        if mph and p2 is not None:
            items.append({"k": "speed", "lat": p2[0], "lon": p2[1], "brg": b2, "mph": int(round(mph / 5.0) * 5)})
    for wi, w in enumerate(net.ways):
        if w["link"]:
            continue
        mph = mph_of(w["tags"].get("maxspeed"))
        prev = [v for v in net.ends.get(w["nodes"][0], ()) if net._main(v)]
        if not mph or not prev:
            continue
        pm = mph_of(net.ways[prev[0]]["tags"].get("maxspeed"))
        if pm and abs(pm - mph) >= 4:
            p, b = _at(net.path(wi, 0, True, 200.0), 120.0)
            if p is not None:
                items.append({"k": "speed", "lat": p[0], "lon": p[1], "brg": b, "mph": int(round(mph / 5.0) * 5)})
    items = _dedupe(items, "reassure", 1200.0) if items else items
    items = _dedupe(items, "speed", 500.0)
    items += mile_markers(net)
    return items


SERVICES = re.compile(r"weigh|chain|scenic|overlook|rest area|visitor|port of entry|brake check|parking|view",
                      re.I)          # exits to these: one sign beside the road, no advance signs


def _dedupe(items, kind, within):
    """items of kind kept at least within m apart along the same direction (the first kept)."""
    out, kept = [], []
    for it in items:
        if it["k"] == kind:
            p = (it["lat"], it["lon"])
            if any(_dist(p, q) < within and abs(_turn(b, it["brg"])) < 30 and m == it.get("mph") for q, b, m in kept):
                continue
            kept.append((p, it["brg"], it.get("mph")))
        out.append(it)
    return out


def mile_markers(net):
    """A marker every mile along each route whose exits are numbered (the mileposts: Colorado's exit numbers are
    its mile markers), interpolated between the exits along each carriageway, and out to 3 miles past the last."""
    items = []
    for wi, w in enumerate(net.ways):
        if w["link"] or not w["refs"]:
            continue
        route = w["refs"][0][:2]
        if not re.match(r"^\d+", route[1] or ""):
            continue
        prev = [v for v in net.ends.get(w["nodes"][0], ()) if net._main(v) and net.ways[v]["refs"]
                and net.ways[v]["refs"][0][:2] == route]
        if prev:
            continue                    # (not a chain's first way)
        pts, seen, cur = [], set(), wi
        s = 0.0
        while cur is not None and cur not in seen:
            seen.add(cur)
            cw = net.ways[cur]
            for n, p in zip(cw["nodes"], cw["geom"]):
                if pts and n == pts[-1][2]:
                    continue
                if pts:
                    s += _dist(pts[-1][1], p)
                pts.append((s, p, n))
            nxt = [v for v in net.starts.get(cw["nodes"][-1], ()) if net._main(v) and net.ways[v]["refs"]
                   and net.ways[v]["refs"][0][:2] == route and v not in seen]
            cur = nxt[0] if nxt else None
        marks = []
        for d, p, n in pts:
            jn = net.junctions.get(n)
            if jn is None:
                continue
            m = re.match(r"^\s*(\d+)", (jn.get("tags", {}).get("ref") or ""))
            if m:
                marks.append((d, float(m.group(1))))
        if len(marks) < 2:
            continue
        slopes = sorted((b[1] - a[1]) / max(1.0, b[0] - a[0]) * MILE_M for a, b in zip(marks, marks[1:]))
        sign = 1.0 if slopes[len(slopes) // 2] >= 0 else -1.0
        good = [marks[0]]
        for d, m in marks[1:]:
            sl = (m - good[-1][1]) / max(1.0, d - good[-1][0]) * MILE_M * sign
            if 0.5 <= sl <= 1.6:
                good.append((d, m))
            elif len(good) == 1 and not (0.5 <= sl <= 1.6):
                good = [(d, m)]
        if len(good) < 2:
            continue
        S = [g[0] for g in good]
        Mv = [g[1] for g in good]
        lo_s = max(0.0, S[0] - 3 * MILE_M)
        hi_s = min(pts[-1][0], S[-1] + 3 * MILE_M)

        def mile_at(d):
            if d <= S[0]:
                return Mv[0] + sign * (d - S[0]) / MILE_M
            if d >= S[-1]:
                return Mv[-1] + sign * (d - S[-1]) / MILE_M
            j = int(np.searchsorted(S, d)) - 1
            return Mv[j] + (Mv[j + 1] - Mv[j]) * (d - S[j]) / (S[j + 1] - S[j])
        path = [(d, p, n) for d, p, n in pts]
        step = 20.0
        d = lo_s
        last = mile_at(d)
        while d + step <= hi_s:
            nxt = mile_at(d + step)
            lo_m, hi_m = min(last, nxt), max(last, nxt)
            for M in range(int(math.ceil(lo_m)), int(math.floor(hi_m)) + 1):
                if M <= 0 or not lo_m <= M < hi_m:
                    continue
                t = (M - last) / (nxt - last) if nxt != last else 0.0
                p, b = _at(path, d + step * t)
                if p is not None:
                    items.append({"k": "mile", "lat": p[0], "lon": p[1], "brg": b, "n": M})
            d += step
            last = nxt
    return items


class Plans:
    """The sign items of every region file (REGION_DIR), loaded when first asked for, indexed by 0.02 degree cells."""

    CELL = 0.02

    def __init__(self, dirs=(REGION_DIR,)):
        self.dirs = dirs
        self._lock = threading.Lock()
        self._grid = None

    SAME_M = 40.0       # an item of a later file this close to one of an earlier file's of its kind is that one

    def _key(self, lat, lon):
        return int(math.floor(lat / self.CELL)), int(math.floor(lon / self.CELL))

    def _seen(self, grid, it):
        """Whether an earlier file already has this sign (overlapping files: a region's inside the state's)."""
        i, j = self._key(it["lat"], it["lon"])
        kx = math.cos(math.radians(it["lat"]))
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for o in grid.get((i + di, j + dj), ()):
                    if o["k"] == it["k"] and o.get("_f") != it["_f"] and math.hypot(
                            (o["lat"] - it["lat"]) * 111_195.0, (o["lon"] - it["lon"]) * 111_195.0 * kx) < self.SAME_M:
                        return True
        return False

    def _load(self):
        paths = []
        for d in self.dirs:
            if os.path.isdir(d):
                paths += [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".json.gz")]
        paths.sort(key=lambda p: (-os.path.getsize(p), p))     # the biggest (the whole state's) first
        grid, later = {}, []
        for fi, path in enumerate(paths):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("kind") == "routes":        # (tools/build_route_signs.py's: routesigns, after the exits')
                import routesigns
                later += [dict(it, _f=fi) for it in routesigns.plan(data)]
                continue
            for it in plan(data):
                it["_f"] = fi
                if fi and self._seen(grid, it):
                    continue
                grid.setdefault(self._key(it["lat"], it["lon"]), []).append(it)
        for it in later:
            if it["k"] == "rmile" and self._near(grid, it, "mile", ROUTE_MILE_CLEAR_M):
                continue                # (a freeway with numbered exits: its exits' mile markers)
            if self._seen(grid, it):
                continue
            grid.setdefault(self._key(it["lat"], it["lon"]), []).append(it)
        return grid

    def _near(self, grid, it, kind, within):
        """Is there an item of kind within m of it, for the traffic its way?"""
        i, j = self._key(it["lat"], it["lon"])
        r = int(math.ceil(within / (self.CELL * 111_195.0 * 0.75)))
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                for o in grid.get((i + di, j + dj), ()):
                    if o["k"] == kind and abs(_turn(o["brg"], it["brg"])) < 45 and \
                            _dist((o["lat"], o["lon"]), (it["lat"], it["lon"])) < within:
                        return True
        return False

    def _cache_path(self):
        """The planned grid's pickle in the server's cache (revisit.CACHE_DIR), named by the region files (name, size,
        time) and this code: a statewide plan takes ~15 s, every worker process wants it."""
        import hashlib
        import revisit
        if not revisit.CACHE_DIR:
            return None
        h = hashlib.sha1(str(PLAN_V).encode())
        for d in self.dirs:
            if os.path.isdir(d):
                for n in sorted(os.listdir(d)):
                    if n.endswith(".json.gz"):
                        st = os.stat(os.path.join(d, n))
                        h.update(f"{d}|{n}|{st.st_size}|{int(st.st_mtime)}".encode())
        for mod in ("guidesigns.py", "routesigns.py"):
            with open(os.path.join(HERE, mod), "rb") as f:
                h.update(f.read())
        return os.path.join(revisit.CACHE_DIR, "hwysigns", f"plan-{h.hexdigest()[:16]}.pkl")

    def grid(self):
        with self._lock:
            if self._grid is None:
                import pickle
                try:
                    path = self._cache_path()
                except Exception:
                    path = None
                if path and os.path.exists(path):
                    try:
                        with open(path, "rb") as f:
                            self._grid = pickle.load(f)
                    except Exception:
                        self._grid = None
                if self._grid is None:
                    self._grid = self._load()
                    if path:
                        try:
                            os.makedirs(os.path.dirname(path), exist_ok=True)
                            with open(path + f".{os.getpid()}.tmp", "wb") as f:
                                pickle.dump(self._grid, f, protocol=pickle.HIGHEST_PROTOCOL)
                            os.replace(path + f".{os.getpid()}.tmp", path)
                        except OSError:
                            pass
            return self._grid

    def near(self, lat0, lon0, lat1, lon1):
        """The items inside the lat / lon box."""
        g = self.grid()
        if not g:
            return []
        out = []
        for i in range(int(math.floor(lat0 / self.CELL)), int(math.floor(lat1 / self.CELL)) + 1):
            for j in range(int(math.floor(lon0 / self.CELL)), int(math.floor(lon1 / self.CELL)) + 1):
                for it in g.get((i, j), ()):
                    if lat0 <= it["lat"] <= lat1 and lon0 <= it["lon"] <= lon1:
                        out.append(it)
        return out


_plans = None


def plans():
    global _plans
    if _plans is None:
        _plans = Plans()
    return _plans


# --------------------------------------------------------------------------------------------------------------- tile

def _h01(*vals):
    """A hash of vals in 0..1 (stable across runs)."""
    import zlib
    return (zlib.crc32(repr(vals).encode()) & 0xffffffff) / 4294967296.0


def _cum(line):
    seg = np.hypot(*np.diff(line[:, :2], axis=0).T)
    return np.concatenate([[0.0], np.cumsum(seg)]), seg


def _project(line, q):
    """(distance, station, point (3,), unit tangent along the geometry (2,)) of q's nearest point on line (k, 3)."""
    a, b = line[:-1, :2], line[1:, :2]
    ab = b - a
    L2 = np.maximum(np.sum(ab * ab, axis=1), 1e-12)
    t = np.clip(np.sum((q[None, :2] - a) * ab, axis=1) / L2, 0.0, 1.0)
    p = a + t[:, None] * ab
    d = np.hypot(p[:, 0] - q[0], p[:, 1] - q[1])
    i = int(np.argmin(d))
    cum, seg = _cum(line)
    z = line[i, 2] + (line[i + 1, 2] - line[i, 2]) * t[i]
    tan = ab[i] / max(seg[i], 1e-9)
    return float(d[i]), float(cum[i] + t[i] * seg[i]), np.array([p[i, 0], p[i, 1], z]), tan


def _point_at(line, s):
    """(point (3,), unit tangent (2,)) at station s along line, or (None, None) off its ends."""
    cum, seg = _cum(line)
    if s < 0 or s > cum[-1]:
        return None, None
    i = min(int(np.searchsorted(cum, s, side="right")) - 1, len(seg) - 1)
    t = (s - cum[i]) / max(seg[i], 1e-9)
    return line[i] + (line[i + 1] - line[i]) * t, (line[i + 1, :2] - line[i, :2]) / max(seg[i], 1e-9)


def _lanes(spec):
    if spec.lanes:
        return max(1, int(spec.lanes) // (1 if spec.oneway else 2))
    return max(1, int(round((spec.width - 3.0) / 3.6)))


def match(roads, xy, bvec, link=False, max_d=25.0):
    """The carriageway an item stands by: the nearest road of HIGHWAY_MAIN (a ramp when link) within max_d of xy whose
    travel runs along bvec. A dict: fid, spec, line, s, pt (3,), t (travel, unit 2D), sgn (travel along the geometry:
    +1 / -1), hw (half width), lanes, d (its distance); None when none."""
    got = candidates(roads, xy, bvec, link, max_d)
    return min(got, key=lambda m: m["d"]) if got else None


def candidates(roads, xy, bvec, link=False, max_d=25.0):
    """Every carriageway match() chooses from (its dicts, in roads' order)."""
    out = []
    for fid, spec, line, _ in roads:
        if spec.cls not in HIGHWAY_MAIN or bool(spec.link) != link or len(line) < 2:
            continue
        d, s, pt, tan = _project(line, xy)
        if d > max_d + spec.width / 2:
            continue
        sgn = spec.dir if spec.dir else (1 if float(np.dot(tan, bvec)) >= 0 else -1)
        t = tan * sgn
        if float(np.dot(t, bvec)) < 0.75:
            continue
        out.append({"d": d, "fid": fid, "spec": spec, "line": line, "s": s, "pt": pt, "t": t, "sgn": sgn,
                    "hw": spec.width / 2, "lanes": _lanes(spec)})
    return out


def _right(t, left_hand=False):
    r = np.array([t[1], -t[0]])
    return -r if left_hand else r


def _v3(v2):
    return np.array([v2[0], v2[1], 0.0])


def _gz(ground, x, y, fallback):
    if ground is None:
        return float(fallback)
    try:
        z = np.asarray(ground(np.array([x], float), np.array([y], float)), float).ravel()
        return float(z[0]) if len(z) and np.isfinite(z[0]) else float(fallback)
    except Exception:
        return float(fallback)


def lane_centres(m):
    """The lane centres' offsets to the right of the centreline (m), rightmost first."""
    hw, n = m["hw"], m["lanes"]
    sh_r = max(0.5, min(3.0, (2 * hw - n * 3.6) * 0.65))
    return [hw - sh_r - 1.8 - 3.6 * i for i in range(n)]


def overhead(mesh, col, m, panels, ground=None, left_hand=False):
    """Panels on a gantry over the carriageway m (match()): [(face, (W, H), u)] u: the panel's middle, m to the right
    of the centreline. A leg beyond the right edge (POST_OFF_M) and one in the median where it is wide enough
    (MEDIAN_LEG_GAP_M), else a cantilever from the right. Returns the structure's xy."""
    t = m["t"]
    R = _right(t, left_hand)
    pt = m["pt"]
    z_road = float(pt[2])
    bottom = z_road + CLEARANCE_M
    hmin = min(s[1] for _, s, _ in panels)
    tz = bottom + max(0.3, hmin / 2 - 0.6)
    u_r = m["hw"] + POST_OFF_M
    u_left = min(u - s[0] / 2 for _, s, u in panels)
    sp = m["spec"]
    gap = sp.median - 2 * m["hw"] if sp.median is not None else None
    median_leg = gap is not None and gap >= MEDIAN_LEG_GAP_M[0]
    if not median_leg:
        u_l = u_left - 0.4
    elif gap <= MEDIAN_LEG_GAP_M[1]:
        u_l = -sp.median / 2
    else:
        u_l = -m["hw"] - POST_OFF_M
    if median_leg and u_l > u_left - 0.3:
        u_l, median_leg = u_left - 0.4, False
    c = np.array([pt[0], pt[1], tz])
    a3, b3 = c + _v3(R) * u_l, c + _v3(R) * u_r
    truss(mesh, a3, b3, along=_v3(t))
    for u, has in ((u_r, True), (u_l, median_leg)):
        if not has:
            continue
        f = pt[:2] + R * u
        gz = _gz(ground, f[0], f[1], z_road)
        leg(mesh, col, (f[0], f[1], gz), tz + 1.2, _v3(t))
    for face, (W, H), u in panels:
        o = pt[:2] + R * u - t * (0.5 + 0.12)
        draw_face(mesh, face, (o[0], o[1], bottom), _v3(R), -_v3(t))
    return pt[:2]


def _guide_panels(it, m, overhead_ok):
    """The panels of an exit's guide sign item, with their offsets."""
    kind = it["k"]
    sh = [tuple(s) for s in it.get("sh", ())]
    bottom = {"adv_1": "1 MILE", "adv_half": "½ MILE"}.get(kind)
    left = bool(it.get("left"))
    lanes = lane_centres(m)
    if not overhead_ok:
        face, size = guide_face(sh, it.get("dst", ()), it.get("ref"), bottom,
                                None if bottom else ("up_left" if left else "up_right"), left)
        return [(face, size, None)]
    face, size = guide_face(sh, it.get("dst", ()), it.get("ref"), bottom, None if bottom else "down", left)
    u_exit = lanes[-1] if left else lanes[0]
    u_exit = min(u_exit, m["hw"] + 0.5 - size[0] / 2) if not left else max(u_exit, -m["hw"] - 0.5 + size[0] / 2)
    out = [(face, size, u_exit)]
    thr = it.get("thr")
    if kind == "exit_dir" and thr and (thr.get("sh") or thr.get("dst")) and len(lanes) >= 2:
        tf, ts = guide_face([tuple(s) for s in thr.get("sh", ())], thr.get("dst", ()), None, None, "down")
        u_thr = float(np.mean(lanes[:-1] if left else lanes[1:]))
        if left:
            u_thr = max(u_thr, u_exit + size[0] / 2 + 0.4 + ts[0] / 2)
        else:
            u_thr = min(u_thr, u_exit - size[0] / 2 - 0.4 - ts[0] / 2)
        out.append((tf, ts, u_thr))
    return out


def _side_sign(mesh, col, m, face, size, ground, left_hand=False, off=POST_OFF_M, bottom=POST_SIGN_BOTTOM_M,
               collide=True):
    """A ground-mounted face beside the carriageway's right edge (its near edge off m out), facing the traffic."""
    R = _right(m["t"], left_hand)
    base = m["pt"][:2] + R * (m["hw"] + off + size[0] / 2)
    gz = _gz(ground, base[0], base[1], m["pt"][2])
    ground_mount(mesh, col if collide else Boxes(), face, size, (base[0], base[1], gz), _v3(R), -_v3(m["t"]), gz,
                 bottom)
    return base


def gore(mesh, col, roads, it, xy, ground, left_hand=False):
    """The painted chevrons between a ramp and its carriageway where they part, and the EXIT sign in the gore."""
    bl = np.array([math.sin(math.radians(it["brg"])), math.cos(math.radians(it["brg"]))])
    bm = np.array([math.sin(math.radians(it["mbrg"])), math.cos(math.radians(it["mbrg"]))])
    ml = match(roads, xy, bl, link=True, max_d=40.0)
    # the carriageway the ramp leaves: every one at the gore running its way, the nearest at each point along the ramp.
    # Where the motorway's ways split at the exit (a lane dropped into it) the junction is the end of one and the start
    # of the next; matched to the one ending there, the gap grew toward that end point, along the road, and the EXIT sign
    # stood in the continuing lanes (I-25 at 39.9884,-104.9873: the bake's autodrive drove into it, 2026-10-28)
    mains = candidates(roads, xy, bm, link=False, max_d=30.0)
    if ml is None or not mains:
        return None
    line, sgn, s0, k0, hwl = ml["line"], ml["sgn"], ml["s"], 0, ml["hw"]
    painted, sign_at = [], None
    last_stripe = -1e9
    used, tan = {ml["fid"]}, ml["t"]
    for k in range(0, 260):
        q, tan_k = _point_at(line, s0 + sgn * (k - k0))
        if q is None:
            # on along the ramp's next way (a ramp split into short ways: the first can end before the gap opens)
            end = line[-1] if sgn > 0 else line[0]
            nxt = [m for m in candidates(roads, end[:2], tan, link=True, max_d=1.0) if m["fid"] not in used]
            if not nxt:
                break
            m = min(nxt, key=lambda c: c["d"])
            used.add(m["fid"])
            line, sgn, s0, k0, hwl = m["line"], m["sgn"], m["s"], k, m["hw"]
            q, tan_k = _point_at(line, s0)
            if q is None:
                break
        tan = tan_k * sgn
        gap, dm, pm, mm = min(((d - m["hw"] - hwl, d, p, m) for m in mains
                               for d, _, p, _ in (_project(m["line"], q),)), key=lambda g: g[0])
        if gap > 12.0:
            break
        if GORE_PAINT_M[0] <= gap <= GORE_PAINT_M[1] and k - last_stripe >= CHEVRON_EVERY_M:
            last_stripe = k
            painted.append((q, pm, dm, mm["hw"]))
        if gap >= GORE_SIGN_GAP_M and sign_at is None:
            sign_at = (q, pm, dm, gap, mm["hw"])
            break
    tv = min(mains, key=lambda m: m["d"])["t"]
    for q, pm, dm, hw in painted:
        across = (q[:2] - pm[:2]) / max(dm, 1e-6)
        em = pm[:2] + across * hw
        el = q[:2] - across * ml["hw"]
        span = float(np.linalg.norm(el - em))
        if span < 0.3:
            continue
        sk = tv * (0.8 * span)
        z = (pm[2] + q[2]) / 2 + 0.04
        w = tv * (CHEVRON_W / 2)
        a, b, c, d = em - w, em + w, el + sk + w, el + sk - w
        pts = [(float(p[0]), float(p[1]), float(z)) for p in (a, b, c, d)]
        if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
            pts = pts[::-1]
        mesh.quad(*pts, M_PAINT, WHITE)
    if sign_at is not None:
        q, pm, dm, gap, hw = sign_at
        across = (q[:2] - pm[:2]) / max(dm, 1e-6)
        mid = pm[:2] + across * (hw + gap / 2)
        face, size = gore_face(it.get("ref"))
        gz = _gz(ground, mid[0], mid[1], (q[2] + pm[2]) / 2)
        R = _right(tv, left_hand)
        ground_mount(mesh, col, face, size, (mid[0], mid[1], gz), _v3(R), -_v3(tv), gz, 1.5)
    return xy


def delineators(mesh, roads, box, barriers=None, left_hand=False):
    """Delineator posts along motorway and ramp edges every DELINEATOR_EVERY_M (white reflectors on the right,
    amber on the left), off where a barrier runs, or another carriageway is."""
    x0, y0, x1, y1 = box
    bpts = None
    if barriers:
        got = [np.asarray(b["line"], float)[:, :2] for b in barriers if len(b.get("line", ())) >= 1]
        if got:
            bpts = np.concatenate(got)
    others = [(line[:, :2], spec.width / 2) for _, spec, line, _ in roads if len(line) >= 2]
    for fid, spec, line, _ in roads:
        if spec.cls not in HIGHWAY_MAIN or not spec.oneway or len(line) < 2:
            continue
        cum, _ = _cum(line)
        L = float(cum[-1])
        phase = _h01("delin", fid) * DELINEATOR_EVERY_M
        s = phase
        while s < L:
            p, tan = _point_at(line, s)
            s += DELINEATOR_EVERY_M
            if p is None:
                continue
            t = tan * (spec.dir or 1)
            R = _right(t, left_hand)
            for side, mat in ((1.0, M_REFLECT), (-1.0, M_AMBER)):
                q = p[:2] + R * side * (spec.width / 2 + DELINEATOR_OFF_M)
                if not (x0 <= q[0] < x1 and y0 <= q[1] < y1):
                    continue
                if bpts is not None and float(np.min(np.hypot(*(bpts - q).T))) < 3.0:
                    continue
                if any(_inside(q, ol, hw) for ol, hw in others):
                    continue
                z = float(p[2])
                mesh.box((q[0], q[1], z - 0.2), (q[0], q[1], z + 1.15), 0.05, 0.025, M_PANEL, POST)
                n = -t
                c = np.array([q[0] + n[0] * 0.03, q[1] + n[1] * 0.03])
                r = R * 0.04
                a, b = c - r, c + r
                mesh.quad((a[0], a[1], z + 0.88), (b[0], b[1], z + 0.88), (b[0], b[1], z + 1.08),
                          (a[0], a[1], z + 1.08), mat, WHITE)


def _inside(q, line, hw, margin=0.3):
    a, b = line[:-1], line[1:]
    ab = b - a
    L2 = np.maximum(np.sum(ab * ab, axis=1), 1e-12)
    t = np.clip(np.sum((q[None] - a) * ab, axis=1) / L2, 0.0, 1.0)
    p = a + t[:, None] * ab
    return float(np.min(np.hypot(p[:, 0] - q[0], p[:, 1] - q[1]))) < hw - margin


SUFFIX = {"street": "St", "avenue": "Ave", "boulevard": "Blvd", "drive": "Dr", "road": "Rd", "parkway": "Pkwy",
          "place": "Pl", "court": "Ct", "lane": "Ln", "circle": "Cir", "highway": "Hwy", "terrace": "Ter",
          "trail": "Trl", "square": "Sq", "expressway": "Expy", "freeway": "Fwy", "mall": "Mall", "way": "Way",
          "crossing": "Xing", "point": "Pt"}
DIRS = {"north": "N", "south": "S", "east": "E", "west": "W", "northeast": "NE", "northwest": "NW",
        "southeast": "SE", "southwest": "SW"}


def street_name(name):
    """A street's name as its blade spells it: the suffix and a leading or trailing direction abbreviated (East
    Colfax Avenue: E Colfax Ave); '' for none."""
    nm = str(name or "").strip()
    if not nm or nm == "None" or nm == "nan":
        return ""
    w = nm.split()
    if len(w) >= 3 and w[0].lower() in DIRS:
        w[0] = DIRS[w[0].lower()]
    if len(w) >= 3 and w[-1].lower() in DIRS:
        w[-1] = DIRS[w[-1].lower()]
    for k in (len(w) - 1, len(w) - 2):
        if 1 <= k < len(w) and w[k].lower() in SUFFIX and (k == len(w) - 1 or w[-1] in DIRS.values()):
            w[k] = SUFFIX[w[k].lower()]
            break
    glyphs = font()["glyphs"]
    out = []
    for ch in " ".join(w):
        if ch != " " and ch not in glyphs:      # (an accent the atlas lacks: its letter)
            ch = unicodedata.normalize("NFKD", ch)[0]
        if ch == " " or ch in glyphs:
            out.append(ch)
    return "".join(out)


def blade_face(text, cap):
    """A street name blade (MUTCD D3-1): white mixed-case lettering on green, a thin white border; bottom middle at
    the origin."""
    H = round(cap * 2.0 + 0.03, 3)
    W = max(0.6, text_width(text, cap) + cap * 1.4)
    f = Face()
    f.poly(rounded_rect(-W / 2, 0, W / 2, H, cap * 0.25), WHITE, 0)
    f.poly(rounded_rect(-W / 2 + 0.012, 0.012, W / 2 - 0.012, H - 0.012, cap * 0.2), GREEN, 1)
    f.text_c(text, cap, 0.0, (H - cap) / 2, "w", 2)
    return f, (W, H)


def _blade(mesh, face, o, along):
    """A two-sided blade: face on both sides of o (3D, its bottom middle), its length along along (unit 2D)."""
    a = np.array([along[0], along[1], 0.0])
    nrm = np.array([along[1], -along[0], 0.0])          # (right x up: the side drawn faces it)
    o = np.asarray(o, float)
    draw_face(mesh, face, o + nrm * 0.004, a, nrm, back=False)
    draw_face(mesh, face, o - nrm * 0.004, -a, -nrm, back=False)


def _corner(c, a, b, clear):
    """The point outside both carriageways of arms a and b (furniture._arms' entries, u away from the junction c)
    clear m from each edge, or None where they are nearly in line (no corner)."""
    ua, ub = np.asarray(a[2], float), np.asarray(b[2], float)
    sin = abs(ua[0] * ub[1] - ua[1] * ub[0])
    if sin < 0.35 or float(ua @ ub) < -0.94:
        return None
    s_a = (b[1].width / 2 + clear) / sin
    s_b = (a[1].width / 2 + clear) / sin
    if max(s_a, s_b) > 30.0:
        return None
    return np.asarray(c, float)[:2] + ua * s_a + ub * s_b


def street_signs(mesh, col, roads, box, names, ground=None, avoid=(), left_hand=False):
    """Street name signs at the junctions in town where two named streets meet: a post on a corner with the two
    blades crossed on top, each blade along its street (read by the traffic on the other); two corners at a wide
    junction. names: {fid: OpenStreetMap / Overture name}; avoid: (x, y) of the props already standing near (signals,
    stop signs, lamps). Junctions inside box only (each is built by one tile)."""
    import furniture
    x0, y0, x1, y1 = box
    lines = [(sp.width / 2, np.asarray(pts, float)[:, :2]) for _, sp, pts, _ in roads if len(pts) >= 2]
    av = np.asarray([p[:2] for p in avoid], float).reshape(-1, 2)
    n = 0
    for k, arms in sorted(furniture._arms(roads).items()):
        if len(mesh.P) > VERTS_MAX:
            break
        c = np.asarray(arms[0][3], float)
        if len(arms) < 3 or not (x0 <= c[0] < x1 and y0 <= c[1] < y1) or not all(a[1].urban for a in arms):
            continue
        named = [(a, street_name(names.get(a[0]))) for a in arms
                 if a[1].cls in STREET_CLASSES and not a[1].link]
        named = sorted(((a, nm) for a, nm in named if nm), key=lambda t: (-t[0][1].width, t[1]))
        if len({nm for _, nm in named}) < 2:
            continue
        sa = named[0]
        sb = next(t for t in named if t[1] != sa[1])
        order = sorted(arms, key=lambda a: math.atan2(a[2][1], a[2][0]))
        near = [(h, ln) for h, ln in lines if np.hypot(*(ln - c[:2]).T).min() < 60.0]
        spots = []
        for i in range(len(order)):
            p = _corner(c, order[i], order[(i + 1) % len(order)], STREET_CLEAR_M)
            if p is None or not all(furniture._seg_dist_min(p, ln) >= h + 0.8 for h, ln in near):
                continue
            if len(av) and float(np.min(np.hypot(*(av - p).T))) < 1.2:
                continue
            spots.append(p)
        if not spots:
            continue
        spots.sort(key=lambda p: _h01(round(float(p[0]), 1), round(float(p[1]), 1)))
        put = [spots[0]]
        if max(a[1].width for a in arms) > STREET_WIDE_M and len(spots) > 1:
            put.append(max(spots[1:], key=lambda p: float(np.hypot(*(p - spots[0])))))
        for p in put:
            z = _gz(ground, p[0], p[1], c[2] + 0.15)
            top = z + BLADE_TOP_M
            mesh.box((p[0], p[1], z - 0.3), (p[0], p[1], top + 0.05), 0.03, 0.03, M_STEEL)
            col.add((p[0], p[1], z - 0.3), (p[0], p[1], top), 0.04)
            zb = top + 0.02
            for (arm, nm) in (sa, sb):
                cap = BLADE_CAP[0] if arm[1].cls in STREET_MAJOR else BLADE_CAP[1]
                face, (W, H) = blade_face(nm, cap)
                u = np.asarray(arm[2], float)
                if u[0] < -1e-6 or (abs(u[0]) <= 1e-6 and u[1] < 0):     # (either way along: the same blade)
                    u = -u
                _blade(mesh, face, (p[0], p[1], zb), u)
                zb += H + 0.03
            n += 1
    return n


MAST_ARM = ((0.3, 4.75), (3.7, 6.0), (6.0, 6.1))  # the stock boom's (s_trafficlight_boom_sn) mast arm: its
                                # underside's height over the foot (m) at m out from the pole (rising to 3.7 m out)
MAST_SIGN_AT = (1.5, 4.95)      # m out along the arm where a sign may hang (its overhead head from 5.1 m)
MAST_CAP = 0.20                 # its lettering (8")
MAST_ARM_AXIS = -1.0            # the arm reaches along the boom's local x this way (-1: over the road from the kerb)


def _arm_z(u):
    """The mast arm's underside over the boom's foot, u m out from its pole."""
    xs, zs = zip(*MAST_ARM)
    return float(np.interp(u, xs, zs))


def mast_arm_signs(mesh, roads, box, names, booms, left_hand=False):
    """Street name signs on the signals' mast arms (MUTCD D3-1 overhead): on each boom (junction_props' "signal"
    rows), the name of the street its approach crosses, facing the drivers who come in, hung under the arm between
    the pole and the first head. names: {fid: name}; booms: the tile's own (box: unused, each boom is its
    junction's tile's)."""
    import furniture
    junctions = [(np.asarray(arms[0][3], float)[:2], arms) for arms in furniture._arms(roads).values()
                 if len(arms) >= 3]
    if not junctions:
        return 0
    C = np.array([c for c, _ in junctions])
    n = 0
    for row in booms:
        if len(row) < 5 or len(mesh.P) > VERTS_MAX:
            break
        x, y, z, yaw = float(row[1]), float(row[2]), float(row[3]), math.radians(float(row[4]))
        R = np.array([math.cos(yaw), math.sin(yaw)])           # (the boom's local +x: the approach's kerb side)
        trav = np.array([-math.sin(yaw), math.cos(yaw)])       # (its local +y: the approach's travel)
        d = np.hypot(C[:, 0] - x, C[:, 1] - y)
        j = int(np.argmin(d))
        if d[j] > 45.0:
            continue
        arms = junctions[j][1]
        appr = min(arms, key=lambda a: float(np.dot(a[2], trav)))
        if float(np.dot(appr[2], trav)) > -0.7:
            continue
        own = street_name(names.get(appr[0]))
        cross = [(abs(float(a[2][0] * trav[1] - a[2][1] * trav[0])), street_name(names.get(a[0])), a) for a in arms
                 if a[1].cls in STREET_CLASSES and not a[1].link]
        cross = [c for c in cross if c[1] and c[1] != own and c[0] > 0.6]
        if not cross:
            continue
        cross.sort(key=lambda c: (c[2][1].cls not in STREET_MAJOR, -c[0], c[1]))
        face, (W, H) = blade_face(cross[0][1], MAST_CAP)
        if W > MAST_SIGN_AT[1] - MAST_SIGN_AT[0]:
            continue                # (too long a name for the arm's free span)
        out = MAST_SIGN_AT[1] - W / 2                           # (as far out as the head lets: the level part)
        mid = np.array([x, y]) + R * (MAST_ARM_AXIS * out)
        zb = z + _arm_z(out - W / 2) - 0.04 - H                 # (its top under the arm's lowest point over it)
        o = mid - trav * 0.18                                   # (in front of the arm: the heads' side, the drivers')
        draw_face(mesh, face, (o[0], o[1], zb), _v3(R), -_v3(trav))
        for u in (-W * 0.3, W * 0.3):                          # (its two hangers up to the arm)
            h = mid + R * u - trav * 0.1
            zt = z + _arm_z(out + MAST_ARM_AXIS * u) + 0.05
            mesh.box((h[0], h[1], zb + H * 0.5), (h[0], h[1], zt), 0.015, 0.015, M_STEEL)
        n += 1
    return n


def billboards(roads, box, frame, urban, ground, avoid, left_hand=False):
    """Prop rows (trees.lua) of billboards along motorways outside towns: West Coast USA's drag-strip billboard
    and one of its ads, BILLBOARD_EVERY_M or so apart, BILLBOARD_OFF_M beyond the right edge, facing the traffic,
    clear of sign gantries (avoid: xy) and of other roads."""
    if urban is None or frame is None:
        return []
    x0, y0, x1, y1 = box
    cand = []
    for fid, spec, line, _ in roads:
        if spec.cls != "motorway" or spec.link or not spec.oneway or len(line) < 2:
            continue
        cum, _ = _cum(line)
        s = _h01("bb", fid) * BILLBOARD_EVERY_M
        while s < cum[-1]:
            p, tan = _point_at(line, s)
            k = int(s // BILLBOARD_EVERY_M)
            s += BILLBOARD_EVERY_M * (0.7 + 0.6 * _h01("bbs", fid, k))
            if p is None:
                continue
            t = tan * (spec.dir or 1)
            R = _right(t, left_hand)
            off = BILLBOARD_OFF_M[0] + (BILLBOARD_OFF_M[1] - BILLBOARD_OFF_M[0]) * _h01("bbo", fid, k)
            q = p[:2] + R * (spec.width / 2 + off)
            if not (x0 <= q[0] < x1 and y0 <= q[1] < y1):
                continue
            if any(float(np.hypot(*(q - np.asarray(a)[:2]))) < BILLBOARD_CLEAR_M for a in avoid):
                continue
            if any(_inside(q, ln[:, :2], sp.width / 2 + 12.0, 0.0) for _, sp, ln, _ in roads if len(ln) >= 2):
                continue
            cand.append((q, R, p, fid, k))
    if not cand:
        return []
    qs = np.array([c[0] for c in cand])
    la, lo, _ = frame.enu_to_geodetic(qs[:, 0], qs[:, 1], np.zeros(len(qs)))
    try:
        town = np.asarray(urban(np.asarray(la), np.asarray(lo)), bool).ravel()
    except Exception:
        return []
    rows = []
    for (q, R, p, fid, k), u in zip(cand, town):
        if u:
            continue
        gz = _gz(ground, q[0], q[1], p[2])
        yaw = round(math.degrees(math.atan2(R[1], R[0])), 1) % 360.0     # (furniture._yaw: +x along R, +y the travel)
        ad = 1 + int(_h01("bbad", fid, k) * BILLBOARD_ADS) % BILLBOARD_ADS
        for g in ("billboard", f"bbad{ad:02d}"):
            rows.append([g, round(float(q[0]), 2), round(float(q[1]), 2), round(gz, 2), yaw, 1.0, 11.95, 0.0])
    return rows


def items_near(frame, box, margin=400.0, source=None):
    """The plan items within margin of the tile box, with their frame xy: [(item, (x, y))]."""
    x0, y0, x1, y1 = box
    e = np.array([x0 - margin, x1 + margin, x0 - margin, x1 + margin])
    n = np.array([y0 - margin, y0 - margin, y1 + margin, y1 + margin])
    la, lo, _ = frame.enu_to_geodetic(e, n, np.zeros(4))
    la, lo = np.asarray(la, float), np.asarray(lo, float)
    got = (source or plans()).near(float(la.min()), float(lo.min()), float(la.max()), float(lo.max()))
    if not got:
        return []
    ex, ny, _ = frame.geodetic_to_enu(np.array([g["lat"] for g in got]), np.array([g["lon"] for g in got]),
                                      np.zeros(len(got)))
    return [(g, (float(a), float(b))) for g, a, b in zip(got, np.atleast_1d(ex), np.atleast_1d(ny))]


def tile(frame, roads, box, ground=None, urban=None, barriers=None, mph=True, left_hand=False, items=None,
         names=None, avoid=(), booms=()):
    """A road tile's highway signs: {"mesh": Mesh.result (render-only), "col": Boxes.result (posts and legs,
    collision only), "props": billboard rows}, positions relative to the tile's corner (box[0], box[1], 0). roads:
    roads.build_tile's (fid, spec, centreline (k, 3), lon); items: items_near's (None: looked up); names: {fid: the
    street's name} for the street name signs (where mph: the American blades), avoid: the props' (x, y) they keep
    clear of; booms: the signals' prop rows (their mast arms' street name signs)."""
    import routesigns
    x0, y0, x1, y1 = box
    origin = (x0, y0, 0.0)
    mesh, col = Mesh(), Boxes()
    if names and mph:
        try:
            street_signs(mesh, col, roads, box, names, ground, avoid, left_hand)
            if booms:
                mast_arm_signs(mesh, roads, box, names, booms, left_hand)
        except Exception:               # (the highway signs regardless)
            mesh, col = Mesh(), Boxes()
    if items is None:
        items = items_near(frame, box) if frame is not None else []
    if not any(sp.cls in HIGHWAY_MAIN for _, sp, _, _ in roads):
        items = [(it, xy) for it, xy in items if it["k"] in routesigns.KINDS]
        if not items:
            return {"mesh": mesh.result(origin), "col": col.result(origin), "props": []}
    avoid = []
    for it, (x, y) in items:
        xy = np.array([x, y])
        if it["k"] in ("exit_dir", "adv_1", "adv_half", "gore"):
            avoid.append(xy)
        if not (x0 <= x < x1 and y0 <= y < y1):
            continue
        b = np.array([math.sin(math.radians(it["brg"])), math.cos(math.radians(it["brg"]))])
        k = it["k"]
        try:
            if k in routesigns.KINDS:
                if mph or k in ("pass", "city"):
                    routesigns.build(mesh, col, roads, it, xy, ground, left_hand)
                continue
            if k == "gore":
                gore(mesh, col, roads, it, xy, ground, left_hand)
                continue
            m = match(roads, xy, b)
            if m is None:
                continue
            if k in ("exit_dir", "adv_1", "adv_half"):
                up = not it.get("svc") and (k == "exit_dir" or m["spec"].urban or m["lanes"] >= 3)
                panels = _guide_panels(it, m, up)
                if up:
                    overhead(mesh, col, m, panels, ground, left_hand)
                else:
                    face, size, _ = panels[0]
                    _side_sign(mesh, col, m, face, size, ground, left_hand)
            elif k == "reassure":
                face, size = reassurance_face([tuple(s) for s in it.get("sh", ())])
                _side_sign(mesh, col, m, face, size, ground, left_hand)
            elif k == "speed" and mph:
                face, size = speed_face(it["mph"])
                _side_sign(mesh, col, m, face, size, ground, left_hand)
            elif k == "mile":
                face, size = mile_face(it["n"])
                _side_sign(mesh, col, m, face, size, ground, left_hand, off=1.8, bottom=1.2, collide=False)
        except Exception:               # (one odd item: the rest of the tile's signs)
            continue
    delineators(mesh, roads, box, barriers, left_hand)
    props = billboards(roads, box, frame, urban, ground, avoid, left_hand) if mph else []
    return {"mesh": mesh.result(origin), "col": col.result(origin), "props": props}
