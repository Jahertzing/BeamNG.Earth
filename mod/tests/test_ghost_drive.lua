-- luajit mod/tests/test_ghost_drive.lua  (tools/ghost_drive.py's GE chunk, GHOST_LUA)
-- The chunk against stubs.lua, a fake car, core and roads, with the mod's real geodesy.lua: a pending anchor or no
-- ground leaves the car alone; a step moves it to the route point in the current anchor's frame (by its reference
-- node, not turned) with the drive's velocity along the road; a turned road, an upset car or a reset sets it again
-- with spawn.safeTeleport facing the road, without the vehicle reset.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
dofile(dir .. '/stubs.lua')

function jsonEncode(v) return v end -- answers stay tables here
function jsonDecode(s) error('the test passes tables') end

-- the chunk out of the tool
local src = io.open(dir .. '/../../tools/ghost_drive.py'):read('*a')
local chunk = src:match('GHOST_LUA = r"""(.-)"""')
assert(chunk, 'GHOST_LUA in tools/ghost_drive.py')
chunk = chunk:gsub('%%%(turn%)r', '20.0')
assert(assert(loadstring(chunk))() == 'ok')
local G = BNGE_GHOST

local geodesy = require('beamngEarth/geodesy')
local anchor = geodesy.newAnchor(40.1, -104.94)

