-- luajit mod/tests/test_headlights.lua  (headlights.lua): the player car's headlamps brighter and reaching further, on
-- the engine's lights and in the vehicle's own prop table, again for a new car or a respawn, from the base each time.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
dofile(dir .. '/stubs.lua')
local clock = 0
os.clockhp = function() return clock end
tableSizeC = function(t) local n = 0 while t[n] ~= nil do n = n + 1 end return n end
parseColor = function() return 0xffffffff end    -- (the global one: a packed number, never used)
ColorF = function(r, g, b, a) return { r, g, b, a } end

local hl = require('beamngEarth/headlights')

T.check(hl.isHeadlamp({ mesh = 'SPOTLIGHT', func = 'lowbeam', pid = 1 }), 'a low beam lamp')
T.check(hl.isHeadlamp({ mesh = 'SPOTLIGHT', func = 'highbeam', pid = 1 }), 'a high beam lamp')
T.check(not hl.isHeadlamp({ mesh = 'POINTLIGHT', func = 'lowbeam', pid = 1 }), 'not a point light')
T.check(not hl.isHeadlamp({ mesh = 'SPOTLIGHT', func = 'brakelight', pid = 1 }), 'not a brake light')
T.check(not hl.isHeadlamp({ mesh = 'SPOTLIGHT', func = 'lowbeam' }), 'not without a prop id')

local function car(id)
  local lights, cmds = {}, {}
  local v = {
    getID = function() return id end,
    getProp = function(_, pid)
      lights[pid] = lights[pid] or { setLightArgs = function(self, ...) self.args = { ... } end }
      return { getLight = function() return lights[pid] end }
    end,
    queueLuaCommand = function(_, c) cmds[#cmds + 1] = c end,
  }
  local vdata = { props = {
    [0] = { mesh = 'SPOTLIGHT', func = 'lowbeam', pid = 7, lightBrightness = 4, lightRange = 60, lightInnerAngle = 10,
            lightOuterAngle = 30, lightColor = { r = 255, g = 204, b = 153, a = 255 } },
    [1] = { mesh = 'SPOTLIGHT', func = 'brakelight', pid = 8, lightBrightness = 1, lightRange = 5 },
    [2] = { mesh = 'SPOTLIGHT', func = 'highbeam', pid = 9, lightBrightness = 8, lightRange = 120 },
  } }
  return v, vdata, lights, cmds
end

local veh, vdata, lights, cmds = car(11)
be = { getPlayerVehicle = function() return veh end }
core_vehicle_manager = { getVehicleData = function() return { vdata = vdata } end }

hl.reset()
hl.setMode('bright')
hl.update()
T.check(lights[7].args[3] == 8 and lights[7].args[4] == 90, 'bright: the low beam twice as bright, half again as far')
local col = lights[7].args[5]
T.check(lights[7].args[1] == 10 and lights[7].args[2] == 30 and type(col) == 'table' and col[1] == 1
  and math.abs(col[2] - 0.8) < 1e-6 and col[4] == 1, '... its own cone and colour, a ColorF')
T.check(lights[9].args[3] == 16 and lights[9].args[4] == 180, '... the high beam too')
T.check(lights[8] == nil, 'the brake light untouched')
T.check(#cmds == 1 and cmds[1]:find('k=2.0000', 1, true), 'the vehicle side told: ' .. tostring(cmds[1]))
T.check(hl.status().lamps == 2, 'status: two lamps')

clock = 5
hl.update()
T.check(#cmds == 1, 'nothing changed: nothing done again')

hl.setMode('brighter')
hl.update()
T.check(lights[7].args[3] == 16 and lights[7].args[4] == 120, 'brighter: from the base, not the bright values')
T.check(#cmds == 2 and cmds[2]:find('k=4.0000', 1, true), '... the vehicle side too')

hl.setMode('stock')
clock = 10
hl.update()
T.check(lights[7].args[3] == 4 and lights[7].args[4] == 60, 'stock: the car\'s own again')

-- a respawn: new jbeam data, new lights
hl.setMode('bright')
local _
_, vdata, lights = car(11)
local v2 = veh
v2.getProp = function(_, pid)
  lights[pid] = lights[pid] or { setLightArgs = function(self, ...) self.args = { ... } end }
  return { getLight = function() return lights[pid] end }
end
clock = 20
hl.update()
T.check(lights[7] and lights[7].args[3] == 8, 'a respawned car: scaled again')

local hx = hl.colorOf('#ff800040')
T.check(hx[1] == 1 and math.abs(hx[2] - 128 / 255) < 1e-6 and math.abs(hx[4] - 64 / 255) < 1e-6, 'colorOf: #rrggbbaa')
T.check(hl.colorOf(nil)[4] == 0, 'colorOf: none, black')
hl.setMode('nonsense')
T.check(hl.mode == 'bright', 'an unknown mode: bright')

-- the vehicle's command runs as Lua (vehicle side): scales from the base once
local vprops = { [0] = { mesh = 'SPOTLIGHT', func = 'lowbeam', lightBrightness = 3 } }
local env = setmetatable({ v = { data = { props = vprops } }, tableSizeC = tableSizeC }, { __index = _G })
local f = assert(loadstring(hl.vluaCommand(2)))
setfenv(f, env)
f()
f()
T.check(vprops[0].lightBrightness == 6 and vprops[0].lastVal == math.huge, 'vehicle side: twice, from the base, once')

be = nil
T.done('test_headlights')
