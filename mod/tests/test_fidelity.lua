-- luajit mod/tests/test_fidelity.lua  (render fidelity, fidelity.lua: city-scale AO, sun shafts)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local F = require('beamngEarth/fidelity')

-- the sun's vector: core_celestial's azimuth (from north, clockwise) and elevation, north +Y, east +X
local function vec(az, el) return F.sunVector(az, el) end
local v = vec(0, 0)
T.near(v[1], 0, 1e-12, 'north on the horizon: x 0'); T.near(v[2], 1, 1e-12, 'north: +Y'); T.near(v[3], 0, 1e-12, 'z 0')
v = vec(90, 0)
T.near(v[1], 1, 1e-12, 'east: +X'); T.near(v[2], 0, 1e-12, 'east: y 0')
v = vec(270, 0)
T.near(v[1], -1, 1e-12, 'west: -X')
v = vec(123, 90)
T.near(v[3], 1, 1e-12, 'the zenith whatever the azimuth'); T.near(v[1], 0, 1e-12, 'zenith x'); T.near(v[2], 0, 1e-12, 'zenith y')
v = vec(200, 25)
T.near(v[1] * v[1] + v[2] * v[2] + v[3] * v[3], 1, 1e-12, 'unit length')
T.near(F.sunVector(90, 0, -1)[1], -1, 1e-12, 'the azimuth sign flips east and west')

-- the shafts' strength from the sun's height
T.check(F.sunStrength(-5) == 0 and F.sunStrength(-1) == 0, 'below the horizon: off')
T.near(F.sunStrength(0), 0.5, 1e-12, 'on the horizon: fading in')
T.check(F.sunStrength(10) == 1 and F.sunStrength(30) == 1, 'a low sun: full')
T.near(F.sunStrength(45), 1 + (F.SUN_FADE.fullHigh - 1) * 0.5, 1e-12, 'halfway down to the high sun\'s share')
T.check(F.sunStrength(80) == F.SUN_FADE.fullHigh, 'a high sun: its share')
T.check(F.sunStrength(nil) == 0 and F.sunStrength('x') == 0, 'no sun: off')

