"""build_street_props.py: street life's own props (mod/.../art/shapes/beamngEarth/bne_mailbox*.dae, bne_cart_*.dae and
street_props.materials.json): the stock maps have no US curbside mailbox, cluster mailbox or wheeled trash cart
(2026-10-28: every .dae / .cdae of BeamNG.drive 0.40.3's content searched).

    python3 tools/build_street_props.py

Each model stands on the ground at its origin, Z up, its front (the mailbox's door, the cart's lid opening) towards +X
(server streetlife.py turns +X to the road). Heights as trees.lua's CATALOG has them (the rows: scale 1):
  bne_mailbox       a black rural mailbox (USPS standard No. 1, ~19 x 6.5 x 8.5 in) on a 4 x 4 wooden post, its
                    floor 41 in up, the red flag on its side: 1.25 m
  bne_mailbox_b     a grey one on a black steel post
  bne_mailbox_cbu   a cluster box unit (USPS 1118-F type 2: 16 doors, ~47 x 18 x 62 in on a pedestal): 1.75 m
  bne_cart_trash    a 96-gallon wheeled cart (~29 x 25 x 42 in), charcoal, its wheels and handle at the back: 1.07 m
  bne_cart_recycle  the same, blue
Two levels of detail and a collision box each.
"""
import json
import math
import os
import sys
import uuid

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_rail_crossing_props import Mesh, write_dae  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth")

# (base colour, metallic, roughness)
COLOURS = {
    "bne_sl_black": ((0.03, 0.03, 0.03), 0.3, 0.45),        # the mailbox's enamel
    "bne_sl_grey": ((0.42, 0.43, 0.44), 0.7, 0.4),
    "bne_sl_red": ((0.62, 0.05, 0.04), 0.0, 0.5),
    "bne_sl_wood": ((0.36, 0.27, 0.18), 0.0, 0.85),         # a weathered cedar post
    "bne_sl_steel": ((0.08, 0.08, 0.08), 0.6, 0.5),
    "bne_sl_cbu": ((0.55, 0.50, 0.40), 0.4, 0.45),          # the cluster box's sandstone powder coat
    "bne_sl_cbu_seam": ((0.22, 0.20, 0.16), 0.3, 0.6),
    "bne_sl_lock": ((0.75, 0.75, 0.73), 0.9, 0.3),
    "bne_sl_cart": ((0.10, 0.10, 0.11), 0.0, 0.7),          # HDPE: matte
    "bne_sl_cart_blue": ((0.05, 0.18, 0.45), 0.0, 0.65),
    "bne_sl_tyre": ((0.03, 0.03, 0.03), 0.0, 0.9),
}

MB_FLOOR_Z, MB_L, MB_W, MB_WALL_H = 1.04, 0.48, 0.17, 0.11     # the mailbox: its floor, length, width, straight side


