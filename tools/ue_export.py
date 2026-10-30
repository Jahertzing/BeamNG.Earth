#!/usr/bin/env python3
"""Export a piece of the world for Unreal: the road tiles and one terrain block, built by the world server's own builders
in one fixed frame (no anchors, no game, no downloads), as .glb files plus a manifest.

    .venv\\Scripts\\python tools\\ue_export.py --at 40.09,-104.9806 --km 1 --out C:/beamng-earth/Earth.UE/export/slice01

Every mesh the builders hand to cdae.write is caught there and written as glTF instead, so whatever the road tiles
contain comes along. Coordinates: frame metres (x east, y north, z up) in the manifest, glTF's own (x, z, -y) in the files.
"""
import argparse
import json
import os
import queue
import shutil
import struct
import sys
import time

os.environ.setdefault("BNE_REVISIT", "off")     # (a revisit hit would skip the cdae.write this tool catches)
os.environ.setdefault("BNGE_WORLD_DIR", "C:/beamng-earth/world")
os.environ.setdefault("BNGE_WORLD_CACHE", os.path.abspath(os.path.expanduser("~/.cache/beamng-earth")))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import numpy as np  # noqa: E402

FRAME = (40.0992, -104.9372)    # Frederick's centre: the one frame of the Unreal world
GROUND_LIFT_M = 0.025           # earthd.DETAIL_ROAD_LIFT: roads over the drawn ground
BLOCK = 1024                    # samples a side, 2 m each
PHOTO_PX = 4096                 # ground photo: 0.5 m a texel over the block
PHOTO_SQUARES = 4               # rendered this many squares a side and joined


def vertex_normals(P, T):
    """Area-weighted vertex normals, (0, 0, 1) where a vertex has no face."""
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    fn = np.cross(b - a, c - a)
    N = np.zeros_like(P)
    for k in range(3):
        np.add.at(N, T[:, k], fn)
    ln = np.linalg.norm(N, axis=1)
    N[ln == 0] = (0.0, 0.0, 1.0)
    ln[ln == 0] = 1.0
    return N / ln[:, None]


def to_gltf_axes(v):
    """Frame axes (x east, y north, z up) to glTF's (x, y up, z south)."""
    return np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1)


class Glb:
    """One .glb: buffers, accessors, a mesh of one primitive per material."""

    def __init__(self):
        self.bin = bytearray()
        self.views, self.accessors, self.materials, self.images, self.textures, self.primitives = [], [], [], [], [], []

    def blob(self, data, target=None):
        self.bin += b"\0" * (-len(self.bin) % 4)
        view = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            view["target"] = target
        self.views.append(view)
        self.bin += data
        return len(self.views) - 1

    def accessor(self, arr, component, kind, normalized=False, bounds=False, target=34962):
        acc = {"bufferView": self.blob(arr.tobytes(), target), "componentType": component, "type": kind,
               "count": len(arr)}
        if normalized:
            acc["normalized"] = True
        if bounds:
            acc["min"], acc["max"] = arr.min(axis=0).tolist(), arr.max(axis=0).tolist()
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def material(self, name, image=None):
        mat = {"name": name, "doubleSided": False,
               "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.9}}
        if image is not None:
            self.images.append({"bufferView": self.blob(image), "mimeType": "image/jpeg"})
            self.textures.append({"source": len(self.images) - 1})
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": len(self.textures) - 1}
        self.materials.append(mat)
        return len(self.materials) - 1

    def add(self, P, T, UV, names, M=None, colors=None, image=None):
        """Vertices shared by the primitives; one primitive per material used."""
        P = np.asarray(P, np.float64)
        T = np.asarray(T, np.int64).reshape(-1, 3)
        M = np.zeros(len(T), np.int64) if M is None else np.asarray(M, np.int64)
        attrs = {"POSITION": self.accessor(to_gltf_axes(P).astype(np.float32), 5126, "VEC3", bounds=True),
                 "NORMAL": self.accessor(to_gltf_axes(vertex_normals(P, T)).astype(np.float32), 5126, "VEC3")}
        if UV is not None:
            uv = np.array(UV, np.float32)
            uv[:, 1] = 1.0 - uv[:, 1]       # (Collada's v up, glTF's down)
            attrs["TEXCOORD_0"] = self.accessor(uv, 5126, "VEC2")
        if colors is not None:
            attrs["COLOR_0"] = self.accessor(np.asarray(colors, np.uint8), 5121, "VEC4", normalized=True)
        for m in np.unique(M):
            mat = self.material(names[int(m)], image)
            self.primitives.append({"attributes": attrs, "material": mat, "mode": 4,
                                    "indices": self.accessor(T[M == m].ravel().astype(np.uint32), 5125, "SCALAR",
                                                             target=34963)})

    def write(self, path, name, translation):
        doc = {"asset": {"version": "2.0", "generator": "BeamNG.Earth tools/ue_export.py"},
               "scene": 0, "scenes": [{"nodes": [0]}],
               "nodes": [{"name": name, "mesh": 0, "translation": [float(v) for v in translation]}],
               "meshes": [{"name": name, "primitives": self.primitives}],
               "materials": self.materials, "accessors": self.accessors, "bufferViews": self.views,
               "buffers": [{"byteLength": len(self.bin)}]}
        if self.images:
            doc["images"], doc["textures"] = self.images, self.textures
            doc["samplers"] = [{"magFilter": 9729, "minFilter": 9987}]
        js = json.dumps(doc, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        self.bin += b"\0" * (-len(self.bin) % 4)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin)))
            f.write(struct.pack("<I4s", len(js), b"JSON") + js)
            f.write(struct.pack("<I4s", len(self.bin), b"BIN\0") + bytes(self.bin))


