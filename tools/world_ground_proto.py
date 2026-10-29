#!/usr/bin/env python3
"""Prototype: the physics block as a resample of world-space graded ground tiles (2026-10-28).

Today every anchor grades its own blocks: blockworker.build's elev(lat, lon) runs the DEM through a grading.Grader made
in that anchor's frame, which plans every road cell again for that frame. The proposal (the "Ground Pipeline Plan"
doc, stage 1): grade each place once in a world tile (its own small ENU frame on a fixed geodetic grid), store the
graded elevation as a raster, and let any anchor's block resample it through the same elev(lat, lon) interface.

This measures, at one place and two anchors that both see it:
  direct   today's build for each anchor (a fresh Grader in the anchor's frame, plans cold);
  world    one world tile graded once (a Grader in the tile's frame), then each anchor's block resampled from it;
  raw      the same with the ungraded DEM (the interpolation error alone, no grading).
and prints times and the height differences against direct (all samples, and the road cores alone).

    python tools/world_ground_proto.py [--lat 39.7255 --lon -105.0055] [--res 1.0] [--offline]

Elevation and map data come from the cache and the world folder (apppaths); --offline uses nothing else.
"""
import argparse
import os
import queue
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

ANCHORS = {"a3": (39.71, -105.0), "a2": (39.84, -104.98)}  # the route's pinned anchors 3 and 2 (world/bake)
SIZE = 512                      # the block (detail mode: 512² at 2 m)
TILE_DEG = 0.01                 # world tile grid (tile frames at the cell centres)


def bilinear(raster, x0, y0, res, x, y):
    """raster [j, i] at (x0 + i·res, y0 + j·res), sampled at (x, y)."""
    fx = (x - x0) / res
    fy = (y - y0) / res
    i = np.clip(np.floor(fx).astype(np.int64), 0, raster.shape[1] - 2)
    j = np.clip(np.floor(fy).astype(np.int64), 0, raster.shape[0] - 2)
    tx = np.clip(fx - i, 0.0, 1.0)
    ty = np.clip(fy - j, 0.0, 1.0)
    a = raster[j, i] * (1 - tx) + raster[j, i + 1] * tx
    b = raster[j + 1, i] * (1 - tx) + raster[j + 1, i + 1] * tx
    return a * (1 - ty) + b * ty


class WorldTile:
    """Graded elevation on a grid of one tile frame: elev(lat, lon) by bilinear lookup."""

    def __init__(self, frame, half, res, elev_fn):
        import geodesy  # noqa: F401
        self.frame, self.res = frame, res
        n = int(round(2 * half / res)) + 1
        self.x0 = self.y0 = -half
        xs = self.x0 + np.arange(n) * res
        self.raster = np.empty((n, n))
        rows = 256
        for r0 in range(0, n, rows):
            e, nn = np.meshgrid(xs, xs[r0:r0 + rows])
            la, lo, _ = frame.enu_to_geodetic(e, nn, 0.0)
            self.raster[r0:r0 + rows] = elev_fn(np.asarray(la), np.asarray(lo))

    def elev(self, lat, lon):
        x, y, _ = self.frame.geodetic_to_enu(lat, lon, 0.0)
        return bilinear(self.raster, self.x0, self.y0, self.res, np.asarray(x), np.asarray(y))


