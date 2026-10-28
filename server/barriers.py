"""Highway barriers (the user, 2026-10-28: "a beautiful, fun drive from Frederick to downtown Denver and on to Castle
Rock"; the route roadmap's highway roadside, which had none): concrete median barriers, W-beam guardrails, cable
barriers and noise walls along motorways and expressways, drawn into each road tile's colliding mesh (roadmesh.tunnel,
with the retaining walls). They are made with the road tile, so a pre-built route has them (nothing streams).

Where they stand:
- mapped: OpenStreetMap's barrier lines (Overture infrastructure, subtype "barrier"): guard_rail, jersey_barrier,
  cable_barrier, and the walls and fences tagged as noise barriers (wall=noise_barrier, noise_barrier=yes), where
  they keep off every carriageway;
- a motorway's median (or an expressway's: a divided trunk that is no arterial, roads._arterial): two carriageways side
  by side, parallel, their edges MEDIAN_JERSEY_M or less apart: a concrete F-shape barrier halfway between the edges
  (CDOT's Type 7, 42 in); further apart, up to MEDIAN_CABLE_M, and running opposite ways: a four-cable barrier on the
  median's middle line (CDOT's high-tension cable rail, I-25 south of Castle Rock);
- a motorway's, expressway's or ramp's outer edge (and a wide median's): a W-beam guardrail (31 in MGS on steel posts
  every 6 ft 3 in) where the ground falls GUARD_DROP_M within GUARD_PROBE_M of the edge (an embankment), over
  GUARD_DECK_M before a bridge or a tunnel (the road ends with no ground road going on) and past an overpass' piers;
- in the US and Canada the rural roads' stone parapets (roads._parapets: Alpine dry-stone walls) as W-beam guardrails.
Procedural barriers give way to mapped ones nearby (OSM_NEAR_M). A W-beam's real ends turn down into the ground (a
buried terminal) over TERMINAL_M; where a run only stops at a tile edge or the end of a road feature it goes on in the
next one. Pure given its inputs.

A barrier is a dict: kind ("jersey", "wbeam", "cable", "noise"), line (k, 2) frame metres, z (k,) its base (the ground
at the line, a median barrier's the higher carriageway's edge), foot (k,) how far down its face reaches, h m tall,
face (+1 / -1: the traffic's side, the line's left / right; 0 both), ends ((bool, bool): real ends), fid (its road's
feature id, or the mapped line's), side (+1 left / -1 right of its road's geometry, 0 a median or a mapped line), mapped
(OpenStreetMap's).
"""
import json
import math

import numpy as np

STEP_M = 2.0                # m between the samples along a road (as its retaining walls')
RUN_MARGIN_M = 60.0         # m past the tile a road is sampled: whether a run's end there is real (a terminal)
MAIN_CLASSES = {"motorway", "trunk"}
# the median
MEDIAN_JERSEY_M = 12.0      # m between two carriageways' edges at most for a concrete barrier between them ...
MEDIAN_CABLE_M = 35.0       # ... and for a cable barrier (opposite ways only); wider medians have none
MEDIAN_MIN_M = -0.3         # m: carriageways overlapping more than this are one road
PARALLEL_DEG = 25.0         # a carriageway within this of a road's direction runs beside it ...
ACROSS_M = 4.0              # ... where its nearest point lies within this along the road (square across from it)
# the edges
EDGE_OFF_M = 0.6            # m from a carriageway's edge to a guardrail's face (a rural motorway's width has its
                            # 2.2 m shoulder already; roads.carriageway)
GUARD_DROP_M = 1.8          # m the ground falls ...
GUARD_PROBE_M = 7.0         # ... within this beyond the edge: an embankment, a guardrail along it
GUARD_END_M = 36.0          # m of guardrail before a road ends with no ground road going on (a bridge or a tunnel; under
                            # roads.NEAR_M, so the road going on is always among the tile's roads)
GUARD_JOIN_M = 1.5          # m: another ground road's end this near a road's end: it goes on
GUARD_UNDER_M = 20.0        # m of guardrail either side of a stretch under another road's deck (its piers)
GUARD_ROAD_GAP_M = 3.0      # m a guardrail keeps from any other carriageway (ramps leaving, gores, junctions)
MEDIAN_ROAD_GAP_M = 1.5     # m a median barrier keeps from a crossing road's carriageway (a trunk's junctions)
GAP_FILL_N = 4              # a run's gaps of this many samples or fewer are closed ...
MIN_RUN_N = 6               # ... and runs shorter than this many samples dropped (12 m)
OSM_NEAR_M = 4.0            # m: a procedural barrier this near a mapped guardrail / barrier gives way to it
OSM_ROAD_GAP_M = 0.2        # m a mapped barrier's line keeps from every carriageway (our widths are estimates: a mapped
                            # guardrail on a lane where we draw the road wider is left out there)
SIMPLIFY_M = 0.04           # m: a barrier's line simplified (xy) ...
SIMPLIFY_Z_M = 0.03         # ... and its heights
# the shapes (m): CDOT / AASHTO standards
JERSEY_H = 1.07             # a median's F-shape (Type 7: 42 in)
JERSEY_BASE = 0.61          # its base (24 in) ...
JERSEY_TOE_H = 0.076        # ... a 3 in reveal, the 55-degree slope up to ...
JERSEY_BREAK_H = 0.254      # ... 10 in, then 84 degrees to the top
JERSEY_BREAK_W = 0.36
JERSEY_TALL_M = 3.0         # m from a median barrier's foot to its top at most (two carriageways at very different
                            # heights: a slope between, no barrier)
