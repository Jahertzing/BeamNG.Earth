-- luajit mod/tests/test_snapshot.lua  (snapshot.lua: the drive snapshot's capture, its files and the drivers' answers)
-- Engine globals are stubbed here; which ones the real game has is checked in-game.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')

local files, dirs, logs = {}, {}, {}
local clockMs = 0
function hptimer()
  local last = clockMs
  return { stopAndReset = function() local d = clockMs - last; last = clockMs; return d end }
end
function log(_, _, msg) logs[#logs + 1] = msg end
function getCurrentLevelIdentifier() return 'beamng_earth' end
function writeFile(p, s) files[p] = s; return true end
function readFile(p) return files[p] end
local function enc(v)
  local t = type(v)
  if t == 'table' then
    local parts = {}
    for k, x in pairs(v) do parts[#parts + 1] = string.format('%q:%s', tostring(k), enc(x)) end
    table.sort(parts)
    return '{' .. table.concat(parts, ',') .. '}'
  elseif t == 'string' then return string.format('%q', v) end
  return tostring(v)
end
jsonEncode = enc
FS = { directoryExists = function(_, p) return dirs[p] end, directoryCreate = function(_, p) dirs[p] = true end,
  getUserPath = function() return 'C:\\u\\' end }
local shots = {}
function createScreenshot2(o) shots[#shots + 1] = o.filename end

local function vec(x, y, z) return { x = x, y = y, z = z, length = function(s) return math.sqrt(s.x ^ 2 + s.y ^ 2 + s.z ^ 2) end } end
local player = { getPosition = function() return vec(0, 0, 0) end, getVelocity = function() return vec(0, 20, 0) end,
  getDirectionVector = function() return vec(0, 1, 0) end, getID = function() return 1 end,
  getJBeamFilename = function() return 'etk800' end }
function getPlayerVehicle() return player end
local asked = {}
function getObjectByID(id) return { queueLuaCommand = function(_, cmd) asked[#asked + 1] = { id, cmd } end } end
gameplay_traffic = {
  getTrafficAmount = function() return 3 end,
  getTrafficData = function() return {
    [7] = { pos = vec(30, 40, 0), speed = 10, state = 'active', isAi = true, role = { name = 'standard' },
      dirVec = vec(1, 0, 0), model = 'covet', respawnCount = 2 },
    [8] = { pos = vec(900, 0, 0), speed = 5, state = 'active' },
    [9] = { pos = vec(3, 4, 0), speed = 0, state = 'fadeIn', dirVec = vec(0, -1, 0), model = { odd = true } },
  } end }
package.loaded['beamngEarth/navgraph'] = { status = function() return { nodes = 12, traffic = { state = 'on' } } end }
package.loaded['beamngEarth/statusbar'] = { location = function() return { street = 'Main St', place = 'Frederick' } end }
package.loaded['beamngEarth/sky'] = { status = function() return { localTime = '17:45', mode = 'real' } end }

local snap = require('beamngEarth/snapshot')

-- 1) pure parts
local fs = snap.frameSummary({ 10, 50, 10, 45, 10 }, 40, nil)
T.check(#fs.hitches == 2 and fs.hitches[1].ms == 45 and math.abs(fs.hitches[1].agoS - 0.010) < 1e-9
  and fs.hitches[2].ms == 50 and math.abs(fs.hitches[2].agoS - 0.065) < 1e-9, 'hitches, newest first, with how long ago')
T.check(math.abs(fs.seconds - 0.125) < 1e-9, 'the strip spans its frames')
local rows = snap.nearTraffic(gameplay_traffic.getTrafficData(), vec(0, 0, 0), 400, 16)
T.check(#rows == 2 and rows[1].id == 9 and rows[2].id == 7, 'traffic within 400 m, nearest first')
T.check(math.abs(rows[2].distM - 50) < 1e-9 and math.abs(rows[2].speedMph - 22.3694) < 1e-6 and rows[2].role == 'standard'
  and math.abs(rows[2].headingDeg - 90) < 1e-9 and rows[2].model == 'covet', 'a row: distance, speed, role, heading')
T.check(rows[1].model == nil and math.abs(rows[1].headingDeg - 180) < 1e-9, 'a model that is not a string is left out')
T.check(#snap.nearTraffic(gameplay_traffic.getTrafficData(), vec(0, 0, 0), 400, 1) == 1, 'at most N cars')
local c = snap.clean({ a = 0 / 0, b = math.huge, c = 'x', d = { 1, 2 }, e = function() end, f = vec(1, 2, 3) })
T.check(c.a == nil and c.b == nil and c.c == 'x' and c.d[2] == 2 and c.e == nil, 'clean: no NaN, inf or functions')
T.check(type(c.f) == 'table' and c.f.x == 1, 'clean: a plain table stays a table')

-- 2) other levels: the key does nothing
getCurrentLevelIdentifier = function() return 'gridmap_v2' end
snap.onClientStartMission()
snap.take()
T.check(snap.status().taken == 0, 'Ctrl+Alt+S does nothing on another level')
getCurrentLevelIdentifier = function() return 'beamng_earth' end
snap.onClientStartMission()

-- 3) a snapshot: the frames, the files, the drivers asked
for _ = 1, 50 do clockMs = clockMs + 16; snap.onPreRender(0.016) end
snap.onPreRender(0.120)
snap.take()
local st = snap.status()
T.check(st.taken == 1 and st.pending, 'one snapshot, waiting for its drivers')
local folder = st.last
T.check(folder and folder:match('^C:/u/beamngEarth/snapshots/%d+_%d+_1/$'), 'the folder, forward slashes: ' .. tostring(folder))
local rel = folder:gsub('^C:/u/', '')
T.check(files[rel .. 'snapshot.json'] ~= nil, 'snapshot.json written at once')
T.check(shots[1] == rel .. 'screenshot', 'the screenshot asked for')
T.check(#asked == 3 and asked[1][1] == 1, 'the player and the two near cars asked for their AI mode')
local first = files[rel .. 'snapshot.json']
T.check(first:find('"kind":"snapshot"', 1, true) and first:find('"street":"Main St"', 1, true)
  and first:find('"localTime":"17:45"', 1, true) and first:find('"nodes":12', 1, true), 'where, time of day, navgraph')
T.check(first:find('"ms":120', 1, true) ~= nil, 'the hitch is in the strip')

-- the drivers' answer chunk is valid Lua that calls onAi
local cmd = asked[2][2]
T.check(cmd:find('beamngEarth_snapshot.onAi', 1, true) ~= nil, 'the answer goes to onAi')
local fn = loadstring(cmd)
T.check(fn ~= nil, 'the vehicle command compiles')
local sent
ai = { mode = 'traffic', speedMode = 'legal', routeSpeed = nil, driveInLaneFlag = 'on', extAggression = 0.3 }
obj = { getID = function() return 9 end, queueGameEngineLua = function(_, s) sent = s end }
pcall(fn)
T.check(sent ~= nil and loadstring(sent) ~= nil, 'its answer compiles: ' .. tostring(sent))
beamngEarth_snapshot = snap
loadstring(sent)()
snap.onAi(1, 'disabled', 'nil', nil, 'off', 0.3)
T.check(snap.status().pending, 'still waiting for car 7')
snap.onAi(7, 'traffic', 'legal', 25, 'on', 0.3)
T.check(not snap.status().pending, 'every driver answered: written')
T.check(files[rel .. 'snapshot.json']:find('"serverTold":false', 1, true) ~= nil, 'no server: not told')

-- the world server told (it copies the folder for good): once the drivers answered, and again for a note
local sentMsgs = {}
package.loaded['beamngEarth/net'] = { isConnected = function() return true end,
  send = function(m) sentMsgs[#sentMsgs + 1] = m; return true end }
local done = files[rel .. 'snapshot.json']
T.check(done:find('"mode":"traffic"', 1, true) and done:find('"aiAnswered":2', 1, true), 'the AI modes in the file')
T.check(done:find('"mode":"disabled"', 1, true) ~= nil, 'the player car\'s AI mode')

-- 4) a second press soon after: the note, not another snapshot
snap.take()
T.check(snap.status().taken == 1 and snap.status().noteOpen, 'the second press opens the note')
T.check(snap.saveNote('weird merge here'), 'the note saved')
T.check(files[rel .. 'snapshot.json']:find('"note":"weird merge here"', 1, true) ~= nil, 'the note in the file')
T.check(#sentMsgs == 1 and sentMsgs[1].type == 'snapshot' and sentMsgs[1].folder == folder and sentMsgs[1].userDir == 'C:/u/',
  'the note: the server told again, with the folder')

-- 5) no answers: written after AI_WAIT_S anyway
clockMs = clockMs + 20000
package.loaded['beamngEarth/navgraph'] = nil
local before = #asked
snap.take()   -- (the note window is still open in this test: no imgui to close it)
T.check(snap.status().taken == 1, 'not while the note is open')
snap.onClientEndMission()
snap.onClientStartMission()
snap.take()
T.check(snap.status().taken == 2 and #asked > before, 'the next snapshot')
clockMs = clockMs + 1000
snap.onUpdate()
T.check(not snap.status().pending, 'no answers: written after the wait')

T.done('test_snapshot')
