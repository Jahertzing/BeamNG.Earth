-- luajit mod/tests/test_census.lua  (census.lua: where the Lua heap goes, by module)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local C = require('beamngEarth/census')

-- a module whose state is in chunk locals (upvalues) and one in its table; a shared game table not counted
local game = {}
for i = 1, 50000 do game[i] = { i } end
local big, small = {}, {}
for i = 1, 30000 do big[i] = { x = i, y = i } end
local modA = { get = function() return big, game end }
local modB = { small = small, name = string.rep('n', 100) }
small[1] = modA                                 -- (a table already counted under a is not counted again)
local r = C.measure({ { 'a', modA }, { 'b', modB } }, { exclude = { game } })
T.check(r.byRoot[1][1] == 'a' and r.byRoot[1][3] > 30000, 'the upvalue tables counted to their module')
T.check(r.byRoot[2][1] == 'b' and r.byRoot[2][3] == 2, 'b: its own table and small, not a again')
T.check(r.biggest[1][1] == 'a.get:big' and r.biggest[1][2] == 30000, 'the biggest table named by its path')
T.check(r.items < 31000 + 10, 'the excluded game table not walked')
local r2 = C.measure({ { 'a', modA } }, { exclude = { game }, maxItems = 100 })
T.check(r2.truncated, 'a cap on the walk')

-- run: the mod's modules from package.loaded, the game's globals excluded
package.loaded['beamngEarth/fake'] = modA
_G.gameThing = game
local line = C.run().line
T.check(line:find('^Lua heap %d+ MB') and line:find('fake') and line:find('fake.get:big 30k', 1, true), 'run: ' .. line)

-- once, after a LuaGC hitch of TRIGGER_MS
T.check(C.afterHitch({ Render = 300 }) == nil and C.afterHitch({ LuaGC = 50 }) == nil, 'other hitches: nothing')
T.check(C.afterHitch({ LuaGC = 144 }):find('^Lua heap'), 'a long LuaGC: the census')
T.check(C.afterHitch({ LuaGC = 200 }) == nil, '... once')
T.done('test_census')
