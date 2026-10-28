"""build_static_cars.py: static parked cars (mod/.../art/shapes/beamngEarth/cars/bne_car_<model>_<colour>.dae,
static_cars.materials.json, cars.json) from the stock traffic cars (BeamNG.drive's content/vehicles/simple_traffic.zip,
read, never changed): many cheap TSStatic / forest cars where the game's parking system animates only ~16.

    python3 tools/build_static_cars.py [--zip PATH] [--out DIR] [--models midsize,pickup] [--colours white,red]

Per model, the meshes one parked config (<model>_*_parked.pc) shows: its parts' flexbodies (jbeam), the body ones where
the .dae has them (vehicle space), the wheels, tyres and hubcaps (modelled at the origin) put at the hub centres the
jbeam gives: the suspension's wheel slot's nodeOffset (its $susp?HubOffsetX / Z), the body's suspension slot's
nodeMove (y) and the flexbody's own pos and z rotation (the right side's turned 180 degrees). Then turned to ours: front
at +X (the stock cars face -Y), Z up, the tyres' bottom at z 0, centred on the car in x and y.

Materials: our own names (bne_car_*) on the stock stages and textures (/vehicles/simple_traffic/..., mounted with the
game's vehicles); the body paint (the stock instanceDiffuse palette, a car's colour at run time) a plain colour, one
shape per colour; the per-function lamps (lowbeam, signal_L, ...: the jbeam's glowMap names) on the off lamp
material. Two levels of detail (the stock meshes; a hull of boxes and 8-sided wheels) and a collision box.

These are derived from BeamNG's content: built on the player's machine, never committed (.gitignore).
"""
import argparse
import glob
import io
import json
import math
import os
import re
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_rail_crossing_props import Mesh, write_dae  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth", "cars")
NS = "{http://www.collada.org/2005/11/COLLADASchema}"
ST = "vehicles/simple_traffic/"

MODELS = ["midsize", "fullsize", "pessima", "covet", "lansdale", "roamer", "pickup", "van", "sunburst2", "wendover"]
# (base colour, metallic, roughness) under a clear coat
COLOURS = {
    "white": ((0.80, 0.80, 0.78), 0.0, 0.45),
    "black": ((0.02, 0.02, 0.02), 0.4, 0.35),
    "silver": ((0.55, 0.56, 0.57), 0.9, 0.35),
    "darkgrey": ((0.13, 0.13, 0.14), 0.7, 0.4),
    "darkblue": ((0.03, 0.06, 0.20), 0.7, 0.4),
    "red": ((0.45, 0.03, 0.03), 0.3, 0.4),
}
# a parked config without a livery, the first of these found (else the first that is not one)
PREFER = ("base", "standard", "stock", "dx", "d15_parked")
LIVERY = ("taxi", "police", "security", "drivingschool", "turboburger", "fleet", "farm", "_eu_", "ambulance", "beamcom")
HIGH_PX, LOW_PX = 128, 16
MAX_MB = 400.0


