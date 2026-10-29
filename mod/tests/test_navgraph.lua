-- luajit mod/tests/test_navgraph.lua  (the server's road graph as the stock map's custom node table)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
function vec3(x, y, z) return { x = x, y = y, z = z } end
local N = require('beamngEarth/navgraph')

T.check(N.lanes(2, 1, false) == '-++' and N.lanes(2, 1, true) == '++-' and N.lanes(1, 0, false) == '+',
  'lanes: against first for right-hand traffic')

local g = {
  nodes = { a = { 0, 0, 1, 4 }, b = { 50, 0, 1, 4 }, c = { 100, 0, 1, 3 }, far = { 5000, 0, 1, 3 } },
  edges = { { 'a', 'b', 0, 1, 1, 11.2, 0.8, 4 }, { 'b', 'c', 1, 1, 0, 8.9, 0.8, 3 }, { 'c', 'far', 0, 1, 1, 11.2, 1, 3 },
            { 'b', 'a', 0, 1, 1, 11.2, 0.8, 4 } },
}
local nodes, nn, ne, cuts = N.toNodes(g, { -500, -500, 500, 500 }, 60, false)
T.check(nn == 3 and ne == 2 and not nodes.far, 'clipped to the block: the far node and its edge left out')
-- the radius sentinel (2026-10-29): one node of the largest radius map.lua allows, far away, no road to it, once
local sent = N.SENTINEL.name
N.withSentinel(nodes)
T.check(nodes[sent] and nodes[sent].radius == 15 and next(nodes[sent].links) == nil and nodes[sent].pos.x == 2e6, 'the sentinel: radius 15, no links, far away')
N.withSentinel(nodes)
local cnt = 0
for _ in pairs(nodes) do cnt = cnt + 1 end
T.check(cnt == nn + 1, 'added once')
nodes[sent] = nil
T.check(#cuts == 1 and cuts[1][1] == 100 and cuts[1][2] == 0, 'the cut: c, whose road to the far node was left out')
-- GitHub #131: a link 6.4 m up in 8 m (a bridge's first node over its junction) is left out; a steep real street not
local steep = { nodes = { j = { 0, 0, 22.7, 4 }, d = { 8, 0, 29.1, 3 }, h = { 0, 30, 33.7, 3 } },
  edges = { { 'j', 'd', 0, 1, 1, 11.2, 1, 3 }, { 'j', 'h', 0, 1, 1, 11.2, 1, 3 } } }
local sn, _, sne = N.toNodes(steep, { -500, -500, 500, 500 }, 60, false)
T.check(sne == 1 and sn.j.links.h and not sn.j.links.d, 'a link steeper than MAX_GRADE left out; 37 % kept: ' .. sne)
T.check(N.headingToCut(60, 0, 1, 0, cuts, 60), 'a car 40 m from the cut, heading for it')
T.check(not N.headingToCut(60, 0, -1, 0, cuts, 60), '... heading away from it: not')
T.check(not N.headingToCut(60, 0, 0, 1, cuts, 60), '... across: not')
T.check(not N.headingToCut(-20, 0, 1, 0, cuts, 60), '... 120 m from it: not yet')
T.check(N.headingToCut(-20, 0, 1, 0, cuts, 30 * N.CUT_S), '... at 67 mph: from 120 m')
-- built over several frames (navgraph's buildNodes: a coroutine pausing every yieldEvery items): the same tables
do
  local co = coroutine.create(function() return { N.toNodes(g, { -500, -500, 500, 500 }, 60, false, 2) } end)
  local pauses, res = 0, nil
  while true do
    local ok, r = coroutine.resume(co)
    T.check(ok, 'resumes cleanly')
    if coroutine.status(co) == 'dead' then res = r break end
    pauses = pauses + 1
  end
  T.check(pauses >= 3, 'it paused along the way: ' .. pauses)
  T.check(res[2] == nn and res[3] == ne and #res[4] == #cuts and res[1].a.links.b.speedLimit == 11.2
    and not res[1].far, 'the same nodes, edges and cuts as in one go')
end
local ab = nodes.a.links.b
T.check(ab and nodes.b.links.a == ab, 'one edge table shared by both ends (double-sided)')
T.check(ab.inNode == 'a' and ab.outNode == 'b' and ab.inPos == nodes.a.pos and ab.outPos == nodes.b.pos, 'in / out nodes and positions')
T.check(ab.speedLimit == 11.2 and ab.lanes == '-+' and not ab.oneWay and ab.inRadius == 4 and ab.outRadius == 4, 'edge fields')
local bc = nodes.b.links.c
T.check(bc.oneWay and bc.lanes == '+' and bc.inNode == 'b', 'one-way: in -> out is the allowed direction')
T.check(N.inside(nil, 1e9, 0, 60) and not N.inside({ 0, 0, 100, 100 }, 50, 50, 60), 'inside')

-- a new signals table keeps the junctions a car may still remember (ai.lua reads them unchecked)
local got
mapmgr = { signalsData = { j1 = { a = { { action = 2 } } }, j2 = { b = { { action = 0 } }, c = { { action = 1 } } } },
           setSignals = function(d) got = d; mapmgr.signalsData = d end }
local f = loadstring(N.setSignalsLua('{ j2 = { b = { { action = 3 } } }, j3 = { d = { { action = 0 } } } }'))
f()
T.check(got.j3.d[1].action == 0 and got.j2.b[1].action == 3, 'the new signals')
T.check(got.j1.a[1].action == 2 and got.j2.c[1].action == 1, 'old junctions / approaches kept')
mapmgr = { setSignals = function(d) got = d end }
loadstring(N.setSignalsLua('{ j9 = {} }'))()
T.check(got.j9 and not got.j1, 'no old table: the new one as is')
T.check(not N.stuck(nil, 100, 500), 'moving: not stuck')
T.check(not N.stuck(60, 100, 500), 'still 40 s: a long signal, not stuck yet')
T.check(N.stuck(50, 100, 500) and not N.stuck(50, 100, 20), 'still 50 s: stuck, unless next to the player')
local o = { trafficMode = 'enabled', trafficParked = 'enabled', trafficPolice = 'disabled', trafficAmount = -1,
            trafficParkedAmount = 10 }
local c = N.takeChoice(o)
T.check(c.traffic and c.parked and not c.police and c.parkedAmount == 10, 'the configurator choice taken')
T.check(o.trafficMode == 'disabled' and o.trafficParked == 'disabled', 'freeroam spawns nothing before our roads')
T.check(N.takeChoice(o).traffic, 'taken twice (level start runs twice): the first choice kept')
N.restoreChoice()
T.check(o.trafficMode == 'enabled' and o.trafficParked == 'enabled', 'the player\'s options back afterwards')
T.check(N.MODE == 'normal' and N.takeChoice({ trafficMode = 'fromSetting', trafficParked = 'disabled' }).traffic,
  'fromSetting (freeroam\'s default): the AI traffic setting, normal by default')
T.check(N.choice.mode == 'normal' and N.choice.parked and N.choice.parkedMode == 'normal',
  '... its mode kept for the amount; the parked cars (their option "disabled" by default) left to parkedCars too')
N.restoreChoice()
N.setMode('off')
T.check(not N.takeChoice({ trafficMode = 'fromSetting', trafficParked = 'disabled' }).traffic, 'fromSetting, setting off: off')
N.restoreChoice()
T.check(N.takeChoice({ trafficMode = 'enabled', trafficParked = 'disabled' }).traffic and not N.choice.mode,
  'traffic picked at level start: on whatever the setting, the game\'s amount')
N.restoreChoice()
N.setMode('busy')
T.check(not N.takeChoice({ trafficMode = 'disabled', trafficParked = 'disabled' }).traffic, 'disabled at level start: off')
N.restoreChoice()
T.check(N.setMode('nonsense') == 'busy', 'setMode: an unknown mode is ignored')
N.setMode('normal')
T.check(N.modeAmount('off', 10) == 0 and N.modeAmount('normal', 10) == -1 and N.modeAmount('normal', nil) == -1,
  'modeAmount: off none, normal the game\'s own')
T.check(N.modeAmount('light', 10) == 5 and N.modeAmount('light', 2) == 2 and N.modeAmount('light', 4) == 3,
  'modeAmount: light about half, at least 3 (never more than normal)')
T.check(N.modeAmount('busy', 10) == 15 and N.modeAmount('busy', 4) == 8 and N.modeAmount('busy', 30) == N.BUSY_MAX,
  'modeAmount: busy one and a half, at least 4 more, at most BUSY_MAX')
T.check(N.modeAmount('busy', nil) == -1, 'modeAmount: the game\'s amount unknown: the game\'s own')
-- the game's own pick (at most 8, made at install) raised to the CPU's floor: 10 on a 12-core CPU, busy 15 from it
T.check(N.cpuFloor(12) == 10 and N.cpuFloor(4) == 2 and N.cpuFloor(64) == N.CPU_FLOOR_MAX and N.cpuFloor(nil) == 0,
  'cpuFloor: the cores less CPU_SPARE, at most CPU_FLOOR_MAX')
T.check(N.modeAmount('busy', math.max(7, N.cpuFloor(12))) == 15, 'busy on a 12-core CPU: 15')
-- what startTraffic sets up (tools set traffic up with it: bench8 ran the game's 7, not the mod's 10)
do
  local gt0, eng0 = gameplay_traffic, Engine
  gameplay_traffic = { getIdealSpawnAmount = function() return 7 end }
  Engine = { Platform = { getCPUInfo = function() return { coresPhysical = 12 } end } }
  local n, own = N.plannedAmount('normal')
  T.check(n == 10 and own == 10, 'plannedAmount: normal on a 12-core CPU: 10 (the game picks 7): ' .. tostring(n))
  T.check(N.plannedAmount('busy') == 15 and N.plannedAmount('off') == 0, 'plannedAmount: busy 15, off 0')
  gameplay_traffic, Engine = gt0, eng0
end
T.check(not N.takeChoice(nil).parked, 'no freeroam: off (the radial menu still works)')
settings = { getValue = function(k) if k == 'trafficParkedVehicles' then return false end end }
T.check(not N.takeChoice({ trafficMode = 'enabled', trafficParked = 'enabled' }).parked,
  'traffic and parked chosen, the parked vehicles setting off: none (as stock freeroam, GitHub #320)')
N.restoreChoice()
T.check(N.takeChoice({ trafficMode = 'disabled', trafficParked = 'enabled' }).parked, 'parked only: the setting does not apply')
N.restoreChoice()
settings = nil
-- the parked cars' own setting (parkedCars) when the level-start options leave them to it
T.check(N.ownParked(5, 16) == 16 and N.ownParked(5, nil) == 5 and N.ownParked(0, nil) == nil and N.ownParked(nil, 0) == nil,
  'ownParked: the larger of 0.40\'s trafficParkingAmount and the old trafficParkedAmount; 0: automatic')
T.check(N.parkedAmount('off', 16) == 0 and N.parkedAmount('normal', 16) == 16 and N.parkedAmount('normal', nil) == -1,
  'parkedAmount: off none, normal the player\'s own (automatic: the game\'s)')
T.check(N.parkedAmount('few', 16) == 8 and N.parkedAmount('few', 5) == 4 and N.parkedAmount('few', 3) == 3,
  'parkedAmount: few about half, at least PARKED_FEW_MIN, never more than normal')
T.check(N.parkedAmount('many', 10) == 20 and N.parkedAmount('many', 16) == N.PARKED_MAX and N.parkedAmount('many', nil) == -1,
  'parkedAmount: many twice, at most PARKED_MAX')
settings = { getValue = function(k) return ({ trafficParkingAmount = 5, trafficParkedAmount = 16 })[k] end }
c = N.takeChoice({ trafficMode = 'fromSetting', trafficParked = 'fromSetting' })
T.check(c.parked and c.parkedMode == 'normal' and c.parkedAmount == 16,
  'fromSetting parked cars: on by default at the player\'s own amount (16 in the old key, 5 in the new)')
N.restoreChoice()
c = N.takeChoice({ trafficMode = 'fromSetting' })
T.check(c.parked and c.parkedMode == 'normal', 'no parked option at all: left to the setting')
N.restoreChoice()
N.setParked('many')
T.check(N.takeChoice({ trafficMode = 'fromSetting', trafficParked = 'fromSetting' }).parkedAmount == N.PARKED_MAX, 'many: 32')
N.restoreChoice()
N.setParked('off')
T.check(not N.takeChoice({ trafficMode = 'fromSetting', trafficParked = 'fromSetting' }).parked, 'parkedCars off: none')
N.restoreChoice()
T.check(N.takeChoice({ trafficMode = 'fromSetting', trafficParked = 'enabled', trafficParkingAmount = 7 }).parkedAmount == 7
  and not N.choice.parkedMode, 'parked cars picked at level start: on whatever the setting, the amount picked there')
N.restoreChoice()
T.check(not N.takeChoice({ trafficMode = 'enabled', trafficParked = 'disabled' }).parked,
  'the configurator applied, traffic without parked cars: none')
N.restoreChoice()
N.setParked('normal')
T.check(N.takeChoice({ trafficMode = 'enabled', trafficParked = 'enabled', trafficParkedAmount = -1 }).parkedAmount == 16,
  'the configurator\'s "enabled" (its amount automatic): the setting\'s amount, 16 from the old key')
N.restoreChoice()
T.check(N.setParked('nonsense') == 'normal', 'setParked: an unknown mode is ignored')
N.setParked('normal')
settings = { getValue = function(k) if k == 'trafficParkedVehicles' then return false end end }
T.check(not N.takeChoice({ trafficMode = 'fromSetting', trafficParked = 'fromSetting' }).parked,
  'fromSetting, with traffic on and Options > Gameplay parked vehicles off: none (the stock rule)')
N.restoreChoice()
settings = nil
T.check(math.abs(N.boxMoved({ -708, -452, 196, 452 }, { -1092, -452, -188, 452 }) - 384) < 1e-9,
  'the block moved 384 m: measured between the boxes\' centres')
T.check(N.boxMoved(nil, { 0, 0, 1, 1 }) == 0 and N.boxMoved({}, { 0, 0, 1, 1 }) == 0, 'no box yet: not moved')
T.check(384 >= N.RECLIP_M and 128 < N.RECLIP_M, 'a 384 m move re-clips the graph, one step of 128 m does not')
-- GitHub #353: at motorway speed a respawned car goes ahead, short of the block's edge guard; slower: behind (nil)
do
  -- the terrain block: 2046 m, the player 600 m from its east edge
  local box = { -1446, -1023, 600, 1023 }
  local x, y, dx, dy, lo, hi = N.aheadSpot(0, 0, 30, 0, box)
  T.check(x == 0 and dx == 1 and dy == 0 and lo == N.AHEAD_M[1] and hi == N.AHEAD_M[2],
    'aheadSpot: 67 mph east, 600 m of block ahead: the full AHEAD_M: ' .. tostring(lo) .. '-' .. tostring(hi))
  T.check(N.aheadSpot(0, 0, 10, 0, box) == nil, 'aheadSpot: 22 mph: behind the camera as before')
  -- with the private ring past the edge (OUTER_M) the farthest spot is the edge guard and a margin
  T.check(N.aheadRoom(30) == N.GUARD_M + N.AHEAD_MARGIN_M, 'aheadRoom with the ring: the edge guard + margin')
  -- without it: the farthest spot leaves the clip's edge and a car's cut reach at its starting speed (the loop)
  local outer = N.OUTER_M
  N.OUTER_M = 0
  local room = N.aheadRoom(30)
  T.check(math.abs(room - (N.EDGE_M + N.cutReach(math.huge, 30 * N.AHEAD_PACE, true) + N.AHEAD_MARGIN_M)) < 1e-9,
    'aheadRoom: ' .. room)
  local _, _, _, _, lo2, hi2 = N.aheadSpot(-100, 0, 30, 0, box)
  T.check(hi2 == math.min(N.AHEAD_M[2], 700 - room) and lo2 == N.AHEAD_M[1], 'aheadSpot: 700 m of block: to ' .. tostring(hi2))
  -- a car put at the far end is out of its cut reach (the cut nodes stand EDGE_M inside the edge)
  T.check(700 - N.EDGE_M - hi2 > N.cutReach(0, N.aheadSpeed(30), true), 'aheadSpot: the far end is clear of the cut reach')
  T.check(N.aheadSpot(400, 0, 30, 0, box) == nil, 'aheadSpot: 200 m of block ahead: no room, behind')
  N.OUTER_M = outer
  -- with the ring: 320 m of block (the nearest the block lets the edge come) still has room ahead
  local _, _, _, _, lo4, hi4 = N.aheadSpot(280, 0, 30, 0, box)
  T.check(lo4 == N.AHEAD_M[1] and hi4 == 320 - N.aheadRoom(30), 'aheadSpot with the ring, 320 m: ' .. tostring(hi4))
  local _, _, _, _, _, h5 = N.aheadSpot(0, 700, 0, 30, box)
  T.check(h5 and h5 == 323 - N.aheadRoom(30), 'aheadSpot: north, limited by the box too: ' .. tostring(h5))
end
-- the private ring past the block: its nodes kept, its edges private (never a spawn point), the cuts past it
do
  local g = { nodes = { a = { 0, 0, 1, 4 }, b = { 300, 0, 1, 4 }, c = { 600, 0, 1, 4 }, d = { 900, 0, 1, 4 } },
    edges = { { 'a', 'b', 1, 2, 0, 31, 1, 4 }, { 'b', 'c', 1, 2, 0, 31, 1, 4 }, { 'c', 'd', 1, 2, 0, 31, 1, 4 } } }
  local box = { -500, -500, 500, 500 }
  local n0, _, _, c0 = N.toNodes(g, box, 60, false)
  T.check(n0.b and not n0.c and #c0 == 1 and c0[1].name == 'b', 'no ring: the graph ends at b, a cut 300 m in')
  local n1, _, ne1, c1 = N.toNodes(g, box, 60, false, nil, 300)
  T.check(n1.c and not n1.d and ne1 == 2, 'ring: c, 100 m past the edge, kept; d, 400 m past, not')
  T.check(n1.a.links.b.type == nil and n1.b.links.c.type == 'private', 'ring: its edges private, the block ones not')
  T.check(#c1 == 1 and c1[1].name == 'c', 'ring: the cut is past the edge')
end
-- the cut test walks the car's own road on (bench4: the 45-degree cone respawned cars for cut side streets and junctions)
do
  local function nd(x, y) return { pos = vec3(x, y, 0), radius = 7, links = {} } end
  local g = { a = nd(0, 0), b = nd(80, 0), c = nd(160, 0), d = nd(240, 0), s = nd(80, 60), t = nd(-40, 40), u = nd(-40, 120) }
  local function link(p, q, oneWay)
    local e = { oneWay = oneWay, inNode = p, outNode = q }
    g[p].links[q] = e
    g[q].links[p] = e
  end
  link('a', 'b', true) link('b', 'c', true) link('c', 'd', true) link('b', 's', false) link('a', 't', false)
  link('t', 'u', false)
  -- d and s lost roads to the clip; u is a real dead end (a cul-de-sac)
  local cutSet = N.cutSet({ { 240, 0, name = 'd' }, { 80, 60, name = 's' }, { 0, 0 } })
  T.check(cutSet.d and cutSet.s and not cutSet.u, 'cutSet: the named cuts')
  T.check(N.cutAhead(g, cutSet, 'a', 'b', 10, 0, 1, 0, 300) == true, 'cutAhead: the motorway itself ends at a cut ahead')
  T.check(N.cutAhead(g, cutSet, 'a', 'b', 10, 0, 1, 0, 150) == false, '... but farther than reach: not yet')
  T.check(N.cutAhead(g, cutSet, 'b', 'a', 10, 0, 1, 0, 300) == true, '... the stock search gave the nodes the other way round')
  -- d continues: the junction b whose side street s is cut is no dead end for a car going on
  g.e = nd(320, 0) link('d', 'e', true)
  cutSet.d = nil
  T.check(N.cutAhead(g, cutSet, 'a', 'b', 10, 0, 1, 0, 300) == false, 'cutAhead: a junction whose cross street was cut: not')
  T.check(N.cutAhead(g, cutSet, 't', 'u', -40, 50, 0, 1, 300) == false, 'cutAhead: a real dead end (no cut): left to the AI')
  -- a one-way road that ends (the server graph's edge, not the clip's): a cut too
  g.f = nd(400, 0) g.e.links.f = { oneWay = true, inNode = 'e', outNode = 'f' } g.f.links.e = g.e.links.f
  T.check(N.cutAhead(g, cutSet, 'd', 'e', 250, 0, 1, 0, 300) == true, 'cutAhead: a motorway that just ends: a cut')
  T.check(N.cutAhead(g, cutSet, 'x', 'y', 0, 0, 1, 0, 300) == nil, 'cutAhead: not on this graph: nil (the cone decides)')
  local adj = N.adjacency(g)
  g.b.links = { a = g.b.links.a }       -- (map.reset's convertToSingleSided gives each node a new links table)
  T.check(adj.b.links.c and adj.b.links.s and adj.b.pos == g.b.pos, 'adjacency: the links as they were before the reset')
end
-- a car keeps its route across a new map when every node of it is still there, in place and linked (keepRouteLua);
-- ai.lua is stood in for by a closure with its two upvalues, run through the same debug-library calls
do
  local function graphOf(shift, drop)
    local pos, g = {}, {}
    for i, nm in ipairs({ 'c1', 's5_1', 's5_2', 'c2', 'c3' }) do
      if nm ~= drop then pos[nm] = { x = i * 50 + shift, y = 0, z = 1 } g[nm] = {} end
    end
    local function link(a, b) if g[a] and g[b] then g[a][b] = {} g[b][a] = g[a][b] end end
    link('c1', 's5_1') link('s5_1', 's5_2') link('s5_2', 'c2') link('c2', 'c3')
    return { positions = pos, graph = g }
  end
  -- aiMap: the map ai.lua last saw (default: the map being replaced; false: none, a car respawned since)
  local function run(newMap, route, mode, aiMap)
    local current = graphOf(0)
    local mapData, currentRoute = current, route
    if aiMap ~= nil then mapData = aiMap or nil end
    local reports = {}
    _G.ai = { mode = mode or 'traffic' }
    _G.ai.updateGFX = function()
      if mapData ~= mapmgr.mapData then currentRoute = nil end
      mapData = mapmgr.mapData
      return currentRoute
    end
    _G.mapmgr = { mapData = current, setMap = function() mapmgr.mapData = newMap end }
    _G.obj = { queueGameEngineLua = function(_, s) reports[#reports + 1] = s end }
    _G.objectId = 42
    assert(loadstring(N.keepRouteLua()))()
    mapmgr.setMap(7)
    return ai.updateGFX(), reports
  end
  local route = { path = { 'c1', 's5_1', 's5_2', 'c2' }, plan = { { pathidx = 1 } } }
  local kept, rep = run(graphOf(0), route)
  T.check(kept == route, 'keepRoute: the same nodes in the new map: the route kept')
  T.check(#rep == 1 and rep[1]:find('routeKept(42, "kept")', 1, true), 'keepRoute: reported kept: ' .. tostring(rep[1]))
  T.check(run(graphOf(0.3), route) == route, 'keepRoute: nodes 0.3 m off (rounding): kept')
  local zg = graphOf(0)
  for _, p in pairs(zg.positions) do p.z = p.z + 1.5 end
  T.check(run(zg, route) == route, 'keepRoute: the ground under the nodes 1.5 m higher (streamed in): kept')
  local moved, rep2 = run(graphOf(15000), route)
  T.check(moved == nil and rep2[1]:find('"moved"', 1, true), 'keepRoute: a rebase moved every node: dropped, moved')
  local _, rep4 = run(graphOf(0, 's5_2'), route)
  T.check(rep4[1]:find('"missing"', 1, true), 'keepRoute: a node of the route clipped away: dropped, missing')
  local g = graphOf(0)
  g.graph.s5_1.s5_2, g.graph.s5_2.s5_1 = nil, nil
  local _, rep5 = run(g, route)
  T.check(rep5[1]:find('"link"', 1, true), 'keepRoute: a link of the route gone: dropped, link')
  -- bench4: the route's nodes behind the car (up to ten) clipped away as the block moved on: they do not count
  local behind = { path = { 'c1', 's5_1', 's5_2', 'c2', 'c3' }, plan = { { pathidx = 3 } } }
  T.check(run(graphOf(0, 'c1'), behind) == behind, 'keepRoute: a node behind the start of the car plan clipped away: kept')
  -- ai.lua's map older than the one replaced (two maps in a row before its next frame): checked against its own
  local older = graphOf(0)
  T.check(run(graphOf(0), route, nil, older) == route, 'keepRoute: against the map the route was made on: kept')
  local _, rep6 = run(graphOf(0), route, nil, false)
  T.check(#rep6 == 0, 'keepRoute: a car respawned since (ai.lua has no map): nothing to keep, nothing reported')
  T.check(run(graphOf(0), route, 'manual') == nil, 'keepRoute: only traffic mode (the GPS autodrive is left alone)')
  local _, rep3 = run(graphOf(0), nil)
  T.check(#rep3 == 0, 'keepRoute: no route yet: nothing to keep, nothing reported')
  N.routeKept(1, 'kept') N.routeKept(2, 'missing') N.routeKept(3, 'kept') N.routeKept(4, 'missing')
  local st = N.status()
  T.check(st.routesKept >= 2 and st.routesDropped >= 2 and st.routeDrops.missing >= 2, 'routeKept: counted in status, by reason')
  _G.ai, _G.mapmgr, _G.obj, _G.objectId = nil, nil, nil, nil
end
-- a rebase (core finishRebase): the graph in the new frame, its signals' stop points, directions and heads too
do
  local c, sn = math.cos(math.rad(10)), math.sin(math.rad(10))
  local xf = { apply = function(_, x, y, z) return c * x - sn * y - 15000, sn * x + c * y + 20, z - 3 end,
               rotate = function(_, x, y, z) return c * x - sn * y, sn * x + c * y, z end }
  local g0 = { anchor = 7, serial = 3, nodes = { a = { 15000, -20, 5, 4 } }, edges = { { 'a', 'a' } }, rules = { mph = true },
    signals = { { j = 'a', kind = 'signal', app = { { 'a', 15010, -20, 5, 1, 0, 1, 15012, -24, 5, 90 } } } } }
  local g1 = N.moved(g0, xf, 8)
  local n = g1.nodes.a
  T.check(g1.anchor == 8 and g1.serial == 3 and g1.edges == g0.edges and g1.rules == g0.rules,
    'moved: the new anchor, the same edges and rules')
  T.check(math.abs(n[1] - (c * 15000 + sn * 20 - 15000)) < 1e-6 and math.abs(n[3] - 2) < 1e-9 and n[4] == 4,
    'moved: a node through the transform, its radius kept')
  T.check(g0.nodes.a[1] == 15000, 'moved: the old graph left as it was')
  local a = g1.signals[1].app[1]
  T.check(a[1] == 'a' and a[7] == 1 and math.abs(a[5] - c) < 1e-9 and math.abs(a[6] - sn) < 1e-9,
    'moved: a signal\'s direction turned with the frame')
  T.check(math.abs(a[11] - 80) < 1e-9 and math.abs(a[2] - (c * 15010 + sn * 20 - 15000)) < 1e-6,
    'moved: its stop point moved, its head\'s (clockwise) heading turned')
  T.check(g1.signals[1].kind == 'signal' and g1.signals[1].j == 'a', 'moved: the junction\'s fields kept')
  T.check(not N.transform(xf, 8), 'transform: no graph yet: nothing')
end
-- each traffic car's pace on fast roads (paceFactor, paceLua run in a stand-in for the car's Lua)
do
  T.check(N.paceFactor(0.5, 0.5, 0.5) == 1, 'paceFactor: the middle draw: the AI\'s target')
  T.check(math.abs(N.paceFactor(1, 1, 1) - (1 + N.SPREAD_MAX)) < 1e-12 and math.abs(N.paceFactor(0, 0, 0) - (1 - N.SPREAD_MAX)) < 1e-12,
    'paceFactor: clipped to 1 +- SPREAD_MAX')
  local fast = { inNode = 'a', outNode = 'b', speedLimit = 31.3 }
  local slow = { inNode = 'b', outNode = 'c', speedLimit = 11.2 }
  local md = { graph = { a = { b = fast }, b = { a = fast, c = slow }, c = { b = slow } } }
  local handed = md
  mapmgr = { mapData = md, setMap = function() mapmgr.mapData = handed end }
  local run = function(code) assert(loadstring(code))() end
  run(N.paceLua(1.1))
  T.check(math.abs(fast.speedLimit - 31.3 * 1.1) < 1e-9 and slow.speedLimit == 11.2,
    'paceLua: a fast edge scaled once (shared by both directions), a town street not')
  run(N.paceLua(1.1))
  mapmgr.setMap(7)
  T.check(math.abs(fast.speedLimit - 31.3 * 1.1) < 1e-9, 'paceLua: run again, the same map handed again: not scaled twice')
  local fast2 = { inNode = 'a', outNode = 'b', speedLimit = 29.0 }
  handed = { graph = { a = { b = fast2 }, b = { a = fast2 } } }
  mapmgr.setMap(8)
  T.check(math.abs(fast2.speedLimit - 29.0 * 1.1) < 1e-9, 'paceLua: a new map scaled as it comes (setMap wrapped once)')
  mapmgr = nil
end
-- the pace and keep-route Lua: sent once a car is active, and again every INSTALL_S (bench5: sent while inactive, lost)
do
  T.check(N.dueInstall(nil, 10, true), 'dueInstall: an active car never sent to: due')
  T.check(not N.dueInstall(nil, 10, false), 'dueInstall: an inactive (pooled) car: not yet')
  T.check(not N.dueInstall(8, 10, true), 'dueInstall: sent 2 s ago: not again yet')
  T.check(N.dueInstall(10 - N.INSTALL_S, 10, true), 'dueInstall: INSTALL_S since: again')
end
-- a car put ahead is pushed to speed gently, not in the stock boost's 0.25 s (12 g at 70 mph: smoke, dust, sideways)
do
  T.check(math.abs(N.pushTime(26) - 26 / N.PUSH_ACCEL) < 1e-9, 'pushTime: 26 m/s at PUSH_ACCEL')
  T.check(N.pushTime(2) == N.PUSH_S[1] and N.pushTime(80) == N.PUSH_S[2], 'pushTime: within PUSH_S')
  T.check(26 / N.pushTime(26) <= 0.5 * 9.81, 'pushTime: under half a g, the tyres keep rolling')
  local lua = N.pushLua(26)
  T.check(lua:find('thrusters.applyVelocity(obj:getDirectionVector() * 26.00, 6.50)', 1, true), 'pushLua: ' .. lua)
  -- every boost a car gets is stretched, the stock respawn's 0.25 s too (gentleLua, once per car)
  local got = {}
  _G.thrusters = { applyVelocity = function(vel, dt) got[#got + 1] = dt end }
  local function v(x) return { length = function() return x end } end
  assert(loadstring(N.gentleLua()))()
  assert(loadstring(N.gentleLua()))()          -- (sent again every INSTALL_S: wrapped once)
  thrusters.applyVelocity(v(13.9), 0.25)
  thrusters.applyVelocity(v(2), 0.1)
  T.check(#got == 2 and math.abs(got[1] - 13.9 / N.PUSH_ACCEL) < 1e-6 and got[2] == N.PUSH_S[1],
    'gentleLua: 14 m/s in ' .. tostring(got[1]) .. ' s, 2 m/s in ' .. tostring(got[2]) .. ' s')
  _G.thrusters = nil
end
-- a car put somewhere fades in (Jeff's drive 2026-10-29: cars appearing in front of him)
do
  T.check(N.fadeAlpha(0) == 0 and N.fadeAlpha(N.FADE_DELAY_S) == 0, 'fadeAlpha: hidden while it lands')
  T.check(math.abs(N.fadeAlpha(N.FADE_DELAY_S + N.FADE_S / 2) - 0.5) < 1e-9, 'fadeAlpha: half way')
  T.check(N.fadeAlpha(10) == 1, 'fadeAlpha: whole at the end')
  T.check(N.AHEAD_TIGHT_M >= N.AHEAD_M[1] and N.AHEAD_M[1] >= 250, 'never put nearer ahead than 250 m')
  local alphas = {}
  local car = { setMeshAlpha = function(_, a) alphas[#alphas + 1] = a end }
  local g0 = getObjectByID
  getObjectByID = function() return car end
  N.fadeIn(5)
  T.check(alphas[1] == 0, 'fadeIn: hidden at once')
  getObjectByID = g0
end
-- how near a cut a car is respawned: fresh cars far off, unless this module put them ahead along the player's way
do
  T.check(N.cutReach(1, 10, nil) == N.CUT_FRESH_M, 'cutReach: a fresh car (the stock respawn): CUT_FRESH_M')
  T.check(math.abs(N.cutReach(1, 30, true) - (0.5 * 900 / (0.2 * 9.81) + N.PLAN_MARGIN_M)) < 1e-9,
    'cutReach: a fresh car we put ahead: as far as its plan sees at its speed (not CUT_FRESH_M)')
  T.check(N.cutReach(10, 10, nil) == 100 + N.PLAN_MARGIN_M, 'cutReach: an older slow car: the plan 100 m + margin')
  T.check(N.cutReach(10, 0, nil) >= N.CUT_M, 'cutReach: never under CUT_M')
end
-- a car put ahead starts near the player's speed, not standing
do
  T.check(math.abs(N.aheadSpeed(30) - 30 * N.AHEAD_PACE) < 1e-9, 'aheadSpeed: a share of the player\'s speed')
  T.check(N.aheadSpeed(60) == N.AHEAD_V_MAX, 'aheadSpeed: capped')
  T.check(N.aheadSpeed(N.AHEAD_MS) > 13.9, 'aheadSpeed: faster than the stock boost\'s cap from AHEAD_MS on')
end
-- traffic that went off on its own soon after a start or a teleport is set up once again, with an explicit amount
do
  local w = { untilT = 100, seenOn = true, done = false }
  T.check(N.shouldRestart(w, 50, 'off'), 'shouldRestart: ran, went off within the watch')
  T.check(not N.shouldRestart(w, 50, 'on') and not N.shouldRestart(w, 50, 'spawning'), 'shouldRestart: still on or spawning: no')
  T.check(not N.shouldRestart(w, 101, 'off'), 'shouldRestart: after the watch (the player turned it off): no')
  T.check(not N.shouldRestart({ untilT = 100, seenOn = false, done = false }, 50, 'off'), 'shouldRestart: never ran yet: no')
  T.check(not N.shouldRestart({ untilT = 100, seenOn = true, done = true }, 50, 'off') and not N.shouldRestart(nil, 50, 'off'),
    'shouldRestart: once; not watched: no')
  T.check(N.restartAmount({ traffic = true, mode = 'normal', trafficAmount = -1 }, 7) == 7, 'restartAmount: normal: the game\'s own, explicit')
  T.check(N.restartAmount({ traffic = true, mode = 'busy', trafficAmount = -1 }, 7) == 11, 'restartAmount: busy as modeAmount')
  T.check(N.restartAmount({ traffic = true, trafficAmount = 5 }, 7) == 5, 'restartAmount: the configurator\'s amount')
  T.check(N.restartAmount({ traffic = true, trafficAmount = -1 }, 7) == 7, 'restartAmount: the configurator on auto')
  T.check(N.restartAmount({ traffic = false, mode = 'normal' }, 7) == 0 and N.restartAmount({ traffic = true, mode = 'off' }, 7) == 0,
    'restartAmount: no traffic chosen: none')
end
-- the game's own amount here: the setting's less the player's other cars only (not the parked cars still spawning)
do
  local vs = { { id = 1, kind = 'player' }, { id = 2, kind = 'parked' }, { id = 3, kind = 'parked' }, { id = 4, kind = 'traffic' },
    { id = 5, kind = 'player' } }
  T.check(N.autoAmount(7, vs, 1) == 6, 'autoAmount: less the player\'s other car, not the parked or traffic ones')
  T.check(N.autoAmount(1, vs, 1) == 0 and N.autoAmount(nil, vs, 1) == nil, 'autoAmount: never below 0; unknown: nil')
  T.check(N.modeAmount('normal', N.autoAmount(7, {}, 1)) == -1, 'normal: the caller then sets up the own amount explicitly')
end
T.done('test_navgraph')
