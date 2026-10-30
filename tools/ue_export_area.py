#!/usr/bin/env python3
"""Export a whole area for Unreal, cell by cell: each 2048 m cell is one ground block with its photo, and its 64 road
tiles merged into one .glb per kind (road, bld, deck, ...), so Unreal sees a few files a cell and not one for every
tile. Cells run in processes at low priority, and a finished cell is skipped on a rerun.

    .venv\\Scripts\\python tools\\ue_export_area.py --km 20 --jobs 5 --out C:/beamng-earth/Earth.UE/export/frederick
    .venv\\Scripts\\python tools\\ue_export_area.py --cells -2,-1;-2,0 ...     (only these cells)

The manifest has the shape tools/ue_export.py writes, so scripts/import_slice.py reads either.
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue_export  # noqa: E402  (sets the world folder up and puts server/ on the path)

import numpy as np  # noqa: E402

CELL = 2048.0
TILES = int(CELL / 256.0)
_w = {}


def init_worker(cache_dir, fit=None):
    """One blockworker state per process; fit: tools/ue_fit.py's corrections, applied to the map."""
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    import blockworker
    import cdae
    import dem as demmod
    import earthd
    import geodesy
    import heightmap
    import imagery as imagerymod
    import netfetch
    import roads as roadsmod
    roadsmod.ROADS_IN_GROUND = False
    blockworker.init(cache_dir, True, True, True, True, True, None, 40, True, True)
    if fit:
        import ue_fit
        ue_fit.install(fit)
    frame = geodesy.EnuFrame(*ue_export.FRAME)
    catch = ue_export.Catch()
    cdae.write = catch.write
    heights = {}
    generate = heightmap.generate

    def caught_generate(*args, **kw):
        info = generate(*args, **kw)
        heights["u"] = kw["keep"]["u"]
        heights["elev"] = args[1]
        return info
    heightmap.generate = caught_generate
    _w.update(blockworker=blockworker, frame=frame, catch=catch, heights=heights, heightmap=heightmap, roads=roadsmod,
              zoom=demmod.zoom_for(ue_export.FRAME[0], 2.5), q=earthd.survey_q(frame, blockworker._s.dem, 1024),
              imagery=imagerymod.Imagery(os.path.join(cache_dir, "imagery"), imagerymod.DEFAULT_CHAIN,
                                         fetcher=netfetch.Fetcher(offline=True)))


def merged(shapes):
    """One mesh from [(name, P, T, kw, origin - cell origin)]: positions in the cell's frame, the materials' union."""
    names, P, T, UV, C, M = [], [], [], [], [], []
    base = 0
    for _name, p, t, kw, off in shapes:
        mats = kw.get("material", "bngEarthDae")
        mats = [mats] if isinstance(mats, str) else list(mats)
        index = []
        for m in mats:
            if m not in names:
                names.append(m)
            index.append(names.index(m))
        tm = kw.get("tri_materials")
        tm = np.zeros(len(t), np.int64) if tm is None else np.asarray(tm, np.int64)
        uv = kw.get("uvs")
        col = kw.get("colors")
        P.append(p + off)
        T.append(t + base)
        UV.append(np.zeros((len(p), 2)) if uv is None else np.asarray(uv, np.float64))
        C.append(np.full((len(p), 4), 255, np.uint8) if col is None else np.asarray(col, np.uint8))
        M.append(np.asarray(index, np.int64)[tm])
        base += len(p)
    return np.concatenate(P), np.concatenate(T), np.concatenate(UV), np.concatenate(C), names, np.concatenate(M)


def full_heights(frame, heightmap, elev, u, x0, y0):
    """The 1024 x 1024 block's heights with the next row and column added, so neighbouring cells meet with no gap."""
    n = u.shape[0]
    xs = x0 + np.arange(n + 1) * 2.0
    ys = y0 + np.arange(n + 1) * 2.0
    out = np.empty((n + 1, n + 1))
    out[:n, :n] = u
    out[:, n] = heightmap.surface_u(frame, elev, xs[-1:], ys)[:, 0]
    out[n, :] = heightmap.surface_u(frame, elev, xs, ys[-1:])[0, :]
    return out


