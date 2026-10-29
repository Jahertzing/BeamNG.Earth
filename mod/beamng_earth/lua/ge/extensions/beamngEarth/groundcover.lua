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
-- tint: the definition's material takes the ground's colour (M.setTint)
M.DEFS = {
  { source = 'hirochi', src = 'small_grass_close', layer = 'bngEarthGroundGrass', share = 0.25, reach = SHORT, tint = true },
  { source = 'hirochi', src = 'long_grass_distant', layer = 'bngEarthGroundGrass', share = 0.17, reach = LONG, tint = true },
  { source = 'wcusa', src = 'grass_dry_1', layer = 'bngEarthGroundGrass', share = 0.05, reach = DRY, tint = true },
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
-- The edge (the user, 2026-10-28: the grass "just abruptly stops so close by"): past dissolveRadius GroundCover
-- shrinks its elements to nothing at radius. Beyond 'near' the fade starts at FADE_SHARE of the radius at the latest:
-- a long, soft fade (open-world games thin and shrink their grass over a wide band) instead of a line at the end.
M.FADE_SHARE = 0.5

-- The grass takes the ground's colour (the same report): open-world games colour each blade from the terrain under
-- it (Ghost of Tsushima), so where the blades end the ground is the same colour; GroundCover cannot read the ground,
-- so the grass draws with a tinted copy of its stock material, the copy's baseColorFactor set from the server's mean
-- photo colour under the block's grass (surface.grass_tint, the terrain message's grassTint; detail mode).
-- TEX_LINEAR: each material's own texture's mean linear colour over its opaque texels (measured from the stock
-- textures), TINT_STRENGTH how far toward the photo (1: the photo's colour; geometric), TINT_BRIGHT a brightness on
-- top (lit blades vs the photo's ground).
-- No material is ever changed once made (2026-10-28: the first version set the stock materials' factor and reloaded
-- them under live grass, and Jeff's first drive with it ended in an engine crash, a main-thread call through a freed
-- object during the block changes that set the tint). A copy is made per colour step (TINT_STEP, so a drive makes a
-- few dozen at most, re-used, never deleted), fully set up before it is registered, and the GroundCover objects are
-- made again on it at the next block change (as at a teleport: afterBlockChange regenerates them that frame), only
-- when the colour moved more than TINT_MIN_CHANGE.
-- On by default since 2026-10-29: checked in game (dry gold matching the photo, not white; no fps cost, p99 16.2 vs
-- 16.4 ms off; no crash; Jeff's two crashes trace to rebases, not to the tint).
M.TINT = true
M.TINT_STRENGTH = 0.7
-- (Jeff's drive with the first version, 2026-10-28: the grass "sometimes looked white": the green textures need ~5x
-- red and ~10x blue for tan, and lit blades read brighter than the photo's ground; kept lower until tuned in game)
M.TINT_BRIGHT = 0.75
M.TINT_MIN_CHANGE = 0.15    -- a smaller change (per channel, relative) keeps the copies in use (was 0.08: the fields
                            -- along I-25 toggled it block after block, each toggle a remake; 2026-10-29)
-- A moved tint goes on (the objects made again, one a frame: M.update) only at or under this speed (m/s), or at the
-- next teleport / rebase (M.rebuild, which makes them again anyway). At a block change while driving it waits
-- (tintPending): the remake in that frame was the hitching half of Jeff's block swaps (M.update).
M.TINT_APPLY_V = 3
M.TINT_STEP = 1.06          -- the copies' factors are powers of this (per channel)
M.TINT_RANGE = { 0.2, 4.0 } -- the factor's bounds per channel (the green grass has almost no blue: tan wants ~10x)
M.TINT_PREFIX = 'bngEarthGT_'
-- material name -> { r, g, b } (linear 0-1), see above: the opaque texels' mean of the full-size base colour map
-- (opacity >= alphaRef), measured 2026-10-28 from content/.../foliage.zip, before the stock baseColorFactor
M.TEX_LINEAR = {
  m_grass_green_short_01 = { 0.1017, 0.1299, 0.0274 },   -- Hirochi small_grass_close (t_grass_green_short_02)
  m_grass_green_long_01 = { 0.0499, 0.0834, 0.0067 },    -- Hirochi long_grass_distant
  m_grass_dry_01 = { 0.4781, 0.3919, 0.2020 },           -- West Coast USA grass_dry_1
}
local tintRgb = nil         -- the last tint asked for (sRGB 0-255)
local tintUsed = {}         -- stock material name -> { name = the material the objects draw with, f = its factor }
local tintPending = false   -- the objects are to be made again on new copies at the next block change
local stockJson = {}        -- source -> its materials file decoded (the copies' fields)

-- Bare stretches at speed (the user, 2026-10-29: the grass "still pops in a lot weirdly"; his drive at 110-180 mph,
-- tools/gcpatch / gcpatchwin on, the physics block 0.7-1.7 s late). GroundCover makes a cell once, when it comes
-- within the radius, from the block's layer map at that moment, and with the patch a block move no longer frees the
-- cells (that is the patch's point). A cell made past the block's edge (the block not there yet) is empty, and stays
-- empty while the player drives through it; everything comes back at the next teleport or rebase (a rebuild): bare
-- patches ahead, then grass popping in all at once. So at a block change, when the grass's square around the player
-- reached past the old block (by more than STALE_MARGIN_M), the objects are made again on the new block and
-- generated that frame: the stock engine's own regrow (one slower frame) only when cells can be stale; on time, the
-- patch keeps everything.
M.STALE_MARGIN_M = 4
local blockBox = nil      -- the block's { x0, y0, x1, y1 } (noteBlock)
local maxRadius = 0       -- the largest radius of the objects made (ensure)
local remake = false      -- the objects are to be made again at the next block change (stale cells)
local nStale = 0          -- block changes that remade them (status)

local created = {}        -- object names we made
-- Making the objects again, one a frame (M.update). The remake in one frame (removeAll + ensure, then
-- flushGroundCoverGrids regenerating every cell within the reach in that frame's render) was the hitching half of the
-- block swaps on Jeff's afternoon drive (2026-10-29: of the 20 block commits his profile recorded, the 8 with a remake
-- cost 100-147 ms of render and 4-6 ms more Lua, the quiet ones 13-16 ms; the builder's A/B with the block not drawn
-- and with a smaller base texture changed nothing, as neither touches the grass). Staged, each frame deletes and
-- makes one definition's object and flushes its cells: 7 frames of a part of the work instead of one with all of it.
local staged = nil        -- { i = the next definition's index, why = 'tint' | 'stale' } while a remake runs
local madeAt = {}         -- definition index -> the object's name (makeObject)
local remakes = {}        -- remakes started, by why (status)
M.lastChange = 'none'     -- what the last block change did about the grass (core's commit log)
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
    if reach ~= 'near' and M.FADE_SHARE then
      out.dissolveRadius = math.min(r[2] or r[1], r[1] * M.FADE_SHARE)
    end
    factor = (r[1] / d.reach.near[1]) ^ M.REACH_EXP
  end
  return out, factor
end

local function removeAll()
  for _, name in ipairs(created) do
    local o = scenetree.findObject(name)
    if o then pcall(function() o:delete() end) end
  end
  created, madeAt, staged = {}, {}, nil
end

-- The stock definitions of definition d's source (read once per level: defsCache), or nil and why.
local function defsOf(d)
  local defs = defsCache
  if not defs[d.source] then
    if not loadedMaterials[d.source] then
      loadJsonMaterialsFile(M.SOURCES[d.source].materials)
      loadedMaterials[d.source] = true
    end
    local e
    defs[d.source], e = readDefs(d.source)
    if not defs[d.source] then return nil, e end
  end
  return defs[d.source]
end

-- Makes definition i's GroundCover (an older one of that name deleted first). Returns its name, or nil (no such
-- stock definition, logged). Errors propagate (the callers pcall).
local function makeObject(i)
  local d = M.DEFS[i]
  local defs, e = defsOf(d)
  if not defs then error(e) end
  local def = defs[d.src]
  if not def then
    log('W', 'beamngEarth', 'ground cover: no ' .. d.src .. ' in ' .. d.source)
    return nil
  end
  local name = M.PREFIX .. (d.name or d.src)
  local stock = d.tint and (def.material or def.Material)
  local mat = type(stock) == 'string' and M.tintMaterial(d.source, stock) or nil
  -- one left by an earlier module instance (a GE Lua reload keeps the level's objects) may
  -- have older fields, e.g. GPU placement: made again from the current definition
  local old = scenetree.findObject(name)
  if old then old:delete() end
  local gc = createObject('GroundCover')
  local fields, factor = M.reachFor(d, M.reach)
  maxRadius = math.max(maxRadius, tonumber(fields.radius) or tonumber(def.radius) or 0)
  for _, c in ipairs(M.fieldsFor(def, d.layer, d.share * M.density * factor)) do gc:setField(c[1], c[2], c[3]) end
  for k, v in pairs(fields) do gc:setField(k, 0, tostring(v)) end
  if mat then gc:setField('material', 0, mat) end     -- (a tinted copy of the stock one)
  for _, f in ipairs(M.PLACEMENT) do gc:setField(f[1], 0, f[2]) end
  gc.canSave = false
  gc:registerObject(name)
  scenetree.MissionGroup:addObject(gc)
  pcall(function() gc:postApply() end) -- the first layered GroundCover once showed nothing until this
  madeAt[i] = name
  return name
end

-- Create the GroundCovers (once per level; the surface layers exist). Returns true if made.
function M.ensure()
  layered = true
  if not M.enabled or #created > 0 then return #created > 0 end
  if not scenetree.MissionGroup then return false end
  maxRadius = 0
  local ok, err = pcall(function()
    for i = 1, #M.DEFS do
      local name = makeObject(i)
      if name then created[#created + 1] = name end
    end
  end)
  if not ok then
    lastError = tostring(err)
    log('W', 'beamngEarth', 'ground cover: ' .. lastError)
    removeAll()
  end
  return #created > 0
end

-- One step of a staged remake: definition i's object made again, the next definition next frame; the remake ends
-- after the last one. Its cells: generated in this frame (the flush) while the car is slow (TINT_APPLY_V), so
-- standing grass never blinks; under the engine's own per-frame budget while it moves (a few frames of that type
-- filling in round the car), so no frame carries them all.
local function stepStaged(speed)
  local i = staged.i
  local ok, err = pcall(function()
    local old = madeAt[i]
    if old then
      local o = scenetree.findObject(old)
      if o then pcall(function() o:delete() end) end
      madeAt[i] = nil
    end
    makeObject(i)
  end)
  if not ok then
    lastError = 'grass remake: ' .. tostring(err)
    log('W', 'beamngEarth', 'ground cover: ' .. lastError)
  end
  if flushGroundCoverGrids and (speed or 0) <= M.TINT_APPLY_V then flushGroundCoverGrids() end
  staged.i = i + 1
  if staged.i > #M.DEFS then staged = nil end
end

local function startStaged(why)
  remakes[why] = (remakes[why] or 0) + 1
  staged = { i = 1, why = why }
end

-- Once a frame (core onUpdate, after the world's update): a staged remake's next object; a moved tint's remake
-- started once the car is slow (TINT_APPLY_V). speed: the player's (m/s; nil: standing).
function M.update(speed)
  if #created == 0 then staged = nil; return end
  if staged then stepStaged(speed); return end
  if tintPending and (speed or 0) <= M.TINT_APPLY_V then
    tintPending = false
    startStaged('tint')
    stepStaged(speed)
  end
end

local function srgbToLinear(c)
  c = c / 255
  if c <= 0.04045 then return c / 12.92 end
  return ((c + 0.055) / 1.055) ^ 2.4
end

-- Pure: the baseColorFactor { r, g, b, a } that gives a material with stock factor `orig` and texture mean `tex`
-- (linear) the photo's colour `rgb` (sRGB 0-255) at `strength` (0: the stock look, 1: the photo's), times `bright`.
function M.tintFactor(orig, tex, rgb, strength, bright)
  local out = {}
  for i = 1, 3 do
    local o = tonumber(orig[i]) or 1
    local seen = math.max(1e-4, (tonumber(tex[i]) or 0) * o)
    local want = srgbToLinear(tonumber(rgb[i]) or 0) * (bright or 1)
    local f = o * (math.max(want, 1e-4) / seen) ^ (strength or 1)
    out[i] = math.min(M.TINT_RANGE[2], math.max(M.TINT_RANGE[1], f))
  end
  out[4] = tonumber(orig[4]) or 1
  return out
end

-- Pure: factor f snapped to powers of TINT_STEP per channel ({ r, g, b, a } and its key "i_j_k").
function M.tintStep(f)
  local q, k = {}, {}
  local l = math.log(M.TINT_STEP)
  for i = 1, 3 do
    k[i] = math.floor(math.log(math.max(f[i], 1e-6)) / l + 0.5)
    q[i] = M.TINT_STEP ^ k[i]
  end
  q[4] = f[4] or 1
  return q, table.concat(k, '_'):gsub('-', 'm')
end

-- The stock definition of material `name` in source's materials file (its decoded entry), or nil.
local function stockEntry(source, name)
  if stockJson[source] == nil then
    local text = readFile and readFile(M.SOURCES[source].materials)
    local ok, t = pcall(jsonDecode, text or '')
    stockJson[source] = ok and type(t) == 'table' and t or false
  end
  local t = stockJson[source]
  return t and type(t[name]) == 'table' and t[name] or nil
end

-- A new Material `name`: stock entry e's fields, its stage-0 baseColorFactor f, set before it is registered.
local function makeCopy(name, e, f)
  local m = createObject('Material')
  if e.version ~= nil then m:setField('version', 0, fieldValue(e.version)) end
  for k, v in pairs(e) do
    if k ~= 'name' and k ~= 'mapTo' and k ~= 'class' and k ~= 'persistentId' and k ~= 'Stages' and k ~= 'version'
        and v ~= jsonNull then
      m:setField(k, 0, fieldValue(v))
    end
  end
  for i, st in ipairs(type(e.Stages) == 'table' and e.Stages or {}) do
    for k, v in pairs(st) do
      if v ~= jsonNull then m:setField(k, i - 1, fieldValue(v)) end
    end
  end
  m:setField('baseColorFactor', 0, string.format('%.4f %.4f %.4f %.4f', f[1], f[2], f[3], f[4]))
  m:setField('mapTo', 0, name)
  m.canSave = false
  m:registerObject(name)
  return m
end

-- The material a tinted definition's GroundCover draws with: the stock one (tint off, no colour yet, unmeasured or
-- unknown texture) or a tinted copy, re-used while the colour stays within TINT_MIN_CHANGE of it.
function M.tintMaterial(source, stock)
  local tex = M.TEX_LINEAR[stock]
  local e = tex and stockEntry(source, stock)
  if not (M.TINT and tintRgb and e) then
    tintUsed[stock] = nil
    return stock
  end
  local st = type(e.Stages) == 'table' and type(e.Stages[1]) == 'table' and e.Stages[1] or {}
  local orig = type(st.baseColorFactor) == 'table' and st.baseColorFactor or { 1, 1, 1, 1 }
  local f = M.tintFactor(orig, tex, tintRgb, M.TINT_STRENGTH, M.TINT_BRIGHT)
  local cur = tintUsed[stock]
  if cur and not M.tintMoved(cur.f, f) and scenetree.findObject(cur.name) then return cur.name end
  local q, key = M.tintStep(f)
  local name = M.TINT_PREFIX .. stock .. '_' .. key
  if not scenetree.findObject(name) then
    local ok, err = pcall(makeCopy, name, e, q)
    if not ok then
      lastError = 'grass tint: ' .. tostring(err)
      return stock
    end
  end
  tintUsed[stock] = { name = name, f = f }
  return name
end

-- Pure: has factor b moved from a by more than TINT_MIN_CHANGE in any channel?
function M.tintMoved(a, b)
  for i = 1, 3 do
    if math.abs(b[i] - a[i]) > M.TINT_MIN_CHANGE * math.max(a[i], 0.1) then return true end
  end
  return false
end

-- Would the objects draw with other materials now (the tint switched, or its colour moved)?
local function tintChanged()
  for _, d in ipairs(M.DEFS) do
    if d.tint then
      local def = defsCache[d.source] and defsCache[d.source][d.src]
      local stock = def and (def.material or def.Material)
      if type(stock) == 'string' and M.TEX_LINEAR[stock] then
        local cur = tintUsed[stock]
        if (cur ~= nil) ~= (M.TINT and tintRgb ~= nil) then return true end
        if cur and M.TINT and tintRgb then
          local e = stockEntry(d.source, stock)
          local st = e and type(e.Stages) == 'table' and type(e.Stages[1]) == 'table' and e.Stages[1] or {}
          local orig = type(st.baseColorFactor) == 'table' and st.baseColorFactor or { 1, 1, 1, 1 }
          if M.tintMoved(cur.f, M.tintFactor(orig, M.TEX_LINEAR[stock], tintRgb, M.TINT_STRENGTH, M.TINT_BRIGHT)) then
            return true
          end
        end
      end
    end
  end
  return false
end

-- The photo's colour under the block's grass (sRGB 0-255 { r, g, b }, the terrain message's grassTint), or nil (keep
-- the last); core calls it before the block change, whose afterBlockChange makes the objects again when the colour
-- moved. Returns true when they will be.
function M.setTint(rgb)
  if type(rgb) == 'table' and #rgb >= 3 then tintRgb = { tonumber(rgb[1]), tonumber(rgb[2]), tonumber(rgb[3]) } end
  if #created > 0 then tintPending = tintChanged() end   -- (a colour that moved back drops the pending remake)
  return tintPending
end

-- Tint on / off (the settings page): the objects made again at once on the tinted copies or the stock materials.
function M.setTintEnabled(on)
  M.TINT = on and true or false
  if #created > 0 and tintChanged() then M.rebuild() end
  return M.TINT
end

-- The physics block was just imported or moved (core, same tick): regenerate all the grass before
-- this frame is drawn. Returns true if it asked for that.
-- Pure: does the square of half-side r around p reach past box { x0, y0, x1, y1 } by more than margin?
function M.reachedPast(box, p, r, margin)
  if not box or type(p) ~= 'table' or not p.x or not r or r <= 0 then return false end
  local m = margin or 0
  return p.x - r < box[1] - m or p.x + r > box[3] + m or p.y - r < box[2] - m or p.y + r > box[4] + m
end

-- The block is about to change to box { x0, y0, x1, y1 } (core, before afterBlockChange); player: the player's
-- position (default: the player vehicle's). Returns true when the grass will be made again (stale cells).
function M.noteBlock(x0, y0, x1, y1, player)
  if player == nil and getPlayerVehicle then
    local v = getPlayerVehicle(0)
    local pos = v and v:getPosition()
    if pos then player = { x = pos.x, y = pos.y } end
  end
  local stale = #created > 0 and M.reachedPast(blockBox, player, maxRadius, M.STALE_MARGIN_M)
  blockBox = { x0, y0, x1, y1 }
  if stale then remake = true end
  return stale
end

-- speed: the player's (m/s). Nothing is made again in this frame (see `staged` above): stale cells start a staged
-- remake next frame; a moved tint waits for the car to slow down (M.update), or the next rebuild.
function M.afterBlockChange(speed)
  if #created == 0 or not flushGroundCoverGrids then M.lastChange = 'none'; return false end
  local what = 'quiet'
  if remake then                  -- (cells made off the old block: made again, one object a frame)
    remake, nStale = false, nStale + 1
    if not staged then startStaged('stale') end
    what = 'stale cells: made again over frames'
  elseif staged then
    what = staged.why .. ' remake under way'
  elseif tintPending then
    what = (speed or 0) <= M.TINT_APPLY_V and 'tint: made again over frames' or 'tint moved: waits for a stop'
  end
  flushGroundCoverGrids()
  M.lastChange = what
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
  created, madeAt, staged, remakes = {}, {}, nil, {}
  M.lastChange = 'none'
  blockBox, maxRadius, remake, nStale = nil, 0, false, 0
  tintUsed = {}
  tintPending = false
  stockJson = {}
  defsCache = {}
  loadedMaterials = {}
  layered = false
end

-- A teleport or a floating-origin rebase moved the world under the grass: make the objects again
-- (their cells then start from nothing whatever the engine kept) and generate them this frame.
function M.rebuild()
  if #created == 0 then return false end
  tintPending, remake = false, false
  blockBox = nil                -- (a teleport or rebase: the old block's box is in another frame)
  removeAll()
  M.ensure()
  local ok = M.afterBlockChange(0)
  M.lastChange = 'rebuilt'
  return ok
end

function M.status()
  local mats = {}
  for _, u in pairs(tintUsed) do mats[#mats + 1] = u.name end
  table.sort(mats)
  return { enabled = M.enabled, density = M.density, reach = M.reach, objects = #created, materialsLoaded = next(loadedMaterials) ~= nil,
    error = lastError, tint = M.TINT and tintRgb or nil, tintMaterials = mats, radius = maxRadius, staleRemakes = nStale,
    tintPending = tintPending, staged = staged and staged.why or nil, remakes = remakes, lastChange = M.lastChange }
end

return M
