-- BeamNG.Earth colour grade (the colorGrade setting): a post effect of our own on the tone mapped image,
-- shaders/beamngEarth/grade.cs.hlsl. BeamNG 0.40's combine pass skips the stock colour-correction ramp and HSL
-- factors (combinePass.cs.hlsl main_coconut), so without it our level shows the aerial photos and the sky
-- exactly as tone mapped: flat greens and browns, a pale sky. The effect is created once (like the game's own
-- client/postFx/*.lua), enabled in our level with the preset's constants and disabled when it ends; the
-- player's graphics settings are never touched.
local M = {}

M.SHADER = 'shaders/beamngEarth/grade.cs.hlsl'
M.PFX_NAME = 'BeamNGEarthGradePostFx'
M.CHECK_S = 2           -- re-assert enabled / constants this often (a graphics-settings change rebuilds post effects)

-- Presets: balance = white-balance gains and strength; tone = contrast, pivot, saturation, vibrance;
-- shadows / highlights = tint (0.5 = none) and amount; misc = vignette amount, radius, share of the contrast below
-- the pivot (the probe's dusk shots: full contrast crushed the foreground to black).
M.PRESETS = {
  -- dry high-plains light: a touch warm, firmer contrast, greens and browns lifted without neon, cool shadows
  colorado = { balance = { 1.025, 1.0, 0.965, 1 }, tone = { 1.15, 0.38, 1.08, 0.35 },
    shadows = { 0.47, 0.495, 0.545, 0.55 }, highlights = { 0.545, 0.515, 0.47, 0.45 }, misc = { 0.05, 0.95, 0.4, 0 } },
  -- only the photos' flatness taken out
  natural = { balance = { 1.0, 1.0, 1.0, 1 }, tone = { 1.06, 0.42, 1.0, 0.18 },
    shadows = { 0.5, 0.5, 0.5, 0 }, highlights = { 0.5, 0.5, 0.5, 0 }, misc = { 0.03, 0.95, 0.6, 0 } },
  vivid = { balance = { 1.02, 1.0, 0.97, 1 }, tone = { 1.22, 0.38, 1.16, 0.45 },
    shadows = { 0.465, 0.49, 0.55, 0.65 }, highlights = { 0.55, 0.52, 0.465, 0.55 }, misc = { 0.07, 0.92, 0.5, 0 } },
}
M.NAMES = { 'off', 'natural', 'colorado', 'vivid' }
-- Every preset blends to this at night (nightlights.lua's state, faded over NIGHT_FADE_S): the day contrast and
-- vibrance sank the unlit ground to black (look probe run5, Denver 20:30), so a softer toe, a faint moonlit-blue
-- lift in the shadows, cooler balance, the sodium lamps' warmth kept in the highlights.
M.NIGHT = { balance = { 0.97, 1.0, 1.04, 1 }, tone = { 1.08, 0.30, 0.92, 0.25 },
  shadows = { 0.49, 0.505, 0.545, 0.7 }, highlights = { 0.54, 0.515, 0.47, 0.35 }, misc = { 0.05, 0.95, 0, 0 } }
M.NIGHT_FADE_S = 10

-- Pure: the shader constants of a preset, as setShaderConst strings; night (0..1) blends it to M.NIGHT.
function M.constsFor(name, night)
  local p = M.PRESETS[name]
  if not p then return nil end
  local k = math.min(math.max(tonumber(night) or 0, 0), 1)
  local function s(key)
    local a, b = p[key], M.NIGHT[key]
    local v = {}
    for i = 1, 4 do v[i] = a[i] + (b[i] - a[i]) * k end
    return string.format('%.6g %.6g %.6g %.6g', v[1], v[2], v[3], v[4])
  end
  return { ['$gradeBalance'] = s('balance'), ['$gradeTone'] = s('tone'), ['$gradeShadows'] = s('shadows'),
    ['$gradeHighlights'] = s('highlights'), ['$gradeMisc'] = s('misc') }
end

local mode = 'colorado'
local active = false
local lastCheck = -math.huge
local nightK = 0          -- the night blend now (moves towards nightlights.lua's state)

-- Is it night (nightlights.lua's state; false without it)?
local function isNight()
  local ok, nl = pcall(require, 'beamngEarth/nightlights')
  return ok and type(nl) == 'table' and nl.night == true
end

local function create()
  local pfx = scenetree.findObject(M.PFX_NAME)
  if pfx then return pfx end
  if not scenetree.findObject('BeamNGEarthGradeShader') then
    local sh = createObject('ShaderData')
    sh.DXComputShaderName = M.SHADER
    sh.pixVersion = 5.0
    sh:registerObject('BeamNGEarthGradeShader')
  end
  if not scenetree.findObject('BeamNGEarthGradeStateBlock') then
    local sb = createObject('GFXStateBlockData')
    sb.zDefined = true
    sb.zEnable = false
    sb.zWriteEnable = false
    sb.samplersDefined = true
    sb:setField('samplerStates', 0, 'SamplerClampPoint')
    sb:registerObject('BeamNGEarthGradeStateBlock')
  end
  pfx = createObject('PostEffect')
  pfx:setField('isEnabled', 0, '0')
  pfx.allowReflectPass = false
  pfx:setField('renderTime', 0, 'PFXAfterBin')
  pfx:setField('renderBin', 0, 'AfterPostFX')    -- the tone mapped image (as smaa.lua's T2x pass)
  pfx:setField('shader', 0, 'BeamNGEarthGradeShader')
  pfx:setField('stateBlock', 0, 'BeamNGEarthGradeStateBlock')
  pfx:setField('texture', 0, '$backBuffer')
  pfx:setField('target', 0, '$backBuffer')
  pfx:registerObject(M.PFX_NAME)
  return pfx
end

local function enabledOf(pfx)
  local ok, v = pcall(function() return pfx:isEnabled() end)
  return ok and v and true or false
end

local function setConsts(pfx)
  for k, v in pairs(M.constsFor(mode, nightK) or {}) do pfx:setShaderConst(k, v) end
end

function M.setMode(m)
  if m ~= 'off' and not M.PRESETS[m] then return false end
  mode = m
  lastCheck = -math.huge
  return true
end
function M.getMode() return mode end

-- Every frame: in our level the effect on with the preset's constants (checked every CHECK_S), else off.
function M.update(inOurLevel)
  if not inOurLevel then
    if active then M.restore() end
    return
  end
  local t = os.clock()
  if t - lastCheck < M.CHECK_S then return end
  local dt = math.min(t - lastCheck, M.NIGHT_FADE_S)
  lastCheck = t
  local target = isNight() and 1 or 0
  local step = dt / M.NIGHT_FADE_S
  nightK = target > nightK and math.min(target, nightK + step) or math.max(target, nightK - step)
  if mode == 'off' then
    if active then M.restore() end
    return
  end
  local ok, pfx = pcall(create)
  if not ok or not pfx then
    log('W', 'beamngEarth', 'grade: could not create the post effect: ' .. tostring(pfx))
    mode = 'off'
    return
  end
  setConsts(pfx)
  if not enabledOf(pfx) then pfx:enable() end
  active = true
end

-- Leaving our level: the effect off (it stays registered, as the game's own effects do).
function M.restore()
  lastCheck = -math.huge
  if not active then return end
  active = false
  local pfx = scenetree.findObject(M.PFX_NAME)
  if pfx and enabledOf(pfx) then pfx:disable() end
end

function M.isActive() return active end
function M.nightBlend() return nightK end

return M
