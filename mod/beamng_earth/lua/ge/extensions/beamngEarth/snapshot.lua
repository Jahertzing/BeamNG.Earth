-- One-press drive snapshots (the user, 2026-10-28: "one Stream Deck button (and a keyboard shortcut) should
-- capture a screenshot plus logs and telemetry from the moment", to browse after the drive and hand to threads).
--
--   Ctrl+Alt+S   a snapshot, and the drive goes on (no window): the screenshot, where (GPS and game position,
--                heading, speed), the last FRAMES_S seconds of frame times and their hitches, the AI traffic near
--                the car (the navgraph's status; each car's speed, state and its driver's AI mode), the time of
--                day, the mod's state (report.lua's capture) and version.
--                Pressed again within NOTE_S seconds: a one-line note for that snapshot (Enter saves).
--
-- A snapshot goes to <user folder>/beamngEarth/snapshots/<date>_<time>_<n>/ (snapshot.json, screenshot.jpg) and the
-- world server is told (message 'snapshot'): server/snapshots.py copies it to the snapshots folder for good
-- (C:\beamng-earth\snapshots on the user's PC) with its own log lines and beamng.log's from that moment and the
-- build. tools/snapshots.py is the catalog: a local web page to go through them after a drive (notes, regions
-- drawn on the screenshot, issues).
local M = {}
M.dependencies = { 'ui_imgui' }

M.LEVEL_ID = 'beamng_earth'
M.DIR = 'beamngEarth/snapshots/'
M.FRAMES_S = 10          -- the frame-time strip kept in a snapshot
M.RING = 4096            -- frames in the ring (FRAMES_S at up to ~400 fps)
M.HITCH_MS = 40          -- a frame this long is a hitch in the snapshot's list
M.TRAFFIC_M = 400        -- traffic cars this near the player go in the snapshot
M.TRAFFIC_N = 16         -- at most this many (the nearest); their drivers are asked for their AI mode
M.AI_WAIT_S = 0.6        -- the drivers' answers (vehicle Lua, a frame or two) are waited for this long
M.NOTE_S = 8             -- a second press this soon after a snapshot opens its note
M.TOAST_S = 3.5
M.TOAST_DELAY_FRAMES = 3 -- the toast shows after the screenshot is taken (not in it)

local ffi = require('ffi')
local im = nil
local ourLevel = false
local ring, ringN = {}, 0
local count = 0
local pending = nil      -- the snapshot waiting for its drivers' answers: { rel, data, cars = {id = row}, untilT }
local last = nil         -- the newest snapshot: { rel, data, n, t } (the second press, the note)
local noteForm = nil     -- { buf, frames }
local toast, toastFrom, toastUntil = nil, 0, 0
local frame = 0

local wallMs = require('beamngEarth/stopwatch').clock()
local function now() return wallMs() / 1000 end

local function onLevel() ourLevel = rawget(_G, 'getCurrentLevelIdentifier') ~= nil and getCurrentLevelIdentifier() == M.LEVEL_ID end
M.onClientStartMission = onLevel
function M.onClientEndMission() ourLevel = false; pending = nil; noteForm = nil end
function M.onExtensionLoaded() if scenetree and scenetree.MissionGroup then onLevel() end end

local function try(f, ...)
  if type(f) ~= 'function' then return nil end
  local ok, v = pcall(f, ...)
  if ok then return v end
  return nil
end

-- A copy that jsonEncode always takes and Python's json reads: tables, strings, booleans and finite numbers,
-- DEPTH levels down, at most WIDTH entries a table (vec3 and other userdata as {x, y, z} when they have them).
local DEPTH, WIDTH = 6, 400
local function clean(v, depth)
  local t = type(v)
  if t == 'number' then
    if v ~= v or v == math.huge or v == -math.huge then return nil end
    return v
  elseif t == 'string' or t == 'boolean' then return v
  elseif t == 'cdata' or t == 'userdata' then
    local ok, x, y, z = pcall(function() return v.x, v.y, v.z end)
    if ok and type(x) == 'number' then return { clean(x, 0), clean(y, 0), clean(z, 0) } end
    return nil
  elseif t ~= 'table' or depth <= 0 then return nil end
  local out, n = {}, 0
  for k, x in pairs(v) do
    n = n + 1
    if n > WIDTH then break end
    local tk = type(k)
    if tk == 'string' or tk == 'number' then
      local c = clean(x, depth - 1)
      if c ~= nil then out[k] = c end
    end
  end
  return out
end
M.clean = function(v) return clean(v, DEPTH) end

-- The last `seconds` of frame times, oldest first (ms).
local function recentFrames(seconds)
  local back, sum = {}, 0
  local n = math.min(ringN, M.RING)
  for i = 0, n - 1 do
    local ms = ring[(ringN - 1 - i) % M.RING + 1]
    back[#back + 1] = ms
    sum = sum + ms
    if sum >= seconds * 1000 then break end
  end
  local out = {}
  for i = #back, 1, -1 do out[#out + 1] = back[i] end
  return out
end

-- Pure: the frame-time strip's numbers: stats (report.lua's) and the hitches, each with how long before the
-- snapshot it ended (s).
function M.frameSummary(frames, hitchMs, stats)
  local res = stats and stats(frames) or { frames = #frames }
  local hitches, ago = {}, 0
  for i = #frames, 1, -1 do
    if frames[i] >= hitchMs then hitches[#hitches + 1] = { agoS = ago / 1000, ms = frames[i] } end
    ago = ago + frames[i]
  end
  res.hitches = hitches
  res.hitchMs = hitchMs
  res.seconds = ago / 1000
  return res
end

local function heading(f) return math.deg(math.atan2(f.x, f.y)) % 360 end

-- Pure: the traffic table's rows near pos, nearest first (data: gameplay_traffic.getTrafficData()).
function M.nearTraffic(data, pos, maxM, maxN)
  local rows = {}
  if type(data) ~= 'table' or not pos then return rows end
  for id, v in pairs(data) do
    local p = type(v) == 'table' and v.pos
    if p then
      local dx, dy, dz = p.x - pos.x, p.y - pos.y, p.z - pos.z
      local d = math.sqrt(dx * dx + dy * dy + dz * dz)
      if d <= maxM then
        local dir = v.dirVec
        rows[#rows + 1] = { id = id, distM = d, speedMph = (tonumber(v.speed) or 0) * 2.23694, state = v.state,
          isAi = v.isAi, role = type(v.role) == 'table' and v.role.name or nil,
          model = type(v.model) == 'string' and v.model or (type(v.modelName) == 'string' and v.modelName or nil),
          respawns = v.respawnCount, headingDeg = dir and (dir.x ~= 0 or dir.y ~= 0) and heading(dir) or nil,
          pos = { p.x, p.y, p.z } }
      end
    end
  end
  table.sort(rows, function(a, b) return a.distM < b.distM end)
  while #rows > maxN do rows[#rows] = nil end
  return rows
end

-- What the vehicle's driver (vehicle Lua ai.lua) is doing, sent back to onAi.
local AI_LUA = 'local a = ai or {} obj:queueGameEngineLua(string.format('
  .. '"if beamngEarth_snapshot then beamngEarth_snapshot.onAi(%d, %q, %q, %s, %q, %s) end", '
  .. 'obj:getID(), tostring(a.mode), tostring(a.speedMode), tostring(tonumber(a.routeSpeed)), '
  .. 'tostring(a.driveInLaneFlag), tostring(tonumber(a.extAggression))))'

local function askDriver(id)
  local o = rawget(_G, 'getObjectByID') and getObjectByID(id)
  if o and o.queueLuaCommand then pcall(o.queueLuaCommand, o, AI_LUA) end
end

local function modStatus(name)
  local m = package.loaded['beamngEarth/' .. name]
  return m and type(m.status) == 'function' and try(m.status) or nil
end

local function timeOfDay()
  local env = rawget(_G, 'core_environment')
  local tod = env and try(env.getTimeOfDay)
  local out = { sky = modStatus('sky') }
  if type(tod) == 'table' then
    local h = ((tonumber(tod.time) or 0) + 0.5) % 1.0 * 24
    out.clock = string.format('%02d:%02d', math.floor(h), math.floor((h % 1) * 60))
    out.raw = { time = tod.time, play = tod.play, dayScale = tod.dayScale, nightScale = tod.nightScale }
  end
  return out
end

-- Everything the moment can tell (report.lua's capture, then what a drive snapshot adds).
function M.capture()
  local rep = rawget(_G, 'beamngEarth_report')
  local d = rep and type(rep.capture) == 'function' and try(rep.capture) or
    { time = os.date('%Y-%m-%d %H:%M:%S'), unix = os.time(), level = try(rawget(_G, 'getCurrentLevelIdentifier')) }
  d.kind = 'snapshot'
  d.utcOffset = os.date('%z')
  d.userDir = FS and FS.getUserPath and (FS:getUserPath()):gsub('\\', '/') or nil
  local core = rawget(_G, 'beamngEarth_core')
  d.modVersion = core and core.MOD_VERSION
  d.server = core and try(core.serverHello) or nil
  local frames = recentFrames(M.FRAMES_S)
  d.frames = M.frameSummary(frames, M.HITCH_MS, rep and rep.stats)
  d.frames.ms = frames
  local sb = package.loaded['beamngEarth/statusbar']
  d.where = sb and try(sb.location) or nil
  d.tod = timeOfDay()
  d.weather = modStatus('weather')
  d.gps = modStatus('gps')
  d.drive = modStatus('thedrive')
  d.aibrain = modStatus('aibrain')
  local veh = rawget(_G, 'getPlayerVehicle') and getPlayerVehicle(0)
  local ppos = veh and veh:getPosition()
  if veh then
    d.player = { id = veh:getID(), ai = nil }
    if d.vehicle then d.vehicle.id = veh:getID() end
  end
  local T = rawget(_G, 'gameplay_traffic')
  d.traffic = { navgraph = modStatus('navgraph'), amount = T and try(T.getTrafficAmount) or nil,
    cars = M.nearTraffic(T and try(T.getTrafficData), ppos, M.TRAFFIC_M, M.TRAFFIC_N) }
  return d
end

local function encode(t)
  local c = clean(t, DEPTH)
  local ok, s = pcall(jsonEncodePretty or jsonEncode, c)
  if ok and s then return s end
  return jsonEncode(c)
end

local function write(rel, data)
  pcall(writeFile, rel .. 'snapshot.json', encode(data))
end

-- The world server copies the folder to the snapshots folder for good, with its log lines (server/snapshots.py).
local function tellServer(rel, data)
  local net = package.loaded['beamngEarth/net']
  if not (net and net.isConnected and net.isConnected()) then return false end
  return net.send({ type = 'snapshot', folder = data.folder, id = data.id, unix = data.unix, userDir = data.userDir }) and true or false
end

local function finish(p)
  p.data.aiAnswered = 0
  for _, row in ipairs(p.data.traffic and p.data.traffic.cars or {}) do
    local a = p.answers[row.id]
    if a then row.ai = a; p.data.aiAnswered = p.data.aiAnswered + 1 end
  end
  if p.data.player then p.data.player.ai = p.answers[p.data.player.id] end
  write(p.rel, p.data)
  p.data.serverTold = tellServer(p.rel, p.data)    -- (it copies the folder some seconds later: this write is in)
  write(p.rel, p.data)
end

local function show(text)
  toast, toastFrom, toastUntil = text, frame + M.TOAST_DELAY_FRAMES, now() + M.TOAST_S
end

-- Ctrl+Alt+S (the Stream Deck's Snapshot key): a snapshot; again within NOTE_S: its note.
function M.take()
  if not ourLevel then return end
  if noteForm then return end
  if last and now() - last.t < M.NOTE_S and not pending then
    noteForm = { buf = im and im.ArrayChar(512, last.data.note or '') or nil, frames = 0 }
    return
  end
  if pending then finish(pending); pending = nil end
  count = count + 1
  local id = os.date('%Y%m%d_%H%M%S') .. '_' .. count
  local rel = M.DIR .. id .. '/'
  if not FS:directoryExists(rel) then FS:directoryCreate(rel, true) end
  local ok, data = pcall(M.capture)
  if not ok then data = { time = os.date('%Y-%m-%d %H:%M:%S'), unix = os.time(), kind = 'snapshot', error = tostring(data) } end
  data.id = id
  data.n = count
  data.folder = (FS:getUserPath() .. rel):gsub('\\', '/')
  if type(createScreenshot2) == 'function' and
      pcall(createScreenshot2, { filename = rel .. 'screenshot', writeJPG = true, superSampling = 1 }) then
    data.screenshot = 'screenshot.jpg'
  end
  if not data.traffic then data.traffic = { cars = {} } end
  write(rel, data)          -- at once: nothing is lost if the game stops now
  pending = { rel = rel, data = data, answers = {}, untilT = now() + M.AI_WAIT_S, asked = 0 }
  if data.player then askDriver(data.player.id); pending.asked = pending.asked + 1 end
  for _, row in ipairs(data.traffic.cars or {}) do askDriver(row.id); pending.asked = pending.asked + 1 end
  last = { rel = rel, data = data, n = count, t = now() }
  log('I', 'beamngEarth', 'snapshot ' .. id .. ': ' .. data.folder)
  show(string.format('Snapshot %d saved  |  press again for a note', count))
end

-- A driver's answer (AI_LUA).
function M.onAi(id, mode, speedMode, routeSpeed, inLane, aggression)
  if not pending then return end
  pending.answers[id] = { mode = mode, speedMode = speedMode ~= 'nil' and speedMode or nil, routeSpeed = routeSpeed,
    driveInLane = inLane, aggression = aggression }
  local n = 0
  for _ in pairs(pending.answers) do n = n + 1 end
  if n >= pending.asked then finish(pending); pending = nil end
end

-- The note for the newest snapshot (the window's Save; the console and tests).
function M.saveNote(text)
  if not last then return false end
  last.data.note = tostring(text or '')
  write(last.rel, last.data)
  tellServer(last.rel, last.data)
  return true
end

function M.status()
  return { ourLevel = ourLevel, taken = count, pending = pending ~= nil, noteOpen = noteForm ~= nil,
    last = last and last.data.folder or nil }
end

function M.onPreRender(dtReal)
  if not ourLevel or not dtReal or dtReal <= 0 then return end
  ringN = ringN + 1
  ring[(ringN - 1) % M.RING + 1] = dtReal * 1000
end

local function drawNote()
  local f = noteForm
  if not f.buf then f.buf = im.ArrayChar(512, last.data.note or '') end
  f.frames = f.frames + 1
  im.SetNextWindowPos(im.ImVec2(20, 200), im.Cond_FirstUseEver)
  im.SetNextWindowSize(im.ImVec2(460, 0), im.Cond_FirstUseEver)
  if im.Begin('Snapshot note##bngeSnapNote', nil, im.WindowFlags_AlwaysAutoResize) then
    im.TextUnformatted(string.format('Snapshot %d  |  %s', last.n, last.data.time or ''))
    if f.frames == 2 then im.SetKeyboardFocusHere() end
    im.PushItemWidth(420)
    local enter = im.InputText('##bngeSnapNoteText', f.buf, im.GetLengthArrayCharPtr(f.buf),
      im.InputTextFlags_EnterReturnsTrue or 0)
    im.PopItemWidth()
    if enter or im.Button('Save note') then
      M.saveNote(ffi.string(f.buf))
      noteForm = nil
      show(string.format('Snapshot %d: note saved', last.n))
    end
    if noteForm then
      im.SameLine()
      if im.Button('Skip') then noteForm = nil end
    end
  end
  im.End()
end

function M.onUpdate()
  frame = frame + 1
  if pending and now() >= pending.untilT then finish(pending); pending = nil end
  im = im or rawget(_G, 'ui_imgui')
  if not im then return end
  if toast and frame >= toastFrom and now() < toastUntil then
    im.SetNextWindowPos(im.ImVec2(20, 200), im.Cond_Always)
    if im.Begin('##bngeSnapToast', nil, im.WindowFlags_NoDecoration + im.WindowFlags_AlwaysAutoResize
        + im.WindowFlags_NoInputs) then
      im.TextUnformatted(toast)
    end
    im.End()
  end
  if noteForm and last then drawNote() end
end

return M
