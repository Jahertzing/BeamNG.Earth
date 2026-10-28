-- A persistent traffic population (docs/TRAFFIC_POPULATION.md; Jeff's drive 2026-10-28: "it's not super fun driving
-- past all these cars and then more keep appearing ... it's not seamless"). Instead of recycling the game's few traffic
-- cars by teleporting them ahead of the player, about a hundred "ghosts" live on the server's whole road graph (the
-- ~1.1 km window around the player, navgraph.py WINDOW_M), each with an edge, a place on it, a speed and a way on.
-- They move every TICK_S, at their road's speed times their own pace, behind the car ahead in their lane (IDM), and pick
-- their way at forks as the stock AI does (drivability ratio squared, straightness squared, lanes). Only the K ghosts
-- nearest the player on the physics ground are the game's traffic cars (K: as many as gameplay_traffic has); a car
-- swaps between ghost and real exactly where it is, at the speed it was going. A ghost that drives off the graph's edge
-- comes back at a far edge driving in, so cars arrive from beyond the map data, not out of thin air in front.
-- The game's own recycling is off while this runs (gameplay_traffic spawnValue 0); navgraph's guard leaves the cars be.
local M = {}

M.TICK_S = 0.25
M.SWAP_S = 0.5           -- real cars re-chosen this often, at most SWAPS_PER of them each time
M.SWAPS_PER = 2
M.MAX_GHOSTS = 160
M.REAL_M = 700           -- a ghost farther than this from the player is never real
M.GROUND_IN_M = 80       -- ... nor one this near the physics block's edge (navgraph GUARD_M is 40)
M.KEEP_FACTOR = 1.25     -- a real car stays real until it is this much farther than the K-th nearest candidate
M.ENTRY_MIN_M = 500      -- a ghost coming back starts at least this far from the player
M.BEHIND_M = 1300        -- a ghost this far from the player comes back elsewhere
M.SPREAD = 0.06          -- pace: about 1 +- this (navgraph paceFactor's)
M.SPREAD_MAX = 0.12
M.IDM = { T = 1.4, a = 1.4, b = 2.0, s0 = 4, len = 5 }
-- cars per km of lane by the road's speed (m/s), times the traffic mode's share (navgraph MODES)
M.DENSITY = { { 27, 4.0 }, { 20, 3.0 }, { 15, 1.8 }, { 11, 0.8 }, { 0, 0.3 } }
M.MIN_DRIVABILITY = 0.5  -- (alleys and living streets carry none: they are a way through, not a home)

-- ---- the graph ----

-- Pure: the directed adjacency of a server graph g ({nodes = {name = {x, y, z, r}}, edges = {{a, b, oneWay, lanesOut,
-- lanesIn, speed, drivability, radius, ramp}}}): out[name] = { { to, len, lanes, other, speed, driv, radius, oneWay } }
-- (other: the lanes the other way, for the lane offsets). Edges with a node missing are left out.
function M.adjacency(g)
  local out = {}
  for _, e in ipairs(g.edges or {}) do
    local a, b = e[1], e[2]
    local pa, pb = g.nodes[a], g.nodes[b]
    if pa and pb and a ~= b then
      local len = math.sqrt((pb[1] - pa[1]) ^ 2 + (pb[2] - pa[2]) ^ 2)
      if len > 0.5 then
        local oneWay = e[3] == 1
        out[a] = out[a] or {}
        out[a][#out[a] + 1] = { to = b, len = len, lanes = math.max(1, e[4] or 1), other = oneWay and 0 or (e[5] or 1),
          speed = e[6] or 13, driv = e[7] or 1, radius = e[8] or 3, oneWay = oneWay }
        if not oneWay then
          out[b] = out[b] or {}
          out[b][#out[b] + 1] = { to = a, len = len, lanes = math.max(1, e[5] or 1), other = e[4] or 1,
            speed = e[6] or 13, driv = e[7] or 1, radius = e[8] or 3, oneWay = false }
        end
      end
    end
  end
  return out
end

-- Pure: the edge from a to b in adjacency adj, or nil.
function M.edge(adj, a, b)
  for _, l in ipairs(adj[a] or {}) do if l.to == b then return l end end
  return nil
end

-- Pure: cars per km of lane on a road of speed v (m/s).
function M.density(v)
  for _, d in ipairs(M.DENSITY) do if v >= d[1] then return d[2] end end
  return 0
end

-- Pure: the way on from a ghost on from -> at (adjacency adj, node positions nodes), chosen with u in [0, 1) as the
-- stock AI weighs a fork: (drivability ratio, at most 1)^2 * ((1 + cos turn) / 2)^2 * lanes; no U-turn unless it is the
-- only way. nil at a dead end.
function M.nextEdge(adj, nodes, from, at, u, drivIn)
  local links = adj[at]
  if not links or #links == 0 then return nil end
  local pf, pa = nodes[from], nodes[at]
  local ix, iy = pa[1] - pf[1], pa[2] - pf[2]
  local il = math.sqrt(ix * ix + iy * iy)
  local ws, sum = {}, 0
  for i, l in ipairs(links) do
    local w = 0
    if l.to ~= from then
      local pt = nodes[l.to]
      local ox, oy = pt[1] - pa[1], pt[2] - pa[2]
      local ol = math.sqrt(ox * ox + oy * oy)
      local cos = (il > 1e-6 and ol > 1e-6) and (ix * ox + iy * oy) / (il * ol) or 1
      local r = math.min(1, l.driv / math.max(1e-6, drivIn or 1))
      w = r * r * (0.5 * (1 + cos)) ^ 2 * l.lanes
    end
    ws[i] = w
    sum = sum + w
  end
  if sum <= 1e-9 then
    for _, l in ipairs(links) do if l.to == from then return l end end    -- (only the way back: a U-turn)
    return nil
  end
  local pick = u * sum
  for i, l in ipairs(links) do
    pick = pick - ws[i]
    if pick < 0 and ws[i] > 0 then return l end
  end
  for i = #links, 1, -1 do if ws[i] > 0 then return links[i] end end
  return nil
end

-- Pure: a ghost's world position and heading (x, y, z, dx, dy) on graph nodes: s m along from -> to (link l), in lane
-- `lane` (1 = the kerb side), driving on the left when left.
function M.pose(nodes, from, l, s, lane, left)
  local pa, pb = nodes[from], nodes[l.to]
  local t = math.max(0, math.min(1, s / l.len))
  local dx, dy = (pb[1] - pa[1]) / l.len, (pb[2] - pa[2]) / l.len
  local x, y, z = pa[1] + (pb[1] - pa[1]) * t, pa[2] + (pb[2] - pa[2]) * t, pa[3] + (pb[3] - pa[3]) * t
  -- the lanes: the road's half width over all its lanes, this direction's on the right (left when left)
  local total = l.lanes + l.other
  local w = 2 * l.radius / math.max(1, total)
  local k = math.max(1, math.min(l.lanes, lane or 1))
  local off = l.radius - (k - 0.5) * w     -- (lane 1 at the kerb: this direction's lanes are on the right half)
  local side = left and -1 or 1
  local rx, ry = dy * side, -dx * side     -- (the right of the heading; mirrored on the left)
  return x + rx * off, y + ry * off, z, dx, dy
end

-- Pure: IDM acceleration at speed v toward v0 behind a gap (m, inf: none) to a leader dv m/s slower.
function M.idm(v, v0, gap, dv)
  local p = M.IDM
  local free = 1 - (math.max(0, v) / math.max(0.5, v0)) ^ 4
  if not gap or gap == math.huge then return p.a * free end
  local s = p.s0 + math.max(0, v * p.T + v * dv / (2 * math.sqrt(p.a * p.b)))
  return p.a * (free - (s / math.max(0.5, gap)) ^ 2)
end

-- Pure: a pace factor from three uniform draws (navgraph.paceFactor's shape).
function M.pace(u1, u2, u3)
  local z = (u1 + u2 + u3 - 1.5) * 2
  return math.max(1 - M.SPREAD_MAX, math.min(1 + M.SPREAD_MAX, 1 + z * M.SPREAD))
end

-- ---- the population ----

local P = { ghosts = {}, adj = nil, graph = nil, nextId = 0, lastTick = -math.huge, lastSwap = -math.huge,
            stats = { promoted = 0, demoted = 0, recycled = 0, seeded = 0, pushes = 0 }, rnd = math.random }
M.state = P

-- Pure-ish (P.rnd): the directed edges weighted for seeding or coming back: { from, link, weight }, those of drivability
-- under MIN_DRIVABILITY left out; entries only (from has no way in but from to): the graph's edges driving in.
function M.weighted(adj, entriesOnly)
  P.wcache = P.wcache or {}
  local key = entriesOnly and 'entries' or 'all'
  local c = P.wcache[key]
  if c and c.adj == adj then return c.list, c.total end
  local list, total = {}, 0
  local into = {}
  if entriesOnly then
    for a, links in pairs(adj) do
      for _, l in ipairs(links) do into[l.to] = (into[l.to] or 0) + 1 end
    end
  end
  for a, links in pairs(adj) do
    for _, l in ipairs(links) do
      if l.driv >= M.MIN_DRIVABILITY then
        local ok = true
        if entriesOnly then
          -- a node only reached back from where it leads: the graph's edge (a cut of the server's window)
          local n = into[a] or 0
          local back = M.edge(adj, l.to, a) and 1 or 0
          ok = n - back <= 0
        end
        if ok then
          local w = l.len * l.lanes * M.density(l.speed)
          if w > 0 then
            list[#list + 1] = { from = a, link = l, weight = w }
            total = total + w
          end
        end
      end
    end
  end
  P.wcache[key] = { adj = adj, list = list, total = total }
  return list, total
end

local function pick(list, total, u)
  local x = u * total
  for _, it in ipairs(list) do
    x = x - it.weight
    if x < 0 then return it end
  end
  return list[#list]
end

-- Pure-ish: the number of ghosts graph adjacency adj carries at traffic share f (navgraph MODES: 0.5, 1, 1.5).
function M.target(adj, f)
  local _, total = M.weighted(adj, false)
  return math.min(M.MAX_GHOSTS, math.floor(total / 1000 * (f or 1) + 0.5))
end

local function newGhost(from, l, s, v)
  P.nextId = P.nextId + 1
  local rnd = P.rnd
  return { id = P.nextId, from = from, link = l, s = s, v = v, pace = M.pace(rnd(), rnd(), rnd()),
           lane = 1 + math.floor(rnd() * l.lanes), drivIn = l.driv }
end

-- Seed: count ghosts spread over the graph by weight (len * lanes * density), at their road's speed.
function M.seed(count)
  local list, total = M.weighted(P.adj, false)
  if #list == 0 then return 0 end
  for _ = 1, count do
    local it = pick(list, total, P.rnd())
    local g = newGhost(it.from, it.link, P.rnd() * it.link.len, it.link.speed * 0.9)
    P.ghosts[#P.ghosts + 1] = g
  end
  P.stats.seeded = P.stats.seeded + count
  return count
end

-- A ghost comes back at an entry of the graph at least ENTRY_MIN_M from the player (px, py), else anywhere that far.
local function recycle(g, px, py)
  local nodes = P.graph.nodes
  for _, entries in ipairs({ true, false }) do
    local list, total = M.weighted(P.adj, entries)
    if #list > 0 then
      for _ = 1, 12 do
        local it = pick(list, total, P.rnd())
        local p = nodes[it.from]
        if not px or (p[1] - px) ^ 2 + (p[2] - py) ^ 2 >= M.ENTRY_MIN_M ^ 2 then
          g.from, g.link, g.s, g.v = it.from, it.link, 0, it.link.speed * 0.9
          g.lane, g.drivIn, g.real = 1 + math.floor(P.rnd() * it.link.lanes), it.link.driv, nil
          P.stats.recycled = P.stats.recycled + 1
          return true
        end
      end
    end
  end
  return false
end

-- A new graph from the server (or the same one moved by a rebase): ghosts on edges it still has keep their place.
function M.setGraph(g)
  if not g or g == P.graph then return end
  P.graph = g
  P.adj = M.adjacency(g)
  local keep = {}
  for _, gh in ipairs(P.ghosts) do
    local l = M.edge(P.adj, gh.from, gh.link.to)
    if l then
      gh.link = l
      gh.s = math.min(gh.s, l.len)
      keep[#keep + 1] = gh
    elseif gh.real then
      keep[#keep + 1] = gh    -- (a real car's ghost is placed again from its car, trackReal)
    end
  end
  P.ghosts = keep
  P.graphChanged = true
end

-- The population back to its road's density after a new graph (the window moved on: the roads behind gone, new ones
-- ahead): missing ghosts start ENTRY_MIN_M or more from the player at (px, py), out of sight's reach; a surplus goes
-- farthest first (never a real car's).
function M.rebalance(px, py)
  local want = M.target(P.adj, P.share or 1)
  local n = #P.ghosts
  if n < want then
    local list, total = M.weighted(P.adj, false)
    local nodes = P.graph.nodes
    local tries, maxTries = 0, (want - n) * 8
    while n < want and #list > 0 and tries < maxTries do
      tries = tries + 1
      local it = pick(list, total, P.rnd())
      local gh = newGhost(it.from, it.link, P.rnd() * it.link.len, it.link.speed * 0.9)
      local x, y = M.pose(nodes, gh.from, gh.link, gh.s, gh.lane, P.left)
      if (x - px) ^ 2 + (y - py) ^ 2 >= M.ENTRY_MIN_M ^ 2 then
        P.ghosts[#P.ghosts + 1] = gh
        n = n + 1
        P.stats.seeded = P.stats.seeded + 1
      end
    end
  elseif n > want * 1.25 then
    local nodes = P.graph.nodes
    local far = {}
    for i, gh in ipairs(P.ghosts) do
      if not gh.real then
        local x, y = M.pose(nodes, gh.from, gh.link, gh.s, gh.lane, P.left)
        far[#far + 1] = { i = i, d2 = (x - px) ^ 2 + (y - py) ^ 2 }
      end
    end
    table.sort(far, function(a, b) return a.d2 > b.d2 end)
    local drop = {}
    for k = 1, math.min(#far, n - want) do drop[far[k].i] = true end
    local keep = {}
    for i, gh in ipairs(P.ghosts) do if not drop[i] then keep[#keep + 1] = gh end end
    P.ghosts = keep
  end
end

-- Pure-ish: move every ghost that is not a real car by dt (s); leaders found per directed edge and lane.
function M.step(dt, px, py)
  local nodes = P.graph and P.graph.nodes
  if not nodes then return end
  local lanes = {}      -- key -> sorted ghosts on that edge and lane
  for _, g in ipairs(P.ghosts) do
    local k = g.from .. '\0' .. g.link.to .. '\0' .. g.lane
    lanes[k] = lanes[k] or {}
    local t = lanes[k]
    t[#t + 1] = g
  end
  for _, t in pairs(lanes) do table.sort(t, function(a, b) return a.s < b.s end) end
  for _, g in ipairs(P.ghosts) do
    if not g.real then
      local k = g.from .. '\0' .. g.link.to .. '\0' .. g.lane
      local t = lanes[k]
      local gap, dv = math.huge, 0
      for i = 1, #t do
        if t[i] == g and t[i + 1] then
          gap = t[i + 1].s - g.s - M.IDM.len
          dv = g.v - t[i + 1].v
          break
        end
      end
      local v0 = g.link.speed * g.pace
      g.v = math.max(0, g.v + M.idm(g.v, v0, gap, dv) * dt)
      g.s = g.s + g.v * dt
      local guard = 0
      while g.s >= g.link.len and guard < 8 do
        guard = guard + 1
        local nxt = M.nextEdge(P.adj, nodes, g.from, g.link.to, P.rnd(), g.link.driv)
        if not nxt then
          recycle(g, px, py)
          break
        end
        g.s = g.s - g.link.len
        g.from, g.link, g.drivIn = g.link.to, nxt, g.link.driv
        g.lane = math.max(1, math.min(nxt.lanes, g.lane))
      end
      if px then
        local x, y = M.pose(nodes, g.from, g.link, g.s, g.lane, P.left)
        if (x - px) ^ 2 + (y - py) ^ 2 > M.BEHIND_M ^ 2 then recycle(g, px, py) end
      end
    end
  end
end

-- Pure: which ghosts should be real: the k nearest (px, py) that stand on the physics ground box b (GROUND_IN_M inside)
-- within REAL_M; a ghost that is real now stays so while it is within KEEP_FACTOR of the k-th one's distance and on the
-- ground. Returns a set { ghost = true }.
function M.chooseReal(ghosts, nodes, k, px, py, b, left)
  local cand = {}
  for _, g in ipairs(ghosts) do
    local x, y = M.pose(nodes, g.from, g.link, g.s, g.lane, left)
    local onGround = not b or (x >= b[1] + M.GROUND_IN_M and x <= b[3] - M.GROUND_IN_M and
                               y >= b[2] + M.GROUND_IN_M and y <= b[4] - M.GROUND_IN_M)
    local d2 = (x - px) ^ 2 + (y - py) ^ 2
    if onGround and d2 <= M.REAL_M ^ 2 then cand[#cand + 1] = { g = g, d2 = d2 } end
  end
  table.sort(cand, function(a, b2) return a.d2 < b2.d2 end)
  local want = {}
  local kth = cand[math.min(k, #cand)]
  local keepD2 = kth and kth.d2 * M.KEEP_FACTOR ^ 2 or 0
  local n = 0
  for i = 1, math.min(k, #cand) do want[cand[i].g] = true n = n + 1 end
  -- (hysteresis: a real car near the edge of the set stays, the farthest new one waits)
  for _, c in ipairs(cand) do
    if c.g.real and not want[c.g] and c.d2 <= keepD2 then
      -- drop the farthest wanted one that is not real yet
      for i = math.min(k, #cand), 1, -1 do
        local w = cand[i].g
        if want[w] and not w.real then want[w] = nil want[c.g] = true break end
      end
    end
  end
  return want
end

-- ---- the game side ----

local function trafficIds()
  local tr = rawget(_G, 'gameplay_traffic')
  local ids = {}
  if not (tr and tr.getTrafficData and tr.getState and tr.getState() == 'on') then return ids end
  local me = be and be.getPlayerVehicleID and be:getPlayerVehicleID(0) or -1
  for id in pairs(tr.getTrafficData() or {}) do if id ~= me then ids[#ids + 1] = id end end
  table.sort(ids)
  return ids
end

-- A real car's ghost follows its car: the graph edge under it (the game map's nearest road, in its direction).
local function trackReal(g, o)
  local p, u = o:getPosition(), o:getVelocity()
  local sp = math.sqrt(u.x * u.x + u.y * u.y)
  g.v = sp
  if not (map and map.findBestRoad) then return end
  local dir = sp > 1 and vec3(u.x / sp, u.y / sp, 0) or o:getDirectionVector()
  local a, b = map.findBestRoad(p, dir)
  if not (a and b) then return end
  local nodes = P.graph.nodes
  for _, pair in ipairs({ { a, b }, { b, a } }) do
    local l = M.edge(P.adj, pair[1], pair[2])
    if l then
      local pa = nodes[pair[1]]
      local s = ((p.x - pa[1]) * (nodes[l.to][1] - pa[1]) + (p.y - pa[2]) * (nodes[l.to][2] - pa[2])) / l.len
      local fx, fy = (nodes[l.to][1] - pa[1]) / l.len, (nodes[l.to][2] - pa[2]) / l.len
      if fx * dir.x + fy * dir.y >= 0 then
        g.from, g.link, g.s = pair[1], l, math.max(0, math.min(l.len, s))
        return
      end
    end
  end
end

local function place(id, g)
  local tr = gameplay_traffic
  local x, y, z, dx, dy = M.pose(P.graph.nodes, g.from, g.link, g.s, g.lane, P.left)
  local pos = vec3(x, y, z + 0.3)
  local ok = pcall(function()
    local rot = quatFromDir(vec3(dx, dy, 0), map.surfaceNormal and map.surfaceNormal(pos) or vec3(0, 0, 1))
    tr.respawnVehicle(id, pos, rot)
  end)
  if not ok then return false end
  -- pushed to the ghost's speed once landed (navgraph's gentle push)
  P.pushes = P.pushes or {}
  P.pushes[id] = { v = g.v, at = os.clock() }
  return true
end

-- Pure-ish (P.rnd): a new ghost for a car that has nowhere to be (every wanted ghost is real and it stands off the ground):
-- on the ground box b, SPARE_M or more from the player at (px, py), behind the camera (looking along fx, fy) if it can.
M.SPARE_M = 300
function M.spare(b, px, py, fx, fy)
  local list, total = M.weighted(P.adj, false)
  if #list == 0 then return nil end
  local nodes = P.graph.nodes
  local best = nil
  for i = 1, 40 do
    local it = pick(list, total, P.rnd())
    local s = P.rnd() * it.link.len
    local g = newGhost(it.from, it.link, s, it.link.speed * 0.9)
    local x, y = M.pose(nodes, g.from, g.link, g.s, g.lane, P.left)
    local d2 = (x - px) ^ 2 + (y - py) ^ 2
    if M.onGround(b, x, y) and d2 >= M.SPARE_M ^ 2 and d2 <= M.REAL_M ^ 2 then
      if not fx or (x - px) * fx + (y - py) * fy < 0 then return g end
      best = best or g
    end
  end
  return best
end

-- Pure: is (x, y) on the physics ground box b, GROUND_IN_M inside its edge (no box: anywhere)?
function M.onGround(b, x, y)
  return not b or (x >= b[1] + M.GROUND_IN_M and x <= b[3] - M.GROUND_IN_M and
                   y >= b[2] + M.GROUND_IN_M and y <= b[4] - M.GROUND_IN_M)
end

-- Pure: the swaps to make this round. want: chooseReal's set; bound: car id -> ghost; free: car ids with no ghost;
-- dist(id) -> m from the player; ground(id) -> the car is on the physics ground; stuck(id) -> it has stood for long.
-- Returns { { ghost, id, demote = ghost or nil } } for the wanted ghosts that are not real, nearest first (need: their
-- order), at most SWAPS_PER: a free car first, then a car whose ghost is not wanted, off the ground or stuck first, then
-- the farthest.
function M.plan(need, bound, free, want, dist, ground, stuck)
  local donors = {}
  for _, id in ipairs(free) do donors[#donors + 1] = { id = id, rank = 0 } end
  for id, g in pairs(bound) do
    if not want[g] then
      local r = (not ground(id) or stuck(id)) and 1 or 2
      donors[#donors + 1] = { id = id, rank = r, d = dist(id), g = g }
    end
  end
  table.sort(donors, function(a, b)
    if a.rank ~= b.rank then return a.rank < b.rank end
    return (a.d or 0) > (b.d or 0)
  end)
  local out = {}
  for i = 1, math.min(#need, #donors, M.SWAPS_PER) do
    out[#out + 1] = { ghost = need[i], id = donors[i].id, demote = donors[i].g }
  end
  return out
end

M.STUCK_S = 60           -- a real car standing this long (40 m or more from the player) may be taken for another ghost

-- Every frame (navgraph.update): move the ghosts, keep the real cars on the nearest ones.
function M.update(b)
  local now = os.clock()
  if not P.enabled or not P.graph or now - P.lastTick < M.TICK_S then return end
  local dt = P.lastTick == -math.huge and 0 or math.min(1, now - P.lastTick)
  P.lastTick = now
  local me = be:getPlayerVehicleID(0)
  local pv = me and me >= 0 and getObjectByID(me) or nil
  local pp = pv and pv:getPosition()
  if not pp then return end
  if #P.ghosts == 0 then
    M.seed(M.target(P.adj, P.share or 1))
  elseif P.graphChanged then
    M.rebalance(pp.x, pp.y)
  end
  P.graphChanged = false
  local ids = trafficIds()
  local idSet = {}
  for _, id in ipairs(ids) do idSet[id] = true end
  -- bindings of cars that are gone are dropped; the real cars' ghosts follow their cars
  local bound = {}
  for _, g in ipairs(P.ghosts) do
    if g.real and not idSet[g.real] then g.real = nil end
    if g.real then bound[g.real] = g end
  end
  P.stillSince = P.stillSince or {}
  for id, g in pairs(bound) do
    local o = getObjectByID(id)
    if o then
      pcall(trackReal, g, o)
      if o:getVelocity():length() < 0.5 then P.stillSince[id] = P.stillSince[id] or now else P.stillSince[id] = nil end
    end
  end
  M.step(dt, pp.x, pp.y)
  -- pushes due (the car has landed: standing after its teleport)
  for id, push in pairs(P.pushes or {}) do
    local o = getObjectByID(id)
    if not o or now - push.at > 3 then
      P.pushes[id] = nil
    elseif now - push.at > 0.4 and o:getVelocity():length() < 3 then
      o:queueLuaCommand(require('beamngEarth/navgraph').pushLua(push.v))
      P.stats.pushes = P.stats.pushes + 1
      P.pushes[id] = nil
    end
  end
  if now - P.lastSwap < M.SWAP_S then return end
  P.lastSwap = now
  -- the game's own recycling stays off (a traffic set up again resets its vars)
  local tr = gameplay_traffic
  local vars = tr.getTrafficVars and tr.getTrafficVars()
  if vars and vars.spawnValue ~= 0 then pcall(tr.setTrafficVars, { spawnValue = 0 }) end
  local nodes = P.graph.nodes
  local want = M.chooseReal(P.ghosts, nodes, #ids, pp.x, pp.y, b, P.left)
  local need = {}
  for _, g in ipairs(P.ghosts) do
    if want[g] and not g.real then
      local x, y = M.pose(nodes, g.from, g.link, g.s, g.lane, P.left)
      need[#need + 1] = { g = g, d2 = (x - pp.x) ^ 2 + (y - pp.y) ^ 2 }
    end
  end
  table.sort(need, function(a, c) return a.d2 < c.d2 end)
  for i, n in ipairs(need) do need[i] = n.g end
  local free = {}
  for _, id in ipairs(ids) do if not bound[id] then free[#free + 1] = id end end
  local function pos(id) local o = getObjectByID(id) return o and o:getPosition() end
  local plan = M.plan(need, bound, free, want,
    function(id) local p = pos(id) return p and (p - pp):length() or math.huge end,
    function(id) local p = pos(id) return p ~= nil and M.onGround(b, p.x, p.y) end,
    function(id)
      local p, t = pos(id), P.stillSince[id]
      return t ~= nil and now - t >= M.STUCK_S and p ~= nil and (p - pp):length() >= 40
    end)
  for _, sw in ipairs(plan) do
    if sw.demote then
      sw.demote.real = nil
      P.stats.demoted = P.stats.demoted + 1
    end
    if place(sw.id, sw.ghost) then
      sw.ghost.real = sw.id
      P.stillSince[sw.id] = nil
      P.stats.promoted = P.stats.promoted + 1
    end
  end
  -- a car off the ground that no wanted ghost took: a spare ghost for it behind the camera
  local taken = {}
  for _, sw in ipairs(plan) do taken[sw.id] = true end
  for id, g in pairs(bound) do
    local p = pos(id)
    if not taken[id] and p and not M.onGround(b, p.x, p.y) and not want[g] then
      local cf = core_camera and core_camera.getForward and core_camera.getForward()
      local ng = M.spare(b, pp.x, pp.y, cf and cf.x, cf and cf.y)
      if ng then
        g.real = nil
        P.ghosts[#P.ghosts + 1] = ng
        if place(id, ng) then ng.real = id P.stats.spares = (P.stats.spares or 0) + 1 end
      end
    end
  end
  local real = 0
  for _, g in ipairs(P.ghosts) do if g.real then real = real + 1 end end
  P.stats.real, P.stats.ghosts, P.stats.cars = real, #P.ghosts, #ids
end

-- On / off (navgraph: the setting trafficPopulation). Off hands the recycling back to the game.
function M.setEnabled(on, share, left)
  P.enabled = on and true or false
  P.share, P.left = share or 1, left and true or false
  local tr = rawget(_G, 'gameplay_traffic')
  if tr and tr.setTrafficVars then pcall(tr.setTrafficVars, { spawnValue = on and 0 or 1 }) end
  if not on then P.ghosts = {} end
end

function M.enabled() return P.enabled == true end

-- The player teleported, or the level reset: everything seeds again on the next graph.
function M.reset()
  P.ghosts, P.graph, P.adj, P.pushes = {}, nil, nil, {}
  P.lastTick, P.lastSwap = -math.huge, -math.huge
end

function M.status()
  local s = {}
  for k, v in pairs(P.stats) do s[k] = v end
  s.enabled = P.enabled == true
  return s
end

return M
