-- luajit mod/tests/test_weatherfx.lua
-- beamngEarth/weatherfx.lua: the ground's state (wetting, drying, puddles, snow, melting, ice), the start from the
-- weather a level loads in, the grip (wet / snow / ice over the dry values, the collision types), the lightning, the
-- shader's constants, then the runtime against stubbed ground models and a stubbed weather: grip written and put back,
-- a reload of the game's ground models, the surface pass switched on and off, the keys.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')
local F = require('beamngEarth/weatherfx')
local check, near = T.check, T.near

local function run(s, e, secs, step)
  step = step or 1
  for _ = 1, math.floor(secs / step) do F.stepSurface(s, e, step) end
  return s
end
local function dry() return { wet = 0, puddle = 0, snow = 0, ice = 0 } end

------------------------------------------------------------------------------------------
-- the ground's state

do -- full rain soaks a dry road in WET_IN_S, puddles only later; it dries slowly after
  local s = run(dry(), { rain = 1, tempC = 15 }, F.WET_IN_S / 2)
  near(s.wet, 0.5, 0.02, 'half the soak time: half wet')
  check(s.puddle == 0, 'no puddles on a road not yet soaked')
  run(s, { rain = 1, tempC = 15 }, F.WET_IN_S / 2 + 300)
  check(s.wet == 1 and s.puddle > 0.3 and s.puddle < 0.7, 'soaked; puddles filling: ' .. s.puddle)
  local wet0 = s.wet
  run(s, { rain = 0, tempC = 15 }, 600)
  check(s.wet < wet0 and s.wet > 0.5, 'ten minutes after the rain: still wet ' .. s.wet)
  check(s.puddle <= s.wet, 'never more puddle than water')
  run(s, { rain = 0, tempC = 15 }, 3000)
  check(s.wet == 0 and s.puddle == 0, 'dry again after an hour')
end
do -- light rain wets slower; warm wind dries faster
  local a = run(dry(), { rain = 0.2, tempC = 15 }, 60)
  local b = run(dry(), { rain = 1, tempC = 15 }, 60)
  check(a.wet < b.wet / 3, 'drizzle wets slower than a downpour')
  local c = run({ wet = 1, puddle = 0, snow = 0, ice = 0 }, { tempC = 30, windMs = 10 }, 600)
  local d = run({ wet = 1, puddle = 0, snow = 0, ice = 0 }, { tempC = 5, windMs = 0 }, 600)
  check(c.wet < d.wet, 'warm and windy dries faster')
  check(c.wet == 0, 'a hot windy day dries a road in 10 minutes')
end
do -- snow below freezing settles; above it is slush (wet); melting wets the road
  local s = run(dry(), { snow = 1, tempC = -3 }, F.SNOW_IN_S)
  near(s.snow, 1, 0.01, 'full snow covers the ground in SNOW_IN_S')
  check(s.wet == 0, 'dry cold snow: not wet')
  local w = run(dry(), { snow = 1, tempC = 4 }, 120)
  check(w.snow == 0 and w.wet > 0.5, 'warm snow is slush: wet, nothing settles')
  run(s, { tempC = 6 }, 600)
  check(s.snow < 1 and s.snow > 0 and s.wet > 0.5, 'melting at 6 C: less snow, wet road ' .. s.snow .. ' ' .. s.wet)
  run(s, { tempC = 6, rain = 1 }, 900)
  check(s.snow == 0, 'rain washes it away')
end
do -- a wet road freezes in a frost, thaws above
  local s = run({ wet = 1, puddle = 0.5, snow = 0, ice = 0 }, { tempC = -5 }, F.FREEZE_S)
  check(s.ice > 0.8 and s.wet < 0.1 and s.puddle <= s.wet, 'frozen: ice ' .. s.ice .. ', wet ' .. s.wet)
  run(s, { tempC = 5 }, F.THAW_S)
  check(s.ice == 0 and s.wet > 0.5, 'thawed back to wet')
