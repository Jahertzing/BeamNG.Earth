-- Grass and low shrubs near the car (docs/ROADMAP.md v1.5; server --surface on). GroundCover renders on
-- the render-disabled physics block and honours its layer map (docs/research/2026-10-11-engine-probes.md
-- §4), so binding stock GroundCover definitions to the surface layers (server/surface.py: grassland /
-- forest floor -> bngEarthGroundGrass, shrubland -> bngEarthGroundDusty) puts grass where the land
-- cover says grass, nothing on asphalt, bare ground, rock, snow or mud.
--
-- No flicker at block moves (in-game and binary analysis 2026-10-11, docs/ARCHITECTURE.md "Ground
-- surfaces"): every import / move of the block makes the engine drop every GroundCover cell on it
-- (no way around that from Lua), and the cells come back under a per-frame generation budget.
-- core calls M.afterBlockChange() in the same tick as each block change: flushGroundCoverGrids()
-- lifts the budget for that frame, so all the cells are regenerated before the frame is drawn. That
-- needs CPU placement (useNewPlacementGpu 0): GPU-placed cells only draw one frame later (a blank
-- frame), and GPU placement reads a layer copy the hidden block never refreshes. The price is one
-- slower frame per block move, which grows with the element count (the shares below). With the
-- optional tools/gcpatch preload library (Linux; gcpatchwin.py on Windows) the engine keeps the grass across
-- block moves and that frame goes away; where the world really moves (teleport, rebase) core calls
-- M.rebuild(), which works with or without it.
--
-- The definitions are stock ones, read at runtime from official levels' own files (nothing is copied
-- into the mod; Polish Roads is not used, CREDITS.md): their materials are loaded first (a shape whose
-- material is missing when it is first drawn stays "no material"), then each GroundCover is recreated
-- with its plant types' layer pointed at ours and its element count scaled by `share`. Green Hirochi
-- Raceway grass with a little of West Coast USA's dry grass on grassland, West Coast scrub with sparse
-- green grass between on dusty ground. Generic: one rule for every place; per-biome / per-climate /
-- per-season variants are a later step. Console: beamngEarth_groundcover.setEnabled(bool), .setReach('far'), .status()

local M = {}

-- tools/gcpatch acts only while this file exists (its thread looks every 250 ms): created when our level starts,
-- removed when it ends, so other levels keep the stock engine (the user, 2026-10-20: the mod effectively off there).
M.PATCH_FLAG = '/temp/beamngearth_gcpatch_on'   -- lower case: the game's file system writes new names so
function M.enginePatch(on)
  local ok, err = pcall(function()
    if on then
      writeFile(M.PATCH_FLAG, '1')
    elseif FS and FS:fileExists(M.PATCH_FLAG) then
      FS:removeFile(M.PATCH_FLAG)
    end
  end)
  if not ok then log('W', 'beamngEarth', 'grass patch flag: ' .. tostring(err)) end
end

