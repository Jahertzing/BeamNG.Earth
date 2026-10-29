-- luajit mod/tests/test_parking.lua  (parked cars: the road tiles' spots handed to the stock parking system)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')

-- engine stubs: vectors, a minimal sites object as gameplay/sites/sites.lua builds it, gameplay_parking
function vec3(x, y, z) return { x = x, y = y, z = z } end
function quatFromDir(d) return { d = d } end
function log() end
table.clear = table.clear or function(t) for k in pairs(t) do t[k] = nil end end
local uid = 0
package.preload['/lua/ge/extensions/gameplay/sites/sites'] = function()
  return function()
    local s = { finalized = 0 }
    s.parkingSpots = { objects = {}, sorted = {} }
    function s.parkingSpots.objectConstructor()
      uid = uid + 1
      local ps = { id = uid, customFields = { tags = {} } }
      function ps:set(pos, rot, scl) self.pos, self.rot, self.scl = pos, rot, scl end
      function ps.customFields:addTag(t) self.tags[t] = true end
      return ps
    end
    function s.parkingSpots:sort()
      self.sorted = {}
      for _, o in pairs(self.objects) do self.sorted[#self.sorted + 1] = o end
    end
    function s:finalizeSites() self.finalized = self.finalized + 1 end
    return s
  end
end
local setupCalls, scatterCalls, active, given = 0, 0, false, nil
gameplay_parking = {
  setSites = function(s) given = s end,
  getState = function() return active end,
  setupVehicles = function() setupCalls = setupCalls + 1; active = true end,
  scatterParkedCars = function() scatterCalls = scatterCalls + 1 end,
  getParkedCarsList = function() return {} end,
}
settings = { getValue = function(k) return k == 'trafficParkedAmount' and 16 or nil end }
local clock = 0
os.clock = function() return clock end

local P = require('beamngEarth/parking')
local NG = require('beamngEarth/navgraph')
NG.takeChoice({ trafficMode = 'disabled', trafficParked = 'enabled' })   -- the player chose parked cars
local player = { x = 0, y = 0, z = 0 }

local function rows(n, x0, y)
  local r = {}
  for i = 1, n do r[i] = { x0 + i * 6.5, y, 1.0, 90 } end
  return r
end

T.check(P.spotKey({ 1.04, -2.0, 0, 0 }) == '1.0_-2.0', 'spot key: position to 0.1 m')

-- spots within KEEP_M go to the sites; the cars start once START_MIN are near
P.onRows(1, '1:0_0', rows(30, 0, 10))
P.onRows(1, '1:9_9', rows(5, 5000, 0))            -- far away: not in the sites
P.update(1, player)
T.check(given and #given.parkingSpots.sorted == 30, 'the 30 near spots are in the sites')
T.check(given.finalized == 1, 'quadtree built')
local s1 = given.parkingSpots.sorted[1]
T.check(s1.scl.x == 2.5 and s1.scl.y == 6.0 and s1.customFields.tags.street and s1.customFields.tags.forwards,
  'stock spot size, street + forwards tags')
T.check(setupCalls == 1, 'parked cars started (the player chose them)')
P.onRows(1, '1:0_1', { { 50, 50, 1.0, 0, 1 } })
clock = clock + 3
P.update(1, player)
local lot
for _, ps in pairs(given.parkingSpots.objects) do if ps.pos.x == 50 then lot = ps end end
T.check(lot and not lot.customFields.tags.street and not lot.customFields.tags.forwards, 'a lot stall: no street / forwards tags')
P.dropKey('1:0_1')
clock = clock + 3
P.update(1, player)

-- rebuilds keep the same spot objects (a car's spot stays its spot)
local ids = {}
for id in pairs(given.parkingSpots.objects) do ids[id] = true end
P.onRows(1, '1:1_0', rows(3, 300, 10))
clock = clock + 1
P.update(1, player)
T.check(#given.parkingSpots.sorted == 30, 'no rebuild within REBUILD_S')
clock = clock + 2
P.update(1, player)
T.check(#given.parkingSpots.sorted == 33, 'rebuilt with the new tile')
local same = 0
for id in pairs(given.parkingSpots.objects) do if ids[id] then same = same + 1 end end
T.check(same == 30, 'the earlier spots kept their objects')

-- a dropped tile's spots go, except one a car stands on
local occupied
for _, ps in pairs(given.parkingSpots.objects) do
  if ps.pos.x > 300 then occupied = ps; ps.vehicle = 7; break end
end
P.dropKey('1:1_0')
clock = clock + 3
P.update(1, player)
T.check(#given.parkingSpots.sorted == 31 and given.parkingSpots.objects[occupied.id] == occupied,
  'dropped tile: its spots go but the occupied one stays')
T.check(setupCalls == 1, 'started once')

-- teleport: scattered onto the new place's spots once there are enough
P.onTeleport()
P.onRows(2, '2:0_0', rows(40, 0, -10))
clock = clock + 3
P.update(2, player)
T.check(#given.parkingSpots.sorted == 40, 'a new anchor: only its spots')
T.check(scatterCalls == 1, 'parked cars scattered onto them')

-- the block: spots EDGE_M inside its ground box only
T.check(P.inside({ 0, 0, 100, 100 }, 50, 50, 10) and not P.inside({ 0, 0, 100, 100 }, 95, 50, 10) and P.inside(nil, 1e9, 0, 10),
  'inside the box shrunk by m (no box: anywhere)')
P.update(2, player, { -100, -1000, 200 + P.EDGE_M, 1000 })
clock = clock + 3
P.update(2, player, { -100, -1000, 200 + P.EDGE_M, 1000 })
local maxX = -1e9
for _, ps in pairs(given.parkingSpots.objects) do maxX = math.max(maxX, ps.pos.x) end
T.check(maxX <= 200.01 and #given.parkingSpots.sorted == 30, 'spots near the block edge left out')

P.reset()
T.check(P.status().spots == 0 and not P.status().started, 'reset')

-- at most MAX_SPOTS, the nearest
P.MAX_SPOTS = 10
active = true
P.onRows(3, '3:0_0', rows(30, 0, 10))
clock = clock + 3
P.update(3, player)
local far = 0
for _, ps in pairs(given.parkingSpots.objects) do far = math.max(far, ps.pos.x) end
T.check(#given.parkingSpots.sorted == 10 and far <= 10 * 6.5 + 0.01, 'the MAX_SPOTS nearest spots only')
P.MAX_SPOTS = 800
P.reset()

-- parked cars off: the sites are not kept up; turning them on (the stock setupVehicles: the radial menu) builds them
-- first
NG.takeChoice({ trafficMode = 'disabled', trafficParked = 'disabled' })
active, given = false, nil
local calls0 = setupCalls
P.onRows(4, '4:0_0', rows(30, 0, 10))
clock = clock + 3
P.update(4, player)
T.check(given and #given.parkingSpots.sorted == 30, 'parked cars off: the sites built once')
P.onRows(4, '4:1_0', rows(3, 300, 10))
clock = clock + 3
P.update(4, player)
T.check(#given.parkingSpots.sorted == 30, 'parked cars off: not rebuilt every REBUILD_S')
gameplay_parking.setupVehicles(8)
T.check(#given.parkingSpots.sorted == 33 and setupCalls == calls0 + 1, 'turned on: the sites rebuilt first, then the cars')
P.reset()
local P0 = { x = 0, y = 0 }
T.check(not P.offSpot({ x = 10, y = 0 }, nil, P0), 'near the player: never moved (one it pushed stays in view)')
T.check(P.offSpot({ x = 100, y = 0 }, nil, P0) and P.offSpot({ x = 100, y = 0 }, { missing = true }, P0),
  'far, no spot (or another frame\'s): moved')
T.check(not P.offSpot({ x = 100, y = 0 }, { pos = { x = 101, y = 1 } }, P0), 'on its spot: stays')
T.check(P.offSpot({ x = 100, y = 0 }, { pos = { x = 110, y = 0 } }, P0), 'off its spot: moved')
-- nothing parked appears or disappears in view (Jeff's drive 2026-10-28: a car lot's cars flickering in and out)
T.check(P.inView(100, 0, 0, 0, 1, 0), 'inView: 100 m straight ahead')
T.check(not P.inView(-100, 0, 0, 0, 1, 0), 'inView: 100 m behind: not')
T.check(P.inView(-20, 0, 0, 0, 1, 0), 'inView: 20 m behind (mirrors, side windows): in view')
T.check(not P.inView(P.VIEW_M + 10, 0, 0, 0, 1, 0), 'inView: past VIEW_M: not')
T.check(not P.inView(100, 0, nil, nil, nil, nil), 'inView: no camera: not')
do
  -- spots ahead of the camera banned, behind not; a parked car ahead held, one behind left to the stock system
  local cam = { pos = vec3(0, 0, 0), fwd = vec3(1, 0, 0) }
  core_camera = { getPosition = function() return cam.pos end, getForward = function() return cam.fwd end }
  local cars = {
    [71] = { p = vec3(200, 0, 0) },      -- in view, on a lot ahead
    [72] = { p = vec3(-300, 0, 0) },     -- behind the camera, far: the stock system may move it
  }
  for _, c in pairs(cars) do
    function c:getActive() return true end
    function c:getPosition() return self.p end
    function c:getVelocity() return vec3(0, 0, 0) end
    function c:setActive() end
  end
  function getObjectByID(id) return cars[id] end
  local data = { [71] = {}, [72] = {} }
  local teleported = {}
  active = true
  gameplay_parking.getParkedCarsList = function() return { 71, 72 } end
  gameplay_parking.getParkedCarsData = function() return data end
  gameplay_parking.forceTeleport = function(id) teleported[#teleported + 1] = id end
  P.reset()
  P.onRows(9, '9:0_0', { { 200, 5, 1600, 0, 1 }, { -300, 5, 1600, 0, 1 }, { 120, -6, 1600, 90, 1 } })
  clock = clock + 3
  P.update(9, { x = 0, y = 0, z = 1600 }, { -1000, -1000, 1000, 1000 })
  clock = clock + 1
  P.update(9, { x = 0, y = 0, z = 1600 }, { -1000, -1000, 1000, 1000 })
  local ahead, behind = 0, 0
  for _, ps in pairs(given.parkingSpots.objects) do
    if ps.pos.x > 0 and ps.customFields.tags.banned then ahead = ahead + 1 end
    if ps.pos.x < 0 and ps.customFields.tags.banned then behind = behind + 1 end
  end
  T.check(ahead == 2 and behind == 0, 'holdView: the 2 spots in view banned, the one behind not: ' .. ahead .. '/' .. behind)
  T.check(data[71].ignoreTeleport and not data[72].ignoreTeleport, 'holdView: the car in view held, the one behind not')
  -- the camera turns round: the hold moves with it, and only what this module set is undone
  cam.fwd = vec3(-1, 0, 0)
  clock = clock + 1
  P.update(9, { x = 0, y = 0, z = 1600 }, { -1000, -1000, 1000, 1000 })
  T.check(not data[71].ignoreTeleport and data[72].ignoreTeleport, 'holdView: turned round, the holds follow')
  local nowAhead = 0
  for _, ps in pairs(given.parkingSpots.objects) do
    if ps.pos.x > 0 and ps.customFields.tags.banned then nowAhead = nowAhead + 1 end
  end
  T.check(nowAhead == 0, 'holdView: the spots now behind free again')
  -- a car off its spot but in view stays; out of view it is moved
  clock = clock + 1
  P.update(9, { x = 0, y = 0, z = 1600 }, { -1000, -1000, 1000, 1000 })   -- (a car the first pass hid comes back)
  for i = #teleported, 1, -1 do teleported[i] = nil end
  data[72].parkingSpotId = nil
  cars[72].p = vec3(-300, 50, 0)
  cam.fwd = vec3(-1, 0, 0)
  clock = clock + 1
  P.update(9, { x = 0, y = 0, z = 1600 }, { -1000, -1000, 1000, 1000 })
  local function moved(id) for _, t in ipairs(teleported) do if t == id then return true end end return false end
  T.check(not moved(72), 'guard: a car off its spot in view is not moved')
  cam.fwd = vec3(1, 0, 0)
  clock = clock + 1
  P.update(9, { x = 0, y = 0, z = 1600 }, { -1000, -1000, 1000, 1000 })
  T.check(moved(72), 'guard: out of view it is')
  core_camera, getObjectByID = nil, nil
  P.reset()
end
do
  -- the guard moves MOVES_PER_PASS cars a pass, the rest on the next one (GUARD_BACKLOG_S later); a falling car with no
  -- move left is hidden at once (south Denver 2026-10-29: one frame of parking.lua moves over 90 ms)
  local cam = { pos = vec3(0, 0, 0), fwd = vec3(1, 0, 0) }
  core_camera = { getPosition = function() return cam.pos end, getForward = function() return cam.fwd end }
  local cars = {}
  for i, id in ipairs({ 81, 82, 83, 84 }) do
    local c = { p = vec3(-300, i * 10, 0), v = vec3(0, 0, 0), on = true }
    function c:getActive() return self.on end
    function c:getPosition() return self.p end
    function c:getVelocity() return self.v end
    function c:setActive(a) self.on = a == 1 end
    cars[id] = c
  end
  function getObjectByID(id) return cars[id] end
  local data = { [81] = {}, [82] = {}, [83] = {}, [84] = {} }
  local teleported = {}
  active = true
  gameplay_parking.getParkedCarsList = function() return { 81, 82, 83, 84 } end
  gameplay_parking.getParkedCarsData = function() return data end
  gameplay_parking.forceTeleport = function(id)
    teleported[#teleported + 1] = id
    cars[id].p, cars[id].v = vec3(10, 0, 0), vec3(0, 0, 0)   -- (onto a spot by the player)
  end
  P.reset()
  local rows = {}
  for i = 1, 10 do rows[i] = { 150 + i * 5, -150, 1600, 0, 1 } end   -- (out of view, 100-400 m: free)
  P.onRows(9, '9:0_0', rows)
  cam.fwd = vec3(0, 1, 0)
  clock = clock + 3
  P.update(9, { x = 0, y = 0, z = 1600 }, { -1000, -1000, 1000, 1000 })   -- (the sites built: no spot data before)
  cars[84].v = vec3(0, 0, -30)            -- falling
  for i = #teleported, 1, -1 do teleported[i] = nil end
  clock = clock + P.GUARD_S + 0.01
  P.update(9, { x = 0, y = 0, z = 1600 }, { -1000, -1000, 1000, 1000 })
  T.check(#teleported == P.MOVES_PER_PASS, 'guard: one move a pass: ' .. #teleported)
  T.check(not cars[84].on, 'guard: a falling car past the move cap is hidden at once')
  clock = clock + P.GUARD_BACKLOG_S + 0.01
  P.update(9, { x = 0, y = 0, z = 1600 }, { -1000, -1000, 1000, 1000 })
  T.check(#teleported == 2 * P.MOVES_PER_PASS, 'guard: the next move a backlog interval later: ' .. #teleported)
  for _ = 1, 6 do
    clock = clock + P.GUARD_BACKLOG_S + 0.01
    P.update(9, { x = 0, y = 0, z = 1600 }, { -1000, -1000, 1000, 1000 })
  end
  T.check(cars[84].on, 'guard: the hidden car comes back once the others are moved')
  T.check(P.parts.guard >= 0 and P.parts.view >= 0, 'parts: guard and view timed')
  core_camera, getObjectByID = nil, nil
  P.reset()
end
T.done('test_parking')
