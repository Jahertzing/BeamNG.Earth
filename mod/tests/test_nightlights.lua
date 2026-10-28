-- luajit mod/tests/test_nightlights.lua  (night lighting, nightlights.lua): the road tiles' lamp lenses kept per tile,
-- the nearest within reach, moved by a rebase, gone with their tile; day / night from the stock environment.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
dofile(dir .. '/stubs.lua')
os.clockhp = function() return 0 end

local nl = require('beamngEarth/nightlights')
local groups = { 'streetlight', 'lamplens', 'pole' }
nl.onRows('1:0_0', { { 0, 0, 0, 0 }, { 1, 10, 0, 8 }, { 1, 50, 0, 8 }, { 2, 5, 5, 0 }, { 1, 400, 0, 8 } }, groups)
nl.onRows('1:1_0', { { 1, 20, 3, 8 } }, groups)
T.check(nl.status().lenses == 4, 'the lens rows only: ' .. nl.status().lenses)
local near = nl.nearest(0, 0, 150, 2)
T.check(#near == 2 and near[1][1] == 10 and near[2][1] == 20, 'the two nearest within reach, nearest first')
T.check(#nl.nearest(0, 0, 150, 10) == 3, 'the one 400 m away left out')
local ahead = nl.nearest(0, 0, 500, 10, -1, 0, 30)
T.check(#ahead == 2 and ahead[1][1] == 10, 'looking away: only those within reach all round: ' .. #ahead)
T.check(#nl.nearest(0, 0, 500, 10, 1, 0, 30) == 4, 'looking at them: out to the far reach')
nl.transform({ apply = function(_, x, y, z) return x + 100, y, z end })
T.check(nl.nearest(110, 0, 1, 1)[1] ~= nil and nl.nearest(10, 0, 1, 1)[1] == nil, 'a rebase moves them')
nl.dropKey('1:0_0')
T.check(nl.status().lenses == 1, 'its tile goes: its lenses')
nl.onRows('1:1_0', { { 0, 1, 1, 1 } }, { 'streetlight' })
T.check(nl.status().lenses == 0, 'a tile without lenses: none kept')

core_environment = { getLightState = function() return { isNight = true } end }
T.check(nl.isNight() == true, 'night from the stock environment')
-- the sun drawn up: day, whatever the clock says (a ScatterSky at 144 degrees: a morning sun over a night clock)
T.check(nl.sunUpAt(144) and nl.sunUpAt(30) and not nl.sunUpAt(200) and not nl.sunUpAt(-10) and not nl.sunUpAt(1)
  and nl.sunUpAt(nil) == nil, 'sunUpAt: over the horizon from 2 to 178 degrees')
local realFind, realFindObj = scenetree.findClassObjects, scenetree.findObject
scenetree.findClassObjects = function(c) return c == 'ScatterSky' and { 'sunsky' } or {} end
scenetree.findObject = function(n) return n == 'sunsky' and { elevation = 144 } or nil end
T.check(nl.isNight() == false, 'night by the clock, the sun drawn up: not night')
scenetree.findObject = function(n) return n == 'sunsky' and { elevation = 250 } or nil end
T.check(nl.isNight() == true, '... the sun drawn down: night')
scenetree.findClassObjects, scenetree.findObject = realFind, realFindObj
core_environment = nil
T.check(nl.isNight() == nil, 'no environment (the menu): unknown')
-- the stadiums' evenings and the Castle Rock star's season
T.check(nl.evening(19.5) and not nl.evening(23.5) and not nl.evening(2.0), 'stadium lights: dusk to 23:00 only')
T.check(not nl.starSeason(2026, 11, 20) and nl.starSeason(2026, 11, 21), 'the star: from the Saturday before Thanksgiving (2026-11-26)')
T.check(nl.starSeason(2026, 12, 31) and nl.starSeason(2027, 1, 25) and not nl.starSeason(2027, 1, 26), '... to the Stock Show')
T.check(not nl.starSeason(2026, 10, 28) and not nl.starSeason(nil, 1, 1), 'not in October, not without a date')
T.check(nl.starSeason(2025, 11, 22) and not nl.starSeason(2025, 11, 21), '2025: Thanksgiving 11-27, Starlighting 11-22')

local ng = require('beamngEarth/nightglow')
local r1, h1 = ng.glowSize(1)
local r7 = ng.glowSize(7)
T.check(r1 > r7 and h1 >= ng.GLOW_H_MIN_M, 'a big city glows wider than a town')
T.check(ng.glowSize(99) == ng.GLOW_R[ng.GLOW_RANK_MAX], 'ranks past the last: the last size')
-- the glows only at night (by day the fog pass lit their additive cylinders: pale rings in the sky)
local S = dofile(dir .. '/stubs.lua')
readFile = function() return 'export default {};' end
jsonDecode = function() return { places = { { 'Denver', -104.99, 39.74, 1, 1 } } } end
core_camera = { getPosition = function() return vec3(0, 0, 1600) end }
local anchor = { geodeticToEnu = function(_, lat, lon, h) return (lon + 104.99) * 85000, (lat - 39.74) * 111000, h end }
local function glows() local n = 0 for _, o in ipairs(S.liveOf('TSStatic')) do
  if o.fields.shapeName == ng.GLOW_SHAPE then n = n + 1 end end return n end
ng.reset()
ng.update(anchor, 1, false)
T.check(glows() == 0, 'by day: no glow')
ng.reset()
ng.update(anchor, 1, true)
T.check(glows() == 1, 'at night: the near city glows: ' .. glows())
ng.update(anchor, 1, false)
T.check(glows() == 1, 'checked every CHECK_S only')
os.clockhp = function() return 10 end
ng.update(anchor, 1, false)
T.check(glows() == 0, 'day again: the glow goes')
ng.update(anchor, 1, nil)
T.check(glows() == 0, 'unknown (the menu): no glow')
T.done('test_nightlights')
