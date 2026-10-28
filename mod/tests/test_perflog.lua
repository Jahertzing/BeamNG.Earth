-- luajit mod/tests/test_perflog.lua  (the performance log's hitches, drops and minute lines)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local P = require('beamngEarth/perflog')
local now, lines = 0, {}
local ctx = function() return { lat = 39.28345, lon = -76.59341, mph = 52, block = '1024,512', doing = 'far tiles 3 queued' } end
local function run(seconds, ms, hitch, mod)
  local t_end = now + seconds
  while now < t_end do
    now = now + ms / 1000
    for _, l in ipairs(P.frame(ms, now, ctx, hitch, mod, function() return { Render = ms * 0.5, Lua = 2 } end)) do
      lines[#lines + 1] = l
    end
  end
end
local function find(prefix)
  local out = {}
  for _, l in ipairs(lines) do if l:sub(1, #prefix) == prefix then out[#out + 1] = l end end
  return out
end
run(30, 1000 / 60)
T.check(#lines == 0, 'steady 60 fps: nothing logged')
run(10, 50, nil, { roads = 20 })
local d = find('perf drop')
T.check(#d == 1, 'a drop to 20 fps: one line')
T.check(d[1] and d[1]:find('^perf drop 20 fps') and d[1]:find('usual 60', 1, true) and d[1]:find('at 39.28345,-76.59341', 1, true)
  and d[1]:find('52 mph', 1, true) and d[1]:find('roads', 1, true) and d[1]:find('Render', 1, true)
  and d[1]:find('doing: far tiles', 1, true), 'its fps, the usual rate, where, the mod and engine parts, what ran')
run(10, 1000 / 60)
local b = find('perf back')
T.check(#b == 1 and b[1]:find('^perf back to 60 fps after') and b[1]:find('lowest 20', 1, true), 'back: how long, the lowest')
lines = {}
now = now + 0.3
local got = P.frame(300, now, ctx, { mod = { terrainCommit = 280 }, engine = { RenderShadows = 250, Lua = 20 } }, nil, nil)
T.check(#got == 1 and got[1]:find('^perf hitch 300 ms at 39.28345') and got[1]:find('mod: terrainCommit 280', 1, true)
  and got[1]:find('engine: RenderShadows 250', 1, true), 'a hitch: where, the mod part and the engine subsystem')
got = P.frame(200, now + 0.2, ctx, { mod = {}, engine = {} }, nil, nil)
T.check(#got == 0, 'another within HITCH_GAP_S: counted, not a line')
now = now + 3
got = P.frame(150, now, ctx, { mod = {}, engine = {} }, nil, nil)
T.check(#got == 1 and got[1]:find('1 more hitches', 1, true), 'the next line says how many were skipped')
run(70, 1000 / 60)
local m = find('perf minute')
T.check(#m >= 1 and m[1]:find('^perf minute %d+ fps, 1%% low') and m[1]:find('3 hitches', 1, true)
  and m[1]:find('worst: terrainCommit 280', 1, true), 'the minute line: fps, 1 % low, hitches, the worst one\'s cause')
T.check(m[1] and m[1]:find('3 hitches %(worst: terrainCommit 280%), 201 stutters 50%-100 ms') and m[1]:find('hitches by cause: ', 1, true)
  and m[1]:find('terrainCommit 1', 1, true), 'the minute line: the stutter count (the drop\'s 50 ms frames), the hitches by cause')

-- stutters: frames of STUTTER_MS up to HITCH_MS, by cause; the collision rebuilds of the minute
lines = {}
local reloads = 10
ctx = function() return { lat = 39.7, lon = -105, mph = 60, extra = 'Lua 200 MB', reloads = reloads } end
run(61, 1000 / 60)                -- a minute line: the rebuild count's start
for i = 1, 3 do now = now + 0.07; P.frame(70, now, ctx, { mod = {}, engine = { Ticking = 60 } }, nil, nil) end
now = now + 0.06; P.frame(60, now, ctx, { mod = { roads = 40 }, engine = {} }, nil, nil)
now = now + 0.08; P.frame(80, now, ctx, { mod = {}, engine = { FPSLimiter = 2 } }, nil, nil)
reloads = 17
run(61, 1000 / 60)
m = find('perf minute')
T.check(#m == 2 and m[2]:find('0 hitches, 5 stutters 50-100 ms', 1, true)
  and m[2]:find('stutters by cause: Ticking 3, roads 1, unmeasured 1', 1, true), 'stutters counted, by cause: ' .. tostring(m[2]))
T.check(m[2]:find('Lua 200 MB, 7 collision rebuilds', 1, true), 'the minute\'s collision rebuilds')

-- a level restart: the caller's clock starts at 0 again; the lines must not wait for the old clock to come back
lines = {}
now = 0
run(5, 1000 / 60)
P.frame(300, now + 0.3, ctx, { mod = {}, engine = { Lua = 290 } }, nil, nil)
now = now + 0.3
T.check(#find('perf hitch') == 0, 'a hitch this soon: no line made by run()')
lines[#lines + 1] = P.frame(300, now + 0.3, ctx, { mod = {}, engine = { Lua = 290 } }, nil, nil)[1]
now = now + 0.3
run(61, 1000 / 60)
T.check(#find('perf minute') >= 1, 'a minute line within a minute of the clock going back')

T.check(P.cause(1600, { mod = {}, engine = { FPSLimiter = 13 } }) == P.UNMEASURED,
  'a 1.6 s frame with 13 ms measured: not blamed on that part')
T.check(P.cause(300, { mod = { terrainCommit = 280 }, engine = {} }) == 'terrainCommit 280', 'a part that explains it: named')
T.check(#P.frame(0 / 0, now, ctx) == 0 and #P.frame(-1, now, ctx) == 0, 'bad frame times ignored')
T.done('test_perflog')