-- the car: position, facing (forward, up), what was done to it
local veh = { pos = vec3(0, 0, 1500), fwd = vec3(0, 1, 0), up = vec3(0, 0, 1), moves = {}, vels = {} }
function veh:getPosition() return vec3(self.pos.x, self.pos.y, self.pos.z) end
function veh:getDirectionVector() return self.fwd end
function veh:getDirectionVectorUp() return self.up end
function veh:getRefNodeId() return 7 end
function veh:getNodePosition(ref) return vec3(0, 1, 0.4) end
function veh:setClusterPosRelRot(ref, x, y, z, qx, qy, qz, qw)
  self.moves[#self.moves + 1] = { ref = ref, x = x, y = y, z = z, q = { qx, qy, qz, qw } }
end
function veh:applyClusterVelocityScaleAdd(ref, scale, vx, vy, vz)
  self.vels[#self.vels + 1] = { ref = ref, scale = scale, vx = vx, vy = vy, vz = vz }
end
function getPlayerVehicle() return veh end

local tele = {}
spawn = { safeTeleport = function(v, pos, rot, a, b, removeTraffic, c, reset)
  tele[#tele + 1] = { pos = pos, rot = rot, removeTraffic = removeTraffic, reset = reset }
end }

local ground = 1490                    -- the surface under any point (nil: none)
local rays = {}
be.getSurfaceHeightBelow = function(self, p)
  rays[#rays + 1] = p
  return ground or -1e10
end

local st = { anchorId = 3, anchor = anchor, serverBacklog = 0 }
beamngEarth_core = { _state = function() return st end, hitches = function() return {} end }
package.loaded['beamngEarth/roads'] = {
  status = function() return { walksQueued = 2, queued = 0, tiles = 9, quietS = 4 } end,
  ahead = function(x, y, hx, hy, dist) return { { d = 0, arrived = 1, road = true }, { d = 32, arrived = false } } end,
}

-- a point 200 m east, 100 m north of the anchor; the heading point due east of it
local lat, lon = anchor:enuToGeodetic(200, 100, 0)
local hlat, hlon = anchor:enuToGeodetic(230, 100, 0)

-- a pending anchor request: the state alone, the car untouched
st.pending = { purpose = 'rebase' }
local r = G.step({ lat = lat, lon = lon, hlat = hlat, hlon = hlon, v = 27 })
T.check(r.pending == 'rebase' and #veh.moves == 0 and #tele == 0 and #veh.vels == 0, 'pending: nothing moves')
T.check(r.roads.q == 2 and r.backlog == 0 and r.anchor == 3, 'pending: the load state still reported')
st.pending = nil

-- no ground at the point: untouched, said
ground = nil
r = G.step({ lat = lat, lon = lon, hlat = hlat, hlon = hlon, v = 27 })
T.check(r.noground and #veh.moves == 0 and #tele == 0, 'no ground: nothing moves')
T.check(#rays == 2 and math.abs(rays[1].z - 1504) < 1e-6 and math.abs(rays[2].z - 1560) < 1e-6,
  'the ray from just over the car, then from higher up (a climb)')
ground = 1490

-- the car faces north, the road runs east: set again facing it, no reset, traffic left alone
r = G.step({ lat = lat, lon = lon, hlat = hlat, hlon = hlon, v = 27 })
T.check(r.turned and #tele == 1 and #veh.moves == 0, 'a turned road: safeTeleport')
T.check(tele[1].reset == false and tele[1].removeTraffic == false, 'safeTeleport without the vehicle reset')
T.near(tele[1].pos.x, 200, 0.05, 'teleport x in the anchor frame')
T.near(tele[1].pos.y, 100, 0.05, 'teleport y')
T.near(tele[1].pos.z, 1490.5, 1e-6, 'teleport z: over the surface')
T.check(veh.vels[1].ref == 7 and veh.vels[1].scale == 0, 'the velocity replaced, by the reference node')
T.near(veh.vels[1].vx, 27, 0.05, 'the velocity along the road (east)')
T.near(veh.vels[1].vy, 0, 0.05, 'nothing across it')
T.near(r.x, 200, 0.05, 'the answer says where')
T.check(r.lead == 32, 'the road tiles ready this far ahead')

-- facing the road now: moved by its reference node, not turned
veh.fwd = vec3(1, 0.05, 0)
r = G.step({ lat = lat, lon = lon, hlat = hlat, hlon = hlon, v = 0 })
T.check(not r.turned and #tele == 1 and #veh.moves == 1, 'facing the road: moved, not teleported')
local m = veh.moves[1]
T.check(m.ref == 7 and m.q[4] == 1 and m.q[1] == 0, 'the cluster by its reference node, no rotation')
T.near(m.x, 200, 0.05, 'x plus the reference node offset 0')
T.near(m.y, 101, 0.05, 'y plus the reference node offset 1')
T.near(m.z, 1490.9, 1e-6, 'z: the surface, the lift and the node offset')
T.near(veh.vels[2].vx, 0, 1e-9, 'standing: velocity 0')

-- upset, or asked to: set again
veh.up = vec3(0, 0.9, 0.3)
G.step({ lat = lat, lon = lon, hlat = hlat, hlon = hlon, v = 27 })
T.check(#tele == 2, 'an upset car: set again')
veh.up = vec3(0, 0, 1)
G.step({ lat = lat, lon = lon, hlat = hlat, hlon = hlon, v = 27, reset = true })
T.check(#tele == 3, 'reset: set again')

-- after a rebase the same point lands in the new frame
local anchor2 = geodesy.newAnchor(40.1, -104.92)
st.anchor, st.anchorId = anchor2, 4
local ex, ey = anchor2:geodeticToEnu(lat, lon, 0)
r = G.step({ lat = lat, lon = lon, hlat = hlat, hlon = hlon, v = 27 })
T.near(r.x, ex, 0.05, 'the new anchor frame: x')
T.near(r.y, ey, 0.05, 'the new anchor frame: y')
T.check(r.anchor == 4, 'the new anchor said')

-- no lat: the state alone (perf asked: the frame times and the frame cap too)
beamngEarth_perfmon = { snapshot = function() return { frames = 30, avgMs = 16.7, p99 = 18, maxMs = 20 } end }
Engine = { getCurrentFpsLimitReason = function() return { windowFocused = false, foregroundCap = 60,
  foregroundActive = true, vsync = false } end }
local n = #veh.moves + #tele
r = G.step({ perf = true })
T.check(#veh.moves + #tele == n, 'no point: nothing moves')
T.check(r.perf.frames == 30 and r.limit.focused == false and r.limit.fg == 60, 'perf and the frame cap')

T.done('test_ghost_drive')
