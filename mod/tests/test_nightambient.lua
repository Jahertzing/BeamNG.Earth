-- luajit mod/tests/test_nightambient.lua  (the night's ambient, nightambient.lua; the lamps' fill, nightlights.lua): a
-- city's sky light near the big places, a starlit one away from them, written over the real sky's black; the street
-- lamps' fill lights under the nearest lamps.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')
local clock = 0
os.clockhp = function() return clock end
quatFromDir = function() return quat(0, 0, 0, 1) end
MatrixF = function() return { setFromEuler = function(self, e) self.e = e end,
  setPosition = function(self, p) self.p = p end } end

local na = require('beamngEarth/nightambient')
-- the share: full within a place's radius, gone GONE_R radii out, by its weight
local pts = { { 0, 0, 10000, 1 }, { 50000, 0, 3000, 0.5 } }
T.check(na.urbanShare(pts, 5000, 0) == 1, 'within the city: full')
local mid = na.urbanShare(pts, 20000, 0)
T.check(mid > 0.3 and mid < 0.7, 'two radii out: about half: ' .. mid)
T.check(na.urbanShare(pts, 30000, 0) == 0 and na.urbanShare(pts, 40000, 0) == 0, 'three radii out: none')
T.check(na.urbanShare(pts, 50000, 0) == 0.5, 'a small town: its weight')
T.check(na.urbanShare({}, 0, 0) == 0, 'no places: the country')
T.check(na.colorFor({ 0, 0, 0 }, { 0.1, 0.2, 0.3 }, 0.5, 2) == '0.1000 0.2000 0.3000 1', 'colorFor: lerp, times k')
T.check(na.same('0.1 0.2 0.3 1', '0.1000 0.2000 0.3000 1') and not na.same('0 0 0 1', '0.1000 0.2000 0.3000 1')
  and not na.same(nil, '0 0 0 1'), 'same: the numbers, within 0.002')

