-- luajit mod/tests/test_population.lua  (the persistent traffic population: ghosts on the graph, the nearest real)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local P = require('beamngEarth/population')

-- A motorway east along y = 0 (one-way, 3 lanes, 31 m/s) from w (-2000) to e (+2000) through j (0); at j a two-way
-- residential street goes north to n, and an alley south to s.
local g = { nodes = { w = { -2000, 0, 0, 5.5 }, m1 = { -1000, 0, 0, 5.5 }, j = { 0, 0, 0, 5.5 }, m2 = { 1000, 0, 0, 5.5 },
                      e = { 2000, 0, 0, 5.5 }, n = { 0, 400, 0, 3 }, s = { 0, -100, 0, 2 } },
  edges = { { 'w', 'm1', 1, 3, 0, 31, 1, 5.5 }, { 'm1', 'j', 1, 3, 0, 31, 1, 5.5 }, { 'j', 'm2', 1, 3, 0, 31, 1, 5.5 },
            { 'm2', 'e', 1, 3, 0, 31, 1, 5.5 }, { 'j', 'n', 0, 1, 1, 11, 0.8, 3 }, { 'j', 's', 0, 1, 1, 7, 0.1, 2 } } }
local adj = P.adjacency(g)
T.check(#adj.w == 1 and adj.w[1].to == 'm1' and adj.w[1].lanes == 3 and adj.w[1].other == 0, 'adjacency: one-way out only')
T.check(adj.m1 and not P.edge(adj, 'm1', 'w'), 'adjacency: no way back on a one-way')
T.check(P.edge(adj, 'j', 'n') and P.edge(adj, 'n', 'j'), 'adjacency: a two-way street both ways')
T.near(P.edge(adj, 'w', 'm1').len, 1000, 1e-9, 'adjacency: edge length')

-- forks: weighted as the stock AI (drivability ratio^2, straightness^2, lanes); the alley next to never
local counts = { m2 = 0, n = 0, s = 0 }
for i = 0, 999 do
  local l = P.nextEdge(adj, g.nodes, 'm1', 'j', (i + 0.5) / 1000, 1)
  counts[l.to] = counts[l.to] + 1
end
T.check(counts.m2 > 900, 'nextEdge: straight on along the motorway mostly: ' .. counts.m2)
T.check(counts.s <= 3, 'nextEdge: the alley next to never: ' .. counts.s)
T.check(P.nextEdge(adj, g.nodes, 'm2', 'e', 0.5, 1) == nil, 'nextEdge: the graph edge (a dead end): nil')
local back = P.nextEdge(adj, g.nodes, 'j', 'n', 0.5, 0.8)
T.check(back and back.to == 'j', 'nextEdge: a two-way dead end: the only way is back')

-- the pose: lane 1 at the kerb (right of the heading), lanes across the half width
local x, y, z, dx, dy = P.pose(g.nodes, 'w', P.edge(adj, 'w', 'm1'), 500, 1, false)
T.near(x, -1500, 1e-9, 'pose: 500 m along')
T.check(dx == 1 and dy == 0, 'pose: heading east')
T.near(y, -(5.5 - 0.5 * 11 / 3), 1e-9, 'pose: lane 1 is the right one (south of an eastbound road)')
local _, y3 = P.pose(g.nodes, 'w', P.edge(adj, 'w', 'm1'), 500, 3, false)
T.check(y3 > y, 'pose: lane 3 farther left')
local _, yl = P.pose(g.nodes, 'w', P.edge(adj, 'w', 'm1'), 500, 1, true)
T.near(yl, -y, 1e-9, 'pose: driving on the left mirrors it')
local _, yn = P.pose(g.nodes, 'j', P.edge(adj, 'j', 'n'), 100, 1, false)
local xn = P.pose(g.nodes, 'j', P.edge(adj, 'j', 'n'), 100, 1, false)
T.check(xn > 0, 'pose: a northbound two-way lane is east of the centreline')

-- IDM
T.check(P.idm(0, 30, math.huge, 0) > 1, 'idm: free road accelerates')
T.check(P.idm(30, 30, 15, 10) < -3, 'idm: a slow car close ahead brakes')

-- density: by the road's speed; an alley none
T.check(P.density(31) > P.density(20) and P.density(20) > P.density(11), 'density: faster roads busier')
local list, total = P.weighted(adj, false)
for _, it in ipairs(list) do T.check(it.link.driv >= P.MIN_DRIVABILITY, 'weighted: no alley ever seeded') end
local entries = P.weighted(adj, true)
local fromW = false
for _, it in ipairs(entries) do if it.from == 'w' then fromW = true end end
T.check(fromW and #entries >= 1, 'weighted: entries are the graph edges driving in (w): ' .. #entries)
local tn = P.target(adj, 1)
T.check(tn >= 30 and tn <= P.MAX_GHOSTS, 'target: about 4 a lane-km on 4 km of 3-lane motorway: ' .. tn)

-- the state: a graph, seeded ghosts, stepping
P.reset()
P.state.rnd = (function() local k = 0 return function() k = (k * 7919 + 13) % 10007 return k / 10007 end end)()
P.setGraph(g)
P.seed(40)
T.check(#P.state.ghosts == 40, 'seed: 40 ghosts')
for _, gh in ipairs(P.state.ghosts) do
  T.check(gh.link.driv >= P.MIN_DRIVABILITY and gh.s >= 0 and gh.s <= gh.link.len, 'seed: on a carried road, on its edge')
end
-- one ghost alone on the motorway runs up to its pace
P.state.ghosts = { { id = 1, from = 'w', link = P.edge(adj, 'w', 'm1'), s = 0, v = 0, pace = 1, lane = 1, drivIn = 1 } }
for _ = 1, 200 do P.step(0.25, -1500, 0) end
local gh = P.state.ghosts[1]
T.check(gh.v > 28 and gh.v <= 31.5, 'step: up to the road speed: ' .. gh.v)
T.check(gh.from ~= 'w' or gh.s > 0, 'step: it moved on')
-- a follower behind a slow leader in its lane slows down
P.state.ghosts = { { id = 1, from = 'w', link = P.edge(adj, 'w', 'm1'), s = 100, v = 30, pace = 1, lane = 1 },
                   { id = 2, from = 'w', link = P.edge(adj, 'w', 'm1'), s = 140, v = 10, pace = 0.33, lane = 1 },
                   { id = 3, from = 'w', link = P.edge(adj, 'w', 'm1'), s = 140, v = 10, pace = 0.33, lane = 2 } }
for _ = 1, 8 do P.step(0.25, -1800, 0) end
T.check(P.state.ghosts[1].v < 25, 'step: behind a slow car in its lane: slows: ' .. P.state.ghosts[1].v)
-- a ghost reaching the graph's edge comes back at an entry, far from the player
P.state.ghosts = { { id = 9, from = 'm2', link = P.edge(adj, 'm2', 'e'), s = 990, v = 30, pace = 1, lane = 1 } }
P.step(1, 1500, 0)
local r = P.state.ghosts[1]
T.check(r.from == 'w' and r.s < 40, 'step: off the graph\'s end: back at the entry w, ' .. r.from .. ' ' .. r.s)
T.check(P.status().recycled >= 1, 'status: recycled counted')
-- a ghost left far behind the player comes back too
P.state.ghosts = { { id = 10, from = 'w', link = P.edge(adj, 'w', 'm1'), s = 10, v = 0, pace = 1, lane = 1 } }
P.step(0.25, 1500, 0)
T.check(P.state.ghosts[1].s ~= 10 or P.state.ghosts[1].from ~= 'w', 'step: 3.5 km behind: comes back elsewhere')

-- which are real: the k nearest on the ground, with hysteresis
local function at(id, s, real) return { id = id, from = 'w', link = P.edge(adj, 'w', 'm1'), s = s, v = 30, lane = 1, real = real } end
local gs = { at(1, 500), at(2, 450), at(3, 560), at(4, 100), at(5, 990) }
local box = { -1700, -1000, 300, 1000 }       -- (the player at -1500: ghost 4 at -1900 is off the ground)
local want = P.chooseReal(gs, g.nodes, 2, -1500, 0, box, false)
T.check(want[gs[1]] and want[gs[2]] and not want[gs[3]] and not want[gs[4]], 'chooseReal: the 2 nearest on the ground')
gs[3].real = 77      -- ghost 3 is real now, a little farther than the 2nd
local want2 = P.chooseReal(gs, g.nodes, 2, -1500, 0, box, false)
T.check(want2[gs[3]] and want2[gs[1]] and not want2[gs[2]], 'chooseReal: a real one near the edge of the set stays')

-- the swaps: free cars first, then cars off the ground or stuck, then the farthest
local need = { gs[1], gs[2] }
local bound = { [21] = gs[3], [22] = gs[5] }
local w3 = { [gs[1]] = true, [gs[2]] = true }
local plan = P.plan(need, bound, { 30 }, w3, function(id) return id == 21 and 600 or 100 end,
  function(id) return id ~= 22 end, function() return false end)
T.check(#plan == 2 and plan[1].id == 30 and not plan[1].demote, 'plan: a free car first')
T.check(plan[2].id == 22 and plan[2].demote == gs[5], 'plan: then the car off the ground, its ghost demoted')
P.SWAPS_PER = 1
T.check(#P.plan(need, bound, {}, w3, function() return 1 end, function() return true end, function() return false end) == 1,
  'plan: at most SWAPS_PER')
P.SWAPS_PER = 2
T.check(#P.plan(need, {}, {}, w3, function() return 1 end, function() return true end, function() return false end) == 0,
  'plan: no car to spare: none')

-- a new graph keeps the ghosts on its edges by name
P.reset()
P.setGraph(g)
P.state.ghosts = { at(1, 500), { id = 2, from = 'j', link = P.edge(adj, 'j', 'n'), s = 50, v = 10, lane = 1 } }
local g2 = { nodes = g.nodes, edges = { g.edges[1], g.edges[2], g.edges[3], g.edges[4] } }
P.setGraph(g2)
T.check(#P.state.ghosts == 1 and P.state.ghosts[1].id == 1, 'setGraph: the ghost on a road the new graph lacks is gone')
T.check(P.state.ghosts[1].link == P.edge(P.state.adj, 'w', 'm1'), 'setGraph: the other on the new graph\'s edge')

-- after a new graph the population goes back to its density, the new ghosts far from the player
P.reset()
P.setGraph(g)
P.state.ghosts = {}
P.rebalance(0, 0)
local want = P.target(P.state.adj, 1)
T.check(#P.state.ghosts == want, 'rebalance: up to the target: ' .. #P.state.ghosts .. ' of ' .. want)
for _, gh in ipairs(P.state.ghosts) do
  local x, y = P.pose(g.nodes, gh.from, gh.link, gh.s, gh.lane, false)
  T.check(x * x + y * y >= P.ENTRY_MIN_M ^ 2, 'rebalance: a new ghost starts far from the player')
end
for i = 1, want do P.state.ghosts[#P.state.ghosts + 1] = at(100 + i, 10 * i) end
P.state.ghosts[#P.state.ghosts].real = 5
P.rebalance(0, 0)
T.check(#P.state.ghosts == want, 'rebalance: a surplus taken off: ' .. #P.state.ghosts)
local stillReal = false
for _, gh in ipairs(P.state.ghosts) do if gh.real == 5 then stillReal = true end end
T.check(stillReal, 'rebalance: never a real car\'s ghost')

-- on / off: the game's recycling off while on, back after
do
  local vars = {}
  gameplay_traffic = { setTrafficVars = function(v) for k, x in pairs(v) do vars[k] = x end end }
  P.setEnabled(true, 1, false)
  T.check(P.enabled() and vars.spawnValue == 0, 'setEnabled: on, the stock recycling off')
  P.setEnabled(false)
  T.check(not P.enabled() and vars.spawnValue == 1, 'setEnabled: off, back on')
  gameplay_traffic = nil
end

-- a real car's ghost on an edge a new graph lacks: kept (its car places it again), and nothing dies on it
P.reset()
P.setGraph(g)
P.state.ghosts = { { id = 1, from = 'j', link = P.edge(P.state.adj, 'j', 'n'), s = 50, v = 10, lane = 1, real = 77 },
                   at(2, 500) }
P.state.ghosts[2].pace = 1
local g3 = { nodes = { w = g.nodes.w, m1 = g.nodes.m1, j = g.nodes.j, m2 = g.nodes.m2, e = g.nodes.e },
             edges = { g.edges[1], g.edges[2], g.edges[3], g.edges[4] } }
P.setGraph(g3)
T.check(#P.state.ghosts == 2, 'setGraph: the ghost of a real car kept although its edge is gone')
T.check(P.pose(g3.nodes, 'j', P.state.ghosts[1].link, 50, 1, false) == nil, 'pose: a node not in the graph: nil')
local okC, wantC = pcall(P.chooseReal, P.state.ghosts, g3.nodes, 2, -1500, 0, nil, false)
T.check(okC and not wantC[P.state.ghosts[1]], 'chooseReal: the stale ghost skipped, no error')
local okS, errS = pcall(P.step, 0.25, -1500, 0)
T.check(okS, 'step: no error on the stale ghost: ' .. tostring(errS))
P.rebalance(-1500, 0)
T.check(true, 'rebalance: no error')

-- the game side with a stubbed game: a player driving east at 30 m/s, 4 traffic cars; they end up on the nearest
-- ghosts, are placed and pushed, and the stock recycling stays off
do
  local vecmt = {}
  vecmt.__index = vecmt
  function vec3(x, y, z) return setmetatable({ x = x or 0, y = y or 0, z = z or 0 }, vecmt) end
  vecmt.__sub = function(a, b) return vec3(a.x - b.x, a.y - b.y, a.z - b.z) end
  function vecmt:length() return math.sqrt(self.x ^ 2 + self.y ^ 2 + self.z ^ 2) end
  function quatFromDir() return { 0, 0, 0, 1 } end
  function log() end
  local clock = 1000
  local realClock = os.clock
  os.clock = function() return clock end
  local objs = {}
  local function car(id, x, y)
    local o = { id = id, p = vec3(x, y, 0), u = vec3(0, 0, 0), lua = {} }
    function o:getPosition() return self.p end
    function o:getVelocity() return self.u end
    function o:getDirectionVector() return vec3(1, 0, 0) end
    function o:queueLuaCommand(c) self.lua[#self.lua + 1] = c end
    objs[id] = o
    return o
  end
  local player = car(1, -1500, -3)
  for id = 11, 14 do car(id, -1450 + id, -3) end
  be = { getPlayerVehicleID = function() return 1 end }
  function getObjectByID(id) return objs[id] end
  local placed, vars = 0, { spawnValue = 1 }
  gameplay_traffic = {
    getState = function() return 'on' end,
    getTrafficData = function() return { [11] = {}, [12] = {}, [13] = {}, [14] = {} } end,
    getTrafficVars = function() return vars end,
    setTrafficVars = function(v) for k, x in pairs(v) do vars[k] = x end end,
    respawnVehicle = function(id, pos) objs[id].p = vec3(pos.x, pos.y, pos.z) placed = placed + 1 end }
  map = { findBestRoad = function(p) return nil end, surfaceNormal = function() return vec3(0, 0, 1) end }
  package.loaded['beamngEarth/navgraph'] = { pushLua = function(v) return 'push ' .. v end }
  P.reset()
  P.state.rnd = math.random
  P.setEnabled(true, 1, false)
  P.setGraph(g)
  local box = { -2500, -1000, -500, 1000 }
  local ok, err = true, nil
  for step = 1, 200 do
    clock = clock + 0.1
    player.p = vec3(player.p.x + 3, -3, 0)
    box = { player.p.x - 1000, -1000, player.p.x + 1000, 1000 }
    local okU, e = pcall(P.update, box)
    if not okU then ok, err = false, e break end
  end
  T.check(ok, 'update: runs: ' .. tostring(err))
  local st = P.status()
  T.check(st.ghosts and st.ghosts > 20 and st.real == 4, 'update: ghosts about, the 4 cars real: ' .. tostring(st.ghosts) .. ' / ' .. tostring(st.real))
  T.check(placed >= 4 and st.promoted >= 4, 'update: the cars placed on their ghosts: ' .. placed)
  T.check(vars.spawnValue == 0, 'update: the stock recycling off')
  local pushed = 0
  for id = 11, 14 do for _, c in ipairs(objs[id].lua) do if c:find('^push ') then pushed = pushed + 1 end end end
  T.check(pushed >= 1, 'update: placed cars pushed to their ghost speed: ' .. pushed)
  for id = 11, 14 do
    local d = (objs[id].p - player.p):length()
    T.check(d <= P.REAL_M + 50, 'update: car ' .. id .. ' near the player: ' .. math.floor(d))
  end
  P.setEnabled(false)
  os.clock = realClock
  be, gameplay_traffic, map, getObjectByID = nil, nil, nil, nil
  package.loaded['beamngEarth/navgraph'] = nil
end

T.done('test_population')
