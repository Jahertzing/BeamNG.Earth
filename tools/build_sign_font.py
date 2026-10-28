#!/usr/bin/env python3
"""The highway signs' lettering (server/guidesigns.py): one glyph atlas the guide signs, shields, mile markers and
speed limits spell their text with, a textured quad per letter over the sign's panel.

    python tools/build_sign_font.py --font Overpass[wght].ttf

The atlas (mod .../art/shapes/beamngEarth/bne_signfont.png) is white glyphs on transparent black: the materials
(guidesigns.materials.json) alpha-test it and tint it white (bne_gsign_text, which glows at night like a
retroreflective legend) or black (bne_gsign_textk). The metrics (server/signfont.json) give every glyph its atlas
rectangle, its box from the pen position and its advance, all in cap heights (a sign's letter height is the height
of its capitals, as the MUTCD gives them), and the kerning of the pairs that have any.

The committed atlas was drawn with Overpass SemiBold (SIL Open Font License 1.1; Delve Withrington, Red Hat: a free
typeface after the FHWA's Highway Gothic, https://github.com/RedHatOfficial/Overpass), from Google Fonts'
ofl/overpass/Overpass[wght].ttf.
"""
import argparse
import json
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATLAS = os.path.join(ROOT, "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth",
                     "bne_signfont.png")
METRICS = os.path.join(ROOT, "server", "signfont.json")

CHARS = "".join(chr(c) for c in range(33, 127)) + "½¼¾éñ"
SIZE = (2048, 512)
PX = 112            # font size (px)
PAD = 6             # px of transparent border round each glyph (mipmaps)
WEIGHT = 600        # SemiBold: Highway Gothic's Series E(M) weight


def build(font_path, atlas=ATLAS, metrics=METRICS, weight=WEIGHT):
    font = ImageFont.truetype(font_path, PX)
    try:
        font.set_variation_by_axes([weight])
    except (OSError, AttributeError):       # (a static font: its own weight)
        pass
    cap_px = float(-font.getbbox("H", anchor="ls")[1])     # the baseline at 0: the top of "H" at -cap height
    img = Image.new("RGBA", SIZE, (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    x = y = PAD
    row_h = 0
    glyphs = {}
    for ch in CHARS:
        l, t, r, b = font.getbbox(ch, anchor="ls")          # box from the pen at the baseline (y down)
        w, h = r - l, b - t
        if x + w + 2 * PAD > SIZE[0]:
            x, y = PAD, y + row_h + 2 * PAD
            row_h = 0
        if y + h + 2 * PAD > SIZE[1]:
            raise SystemExit("atlas too small")
        draw.text((x + PAD - l, y + PAD - t), ch, font=font, fill=(255, 255, 255, 255), anchor="ls")
        u0, v0 = (x) / SIZE[0], (y) / SIZE[1]
        u1, v1 = (x + w + 2 * PAD) / SIZE[0], (y + h + 2 * PAD) / SIZE[1]
        glyphs[ch] = {"uv": [round(u0, 6), round(v0, 6), round(u1, 6), round(v1, 6)],
                      # the padded box from the pen: x0, y0 (bottom, up), x1, y1, in cap heights
                      "box": [round((l - PAD) / cap_px, 4), round((-b - PAD) / cap_px, 4),
                              round((r + PAD) / cap_px, 4), round((-t + PAD) / cap_px, 4)],
                      "adv": round(font.getlength(ch) / cap_px, 4)}
        x += w + 2 * PAD
        row_h = max(row_h, h)
    space = round(font.getlength(" ") / cap_px, 4)
    kern = {}
    pairs = [c for c in CHARS if c.isalnum() or c in ".,-'/"]
    for a in pairs:
        la = font.getlength(a)
        for b in pairs:
            k = font.getlength(a + b) - la - font.getlength(b)
            if abs(k) >= 0.5:
                kern[a + b] = round(k / cap_px, 4)
    img.save(atlas, optimize=True)
    with open(metrics, "w") as f:
        json.dump({"font": os.path.basename(font_path), "weight": weight, "space": space,
                   "lower": round(-font.getbbox("x", anchor="ls")[1] / cap_px, 4),
                   "glyphs": glyphs, "kern": kern}, f, separators=(",", ":"), sort_keys=True)
    return len(glyphs), len(kern)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--font", required=True, help="Overpass[wght].ttf (or another highway-style typeface)")
    ap.add_argument("--weight", type=int, default=WEIGHT)
    a = ap.parse_args()
    n, k = build(a.font, weight=a.weight)
    print(f"{n} glyphs, {k} kerning pairs -> {ATLAS}, {METRICS}")


if __name__ == "__main__":
    main()