def stats(d):
    d = np.abs(d.ravel())
    return (f"median {np.median(d) * 100:6.2f} cm  p99 {np.percentile(d, 99) * 100:6.2f} cm  "
            f"max {d.max() * 100:6.2f} cm")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=39.7255)      # I-25 at 6th Avenue (Denver)
    ap.add_argument("--lon", type=float, default=-105.0055)
    ap.add_argument("--res", type=float, nargs="+", default=[1.0, 0.5])
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()

    import apppaths
    import cache
    cache_dir = apppaths.default_cache_dir()
    world = apppaths.saved_world_dir() or apppaths.default_world_dir()
    cache.set_world(world, cache_dir)
    os.environ.setdefault("BNE_REVISIT", "off")

    import blockworker
    import dem as demmod
    import geodesy
    import grading as gradingmod
    import heightmap
    import decklidar
    blockworker.init(cache_dir, a.offline, True, True, True, False, queue.Queue())
    s = blockworker._s
    zoom = demmod.zoom_for(a.lat, 1.25, demmod.FINE_ZOOM)
    print(f"place {a.lat}, {a.lon}; DEM z{zoom}; cache {cache_dir}; world {world}")

    def grader(frame):
        g = gradingmod.Grader(frame, s.md, lambda la, lo: s.dem.sample(la, lo, gradingmod.PROFILE_ZOOM), s.widths)
        g.wet = None
        g.dem = s.dem
        g.surface = decklidar.surface_for(s.md, s.dem)
        g.shared = None                 # (no plans shared between frames: each pays its own planning, as today)
        return g

    def raw_fn(frame):
        return lambda la, lo: blockworker.ground(frame, la, lo, s.dem.sample(la, lo, zoom))

    def graded_fn(frame, g):
        raw = raw_fn(frame)
        return lambda la, lo: g.apply(la, lo, raw(la, lo), gradingmod.ROAD_LIFT)

    # warm the DEM tiles first, so no timing below pays for reading them from disk
    f0 = geodesy.EnuFrame(a.lat, a.lon)
    heightmap.grid_u(f0, raw_fn(f0), 0.0, 0.0, SIZE)

    blocks = {}
    for name, (alat, alon) in ANCHORS.items():
        frame = geodesy.EnuFrame(alat, alon)
        x, y, _ = frame.geodetic_to_enu(a.lat, a.lon, 0.0)
        cx, cy = heightmap.snap(float(x), 128.0), heightmap.snap(float(y), 128.0)
        xs, ys = heightmap.sample_coords(cx, cy, SIZE)
        t0 = time.perf_counter()
        g = grader(frame)
        u = heightmap.surface(frame, graded_fn(frame, g), xs, ys)[1]
        t_direct = time.perf_counter() - t0
        t0 = time.perf_counter()
        u_raw = heightmap.surface(frame, raw_fn(frame), xs, ys)[1]
        t_raw = time.perf_counter() - t0
        blocks[name] = dict(frame=frame, xs=xs, ys=ys, u=u, u_raw=u_raw)
        print(f"{name}: block centre ({cx:.0f}, {cy:.0f}) in its frame; direct graded build {t_direct:6.2f} s "
              f"(raw DEM alone {t_raw:5.2f} s); graded moved {np.mean(np.abs(u - u_raw) > 0.05) * 100:4.1f} % "
              f"of samples > 5 cm")

    # the world tile: frame at its grid cell's centre; half-size covers both blocks' footprints (1024 m, rotated)
    tlat = (np.floor(a.lat / TILE_DEG) + 0.5) * TILE_DEG
    tlon = (np.floor(a.lon / TILE_DEG) + 0.5) * TILE_DEG
    tf = geodesy.EnuFrame(tlat, tlon)
    half = 0.0
    for b in blocks.values():
        e, n = np.meshgrid(b["xs"][[0, -1]], b["ys"][[0, -1]])
        la, lo, _ = b["frame"].enu_to_geodetic(e, n, 0.0)
        tx, ty, _ = tf.geodetic_to_enu(la, lo, 0.0)
        half = max(half, float(np.max(np.abs(tx))), float(np.max(np.abs(ty))))
    half = np.ceil(half + 8.0)
    print(f"world tile frame ({tlat:.3f}, {tlon:.3f}), {2 * half:.0f} m square")

    # region grader: one Grader in the tile's frame serves every anchor's block (plans made once, applied per point)
    gr = grader(tf)
    for name, b in blocks.items():
        t0 = time.perf_counter()
        u_r = heightmap.surface(b["frame"], graded_fn(b["frame"], gr), b["xs"], b["ys"])[1]
        d = np.abs(u_r - b["u"])
        print(f"region grader, {name}: {time.perf_counter() - t0:5.2f} s (plans {'shared' if name != 'a3' else 'cold'});"
              f" vs direct {stats(d)}; > 10 cm {int((d > 0.1).sum())}, > 1 m {int((d > 1).sum())}")

    for res in a.res:
        t0 = time.perf_counter()
        gt = grader(tf)
        wt = WorldTile(tf, half, res, graded_fn(tf, gt))
        t_tile = time.perf_counter() - t0
        wr = WorldTile(tf, half, res, raw_fn(tf))
        mb = wt.raster.size * 2 / 1e6
        print(f"\nres {res} m: world tile graded once in {t_tile:6.2f} s ({wt.raster.shape[0]}², "
              f"{mb:.1f} MB at 16 bits)")
        for name, b in blocks.items():
            t0 = time.perf_counter()
            u_w = heightmap.surface(b["frame"], wt.elev, b["xs"], b["ys"])[1]
            t_res = time.perf_counter() - t0
            u_wr = heightmap.surface(b["frame"], wr.elev, b["xs"], b["ys"])[1]
            core = np.abs(b["u"] - b["u_raw"]) > 0.05
            d = np.abs(u_w - b["u"])
            gy, gx = np.gradient(b["u"], heightmap.SQUARE)
            steep = np.hypot(gx, gy) > 0.5
            print(f"  {name}: resample {t_res:5.2f} s; > 10 cm {int((d > 0.1).sum())} ({np.mean(steep[d > 0.1]) * 100:.0f} %"
                  f" on slopes over 1:2), > 50 cm {int((d > 0.5).sum())}, > 1 m {int((d > 1).sum())} of {d.size}")
            print(f"    graded, all samples   {stats(u_w - b['u'])}")
            print(f"    graded, road cores    {stats((u_w - b['u'])[core])}  ({core.sum()} samples)")
            print(f"    raw DEM, all samples  {stats(u_wr - b['u_raw'])}   (interpolation alone)")


if __name__ == "__main__":
    main()
