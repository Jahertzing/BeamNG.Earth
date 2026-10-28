-- BeamNG.Earth: distance haze (the hazeStrength setting; horizon.lua's M1h TODO). Real visibility on a clear
-- Front Range day is 100-200 km and limited by aerosols, so the Rockies 60 km away are blue-grey silhouettes
-- that fade into the sky; our level had the stock "Earth" aerosol (atmoMieScattering 0.004 / km at sea level),
-- which at Denver's 1.6 km leaves ~0.001 / km, a sky as clean as a mountain top's: far ranges and the skyline
-- looked cut out against the sky.
--
-- BeamNG 0.40's sky (ScatterSky "atmo*" fields, the coconut sky LUTs) already does aerial perspective: a camera
-- volume of in-scattered light and transmittance, 32 slices of 4 km (shaders/common/sky/skyFog.h.hlsl), applied
-- to every scene pixel by the fog pass (shaders/common/postFx/fog.cs.hlsl) on top of the LevelInfo height fog
-- (weather.lua: from the real visibility). This module gives that atmosphere a boundary-layer aerosol:
--   * Mie scattering at the player's ground = BG_KM + strength * HAZE_KM (1 / km), with a weak Angstrom
--     wavelength dependence (fine aerosol scatters blue a little more: haze reads blue-grey, not milk);
--   * the engine's Mie density falls off from z = 0 (sea level) with atmoMieScaleHeight, so the coefficient is
--     raised by exp(groundKm / SCALE_KM) to put that value at the ground under the player (as weather.lua's
--     fog compensation does for the height fog), re-checked as the ground height changes;
--   * extinction = scattering / SSA (single-scattering albedo; the stock ratio 0.00444 / 0.003996 = 0.9).
-- Strength 0 is the clean background (BG_KM at the ground). The level's own values come back when our level
-- ends (M.restore).
-- Everything runs in the BeamNG.Earth level only.
local M = {}

M.BG_KM = 0.004          -- background aerosol at the ground (1 / km): the stock value, clean continental air
M.HAZE_KM = 0.045        -- extra at strength 1: with Rayleigh ~0.0095 at 1.6 km, visibility ~70 km
M.SCALE_KM = 1.6         -- aerosol scale height: the high plains' mixed layer
M.ANGSTROM = 0.8         -- wavelength exponent across 680 / 550 / 440 nm (0 = grey)
M.SSA = 0.9              -- single-scattering albedo
M.PHASE_G = 0.76         -- Mie phase anisotropy (stock 0.8): a slightly wider glow around the sun
M.GROUND_MAX_KM = 4.5    -- the compensation stops here (a plane high up keeps the haze below it)
M.CHECK_S = 2            -- re-check the ground height this often
M.REL_EPS = 0.02         -- a relative change smaller than this is not written
M.WAVELENGTH = { 680, 550, 440 }

-- Pure: the per-channel factor of the Angstrom law, relative to 550 nm.
function M.spectral(angstrom)
  local out = {}
  for i, l in ipairs(M.WAVELENGTH) do out[i] = (550 / l) ^ (angstrom or 0) end
  return out
end

-- Pure: the ScatterSky fields for haze strength s (0..1) at ground height groundM (m above sea level):
-- { atmoMieScattering = {r, g, b}, atmoMieExtinction = {r, g, b}, atmoMieScaleHeight, atmoMiePhaseG }.
function M.fieldsFor(s, groundM)
  s = math.min(math.max(tonumber(s) or 0, 0), 1)
  local gKm = math.min(math.max((tonumber(groundM) or 0) / 1000, 0), M.GROUND_MAX_KM)
  local atGround = M.BG_KM + s * M.HAZE_KM
  local base = atGround * math.exp(gKm / M.SCALE_KM)
  local spec = M.spectral(M.ANGSTROM)
  local sca, ext = {}, {}
  for i = 1, 3 do
    sca[i] = base * spec[i]
    ext[i] = sca[i] / M.SSA
  end
  return { atmoMieScattering = sca, atmoMieExtinction = ext, atmoMieScaleHeight = M.SCALE_KM,
    atmoMiePhaseG = M.PHASE_G }
end

-- Pure: does `want` differ from `have` (both vectors or numbers) by more than REL_EPS anywhere?
function M.differs(want, have)
  if type(want) ~= 'table' then want = { want } end
  if type(have) ~= 'table' then have = { have } end
  for i = 1, #want do
    local a, b = tonumber(want[i]), tonumber(have[i])
    if not b or math.abs(a - b) > M.REL_EPS * math.max(math.abs(a), 1e-6) then return true end
  end
  return false
end

local function parseVec(s)
  local out = {}
  for x in tostring(s or ''):gmatch('[-%d%.eE]+') do out[#out + 1] = tonumber(x) end
  return out
end

local function vstr(v)
  if type(v) ~= 'table' then return string.format('%.9g', v) end
  return string.format('%.9g %.9g %.9g', v[1], v[2], v[3])
end

local function sky()
  local names = scenetree.findClassObjects('ScatterSky')
  return names and names[1] and scenetree.findObject(names[1]) or nil
end

local FIELDS = { 'atmoMieScattering', 'atmoMieExtinction', 'atmoMieScaleHeight', 'atmoMiePhaseG' }
local saved = nil        -- { id, fields = {name = string} }: the level's values before the first write
local lastCheck = -math.huge
local strength = 0.5
M.last = nil             -- the fields last written (status)

function M.setStrength(s) strength = math.min(math.max(tonumber(s) or 0, 0), 1); lastCheck = -math.huge end
function M.strength() return strength end

-- Write `want` to the sky (only the fields that moved), remembering the level's values once per sky object.
function M.apply(want)
  local s = sky()
  if not s then return false end
  if not saved or saved.id ~= s:getID() then
    saved = { id = s:getID(), fields = {} }
    for _, k in ipairs(FIELDS) do saved.fields[k] = s:getField(k, 0) end
  end
  local wrote = false
  for _, k in ipairs(FIELDS) do
    local have = parseVec(s:getField(k, 0))
    if M.differs(want[k], have) then
      s:setField(k, 0, vstr(want[k]))
      wrote = true
    end
  end
  if wrote and s.postApply then s:postApply() end
  M.last = want
  return wrote
end

-- The ground under the player: the weather module's (it casts for its fog), else the player's height.
local function groundM(playerZ)
  local w = rawget(_G, 'beamngEarth_weather')
  if w and w.status then
    local ok, st = pcall(w.status)
    if ok and type(st) == 'table' and tonumber(st.groundZ) then return tonumber(st.groundZ) end
  end
  return tonumber(playerZ) or 0
end

-- Every frame: in our level, the haze for the strength and the ground under the player, checked every CHECK_S.
function M.update(inOurLevel, playerZ)
  if not inOurLevel then return end
  local t = os.clock()
  if t - lastCheck < M.CHECK_S then return end
  lastCheck = t
  M.apply(M.fieldsFor(strength, groundM(playerZ)))
end

-- Leaving our level (or the module off): the level's own sky values back.
function M.restore()
  if not saved then return false end
  local s = scenetree.findObjectById(saved.id)
  if s then
    for k, v in pairs(saved.fields) do s:setField(k, 0, v) end
    if s.postApply then s:postApply() end
  end
  saved, M.last, lastCheck = nil, nil, -math.huge
  return s ~= nil
end

return M
