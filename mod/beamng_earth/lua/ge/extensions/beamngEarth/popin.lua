-- What pops in near the camera (the user, 2026-10-29: "kinda everything still [pops in] a bit"): every object the mod
-- makes is noted with its distance from the camera; the ones made within NEAR_M (where the eye sees them appear) are
-- counted by kind, with the nearest, for the perf log's minute line ("popped in within 300 m: bld 12 (nearest 85 m),
-- deciduous 340 (40 m)"). Things made farther out are not counted: their pop is the distance rings' by design.
local M = {}

M.NEAR_M = 300
M.camera = nil            -- core: function returning {x, y, ...} of the camera this frame, or nil

local counts, nearest = {}, {}
local any = false

local function note(kind, d)
  if d > M.NEAR_M then return end
  kind = tostring(kind or '?')
  counts[kind] = (counts[kind] or 0) + 1
  if not nearest[kind] or d < nearest[kind] then nearest[kind] = d end
  any = true
end

local function cam()
  local ok, c = pcall(M.camera or function() return nil end)
  return ok and c or nil
end

-- An object at (x, y) (world, the current anchor's frame).
function M.notePoint(kind, x, y)
  local c = cam()
  if not c or not x or not y then return end
  note(kind, math.sqrt((x - c.x) ^ 2 + (y - c.y) ^ 2))
end

-- An object filling the square of half side h round (cx, cy) (a road tile's mesh): its nearest point.
function M.noteBox(kind, cx, cy, h)
  local c = cam()
  if not c or not cx or not cy then return end
  local dx = math.max(math.abs(c.x - cx) - h, 0)
  local dy = math.max(math.abs(c.y - cy) - h, 0)
  note(kind, math.sqrt(dx * dx + dy * dy))
end

-- "bld 12 (85 m), deciduous 340 (40 m)", most first (at most n kinds), and the counts start again; '' if none.
function M.take(n)
  if not any then return '' end
  local arr = {}
  for k, v in pairs(counts) do arr[#arr + 1] = { k, v } end
  table.sort(arr, function(a, b) return a[2] > b[2] or (a[2] == b[2] and a[1] < b[1]) end)
  local out = {}
  for i = 1, math.min(n or 8, #arr) do
    out[i] = string.format('%s %d (%d m)', arr[i][1], arr[i][2], math.floor(nearest[arr[i][1]] + 0.5))
  end
  counts, nearest, any = {}, {}, false
  return table.concat(out, ', ')
end

return M