local HIROCHI, WCUSA = '/levels/hirochi_raceway/', '/levels/west_coast_usa/'
M.SOURCES = {
  hirochi = { materials = HIROCHI .. 'art/shapes/groundcover/main.materials.json',
              items = HIROCHI .. 'main/MissionGroup/vegetation/terrain_grass/items.level.json' },
  wcusa = { materials = WCUSA .. 'art/groundcover/main.materials.json',
            items = WCUSA .. 'main/MissionGroup/vegetation/items.level.json' },
}
-- stock GroundCover (source, src name) -> our layer, element share; `name` (default src) names our
-- object, for a stock definition used on two layers; `fields` override stock fields; `reach` gives
-- the object's { radius, dissolveRadius } per grass distance (M.REACH, the settings page). The shares
-- keep the block-move rebuild near +15 ms at 'near' (in-game at Serengeti grassland 2026-10-11:
-- stock-ish shares +30 ms; the dense near grass costs most, the long grass was kept close, radius 70 m
-- instead of 120). 'near' is that tuning; 'normal' gives the stock maps' distances (Hirochi's long
-- grass to 120 m), 'far' more. The user (2026-10-28): grass only showed up really close to the camera,
-- then "200m is NOTHING": 'max' reaches what the physics block allows. GroundCover places only on the
-- block's layer map, and the 512² block (1 km) moves on a 128 m grid with the player up to its follow
-- reach (260 m, earthd FOLLOW_RULES) from its centre, so ~250 m ahead is always on it; past its edge
-- the grass just stops, inside the dissolve. Beyond that the ground's own grass layer takes over
-- (satmat.lua GRASS_LAYER).
local LONG = { near = { 70, 45 }, normal = { 120, 80 }, far = { 200, 150 }, max = { 300, 220 } }
local SHORT = { near = { 50, 30 }, normal = { 60, 42 }, far = { 80, 60 }, max = { 100, 75 } }
local DRY = { near = { 60, 30 }, normal = { 90, 60 }, far = { 150, 110 }, max = { 220, 160 } }
local SHRUB = { near = { 100 }, normal = { 150, 110 }, far = { 250, 190 }, max = { 300, 230 } }
M.DEFS = {
  { source = 'hirochi', src = 'small_grass_close', layer = 'bngEarthGroundGrass', share = 0.25, reach = SHORT },
  { source = 'hirochi', src = 'long_grass_distant', layer = 'bngEarthGroundGrass', share = 0.17, reach = LONG },
  { source = 'wcusa', src = 'grass_dry_1', layer = 'bngEarthGroundGrass', share = 0.05, reach = DRY },
  -- mown town yards: a little short grass, nothing long (server/surface.py LAWN)
  { source = 'hirochi', src = 'small_grass_close', name = 'small_grass_close_lawn', layer = 'bngEarthGroundLawn', share = 0.1,
    reach = SHORT },
  { source = 'wcusa', src = 'shrubs_near', layer = 'bngEarthGroundDusty', share = 0.15, reach = SHRUB },
  -- sparse grass between the shrubs (user 2026-10-11: a hard line between grassland and shrubland)
  { source = 'hirochi', src = 'small_grass_close', name = 'small_grass_close_scrub', layer = 'bngEarthGroundDusty', share = 0.08,
    reach = SHORT },
  { source = 'hirochi', src = 'long_grass_distant', name = 'long_grass_distant_scrub', layer = 'bngEarthGroundDusty', share = 0.05,
    reach = LONG },
}
M.PREFIX = 'bngEarthGC_'
-- set on every object after the stock fields (see the header)
M.PLACEMENT = { { 'useNewPlacement', '1' }, { 'useNewPlacementGpu', '0' } }
M.enabled = true
-- element count factor on top of the shares (the settings page: low / normal / high); the block-move
-- rebuild frame grows with it where tools/gcpatch is not loaded
M.DENSITY = { low = 0.5, normal = 1.0, high = 2.0 }
M.density = 1.0
-- grass distance (the settings page): which `reach` of each definition. maxElements is per object, not
-- per area, so a larger radius alone would thin the grass out; the elements grow with it, by
-- (radius / near radius)^REACH_EXP: 2 would keep the near density everywhere, a little less is enough
-- where far blades overlap on screen. More elements make the block-move rebuild frame longer where
-- tools/gcpatch is not loaded (it is Linux only).
M.REACH = { near = true, normal = true, far = true, max = true }
M.REACH_EXP = 1.5
M.reach = 'normal'

local created = {}        -- object names we made
local layered = false     -- a block with surface layers was loaded this level (objects can exist)
local defsCache = {}      -- source -> stock definitions by name (read once per level)
local loadedMaterials = {} -- source -> true
local lastError = nil

local SKIP = { name = true, class = true, persistentId = true, __parent = true, position = true, Types = true,
  internalName = true, canSave = true }

local function fieldValue(v)
  if type(v) == 'table' then
    local parts = {}
    for i, x in ipairs(v) do parts[i] = tostring(x) end
    return table.concat(parts, ' ')
  end
  if type(v) == 'boolean' then return v and '1' or '0' end
  return tostring(v)
end

-- The stock definitions of one source by name, from its items file (one JSON object per line).
local function readDefs(source)
  local file = M.SOURCES[source].items
  local text = readFile and readFile(file)
  if type(text) ~= 'string' then return nil, 'cannot read ' .. file end
  local want = {}
  for _, d in ipairs(M.DEFS) do if d.source == source then want[d.src] = true end end
  local out = {}
  for line in text:gmatch('[^\r\n]+') do
    if line:find('"GroundCover"', 1, true) then
      local ok, d = pcall(jsonDecode, line)
      if ok and type(d) == 'table' and want[d.name] then out[d.name] = d end
    end
  end
  return out
end

