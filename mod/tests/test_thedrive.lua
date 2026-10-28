-- luajit mod/tests/test_thedrive.lua  (The Drive: Frederick to Castle Rock, timed; thedrive.lua)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local D = require('beamngEarth/thedrive')
local G = require('beamngEarth/gps')
local P = require('beamngEarth/places')
local sky = require('beamngEarth/sky')

T.check(D.fmt(59.6) == '1:00' and D.fmt(3725) == '1:02:05' and D.fmt(-3) == '0:00', 'times as m:ss and h:mm:ss')
T.check(D.vsBest(100, nil) == ' (first run)', 'no best yet')
T.check(D.vsBest(90, 100):find('new best, was 1:40', 1, true), 'a new best')
T.check(D.vsBest(110, 100) == ' (best 1:40, +0:10)', 'slower than the best')
T.check(D.ROUTES.downtown.vias[1].name == 'Union Station' and #D.ROUTES.i25.vias == 0 and D.ROUTES.i25.avoid == 'toll',
  'two routes: through Union Station, and straight down I-25 off the toll roads')
local g = D.hoursFor('golden', os.time({ year = 2026, month = 6, day = 21, hour = 12 }), 39.74, -104.99)
T.check(g and g > 19 and g < 20.5, 'golden hour in June: before the ~20:31 sunset: ' .. tostring(g))
T.check(D.hoursFor('night') == 21.5 and D.hoursFor('real') == nil, 'night 21:30, real: left alone')

-- a whole run against stubs: teleport, loading, the GPS set, the clock from the first move, the split, the finish
local msgs, teleports, saved = {}, {}, nil
ui_message = function(t) msgs[#msgs + 1] = t end
jsonReadFile = function() return nil end
jsonWriteFile = function(path, data) saved = data; return true end
local cstate = { inOurLevel = true }
beamngEarth_core = { _state = function() return cstate end,
  teleport = function(lat, lon, name) teleports[#teleports + 1] = { lat, lon, name }; return true end,
  requestRoute = function() return true end }
beamngEarth_settings = { get = function(k) return ({ theDriveTime = 'night', theDriveAutodrive = false })[k] end }
local hours
sky.setLocalHour = function(h) hours = h; return h end
P.cannotGo = function() return nil end
D._reset()

T.check(D.start('i25'), 'started')
T.check(#teleports == 1 and teleports[1][1] == D.START.lat, 'off to Frederick')
T.check(D.status().phase == 'teleport' and D.hudLine(D.status()):find('loading Frederick', 1, true), 'teleporting')
local far = { lat = 39.0, lon = -105.0, vx = 0, vy = 0 }
local here = { lat = D.START.lat + 0.001, lon = D.START.lon, vx = 0, vy = 0 }
local loading = true
local function step(s, p, sim) for _ = 1, math.floor(s / 0.25 + 0.5) do D.update(0.25, sim or 0.25, p, function() return loading end) end end
step(5, far)
T.check(D.status().phase == 'teleport' and G.status() == nil, 'not there yet: no GPS')
step(5, here)
T.check(D.status().phase == 'teleport', 'there, still loading: waiting')
loading = false
step(3, here)
local gs = G.status()
T.check(D.status().phase == 'ready' and gs and gs.name == 'Castle Rock' and gs.avoid == 'toll' and gs.viasLeft == 0,
  'loaded: the GPS to Castle Rock off the toll roads')
T.check(hours == 21.5, 'the time of day from the setting (night)')
step(4, here)
T.check(D.status().phase == 'ready' and D.status().t == 0, 'standing: the clock waits')
local moving = { lat = here.lat, lon = here.lon, vx = 10, vy = 0 }
step(1, moving)
T.check(D.status().phase == 'driving', 'moving: the clock runs')
step(100, moving)
T.check(math.abs(D.status().t - 100) < 1, 'about 100 s on the clock: ' .. D.status().t)
step(10, moving, 0)
T.check(math.abs(D.status().t - 100) < 1, 'paused (no game time): the clock stops')
T.check(D.hudLine(D.status()):find('The Drive (I-25) 1:4', 1, true), 'the bar shows the clock: ' .. D.hudLine(D.status()))
-- the GPS arrives at Castle Rock
G.arrivals = (G.arrivals or 0) + 1
G.lastArrival = { name = 'Castle Rock' }
G.clear()
step(0.5, moving)
T.check(D.status().phase == 'done' and saved and saved.routes.i25.best and saved.routes.i25.runs == 1,
  'arrived: the time saved as the best')
T.check(msgs[#msgs]:find('Castle Rock!', 1, true) and msgs[#msgs]:find('first run', 1, true), 'the finish message')
step(D.DONE_S + 1, moving)
T.check(D.status() == nil, 'the result goes after DONE_S')

-- through downtown: the split at Union Station; the GPS cleared ends a run
T.check(D.start('downtown', { auto = false, time = 'real' }), 'downtown started')
hours = nil
step(5, here)
T.check(hours == nil and G.status().viasLeft == 1, 'time kept, the GPS through Union Station')
step(1, moving)
step(30, moving)
local s = G.status()
T.check(s.via.name == 'Union Station', 'next: Union Station')
-- the car at Union Station (the gps passes the waypoint itself in update; here the count as it would be)
local anchor = { geodeticToEnu = function(_, lat, lon) return (lon - D.ROUTES.downtown.vias[1].lon) * 1e5,
  (lat - D.ROUTES.downtown.vias[1].lat) * 1e5, 0 end }
G.update(anchor, { x = 10, y = 10, lat = D.ROUTES.downtown.vias[1].lat, lon = D.ROUTES.downtown.vias[1].lon })
step(0.5, moving)
T.check(#D.status().splits == 1 and D.status().splits[1].name == 'Union Station', 'the split at Union Station')
T.check(msgs[#msgs]:find('Union Station: 0:3', 1, true), 'its message: ' .. msgs[#msgs])
T.check(D.status().nextName == 'Castle Rock', 'next: Castle Rock')
G.clear()
step(0.5, moving)
T.check(D.status() == nil and msgs[#msgs]:find('ended', 1, true), 'the GPS cleared: the run ends')

-- not in our level: refused
cstate.inOurLevel = false
local ok, why = D.start('downtown')
T.check(not ok and why:find('level', 1, true), 'another level: refused')
cstate.inOurLevel = true

-- the autodrive key without a route
T.check(D.toggleAutodrive() == nil, 'no route: nothing to drive')
T.check(D.records().downtown.runs == 0 and D.records().i25.best ~= nil, 'records per route')

T.done('test_thedrive')
