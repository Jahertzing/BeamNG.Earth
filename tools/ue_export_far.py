#!/usr/bin/env python3
"""The far terrain for Unreal: one mesh of the real ground out to --km around the Frederick frame, with a photo over it,
added to a tools/ue_export.py folder. Heights are the DEM's ENU z in the fixed frame, so Earth's curvature comes along.

    .venv\\Scripts\\python tools\\ue_export_far.py --out C:/beamng-earth/Earth.UE/export/slice01

Fetches what the world folder lacks (elevation, USGS and EOX imagery), at the sources' usual limits.
"""
import argparse
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402  (sets the world folder up and puts server/ on the path)

import numpy as np  # noqa: E402

HOLE_MARGIN_M = 150.0       # the far mesh runs this far under the near ground's edge ...
SINK_M = 3.0                # ... and sinks this much near it, so the two never fight
SINK_REACH_M = 4000.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="C:/beamng-earth/Earth.UE/export/slice01")
    ap.add_argument("--km", type=float, default=130.0, help="half side of the far square")
    ap.add_argument("--step", type=float, default=100.0, help="m between vertices")
    ap.add_argument("--photo-px", type=int, default=8192)
    ap.add_argument("--cache-dir", default=os.environ["BNGE_WORLD_CACHE"])
    a = ap.parse_args()
    t0 = time.perf_counter()
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    import dem as demmod
    import geodesy
    import heightmap
    import imagery as imagerymod
    import netfetch
    from PIL import Image

    with open(os.path.join(a.out, "manifest.json")) as f:
        manifest = json.load(f)
    lat0, lon0 = manifest["frame"]["lat"], manifest["frame"]["lon"]
    cx, cy = manifest["ground"]["cx"], manifest["ground"]["cy"]
    frame = geodesy.EnuFrame(lat0, lon0)
    cache_dir = os.path.abspath(a.cache_dir)
    net = netfetch.Fetcher(offline=False)
    dem = demmod.Dem(os.path.join(cache_dir, "dem"), fetcher=net)
    half = a.km * 1000.0
    n = int(2 * half / a.step) + 1
    xs = cx - half + np.arange(n) * a.step
    ys = cy - half + np.arange(n) * a.step
    zoom = demmod.zoom_for(lat0, a.step)
    print(f"{n}x{n} vertices, DEM zoom {zoom}", flush=True)
    u = heightmap.surface_u(frame, lambda la, lo: dem.sample(la, lo, zoom), xs, ys)
    print(f"heights {u.min():.0f}..{u.max():.0f} m, {time.perf_counter() - t0:.0f} s", flush=True)

    X, Y = np.meshgrid(xs, ys)
    block = float(manifest["ground"].get("half", ue_export.BLOCK))
    d = np.maximum(np.abs(X - cx), np.abs(Y - cy)) - block      # m outside the near ground's square
    u = u - SINK_M * np.clip(1.0 - d / SINK_REACH_M, 0.0, 1.0)
    P = np.stack([(X - cx).ravel(), (Y - cy).ravel(), u.ravel()], axis=1)
    i = np.arange(n - 1)
    a0 = (i[None, :] + i[:, None] * n).ravel()
    T = np.stack([a0, a0 + 1, a0 + n, a0 + 1, a0 + n + 1, a0 + n], axis=1).reshape(-1, 3)
    corner = np.minimum.reduce([d.ravel()[T[:, k]] for k in range(3)])
    far_out = np.maximum.reduce([d.ravel()[T[:, k]] for k in range(3)])
    T = T[(far_out > -HOLE_MARGIN_M) | (corner > 0)]            # the hole under the near ground
    span = (n - 1) * a.step
    UV = np.stack([((X - xs[0]) / span).ravel(), ((Y - ys[0]) / span).ravel()], axis=1)

    im = imagerymod.Imagery(os.path.join(cache_dir, "imagery"), ("usgs", "eox2023"), fetcher=net)
    squares = a.photo_px // 512
    step = a.photo_px // squares
    photo = np.zeros((a.photo_px, a.photo_px, 3), np.uint8)
    m = 2.0 * half / squares
    failed = 0
    for j in range(squares):
        for k in range(squares):
            try:
                rgb, _ = im.render(frame, xs[0] + k * m, ys[0] + j * m, m, step, set())
                photo[a.photo_px - (j + 1) * step:a.photo_px - j * step, k * step:(k + 1) * step] = np.asarray(rgb, np.uint8)
            except imagerymod.ImageryError:
                failed += 1
        print(f"photo row {j + 1}/{squares}, {time.perf_counter() - t0:.0f} s", flush=True)
    buf = io.BytesIO()
    Image.fromarray(photo).save(buf, "JPEG", quality=90)
    g = ue_export.Glb()
    g.add(P, T, UV, ["far_photo"], image=buf.getvalue())
    g.write(os.path.join(a.out, "far.glb"), "far", (0.0, 0.0, 0.0))
    Image.fromarray(photo).resize((1600, 1600)).save(os.path.join(a.out, "check_far.jpg"), quality=88)

    manifest["meshes"] = [e for e in manifest["meshes"] if e["kind"] != "far"]
    manifest["meshes"].append({"file": "far.glb", "kind": "far", "collision_only": False, "origin": [cx, cy, 0.0],
                               "tris": int(len(T)), "verts": int(len(P)), "materials": ["far_photo"]})
    manifest["far"] = {"km": a.km, "step_m": a.step, "dem_zoom": zoom, "photo_px": a.photo_px, "failed_squares": failed,
                       "height_range_m": [float(u.min()), float(u.max())]}
    with open(os.path.join(a.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(json.dumps(manifest["far"]), f"tris {len(T)}, {time.perf_counter() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