def frustum(m, mat, c, z0, z1, h0, h1, top=True, bottom=True):
    """A box tapering from half sizes h0 (x, y) at z0 to h1 at z1, centred on c (x, y)."""
    cx, cy = c
    lo = [(cx + sx * h0[0], cy + sy * h0[1], z0) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    hi = [(cx + sx * h1[0], cy + sy * h1[1], z1) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    for i in range(4):
        j = (i + 1) % 4
        m.face(mat, [lo[i], lo[j], hi[j], hi[i]])
    if top:
        m.face(mat, hi)
    if bottom:
        m.face(mat, lo[::-1])


def tunnel(m, mat, x0, x1, w, z0, wall, segs):
    """The mailbox's body: straight sides wall high, a half-round top, along x from x0 to x1 (both ends closed)."""
    r = w / 2
    prof = [(-r, z0), (r, z0), (r, z0 + wall)]
    prof += [(r * math.cos(a), z0 + wall + r * math.sin(a)) for a in np.linspace(0, math.pi, segs + 1)[1:-1]]
    prof += [(-r, z0 + wall)]
    n = len(prof)
    for i in range(n):
        (ya, za), (yb, zb) = prof[i], prof[(i + 1) % n]
        m.face(mat, [(x0, ya, za), (x0, yb, zb), (x1, yb, zb), (x1, ya, za)])
    m.face(mat, [(x1, y, z) for y, z in prof])            # the door (+x)
    m.face(mat, [(x0, y, z) for y, z in prof][::-1])


def mailbox(segs, box_mat, post_mat, wood=True, detail=True):
    m = Mesh(segs)
    pw = 0.045 if wood else 0.03
    m.box(post_mat, (0.0, 0.0, (MB_FLOOR_Z - 0.04) / 2), (pw, pw, (MB_FLOOR_Z - 0.04) / 2))
    m.box(post_mat, (0.0, 0.0, MB_FLOOR_Z - 0.02), (0.2, 0.06, 0.02))            # the board under the box
    tunnel(m, box_mat, -MB_L / 2, MB_L / 2, MB_W, MB_FLOOR_Z, MB_WALL_H, segs // 2 if detail else 3)
    if detail:
        m.box(box_mat, (MB_L / 2 + 0.006, 0.0, MB_FLOOR_Z + MB_WALL_H + 0.04), (0.006, 0.012, 0.012))   # the latch
        # the flag, down, on the box's right side (+y seen from the road: -y here faces it... either side is seen)
        m.box("bne_sl_red", (-0.08, -MB_W / 2 - 0.008, MB_FLOOR_Z + 0.09), (0.12, 0.004, 0.018))
        m.box("bne_sl_red", (0.03, -MB_W / 2 - 0.008, MB_FLOOR_Z + 0.09), (0.03, 0.004, 0.04))
    return m


CBU_D, CBU_W, CBU_Z0, CBU_Z1 = 0.46, 1.19, 0.55, 1.70


def cbu(detail=True):
    m = Mesh(8)
    m.box("bne_sl_cbu", (-0.02, 0.0, CBU_Z0 / 2), (0.14, 0.24, CBU_Z0 / 2))                # the pedestal
    m.box("bne_sl_cbu", (-0.02, 0.0, 0.03), (0.2, 0.3, 0.03))                              # its foot
    m.box("bne_sl_cbu", (0.0, 0.0, (CBU_Z0 + CBU_Z1) / 2), (CBU_D / 2, CBU_W / 2, (CBU_Z1 - CBU_Z0) / 2))
    m.box("bne_sl_cbu", (0.0, 0.0, CBU_Z1 + 0.025), (CBU_D / 2 + 0.03, CBU_W / 2 + 0.03, 0.025))   # the cap
    if detail:
        fx = CBU_D / 2 + 0.003
        # the doors: 4 columns x 4 rows of customer doors over two parcel lockers and the outgoing slot
        for k in range(1, 4):
            y = -CBU_W / 2 + k * CBU_W / 4
            m.box("bne_sl_cbu_seam", (fx, y, CBU_Z0 + 0.62), (0.003, 0.004, 0.48))
        for k in range(0, 6):
            z = CBU_Z0 + 0.16 + k * 0.19
            m.box("bne_sl_cbu_seam", (fx, 0.0, z), (0.003, CBU_W / 2 - 0.03, 0.004))
        for k in range(4):
            for r in range(4):
                y = -CBU_W / 2 + (k + 0.8) * CBU_W / 4
                z = CBU_Z0 + 0.255 + (r + 2) * 0.19 - 0.19
                m.box("bne_sl_lock", (fx + 0.004, y, z), (0.004, 0.012, 0.012))
        m.box("bne_sl_cbu_seam", (fx, 0.0, CBU_Z0 + 0.08), (0.003, 0.12, 0.008))             # the outgoing slot
    return m


CART_D, CART_W, CART_H = 0.74, 0.62, 1.07


def cart(body, detail=True):
    m = Mesh(10 if detail else 5)
    z_lid = CART_H - 0.05
    frustum(m, body, (0.02, 0.0), 0.08, z_lid, (0.26, 0.24), (CART_D / 2 - 0.03, CART_W / 2 - 0.02))
    m.box(body, (0.0, 0.0, z_lid + 0.025), (CART_D / 2, CART_W / 2, 0.025))                    # the lid
    if detail:
        m.box(body, (CART_D / 2 - 0.01, 0.0, z_lid - 0.02), (0.02, CART_W / 2 - 0.04, 0.03))     # its lip
        m.box(body, (-CART_D / 2 - 0.02, 0.0, z_lid - 0.05), (0.02, CART_W / 2 - 0.06, 0.02))   # the handle
        m.box(body, (-0.3, 0.0, 0.14), (0.05, CART_W / 2 - 0.04, 0.04))                         # the axle housing
    for y in (-1, 1):                                                                           # the wheels
        yc = y * (CART_W / 2 - 0.02)
        ang = [2 * math.pi * i / m.segs for i in range(m.segs)]
        ring = [(-0.3 + 0.13 * math.cos(a), 0.13 + 0.13 * math.sin(a)) for a in ang]
        for i in range(m.segs):
            (xa, za), (xb, zb) = ring[i], ring[(i + 1) % m.segs]
            m.face("bne_sl_tyre", [(xa, yc + 0.04, za), (xb, yc + 0.04, zb), (xb, yc - 0.04, zb), (xa, yc - 0.04, za)])
        m.face("bne_sl_tyre", [(x, yc + 0.04, z) for x, z in ring][::-1])
        m.face("bne_sl_tyre", [(x, yc - 0.04, z) for x, z in ring])
    return m


def col_box(c, half):
    m = Mesh(4)
    m.box("bne_sl_black", c, half)
    return m


def materials():
    out = {}
    pid = lambda n: str(uuid.uuid5(uuid.NAMESPACE_URL, "beamng-earth/" + n))     # noqa: E731
    for k, (col, met, rough) in COLOURS.items():
        out[k] = {"name": k, "mapTo": k, "class": "Material", "version": 1.5, "persistentId": pid(k),
                  "Stages": [{"baseColorFactor": [*col, 1.0], "metallicFactor": met, "roughnessFactor": rough},
                             {}, {}, {}]}
    with open(os.path.join(OUT, "street_props.materials.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)


def models():
    """name -> ([(lod size px, Mesh)], collision Mesh)."""
    mb_col = col_box((0.0, 0.0, 0.62), (0.08, 0.1, 0.62))
    cart_col = col_box((0.0, 0.0, CART_H / 2), (CART_D / 2, CART_W / 2, CART_H / 2))
    return {
        "bne_mailbox": ([(64, mailbox(16, "bne_sl_black", "bne_sl_wood")),
                         (8, mailbox(6, "bne_sl_black", "bne_sl_wood", detail=False))], mb_col),
        "bne_mailbox_b": ([(64, mailbox(16, "bne_sl_grey", "bne_sl_steel", wood=False)),
                           (8, mailbox(6, "bne_sl_grey", "bne_sl_steel", wood=False, detail=False))], mb_col),
        "bne_mailbox_cbu": ([(64, cbu()), (8, cbu(False))],
                            col_box((0.0, 0.0, (CBU_Z1 + 0.05) / 2), (CBU_D / 2, CBU_W / 2, (CBU_Z1 + 0.05) / 2))),
        "bne_cart_trash": ([(64, cart("bne_sl_cart")), (8, cart("bne_sl_cart", False))], cart_col),
        "bne_cart_recycle": ([(64, cart("bne_sl_cart_blue")), (8, cart("bne_sl_cart_blue", False))], cart_col),
    }


def main():
    materials()
    for name, (lods, col) in models().items():
        path = os.path.join(OUT, name + ".dae")
        write_dae(path, name, lods, col)
        top = max(float(np.asarray(q)[2]) for q in lods[0][1].p)
        print(path, os.path.getsize(path), "bytes", sum(len(t) for t in lods[0][1].tri.values()), "triangles",
              f"top {top:.3f} m")


if __name__ == "__main__":
    main()
