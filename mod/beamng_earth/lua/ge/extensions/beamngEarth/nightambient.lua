-- The night's ambient light (the user, 2026-10-28: "the streetlamps and lights dont actually have any ambient light
-- bouncing ... outside of the direct cone is dark as heck"). The stock real sky (core_celestial, loaded by sky.lua)
-- blacks out the ScatterSky's nightColor and nightFogColor (celestial.lua enableChrome: so its stars stay crisp), and
-- the night's ambient with them: whatever a lamp's cone missed was black. This gives the night a sky again: a city's
-- (sodium and LED light scattered back down by the air: a dim warm brown, the light that lets you see a street's
-- walls and yards between the lamps) near the big places (nightglow.lua's, by their rank), a starlit country one
-- away from them, scaled by the "Night ambient light" setting. Written every CHECK_S when it changed (the sky reads it
-- each frame; the stock night colour blends in as the sun sets, so it is the same field day and night).
--
-- That colour turned out to tint the sky only (the user's drive, 2026-10-29: "so darker than i would expect at night,
-- and my headlights do like nothing"): BeamNG 0.40's ambient is the sky's SH9 (calculateSkySH9Skylut.cs.hlsl: the
-- atmosphere LUT, the night panorama, and a fixed moonless 0.25 mcd/m2), and nightColor is not in it. Two more parts:
--   * The sky's light (SKYFILL): one dim point light SKYFILL.height m over the camera, reaching SKYFILL.radius: what a
--     city's lit sky (or the starlit country's) throws down on roofs, roads, yards and cars round the camera, warm by
--     the city share. One light: a full-screen pass, no shadows.
--   * The eye's night adaptation (NIGHT_EXPOSURE, on the stock auto exposure, PostEffectLocalExposureObject): by day
--     its highlight protection closes the exposure down by up to 2 EV when the brightest 3% of the frame clips, and
--     at night that 3% is our lamps, windows and glows, so the street was metered for the lamp lenses and everything
--     else (and a car's headlights' pool) sank to black. At night the protection is eased, darker pixels count in the
--     metering and the exposure opens a little (on top of the player's own EV compensation), back to the stock values
--     by day and when leaving the level.
local M = {}

local nightglow = require('beamngEarth/nightglow')

M.CHECK_S = 2.0
-- the night's colour (ScatterSky nightColor: the night's ambient and sky) and its fog's, linear RGB
M.URBAN = { 0.060, 0.046, 0.034 }
M.URBAN_FOG = { 0.040, 0.032, 0.025 }
M.RURAL = { 0.010, 0.011, 0.016 }
M.RURAL_FOG = { 0.006, 0.007, 0.010 }
M.STRENGTH = { off = 0, dim = 0.5, normal = 1.0, bright = 2.0 }
M.FULL_R = 1.0              -- a place's city light is full within this many of its glow radii (nightglow.glowSize)
M.GONE_R = 3.0              -- ... and gone this many out
M.BIG_R = 14000             -- (a place's weight: its glow radius over this, square-rooted: Denver 1, Boulder ~0.5)
M.SHARE_STEP = 0.05
-- the sky's light: urban (warm) and rural (cool) brightness and colour, mixed by the city share, times the setting
M.SKYFILL = { height = 150, radius = 700, urban = 180, rural = 48, urbanColor = { 1.0, 0.74, 0.50 },
  ruralColor = { 0.62, 0.72, 1.0 } }
-- the auto exposure at night (field = value; exposureBiasEV: added to the player's GraphicEVCompensation)
M.NIGHT_EXPOSURE = { highlightProtectStrength = 0.2, darkPixelMaxLogLuminance = -8, darkPixelMinLogLuminance = -16 }
M.NIGHT_BIAS_EV = 0.75
-- ... and its properties at night (engine properties, read and written as o.name, not fields: targetLuminance,
-- autoTargetLuminance, autoExposure, manualEV). The stock auto exposure aims a dark scene at a lower luminance
-- (EV-driven target), so a suburb stays near black whatever the bias (the night slot, 2026-10-29: EV +1.5 changed
-- nothing in Frederick). NIGHT_TARGET_X: the night's target luminance times the stock one, its auto target off (nil:
-- the stock target). Both untuned: the night slot compares them.
M.NIGHT_PROPS = {}
M.NIGHT_TARGET_X = nil
M.mode = 'normal'
M.errors = 0

local lastCheck = -1e9
local cache = { anchorId = nil, pts = nil }   -- the places in the anchor's frame: { {x, y, R, w}, ... }
local written = nil                            -- the colour string last written (nil: none)

-- Pure: the share (0..1) of the city's light at (x, y) from places pts ({ {x, y, R, w} }): the strongest one's,
-- full within FULL_R of its radius, fading smoothly to nothing at GONE_R.
function M.urbanShare(pts, x, y)
  local best = 0
  for _, p in ipairs(pts or {}) do
    local d = math.sqrt((p[1] - x) ^ 2 + (p[2] - y) ^ 2) / p[3]
    local t = math.max(0, math.min(1, (M.GONE_R - d) / (M.GONE_R - M.FULL_R)))
    local s = p[4] * t * t * (3 - 2 * t)
    if s > best then best = s end
  end
  return best
end

-- Pure: the colour string for a share and strength k: rural to urban, times k.
function M.colorFor(rural, urban, share, k)
  local c = {}
  for i = 1, 3 do c[i] = string.format('%.4f', (rural[i] + (urban[i] - rural[i]) * share) * k) end
  return c[1] .. ' ' .. c[2] .. ' ' .. c[3] .. ' 1'
end

-- Pure: is a colour field's value (a string, 'r g b a') within 0.002 of the colour string c?
function M.same(field, c)
  local a, b = {}, {}
  for v in tostring(field or ''):gmatch('[^%s]+') do a[#a + 1] = tonumber(v) end
  for v in c:gmatch('[^%s]+') do b[#b + 1] = tonumber(v) end
  for i = 1, 3 do
    if not a[i] or not b[i] or math.abs(a[i] - b[i]) > 0.002 then return false end
  end
  return true
end

local function sky()
  local names = scenetree.findClassObjects and scenetree.findClassObjects('ScatterSky')
  return names and names[1] and scenetree.findObject(names[1]) or nil
end

local function points(anchor, anchorId)
  if cache.anchorId == anchorId and cache.pts then return cache.pts end
  local pts = {}
  for _, p in ipairs(nightglow.places()) do
    local R = nightglow.glowSize(p.rank)
    local x, y = anchor:geodeticToEnu(p.lat, p.lon, 0)
    pts[#pts + 1] = { x, y, R, math.min(1, math.sqrt(R / M.BIG_R)) }
  end
  cache.anchorId, cache.pts = anchorId, pts
  return pts
end

local fillId = nil
local fillLook = nil      -- the brightness / colour string last given the sky light

-- Pure: the sky light's brightness and colour string for a city share and strength k.
function M.skyFillFor(share, k)
  local F = M.SKYFILL
  local b = (F.rural + (F.urban - F.rural) * share) * k
  local c = {}
  for i = 1, 3 do c[i] = string.format('%.3f', F.ruralColor[i] + (F.urbanColor[i] - F.ruralColor[i]) * share) end
  return b, c[1] .. ' ' .. c[2] .. ' ' .. c[3] .. ' 1'
end

local function fillObj()
  local o = fillId and scenetree.findObjectById(fillId)
  if o then return o end
  o = createObject('PointLight')
  if not o then return nil end
  o:setField('radius', 0, tostring(M.SKYFILL.radius))
  o:setField('castShadows', 0, 'false')
  o:setField('isEnabled', 0, 'false')
  o.canSave = false
  o:registerObject('')
  if scenetree.MissionGroup then scenetree.MissionGroup:addObject(o) end
  fillId, fillLook = o:getID(), nil
  return o
end

-- The sky light over the camera (every frame at night: it follows the camera), off by day and when off.
local function stepFill(on, share, k)
  if not on then
    local o = fillId and scenetree.findObjectById(fillId)
    if o and fillLook then o:setField('isEnabled', 0, 'false'); fillLook = nil end
    return
  end
  local o = fillObj()
  if not o then return end
  local cam = core_camera.getPosition()
  o:setPosition(vec3(cam.x, cam.y, cam.z + M.SKYFILL.height))
  local b, c = M.skyFillFor(share or 0, k)
  local look = string.format('%.2f', b) .. '|' .. c
  if look ~= fillLook then
    o:setField('brightness', 0, string.format('%.2f', b))
    o:setField('color', 0, c)
    o:setField('radius', 0, tostring(M.SKYFILL.radius))
    if not fillLook then o:setField('isEnabled', 0, 'true') end
    fillLook = look
  end
end

local dayExposure = nil   -- the auto exposure's own values, kept while ours are on (nil: ours are not on)

local function exposureObj() return scenetree.findObject('PostEffectLocalExposureObject') end

local function playerBias()
  local okS, st = pcall(function() return settings.getValue('GraphicEVCompensation', 0) end)
  return okS and tonumber(st) or 0
end

-- The auto exposure's night values on (night) or the kept ones back; checked every CHECK_S (the graphics settings
-- write the bias back when the player changes it).
local dayProps = {}         -- the properties' own values, kept while ours are on
local NONE = {}             -- (a property that read nil: nothing to put back)

local function differs(a, b)
  if type(a) == 'number' and type(b) == 'number' then return math.abs(a - b) > 1e-4 end
  return a ~= b
end

-- The night's properties: NIGHT_PROPS, and the target luminance by NIGHT_TARGET_X (from the stock one, kept).
local function nightProps(o)
  local want = {}
  for k, v in pairs(M.NIGHT_PROPS) do want[k] = v end
  if M.NIGHT_TARGET_X then
    for _, k in ipairs({ 'targetLuminance', 'autoTargetLuminance' }) do
      if dayProps[k] == nil then local was = o[k]; if was == nil then was = NONE end; dayProps[k] = was end
    end
    local base = tonumber(dayProps.targetLuminance)
    if base then
      want.autoTargetLuminance = false
      want.targetLuminance = base * M.NIGHT_TARGET_X
    end
  end
  return want
end

local function restoreProps(o, keep)
  for k, v in pairs(dayProps) do
    if not (keep and keep[k] ~= nil) then
      if v ~= NONE then o[k] = v end
      dayProps[k] = nil
    end
  end
end

local function stepExposure(night)
  local o = exposureObj()
  if not o then return end
  if night then
    if not dayExposure then
      dayExposure = {}
      for k in pairs(M.NIGHT_EXPOSURE) do dayExposure[k] = o:getField(k, 0) end
    end
    for k, v in pairs(M.NIGHT_EXPOSURE) do
      if tonumber(o:getField(k, 0)) ~= v then o:setField(k, 0, tostring(v)) end
    end
    local want = playerBias() + M.NIGHT_BIAS_EV
    if math.abs((tonumber(o.exposureBiasEV) or 0) - want) > 1e-3 then o.exposureBiasEV = want end
    local props = nightProps(o)
    restoreProps(o, props)           -- (a property no longer ours: its own value back)
    for k, v in pairs(props) do
      if dayProps[k] == nil then local was = o[k]; if was == nil then was = NONE end; dayProps[k] = was end
      if differs(o[k], v) then o[k] = v end
    end
  elseif dayExposure then
    for k, v in pairs(dayExposure) do if v ~= nil then o:setField(k, 0, tostring(v)) end end
    o.exposureBiasEV = playerBias()
    restoreProps(o, nil)
    dayExposure = nil
  end
end

-- Every frame (nightlights.update; the work every CHECK_S). anchor, anchorId: the frame of the places; night:
-- nightlights.lua's state.
function M.update(anchor, anchorId, night)
  local k = M.STRENGTH[M.mode] or 1
  local lit = night == true and k > 0
  local okF = pcall(stepFill, lit, M.share, k)
  if not okF then M.errors = M.errors + 1 end
  local now = os.clockhp()
  if now - lastCheck < M.CHECK_S then return end
  lastCheck = now
  local okE = pcall(stepExposure, lit)
  if not okE then M.errors = M.errors + 1 end
  if k <= 0 or not anchor then return end
  local o = sky()
  if not o then return end
  local cam = core_camera.getPosition()
  -- (in steps of SHARE_STEP: driving through a town writes the sky a few times, not every check)
  local share = math.floor(M.urbanShare(points(anchor, anchorId), cam.x, cam.y) / M.SHARE_STEP + 0.5) * M.SHARE_STEP
  M.share = share
  local c = M.colorFor(M.RURAL, M.URBAN, share, k)
  -- (core_celestial writes black over it once per sky: read back, not only remembered)
  if c == written and M.same(o:getField('nightColor', 0), c) then return end
  local ok = pcall(function()
    o:setField('nightColor', 0, c)
    o:setField('nightFogColor', 0, M.colorFor(M.RURAL_FOG, M.URBAN_FOG, share, k))
  end)
  if ok then written = c else M.errors = M.errors + 1 end
end

-- The "Night ambient light" setting: off (the real sky's black night), dim, normal, bright.
function M.setMode(v)
  local was = M.mode
  M.mode = M.STRENGTH[v] and v or 'normal'
  lastCheck, written, fillLook = -1e9, nil, nil
  if M.mode == 'off' and was ~= 'off' then           -- back to the real sky's
    local o = sky()
    if o then pcall(function() o:setField('nightColor', 0, '0 0 0 1') o:setField('nightFogColor', 0, '0 0 0 1') end) end
  end
end

-- A new level or leaving ours: the places' frame and the sky are new, the sky light goes, the exposure's own values
-- back.
function M.reset()
  pcall(stepExposure, false)
  local o = fillId and scenetree.findObjectById(fillId)
  if o then o:delete() end
  fillId, fillLook = nil, nil
  lastCheck, written = -1e9, nil
  cache = { anchorId = nil, pts = nil }
end

function M.status()
  local o = exposureObj()
  local ex = o and { bias = o.exposureBiasEV, target = o.targetLuminance, autoTarget = o.autoTargetLuminance,
                     auto = o.autoExposure, manualEV = o.manualEV } or nil
  return { mode = M.mode, share = M.share, written = written, errors = M.errors, fill = fillLook,
           nightExposure = dayExposure ~= nil, exposure = ex }
end

return M