-- Pure: the setField calls (name, index, value) that recreate a stock definition on `layer`, its
-- element count scaled by `share`.
function M.fieldsFor(def, layer, share)
  local calls = {}
  for k, v in pairs(def) do
    if not SKIP[k] and v ~= jsonNull then
      if k == 'maxElements' then v = math.max(1, math.floor((tonumber(v) or 0) * share + 0.5)) end
      calls[#calls + 1] = { k, 0, fieldValue(v) }
    end
  end
  if type(def.Types) == 'table' then
    for i, t in ipairs(def.Types) do
      for k, v in pairs(t) do
        if v ~= nil and v ~= jsonNull then
          if k == 'layer' then v = (v ~= '' and layer or '') end
          calls[#calls + 1] = { k, i - 1, fieldValue(v) }
        end
      end
    end
  end
  table.sort(calls, function(a, b) if a[1] ~= b[1] then return a[1] < b[1] end return a[2] < b[2] end)
  return calls
end

-- Pure: the { field = value } overrides and the element factor of definition `d` at grass distance
-- `reach` ('near' / 'normal' / 'far' / 'max'): its `fields`, then its reach's radius / dissolveRadius.
function M.reachFor(d, reach)
  local out = {}
  for k, v in pairs(d.fields or {}) do out[k] = v end
  local r = d.reach and (d.reach[reach] or d.reach.normal)
  local factor = 1
  if r then
    out.radius = r[1]
    if r[2] then out.dissolveRadius = r[2] end
    factor = (r[1] / d.reach.near[1]) ^ M.REACH_EXP
  end
  return out, factor
end

local function removeAll()
  for _, name in ipairs(created) do
    local o = scenetree.findObject(name)
    if o then pcall(function() o:delete() end) end
  end
  created = {}
end

-- Create the GroundCovers (once per level; the surface layers exist). Returns true if made.
function M.ensure()
  layered = true
  if not M.enabled or #created > 0 then return #created > 0 end
  if not scenetree.MissionGroup then return false end
  local ok, err = pcall(function()
    local defs = defsCache
    for _, d in ipairs(M.DEFS) do
      if not defs[d.source] then
        if not loadedMaterials[d.source] then
          loadJsonMaterialsFile(M.SOURCES[d.source].materials)
          loadedMaterials[d.source] = true
        end
        local e
        defs[d.source], e = readDefs(d.source)
        if not defs[d.source] then error(e) end
      end
      local def = defs[d.source][d.src]
      if def then
        local name = M.PREFIX .. (d.name or d.src)
        -- one left by an earlier module instance (a GE Lua reload keeps the level's objects) may
        -- have older fields, e.g. GPU placement: made again from the current definition
        local old = scenetree.findObject(name)
        if old then old:delete() end
        do
          local gc = createObject('GroundCover')
          local fields, factor = M.reachFor(d, M.reach)
          for _, c in ipairs(M.fieldsFor(def, d.layer, d.share * M.density * factor)) do gc:setField(c[1], c[2], c[3]) end
          for k, v in pairs(fields) do gc:setField(k, 0, tostring(v)) end
          for _, f in ipairs(M.PLACEMENT) do gc:setField(f[1], 0, f[2]) end
          gc.canSave = false
          gc:registerObject(name)
          scenetree.MissionGroup:addObject(gc)
          pcall(function() gc:postApply() end) -- the first layered GroundCover once showed nothing until this
        end
        created[#created + 1] = name
      else
        log('W', 'beamngEarth', 'ground cover: no ' .. d.src .. ' in ' .. d.source)
      end
    end
  end)
  if not ok then
    lastError = tostring(err)
    log('W', 'beamngEarth', 'ground cover: ' .. lastError)
    removeAll()
  end
  return #created > 0
end

-- The physics block was just imported or moved (core, same tick): regenerate all the grass before
-- this frame is drawn. Returns true if it asked for that.
function M.afterBlockChange()
  if #created == 0 or not flushGroundCoverGrids then return false end
  flushGroundCoverGrids()
  return true
end

-- On / off (the settings page): off removes the objects; on makes them at once when the block has
-- surface layers (else with the next layered import).
function M.setEnabled(on)
  M.enabled = on and true or false
  if not M.enabled then
    removeAll()
  elseif layered and #created == 0 and M.ensure() then
    M.afterBlockChange()
  end
  return M.enabled
end

-- Density (the settings page): 'low' / 'normal' / 'high' or a factor; existing objects are made
-- again with it.
function M.setDensity(v)
  local f = M.DENSITY[v] or tonumber(v)
  if not f or f <= 0 then return false end
  if f == M.density then return true end
  M.density = f
  if #created > 0 then M.rebuild() end
  return true
end

-- Grass distance (the settings page): 'near' / 'normal' / 'far' / 'max'; existing objects are made again
-- with it.
function M.setReach(v)
  if not M.REACH[v] then return false end
  if v == M.reach then return true end
  M.reach = v
  if #created > 0 then M.rebuild() end
  return true
end

-- The level ended (its objects are gone with it, the stock materials loaded for it too: a level started again in
-- the same game session found no m_grass_* / wca_groundcover and drew no grass, 2026-10-19).
function M.reset()
  created = {}
  defsCache = {}
  loadedMaterials = {}
  layered = false
end

-- A teleport or a floating-origin rebase moved the world under the grass: make the objects again
-- (their cells then start from nothing whatever the engine kept) and generate them this frame.
function M.rebuild()
  if #created == 0 then return false end
  removeAll()
  M.ensure()
  return M.afterBlockChange()
end

function M.status()
  return { enabled = M.enabled, density = M.density, reach = M.reach, objects = #created, materialsLoaded = next(loadedMaterials) ~= nil, error = lastError }
end

return M