class Catch:
    """cdae.write replaced: keeps what the builders would have written, by file name."""

    def __init__(self):
        self.shapes = {}

    def write(self, path, positions, tris, **kw):
        self.shapes[os.path.basename(path)] = (np.asarray(positions, np.float64), np.asarray(tris, np.int64), kw)
        return 0


def kind_of(shape):
    """roads_<a>_... file names to their kind: road, deck, bld, bldcol, walk, sign ..."""
    return shape.split("_", 1)[0]


def tile_shapes(tile_json, catch):
    """(shape file, tile origin) of every shape a tile's JSON announces. Colliders in pieces share their tile's origin."""
    with open(tile_json, encoding="utf-8") as f:
        data = json.load(f)
    out = []

    def walk(v):
        if isinstance(v, dict):
            if "shape" in v and "pos" in v:
                out.append((os.path.basename(v["shape"]), v["pos"]))
            for w in v.values():
                walk(w)
        elif isinstance(v, list):
            for w in v:
                walk(w)
    walk(data)
    props = len(data.get("props", {}).get("items", []))
    return out, props, len(data.get("park", ()))


def write_shape(out_dir, name, P, T, kw, origin):
    mats = kw.get("material", "bngEarthDae")
    mats = [mats] if isinstance(mats, str) else list(mats)
    M = kw.get("tri_materials")
    g = Glb()
    g.add(P, T, kw.get("uvs"), mats, M, kw.get("colors"))
    base = os.path.splitext(name)[0]
    g.write(os.path.join(out_dir, base + ".glb"), base, (0.0, 0.0, 0.0))    # (placed from the manifest's origin)
    return {"file": base + ".glb", "kind": kind_of(name), "collision_only": bool(kw.get("collision_only")),
            "origin": [float(v) for v in origin], "tris": int(len(T)), "verts": int(len(P)), "materials": mats}


def export_ground(out_dir, u, cx, cy, photo):
    """The block's graded heights (2 m lattice) as one mesh with the photo draped over it."""
    from PIL import Image
    import io
    n = u.shape[0]
    x0, y0 = cx - BLOCK, cy - BLOCK
    xs, ys = np.arange(n) * 2.0, np.arange(n) * 2.0
    X, Y = np.meshgrid(xs, ys)
    P = np.stack([X.ravel(), Y.ravel(), u.ravel()], axis=1)
    i = np.arange(n - 1)
    a = (i[None, :] + (i[:, None]) * n).ravel()
    T = np.stack([a, a + 1, a + n, a + 1, a + n + 1, a + n], axis=1).reshape(-1, 3)
    span = (n - 1) * 2.0
    UV = np.stack([X.ravel() / span, Y.ravel() / span], axis=1)      # (Collada's v up: north at the image's top)
    buf = io.BytesIO()
    Image.fromarray(photo).save(buf, "JPEG", quality=92)
    g = Glb()
    g.add(P, T, UV, ["ground_photo"], image=buf.getvalue())
    g.write(os.path.join(out_dir, "ground.glb"), "ground", (0.0, 0.0, 0.0))
    return {"file": "ground.glb", "kind": "ground", "collision_only": False, "origin": [x0, y0, 0.0],
            "tris": int(len(T)), "verts": int(len(P)), "materials": ["ground_photo"]}


