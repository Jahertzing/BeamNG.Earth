-- The player car's headlights, brighter and reaching further (the user, 2026-10-29: "my headlights do like nothing").
-- A vehicle's lamps are its jbeam's SPOTLIGHT props (func lowbeam, highbeam, ...); their lights get their brightness,
-- range and cone once at the spawn (common/jbeam/sections/meshs.lua, setLightArgs), and the lamps that fade in and out
-- again from the vehicle's own Lua (vehicle/props.lua, setPropLight from v.data.props). So both sides are scaled: the
-- engine's lights here and the vehicle's prop table by a command. The setting "headlights": stock, bright, brighter.
local M = {}

-- the modes: { brightness, range } times the vehicle's own
M.MODES = { stock = { 1, 1 }, bright = { 2, 1.5 }, brighter = { 4, 2 } }
M.mode = 'bright'
M.CHECK_S = 1.0
M.errors = 0
M.lastError = nil

local applied = { vid = nil, vdata = nil, mode = nil, n = 0 }
local lastCheck = -1e9

-- Pure: is a prop one of the headlamps (a spot light run by a beam's electric: lowbeam, highbeam, lowhighbeam, ...)?
function M.isHeadlamp(prop)
  return type(prop) == 'table' and prop.mesh == 'SPOTLIGHT' and type(prop.func) == 'string'
    and prop.func:find('beam', 1, true) ~= nil and prop.pid ~= nil
end

-- The vehicle side: its prop table's headlamps' brightness, from their own (kept as bneBase) times k; lastVal reset so
-- props.lua writes the light again on its next frame.
function M.vluaCommand(k)
  return string.format("local k=%.4f local ps=v.data.props if ps then for i=0,tableSizeC(ps)-1 do local p=ps[i] " ..
    "if p and p.mesh=='SPOTLIGHT' and type(p.func)=='string' and p.func:find('beam',1,true) and p.lightBrightness then " ..
    "p.bneBase=p.bneBase or p.lightBrightness p.lightBrightness=p.bneBase*k p.lastVal=math.huge end end end", k)
end

-- Pure: a jbeam light colour ({ r, g, b, a } 0..255 or '#rrggbbaa') as a ColorF, as the spawn reads it (meshs.lua's own
-- parseColor; the global parseColor gives a packed number, which setLightArgs refuses: "expect userdata, got number").
function M.colorOf(c)
  if type(c) == 'table' then
    return ColorF((c.r or 0) / 255, (c.g or 0) / 255, (c.b or 0) / 255, (c.a or 255) / 255)
  elseif type(c) == 'string' and #c > 7 and c:sub(1, 1) == '#' then
    local h = c:gsub('#', '')
    local function x(i) return (tonumber(h:sub(i, i + 1), 16) or 0) / 255 end
    return ColorF(x(1), x(3), x(5), x(7))
  end
  return ColorF(0, 0, 0, 0)
end

-- The engine side: each headlamp's light set again from its own values (kept as bneBase) times the mode's. Returns how
-- many lamps.
local function scale(veh, props, k, r)
  local n = 0
  for i = 0, (tableSizeC and tableSizeC(props) or #props) - 1 do
    local p = props[i]
    if M.isHeadlamp(p) then
      p.bneBase = p.bneBase or { b = p.lightBrightness or 1, r = p.lightRange or 10 }
      local po = veh:getProp(p.pid)
      local light = po and po:getLight()
      if light then
        local att = p.lightAttenuation and vec3(p.lightAttenuation) or vec3(0, 1, 1)
        light:setLightArgs(p.lightInnerAngle or 40, p.lightOuterAngle or 45, p.bneBase.b * k, p.bneBase.r * r,
          M.colorOf(p.lightColor), att, p.lightCastShadows or false)
        n = n + 1
      end
    end
  end
  return n
end

local function step()
  local veh = be:getPlayerVehicle(0)
  if not veh then return end
  local vid = veh:getID()
  local vd = core_vehicle_manager and core_vehicle_manager.getVehicleData(vid)
  local vdata = vd and vd.vdata
  -- (a new car, a respawn (new jbeam data, new lights) or a new setting)
  if not vdata or (applied.vid == vid and applied.vdata == vdata and applied.mode == M.mode) then return end
  local m = M.MODES[M.mode] or M.MODES.stock
  -- (tried once per car and setting: a failure is kept for status, not retried every check)
  applied.vid, applied.vdata, applied.mode = vid, vdata, M.mode
  local ok, n = pcall(scale, veh, vdata.props or {}, m[1], m[2])
  if not ok then M.errors, M.lastError = M.errors + 1, tostring(n) end
  applied.n = ok and n or 0
  veh:queueLuaCommand(M.vluaCommand(m[1]))
end

-- Every frame (nightlights.update); the work every CHECK_S, and only when the car or the setting changed.
function M.update()
  local now = os.clockhp()
  if now - lastCheck < M.CHECK_S then return end
  lastCheck = now
  local ok = pcall(step)
  if not ok then M.errors = M.errors + 1 end
end

function M.setMode(v)
  M.mode = M.MODES[v] and v or 'bright'
  lastCheck = -1e9
end

function M.reset()
  applied = { vid = nil, vdata = nil, mode = nil, n = 0 }
  lastCheck = -1e9
end

function M.status()
  return { mode = M.mode, lamps = applied.n, vid = applied.vid, errors = M.errors, lastError = M.lastError }
end

return M
