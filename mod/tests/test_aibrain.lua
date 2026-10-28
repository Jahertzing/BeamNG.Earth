-- luajit mod/tests/test_aibrain.lua  (the outside driver bridge's pure parts: lanes, car frame, observations, actions)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local B = require('beamngEarth/aibrain')

-- a two-way road, one lane each way ('-+', 7 m): looking along in -> out the '+' lane is the right half
local li = B.laneInfo('-+', 3.5, 1.75, true)
T.check(li and li.n == 1 and li.idx == 1, 'two-way, along: one lane my way, the first')
T.near(li.width, 3.5, 1e-9, 'lane width = 2 * halfWidth / lanes')
T.near(li.err, 0, 1e-9, 'on the lane centre')
local back = B.laneInfo('-+', 3.5, -1.75, false)
T.check(back and back.n == 1 and back.idx == 1 and math.abs(back.err) < 1e-9, 'two-way, against: the left half is mine')
T.check(B.laneInfo('+', 2, 0, false) == nil, 'a one-way driven against: no lane my way')
T.check(B.laneInfo('', 2, 0, true) == nil and B.laneInfo(nil, 2, 0, true) == nil, 'no lanes string: nil')

-- I-25's southbound carriageway as a one-way with four lanes, 14.8 m
local w = 14.8 / 4
local far = B.laneInfo('++++', 7.4, -7.4 + 0.5 * w, true)
T.check(far.n == 4 and far.idx == 1, 'one-way: leftmost lane is 1')
local kerb = B.laneInfo('++++', 7.4, 7.4 - 0.5 * w + 0.3, true)
T.check(kerb.idx == 4, 'one-way: rightmost lane is 4')
T.near(kerb.err, 0.3, 1e-9, '... 0.3 m right of its centre')
T.check(B.laneInfo('++++', 7.4, 20, true).idx == 4, 'off the pavement on the right: clamped to the last lane')
-- a 2+1 road: two lanes along, one against ('-++')
local two = B.laneInfo('-++', 5.25, 3.5, true)
T.check(two.n == 2 and two.idx == 2, '2+1 along: my right lane is my second')
local one = B.laneInfo('-++', 5.25, 3.5, false)
T.check(one.n == 1 and one.idx == 1, '2+1 against: one lane, clamped to it')

-- the car frame: a car heading north (0, 1); a point 10 m east is 10 m to its right
local f, l = B.toCar(10, 0, 0, 1)
T.near(f, 0, 1e-9, 'east of a north-facing car: not ahead')
T.near(l, -10, 1e-9, '... 10 m right (left negative)')
f, l = B.toCar(0, 20, 0, 1)
T.near(f, 20, 1e-9, 'north of it: 20 m ahead')

-- observations: nearest first, capped, in range only, own car left out
local me = { id = 1, x = 0, y = 0, vx = 0, vy = 30, fx = 0, fy = 1, len = 4.6 }
local others = { me,
  { id = 2, x = 0, y = 40, vx = 0, vy = 25, fx = 0, fy = 1, len = 4.4 },
  { id = 3, x = -3.7, y = -10, vx = 0, vy = 33, fx = 0, fy = 1, len = 5 },
  { id = 4, x = 0, y = 400, vx = 0, vy = 30, fx = 0, fy = 1, len = 5 },
  { id = 5, x = 8, y = 60, vx = 0, vy = -30, fx = 0, fy = -1, len = 5, player = true } }
local road = { speedLimit = 31.3, lanes = '+++', halfWidth = 5.55, right = 0, along = true, oneWay = true, heading = 0 }
local o = B.observe(me, others, road, { steer = 0, throttle = 0.3, brake = 0 })
T.near(o.speed, 30, 1e-9, 'own speed')
T.check(o.road.lanes == 3 and o.road.lane == 2, 'middle of three lanes: lane 2 of 3')
T.check(#o.near == 3, 'three others in range (the one 400 m off left out): ' .. #o.near)
T.check(o.near[1].id == 3 and o.near[2].id == 2 and o.near[3].id == 5, 'nearest first')
T.near(o.near[1].fwd, -10, 1e-9, 'the car behind: 10 m back')
T.near(o.near[1].left, 3.7, 1e-9, '... one lane to the left')
T.near(o.near[2].vFwd, 25, 1e-9, 'the car ahead: its speed along my way')
T.check(o.near[2].same and not o.near[3].same and o.near[3].player, 'oncoming flagged, player flagged')
T.check(o.near[1].d2 == nil, 'no working fields in the message')
T.check(o.inputs.throttle == 0.3, 'own inputs passed through (the labels)')
local old = B.MAX_NEAR
B.MAX_NEAR = 1
T.check(#B.observe(me, others, road).near == 1, 'MAX_NEAR caps the list')
B.MAX_NEAR = old
T.check(B.observe(me, others, nil).road == nil, 'off the graph: no road')

-- actions are clamped per level, junk refused
local a = B.clampAction('tactical', { lane = -0.9, speed = 120 })
T.check(a.lane == -1 and a.speed == 70, 'tactical: lane rounded, speed capped')
T.check(B.clampAction('tactical', {}).lane == 0 and B.clampAction('tactical', {}).speed == nil, 'tactical: nothing = keep')
T.check(B.clampAction('target', { ahead = 20, left = 1 }) == nil, 'target without a speed: refused')
local t = B.clampAction('target', { ahead = 500, left = -50, speed = 20 })
T.check(t.ahead == 200 and t.left == -30, 'target clamped')
local r = B.clampAction('raw', { steer = 2, throttle = -1, brake = 0.5 })
T.check(r.steer == 1 and r.throttle == 0 and r.brake == 0.5, 'raw clamped')
T.check(B.clampAction('raw', { steer = 0 / 0, throttle = 0, brake = 0 }) == nil, 'raw NaN refused')
T.check(B.clampAction('fly', {}) == nil and B.clampAction('raw', 'x') == nil, 'unknown level or not a table: refused')

-- the vehicle Lua
local lua, changed = B.actionLua('tactical', { lane = 1, speed = 29 }, 30, 3.7)
T.check(changed and lua:find("ai.setSpeed(29.00)", 1, true) and lua:find('ai.laneChange(nil, 90.0, 3.70)', 1, true),
  'tactical: limit speed, lane change right spread over 3 s at 30 m/s: ' .. lua)
lua, changed = B.actionLua('tactical', { lane = -1 }, 5, 3.7)
T.check(changed and lua:find('ai.laneChange(nil, 30.0, -3.70)', 1, true) and lua:find("setSpeedMode('legal')", 1, true),
  'tactical: no speed = back to legal; left at a crawl over LANE_MIN_M')
lua, changed = B.actionLua('tactical', { lane = 1 }, 30, nil)
T.check(not changed and not lua:find('laneChange', 1, true), 'tactical: no lane width known, no lane change')
lua = B.actionLua('raw', { steer = -0.25, throttle = 0.5, brake = 0 })
T.check(lua:find("input.event('steering', -0.2500", 1, true) and lua:find("'FILTER_AI'", 1, true), 'raw: input events')

-- a target point in the car's frame back to the world
local x, y = B.targetPoint({ ahead = 20, left = 3 }, 100, 200, 5, 0, 1)
T.near(x, 97, 1e-9, 'north-facing car, 3 m left = 3 m west')
T.near(y, 220, 1e-9, '20 m ahead = 20 m north')

-- each action once; a quiet brain expires it once
local c = { act = { lane = 0 }, actAt = 10, new = true }
T.check(B.due(c, 10.05) == 'apply', 'a new action: apply')
c.new = false
T.check(B.due(c, 10.3) == nil, 'applied, still fresh: nothing')
T.check(B.due(c, 10.6) == 'expire', 'quiet for STALE_S: expire')
c.expired = true
T.check(B.due(c, 11) == nil, '... once')
T.check(B.due({}, 1) == nil, 'no action yet: nothing')

-- the round trip with a stubbed game and socket: start, a tick sends the traffic cars' state, an action comes back
-- and is applied once as vehicle Lua, stop hands the cars back to the stock AI
do
  function jsonEncode(v) return v end    -- (the stub socket carries tables)
  function jsonDecode(v) return v end
  function log() end
  function vec3(x, y, z) return { x = x, y = y, z = z } end
  local sent, inbox = {}, {}
  package.loaded['socket'] = { udp = function()
    return { settimeout = function() end, setpeername = function() return 1 end, close = function() end,
             send = function(_, d) sent[#sent + 1] = d return 1 end,
             receive = function() return table.remove(inbox, 1) end }
  end }
  local function car(id, x, y, vy)
    local o = { id = id, lua = {} }
    function o:getId() return self.id end
    function o:getActive() return true end
    function o:getPosition() return vec3(x, y, 0) end
    function o:getVelocity() return vec3(0, vy, 0) end
    function o:getDirectionVector() return vec3(0, 1, 0) end
    function o:getInitialLength() return 4.5 end
    function o:queueLuaCommand(s) self.lua[#self.lua + 1] = s end
    return o
  end
  local objs = { car(7, 0, 0, 30), car(8, 0, 40, 25), car(1, 3.7, -20, 33) }
  local byId = {}
  for _, o in ipairs(objs) do byId[o.id] = o end
  be = { getPlayerVehicleID = function() return 1 end, getObjectCount = function() return #objs end,
         getObject = function(_, i) return objs[i + 1] end }
  function getObjectByID(id) return byId[id] end
  gameplay_traffic = { getTrafficData = function() return { [7] = {}, [8] = {} } end }
  map = nil                                    -- (no road graph: observations without a road)
  T.check(B.start({ level = 'tactical' }) == 'ok', 'start')
  B.onUpdate()
  T.check(#sent == 1 and #sent[1].cars == 2, 'one datagram with the two traffic cars (not the player): ' .. #sent)
  local o7 = sent[1].cars[1]
  T.check(o7.id == 7 and #o7.near == 2 and o7.near[1].player and o7.near[2].id == 8,
    'car 7 sees the player 20 m behind, then 8 ahead')
  T.check(#byId[7].lua == 1 and byId[7].lua[1]:find('steering_input', 1, true), 'each car is asked for its inputs')
  inbox[1] = { seq = sent[1].seq, actions = { ['7'] = { lane = 0, speed = 20 }, ['99'] = { lane = 1 } } }
  B.onUpdate()
  T.check(byId[7].lua[2] and byId[7].lua[2]:find('ai.setSpeed(20.00)', 1, true), 'the action applied to car 7')
  local n = #byId[7].lua
  B.onUpdate()
  T.check(#byId[7].lua == n, '... once, not every frame')
  local st = B.status()
  T.check(st.running and st.cars == 2 and st.recv == 1 and st.applied == 1, 'status counts')
  T.check(B.stop() == 'ok' and byId[7].lua[#byId[7].lua]:find("setSpeedMode('legal')", 1, true), 'stop: back to legal')
  T.check(not B.status().running, 'stopped')
  T.check(B.start({ level = 'warp' }) ~= 'ok', 'an unknown level is refused')
end

T.done('test_aibrain')
