-- Route anchors (a baked route's fixed re-centre points): the anchors a route was pre-built in (tools/route_bake.py
-- writes them, with the plan of every route it baked, to FILE). A teleport or rebase near one lands on it instead of
-- on the snapped grid point at the player (geodesy.snapAnchor), so the server's revisit cache finds the blocks and
-- tiles built in that frame: the grid point depends on where the car is when the rebase is asked for, and a drive
-- that asked 50 m later can snap to the next one (a different frame: nothing baked there).
--
-- FILE: {"version": 1, "pinM": metres, "anchors": [[lat, lon], ...], "routes": {...}} ("routes": the bake's own
-- record). The nearest anchor within pinM of the player wins; none within it: the grid as before.
--
-- The frame is the nearest planned anchor's (want, core.lua pinWant; rebase.decide's want): as soon as a planned anchor
-- other than the current one is rebase.PIN_HYST_M nearer than it, the world moves to that anchor, whatever path the car
-- took (the distance rule alone made the anchor depend on where the 15 km line was crossed, so a tile could be wanted
-- under any of ~8 frames and no bake could cover an area). A planned area (tools/route_bake.py plan --area) is a lattice
-- of anchors with every point within pinM of one, so every drive in it is in a baked frame.
local geodesy = require('beamngEarth/geodesy')

local M = {}

M.FILE = '/settings/beamngEarth_route_anchors.json'
M.MAX_PIN_M = 9000          -- never farther (the player's float precision in the frame: rebase.lua's distances)

M.CACHE_S = 2               -- load(): the file read again after this long (want() asks every PIN_CHECK_M of driving)
local cache = nil           -- { at = os.clock(), anchors = {...}, pinM = n }

-- The anchors (a list of {lat, lon}) and the pin distance, from FILE (empty when there is none or it is broken). Read
-- again CACHE_S after the last read (clock: os.clock, or the caller's; nil: read now), so a bake planned while the game
-- runs applies within seconds and a pin check every few metres of driving costs no file reads.
function M.load(clock)
  local now = clock or os.clock()
  if cache and now - cache.at < M.CACHE_S and now >= cache.at then return cache.anchors, cache.pinM end
  local anchors, pinM = M.read()
  cache = { at = now, anchors = anchors, pinM = pinM }
  return anchors, pinM
end

function M.forget() cache = nil end

function M.read()
  if not (FS and FS.fileExists and FS:fileExists(M.FILE)) then return {}, 0 end
  local read = rawget(_G, 'jsonReadFile')
  local ok, data = pcall(function() return read and read(M.FILE) end)
  local anchors, pinM = {}, 0
  if ok and type(data) == 'table' and type(data.anchors) == 'table' then
    for _, a in ipairs(data.anchors) do
      local lat, lon = tonumber(a[1] or a.lat), tonumber(a[2] or a.lon)
      if lat and lon and math.abs(lat) <= 90 and math.abs(lon) <= 180 then anchors[#anchors + 1] = { lat, lon } end
    end
    pinM = math.min(math.max(tonumber(data.pinM) or 0, 0), M.MAX_PIN_M)
  elseif not ok then
    log('W', 'beamngEarth', 'route anchors: ' .. tostring(data))
  end
  return anchors, pinM
end

-- The route anchor nearest to (lat, lon) within the pin distance (and within maxM if given: core passes under half
-- its rebase distance, so a rebase never lands on the anchor it leaves when the setting is shorter than the bake's
-- plan); its lat, lon, and the player's spot (dx, dy) in its frame; nil when there is none.
function M.pin(lat, lon, maxM)
  local anchors, pinM = M.load()
  if maxM then pinM = math.min(pinM, maxM) end
  if pinM <= 0 or #anchors == 0 then return nil end
  local best, bx, by, bd
  for _, a in ipairs(anchors) do
    if math.abs(a[1] - lat) < 0.2 then         -- (~22 km: the frames' ENU is only good nearby)
      local x, y = geodesy.newAnchor(a[1], a[2]):geodeticToEnu(lat, lon, 0)
      local d = math.sqrt(x * x + y * y)
      if d <= pinM and (bd == nil or d < bd) then best, bx, by, bd = a, x, y, d end
    end
  end
  if not best then return nil end
  return best[1], best[2], bx, by
end

-- The pinned-anchor rule for a player at (lat, lon), dist from the current anchor (curLat, curLon) (nil: none yet):
-- -> want, aLat, aLon, dx, dy with the nearest planned anchor within maxM (pin) and the player's spot in its frame;
-- want nil when there is none (rebase.decide: the distance rule alone), false when it is the current anchor or not
-- hyst (rebase.PIN_HYST_M) nearer than the current one, true when the frame should move to it.
function M.want(lat, lon, curLat, curLon, dist, maxM, hyst)
  local aLat, aLon, dx, dy = M.pin(lat, lon, maxM)
  if not aLat then return nil end
  if curLat and math.abs(aLat - curLat) < 1e-7 and math.abs(aLon - curLon) < 1e-7 then return false, aLat, aLon, dx, dy end
  local d = math.sqrt(dx * dx + dy * dy)
  return (dist or math.huge) - d >= (hyst or 300), aLat, aLon, dx, dy
end


return M
