#!/usr/bin/env python3
"""Grade preview: shaders/beamngEarth/grade.cs.hlsl in numpy, on an ungraded screenshot (the look probe's
"ungraded" variant), so the colorGrade presets can be tuned between game sessions.

    python3 tools/grade_preview.py IN.jpg --out OUT.jpg [--preset colorado,natural,vivid] [--set misc.z=0.5 ...]

Each preset's constants come from grade.lua itself (luajit), so the preview shows what the game would; --set
overrides one component (balance / tone / shadows / highlights / misc, x y z w) to try a value before writing it
into a preset. Several presets: one image, side by side, labelled, the input first.
"""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
MOD_LUA = HERE.parent / "mod" / "beamng_earth" / "lua" / "ge" / "extensions"
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
KEYS = ("balance", "tone", "shadows", "highlights", "misc")


def presets():
    code = (f"package.path = '{MOD_LUA.as_posix()}/?.lua;' .. package.path "
            "local g = require('beamngEarth/grade') local out = {} "
            "for name, p in pairs(g.PRESETS) do local t = {} "
            "for _, k in ipairs({'balance','tone','shadows','highlights','misc'}) do "
            "t[#t + 1] = '\"' .. k .. '\":[' .. table.concat(p[k], ',') .. ']' end "
            "out[#out + 1] = '\"' .. name .. '\":{' .. table.concat(t, ',') .. '}' end "
            "io.write('{' .. table.concat(out, ',') .. '}')")
    return json.loads(subprocess.run(["luajit", "-e", code], capture_output=True, text=True, check=True).stdout)


def srgb_to_linear(c):
    c = np.clip(c, 0, 1)
    return np.where(c >= 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)


def linear_to_srgb(c):
    c = np.maximum(c, 0)
    return np.where(c >= 0.0031308, 1.055 * c ** (1 / 2.4) - 0.055, c * 12.92)


def contrast(x, pivot, k, k_lo):
    x = np.clip(x, 0, 1)
    lo = pivot * (x / max(pivot, 1e-4)) ** k_lo
    hi = 1 - (1 - pivot) * ((1 - x) / max(1 - pivot, 1e-4)) ** k
    return np.where(x < pivot, lo, hi)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def grade(img, p):
    """img: HxWx3 float 0..1 (sRGB encoded) -> graded, as grade.cs.hlsl."""
    bal, tone, sh, hl, misc = (np.array(p[k], dtype=np.float32) for k in KEYS)
    lin = srgb_to_linear(img)
    before = lin @ LUMA
    lin = lin * bal[:3]
    after = lin @ LUMA
    lin = lin * np.where(after > 1e-6, before / np.maximum(after, 1e-6), 1.0)[..., None]
    lum = (lin @ LUMA)[..., None]
    mx, mn = lin.max(-1, keepdims=True), lin.min(-1, keepdims=True)
    sat = np.where(mx > 1e-5, (mx - mn) / np.maximum(mx, 1e-5), 0)
    s = tone[2] * (1 + tone[3] * (1 - sat))
    lin = np.maximum(lum + (lin - lum) * s, 0)
    c = linear_to_srgb(lin)
    k_lo = 1 + (tone[0] - 1) * np.clip(misc[2], 0, 1)
    c = contrast(c, tone[1], tone[0], k_lo)
    y = (c @ LUMA)[..., None]
    w_s = sh[3] * (1 - smoothstep(0, 0.55, y))
    w_h = hl[3] * smoothstep(0.45, 1, y)
    c = c + (sh[:3] - 0.5) * w_s + (hl[:3] - 0.5) * w_h
    if misc[0] > 0:
        h, w = img.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.stack([(xx + 0.5) / w - 0.5, (yy + 0.5) / h - 0.5], -1)
        d[..., 0] *= w / h
        r = np.linalg.norm(d, axis=-1) / max(misc[1], 1e-3)
        c = c * (1 - misc[0] * smoothstep(0, 1, np.clip(r * r, 0, 1)))[..., None]
    c = np.clip(c, 0, 1)
    return img + (c - img) * np.clip(bal[3], 0, 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--out", required=True)
    ap.add_argument("--preset", default="colorado")
    ap.add_argument("--set", action="append", default=[], help="key.x=value (x y z w), e.g. tone.x=1.1")
    ap.add_argument("--width", type=int, default=1100, help="each panel's width")
    a = ap.parse_args(argv)
    src = Image.open(a.input).convert("RGB")
    src = src.resize((a.width, round(src.height * a.width / src.width)), Image.LANCZOS)
    img = np.asarray(src, dtype=np.float32) / 255
    all_p = presets()
    panels = [("ungraded", src)]
    for name in a.preset.split(","):
        p = {k: list(v) for k, v in all_p[name].items()}
        for s in a.set:
            key, val = s.split("=")
            k, comp = key.split(".")
            p[k]["xyzw".index(comp)] = float(val)
        out = (grade(img, p) * 255 + 0.5).astype(np.uint8)
        panels.append((name + (" " + " ".join(a.set) if a.set else ""), Image.fromarray(out)))
    W, H = src.width, src.height
    sheet = Image.new("RGB", (W * len(panels), H + 22), (17, 17, 17))
    draw = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(panels):
        sheet.paste(im, (i * W, 22))
        draw.text((i * W + 6, 4), label, fill=(230, 230, 230))
    sheet.save(a.out, quality=90)
    print(a.out)


if __name__ == "__main__":
    main()