end
do -- a chosen weather builds up FORCED_SPEED times faster
  local a = run(dry(), { rain = 1, tempC = 15, speed = F.FORCED_SPEED }, F.WET_IN_S / F.FORCED_SPEED + 1)
  near(a.wet, 1, 0.02, 'chosen rain: soaked in WET_IN_S / FORCED_SPEED')
  local b = F.stepSurface(dry(), { rain = 1, tempC = 15 }, -5)
  check(b.wet == 0, 'a negative dt does nothing')
end
do -- the start: what falls now and the snow that lies
  local s = F.seedSurface({ rain = 1, tempC = 12 })
  check(s.wet == 1 and s.puddle > 0.5 and s.snow == 0, 'a level loaded in heavy rain: soaked, puddles')
  s = F.seedSurface({ rain = 0, snow = 0, tempC = -2 }, 0.1)
  check(s.snow == 1 and s.wet == 0, 'snow on the ground (server depth 10 cm): white')
  s = F.seedSurface({ rain = 0, snow = 0, tempC = -2 }, 0.01)
  near(s.snow, 0.2, 1e-9, '1 cm: a fifth')
  s = F.seedSurface({ snow = 0.6, tempC = -4 })
  check(s.snow > 0.5, 'snowing hard and cold, no depth known: mostly white')
  s = F.seedSurface({ rain = 0.5, tempC = -3 })
  check(s.ice > 0 and s.wet > 0, 'freezing rain: some ice')
  s = F.seedSurface({ tempC = 20 })
  check(s.wet == 0 and s.snow == 0 and s.ice == 0, 'a dry day: dry')
end

------------------------------------------------------------------------------------------
-- the grip

local ASPH = { static = 0.98, sliding = 0.70, hydro = 0, stribeck = 4.5 }
do
  local g = F.gripFor('paved', ASPH, dry())
  check(g.static == 0.98 and g.sliding == 0.70 and g.hydro == 0 and g.stribeck == 4.5, 'dry: the dry values')
  g = F.gripFor('paved', ASPH, { wet = 1, puddle = 0, snow = 0, ice = 0 })
  near(g.static, 0.92, 1e-9, 'soaked asphalt: ASPHALT_WET static')
  near(g.sliding, 0.55, 1e-9, 'soaked asphalt: ASPHALT_WET sliding')
  near(g.hydro, 0.0012, 1e-12, 'and its hydrodynamic friction')
  local p = F.gripFor('paved', ASPH, { wet = 1, puddle = 1, snow = 0, ice = 0 })
  check(p.sliding < g.sliding, 'standing water slides more')
  local sn = F.gripFor('paved', ASPH, { wet = 0, puddle = 0, snow = 1, ice = 0 })
  near(sn.static, F.SNOW_PACKED.static, 1e-9, 'snow covered: packed snow')
  local ic = F.gripFor('paved', ASPH, { wet = 0, puddle = 0, snow = 0, ice = 1 })
  check(ic.static < 0.98 and ic.static > F.ICE.static, 'ice: patches only (ICE_MAX), between dry and ice')
  local both = F.gripFor('paved', ASPH, { wet = 1, puddle = 1, snow = 1, ice = 0 })
  near(both.static, F.SNOW_PACKED.static, 1e-9, 'snow over a wet road: the snow counts')
  local mud = F.gripFor('loose', { static = 0.55, sliding = 0.55, hydro = 0.01, stribeck = 6 }, { wet = 0, puddle = 0, snow = 1, ice = 0 })
  check(mud.static == 0.55, 'snow never grips better than the ground under it')
  local x = F.gripFor('other', ASPH, { wet = 1, puddle = 1, snow = 1, ice = 1 })
  check(x.static == 0.98, 'unknown kinds untouched')
  -- monotone in wetness
  local prev = 2
  for i = 0, 10 do
    local v = F.gripFor('loose', { static = 0.7, sliding = 0.73, hydro = 0.0067, stribeck = 5 }, { wet = i / 10, puddle = 0, snow = 0, ice = 0 }).static
    check(v <= prev, 'loose ground: less grip the wetter ' .. i)
    prev = v
  end
  check(F.typeFor('paved', { wet = 1, snow = 0, ice = 0 }) == 'ASPHALT_WET', 'wet paved: ASPHALT_WET (spray)')
  check(F.typeFor('paved', { wet = 0.2, snow = 0, ice = 0 }) == nil, 'damp: its own type')
  check(F.typeFor('paved', { wet = 1, snow = 0.8, ice = 0 }) == 'SNOW', 'snowy: SNOW')
  check(F.typeFor('loose', { wet = 1, snow = 1, ice = 0 }) == nil, 'loose ground keeps its type')
  near(F.gripShare(dry()), 1, 1e-9, 'grip share dry 100 %')
  check(F.gripShare({ wet = 1, puddle = 0, snow = 0, ice = 0 }) < 0.95, 'grip share wet below 95 %')