def check_roads(entries, catch, u, cx, cy):
    """Road surface heights against the ground under them: the export's own sanity check (m)."""
    x0, y0 = cx - BLOCK, cy - BLOCK
    gaps = []
    for e in entries:
        if e["kind"] != "road":
            continue
        P = catch.shapes[e["file"][:-4] + ".cdae"][0]
        gx = np.clip(((P[:, 0] + e["origin"][0] - x0) / 2.0).astype(int), 0, u.shape[1] - 1)
        gy = np.clip(((P[:, 1] + e["origin"][1] - y0) / 2.0).astype(int), 0, u.shape[0] - 1)
        gaps.append(P[:, 2] + e["origin"][2] - u[gy, gx])
    if not gaps:
        return None
    g = np.concatenate(gaps)
    return {"vertices": int(len(g)), "median": float(np.median(g)), "p1": float(np.percentile(g, 1)),
            "p99": float(np.percentile(g, 99))}


def preview(path, entries, catch, photo, cx, cy):
    """A top-down check picture: the ground photo with the road and building triangles' outlines over it."""
    from PIL import Image, ImageDraw
    im = Image.fromarray(photo).convert("RGB").resize((2048, 2048))
    d = ImageDraw.Draw(im, "RGBA")
    x0, y0 = cx - BLOCK, cy - BLOCK
    colours = {"road": (255, 60, 40, 90), "bld": (60, 160, 255, 110), "deck": (255, 220, 40, 120)}
    for e in entries:
        c = colours.get(e["kind"])
        name = e["file"][:-4] + ".cdae"
        if c is None or name not in catch.shapes:
            continue
        P, T, _ = catch.shapes[name]
        px = P[:, 0] + e["origin"][0] - x0
        py = 2048 - (P[:, 1] + e["origin"][1] - y0)
        for t in T[:: max(1, len(T) // 4000)]:
            d.polygon([(px[t[0]], py[t[0]]), (px[t[1]], py[t[1]]), (px[t[2]], py[t[2]])], outline=c)
    im.save(path, quality=90)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--at", default="40.09,-104.9806", help="lat,lon of the piece's centre")
    ap.add_argument("--km", type=float, default=1.0, help="side of the square of road tiles")
    ap.add_argument("--out", default="C:/beamng-earth/Earth.UE/export/slice01")
    ap.add_argument("--start", default="-3668,-1480", help="the car's start, frame x,y m (default: I-25 northbound)")
    ap.add_argument("--cache-dir", default=os.environ["BNGE_WORLD_CACHE"])
    a = ap.parse_args()
    a.cache_dir = os.path.abspath(a.cache_dir)      # (cache.resolve matches the world folder by its native prefix)
    t_start = time.perf_counter()
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

    roadsmod.ROADS_IN_GROUND = False    # (real asphalt in the road mesh: Unreal draws it, nothing is painted into the photo)
    blockworker.init(a.cache_dir, True, True, True, True, True, None, 30, True, True)
    lat0, lon0 = FRAME
    frame = geodesy.EnuFrame(lat0, lon0)
    lat, lon = (float(v) for v in a.at.split(","))
    xc, yc, _ = frame.geodetic_to_enu(lat, lon)
    xc, yc = float(xc), float(yc)
    print(f"frame {FRAME}; piece at ({xc:.0f}, {yc:.0f}) m, {a.km} km", flush=True)

    out_dir = a.out
    scratch = os.path.join(out_dir, "_tiles")
    os.makedirs(scratch, exist_ok=True)
    catch = Catch()
    cdae_write, cdae.write = cdae.write, catch.write

    heights = {}
    generate = heightmap.generate

    def caught_generate(*args, **kw):
        info = generate(*args, **kw)
        heights["u"] = kw["keep"]["u"]
        return info
    heightmap.generate = caught_generate

    zoom = demmod.zoom_for(lat0, 2.5)
    q = earthd.survey_q(frame, blockworker._s.dem, 1024)

    half = a.km * 500.0
    t0x, t1x = int(np.floor((xc - half) / roadsmod.TILE)), int(np.floor((xc + half) / roadsmod.TILE))
    t0y, t1y = int(np.floor((yc - half) / roadsmod.TILE)), int(np.floor((yc + half) / roadsmod.TILE))
    tiles = [(tx, ty) for tx in range(t0x, t1x + 1) for ty in range(t0y, t1y + 1)]
    print(f"{len(tiles)} road tiles", flush=True)

    entries, n_props, n_park, failed = [], 0, 0, []
    for k, (tx, ty) in enumerate(tiles):
        t0 = time.perf_counter()
        try:
            blockworker.roads_tile(1, lat0, lon0, zoom, tx, ty, scratch, "/temp/x", True, GROUND_LIFT_M, True, False,
                                   True, q, False, 0.0)
        except Exception as e:      # reported, the rest goes on
            failed.append([tx, ty, f"{type(e).__name__}: {e}"])
            print(f"tile {tx},{ty}: {type(e).__name__}: {e}", flush=True)
            continue
        shapes, props, park = tile_shapes(os.path.join(scratch, f"roads_1_{tx}_{ty}.json"), catch)
        n_props += props
        n_park += park
        for name, origin in shapes:
            if name not in catch.shapes:
                continue
            P, T, kw = catch.shapes[name]
            if len(T):
                entries.append(write_shape(out_dir, name, P, T, kw, origin))
        print(f"tile {k + 1}/{len(tiles)} ({tx},{ty}): {time.perf_counter() - t0:.1f} s", flush=True)

    grid = heightmap.GRID
    cx, cy = round(xc / grid) * grid, round(yc / grid) * grid
    t0 = time.perf_counter()
    blockworker.build(1, lat0, lon0, zoom, q, cx, cy, BLOCK, scratch, "hm_0.png")
    print(f"ground block ({cx:.0f}, {cy:.0f}): {time.perf_counter() - t0:.1f} s", flush=True)
    t0 = time.perf_counter()
    im = imagerymod.Imagery(os.path.join(a.cache_dir, "imagery"), imagerymod.DEFAULT_CHAIN,
                            fetcher=netfetch.Fetcher(offline=True))
    photo = np.zeros((PHOTO_PX, PHOTO_PX, 3), np.uint8)
    used = {}
    step = PHOTO_PX // PHOTO_SQUARES     # (one render of the whole block gave up the county photos for the coarse base)
    for j in range(PHOTO_SQUARES):
        for i in range(PHOTO_SQUARES):
            m = 2.0 * BLOCK / PHOTO_SQUARES
            rgb, got = im.render(frame, cx - BLOCK + i * m, cy - BLOCK + j * m, m, step, set())
            photo[PHOTO_PX - (j + 1) * step:PHOTO_PX - j * step, i * step:(i + 1) * step] = np.asarray(rgb, np.uint8)
            for s, n in got.items():
                used[s] = used.get(s, 0) + n
    print(f"photo: {time.perf_counter() - t0:.1f} s, sources {used}", flush=True)

    u = np.asarray(heights["u"])
    entries.append(export_ground(out_dir, u, cx, cy, photo))
    gaps = check_roads(entries, catch, u, cx, cy)
    start = None
    for e in entries:                       # the road point nearest the piece's centre: where the car starts
        if e["kind"] == "road":
            P = catch.shapes[e["file"][:-4] + ".cdae"][0] + np.asarray(e["origin"])
            sx, sy = (float(v) for v in a.start.split(","))
            d = np.hypot(P[:, 0] - sx, P[:, 1] - sy)
            k = int(np.argmin(d))
            if start is None or d[k] < start[0]:
                start = (float(d[k]), [sx, sy, float(P[k, 2])])
    preview(os.path.join(out_dir, "check_topdown.jpg"), entries, catch, photo, cx, cy)
    cdae.write = cdae_write
    shutil.rmtree(scratch, ignore_errors=True)

    by_kind = {}
    for e in entries:
        s = by_kind.setdefault(e["kind"], {"meshes": 0, "tris": 0})
        s["meshes"] += 1
        s["tris"] += e["tris"]
    manifest = {"frame": {"lat": lat0, "lon": lon0}, "units": "m", "axes": "x east, y north, z up",
                "piece": {"at": [lat, lon], "xy": [xc, yc], "km": a.km}, "ground": {"cx": cx, "cy": cy},
                "start": start[1] if start else [xc, yc, float(np.median(u))],
                "by_kind": by_kind, "props_skipped": n_props, "parking_spots": n_park, "failed_tiles": failed,
                "road_minus_ground_m": gaps, "photo_sources": sorted(str(s) for s in used), "meshes": entries,
                "seconds": round(time.perf_counter() - t_start, 1)}
    old = os.path.join(out_dir, "manifest.json")
    if os.path.exists(old):                 # (ue_export_far.py's mesh stays)
        with open(old) as f:
            before = json.load(f)
        manifest["meshes"] += [e for e in before["meshes"] if e["kind"] == "far"]
        if "far" in before:
            manifest["far"] = before["far"]
    with open(old, "w") as f:
        json.dump(manifest, f, indent=1)
    print(json.dumps({k: manifest[k] for k in ("by_kind", "props_skipped", "failed_tiles", "road_minus_ground_m",
                                               "photo_sources", "seconds")}, indent=1), flush=True)


if __name__ == "__main__":
    main()