def export_cell(job):
    i, j, out_dir = job
    t0 = time.perf_counter()
    done_mark = os.path.join(out_dir, "cells", f"{i}_{j}.json")
    if os.path.exists(done_mark):
        with open(done_mark) as f:
            return json.load(f)
    b, catch, heights = _w["blockworker"], _w["catch"], _w["heights"]
    lat0, lon0 = ue_export.FRAME
    x0, y0 = i * CELL, j * CELL
    cx, cy = x0 + CELL / 2, y0 + CELL / 2
    scratch = os.path.join(out_dir, "_tiles", f"{i}_{j}")
    os.makedirs(scratch, exist_ok=True)
    catch.shapes.clear()
    groups, failed, props = {}, [], 0
    for a in range(TILES):
        for c in range(TILES):
            tx, ty = i * TILES + a, j * TILES + c
            try:
                b.roads_tile(1, lat0, lon0, _w["zoom"], tx, ty, scratch, "/temp/x", True, ue_export.GROUND_LIFT_M, True,
                             False, True, _w["q"], False, 0.0)
                shapes, n_props, _ = ue_export.tile_shapes(os.path.join(scratch, f"roads_1_{tx}_{ty}.json"), catch)
            except Exception as e:      # reported, the rest goes on
                failed.append([tx, ty, f"{type(e).__name__}: {e}"])
                continue
            props += n_props
            for name, origin in shapes:
                got = catch.shapes.get(name)
                if got is None or not len(got[1]):
                    continue
                off = np.array([origin[0] - x0, origin[1] - y0, origin[2]])
                groups.setdefault(ue_export.kind_of(name), []).append((name, got[0], got[1], got[2], off))
    entries = []
    for kind, shapes in groups.items():
        P, T, UV, C, names, M = merged(shapes)
        name = f"{kind}_{i}_{j}"
        g = ue_export.Glb()
        g.add(P, T, UV, names, M, C)
        g.write(os.path.join(out_dir, name + ".glb"), name, (0.0, 0.0, 0.0))
        entries.append({"file": name + ".glb", "kind": kind, "collision_only": bool(shapes[0][3].get("collision_only")),
                        "origin": [x0, y0, 0.0], "tris": int(len(T)), "verts": int(len(P)), "materials": names})

    b.build(1, lat0, lon0, _w["zoom"], _w["q"], cx, cy, ue_export.BLOCK, scratch, "hm.png")
    u = full_heights(_w["frame"], _w["heightmap"], heights["elev"], np.asarray(heights["u"]), x0, y0)
    photo = np.zeros((ue_export.PHOTO_PX, ue_export.PHOTO_PX, 3), np.uint8)
    step = ue_export.PHOTO_PX // ue_export.PHOTO_SQUARES
    m = CELL / ue_export.PHOTO_SQUARES
    for c in range(ue_export.PHOTO_SQUARES):
        for a in range(ue_export.PHOTO_SQUARES):
            rgb, _ = _w["imagery"].render(_w["frame"], x0 + a * m, y0 + c * m, m, step, set())
            photo[ue_export.PHOTO_PX - (c + 1) * step:ue_export.PHOTO_PX - c * step, a * step:(a + 1) * step] = np.asarray(rgb, np.uint8)
    entries.append(ground_cell(out_dir, i, j, u, x0, y0, photo))
    shutil.rmtree(scratch, ignore_errors=True)
    result = {"cell": [i, j], "entries": entries, "failed": failed, "props": props,
              "seconds": round(time.perf_counter() - t0, 1)}
    os.makedirs(os.path.dirname(done_mark), exist_ok=True)
    with open(done_mark, "w") as f:
        json.dump(result, f)
    return result


