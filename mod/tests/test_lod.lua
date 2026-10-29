-- luajit mod/tests/test_lod.lua  (lod.lua: the detail distance's trees, road fade and engine detail)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')

local scaled, refreshed = nil, 0
local trees = { setDistanceScale = function(s) scaled = s end }
local roads = { FADE = '450 150' }
package.loaded['beamngEarth/trees'] = trees
package.loaded['beamngEarth/roads'] = roads
local vars = { ['$pref::TS::detailAdjust'] = 2.0 }
VariableRegistry = { get = function(k) return vars[k] end, set = function(k, v) vars[k] = v end }

local L = require('beamngEarth/lod')
T.check(L.setLevel('far') == 'far' and scaled == 1.6 and roads.FADE == '650 200', 'far: trees scaled, the road fade out')
T.check(vars['$pref::TS::detailAdjust'] == 2.0, 'outside our level: the game\'s detail')
L.update(true)
T.check(vars['$pref::TS::detailAdjust'] == 3.0, 'in our level: ours')
L.setLevel('max')
T.check(vars['$pref::TS::detailAdjust'] == 4.0 and scaled == 2.2, 'max: farther still')
L.update(false)
T.check(vars['$pref::TS::detailAdjust'] == 2.0, 'leaving the level: the game\'s own back')
L.setLevel('normal')
L.update(true)
T.check(vars['$pref::TS::detailAdjust'] == 2.0 and scaled == 1.0 and roads.FADE == '450 150', 'normal: nothing changed')
L.update(false)
vars['$pref::TS::detailAdjust'] = 5.0
L.setLevel('far')
L.update(true)
T.check(vars['$pref::TS::detailAdjust'] == 5.0, 'a player\'s higher detail is kept')
L.update(false)
T.check(vars['$pref::TS::detailAdjust'] == 5.0, '... and still theirs after')
T.check(L.setLevel('bogus') == L.DEFAULT, 'an unknown level: the default')

-- trees.setDistanceScale on the real module: distances times the scale from the loaded values, rocks kept
package.loaded['beamngEarth/trees'] = nil
dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/stubs.lua')
local real = require('beamngEarth/trees')
local ring1, door, rock = real.RINGS[1][1], real.GROUP_MAX_M.door, real.GROUP_MAX_M.rock
real.setDistanceScale(2)
T.check(real.RINGS[1][1] == ring1 * 2 and real.GROUP_MAX_M.door == door * 2 and real.GROUP_MAX_M.rock == rock,
  'trees: rings and props doubled, rocks kept')
real.setDistanceScale(1)
T.check(real.RINGS[1][1] == ring1 and real.GROUP_MAX_M.door == door, 'trees: back to the loaded values')
T.done('test_lod')
