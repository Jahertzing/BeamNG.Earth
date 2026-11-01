"""Views for the road QA street shots: writes <out>/views.json in audit/audit_shots.py form, spots picked off roadnet.json."""
import json
import math
import pickle
import sys

import numpy as np
from scipy.spatial import cKDTree

NET = "C:/beamng-earth/Earth.UE/export/roadnet"
OUT = sys.argv[1]
net = json.load(open(NET + "/roadnet.json"))
links = [k for k in net["links"] if k["level"] == 0]
P, I, U = [], [], []
for n, k in enumerate(links):
    p = np.array(k["points"])
    L = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p[:, :2], axis=0).T))]
    if L[-1] < 1.0:
        continue
    t = np.r_[np.arange(0.0, L[-1], 4.0), L[-1]]
    q = np.c_[np.interp(t, L, p[:, 0]), np.interp(t, L, p[:, 1]), np.interp(t, L, p[:, 2])]
    u = np.gradient(q[:, :2], axis=0)
    P.append(q)
    U.append(u / np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-6))
    I += [n] * len(q)
P, U = np.concatenate(P), np.concatenate(U)
tree = cKDTree(P[:, :2])
views = []


def add(name, x, y, heading=None, off=0.0, h=1.3, hour=10.0, wx="clear", pitch=0.0, klass=None):
    """A driver's view on the road nearest (x, y), heading degrees (0 east, 90 north), off m to the right."""
    d, k = tree.query((x, y), k=400)
    if klass:
        k = [q for q in k if links[I[q]]["class"] in klass] or k
    k = int(k[0])
    u = U[k]
    if heading is not None:
        v = np.array([math.cos(math.radians(heading)), math.sin(math.radians(heading))])
        if float(u @ v) < 0:
            u = -u
    p = P
    j = k
    q = p[j, :2] + np.array([u[1], -u[0]]) * off
    views.append([name, round(float(q[0]), 2), round(float(q[1]), 2), round(float(p[j, 2]) + h, 2), round(-math.degrees(math.atan2(u[1], u[0])), 1), pitch, hour, wx, h])


add("i25_nb_a", -3670, -2600, 90, 3.9, klass={"motorway"})
add("i25_nb_b", -3667, -1700, 90, 0.0, klass={"motorway"})
add("i25_nb_shoulder", -3668, -3400, 90, 7.5, klass={"motorway"})
add("i25_sb_a", -3706, 300, -90, 3.9, klass={"motorway"})
add("i25_sb_b", -3708, -1000, -90, 0.0, klass={"motorway"})
add("i25_north_c", -3640, 4800, 90, 3.9, klass={"motorway"})
add("lyden_e", 1140, -406, 0)
add("lyden_w", 1440, -406, 180)
add("lyden_mid", 1300, -406, 0)
add("old_arterial", -40, -61, 0)
add("old_resid_n", 52, 80, 90)
add("old_resid_e", 0, 39, 0)
add("old_alley", -50, 87, 0, klass={"service"})
add("firestone_street", 0, 1500, 90)
add("hwy52_east", -1500, -1272, 0, 1.8)
add("hwy52_west", 1250, -1270, 180, 1.8)
add("colorado_blvd", -404, 3198, 90, 1.8)
add("frontage_e", -3648, -3777, 90)
n = 0
for lk in links:
    if lk["control"] == "signal" and lk["class"] in ("primary", "secondary") and n < 4 and not lk["ramp"]:
        p = np.array(lk["points"])
        L = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p[:, :2], axis=0).T))]
        if L[-1] < 90 or any(abs(p[-1, 0] - float(views[i][1])) < 400 and abs(p[-1, 1] - float(views[i][2])) < 400 for i in range(len(views)) if views[i][0].startswith("signal")):
            continue
        s = L[-1] - 45.0
        a = np.array([np.interp(s, L, p[:, 0]), np.interp(s, L, p[:, 1])])
        add(f"signal_{n}", float(a[0]), float(a[1]), math.degrees(math.atan2(*(p[-1, :2] - a)[::-1])), 1.8)
        n += 1
n = 0
for lk in links:
    if lk["control"] == "signal" and lk["class"] in ("primary", "secondary", "trunk") and n < 6 and not lk["ramp"] and len(lk["lanes"]) >= 2:
        p = np.array(lk["points"])
        L = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p[:, :2], axis=0).T))]
        if L[-1] < 60 or int(lk["id"]) % 7:
            continue
        s = L[-1] - 26.0
        a = np.array([np.interp(s, L, p[:, 0]), np.interp(s, L, p[:, 1])])
        add(f"close_{n}", float(a[0]), float(a[1]), math.degrees(math.atan2(*(p[-1, :2] - a)[::-1])), 1.8, pitch=9.0)
        n += 1
inf = pickle.load(open(NET + "/furniture_infra.pkl", "rb"))
T = inf["power_tower"]
T = T[(np.abs(T) < 9000).all(1)]
d, _ = tree.query(T)
for k, i in enumerate(np.argsort(np.abs(d - 60.0))[:60:30]):
    add(f"tower_{k}", float(T[i, 0]), float(T[i, 1]), None, 0.0)
add("county_road", -2000, -9000, klass={"unclassified", "tertiary"})
add("subdivision", -4300, -230, klass={"residential"})
add("night_signal", -3241, -1330, 90, 1.8, hour=21.5, klass={"secondary", "tertiary", "primary"})
add("night_old", 0, 39, 0, hour=21.5)
add("night_lyden", 1300, -406, 0, hour=21.5)
add("night_arterial", -40, -61, 0, hour=21.5)
json.dump({"views": views, "clips": {}}, open(OUT + "/views.json", "w"))
print(len(views))
for v in views:
    print(v)