-- written over the real sky's black, again when the sky is blacked out again, by the setting
local sky = S.newObject('ScatterSky'); sky:registerObject('sunsky')
sky:setField('nightColor', 0, '0 0 0 1')
readFile = function() return 'export default {};' end
jsonDecode = function() return { places = { { 'Denver', -104.99, 39.74, 1, 1 } } } end
local camAt = vec3(0, 0, 1600)
core_camera = { getPosition = function() return camAt end, getForward = function() return vec3(0, 1, 0) end }
local anchor = { geodeticToEnu = function(_, lat, lon, h) return (lon + 104.99) * 85000, (lat - 39.74) * 111000, h end }
na.reset()
na.update(anchor, 1)
local urban = sky:getField('nightColor')
T.check(urban == na.colorFor(na.RURAL, na.URBAN, 1, 1), 'downtown: the city light: ' .. tostring(urban))
T.check(sky:getField('nightFogColor') ~= nil, '... and its fog')
sky:setField('nightColor', 0, '0 0 0 1')          -- (core_celestial's enableChrome on a new sky)
clock = 3
na.update(anchor, 1)
T.check(sky:getField('nightColor') == urban, 'blacked out again: written again')
camAt = vec3(200000, 0, 1600)
clock = 6
na.update(anchor, 1)
T.check(sky:getField('nightColor') == na.colorFor(na.RURAL, na.URBAN, 0, 1), 'far out in the country: starlit')
na.setMode('off')
T.check(sky:getField('nightColor') == '0 0 0 1', 'off: the real sky\'s black')
clock = 9
na.update(anchor, 1)
T.check(sky:getField('nightColor') == '0 0 0 1', '... and left so')
na.setMode('bright')
na.update(anchor, 1)
T.check(sky:getField('nightColor') == na.colorFor(na.RURAL, na.URBAN, 0, 2), 'bright: twice')
na.setMode('nonsense')
T.check(na.mode == 'normal', 'an unknown mode: normal')

-- the sky's light over the camera and the night exposure: on at night, off by day, the stock values back
local b0 = na.skyFillFor(0, 1)
local b1, c1 = na.skyFillFor(1, 1)
T.check(b1 > b0 and c1:match('^1%.000 ') ~= nil, 'skyFillFor: brighter and warm in the city')
local le = S.newObject('PostEffectLocalExposure'); le:registerObject('PostEffectLocalExposureObject')
le:setField('highlightProtectStrength', 0, '0.75'); le:setField('darkPixelMaxLogLuminance', 0, '-4')
le:setField('darkPixelMinLogLuminance', 0, '-14'); le.exposureBiasEV = 0.5
settings = { getValue = function(k, d) if k == 'GraphicEVCompensation' then return 0.5 end return d end }
na.setMode('normal')
local function fills() local n = 0 for _, o in ipairs(S.liveOf('PointLight')) do
  if o.fields.isEnabled == 'true' then n = n + 1 end end return n end
clock = clock + 3
na.update(anchor, 1, true)
T.check(fills() == 1, 'night: the sky light on: ' .. fills())
local sl = S.liveOf('PointLight')[1]
T.check(sl.pos.z == camAt.z + na.SKYFILL.height, '... over the camera')
T.check(le:getField('highlightProtectStrength') == tostring(na.NIGHT_EXPOSURE.highlightProtectStrength)
  and math.abs(le.exposureBiasEV - (0.5 + na.NIGHT_BIAS_EV)) < 1e-9, 'night exposure on, over the player\'s bias')
camAt = vec3(10, 0, 1700)
na.update(anchor, 1, true)
T.check(sl.pos.z == 1700 + na.SKYFILL.height and sl.pos.x == 10, 'every frame: it follows the camera')
clock = clock + 3
na.update(anchor, 1, false)
T.check(fills() == 0, 'day: the sky light off')
T.check(le:getField('highlightProtectStrength') == '0.75' and le:getField('darkPixelMaxLogLuminance') == '-4'
  and le.exposureBiasEV == 0.5, 'day: the stock exposure back')
clock = clock + 3
na.update(anchor, 1, true)
na.reset()
T.check(fills() == 0 and #S.liveOf('PointLight') == 0, 'reset: the sky light gone')
T.check(le:getField('highlightProtectStrength') == '0.75' and le.exposureBiasEV == 0.5, 'reset: the stock exposure back')
camAt = vec3(0, 0, 1600)

-- the lamps' fill: under the nearest street lamps (with an arm), toward the road
local nl = require('beamngEarth/nightlights')
nl.FILL.max = 2
local x, y, z = nl.fillAt({ 10, 20, 9, 90 })
T.check(math.abs(x - 10) < 1e-9 and math.abs(y - (20 + nl.FILL.out)) < 1e-9 and z == 9 - nl.FILL.down,
  'fillAt: out along the arm, under the head')
x, y = nl.fillAt({ 10, 20, 9 })
T.check(x == 10 and y == 20, '... straight under without an arm')
nl.onRows('1:0_0', { { 0, 10, 0, 9, 90 }, { 0, 20, 0, 9, 90 }, { 0, 30, 0, 9, 90 } }, { 'lamplens' })
nl.providers.canopy = function() return { { 5, 0, 5 } } end      -- (a canopy's light: no arm)
camAt = vec3(0, 0, 1600)
core_environment = { getLightState = function() return { isNight = true } end }
na.setMode('normal')
clock = 20
local function pointLights()
  local n = 0
  for _, o in ipairs(S.liveOf('PointLight')) do      -- (the lamps' and shops': not the sky light)
    if o.fields.isEnabled == 'true' and o.fields.radius ~= tostring(na.SKYFILL.radius) then n = n + 1 end
  end
  return n
end
for _ = 1, 3 do clock = clock + 2; nl.update(anchor, 1) end
T.check(#S.liveOf('SpotLight') == 4, 'a spot light at each lamp and the canopy: ' .. #S.liveOf('SpotLight'))
T.check(pointLights() == 2, 'fills under the two nearest street lamps, none under the canopy: ' .. pointLights())
T.check(nl.status().fills == 2, 'status: the fills')
-- the shop windows' spill: a light at the nearest shopfront points (the tiles' shopspill rows)
nl.onRows('1:0_0', { { 0, 10, 0, 9, 90 }, { 0, 20, 0, 9, 90 }, { 0, 30, 0, 9, 90 }, { 1, 15, 8, 2.4, 90 },
  { 1, 900, 0, 2.4, 0 } }, { 'lamplens', 'shopspill' })
for _ = 1, 3 do clock = clock + 2; nl.update(anchor, 1) end
T.check(nl.status().spills == 1 and nl.status().shopfronts == 2, 'a spill light at the near shopfront only: '
  .. tostring(nl.status().spills))
T.check(pointLights() == 3, 'two fills and the spill: ' .. pointLights())
local sp
for _, o in ipairs(S.liveOf('PointLight')) do if o.pos.x == 15 and o.pos.y == 8 then sp = o end end
T.check(sp and sp.pos.z == 2.4 and sp.fields.radius == tostring(nl.SPILL.radius), '... at its point, a spill\'s look')
nl.dropKey('1:0_0')
T.check(nl.status().shopfronts == 0, 'its tile goes: its shopfronts')
nl.onRows('1:0_0', { { 0, 10, 0, 9, 90 }, { 0, 20, 0, 9, 90 }, { 0, 30, 0, 9, 90 } }, { 'lamplens' })
na.setMode('off')
clock = clock + 2; nl.update(anchor, 1)
T.check(pointLights() == 0, 'ambient off: no fills, no spills')
nl.LIGHT.range = 50
nl.applyLook()
local spot = S.liveOf('SpotLight')[1]
T.check(spot.fields.range == '50', 'applyLook: the lights take the new look')
nl.reset()
T.check(#S.liveOf('SpotLight') == 0 and #S.liveOf('PointLight') == 0, 'reset: all gone')
-- the road lines' night glow: a stock material, its own values back by day and when the level ends
local lw = S.newObject('Material'); lw:registerObject('line_white')
lw:setField('emissiveFactor', 0, '0.1 0.1 0.1'); lw:setField('emissiveIntensityNits', 0, '7')
core_environment = { getLightState = function() return { isNight = true } end }
clock = clock + 5; nl.update(anchor, 1)
T.check(lw:getField('emissiveFactor') == nl.MATERIALS.line_white[1], 'night: the lines glow')
nl.reset()
T.check(lw:getField('emissiveFactor') == '0.1 0.1 0.1' and lw:getField('emissiveIntensityNits') == '7',
  'the level ends: the stock line material as it was')
T.done('test_nightambient')