end

------------------------------------------------------------------------------------------
-- lightning and the shader's constants

do
  check(F.flashAt(-1) == 0 and F.flashAt(5) == 0, 'no flash outside the strike')
  check(F.flashAt(0.03) == 1 and F.flashAt(0.12) < 0.3 and F.flashAt(0.2) > 0.7, 'stroke, gap, restrike')
  local near1, far1 = F.strike(0, 0), F.strike(1, 0.99)
  near(near1.km, F.STRIKE_KM[1], 1e-9, 'u1 0: the nearest strike')
  near(far1.km, F.STRIKE_KM[2], 1e-9, 'u1 1: the farthest')
  check(near1.power > far1.power and near1.volume > far1.volume and near1.delay < far1.delay, 'far strikes: dimmer, softer, later')
  near(far1.delay, F.STRIKE_KM[2] * 1000 / 343, 1e-6, 'thunder at the speed of sound')
  check(near1.sound == 1 and far1.sound == #F.THUNDER, 'one of the thunder sounds')
  local c = F.shaderConsts({ wet = 0.5, puddle = 0.2, snow = 0.1, ice = 0 }, { 1, 2, 3 }, 3610, 0.7, 0.4, false, true)
  check(c['$bneWx0'] == '0.5 0.2 0.1 10', 'wet, puddle, snow, time mod 1 h: ' .. c['$bneWx0'])
  check(c['$bneWx1'] == '1 2 3 0.7', 'camera and rain: ' .. c['$bneWx1'])
  check(c['$bneWx2']:match('^0.4 ') and c['$bneWx2']:match(' 1$'), 'flash first, debug last: ' .. c['$bneWx2'])
  local tn = F.shaderConsts({ wet = 1, puddle = 1, snow = 1, ice = 0 }, { 0, 0, 0 }, 0, 1, 0, true, false)
  check(tn['$bneWx0']:match('^0.25 0 0 '), 'in a tunnel: a little wet, no puddles, no snow: ' .. tn['$bneWx0'])
  check(tn['$bneWx1']:match(' 0$'), 'no rain rings in a tunnel')
  check(c['$bneWx4'] == string.format('%.6g %.6g 0 0', F.LOOK.snowAlbedo, F.LOOK.snowLight),
    'snow lit by the light buffer: ' .. c['$bneWx4'])
  -- a shower's last drops dry the road (the wet crept back after dryNow, the builder's test 2026-10-30)
  local d = run({ wet = 0.5, puddle = 0, snow = 0, ice = 0 }, { rain = F.WET_MIN * 0.9, tempC = 15 }, 60)
  check(d.wet < 0.5, 'a trace of rain dries: ' .. d.wet)
  local r = run({ wet = 0.5, puddle = 0, snow = 0, ice = 0 }, { rain = F.WET_MIN * 2, tempC = 15 }, 60)
  check(r.wet > 0.5, 'a light rain still wets: ' .. r.wet)
end

------------------------------------------------------------------------------------------
-- runtime: stubbed ground models, weather, scene

