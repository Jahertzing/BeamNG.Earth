-- Sky probe (tools/mcp.py luafile tools/sky_probe.lua [step]): what draws the sun. The night probe of 2026-10-28 set
-- the TimeOfDay to a December 21:30 (core_environment said night, the street lamps lit) and the shots were broad
-- daylight. This dumps every TimeOfDay and ScatterSky's sun, core_environment's view and core_celestial's functions;
-- with BNGE_SKY_STEP = 'night' first it sets 21:30 through core_environment.setTimeOfDay (the sky manual, not saved),
-- with 'restore' it puts the sky back on real time (the setting) and syncs it.
local step = rawget(_G, 'BNGE_SKY_STEP')
local sky = require('beamngEarth/sky')
if step == 'night' then
  sky.setMode('manual')
  core_environment.setTimeOfDay({ time = 0.3958, play = true, dayLength = 1e7 })
elseif step == 'restore' then
  require('beamngEarth/settings').set('skyMode', 'real')
  sky.setMode('real')
  sky.sync()
end
local out = { step = step or 'dump', skies = {}, tods = {} }
for _, n in ipairs(scenetree.findClassObjects('ScatterSky') or {}) do
  local o = scenetree.findObject(n)
  if o then
    out.skies[n] = { elevation = o.elevation, azimuth = o.azimuth, brightness = o.brightness,
                     skyBrightness = o.skyBrightness, sunScale = tostring(o.sunScale) }
  end
end
for _, n in ipairs(scenetree.findClassObjects('TimeOfDay') or {}) do
  local t = scenetree.findObject(n)
  if t then
    out.tods[n] = { time = t.time, play = t.play, dayLength = t.dayLength, year = t.year, month = t.month,
                    day = t.day, utcOffset = t.utcOffset, latitude = t.latitude, longitude = t.longitude,
                    azimuthOverride = t.azimuthOverride }
  end
end
local ok, ls = pcall(core_environment.getLightState)
out.lightState = ok and ls or tostring(ls)
ok, ls = pcall(core_environment.getTimeOfDay)
out.envTod = ok and ls or tostring(ls)
out.skyStatus = sky.status and sky.status() or nil
if core_celestial then
  local fns = {}
  for k, v in pairs(core_celestial) do if type(v) == 'function' then fns[#fns + 1] = k end end
  table.sort(fns)
  out.celestialFns = table.concat(fns, ' ')
end
return jsonEncode(out)
