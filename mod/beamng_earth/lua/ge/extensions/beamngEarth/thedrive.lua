-- The Drive (the user, 2026-10-28: "a great drive from Frederick to downtown Denver, into Castle Rock", then "id also
-- like to add going straight down i-25 to castle rock too, not just through downtown"). One key, Stream Deck button or
-- settings-page button puts the car in Frederick, sets the GPS to Castle Rock (through Denver Union Station, or
-- straight down I-25, keeping off the toll roads where the router can; routing.openstreetmap.de can't, but its
-- plain route stays on I-25), sets the time of day (golden hour by default: it starts before sunset and gets dark
-- on the way) and times the run. The clock starts when the car first moves, shows on the status bar, takes a split
-- at each waypoint and keeps the best times per route in FILE. The car can drive itself the whole way (the GPS
-- autodrive, gps.lua).
--
-- Phases: nil (none) -> 'teleport' (the world loading at the start) -> 'ready' (the GPS set, waiting for the car to
-- move) -> 'driving' -> 'done' (the result on the bar for DONE_S). The GPS cleared or sent elsewhere ends it.
-- Pure but for the globals it reaches through rawget (runs under plain luajit with stubs).
local M = {}

local gps = require('beamngEarth/gps')
local places = require('beamngEarth/places')
local sky = require('beamngEarth/sky')

-- Old Town Frederick (4th Street): the start
M.START = { lat = 40.09953, lon = -104.93700, name = 'Frederick' }
-- downtown Castle Rock (Wilcox Street just south of 3rd): the finish
M.DEST = { lat = 39.37180, lon = -104.86002, name = 'Castle Rock' }
M.ROUTES = {
  downtown = { label = 'through downtown Denver', short = 'Downtown',
    vias = { { lat = 39.75292, lon = -104.99975, name = 'Union Station' } } },   -- Wynkoop Street at the station
  i25 = { label = 'straight down I-25', short = 'I-25', vias = {}, avoid = 'toll' },
}
M.ROUTE_ORDER = { 'downtown', 'i25' }

M.FILE = '/settings/beamngEarth_thedrive.json'
M.GOLDEN_BEFORE_H = 0.75   -- golden hour: the drive starts this long before today's sunset
M.GOLDEN_FALLBACK_H = 17.5 -- (no sunset: a polar day or night)
M.HOURS = { midday = 12.5, night = 21.5 }
M.TIMES = { 'golden', 'midday', 'night', 'real' }   -- the setting theDriveTime ('real': the sky left as it is)
M.NEAR_START_M = 1500      -- the teleport landed: the player this close to the start
M.SETTLE_S = 2             -- ... and the world loaded (or LOAD_WAIT_S passed) this long
M.LOAD_WAIT_S = 90         -- the GPS is set after this long near the start even while the world still loads
M.TELEPORT_GIVEUP_S = 240  -- no landing in this long: the drive is called off
M.MOVE_MS = 2              -- the clock starts when the car first goes faster than this (m/s, horizontal)
M.DONE_S = 45              -- the result stays on the bar this long
M.SPLIT_MSG_S = 8
M.CHECK_S = 0.25

local run = nil            -- { route, phase, since (real s), nearSince, time, auto, t (sim s), splits = {}, viasSeen,
                           --   arrivals, result }
local clockReal = 0
local checkAt = 0
local record = nil         -- the file: { routes = { [route] = { best, splits = { [name] = s }, runs, last } } }

local function msg(text, ttl)
  local m = rawget(_G, 'ui_message')
  if m then pcall(m, text, ttl or 6, 'beamngEarthTheDrive', 'directions_car') end
  local lg = rawget(_G, 'log')
  if lg then lg('I', 'beamngEarth', 'the drive: ' .. text) end
end

-- Pure: seconds as m:ss or h:mm:ss.
function M.fmt(s)
  s = math.max(0, math.floor((tonumber(s) or 0) + 0.5))
  local h, m, x = math.floor(s / 3600), math.floor(s / 60) % 60, s % 60
  if h > 0 then return string.format('%d:%02d:%02d', h, m, x) end
  return string.format('%d:%02d', m, x)
end

-- Pure: a time against the best (nil: none yet): ' (best 1:02:03)', ' (new best, was 1:02:03)' or ' (new best)'.
function M.vsBest(t, best)
  if not best then return ' (first run)' end
  if t < best then return ' (new best, was ' .. M.fmt(best) .. ')' end
  return ' (best ' .. M.fmt(best) .. ', +' .. M.fmt(t - best) .. ')'
end

------------------------------------------------------------------------------------------
-- the best times (FILE)

function M.load(path)
  local read = rawget(_G, 'jsonReadFile')
  local ok, data = pcall(function() return read and read(path or M.FILE) end)
  record = (ok and type(data) == 'table' and type(data.routes) == 'table') and data or { routes = {} }
  return record
end

local function routeRecord(key)
  if not record then M.load() end
  local r = record.routes[key]
  if type(r) ~= 'table' then r = { runs = 0, splits = {} }; record.routes[key] = r end
  if type(r.splits) ~= 'table' then r.splits = {} end
  return r
end

local function save()
  local write = rawget(_G, 'jsonWriteFile')
  if write and record then pcall(write, M.FILE, record, true) end
end

-- The best times for the settings page: { [route] = { best, last, runs, label } }.
function M.records()
  local out = {}
  for _, k in ipairs(M.ROUTE_ORDER) do
    local r = routeRecord(k)
    out[k] = { best = r.best, last = r.last, runs = r.runs or 0, label = M.ROUTES[k].label }
  end
  return out
end

------------------------------------------------------------------------------------------
-- the time of day

local function setting(key)
  local st = rawget(_G, 'beamngEarth_settings')
  return st and st.get and st.get(key) or nil
end

-- The local hours for a time choice at (lat, lon) now (epoch), nil for 'real' or an unknown one.
function M.hoursFor(which, epoch, lat, lon)
  if which == 'golden' then
    local set = sky.sunsetToday(epoch or os.time(), lat, lon)
    return set and (set - M.GOLDEN_BEFORE_H) or M.GOLDEN_FALLBACK_H
  end
  return M.HOURS[which]
end

local function timeSet(set)
  if set then
    msg(string.format('Time set to %d:%02d (Real time again at the next level load, or in the settings)',
      math.floor(set), math.floor(set % 1 * 60)), 4)
  end
  return set ~= nil
end

-- The sky set to golden hour / midday / night now, wherever the player is (also the Stream Deck's sky keys).
function M.skyNow(which)
  local here = { places.here() }
  local lat, lon = here[1] or M.START.lat, here[2] or M.START.lon
  local h = M.hoursFor(which, os.time(), lat, lon)
  if not h then return false end
  return timeSet(sky.setLocalHour(h, 'a sky key'))
end

-- The clock dh hours on or back from the time it shows (the Stream Deck's +1 h / -1 h keys).
function M.skyShift(dh)
  return timeSet(sky.shiftLocalHour(dh, 'a sky key'))
end

------------------------------------------------------------------------------------------
-- starting

-- Why The Drive cannot start now (nil: it can).
function M.cannotStart()
  local core = rawget(_G, 'beamngEarth_core')
  local st = core and core._state and core._state()
  if st and st.inOurLevel == false then return 'load the BeamNG.Earth level first' end
  return places.cannotGo()
end

-- The run over: the sky's mode before its time of day comes back (sky.setLocalHour is live, for this drive only).
local function stop()
  run = nil
  sky.restoreLive('The Drive')
end

-- route: 'downtown' | 'i25' (default downtown); opts: { auto = true|false, time = one of TIMES } (default: the
-- settings theDriveAutodrive, theDriveTime). Returns true, or false and why.
function M.start(route, opts)
  route = M.ROUTES[route] and route or 'downtown'
  opts = type(opts) == 'table' and opts or {}
  local why = M.cannotStart()
  if why then msg('The Drive: ' .. why, 5); return false, why end
  gps.clear()                                     -- (an autodrive on stops before the car is moved)
  stop()                                          -- (a run under way: its time of day goes)
  local core = rawget(_G, 'beamngEarth_core')
  if not core.teleport(M.START.lat, M.START.lon, 'The Drive: ' .. M.START.name) then
    msg('The Drive: the teleport was refused (see the status line)', 5)
    return false, 'teleport refused'
  end
  local auto = opts.auto
  if auto == nil then auto = setting('theDriveAutodrive') == true end
  local time = opts.time or setting('theDriveTime') or 'golden'
  run = { route = route, phase = 'teleport', since = clockReal, time = time, auto = auto and true or false, t = 0,
    splits = {} }
  msg(string.format('The Drive %s: off to %s, loading the world ...', M.ROUTES[route].label, M.START.name), 5)
  return true
end

-- The GPS autodrive on or off for the route under way (the Ctrl+Alt+A key). Returns the state, or nil without a route.
function M.toggleAutodrive()
  if not gps.status() then msg('Autodrive: no GPS route (start The Drive or pick a place on the map)', 4); return nil end
  local on = gps.setAutodrive(not gps.autodrive())
  if run then run.auto = on end
  msg(on and 'Autodrive on: the car drives the route' or 'Autodrive off: your car', 3)
  return on
end

function M.cancel()
  if run and run.phase ~= 'done' then msg('The Drive called off', 4) end
  stop()
end

-- The run for the settings page, the bar and the tests: { route, phase, t, auto, splits, result, nextName } or nil.
function M.status()
  if not run then return nil end
  return { route = run.route, phase = run.phase, t = run.t, auto = run.auto, splits = run.splits, result = run.result,
    nextName = run.nextName }
end

------------------------------------------------------------------------------------------
-- every frame

-- Pure: the status bar's lead for the run (nil: none).
function M.hudLine(r)
  if not r then return nil end
  local name = 'The Drive (' .. M.ROUTES[r.route].short .. ')'
  if r.phase == 'teleport' then return name .. ': loading Frederick' end
  if r.phase == 'ready' then return name .. ': the clock starts when you go' .. (r.auto and ' (autodrive)' or '') end
  if r.phase == 'done' then return name .. ' ' .. (r.result or '') end
  local next = r.nextName and (' · next ' .. r.nextName) or ''
  return name .. ' ' .. M.fmt(r.t) .. next
end

local function speed(player)
  local vx, vy = player and player.vx or 0, player and player.vy or 0
  return math.sqrt(vx * vx + vy * vy)
end

local function begin()
  local r = M.ROUTES[run.route]
  gps.setDestination(M.DEST.lat, M.DEST.lon, M.DEST.name, r.vias, r.avoid)
  local h = M.hoursFor(run.time, os.time(), M.START.lat, M.START.lon)
  if h then sky.setLocalHour(h, 'The Drive') end
  if run.auto then gps.setAutodrive(true) end
  local s = gps.status()
  run.phase, run.viasSeen, run.arrivals = 'ready', 0, gps.arrivals or 0
  run.nextName = s and s.via and s.via.name or M.DEST.name
  msg(string.format('The Drive %s to %s is set. %s', r.label, M.DEST.name,
    run.auto and 'Autodrive on: sit back.' or 'The clock starts when you go.'), 8)
end

local function finish(t)
  local rec = routeRecord(run.route)
  local text = M.fmt(t) .. M.vsBest(t, rec.best)
  if not rec.best or t < rec.best then rec.best = t end
  rec.last, rec.runs = t, (rec.runs or 0) + 1
  if run.auto then rec.lastAuto = true end
  save()
  run.phase, run.result, run.since = 'done', text, clockReal
  msg(string.format('%s! The Drive %s: %s', M.DEST.name, M.ROUTES[run.route].label, text), 15)
end

local function split(name, t)
  local rec = routeRecord(run.route)
  local best = tonumber(rec.splits[name])
  run.splits[#run.splits + 1] = { name = name, t = t }
  msg(string.format('%s: %s%s', name or 'Waypoint', M.fmt(t), M.vsBest(t, best)), M.SPLIT_MSG_S)
  if not best or t < best then rec.splits[name] = t end
end

-- dtReal, dtSim: the frame's real and game seconds (the clock runs on game time: paused, it stops); player: core's
-- { lat, lon, vx, vy } or nil; loading(): whether the world is still loading round the player.
function M.update(dtReal, dtSim, player, loading)
  clockReal = clockReal + (dtReal or 0)
  if not run then return end
  if run.phase == 'driving' then run.t = run.t + math.max(0, dtSim or 0) end
  if clockReal < checkAt then return end
  checkAt = clockReal + M.CHECK_S

  if run.phase == 'teleport' then
    local core = rawget(_G, 'beamngEarth_core')
    local st = core and core._state and core._state()
    local near = player and player.lat and places.distance(player.lat, player.lon, M.START.lat, M.START.lon)
      <= M.NEAR_START_M and not (st and st.pending)
    if not near then
      run.nearSince = nil
      if clockReal - run.since > M.TELEPORT_GIVEUP_S then
        msg('The Drive: Frederick did not load, called off', 6)
        stop()
      end
      return
    end
    run.nearSince = run.nearSince or clockReal
    local busy = loading and loading()
    if busy then run.loadedSince = nil else run.loadedSince = run.loadedSince or clockReal end
    if (run.loadedSince and clockReal - run.loadedSince >= M.SETTLE_S) or clockReal - run.nearSince > M.LOAD_WAIT_S then
      begin()
    end
    return
  end

  if run.phase == 'done' then
    if clockReal - run.since > M.DONE_S then stop() end
    return
  end

  -- ready / driving: the GPS still on our route
  local s = gps.status()
  if (gps.arrivals or 0) > run.arrivals then
    local a = gps.lastArrival
    if a and a.name == M.DEST.name and run.phase == 'driving' then finish(run.t) else stop() end
    return
  end
  if not s or s.name ~= M.DEST.name then
    msg('The Drive ended: the GPS was cleared or set elsewhere', 5)
    stop()
    return
  end
  if run.phase == 'ready' then
    if speed(player) > M.MOVE_MS then
      run.phase, run.t = 'driving', 0
      msg('Go! The clock is running.', 3)
    end
    return
  end
  local passed = s.viasPassed or 0
  while run.viasSeen < passed do
    run.viasSeen = run.viasSeen + 1
    local w = M.ROUTES[run.route].vias[run.viasSeen]
    split(w and w.name, run.t)
  end
  run.nextName = s.via and s.via.name or M.DEST.name
end

function M._reset() run, record, clockReal, checkAt = nil, nil, 0, 0 end -- tests

return M
