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
  require('beamngEarth/routepins').forget()      -- (the cache: CACHE_S; a rewritten file is read within seconds)
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
T.check(pins.pin(39.95, -104.985, 1000) == nil, 'maxM caps the pin distance (a shorter rebase setting)')
T.check(pins.pin(39.95, -104.985, 5000) == 39.97, '... within it: pinned')
write({ version = 1, pinM = 0, anchors = { { 40.10, -104.94 } } })
T.check(pins.pin(40.10, -104.94) == nil, 'pinM 0: off')
write({ version = 1, pinM = 50000, anchors = { { 40.10, -104.94 } } })
T.check(pins.pin(40.0, -104.94) == nil, 'pinM capped at MAX_PIN_M (11 km away: no pin)')
T.check(pins.pin(40.05, -104.94) == 40.10, '... 5.6 km away: pinned')
write('THROW')
T.check(pins.pin(40.10, -104.94) == nil, 'a broken file: no pin')
files = {}
pins.forget()
T.check(pins.pin(40.10, -104.94) == nil, 'the file removed: no pin')

-- the cache: the file is read again only CACHE_S after the last read (clock given)
write({ version = 1, pinM = 7500, anchors = { { 40.10, -104.94 } } })
local a1 = pins.load(100)
files['/settings/beamngEarth_route_anchors.json'] = { data = { version = 1, pinM = 7500, anchors = {} } }
T.check(#pins.load(101) == 1 and #a1 == 1, 'within CACHE_S: the cached anchors')
T.check(#pins.load(100 + pins.CACHE_S) == 0, 'after CACHE_S: read again')

-- want: the frame is the nearest planned anchor's (rebase.decide's want), with hysteresis
write({ version = 1, pinM = 7500, anchors = { { 40.10, -104.94 }, { 39.97, -104.99 } } })
local A, B = { 40.10, -104.94 }, { 39.97, -104.99 }
local function at(lat, lon, cur) -- the player at (lat, lon) in cur's frame
  local x, y = g.newAnchor(cur[1], cur[2]):geodeticToEnu(lat, lon, 0)
  return pins.want(lat, lon, cur[1], cur[2], math.sqrt(x * x + y * y), 6750, 300)
end
T.check(at(40.09, -104.94, A) == false, 'near A, in A: stay')
T.check(at(39.98, -104.99, A) == true, 'near B, in A: move to B')
local w, la = at(39.98, -104.99, A)
T.check(la == 39.97, '... B is the anchor returned')
T.check(at(39.98, -104.99, B) == false, 'near B, in B: stay')
T.check(at(39.5, -104.9, A) == nil, 'no planned anchor within the pin distance: nil (the distance rule)')
-- the midpoint between A and B (7.2 km from each): outside the pin distance of both: nil; just past the midpoint
-- towards B, B within 6.75 km but not 300 m nearer than A: false; 300 m nearer: true
local mid = { (A[1] + B[1]) / 2, (A[2] + B[2]) / 2 }
T.check(at(mid[1], mid[2], A) == nil, 'the midpoint: neither within the pin distance')
local dLat = 0.0012 -- ~130 m
T.check(at(B[1] + 0.0607 - dLat * 0, B[2], A) ~= true, 'B just within 6.75 km, not 300 m nearer: stay')
T.check(at(B[1] + 0.055, B[2], A) == true, 'B clearly nearer: move')
T.done('test_routepins')
