-- How fast Lua makes garbage, and which of the mod's parts make it (2026-10-29: LuaGC hitches of 100-157 ms while
-- driving, with the mod holding ~7 MB of a 170-250 MB heap: census.lua). The collector's cycles come as often as
-- garbage is made, and each ends in one atomic step over the whole heap's written tables; the mod's live data is small,
-- so what it can cut is its garbage. The heap is read (collectgarbage('count'), a field read) at the start of the
-- mod's frame (core onUpdate: begin) and at each of its parts' marks (core mark: mark): what it grew by in a part is
-- that part's, and from one frame's start to the next the whole game's. Both are floors (a collector step inside a part
-- or a frame takes off what it frees), for the perf log's minute line:
--   "Lua GC: allocated >= 410 MB, freed 395 MB, heap 170-251 MB | allocated by part (MB): roads 120, trees 31"
local M = {}

local gc = collectgarbage
local last, prevFrame = nil, nil
local parts, alloc, freed, low, high = {}, 0, 0, nil, nil
local frameFrom, frameTo = nil, nil   -- the heap (kb) at the last two frames' starts (M.lastFrame)

function M.begin()
  local kb = gc('count')
  if prevFrame then
    local d = kb - prevFrame
    if d > 0 then alloc = alloc + d else freed = freed - d end
  end
  frameFrom, frameTo = prevFrame, kb
  if not low or kb < low then low = kb end
  if not high or kb > high then high = kb end
  prevFrame, last = kb, kb
end

function M.mark(name)
  local kb = gc('count')
  if last then
    local d = kb - last
    if d > 0 then parts[name] = (parts[name] or 0) + d end
  end
  last = kb
end

-- The heap (MB) at the start of the frame that just ended and at this frame's start: what that frame did to it
-- (a drop of tens of MB in one frame is a full collection, not an incremental step; 2026-10-29: were the LuaGC
-- frames of 103-193 ms full collections? the hitch line says). nil before two frames were seen.
function M.lastFrame()
  if not (frameFrom and frameTo) then return nil end
  return frameFrom / 1024, frameTo / 1024
end

-- The line's text since the last take ('' before a frame was seen), and the counts start again.
function M.take(n)
  if not low then return '' end
  local arr = {}
  for k, v in pairs(parts) do if v >= 512 then arr[#arr + 1] = { k, v } end end
  table.sort(arr, function(a, b) return a[2] > b[2] end)
  local p = {}
  for i = 1, math.min(n or 6, #arr) do p[i] = string.format('%s %.0f', arr[i][1], arr[i][2] / 1024) end
  local s = string.format('Lua GC: allocated >= %.0f MB, freed %.0f MB, heap %.0f-%.0f MB', alloc / 1024, freed / 1024,
    low / 1024, high / 1024)
  if #p > 0 then s = s .. ' | allocated by part (MB): ' .. table.concat(p, ', ') end
  parts, alloc, freed, low, high = {}, 0, 0, nil, nil
  return s
end

function M.reset()
  last, prevFrame, frameFrom, frameTo = nil, nil, nil, nil
  parts, alloc, freed, low, high = {}, 0, 0, nil, nil
end

return M
