-- An outside "brain" for the AI cars (docs/AI_BRAIN.md): every TICK_S this extension sends the state of the cars it
-- drives to a program on this PC (tools/aibrain.py, or your own) as one UDP datagram of JSON, and applies the actions
-- that come back. Nothing here runs until start(): it is not in modScript, so normal play never loads it. From the
-- game's console (~) or tools/mcp.py:
--   extensions.load('beamngEarth_aibrain'); beamngEarth_aibrain.start({ level = 'tactical' })
--   beamngEarth_aibrain.status()    beamngEarth_aibrain.stop()
--
-- Three levels of control, the stock ai.lua doing less at each:
--   tactical  the stock traffic AI keeps its route, junctions, signals and steering; the brain picks a lane change
--             (ai.laneChange) and a speed (ai.setSpeedMode('limit') + ai.setSpeed). Action { lane = -1 | 0 | 1, speed }
--   target    the brain picks a point to drive at and a speed; the stock low-level controller (ai.lua driveToTarget:
--             steering, ABS, traction control) drives there (ai.setSlotTrafficTarget, mode 'slotTraffic', which brakes
--             to a stop by itself when no target came for 0.5 s). Action { ahead = m, left = m, speed }
--   raw       the brain sends the pedals and the wheel (input.event, the stock AI off). Action { steer = -1..1
--             (positive right, as ai.lua's), throttle = 0..1, brake = 0..1 }; STALE_S without one: full brake
-- Positions the brain sees are the car's own frame (forward, left), so a floating-origin rebase changes nothing for it.
local M = {}

M.HOST, M.PORT = '127.0.0.1', 47620
M.TICK_S = 0.1          -- 10 Hz: a System One-style model answers in 70-500 ms; ai.lua's own planner runs every frame
M.STALE_S = 0.5         -- an action older than this is not applied (raw: brake; target: slotTraffic stops by itself)
M.RANGE_M = 150         -- other vehicles this near are in a car's observation
M.MAX_NEAR = 8          -- at most this many, nearest first
M.LANE_COOLDOWN_S = 4   -- a car changes lane at most once in this time (a laneChange shifts the plan by a lane width)
M.LANE_MIN_M = 30       -- a lane change is spread over max(this, LANE_TIME_S at the car's speed)
M.LANE_TIME_S = 3
M.LEVELS = { tactical = true, target = true, raw = true, observe = true }

local running = false
local opts = nil
local sock = nil
local seq = 0
local lastTick = -math.huge
local cars = {}         -- id -> { level, act, actAt, laneAt, inputs, took }
local stats = { sent = 0, recv = 0, applied = 0, stale = 0, bad = 0, lastRttMs = nil, err = nil }
local sentAt = {}       -- seq -> os.clock() (round trip)

-- Pure: which lanes of an edge a car is in. lanes: the edge's lanes string as navgraph.lanes made it ('-' against
-- in -> out, '+' along, written left to right looking along in -> out); halfWidth: the edge's half width (m);
-- right: the car's offset to the right of the centreline looking along in -> out (m); along: true when the car drives
-- in -> out. Returns { n = lanes in the car's direction, idx = 1 for its leftmost, width = m, err = m from that lane's
-- centre (positive: right of it, looking the car's way) } or nil when the edge has no lane the car's way.
function M.laneInfo(lanes, halfWidth, right, along)
  if type(lanes) ~= 'string' or #lanes == 0 or not halfWidth or halfWidth <= 0 then return nil end
  local total = #lanes
  local w = 2 * halfWidth / total
  if not along then
    lanes = lanes:reverse()
    right = -right
  end
  local mine = along and '+' or '-'
  local first, last = nil, nil
  for k = 1, total do
    if lanes:sub(k, k) == mine then
      first = first or k
      last = k
    end
  end
  if not first then return nil end
  -- the lane under the car: k counted from the left edge at -halfWidth (looking the car's way)
  local k = math.floor((right + halfWidth) / w) + 1
  k = math.max(first, math.min(last, k))
  local centre = -halfWidth + (k - 0.5) * w
  return { n = last - first + 1, idx = k - first + 1, width = w, err = right - centre }
end

-- Pure: (dx, dy) in the world turned into the frame of a car heading (fx, fy) (unit): forward, left.
function M.toCar(dx, dy, fx, fy)
  return dx * fx + dy * fy, -dx * fy + dy * fx
end

-- Pure: a car's observation. me: { id, x, y, vx, vy, fx, fy, len }; others: { id, x, y, vx, vy, fx, fy, len, player };
-- road: { speedLimit, lanes, halfWidth, right, along, oneWay } (nil: off the graph); inputs: { steer, throttle, brake }
-- the car's own last inputs (the stock AI's or the player's: the labels for learning from them).
function M.observe(me, others, road, inputs)
  local speed = math.sqrt(me.vx * me.vx + me.vy * me.vy)
  local o = { id = me.id, speed = speed, len = me.len, inputs = inputs }
  if road then
    local li = M.laneInfo(road.lanes, road.halfWidth, road.right, road.along)
    o.road = { limit = road.speedLimit, oneWay = road.oneWay and true or false, lanes = li and li.n or 0,
               lane = li and li.idx or 0, laneWidth = li and li.width or nil, laneErr = li and li.err or nil,
               heading = road.heading }
  end
  local near = {}
  for _, v in ipairs(others) do
    if v.id ~= me.id then
      local dx, dy = v.x - me.x, v.y - me.y
      local d2 = dx * dx + dy * dy
      if d2 <= M.RANGE_M * M.RANGE_M then
        local fwd, left = M.toCar(dx, dy, me.fx, me.fy)
        local vf, vl = M.toCar(v.vx, v.vy, me.fx, me.fy)
        local hf = M.toCar(v.fx, v.fy, me.fx, me.fy)
        near[#near + 1] = { id = v.id, d2 = d2, fwd = fwd, left = left, vFwd = vf, vLeft = vl,
                            same = hf > 0.5, len = v.len, player = v.player or nil }
      end
    end
  end
  table.sort(near, function(a, b) return a.d2 < b.d2 end)
  for i = #near, M.MAX_NEAR + 1, -1 do near[i] = nil end
  for _, n in ipairs(near) do n.d2 = nil end
  o.near = near
  return o
end

-- Pure: an action from the brain made safe for level, or nil (and why) when it cannot be applied.
function M.clampAction(level, a)
  if type(a) ~= 'table' then return nil, 'not a table' end
  local function num(v, lo, hi)
    v = tonumber(v)
    if not v or v ~= v then return nil end
    return math.max(lo, math.min(hi, v))
  end
  if level == 'tactical' then
    local lane = tonumber(a.lane) or 0
    lane = lane > 0.5 and 1 or (lane < -0.5 and -1 or 0)
    return { lane = lane, speed = a.speed ~= nil and num(a.speed, 0, 70) or nil }
  elseif level == 'target' then
    local ahead, left, sp = num(a.ahead, 1, 200), num(a.left, -30, 30), num(a.speed, 0, 70)
    if not (ahead and left and sp) then return nil, 'target needs ahead, left, speed' end
    return { ahead = ahead, left = left, speed = sp }
  elseif level == 'raw' then
    local s, t, b = num(a.steer, -1, 1), num(a.throttle, 0, 1), num(a.brake, 0, 1)
    if not (s and t and b) then return nil, 'raw needs steer, throttle, brake' end
    return { steer = s, throttle = t, brake = b }
  elseif level == 'observe' then
    return {}
  end
  return nil, 'unknown level ' .. tostring(level)
end

-- Pure: the vehicle Lua that applies action a at level (speed m/s for the lane-change spread; laneWidth m), and
-- whether a lane change was in it.
function M.actionLua(level, a, speed, laneWidth)
  if level == 'tactical' then
    local parts = {}
    if a.speed then
      parts[#parts + 1] = string.format("ai.setSpeedMode('limit') ai.setSpeed(%.2f)", a.speed)
    else
      parts[#parts + 1] = "ai.setSpeedMode('legal') ai.setSpeed(nil)"
    end
    local changed = false
    if a.lane ~= 0 and laneWidth then
      -- ai.laneChange's displacement is along the plan's normal: positive to the right (the pull-over code in ai.lua
      -- moves to the kerb with +halfWidth on right-hand roads); lane -1 = one lane to the left
      local dist = math.max(M.LANE_MIN_M, (speed or 0) * M.LANE_TIME_S)
      parts[#parts + 1] = string.format('ai.laneChange(nil, %.1f, %.2f)', dist, a.lane * laneWidth)
      changed = true
    end
    return table.concat(parts, ' '), changed
  elseif level == 'raw' then
    return string.format("input.event('steering', %.4f, 'FILTER_AI', nil, nil, nil, 'ai') "
      .. "input.event('throttle', %.4f, 'FILTER_AI', nil, nil, nil, 'ai') "
      .. "input.event('brake', %.4f, 'FILTER_AI', nil, nil, nil, 'ai') "
      .. "input.event('parkingbrake', 0, 'FILTER_AI', nil, nil, nil, 'ai')", a.steer, a.throttle, a.brake), false
  end
  return nil, false
end

-- Pure: the world point a target action { ahead, left } means for a car at (x, y, z) heading (fx, fy).
function M.targetPoint(a, x, y, z, fx, fy)
  return x + fx * a.ahead - fy * a.left, y + fy * a.ahead + fx * a.left, z
end

-- the vehicle Lua that reports a car's own inputs back (one frame late: the labels for learning from the stock AI or you)
local INPUTS_LUA = "local e = electrics.values obj:queueGameEngineLua(string.format("
  .. "'if beamngEarth_aibrain then beamngEarth_aibrain.inputs(%d, %.3f, %.3f, %.3f) end', objectId, "
  .. "e.steering_input or 0, e.throttle_input or 0, e.brake_input or 0))"

function M.inputs(id, s, t, b)
  local c = cars[id]
  if c then c.inputs = { steer = s, throttle = t, brake = b } end
end

local function playerId() return be and be.getPlayerVehicleID and be:getPlayerVehicleID(0) or -1 end

-- the ids the brain drives: the traffic cars (gameplay_traffic), plus the player's car with opts.player
local function chosenIds()
  local ids = {}
  if opts.ids then
    for _, id in ipairs(opts.ids) do ids[#ids + 1] = id end
    return ids
  end
  local gt = rawget(_G, 'gameplay_traffic')
  local me = playerId()
  if gt and gt.getTrafficData then
    for id in pairs(gt.getTrafficData() or {}) do
      if id ~= me then ids[#ids + 1] = id end
    end
  end
  if opts.player and me >= 0 then ids[#ids + 1] = me end
  table.sort(ids)
  if opts.max then for i = #ids, opts.max + 1, -1 do ids[i] = nil end end
  return ids
end

local function snapshot(o, id, me)
  local p, v, f = o:getPosition(), o:getVelocity(), o:getDirectionVector()
  local fl = math.sqrt(f.x * f.x + f.y * f.y)
  local fx, fy = fl > 1e-6 and f.x / fl or 1, fl > 1e-6 and f.y / fl or 0
  local len = 4.5
  local okL, l = pcall(function() return o:getInitialLength() end)
  if okL and tonumber(l) then len = l end
  return { id = id, x = p.x, y = p.y, z = p.z, vx = v.x, vy = v.y, fx = fx, fy = fy, len = len, player = id == me }
end

-- the road under a car from the game's map (navgraph.lua put our graph there): nil off the graph
local function roadAt(s)
  if not (map and map.getMap) then return nil end
  local pos = vec3(s.x, s.y, s.z)
  local n1, n2
  if map.findBestRoad then n1, n2 = map.findBestRoad(pos, vec3(s.fx, s.fy, 0)) end
  if not n1 and map.findClosestRoad then n1, n2 = map.findClosestRoad(pos) end
  if not (n1 and n2) then return nil end
  -- (navgraph's copy has every edge on both its nodes; the game's map keeps each on one of them only)
  local okN, nav = pcall(require, 'beamngEarth/navgraph')
  local nodes = okN and nav.graphNodes and nav.graphNodes() or map.getMap().nodes
  local a, b = nodes[n1], nodes[n2]
  local e = a and a.links and a.links[n2] or b and b.links and b.links[n1]
  if not (a and b and e) then return nil end
  local inN, outN = nodes[e.inNode or n1] or a, nodes[e.outNode or n2] or b
  local ex, ey = outN.pos.x - inN.pos.x, outN.pos.y - inN.pos.y
  local el = math.sqrt(ex * ex + ey * ey)
  if el < 1e-6 then return nil end
  ex, ey = ex / el, ey / el
  local right = (s.x - inN.pos.x) * ey - (s.y - inN.pos.y) * ex     -- offset to the right of in -> out
  local along = (s.fx * ex + s.fy * ey) >= 0
  local _, hl = M.toCar(ex, ey, s.fx, s.fy)
  return { speedLimit = e.speedLimit, lanes = e.lanes, halfWidth = math.max(inN.radius or 0, outN.radius or 0),
           right = right, along = along, oneWay = e.oneWay, heading = along and hl or -hl }
end

local function send(tbl)
  if not sock then return end
  local ok, data = pcall(jsonEncode, tbl)
  if not ok then stats.err = 'encode: ' .. tostring(data) return end
  local okS, err = sock:send(data)
  if not okS and err then stats.err = 'send: ' .. tostring(err) end
end

local function setLevel(o, id, level)
  local c = cars[id]
  if c and c.level == level then return end
  if level == 'target' then
    -- (setSlotTrafficTarget switches to 'slotTraffic' itself on the first target)
  elseif level == 'raw' then
    o:queueLuaCommand("ai.setMode('disabled')")
  end
  cars[id] = c or {}
  cars[id].level = level
  cars[id].took = true
end

local function release(id)
  local o = getObjectByID(id)
  local c = cars[id]
  if o and c and c.took then
    local wasPlayer = id == playerId() and not (opts and opts.ids)
    if c.level == 'tactical' then
      o:queueLuaCommand("ai.setSpeedMode('legal') ai.setSpeed(nil)")
    elseif c.level == 'raw' or c.level == 'target' then
      o:queueLuaCommand("input.event('throttle', 0, 'FILTER_AI', nil, nil, nil, 'ai') input.event('brake', 0, 'FILTER_AI', nil, nil, nil, 'ai') "
        .. "input.event('steering', 0, 'FILTER_AI', nil, nil, nil, 'ai')")
      o:queueLuaCommand(wasPlayer and "ai.setMode('disabled')" or "ai.setMode('traffic')")
    end
  end
  cars[id] = nil
end

local function receive(now)
  for _ = 1, 16 do
    local data = sock and sock:receive()
    if not data then return end
    stats.recv = stats.recv + 1
    local ok, msg = pcall(jsonDecode, data)
    if not ok or type(msg) ~= 'table' or type(msg.actions) ~= 'table' then
      stats.bad = stats.bad + 1
    else
      local t0 = sentAt[msg.seq]
      if t0 then stats.lastRttMs = (now - t0) * 1000; sentAt[msg.seq] = nil end
      for key, a in pairs(msg.actions) do
        local id = tonumber(key)
        local c = id and cars[id]
        if c then
          local act, why = M.clampAction(c.level, a)
          if act then c.act, c.actAt, c.new = act, now, true else stats.bad = stats.bad + 1; stats.err = why end
        end
      end
    end
  end
end

-- Pure: what to do with car c's action at `now`: 'apply' (a new one), 'expire' (it went stale: once) or nil.
function M.due(c, now)
  if not c.act then return nil end
  if c.new then return 'apply' end
  if not c.expired and now - c.actAt > M.STALE_S then return 'expire' end
  return nil
end

-- Each action is applied once, when it arrives (the vehicle keeps a speed, a target or a pedal until told otherwise);
-- a car whose brain went quiet for STALE_S goes back to safe: tactical to the legal speed, raw to full brake, target
-- stops by itself (slotTraffic's own 0.5 s timeout).
local function apply(now)
  for id, c in pairs(cars) do
    local o = getObjectByID(id)
    local what = o and c.level ~= 'observe' and M.due(c, now)
    if what == 'expire' then
      c.expired = true
      stats.stale = stats.stale + 1
      if c.level == 'raw' then
        o:queueLuaCommand(M.actionLua('raw', { steer = 0, throttle = 0, brake = 1 }))
      elseif c.level == 'tactical' then
        o:queueLuaCommand("ai.setSpeedMode('legal') ai.setSpeed(nil)")
      end
    elseif what == 'apply' then
      c.new, c.expired = false, false
      stats.applied = stats.applied + 1
      if c.level == 'target' then
        local s = c.snap
        if s then
          local x, y, z = M.targetPoint(c.act, s.x, s.y, s.z, s.fx, s.fy)
          o:queueLuaCommand(string.format('ai.setSlotTrafficTarget(%.2f, %.2f, %.2f, %.2f)', x, y, z, c.act.speed))
        end
      else
        local a = c.act
        if c.level == 'tactical' and a.lane ~= 0 and now - (c.laneAt or -math.huge) < M.LANE_COOLDOWN_S then
          a = { lane = 0, speed = a.speed }
        end
        local lua, changed = M.actionLua(c.level, a, c.snap and c.snap.speed, c.laneWidth)
        if lua then o:queueLuaCommand(lua) end
        if changed then c.laneAt = now end
      end
    end
  end
end

local function tick(now)
  local me = playerId()
  local ids = chosenIds()
  local want = {}
  for _, id in ipairs(ids) do want[id] = true end
  for id in pairs(cars) do if not want[id] then release(id) end end
  -- every vehicle about (the brain's cars see all of them, parked ones too)
  local all = {}
  for i = 0, (be:getObjectCount() or 0) - 1 do
    local o = be:getObject(i)
    if o and o.getActive and o:getActive() then all[#all + 1] = snapshot(o, o:getId(), me) end
  end
  local byId = {}
  for _, s in ipairs(all) do byId[s.id] = s end
  local out = {}
  for _, id in ipairs(ids) do
    local o, s = getObjectByID(id), byId[id]
    if o and s then
      setLevel(o, id, opts.level)
      local c = cars[id]
      s.speed = math.sqrt(s.vx * s.vx + s.vy * s.vy)
      c.snap = s
      local okR, road = pcall(roadAt, s)
      road = okR and road or nil
      local obs = M.observe(s, all, road, c.inputs)
      c.laneWidth = obs.road and obs.road.laneWidth or nil
      out[#out + 1] = obs
      o:queueLuaCommand(INPUTS_LUA)
    end
  end
  seq = seq + 1
  sentAt[seq] = now
  sentAt[seq - 50] = nil
  stats.sent = stats.sent + 1
  -- (navgraph's rules: `rightHandDrive = true` means driving on the left)
  local okN, nav = pcall(require, 'beamngEarth/navgraph')
  local leftSide = okN and nav.rules and nav.rules.rightHandDrive or false
  send({ seq = seq, t = now, level = opts.level, driveOnLeft = leftSide and true or false, cars = out })
end

-- start({ level = 'tactical' | 'target' | 'raw' | 'observe', player = false, max = n, ids = { ... }, port = 47620 })
function M.start(o)
  o = o or {}
  if not M.LEVELS[o.level or 'tactical'] then return 'unknown level ' .. tostring(o.level) end
  if running then M.stop() end
  opts = { level = o.level or 'tactical', player = o.player and true or false, max = tonumber(o.max), ids = o.ids }
  local okS, socket = pcall(require, 'socket')
  if not okS then return 'no luasocket: ' .. tostring(socket) end
  sock = socket.udp()
  sock:settimeout(0)
  local ok, err = sock:setpeername(o.host or M.HOST, tonumber(o.port) or M.PORT)
  if not ok then sock = nil return 'udp: ' .. tostring(err) end
  running, seq, lastTick, cars = true, 0, -math.huge, {}
  stats = { sent = 0, recv = 0, applied = 0, stale = 0, bad = 0 }
  log('I', 'beamngEarth', 'aibrain: started, level ' .. opts.level .. ', brain at udp ' .. (o.host or M.HOST) .. ':' .. (tonumber(o.port) or M.PORT))
  return 'ok'
end

function M.stop()
  if not running then return 'not running' end
  running = false
  for id in pairs(cars) do pcall(release, id) end
  cars = {}
  if sock then pcall(sock.close, sock) end
  sock = nil
  log('I', 'beamngEarth', 'aibrain: stopped')
  return 'ok'
end

-- Is car id driven by the brain now (the merge assist leaves those be)?
function M.controls(id) return running and cars[id] ~= nil end

function M.status()
  local n = 0
  for _ in pairs(cars) do n = n + 1 end
  return { running = running, level = opts and opts.level, cars = n, sent = stats.sent, recv = stats.recv,
           applied = stats.applied, stale = stats.stale, bad = stats.bad, rttMs = stats.lastRttMs, err = stats.err }
end

function M.onUpdate()
  if not running then return end
  local now = os.clock()
  local ok, err = pcall(receive, now)
  if not ok then stats.err = 'receive: ' .. tostring(err) end
  ok, err = pcall(apply, now)
  if not ok then stats.err = 'apply: ' .. tostring(err) end
  if now - lastTick >= M.TICK_S then
    lastTick = now
    ok, err = pcall(tick, now)
    if not ok then stats.err = 'tick: ' .. tostring(err) end
  end
end

-- the level ended or the mod is unloading: every car back to the stock AI
function M.onClientEndMission() if running then M.stop() end end
function M.onExtensionUnloaded() if running then M.stop() end end

return M
