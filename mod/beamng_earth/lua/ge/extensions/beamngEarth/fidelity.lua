-- BeamNG.Earth render fidelity (the renderFidelity setting): the lighting levers BeamNG 0.40 has but tunes for its
-- own small, enclosed maps, set for ours while our level runs, plus a post effect of our own.
--
--   ao      the game's ambient occlusion (XeGTAO, SSAOPostFx) at a city's scale: a 1.5 m radius darkens the inside of a
--           window frame but not a street between buildings, the foot of a wall or the ground under a tree; a wider
--           radius, a firmer contrast, 64 samples, and specular occlusion (its bent normals keep reflections out of the
--           corners they can't reach: the bright rims along kerbs and under eaves).
--   shafts  sun shafts (shaders/beamngEarth/sunshafts*.cs.hlsl, three compute passes on the HDR image before tone
--           mapping, where the game's depth of field runs): the sky around a low sun blurred towards it, so the light
--           breaks round buildings and through trees at golden hour.
--
-- The player's graphics settings are never written: the objects' runtime values are, and leaving the level puts the
-- player's values back (the ones read before the first change; the AO samples and specular occlusion from settings).
local M = {}

M.MODES = { 'off', 'on' }
M.CHECK_S = 2           -- re-assert this often (a graphics-settings change rebuilds post effects)

-- AO in our level. Stock (ssao.lua): radius 1.5 m, contrast 2, samples 16 (Normal) / 64 (High).
M.AO = { radius = 3.5, contrast = 2.3, samples = 64, bentNormals = true }
M.AO_STOCK = { radius = 1.5, contrast = 2 }

-- Sun shafts: intensity, decay per tap, length (share of the way to the sun), falloff power; sky cap, taps per run;
-- and the second (gap-filling) run's length and taps.
M.SHAFTS = { intensity = 0.35, decay = 0.965, length = 0.9, power = 6, cap = 6, taps = 40,
  fillLength = 0.12, fillTaps = 12 }
-- the effect's strength from the sun's elevation (degrees): gone below the horizon, full from 1 to 30, then fading to
-- FULL_HIGH (shafts from a high sun are rarely on screen and look odd when they are)
M.SUN_FADE = { lo = -1, full = 1, high = 30, top = 60, fullHigh = 0.35 }
M.SUN_AZ_SIGN = 1       -- the world's east-west sense of the sky's azimuth (core_celestial's: north +Y, east +X)

M.PFX = 'BeamNGEarthShaftsMaskPfx'
M.SHADERS = {
  mask = 'shaders/beamngEarth/sunshaftsMask.cs.hlsl',
  blur = 'shaders/beamngEarth/sunshaftsBlur.cs.hlsl',
  apply = 'shaders/beamngEarth/sunshaftsApply.cs.hlsl',
}

local mode = 'on'
local features = { ao = true, shafts = true }
local debug = false
local lastCheck = -math.huge
local aoSaved = nil       -- the player's AO values before ours (nil: not applied)
local shaftsOn = false

-- Pure: the unit direction to a sun at azimuth `az` (degrees from north, clockwise) and elevation `el` (degrees over
-- the horizon), in the level's frame (+X east, +Y north, +Z up).
function M.sunVector(az, el, azSign)
  local a, e = math.rad(az or 0), math.rad(el or 0)
  local s = azSign or M.SUN_AZ_SIGN
  return { s * math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e) }
end

-- Pure: the shafts' strength (0..1) for a sun `el` degrees over the horizon.
function M.sunStrength(el)
  local f = M.SUN_FADE
  el = tonumber(el)
  if not el or el <= f.lo then return 0 end
  if el < f.full then return (el - f.lo) / (f.full - f.lo) end
  if el <= f.high then return 1 end
  if el >= f.top then return f.fullHigh end
  return 1 + (f.fullHigh - 1) * (el - f.high) / (f.top - f.high)
end

-- Pure: the shader constants for a sun (azimuth, elevation; degrees) and the SHAFTS table `p` (the fill run's when
-- `fill`), as setShaderConst strings.
function M.shaftConsts(az, el, p, fill, dbg)
  p = p or M.SHAFTS
  local v = M.sunVector(az, el)
  local fmt = function(a, b, c, d) return string.format('%.6g %.6g %.6g %.6g', a, b, c, d) end
  return {
    ['$bneSunDir'] = fmt(v[1], v[2], v[3], M.sunStrength(el)),
    ['$bneShaft'] = fmt(p.intensity, fill and 1 or p.decay, fill and p.fillLength or p.length, p.power),
    ['$bneShaft2'] = fmt(p.cap, fill and p.fillTaps or p.taps, dbg and 1 or 0, 0),
  }
end

-- The drawn sun (the level's ScatterSky, as core_celestial moves it): azimuth, elevation in degrees, or nil.
local function sun()
  if not scenetree or not scenetree.findClassObjects then return nil end
  local names = scenetree.findClassObjects('ScatterSky')
  local o = names and names[1] and scenetree.findObject(names[1])
  if not o then return nil end
  local az, el = tonumber(o.azimuth), tonumber(o.elevation)
  if not az or not el then return nil end
  return az, el
end

------------------------------------------------------------------------------------------
-- ambient occlusion

local function ssaoObject()
  return scenetree and scenetree.findObject and scenetree.findObject('SSAOPostFx') or nil
end

local function stockSsao()
  local ok, m = pcall(require, 'client/postFx/ssao')
  return ok and type(m) == 'table' and m or nil
end

local function settingValue(k)
  if settings and settings.getValue then return settings.getValue(k) end
  return nil
end

local function applyAo()
  local o = ssaoObject()
  if not o then return end
  local okE, on = pcall(function() return o:isEnabled() end)
  if okE and on == false then return end      -- the player turned AO off: theirs
  if not aoSaved then
    local m = stockSsao()
    aoSaved = { radius = m and m.getRadius and m.getRadius() or M.AO_STOCK.radius,
      contrast = m and m.getContrast and m.getContrast() or M.AO_STOCK.contrast }
  end
  -- the stock module's setters keep their getters in step (screenshot tools read them back)
  local m = stockSsao()
  if m and m.setRadius then m.setRadius(M.AO.radius) else o:setRadius(M.AO.radius) end
  if m and m.setContrast then m.setContrast(M.AO.contrast) else o:setContrast(M.AO.contrast) end
  if o.setSamples then o:setSamples(M.AO.samples) end
  o.bentNormals = M.AO.bentNormals
end

local function restoreAo()
  if not aoSaved then return end
  local saved = aoSaved
  aoSaved = nil
  local o = ssaoObject()
  if not o then return end
  local m = stockSsao()
  if m and m.setRadius then m.setRadius(saved.radius) else o:setRadius(saved.radius) end
  if m and m.setContrast then m.setContrast(saved.contrast) else o:setContrast(saved.contrast) end
  if o.setSamples then o:setSamples(settingValue('PostFXSSAOGeneralQuality') == 'High' and 64 or 16) end
  o.bentNormals = settingValue('PostFXSpecularOcclusionEnabled') == true
end

------------------------------------------------------------------------------------------
-- sun shafts

local function shader(name, file)
  if scenetree.findObject(name) then return end
  local sh = createObject('ShaderData')
  sh.DXComputShaderName = file
  sh.pixVersion = 5.0
  sh:registerObject(name)
end

local function stateBlock()
  local name = 'BeamNGEarthShaftsStateBlock'
  if scenetree.findObject(name) then return name end
  local sb = createObject('GFXStateBlockData')
  sb.zDefined = true
  sb.zEnable = false
  sb.zWriteEnable = false
  sb.samplersDefined = true
  sb:setField('samplerStates', 0, 'SamplerClampLinear')
  sb:setField('samplerStates', 1, 'SamplerClampLinear')
  sb:setField('samplerStates', 2, 'SamplerClampPoint')
  sb:registerObject(name)
  return name
end

-- The chain (dof.lua's pattern): the mask at a quarter of the resolution, the two blur runs on it, the apply pass on
-- the scene. Created once; the children follow the parent's enable.
local function createShafts()
  local pfx = scenetree.findObject(M.PFX)
  if pfx then return pfx end
  shader('BeamNGEarthShaftsMaskShader', M.SHADERS.mask)
  shader('BeamNGEarthShaftsBlurShader', M.SHADERS.blur)
  shader('BeamNGEarthShaftsApplyShader', M.SHADERS.apply)
  local sb = stateBlock()
  local function effect(name, shaderName, textures, target, fmt)
    local e = createObject('PostEffect')
    e.allowReflectPass = false
    e:setField('shader', 0, shaderName)
    e:setField('stateBlock', 0, sb)
    for i, t in ipairs(textures) do e:setField('texture', i - 1, t) end
    e:setField('target', 0, target)
    if fmt then e:setField('targetFormat', 0, fmt) end
    return e
  end
  local HDR = 'GFXFormatR16G16B16A16F'
  pfx = effect(M.PFX, 'BeamNGEarthShaftsMaskShader', { '$backBuffer', '#prepass[RT0]', '#prepass[Depth]' },
    '#bneShaftMask', HDR)
  pfx:setField('isEnabled', 0, '0')
  pfx:setField('renderTime', 0, 'PFXAfterBin')
  pfx:setField('renderBin', 0, 'UpscaleDispatch')    -- HDR, before tone mapping (dof.lua's place)
  pfx.renderPriority = 0.05
  pfx:setField('targetScale', 0, '0.25 0.25')
  pfx:registerObject(M.PFX)
  local blur = effect('BeamNGEarthShaftsBlurPfx', 'BeamNGEarthShaftsBlurShader', { '#bneShaftMask' }, '$outTex', HDR)
  blur:registerObject('BeamNGEarthShaftsBlurPfx')
  pfx:add(blur)
  local fill = effect('BeamNGEarthShaftsFillPfx', 'BeamNGEarthShaftsBlurShader', { '$inTex' }, '#bneShaftBlur', HDR)
  fill:registerObject('BeamNGEarthShaftsFillPfx')
  pfx:add(fill)
  local apply = effect('BeamNGEarthShaftsApplyPfx', 'BeamNGEarthShaftsApplyShader', { '$backBuffer', '#bneShaftBlur' },
    '$backBuffer')
  apply:registerObject('BeamNGEarthShaftsApplyPfx')
  pfx:add(apply)
  return pfx
end

local function enabledOf(pfx)
  local ok, v = pcall(function() return pfx:isEnabled() end)
  return ok and v and true or false
end

local function setShaftConsts(az, el)
  local main = M.shaftConsts(az, el, M.SHAFTS, false, debug)
  local fillC = M.shaftConsts(az, el, M.SHAFTS, true, debug)
  for _, n in ipairs({ M.PFX, 'BeamNGEarthShaftsBlurPfx', 'BeamNGEarthShaftsApplyPfx' }) do
    local o = scenetree.findObject(n)
    if o then for k, v in pairs(main) do o:setShaderConst(k, v) end end
  end
  local f = scenetree.findObject('BeamNGEarthShaftsFillPfx')
  if f then for k, v in pairs(fillC) do f:setShaderConst(k, v) end end
end

local function updateShafts()
  local az, el = sun()
  if not az then return end
  local pfx = createShafts()
  setShaftConsts(az, el)
  local want = M.sunStrength(el) > 0
  if want and not enabledOf(pfx) then pfx:enable() elseif not want and enabledOf(pfx) then pfx:disable() end
  shaftsOn = want
end

local function stopShafts()
  shaftsOn = false
  local pfx = scenetree and scenetree.findObject and scenetree.findObject(M.PFX)
  if pfx and enabledOf(pfx) then pfx:disable() end
end

------------------------------------------------------------------------------------------
-- live reflections (experimental, by hand: M.captureReflection() from the console or the look probe)
--
-- The level's reflections come from one stock cubemap (LevelInfo globalEnviromentMap BNG_Sky_01_cubemap: a generic
-- sky and hills), so glass, car paint and wet roads mirror a place that isn't there. The game can capture a cubemap
-- from the camera (captureCameraCubemap, the editor's cubemap tool); captured where the player is and made the
-- level's environment map (normalised: the sky's live ambient scales it, so it follows the time of day), the city
-- reflects the city. Two file sets in turn, so a capture never overwrites the faces in use.

M.REFLECT = { name = 'BeamNGEarthLiveReflection', dir = '/temp/beamngEarth/reflect/', res = 256, waitS = 5 }
local reflect = { slot = 'b', pending = nil, savedEnv = nil }

local function levelInfo()
  if not scenetree or not scenetree.findClassObjects then return nil end
  local names = scenetree.findClassObjects('LevelInfo')
  return names and names[1] and scenetree.findObject(names[1]) or nil
end

local function facesReady(prefix)
  if not FS or not FS.fileExists then return true end
  for i = 0, 5 do
    if not FS:fileExists(prefix .. i .. '.hdr.dds') then return false end
  end
  return true
end

local function assignReflection(prefix)
  local cm = scenetree.findObject(M.REFLECT.name)
  if not cm then
    cm = createObject('CubemapData')
    cm:setField('normalize', 0, '1')
    for i = 0, 5 do cm:setField('cubeFace', i, prefix .. i .. '.hdr.dds') end
    cm:registerObject(M.REFLECT.name)
  else
    for i = 0, 5 do cm:setField('cubeFace', i, prefix .. i .. '.hdr.dds') end
  end
  if cm.updateFaces then pcall(cm.updateFaces, cm) end
  local li = levelInfo()
  if not li then return false end
  if not reflect.savedEnv then reflect.savedEnv = li:getField('globalEnviromentMap', 0) end
  li:setField('globalEnviromentMap', 0, M.REFLECT.name)
  if li.postApply then pcall(li.postApply, li) end
  log('I', 'beamngEarth', 'fidelity: live reflection from ' .. prefix)
  return true
end

-- Capture a cubemap at the camera now; it becomes the level's environment map when its faces are on disk.
function M.captureReflection(res)
  if type(captureCameraCubemap) ~= 'function' then return false, 'no captureCameraCubemap in this build' end
  reflect.slot = reflect.slot == 'a' and 'b' or 'a'
  local prefix = M.REFLECT.dir .. reflect.slot .. '_skybox'
  if FS and FS.directoryCreate then FS:directoryCreate(M.REFLECT.dir, true) end
  captureCameraCubemap(prefix, tonumber(res) or M.REFLECT.res)
  reflect.pending = { prefix = prefix, t = os.clock() }
  return true, prefix
end

local function updateReflection()
  local p = reflect.pending
  if not p then return end
  if facesReady(p.prefix) then
    reflect.pending = nil
    assignReflection(p.prefix)
  elseif os.clock() - p.t > M.REFLECT.waitS then
    reflect.pending = nil
    log('W', 'beamngEarth', 'fidelity: the reflection capture wrote no faces at ' .. p.prefix)
  end
end

-- The level's own environment map back.
function M.restoreReflection()
  reflect.pending = nil
  if not reflect.savedEnv then return false end
  local li = levelInfo()
  if li then
    li:setField('globalEnviromentMap', 0, reflect.savedEnv)
    if li.postApply then pcall(li.postApply, li) end
  end
  reflect.savedEnv = nil
  return true
end

------------------------------------------------------------------------------------------
-- public

function M.setMode(m)
  local ok = false
  for _, v in ipairs(M.MODES) do ok = ok or v == m end
  if not ok then return false end
  mode = m
  lastCheck = -math.huge
  return true
end
function M.getMode() return mode end

-- One feature on or off ('ao', 'shafts'; the look probe's A/B), and the shafts' debug view (mask and the sun's ring).
function M.setFeature(name, on)
  if features[name] == nil then return false end
  features[name] = on and true or false
  lastCheck = -math.huge
  return true
end
function M.setDebug(on) debug = on and true or false; lastCheck = -math.huge end

-- Every frame: in our level, the features on (checked every CHECK_S, the sun's constants with it); else off.
function M.update(inOurLevel)
  if not inOurLevel then
    if aoSaved or shaftsOn or reflect.savedEnv then M.restore() end
    return
  end
  updateReflection()
  local t = os.clock()
  if t - lastCheck < M.CHECK_S then return end
  lastCheck = t
  if mode ~= 'on' then M.restore() return end
  if features.ao then applyAo() else restoreAo() end
  if features.shafts then updateShafts() else stopShafts() end
end

-- Leaving our level (or off): the player's AO back, the shafts off (they stay registered, as the game's effects do).
function M.restore()
  lastCheck = -math.huge
  restoreAo()
  stopShafts()
  M.restoreReflection()
end

function M.state()
  return { mode = mode, ao = aoSaved ~= nil, shafts = shaftsOn, debug = debug, reflection = reflect.savedEnv ~= nil }
end

return M
