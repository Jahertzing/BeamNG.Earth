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

-- stutters named (2026-10-29): the mod's largest part of each 50-100 ms frame, the first few a minute as lines with
-- the parts and what the mod was doing; the GPU pass timings when the engine gives them; the pacing numbers
lines = {}
P.reset()
now = 0
ctx = function() return { lat = 39.7, lon = -105, mph = 120, doing = 'tree tiles 900 pending' } end
P.setRefresh(85)
run(5, 1000 / 85)
for i = 1, 5 do
  now = now + 0.07
  for _, l in ipairs(P.frame(70, now, ctx, { mod = { roads = 38 + i, trees = 4 }, engine = { Lua = 52 } }, nil, nil)) do
    lines[#lines + 1] = l
  end
end
local sl = find('perf stutter')
T.check(#sl == P.STUTTER_LINES and sl[1]:find('^perf stutter 70 ms at 39.7') ~= nil and sl[1]:find('mod: roads 39', 1, true)
  and sl[1]:find('engine: Lua 52', 1, true) and sl[1]:find('doing: tree tiles', 1, true),
  'stutter lines: the first ' .. P.STUTTER_LINES .. ' a minute, with the parts and the doing')
now = now + 0.2
lines[#lines + 1] = P.frame(200, now, ctx, { mod = {}, engine = { Render = 150 }, gpu = { Shadows = 41.2, Main = 9 } }, nil, nil)[1]
local hl = find('perf hitch')
T.check(#hl == 1 and hl[1]:find('gpu: Shadows 41.2, Main 9', 1, true), 'a hitch line carries the GPU pass timings')
now = now + 3
local gl = P.frame(150, now, ctx, { mod = {}, engine = { LuaGC = 140 }, heap = { 243.4, 171.2 } }, nil, nil)
T.check(#gl == 1 and gl[1]:find('heap 243 -> 171 MB', 1, true), 'a LuaGC hitch line says what the frame did to the heap')
run(61, 1000 / 85)
m = find('perf minute')
T.check(#m >= 1 and m[#m]:find('stutter parts: roads 5 (43 ms)', 1, true), "the minute line: the mod's largest part of each stutter, its worst: " .. tostring(m[#m]))
T.check(m[#m]:find('pacing: %d+%% within 11.8 ms, %d+%% at 2%+ refreshes, jitter [%d.]+ ms') ~= nil, "pacing: within the refresh's budget, two refreshes or more, jitter")
local within, two = m[#m]:match('pacing: (%d+)%% within 11.8 ms, (%d+)%% at 2%+ refreshes')
T.check(tonumber(within) >= 99 and tonumber(two) == 0, 'steady 85 fps: all within, none at two refreshes (' .. tostring(within) .. ', ' .. tostring(two) .. ')')
lines = {}
run(30, 1000 / 85)
for _ = 1, 100 do now = now + 0.0235; P.frame(23.5, now, ctx, nil, nil, nil); now = now + 0.0118; P.frame(11.8, now, ctx, nil, nil, nil) end
run(61, 1000 / 85)
m = find('perf minute')
within, two = m[#m]:match('pacing: (%d+)%% within 11.8 ms, (%d+)%% at 2%+ refreshes')
local jit = m[#m]:match('jitter ([%d.]+) ms')
T.check(tonumber(two) >= 1 and tonumber(jit) > 0.3, 'vsync misses (23.5 ms frames): counted at two refreshes, the jitter up (' .. tostring(two) .. '%, ' .. tostring(jit) .. ' ms)')
-- the refresh from the game's video mode, the desktop mode's string or table, else nothing
T.check(P.refreshFrom({ width = 3440, height = 1440, refreshRate = 85 }, nil) == 85, 'the video mode table')
T.check(P.refreshFrom(nil, '3440 1440 Window 32 85 0') == 85, 'the desktop mode string (lap 1 said 16.7 ms at 85 Hz)')
T.check(P.refreshFrom({ refreshRate = 0 }, { refreshRate = 100 }) == 100, 'a zero rate falls through to the desktop table')
T.check(P.refreshFrom(nil, 'garbage') == nil and P.refreshFrom(nil, nil) == nil, 'neither: nil')
GFXDevice = { getVideoMode = function() return { refreshRate = 100 } end }
T.check(P.gameRefresh() == 100, 'gameRefresh reads GFXDevice')
GFXDevice = nil
getDesktopVideoMode = function() return '2560 1440 Borderless 32 144 0' end
T.check(P.gameRefresh() == 144, 'gameRefresh falls back to the desktop mode')
getDesktopVideoMode = nil
T.check(P.gameRefresh() == nil, 'gameRefresh: nothing known')
-- the budget: the longer of the refresh period and the cap's (a 97 cap under 100 Hz: 10.31 ms, lap 3 2026-10-29)
T.check(P.capFrom({ fpsCap = 97, foregroundCap = 97, foregroundActive = true, backgroundCap = 60, backgroundActive = false, vsync = true }) == 97, 'the cap in force')
T.check(P.capFrom({ fpsCap = 0, foregroundCap = 85, foregroundActive = false, backgroundCap = 30, backgroundActive = true }) == 30, 'the background cap while it is active')
T.check(P.capFrom('vsync') == nil and P.capFrom({ fpsCap = 0 }) == nil, 'no cap: nil')
GFXDevice = { getVideoMode = function() return { refreshRate = 100 } end }
Engine = { getCurrentFpsLimitReason = function() return { fpsCap = 97, foregroundCap = 97, foregroundActive = true } end }
T.near(P.gameBudgetMs(), 1000 / 97, 1e-9, 'budget: the cap under the refresh')
Engine = { getCurrentFpsLimitReason = function() return { fpsCap = 144 } end }
T.near(P.gameBudgetMs(), 10, 1e-9, 'budget: the refresh under the cap')
Engine = nil
T.near(P.setBudget(P.gameBudgetMs()), 10, 1e-9, 'setBudget')
GFXDevice = nil
T.check(P.gameBudgetMs() == nil, 'budget: nothing known')
P.setRefresh(85)
T.done('test_perflog')
