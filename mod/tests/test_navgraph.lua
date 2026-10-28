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
  local box = { -256, -256, 256, 256 }
  local x, y, dx, dy, lo, hi = N.aheadSpot(0, 0, 30, 0, box)
  T.check(x == 0 and dx == 1 and dy == 0 and lo == N.AHEAD_M[1] and hi == math.min(N.AHEAD_M[2], 256 - N.GUARD_M - 20),
    'aheadSpot: 67 mph east, ahead along the way: ' .. tostring(hi))
  T.check(N.aheadSpot(0, 0, 10, 0, box) == nil, 'aheadSpot: 22 mph: behind the camera as before')
  T.check(N.aheadSpot(150, 0, 30, 0, box) == nil, 'aheadSpot: near the block edge ahead: no room, behind')
  local _, _, _, _, _, h2 = N.aheadSpot(0, 0, 0, -30, box)
  T.check(h2 and h2 <= 256 - N.GUARD_M - 20, 'aheadSpot: south, limited by the box too')
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
-- how near a cut a car is respawned: fresh cars far off, unless this module put them ahead along the player's way
do
  T.check(N.cutReach(1, 10, nil) == N.CUT_FRESH_M, 'cutReach: a fresh car (the stock respawn): CUT_FRESH_M')
  T.check(N.cutReach(1, 30, true) == 30 * N.CUT_S, 'cutReach: a fresh car we put ahead: its speed\'s reach, not CUT_FRESH_M')
  T.check(N.cutReach(10, 10, nil) == N.CUT_M, 'cutReach: an older slow car: CUT_M')
  T.check(N.cutReach(1, 30, true) < N.CUT_FRESH_M, 'cutReach: within the AHEAD_M spots of the far end, so no loop')
end
T.done('test_navgraph')
