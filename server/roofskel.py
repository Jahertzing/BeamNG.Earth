"""Straight-skeleton roofs on a building's real outline (buildings.py: hipped and gabled roofs; 2026-10-28, the user's
"better buildings" list: an L-shaped house got the hipped roof of its oriented box, a box roof over a yard).

The roof of a pitched building is its outline's straight skeleton (Aichholzer et al. 1995): every wall's eave moves
inward at the same speed, and rises with the pitch as it goes, so each wall carries one roof plane, z = eave + d * tan
(pitch) at distance d from that wall's line; where two moving eaves meet they draw a hip (a convex corner) or a valley
(a reflex one), and the ridge is where the last ones meet. A rectangle gives exactly buildings._roof's hipped roof; an
L gets its two wings' ridges and a valley in the inner corner; a T three wings.

The skeleton is computed by the wavefront simulation of Felkel & Obdrzalek (1998) for a simple polygon without holes:
a list of active vertices (one cycle per remaining piece of the shrinking outline), each moving along its corner's
bisector at the speed that keeps it on both of its walls' offset lines; edge events (an edge of the wavefront shrinks
to nothing: its two vertices merge) and split events (a reflex vertex runs into the opposite part of the wavefront: the
piece splits in two) taken in time order from a heap. Everything is plain float arithmetic in a fixed order on the
ring as given, so the result is a pure function of the ring (no time limit: a cap on the number of events, MAX_EVENTS
per corner, gives up deterministically, never by the clock).

Gables (gable()): a gabled house turns each wing's end hip, a triangle whose apex is the end of a ridge, into a
vertical gable wall: the apex moves out onto the end wall's line (the two slopes beside it reach it there).

skeleton() returns None, and the building keeps its oriented-box roof, when the outline is degenerate (an edge shorter
than MIN_EDGE_M, a corner of less than MIN_ANGLE_DEG, more than MAX_CORNERS corners) or the result does not check out
(the faces must tile the outline exactly, each one a simple polygon in front of its own wall).
"""
import heapq
import math

MIN_EDGE_M = 0.4            # an outline with a shorter edge keeps its box roof
MIN_ANGLE_DEG = 12.0        # ... or a sharper corner (a spike: its bisector runs off far)
MAX_CORNERS = 32            # ... or more corners than this (a round tower, a ragged trace: hipped boxes look better)
MAX_EVENTS = 12             # events per corner at most (a simple polygon needs ~2 per corner)
EPS = 1e-7                  # m: points this close are the same
AREA_TOL = 2e-3             # the faces' areas sum to the outline's within this share
GABLE_SPAN = (0.15, 0.85)   # a hip's apex is a ridge's end only if it projects into this middle part of its wall


class _V:
    """A wavefront vertex: at time t it is at p; it moves with velocity v; its walls are el (the one before it, in
    ring order) and er (after it)."""
    __slots__ = ("p", "t", "v", "el", "er", "prev", "next", "ok", "node", "reflex", "lav")

    def __init__(self, p, t, el, er, node):
        self.p, self.t, self.el, self.er, self.node = p, t, el, er, node
        self.prev = self.next = None
        self.ok = True
        self.v = None
        self.reflex = False
        self.lav = 0

    def at(self, t):
        return (self.p[0] + self.v[0] * (t - self.t), self.p[1] + self.v[1] * (t - self.t))


