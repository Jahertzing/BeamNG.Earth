#!/usr/bin/env python3
"""The street name signs' lettering as geometry (server/guidesigns.py, Face.vtext): every glyph's outline from the
font, its curves flattened, triangulated, in cap heights from the pen on the baseline, written to
server/signglyphs.json. Drawn as triangles of the sign's own white / black material, the letters stay sharp at any
distance and any texture quality (the glyph atlas' letters turned to blocks up close: the user, 2026-10-28).

    python tools/build_sign_glyphs.py --font Overpass[wght].ttf      (needs fonttools and mapbox_earcut)

The font: Overpass SemiBold (SIL Open Font License 1.1), as tools/build_sign_font.py's atlas, so the advances and
kerning of server/signfont.json fit.
"""
import argparse
import json
import math
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "server", "signglyphs.json")
CHARS = "".join(chr(c) for c in range(33, 127)) + "½¼¾éñ"
WEIGHT = 600
STEP = 0.16             # cap heights per flattened curve segment (at most): ~28 vertices a letter


def _contours(glyphset, name):
    """The glyph's closed contours: [[(x, y), ...]] in font units, quadratic curves flattened."""
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    pen = DecomposingRecordingPen(glyphset)
    glyphset[name].draw(pen)
    out, cur = [], []

    def curve(p0, ctrl, end):
        # a TrueType qCurveTo: implied on-curve points between consecutive controls
        pts = [p0] + list(ctrl) + [end]
        segs = []
        for i in range(1, len(pts) - 1):
            a = pts[i - 1] if i == 1 else ((pts[i - 1][0] + pts[i][0]) / 2, (pts[i - 1][1] + pts[i][1]) / 2)
            b = pts[i]
            c = pts[i + 1] if i == len(pts) - 2 else ((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2)
            segs.append((a, b, c))
        return segs
    for op, args in pen.value:
        if op == "moveTo":
            cur = [args[0]]
        elif op == "lineTo":
            cur.append(args[0])
        elif op == "qCurveTo":
            if args[-1] is None:
                raise SystemExit("closed off-curve contours are not handled")
            for a, b, c in curve(cur[-1], args[:-1], args[-1]):
                cur += _quad(a, b, c)
        elif op == "curveTo":
            a, b, c = args
            cur += _cubic(cur[-1], a, b, c)
        elif op in ("closePath", "endPath"):
            if len(cur) > 2:
                if cur[0] == cur[-1]:
                    cur = cur[:-1]
                out.append(cur)
            cur = []
    return out


UNIT = [None]           # font units per cap height (set in build)


def _n(length):
    return max(1, min(5, int(math.ceil(length / (STEP * UNIT[0])))))


def _quad(a, b, c):
    n = _n(math.dist(a, b) + math.dist(b, c))
    return [((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * b[0] + t * t * c[0],
             (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * b[1] + t * t * c[1]) for t in (k / n for k in range(1, n + 1))]


def _cubic(a, b, c, d):
    n = _n(math.dist(a, b) + math.dist(b, c) + math.dist(c, d))
    out = []
    for k in range(1, n + 1):
        t = k / n
        u = 1 - t
        out.append((u ** 3 * a[0] + 3 * u * u * t * b[0] + 3 * u * t * t * c[0] + t ** 3 * d[0],
                    u ** 3 * a[1] + 3 * u * u * t * b[1] + 3 * u * t * t * c[1] + t ** 3 * d[1]))
    return out


def _area(c):
    return 0.5 * sum(c[i][0] * c[(i + 1) % len(c)][1] - c[(i + 1) % len(c)][0] * c[i][1] for i in range(len(c)))


def _inside(p, c):
    x, y = p
    hit = False
    for i in range(len(c)):
        (x0, y0), (x1, y1) = c[i], c[(i + 1) % len(c)]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def triangulate(contours):
    """Vertices [(x, y)] and triangles [(a, b, c)] counter-clockwise: each outer contour with the holes inside it."""
    import mapbox_earcut as earcut
    outer = [c for c in contours if _area(c) != 0]
    sign = 1.0 if sum(_area(c) for c in outer) >= 0 else -1.0      # (TrueType outers run clockwise)
    outs = [c for c in outer if _area(c) * sign > 0]
    holes = [c for c in outer if _area(c) * sign < 0]
    V, T = [], []
    for o in outs:
        hs = [h for h in holes if _inside(h[0], o)]
        rings = [o] + hs
        pts = np.array([p for r in rings for p in r], np.float64)
        ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
        tri = earcut.triangulate_float64(pts, ends).reshape(-1, 3)
        base = len(V)
        V += [tuple(p) for p in pts]
        for a, b, c in tri:
            pa, pb, pc = pts[a], pts[b], pts[c]
            if (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0]) < 0:
                b, c = c, b
            T.append((base + int(a), base + int(b), base + int(c)))
    return V, T


def build(font_path, out=OUT, weight=WEIGHT):
    from fontTools.ttLib import TTFont
    font = TTFont(font_path)
    if "fvar" in font:
        from fontTools.varLib import instancer
        font = instancer.instantiateVariableFont(font, {"wght": weight})
    gs = font.getGlyphSet()
    cmap = font.getBestCmap()
    cap = float(getattr(font["OS/2"], "sCapHeight", 0) or 0)
    if not cap:
        from fontTools.pens.boundsPen import BoundsPen
        bp = BoundsPen(gs)
        gs[cmap[ord("H")]].draw(bp)
        cap = bp.bounds[3]
    UNIT[0] = cap
    glyphs = {}
    nv = 0
    for ch in CHARS:
        name = cmap.get(ord(ch))
        if name is None:
            continue
        V, T = triangulate(_contours(gs, name))
        glyphs[ch] = {"v": [[round(x / cap, 4), round(y / cap, 4)] for x, y in V], "t": [list(t) for t in T]}
        nv += len(V)
    with open(out, "w") as f:
        json.dump({"source": "Overpass SemiBold (SIL OFL 1.1), tools/build_sign_glyphs.py", "glyphs": glyphs}, f,
                  separators=(",", ":"))
    print(f"{out}: {len(glyphs)} glyphs, {nv} vertices ({nv / max(1, len(glyphs)):.0f} a glyph), "
          f"{os.path.getsize(out) / 1e3:.0f} kB")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--font", required=True, help="Overpass[wght].ttf (Google Fonts ofl/overpass)")
    ap.add_argument("--weight", type=int, default=WEIGHT)
    a = ap.parse_args()
    build(a.font, weight=a.weight)


if __name__ == "__main__":
    main()
