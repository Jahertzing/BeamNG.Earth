-- luajit mod/tests/test_mergeassist.lua  (ramp cars finding a gap in the motorway's traffic)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
function vec3(x, y, z) return { x = x, y = y, z = z } end
local A = require('beamngEarth/mergeassist')

-- A motorway running east along y = 0 (m1 -> m2 -> j -> m3), a ramp coming in from the south-west (r1 -> r2 -> j),
-- an exit ramp leaving it at m3 for a two-way street (m3 -> x1 -> s1, s1 - s2 both ways).
local function node(x, y, r) return { pos = vec3(x, y, 0), radius = r or 7.4, links = {} } end
local nodes = { m1 = node(-400, 0), m2 = node(-200, 0), j = node(0, 0), m3 = node(200, 0), m4 = node(400, 0),
                r1 = node(-220, -60, 2.5), r2 = node(-100, -20, 2.5), x1 = node(300, -40, 2.5),
                s1 = node(380, -120, 4), s2 = node(380, -300, 4) }
local function link(a, b, oneWay, ramp)
  local e = { oneWay = oneWay, ramp = ramp, inNode = a, outNode = b, speedLimit = ramp and 20 or 31 }
  nodes[a].links[b] = e
  nodes[b].links[a] = e
end
link('m1', 'm2', true) link('m2', 'j', true) link('j', 'm3', true) link('m3', 'm4', true)
link('r1', 'r2', true, true) link('r2', 'j', true, true)
link('m3', 'x1', true, true) link('x1', 's1', true, true) link('s1', 's2', false)

-- the merge ahead of a car on the ramp
local m = A.findMerge(nodes, 'r1', 'r2', vec3(-200, -53, 0))
T.check(m and m.j == 'j', 'on the ramp: its merge point is j')
T.near(m.d, math.sqrt(100 ^ 2 + 33 ^ 2) + math.sqrt(100 ^ 2 + 20 ^ 2), 1e-6, 'the distance along the ramp to it')
T.check(#m.up >= 3 and m.up[1] == nodes.j.pos and m.up[2] == nodes.m2.pos, 'the motorway back from j: j, m2, m1')
T.check(m.halfWidth == 7.4, 'its half width: the motorway\'s')
T.check(A.findMerge(nodes, 'r1', 'r2', vec3(-500, -200, 0), 250) == nil, 'farther than LOOK_M: none yet')
T.check(A.findMerge(nodes, 'm2', 'j', vec3(-100, 0, 0)) == nil, 'on the motorway itself: none')
T.check(A.findMerge(nodes, 'x1', 's1', vec3(320, -60, 0)) == nil, 'an exit ramp ending in a street: none')
T.check(A.findMerge(nodes, 'r2', 'r1', vec3(-200, -53, 0)) == nil, 'the ramp the wrong way (not its one-way): none')

-- who is on the motorway there
local vehicles = {
  { id = 1, pos = { x = -150, y = 3 }, vel = { x = 30, y = 0 } },      -- 150 m before j, at 30 m/s
  { id = 2, pos = { x = -60, y = -3 }, vel = { x = 25, y = 0 } },      -- 60 m before j
  { id = 3, pos = { x = 20, y = 0 }, vel = { x = 22, y = 0 } },        -- 20 m past j
  { id = 4, pos = { x = -150, y = 30 }, vel = { x = 30, y = 0 } },     -- 30 m off: another road
  { id = 5, pos = { x = -100, y = 2 }, vel = { x = -30, y = 0 } },     -- going the other way
  { id = 9, pos = { x = -200, y = -53 }, vel = { x = 18, y = 6 } },    -- the ramp car itself
}
local mains = A.mainline(m, vehicles, 9)
table.sort(mains, function(a, b) return a.d > b.d end)
T.check(#mains == 3, 'three on the motorway going its way: ' .. #mains)
T.near(mains[1].d, 150, 1e-6, '... 150 m before j')
T.near(mains[3].d, -20, 1e-6, '... and one 20 m past it (negative)')

-- the gap
T.check(A.choose(150, 20, 20, {}) == nil, 'an empty motorway: no cap')
T.check(A.choose(150, 20, 20, { { d = 400, v = 30 } }) == nil, 'a car far back (13 s away vs 7.5 s): no cap')
local v = A.choose(150, 20, 20, { { d = 225, v = 30 } })   -- both at the merge point in 7.5 s
T.check(v and v < 20, 'a car arriving with it: it slows to fall in behind: ' .. tostring(v))
T.check(math.abs(150 / v - (7.5 + A.GAP_S)) < 0.05, '... GAP_S after it')
local v2 = A.choose(150, 20, 20, { { d = 250, v = 30 } })  -- 8.33 s vs 7.5 s: going ahead needs 21.4 m/s (within V_UP)
T.check(v2 and v2 > 20, 'a car just behind: it goes ahead, a little faster: ' .. tostring(v2))
local v3 = A.choose(150, 20, 20, { { d = 225, v = 30 }, { d = 180, v = 30 }, { d = 135, v = 30 }, { d = 270, v = 30 } })
T.check(v3 and v3 >= A.V_MIN, 'a platoon: a cap no lower than V_MIN: ' .. tostring(v3))
local T3 = 150 / v3
local ok = v3 == A.V_MIN
if not ok then
  ok = true
  for _, d in ipairs({ 225, 180, 135, 270 }) do if math.abs(d / 30 - T3) < A.GAP_S then ok = false end end
end
T.check(ok, '... clear of every car of it, or V_MIN')

-- the GE glue with a stubbed game: the ramp car gets a cap, and gets it back once on the motorway
do
  local objs = {}
  local function car(id, x, y, vx, vy)
    local o = { id = id, lua = {}, p = vec3(x, y, 0), u = vec3(vx, vy, 0) }
    function o:getId() return self.id end
    function o:getActive() return true end
    function o:getPosition() return self.p end
    function o:getVelocity() return self.u end
    function o:getDirectionVector() return vec3(1, 0, 0) end
    function o:queueLuaCommand(s) self.lua[#self.lua + 1] = s end
    objs[#objs + 1] = o
    return o
  end
  local rampCar = car(9, -200, -53, 18, 6)
  car(1, -326, 0, 30, 0)                    -- arrives with it (10.9 s, as the ramp car)
  be = { getObjectCount = function() return #objs end, getObject = function(_, i) return objs[i + 1] end }
  local function find(p)
    if p.y < -5 then return 'r2', 'r1' end  -- (the stock search may give the nodes either way round)
    return 'j', 'm3'
  end
  local capped = {}
  local n, new = A.update({ 9 }, nodes, capped, find)
  T.check(n == 1 and new == 1 and capped[9] and rampCar.lua[1]:find("setSpeedMode('limit')", 1, true),
    'the ramp car capped: ' .. tostring(rampCar.lua[1]))
  local n2, new2 = A.update({ 9 }, nodes, capped, find)
  T.check(n2 == 1 and new2 == 0 and #rampCar.lua == 1, 'the same cap again: not sent again')
  rampCar.p, rampCar.u = vec3(50, 0, 0), vec3(25, 0, 0)
  A.update({ 9 }, nodes, capped, find)
  T.check(not capped[9] and rampCar.lua[2]:find("setSpeedMode('legal')", 1, true), 'merged: the cap lifted')
  local n3 = A.update({ 9 }, nodes, capped, find, function() return true end)
  T.check(n3 == 0, 'a car the brain drives: left be')
  be = nil
end

T.done('test_mergeassist')
