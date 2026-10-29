-- The performance log (the user, 2026-10-23: "update the log to include as much useful info as possible. Including
-- detecting any performance drops and where they occurred. So players who experience issues can send me their log").
-- Fed every frame by core.lua; returns lines for the world server's log (server.log, the file players send, beside
-- the server's own block timings) and the game's beamng.log:
--   perf hitch    a frame of HITCH_MS or more: where, how fast, the mod's parts and the engine's subsystems in it, and
--                 what the mod was doing (core's context: block swap, far tiles, roads, trees, holds, server backlog)
--   perf drop     a WINDOW_S window under DROP_SHARE of the usual frame rate (the median of the last BASELINE_N
--                 windows): the window's fps, its slowest frames, the mod's parts summed over it, the engine's mean
--   perf back     the frame rate back over BACK_SHARE of the usual: how long the drop lasted, its lowest fps
--   perf minute   every SUMMARY_S: average fps, 1 % low, hitches, the worst frame and its cause, where; the stutters
--                 (frames of STUTTER_MS up to HITCH_MS: under the hitch line, but a visible jerk at 85-90 fps) and the
--                 causes of both by count
-- Pure but for the clock and the context the caller passes.
local M = {}

M.HITCH_MS = 100
M.STUTTER_MS = 50           -- (core.lua's hitch record starts here: a cause for each)
M.WINDOW_S = 5
M.DROP_SHARE = 0.65
M.BACK_SHARE = 0.85
M.BASELINE_N = 24           -- (2 minutes of windows)
M.BASELINE_MIN = 3          -- windows before a drop can be called
M.MIN_FPS = 12              -- a usual rate below this (a weak machine, a menu): no drops called
M.SUMMARY_S = 60
M.HITCH_GAP_S = 2           -- hitch lines at most this often (the ones between are counted into the next)
M.ENGINE_EVERY = 5          -- the engine's breakdown sampled every this many frames for the window means
-- Stutters (2026-10-29: 240 frames of 50-100 ms on Jeff's 36-minute drive, 15-21 a minute at speed, and the minute
-- line named only the engine's subsystem of each: "Lua" said nothing about which part of the mod). The mod's largest
-- part in each stutter of PART_MIN_MS or more is counted ("stutter parts: roads 4 (38 ms)"), and the first
-- STUTTER_LINES a minute get a line of their own with the parts and what the mod was doing, as the hitches have.
M.STUTTER_LINES = 3
M.PART_MIN_MS = 5
-- Pacing (Jeff, 2026-10-29: the frame rate "60, 80, 50, 80, 90, 70, over and over"): the minute line's share of
-- frames within the display's refresh period (BUDGET_MS, from M.setRefresh), the share that took two refreshes or
-- more (vsync: a frame over the budget waits for the next refresh), and the mean change from one frame to the next.
M.REFRESH_HZ = 60
M.BUDGET_MS = 1000 / 60

local w, base, frames, sumMin, hitchesMin, worstMin, lastHitchAt, skipped, drop, summaryAt
local stuttersMin, causesMin, reloadsAt, partsMin, stutterLines, prevMs, jitterSum, jitterN

function M.reset()
  w = { t0 = nil, n = 0, ms = 0, worst = {}, mod = {}, eng = {}, engN = 0, k = 0 }
  base = {}
  frames, sumMin, hitchesMin, worstMin = {}, 0, 0, nil
  lastHitchAt, skipped, drop, summaryAt = -math.huge, 0, nil, nil
  stuttersMin, causesMin, reloadsAt = 0, { stutter = {}, hitch = {} }, nil
  partsMin, stutterLines, prevMs, jitterSum, jitterN = {}, 0, nil, 0, 0
end
M.reset()

-- The display's refresh (Hz): the pacing numbers' frame budget.
function M.setRefresh(hz)
  hz = tonumber(hz)
  if hz and hz > 0 then M.REFRESH_HZ, M.BUDGET_MS = hz, 1000 / hz end
  return M.BUDGET_MS
end

-- Pure: the refresh (Hz) from the game's video mode (GFXDevice.getVideoMode(): a table with refreshRate) or the
-- desktop video mode (getDesktopVideoMode(): "3440 1440 Window 32 85 0", width height mode depth refresh ...; or a
-- table), nil when neither says. (Lap 1 of 2026-10-29 ran at 85 Hz and the minute lines said "within 16.7 ms".)
function M.refreshFrom(videoMode, desktopMode)
  if type(videoMode) == 'table' and tonumber(videoMode.refreshRate) and videoMode.refreshRate > 0 then
    return tonumber(videoMode.refreshRate)
  end
  if type(desktopMode) == 'table' and tonumber(desktopMode.refreshRate) and desktopMode.refreshRate > 0 then
    return tonumber(desktopMode.refreshRate)
  end
  if type(desktopMode) == 'string' then
    local f = {}
    for w in desktopMode:gmatch('%S+') do f[#f + 1] = w end
    local hz = tonumber(f[5])
    if hz and hz > 0 then return hz end
  end
  return nil
end

-- The refresh from the game itself (GFXDevice, getDesktopVideoMode), or nil.
function M.gameRefresh()
  local G = rawget(_G, 'GFXDevice')
  local okV, vm = pcall(function() return G and G.getVideoMode and G.getVideoMode() end)
  local okD, dm = pcall(function() local f = rawget(_G, 'getDesktopVideoMode'); return f and f() end)
  return M.refreshFrom(okV and vm or nil, okD and dm or nil)
end

-- The largest numeric entry of a {name = ms} table: name, ms (nil when none).
local function largest(t)
  local bk, best = nil, 0
  for k, v in pairs(t or {}) do if type(v) == 'number' and v > best then best, bk = v, k end end
  return bk, best
end

local function r1(v) return math.floor(v * 10 + 0.5) / 10 end
local function r0(v) return math.floor(v + 0.5) end

-- "k1 v1, k2 v2" of a {name = ms} table, largest first, at most n, those of min ms or more
local function top(t, n, min)
  local arr = {}
  for k, v in pairs(t or {}) do if type(v) == 'number' and v >= (min or 0) then arr[#arr + 1] = { k, v } end end
  table.sort(arr, function(a, b) return a[2] > b[2] end)
  local out = {}
  for i = 1, math.min(n, #arr) do out[i] = arr[i][1] .. ' ' .. r1(arr[i][2]) end
  return table.concat(out, ', ')
end
M.top = top

-- The cause named for a slow frame: its largest measured part (the mod's, else the engine's) when that part is at
-- least CAUSE_SHARE of the frame; else UNMEASURED (a first-time load, the GPU driver, the OS: outside both timers).
M.CAUSE_SHARE = 0.25
M.UNMEASURED = 'mostly outside the mod and engine timers (a load, the GPU driver or the system)'
function M.cause(ms, hitch)
  local best, bk = 0, nil
  for _, part in ipairs({ hitch and hitch.mod or {}, hitch and hitch.engine or {} }) do
    for k, v in pairs(part) do if type(v) == 'number' and v > best then best, bk = v, k end end
  end
  if bk and best >= M.CAUSE_SHARE * ms then return bk .. ' ' .. r1(best) end
  return M.UNMEASURED
end

-- The part's name of a cause ('Ticking 187.9' -> 'Ticking'), 'unmeasured' for UNMEASURED
local function causeName(c)
  return c:match('^(.-) [%d.]+$') or 'unmeasured'
end

-- "Ticking 6, Lua 4" of a {name = count} table, most first, at most n
local function counts(t, n)
  local arr = {}
  for k, v in pairs(t) do arr[#arr + 1] = { k, v } end
  table.sort(arr, function(a, b) return a[2] > b[2] or (a[2] == b[2] and a[1] < b[1]) end)
  local out = {}
  for i = 1, math.min(n, #arr) do out[i] = arr[i][1] .. ' ' .. arr[i][2] end
  return table.concat(out, ', ')
end

local function median(t)
  local s = {}
  for i, v in ipairs(t) do s[i] = v end
  table.sort(s)
  local n = #s
  if n == 0 then return nil end
  if n % 2 == 1 then return s[(n + 1) / 2] end
  return 0.5 * (s[n / 2] + s[n / 2 + 1])
end

-- "at 39.28345,-76.59341, 52 mph, block 1024,512 [extra]" from the caller's context
local function where(c)
  if not c then return '' end
  local s = {}
  if c.lat and c.lon then s[#s + 1] = string.format('at %.5f,%.5f', c.lat, c.lon) end
  if c.mph then s[#s + 1] = string.format('%d mph', r0(c.mph)) end
  if c.block then s[#s + 1] = 'block ' .. c.block end
  if c.state and c.state ~= '' then s[#s + 1] = c.state end
  return table.concat(s, ', ')
end

-- One frame. ms: its wall time; now: a clock (s); ctx(): the caller's context {lat, lon, mph, block, state, doing,
-- extra, reloads (collision rebuilds so far: each a whole static collision rebuild, 10-80 ms on the 2026-10-28 drive)}
-- (called only when a line is made); hitch: core's hitch record for this frame ({mod = {}, engine = {}}) or nil;
-- mod: the mod's parts this frame {name = ms} (summed over the window); engine(): the engine's breakdown {name = ms}
-- (sampled every ENGINE_EVERY frames). Returns a list of lines (often empty).
function M.frame(ms, now, ctx, hitch, mod, engine)
  local out = {}
  if type(ms) ~= 'number' or ms ~= ms or ms <= 0 then return out end
  -- a clock that went back (core's st.clock starts at 0 again with each level start): the old marks would hold every
  -- line back for as long as the last session ran (2026-10-28: a whole 22-minute drive after a level restart unlogged)
  if (summaryAt and now < summaryAt) or (w.t0 and now < w.t0) or now < lastHitchAt then M.reset() end
  summaryAt = summaryAt or now
  -- stutters
  if ms >= M.STUTTER_MS and ms < M.HITCH_MS then
    stuttersMin = stuttersMin + 1
    local n = causeName(M.cause(ms, hitch))
    causesMin.stutter[n] = (causesMin.stutter[n] or 0) + 1
    local pk, pv = largest(hitch and hitch.mod)       -- (the mod's largest part in it, whatever the cause names)
    if pk and pv >= M.PART_MIN_MS then
      local p = partsMin[pk] or { n = 0, max = 0 }
      p.n, p.max = p.n + 1, math.max(p.max, pv)
      partsMin[pk] = p
    end
    if stutterLines < M.STUTTER_LINES then
      stutterLines = stutterLines + 1
      local c = ctx and ctx() or {}
      local line = string.format('perf stutter %d ms %s', r0(ms), where(c))
      if hitch and next(hitch.mod or {}) then line = line .. ' | mod: ' .. top(hitch.mod, 4, 1) end
      if hitch and next(hitch.engine or {}) then line = line .. ' | engine: ' .. top(hitch.engine, 4, 1) end
      if hitch and next(hitch.gpu or {}) then line = line .. ' | gpu: ' .. top(hitch.gpu, 4, 1) end
      if c.doing and c.doing ~= '' then line = line .. ' | doing: ' .. c.doing end
      out[#out + 1] = line
    end
  end
  -- pacing: the change from the last frame
  if prevMs then jitterSum, jitterN = jitterSum + math.abs(ms - prevMs), jitterN + 1 end
  prevMs = ms
  -- hitches
  if ms >= M.HITCH_MS then
    hitchesMin = hitchesMin + 1
    local n = causeName(M.cause(ms, hitch))
    causesMin.hitch[n] = (causesMin.hitch[n] or 0) + 1
    if not worstMin or ms > worstMin.ms then worstMin = { ms = ms, cause = M.cause(ms, hitch) } end
    if now - lastHitchAt >= M.HITCH_GAP_S then
      local c = ctx and ctx() or {}
      local line = string.format('perf hitch %d ms %s', r0(ms), where(c))
      if hitch and next(hitch.mod or {}) then line = line .. ' | mod: ' .. top(hitch.mod, 5, 1) end
      if hitch and next(hitch.engine or {}) then line = line .. ' | engine: ' .. top(hitch.engine, 5, 1) end
      if hitch and next(hitch.gpu or {}) then line = line .. ' | gpu: ' .. top(hitch.gpu, 4, 1) end
      -- a LuaGC-led frame: what it did to the heap (a drop of tens of MB in one frame is a full collection)
      if hitch and hitch.heap and (tonumber(hitch.engine and hitch.engine.LuaGC) or 0) >= 20 then
        line = line .. string.format(' | heap %.0f -> %.0f MB', hitch.heap[1], hitch.heap[2])
      end
      if M.cause(ms, hitch) == M.UNMEASURED then line = line .. ' | ' .. M.UNMEASURED end
      if c.doing and c.doing ~= '' then line = line .. ' | doing: ' .. c.doing end
      if skipped > 0 then line = line .. string.format(' | (%d more hitches since the last line)', skipped) end
      out[#out + 1] = line
      lastHitchAt, skipped = now, 0
    else
      skipped = skipped + 1
    end
  end
  -- the minute's frames
  frames[#frames + 1] = ms
  sumMin = sumMin + ms
  -- the window
  w.t0 = w.t0 or now
  w.n, w.ms, w.k = w.n + 1, w.ms + ms, w.k + 1
  if #w.worst < 3 or ms > w.worst[#w.worst] then
    w.worst[#w.worst + 1] = ms
    table.sort(w.worst, function(a, b) return a > b end)
    if #w.worst > 3 then w.worst[4] = nil end
  end
  for k, v in pairs(mod or {}) do if type(v) == 'number' then w.mod[k] = (w.mod[k] or 0) + v end end
  if engine and w.k % M.ENGINE_EVERY == 0 then
    local e = engine()
    if e then
      w.engN = w.engN + 1
      for k, v in pairs(e) do if type(v) == 'number' then w.eng[k] = (w.eng[k] or 0) + v end end
    end
  end
  if now - w.t0 >= M.WINDOW_S then
    local fps = w.n / (w.ms / 1000)
    local usual = #base >= M.BASELINE_MIN and median(base) or nil
    if usual and usual >= M.MIN_FPS then
      if not drop and fps < M.DROP_SHARE * usual then
        local c = ctx and ctx() or {}
        local engMean = {}
        for k, v in pairs(w.eng) do engMean[k] = v / math.max(1, w.engN) end
        local worst = {}
        for i, v in ipairs(w.worst) do worst[i] = tostring(r0(v)) end
        local line = string.format('perf drop %.0f fps for %ds (usual %.0f) %s | slowest frames %s ms',
          fps, r0(now - w.t0), usual, where(c), table.concat(worst, ' '))
        local m = top(w.mod, 5, 10)
        if m ~= '' then line = line .. ' | mod over the window (ms): ' .. m end
        local e = top(engMean, 5, 1)
        if e ~= '' then line = line .. ' | engine per frame: ' .. e end
        if c.doing and c.doing ~= '' then line = line .. ' | doing: ' .. c.doing end
        out[#out + 1] = line
        drop = { since = w.t0, low = fps }
      elseif drop then
        drop.low = math.min(drop.low, fps)
        if fps >= M.BACK_SHARE * usual then
          out[#out + 1] = string.format('perf back to %.0f fps after %ds (lowest %.0f) %s', fps, r0(now - drop.since),
            drop.low, where(ctx and ctx() or {}))
          drop = nil
        end
      end
    end
    if not drop then                -- the usual rate: windows outside drops
      base[#base + 1] = fps
      if #base > M.BASELINE_N then table.remove(base, 1) end
    end
    w = { t0 = now, n = 0, ms = 0, worst = {}, mod = {}, eng = {}, engN = 0, k = 0 }
  end
  -- the minute
  if now - summaryAt >= M.SUMMARY_S and #frames > 0 then
    local s = {}
    for i, v in ipairs(frames) do s[i] = v end
    table.sort(s, function(a, b) return a > b end)
    local low = s[math.max(1, math.ceil(#s * 0.01))]
    local c = ctx and ctx() or {}
    local line = string.format('perf minute %.0f fps, 1%% low %.0f, worst %d ms, %d hitches', #frames / (sumMin / 1000),
      1000 / low, r0(s[1]), hitchesMin)
    if worstMin and worstMin.cause ~= '' then line = line .. ' (worst: ' .. worstMin.cause .. ')' end
    line = line .. string.format(', %d stutters %d-%d ms', stuttersMin, M.STUTTER_MS, M.HITCH_MS)
    line = line .. ' ' .. where(c)
    if hitchesMin > 0 then line = line .. ' | hitches by cause: ' .. counts(causesMin.hitch, 5) end
    if stuttersMin > 0 then line = line .. ' | stutters by cause: ' .. counts(causesMin.stutter, 5) end
    if next(partsMin) then
      local arr = {}
      for k, p in pairs(partsMin) do arr[#arr + 1] = { k, p.n, p.max } end
      table.sort(arr, function(a, b) return a[2] > b[2] or (a[2] == b[2] and a[1] < b[1]) end)
      local sp = {}
      for i = 1, math.min(5, #arr) do sp[i] = string.format('%s %d (%d ms)', arr[i][1], arr[i][2], r0(arr[i][3])) end
      line = line .. ' | stutter parts: ' .. table.concat(sp, ', ')
    end
    do                              -- pacing: within the refresh's budget, two refreshes or more, frame-to-frame change
      local within, two = 0, 0
      for _, v in ipairs(frames) do
        if v <= M.BUDGET_MS + 0.5 then within = within + 1 end
        if v >= 2 * M.BUDGET_MS - 1 then two = two + 1 end
      end
      line = line .. string.format(' | pacing: %d%% within %.1f ms, %d%% at 2+ refreshes, jitter %.1f ms',
        r0(100 * within / #frames), M.BUDGET_MS, r0(100 * two / #frames), jitterN > 0 and jitterSum / jitterN or 0)
    end
    if c.extra and c.extra ~= '' then line = line .. ' | ' .. c.extra end
    if c.reloads and reloadsAt then line = line .. string.format(', %d collision rebuilds', c.reloads - reloadsAt) end
    local okP, pop = pcall(c.popin or function() return '' end)
    if okP and type(pop) == 'string' and pop ~= '' then line = line .. ' | popped in within 300 m: ' .. pop end
    local okG, gcs = pcall(c.gc or function() return '' end)
    if okG and type(gcs) == 'string' and gcs ~= '' then line = line .. ' | ' .. gcs end
    reloadsAt = c.reloads
    out[#out + 1] = line
    frames, sumMin, hitchesMin, worstMin, summaryAt = {}, 0, 0, nil, now
    stuttersMin, causesMin = 0, { stutter = {}, hitch = {} }
    partsMin, stutterLines, jitterSum, jitterN = {}, 0, 0, 0
  end
  return out
end

return M