def steam_zip():
    """content/vehicles/simple_traffic.zip of the BeamNG.drive install (the Steam libraries)."""
    roots = [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam", os.path.expanduser("~/.steam/steam")]
    libs = list(roots)
    for r in roots:
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            with open(vdf, encoding="utf-8", errors="replace") as fh:
                libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', fh.read())]
    for lib in libs:
        p = os.path.join(lib, "steamapps", "common", "BeamNG.drive", "content", "vehicles", "simple_traffic.zip")
        if os.path.exists(p):
            return p
    return None


# --- jbeam / BeamNG's lenient JSON (comments, missing and trailing commas) ---------------------------------------

_TOK = re.compile(r'\s+|//[^\n]*|/\*.*?\*/|"(?:[^"\\]|\\.)*"|[{}\[\]:,]|[^\s{}\[\]:,"]+', re.S)


def loads(text):
    toks = [t for t in _TOK.findall(text) if t.strip() and not t.startswith("//") and not t.startswith("/*") and t != ","]
    pos = [0]

    def value():
        t = toks[pos[0]]
        pos[0] += 1
        if t == "{":
            d = {}
            while toks[pos[0]] != "}":
                k = value()
                if toks[pos[0]] == ":":
                    pos[0] += 1
                d[k] = value()
            pos[0] += 1
            return d
        if t == "[":
            a = []
            while toks[pos[0]] != "]":
                a.append(value())
            pos[0] += 1
            return a
        if t.startswith('"'):
            return json.loads(t)
        if t in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[t]
        try:
            return float(t) if any(c in t for c in ".eE") else int(t)
        except ValueError:
            return t
    out = {}
    while pos[0] < len(toks):
        v = value()
        if isinstance(v, dict):
            out.update(v)
    return out


def table(rows):
    """A jbeam table's rows (the header and the option dicts left out)."""
    return [r for r in (rows or [])[1:] if isinstance(r, list)]


def ev(v, var):
    """A jbeam value: a number, "$name" or "$=expression"."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v.startswith("$="):
        expr = re.sub(r"\$(\w+)", lambda m: repr(float(var.get("$" + m.group(1), 0.0))), v[2:])
        return float(eval(expr, {"__builtins__": {}}, {"math": math}))      # (jbeam's arithmetic only)
    if isinstance(v, str) and v.startswith("$"):
        return float(var.get(v, 0.0))
    return float(v or 0.0)


# --- the stock car ------------------------------------------------------------------------------------------------

def read_dae(data):
    """node name -> [(material, P (n,3), N (n,3), UV (n,2), tris (k,3))] in vehicle space."""
    root = ET.fromstring(data)
    geoms = {}
    for g in root.iter(NS + "geometry"):
        m = g.find(NS + "mesh")
        if m is None:
            continue
        src = {s.get("id"): np.array(s.find(NS + "float_array").text.split(), np.float64)
               .reshape(-1, int(s.find(NS + "technique_common/" + NS + "accessor").get("stride")))
               for s in m.findall(NS + "source")}
        vin = {i.get("semantic"): i.get("source")[1:] for i in m.find(NS + "vertices").findall(NS + "input")}
        parts = []
        for prim in m:
            tag = prim.tag.replace(NS, "")
            if tag not in ("triangles", "polylist"):
                continue
            ins = prim.findall(NS + "input")
            stride = max(int(i.get("offset")) for i in ins) + 1
            idx = np.array(prim.find(NS + "p").text.split(), np.int64).reshape(-1, stride)
            if tag == "polylist":           # (fanned)
                vc = np.array(prim.find(NS + "vcount").text.split(), np.int64)
                rows, k = [], 0
                for c in vc:
                    for j in range(1, c - 1):
                        rows += [idx[k], idx[k + j], idx[k + j + 1]]
                    k += c
                idx = np.array(rows, np.int64)
            off = {}
            for i in ins:
                sem = i.get("semantic")
                if sem == "TEXCOORD" and int(i.get("set", 0)) != 0:
                    continue
                off.setdefault(sem, (int(i.get("offset")), i.get("source")[1:]))
            vo, _ = off["VERTEX"]
            P = src[vin["POSITION"]][idx[:, vo], :3]
            N = src[off["NORMAL"][1]][idx[:, off["NORMAL"][0]], :3] if "NORMAL" in off else \
                src[vin["NORMAL"]][idx[:, vo], :3] if "NORMAL" in vin else np.zeros_like(P)
            T = src[off["TEXCOORD"][1]][idx[:, off["TEXCOORD"][0]], :2] if "TEXCOORD" in off else \
                src[vin["TEXCOORD"]][idx[:, vo], :2] if "TEXCOORD" in vin else np.zeros((len(P), 2))
            parts.append((prim.get("material", "").replace("-material", ""), P, N, T))
        geoms[g.get("id")] = parts
    nodes = {}
    for vs in root.iter(NS + "visual_scene"):
        def walk(n, M):
            M2 = M @ node_matrix(n)
            for ig in n.findall(NS + "instance_geometry"):
                sym = {im.get("symbol"): im.get("target", "")[1:] for im in ig.iter(NS + "instance_material")}
                for mat, P, N, T in geoms.get(ig.get("url")[1:], []):
                    mat = sym.get(mat + "-material", mat + "-material").replace("-material", "") or mat
                    P2 = P @ M2[:3, :3].T + M2[:3, 3]
                    N2 = N @ M2[:3, :3].T
                    nodes.setdefault(n.get("name"), []).append((mat, P2, N2, T))
            for c in n.findall(NS + "node"):
                walk(c, M2)
        for n in vs.findall(NS + "node"):
            walk(n, np.eye(4))
    return nodes


def node_matrix(n):
    M = np.eye(4)
    for c in n:
        t = c.tag.replace(NS, "")
        v = [float(x) for x in (c.text or "").split()]
        if t == "matrix":
            M = M @ np.array(v).reshape(4, 4)
        elif t == "translate":
            T = np.eye(4)
            T[:3, 3] = v
            M = M @ T
        elif t == "scale":
            M = M @ np.diag([*v, 1.0])
        elif t == "rotate" and v[3]:
            M = M @ rot4(np.array(v[:3]), math.radians(v[3]))
    return M


def rot4(axis, a):
    x, y, z = axis / np.linalg.norm(axis)
    c, s, C = math.cos(a), math.sin(a), 1 - math.cos(a)
    R = np.eye(4)
    R[:3, :3] = [[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                 [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                 [z * x * C - y * s, z * y * C + x * s, c + z * z * C]]
    return R


def euler(r):
    """A flexbody's rot {x, y, z} (degrees) as a 3x3 matrix (z after y after x)."""
    R = np.eye(4)
    for ax, k in ((np.array([1.0, 0, 0]), "x"), (np.array([0, 1.0, 0]), "y"), (np.array([0, 0, 1.0]), "z")):
        if (r or {}).get(k):
            R = rot4(ax, math.radians(float(r[k]))) @ R
    return R[:3, :3]


class Stock:
    """simple_traffic.zip, read once: its parts (jbeam), materials and configs."""

    def __init__(self, path):
        self.z = zipfile.ZipFile(path)
        self.names = self.z.namelist()
        self.parts = {}
        for n in self.names:
            if n.endswith(".jbeam"):
                try:
                    self.parts.update(loads(self.z.read(n).decode("utf-8", "replace")))
                except Exception as e:      # (a part file this tool does not read: its parts left out)
                    print(f"  jbeam {n}: {type(e).__name__}: {e}")
        self._daes = {}
        self.materials = {}
        for n in self.names:
            if n.endswith(".materials.json"):
                for k, v in loads(self.z.read(n).decode("utf-8", "replace")).items():
                    if isinstance(v, dict) and v.get("class") == "Material" and "." not in k:   # (skins left out)
                        self.materials[k] = v

    def configs(self, model):
        """The model's parked configs, the plainest first (no livery: PREFER's words first)."""
        pcs = sorted(n for n in self.names if re.fullmatch(ST + re.escape(model) + r"_[^/]*_parked\.pc", n))
        if not pcs:
            raise ValueError("no parked config")
        plain = [p for p in pcs if not any(w in p for w in LIVERY)]
        first = [p for w in PREFER for p in plain if w in p.rsplit("/", 1)[1]]
        order = list(dict.fromkeys(first + plain + pcs))
        return [(p.rsplit("/", 1)[1], loads(self.z.read(p).decode("utf-8"))) for p in order]

    def dae(self, model):
        if model not in self._daes:
            n = ST + f"traffic_{model}.dae"
            self._daes[model] = read_dae(self.z.read(n)) if n in self.names else {}
        return self._daes[model]

    def mesh(self, model, name):
        """A mesh's pieces: the model's own .dae, else the one its name says (traffic_<other>_...: a config may
        borrow another car's wheels)."""
        own = self.dae(model)
        if name in own:
            return own[name]
        m = re.match(r"traffic_([a-z0-9]+)_", name)
        return self.dae(m.group(1)).get(name) if m and m.group(1) != model else None


def slot_options(part, child):
    """The options dict of part's slot holding part name child (slots / slots2 rows), {} without one."""
    for key in ("slots2", "slots"):
        for row in table(part.get(key)):
            names = [x for x in row if isinstance(x, str)] + [y for x in row if isinstance(x, list) for y in x]
            if child in names:
                return row[-1] if isinstance(row[-1], dict) else {}
    return {}


def part_tree(stock, parts):
    """{part: (parent part, its slot's options)} for the config's parts (the root: the model's body part), and the
    parts. A part no chosen part has a slot for (a config naming an older suspension than the body takes) is
    swapped for the default of the body's slot of its family (suspension_F_strut -> the body's suspension_F_...), as
    the game would."""
    chosen = [p for p in dict.fromkeys(parts.values()) if p and p in stock.parts]
    root = parts.get("simple_traffic_model")
    for _ in range(4):
        tree = _attach(stock, parts, chosen)
        loose = [p for p in chosen if p not in tree and p != root]
        swapped = False
        for p in loose:
            fam = p.rsplit("_", 1)[0]
            for q in [x for x in chosen if x in tree or x == root]:
                for key in ("slots2", "slots"):
                    for row in table(stock.parts[q].get(key)):
                        dflt = row[3] if key == "slots2" and len(row) > 3 else row[1] if len(row) > 1 else None
                        if (isinstance(row[0], str) and row[0].rsplit("_", 1)[0] == fam and isinstance(dflt, str)
                                and dflt in stock.parts and dflt not in chosen):
                            chosen[chosen.index(p)] = dflt
                            swapped = True
                            break
                    if swapped:
                        break
                if swapped:
                    break
        if not swapped:
            return chosen, tree
    return chosen, _attach(stock, parts, chosen)


def _attach(stock, parts, chosen):
    tree = {}
    for child in chosen:
        # a slot names the type it takes (the child's slotType, the config's key for it), or the part itself
        keys = [child, stock.parts[child].get("slotType")] + [k for k, v in parts.items() if v == child]
        for parent in chosen:
            if parent == child:
                continue
            row = next((r for k in keys if isinstance(k, str) for r in [_slot_row(stock.parts[parent], k)] if r), None)
            if row is not None:
                tree[child] = (parent, row[-1] if isinstance(row[-1], dict) else {})
                break
    return tree


def _slot_row(part, name):
    for key in ("slots2", "slots"):
        for row in table(part.get(key)):
            if row and (row[0] == name or (len(row) > 1 and isinstance(row[1], list) and name in row[1])):
                return row
    return None


def variables(stock, chosen, tree, pc_vars):
    """The config's jbeam variables: its parts' defaults, their slots' overrides, the config's own."""
    var = {}
    for p in chosen:
        for r in table(stock.parts[p].get("variables")):
            if len(r) > 4 and isinstance(r[0], str):
                var[r[0]] = r[4]
    for p in chosen:
        var.update((tree.get(p, (None, {}))[1] or {}).get("variables", {}) or {})
    var.update(pc_vars or {})
    return {k: (ev(v, var) if not isinstance(v, (int, float)) else float(v)) for k, v in var.items()}


def offsets(tree, var):
    """{part: (x mirrored, x, y, z)}: what its slots' nodeOffset (x mirrored about the centre line: the left side's
    +x, the right's -x) and nodeMove add up to, along its chain of parents. BeamNG moves a part's nodes and
    flexbodies by them: the wheels to their hubs, a long body's bumper back."""
    out = {}

    def of(p, depth=0):
        if p in out:
            return out[p]
        if p not in tree or depth > 20:
            return (0.0, 0.0, 0.0, 0.0)
        parent, opt = tree[p]
        sx, mx, y, z = of(parent, depth + 1)
        off, mv = (opt or {}).get("nodeOffset") or {}, (opt or {}).get("nodeMove") or {}
        r = (sx + ev(off.get("x", 0), var), mx + ev(mv.get("x", 0), var),
             y + ev(off.get("y", 0), var) + ev(mv.get("y", 0), var),
             z + ev(off.get("z", 0), var) + ev(mv.get("z", 0), var))
        out[p] = r
        return r
    for p in tree:
        of(p)
    return out


def car(stock, model):
    """The parked config's meshes: [(material, P, N, UV)] in our frame, and facts about the car."""
    for pc, cfg in stock.configs(model):       # (a config whose suspensions fit the body: some name parts it has no
        parts = cfg.get("parts", {})           # slot for, which the game would swap for the slot's default)
        chosen, tree = part_tree(stock, parts)
        if all(any(f"suspension_{ax}" in p and p in tree for p in chosen) for ax in "FR"):
            break
    else:
        raise ValueError("no parked config whose suspensions fit its body")
    pc_vars = cfg.get("vars", {})
    var = variables(stock, chosen, tree, pc_vars)
    offs = offsets(tree, var)
    pieces, used, missing = [], [], []
    wheel_meshes = []
    for name in chosen:
        part = stock.parts[name]
        sx, mx, oy, oz = offs.get(name, (0.0, 0.0, 0.0, 0.0))
        for row in table(part.get("flexbodies")):
            mesh = row[0]
            if not isinstance(mesh, str) or mesh.endswith("_dmg"):
                continue
            groups = row[1] if len(row) > 1 and isinstance(row[1], list) else []
            opts = next((x for x in row[2:] if isinstance(x, dict)), {})
            pieces_of = stock.mesh(model, mesh)
            if pieces_of is None:
                missing.append(mesh)
                continue
            wheelish = [g for g in groups if isinstance(g, str) and re.match(r"(wheel|tire)_[FR][RL]$", g)]
            pos = opts.get("pos", {}) or {}
            R = euler(opts.get("rot"))
            t = np.array([ev(pos.get(k, 0.0), var) for k in "xyz"])
            for mat, P, N, T in pieces_of:
                P2 = P @ R.T + t
                side = np.sign(P2[:, 0]) if not wheelish else \
                    np.full(len(P2), -1.0 if wheelish[0].endswith("R") else 1.0)
                P2 = P2 + np.c_[side * sx + mx, np.full(len(P2), oy), np.full(len(P2), oz)]
                pieces.append((mat, P2, N @ R.T, T, bool(wheelish)))
            if wheelish:            # (modelled at the origin: now at its hub)
                c = t + np.array([(-1.0 if wheelish[0].endswith("R") else 1.0) * sx + mx, oy, oz])
                wheel_meshes.append(("tyre" if "tire" in mesh else "wheel", c))
            used.append(mesh)
    if not pieces:
        raise ValueError("no meshes")
    tyres = [t for k, t in wheel_meshes if k == "tyre"]
    if len(tyres) < 4:
        raise ValueError(f"{len(tyres)} tyres placed")
    # the tyre's radius: its mesh's extent round its axle (at the origin, the axle along x)
    tname = next(m for m in used if "tire" in m)
    tp = np.concatenate([P for _, P, _, _ in stock.mesh(model, tname)])
    radius = float(np.max(np.hypot(tp[:, 1], tp[:, 2])))
    half_w = float(np.max(np.abs(tp[:, 0])))
    centres = np.array(sorted({tuple(np.round(t, 4)) for t in tyres}))
    ground = float(np.min(centres[:, 2])) - radius
    allp = np.concatenate([P for _, P, _, _, _ in pieces])
    cx, cy = (allp[:, 0].min() + allp[:, 0].max()) / 2, (allp[:, 1].min() + allp[:, 1].max()) / 2
    # ours: front (stock -Y) to +X: (x, y) -> (-y, x); centred, the tyres' bottom at z 0
    Q = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    shift = np.array([cx, cy, ground])
    out = [(mat, (P - shift) @ Q.T, N @ Q.T, T, w) for mat, P, N, T, w in pieces]
    wheels = [((np.array(c) - shift) @ Q.T) for c in centres]
    return dict(pc=pc, pieces=out, wheels=wheels, radius=radius, half_w=half_w, used=used,
                missing=sorted(set(missing)))


# --- materials ----------------------------------------------------------------------------------------------------

def mat_name(stock, model, stock_mat, colour):
    """Our material for a stock one (None: left out)."""
    s = stock_mat
    if s in ("traffic_glass_invisible",):
        return None
    m = stock.materials.get(s)
    paint = s.startswith("traffic_paint") or (m is not None and _palette(m))
    if paint:
        return f"bne_car_paint_{colour}"
    if m is not None:
        return "bne_car_" + s[len("traffic_"):] if s.startswith("traffic_") else "bne_car_" + s
    # the per-function lamps (the jbeam's glowMap names): the off lamp or its glass
    base = f"traffic_{model}_lightglass" if "glass" in s else f"traffic_{model}_lights"
    if base in stock.materials:
        return "bne_car_" + base[len("traffic_"):]
    return "bne_car_glass" if "glass" in s else "bne_car_main"


def _palette(m):
    st = (m.get("Stages") or [{}])[0] or {}
    return bool(st.get("instanceDiffuse")) or "colorPaletteMap" in st


def material_defs(stock, names):
    """static_cars.materials.json's entries for our names."""
    pid = lambda n: str(uuid.uuid5(uuid.NAMESPACE_URL, "beamng-earth/" + n))      # noqa: E731
    out = {}
    paint = stock.materials.get("traffic_paint", {})
    ao = ((paint.get("Stages") or [{}])[0] or {}).get("ambientOcclusionMap")
    for n in sorted(names):
        if n.startswith("bne_car_paint_"):
            col, met, rough = COLOURS[n[len("bne_car_paint_"):]]
            st0 = {"baseColorFactor": [*col, 1.0], "metallicFactor": met, "roughnessFactor": rough,
                   "clearCoatFactor": 1, "clearCoatRoughnessFactor": 0.05}
            if ao:
                st0["ambientOcclusionMap"] = ao
            out[n] = {"name": n, "mapTo": n, "class": "Material", "version": 1.5, "persistentId": pid(n),
                      "Stages": [st0, {}, {}, {}], "dynamicCubemap": True, "materialTag0": "beamng",
                      "materialTag1": "vehicle"}
            continue
        src = stock.materials["traffic_" + n[len("bne_car_"):]] if ("traffic_" + n[len("bne_car_"):]) in stock.materials \
            else stock.materials[n[len("bne_car_"):]]
        d = json.loads(json.dumps(src))
        for st in d.get("Stages", []):
            if isinstance(st, dict):
                for k in ("instanceDiffuse", "colorPaletteMap", "colorPaletteMapUseUV"):
                    st.pop(k, None)
                for k, v in list(st.items()):
                    if v is None:
                        del st[k]
                    elif isinstance(v, str) and k.endswith("Map") and not v.startswith("/"):
                        st[k] = "/" + v                     # (vehicle-relative paths: absolute)
        d.update({"name": n, "mapTo": n, "persistentId": pid(n)})
        out[n] = d
    return out


# --- the shape ----------------------------------------------------------------------------------------------------

def high_mesh(c, mats):
    """The stock meshes as one Mesh (vertices as arrays)."""
    m = Mesh(8)
    P, N, T, tri = [], [], [], {}
    k = 0
    for mat, Pm, Nm, Tm, _ in c["pieces"]:
        name = mats(mat)
        if name is None:
            continue
        n = len(Pm)
        P.append(Pm)
        N.append(Nm)
        T.append(Tm)
        tri.setdefault(name, []).extend((k + i, k + i + 1, k + i + 2) for i in range(0, n, 3))
        k += n
    m.p, m.n, m.uv = np.concatenate(P), np.concatenate(N), np.concatenate(T)
    m.tri = tri
    return dedupe(m)


def dedupe(m):
    """Corners shared where position, normal and uv agree (the stock files' indexing, ~3x smaller)."""
    key = np.round(np.c_[m.p, m.n, m.uv], 5)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    out = Mesh(m.segs)
    out.p, out.n, out.uv = uniq[:, :3], uniq[:, 3:6], uniq[:, 6:8]
    out.tri = {k: [tuple(int(inv[i]) for i in t) for t in v] for k, v in m.tri.items()}
    return out


def hull(c, paint, size):
    """The far level: a body box, a cabin box and four 8-sided wheels."""
    m = Mesh(8)
    body = np.concatenate([P for _, P, _, _, w in c["pieces"] if not w])
    lo, hi = np.percentile(body, 1, axis=0), np.percentile(body, 99, axis=0)
    glass = [P for mat, P, _, _, w in c["pieces"] if "glass" in mat and "light" not in mat and not w]   # (windows)
    g = np.concatenate(glass) if glass else np.zeros((0, 3))
    # the belt line: under most of the windows (a few lamp lenses share the glass material)
    gz = float(np.percentile(g[:, 2], 10)) if len(g) else lo[2] + (hi[2] - lo[2]) * 0.55
    gz = min(max(gz, lo[2] + 0.25), hi[2] - 0.2)
    sill = max(lo[2], c["radius"] * 0.55)
    m.box(paint, ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (sill + gz) / 2),
          ((hi[0] - lo[0]) / 2, (hi[1] - lo[1]) / 2, (gz - sill) / 2))
    up = g[g[:, 2] > (gz + hi[2]) / 2] if len(g) else g     # (the upper half: no lamp glass)
    if len(up):             # the cabin: the windows' length, from the belt line to the roof
        x0, x1 = np.percentile(up[:, 0], 2), np.percentile(up[:, 0], 98)
        m.box("bne_car_glass", ((x0 + x1) / 2, (lo[1] + hi[1]) / 2, (gz + hi[2]) / 2),
              ((x1 - x0) / 2, (hi[1] - lo[1]) / 2 * 0.9, (hi[2] - gz) / 2))
    r, w = c["radius"], c["half_w"]
    for x, y, z in c["wheels"]:
        ang = [2 * math.pi * i / 8 for i in range(8)]
        ring = [(x + r * math.cos(a), z + r * math.sin(a)) for a in ang]
        for i in range(8):
            (xa, za), (xb, zb) = ring[i], ring[(i + 1) % 8]
            m.face("bne_car_tyre", [(xa, y + w, za), (xb, y + w, zb), (xb, y - w, zb), (xa, y - w, za)])
        m.face("bne_car_tyre", [(px, y + w, pz) for px, pz in ring][::-1])
        m.face("bne_car_tyre", [(px, y - w, pz) for px, pz in ring])
    return m