def _area2(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return s


class _Skel:
    def __init__(self, ring):
        self.P = [(float(x), float(y)) for x, y in ring]
        n = len(self.P)
        self.n = n
        self.d, self.nrm = [], []
        for i in range(n):
            (x0, y0), (x1, y1) = self.P[i], self.P[(i + 1) % n]
            L = math.hypot(x1 - x0, y1 - y0)
            dx, dy = (x1 - x0) / L, (y1 - y0) / L
            self.d.append((dx, dy))
            self.nrm.append((-dy, dx))          # inward: the ring is counter-clockwise
        self.nodes = list(self.P)               # node id -> (x, y); its time in self.nt
        self.nt = [0.0] * n
        self.arcs = []                          # (node a, node b, wall, wall): a skeleton arc between two faces
        self.heap = []
        self.seq = 0
        self.events = 0
        self.made = []                          # every wavefront vertex (close_left)
        self.t_end = 0.0

    def node(self, p, t):
        self.nodes.append(p)
        self.nt.append(t)
        return len(self.nodes) - 1

    def velocity(self, el, er):
        (ax, ay), (bx, by) = self.nrm[el], self.nrm[er]
        det = ax * by - ay * bx
        if abs(det) < 1e-9:
            if ax * bx + ay * by > 0:
                return (ax, ay)                 # collinear walls: straight in
            return None                         # facing walls: their offset lines are one (a ridge)
        return ((by - ay) / det, (ax - bx) / det)

    def setup(self, V):
        self.made.append(V)
        self._setup(V)

    def _setup(self, V):
        """V's velocity and kind. Two facing walls (a wing's two long walls once its end has collapsed) meet only when
        their offset lines are one: the vertex is on a ridge and goes no further (static), ended by the events of the
        same moment round it (the 2- and 3-vertex cycles: collapse, on_edge)."""
        V.v = self.velocity(V.el, V.er)
        if V.v is None:
            V.v = (0.0, 0.0)
            V.reflex = False
            return
        (ax, ay), (bx, by) = self.d[V.el], self.d[V.er]
        V.reflex = ax * by - ay * bx < -1e-9

    def push(self, t, kind, a, b=None, x=None):
        self.seq += 1
        heapq.heappush(self.heap, (t, self.seq, kind, a, b, x))

    def edge_event(self, A, B):
        """When A and B (A.next) meet: their wall's wavefront edge shrinks to nothing."""
        if A is B:
            return
        e = A.er
        dx, dy = self.d[e]
        t0 = max(A.t, B.t)
        pa, pb = A.at(t0), B.at(t0)
        if math.hypot(pa[0] - pb[0], pa[1] - pb[1]) < 1e-6:
            self.push(t0, 0, A, B)
            return
        sa, sb = pa[0] * dx + pa[1] * dy, pb[0] * dx + pb[1] * dy
        va, vb = A.v[0] * dx + A.v[1] * dy, B.v[0] * dx + B.v[1] * dy
        if va - vb <= 1e-12:
            return                              # the edge does not shrink
        t = t0 + (sb - sa) / (va - vb)
        self.push(t, 0, A, B)

    def split_event(self, V):
        """When reflex vertex V reaches each wall's offset line in front of it (checked against the wavefront's actual
        extent when the event comes up)."""
        for e in range(self.n):
            if e in (V.el, V.er):
                continue
            nx, ny = self.nrm[e]
            ax, ay = self.P[e]
            s = V.v[0] * nx + V.v[1] * ny
            if 1.0 - s <= 1e-12:
                continue
            t = ((V.p[0] - ax) * nx + (V.p[1] - ay) * ny - s * V.t) / (1.0 - s)
            if t < V.t - 1e-9:
                continue
            x = V.at(t)
            # in front of the wall's original segment's half-plane strip at least (cheap pre-check)
            if (x[0] - ax) * nx + (x[1] - ay) * ny < -1e-6:
                continue
            self.push(t, 1, V, e, x)

    def events_for(self, V):
        if V.v == (0.0, 0.0):
            return                              # static (setup): its neighbours' events end it
        self.edge_event(V.prev, V)
        self.edge_event(V, V.next)
        if V.reflex:
            self.split_event(V)

    def link(self, a, b):
        a.next, b.prev = b, a

    def finish(self, V, node):
        self.arcs.append((V.node, node, V.el, V.er))
        V.ok = False

    def lav_len(self, V, cap):
        n, X = 1, V.next
        while X is not V and n <= cap:
            n, X = n + 1, X.next
        return n

    def run(self):
        n = self.n
        vs = [_V(self.P[i], 0.0, (i - 1) % n, i, i) for i in range(n)]
        for i in range(n):
            self.link(vs[i], vs[(i + 1) % n])
        for V in vs:
            self.setup(V)
        for V in vs:
            self.edge_event(V, V.next)
            if V.reflex:
                self.split_event(V)
        cap = MAX_EVENTS * n
        while self.heap:
            t, _, kind, a, b, x = heapq.heappop(self.heap)
            self.events += 1
            if self.events > cap:
                raise ValueError("too many events")
            if kind == 0:
                self.on_edge(t, a, b)
            else:
                self.on_split(t, a, b, x)
        self.t_end = max(self.nt)               # (the last event that did something; stale ones pop later)
        self.close_left(vs)
        return self

    def close_left(self, vs):
        """Cycles left with no event (events of one moment that left a zero-area cycle: a cross whose wings end at
        once): every vertex goes to where it is at the last event's time, and each wavefront edge of the cycle, now a
        segment on the ridge, is an arc of its wall's face. A cycle that is not flat leaves faces open (skeleton()
        then gives up)."""
        seen = set()
        left = [V for V in self.made if V.ok]
        for V in left:
            if id(V) in seen:
                continue
            cyc, X = [], V
            while id(X) not in seen and X.ok:
                seen.add(id(X))
                cyc.append(X)
                X = X.next
            end = {}
            for X in cyc:
                q = X.at(self.t_end)
                if math.hypot(q[0] - X.p[0], q[1] - X.p[1]) > EPS:
                    node = self.node(q, self.t_end)
                    self.arcs.append((X.node, node, X.el, X.er))
                    end[id(X)] = node
                else:
                    end[id(X)] = X.node
            for X in cyc:
                if X.next.ok and id(X.next) in end:
                    self.arcs.append((end[id(X)], end[id(X.next)], X.er, X.er))
            for X in cyc:
                X.ok = False

    def collapse(self, V, t):
        """A cycle of two or three vertices: they all end at one node (the last piece of a wing)."""
        k = self.lav_len(V, 4)
        if k == 2:
            W = V.next
            pa, pb = V.at(t), W.at(t)
            node = self.node(((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2), t)
            self.finish(V, node)
            self.finish(W, node)
            return True
        return False

    def on_edge(self, t, A, B):
        if not (A.ok and B.ok) or A.next is not B:
            return
        x = A.at(t)
        if B.next is A:                         # two left: they meet
            node = self.node(x, t)
            self.finish(A, node)
            self.finish(B, node)
            return
        C = B.next
        if C.next is A:                         # a triangle: all three end at one node (a peak)
            node = self.node(x, t)
            for V in (A, B, C):
                self.finish(V, node)
            return
        node = self.node(x, t)
        self.finish(A, node)
        self.finish(B, node)
        N = _V(x, t, A.el, B.er, node)
        self.link(A.prev, N)
        self.link(N, C)
        self.setup(N)
        self.events_for(N)

    def on_split(self, t, V, e, x):
        if not V.ok:
            return
        dx, dy = self.d[e]
        sx = x[0] * dx + x[1] * dy
        # the piece of the wavefront along wall e that x lies on, in V's cycle
        Y, found = V.next.next, None
        while Y is not V.prev and Y is not V:
            if Y.er == e and Y.ok:
                p0, p1 = Y.at(t), Y.next.at(t)
                s0, s1 = p0[0] * dx + p0[1] * dy, p1[0] * dx + p1[1] * dy
                if s0 - 1e-6 <= sx <= s1 + 1e-6:
                    found = Y
                    break
            Y = Y.next
        if found is None:
            return
        Y = found
        Z = Y.next
        node = self.node(x, t)
        self.finish(V, node)
        A, B = V.prev, V.next
        N1 = _V(x, t, V.el, e, node)
        N2 = _V(x, t, e, V.er, node)
        self.link(A, N1)
        self.link(N1, Z)
        self.link(Y, N2)
        self.link(N2, B)
        for N in (N1, N2):
            self.setup(N)
        for N in (N1, N2):
            if N.ok and not self.collapse(N, t):
                self.events_for(N)

    def faces(self):
        """[[node, ...]] per wall: the wall's corners, then the skeleton nodes round its face, counter-clockwise."""
        adj = [dict() for _ in range(self.n)]
        for a, b, e1, e2 in self.arcs:
            if a == b:
                continue
            for e in ((e1,) if e1 == e2 else (e1, e2)):
                adj[e].setdefault(a, []).append(b)
                adj[e].setdefault(b, []).append(a)
        out = []
        for i in range(self.n):
            a, b = i, (i + 1) % self.n
            chain, prev, cur = [a, b], a, b
            # from the wall's far corner up its face's boundary and back down to its near corner (a node may sit at the
            # same place as another: zero-length arcs are walked like the rest)
            for _ in range(4 * len(self.nodes)):
                nxt = [q for q in adj[i].get(cur, []) if q != prev]
                if cur == b and len(chain) == 2:
                    nxt = [q for q in adj[i].get(cur, []) if q != a]
                if not nxt:
                    raise ValueError(f"face {i} is open")
                if len(nxt) > 1:
                    nxt.sort(key=lambda q: (q == a, q))     # (deterministic; a branch means a broken face)
                    if a in nxt:
                        nxt = [a]
                    else:
                        raise ValueError(f"face {i} branches")
                prev, cur = cur, nxt[0]
                if cur == a:
                    break
                chain.append(cur)
            else:
                raise ValueError(f"face {i} does not close")
            out.append(chain)
        return out


def degenerate(ring):
    """Why the outline cannot have a skeleton roof (a short edge, a spike, too many corners), or None."""
    n = len(ring)
    if n < 3 or n > MAX_CORNERS:
        return "corners"
    if _area2(ring) <= 0:
        return "orientation"
    cmin = math.cos(math.radians(180.0 - MIN_ANGLE_DEG))
    for i in range(n):
        (x0, y0), (x1, y1), (x2, y2) = ring[i - 1], ring[i], ring[(i + 1) % n]
        a = math.hypot(x1 - x0, y1 - y0)
        b = math.hypot(x2 - x1, y2 - y1)
        if a < MIN_EDGE_M or b < MIN_EDGE_M:
            return "short edge"
        c = ((x0 - x1) * (x2 - x1) + (y0 - y1) * (y2 - y1)) / (a * b)
        if c > -cmin:                           # the corner's angle under MIN_ANGLE_DEG
            return "spike"
    return None


def offset_ring(ring, d):
    """The counter-clockwise ring moved out by d (each wall along its outward normal, corners mitred); None when a wall
    turns over (shorter than the offset makes it)."""
    n = len(ring)
    P = [(float(x), float(y)) for x, y in ring]
    lines = []
    for i in range(n):
        (x0, y0), (x1, y1) = P[i], P[(i + 1) % n]
        L = math.hypot(x1 - x0, y1 - y0)
        dx, dy = (x1 - x0) / L, (y1 - y0) / L
        nx, ny = dy, -dx                        # outward
        lines.append(((x0 + nx * d, y0 + ny * d), (dx, dy)))
    out = []
    for i in range(n):
        (p, u), (q, w) = lines[i - 1], lines[i]
        det = u[0] * w[1] - u[1] * w[0]
        if abs(det) < 1e-9:
            out.append((P[i][0] + w[1] * d, P[i][1] - w[0] * d))
            continue
        s = ((q[0] - p[0]) * w[1] - (q[1] - p[1]) * w[0]) / det
        out.append((p[0] + u[0] * s, p[1] + u[1] * s))
    for i in range(n):                          # every wall keeps its direction
        (x0, y0), (x1, y1) = out[i], out[(i + 1) % n]
        dx, dy = lines[i][1]
        if (x1 - x0) * dx + (y1 - y0) * dy <= MIN_EDGE_M * 0.25:
            return None
    return out


def skeleton(ring):
    """The straight skeleton's faces of a counter-clockwise ring: (nodes [(x, y)], node times [d], faces [[node]]),
    one face per wall i (starting at its corners i, i + 1); None when the ring is degenerate or the result fails its
    checks (module docstring)."""
    ring = [(float(x), float(y)) for x, y in ring]
    if degenerate(ring) is not None:
        return None
    try:
        s = _Skel(ring).run()
        faces = s.faces()
    except (ValueError, ZeroDivisionError):
        return None
    total = _area2(ring)
    got = 0.0
    for i, f in enumerate(faces):
        pts = [s.nodes[k] for k in f]
        a = _area2(pts)
        if a <= -1e-6:
            return None                         # a face turned inside out
        got += a
        nx, ny = s.d[i][1] * -1.0, s.d[i][0]    # (the wall's inward normal)
        ax, ay = ring[i]
        for k in f[2:]:                         # in front of its own wall, at the height of its distance
            x, y = s.nodes[k]
            dist = (x - ax) * nx + (y - ay) * ny
            if dist < -1e-4 or abs(dist - s.nt[k]) > 1e-3 * max(1.0, s.nt[k]):
                return None
    if abs(got - total) > AREA_TOL * total:
        return None
    return s.nodes, s.nt, faces


def gable(ring, nodes, nt, faces):
    """Gabled: (nodes, times, faces, gables) with each wing's end hip turned into a gable wall. A face is an end hip
    when it is a triangle (its wall's two corners and one apex), the apex is shared by exactly that face and its two
    neighbours (the end of a ridge, not a pyramid's peak) and projects into the middle of the wall (GABLE_SPAN). The
    apex moves onto the wall's line (a new node at the same height) in the neighbours' faces; gables: [(wall, apex node
    on the wall's line)], the triangle's face left empty."""
    n = len(ring)
    nodes, nt = list(nodes), list(nt)
    faces = [list(f) for f in faces]
    users = {}
    for i, f in enumerate(faces):
        for k in f[2:]:
            users.setdefault(k, set()).add(i)
    gables = []
    moved = {}
    for i in range(n):
        f = faces[i]
        if len(f) != 3:
            continue
        k = f[2]
        if users.get(k) != {(i - 1) % n, i, (i + 1) % n} or k in moved:
            continue
        (ax, ay), (bx, by) = ring[i], ring[(i + 1) % n]
        L = math.hypot(bx - ax, by - ay)
        dx, dy = (bx - ax) / L, (by - ay) / L
        x, y = nodes[k]
        s = (x - ax) * dx + (y - ay) * dy
        if not (GABLE_SPAN[0] * L <= s <= GABLE_SPAN[1] * L):
            continue
        nodes.append((ax + dx * s, ay + dy * s))
        nt.append(nt[k])
        g = len(nodes) - 1
        moved[k] = g
        for j in ((i - 1) % n, (i + 1) % n):
            faces[j] = [g if q == k else q for q in faces[j]]
        faces[i] = []
        gables.append((i, g))
    return nodes, nt, faces, gables