local written = {}
be = { setGroundModel = function(_, name, gm) written[#written + 1] = { name = name, static = gm.staticFrictionCoefficient, ctype = gm.collisiontype } end }
local function gm(static, sliding)
  return { staticFrictionCoefficient = static, slidingFrictionCoefficient = sliding, hydrodynamicFriction = 0,
    stribeckVelocity = 4.5, collisiontype = 1, skidMarks = true }
end
local asphalt, grass, metal = gm(0.98, 0.7), gm(0.61, 0.65), gm(0.5, 0.5)
core_environment = { groundModels = {
  ASPHALT = { cdata = asphalt, isAlias = false, parent = 'none' },
  CONCRETE = { cdata = asphalt, isAlias = true, parent = 'ASPHALT' },
  GRASS = { cdata = grass, isAlias = false, parent = 'none' },
  METAL = { cdata = metal, isAlias = false, parent = 'none' },
} }
package.loaded['particles'] = { getMaterialsParticlesTable = function() return {} end,
  getOrAddMaterialIDByName = function(_, name) return ({ ASPHALT_WET = 7, SNOW = 9 })[name] end }
local W = { mode = 'real', rainNow = 0, snowNow = 0, temp = 10, forcedName = nil, storm = false, rainT = 0, snowT = 0 }
function W.getMode() return W.mode end
function W.airTempC() return W.temp end
function W.current() return { wind = { ms = 0 } } end
function W.forced() return W.forcedName end
function W.force(name) W.forcedName = name; return name end
function W.cycleNext(name, dir) return ({ [''] = 'clear', clear = 'cloudy' })[name or ''] end
function W.stormNow() return W.storm end
function W.targetPrecip() return W.rainT, W.snowT end
function W.snowDepthM() return nil end
beamngEarth_weather = W
local pfx = nil
local consts = {}
local scene = {}
scenetree = { findObject = function(n) return scene[n] end }
createObject = function(class)
  local o = S.newObject(class)
  function o:registerObject(n) self.name = n; scene[n] = self end
  function o:enable() self.on = true end
  function o:disable() self.on = false end
  function o:setShaderConst(k, v) consts[k] = v end
  return o
end
core_camera = { getPositionXYZ = function() return 10, 20, 1600 end }
local toasts = {}
ui_message = function(t) toasts[#toasts + 1] = t end

do -- dry: nothing written, no pass
  F.onLevel()
  F.update(0.5, true)
  F.update(0.5, true)
  check(#written == 0 and scene[F.PFX] == nil, 'dry weather: no grip writes, no pass')
end
do -- rain: wet, the grip written for asphalt (+ its alias) and grass, not metal; the pass on with its constants
  W.rainNow, W.rainT = 1, 1
  for _ = 1, 400 do F.update(0.5, true) end  -- (the level started dry: the rain soaks the road in WET_IN_S)
  local names = {}
  for _, w in ipairs(written) do names[w.name] = w end
  check(names.ASPHALT and names.CONCRETE and names.GRASS and not names.METAL, 'paved (and its alias) and loose written, metal not')
  check(asphalt.staticFrictionCoefficient < 0.98 and asphalt.collisiontype == 7 and asphalt.skidMarks == false,
    'wet asphalt: less grip, ASPHALT_WET type, no skid marks')
  check(grass.collisiontype == 1, 'grass keeps its type')
  pfx = scene[F.PFX]
  check(pfx and pfx.on, 'the surface pass is on')
  check(consts['$bneWx1'] == '10 20 1600 1', 'camera and rain in the constants: ' .. tostring(consts['$bneWx1']))
  check(F.statusLine():find('wet', 1, true) ~= nil, 'status: ' .. F.statusLine())
end
do -- the game reloads its ground models (a new cdata with the dry values): written again
  local fresh = gm(0.98, 0.7)
  core_environment.groundModels.ASPHALT.cdata = fresh
  core_environment.groundModels.CONCRETE.cdata = fresh
  for _ = 1, 6 do F.update(0.5, true) end
  check(fresh.staticFrictionCoefficient < 0.98, 'after a ground model reload the wet grip is back on')
  asphalt = fresh
end
do -- grip off: the dry values back; on again: wet
  F.setGrip(false)
  F.update(0.5, true)
  check(asphalt.staticFrictionCoefficient == 0.98 and asphalt.collisiontype == 1 and asphalt.skidMarks == true, 'grip off: dry values back')
  F.setGrip(true)
  for _ = 1, 4 do F.update(0.5, true) end
  check(asphalt.staticFrictionCoefficient < 0.98, 'grip on again: wet')
end
do -- manual weather / leaving the level: everything back, the pass off
  W.mode = 'manual'
  F.update(0.5, true)
  check(asphalt.staticFrictionCoefficient == 0.98 and not pfx.on, 'manual weather: dry grip, pass off')
  W.mode = 'real'
  for _ = 1, 4 do F.update(0.5, true) end
  check(asphalt.staticFrictionCoefficient < 0.98 and pfx.on, 'real again: the wet road is still wet')
  F.update(0.5, false)
  check(asphalt.staticFrictionCoefficient == 0.98 and not pfx.on, 'out of our level: back')
end
do -- dryNow, setSurface, the debug view
  F.update(0.5, true)
  F.dryNow()
  W.rainNow = 0
  for _ = 1, 4 do F.update(0.5, true) end
  check(F.surface().wet == 0 and asphalt.staticFrictionCoefficient == 0.98, 'dry now: dry, dry grip')
  check(not pfx.on, 'and the pass off again')
  F.setSurface({ snow = 1 })
  for _ = 1, 4 do F.update(0.5, true) end
  check(F.surface().snow == 1 and asphalt.collisiontype == 9, 'a fixed snowy ground: SNOW type')
  F.setDebug(true)
  F.update(0.5, true)
  check(consts['$bneWx2']:match(' 1$'), 'debug view in the constants')
  F.setDebug(false)
  F.setSurface(nil)
end
do -- dryNow while the rain fades out: held dry until it stopped; steady or new rain wets again
  F.setSurface(nil)
  W.rainNow, W.rainT = 0.6, 0
  F.dryNow()
  for i = 1, 180 do                  -- (90 s: the rain fades as weather.lua's lerp does)
    W.rainNow = 0.6 * 0.97 ^ i
    F.update(0.5, true)
  end
  check(F.surface().wet == 0, 'held dry while the rain faded: ' .. F.surface().wet)
  check(F.status().dryHold == false, 'and let go once it stopped')
  W.rainNow, W.rainT = 0.5, 0.5
  F.dryNow()
  for _ = 1, 60 do F.update(0.5, true) end
  check(F.surface().wet > 0, 'steady rain: wet again after dryNow: ' .. F.surface().wet)
  F.dryNow()
  W.rainNow, W.rainT = 0.5, 0.2
  F.update(0.5, true)
  W.rainNow, W.rainT = 0.3, 1      -- (a new rain chosen while the old one fades)
  for _ = 1, 60 do F.update(0.5, true) end
  check(F.surface().wet > 0, 'a new rain building up: wet again: ' .. F.surface().wet)
  -- 9 then W while a forced rain still falls: the weather chosen just after holds the road dry again
  W.rainNow, W.rainT = 1, 1
  F.dryNow()
  for _ = 1, 20 do F.update(0.5, true) end          -- (10 s: the forced rain goes on, the hold lets go)
  local wetBefore = F.surface().wet
  F.dryNow()
  F.real()
  W.rainT = 0
  for i = 1, 180 do W.rainNow = 0.97 ^ i; F.update(0.5, true) end
  check(wetBefore > 0 and F.surface().wet == 0, 'dry key then real weather mid-rain: held dry while it fades: '
    .. F.surface().wet)
  W.rainNow, W.rainT = 0, 0
  F.dryNow()
  F.update(0.5, true)
end
do -- setSurface(nil): the weather's own state back, not the fixed one
  F.update(0.5, true)
  local before = F.surface()
  F.setSurface({ snow = 1, wet = 0.4 })
  F.update(0.5, true)
  check(F.surface().snow == 1, 'fixed: snow')
  F.setSurface(nil)
  F.update(0.5, true)
  check(F.surface().snow == before.snow and F.surface().wet <= before.wet + 1e-9, 'setSurface(nil): back as before ('
    .. F.surface().snow .. ')')
end
do -- the snow's albedo: eased toward the night's under the street lights
  package.loaded['beamngEarth/nightlights'] = { night = true }
  F.setSurface({ snow = 0.5 })
  for _ = 1, 80 do F.update(0.25, true) end
  local a = tonumber(consts['$bneWx4']:match('^(%S+)'))
  near(a, F.LOOK.snowAlbedoNight, 0.01, 'night: the night albedo')
  package.loaded['beamngEarth/nightlights'].night = false
  F.update(0.25, true)
  local b = tonumber(consts['$bneWx4']:match('^(%S+)'))
  check(b > a and b < F.LOOK.snowAlbedo, 'day again: eased, not jumped (' .. b .. ')')
  package.loaded['beamngEarth/nightlights'] = nil
  F.setSurface(nil)
end
do -- deep snow buries the 3D grass; it comes back once the snow thinned
  local calls = {}
  package.loaded['beamngEarth/groundcover'] = { setBuried = function(on) calls[#calls + 1] = on; return on end }
  F.setSurface({ snow = 0.5 })
  F.update(0.5, true)
  check(#calls == 0, 'thin snow: the grass stays')
  F.setSurface({ snow = F.BURY_AT })
  F.update(0.5, true)
  F.update(0.5, true)
  check(#calls == 1 and calls[1] == true and F.status().grassBuried, 'deep snow: the grass goes under, once')
  F.setSurface({ snow = 0.5 })
  F.update(0.5, true)
  check(#calls == 1, 'between the two: no change (hysteresis)')
  F.setSurface({ snow = 0.1 })
  F.update(0.5, true)
  check(#calls == 2 and calls[2] == false, 'little snow left: the grass back')
  F.setSurface({ snow = 1 })
  F.update(0.5, true)
  F.update(0.5, false)
  check(calls[#calls] == false and not F.status().grassBuried, 'out of our level: the grass back')
  F.setSurface(nil)
  package.loaded['beamngEarth/groundcover'] = nil
end
do -- keys: the round, a weather directly, real weather; toasts say which
  local n = #toasts
  check(F.cycle(1) == 'clear' and W.forcedName == 'clear', 'next: clear')
  check(F.choose('storm') == 'storm', 'storm directly')
  check(toasts[#toasts]:find('Thunderstorm', 1, true), 'toast names it: ' .. toasts[#toasts])
  check(F.real() == nil and toasts[#toasts]:find('real weather', 1, true), 'real again')
  check(#toasts == n + 3, 'one toast a key')
end
do -- a thunderstorm: strikes flash, thunder plays later
  local played = {}
  Engine = { Audio = { playOnce = function(_, path, o) played[#played + 1] = { path = path, v = o.volume } end } }
  FS = { fileExists = function() return true end }
  W.storm, W.rainNow, W.rainT = true, 1, 1
  F.setSurface({ wet = 1 })
  local flashes = 0
  for _ = 1, 240 * 4 do
    F.update(0.25, true)
    local f = tonumber(consts['$bneWx2']:match('^(%S+)'))
    if f and f > 0 then flashes = flashes + 1 end
  end
  check(flashes > 0, 'lightning flashed (' .. flashes .. ' frames)')
  check(#played >= 3, 'thunder after the strikes (' .. #played .. ')')
  check(played[1].path:match('thunder_%d%.ogg$'), 'a thunder sound: ' .. tostring(played[1] and played[1].path))
  W.storm = false
  F.setSurface(nil)
end

T.done('test_weatherfx')
