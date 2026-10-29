-- How far out the detail reaches (the user, 2026-10-29: "grass still pops in a lot weirdly, and kinda everything still
-- a bit"; an RTX 5080). One setting, detailDistance, moves three levers together:
--   trees.lua   the rank rings, the per-tile caps' rings and the small props' distances (GROUP_MAX_M), times `scale`
--               (rocks keep theirs: past the fine ground they float, trees.lua GROUP_MAX_M)
--   roads.lua   the road decals' distanceFade (their tiles reach ~960 m: the fade ends before)
--   the engine  $pref::TS::detailAdjust (the game's Mesh Quality: Ultra is 2.0) while in our level: every shape's LOD
--               and the trees' impostors switch this much farther out; the game's own value back when leaving
-- Console: beamngEarth_lod is not an extension; require('beamngEarth/lod').status()
local M = {}

M.LEVELS = {
  normal = { scale = 1.0, fade = '450 150', detail = nil },
  far = { scale = 1.6, fade = '650 200', detail = 3.0 },
  max = { scale = 2.2, fade = '750 150', detail = 4.0 },
}
M.DEFAULT = 'far'
M.DETAIL_VAR = '$pref::TS::detailAdjust'

local level = M.DEFAULT
local applied = nil        -- the detailAdjust we set (nil: the game's own in force)
local gameDetail = nil     -- the game's value before we set ours

local function registry() return rawget(_G, 'VariableRegistry') end

-- The engine's detail: ours in our level (when the level asks for one above the game's), the game's elsewhere.
local function setDetail(inLevel)
  local reg = registry()
  if not reg or not reg.get or not reg.set then return end
  local want = inLevel and M.LEVELS[level].detail or nil
  if want and applied ~= want then
    if applied == nil then gameDetail = tonumber(reg.get(M.DETAIL_VAR)) end
    if gameDetail and gameDetail >= want then want = nil end      -- (the player's own setting is higher already)
  end
  if want and applied ~= want then
    reg.set(M.DETAIL_VAR, want)
    applied = want
  elseif not want and applied ~= nil then
    if gameDetail then reg.set(M.DETAIL_VAR, gameDetail) end
    applied, gameDetail = nil, nil
  end
end

local inLevelNow = false

-- The setting (settings.lua APPLY): 'normal' | 'far' | 'max'.
function M.setLevel(v)
  if not M.LEVELS[v] then v = M.DEFAULT end
  level = v
  local L = M.LEVELS[v]
  local okT, trees = pcall(require, 'beamngEarth/trees')
  if okT and trees.setDistanceScale then trees.setDistanceScale(L.scale) end
  local okR, roads = pcall(require, 'beamngEarth/roads')
  if okR then roads.FADE = L.fade end
  setDetail(inLevelNow)
  return v
end

-- Once a frame (core onUpdate): our detail in our level only.
function M.update(inLevel)
  inLevel = inLevel and true or false
  if inLevel == inLevelNow then return end
  inLevelNow = inLevel
  setDetail(inLevel)
end

function M.status()
  return { level = level, detailApplied = applied, gameDetail = gameDetail, inLevel = inLevelNow }
end

return M