def collision(c):
    allp = np.concatenate([P for _, P, _, _, _ in c["pieces"]])
    lo, hi = np.percentile(allp, 0.5, axis=0), np.percentile(allp, 99.5, axis=0)
    lo[2] = max(lo[2], 0.15)
    m = Mesh(4)
    m.box("bne_car_tyre", (lo + hi) / 2, (hi - lo) / 2)
    return m


def check(mesh):
    """(degenerate triangles, triangles whose winding disagrees with their corners' normals)."""
    P, N = np.asarray(mesh.p), np.asarray(mesh.n)
    deg = bad = 0
    for tris in mesh.tri.values():
        t = np.asarray(tris)
        if not len(t):
            continue
        a, b, cc = P[t[:, 0]], P[t[:, 1]], P[t[:, 2]]
        cr = np.cross(b - a, cc - a)
        area = np.linalg.norm(cr, axis=1)
        deg += int(np.sum(area < 1e-9))
        nn = N[t[:, 0]] + N[t[:, 1]] + N[t[:, 2]]
        bad += int(np.sum(np.einsum("ij,ij->i", cr, nn) < 0))
    return deg, bad


TYRE = {"bne_car_tyre": ((0.03, 0.03, 0.03), 0.0, 0.9)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", default=None, help="simple_traffic.zip (default: the Steam install's)")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--colours", default=",".join(COLOURS))
    a = ap.parse_args()
    path = a.zip or steam_zip()
    if not path or not os.path.exists(path):
        sys.exit("simple_traffic.zip not found (--zip)")
    os.makedirs(a.out, exist_ok=True)
    stock = Stock(path)
    colours = [c for c in a.colours.split(",") if c in COLOURS]
    built, failed, names = {}, {}, set(TYRE)
    cars = {}
    for model in [m for m in a.models.split(",") if m]:
        try:
            cars[model] = car(stock, model)
        except Exception as e:
            failed[model] = f"{type(e).__name__}: {e}"
    # one colour's file size times the colours: cut to four past MAX_MB
    for model, c in cars.items():
        mats = lambda s, model=model: mat_name(stock, model, s, colours[0])      # noqa: E731
        hm = high_mesh(c, mats)
        c["mesh"], c["hull_paint"] = hm, f"bne_car_paint_{colours[0]}"
    total = 0
    summary = {}
    for model, c in cars.items():
        files = []
        lo = np.asarray(c["mesh"].p).min(axis=0)
        hi = np.asarray(c["mesh"].p).max(axis=0)
        for colour in colours:
            mats = lambda s, model=model, colour=colour: mat_name(stock, model, s, colour)     # noqa: E731
            hm = high_mesh(c, mats)
            names |= set(hm.tri)
            low = hull(c, f"bne_car_paint_{colour}", LOW_PX)
            names |= set(low.tri)
            name = f"bne_car_{model}_{colour}"
            p = os.path.join(a.out, name + ".dae")
            write_dae(p, name, [(HIGH_PX, hm), (LOW_PX, low)], collision(c))
            files.append(name + ".dae")
            total += os.path.getsize(p)
            c.setdefault("sizes", []).append(os.path.getsize(p))
            if colour == colours[0]:
                c["checks"] = check(hm)
                c["verts"] = (len(hm.p), sum(len(v) for v in hm.tri.values()), len(low.p))
        summary[model] = {"length": round(float(hi[0] - lo[0]), 3), "width": round(float(hi[1] - lo[1]), 3),
                          "height": round(float(hi[2] - lo[2]), 3), "config": c["pc"], "colours": colours,
                          "files": files, "wheels": [[round(float(v), 3) for v in w] for w in c["wheels"]],
                          "tyre_radius": round(c["radius"], 3)}
        print(f"{model:10s} {c['pc']:34s} L {hi[0]-lo[0]:.2f} W {hi[1]-lo[1]:.2f} H {hi[2]-lo[2]:.2f} "
              f"(z {lo[2]:.2f}..{hi[2]:.2f}) verts {c['verts'][0]} tris {c['verts'][1]} low verts {c['verts'][2]} "
              f"file {np.mean(c['sizes'])/1e6:.1f} MB x{len(colours)} | degenerate {c['checks'][0]} "
              f"inward {c['checks'][1]} | not in the dae: {', '.join(c['missing']) or '-'}")
    defs = material_defs(stock, names - set(TYRE))
    pid = lambda n: str(uuid.uuid5(uuid.NAMESPACE_URL, "beamng-earth/" + n))      # noqa: E731
    for k, (col, met, rough) in TYRE.items():
        defs[k] = {"name": k, "mapTo": k, "class": "Material", "version": 1.5, "persistentId": pid(k),
                   "Stages": [{"baseColorFactor": [*col, 1.0], "metallicFactor": met, "roughnessFactor": rough},
                              {}, {}, {}]}
    with open(os.path.join(a.out, "static_cars.materials.json"), "w", encoding="utf-8") as fh:
        json.dump(defs, fh, indent=2)
    with open(os.path.join(a.out, "cars.json"), "w", encoding="utf-8") as fh:
        # (trees.lua installCars: model -> {length, width, height, colours, files}; the "_" keys are not tables of
        # files, so it skips them)
        json.dump({**summary, "_source": os.path.basename(path), "_failed": failed}, fh, indent=1)
    undefined = names - set(defs)
    # (the materials name .png; the zip holds the engine's .dds of them, which it reads in their place)
    have = {"/" + os.path.splitext(n.lower())[0] for n in stock.names}
    lost = sorted({v for d in defs.values() for st in d.get("Stages", []) if isinstance(st, dict)
                   for k, v in st.items() if k.endswith("Map") and isinstance(v, str) and os.path.splitext(v.lower())[0] not in have})
    print(f"total {total / 1e6:.1f} MB in {sum(len(s['files']) for s in summary.values())} shapes; "
          f"{len(defs)} materials; undefined: {sorted(undefined) or 'none'}; textures not in the zip: "
          f"{lost or 'none'}; failed: {failed or 'none'}")
    if total / 1e6 > MAX_MB and len(colours) > 4:
        print(f"over {MAX_MB:.0f} MB: run again with --colours {','.join(colours[:4])}")


if __name__ == "__main__":
    main()