FOOT_M = 0.3                # m a barrier reaches under the ground
WBEAM_TOP = 0.787           # a W-beam rail's top over the ground (31 in, MGS) ...
WBEAM_DEPTH = 0.311         # ... 12.25 in deep, its two peaks ...
WBEAM_BULGE = 0.083         # ... 3.25 in out from its flanges
WBEAM_BLOCK = 0.2           # m of blockout between the rail's back and its post
POST_W = 0.15               # a W-beam post's section (W6x9 steel)
POST_EVERY = 1.905          # m between posts (6 ft 3 in)
POST_DOWN_M = 0.9           # m a post reaches into the ground
TERMINAL_M = 7.6            # m over which a W-beam's real end turns down into the ground (25 ft)
CABLE_HS = (0.53, 0.64, 0.75, 0.86)     # m: a cable barrier's four cables ...
CABLE_R = 0.012             # ... 3/4 in wire rope (drawn this half thick)
CABLE_POST_EVERY = 3.2      # m between its posts ...
CABLE_POST_W = 0.08         # ... S3x5.7 posts (drawn square)
CABLE_POST_H = 0.95
NOISE_H_M = 4.5             # a noise wall's height where the map has none (CDOT's are 3-6 m) ...
NOISE_H_RANGE = (2.0, 9.0)  # ... a mapped height outside this not believed
NOISE_T = 0.25              # its panels' thickness ...
PILASTER_EVERY = 6.0        # ... between pilasters (H-piles in concrete) every 6 m ...
PILASTER_W = (0.45, 0.5)    # ... this wide along it and across ...
PILASTER_OVER = 0.15        # ... standing this over the panels
UV_M = 4.0                  # m per texture repeat (as roadmesh.CONCRETE_UV_M)
STEEL_UV_M = 2.0
# the tunnel mesh's material indices (roadmesh.tunnel's allm)
MAT_CONCRETE, MAT_STEEL, MAT_NOISE = 0, 6, 7

# OpenStreetMap's barrier classes (Overture infrastructure class) and the walls / fences that are noise barriers
OSM_KINDS = {"guard_rail": "wbeam", "jersey_barrier": "jersey", "cable_barrier": "cable", "noise_barrier": "noise"}
NOISE_CLASSES = {"wall", "fence"}
SAFETY = {"wbeam", "jersey", "cable"}       # the kinds a procedural barrier gives way to


def _noise(tags):
    """Do a feature's OpenStreetMap tags (Overture source_tags as JSON) make it a noise barrier?"""
    s = "" if tags is None else str(tags)
    if "noise" not in s:
        return False
    try:
        t = json.loads(s)
    except ValueError:
        return "noise_barrier" in s
    if isinstance(t, list):         # (a map read as [{"key", "value"}])
        t = {str(e.get("key")): e.get("value") for e in t if isinstance(e, dict)}
    if not isinstance(t, dict):
        return "noise_barrier" in s
    return (str(t.get("wall")) == "noise_barrier" or str(t.get("noise_barrier")) in ("yes", "true", "1")
            or str(t.get("fence_type")) == "noise_barrier")


def mapped_lines(frame, infra_list):
    """[(kind, xy (k, 2) frame metres, height m or None, id)] of OpenStreetMap's barriers (OSM_KINDS, noise walls) in
    infra_list (infrastructure Features of map cells; duplicates taken once). Pure."""
    import mapgeom
    seen, out = set(), []
    for f in infra_list or ():
        if f is None or len(f) == 0:
            continue
        cls = f.attrs.get("class")
        if cls is None:
            continue
        cls = np.asarray(cls).astype(str)
        hit = np.isin(cls, list(OSM_KINDS) + list(NOISE_CLASSES))
        sub = f.attrs.get("subtype")
        if sub is not None:
            hit &= np.asarray(sub).astype(str) == "barrier"
        tags, hs = f.attrs.get("tags"), f.attrs.get("height")
        for i in np.flatnonzero(hit):
            kind = OSM_KINDS.get(cls[i])
            if kind is None:
                if tags is None or not _noise(tags[i]):
                    continue
                kind = "noise"
            fid = str(f.ids[i])
            if fid in seen:
                continue
            seen.add(fid)
            h = None
            if hs is not None:
                try:
                    h = float(hs[i])
                except (TypeError, ValueError):
                    h = None
                if h is not None and not (NOISE_H_RANGE[0] <= h <= NOISE_H_RANGE[1]):
                    h = None
            for part in f.parts(i):
                for ring in part:
                    if len(ring) < 2:
                        continue
                    e, n = mapgeom.enu_xy(f, frame, ring)
                    out.append((kind, np.column_stack([np.asarray(e, np.float64), np.asarray(n, np.float64)]), h, fid))
    return out


def mapped_for(md, frame, keys):
    """mapped_lines of the map cells keys (md: mapdata.MapData); [] when their infrastructure cannot be read."""
    try:
        return mapped_lines(frame, [md.cell(k, layers=("infrastructure",), kind="demand").layers.get("infrastructure")
                                    for k in keys])
    except Exception as e:          # procedural barriers alone rather than no road tile
        import logging
        logging.getLogger("earthd.barriers").debug("mapped barriers: %s: %s", type(e).__name__, e)
        return []


def _main(spec):
    """A carriageway whose median gets a barrier: a one-way motorway's, or an expressway's (no arterial), no ramp."""
    import roads
    return (spec.cls in MAIN_CLASSES and spec.kind in roads.BUILT and spec.oneway and not spec.link
            and not (spec.cls == "trunk" and roads._arterial(spec)))


def _guarded(spec):
    """A road whose edges get guardrails: a motorway's or an expressway's carriageway or ramp."""
    import roads
    return (spec.cls in MAIN_CLASSES and spec.kind in roads.BUILT
            and not (spec.cls == "trunk" and not spec.link and roads._arterial(spec)))


def _ground_fn(frame, elev):
    def ground(P):
        P = np.asarray(P, np.float64).reshape(-1, 2)
        if not len(P):
            return np.zeros(0)
        la, lo, _ = frame.enu_to_geodetic(P[:, 0], P[:, 1], np.zeros(len(P)))
        return np.asarray(frame.geodetic_to_enu(la, lo, np.asarray(elev(la, lo), np.float64))[2], np.float64)
    return ground


