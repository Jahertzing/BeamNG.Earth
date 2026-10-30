-- Roads near the player (docs/ROADMAP.md v1.6 "Roads", server/roads.py): the server announces road
-- tiles (`roads_tile`: a JSON file of chunks in the anchor frame) and drops them (`roads_drop`); each
-- chunk becomes one DecalRoad drawn on the physics ground (engine probes P1-P3, 2026-10-11: they
-- render on the hidden block, re-clip by themselves when it is re-imported or moved, and cost
-- ~0.45 ms per 100-node road with insertNodeNoRegen + regenerate). Creation and deletion run under the
-- frame governor (kinds roadChunk / roadDelete). A new anchor makes every road of the old one go
-- (the server sends the new frame's tiles again).
--
-- Materials are stock ones from official levels, loaded at runtime from the player's own install
-- (nothing copied): West Coast USA's asphalt strip, sidewalks and lines, Johnson Valley's dirt road
-- tracks, Italy's cobblestone.
--
-- Raised sidewalks (curbs, sidewalk slabs): one mesh per tile (server/sidewalks.py, the way West
-- Coast USA builds its sidewalks: one modelled mesh per district, with its `curbs` and `sidewalk1`
-- materials), announced as the tile's "walk" {shape, pos} and shown as a TSStatic that collides with
-- its visible mesh. A TSStatic only collides after be:reloadCollision() (asynchronous: in effect a
-- frame later; ~1.8 ms), so that runs once per RELOAD_EVERY_S at most while sidewalks come and go.
-- (First cut, replaced: a MeshRoad per sidewalk piece, 3 draw calls each and no curb profile.)
-- Storm drains are DecalRoad chunks like the rest (Driver Training's m_metal_grid_drain).
-- Console: beamngEarth_roads.status(), .setEnabled(bool)

local stopwatch = require('beamngEarth/stopwatch')
local popin = require('beamngEarth/popin')

local M = {}

M.MATERIAL_FILES = { '/levels/west_coast_usa/art/road/main.materials.json',
  '/levels/east_coast_usa/art/road/main.materials.json',         -- ECA_road_edge_dirt (the asphalt's edge, roads.EDGE_DECAL)
  '/levels/west_coast_usa/art/decals/main.materials.json',       -- roadmarkings1 (turn arrows, markings.ARROW_MATERIAL)
  '/levels/west_coast_usa/art/shapes/roads/main.materials.json',   -- curbs, sidewalk1 (the sidewalk mesh)
  '/levels/johnson_valley/art/decalroad/main.materials.json',
  '/levels/italy/art/shapes/buildings/main.materials.json',        -- italy_cobblestone (paving stones)
  '/levels/driver_training/art/road/main.materials.json',          -- m_metal_grid_drain (storm drains)
  '/levels/gridmap_v2/art/shapes/grid/main.materials.json',        -- m_gm_concrete_plain (concrete streets)
  '/levels/beamng_earth/art/shapes/beamngEarth/buildings.materials.json',   -- buildings (server/buildings.py)
  '/levels/beamng_earth/art/shapes/beamngEarth/guidesigns.materials.json',  -- highway signs (server/guidesigns.py)
  '/levels/beamng_earth/art/shapes/beamngEarth/grasscards.materials.json',  -- grass cards (server/grasscards.py)
  -- upstream's highway signs (server/signstream.py: exit and overhead panels, mile markers, merge warnings)
  '/levels/beamng_earth/art/shapes/beamngEarth/roadside/exit.materials.json',
  '/levels/beamng_earth/art/shapes/beamngEarth/roadside/merge.materials.json' }
M.FADE = '450 150'          -- distanceFade: start, range (m)
M.enabled = true
-- the highway signs (server signstream.py, from cody196's PR #363) and the speed-limit signs (speedsigns.py): sign tiles
-- (key "s<tx>_<ty>") carry the render-only shapes SIGN_WHATS; switched off, their objects go but each tile keeps its data
-- (signData) to show again at once
M.signsEnabled = false   -- (this fork: upstream's highway signs off by default; its own come in the road tiles, "sign")
M.SIGN_WHATS = { 'exits', 'mileposts', 'merges', 'speeds' }
-- be:reloadCollision() at most this often: every rebuild is a ~20 ms physics frame (2026-10-13, the
-- engine's frame metrics), and while driving new sidewalk tiles arrive all the time. They arrive ~640 m
-- ahead (11 s at 58 m/s), so a few seconds before they collide do not matter. A terrain commit rebuilds
-- collision anyway: M.collisionRebuilt() clears the pending rebuild.
-- 2026-10-14: a rebuild had grown to ~56 ms (1.1 M collision vertices with the buildings) and ran every 3 s
-- while driving, most of the hitches of a 34 mph drive: new colliders now wait up to 6 s (still 11 s of margin
-- at 130 mph), and colliders deleted far behind the car (their stale collision harms nobody) up to 30 s.
-- 2026-10-29: 15 s (from 6). The rebuild is still the drive's commonest stutter (~21 ms of Lua and a ~15 ms physics
-- frame, 8-10 a minute on the Union Station replay); a collider waiting on this timer turns urgent (urgentColliders,
-- re-checked every frame) as soon as the car comes within URGENT_S of it, so the timer only paces the ones it never
-- nears. Standing or crawling (under RELOAD_MOVING_MS: a teleport's wait, core.floorCheck's tunnel floor) it stays 6 s.
M.RELOAD_EVERY_S = 15
M.RELOAD_STAND_S = 6
M.RELOAD_MOVING_MS = 5
-- Collision only near the car (2026-10-15: the rebuild cost scales with every colliding triangle loaded, ~40 ms in
-- Fells Point with all 36 tiles' buildings and sidewalks): a tile's colliders (sidewalks, decks, the buildings'
-- lean collision mesh "bldcol") are made once its centre is within COLLIDE_LEAD_S of driving (COLLIDE_M min / max)
-- and dropped again UNCOLLIDE_M beyond that. The drawn building meshes only render when the tile has a bldcol.
M.TILE_M = 256
M.COLLIDE_M = { 320, 2000 }
M.COLLIDE_LEAD_S = 14
M.UNCOLLIDE_M = 350
M.COLLIDE_CHECK_S = 0.25
M.RELOAD_DELETES_S = 30
M.DELETE_NEAR_M = 200       -- a collider deleted this close to the player counts as new (no ghost walls)
-- High speed (the user, 2026-10-19: "I always want the ground and any bridges/buildings to have collision ... going at
-- high speeds"; at 180 mph a bridge's collider was solid only 32 m ahead, held back by RELOAD_EVERY_S): above
-- FAST_MS the COLLIDE_LEAD_S reach is ahead of the car only (COLLIDE_M max), beside and behind it COLLIDE_M min;
-- and a collider the car reaches within URGENT_S is made solid at once (URGENT_GAP_S between those rebuilds at most).
M.FAST_MS = 25
M.SIDE_PENALTY = 4          -- nextWalk at FAST_MS: m along the path a metre sideways counts as (server roads.py)
M.LEAD_MAX_M = 2000         -- ... and what every tile behind the car comes after
M.URGENT_S = 6
M.URGENT_GAP_S = 1
-- ... but not for every collider made near the car (2026-10-28, Jeff's Drive through Denver: 257 rebuilds in 22 min,
-- ~100 of them under 6 s apart, and one every second for 10+ s after a rebase, each a 15-80 ms frame). The urgent
-- reach starts at URGENT_MIN_M (150 m plus the tile's half diagonal took in every tile entering COLLIDE_M's 320 m at
-- town speeds: all urgent), a tile behind a moving car never is, and neither is one where a rebase's ghost of the same
-- kind still collides (the old frame's copy of the same mesh: solid already).
M.URGENT_MIN_M = 100
M.GHOST_COVER_M = 0.75 * 256  -- a ghost tile's centre this close to the new tile's: the same place
-- What the car drives on comes first (nextWalk): a bridge deck's or a tunnel's collider counts this much nearer than the
-- rest (on a Steam Deck at 80 mph in Phoenix the import queue grew to 140 behind ~20 ms imports, and an I-10 overpass's
-- deck turned solid only as the car reached it, behind the sidewalks' and buildings' colliders of the same tiles)
M.DRIVE_FIRST_M = 400
-- ... and one the car reaches within URGENT_S (150 m at least) is made at once, whatever the governor's budget (its cost
-- still charged: what follows waits), one every URGENT_EVERY_FRAMES at most: at ~20 ms an import on a Steam Deck, the
-- 2-4 ms budget let ~3 a second through, fewer than a city's tiles bring at 80 mph
M.URGENT_EVERY_FRAMES = 3
-- No be:reloadCollision for SWAP_QUIET_FRAMES frames and SWAP_QUIET_MS after a terrain .ter swap (M.terrainSwapped): the
-- swapped block's collision follows by itself a frame later, and a rebuild in between took the block before its
-- heights were in: flat ground at z 0 for a second, and a ball at 180 mph sank under the real ground and fell through
-- it for good (in-game 2026-10-19, once the urgent rebuilds above made that coincidence common).
M.SWAP_QUIET_FRAMES = 3
M.SWAP_QUIET_MS = 150
-- A collision rebuild (be:reloadCollision, ~10-25 ms on a Steam Deck) in a frame that already spent RELOAD_ROOM_MS on
-- imports waits for the next frame, RELOAD_WAIT_FRAMES at most, unless the car is held over a collider (holdActive):
-- an urgent import, another and the rebuild made 70-80 ms frames on a Deck's Baltimore drive (2026-10-23)
M.RELOAD_ROOM_MS = 8.0
M.RELOAD_WAIT_FRAMES = 3
local reloadWaits = 0

local newColliders = {}    -- { id, walkQ entry } of the colliders made since the last reloadCollision
local parked = {}          -- walkQ entries of colliders whose tile is not near enough yet
local sinceCollideCheck = stopwatch.new()
local held = {}            -- walkQ entries of colliding meshes held while the player is in their boxes
local noteNewColliders, playerIn, releaseHeld
local tiles = {}            -- key "a:tx_ty" -> { anchor, objs = { DecalRoad ids }, queued = n, walk = TSStatic id,
                            --   road = TSStatic id }
local walkQ = {}            -- { key, walk = {shape, pos}, what = 'walk' | 'road' } FIFO
local createQ = {}          -- { key, chunk } FIFO
local deleteQ = {}          -- object ids
local loadedMaterials = false
local anchorId = nil
local far = {}              -- far buildings (server buildings.far_tile): key "a:tx_ty" -> { anchor, id, shape, pos, hidden }
local farSeen = {}          -- far tile keys that arrived (with buildings or none): the skyline tiles' test
local farQ = {}             -- keys of far tiles to make
local grassQ = {}           -- { key, i } of grass card squares to make (a far tile's subs)
local sky = {}              -- skyline tiles (server skyline.py): key "a:x_y" -> entry (M.onSkyTile)
local lastError = nil
local sinceChange = stopwatch.new()  -- ms since a tile arrived / went or a road was made / deleted
local sinceReload = stopwatch.new()
local collisionDirty = false
local lastReloadAt = -math.huge   -- now() of the last be:reloadCollision (ours or another's): M.ahead
local reloads = 0                 -- be:reloadCollision runs so far (ours or another's): M.reloads
local sinceUrgent = math.huge     -- frames since the last urgent walk import (M.urgentWalk)
local deletesDirty = false  -- only far colliders deleted since the last rebuild (RELOAD_DELETES_S)

-- Every road object lives in one SimGroup. A Lua reload (Lua:requestReload) rebuilds the VM without
-- unloading the extensions, so nothing deletes the old objects: 2026-10-11, after a few reloads, 3,558
-- DecalRoads and 120 sidewalk MeshRoads were in the scene for 1,011 + 51 tracked (the user saw fps drop).
-- The group is emptied when this module loads (see the end of the file).
M.GROUP = 'bngEarthRoads'

local function group()
  local g = scenetree.findObject(M.GROUP)
  if not g then
    g = createObject('SimGroup')
    g.canSave = false
    g:registerObject(M.GROUP)
    scenetree.MissionGroup:addObject(g)
  end
  return g
end

-- Objects of a previous Lua state deleted. Returns how many.
function M.sweep()
  local g = scenetree.MissionGroup and scenetree.findObject(M.GROUP)
  if not g then return 0 end
  local n = g:getCount()
  if n > 0 then
    g:deleteAllObjects()
    be:reloadCollision()
  end
  return n
end

-- The road surfaces drawn opaque (2026-10-14): the stock decal materials are all translucent, and inside
-- one road mesh the markings showed only while the engine happened to draw them after the asphalt; with
-- many more translucent objects in view (buildings' windows, signals) they vanished. Opaque asphalt is
-- drawn first, always, and the translucent lines, crossings and gutters over it.
M.OPAQUE = { 'road_asphalt_2lane', 'italy_cobblestone' }
-- The opaque road surfaces cast no sun shadow (from Madderose's PR #41). Translucent, as the stock decal materials are,
-- they drew none; made opaque (above) they joined the shadow pass: every road tile's mesh drawn into each shadow
-- cascade every frame (the tiles are dynamic TSStatics, no cached shadow map), for a shadow that fell under the road
-- itself. The decks' slabs, the curbs and the sidewalks keep theirs.
M.NO_SHADOW = { 'road_asphalt_2lane', 'italy_cobblestone', 'm_gm_concrete_plain' }

local function loadMaterials()
  if loadedMaterials then return end
  for _, f in ipairs(M.MATERIAL_FILES) do loadJsonMaterialsFile(f) end
  for _, f in ipairs(require('beamngEarth/gasstations').MATERIAL_FILES) do loadJsonMaterialsFile(f) end   -- (pumps)
  local fields, order = {}, {}
  for _, name in ipairs(M.OPAQUE) do
    if not fields[name] then fields[name] = {}; order[#order + 1] = name end
    fields[name].translucent = '0'
  end
  for _, name in ipairs(M.NO_SHADOW) do
    if not fields[name] then fields[name] = {}; order[#order + 1] = name end
    fields[name].castShadows = '0'
  end
  for _, name in ipairs(order) do       -- (one reload a material)
    local m = scenetree.findObject(name)
    if m then
      pcall(function()
        for k, v in pairs(fields[name]) do m:setField(k, 0, v) end
        m:flush(); m:reload()
      end)
    end
  end
  local pad = scenetree.findObject('tactile_indicator')    -- curb ramp pads: US truncated domes are yellow
  if pad then pcall(function() pad:setField('baseColorFactor', 0, '0.95 0.78 0.18 1'); pad:flush(); pad:reload() end) end
  loadedMaterials = true
end

local function tileKey(aid, k) return tostring(aid) .. ':' .. tostring(k) end

-- Pooled shape files (server/matslots.py, 2026-10-16: the engine's frame cost grows with every distinct shape file it
-- ever loaded): roads_tile / bldfar_tile carry `slots` {what = {path, serial}}, shape files the server re-uses for
-- later tiles once we report [slot, serial] gone (core.lua pose.slotsGone). A slot goes when its tile is dropped AND
-- its last object is deleted (a collider deleted and made again while the tile lives keeps it).
local session = nil        -- the server session the slots belong to (setSession)
local objSlot = {}         -- object id -> slot number of the pooled file it loaded
local slotLive = {}        -- slot number -> objects alive with it
local slotDropped = {}     -- slot number -> { serial, session } of a dropped tile's slot, reported at its last delete
local slotsGone = {}       -- { n, serial, session } to report
local function slotOf(path) return tonumber(type(path) == 'string' and path:match('/slot_(%d+)%.cdae$') or nil) end

function M.setSession(s) session = s end

-- An object with a pooled shape was made / is deleted.
local function slotMade(id, path)
  local n = slotOf(path)
  if not n then return end
  objSlot[id] = n
  slotLive[n] = (slotLive[n] or 0) + 1
end
local function slotObjGone(id)
  local n = objSlot[id]
  if not n then return end
  objSlot[id] = nil
  slotLive[n] = (slotLive[n] or 1) - 1
  if slotLive[n] <= 0 then
    slotLive[n] = nil
    local d = slotDropped[n]
    if d then slotDropped[n] = nil; slotsGone[#slotsGone + 1] = { n, d[1], d[2] } end
  end
end
-- A tile's slot { n, serial, session } is let go: at once if no object has it, else at its last delete.
local function slotDrop(sl)
  if not sl then return end
  if (slotLive[sl[1]] or 0) > 0 then slotDropped[sl[1]] = { sl[2], sl[3] }
  else slotsGone[#slotsGone + 1] = { sl[1], sl[2], sl[3] } end
end
-- The slots of msg.slots[what] as { n, serial, session } (nil: not pooled).
local function msgSlot(msg, what)
  local e = type(msg.slots) == 'table' and msg.slots[what]
  if type(e) ~= 'table' then return nil end
  local n, serial = slotOf(e[1]), tonumber(e[2])
  if not n or not serial then return nil end
  return { n, serial, session, path = e[1] }
end

-- The [slot, serial] pairs gone since the last call, for server session `sess`.
function M.takeSlotsGone(sess)
  if #slotsGone == 0 then return {} end
  local out = {}
  for _, g in ipairs(slotsGone) do if g[3] ~= nil and g[3] == sess then out[#out + 1] = { g[1], g[2] } end end
  slotsGone = {}
  return out
end

-- Every [slot, serial] held for session `sess` (hello after a reconnect): live tiles' slots and dropped ones whose
-- objects are not deleted yet.
function M.slotsHeld(sess)
  local out = {}
  for _, t in pairs(tiles) do
    for _, sl in pairs(t.slots or {}) do if sl[3] == sess then out[#out + 1] = { sl[1], sl[2] } end end
  end
  for _, f in pairs(far) do
    if f.slot and f.slot[3] == sess then out[#out + 1] = { f.slot[1], f.slot[2] } end
    for _, sq in ipairs(f.subs or {}) do
      if sq.slot and sq.slot[3] == sess then out[#out + 1] = { sq.slot[1], sq.slot[2] } end
    end
    for _, p in pairs(f.parts or {}) do
      if p.slot and p.slot[3] == sess then out[#out + 1] = { p.slot[1], p.slot[2] } end
    end
  end
  for n, d in pairs(slotDropped) do if d[2] == sess then out[#out + 1] = { n, d[1] } end end
  return out
end

local function now() return os.clock() * 1000 end

-- Main-thread cost of each kind of unit (n, total ms, worst ms) since the level loaded: road chunks, deletes, each
-- mesh kind of a road tile (road, walk, bld, bldcol, deck ...), far building tiles, collision reloads. status().units
local unitStats = {}
local unitLog = {}       -- the last UNIT_LOG units: { kind, ms, shape } (status().unitLog)
M.UNIT_LOG = 400
local frameUnits = {}    -- this frame's units of FRAME_UNIT_MS or more (M.frameUnits: the perf log's hitch lines)
M.FRAME_UNIT_MS = 5
local function noteUnit(kind, ms, shape, info)
  if ms >= M.FRAME_UNIT_MS then frameUnits[#frameUnits + 1] = { kind, ms, shape, info } end
  unitLog[#unitLog + 1] = { kind, ms, shape }
  if #unitLog > M.UNIT_LOG then table.remove(unitLog, 1) end
  local u = unitStats[kind]
  if not u then u = { n = 0, ms = 0, max = 0 }; unitStats[kind] = u end
  u.n, u.ms = u.n + 1, u.ms + ms
  if ms > u.max then u.max = ms end
end

-- A colliding mesh comes in pieces when it is big (server meshpieces.py: COLLIDER_PIECE_TRIS triangles each, the
-- engine's collision set-up at registration being ~50 us a triangle on the main thread, 2026-10-29: a deck 53 ms, a
-- railway 112 ms, a building collision mesh 1.3 s in one unit): "deck", "deck2", ... up to PIECES, each a unit of
-- its own that the governor spreads. kindOf('deck2') == 'deck': the rules go by the kind, the bookkeeping (t[what],
-- colWant, colAt, cq) by the name. The drawn buildings keep their own "bld2".."bld4" (the engine's index limit).
M.PIECES = 12
local PIECE_KINDS = { deck = true, tunnel = true, rail = true, bldcol = true, walkcol = true }
local function kindOf(what) return type(what) == 'string' and what:match('^%a+') or what end
M.kindOf = kindOf
local function withPieces(kinds)
  local out = {}
  for _, k in ipairs(kinds) do
    out[#out + 1] = k
    if PIECE_KINDS[k] then for i = 2, M.PIECES do out[#out + 1] = k .. i end end
  end
  return out
end
local DROP_WHATS = withPieces({ 'deck', 'bld', 'bld2', 'bld3', 'bld4', 'bldcol', 'walkcol', 'tunnel', 'rail', 'railcol', 'sign',
  'signcol' })
local SLOT_WHATS = withPieces({ 'road', 'deck', 'walk', 'walkcol', 'bld', 'bld2', 'bld3', 'bld4', 'bldcol', 'wire', 'tunnel',
  'rail', 'railcol', 'sign', 'signcol', 'exits', 'mileposts', 'merges', 'speeds' })
local COLLIDER_WHATS = withPieces({ 'walk', 'deck', 'bld', 'bld2', 'bld3', 'bld4', 'bldcol', 'walkcol', 'tunnel', 'rail', 'railcol',
  'signcol' })
-- the collider boxes of a unit: its own, or its kind's (the pieces share the kind's boxes)
local function boxesOf(t, what)
  local c = t and t.colliders
  if type(c) ~= 'table' then return nil end
  return c[what] or c[kindOf(what)]
end

local function dropTile(key)
  local t = tiles[key]
  if not t then return end
  for _, sl in pairs(t.slots or {}) do slotDrop(sl) end
  for w, v in pairs(t.oldSkin or {}) do       -- (a geotile's previous skin and replaced parts: road geotiles below)
    if type(w) == 'string' and w:sub(1, 2) == 's:' then slotDrop(v) else deleteQ[#deleteQ + 1] = v end
  end
  for _, id in pairs(t.oldPart or {}) do deleteQ[#deleteQ + 1] = id end
  for _, sl in pairs(t.oldPartSlot or {}) do slotDrop(sl) end
  t.oldSkin, t.oldPart, t.oldPartSlot = nil, nil, nil
  for _, id in ipairs(t.objs) do deleteQ[#deleteQ + 1] = id end
  if t.walk then deleteQ[#deleteQ + 1] = t.walk end
  if t.road then deleteQ[#deleteQ + 1] = t.road end
  for _, w in ipairs(DROP_WHATS) do if t[w] then deleteQ[#deleteQ + 1] = t[w] end end
  if t.wire then deleteQ[#deleteQ + 1] = t.wire end
  for _, w in ipairs(M.SIGN_WHATS) do if t[w] then deleteQ[#deleteQ + 1] = t[w] end end
  -- and its meshes not made yet (from KyzoFox's 1.5.4, PR #64): a tile sent again under the same key left its old
  -- walkQ / held / createQ entries, which were then made for the new tile from pooled shape paths that had just been
  -- reported gone and refilled with another tile's shape: that tile's buildings drawn here at its ground height (houses
  -- floating in the sky near Cleburne, AL, 2026-10-19)
  local function purge(list)
    local keep = {}
    for _, q in ipairs(list) do if q.key ~= key then keep[#keep + 1] = q end end
    return keep
  end
  parked, walkQ, held, createQ = purge(parked), purge(walkQ), purge(held), purge(createQ)
  if t.props then require('beamngEarth/trees').dropKey((key:gsub(':', ':p', 1))) end
  if t.guardrails then require('beamngEarth/trees').dropKey((key:gsub(':', ':g', 1))) end
  if t.props then require('beamngEarth/nightlights').dropKey(key) end
  if t.park then require('beamngEarth/parking').dropKey(key) end
  if t.fuel then require('beamngEarth/gasstations').dropKey(key) end
  if t.sound then require('beamngEarth/ambience').dropSpaces(key) end
  if t.wells then require('beamngEarth/pumpjacks').dropKey(key) end
  tiles[key] = nil
end

-- A far tile's street lamps (server buildings.far_lamps: { {x, y, z}, ... }, the far and skyline tiles' streets past
-- the road tiles; the user, 2026-10-27: "extend the street and window lights as far as the buildings render"): glow
-- rows of trees.lua's lampglow group (drawn at night only) while the tile shows, one trees tile per 256 m square
-- (its distance rings and caps; ranks hashed under the farthest ring's 0.2: every one shown, a cap thinning evenly).
-- Its tall buildings' red obstruction lights (buildings.beacon_points, f.beacons) the same way, group beaconglow.
M.FAR_LAMP_GROUPS = { 'lampglow', 'beaconglow' }
local function farLamps(key, f, on)
  if on == (f.lampKeys ~= nil) then return end
  local ok, trees = pcall(require, 'beamngEarth/trees')
  if not ok then return end
  if not on then
    for _, k in ipairs(f.lampKeys) do trees.dropKey(k) end
    f.lampKeys = nil
    return
  end
  local by, tag = {}, 'FL' .. tostring(key):gsub('[^%w]', '') .. '|'
  for g, list in ipairs({ f.lamps or {}, f.beacons or {} }) do
    for i, p in ipairs(list) do
      if type(p) == 'table' and p[1] and p[2] and p[3] then
        local k = tag .. math.floor(p[1] / 256) .. '_' .. math.floor(p[2] / 256)
        by[k] = by[k] or {}
        by[k][#by[k] + 1] = { g - 1, p[1], p[2], p[3], 0, 1, 1, ((i * 7919) % 997) / 997 * 0.19 }
      end
    end
  end
  f.lampKeys = {}
  for k, rows in pairs(by) do
    trees.onRows(f.anchor, k, rows, M.FAR_LAMP_GROUPS)
    f.lampKeys[#f.lampKeys + 1] = f.anchor .. ':' .. k
  end
end

-- A grass card tile's squares (f.subs) go: their objects deleted, and with gone their slots let go too.
local function grassSubsDelete(f, gone)
  if not f.subs then return end
  for _, s in ipairs(f.subs) do
    if s.id then deleteQ[#deleteQ + 1] = s.id; s.id = nil end
    s.queued, s.hidden = nil, nil
    if gone then slotDrop(s.slot) end
  end
  if gone then f.subs, f.subSize = nil, nil end
end

-- Every road not of anchor aid goes (a new anchor: the server re-sends its tiles).
local function switchAnchor(aid)
  if aid == anchorId then return end
  anchorId = aid
  for key, t in pairs(tiles) do
    if t.anchor ~= aid and not t.ghost and not t.geo then dropTile(key) end   -- (geotiles: M.replace)
  end
  for key, f in pairs(far) do
    if f.anchor ~= aid and not f.ghost and not f.geo then      -- (far geotiles: M.replace)
      farLamps(key, f, false)
      if f.id then deleteQ[#deleteQ + 1] = f.id end
      grassSubsDelete(f, true)
      slotDrop(f.slot)
      far[key] = nil
    end
  end
  for key, e in pairs(sky) do
    if e.anchor ~= aid and not e.ghost then M.skyDelete(e); sky[key] = nil end
  end
  for key in pairs(farSeen) do
    if string.sub(key, 1, #tostring(aid) + 1) ~= tostring(aid) .. ':' and not (far[key] and far[key].geo) then
      farSeen[key] = nil
    end
  end
  local keep = {}
  for _, q in ipairs(createQ) do if tiles[q.key] then keep[#keep + 1] = q end end
  createQ = keep
  keep = {}
  for _, q in ipairs(walkQ) do if tiles[q.key] then keep[#keep + 1] = q end end
  walkQ = keep
end

-- Pure: the DecalRoad fields of a chunk (name, value), in order.
function M.fieldsFor(chunk)
  return {
    { 'material', chunk.m }, { 'textureLength', tostring(chunk.tl or 10) },
    { 'renderPriority', tostring(chunk.p or 20) }, { 'drivability', '-1' }, { 'improvedSpline', 'true' },
    { 'overObjects', 'false' }, { 'distanceFade', M.FADE }, { 'decalBias', '0.1' },
  }
end

-- The collision radius around the player (m): COLLIDE_LEAD_S of driving at its speed, within COLLIDE_M.
function M.collideRadius(speed)
  return math.max(M.COLLIDE_M[1], math.min(M.COLLIDE_M[2], (speed or 0) * M.COLLIDE_LEAD_S))
end

local lastRadius = M.COLLIDE_M[1]
local foci = {}             -- other vehicles' collision islands { x, y, r } (M.setFoci)
local collisionfoci = require('beamngEarth/collisionfoci')
local lastSpeed = 0
local holdActive = false
local swapQuiet = nil       -- { frames left, stopwatch } after a terrain swap (M.terrainSwapped)    -- core's drop hold: the game paused, the car frozen where it was dropped (M.setHold)
local lastDir = nil         -- unit { x, y } of the player's motion at FAST_MS or more, else nil
-- The collision radius towards tile centre (cx, cy) from the player at p: lastRadius ahead of a fast car (within 45
-- degrees of its heading, and its own tile), COLLIDE_M min beside and behind it; lastRadius all round when slow.
function M.radiusToward(p, cx, cy, radius, dir)
  if not dir then return radius end
  local dx, dy = cx - p.x, cy - p.y
  local d = math.sqrt(dx * dx + dy * dy)
  if d <= M.TILE_M or (dx * dir.x + dy * dir.y) >= 0.7071 * d then return radius end
  return math.min(radius, M.COLLIDE_M[1])
end
-- Is tile t's centre within the collision radius of the player? (no car or no centre: yes)
function M.collideNear(t, extra)
  if not t or not t.cx then return true end
  local veh = getPlayerVehicle and getPlayerVehicle(0)
  if not veh then return true end
  local r = M.radiusToward(veh:getPosition(), t.cx, t.cy, lastRadius, lastDir)
  if M.nearPlayer(vec3(t.cx, t.cy, 0), r + (extra or 0) + M.TILE_M * 0.71) then return true end
  -- or near another vehicle's collision island (collisionfoci.lua: other players' cars, traffic)
  return #foci > 0 and collisionfoci.near(foci, t.cx, t.cy, (extra or 0) + M.TILE_M * 0.71) or false
end

-- Other vehicles' collision foci (collisionfoci.lua, through core.lua): their tiles' colliders are made too.
function M.setFoci(f) foci = f or {} end

-- Every COLLIDE_CHECK_S: parked colliders whose tile came near queued; far tiles' colliders dropped.
local function updateCollideRange()
  if sinceCollideCheck() < M.COLLIDE_CHECK_S * 1000 then return end
  sinceCollideCheck = stopwatch.new()
  local veh = getPlayerVehicle and getPlayerVehicle(0)
  local v = veh and veh.getVelocity and veh:getVelocity()
  local sp = v and math.sqrt(v.x * v.x + v.y * v.y) or 0
  lastRadius = M.collideRadius(sp)
  lastSpeed = sp
  lastDir = sp >= M.FAST_MS and { x = v.x / sp, y = v.y / sp } or nil
  if #parked > 0 then
    local keep = {}
    for _, q in ipairs(parked) do
      local t = tiles[q.key]
      if t then
        if M.collideNear(t) then walkQ[#walkQ + 1] = q else keep[#keep + 1] = q end
      end
    end
    parked = keep
  end
  for key, t in pairs(tiles) do
    if t.cq and not M.collideNear(t, M.UNCOLLIDE_M) then
      for what, q in pairs(t.cq) do
        if t[what] then deleteQ[#deleteQ + 1] = t[what]; t[what] = nil end
        if not t.ghost then parked[#parked + 1] = q end   -- a ghost's queue entries are the old frame's
      end
      t.cq = nil
    end
  end
end

local function isCollider(o)
  local c = o.class or (o.getClassName and o:getClassName())
  if c == 'MeshRoad' then return true end
  return c == 'TSStatic' and o:getField('collisionType', 0) ~= 'None'
end

-- collide: true ('Visible Mesh Final'), a collision type name, or false / nil (render only)
-- The last createWalk's steps (ms): the object made with its fields, registered (the shape loaded), added to the
-- scene (a collider: into the physics). A unit of SLOW_UNIT_MS or more is logged with them (unitInfo): a 190 KB
-- building collision mesh took 1.3 s on Jeff's drive 2026-10-29 and nothing said which step.
local walkPhases = { 0, 0, 0 }
M.SLOW_UNIT_MS = 30
local function createWalk(walk, collide)
  local t0 = now()
  local o = createObject('TSStatic')
  o:setField('shapeName', 0, walk.shape)
  -- 'Visible Mesh Final' (the engine's own suggestion: 'Performance: Mesh is using visible collision ... switch the
  -- mesh to Visible Mesh Final'); the same collision (in-game rays, 2026-10-17)
  o:setField('collisionType', 0, type(collide) == 'string' and collide or (collide and 'Visible Mesh Final' or 'None'))
  o:setField('decalType', 0, 'None')
  o:setField('dynamic', 0, 'true')    -- no redraw of the cached sun shadow map for each tile (farfield.lua spawn)
  o.canSave = false
  o:setPosition(vec3(walk.pos[1], walk.pos[2], walk.pos[3]))
  local t1 = now()
  o:registerObject('')
  if walk.rot then        -- (a geotile's part: placed from its tile's frame, M.geoWalk)
    local r = walk.rot
    o:setPosRot(walk.pos[1], walk.pos[2], walk.pos[3], r[1], r[2], r[3], r[4])
  end
  local t2 = now()
  group():addObject(o)
  walkPhases[1], walkPhases[2], walkPhases[3] = t1 - t0, t2 - t1, now() - t2
  return o:getID()
end

-- A slow unit's detail: createWalk's steps, the shape file's size and how long ago it was written (FS:stat).
local function unitInfo(path)
  local parts = { string.format('made %d, registered %d, added %d ms', math.floor(walkPhases[1] + 0.5),
    math.floor(walkPhases[2] + 0.5), math.floor(walkPhases[3] + 0.5)) }
  local okS, st = pcall(function() return FS and FS.stat and FS:stat(path) or nil end)
  if okS and type(st) == 'table' then
    if tonumber(st.filesize) then parts[#parts + 1] = string.format('%d KB', math.floor(st.filesize / 1024 + 0.5)) end
    local mt, t = tonumber(st.modtime), os.time and os.time() or nil
    if mt and t and math.abs(t - mt) < 86400 then parts[#parts + 1] = string.format('written %d s ago', t - mt) end
  end
  return table.concat(parts, ', ')
end

local function createChunk(chunk)
  local r = createObject('DecalRoad')
  for _, f in ipairs(M.fieldsFor(chunk)) do r:setField(f[1], 0, f[2]) end
  r.canSave = false
  r:registerObject('')
  group():addObject(r)
  for _, n in ipairs(chunk.n) do r:insertNodeNoRegen(vec3(n[1], n[2], n[3]), chunk.w, -1) end
  r:regenerate()
  return r:getId()
end

-- Road geotiles (server --geo-tiles on, hello.roadGeo = M.ROAD_GEO; .claude/work/geotiles_plan.md phase 1c): a road
-- tile keyed 'g17_x_y' (geotile.lua) whose CONTENT (buildings and their collision mesh, decks, tunnels, rails, wires,
-- street furniture and guard rails, parking spots, gas stations, pump jacks, sound spaces) is in the tile's own frame
-- and belongs to no anchor: it stays across rebases and teleports near, each shape placed into the current anchor's
-- world at its pivot (setPosRot: geotile.placement, rigid; a tall part the rigid move would leave off comes as its
-- "exact variant" from the server, roads_exact), each row mapped exactly (geotile.apply). Its SKIN (the draped road
-- mesh, plazas, sidewalks and their collision) follows the anchor's 2 m lattice and height quantum: a skin per anchor
-- (roads_skin), the old one kept, moved and lifted q/2 + SKIN_LIFT_M (no poke-through on the new block) until the
-- new one is made. M.replace(anchor) re-places everything at a rebase (core.lua finishRebase): no ghosts, colliders
-- and slots kept.
M.ROAD_GEO = 1
M.SKIN_LIFT_M = 0.003
M.geoStats = { tiles = 0, kept = 0, replaced = 0, skins = 0, skinSwaps = 0, exact = 0, exactBase = 0 }
local geotile = require('beamngEarth/geotile')
local geodesy = require('beamngEarth/geodesy')
local anchorObj = nil       -- the current anchor (geodesy) the geotiles are placed into (M.update, onTile)
M.CONTENT_WHATS = { 'deck', 'bld', 'bld2', 'bld3', 'bld4', 'bldcol', 'wire', 'tunnel', 'rail', 'railcol' }
M.SKIN_WHATS = { 'road', 'walk', 'walkcol' }

local function geoKey(gkey) return 'g:' .. tostring(gkey) end
M.geoKey = geoKey

-- A geotile's placement of a part (its pivot) into anchor a: { pl = geotile.placement, xf = rigid transform, q }.
local function partPlacement(t, a, pivot)
  local pl = geotile.placement(t.frame, a, 0, 0, pivot or 0)
  local xf = geodesy.transformOf(pl.r, pl.t)
  local qx, qy, qz, qw = xf:beamngQuat()
  return { pl = pl, xf = xf, q = { qx, qy, qz, qw }, inv = xf:inverse() }
end

-- Pure-ish: tile t's placements into anchor a (each part's at its pivot, the rows' at the tile's), its centre.
function M.placeGeoTile(t, a)
  t.anchorObj = a
  t.pls = {}
  for what, p in pairs(t.parts or {}) do t.pls[what] = partPlacement(t, a, p.pivot) end
  t.rowPl = partPlacement(t, a, t.rowPivot or 0)
  local cx, cy = geotile.apply(t.rowPl.pl, 0, 0, t.rowPivot or 0)
  t.cx, t.cy = cx, cy
end

-- Rows of a road tile's content mapped into its placement (exact: geotile.apply with the shear): parking spots
-- [x, y, z, heading], pump jacks [x, y, z, yaw], sound rows (flat, 8 a row: kind, x, y, z, l, w, h, yaw rad),
-- station records (props / areas pos + yaw, area icons), furniture rows [group, x, y, z, yaw, ...] (the lamps).
local function mapPt(pl, p)
  local x, y, z = geotile.apply(pl, p[1], p[2], p[3])
  return { x, y, z }
end
function M.mapRows(pl, rows, xi, yawi, rad)
  if type(rows) ~= 'table' then return rows end
  local dyaw = geotile.yawOf(pl)
  if rad then dyaw = math.rad(dyaw) end
  local out = {}
  for i, r in ipairs(rows) do
    if type(r) == 'table' then
      local n = {}
      for k, v in pairs(r) do n[k] = v end
      if type(r[xi]) == 'number' and type(r[xi + 1]) == 'number' and type(r[xi + 2]) == 'number' then
        n[xi], n[xi + 1], n[xi + 2] = geotile.apply(pl, r[xi], r[xi + 1], r[xi + 2])
      end
      if yawi and type(r[yawi]) == 'number' then n[yawi] = r[yawi] + dyaw end
      out[i] = n
    else
      out[i] = r
    end
  end
  return out
end
function M.mapFlat(pl, flat, stride, xi, yawi)
  if type(flat) ~= 'table' then return flat end
  local out, dyaw = {}, math.rad(geotile.yawOf(pl))
  for i = 1, #flat do out[i] = flat[i] end
  for b = 0, #flat - stride, stride do
    out[b + xi], out[b + xi + 1], out[b + xi + 2] = geotile.apply(pl, flat[b + xi], flat[b + xi + 1], flat[b + xi + 2])
    if yawi then out[b + yawi] = flat[b + yawi] + dyaw end
  end
  return out
end
function M.mapStations(pl, recs)
  if type(recs) ~= 'table' then return recs end
  local dyaw = geotile.yawOf(pl)
  local out = {}
  for i, r in ipairs(recs) do
    local n = {}
    for k, v in pairs(r) do n[k] = v end
    n.props, n.areas = {}, {}
    for j, p in ipairs(r.props or {}) do
      local q = {}
      for k, v in pairs(p) do q[k] = v end
      if type(p.pos) == 'table' then q.pos = mapPt(pl, p.pos) end
      if type(p.yaw) == 'number' then q.yaw = p.yaw + dyaw end
      n.props[j] = q
    end
    for j, a in ipairs(r.areas or {}) do
      local q = {}
      for k, v in pairs(a) do q[k] = v end
      if type(a.pos) == 'table' then q.pos = mapPt(pl, a.pos) end
      if type(a.icon) == 'table' then q.icon = mapPt(pl, a.icon) end
      if type(a.yaw) == 'number' then q.yaw = a.yaw + dyaw end
      n.areas[j] = q
    end
    out[i] = n
  end
  return out
end

-- The rows of tile t (key) handed to their modules, mapped into its current placement (made: replaced in place).
local function geoRows(key, t, data, replacing)
  local pl = t.rowPl.pl
  if type(data.props) == 'table' and type(data.props.items) == 'table' then   -- street lights, poles (trees.lua)
    -- (at a rebase trees.lua places its own geotiles: trees.transform)
    if not replacing then
      require('beamngEarth/trees').onGeoRows('p' .. t.gkey, t.gkey, data.props.items, data.props.groups, nil,
        t.frame, t.rowPivot or 0, t.anchorObj)
    end
    require('beamngEarth/nightlights').onRows(key, M.mapRows(pl, data.props.items, 2, 5), data.props.groups)
    t.props = true
  end
  if type(data.guardrails) == 'table' and type(data.guardrails.items) == 'table' and #data.guardrails.items > 0 then
    if not replacing then
      require('beamngEarth/trees').onGeoRows('g' .. t.gkey, t.gkey, data.guardrails.items, data.guardrails.groups,
        17, t.frame, t.rowPivot or 0, t.anchorObj)
    end
    t.guardrails = true
    t.guardrailRows = #data.guardrails.items
  end
  if type(data.park) == 'table' and data.park[1] then
    require('beamngEarth/parking').onRows(t.anchor, key, M.mapRows(pl, data.park, 1, 4))
    t.park = true
  end
  if type(data.fuel) == 'table' and data.fuel[1] then
    require('beamngEarth/gasstations').onRows(t.anchor, key, M.mapStations(pl, data.fuel))
    t.fuel = true
  end
  if type(data.wells) == 'table' and data.wells[1] then
    local pj = require('beamngEarth/pumpjacks')
    local rows = M.mapRows(pl, data.wells, 1, 4)
    if t.wells and pj.placeRows then pj.placeRows(key, rows) else pj.onRows(key, rows) end
    t.wells = true
  end
  if type(data.sound) == 'table' and data.sound[1] then
    local amb = require('beamngEarth/ambience')
    local flat = M.mapFlat(pl, data.sound, 8, 2, 8)
    if t.sound and amb.placeSpaces then amb.placeSpaces(key, flat) else amb.onSpaces(key, flat) end
    t.sound = true
  end
end

-- The mesh parts of geotile t queued (each its placement's, made by update); t.exactShape[what]: its exact variant.
local function queueGeoParts(key, t, data)
  local lean = t.parts.bldcol ~= nil
  local leanRail = t.parts.railcol ~= nil
  for _, what in ipairs(M.CONTENT_WHATS) do
    if t.parts[what] then
      local q = { key = key, what = what, tile = t, geo = true,
        renderOnly = what == 'wire' or (lean and what:sub(1, 3) == 'bld' and what ~= 'bldcol') or (leanRail and what == 'rail') }
      if not q.renderOnly then t.colWant[what] = true end
      if q.renderOnly or M.collideNear(t) then walkQ[#walkQ + 1] = q else parked[#parked + 1] = q end
    end
  end
end

-- The shape a geotile's part shows now: its exact variant for this anchor, else its base.
local function partShape(t, what)
  return (t.exactShape and t.exactShape[what]) or (t.parts[what] and t.parts[what].shape)
end

-- A geotile's part as createWalk takes it: its shape, placed (position and rotation).
function M.geoWalk(t, what)
  local p = t.parts[what]
  local pp = t.pls and t.pls[what]
  if not p or not pp then return nil end
  local x, y, z = pp.xf:apply(p.pos[1], p.pos[2], p.pos[3])
  return { shape = partShape(t, what), pos = { x, y, z }, rot = pp.q }
end

-- Server message `roads_tile` for a geotile key: its content (data: the JSON; frame, h, slots, exact).
function M.onGeoTile(msg, currentAnchor, a)
  a = a or anchorObj
  if not a then return false end
  anchorObj = a
  local ok, data = true, msg.data
  if type(data) ~= 'table' then ok, data = pcall(jsonDecode, readFile(msg.path) or '') end
  if not ok or type(data) ~= 'table' then
    lastError = 'unreadable road geotile ' .. tostring(msg.path)
    return false
  end
  local key = geoKey(msg.key)
  local old = tiles[key]
  local h = msg.h or data.h
  if old and old.geo and h and old.h == h then
    -- the same content again (a server restart): its objects stay; the new message's slots are not used
    if type(msg.slots) == 'table' then
      for w in pairs(msg.slots) do slotDrop(msgSlot(msg, w)) end
    end
    old.anchor, old.orphan = currentAnchor, nil
    M.geoStats.kept = M.geoStats.kept + 1
    return true
  end
  if old then dropTile(key) end         -- (new content: its skin comes with it, roads.GeoTracker)
  local frame = geotile.frameOf({ frame = msg.frame or data.frame, key = msg.key })
  if not frame then lastError = 'road geotile without a frame: ' .. tostring(msg.key); return false end
  local t = { anchor = currentAnchor, geo = true, gkey = msg.key, frame = frame, h = h, objs = {}, queued = 0,
    colliders = {}, slots = {}, at = now(), colWant = {}, parts = {}, exactShape = {}, exactOf = {} }
  local cols = type(data.colliders) == 'table' and data.colliders or {}
  local pivots, np = 0, 0
  for _, what in ipairs(M.CONTENT_WHATS) do
    local e = data[what]
    if type(e) == 'table' and e.shape and type(e.pos) == 'table' then
      local sl = msgSlot(msg, what)
      t.parts[what] = { shape = sl and sl.path or e.shape, pos = e.pos, pivot = tonumber(e.pivot) or 0,
        relief = tonumber(e.relief) or 0 }
      if sl then t.slots[what] = sl end
      if cols[what] then t.colliders[what] = cols[what] end
      pivots, np = pivots + (tonumber(e.pivot) or 0), np + 1
    else
      slotDrop(msgSlot(msg, what))
    end
  end
  for what, path in pairs(type(msg.exact) == 'table' and msg.exact or {}) do
    if t.parts[what] and type(path) == 'string' then
      local sl = msgSlot(msg, 'x:' .. what)
      t.exactShape[what] = sl and sl.path or path
      t.exactOf[what] = currentAnchor
      if sl then t.slots['x:' .. what] = sl end
    end
  end
  t.rowPivot = np > 0 and pivots / np or 0
  t.data = data
  M.placeGeoTile(t, a)
  tiles[key] = t
  sinceChange = stopwatch.new()
  geoRows(key, t, data)
  queueGeoParts(key, t, data)
  M.geoStats.tiles = M.geoStats.tiles + 1
  return true
end

-- Server message `roads_skin` {anchorId, key, data / path, slots}: the draped surfaces of a geotile for this anchor.
-- The old skin's objects stay until the new ones are made (per part; what the new skin lacks: with its road mesh).
function M.onSkin(msg, currentAnchor)
  local t = tiles[geoKey(msg.key)]
  local function release()
    if type(msg.slots) == 'table' then for w in pairs(msg.slots) do slotDrop(msgSlot(msg, w)) end end
  end
  if not t or msg.anchorId ~= currentAnchor then release(); return false end
  local ok, data = true, msg.data
  if type(data) ~= 'table' then ok, data = pcall(jsonDecode, readFile(msg.path) or '') end
  if not ok or type(data) ~= 'table' then release(); lastError = 'unreadable road skin ' .. tostring(msg.path); return false end
  -- the previous skin: its objects kept as the old ones (swapped as the new are made), its queue entries gone
  t.oldSkin = t.oldSkin or {}
  for _, w in ipairs(M.SKIN_WHATS) do
    if t[w] then
      if t.oldSkin[w] then deleteQ[#deleteQ + 1] = t.oldSkin[w] end
      t.oldSkin[w] = t[w]
      t[w] = nil
    end
    if t.cq then t.cq[w] = nil end
    if t.slots[w] then t.oldSkin['s:' .. w] = t.slots[w]; t.slots[w] = nil end
  end
  for _, id in ipairs(t.objs) do deleteQ[#deleteQ + 1] = id end
  t.objs, t.queued = {}, 0
  local function purge(list)
    local keep = {}
    for _, q in ipairs(list) do if not (q.tile == t and q.skin) then keep[#keep + 1] = q end end
    return keep
  end
  walkQ, parked, held, createQ = purge(walkQ), purge(parked), purge(held), purge(createQ)
  local cols = type(data.colliders) == 'table' and data.colliders or {}
  t.colliders.walk, t.colliders.walkcol = cols.walk, cols.walkcol
  t.colWant.walk, t.colWant.walkcol = nil, nil
  local pv = type(data.pivot) == 'table' and data.pivot or {}
  t.skin = { anchor = msg.anchorId, anchorObj = anchorObj, pending = 0,
    pivot = { tonumber(pv[1]) or t.cx or 0, tonumber(pv[2]) or t.cy or 0, tonumber(pv[3]) or 0 }, q = tonumber(data.q) or 0 }
  local road = type(data.road) == 'table' and data.road.shape and type(data.road.pos) == 'table' and data.road
  local leanWalk = type(data.walkcol) == 'table' and data.walkcol.shape ~= nil
  for _, what in ipairs(M.SKIN_WHATS) do
    local w = data[what]
    local sl = msgSlot(msg, what)
    if type(w) == 'table' and w.shape and type(w.pos) == 'table' then
      if sl then w = { shape = sl.path, pos = w.pos }; t.slots[what] = sl end
      t.skin[what] = { shape = w.shape, pos = w.pos }
      local q = { key = geoKey(msg.key), walk = w, what = what, tile = t, skin = true,
        renderOnly = what == 'road' or (leanWalk and what == 'walk') }
      if not q.renderOnly then t.colWant[what] = true end
      t.skin.pending = t.skin.pending + 1
      if q.renderOnly or M.collideNear(t) then walkQ[#walkQ + 1] = q else parked[#parked + 1] = q end
    else
      slotDrop(sl)
    end
  end
  if not road and type(data.chunks) == 'table' then       -- (no road mesh: DecalRoads, as an anchor tile's)
    for _, c in ipairs(data.chunks) do createQ[#createQ + 1] = { key = geoKey(msg.key), chunk = c, skin = true, tile = t } end
    t.queued = #data.chunks
  end
  if t.skin.pending == 0 then M.dropOldSkin(t) end
  M.geoStats.skins = M.geoStats.skins + 1
  sinceChange = stopwatch.new()
  return true
end

-- A geotile's old skin objects go (all, or only part what: its new one is made).
function M.dropOldSkin(t, what)
  if not t.oldSkin then return end
  local function drop(w)
    if t.oldSkin[w] then deleteQ[#deleteQ + 1] = t.oldSkin[w]; t.oldSkin[w] = nil; M.geoStats.skinSwaps = M.geoStats.skinSwaps + 1 end
    if t.oldSkin['s:' .. w] then slotDrop(t.oldSkin['s:' .. w]); t.oldSkin['s:' .. w] = nil end
  end
  if what then drop(what) else for _, w in ipairs(M.SKIN_WHATS) do drop(w) end end
  if next(t.oldSkin) == nil then t.oldSkin = nil end
end

-- Server message `roads_exact` {anchorId, key, parts {what: path | false}, slots}: parts shown as their exact variant
-- for this anchor (false: the base shape again), each swapped once its new object is made.
function M.onExact(msg, currentAnchor)
  local t = tiles[geoKey(msg.key)]
  if not t or msg.anchorId ~= currentAnchor or type(msg.parts) ~= 'table' then
    if type(msg.slots) == 'table' then for w in pairs(msg.slots) do slotDrop(msgSlot(msg, w)) end end
    return false
  end
  for what, path in pairs(msg.parts) do
    if t.parts[what] then
      local sl = msgSlot(msg, what)
      if t.slots['x:' .. what] then t.oldPartSlot = t.oldPartSlot or {}; t.oldPartSlot[what] = t.slots['x:' .. what] end
      t.slots['x:' .. what] = sl
      if path then
        t.exactShape[what] = sl and sl.path or path
        t.exactOf[what] = msg.anchorId
        M.geoStats.exact = M.geoStats.exact + 1
      else
        t.exactShape[what], t.exactOf[what] = nil, nil
        M.geoStats.exactBase = M.geoStats.exactBase + 1
      end
      if t[what] then         -- made: the new shape made beside it, the old one deleted then (update)
        t.oldPart = t.oldPart or {}
        if t.oldPart[what] then deleteQ[#deleteQ + 1] = t.oldPart[what] end
        t.oldPart[what] = t[what]
        t[what] = nil
        if t.cq then t.cq[what] = nil end
        local keep = {}
        for _, q in ipairs(walkQ) do if not (q.tile == t and q.what == what) then keep[#keep + 1] = q end end
        walkQ = keep
        local lean = t.parts.bldcol ~= nil
        local q = { key = geoKey(msg.key), what = what, tile = t, geo = true, swap = true,
          renderOnly = what == 'wire' or (lean and what:sub(1, 3) == 'bld' and what ~= 'bldcol')
            or (t.parts.railcol ~= nil and what == 'rail') }
        walkQ[#walkQ + 1] = q
      end
    end
  end
  sinceChange = stopwatch.new()
  return true
end

-- A rebase / teleport near (core.lua): every geotile placed into anchor a (aid): its made parts moved (setPosRot),
-- its rows mapped again for their modules, its skin moved and lifted until the new anchor's comes (onSkin). Returns
-- the tiles moved.
function M.replace(a, aid)
  if not a then return 0 end
  anchorObj = a
  local n = 0
  for key, t in pairs(tiles) do
    if t.geo then
      t.anchor = aid
      M.placeGeoTile(t, a)
      for what, pp in pairs(t.pls) do
        local p = t.parts[what]
        local x, y, z = pp.xf:apply(p.pos[1], p.pos[2], p.pos[3])
        for _, id in ipairs({ t[what], t.oldPart and t.oldPart[what] or false }) do
          local o = id and scenetree.findObjectById(id)
          if o then o:setPosRot(x, y, z, pp.q[1], pp.q[2], pp.q[3], pp.q[4]) end
        end
      end
      -- the skin (its anchor's world): moved rigidly at its pivot into this one, lifted over the new block's quantum
      local sk = t.skin
      if sk and sk.anchorObj and sk.anchor ~= aid then
        local pv = sk.pivot
        local xf = sk.anchorObj:worldTransformTo(a, pv[1], pv[2], pv[3])
        local qx, qy, qz, qw = xf:beamngQuat()
        local lift = (sk.q or 0) / 2 + M.SKIN_LIFT_M
        for _, w in ipairs(M.SKIN_WHATS) do
          local e = sk[w]
          local o = e and t[w] and scenetree.findObjectById(t[w])
          if o then
            local x, y, z = xf:apply(e.pos[1], e.pos[2], e.pos[3])
            o:setPosRot(x, y, z + lift, qx, qy, qz, qw)
          end
        end
        for _, id in ipairs(t.objs) do deleteQ[#deleteQ + 1] = id end      -- (DecalRoads cannot turn)
        t.objs, t.queued = {}, 0
        sk.stale = true
      end
      if t.data then geoRows(key, t, t.data, true) end
      n = n + 1
    end
  end
  -- the far geotiles: their made parts moved (their lamps are trees.lua geotiles: placed by trees.transform / reanchor)
  for _, f in pairs(far) do
    if f.geo then
      f.anchor = aid
      for _, p in pairs(f.parts) do
        M._farPartPlace(f, p, a)          -- (defined below, with the far geotiles)
        local o = p.id and scenetree.findObjectById(p.id)
        if o then o:setPosRot(p.at[1], p.at[2], p.at[3], p.rot[1], p.rot[2], p.rot[3], p.rot[4]) end
      end
      M.geoStats.farReplaced = (M.geoStats.farReplaced or 0) + 1
    end
  end
  -- queued entries of the skins of other anchors go (their positions are those anchors')
  local function keep(list)
    local out = {}
    for _, q in ipairs(list) do
      if not (q.skin and q.tile and q.tile.skin and q.tile.skin.anchor ~= aid) then out[#out + 1] = q end
    end
    return out
  end
  walkQ, parked, held, createQ = keep(walkQ), keep(parked), keep(held), keep(createQ)
  if n > 0 then collisionDirty = true; sinceChange = stopwatch.new() end
  M.geoStats.replaced = M.geoStats.replaced + n
  return n
end

-- The geotile keys whose quads in the current anchor's world come within r of (x, y).
function M.geoKeysNear(x, y, r)
  local a = anchorObj
  if not a then return {} end
  local la, lo = a:worldToGeodetic(x, y, 0)
  local kx, ky = geotile.tileOf(la, lo)
  local side = 40075016.686 * math.max(math.cos(math.rad(la)), 0.01) / 2 ^ geotile.Z
  local rings = math.ceil(r / side) + 1
  local out = {}
  for dy = -rings, rings do
    for dx = -rings, rings do
      local gx, gy = kx + dx, ky + dy
      if geotile.quadDist(geotile.quad(a, geotile.Z, gx, gy), x, y) <= r then
        out[#out + 1] = geotile.key(gx, gy)
      end
    end
  end
  return out
end

-- The geotile under (x, y) of the current anchor's world, or nil.
function M.geoKeyAt(x, y)
  local a = anchorObj
  if not a then return nil end
  local la, lo = a:worldToGeodetic(x, y, 0)
  return geotile.key(geotile.tileOf(la, lo))
end

-- Is geotile t made for anchor aid: its content's colliding parts made (busy: tile keys with meshes queued), its skin
-- this anchor's and made?
local function geoMade(t, aid, busy)
  if not t or not t.geo or busy[geoKey(t.gkey)] then return false end
  if not t.skin or t.skin.anchor ~= aid or t.skin.pending > 0 or t.queued > 0 then return false end
  return true
end
M._geoMade = geoMade

-- Server message `roads_tile` {anchorId, key, path, n, data (the file's JSON, optional), slots}. A geotile key
-- (geotile.lua): onGeoTile (a: the current anchor, geodesy).
function M.onTile(msg, currentAnchor, a)
  if not M.enabled then return false end
  switchAnchor(currentAnchor)
  if geotile.isKey(msg.key) then return M.onGeoTile(msg, currentAnchor, a) end
  if msg.anchorId ~= currentAnchor then return false end
  local key = tileKey(msg.anchorId, msg.key)
  if tiles[key] then dropTile(key) end
  -- the file's JSON in the message (server staging.inline_json: Windows' first read of a file the server just wrote took
  -- up to 0.5 s in the frame), else the file
  local ok, data = true, msg.data
  if type(data) ~= 'table' then ok, data = pcall(jsonDecode, readFile(msg.path) or '') end
  if not ok or type(data) ~= 'table' or type(data.chunks) ~= 'table' then
    lastError = 'unreadable road tile ' .. tostring(msg.path)
    return false
  end
  -- pooled shape files: the server's slot paths instead of the tile's own files
  local slots = {}
  for _, what in ipairs(SLOT_WHATS) do
    local sl = msgSlot(msg, what)
    if sl then
      if type(data[what]) == 'table' and type(data[what].pos) == 'table' then
        data[what] = { shape = sl.path, pos = data[what].pos }
        slots[what] = sl
      else
        slotDrop(sl)                -- nothing will load it
      end
    end
  end
  local road = type(data.road) == 'table' and data.road.shape and type(data.road.pos) == 'table' and data.road
  tiles[key] = { anchor = msg.anchorId, objs = {}, queued = road and 0 or #data.chunks,
    colliders = type(data.colliders) == 'table' and data.colliders or {}, slots = slots, at = now(), colWant = {} }
  sinceChange = stopwatch.new()
  if road then                -- the tile's road mesh replaces its DecalRoads (server/roadmesh.py)
    walkQ[#walkQ + 1] = { key = key, walk = road, what = 'road', tile = tiles[key] }
  else
    for _, c in ipairs(data.chunks) do createQ[#createQ + 1] = { key = key, chunk = c } end
  end
  if type(data.props) == 'table' and type(data.props.items) == 'table' then   -- street lights, poles (trees.lua)
    require('beamngEarth/trees').onRows(msg.anchorId, 'p' .. tostring(msg.key), data.props.items, data.props.groups)
    require('beamngEarth/nightlights').onRows(key, data.props.items, data.props.groups)   -- (the lamps' light)
    tiles[key].props = true
  end
  if type(data.guardrails) == 'table' and type(data.guardrails.items) == 'table' and #data.guardrails.items > 0 then
    require('beamngEarth/trees').onRows(msg.anchorId, 'g' .. tostring(msg.key),
      data.guardrails.items, data.guardrails.groups, 17)
    tiles[key].guardrails = true
    tiles[key].guardrailRows = #data.guardrails.items
  end
  if type(data.park) == 'table' and data.park[1] then      -- curbside parking spots (parking.lua)
    require('beamngEarth/parking').onRows(msg.anchorId, key, data.park)
    tiles[key].park = true
  end
  if type(data.fuel) == 'table' and data.fuel[1] then      -- gas stations: pumps, refuel areas (gasstations.lua)
    require('beamngEarth/gasstations').onRows(msg.anchorId, key, data.fuel)
    tiles[key].fuel = true
  end
  if type(data.wells) == 'table' and data.wells[1] then    -- oil wells' pump jacks (pumpjacks.lua)
    require('beamngEarth/pumpjacks').onRows(key, data.wells)
    tiles[key].wells = true
  end
  if type(data.sound) == 'table' and data.sound[1] then    -- tunnels' and bridges' sound spaces (ambience.lua)
    require('beamngEarth/ambience').onSpaces(key, data.sound)
    tiles[key].sound = true
  end
  if type(data.wire) == 'table' and data.wire.shape and type(data.wire.pos) == 'table' then    -- pole wires
    walkQ[#walkQ + 1] = { key = key, walk = data.wire, what = 'wire', tile = tiles[key] }
  end
  -- highway signs (server guidesigns.py): the drawn panels, gantries and delineators render-only, their posts' and
  -- legs' lean collision mesh ("signcol") with the colliders below
  if type(data.sign) == 'table' and data.sign.shape and type(data.sign.pos) == 'table' then
    walkQ[#walkQ + 1] = { key = key, walk = data.sign, what = 'sign', tile = tiles[key], renderOnly = true }
  end
  -- a sign tile (key "s<tx>_<ty>"): its render-only sign shapes, kept in signData for setSignsEnabled
  tiles[key].signOnly = tostring(msg.key):match('^s%-?%d+_') ~= nil
  tiles[key].signData = {}
  for _, what in ipairs(M.SIGN_WHATS) do
    local sign = data[what]
    if type(sign) == 'table' and sign.shape and type(sign.pos) == 'table' then
      tiles[key].signData[what] = sign
      if M.signsEnabled then
        walkQ[#walkQ + 1] = { key = key, walk = sign, what = what, tile = tiles[key], renderOnly = true }
      end
    end
  end
  -- sidewalks, bridge decks, buildings (split in parts under the engine's 16-bit index limit), the buildings'
  -- lean collision mesh: colliders only near the car (M.COLLIDE_M); with a bldcol the drawn buildings only render
  local tx, ty = tostring(msg.key):match('^s?(%-?%d+)_(%-?%d+)$')
  tiles[key].cx = tx and (tonumber(tx) + 0.5) * M.TILE_M or nil
  tiles[key].cy = ty and (tonumber(ty) + 0.5) * M.TILE_M or nil
  -- a lean collision mesh ("bldcol", "walkcol", "railcol": server cdae collision_only) makes the drawn ones render-only
  -- (railcol: the track's bed and decks without its rails and sleepers, server railways.collision_mesh; mpsteppe's, from
  -- Madderose's PR #41)
  local lean = type(data.bldcol) == 'table' and data.bldcol.shape ~= nil
  local leanWalk = type(data.walkcol) == 'table' and data.walkcol.shape ~= nil
  local leanRail = type(data.railcol) == 'table' and data.railcol.shape ~= nil
  -- tunnels (server roadmesh.tunnel: tubes and portals) and railway tracks (server railways.py) collide like the decks
  for _, what in ipairs(COLLIDER_WHATS) do
    local w = data[what]
    if type(w) == 'table' and w.shape and type(w.pos) == 'table' then
      local kind = kindOf(what)
      local q = { key = key, walk = w, what = what, tile = tiles[key],
        renderOnly = (lean and kind:sub(1, 3) == 'bld' and kind ~= 'bldcol') or (leanWalk and kind == 'walk')
          or (leanRail and kind == 'rail') }
      if not q.renderOnly then tiles[key].colWant[what] = true end
      if q.renderOnly or M.collideNear(tiles[key]) then walkQ[#walkQ + 1] = q else parked[#parked + 1] = q end
    end
  end
  return true
end

-- Server message `bldfar_tile` {anchorId, key, path, n}: a far tile's buildings (render-only, the lite mesh);
-- shown only while the road tile of the same square has no building mesh of its own. `bldsky_tile` the same for a
-- skyline tile (server buildings.skyline_tile: SKY_UNIT x SKY_UNIT road tiles, key "ux_uy"), shown only while not all
-- of its squares' far tiles are here (they come as whole groups: no hole while they arrive, none left over after).
M.SKY_UNIT = 4
-- `brfar_tile` (kind 'B'): a far bridge tile's decks (server buildings.far_bridge_tile), shown only while the road
-- tile of the same square has no deck or road mesh of its own.
-- `grfar_tile` (kind 'G'): a tile's grass cards past the 3D grass (server grasscards.py), shown while M.grassCards
-- (the setting grassCards) is on. Cards are hidden while the player or the camera comes near them: a card up close is
-- a flat cross, and may lean over a road's edge (the grass mask has 2 m texels); there the 3D grass is. A tile comes
-- whole, and once near again as its squares (`grsub_tile` {anchorId, key, path, n, data {subSize, subs}, slots "s<i>"},
-- 64 m; `grsub_drop` when away): within GRASS_SUB_M of the player or camera its squares are drawn instead, each hidden
-- within GRASS_SUB_NEAR_M of its own square (the whole tile shows until all are made). Without squares (an older
-- server, or not here yet) the whole tile hides within GRASS_CARD_NEAR_M: in Jeff's drive (2026-10-29) 256 m of cards
-- out to ~300 m from the car came and went at once, past the 3D short grass's 80 m: "grass still pops in a lot weirdly".
M.grassCards = true
M.GRASS_CARD_NEAR_M = 48
M.GRASS_SUB_M = 128         -- m (Chebyshev) from a tile's square: its squares drawn (the server sends them within 192 m)
M.GRASS_SUB_NEAR_M = 24     -- m from a square: hidden (the 3D short grass is full to 40 m; a hole of 24-88 m at most)
function M.onFarTile(msg, currentAnchor, kind)
  if kind == true then kind = 'S' end
  local sky = kind == 'S'
  if not M.enabled then return false end
  if geotile.isKey(msg.key) then return M.onFarGeo(msg, currentAnchor, kind) end
  switchAnchor(currentAnchor)
  if msg.anchorId ~= currentAnchor then return false end
  local ok, data = true, msg.data              -- (in the message, else the file: as onTile)
  if type(data) ~= 'table' then ok, data = pcall(jsonDecode, readFile(msg.path) or '') end
  if not ok or type(data) ~= 'table' then lastError = 'unreadable far building tile ' .. tostring(msg.path); return false end
  local key = tileKey(msg.anchorId, (kind or '') .. msg.key)
  if not kind then farSeen[key] = true end
  local old = far[key]
  if old then farLamps(key, old, false) end
  if old and old.id then deleteQ[#deleteQ + 1] = old.id end
  if old then grassSubsDelete(old, true) end
  if old then slotDrop(old.slot) end
  far[key] = nil
  local sl = msgSlot(msg, 'shape')
  if data.shape and type(data.pos) == 'table' then
    local ux, uy = nil, nil
    if sky then ux, uy = string.match(tostring(msg.key), '^(-?%d+)_(-?%d+)$') end
    far[key] = { anchor = msg.anchorId, shape = sl and sl.path or data.shape, pos = data.pos, slot = sl,
                 sky = sky and { tonumber(ux), tonumber(uy) } or nil,
                 deckOf = kind == 'B' and tileKey(msg.anchorId, msg.key) or nil, grass = kind == 'G' or nil,
                 lamps = type(data.lamps) == 'table' and #data.lamps > 0 and data.lamps or nil,
                 beacons = type(data.beacons) == 'table' and #data.beacons > 0 and data.beacons or nil }
    farQ[#farQ + 1] = key
  else
    slotDrop(sl)
  end
  return true
end

-- Server message `bldfar_drop` {anchorId, key} (`bldsky_drop` with sky).
function M.onFarDrop(msg, kind)
  if kind == true then kind = 'S' end
  if geotile.isKey(msg.key) then return M.dropFarGeo(M.farGeoKey(kind or 'F', msg.key)) end
  local key = tileKey(msg.anchorId, (kind or '') .. msg.key)
  if not kind then farSeen[key] = nil end
  local f = far[key]
  if f and f.ghost then return end
  if f then farLamps(key, f, false) end
  if f and f.id then deleteQ[#deleteQ + 1] = f.id end
  if f then grassSubsDelete(f, true); slotDrop(f.slot) end
  far[key] = nil
end

-- Server message `grsub_tile`: the squares of grass card tile `key` (its far entry: kind 'G'), pooled shapes.
function M.onGrassSubs(msg, currentAnchor)
  if not M.enabled then return false end
  switchAnchor(currentAnchor)
  if msg.anchorId ~= currentAnchor then return false end
  local ok, data = true, msg.data
  if type(data) ~= 'table' then ok, data = pcall(jsonDecode, readFile(msg.path) or '') end
  local f = far[tileKey(msg.anchorId, 'G' .. msg.key)]
  local subs = {}
  if ok and type(data) == 'table' and type(data.subs) == 'table' then
    for i, e in ipairs(data.subs) do
      local sl = msgSlot(msg, 's' .. (i - 1))
      if type(e) == 'table' and e.shape and type(e.pos) == 'table' then
        subs[#subs + 1] = { shape = sl and sl.path or e.shape, pos = e.pos, slot = sl }
      else slotDrop(sl) end
    end
  end
  if not f or f.ghost or #subs == 0 or not tonumber(data.subSize) then     -- (nothing to draw them for)
    for _, sq in ipairs(subs) do slotDrop(sq.slot) end
    return false
  end
  grassSubsDelete(f, true)
  f.subs, f.subSize = subs, tonumber(data.subSize)
  return true
end

-- Server message `grsub_drop` {anchorId, key}: the tile's squares go (the whole tile stays).
function M.onGrassSubsDrop(msg)
  local f = far[tileKey(msg.anchorId, 'G' .. msg.key)]
  if f and not f.ghost then grassSubsDelete(f, true) end
end

-- A far tile is hidden while its square's road tile shows buildings (they are the same, colliding).
-- Pure: are all the far tiles of skyline square (ux, uy) of anchor aid here (seen: key -> true)?
function M.skyCovered(seen, aid, ux, uy)
  local n = M.SKY_UNIT
  for i = 0, n - 1 do
    for j = 0, n - 1 do
      if not seen[tileKey(aid, (ux * n + i) .. '_' .. (uy * n + j))] then return false end
    end
  end
  return true
end

-- Pure: m (Chebyshev) from the nearer of the player and camera (positions or nil) to the square of side size at pos (its
-- south-west corner); math.huge with neither.
function M.squareNearest(pos, size, player, view)
  local best = math.huge
  for _, p in ipairs({ player or false, view or false }) do
    if p and p.x and type(pos) == 'table' and pos[1] then
      best = math.min(best, math.max(pos[1] - p.x, p.x - (pos[1] + size), pos[2] - p.y, p.y - (pos[2] + size)))
    end
  end
  return best
end

-- Pure: is a grass card tile (or a square of one: size, near) at pos (its square's south-west corner) hidden, with the
-- player and camera at player and view (positions or nil)?
function M.grassCardHidden(on, pos, player, view, size, near)
  if not on then return true end
  return M.squareNearest(pos, size or M.TILE_M, player, view) < (near or M.GRASS_CARD_NEAR_M)
end

-- A grass card tile f (made) with squares: its squares queued, shown or hidden; is the whole tile hidden?
local function grassUpdate(key, f, player, view)
  if not f.subs then return M.grassCardHidden(M.grassCards, f.pos, player, view) end
  if f.ghost then return f.hidden or false end     -- (a rebase's: tile and squares as they were, until the new ones come)
  local d = M.squareNearest(f.pos, M.TILE_M, player, view)
  local want = M.grassCards and d < M.GRASS_SUB_M
  local ready = want
  for i, s in ipairs(f.subs) do
    if want and not s.id and not s.queued then
      s.queued = true
      grassQ[#grassQ + 1] = { key, i }
    end
    if not s.id then ready = false end
  end
  for _, s in ipairs(f.subs) do
    if s.id then
      local hide = not ready or M.grassCardHidden(M.grassCards, s.pos, player, view, f.subSize, M.GRASS_SUB_NEAR_M)
      if hide ~= (s.hidden or false) then
        local o = scenetree.findObjectById(s.id)
        if o then o:setHidden(hide) end
        s.hidden = hide
      end
    end
  end
  if ready then return true end
  return M.grassCardHidden(M.grassCards, f.pos, player, view)
end


-- Road geotiles (M.geo): a far tile's 256 m square (key "a:[B]tx_ty") is the road tiles' when every geotile within its
-- reach has its content here (buildings made, or none; decks: its deck made or none, and its skin's road mesh), the
-- keys found once per far tile (its anchor's world). With anchor far tiles (a server without hello.farGeo) a square at
-- the road tiles' edge shows its far buildings over the geotiles it overlaps; far geotiles (below) have no such seam.
local function geoSquareCovered(f, sq, deck)
  if not f.geoKeys then
    local tx, ty = tostring(sq):match('(%-?%d+)_(%-?%d+)$')
    if not tx or not anchorObj then return false end
    f.geoKeys = M.geoKeysNear((tonumber(tx) + 0.5) * M.TILE_M, (tonumber(ty) + 0.5) * M.TILE_M, M.TILE_M * 0.5)
  end
  if #f.geoKeys == 0 then return false end
  for _, k in ipairs(f.geoKeys) do
    local t = tiles[geoKey(k)]
    if not t then return false end
    if deck then
      if (t.parts.deck and not t.deck) or not t.road then return false end
    elseif t.parts.bld and not t.bld then
      return false
    end
  end
  return true
end

-- Far geotiles (server hello.farGeo == M.FAR_GEO: blockworker.far_geo; .claude/work/geotiles_plan.md phase 2): the far
-- buildings (kind 'F', key 'g17_x_y': the road geotiles' own grid), the skyline ('S', 'g15_x_y': 4 x 4 of them) and
-- the far bridges ('B', 'g15_x_y': a piece per road geotile under it), each made once in its tile's frame and kept
-- across rebases and teleports near: M.replace places its parts rigidly at their pivots into the new anchor (render-
-- only, nothing made again). One ownership rule by geotile key: a far buildings tile holds the very buildings the road
-- geotile of its key holds and hides once that one's are made; a far bridge piece hides once its road geotile's deck
-- and road mesh are made; a skyline tile while all 16 far buildings tiles of its square are here. Their street lamps
-- are trees.lua geotiles (one per road geotile key, placed and moved with the trees'), shown while their tile shows.
M.FAR_GEO = 1
M.farGeo = false
function M.setFarGeo(on) M.farGeo = on and true or false end
local function farGeoKey(kind, gkey) return tostring(kind) .. ':' .. tostring(gkey) end
M.farGeoKey = farGeoKey
M.FAR_GEO_ORPHAN_S = 120    -- a far geotile of an old server session not sent again goes after this long

-- A far geotile part's placement into anchor a: its position (p.at) and rotation (p.rot), rigid at its pivot.
local function farPartPlace(f, p, a)
  local pl = geotile.placement(f.frame, a, 0, 0, p.pivot or 0)
  local xf = geodesy.transformOf(pl.r, pl.t)
  local qx, qy, qz, qw = xf:beamngQuat()
  local x, y, z = xf:apply(p.pos[1], p.pos[2], p.pos[3])
  p.at, p.rot = { x, y, z }, { qx, qy, qz, qw }
end
M._farPartPlace = farPartPlace

-- A far geotile's lamps on / off: each road geotile key's rows a trees.lua geotile 'FL<kind><key>' (lampglow).
local function farGeoLamps(f, on)
  on = on and f.lamps ~= nil and anchorObj ~= nil
  if on == (f.lampOn == true) then return end
  local ok, trees = pcall(require, 'beamngEarth/trees')
  if not ok then return end
  f.lampOn = on or nil
  for k17, g in pairs(f.lamps or {}) do
    local tk = 'FL' .. f.kind .. tostring(k17)
    if on then
      local rows = {}
      for i, pt in ipairs(type(g) == 'table' and type(g.rows) == 'table' and g.rows or {}) do
        if type(pt) == 'table' and tonumber(pt[1]) and tonumber(pt[2]) and tonumber(pt[3]) then
          rows[#rows + 1] = { 0, pt[1], pt[2], pt[3], 0, 1, 1, ((i * 7919) % 997) / 997 * 0.19 }
        end
      end
      local fr = geotile.frameOf(k17)
      if #rows > 0 and fr then
        trees.onGeoRows(tk, k17, rows, M.FAR_LAMP_GROUPS, nil, fr, tonumber(g.pivot) or 0, anchorObj)
      end
    else
      trees.dropKey('g:' .. tk)
    end
  end
end

-- A far geotile goes (its objects, slots and lamps).
function M.dropFarGeo(key)
  local f = far[key]
  if not f or not f.geo then return false end
  farGeoLamps(f, false)
  for _, p in pairs(f.parts) do
    if p.id then deleteQ[#deleteQ + 1] = p.id end
    slotDrop(p.slot)
  end
  if f.kind == 'F' then farSeen[key] = nil end
  far[key] = nil
  return true
end

-- Server messages `bldfar_tile` / `bldsky_tile` / `brfar_tile` with a geotile key (kind nil / 'S' / 'B'): {key, frame,
-- path / data {shape, pos, pivot, lamps {key: {pivot, rows}}; brfar: parts {key: {shape, pos, pivot}}, h}, slots}.
-- The same content again (h: a server restart, a tile dropped and wanted again) keeps its objects.
function M.onFarGeo(msg, currentAnchor, kind)
  local k = kind or 'F'
  local function release(keep)
    if type(msg.slots) == 'table' then
      for w in pairs(msg.slots) do if not (keep and keep[w]) then slotDrop(msgSlot(msg, w)) end end
    end
  end
  local ok, data = true, msg.data
  if type(data) ~= 'table' then ok, data = pcall(jsonDecode, readFile(msg.path) or '') end
  if not ok or type(data) ~= 'table' then
    release()
    lastError = 'unreadable far geotile ' .. tostring(msg.path)
    return false
  end
  local key = farGeoKey(k, msg.key)
  if k == 'F' then farSeen[key] = true end
  local old = far[key]
  local h = data.h
  if old and old.geo and h ~= nil and old.h == h then
    release()
    old.orphan, old.anchor = nil, currentAnchor
    M.geoStats.farKept = (M.geoStats.farKept or 0) + 1
    return true
  end
  if old then
    if old.geo then M.dropFarGeo(key) else far[key] = nil end
    if k == 'F' then farSeen[key] = true end
  end
  local frame = geotile.frameOf({ frame = msg.frame or data.frame, key = msg.key })
  if not frame then release(); lastError = 'far geotile without a frame: ' .. tostring(msg.key); return false end
  local f = { geo = true, kind = k, gkey = msg.key, frame = frame, h = h, anchor = currentAnchor, parts = {},
    lamps = type(data.lamps) == 'table' and next(data.lamps) ~= nil and data.lamps or nil }
  if k == 'S' then
    local z, x, y = geotile.parse(msg.key)
    f.sky = { z, x, y }
  end
  local list = {}
  if k == 'B' then
    for sub, e in pairs(type(data.parts) == 'table' and data.parts or {}) do list[#list + 1] = { sub, e } end
    table.sort(list, function(a, b) return tostring(a[1]) < tostring(b[1]) end)
  elseif type(data.shape) == 'string' then
    list[1] = { 'shape', { shape = data.shape, pos = data.pos, pivot = data.pivot } }
  end
  local used = {}
  for _, it in ipairs(list) do
    local sub, e = it[1], it[2]
    if type(e) == 'table' and type(e.shape) == 'string' and type(e.pos) == 'table' then
      local sl = msgSlot(msg, sub)
      used[sub] = true
      -- (near: the road geotile whose own content shows these buildings / this deck)
      local p = { shape = sl and sl.path or e.shape, slot = sl, pos = e.pos, pivot = tonumber(e.pivot) or 0,
        near = (k == 'B' and sub) or (k == 'F' and msg.key) or nil }
      if anchorObj then farPartPlace(f, p, anchorObj) end
      f.parts[sub] = p
      farQ[#farQ + 1] = key .. '#' .. sub
    end
  end
  release(used)
  far[key] = f
  M.geoStats.far = (M.geoStats.far or 0) + 1
  return true
end

-- Is road geotile gkey showing its own buildings (every building mesh of its content made; none: it has none)?
-- (tk: its key in tiles, 'g:' .. gkey, when the caller has it: the far geotiles ask every frame)
local BLD_WHATS = { 'bld', 'bld2', 'bld3', 'bld4' }
function M.nearShowsBuildings(gkey, tk)
  local t = tiles[tk or geoKey(gkey)]
  if not t or not t.geo then return false end
  for _, what in ipairs(BLD_WHATS) do
    if t.parts[what] and not t[what] then return false end
  end
  return true
end

-- ... its own deck (made, or none) and road surface (its skin's road mesh made, or a skin without one)?
function M.nearShowsDeck(gkey, tk)
  local t = tiles[tk or geoKey(gkey)]
  if not t or not t.geo then return false end
  if t.parts.deck and not t.deck then return false end
  if t.road then return true end
  return t.skin ~= nil and t.skin.road == nil and (t.skin.pending or 0) <= 0
end

-- Pure-ish: is far geotile f's part sub hidden now (the ownership rule above)?
function M.farGeoHidden(f, sub)
  if f.kind == 'S' then
    if not f.skyKeys then           -- (its square's far buildings tiles' keys, once)
      local z, x, y = f.sky[1], f.sky[2], f.sky[3]
      if not z then return false end
      local n = 2 ^ (geotile.Z - z)
      f.skyKeys = {}
      for j = 0, n - 1 do
        for i = 0, n - 1 do f.skyKeys[#f.skyKeys + 1] = farGeoKey('F', geotile.key(x * n + i, y * n + j, geotile.Z)) end
      end
    end
    for _, k in ipairs(f.skyKeys) do if not farSeen[k] then return false end end
    return true
  end
  local p = f.parts[sub]
  if not p or not p.near then return false end
  p.nearKey = p.nearKey or geoKey(p.near)
  if f.kind == 'B' then return M.nearShowsDeck(p.near, p.nearKey) end
  return M.nearShowsBuildings(p.near, p.nearKey)
end

local function updateFarGeo(key, f)
  local shown = false
  for sub, p in pairs(f.parts) do
    if p.id then
      local hide = M.farGeoHidden(f, sub)
      if hide ~= (p.hidden or false) then
        local o = scenetree.findObjectById(p.id)
        if o then o:setHidden(hide) end
        p.hidden = hide
      end
      if not hide then shown = true end
    end
  end
  if f.lamps then farGeoLamps(f, shown) end
end

-- The far tiles' hide checks, round-robin: at most FAR_CHECKS_PER_FRAME a frame (all ~600 of them every frame cost
-- ~0.85 ms of every frame while driving, 2026-10-29; a far building shown or hidden a few frames later is not seen)
M.FAR_CHECKS_PER_FRAME = 80
local farCursor = nil
local function updateFarOne(key, f, player, view)
    if f.geo then
      updateFarGeo(key, f)
    elseif f.id then
      local hide
      if f.grass then
        hide = grassUpdate(key, f, player, view)
      elseif f.sky then
        hide = M.skyCovered(farSeen, f.anchor, f.sky[1], f.sky[2])
      elseif f.deckOf and M.geo then      -- (a ghost: as it was, its square is another anchor's)
        hide = f.ghost and (f.hidden or false) or geoSquareCovered(f, f.deckOf, true)
      elseif f.deckOf then
        local t = tiles[f.deckOf]
        hide = t ~= nil and (t.deck ~= nil or t.road ~= nil)
      elseif M.geo then
        hide = f.ghost and (f.hidden or false) or geoSquareCovered(f, key, false)
      else
        hide = tiles[key] ~= nil and tiles[key].bld ~= nil
      end
      if hide ~= (f.hidden or false) then
        local o = scenetree.findObjectById(f.id)
        if o then o:setHidden(hide) end
        f.hidden = hide
      end
    end
    if (f.lamps or f.beacons) and not f.geo then farLamps(key, f, f.id ~= nil and not f.hidden) end
end
local function updateFar(gov, player, view)
  if farCursor ~= nil and far[farCursor] == nil then farCursor = nil end     -- (gone: next() cannot go on from it)
  local key, f = next(far, farCursor)
  local wrapped = farCursor == nil
  for _ = 1, M.FAR_CHECKS_PER_FRAME do
    if key == nil then
      farCursor = nil
      if wrapped then break end
      wrapped = true
      key, f = next(far)
      if key == nil then break end
    end
    updateFarOne(key, f, player, view)
    farCursor = key
    key, f = next(far, key)
  end
  -- (the near grass squares before the far tiles: they are near the car, and small)
  while #grassQ > 0 and #walkQ == 0 and gov:try('roadWalk') do
    local q = table.remove(grassQ, 1)
    local f = far[q[1]]
    local s = f and f.subs and f.subs[q[2]]
    if s and s.queued and not s.id then
      s.queued = nil
      local t0 = now()
      loadMaterials()
      local ok, id = pcall(createWalk, { shape = s.shape, pos = s.pos }, false)
      if ok then
        s.id = id
        slotMade(id, s.shape)
        local o = scenetree.findObjectById(id)            -- (shown by grassUpdate once its tile's squares are all made)
        if o then o:setHidden(true) end
        s.hidden = true
      else lastError = tostring(id) end
      local ms = now() - t0
      gov:charge('roadWalk', ms)
      noteUnit('bldfar', ms, s.shape, ok and ms >= M.SLOW_UNIT_MS and unitInfo(s.shape) or nil)
    end
  end
  while #farQ > 0 and #walkQ == 0 and gov:try('roadWalk') do
    local key = table.remove(farQ, 1)
    local gk, sub = key:match('^(.-)#(.*)$')
    local f = far[gk or key]
    local p = f and f.geo and sub and f.parts[sub]
    if p and not p.id and anchorObj then        -- (a far geotile's part: placed as its tile is now)
      local t0 = now()
      loadMaterials()
      farPartPlace(f, p, anchorObj)
      local ok, id = pcall(createWalk, { shape = p.shape, pos = p.at, rot = p.rot }, false)
      if ok then
        p.id = id
        slotMade(id, p.shape)
        p.hidden = M.farGeoHidden(f, sub)
        if p.hidden then scenetree.findObjectById(id):setHidden(true) end
      else lastError = tostring(id) end
      local ms = now() - t0
      gov:charge('roadWalk', ms)
      noteUnit('far' .. f.kind, ms, p.shape)
    elseif f and not f.geo and not f.id then
      local t0 = now()
      loadMaterials()
      local ok, id = pcall(createWalk, { shape = f.shape, pos = f.pos }, false)
      if ok then f.id = id; slotMade(id, f.shape) else lastError = tostring(id) end
      local ms = now() - t0
      gov:charge('roadWalk', ms)
      noteUnit('bldfar', ms, f.shape, ok and ms >= M.SLOW_UNIT_MS and unitInfo(f.shape) or nil)
    end
  end
end

-- The skyline (server skyline.py; `skyline_tile` {anchorId, key "x_y" (a z12 cell), path, n, tier, data, slots},
-- `skyline_drop` {anchorId, key}): a cell's tall buildings out to 40 km, render-only, as one mesh per road tile square
-- (`parts`, keyed "tx_ty" as the road and far building tiles) and the whole cell merged (`shapes`, pooled: slots
-- "m<k>"). Near the cell (SKY_PARTS_M) or once a near tier covers one of its squares, the parts are drawn and each is
-- hidden while its square's road tile, far building tile or bldsky square shows its buildings (drawn once); the
-- merged mesh stays until every part is made. Farther, the merged mesh alone (one object instead of hundreds). Made
-- last of all (after the near meshes and the far building tiles), nearest cell first. A tile sent again (another
-- tier) replaces the old one once its own meshes are made.
M.SKY_PARTS_M = 2600        -- m from the cell's square: its parts drawn (the far building tiles reach 1900 m)
M.SKY_CHECK_S = 0.1         -- s between the hide / mode checks
M.SKY_GHOST_MAX_S = 600     -- a rebase's skyline ghost goes after this long anyway (the new anchor's cells come after
                            -- its far building tiles: a minute or more)
local sinceSky = stopwatch.new()

local function skyObjsDelete(list)
  for _, o in ipairs(list) do
    if o.id then deleteQ[#deleteQ + 1] = o.id; o.id = nil end
    o.hidden, o.failed = nil, nil
  end
end

function M.skyDelete(e)
  skyObjsDelete(e.shapes)
  skyObjsDelete(e.parts)
  for _, o in ipairs(e.shapes) do slotDrop(o.slot); o.slot = nil end
  if e.old then M.skyDelete(e.old); e.old = nil end
end

function M.onSkyTile(msg, currentAnchor)
  if not M.enabled then return false end
  switchAnchor(currentAnchor)
  if msg.anchorId ~= currentAnchor then return false end
  local ok, data = true, msg.data              -- (in the message, else the file: as onTile)
  if type(data) ~= 'table' then ok, data = pcall(jsonDecode, readFile(msg.path) or '') end
  if not ok or type(data) ~= 'table' then lastError = 'unreadable skyline tile ' .. tostring(msg.path); return false end
  local key = tileKey(msg.anchorId, msg.key)
  local e = { anchor = msg.anchorId, gkey = tostring(msg.key), tier = msg.tier, shapes = {}, parts = {},
    box = (type(data.box) == 'table' and #data.box == 4) and data.box or nil }
  for i, s in ipairs(type(data.shapes) == 'table' and data.shapes or {}) do
    local sl = msgSlot(msg, 'm' .. tostring(i - 1))
    if type(s) == 'table' and s.shape and type(s.pos) == 'table' then
      e.shapes[#e.shapes + 1] = { shape = sl and sl.path or s.shape, pos = s.pos, slot = sl }
    else
      slotDrop(sl)
    end
  end
  for _, p in ipairs(type(data.parts) == 'table' and data.parts or {}) do
    if type(p) == 'table' and p.shape and type(p.pos) == 'table' and p.k then
      e.parts[#e.parts + 1] = { k = tostring(p.k), shape = p.shape, pos = p.pos }
    end
  end
  local old = sky[key]
  if old and old.old then M.skyDelete(old.old); old.old = nil end
  if #e.shapes == 0 and #e.parts == 0 then
    if old then M.skyDelete(old) end
    sky[key] = nil
  else
    e.old = old                 -- drawn until e's own meshes are made (updateSky)
    sky[key] = e
  end
  return true
end

function M.onSkyDrop(msg)
  local key = tileKey(msg.anchorId, msg.key)
  local e = sky[key]
  if e and e.ghost then return end
  if e then M.skyDelete(e) end
  sky[key] = nil
end

-- Does a nearer tier of anchor aid show the buildings of road tile square k ("tx_ty")? Its road tile, its far
-- building tile, or (upstream 1.7.0's skyline squares to 4.8 km, `bldsky_tile`) the SKY_UNIT square holding it: that
-- draws every building of 40 m2 and more there, ours with them (fix/upstream-170-overlaps: drawn twice from 1.9 km to
-- 4.8 km before). Pure but for the tables it reads.
function M.skyPartCovered(aid, k)
  local key = tileKey(aid, k)
  local t = tiles[key]
  if t and t.bld ~= nil then return true end
  local f = far[key]
  if f ~= nil and f.id ~= nil then return true end
  local tx, ty = string.match(tostring(k), '^(-?%d+)_(-?%d+)$')
  if not tx then return false end
  local n = M.SKY_UNIT
  local s = far[tileKey(aid, 'S' .. math.floor(tonumber(tx) / n) .. '_' .. math.floor(tonumber(ty) / n))]
  return s ~= nil and s.id ~= nil
end
local skyCovered = M.skyPartCovered

local function boxDist(b, p)
  if not b or type(p) ~= 'table' or not p.x then return math.huge end
  local dx = math.max(b[1] - p.x, 0, p.x - b[3])
  local dy = math.max(b[2] - p.y, 0, p.y - b[4])
  return math.sqrt(dx * dx + dy * dy)
end

local function allMade(list)
  for _, o in ipairs(list) do if not o.id and not o.failed then return false end end
  return true
end

-- A box {x0, y0, x1, y1} through a rebase's transform (the box round its moved corners).
local function transformBox(xf, b)
  if not b then return nil end
  local x0, y0, x1, y1 = math.huge, math.huge, -math.huge, -math.huge
  for _, c in ipairs({ { b[1], b[2] }, { b[3], b[2] }, { b[3], b[4] }, { b[1], b[4] } }) do
    local x, y = xf:apply(c[1], c[2], 0)
    x0, y0, x1, y1 = math.min(x0, x), math.min(y0, y), math.max(x1, x), math.max(y1, y)
  end
  return { x0, y0, x1, y1 }
end

local function skySetHidden(o, h)
  if o.id and (o.hidden or false) ~= h then
    local obj = scenetree.findObjectById(o.id)
    if obj then obj:setHidden(h) end
    o.hidden = h
  end
end

-- The skyline's state (every SKY_CHECK_S: what is hidden, parts or merged), then its meshes made within the budget,
-- only while nothing else waits (walkQ, farQ). player / view: { x, y } (view: the camera when away from the car).
local function updateSky(gov, player, view)
  if next(sky) == nil then return end
  if sinceSky() >= M.SKY_CHECK_S * 1000 then
    sinceSky = stopwatch.new()
    local ready = {}
    for _, e in pairs(sky) do
      e.d = math.min(boxDist(e.box, player), boxDist(e.box, view))
      if not e.ghost then
        local any = false
        for _, p in ipairs(e.parts) do p.cov = skyCovered(e.anchor, p.k); any = any or p.cov end
        e.near = any or e.d < M.SKY_PARTS_M or #e.shapes == 0
        local all
        if e.near then
          all = allMade(e.parts)
          for _, p in ipairs(e.parts) do skySetHidden(p, p.cov) end
          for _, s in ipairs(e.shapes) do skySetHidden(s, all) end     -- the merged mesh until every part is there
        else
          all = allMade(e.shapes)
          for _, s in ipairs(e.shapes) do skySetHidden(s, false) end
          if all then skyObjsDelete(e.parts) end
        end
        if all then
          if e.old then M.skyDelete(e.old); e.old = nil end
          ready[e.gkey] = true
        end
      end
    end
    for key, e in pairs(sky) do         -- a rebase's ghosts: gone once the new anchor's cell is drawn, or near the car
      if e.ghost and (ready[e.gkey] or e.d < M.SKY_PARTS_M or e.ghostAt() > M.SKY_GHOST_MAX_S * 1000) then
        M.skyDelete(e)
        sky[key] = nil
      end
    end
  end
  if #walkQ > 0 or #farQ > 0 then return end
  local order = {}
  for _, e in pairs(sky) do if not e.ghost and e.d then order[#order + 1] = e end end
  table.sort(order, function(a, b) return a.d < b.d end)
  for _, e in ipairs(order) do
    for _, o in ipairs(e.near and e.parts or e.shapes) do
      if not o.id and not o.failed then
        if not gov:try('roadWalk') then return end
        local t0 = now()
        loadMaterials()
        local ok, id = pcall(createWalk, { shape = o.shape, pos = o.pos }, false)
        if ok then
          o.id = id
          slotMade(id, o.shape)
          local hide = (e.near and o.cov) or false
          if hide then
            local obj = scenetree.findObjectById(id)
            if obj then obj:setHidden(true) end
          end
          o.hidden = hide
        else
          o.failed = true
          lastError = tostring(id)
        end
        local ms = now() - t0
        gov:charge('roadWalk', ms)
        noteUnit('skyline', ms, o.shape)
      end
    end
  end
end

-- Server message `roads_drop` {anchorId, key}. (A ghost goes when covered: releaseGhosts.)
function M.onDrop(msg)
  local key = geotile.isKey(msg.key) and geoKey(msg.key) or tileKey(msg.anchorId, msg.key)   -- (a geotile: any anchor's)
  local t = tiles[key]
  if t and t.ghost then return end
  dropTile(key)
  sinceChange = stopwatch.new()
end

-- A gas station's colliding props were made (gasstations.lua): rebuilt with the roads' next collision rebuild.
function M.markCollisionDirty() collisionDirty = true end

-- Origin rebase (core.lua finishRebase): the shown tiles move into the new frame (p_new = R*p + t: their TSStatics
-- turned by R, as farfield.transform) and stay as "ghosts" until the new anchor's tiles over them are made
-- (ghostsCovered). Before (2026-10-17, forced rebase at Fells Point) every building, road mesh and sidewalk went at
-- once and came back over ~26 s as the server re-sent the tiles. DecalRoads (no rotation) and anything still queued go
-- now: their positions are the old frame's. A ghost's colliders are not made again (updateCollideRange).
M.GHOST_MAX_S = 120         -- ghosts left after this long go anyway
M.GHOST_FAR_M = 960         -- ... and once farther from the player than this (the server's roads DROP_M; a disc)
-- Pure: m from (x, y) to the square of half-size h round (cx, cy): to its nearest point (Euclidean, as the server's
-- roads.tile_distance; 0 inside).
function M.boxGap(x, y, cx, cy, h)
  local dx = math.max(math.abs(x - cx) - h, 0)
  local dy = math.max(math.abs(y - cy) - h, 0)
  return math.sqrt(dx * dx + dy * dy)
end
M.GHOST_FAR_WAIT_S = 2      -- ... from this long after the rebase on
local ghostSince = nil      -- stopwatch since the last rebase that left ghosts
local ghostCols = nil       -- { {x, y, what = {kind = true}} } of the rebase's ghost tiles with colliders (M.urgentColliders):
                            -- kept until the first rebuild after the last ghost went (a deleted collider's collision stays
                            -- until a rebuild)
local ghostColsEnd = false

local WHATS = withPieces({ 'walk', 'road', 'deck', 'bld', 'bld2', 'bld3', 'bld4', 'bldcol', 'walkcol', 'wire', 'tunnel', 'rail',
  'railcol', 'sign', 'signcol', 'exits', 'mileposts', 'merges', 'speeds' })

function M.transform(xf, geodesy)
  local bx, by, bz, bw = xf:beamngQuat()
  local function moveObj(id, q)
    local o = id and scenetree.findObjectById(id)
    if not o then return end
    local p = o:getPosition()
    local x, y, z = xf:apply(p.x, p.y, p.z)
    o:setPosRot(x, y, z, q[1], q[2], q[3], q[4])
  end
  local function newQ(q)
    local x, y, z, w = geodesy.quatMul(q[1], q[2], q[3], q[4], bx, by, bz, bw)
    return { x, y, z, w }
  end
  local any = false
  ghostCols, ghostColsEnd = ghostCols or {}, false
  for key, t in pairs(tiles) do
    if t.geo then goto continue end        -- (placed again by M.replace)
    t.q = newQ(t.q or { 0, 0, 0, 1 })
    for _, what in ipairs(WHATS) do moveObj(t[what], t.q) end
    if t.cx and t.colAt then
      local x, y = xf:apply(t.cx, t.cy, 0)
      local kinds = {}
      for what in pairs(t.colAt) do if t[what] then kinds[what] = true end end
      if next(kinds) then ghostCols[#ghostCols + 1] = { x = x, y = y, what = kinds } end
    end
    for _, id in ipairs(t.objs) do deleteQ[#deleteQ + 1] = id end
    t.objs, t.queued, t.cq = {}, 0, nil
    if t.cx then t.cx, t.cy = xf:apply(t.cx, t.cy, 0) end
    t.ghost = true
    any = true
    ::continue::
  end
  for _, f in pairs(far) do
    if f.id and not f.geo then          -- (far geotiles: placed again by M.replace)
      f.q = newQ(f.q or { 0, 0, 0, 1 })
      moveObj(f.id, f.q)
      f.ghost = true
      any = true
      if f.grass and type(f.pos) == 'table' then
        f.pos = { xf:apply(f.pos[1], f.pos[2], f.pos[3] or 0) }
        for _, s in ipairs(f.subs or {}) do
          if s.id then moveObj(s.id, f.q) end
          s.queued = nil
        end
      end
    end
  end
  grassQ = {}
  -- the skyline: a cell drawn merged moves along as a ghost (gone once the new anchor's is drawn: updateSky); one drawn
  -- in parts lies under the near tiers, whose squares change with the anchor: it goes now
  for key, e in pairs(sky) do
    if e.old then M.skyDelete(e.old); e.old = nil end
    if not e.ghost and (e.near or not allMade(e.shapes)) then
      M.skyDelete(e)
      sky[key] = nil
    elseif not e.ghost then
      skyObjsDelete(e.parts)
      e.q = newQ(e.q or { 0, 0, 0, 1 })
      for _, o in ipairs(e.shapes) do moveObj(o.id, e.q) end
      e.box = transformBox(xf, e.box)
      e.ghost, e.ghostAt = true, stopwatch.new()
    end
  end
  local function keepNew(list)
    local keep = {}
    for _, q in ipairs(list) do if tiles[q.key] and not tiles[q.key].ghost then keep[#keep + 1] = q end end
    return keep
  end
  walkQ, createQ, parked = keepNew(walkQ), keepNew(createQ), keepNew(parked)
  local fq = {}
  for _, k in ipairs(farQ) do
    local f = far[k:match('^(.-)#') or k]
    if f and not f.ghost then fq[#fq + 1] = k end
  end
  farQ = fq
  if any then ghostSince = stopwatch.new(); sinceChange = stopwatch.new(); collisionDirty = true end
end

-- Is the square of side M.TILE_M around (cx, cy) covered by made tiles of the current anchor (every one of the new
-- grid's squares it overlaps has a tile with nothing queued)?
-- done: the new grid's squares whose tile is made (madeSquares). signs: the sign tiles' squares instead (a sign
-- tile is no road tile: it never covers a road ghost, nor a road tile a sign ghost)
local function madeSquares(aid, busy, signs)
  local n, done = M.TILE_M, {}
  for key, t in pairs(tiles) do
    if t.anchor == aid and not t.ghost and not t.geo and t.cx and (t.signOnly == true) == (signs == true) then
      done[math.floor(t.cx / n) .. '_' .. math.floor(t.cy / n)] = t.queued <= 0 and not busy[key]
    end
  end
  return done
end
-- True once the road tile of anchor aid under (x, y) is made, no other anchor's road (a rebase's ghost too) is near
-- it, and the collision is rebuilt after (core.floorCheck: a ray found the old session's tunnel floor at a teleport's
-- spot, the car went down onto it and fell when that mesh went, 2026-10-17). Must come after madeSquares.
function M.settledFor(aid, x, y)
  if anchorId ~= aid or #deleteQ > 0 or collisionDirty then return false end
  local n = M.TILE_M
  for _, t in pairs(tiles) do
    -- (a sign tile has no collision; a geotile's old skin, moved from another anchor, counts as another's road)
    local other = (not t.geo and (t.anchor ~= aid or t.ghost)) or (t.geo and t.skin and t.skin.anchor ~= aid)
    if not t.signOnly and other then
      if not t.cx or (math.abs(t.cx - x) < 1.5 * n and math.abs(t.cy - y) < 1.5 * n) then return false end
    end
  end
  local busy = {}
  for _, q in ipairs(walkQ) do busy[q.key] = true end
  if M.geo then
    local k = M.geoKeyAt(x, y)
    return k ~= nil and geoMade(tiles[geoKey(k)], aid, busy)
  end
  return madeSquares(aid, busy)[math.floor(x / n) .. '_' .. math.floor(y / n)] == true
end

local covered0
-- (a ghost square is covered by geotiles too: every geotile within its corners' reach made for the anchor)
local function covered(done, cx, cy, busy)
  if covered0(done, cx, cy) then return true end
  if not M.geo or not anchorObj then return false end
  for _, k in ipairs(M.geoKeysNear(cx, cy, M.TILE_M * 0.71)) do
    if not geoMade(tiles[geoKey(k)], anchorId, busy or {}) then return false end
  end
  return true
end
covered0 = function(done, cx, cy)
  local h, n = M.TILE_M / 2, M.TILE_M
  for gx = math.floor((cx - h) / n), math.floor((cx + h - 1e-6) / n) do
    for gy = math.floor((cy - h) / n), math.floor((cy + h - 1e-6) / n) do
      if not done[gx .. '_' .. gy] then return false end
    end
  end
  return true
end

local function releaseGhosts(player)
  if not ghostSince then return end
  local busyKeys = {}
  for _, q in ipairs(walkQ) do busyKeys[q.key] = true end
  local all = ghostSince() > M.GHOST_MAX_S * 1000
  local left = false
  local done = madeSquares(anchorId, busyKeys)
  local signsDone = madeSquares(anchorId, busyKeys, true)
  local p = player             -- core's player (the camera's focus: a test drive's vehicle is not the player's car)
  for key, t in pairs(tiles) do
    if t.ghost then
      -- (not in the rebase's first GHOST_FAR_WAIT_S: in its frame the player is still read in the old frame, every
      -- ghost looked kilometres away and all went: 46 road tiles -> 0 at each rebase, 2026-10-17)
      local far = p and t.cx and ghostSince() > M.GHOST_FAR_WAIT_S * 1000
        and M.boxGap(p.x, p.y, t.cx, t.cy, M.TILE_M / 2) > M.GHOST_FAR_M
      if all or far or (t.cx and (t.signOnly and covered0(signsDone, t.cx, t.cy) or (not t.signOnly and covered(done, t.cx, t.cy, busyKeys)))) then
        dropTile(key)
        if not t.signOnly then collisionDirty = true end
      else left = true end
    end
  end
  for key, f in pairs(far) do
    if f.ghost then
      if all then
        farLamps(key, f, false)
        if f.id then deleteQ[#deleteQ + 1] = f.id end
        grassSubsDelete(f, true)
        slotDrop(f.slot)
        far[key] = nil
      else left = true end
    end
  end
  if not left then ghostSince, ghostColsEnd = nil, ghostCols ~= nil end
end

-- Once per frame (core onUpdate): deletions, then creations, within the governor's budget.
local viewAt = nil  -- core.viewAway(): the camera's { x, y } when it is away from the car, else nil (M.nextWalk)
local prewarmId, prewarmPath, prewarmWant = nil, nil, nil     -- (the materials' prewarm shape, below)
local prewarmNow
-- The prewarm shape is drawn in front of the camera for PREWARM_FRAMES frames when made (M.update), then kept far
-- below the map: a shape the camera never sees is culled before its materials' pipelines are built (the streaming
-- research, 2026-10-29: the D3D12 pipeline cache fills at the first draw, and the cold cache after a game update
-- froze Jeff's drive four times for 4.6-8.8 s). Its triangles are 5 cm, PREWARM_AHEAD_M ahead: specks for a few frames
-- at level start, under the loading screen when the scene renders there. The frames' wall times are logged
-- (prewarmFrames: a slow one is the pipelines being built, which then never happens on the road).
M.PREWARM_FRAMES = 4
M.PREWARM_AHEAD_M = 2.0
local prewarmShow = 0           -- frames left in front of the camera
local prewarmFrames = {}        -- their wall ms (the log line after the last)
local prewarmT = nil

-- The last update's slow units, "bldcol 62 ms (bldcol_5_3_-2.cdae), reloadCollision 21 ms", slowest first ('' if none).
-- (core's perf log reads it before this frame's update: the units of the frame a hitch line is about.)
function M.frameUnits()
  local arr = {}
  for i, u in ipairs(frameUnits) do arr[i] = u end
  table.sort(arr, function(a, b) return a[2] > b[2] end)
  local out = {}
  for i = 1, math.min(4, #arr) do
    local u = arr[i]
    local shape = type(u[3]) == 'string' and u[3]:match('([^/]+)$')
    local detail = (shape or '') .. (u[4] and ((shape and '; ' or '') .. u[4]) or '')
    out[i] = string.format('%s %d ms%s', tostring(u[1]), math.floor(u[2] + 0.5), detail ~= '' and (' (' .. detail .. ')') or '')
  end
  return table.concat(out, ', ')
end

-- The prewarm shape in front of the camera (cam: core's {x, y, z, fx, fy, fz}), or far below without one.
local function placePrewarm(cam)
  local o = prewarmId and scenetree.findObjectById(prewarmId)
  if not o then return end
  if cam and cam.x then
    o:setPosition(vec3(cam.x + (cam.fx or 0) * M.PREWARM_AHEAD_M, cam.y + (cam.fy or 0) * M.PREWARM_AHEAD_M,
      cam.z + (cam.fz or 0) * M.PREWARM_AHEAD_M))
  else
    o:setPosition(vec3(0, 0, -5000))
  end
end

-- The last frame's update in parts (ms; diagnostics: the frame-time probes, 2026-10-29)
M.steps = {}
local stepClock = stopwatch.clock()
local stepT0 = 0
local function step(name)
  local t = stepClock()
  M.steps[name] = t - stepT0
  stepT0 = t
end
function M.update(gov, currentAnchor, player, view, a, cam)
  stepT0 = stepClock()
  for i = #frameUnits, 1, -1 do frameUnits[i] = nil end
  viewAt = view
  if a then anchorObj = a end
  if prewarmWant then prewarmNow()
  elseif prewarmId and not scenetree.findObjectById(prewarmId) then   -- (the level loaded again: gone with it)
    prewarmId, prewarmWant = nil, prewarmPath
  end
  if prewarmShow > 0 then           -- (drawn: in front of the camera this frame; the frame's time noted next frame)
    if prewarmT then prewarmFrames[#prewarmFrames + 1] = prewarmT() end
    prewarmT = stopwatch.new()
    prewarmShow = prewarmShow - 1
    placePrewarm(cam)
    if prewarmShow == 0 then
      local ms = {}
      for i, v in ipairs(prewarmFrames) do ms[i] = string.format('%.0f', v) end
      log('I', 'beamngEarth', 'roads: materials prewarm drawn for ' .. M.PREWARM_FRAMES .. ' frames (ms: ' .. table.concat(ms, ' ') .. ')')
    end
  elseif prewarmT then
    prewarmFrames[#prewarmFrames + 1] = prewarmT()
    prewarmT = nil
    placePrewarm(nil)
  end
  if currentAnchor then switchAnchor(currentAnchor) end
  step('anchor')
  releaseGhosts(player)
  step('ghosts')
  if M._releaseOrphans then M._releaseOrphans(player) end
  step('orphans')
  while #deleteQ > 0 and gov:try('roadDelete') do
    local t0 = now()
    local did = table.remove(deleteQ)
    local o = scenetree.findObjectById(did)
    if o then
      if isCollider(o) then
        if M.nearPlayer(o:getPosition(), M.DELETE_NEAR_M) then collisionDirty = true else deletesDirty = true end
      end
      o:delete()
    end
    slotObjGone(did)
    local ms = now() - t0
    gov:charge('roadDelete', ms)
    noteUnit('delete', ms)
    sinceChange = stopwatch.new()
  end
  while #createQ > 0 and gov:try('roadChunk') do
    local q = table.remove(createQ, 1)
    local t = tiles[q.key]
    if t then
      local t0 = now()
      loadMaterials()
      local ok, id = pcall(createChunk, q.chunk)
      if ok then t.objs[#t.objs + 1] = id; popin.noteBox('decal', t.cx, t.cy, M.TILE_M / 2) else lastError = tostring(id) end
      t.queued = t.queued - 1
      local ms = now() - t0
      gov:charge('roadChunk', ms)
      noteUnit('chunk', ms)
      sinceChange = stopwatch.new()
    end
  end
  step('deletes+chunks')
  releaseHeld()
  step('held')
  updateCollideRange()
  step('collideRange')
  local forced = nil
  if #walkQ > 0 and sinceUrgent >= M.URGENT_EVERY_FRAMES and not (gov.isHeavyFrame and gov:isHeavyFrame()) then
    forced = M.urgentWalk(walkQ)
  end
  sinceUrgent = sinceUrgent + 1
  while #walkQ > 0 and (forced or gov:try('roadWalk')) do
    local q = table.remove(walkQ, forced or M.nextWalk(walkQ))
    if forced then forced, sinceUrgent, M.urgentWalks = nil, 0, (M.urgentWalks or 0) + 1 end
    local t = tiles[q.key]
    if t and q.tile and q.tile ~= t then t = nil end
    -- (an entry of an earlier tile under this key: not made)
    -- (only a colliding mesh waits: a render-only one (q.renderOnly: the drawn sidewalks and buildings beside their lean
    -- collision meshes) cannot catch the car, and holding it hid a whole tile's sidewalks while the car stood in their
    -- box: a roundabout's island band was never drawn, 2026-10-17 Harbor Point)
    if t and q.what ~= 'road' and not q.renderOnly and playerIn(boxesOf(t, q.what), q.what, t) then
      held[#held + 1] = q           -- the car is on it: made once the car is clear (playerIn)
      t = nil
    end
    local walk = q.walk
    if t and q.geo then walk = M.geoWalk(t, q.what) end      -- (a geotile's part: placed as the tile is now)
    if t and walk then
      local t0 = now()
      loadMaterials()
      local isRoad = q.what == 'road' or q.what == 'wire' or q.renderOnly   -- render-only meshes
      -- the buildings' and sidewalks' lean meshes are a collision detail (server cdae collision_only): never drawn
      local kind = kindOf(q.what)
      local leanCol = kind == 'bldcol' or kind == 'walkcol' or kind == 'signcol' or kind == 'railcol'
      local ok, id = pcall(createWalk, walk, leanCol and 'Collision Mesh' or not isRoad)
      if ok then slotMade(id, walk.shape) end
      if ok and not leanCol then   -- (drawn: popin.lua)
        popin.noteBox(q.what, t.cx, t.cy, M.TILE_M / 2)
      end
      if ok and q.skin and t.skin then             -- a new skin's part: the old one's goes (all, with the last)
        t.skin.pending = t.skin.pending - 1
        M.dropOldSkin(t, q.what)
        if t.skin.pending <= 0 then M.dropOldSkin(t) end
      end
      if ok and q.swap and t.oldPart and t.oldPart[q.what] then     -- an exact variant / the base again: swapped
        deleteQ[#deleteQ + 1] = t.oldPart[q.what]
        t.oldPart[q.what] = nil
        if t.oldPartSlot and t.oldPartSlot[q.what] then slotDrop(t.oldPartSlot[q.what]); t.oldPartSlot[q.what] = nil end
      end
      if ok then
        if not isRoad then t.cq = t.cq or {}; t.cq[q.what] = q end
        if isRoad then t[q.what] = id else
          t[q.what] = id; collisionDirty = true; newColliders[#newColliders + 1] = { id, q }
          t.colAt = t.colAt or {}; t.colAt[q.what] = now()
        end
      else lastError = tostring(id) end
      local ms = now() - t0
      gov:charge('roadWalk', ms)
      noteUnit(tostring(q.what), ms, walk and walk.shape, ok and ms >= M.SLOW_UNIT_MS and unitInfo(walk.shape) or nil)
      sinceChange = stopwatch.new()
    end
  end
  step('walks')
  updateFar(gov, player, view)  -- far buildings: only with the near meshes all made (they share the budget)
  updateSky(gov, player, view)  -- the skyline: after both
  step('far')
  if swapQuiet then
    swapQuiet.frames = swapQuiet.frames - 1
    if swapQuiet.frames <= 0 and swapQuiet.since() >= M.SWAP_QUIET_MS then swapQuiet = nil end
  end
  local urgent = collisionDirty and (holdActive or M.urgentColliders()) and sinceReload() > M.URGENT_GAP_S * 1000
  local due = not swapQuiet and ((collisionDirty and sinceReload() > (lastSpeed >= M.RELOAD_MOVING_MS and M.RELOAD_EVERY_S
      or M.RELOAD_STAND_S) * 1000) or urgent
      or (deletesDirty and sinceReload() > M.RELOAD_DELETES_S * 1000))
  if due and not holdActive and reloadWaits < M.RELOAD_WAIT_FRAMES and gov.roomFor and not gov:roomFor(M.RELOAD_ROOM_MS) then
    reloadWaits = reloadWaits + 1
    due = false
  end
  if due then
    reloadWaits = 0
    local t0 = now()
    noteNewColliders()
    be:reloadCollision()
    lastReloadAt, reloads = now(), reloads + 1
    collisionDirty, deletesDirty, sinceReload = false, false, stopwatch.new()
    if ghostColsEnd then ghostCols, ghostColsEnd = nil, false end
    local ms = now() - t0
    -- out of the frame's budget, not into the meshes' cost estimate (from PR #41: a ~40 ms rebuild in the roadWalk
    -- EMA held the next sidewalk and building meshes back as if each cost that much more)
    gov:chargeMotion(ms)
    noteUnit('reloadCollision', ms)
  end
  step('collision')
end

-- A colliding mesh (sidewalks, bridge decks) never turns solid around the player's car (2026-10-13: the
-- intermittent NaN drives; a late curb or deck appearing around a vehicle). The server's road tile lists
-- boxes around its pieces (roads.collider_boxes); a mesh the car is in is not made (held) or, made since
-- the last reloadCollision, is removed again before it, and made once the car is clear.
M.PLAYER_R = 3.0    -- m around the player's position (half a car's length, and some)
M.PLAYER_DZ = 2.5   -- m above / below
-- a sidewalk's boxes already reach past its slab by about 1 m (server roads.collider_boxes: half its width + 0.5 m
-- + COLLIDER_MARGIN_M): only the car over the sidewalk itself holds it. With PLAYER_R a car anywhere within ~4 m of a
-- curb held the whole tile's sidewalk collision (one mesh a tile), and a tile made again while the car drove its
-- streets had no solid sidewalks at all (the user, 2026-10-27: "sidewalks are missing collision", Fells Point)
M.WALK_PLAYER_R = 0.0
M.DECK_BOX_BELOW_M = 2.2    -- a deck box's floor is this far under its lowest surface (server roads.collider_boxes:
                            -- roadmesh.DECK_DEPTH 1.2 + 1.0)
M.DECK_ON_M = 0.2           -- the car's position this far under that surface or less: on the deck (not held)

-- Is world position p within r m (horizontally) of the player's car? (no car: yes, to be safe)
function M.nearPlayer(p, r)
  local veh = getPlayerVehicle and getPlayerVehicle(0)
  if not veh or not p then return true end
  local q = veh:getPosition()
  local dx, dy = p.x - q.x, p.y - q.y
  return dx * dx + dy * dy <= r * r
end

playerIn = function(boxes, what, t)
  -- (a drop hold: the car does not move until its ground and colliders are in, then it is set on top of them)
  if holdActive or type(boxes) ~= 'table' or #boxes == 0 then return false end
  local veh = getPlayerVehicle and getPlayerVehicle(0)
  if not veh then return false end
  local p = veh:getPosition()
  local pp = t and t.geo and t.pls and t.pls[what]
  if pp then      -- a geotile's content: its boxes are in the tile's frame, the player mapped into it
    local x, y, z = pp.inv:apply(p.x, p.y, p.z)
    p = { x = x, y = y, z = z }
  end
  what = kindOf(what)
  local r, dz = (what == 'walk' or what == 'walkcol') and M.WALK_PLAYER_R or M.PLAYER_R, M.PLAYER_DZ
  for _, b in ipairs(boxes) do
    if p.x >= b[1] - r and p.x <= b[4] + r and p.y >= b[2] - r and p.y <= b[5] + r then
      if what == 'deck' then
        -- a deck: only with the car under its surface (in its slab, or under it within a car's height): driving up
        -- onto a late deck the car was always in its box, the deck held until the car was past, and the car fell
        -- through it (I-95, Baltimore, a Deck test drive 2026-10-20)
        local surf = b[3] + M.DECK_BOX_BELOW_M
        if p.z < surf - M.DECK_ON_M and p.z > surf - M.DECK_BOX_BELOW_M - dz then return true end
      elseif p.z >= b[3] - dz and p.z <= b[6] + dz then
        return true
      end
    end
  end
  return false
end

releaseHeld = function()
  if #held == 0 then return end
  local keep = {}
  for _, q in ipairs(held) do
    local t = tiles[q.key]
    if t then
      if playerIn(boxesOf(t, q.what), q.what, t) then keep[#keep + 1] = q else walkQ[#walkQ + 1] = q end
    end
  end
  held = keep
end

noteNewColliders = function()
  local removed = 0
  for _, c in ipairs(newColliders) do
    local q = c[2]
    local t = tiles[q.key]
    if t and t[q.what] == c[1] and playerIn(boxesOf(t, q.what), q.what, t) then
      local o = scenetree.findObjectById(c[1])
      if o then o:delete() end
      t[q.what] = nil
      held[#held + 1] = q
      removed = removed + 1
      M.solidAround = (M.solidAround or 0) + 1
      log('I', 'beamngEarth', 'roads: a ' .. tostring(q.what) .. ' mesh around the player held until it is clear')
    end
  end
  newColliders = {}
  return removed
end
M._noteNewColliders = noteNewColliders

-- The player's position and motion now: p (vec3) and its unit horizontal direction at FAST_MS or more, else nil.
local function playerNow()
  local veh = getPlayerVehicle and getPlayerVehicle(0)
  if not veh then return nil end
  return veh:getPosition(), lastDir
end

-- Which walkQ entry to make next (index): the road meshes and colliders before the render-only meshes (drawn
-- buildings, wires), each by the distance to its tile's centre (behind a fast car counting three times): the tiles
-- the car reaches first (the user, 2026-10-19: high speeds, the road texture struggling to keep up). A render-only
-- mesh by its distance to the camera instead when that is nearer (viewAt: what is drawn follows the camera).
function M.nextWalk(q)
  if #q <= 1 then return 1 end
  local p, dir = playerNow()
  if not p then return 1 end
  local best, bestKey = 1, math.huge
  for i, e in ipairs(q) do
    local t = tiles[e.key]
    local k = 1e9
    if t and t.cx then
      local dx, dy = t.cx - p.x, t.cy - p.y
      k = math.sqrt(dx * dx + dy * dy)
      if dir then         -- fast: by the way along the path ahead, sideways counting SIDE_PENALTY times (the server's
        local along = dx * dir.x + dy * dir.y               -- roads.path_key); behind the car after all of those
        local side = math.abs(dx * dir.y - dy * dir.x)
        if along < -M.TILE_M / 2 then k = k * 3 + M.LEAD_MAX_M     -- (its centre half a tile behind: left behind)
        else k = math.max(0, along) + M.SIDE_PENALTY * math.max(0, side - M.TILE_M / 2) end
      end
      if viewAt and (e.renderOnly or e.what == 'wire') then
        local vx, vy = t.cx - viewAt.x, t.cy - viewAt.y
        k = math.min(k, math.sqrt(vx * vx + vy * vy))
      end
    end
    if e.renderOnly or e.what == 'wire' then k = k + 1e6 end
    local ek = kindOf(e.what)
    if ek == 'deck' or ek == 'tunnel' then k = math.max(0, k - M.DRIVE_FIRST_M) end
    if k < bestKey then best, bestKey = i, k end
  end
  return best
end

-- The walkQ index of a bridge deck's or tunnel's collider the car reaches within URGENT_S (150 m at least; not behind a
-- fast car), the nearest, or nil.
function M.urgentWalk(q)
  local p, dir = playerNow()
  if not p then return nil end
  local reach = math.max(150, lastSpeed * M.URGENT_S) + M.TILE_M * 0.71
  local best, bd = nil, math.huge
  for i, e in ipairs(q) do
    local ek = kindOf(e.what)
    if ek == 'deck' or ek == 'tunnel' then
      local t = tiles[e.key]
      if t and t.cx and not t.ghost and (not e.tile or e.tile == t) then
        local dx, dy = t.cx - p.x, t.cy - p.y
        local d = math.sqrt(dx * dx + dy * dy)
        if d <= reach and d < bd and (not dir or dx * dir.x + dy * dir.y > -M.TILE_M / 2) then best, bd = i, d end
      end
    end
  end
  return best
end

-- Does a rebase's ghost of this kind still collide at (x, y)?
local function ghostCovers(x, y, what)
  if not ghostCols then return false end
  local r2 = M.GHOST_COVER_M * M.GHOST_COVER_M
  for _, g in ipairs(ghostCols) do
    if g.what[what] and (g.x - x) ^ 2 + (g.y - y) ^ 2 <= r2 then return true end
  end
  return false
end

-- Is a collider made since the last rebuild one the car reaches soon (its tile within URGENT_S of driving, URGENT_MIN_M
-- at least, not behind a moving car, not where a rebase's ghost of it still collides)? Then the rebuild runs now, not
-- after RELOAD_EVERY_S.
function M.urgentColliders()
  if #newColliders == 0 then return false end
  local p, dir = playerNow()
  if not p then return true end
  local reach = math.max(M.URGENT_MIN_M, lastSpeed * M.URGENT_S) + M.TILE_M * 0.71
  for _, c in ipairs(newColliders) do
    local t = tiles[c[2].key]
    if t and t.cx then
      local dx, dy = t.cx - p.x, t.cy - p.y
      if dx * dx + dy * dy <= reach * reach and (not dir or dx * dir.x + dy * dir.y > -M.TILE_M / 2)
          and not ghostCovers(t.cx, t.cy, c[2].what) then
        return true
      end
    end
  end
  return false
end

-- core swapped the terrain block's .ter this frame (terrain.swap): no collision rebuild until it has settled.
function M.terrainSwapped() swapQuiet = { frames = M.SWAP_QUIET_FRAMES, since = stopwatch.new() } end
function M.swapQuiet() return swapQuiet ~= nil end

-- core's drop hold on / off: while it holds, colliders around the frozen car are made (not held for it) and rebuilt
-- at once, so the hold can wait for them (M.readyAround) and set the car on top.
function M.setHold(on) holdActive = on and true or false end

-- Are the road tiles of anchor aid within r of (x, y) all in, their colliders (decks, tunnels, sidewalks, buildings)
-- made and solid (made before the last collision rebuild)? Only what collides counts: the drawn road surface, building
-- facades and wires come when they come (in Manhattan they kept a drop hold 20 s). Roads off: yes.
function M.readyAround(aid, x, y, r)
  if not M.enabled then return true end
  if anchorId ~= aid then return false end
  local busy = {}
  for _, q in ipairs(walkQ) do
    if not q.renderOnly and q.what ~= 'road' and q.what ~= 'wire' then busy[q.key] = true end
  end
  local n = M.TILE_M
  if M.geo then           -- (geotiles: those whose quads come within r, their skins this anchor's)
    for _, k in ipairs(M.geoKeysNear(x, y, r)) do
      local key = geoKey(k)
      local t = tiles[key]
      if not t or busy[key] or not t.skin or t.skin.anchor ~= aid then return false end
      for what in pairs(t.colWant or {}) do
        if not t[what] or ((t.colAt and t.colAt[what]) or math.huge) >= lastReloadAt then return false end
      end
    end
    return true
  end
  for gx = math.floor((x - r) / n), math.floor((x + r) / n) do
    for gy = math.floor((y - r) / n), math.floor((y + r) / n) do
      local key = tileKey(aid, gx .. '_' .. gy)
      local t = tiles[key]
      if not t or t.ghost or busy[key] then return false end
      for what in pairs(t.colWant or {}) do
        if not t[what] or ((t.colAt and t.colAt[what]) or math.huge) >= lastReloadAt then return false end
      end
    end
  end
  return true
end

-- Something else made colliding objects near the player (trees.lua's Forest items): rebuild collision at the
-- next throttled slot.
function M.requestCollision() collisionDirty = true end

-- Collision rebuilds so far (be:reloadCollision, ours or the terrain commit's): what was made before one collides from
-- the frame after it (terrainpatch.groundReady).
function M.reloads() return reloads end

function M.hasWork() return #createQ > 0 or #deleteQ > 0 or #walkQ > 0 or #farQ > 0 or #grassQ > 0 or collisionDirty end


-- Somebody else just ran be:reloadCollision() (the terrain commit): the sidewalks made so far collide.
function M.collisionRebuilt()
  -- that rebuild already made them solid: one held now leaves the physics at the next one
  local removed = noteNewColliders()
  lastReloadAt, reloads = now(), reloads + 1
  collisionDirty, deletesDirty, sinceReload = removed > 0, false, stopwatch.new()
  if ghostColsEnd then ghostCols, ghostColsEnd = nil, false end
end

-- Every road removed now (setting off, mod unload).
-- Server hello.prewarm: a tiny shape listing every material the server's shapes use, loaded once when it connects
-- (server/prewarm.py): a material's first shape in a session set it up (a tunnel's first tile 65 ms, later ones ~2 ms),
-- now while the level starts. Kept far below the map (one per path; nil: an older server, nothing).
prewarmNow = function()
  local path = prewarmWant
  -- (the hello comes at the main menu, before the level's objects and with the roads off outside our level)
  if not path or not M.enabled or not scenetree.MissionGroup then return end
  prewarmWant = nil
  if path == prewarmPath and prewarmId and scenetree.findObjectById(prewarmId) then return end   -- (made already)
  if prewarmId then
    local o = scenetree.findObjectById(prewarmId)
    if o then o:delete() end
    prewarmId = nil
  end
  loadMaterials()
  local t = stopwatch.new()
  local ok, id = pcall(createWalk, { shape = path, pos = { 0, 0, -5000 } }, false)
  if not ok then lastError = tostring(id); return end
  prewarmId, prewarmPath = id, path
  prewarmShow, prewarmFrames, prewarmT = M.PREWARM_FRAMES, {}, nil     -- (drawn from the next update on)
  log('I', 'beamngEarth', string.format('roads: materials prewarmed in %.0f ms (%s)', t(), path))
end

-- (tests, the dev tools) frames the prewarm shape still has in front of the camera, and the frame times so far
function M.prewarmState() return prewarmShow, prewarmFrames end

function M.prewarm(path)
  if type(path) ~= 'string' then return false end
  prewarmWant = path
  prewarmNow()
  return true
end

function M.clear()
  for key in pairs(tiles) do dropTile(key) end
  for key, f in pairs(far) do
    if f.geo then M.dropFarGeo(key) end
    if f.id then deleteQ[#deleteQ + 1] = f.id end
    grassSubsDelete(f, true)
    slotDrop(f.slot)
  end
  far, farQ, grassQ = {}, {}, {}
  createQ, held, newColliders = {}, {}, {}
  for _, id in ipairs(deleteQ) do
    local o = scenetree.findObjectById(id)
    if o then
      if isCollider(o) then collisionDirty = true end
      o:delete()
    end
    slotObjGone(id)
  end
  deleteQ = {}
  walkQ, parked = {}, {}
  if collisionDirty then be:reloadCollision(); collisionDirty = false end
end

-- The level ended: its objects are gone with it.
function M.reset()
  -- (the level took the objects: every slot goes)
  for _, t in pairs(tiles) do for _, sl in pairs(t.slots or {}) do slotsGone[#slotsGone + 1] = { sl[1], sl[2], sl[3] } end end
  for _, f in pairs(far) do
    if f.slot then slotsGone[#slotsGone + 1] = { f.slot[1], f.slot[2], f.slot[3] } end
    for _, sq in ipairs(f.subs or {}) do
      if sq.slot then slotsGone[#slotsGone + 1] = { sq.slot[1], sq.slot[2], sq.slot[3] } end
    end
    for _, p in pairs(f.parts or {}) do
      if p.slot then slotsGone[#slotsGone + 1] = { p.slot[1], p.slot[2], p.slot[3] } end
    end
  end
  for n, d in pairs(slotDropped) do slotsGone[#slotsGone + 1] = { n, d[1], d[2] } end
  objSlot, slotLive, slotDropped = {}, {}, {}
  tiles, createQ, deleteQ, walkQ, anchorId, loadedMaterials = {}, {}, {}, {}, nil, false
  far, farQ, grassQ = {}, {}, {}
  collisionDirty, deletesDirty = false, false
end

-- On / off (the settings page, through core.setRoadsEnabled, which asks the server for the tiles
-- again when switched back on). Returns true if it was off and is on now.
function M.setEnabled(on)
  local was = M.enabled
  M.enabled = on and true or false
  if not M.enabled then M.clear() end
  return M.enabled and not was
end

-- The highway signs on / off (the settings page's highwaySigns, through core.setHighwaySigns): only the sign objects
-- go or come back (from each tile's signData); the roads, their colliders and the held shape slots stay. Returns true
-- if it changed.
function M.setSignsEnabled(on)
  on = on and true or false
  if M.signsEnabled == on then return false end
  M.signsEnabled = on
  local isSign = {}
  for _, w in ipairs(M.SIGN_WHATS) do isSign[w] = true end
  local keep = {}
  for _, q in ipairs(walkQ) do if not isSign[q.what] then keep[#keep + 1] = q end end
  walkQ = keep
  for key, t in pairs(tiles) do
    for _, what in ipairs(M.SIGN_WHATS) do
      if t[what] then deleteQ[#deleteQ + 1] = t[what]; t[what] = nil end
      local sign = t.signData and t.signData[what]
      if on and M.enabled and not t.ghost and sign then
        walkQ[#walkQ + 1] = { key = key, walk = sign, what = what, tile = t, renderOnly = true }
      end
    end
  end
  return true
end

-- The state of the road tiles along a line (tools: how far ahead of a fast car its tiles are ready). From (x, y)
-- along the unit direction (hx, hy) to dist m, every 32 m: { d, key, arrived (ms ago or false), road (its road mesh
-- made), col ('none': no colliders, 'parked' / 'made' / 'solid': made before the last collision rebuild) }.
function M.ahead(x, y, hx, hy, dist)
  local out, seen, n, tnow = {}, {}, M.TILE_M, now()
  for d = 0, dist, 32 do
    local px, py = x + hx * d, y + hy * d
    local sq = math.floor(px / n) .. '_' .. math.floor(py / n)
    local tkey = tileKey(anchorId, sq)
    if M.geo then sq = M.geoKeyAt(px, py) or sq; tkey = geoKey(sq) end
    if not seen[sq] then
      seen[sq] = true
      local t = tiles[tkey]
      local e = { d = d, key = sq, arrived = t and math.floor(tnow - (t.at or tnow)) or false }
      if t then
        e.road = t.road ~= nil or (t.queued or 0) <= 0
        e.bld = t.bld ~= nil                    -- (a tile without buildings never has one)
        e.walk = t.walk ~= nil
        local want, made, solid = 0, 0, 0
        for what in pairs(t.colWant or {}) do
          want = want + 1
          if t[what] then
            made = made + 1
            if (t.colAt and t.colAt[what] or tnow) < lastReloadAt then solid = solid + 1 end
          end
        end
        e.col = want == 0 and 'none' or (solid == want and 'solid') or (made == want and 'made') or 'parked'
        if e.col == 'parked' then       -- where each missing collider waits: walk (the import queue), parked (not near
          e.miss = {}                   -- enough yet), held (the car in its box), none (no entry: dropped)
          for what in pairs(t.colWant or {}) do
            if not t[what] then e.miss[what] = 'none' end
          end
          for _, set in ipairs({ { walkQ, 'walk' }, { parked, 'parked' }, { held, 'held' } }) do
            for _, q in ipairs(set[1]) do
              if q.key == tkey and e.miss[q.what] then e.miss[q.what] = set[2] end
            end
          end
        end
      end
      out[#out + 1] = e
    end
  end
  return out
end

-- The server's hello: road tiles as geotiles (hello.roadGeo == M.ROAD_GEO) or the anchor's.
function M.setGeo(on) M.geo = on and true or false end

-- A new server session (core.lua hello: a restarted server, its anchor ids from 1 again): every anchor tile goes (as
-- M.clear: they belonged to the old session), the geotiles stay as orphans: one the new session sends again with the
-- same content hash (h) keeps its objects (onGeoTile), the others go once ORPHAN_FAR_M away or after ORPHAN_MAX_S
-- (releaseOrphans). Their skins are no anchor's now (the new session's come with their content).
M.ORPHAN_MAX_S = 120
M.ORPHAN_FAR_M = 960
local orphanSince = nil
function M.newSession()
  if not M.geo and not M.farGeo then return M.clear() end      -- (anchor tiles only: all of them go)
  local any = false
  for key, t in pairs(tiles) do
    if t.geo and M.geo then
      t.orphan = true
      if t.skin then t.skin.anchor = -1 end
      any = true
    else
      dropTile(key)
    end
  end
  -- (the far geotiles stay as orphans too: the new session's same content (h) keeps them, the rest go after
  -- FAR_GEO_ORPHAN_S)
  local keepFar, keepSeen = {}, {}
  for key, f in pairs(far) do
    if f.geo and M.farGeo then
      f.orphan = true
      keepFar[key] = f
      if farSeen[key] then keepSeen[key] = true end
      any = true
    else
      if f.id then deleteQ[#deleteQ + 1] = f.id end
      slotDrop(f.slot)
      if f.geo then
        farGeoLamps(f, false)
        for _, p in pairs(f.parts) do
          if p.id then deleteQ[#deleteQ + 1] = p.id end
          slotDrop(p.slot)
        end
      end
    end
  end
  far, farSeen = keepFar, keepSeen
  local fq = {}
  for _, k in ipairs(farQ) do if far[k:match('^(.-)#') or k] then fq[#fq + 1] = k end end
  farQ = fq
  createQ, held, newColliders = {}, {}, {}
  local keep = {}
  for _, q in ipairs(walkQ) do if tiles[q.key] and not q.skin then keep[#keep + 1] = q end end
  walkQ = keep
  orphanSince = any and stopwatch.new() or nil
  collisionDirty = true
end
local function releaseOrphans(player)
  if not orphanSince then return end
  local all = orphanSince() > M.ORPHAN_MAX_S * 1000
  local left = false
  for key, f in pairs(far) do
    if f.orphan then
      if orphanSince() > M.FAR_GEO_ORPHAN_S * 1000 then M.dropFarGeo(key) else left = true end
    end
  end
  for key, t in pairs(tiles) do
    if t.orphan then
      local far_ = player and t.cx and M.boxGap(player.x, player.y, t.cx, t.cy, M.TILE_M / 2) > M.ORPHAN_FAR_M
      if all or far_ then dropTile(key); collisionDirty = true else left = true end
    end
  end
  if not left then orphanSince = nil end
end
M._releaseOrphans = releaseOrphans

-- The sign tiles shown (diagnostics): how many, and the farthest one's centre from (x, y) in m.
function M.signTiles(x, y)
  local n, far = 0, 0
  for _, t in pairs(tiles) do
    if t.signOnly and not t.ghost and t.cx then
      n = n + 1
      far = math.max(far, math.sqrt((t.cx - x) ^ 2 + (t.cy - y) ^ 2))
    end
  end
  return n, far
end

function M.status()
  local nt, no, nw, nr, nd, nb, ngForest, ngRows = 0, 0, 0, 0, 0, 0, 0, 0
  for _, t in pairs(tiles) do
    nt = nt + 1; no = no + #t.objs
    if t.walk then nw = nw + 1 end
    if t.road then nr = nr + 1 end
    if t.deck then nd = nd + 1 end
    if t.bld then nb = nb + 1 end
    if t.guardrails then ngForest = ngForest + 1; ngRows = ngRows + (t.guardrailRows or 0) end
  end
  local nf, ngf, ngShown = 0, 0, 0
  for _, f in pairs(far) do
    if f.id and not f.hidden then nf = nf + 1 end
    for _, sq in ipairs(f.subs or {}) do if sq.id and not sq.hidden then nf = nf + 1 end end
    if f.geo then
      ngf = ngf + 1
      for _, p in pairs(f.parts) do if p.id and not p.hidden then nf, ngShown = nf + 1, ngShown + 1 end end
    end
  end
  local ns, nso = 0, 0          -- skyline cells, and their meshes drawn
  for _, e in pairs(sky) do
    ns = ns + 1
    for _, l in ipairs({ e.shapes, e.parts }) do
      for _, o in ipairs(l) do if o.id and not o.hidden then nso = nso + 1 end end
    end
  end
  return { enabled = M.enabled, tiles = nt, roads = no, queued = #createQ, deleting = #deleteQ, anchor = anchorId,
    walks = nw, roadMeshes = nr, decks = nd, buildings = nb, guardrailForestTiles = ngForest,
    guardrailForestRows = ngRows, farBuildings = nf, skyCells = ns, skyMeshes = nso, walksQueued = #walkQ,
    solidAround = M.solidAround or 0, urgentWalks = M.urgentWalks or 0, quietS = sinceChange() / 1000, error = lastError,
    units = unitStats, unitLog = unitLog, geo = M.geo or false, geoStats = M.geoStats, farGeo = M.farGeo or false,
    farGeoTiles = ngf, farGeoShown = ngShown }
end

local okSweep, swept = pcall(M.sweep)
if okSweep and swept > 0 then log('W', 'beamngEarth', 'roads: deleted ' .. swept .. ' road objects left by a previous Lua state') end

return M
