-- luajit mod/tests/test_gcwatch.lua  (gcwatch.lua: garbage made per minute, by the mod's parts)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local kb = 100 * 1024
collectgarbage = function(what) assert(what == 'count'); return kb end
local G = require('beamngEarth/gcwatch')
T.check(G.take() == '', 'nothing seen: nothing')
G.begin()
kb = kb + 3 * 1024; G.mark('roads')        -- roads made 3 MB
kb = kb + 1024; G.mark('trees')
kb = kb + 2048                               -- the game's own, after the mod's parts
G.begin()
kb = kb - 4 * 1024                           -- a collector step
G.begin()
local s = G.take()
T.check(s:find('allocated >= 6 MB, freed 4 MB, heap 100-106 MB', 1, true), 'totals: ' .. s)
T.check(s:find('allocated by part (MB): roads 3, trees 1', 1, true), 'by part: ' .. s)
T.check(G.take() == '', 'the counts start again')
T.done('test_gcwatch')
