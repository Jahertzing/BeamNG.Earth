-- The night's distant lights (the user, 2026-10-28: "drive into denver at night and see the lights"): a city's sky
-- glow and the Castle Rock star. Both are render-only TSStatics of the mod's own shapes (tools/build_night_landmarks.py)
-- placed from latitude / longitude in the anchor's frame; their materials (bne_city_glow, bne_star_bulb) are lit by
-- nightlights.lua, so by day they draw nothing (the glow is additive black) or a dark frame of bulbs.
--   * The glow: an open cylinder of additive light falling off upward over every big place (Natural Earth's populated
--     places, the map's place names: maplayers.js) within GLOW_NEAR_M, its radius and height by the place's rank
--     (GLOW_R: Denver 1, Boulder 7). From outside a dome of light on the horizon; from inside, the sky glowing all round.
--   * The star: on top of the Rock in Castle Rock (STAR), facing I-25 to the west, lit in its season (nightlights.lua).
local M = {}

M.CHECK_S = 2.0
M.GLOW_SHAPE = '/levels/beamng_earth/art/shapes/beamngEarth/bne_city_glow.dae'
M.STAR_SHAPE = '/levels/beamng_earth/art/shapes/beamngEarth/bne_star.dae'
M.PLACES_FILE = '/ui/ui-vue/mods/beamngEarth/maplayers.js'
M.GLOW_NEAR_M = 90000       -- places this near the camera glow
M.GLOW_RANK_MAX = 8         -- (maplayers.js ranks: 0 the world's capitals ... 10 villages)
-- the glow's radius by rank (m; the height GLOW_H_SHARE of it, at least GLOW_H_MIN_M), its foot GLOW_SINK_M under
-- the place's ground (the terrain and buildings hide its foot)
M.GLOW_R = { [0] = 16000, 14000, 11000, 9000, 7000, 5500, 4200, 3200, 2400 }
M.GLOW_H_SHARE = 0.3
M.GLOW_H_MIN_M = 900
M.GLOW_SINK_M = 400
-- The Castle Rock star: the Rock's summit (Wikipedia: 39.37909 N, 104.85605 W, 2,010 m), the star's foot a few
-- metres under it (the summit's DEM cell is lower than its highest rock)
M.STAR = { lat = 39.37909, lon = -104.85605, h = 2004, near = 60000 }
M.enabled = true

local lastCheck = -1e9
local places = nil          -- { {lat, lon, rank, name}, ... } (read once)
local objs = {}             -- key -> { id, anchorId }
M.errors = 0

-- The populated places of maplayers.js (its "places": [name, lon, lat, rank, capital]).
local function loadPlaces()
  if places then return places end
  places = {}
  local text = readFile(M.PLACES_FILE)
  local at = text and text:find('export default ', 1, true)
  if not at then return places end
  local ok, data = pcall(jsonDecode, (text:sub(at + #'export default '):gsub(';%s*$', '')))
  if not ok or type(data) ~= 'table' or type(data.places) ~= 'table' then return places end
  for _, p in ipairs(data.places) do
    local rank = tonumber(p[4])
    if rank and rank <= M.GLOW_RANK_MAX and tonumber(p[2]) and tonumber(p[3]) then
      places[#places + 1] = { lat = tonumber(p[3]), lon = tonumber(p[2]), rank = rank, name = tostring(p[1]) }
    end
  end
  return places
end

-- Pure: the glow of a place of rank r: radius, height (m).
function M.glowSize(r)
  local R = M.GLOW_R[math.max(0, math.min(M.GLOW_RANK_MAX, math.floor(r or M.GLOW_RANK_MAX)))]
  return R, math.max(M.GLOW_H_MIN_M, R * M.GLOW_H_SHARE)
end

local function drop(key)
  local e = objs[key]
  local o = e and scenetree.findObjectById(e.id)
  if o then o:delete() end
  objs[key] = nil
end

local function make(key, shape, x, y, z, sx, sy, sz)
  local o = createObject('TSStatic')
  if not o then return nil end
  o:setField('shapeName', 0, shape)
  o:setField('collisionType', 0, 'None')
  o:setField('decalType', 0, 'None')
  o:setField('dynamic', 0, 'true')       -- (farfield.lua: a static object added redraws the sun's shadow map)
  o.canSave = false
  o:setScale(vec3(sx, sy, sz))
  o:setPosition(vec3(x, y, z))
  o:registerObject('')
  scenetree.MissionGroup:addObject(o)
  return o:getID()
end

-- Every frame (core.lua; the work every CHECK_S): the glows and the star near the camera, in anchor's frame. The
-- glows only at night (night: nightlights.lua's state): by day their additive cylinders still take the fog pass's
-- in-scattered light (haze.lua's aerosol, the height fog), so each one drew as a pale ring kilometres wide.
function M.update(anchor, anchorId, night)
  local now = os.clockhp()
  if now - lastCheck < M.CHECK_S then return end
  lastCheck = now
  if not anchor or not scenetree.MissionGroup then return end
  local cam = core_camera.getPosition()
  local want = {}
  local function want_(key, lat, lon, h, near)
    local x, y, z = anchor:geodeticToEnu(lat, lon, h)
    if (x - cam.x) ^ 2 + (y - cam.y) ^ 2 > near * near then return nil end
    want[key] = true
    local e = objs[key]
    if e and e.anchorId == anchorId then return nil end
    if e then drop(key) end
    return x, y, z
  end
  if M.enabled and night == true then
    for i, p in ipairs(loadPlaces()) do
      local R, H = M.glowSize(p.rank)
      local key = 'glow' .. i
      -- (the ground there is not known here: the camera's height for it, GLOW_SINK_M under; the frame's curvature
      -- is the anchor's)
      local x, y, z = want_(key, p.lat, p.lon, cam.z, M.GLOW_NEAR_M + R)
      if x then
        local id = make(key, M.GLOW_SHAPE, x, y, z - M.GLOW_SINK_M, R, R, H + M.GLOW_SINK_M)
        if id then objs[key] = { id = id, anchorId = anchorId } else M.errors = M.errors + 1 end
      end
    end
  end
  if M.enabled then
    local s = M.STAR
    local x, y, z = want_('star', s.lat, s.lon, s.h, s.near)
    if x then
      local id = make('star', M.STAR_SHAPE, x, y, z, 1, 1, 1)
      if id then objs.star = { id = id, anchorId = anchorId } else M.errors = M.errors + 1 end
    end
  end
  for key in pairs(objs) do
    if not want[key] then drop(key) end
  end
end

function M.setEnabled(v)
  M.enabled = v and true or false
  lastCheck = -1e9
end

-- A new level or session: the objects go (made again at the next check).
function M.reset()
  for key in pairs(objs) do drop(key) end
  objs, lastCheck = {}, -1e9
end

function M.status()
  local n = 0
  for _ in pairs(objs) do n = n + 1 end
  return { objects = n, places = places and #places or nil, errors = M.errors }
end

return M