def ground_cell(out_dir, i, j, u, x0, y0, photo):
    from PIL import Image
    import io
    n = u.shape[0]
    X, Y = np.meshgrid(np.arange(n) * 2.0, np.arange(n) * 2.0)
    P = np.stack([X.ravel(), Y.ravel(), u.ravel()], axis=1)
    k = np.arange(n - 1)
    a = (k[None, :] + k[:, None] * n).ravel()
    T = np.stack([a, a + 1, a + n, a + 1, a + n + 1, a + n], axis=1).reshape(-1, 3)
    span = (n - 1) * 2.0
    UV = np.stack([X.ravel() / span, Y.ravel() / span], axis=1)
    buf = io.BytesIO()
    Image.fromarray(photo).save(buf, "JPEG", quality=92)
    name = f"ground_{i}_{j}"
    g = ue_export.Glb()
    g.add(P, T, UV, ["ground_photo"], image=buf.getvalue())
    g.write(os.path.join(out_dir, name + ".glb"), name, (0.0, 0.0, 0.0))
    return {"file": name + ".glb", "kind": "ground", "collision_only": False, "origin": [x0, y0, 0.0], "tris": int(len(T)),
            "verts": int(len(P)), "materials": ["ground_photo"]}


def write_manifest(out_dir, results, km):
    entries = [e for r in results for e in r["entries"]]
    by_kind = {}
    for e in entries:
        s = by_kind.setdefault(e["kind"], {"meshes": 0, "tris": 0})
        s["meshes"] += 1
        s["tris"] += e["tris"]
    path = os.path.join(out_dir, "manifest.json")
    far = []
    extra = {}
    if os.path.exists(path):
        with open(path) as f:
            before = json.load(f)
        far = [e for e in before["meshes"] if e["kind"] == "far"]
        if "far" in before:
            extra["far"] = before["far"]
    manifest = {"frame": {"lat": ue_export.FRAME[0], "lon": ue_export.FRAME[1]}, "units": "m", "axes": "x east, y north, z up",
                "piece": {"at": list(ue_export.FRAME), "xy": [-3668.0, -1480.0], "km": km},
                "ground": {"cx": 0.0, "cy": 0.0, "half": km * 512.0}, "start": [-3668.0, -1480.0, 1522.115],
                "by_kind": by_kind, "props_skipped": sum(r["props"] for r in results),
                "failed_tiles": [t for r in results for t in r["failed"]], "meshes": entries + far}
    manifest.update(extra)
    with open(path, "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--km", type=float, default=20.0, help="side of the square round the frame's origin")
    ap.add_argument("--jobs", type=int, default=5)
    ap.add_argument("--cells", default=None, help="i,j;i,j: only these cells")
    ap.add_argument("--out", default="C:/beamng-earth/Earth.UE/export/frederick")
    ap.add_argument("--cache-dir", default=os.environ["BNGE_WORLD_CACHE"])
    ap.add_argument("--fit", default=None, help="corrections file (tools/ue_fit.py)")
    a = ap.parse_args()
    out_dir = a.out
    os.makedirs(out_dir, exist_ok=True)
    if a.cells:
        cells = [tuple(int(v) for v in c.split(",")) for c in a.cells.split(";")]
    else:
        n = int(round(a.km * 1000.0 / CELL / 2.0))
        cells = [(i, j) for j in range(-n, n) for i in range(-n, n)]
    # nearest the car first: an interrupted run still has the part that matters
    cells.sort(key=lambda c: abs(c[0] * CELL + 1024 + 3668) + abs(c[1] * CELL + 1024 + 1480))
    t0 = time.perf_counter()
    results = []
    with concurrent.futures.ProcessPoolExecutor(a.jobs, initializer=init_worker, initargs=(os.path.abspath(a.cache_dir), a.fit)) as pool:
        futures = {pool.submit(export_cell, (i, j, out_dir)): (i, j) for i, j in cells}
        for k, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            cell = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                print(f"cell {cell}: {type(e).__name__}: {e}", flush=True)
                continue
            results.append(r)
            print(f"{k}/{len(cells)} cell {cell}: {r['seconds']:.0f} s, {len(r['entries'])} meshes, "
                  f"{len(r['failed'])} failed tiles, {time.perf_counter() - t0:.0f} s in", flush=True)
            write_manifest(out_dir, results, a.km)
    m = write_manifest(out_dir, results, a.km)
    shutil.rmtree(os.path.join(out_dir, "_tiles"), ignore_errors=True)
    print(json.dumps({k: m[k] for k in ("by_kind", "props_skipped")}), "failed", len(m["failed_tiles"]),
          f"{time.perf_counter() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
