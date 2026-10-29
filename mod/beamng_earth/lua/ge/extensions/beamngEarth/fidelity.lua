-- BeamNG.Earth render fidelity (the renderFidelity setting): the lighting levers BeamNG 0.40 has but tunes for its
-- own small, enclosed maps, set for ours while our level runs, plus a post effect of our own.
--
--   ao      the game's ambient occlusion (XeGTAO, SSAOPostFx) at a city's scale: a 1.5 m radius darkens the inside of a
--           window frame but not a street between buildings, the foot of a wall or the ground under a tree; a wider
--           radius, a firmer contrast, 64 samples, and specular occlusion (its bent normals keep reflections out of the
--           corners they can't reach: the bright rims along kerbs and under eaves).
--   shafts  sun shafts (shaders/beamngEarth/sunshafts*.cs.hlsl, three compute passes on the HDR image before tone
--           mapping, where the game's depth of field runs): the sky around a low sun blurred towards it, so the light
--           breaks round buildings and through trees at golden hour.
--   probes  reflections of the place itself: cubemaps captured along the drive ahead of time, the nearest one the
--           level's environment map (see "reflections" below).
--   wet     wet roads while it rains.
--
-- The player's graphics settings are never written: the objects' runtime values are, and leaving the level puts the
-- player's values back (the ones read before the first change; the AO samples and specular occlusion from settings).
local M = {}

M.MODES = { 'off', 'on' }
M.CHECK_S = 2           -- re-assert this often (a graphics-settings change rebuilds post effects)

-- AO in our level. Stock (ssao.lua): radius 1.5 m, contrast 2, samples 16 (Normal) / 64 (High).
-- (slot F 2026-10-29: 3.5 m / 2.3 barely showed on a facade in sun; 6 m / 2.6 deepened the shop fronts and the foot of
-- the walls with no halos and no cost at 85 fps)
M.AO = { radius = 6, contrast = 2.6, samples = 64, bentNormals = true }
M.AO_STOCK = { radius = 1.5, contrast = 2 }

-- Sun shafts: intensity, decay per tap, length (share of the way to the sun), falloff power; sky cap, taps per run;
-- and the second (gap-filling) run's length and taps.
-- (slot F: power 6 counted sky 30 degrees from the sun nearly in full, a grey veil over every tree near the camera;
-- 40 keeps the light to the sky round the sun, so the shafts break through the branches in front of it)
M.SHAFTS = { intensity = 0.5, decay = 0.965, length = 0.9, power = 40, cap = 6, taps = 40,
  fillLength = 0.12, fillTaps = 12 }
-- the effect's strength from the sun's elevation (degrees): gone below the horizon, full from 1 to 30, then fading to
-- FULL_HIGH (shafts from a high sun are rarely on screen and look odd when they are)
M.SUN_FADE = { lo = -1, full = 1, high = 30, top = 60, fullHigh = 0.35 }
M.SUN_AZ_SIGN = 1       -- the world's east-west sense of the sky's azimuth (core_celestial's: north +Y, east +X)

M.PFX = 'BeamNGEarthShaftsMaskPfx'
M.SHADERS = {
  mask = 'shaders/beamngEarth/sunshaftsMask.cs.hlsl',
  blur = 'shaders/beamngEarth/sunshaftsBlur.cs.hlsl',
  apply = 'shaders/beamngEarth/sunshaftsApply.cs.hlsl',
}

local mode = 'on'
-- probes: the reflection probes captured along the drive; reflect: live captures when stopped (a ~1 s freeze each)
local features = { ao = true, shafts = true, wet = true, probes = true, reflect = false }
local debug = false
local lastCheck = -math.huge
local aoSaved = nil       -- the player's AO values before ours (nil: not applied)
local shaftsOn = false

-- Pure: the unit direction to a sun at azimuth `az` (degrees from north, clockwise) and elevation `el` (degrees over
-- the horizon), in the level's frame (+X east, +Y north, +Z up).
function M.sunVector(az, el, azSign)
  local a, e = math.rad(az or 0), math.rad(el or 0)
  local s = azSign or M.SUN_AZ_SIGN
  return { s * math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e) }
end

-- Pure: the shafts' strength (0..1) for a sun `el` degrees over the horizon.
function M.sunStrength(el)
  local f = M.SUN_FADE
  el = tonumber(el)
  if not el or el <= f.lo then return 0 end
  if el < f.full then return (el - f.lo) / (f.full - f.lo) end
  if el <= f.high then return 1 end
  if el >= f.top then return f.fullHigh end
  return 1 + (f.fullHigh - 1) * (el - f.high) / (f.top - f.high)
end

-- Pure: the shader constants for a sun (azimuth, elevation; degrees) and the SHAFTS table `p` (the fill run's when
-- `fill`), as setShaderConst strings.
function M.shaftConsts(az, el, p, fill, dbg)
  p = p or M.SHAFTS
  local v = M.sunVector(az, el)
  local fmt = function(a, b, c, d) return string.format('%.6g %.6g %.6g %.6g', a, b, c, d) end
  return {
    ['$bneSunDir'] = fmt(v[1], v[2], v[3], M.sunStrength(el)),
    ['$bneShaft'] = fmt(p.intensity, fill and 1 or p.decay, fill and p.fillLength or p.length, p.power),
    ['$bneShaft2'] = fmt(p.cap, fill and p.fillTaps or p.taps, dbg and 1 or 0, 0),
  }
end

-- The drawn sun (the level's ScatterSky, as core_celestial moves it): azimuth, elevation in degrees, or nil.
local function sun()
  if not scenetree or not scenetree.findClassObjects then return nil end
  local names = scenetree.findClassObjects('ScatterSky')
  local o = names and names[1] and scenetree.findObject(names[1])
  if not o then return nil end
  local az, el = tonumber(o.azimuth), tonumber(o.elevation)
  if not az or not el then return nil end
  return az, el
end

------------------------------------------------------------------------------------------
-- ambient occlusion

local function ssaoObject()
  return scenetree and scenetree.findObject and scenetree.findObject('SSAOPostFx') or nil
end

local function stockSsao()
  local ok, m = pcall(require, 'client/postFx/ssao')
  return ok and type(m) == 'table' and m or nil
end

local function settingValue(k)
  if settings and settings.getValue then return settings.getValue(k) end
  return nil
end

local function applyAo()
  local o = ssaoObject()
  if not o then return end
  local okE, on = pcall(function() return o:isEnabled() end)
  if okE and on == false then return end      -- the player turned AO off: theirs
  if not aoSaved then
    local m = stockSsao()
    aoSaved = { radius = m and m.getRadius and m.getRadius() or M.AO_STOCK.radius,
      contrast = m and m.getContrast and m.getContrast() or M.AO_STOCK.contrast }
  end
  -- the stock module's setters keep their getters in step (screenshot tools read them back)
  local m = stockSsao()
  if m and m.setRadius then m.setRadius(M.AO.radius) else o:setRadius(M.AO.radius) end
  if m and m.setContrast then m.setContrast(M.AO.contrast) else o:setContrast(M.AO.contrast) end
  if o.setSamples then o:setSamples(M.AO.samples) end
  o.bentNormals = M.AO.bentNormals
end

local function restoreAo()
  if not aoSaved then return end
  local saved = aoSaved
  aoSaved = nil
  local o = ssaoObject()
  if not o then return end
  local m = stockSsao()
  if m and m.setRadius then m.setRadius(saved.radius) else o:setRadius(saved.radius) end
  if m and m.setContrast then m.setContrast(saved.contrast) else o:setContrast(saved.contrast) end
  if o.setSamples then o:setSamples(settingValue('PostFXSSAOGeneralQuality') == 'High' and 64 or 16) end
  o.bentNormals = settingValue('PostFXSpecularOcclusionEnabled') == true
end

------------------------------------------------------------------------------------------
-- sun shafts

local function shader(name, file)
  if scenetree.findObject(name) then return end
  local sh = createObject('ShaderData')
  sh.DXComputShaderName = file
  sh.pixVersion = 5.0
  sh:registerObject(name)
end

local function stateBlock()
  local name = 'BeamNGEarthShaftsStateBlock'
  if scenetree.findObject(name) then return name end
  local sb = createObject('GFXStateBlockData')
  sb.zDefined = true
  sb.zEnable = false
  sb.zWriteEnable = false
  sb.samplersDefined = true
  sb:setField('samplerStates', 0, 'SamplerClampLinear')
  sb:setField('samplerStates', 1, 'SamplerClampLinear')
  sb:setField('samplerStates', 2, 'SamplerClampPoint')
  sb:registerObject(name)
  return name
end

-- The chain (dof.lua's pattern): the mask at a quarter of the resolution, the two blur runs on it, the apply pass on
-- the scene. Created once; the children follow the parent's enable.
local function createShafts()
  local pfx = scenetree.findObject(M.PFX)
  if pfx then return pfx end
  shader('BeamNGEarthShaftsMaskShader', M.SHADERS.mask)
  shader('BeamNGEarthShaftsBlurShader', M.SHADERS.blur)
  shader('BeamNGEarthShaftsApplyShader', M.SHADERS.apply)
  local sb = stateBlock()
  local function effect(name, shaderName, textures, target, fmt)
    local e = createObject('PostEffect')
    e.allowReflectPass = false
    e:setField('shader', 0, shaderName)
    e:setField('stateBlock', 0, sb)
    for i, t in ipairs(textures) do e:setField('texture', i - 1, t) end
    e:setField('target', 0, target)
    if fmt then e:setField('targetFormat', 0, fmt) end
    return e
  end
  local HDR = 'GFXFormatR16G16B16A16F'
  pfx = effect(M.PFX, 'BeamNGEarthShaftsMaskShader', { '$backBuffer', '#prepass[RT0]', '#prepass[Depth]' },
    '#bneShaftMask', HDR)
  pfx:setField('isEnabled', 0, '0')
  pfx:setField('renderTime', 0, 'PFXAfterBin')
  pfx:setField('renderBin', 0, 'UpscaleDispatch')    -- HDR, before tone mapping (dof.lua's place)
  pfx.renderPriority = 0.05
  pfx:setField('targetScale', 0, '0.25 0.25')
  pfx:registerObject(M.PFX)
  local blur = effect('BeamNGEarthShaftsBlurPfx', 'BeamNGEarthShaftsBlurShader', { '#bneShaftMask' }, '$outTex', HDR)
  blur:registerObject('BeamNGEarthShaftsBlurPfx')
  pfx:add(blur)
  local fill = effect('BeamNGEarthShaftsFillPfx', 'BeamNGEarthShaftsBlurShader', { '$inTex' }, '#bneShaftBlur', HDR)
  fill:registerObject('BeamNGEarthShaftsFillPfx')
  pfx:add(fill)
  local apply = effect('BeamNGEarthShaftsApplyPfx', 'BeamNGEarthShaftsApplyShader', { '$backBuffer', '#bneShaftBlur' },
    '$backBuffer')
  apply:registerObject('BeamNGEarthShaftsApplyPfx')
  pfx:add(apply)
  return pfx
end

local function enabledOf(pfx)
  local ok, v = pcall(function() return pfx:isEnabled() end)
  return ok and v and true or false
end

local function setShaftConsts(az, el)
  local main = M.shaftConsts(az, el, M.SHAFTS, false, debug)
  local fillC = M.shaftConsts(az, el, M.SHAFTS, true, debug)
  for _, n in ipairs({ M.PFX, 'BeamNGEarthShaftsBlurPfx', 'BeamNGEarthShaftsApplyPfx' }) do
    local o = scenetree.findObject(n)
    if o then for k, v in pairs(main) do o:setShaderConst(k, v) end end
  end
  local f = scenetree.findObject('BeamNGEarthShaftsFillPfx')
  if f then for k, v in pairs(fillC) do f:setShaderConst(k, v) end end
end

local function updateShafts()
  local az, el = sun()
  if not az then return end
  local pfx = createShafts()
  setShaftConsts(az, el)
  local want = M.sunStrength(el) > 0
  if want and not enabledOf(pfx) then pfx:enable() elseif not want and enabledOf(pfx) then pfx:disable() end
  shaftsOn = want
end

local function stopShafts()
  shaftsOn = false
  local pfx = scenetree and scenetree.findObject and scenetree.findObject(M.PFX)
  if pfx and enabledOf(pfx) then pfx:disable() end
end

------------------------------------------------------------------------------------------
-- reflections: probes captured ahead of time (feature 'probes'), and a live capture (by hand, or feature 'reflect')
--
-- The level's reflections come from one stock cubemap (LevelInfo globalEnviromentMap BNG_Sky_01_cubemap: a generic
-- sky and hills), so glass, car paint and wet roads mirror a place that isn't there. The game can capture a cubemap
-- from the camera (captureCameraCubemap, the editor's cubemap tool); made the level's environment map (normalised:
-- the sky's live ambient scales it, so it follows the time of day), the city reflects the city.
--
-- A capture stops the game for 0.8-1.3 s (slot F 2026-10-29: RenderReflections ~940 ms, the six faces' import ~360 ms),
-- far too long while driving. So the probes: captured once at places along the drive (M.captureProbe() from the look
-- probe, tools/fidelity_probes.json), kept in PROBES.dir with an index, all loaded as the level starts, and the
-- nearest one within PROBES.rangeM made the environment map as the player moves (a switch costs a field write).
-- Further from every probe than that: the level's own map. The live capture (two file sets in turn, so a capture
-- never overwrites the faces in use) stays for the look probe's A/B and as an option when stopped.

M.REFLECT = { name = 'BeamNGEarthLiveReflection', dir = '/temp/beamngEarth/reflect/', res = 256, waitS = 5,
  moveM = 600, sunDeg = 8, minGapS = 20, maxSpeed = 3 }
-- probes: where (the user folder, not temp: they outlive a session), the index, face size, the reach of one, how much
-- nearer another must be to take over (no flicker between two at the same distance), how many at most; a capture's
-- camera snapped onto the nearest road within snapM, eyeM over it
M.PROBES = { dir = '/beamngEarth/reflect/', index = '/beamngEarth/reflect/probes.json', name = 'BeamNGEarthProbe',
  res = 256, rangeM = 1500, switch = 0.75, max = 48, snapM = 60, eyeM = 2.5 }
-- core_camera names (prefixes) with the camera in the car: the driver's seat, onboard (hood, rider), occupants
M.INSIDE_CAMS = { 'driver', 'onboard', 'occupant', 'passenger' }

-- Pure: is a core_camera name one inside the car (unknown: yes)?
function M.insideCam(name)
  if type(name) ~= 'string' then return true end
  for _, p in ipairs(M.INSIDE_CAMS) do
    if name:sub(1, #p) == p then return true end
  end
  return false
end

-- Pure: a probe's id from where it was captured ("39.7530_m105.0000": a file name).
function M.probeId(lat, lon)
  local s = string.format('%.4f_%.4f', tonumber(lat) or 0, tonumber(lon) or 0):gsub('-', 'm')
  return s
end

-- Pure: metres between two points (equirectangular: a few km apart at most matter here).
function M.geoDist(lat1, lon1, lat2, lon2)
  local k = 111320
  local dy = (lat2 - lat1) * k
  local dx = (lon2 - lon1) * k * math.cos(math.rad((lat1 + lat2) * 0.5))
  return math.sqrt(dx * dx + dy * dy)
end

-- Pure: the probe to show at (lat, lon): the nearest within p.rangeM, but the current one kept until another is
-- p.switch times its distance or it is out of range; nil = none in range. `list` entries have lat, lon.
function M.pickProbe(list, lat, lon, current, p)
  p = p or M.PROBES
  if not list or not lat or not lon then return nil end
  local best, bestD, curD = nil, math.huge, nil
  for _, e in ipairs(list) do
    local d = M.geoDist(lat, lon, e.lat, e.lon)
    if d < bestD then best, bestD = e, d end
    if e == current then curD = d end
  end
  if not best or bestD > p.rangeM then return nil end
  if current and curD and curD <= p.rangeM and best ~= current and bestD > curD * p.switch then return current end
  return best
end

-- Pure: the point of segment a-b nearest to q (tables or vec3s with x, y, z).
function M.nearestOnSegment(q, a, b)
  local abx, aby, abz = b.x - a.x, b.y - a.y, b.z - a.z
  local l2 = abx * abx + aby * aby + abz * abz
  local t = 0
  if l2 > 1e-9 then
    t = ((q.x - a.x) * abx + (q.y - a.y) * aby + (q.z - a.z) * abz) / l2
    t = math.min(math.max(t, 0), 1)
  end
  return { x = a.x + abx * t, y = a.y + aby * t, z = a.z + abz * t }
end

local reflect = { slot = 'b', pending = nil, sync = nil, last = nil }
local env = { saved = nil, name = nil, live = false }   -- the level's own map (nil: ours not set), ours, a live one
local probes = { list = nil, current = nil }

local function levelInfo()
  if not scenetree or not scenetree.findClassObjects then return nil end
  local names = scenetree.findClassObjects('LevelInfo')
  return names and names[1] and scenetree.findObject(names[1]) or nil
end

local function facesReady(prefix)
  if not FS or not FS.fileExists then return true end
  for i = 0, 5 do
    if not FS:fileExists(prefix .. i .. '.hdr.dds') then return false end
  end
  return true
end

-- A CubemapData of the six faces at `prefix`, created once by name, its faces reloaded after.
local function cubemap(name, prefix)
  local cm = scenetree.findObject(name)
  if not cm then
    cm = createObject('CubemapData')
    cm:setField('normalize', 0, '1')
    for i = 0, 5 do cm:setField('cubeFace', i, prefix .. i .. '.hdr.dds') end
    cm:registerObject(name)
  else
    for i = 0, 5 do cm:setField('cubeFace', i, prefix .. i .. '.hdr.dds') end
  end
  if cm.updateFaces then pcall(cm.updateFaces, cm) end
  return cm
end

-- The level's environment map: the CubemapData `name`, or (nil) the level's own back.
local function setEnv(name)
  local li = levelInfo()
  if not li then return false end
  if name then
    if not env.saved then env.saved = li:getField('globalEnviromentMap', 0) end
    li:setField('globalEnviromentMap', 0, name)
  else
    if not env.saved then return false end
    li:setField('globalEnviromentMap', 0, env.saved)
    env.saved = nil
  end
  if li.postApply then pcall(li.postApply, li) end
  env.name = name
  return true
end

local function assignReflection(prefix)
  cubemap(M.REFLECT.name, prefix)
  if not setEnv(M.REFLECT.name) then return false end
  env.live = true
  probes.current = nil
  log('I', 'beamngEarth', 'fidelity: live reflection from ' .. prefix)
  return true
end

-- Pure: should the automatic capture run now? `s` = { dist = m from the last capture (nil: none yet), sunDelta = degrees
-- the sun moved since, since = s since the last one, speed = m/s, inside = the camera is in the car, sync = the
-- capture writes its faces inside the call (nil: not known yet) }.
function M.wantCapture(s, r)
  r = r or M.REFLECT
  if s.since and s.since < r.minGapS then return false end
  if s.inside and s.sync ~= true then return false end     -- the cabin would be in the cubemap
  if (s.speed or 0) > r.maxSpeed then return false end      -- the capture's hitch: only when (nearly) stopped
  if s.dist == nil then return true end
  return s.dist >= r.moveM or (s.sunDelta or 0) >= r.sunDeg
end

local function removeFaces(prefix)
  if not FS or not FS.removeFile or not FS.fileExists then return end
  for i = 0, 5 do
    local f = prefix .. i .. '.hdr.dds'
    if FS:fileExists(f) then FS:removeFile(f) end
  end
end

-- Capture at the camera to `prefix`, the player's car hidden for the call when `hideCar`; returns ok, err, ms.
local function capture(prefix, res, hideCar)
  if type(captureCameraCubemap) ~= 'function' then return false, 'no captureCameraCubemap in this build' end
  removeFaces(prefix)
  local veh = hideCar and getPlayerVehicle and getPlayerVehicle(0) or nil
  if veh and veh.setMeshAlpha then veh:setMeshAlpha(0, '') end
  local t0 = os.clock()
  local ok, err = pcall(captureCameraCubemap, prefix, res)
  local ms = (os.clock() - t0) * 1000
  if veh and veh.setMeshAlpha then veh:setMeshAlpha(1, '') end
  if not ok then return false, tostring(err), ms end
  return true, nil, ms
end

-- Capture a cubemap at the camera now; it becomes the level's environment map when its faces are on disk. With
-- `hideCar`, the player's car is hidden for the call (its cabin when the camera is inside it).
function M.captureReflection(res, hideCar)
  if type(captureCameraCubemap) ~= 'function' then return false, 'no captureCameraCubemap in this build' end
  reflect.slot = reflect.slot == 'a' and 'b' or 'a'
  local prefix = M.REFLECT.dir .. reflect.slot .. '_skybox'
  if FS and FS.directoryCreate then FS:directoryCreate(M.REFLECT.dir, true) end
  local ok, err, ms = capture(prefix, tonumber(res) or M.REFLECT.res, hideCar)
  if not ok then return false, err end
  -- whether the faces are written inside the call (then a capture can hide the player's car around it unseen)
  reflect.sync = facesReady(prefix)
  log('I', 'beamngEarth', string.format('fidelity: captureCameraCubemap %s in %.1f ms, faces %s', prefix, ms,
    reflect.sync and 'written in the call' or 'not yet written'))
  reflect.pending = { prefix = prefix, t = os.clock() }
  return true, prefix
end

local function cameraInside()
  if not core_camera or not core_camera.getActiveCamName then return true end
  local ok, name = pcall(core_camera.getActiveCamName, 0)
  if not ok then return true end
  return M.insideCam(name)
end

-- The automatic capture (feature 'reflect'): where the player is, again after moving REFLECT.moveM or the sun
-- moving REFLECT.sunDeg, only while (nearly) stopped.
local function autoReflection(sunEl)
  if reflect.pending then return end
  local veh = getPlayerVehicle and getPlayerVehicle(0)
  if not veh then return end
  local okP, pos = pcall(function() return veh:getPosition() end)
  local okV, vel = pcall(function() return veh:getVelocity() end)
  if not okP or not pos then return end
  local speed = okV and vel and math.sqrt(vel.x * vel.x + vel.y * vel.y + vel.z * vel.z) or 0
  local last = reflect.last
  local inside = cameraInside()
  local st = { speed = speed, inside = inside, sync = reflect.sync }
  if last then
    local dx, dy = pos.x - last.x, pos.y - last.y
    st.dist = math.sqrt(dx * dx + dy * dy)
    st.sunDelta = math.abs((sunEl or 0) - (last.el or 0))
    st.since = os.clock() - last.t
  end
  if not M.wantCapture(st) then return end
  if M.captureReflection(nil, inside) then
    reflect.last = { x = pos.x, y = pos.y, el = sunEl or 0, t = os.clock() }
  end
end

local function updateReflection()
  local p = reflect.pending
  if not p then return end
  if facesReady(p.prefix) then
    reflect.pending = nil
    assignReflection(p.prefix)
  elseif os.clock() - p.t > M.REFLECT.waitS then
    reflect.pending = nil
    log('W', 'beamngEarth', 'fidelity: the reflection capture wrote no faces at ' .. p.prefix)
  end
end

-- The level's own environment map back (a live capture or a probe).
function M.restoreReflection()
  reflect.pending = nil
  reflect.last = nil
  env.live = false
  probes.current = nil
  return setEnv(nil)
end

-- probes ---------------------------------------------------------------------------------

local function coreState()
  local core = rawget(_G, 'beamngEarth_core')
  local ok, st = pcall(function() return core and core._state and core._state() end)
  return ok and st or nil
end

local function readIndex()
  local there = not (FS and FS.fileExists) or FS:fileExists(M.PROBES.index)
  local t = there and type(jsonReadFile) == 'function' and jsonReadFile(M.PROBES.index) or nil
  return type(t) == 'table' and type(t.probes) == 'table' and t or { version = 1, probes = {} }
end

-- The probes on disk as CubemapData objects (once per level; the faces' import is the cost, done as the level loads).
local function loadProbes()
  local t0 = os.clock()
  local list = {}
  for _, e in ipairs(readIndex().probes) do
    if #list >= M.PROBES.max then break end
    if type(e) == 'table' and tonumber(e.lat) and tonumber(e.lon) and type(e.prefix) == 'string'
        and facesReady(e.prefix) then
      local item = { id = e.id, lat = tonumber(e.lat), lon = tonumber(e.lon), prefix = e.prefix,
        name = M.PROBES.name .. (#list + 1) }
      if pcall(cubemap, item.name, item.prefix) then list[#list + 1] = item end
    end
  end
  probes.list = list
  if #list > 0 then
    log('I', 'beamngEarth', string.format('fidelity: %d reflection probes loaded in %.0f ms', #list,
      (os.clock() - t0) * 1000))
  end
end

-- The nearest probe as the environment map (or the level's own out of their range); not over a live capture.
local function updateProbes()
  if env.live or reflect.pending then return end
  if not probes.list then loadProbes() end
  local st = coreState()
  local pl = st and st.player
  local pick = pl and M.pickProbe(probes.list, pl.lat, pl.lon, probes.current) or nil
  if pick == probes.current then return end
  probes.current = pick
  local t0 = os.clock()
  setEnv(pick and pick.name or nil)
  log('I', 'beamngEarth', string.format('fidelity: reflection probe %s (%.1f ms)', pick and tostring(pick.id) or
    'none: the level\'s own map', (os.clock() - t0) * 1000))
end

local function dropProbes()
  if probes.current then setEnv(nil) end
  probes.current = nil
  probes.list = nil       -- read again next time in the level (a capture since, another session's)
end

-- Where the camera is: on the nearest road (the AI road graph, within PROBES.snapM), PROBES.eyeM over it. Moves a
-- free camera there and returns the old position to put back (nil when it stays put).
local function snapCamera()
  if not core_camera or not core_camera.getPosition or not core_camera.setPosition then return nil end
  local okN, name = pcall(core_camera.getActiveCamName, 0)
  if not okN or name ~= 'free' then return nil end
  local okP, cam = pcall(core_camera.getPosition)
  if not okP or not cam then return nil end
  if not map or not map.findClosestRoad or not map.getMap then return nil end
  local okR, n1, n2 = pcall(map.findClosestRoad, cam, M.PROBES.snapM)
  if not okR or not n1 or not n2 then return nil end
  local nodes = map.getMap().nodes
  local a, b = nodes[n1] and nodes[n1].pos, nodes[n2] and nodes[n2].pos
  if not a or not b then return nil end
  local q = M.nearestOnSegment(cam, a, b)
  local old = vec3(cam.x, cam.y, cam.z)
  core_camera.setPosition(0, vec3(q.x, q.y, q.z + M.PROBES.eyeM))
  return old
end

-- Capture a probe at the camera (snapped onto the road when the free camera is near one), the player's car hidden,
-- and add it to the index (replacing one with the same id: the place rounded to ~10 m). Returns ok, id or error.
function M.captureProbe(id)
  local st = coreState()
  if not st or not st.anchor then return false, 'no world' end
  if FS and FS.directoryCreate then FS:directoryCreate(M.PROBES.dir, true) end
  local old = snapCamera()
  local okC, cam = pcall(core_camera.getPosition)
  if not okC or not cam then
    if old then core_camera.setPosition(0, old) end
    return false, 'no camera'
  end
  local lat, lon = st.anchor:enuToGeodetic(cam.x, cam.y, cam.z)
  id = id or M.probeId(lat, lon)
  local prefix = M.PROBES.dir .. id .. '_'
  local ok, err, ms = capture(prefix, M.PROBES.res, true)
  if old then core_camera.setPosition(0, old) end
  if not ok then return false, err end
  if not facesReady(prefix) then return false, 'no faces written at ' .. prefix end
  local t = readIndex()
  local kept = {}
  for _, e in ipairs(t.probes) do
    if type(e) == 'table' and e.id ~= id then kept[#kept + 1] = e end
  end
  kept[#kept + 1] = { id = id, lat = lat, lon = lon, prefix = prefix, snapped = old ~= nil, t = os.time() }
  t.probes = kept
  if type(jsonWriteFile) == 'function' then jsonWriteFile(M.PROBES.index, t, true) end
  log('I', 'beamngEarth', string.format('fidelity: reflection probe %s captured in %.0f ms (%s, %d in the index)', id,
    ms, old and 'on the road' or 'at the camera', #kept))
  dropProbes()            -- loaded again with this one at the next check
  return true, id
end

------------------------------------------------------------------------------------------
-- wet roads (feature 'wet'): while it rains (weather.lua's rainNow), the road surfaces darken and turn glossy, so
-- they mirror the street through the game's screen-space reflections; dry again slowly after. The materials are
-- the stock ones roads.lua loads; their own values are read before the first change and put back.

M.WET = { materials = { 'road_asphalt_2lane', 'm_gm_concrete_plain', 'sidewalk_concrete', 'italy_cobblestone' },
  rough = 0.35, dark = 0.72, step = 0.1, inS = 90, outS = 900 }
local wet = { k = 0, written = 0, saved = {}, override = nil, at = nil }

-- Pure: a material's wet values from its own (roughness factor, base colour factor "r g b a") at wetness k (0..1).
function M.wetValues(rough, color, k, p)
  p = p or M.WET
  k = math.min(math.max(tonumber(k) or 0, 0), 1)
  local r = (tonumber(rough) or 1) * (1 + (p.rough - 1) * k)
  local c = {}
  for x in tostring(color or ''):gmatch('%S+') do c[#c + 1] = tonumber(x) or 1 end
  for i = 1, 4 do c[i] = c[i] or 1 end
  local d = 1 + (p.dark - 1) * k
  return string.format('%.4g', r), string.format('%.4g %.4g %.4g %.4g', c[1] * d, c[2] * d, c[3] * d, c[4])
end

local function writeWet(k)
  local found, failed, sample = {}, 0, nil
  for _, name in ipairs(M.WET.materials) do
    local m = scenetree.findObject(name)
    if m then
      found[#found + 1] = name
      local s = wet.saved[name]
      if not s then
        s = { rough = m:getField('roughnessFactor', 0), color = m:getField('baseColorFactor', 0) }
        wet.saved[name] = s
      end
      local r, c = M.wetValues(s.rough ~= '' and s.rough or nil, s.color ~= '' and s.color or nil, k)
      sample = sample or string.format('%s roughness %s -> %s, colour %s -> %s', name, tostring(s.rough), r,
        tostring(s.color), c)
      local ok = pcall(function()
        if k <= 0 then
          m:setField('roughnessFactor', 0, s.rough or '')
          m:setField('baseColorFactor', 0, s.color or '')
        else
          m:setField('roughnessFactor', 0, r)
          m:setField('baseColorFactor', 0, c)
        end
        m:flush()
        m:reload()
      end)
      if not ok then failed = failed + 1 end
    end
  end
  -- (slot F showed no change at all: this says which materials were found and what was written)
  log('I', 'beamngEarth', string.format('fidelity: wet %.1f on %d materials (%s)%s; %s', k, #found,
    table.concat(found, ', '), failed > 0 and (', ' .. failed .. ' failed') or '', sample or 'none found'))
  wet.written = k
  if k <= 0 then wet.saved = {} end
end

local function updateWet()
  local now = os.clock()
  local dt = wet.at and math.min(now - wet.at, 10) or 0
  wet.at = now
  local target = wet.override
  if target == nil then
    local w = rawget(_G, 'beamngEarth_weather')
    target = (w and tonumber(w.rainNow) or 0) > 0 and 1 or 0
  end
  if target > wet.k then wet.k = math.min(target, wet.k + dt / M.WET.inS)
  elseif target < wet.k then wet.k = math.max(target, wet.k - dt / M.WET.outS) end
  -- written in steps (each a flush and reload of a few materials)
  local q = math.floor(wet.k / M.WET.step + 0.5) * M.WET.step
  if math.abs(q - wet.written) >= M.WET.step * 0.5 then writeWet(q) end
end

local function dryNow()
  wet.k, wet.at = 0, nil
  if wet.written > 0 then writeWet(0) end
end

-- The look probe's (and a player's console) wet roads whatever the weather: 0..1, nil = the weather's again.
function M.setWet(k)
  wet.override = k and math.min(math.max(tonumber(k) or 0, 0), 1) or nil
  wet.k = wet.override or 0      -- back to the weather's: from dry (it ramps in again if it rains)
  lastCheck = -math.huge
end

------------------------------------------------------------------------------------------
-- public

function M.setMode(m)
  local ok = false
  for _, v in ipairs(M.MODES) do ok = ok or v == m end
  if not ok then return false end
  mode = m
  lastCheck = -math.huge
  return true
end
function M.getMode() return mode end

-- One feature on or off ('ao', 'shafts'; the look probe's A/B), and the shafts' debug view (mask and the sun's ring).
function M.setFeature(name, on)
  if features[name] == nil then return false end
  features[name] = on and true or false
  lastCheck = -math.huge
  return true
end
function M.setDebug(on) debug = on and true or false; lastCheck = -math.huge end

-- Every frame: in our level, the features on (checked every CHECK_S, the sun's constants with it); else off.
function M.update(inOurLevel)
  if not inOurLevel then
    if aoSaved or shaftsOn or env.saved or probes.list or wet.written > 0 then M.restore() end
    return
  end
  updateReflection()
  local t = os.clock()
  if t - lastCheck < M.CHECK_S then return end
  lastCheck = t
  if mode ~= 'on' then M.restore() return end
  if features.ao then applyAo() else restoreAo() end
  if features.shafts then updateShafts() else stopShafts() end
  if features.wet then updateWet() else dryNow() end
  if features.reflect then
    local _, el = sun()
    autoReflection(el)
  end
  if features.probes then updateProbes() elseif probes.list then dropProbes() end
end

-- Leaving our level (or off): the player's AO back, the shafts off (they stay registered, as the game's effects do).
function M.restore()
  lastCheck = -math.huge
  restoreAo()
  stopShafts()
  dryNow()
  M.restoreReflection()
  dropProbes()
end

function M.state()
  return { mode = mode, ao = aoSaved ~= nil, shafts = shaftsOn, debug = debug, reflection = env.live,
    probe = probes.current and probes.current.id or nil, probes = probes.list and #probes.list or nil, wet = wet.written }
end

return M
