"""build_lamp_glow.py: the street lamps' far glow (mod/.../art/shapes/beamngEarth/bne_lamp_glow.dae), for night only
(nightlights.lua turns its group on after dusk; furniture.py G_GLOW rows at every lens). The lens (build_lamp_lens.py)
is flat and 0.6 m long: past about 60 m it is a pixel or two edge-on, and the lamps vanished a short way from the camera
(the user, 2026-10-27: "at night street lamps only turn on when close to the camera"). This is a glowing ball at the
lens that grows with distance, in the engine's own camera detail levels (the stock shapes' base00 / start01 hierarchy:
a node named <object><size> is that detail's mesh): near the camera a speck of HIDDEN_R m inside the lens (the lens is
there; an empty "nulldetail" level first drew the whole shape nowhere, at any distance, in 0.40), then balls of GLOW_R
m, each one kept while its picture spans about MIN_PX pixels.

It also makes bne_beacon_glow.dae, the red aviation obstruction lights over tall buildings (buildings.py G_BEACON,
trees.lua's beaconglow group): the same balls in material bne_beacon, BEACON_R, with no speck (no lens to show near),
out to the skyline's 40 km.

    python3 tools/build_lamp_glow.py
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import dae  # noqa: E402
import staging  # noqa: E402

GLOW_R = (0.3, 0.5, 0.8, 1.3, 2.0, 3.0)  # m: the balls' radii, nearest first
HIDDEN_R = 0.01             # m: the speck drawn near the camera
# Both at the game's own detail (detailAdjust 2, Mesh Ultra): the mod's Detail distance setting raises it while in our
# level (lod.lua: 3 for Far, the default, 4 for Max), and every switch below moves out with it. With the speck to 60 m
# at 2, Far kept it to 90 m and Max to 120 m, past the ~60 m where the edge-on lens is still seen: lamps between there
# and the first ball showed nothing (the user, 2026-10-29: "some lights dont appear until i get within 100m"). From
# 30 m the speck ends at 45 m under Far and 60 m under Max; the balls, kept longer at higher detail, are still ~3 px
# (Far) to ~2.5 px (Max) when they hand over.
NEAR_M = 30.0               # m: the speck nearer (the lens shows there), at the game's detail
MIN_PX = 5.0                # px: a ball's width on screen when the next, bigger one takes over, at the game's detail
SCREEN_PX = 1130.0          # px per radian on screen: 1440 pixels high at the game's 65-degree field of view
# The engine's detail size per (shape radius / distance): measured on the PC (2560 x 1440, mesh quality high, its
# detailAdjust 2: the speck gave way to the next ball at 190 m, that one to the next at 575 m). Other screens and mesh
# qualities scale every switch distance alike: lower ones switch nearer (the balls a little larger on screen).
ENGINE_PX = 4100.0
LAST_PX = 2.0               # the detail size where the last ball stops (the engine's own files' smallest)
SEG, RINGS = 8, 5
# the beacons: a ball from the camera out, doubling, the last ~3 px wide at 40 km
BEACON_R = (0.4, 0.8, 1.6, 3.2, 6.4, 12.8, 25.6, 51.2)
BEACON_MIN_PX = 3.0


def sphere(r):
    """(positions (n,3), tris (m,3) counter-clockwise from outside) of a UV sphere of radius r at the origin."""
    P = [(0.0, 0.0, r)]
    for i in range(1, RINGS):
        th = math.pi * i / RINGS
        for k in range(SEG):
            ph = 2 * math.pi * k / SEG
            P.append((r * math.sin(th) * math.cos(ph), r * math.sin(th) * math.sin(ph), r * math.cos(th)))
    P.append((0.0, 0.0, -r))
    T = []
    ring = lambda i, k: 1 + (i - 1) * SEG + k % SEG            # noqa: E731
    for k in range(SEG):
        T.append((0, ring(1, k), ring(1, k + 1)))
        T.append((len(P) - 1, ring(RINGS - 1, k + 1), ring(RINGS - 1, k)))
    for i in range(1, RINGS - 1):
        for k in range(SEG):
            a, b, c, d = ring(i, k), ring(i, k + 1), ring(i + 1, k), ring(i + 1, k + 1)
            T += [(a, c, d), (a, d, b)]
    return np.array(P), np.array(T, np.int64)


def detail_sizes(radii=GLOW_R, min_px=MIN_PX, near_m=NEAR_M):
    """The detail sizes (the engine's) where the speck and each ball start, falling: the speck from near_m in (none
    without), each ball out to where it spans min_px on screen."""
    R = radii[-1] * math.sqrt(3.0)                  # the shape's radius: its bounding box's half diagonal
    ends = ([near_m] if near_m else []) + [2.0 * r * SCREEN_PX / min_px for r in radii[:-1]]
    return [R * ENGINE_PX / d for d in ends] + [LAST_PX]


def main():
    make("bne_lamp_glow.dae", "bne_lamp_lens", "1 0.8 0.5 1", (HIDDEN_R,) + GLOW_R, detail_sizes())
    make("bne_beacon_glow.dae", "bne_beacon", "0.8 0.1 0.05 1", BEACON_R, detail_sizes(BEACON_R, BEACON_MIN_PX, None))


def make(name_out, mat, colour, radii, sizes):
    meshes = []
    for r in radii:
        P, T = sphere(r)
        n = P / r
        meshes.append((P, n, np.c_[0.5 + 0.5 * n[:, 0], 0.5 + 0.5 * n[:, 1]], T))
    make_lod(name_out, mat, colour, meshes, sizes)


def make_lod(name_out, mat, colour, meshes, sizes):
    """A shape of camera detail levels: meshes[k] = (positions, normals, uvs, tris), drawn from detail size sizes[k]
    down to the next (the engine's base00 / start01 hierarchy; build_night_landmarks.py's star uses it too)."""
    geoms, nodes = [], []
    for k, (P, n, UV, T) in enumerate(meshes):
        g = f"g{k}"
        geoms.append(
            f'<geometry id="{g}" name="{g}"><mesh>\n'
            f'<source id="{g}-pos"><float_array id="{g}-pos-a" count="{P.size}">{dae._floats(P)}</float_array>'
            f'<technique_common><accessor source="#{g}-pos-a" count="{len(P)}" stride="3"><param name="X" type="float"/>'
            '<param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>\n'
            f'<source id="{g}-nrm"><float_array id="{g}-nrm-a" count="{n.size}">{dae._floats(n)}</float_array>'
            f'<technique_common><accessor source="#{g}-nrm-a" count="{len(n)}" stride="3"><param name="X" type="float"/>'
            '<param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>\n'
            f'<source id="{g}-uv"><float_array id="{g}-uv-a" count="{UV.size}">{dae._floats(UV)}</float_array>'
            f'<technique_common><accessor source="#{g}-uv-a" count="{len(UV)}" stride="2"><param name="S" type="float"/>'
            '<param name="T" type="float"/></accessor></technique_common></source>\n'
            f'<vertices id="{g}-vtx"><input semantic="POSITION" source="#{g}-pos"/></vertices>\n'
            f'<triangles material="mat0" count="{len(T)}"><input semantic="VERTEX" source="#{g}-vtx" offset="0"/>'
            f'<input semantic="NORMAL" source="#{g}-nrm" offset="1"/><input semantic="TEXCOORD" source="#{g}-uv" '
            f'offset="2" set="0"/><p>{dae._indices(T, 3)}</p></triangles>\n</mesh></geometry>')
        name = f"glow{int(round(sizes[k]))}"
        nodes.append(f'<node id="{name}" name="{name}" type="NODE"><matrix sid="transform">1 0 0 0 0 1 0 0 0 0 1 0 0 0 '
                     f'0 1</matrix><instance_geometry url="#{g}"><bind_material><technique_common><instance_material '
                     f'symbol="mat0" target="#{mat}"/></technique_common></bind_material></instance_geometry>'
                     '</node>')
    ident = '<matrix sid="transform">1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</matrix>'
    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
<asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>
<library_effects><effect id="{mat}-fx"><profile_COMMON><technique sid="common"><lambert><diffuse><color>{colour}</color></diffuse></lambert></technique></profile_COMMON></effect></library_effects>
<library_materials><material id="{mat}" name="{mat}"><instance_effect url="#{mat}-fx"/></material></library_materials>
<library_geometries>{"".join(geoms)}</library_geometries>
<library_visual_scenes><visual_scene id="scene"><node id="base00" name="base00" type="NODE">{ident}<node id="start01" name="start01" type="NODE">{ident}
{"".join(nodes)}
</node></node></visual_scene></library_visual_scenes>
<scene><instance_visual_scene url="#scene"/></scene>
</COLLADA>
'''
    out = os.path.join(HERE, "..", "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth",
                       name_out)
    tmp = staging.tmp_for(out)
    with open(tmp, "w", newline="\n") as f:
        f.write(xml)
    staging.replace(tmp, out)
    print(out, os.path.getsize(out), "bytes; detail sizes", [round(s, 1) for s in sizes])


if __name__ == "__main__":
    main()
