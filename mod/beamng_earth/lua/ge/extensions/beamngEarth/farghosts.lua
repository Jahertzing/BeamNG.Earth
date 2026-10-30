-- Far ghosts: the traffic population's ghosts (population.lua) drawn as static car models out to FAR_M, so a car is seen
-- coming from the horizon and never turns real out of thin air in view (the streaming research's item 8, 2026-10-29;
-- GTA's "ambient population" has the same split). Each ghost not driven by a real car within FAR_M (the nearest MAX)
-- gets a pooled TSStatic of a stock traffic car (tools/build_static_cars.py, trees.lua's car_* catalog: three detail
-- levels, a 16 px hull far off), moved every frame along its edge at its speed (the ghosts themselves move every
-- population.TICK_S). A ghost made real loses its model in the frame its car is put on its spot (the car then appears
-- where the model stood, so population.place does not fade it in); one made a ghost again gets one where its car was.
-- Off with the population, or with no car models built on this PC (no cars.json).
local M = {}

M.FAR_M = 1300
M.MAX = 64
M.ONE_COLOUR = false     -- every paint: a ghost wears its car's (population.lua); loaded one by one, PRELOAD_S apart
M.PRELOAD_S = 0.25       -- a shape's first load is a hitch: all of them, spread out, once the population starts
M.DYNAMIC = true         -- (farfield.lua: a static object added redraws the cached sun shadow map; dynamic ones do not)

local pool = {}          -- shape -> { free = { obj... } }
local shown = {}         -- ghost -> { obj, shape }
local shapes = nil       -- { { path, height } } the car shapes
local serial = 0
local enabled = false
local preloaded, lastPreload = 0, -math.huge
local stats = { shown = 0, created = 0 }

-- Pure: the car shapes from trees' catalog (car_* groups: { {path without .dae, height}, ... }), one per model when
-- oneColour.
function M.shapesFrom(catalog, oneColour)
  local out = {}
  local groups = {}
  for g in pairs(catalog or {}) do if type(g) == 'string' and g:sub(1, 4) == 'car_' then groups[#groups + 1] = g end end
  table.sort(groups)
  for _, g in ipairs(groups) do
    local list = catalog[g]
    for i, v in ipairs(list) do
      if type(v) == 'table' and v[1] then out[#out + 1] = { path = v[1] .. '.dae', height = v[2] } end
      if oneColour and i >= 1 then break end
    end
  end
  return out
end

-- Pure: the shape a ghost wears: one of shapes by its id (stable for the ghost).
function M.shapeFor(id, list)
  if not list or #list == 0 then return nil end
  return list[(math.floor(id or 0) % #list) + 1]
end

-- Pure: the shape in shapes (shapesFrom's) of a ghost's model and colour: its paint, else another paint of its model, else
-- by its id. A shape's name is bne_car_<model>_<colour>.
function M.shapeOf(g, list)
  if g.model then
    local other = nil
    for _, sh in ipairs(list) do
      local m, c = sh.path:match('bne_car_(%w+)_(%w+)%.dae$')
      if m == g.model then
        if c == g.colour then return sh end
        other = other or sh
      end
    end
    if other then return other end
  end
  return M.shapeFor(g.id, list)
end

-- Pure: the ghosts to draw: not real, within FAR_M of (px, py), the MAX nearest; poseOf(g) -> x, y (nil: none).
-- Returns { { g = ghost, d2 } } nearest first.
function M.choose(ghosts, poseOf, px, py)
  local out = {}
  for _, g in ipairs(ghosts) do
    if not g.real then
      local x, y = poseOf(g)
      if x then
        local d2 = (x - px) ^ 2 + (y - py) ^ 2
        if d2 <= M.FAR_M * M.FAR_M then out[#out + 1] = { g = g, d2 = d2 } end
      end
    end
  end
  table.sort(out, function(a, b) return a.d2 < b.d2 end)
  for i = #out, M.MAX + 1, -1 do out[i] = nil end
  return out
end

local function newObject(shape)
  local o = createObject('TSStatic')
  if not o then return nil end
  o:setField('shapeName', 0, shape.path)
  o:setField('collisionType', 0, 'None')
  o:setField('decalType', 0, 'None')
  if M.DYNAMIC then o:setField('dynamic', 0, 'true') end
  o.canSave = false
  o.hidden = true
  serial = serial + 1
  o:registerObject('bngEarthFarGhost_' .. serial)
  if scenetree and scenetree.MissionGroup then scenetree.MissionGroup:addObject(o) end
  stats.created = stats.created + 1
  return o
end

local function take(shape)
  local p = pool[shape.path]
  if not p then p = { free = {} } pool[shape.path] = p end
  local o = table.remove(p.free)
  if o and not scenetree.findObjectById(o:getID()) then o = nil end
  return o or newObject(shape)
end

local function give(shape, o)
  if not o then return end
  o.hidden = true
  local p = pool[shape.path]
  if not p then p = { free = {} } pool[shape.path] = p end
  p.free[#p.free + 1] = o
end

-- Does ghost g wear a model now (population.place: a car put there needs no fade-in)?
function M.showing(g) return shown[g] ~= nil end

-- Pure: setPosRot's quaternion turning a model's +x (the car models' front) to unit (dx, dy): the yaw counter-clockwise
-- from +x, conjugated (geodesy beamngQuat; gasstations.lua yawQuat).
function M.yawQuat(dx, dy)
  local h = math.atan2(dy, dx) / 2
  return 0, 0, -math.sin(h), math.cos(h)
end

local function place(o, x, y, z, dx, dy)
  local qx, qy, qz, qw = M.yawQuat(dx, dy)
  o:setPosRot(x, y, z, qx, qy, qz, qw)
end

-- Every frame while the population runs (navgraph.update): the far ghosts' models moved, shown and hidden.
-- pop: population.lua; now: os.clock(); px, py: the player.
function M.update(pop, now, px, py)
  if not enabled then return end
  if not shapes then
    local okT, trees = pcall(require, 'beamngEarth/trees')
    shapes = okT and M.shapesFrom(trees.CATALOG, M.ONE_COLOUR) or {}
    if #shapes == 0 then enabled = false return end
  end
  if M.PRELOAD_S and preloaded < #shapes and now - lastPreload >= M.PRELOAD_S then
    lastPreload = now
    preloaded = preloaded + 1
    local sh = shapes[preloaded]
    local p = pool[sh.path]
    if not (p and #p.free > 0) then give(sh, take(sh)) end
  end
  local P = pop.state
  local nodes = P.graph and P.graph.nodes
  if not nodes then return end
  local ahead = math.max(0, math.min(1, now - (P.lastTick or now)))
  local function poseOf(g)
    local s = g.link and math.min(g.link.len, g.s + (g.v or 0) * ahead)
    return pop.pose(nodes, g.from, g.link, s or g.s, g.lane, P.left)
  end
  local want = M.choose(P.ghosts, poseOf, px, py)
  local keep = {}
  for _, w in ipairs(want) do keep[w.g] = true end
  for g, rec in pairs(shown) do
    if not keep[g] then
      give(rec.shape, rec.obj)
      shown[g] = nil
    end
  end
  local n = 0
  for _, w in ipairs(want) do
    local g = w.g
    local rec = shown[g]
    if not rec then
      local shape = M.shapeOf(g, shapes)
      local o = shape and take(shape)
      if o then rec = { obj = o, shape = shape } shown[g] = rec end
    end
    if rec then
      local x, y, z, dx, dy = poseOf(g)
      if x then
        pcall(place, rec.obj, x, y, z, dx, dy)
        rec.obj.hidden = false
        n = n + 1
      end
    end
  end
  stats.shown = n
end

function M.setEnabled(on)
  enabled = on and true or false
  if not enabled then M.clear() end
end

-- Every model hidden and back in the pool (the population off, a teleport, the level's end).
function M.clear()
  for g, rec in pairs(shown) do give(rec.shape, rec.obj) shown[g] = nil end
  stats.shown = 0
end

-- The level ended: the objects went with it.
function M.reset()
  pool, shown, shapes = {}, {}, nil
  enabled = false
  preloaded, lastPreload = 0, -math.huge
end

function M.status()
  local free = 0
  for _, p in pairs(pool) do free = free + #p.free end
  return { enabled = enabled, shown = stats.shown, created = stats.created, pooled = free }
end

return M
