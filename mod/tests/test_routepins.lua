-- luajit mod/tests/test_routepins.lua  (a baked route's anchors, routepins.lua)
-- The nearest anchor within the pin distance wins (lat, lon and the player's spot in its frame), none within it:
-- nil (the grid as before); no file, a broken file or pinM 0: nil; a rewritten file is read again; pinM is capped.
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local files, stamp = {}, 0
FS = {
  fileExists = function(_, p) return files[p] ~= nil end,
}
function jsonReadFile(p)
  local f = files[p]
  if f and f.data == 'THROW' then error('broken json') end
  return f and f.data
end
function log() end
local function write(data)
  stamp = stamp + 1
  files['/settings/beamngEarth_route_anchors.json'] = { data = data, stamp = stamp }
end

local g = require('beamngEarth/geodesy')
local pins = require('beamngEarth/routepins')

T.check(pins.pin(40.0, -105.0) == nil, 'no file: no pin')
write({ version = 1, pinM = 7500, anchors = { { 40.10, -104.94 }, { 39.97, -104.99 }, { lat = 39.84, lon = -104.99 } }
})
local la, lo, x, y = pins.pin(39.95, -104.985)
T.check(la == 39.97 and lo == -104.99, 'the nearest anchor')
local ex, ey = g.newAnchor(39.97, -104.99):geodeticToEnu(39.95, -104.985, 0)
T.near(x, ex, 1e-9, 'the player east of it'); T.near(y, ey, 1e-9, '... and north')
T.check(pins.pin(39.86, -104.99) == 39.84, 'a {lat, lon} entry too')
T.check(pins.pin(39.5, -104.9) == nil, 'none within 7.5 km: no pin (the grid)')
write({ version = 1, pinM = 0, anchors = { { 40.10, -104.94 } } })
T.check(pins.pin(40.10, -104.94) == nil, 'pinM 0: off')
write({ version = 1, pinM = 50000, anchors = { { 40.10, -104.94 } } })
T.check(pins.pin(40.0, -104.94) == nil, 'pinM capped at MAX_PIN_M (11 km away: no pin)')
T.check(pins.pin(40.05, -104.94) == 40.10, '... 5.6 km away: pinned')
write('THROW')
T.check(pins.pin(40.10, -104.94) == nil, 'a broken file: no pin')
files = {}
T.check(pins.pin(40.10, -104.94) == nil, 'the file removed: no pin')
T.done('test_routepins')