-- the constants: four numbers each; the fill run without decay and with its own length and taps
local function nums(s) local t = {} for x in s:gmatch('%S+') do t[#t + 1] = tonumber(x) end return t end
local c = F.shaftConsts(250, 8)
for k, s in pairs(c) do T.check(#nums(s) == 4, k .. ': four numbers') end
T.near(nums(c['$bneSunDir'])[4], 1, 1e-6, 'low sun: strength 1')
T.near(nums(c['$bneShaft'])[2], F.SHAFTS.decay, 1e-6, 'main run decays')
local cf = F.shaftConsts(250, 8, F.SHAFTS, true)
T.check(nums(cf['$bneShaft'])[2] == 1 and math.abs(nums(cf['$bneShaft'])[3] - F.SHAFTS.fillLength) < 1e-6
  and nums(cf['$bneShaft2'])[2] == F.SHAFTS.fillTaps, 'fill run: no decay, its length and taps')
T.check(nums(F.shaftConsts(250, 8, nil, false, true)['$bneShaft2'])[3] == 1, 'debug flag')

-- against stubs: AO set in our level and put back after, the shafts created once and enabled with a sun up
local objects, consts = {}, {}
local function obj(class)
  local o = { class = class, fields = {}, children = {}, enabled = false }
  function o:setField(k, i, val) self.fields[k .. (i ~= 0 and i or '')] = val end
  function o:registerObject(n) objects[n] = self end
  function o:setShaderConst(k, val) consts[k] = val end
  function o:isEnabled() return self.enabled end
  function o:enable() self.enabled = true end
  function o:disable() self.enabled = false end
  function o:add(child) self.children[#self.children + 1] = child end
  return o
end
local ssao = obj('PostEffectSSAO')
ssao.enabled = true
function ssao:setRadius(r) self.radius = r end
function ssao:setContrast(x) self.contrast = x end
function ssao:setSamples(n) self.samples = n end
objects.SSAOPostFx = ssao
local sky = { azimuth = '250', elevation = '8' }
createObject = obj
scenetree = { findObject = function(n) return n == 'sunsky' and sky or objects[n] end,
  findClassObjects = function(cls) return cls == 'ScatterSky' and { 'sunsky' } or {} end }
local stockR, stockC = 1.5, 2
package.loaded['client/postFx/ssao'] = { getRadius = function() return stockR end, getContrast = function() return stockC end,
  setRadius = function(r) ssao:setRadius(r) end, setContrast = function(x) ssao:setContrast(x) end }
local prefs = { PostFXSSAOGeneralQuality = 'Normal', PostFXSpecularOcclusionEnabled = false }
settings = { getValue = function(k) return prefs[k] end }
log = function() end
local clock = 0
os.clock = function() return clock end

F.setMode('on')
F.update(false)
T.check(objects[F.PFX] == nil and ssao.radius == nil, 'other levels: nothing touched')
clock = 10; F.update(true)
T.check(ssao.radius == F.AO.radius and ssao.contrast == F.AO.contrast and ssao.samples == 64 and ssao.bentNormals == true,
  'our level: city AO, 64 samples, specular occlusion')
local pfx = objects[F.PFX]
T.check(pfx and pfx.fields.renderBin == 'UpscaleDispatch' and pfx.fields.targetScale == '0.25 0.25', 'mask: quarter res, HDR')
T.check(#pfx.children == 3 and objects.BeamNGEarthShaftsApplyPfx.fields.target == '$backBuffer', 'blur, fill, apply')
T.check(objects.BeamNGEarthShaftsMaskShader.DXComputShaderName == F.SHADERS.mask, 'compute shader')
T.check(pfx.enabled and F.state().shafts, 'a low sun: shafts on')
T.check(consts['$bneSunDir'] ~= nil, 'the sun\'s constants set')
sky.elevation = '-10'
clock = 11; F.update(true)
T.check(pfx.enabled, 'not again before CHECK_S')
clock = 20; F.update(true)
T.check(not pfx.enabled and not F.state().shafts, 'night: shafts off')
F.setFeature('ao', false)
clock = 30; F.update(true)
T.check(ssao.radius == stockR and ssao.contrast == stockC and ssao.samples == 16 and ssao.bentNormals == false,
  'ao off: the player\'s values back')
F.setFeature('ao', true)
T.check(not F.setFeature('bloom', true), 'unknown feature refused')
clock = 40; F.update(true)
T.check(ssao.radius == F.AO.radius, 'ao on again')
F.update(false)
T.check(ssao.radius == stockR and ssao.samples == 16 and not pfx.enabled, 'leaving the level: all back')
local created = 0
for _ in pairs(objects) do created = created + 1 end
clock = 50; F.update(true)
local after = 0
for _ in pairs(objects) do after = after + 1 end
T.check(after == created, 'created once')
ssao.enabled = false
F.restore()
clock = 60; F.update(true)
T.check(ssao.radius == stockR, 'the player\'s AO off: left alone')
T.check(not F.setMode('max') and F.setMode('off') and F.getMode() == 'off', 'modes')
ssao.enabled = true
sky.elevation = '8'
clock = 70; F.update(true)
T.check(ssao.radius == stockR and not pfx.enabled, 'off: nothing applied')

-- live reflection: captured, assigned when the faces are on disk, the level's map back on restore
F.setMode('on')
local files = {}
FS = { fileExists = function(_, p) return files[p] == true end, directoryCreate = function() return true end }
local captured = nil
captureCameraCubemap = function(prefix, res) captured = { prefix = prefix, res = res } end
local li = obj('LevelInfo')
li.fields.globalEnviromentMap = 'BNG_Sky_01_cubemap'
function li:getField(k) return self.fields[k] end
objects.theLevelInfo = li
scenetree.findClassObjects = function(cls)
  if cls == 'ScatterSky' then return { 'sunsky' } elseif cls == 'LevelInfo' then return { 'theLevelInfo' } end
  return {}
end
clock = 79.5
local ok, prefix = F.captureReflection()
T.check(ok and captured and captured.res == F.REFLECT.res and prefix:find(F.REFLECT.dir, 1, true) == 1, 'capture asked')
clock = 80; F.update(true)
T.check(li.fields.globalEnviromentMap == 'BNG_Sky_01_cubemap', 'not assigned before the faces exist')
for i = 0, 5 do files[prefix .. i .. '.hdr.dds'] = true end
clock = 80.1; F.update(true)
local cm = objects[F.REFLECT.name]
T.check(cm and cm.fields.cubeFace5 == prefix .. '5.hdr.dds' and li.fields.globalEnviromentMap == F.REFLECT.name,
  'assigned: the level\'s environment map')
clock = 80.5
local _, prefix2 = F.captureReflection()
T.check(prefix2 ~= prefix, 'the next capture uses the other file set')
for i = 0, 5 do files[prefix2 .. i .. '.hdr.dds'] = true end
clock = 81; F.update(true)
T.check(cm.fields.cubeFace == prefix2 .. '0.hdr.dds', 'faces swapped on the same cubemap')
T.check(F.state().reflection, 'state: reflection on')
F.update(false)
T.check(li.fields.globalEnviromentMap == 'BNG_Sky_01_cubemap' and not F.state().reflection, 'leaving: the level\'s map back')
clock = 190
files = {}
F.captureReflection()
clock = 200; F.update(true)
T.check(li.fields.globalEnviromentMap == 'BNG_Sky_01_cubemap', 'a capture that wrote nothing: given up, nothing assigned')
captureCameraCubemap = nil
T.check(not F.captureReflection(), 'no capture function: refused')

-- the automatic capture's policy
local W = function(s) return F.wantCapture(s) end
T.check(W({ speed = 0, inside = false }), 'first capture: outside, stopped')
T.check(not W({ speed = 20, inside = false }), 'not while driving')
T.check(not W({ speed = 0, inside = true }), 'inside the car, sync unknown: no')
T.check(W({ speed = 0, inside = true, sync = true }), 'inside, sync: the car hidden for the call')
T.check(not W({ speed = 0, dist = 100, sunDelta = 1, since = 60 }), 'close by, same sun: no')
T.check(W({ speed = 0, dist = 700, sunDelta = 1, since = 60 }), 'moved far enough')
T.check(W({ speed = 0, dist = 10, sunDelta = 9, since = 60 }), 'the sun moved')
T.check(not W({ speed = 0, dist = 700, since = 5 }), 'too soon after the last')
T.check(F.insideCam('driver') and F.insideCam('onboard.hood') and F.insideCam(nil) and not F.insideCam('orbit')
  and not F.insideCam('chase') and not F.insideCam('free'), 'inside cameras')

-- wet roads: the values, then against stub materials (written in steps, put back dry)
local r, col = F.wetValues('1.5', '1 0.9 0.8 1', 1)
T.near(tonumber(r), 1.5 * F.WET.rough, 1e-3, 'wet: rougher map scaled down')
T.near(nums(col)[1], F.WET.dark, 1e-3, 'wet: darker'); T.near(nums(col)[4], 1, 1e-9, 'alpha kept')
r, col = F.wetValues(nil, nil, 0)
T.check(tonumber(r) == 1 and col == '1 1 1 1', 'dry, no own values: defaults')
local reloads = 0
local function mat(rough, color)
  local m = { f = { roughnessFactor = rough, baseColorFactor = color } }
  function m:getField(k) return self.f[k] end
  function m:setField(k, _, v) self.f[k] = v end
  function m:flush() end
  function m:reload() reloads = reloads + 1 end
  return m
end
local asphalt = mat('', '0.9 0.9 0.9 1')
objects.road_asphalt_2lane = asphalt
local weather = { rainNow = 0 }
beamngEarth_weather = weather
F.setMode('on')
clock = 300; F.update(true)
T.check(asphalt.f.baseColorFactor == '0.9 0.9 0.9 1' and reloads == 0, 'dry weather: untouched')
weather.rainNow = 0.4
for i = 1, 60 do clock = 300 + i * 2.5; F.update(true) end
T.check(F.state().wet == 1 and tonumber(nums(asphalt.f.baseColorFactor)[1]) < 0.9 * F.WET.dark + 1e-3, 'raining: wet after inS')
local n = reloads
T.check(n >= 4 and n <= 12, 'written in steps, not every check (' .. n .. ')')
F.update(false)
T.check(asphalt.f.baseColorFactor == '0.9 0.9 0.9 1' and asphalt.f.roughnessFactor == '', 'leaving: own values back')
weather.rainNow = 0
F.setWet(1)
clock = 600; F.update(true)
T.check(F.state().wet == 1, 'forced wet')
F.setWet(nil)
clock = 610; F.update(true)
T.check(F.state().wet == 0 and asphalt.f.baseColorFactor == '0.9 0.9 0.9 1', 'back to the weather: dry at once')

T.done('test_fidelity')