def _runs(mask, fill=GAP_FILL_N, min_n=MIN_RUN_N):
    """[(i, j)] inclusive index ranges of mask's runs, gaps of `fill` or fewer closed, runs under min_n dropped."""
    m = np.asarray(mask, bool).copy()
    idx = np.flatnonzero(m)
    if len(idx) >= 2 and fill > 0:
        for a, b in zip(idx[:-1], idx[1:]):
            if 1 < b - a <= fill + 1:
                m[a:b] = True
    out, i = [], 0
    while i < len(m):
        if not m[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(m) and m[j + 1]:
            j += 1
        if j - i + 1 >= min_n:
            out.append((i, j))
        i = j + 1
    return out


def _dilate(mask, s, reach):
    """mask grown by `reach` m of arc length s either way along the road."""
    idx = np.flatnonzero(mask)
    if not len(idx):
        return mask.copy()
    lo = np.searchsorted(s, s[idx] - reach, "left")
    hi = np.searchsorted(s, s[idx] + reach, "right")
    d = np.zeros(len(s) + 1, np.int64)
    np.add.at(d, lo, 1)
    np.add.at(d, hi, -1)
    return np.cumsum(d)[:-1] > 0


class _Mains:
    """The main carriageways near a tile as one table of segments: a, b (m, 2), half width, travel direction (unit, (m,
    2): along the geometry for dir +1), each one's road index."""

    def __init__(self, rows):
        A, B, H, R, V = [], [], [], [], []
        for r, (xy, half, d) in enumerate(rows):
            if len(xy) < 2:
                continue
            a, b = xy[:-1], xy[1:]
            ab = b - a
            ln = np.maximum(np.hypot(*ab.T), 1e-9)
            A.append(a)
            B.append(b)
            H.append(np.full(len(a), half))
            R.append(np.full(len(a), r))
            V.append(ab / ln[:, None] * (d if d else 0.0))
        f = np.float64
        self.a = np.concatenate(A) if A else np.zeros((0, 2), f)
        self.b = np.concatenate(B) if B else np.zeros((0, 2), f)
        self.h = np.concatenate(H) if H else np.zeros(0, f)
        self.r = np.concatenate(R) if R else np.zeros(0, np.int64)
        self.v = np.concatenate(V) if V else np.zeros((0, 2), f)

    def beside(self, P, D, N, rh, own):
        """Per point P (k, 2) of road `own` (direction D, left normal N, half width rh) and side (+1 left, -1 right): the
        edge-to-edge gap to the nearest main carriageway running beside it there (inf: none within MEDIAN_CABLE_M), its
        road index and whether it runs the other way. {side: (gap (k,), road (k,), opposite (k,))}. Pure."""
        k = len(P)
        out = {s: (np.full(k, np.inf), np.full(k, -1), np.zeros(k, bool)) for s in (1, -1)}
        if not len(self.h) or not k:
            return out
        reach = rh + MEDIAN_CABLE_M + float(self.h.max()) + ACROSS_M
        lo, hi = P.min(0) - reach, P.max(0) + reach
        sel = np.flatnonzero((self.r != own) & (np.maximum(self.a[:, 0], self.b[:, 0]) >= lo[0])
                             & (np.minimum(self.a[:, 0], self.b[:, 0]) <= hi[0])
                             & (np.maximum(self.a[:, 1], self.b[:, 1]) >= lo[1])
                             & (np.minimum(self.a[:, 1], self.b[:, 1]) <= hi[1]))
        if not len(sel):
            return out
        a, b, h, v = self.a[sel], self.b[sel], self.h[sel], self.v[sel]
        ab = b - a
        L2 = np.maximum((ab * ab).sum(1), 1e-12)
        t = np.clip(((P[:, None, :] - a[None]) * ab[None]).sum(-1) / L2[None], 0.0, 1.0)
        V = a[None] + t[..., None] * ab[None] - P[:, None, :]
        lat = (V * N[:, None, :]).sum(-1)
        along = (V * D[:, None, :]).sum(-1)
        u = ab / np.sqrt(L2)[:, None]
        par = np.abs((u[None] * D[:, None, :]).sum(-1)) >= math.cos(math.radians(PARALLEL_DEG))
        for s in (1, -1):
            gap = s * lat - rh - h[None]
            ok = par & (s * lat > 0) & (np.abs(along) <= ACROSS_M) & (gap >= MEDIAN_MIN_M) & (gap <= MEDIAN_CABLE_M)
            g = np.where(ok, gap, np.inf)
            j = np.argmin(g, axis=1) if len(sel) else np.zeros(k, np.int64)
            best = g[np.arange(k), j]
            out[s] = (best, np.where(np.isfinite(best), self.r[sel][j], -1), (v[j] * D).sum(1))
        return out


def tile_barriers(frame, roads, elev, exact, decks=(), mapped=None, parapets=(), left_hand=False):
    """The barriers (see the module doc) of the tile box `exact` (x0, y0, x1, y1): roads as roads.build_tile's (fid,
    spec, centreline (k, 3), lon) near the tile, elev(lat, lon) the graded ground, decks the bridge deck chunks near the
    tile (roads._under_decks), mapped: mapped_lines near the tile, parapets: roads._parapets' walls to stand as W-beam
    guardrails instead. Deterministic (sorted by feature id)."""
    import grading
    import roads as roadsmod
    x0, y0, x1, y1 = exact
    ground = _ground_fn(frame, elev)
    near_box = (x0 - RUN_MARGIN_M, y0 - RUN_MARGIN_M, x1 + RUN_MARGIN_M, y1 + RUN_MARGIN_M)
    built = [(fid, spec, np.asarray(pts, np.float64)) for fid, spec, pts, _ in roads
             if spec.kind in roadsmod.BUILT and len(pts) >= 2]
    built.sort(key=lambda r: r[0])
    mapped = [m for m in (mapped or []) if len(m[1]) >= 2]
    if not mapped and not parapets and not decks and not any(_guarded(spec) for _, spec, _ in built):
        return []                   # (most road tiles: no motorway, nothing mapped)
    others = roadsmod._Segs({fid: (pts[:, :2], spec.width / 2) for fid, spec, pts in built})
    mains_rows, main_of = [], {}
    for fid, spec, pts in built:
        if _main(spec):
            main_of[fid] = len(mains_rows)
            mains_rows.append((pts[:, :2], spec.width / 2, float(spec.dir or 0)))
    mains = _Mains(mains_rows)
    main_fids = [fid for fid, spec, _ in built if _main(spec)]
    side_roads = roadsmod._Segs({fid: (pts[:, :2], spec.width / 2) for fid, spec, pts in built if not _main(spec)})
    main_segs = roadsmod._Segs({fid: (pts[:, :2], spec.width / 2) for fid, spec, pts in built if _main(spec)})
    ends = np.array([p for _, _, pts in built for p in (pts[0, :2], pts[-1, :2])], np.float64).reshape(-1, 2)

    keep_off = [(xy, 0.0) for kind, xy, _h, _f in mapped if kind in SAFETY]

    def off_mapped(pts):
        """Which points (k, 2) keep OSM_NEAR_M from every mapped safety barrier."""
        if not keep_off or not len(pts):
            return np.ones(len(pts), bool)
        return ~roadsmod._Segs({str(n): ln for n, ln in enumerate(keep_off)}).near(pts, OSM_NEAR_M)

    # the roads' samples, the probes' points for one ground call
    info, probe_pts = [], []
    for fid, spec, pts in built:
        if not _guarded(spec):
            continue
        xy, s = grading.resample(pts[:, :2], STEP_M)
        if len(xy) < 2:
            continue
        ext = ((xy[:, 0] >= near_box[0]) & (xy[:, 0] <= near_box[2]) & (xy[:, 1] >= near_box[1])
               & (xy[:, 1] <= near_box[3]))
        if not ext.any():
            continue
        seg = np.hypot(*np.diff(pts[:, :2], axis=0).T)
        z = np.interp(s, np.r_[0.0, np.cumsum(seg)], pts[:, 2])
        D = np.gradient(xy, axis=0)
        D /= np.maximum(np.hypot(*D.T), 1e-9)[:, None]
        N = np.stack([-D[:, 1], D[:, 0]], axis=1)
        # the road ends with no ground road going on (a bridge, a tunnel): guardrail before it
        end_run = np.zeros(len(xy), bool)
        for at in (0, len(xy) - 1):
            if (np.hypot(*(ends - xy[at]).T) <= GUARD_JOIN_M).sum() <= 1:      # (its own end only)
                end_run |= np.abs(s - s[at]) <= GUARD_END_M
        # (the stretch near the tile only: a motorway feature can be kilometres long)
        idx = np.flatnonzero(ext)
        sl = slice(max(int(idx[0]) - 1, 0), int(idx[-1]) + 2)
        xy, s, z, D, N, ext, end_run = xy[sl], s[sl], z[sl], D[sl], N[sl], ext[sl], end_run[sl]
        rh = spec.width / 2
        bes = mains.beside(xy, D, N, rh, main_of.get(fid, -1)) if _main(spec) else \
            {sd: (np.full(len(xy), np.inf), np.full(len(xy), -1), np.zeros(len(xy), bool)) for sd in (1, -1)}
        steps = rh + np.arange(0.5, GUARD_PROBE_M + 0.01, 1.0)
        rec = dict(fid=fid, spec=spec, xy=xy, s=s, z=z, D=D, N=N, rh=rh, ext=ext, bes=bes, end_run=end_run,
                   steps=steps, k0=len(probe_pts))
        for sd in (1, -1):
            probe_pts.append(xy + N * sd * (rh + EDGE_OFF_M))                   # the guardrail's face
            for o in steps:
                probe_pts.append(xy + N * sd * o)
        info.append(rec)
    g = ground(np.concatenate(probe_pts)) if probe_pts else np.zeros(0)
    probe_off = np.r_[0, np.cumsum([len(pp) for pp in probe_pts])]

    raw = []            # (kind, line (k, 2), z, foot, h, face, (real0, real1), fid, side)
    med_pts = []        # the median barriers' three ground points per sample, one ground call below
    for rec in info:
        xy, s, z, N, rh, ext, spec, fid = rec["xy"], rec["s"], rec["z"], rec["N"], rec["rh"], rec["ext"], rec["spec"], \
            rec["fid"]
        k = len(xy)
        steps, end_run = rec["steps"], rec["end_run"]
        for si, sd in enumerate((1, -1)):
            gap, partner, opp = rec["bes"][sd]
            # (each side's probes: its face's points, then each step's; k points each)
            off = int(probe_off[rec["k0"] + si * (1 + len(steps))])
            gface = g[off:off + k]
            gs = g[off + k:off + k * (1 + len(steps))].reshape(len(steps), k).T
            jersey = np.isfinite(gap) & (gap <= MEDIAN_JERSEY_M)
            # (opposite ways: the other's travel against this one's, opp along the geometry times its direction)
            cable = np.isfinite(gap) & (gap > MEDIAN_JERSEY_M) & (gap <= MEDIAN_CABLE_M) & (opp * (spec.dir or 0) < 0)
            # the median: drawn by the lower feature id of the two (each spot once)
            if _main(spec):
                pf = np.array([main_fids[int(p)] if p >= 0 else "" for p in partner])
                mine = pf > fid
                for kind, want in (("jersey", jersey & mine & ext), ("cable", cable & mine & ext)):
                    if not want.any():
                        continue
                    mid = xy + N * sd * (rh + np.where(np.isfinite(gap), gap, 0.0)[:, None] / 2)
                    ok = want & ~side_roads.near(mid, MEDIAN_ROAD_GAP_M) & ~main_segs.near(mid, -0.2) & off_mapped(mid)
                    for i, j in _runs(ok, min_n=MIN_RUN_N if kind == "jersey" else MIN_RUN_N * 2):
                        sl = slice(i, j + 1)
                        med_pts.append((len(raw), mid[sl], xy[sl] + N[sl] * sd * rh,
                                        xy[sl] + N[sl] * sd * (rh + gap[sl, None])))
                        raw.append([kind, mid[sl], None, None, JERSEY_H if kind == "jersey" else CABLE_POST_H, 0,
                                    (i > 0 and bool(ext[i - 1]), j < k - 1 and bool(ext[j + 1])), fid, 0])
            # the edge: a guardrail on an embankment, before a bridge, past an overpass
            covered = jersey | cable
            drop = (gface[:, None] - gs).max(axis=1) >= GUARD_DROP_M
            face = xy + N * sd * (rh + EDGE_OFF_M)
            under = _dilate(roadsmod._under_decks(face, z, list(decks)), s, GUARD_UNDER_M) if decks else np.zeros(k, bool)
            want = (drop | end_run | under) & ~covered & ext
            if not want.any():
                continue
            want &= ~others.near(face, GUARD_ROAD_GAP_M, skip=fid) & off_mapped(face)
            for i, j in _runs(want):
                sl = slice(i, j + 1)
                # (a real end: the run stops inside the sampled stretch, not at its margin or the road's end)
                real = (i > 0 and bool(ext[i - 1]), j < k - 1 and bool(ext[j + 1]))
                raw.append(["wbeam", face[sl], gface[sl], gface[sl] - POST_DOWN_M, WBEAM_TOP, -sd, real, fid, sd])
    if med_pts:
        allp = np.concatenate([np.concatenate([m, a, b]) for _, m, a, b in med_pts])
        gm = ground(allp)
        o = 0
        for n, m, a, b in med_pts:
            k = len(m)
            gmid, ga, gb = gm[o:o + k], gm[o + k:o + 2 * k], gm[o + 2 * k:o + 3 * k]
            o += 3 * k
            r = raw[n]
            if r[0] == "jersey":
                top = np.maximum.reduce([gmid, ga, gb])
                foot = np.minimum.reduce([gmid, ga, gb]) - FOOT_M
                r[2], r[3] = top, foot
            else:
                r[2], r[3] = gmid, gmid - FOOT_M
    raw = [r for r in raw if r[2] is not None]
    # a median barrier with its carriageways too far apart in height: none there (a slope between them)
    tall = []
    for r in raw:
        if r[0] != "jersey":
            tall.append(r)
            continue
        ok = (r[2] - r[3]) <= JERSEY_TALL_M + FOOT_M
        for i, j in _runs(ok, fill=0, min_n=MIN_RUN_N):
            sl = slice(i, j + 1)
            tall.append([r[0], r[1][sl], r[2][sl], r[3][sl], r[4], r[5],
                         (r[6][0] if i == 0 else True, r[6][1] if j == len(ok) - 1 else True), r[7], r[8]])
    raw = tall
    # the medians between twin bridge decks joined into one slab (roadmesh: no parapets there)
    raw.extend(_deck_medians(decks, near_box))
    # the rural roads' parapets as guardrails (the US's and Canada's)
    for w in parapets:
        b = np.asarray(w["base"], np.float64)
        tw = np.asarray(w["toward"], np.float64)
        if len(b) < 2:
            continue
        line = b - tw * (EDGE_OFF_M - roadsmod.PARAPET_OFF_M)
        line_n = _left(line)
        face = 1 if float((tw * line_n).sum()) >= 0 else -1
        zr = np.asarray(w["road_z"], np.float64)
        raw.append(["wbeam", line, zr, zr - POST_DOWN_M, WBEAM_TOP, face, (True, True), "parapet", 0])
    # the mapped ones, where they keep off the carriageways
    if mapped:
        for kind, xy, h, mid_ in mapped:
            for piece in roadsmod.clip_polyline(xy, *near_box):
                if len(piece) < 2:
                    continue
                pts, _s = grading.resample(piece[:, :2], STEP_M)
                if len(pts) < 2:
                    continue
                half = {"jersey": JERSEY_BASE / 2, "noise": NOISE_T / 2}.get(kind, 0.1)
                gz = ground(pts)
                # (none on a carriageway, nor on a bridge: its ground is the valley under the deck)
                ok = ~others.near(pts, half + OSM_ROAD_GAP_M)
                if decks:
                    ok &= ~roadsmod._under_decks(pts, gz, list(decks))
                for i, j in _runs(ok, fill=0, min_n=2):
                    sl = slice(i, j + 1)
                    line = pts[sl]
                    face = _face_toward(line, built) if kind == "wbeam" else 0
                    hh = {"jersey": JERSEY_H, "wbeam": WBEAM_TOP, "cable": CABLE_POST_H}.get(kind, h or NOISE_H_M)
                    foot = gz[sl] - (POST_DOWN_M if kind == "wbeam" else FOOT_M)
                    raw.append([kind, line, gz[sl], foot, float(hh), face, (True, True), mid_, 0])
    return _clip_out(raw, exact, {m[3] for m in mapped})


DECK_MEDIAN_MIN_W = 7.0     # m: a deck this wide or wider (a carriageway's) gets a median barrier beside its twin
DECK_DZ_M = 1.0             # m two decks' surfaces may differ at most for one barrier between them


def _deck_medians(decks, box):
    """Median barriers between two parallel bridge decks whose edges lie under roadmesh.MEDIAN_JOIN_M apart (roadmesh.deck
    joins their slabs and leaves the parapets out: I-25's twin bridges were open to the other carriageway), as
    tile_barriers' raw rows, on the slab at the higher deck's level; each spot by the lower feature id. decks: deck
    chunks ("n" points (k, 3), "w", "fid"); only those with "ctx" (each deck's whole stretch near the tile once). Pure."""
    import grading
    import roadmesh
    rows = [(str(c.get("fid")), np.asarray(c["n"], np.float64)[:, :3], float(c.get("w") or 0) / 2) for c in decks or ()
            if c.get("ctx") and c.get("fid") is not None and float(c.get("w") or 0) >= DECK_MEDIAN_MIN_W
            and len(c.get("n") or ()) >= 2]
    if len(rows) < 2:
        return []
    A = np.concatenate([n[:-1] for _f, n, _h in rows])
    B = np.concatenate([n[1:] for _f, n, _h in rows])
    H = np.concatenate([np.full(len(n) - 1, h) for _f, n, h in rows])
    F = np.concatenate([np.full(len(n) - 1, f, dtype=object) for f, n, _h in rows])
    ab = B[:, :2] - A[:, :2]
    L2 = np.maximum((ab * ab).sum(1), 1e-12)
    u = ab / np.sqrt(L2)[:, None]
    out = []
    for fid, n, h in rows:
        xy, s = grading.resample(n[:, :2], STEP_M)
        if len(xy) < 2:
            continue
        z = np.interp(s, np.r_[0.0, np.cumsum(np.hypot(*np.diff(n[:, :2], axis=0).T))], n[:, 2])
        D = np.gradient(xy, axis=0)
        D /= np.maximum(np.hypot(*D.T), 1e-9)[:, None]
        N = np.stack([-D[:, 1], D[:, 0]], axis=1)
        sel = np.flatnonzero(F > fid)                   # (each spot by the lower id)
        if not len(sel):
            continue
        a, b2, hh, uu = A[sel], B[sel], H[sel], u[sel]
        t = np.clip(((xy[:, None, :] - a[None, :, :2]) * ab[sel][None]).sum(-1) / L2[sel][None], 0.0, 1.0)
        Q = a[None, :, :2] + t[..., None] * ab[sel][None]
        Qz = a[None, :, 2] + t * (b2[None, :, 2] - a[None, :, 2])
        V = Q - xy[:, None, :]
        lat = (V * N[:, None, :]).sum(-1)
        along = (V * D[:, None, :]).sum(-1)
        par = np.abs((uu[None] * D[:, None, :]).sum(-1)) >= math.cos(math.radians(PARALLEL_DEG))
        for sd in (1, -1):
            gap = sd * lat - h - hh[None]
            ok = (par & (sd * lat > 0) & (np.abs(along) <= ACROSS_M) & (gap >= MEDIAN_MIN_M)
                  & (gap < roadmesh.MEDIAN_JOIN_M) & (np.abs(Qz - z[:, None]) <= DECK_DZ_M))
            g = np.where(ok, gap, np.inf)
            j = np.argmin(g, axis=1)
            best = g[np.arange(len(xy)), j]
            have = np.isfinite(best) & (xy[:, 0] >= box[0]) & (xy[:, 0] <= box[2]) & (xy[:, 1] >= box[1]) \
                & (xy[:, 1] <= box[3])
            for i, k2 in _runs(have, min_n=3):
                sl = slice(i, k2 + 1)
                mid = xy[sl] + N[sl] * sd * (h + best[sl, None] / 2)
                zt = np.maximum(z[sl], Qz[np.arange(len(xy)), j][sl])
                zf = np.minimum(z[sl], Qz[np.arange(len(xy)), j][sl]) - FOOT_M
                out.append(["jersey", mid, zt, zf, JERSEY_H, 0, (False, False), fid, 0])
    return out


def _left(line):
    d = np.gradient(np.asarray(line, np.float64), axis=0)
    d /= np.maximum(np.hypot(*d.T), 1e-9)[:, None]
    return np.stack([-d[:, 1], d[:, 0]], axis=1)


def _face_toward(line, built):
    """+1 / -1: the side of a mapped guardrail's line (its left / right) where the nearest road is (its traffic)."""
    mid = line[len(line) // 2]
    best, side = np.inf, 1
    n = _left(line)[len(line) // 2]
    for _fid, _spec, pts in built:
        p = pts[:, :2]
        a, b = p[:-1], p[1:]
        ab = b - a
        t = np.clip(((mid - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0.0, 1.0)
        q = a + t[:, None] * ab
        d = np.hypot(*(q - mid).T)
        i = int(np.argmin(d))
        if d[i] < best:
            best, side = float(d[i]), (1 if float(((q[i] - mid) * n).sum()) >= 0 else -1)
    return side


def _clip_out(raw, exact, mapped_ids=()):
    """The barriers' dicts: each line clipped exactly to the tile box (its next tile draws the rest), simplified
    (mapped: OpenStreetMap's, mapped_ids)."""
    import roads as roadsmod
    x0, y0, x1, y1 = exact
    out = []
    for kind, line, z, foot, h, face, real, fid, side in raw:
        line = np.asarray(line, np.float64)
        P = np.column_stack([line, z, foot])
        for piece in roadsmod.clip_polyline(P, x0, y0, x1, y1):
            if len(piece) < 2 or np.hypot(*(piece[-1, :2] - piece[0, :2])) < 0.5:
                continue
            r0 = bool(real[0]) and np.allclose(piece[0, :2], line[0])
            r1 = bool(real[1]) and np.allclose(piece[-1, :2], line[-1])
            piece = roadsmod.simplify(piece, SIMPLIFY_M, SIMPLIFY_Z_M)
            out.append({"kind": kind, "line": piece[:, :2], "z": piece[:, 2], "foot": piece[:, 3], "h": float(h),
                        "face": int(face), "ends": (r0, r1), "fid": fid, "side": int(side),
                        "mapped": fid in mapped_ids})
    out.sort(key=lambda b: (b["kind"], str(b["fid"]), float(b["line"][0, 0]), float(b["line"][0, 1])))
    return out


# --- the mesh (roadmesh.tunnel: appended to its P, T, UV, M) ----------------------------------------------------------

def _arc(xy):
    return np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]


def _extrude(P, T, UV, M, nv, xy, nrm, zb, zf, prof, mat, uv_m, s, closed=False, both=False, caps=False):
    """A profile [(u, v, at_foot)] swept along a line xy (k, 2): u m along nrm (k, 2), v m over zb (k,), or at zf (k,)
    where at_foot. Quads between the profile's points (and its last and first: closed), faced away from the profile's
    middle (both: seen from both sides, a sheet); caps: the ends closed. Returns the new vertex count."""
    m, k = len(prof), len(xy)
    if k < 2 or m < 2:
        return nv
    u = np.array([p[0] for p in prof], np.float64)
    v = np.array([p[1] for p in prof], np.float64)
    rows = np.empty((m, k, 3))
    for j, (pu, pv, ft) in enumerate(prof):
        rows[j, :, :2] = xy + nrm * pu
        rows[j, :, 2] = zf if ft else zb + pv
    cu, cv = float(u.mean()), float(v.mean())
    plen = np.r_[0.0, np.cumsum(np.hypot(np.diff(u), np.diff(v)))]
    pairs = [(j, j + 1) for j in range(m - 1)] + ([(m - 1, 0)] if closed else [])
    i = np.arange(k - 1)
    for ja, jb in pairs:
        P.append(np.concatenate([rows[ja], rows[jb]]))
        va = plen[ja] if jb == ja + 1 else plen[-1]
        vb = plen[jb] if jb == ja + 1 else plen[-1] + math.hypot(u[0] - u[-1], v[0] - v[-1])
        UV.append(np.column_stack((np.r_[s, s] / uv_m, np.r_[np.full(k, va), np.full(k, vb)] / uv_m)))
        A, A1, B, B1 = nv + i, nv + i + 1, nv + k + i, nv + k + i + 1
        tri = np.concatenate([np.stack([A, B, B1], 1), np.stack([A, B1, A1], 1)])
        pts = np.concatenate([rows[ja], rows[jb]])
        t0, t1, t2 = pts[tri[:, 0] - nv], pts[tri[:, 1] - nv], pts[tri[:, 2] - nv]
        nn = np.cross(t1 - t0, t2 - t0)
        # outward: from the profile's middle to this side's middle, in the road's frame
        ou, ov = (u[ja] + u[jb]) / 2 - cu, (v[ja] + v[jb]) / 2 - cv
        out = np.c_[np.concatenate([nrm[i], nrm[i]]) * ou, np.full(2 * (k - 1), ov)]
        flip = (nn * out).sum(1) < 0
        tri[flip] = tri[flip][:, ::-1]
        T.append(np.concatenate([tri, tri[:, ::-1]]) if both else tri)
        M.append(mat)
        nv += 2 * k
    if caps:
        for at in (0, k - 1):
            q = rows[:, at]
            P.append(q)
            UV.append(np.column_stack((u / uv_m, v / uv_m)))
            fan = np.array([(nv, nv + j, nv + j + 1) for j in range(1, m - 1)], np.int64)
            T.append(np.concatenate([fan, fan[:, ::-1]]))
            M.append(mat)
            nv += m
    return nv


def _boxes(P, T, UV, M, nv, c, along, wa, wc, z0, z1, mat):
    """Upright boxes (posts, pilasters): centres c (n, 2), `along` (n, 2) unit, wa x wc m, from z0 to z1 (n,): four
    sides and a top. Returns the new vertex count."""
    n = len(c)
    if not n:
        return nv
    a = np.asarray(along, np.float64)
    x = np.stack([-a[:, 1], a[:, 0]], axis=1)
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    V = np.empty((n, 8, 3))
    for q, (sa, sx) in enumerate(corners):
        V[:, q, :2] = c + a * (sa * wa / 2) + x * (sx * wc / 2)
        V[:, q + 4, :2] = V[:, q, :2]
        V[:, q, 2] = z0
        V[:, q + 4, 2] = z1
    P.append(V.reshape(-1, 3))
    h = (np.asarray(z1) - np.asarray(z0))[:, None]
    uq = np.array([0.0, 0.25, 0.5, 0.75])
    UV.append(np.concatenate([np.stack([np.broadcast_to(uq, (n, 4)), np.zeros((n, 4))], -1),
                              np.stack([np.broadcast_to(uq, (n, 4)), np.repeat(h / UV_M, 4, 1)], -1)], 1).reshape(-1, 2))
    tri = []
    for q in range(4):
        r = (q + 1) % 4
        tri += [(q, r, r + 4), (q, r + 4, q + 4)]
    tri += [(4, 5, 6), (4, 6, 7)]
    tri = np.array(tri, np.int64)
    Tn = (tri[None] + (np.arange(n) * 8 + nv)[:, None, None]).reshape(-1, 3)
    pts = V.reshape(-1, 3)
    cen = np.repeat(np.c_[c, (np.asarray(z0) + np.asarray(z1)) / 2], len(tri), 0)
    t0, t1, t2 = pts[Tn[:, 0] - nv], pts[Tn[:, 1] - nv], pts[Tn[:, 2] - nv]
    flip = (np.cross(t1 - t0, t2 - t0) * ((t0 + t1 + t2) / 3 - cen)).sum(1) < 0
    Tn[flip] = Tn[flip][:, ::-1]
    T.append(Tn)
    M.append(mat)
    return nv + 8 * n


def _every(s, step, phase=0.5):
    """Arc lengths every `step` m along a line of length s[-1] (the first `phase` of a step in)."""
    L = float(s[-1])
    if L < step * 0.5:
        return np.array([L / 2])
    return np.arange(phase * step, L, step)


def _at(xy, s, at):
    return np.column_stack([np.interp(at, s, xy[:, 0]), np.interp(at, s, xy[:, 1])])


def _terminal(s, ends):
    """Per point: 0 at a real end rising (smoothstep) to 1 over TERMINAL_M (a W-beam's buried end), 1 elsewhere."""
    f = np.ones(len(s))
    for real, d in ((ends[0], s - s[0]), (ends[1], s[-1] - s)):
        if real:
            t = np.clip(d / TERMINAL_M, 0.0, 1.0)
            f = np.minimum(f, t * t * (3 - 2 * t))
    return f


def _dense(b, step=STEP_M):
    """A barrier's line, z and foot every `step` m at most (a terminal's curve needs the points)."""
    xy, z, foot = np.asarray(b["line"], np.float64), np.asarray(b["z"], np.float64), np.asarray(b["foot"], np.float64)
    s = _arc(xy)
    n = max(int(math.ceil(s[-1] / step)), 1)
    at = np.linspace(0.0, s[-1], n + 1)
    return _at(xy, s, at), np.interp(at, s, z), np.interp(at, s, foot), at


def mesh_into(barriers, origin, P, T, UV, M, nv):
    """The barriers appended to a mesh's P, T, UV, M (material indices MAT_*), in the frame less origin. Returns the new
    vertex count. Pure but for the lists."""
    o = np.asarray(origin, np.float64)
    for b in barriers:
        kind = b["kind"]
        if kind == "wbeam" and any(b["ends"]):
            xy, z, foot, s = _dense(b)
        else:
            xy, z, foot = np.asarray(b["line"], np.float64), np.asarray(b["z"], np.float64), np.asarray(b["foot"], np.float64)
            s = _arc(xy)
        if len(xy) < 2 or s[-1] < 0.5:
            continue
        xy = xy - o[:2]
        z = z - o[2]
        foot = foot - o[2]
        n = _left(xy)
        along = np.stack([n[:, 1], -n[:, 0]], axis=1)
        if kind == "jersey":
            h = float(b["h"])
            hb, hk = JERSEY_BASE / 2, JERSEY_BREAK_W / 2
            ht = max(hk - (h - JERSEY_BREAK_H) / math.tan(math.radians(84.0)), 0.05)
            prof = [(hb, 0.0, True), (hb, JERSEY_TOE_H, False), (hk, JERSEY_BREAK_H, False), (ht, h, False),
                    (-ht, h, False), (-hk, JERSEY_BREAK_H, False), (-hb, JERSEY_TOE_H, False), (-hb, 0.0, True)]
            nv = _extrude(P, T, UV, M, nv, xy, n, z, foot, prof, MAT_CONCRETE, UV_M, s, caps=True)
        elif kind == "noise":
            h = float(b["h"])
            prof = [(NOISE_T / 2, 0.0, True), (NOISE_T / 2, h, False), (-NOISE_T / 2, h, False),
                    (-NOISE_T / 2, 0.0, True)]
            nv = _extrude(P, T, UV, M, nv, xy, n, z, foot, prof, MAT_NOISE, UV_M, s, caps=True)
            at = _every(s, PILASTER_EVERY, 0.0)
            at = np.unique(np.r_[at, s[-1]])
            c = _at(xy, s, at)
            nv = _boxes(P, T, UV, M, nv, c, _at(along, s, at), PILASTER_W[0], PILASTER_W[1],
                        np.interp(at, s, foot), np.interp(at, s, z) + h + PILASTER_OVER, MAT_NOISE)
        elif kind == "cable":
            at = _every(s, CABLE_POST_EVERY)
            nv = _boxes(P, T, UV, M, nv, _at(xy, s, at), _at(along, s, at), CABLE_POST_W, CABLE_POST_W,
                        np.interp(at, s, foot), np.interp(at, s, z) + CABLE_POST_H, MAT_STEEL)
            for q, ch in enumerate(CABLE_HS):
                du = CABLE_POST_W / 2 + CABLE_R if q % 2 == 0 else -(CABLE_POST_W / 2 + CABLE_R)
                prof = [(du - CABLE_R, ch - CABLE_R, False), (du + CABLE_R, ch - CABLE_R, False),
                        (du + CABLE_R, ch + CABLE_R, False), (du - CABLE_R, ch + CABLE_R, False)]
                nv = _extrude(P, T, UV, M, nv, xy, n, z, foot, prof, MAT_STEEL, STEEL_UV_M, s, closed=True)
        elif kind == "wbeam":
            nf = n * float(b["face"] or 1)                  # toward the traffic
            f = _terminal(s, b["ends"])
            zr = z - (1.0 - f) * (WBEAM_TOP + 0.05)        # (its end down into the ground)
            c = WBEAM_TOP - WBEAM_DEPTH / 2
            hd = WBEAM_DEPTH / 2
            prof = [(-WBEAM_BULGE, c + hd, False), (0.0, c + hd * 0.55, False), (-WBEAM_BULGE * 0.75, c, False),
                    (0.0, c - hd * 0.55, False), (-WBEAM_BULGE, c - hd, False)]
            nv = _extrude(P, T, UV, M, nv, xy, nf, zr, foot, prof, MAT_STEEL, STEEL_UV_M, s, both=True)
            at = _every(s, POST_EVERY)
            fa = np.interp(at, s, f)
            at = at[fa > 0.35]                              # (none where the rail is down near the ground)
            if len(at):
                back = WBEAM_BULGE + WBEAM_BLOCK + POST_W / 2
                base = _at(xy, s, at) - _at(nf, s, at) * back
                ztop = np.interp(at, s, zr) + WBEAM_TOP - 0.05
                nv = _boxes(P, T, UV, M, nv, base, _at(along, s, at), POST_W, POST_W, np.interp(at, s, foot), ztop,
                            MAT_STEEL)
                blk = _at(xy, s, at) - _at(nf, s, at) * (WBEAM_BULGE + WBEAM_BLOCK / 2)
                zc = np.interp(at, s, zr) + c
                nv = _boxes(P, T, UV, M, nv, blk, _at(along, s, at), POST_W, WBEAM_BLOCK, zc - 0.18, zc + 0.18,
                            MAT_STEEL)
    return nv
