-- Merge assist for the traffic cars on on-ramps (navgraph.lua calls update from its guard). The stock ai.lua has no
-- merge logic: a ramp joins a motorway at a small angle, so neither car gives way (it yields only for 45-135 degree turns
-- or a road of lower drivability, and a ramp given a lower drivability made its cars stop dead at the gore), and a car
-- only slows for cars already in its plan ahead, never for one coming up alongside: ramp cars cut into the motorway's
-- traffic (the AI driving guide, 2026-10-28). Here a car on a ramp within LOOK_M of the point it joins the motorway
-- gets a speed cap (ai.setSpeedMode('limit') + ai.setSpeed) so it arrives at that point GAP_S clear of every vehicle on
-- the motorway's lanes there, ahead of one or behind it, whichever is nearer its own speed; the cap is lifted once it
-- has merged. The graph's ramp flag is navgraph.py's edge[9] (Overture's subclass "link").
local M = {}

M.LOOK_M = 250          -- a ramp car this near its merge point is assisted
M.UP_M = 300            -- the motorway's lanes this far before the merge point are watched
M.DOWN_M = 40           -- ... and this far past it (a slow car just merged ahead is a conflict too)
M.GAP_S = 1.6           -- the time gap kept at the merge point
M.V_MIN = 8             -- m/s: the slowest cap (a ramp car is never stopped by us; the stock AI still stops for a car ahead)
M.V_UP = 1.15           -- the cap may be this much over the ramp's speed to go ahead of a car
M.ALIGN = 0.85          -- cos: the motorway in and out of the merge point (and a car on it) this straight
M.SIDE_M = 1.5          -- a vehicle this far outside the motorway's half width is not on it

local function key(a, b) return a .. '\0' .. b end

-- Pure: the directed one-way edge a -> b of node table `nodes` ({name = {pos, links = {other = edge}}}), or nil.
local function oneWayOut(nodes, a, b)
  local n = nodes[a]
  local e = n and n.links[b]
  if e and e.oneWay and e.inNode == a and e.outNode == b then return e end
  return nil
end

local function dist2d(p, q) return math.sqrt((p.x - q.x) ^ 2 + (p.y - q.y) ^ 2) end

local function unit(p, q)
  local dx, dy = q.x - p.x, q.y - p.y
  local l = math.sqrt(dx * dx + dy * dy)
  if l < 1e-6 then return 0, 0 end
  return dx / l, dy / l
end

-- Pure: the merge ahead of a car on the one-way ramp edge a -> b at position p: { j = node, d = m along the ramp to it,
-- up = { pos... } the motorway's centreline from j back UP_M, down = { pos... } from j on DOWN_M, halfWidth } or nil
-- (not on a ramp, or the ramp ends somewhere that is not a motorway joining it straight, e.g. an exit ramp's street).
function M.findMerge(nodes, a, b, p, look)
  look = look or M.LOOK_M
  local e = oneWayOut(nodes, a, b)
  if not (e and e.ramp) then return nil end
  local cur, prev = b, a
  local d = dist2d(p, nodes[b].pos)
  for _ = 1, 64 do
    if d > look then return nil end
    local n = nodes[cur]
    -- the motorway out of cur (not a ramp), and the one into it, in line with each other
    local outName, inName
    for o in pairs(n.links) do
      local eo = oneWayOut(nodes, cur, o)
      if eo and not eo.ramp then outName = o end
    end
    if outName then
      local ox, oy = unit(n.pos, nodes[outName].pos)
      for i in pairs(n.links) do
        local ei = oneWayOut(nodes, i, cur)
        if ei and not ei.ramp and i ~= prev then
          local ix, iy = unit(nodes[i].pos, n.pos)
          if ix * ox + iy * oy >= M.ALIGN then inName = i end
        end
      end
      if not inName then return nil end   -- (the ramp ends in a street, or a motorway it does not run along)
      local up, down = { n.pos }, { n.pos }
      local hw = math.max(n.radius or 0, nodes[outName].radius or 0, nodes[inName].radius or 0)
      local u, uPrev, run = inName, cur, 0
      for _ = 1, 64 do
        up[#up + 1] = nodes[u].pos
        run = run + dist2d(nodes[u].pos, nodes[uPrev].pos)
        if run >= M.UP_M then break end
        local nxt
        for k in pairs(nodes[u].links) do
          local ek = oneWayOut(nodes, k, u)
          if ek and not ek.ramp and k ~= uPrev then
            local kx, ky = unit(nodes[k].pos, nodes[u].pos)
            local ux, uy = unit(nodes[u].pos, nodes[uPrev].pos)
            if kx * ux + ky * uy >= M.ALIGN then nxt = k end
          end
        end
        if not nxt then break end
        uPrev, u = u, nxt
      end
      down[2] = nodes[outName].pos
      return { j = cur, d = d, up = up, down = down, halfWidth = hw }
    end
    -- on along the ramp
    local nxt
    for o in pairs(n.links) do
      local eo = oneWayOut(nodes, cur, o)
      if eo and eo.ramp then nxt = o end
    end
    if not nxt then return nil end
    d = d + dist2d(n.pos, nodes[nxt].pos)
    prev, cur = cur, nxt
  end
  return nil
end

-- Pure: a point's distance along polyline line (from its first point) and its distance off it, or nil past its ends.
local function along(line, q)
  local acc = 0
  for i = 1, #line - 1 do
    local p0, p1 = line[i], line[i + 1]
    local sx, sy = p1.x - p0.x, p1.y - p0.y
    local L2 = sx * sx + sy * sy
    if L2 > 1e-9 then
      local t = ((q.x - p0.x) * sx + (q.y - p0.y) * sy) / L2
      if t >= 0 and t <= 1 then
        local cx, cy = p0.x + t * sx, p0.y + t * sy
        return acc + t * math.sqrt(L2), math.sqrt((q.x - cx) ^ 2 + (q.y - cy) ^ 2), sx, sy
      end
      acc = acc + math.sqrt(L2)
    end
  end
  return nil
end

-- Pure: the vehicles on the motorway around merge m: { d = m before the merge point (negative: past it), v = m/s }.
-- vehicles: { id, pos = {x, y}, vel = {x, y} }; the car itself (self id) left out.
function M.mainline(m, vehicles, self)
  local out = {}
  for _, v in ipairs(vehicles) do
    if v.id ~= self then
      local sp = math.sqrt(v.vel.x ^ 2 + v.vel.y ^ 2)
      local s, off, sx, sy = along(m.up, v.pos)
      local d = s
      if not s then
        s, off, sx, sy = along(m.down, v.pos)
        d = s and -s or nil
        if sx then sx, sy = -sx, -sy end   -- (the down line runs with the traffic, the up line against it)
      end
      if d and off <= m.halfWidth + M.SIDE_M then
        -- going the motorway's way: against the up line's direction (it runs back from the merge point)
        local L = math.sqrt(sx * sx + sy * sy)
        local with = sp < 1 or (-(v.vel.x * sx + v.vel.y * sy) / (L * sp)) >= M.ALIGN
        if with then out[#out + 1] = { d = d, v = sp } end
      end
    end
  end
  return out
end

-- Pure: the speed cap (m/s) for a ramp car d m from its merge point at speed v (vRamp: its road's speed), with the
-- motorway's vehicles mains, or nil when it arrives clear as it is. Candidate arrival times: as it is, and GAP_S
-- before or after each vehicle on the motorway; the one clear of all, reachable between V_MIN and vRamp * V_UP, whose
-- speed is nearest the car's own, wins (V_MIN when none is).
function M.choose(d, v, vRamp, mains)
  if #mains == 0 or d <= 1 then return nil end
  local function eta(dd, vv) return dd / math.max(vv, 0.5) end
  local function clear(T)
    for _, c in ipairs(mains) do
      if math.abs(eta(c.d, c.v) - T) < M.GAP_S then return false end
    end
    return true
  end
  local now = eta(d, math.max(v, M.V_MIN))
  if clear(now) then return nil end
  local vMax = math.max(M.V_MIN, (vRamp or v) * M.V_UP)
  local best, bestCost = nil, math.huge
  for _, c in ipairs(mains) do
    local tc = eta(c.d, c.v)
    for _, T in ipairs({ tc + M.GAP_S + 0.01, tc - M.GAP_S - 0.01 }) do
      if T > 0 and clear(T) then
        local vv = d / T
        if vv >= M.V_MIN and vv <= vMax then
          -- (behind is cheaper than ahead at the same speed change: slowing is what drivers do)
          local cost = math.abs(vv - v) * (vv > v and 1.5 or 1)
          if cost < bestCost then best, bestCost = vv, cost end
        end
      end
    end
  end
  return best or M.V_MIN
end

-- GE glue: one pass over the traffic cars (ids), node table nodes (navgraph's last applied), the speed caps set
-- (capped: id -> m/s, kept by the caller). find(pos, dir) -> n1, n2 (map.findBestRoad); skip(id): leave that car be.
-- Returns the number of cars capped now and how many of them were not before.
function M.update(ids, nodes, capped, find, skip)
  if not nodes then return 0, 0 end
  local vehicles = {}
  local cars = {}
  for i = 0, (be:getObjectCount() or 0) - 1 do
    local o = be:getObject(i)
    if o and o.getActive and o:getActive() then
      local p, u = o:getPosition(), o:getVelocity()
      vehicles[#vehicles + 1] = { id = o:getId(), pos = { x = p.x, y = p.y }, vel = { x = u.x, y = u.y } }
      cars[o:getId()] = { o = o, p = p, u = u }
    end
  end
  local n, new = 0, 0
  for _, id in ipairs(ids) do
    local c = cars[id]
    if c and not (skip and skip(id)) then
      local cap = nil
      local sp = math.sqrt(c.u.x ^ 2 + c.u.y ^ 2)
      local dir = sp > 1 and vec3(c.u.x / sp, c.u.y / sp, 0) or c.o:getDirectionVector()
      local okF, a, b = pcall(find, c.p, dir)
      if okF and a and b then
        local e = nodes[a] and nodes[a].links[b]
        if e and e.oneWay and e.outNode == a then a, b = b, a end
        local m = M.findMerge(nodes, a, b, c.p)
        if m then
          local edge = nodes[a].links[b]
          cap = M.choose(m.d, sp, edge and edge.speedLimit, M.mainline(m, vehicles, id))
        end
      end
      if cap then
        n = n + 1
        if not capped[id] then new = new + 1 end
        if not capped[id] or math.abs(capped[id] - cap) > 0.5 then
          c.o:queueLuaCommand(string.format("ai.setSpeedMode('limit') ai.setSpeed(%.2f)", cap))
          capped[id] = cap
        end
      elseif capped[id] then
        c.o:queueLuaCommand("ai.setSpeedMode('legal') ai.setSpeed(nil)")
        capped[id] = nil
      end
    end
  end
  return n, new
end

return M
