-- The night's ambient light (the user, 2026-10-28: "the streetlamps and lights dont actually have any ambient light
-- bouncing ... outside of the direct cone is dark as heck"). The stock real sky (core_celestial, loaded by sky.lua)
-- blacks out the ScatterSky's nightColor and nightFogColor (celestial.lua enableChrome: so its stars stay crisp), and
-- the night's ambient with them: whatever a lamp's cone missed was black. This gives the night a sky again: a city's
-- (sodium and LED light scattered back down by the air: a dim warm brown, the light that lets you see a street's
-- walls and yards between the lamps) near the big places (nightglow.lua's, by their rank), a starlit country one
-- away from them, scaled by the "Night ambient light" setting. Written every CHECK_S when it changed (the sky reads it
-- each frame; the stock night colour blends in as the sun sets, so it is the same field day and night).
local M = {}

local nightglow = require('beamngEarth/nightglow')

M.CHECK_S = 2.0
-- the night's colour (ScatterSky nightColor: the night's ambient and sky) and its fog's, linear RGB
M.URBAN = { 0.060, 0.046, 0.034 }
M.URBAN_FOG = { 0.040, 0.032, 0.025 }
M.RURAL = { 0.010, 0.011, 0.016 }
M.RURAL_FOG = { 0.006, 0.007, 0.010 }
M.STRENGTH = { off = 0, dim = 0.5, normal = 1.0, bright = 2.0 }
M.FULL_R = 1.0              -- a place's city light is full within this many of its glow radii (nightglow.glowSize)
M.GONE_R = 3.0              -- ... and gone this many out
M.BIG_R = 14000             -- (a place's weight: its glow radius over this, square-rooted: Denver 1, Boulder ~0.5)
M.SHARE_STEP = 0.05
M.mode = 'normal'
M.errors = 0

local lastCheck = -1e9
local cache = { anchorId = nil, pts = nil }   -- the places in the anchor's frame: { {x, y, R, w}, ... }
local written = nil                            -- the colour string last written (nil: none)

-- Pure: the share (0..1) of the city's light at (x, y) from places pts ({ {x, y, R, w} }): the strongest one's,
-- full within FULL_R of its radius, fading smoothly to nothing at GONE_R.
function M.urbanShare(pts, x, y)
  local best = 0
  for _, p in ipairs(pts or {}) do
    local d = math.sqrt((p[1] - x) ^ 2 + (p[2] - y) ^ 2) / p[3]
    local t = math.max(0, math.min(1, (M.GONE_R - d) / (M.GONE_R - M.FULL_R)))
    local s = p[4] * t * t * (3 - 2 * t)
    if s > best then best = s end
  end
  return best
end

-- Pure: the colour string for a share and strength k: rural to urban, times k.
function M.colorFor(rural, urban, share, k)
  local c = {}
  for i = 1, 3 do c[i] = string.format('%.4f', (rural[i] + (urban[i] - rural[i]) * share) * k) end
  return c[1] .. ' ' .. c[2] .. ' ' .. c[3] .. ' 1'
end

-- Pure: is a colour field's value (a string, 'r g b a') within 0.002 of the colour string c?
function M.same(field, c)
  local a, b = {}, {}
  for v in tostring(field or ''):gmatch('[^%s]+') do a[#a + 1] = tonumber(v) end
  for v in c:gmatch('[^%s]+') do b[#b + 1] = tonumber(v) end
  for i = 1, 3 do
    if not a[i] or not b[i] or math.abs(a[i] - b[i]) > 0.002 then return false end
  end
  return true
end

local function sky()
  local names = scenetree.findClassObjects and scenetree.findClassObjects('ScatterSky')
  return names and names[1] and scenetree.findObject(names[1]) or nil
end

local function points(anchor, anchorId)
  if cache.anchorId == anchorId and cache.pts then return cache.pts end
  local pts = {}
  for _, p in ipairs(nightglow.places()) do
    local R = nightglow.glowSize(p.rank)
    local x, y = anchor:geodeticToEnu(p.lat, p.lon, 0)
    pts[#pts + 1] = { x, y, R, math.min(1, math.sqrt(R / M.BIG_R)) }
  end
  cache.anchorId, cache.pts = anchorId, pts
  return pts
end

-- Every frame (nightlights.update; the work every CHECK_S). anchor, anchorId: the frame of the places.
function M.update(anchor, anchorId)
  local now = os.clockhp()
  if now - lastCheck < M.CHECK_S then return end
  lastCheck = now
  local k = M.STRENGTH[M.mode] or 1
  if k <= 0 or not anchor then return end
  local o = sky()
  if not o then return end
  local cam = core_camera.getPosition()
  -- (in steps of SHARE_STEP: driving through a town writes the sky a few times, not every check)
  local share = math.floor(M.urbanShare(points(anchor, anchorId), cam.x, cam.y) / M.SHARE_STEP + 0.5) * M.SHARE_STEP
  M.share = share
  local c = M.colorFor(M.RURAL, M.URBAN, share, k)
  -- (core_celestial writes black over it once per sky: read back, not only remembered)
  if c == written and M.same(o:getField('nightColor', 0), c) then return end
  local ok = pcall(function()
    o:setField('nightColor', 0, c)
    o:setField('nightFogColor', 0, M.colorFor(M.RURAL_FOG, M.URBAN_FOG, share, k))
  end)
  if ok then written = c else M.errors = M.errors + 1 end
end

-- The "Night ambient light" setting: off (the real sky's black night), dim, normal, bright.
function M.setMode(v)
  local was = M.mode
  M.mode = M.STRENGTH[v] and v or 'normal'
  lastCheck, written = -1e9, nil
  if M.mode == 'off' and was ~= 'off' then           -- back to the real sky's
    local o = sky()
    if o then pcall(function() o:setField('nightColor', 0, '0 0 0 1') o:setField('nightFogColor', 0, '0 0 0 1') end) end
  end
end

-- A new level: the places' frame and the sky are new.
function M.reset()
  lastCheck, written = -1e9, nil
  cache = { anchorId = nil, pts = nil }
end

function M.status()
  return { mode = M.mode, share = M.share, written = written, errors = M.errors }
end

return M
