-- BeamNG.Earth: what the weather does to the ground and the cars (Jeff, 2026-10-30: "weather (rain and snow perhaps?
-- not current in beamng but could be amazing)"). weather.lua drives the sky, the fog, the wind and the falling rain /
-- snow (the level's Precipitation objects); BeamNG has nothing after that: the roads stay dry and grippy in a
-- downpour and nothing settles in a blizzard. This module, updated from weather.lua every frame, adds:
--   * the ground's state (M.stepSurface, pure): wetness that builds up in rain and dries slowly after (faster when warm
--     and windy), puddles after a long, heavy rain, snow that piles up at or below freezing and melts above it (into
--     wet roads), and ice where a wet road freezes. Real weather starts it from what is falling now and the server's
--     snow depth (Open-Meteo), so a level loaded in a snowstorm is white at once (M.seedSurface);
--   * the grip: the game's ground models (core_environment.groundModels, the same ones the stock ground model debug
--     tool edits live with be:setGroundModel) moved towards wet asphalt (the stock ASPHALT_WET), wet dirt, packed snow
--     and ice (M.gripFor, pure). A wet paved road takes ASPHALT_WET's collision type too, so the tyres throw spray and
--     sound wet, a snowy one SNOW's. The level's values come back when the weather clears or the level ends;
--   * the look: shaders/beamngEarth/weatherSurface.cs.hlsl over the lit scene (wet roads that mirror the street lights
--     and the sky, puddles with rain rings, snow on the ground; off, and free, while everything is dry);
--   * thunderstorms: lightning (the same pass flashes the sky and the ground) and thunder after it, later and softer
--     the farther the strike (the game has no thunder: art/sound/beamngEarth/thunder_*.ogg, tools/make_thunder.py);
--   * the keys (core/input/actions/beamngEarth.json): Ctrl+Alt+E the next weather, Ctrl+Alt+Shift+E the one before,
--     Ctrl+Alt+Shift+1..8 one directly (clear, cloudy, drizzle, rain, storm, snow, blizzard, fog), Ctrl+Alt+Shift+W
--     real weather again, Ctrl+Alt+Shift+9 dry roads now (snow and ice gone too).
-- Settings: weatherGrip (the grip), weatherSurfaces (the look, lightning and thunder).
-- Console: require('beamngEarth/weatherfx').status()   .setSurface({ wet = 1, snow = 0 })   .setDebug(true)
local M = {}

------------------------------------------------------------------------------------------
-- the ground's state

M.WET_IN_S = 150          -- a dry road soaks through in this long in full rain (lighter rain: longer, by its share)
M.DRY_S = 1800            -- ... and a soaked one dries in this long at DRY_REF_C in calm air
M.DRY_REF_C = 15
M.PUDDLE_IN_S = 600       -- puddles fill in this long in full rain on a soaked road ...
M.PUDDLE_DRY_S = 1200     -- ... and drain in this long after
M.SNOW_IN_S = 1200        -- the ground turns white in this long in full snow (2 cm/h, weather.SNOW_FULL_CMH)
M.SNOW_STICK_C = 1.0      -- snow settles at or below this air temperature, falls as slush (wet) above
M.MELT_S_PER_C = 5400     -- full cover melts in this long per degree above MELT_C ...
M.MELT_C = 1.0
M.RAIN_MELT_S = 900       -- ... and full rain on it melts it in this long besides
M.FREEZE_C = -1.0         -- a wet road below this freezes ...
M.FREEZE_S = 900          -- ... over this long (water to ice) ...
M.THAW_S = 300            -- ... and thaws back to wet above THAW_C over this long
M.THAW_C = 0.5
M.FORCED_SPEED = 8        -- a weather chosen with the keys builds up and clears this much faster (minutes, not hours)
M.DEPTH_FULL_M = 0.05     -- snow depth (m, the server's) that covers the ground fully

local function clamp(v, lo, hi) return math.max(lo, math.min(hi, v)) end
local function num(v, d) return tonumber(v) or d end

-- Pure: the ground's state s { wet, puddle, snow, ice } (0..1 each) after dt seconds of weather e:
-- { rain = 0..1, snow = 0..1 (weather.lua's shares of a full fall), tempC (air at the player), windMs, speed (1, or
-- FORCED_SPEED) }. Returns s (changed in place).
function M.stepSurface(s, e, dt)
  dt = math.max(0, num(dt, 0)) * math.max(1, num(e.speed, 1))
  local rain, snowfall = clamp(num(e.rain, 0), 0, 1), clamp(num(e.snow, 0), 0, 1)
  local t, wind = num(e.tempC, 10), math.max(0, num(e.windMs, 0))
  local wet, puddle, snow, ice = num(s.wet, 0), num(s.puddle, 0), num(s.snow, 0), num(s.ice, 0)
  -- what falls: snow that settles, or water (rain, or snow too warm to stay)
  local water = rain
  if snowfall > 0 then
    if t <= M.SNOW_STICK_C then snow = snow + snowfall * dt / M.SNOW_IN_S
    else water = water + snowfall end
  end
  -- melting: above MELT_C by the degree, faster in rain; the melt wets the road
  if snow > 0 then
    local melt = math.max(0, t - M.MELT_C) * dt / M.MELT_S_PER_C + rain * dt / M.RAIN_MELT_S
    melt = math.min(melt, snow)
    snow = snow - melt
    wet = wet + melt * 2
  end
  -- wetting and drying
  if water > 0 then
    wet = wet + math.min(water, 1) * dt / M.WET_IN_S
  else
    local rate = clamp(0.4 + t / (M.DRY_REF_C * 1.67), 0.15, 2) * (1 + wind / 10)
    if snow > 0.5 then rate = rate * 0.3 end              -- (under snow: slush that stays)
    wet = wet - rate * dt / M.DRY_S
  end
  -- puddles: only on a soaked road in steady rain
  if water > 0.3 and wet > 0.9 then puddle = puddle + (math.min(water, 1) - 0.3) / 0.7 * dt / M.PUDDLE_IN_S
  elseif water <= 0.05 then puddle = puddle - dt / M.PUDDLE_DRY_S end
  -- ice: the water on the road freezes below FREEZE_C, thaws above THAW_C
  if t < M.FREEZE_C and wet > 0.05 then
    local f = math.min(wet, dt / M.FREEZE_S)
    wet, ice = wet - f, ice + f
    puddle = math.min(puddle, wet)
  elseif t > M.THAW_C and ice > 0 then
    local f = math.min(ice, dt / M.THAW_S)
    ice, wet = ice - f, wet + f
  end
  s.wet, s.snow, s.ice = clamp(wet, 0, 1), clamp(snow, 0, 1), clamp(ice, 0, 1)
  s.puddle = clamp(puddle, 0, s.wet)
  return s
end

-- Pure: the state the ground has when a level starts in weather e (as stepSurface's) with snowDepthM on the ground
-- (the server's, nil = unknown): wet in rain that is falling now, white in snow that lies (or falls cold enough).
function M.seedSurface(e, snowDepthM)
  local s = { wet = 0, puddle = 0, snow = 0, ice = 0 }
  local rain, snowfall, t = clamp(num(e.rain, 0), 0, 1), clamp(num(e.snow, 0), 0, 1), num(e.tempC, 10)
  if rain > 0 or (snowfall > 0 and t > M.SNOW_STICK_C) then
    s.wet = clamp(0.6 + rain, 0, 1)
    s.puddle = clamp((rain - 0.4) * 1.2, 0, 1)
  end
  local depth = tonumber(snowDepthM)
  if depth and depth > 0 then s.snow = clamp(depth / M.DEPTH_FULL_M, 0, 1)
  elseif snowfall > 0 and t <= M.SNOW_STICK_C then s.snow = clamp(0.4 + snowfall * 0.5, 0, 1) end
  if t < M.FREEZE_C and s.wet > 0 and s.snow < 0.5 then s.ice, s.wet = s.wet * 0.5, s.wet * 0.5 end
  return s
end

------------------------------------------------------------------------------------------
-- the grip

-- The ground models the weather changes, by kind: 'paved' (asphalt, concrete, cobbles, rock: wet like ASPHALT_WET,
-- collision type ASPHALT_WET / SNOW) and 'loose' (dirt, gravel, grass, sand, mud: wet like GRAVEL_WET). The rest (the
-- vehicles' own, ice, snow, water-wet ones already, the props') are left alone. Aliases follow their parent.
M.GM_KIND = {
  ASPHALT = 'paved', ASPHALT_OLD = 'paved', ASPHALT_PREPPED = 'paved', RUMBLE_STRIP = 'paved', COBBLESTONE = 'paved',
  ROCK = 'paved',
  DIRT = 'loose', DIRT_DUSTY = 'loose', DIRT_DUSTY_LOOSE = 'loose', GRAVEL = 'loose', GRASS = 'loose', SAND = 'loose',
  MUD = 'loose',
}
-- At full wetness the friction is the dry value times these (paved: the stock ASPHALT_WET over ASPHALT, 0.92 / 0.98
-- and 0.55 / 0.70; loose: GRAVEL_WET over GRAVEL), the hydrodynamic friction at least hydro and the Stribeck velocity
-- (where sliding friction takes over) this.
M.WET = {
  paved = { static = 0.92 / 0.98, sliding = 0.55 / 0.70, hydro = 0.0012, stribeck = 5 },
  loose = { static = 0.62 / 0.69, sliding = 0.60 / 0.74, hydro = 0.009, stribeck = 6 },
}
M.PUDDLE_SLIDING = 0.85   -- standing water: sliding friction times this more (aquaplaning-ish) at full puddles
-- Packed snow and ice on top (absolute values: whatever is under them); ice never fully (patches): at most ICE_MAX.
M.SNOW_PACKED = { static = 0.55, sliding = 0.33, hydro = 0.004, stribeck = 3 }
M.ICE = { static = 0.40, sliding = 0.20, hydro = 0, stribeck = 0.5 }
M.ICE_MAX = 0.6
M.WET_TYPE_AT, M.SNOW_TYPE_AT = 0.45, 0.5 -- the collision type changes at these
M.GRIP_STEP = 0.04        -- written when the state moved this much (each write is a be:setGroundModel per name)
M.GRIP_MIN_S = 1.0
M.FIELDS = { 'staticFrictionCoefficient', 'slidingFrictionCoefficient', 'hydrodynamicFriction', 'stribeckVelocity' }

local function lerp(a, b, k) return a + (b - a) * k end

-- Pure: the friction values of a ground model of kind ('paved' / 'loose') from its dry values base
-- { static, sliding, hydro, stribeck } in ground state s. Returns { static, sliding, hydro, stribeck }.
function M.gripFor(kind, base, s)
  local w = M.WET[kind]
  if not w then return { static = base.static, sliding = base.sliding, hydro = base.hydro, stribeck = base.stribeck } end
  local snow, ice = clamp(num(s.snow, 0), 0, 1), clamp(num(s.ice, 0), 0, 1) * M.ICE_MAX
  local wet = clamp(num(s.wet, 0), 0, 1) * (1 - snow)
  local puddle = clamp(num(s.puddle, 0), 0, 1) * (1 - snow)
  local r = {
    static = base.static * lerp(1, w.static, wet),
    sliding = base.sliding * lerp(1, w.sliding, wet) * lerp(1, M.PUDDLE_SLIDING, puddle),
    hydro = lerp(base.hydro, math.max(base.hydro, w.hydro), wet),
    stribeck = lerp(base.stribeck, w.stribeck, wet),
  }
  for _, layer in ipairs({ { M.SNOW_PACKED, snow }, { M.ICE, ice } }) do
    local t, k = layer[1], layer[2]
    if k > 0 then
      r.static = lerp(r.static, math.min(r.static, t.static), k)
      r.sliding = lerp(r.sliding, math.min(r.sliding, t.sliding), k)
      r.hydro = lerp(r.hydro, t.hydro, k)
      r.stribeck = lerp(r.stribeck, t.stribeck, k)
    end
  end
  return r
end

-- Pure: the collision type name a paved ground model takes in state s (nil: its own).
function M.typeFor(kind, s)
  if kind ~= 'paved' then return nil end
  if num(s.snow, 0) >= M.SNOW_TYPE_AT then return 'SNOW' end
  if num(s.wet, 0) * (1 - num(s.snow, 0)) >= M.WET_TYPE_AT or num(s.ice, 0) >= M.WET_TYPE_AT then return 'ASPHALT_WET' end
  return nil
end

-- Pure: the grip left (0..1, dry asphalt = 1) in state s: the status line's number.
function M.gripShare(s)
  local base = { static = 1, sliding = 1, hydro = 0, stribeck = 4.5 }
  return M.gripFor('paved', base, s).static
end

------------------------------------------------------------------------------------------
-- runtime state

M.CHECK_S = 0.5           -- the state, the grip and the effect's switch (the shader constants: every frame)
M.SEED_WAIT_S = 60        -- the ground starts dry when no weather arrived within this long of the level's start
local surface = { wet = 0, puddle = 0, snow = 0, ice = 0 }
local seeded = false      -- the state started from the weather at this level's start
local override = nil      -- setSurface's fixed state (the probe's, the console's)
local active = false      -- in our level, the weather on
local clock, checkAt = 0, 0
local opts = { grip = true, surfaces = true }
local debugView = false

local function env() return rawget(_G, 'core_environment') end
local function weather() return rawget(_G, 'beamngEarth_weather') end

local function toast(text)
  local m = rawget(_G, 'ui_message')
  if m then pcall(m, text, 5, 'beamngEarthWeather') end
end

------------------------------------------------------------------------------------------
-- grip runtime

local grip = { saved = {}, written = nil, at = -math.huge, typeIds = {} }

local function particleTypeId(name)
  if grip.typeIds[name] ~= nil then return grip.typeIds[name] or nil end
  local ok, id = pcall(function()
    local p = require('particles')
    return p.getOrAddMaterialIDByName(p.getMaterialsParticlesTable(), name)
  end)
  grip.typeIds[name] = (ok and tonumber(id)) or false
  return grip.typeIds[name] or nil
end

-- The ground models by parent: parent -> { kind, cdata, names = { ... } } (aliases share their parent's cdata).
local function groundModels()
  local e = env()
  local gms = e and e.groundModels
  if type(gms) ~= 'table' then return {} end
  local out = {}
  for name, g in pairs(gms) do
    local parent = (g.isAlias and g.parent and g.parent ~= 'none') and g.parent or name
    local kind = M.GM_KIND[parent]
    if kind and g.cdata then
      local o = out[parent]
      if not o then o = { kind = kind, cdata = gms[parent] and gms[parent].cdata or g.cdata, names = {} }; out[parent] = o end
      o.names[#o.names + 1] = name
    end
  end
  return out
end

local function submit(o)
  local b = rawget(_G, 'be')
  if not (b and b.setGroundModel) then return false end
  for _, name in ipairs(o.names) do b:setGroundModel(name, o.cdata) end
  return true
end

-- Write the grip of state s (nil: the level's own values back).
local function writeGrip(s)
  local n = 0
  for parent, o in pairs(groundModels()) do
    local c = o.cdata
    local sv = grip.saved[parent]
    if not sv or sv.cdata ~= c then -- first time, or the game reloaded its ground models (a new cdata): theirs is base
      sv = { cdata = c, static = tonumber(c.staticFrictionCoefficient) or 1, sliding = tonumber(c.slidingFrictionCoefficient) or 0.7,
        hydro = tonumber(c.hydrodynamicFriction) or 0, stribeck = tonumber(c.stribeckVelocity) or 4.5,
        ctype = tonumber(c.collisiontype), skid = c.skidMarks }
      grip.saved[parent] = sv
    end
    local r = s and M.gripFor(o.kind, sv, s) or sv
    c.staticFrictionCoefficient = r.static
    c.slidingFrictionCoefficient = r.sliding
    c.hydrodynamicFriction = r.hydro
    c.stribeckVelocity = r.stribeck
    local tname = s and M.typeFor(o.kind, s)
    local tid = tname and particleTypeId(tname)
    if tid then c.collisiontype = tid; c.skidMarks = false
    elseif sv.ctype then c.collisiontype = sv.ctype; c.skidMarks = sv.skid end
    if submit(o) then n = n + 1 end
  end
  grip.written = s and { wet = s.wet, puddle = s.puddle, snow = s.snow, ice = s.ice } or nil
  grip.at = clock
  if not s then grip.saved = {} end
  return n
end

local function gripMoved(s)
  local w = grip.written
  if not w then return true end
  for _, k in ipairs({ 'wet', 'puddle', 'snow', 'ice' }) do
    if math.abs((s[k] or 0) - (w[k] or 0)) >= M.GRIP_STEP then return true end
  end
  -- the last bit of drying or melting is always written (back to exactly the level's values)
  for _, k in ipairs({ 'wet', 'snow', 'ice' }) do
    if (s[k] or 0) <= 0.001 and (w[k] or 0) > 0.001 then return true end
  end
  return false
end

local function anything(s) return s.wet > 0.001 or s.snow > 0.001 or s.ice > 0.001 end

local function updateGrip(s)
  if not opts.grip or not anything(s) then
    if grip.written then writeGrip(nil) end
    return
  end
  -- a reload of the ground models by the game (core_environment.reset) brought the dry values back: write again
  local stale = false
  for parent, o in pairs(groundModels()) do
    local sv = grip.saved[parent]
    if sv and sv.cdata ~= o.cdata then stale = true end
  end
  if (stale or gripMoved(s)) and clock - grip.at >= M.GRIP_MIN_S then writeGrip(s) end
end

------------------------------------------------------------------------------------------
-- the look: the surface pass

M.PFX = 'BeamNGEarthWeatherSurfacePfx'
M.SHADER = 'shaders/beamngEarth/weatherSurface.cs.hlsl'
M.LOOK = {
  reflect = 0.9,          -- reflection strength (times the Fresnel term)
  reflectM = 350,         -- reflections out to this far (m)
  snowGain = 3.6,         -- snow brightness over the brightness of what it covers (~0.75 albedo over ~0.2)
  wetDark = 0.55,         -- a soaked rough surface's brightness factor
  skyFlash = 4.0,         -- lightning: the sky's brightness times 1 + flash * this ...
  groundFlash = 1.2,      -- ... the ground's 1 + flash * this
  tunnel = 0.25,          -- in a tunnel (the camera under a roof): wet at this share, no snow
}
local pfxOn = false

local function stateBlock()
  local name = 'BeamNGEarthWeatherStateBlock'
  if scenetree.findObject(name) then return name end
  local sb = createObject('GFXStateBlockData')
  sb.zDefined = true
  sb.zEnable = false
  sb.zWriteEnable = false
  sb.samplersDefined = true
  sb:setField('samplerStates', 0, 'SamplerClampLinear')
  sb:setField('samplerStates', 1, 'SamplerClampPoint')
  sb:setField('samplerStates', 2, 'SamplerClampPoint')
  sb:registerObject(name)
  return name
end

-- Created once: before the fog (FogPostFx: PFXBeforeBin ObjTranslucentBin, priority 5; a higher priority goes first),
-- so the fog lies over the wet road and the snow as over the rest; the translucent objects (the drops, the markings,
-- glass) come after.
local function createPfx()
  local pfx = scenetree.findObject(M.PFX)
  if pfx then return pfx end
  if not scenetree.findObject('BeamNGEarthWeatherShader') then
    local sh = createObject('ShaderData')
    sh.DXComputShaderName = M.SHADER
    sh.pixVersion = 5.0
    sh:registerObject('BeamNGEarthWeatherShader')
  end
  pfx = createObject('PostEffect')
  pfx.allowReflectPass = false
  pfx:setField('shader', 0, 'BeamNGEarthWeatherShader')
  pfx:setField('stateBlock', 0, stateBlock())
  pfx:setField('texture', 0, '$backBuffer')
  pfx:setField('texture', 1, '#prepass[RT0]')
  pfx:setField('texture', 2, '#prepass[Depth]')
  pfx:setField('target', 0, '$backBuffer')
  pfx:setField('isEnabled', 0, '0')
  pfx:setField('renderTime', 0, 'PFXBeforeBin')
  pfx:setField('renderBin', 0, 'ObjTranslucentBin')
  pfx.renderPriority = 6
  pfx:registerObject(M.PFX)
  return pfx
end

local function setPfx(on)
  if on == pfxOn then return end
  local ok, err = pcall(function()
    local pfx = on and createPfx() or scenetree.findObject(M.PFX)
    if pfx then if on then pfx:enable() else pfx:disable() end end
  end)
  if not ok then log('W', 'beamngEarth', 'weatherfx: the surface pass: ' .. tostring(err)) end
  pfxOn = on and ok
end

local function cameraXYZ()
  local cam = rawget(_G, 'core_camera')
  if not cam or not cam.getPositionXYZ then return nil end
  local ok, x, y, z = pcall(cam.getPositionXYZ)
  if ok and tonumber(x) then return x, y, z end
end

local function inTunnel(x, y, z)
  local a = package.loaded['beamngEarth/ambience']
  if not (a and a.inTunnel and x) then return false end
  local ok, r = pcall(a.inTunnel, x, y, z)
  return ok and r or false
end

-- Pure: the shader's constants for state s, the camera (x, y, z), time t (s), rain now (0..1), lightning flash
-- (0..), in a tunnel or not, debug view. Strings as setShaderConst takes them.
function M.shaderConsts(s, cam, t, rain, flash, tunnel, dbg, look)
  look = look or M.LOOK
  local fmt = function(a, b, c, d) return string.format('%.6g %.6g %.6g %.6g', a, b, c, d) end
  local wet, puddle, snow = s.wet, s.puddle, s.snow
  if tunnel then wet, puddle, snow = wet * look.tunnel, 0, 0 end
  wet = math.max(wet, num(s.ice, 0) * 0.6)  -- (a frozen road shines like a wet one)
  return {
    ['$bneWx0'] = fmt(wet, puddle, snow, t % 3600),
    ['$bneWx1'] = fmt(cam[1], cam[2], cam[3], tunnel and 0 or rain),
    ['$bneWx2'] = fmt(flash, look.reflect, look.reflectM, dbg and 1 or 0),
    ['$bneWx3'] = fmt(look.snowGain, look.wetDark, look.skyFlash, look.groundFlash),
  }
end

------------------------------------------------------------------------------------------
-- thunderstorms

M.STRIKE_GAP_S = { 7, 28 }  -- seconds between strikes (uniform)
M.STRIKE_KM = { 0.6, 9 }    -- the strike's distance (km, uniform in area)
M.SOUND_MPS = 343
M.THUNDER = { '/art/sound/beamngEarth/thunder_1.ogg', '/art/sound/beamngEarth/thunder_2.ogg',
  '/art/sound/beamngEarth/thunder_3.ogg' }
-- the flash over time since the strike (s, brightness): a stroke, a dim gap, a restrike, the glow fading
M.FLASH = { { 0, 1 }, { 0.06, 1 }, { 0.09, 0.15 }, { 0.16, 0.15 }, { 0.19, 0.85 }, { 0.26, 0.5 }, { 0.6, 0 } }
local storm = { nextAt = nil, flashAt = nil, power = 0, thunder = {} }

-- Pure: the flash brightness at s seconds after a strike (0 outside it).
function M.flashAt(s)
  local f = M.FLASH
  if not s or s < 0 or s >= f[#f][1] then return 0 end
  for i = 2, #f do
    if s <= f[i][1] then
      local a, b = f[i - 1], f[i]
      return a[2] + (b[2] - a[2]) * (s - a[1]) / math.max(b[1] - a[1], 1e-6)
    end
  end
  return 0
end

-- Pure: a strike from two random numbers u1, u2 (0..1): distance (km), flash power (0..1), thunder delay (s), volume.
function M.strike(u1, u2)
  local lo, hi = M.STRIKE_KM[1], M.STRIKE_KM[2]
  local km = math.sqrt(lo * lo + (hi * hi - lo * lo) * u1)  -- uniform over the ring's area
  local power = clamp(1.25 - km / 6, 0.15, 1)
  return { km = km, power = power, delay = km * 1000 / M.SOUND_MPS, volume = clamp(1.1 - km / 10, 0.15, 1),
    sound = math.min(#M.THUNDER, 1 + math.floor(u2 * #M.THUNDER)) }
end

local function playThunder(t)
  local a = rawget(_G, 'Engine') and Engine.Audio
  if not (a and a.playOnce) then return end
  local path = M.THUNDER[t.sound]
  if FS and FS.fileExists and not FS:fileExists(path) then return end
  pcall(a.playOnce, 'AudioGui', path, { volume = t.volume })
end

local function updateStorm(on, dt)
  if not on then storm.nextAt, storm.flashAt = nil, nil end
  if on and not storm.nextAt then storm.nextAt = clock + 2 + math.random() * 8 end
  if on and clock >= storm.nextAt then
    local t = M.strike(math.random(), math.random())
    storm.flashAt, storm.power = clock, t.power
    t.at = clock + t.delay
    storm.thunder[#storm.thunder + 1] = t
    storm.nextAt = clock + M.STRIKE_GAP_S[1] + math.random() * (M.STRIKE_GAP_S[2] - M.STRIKE_GAP_S[1])
  end
  local keep = {}
  for _, t in ipairs(storm.thunder) do
    if clock >= t.at then playThunder(t) elseif on then keep[#keep + 1] = t end
  end
  storm.thunder = keep
  local f = storm.flashAt and M.flashAt(clock - storm.flashAt) * storm.power or 0
  if storm.flashAt and clock - storm.flashAt > 1 then storm.flashAt = nil end
  return f
end

------------------------------------------------------------------------------------------
-- update

local function weatherNow()
  local w = weather()
  local e = { rain = 0, snow = 0, tempC = 10, windMs = 0, speed = 1 }
  if not w then return e, w end
  e.rain = num(w.rainNow, 0)
  e.snow = num(w.snowNow, 0)
  e.tempC = w.airTempC and num(w.airTempC(), 10) or 10
  local m = w.current and w.current()
  e.windMs = m and m.wind and num(m.wind.ms, 0) or 0
  e.speed = (w.forced and w.forced()) and M.FORCED_SPEED or 1
  return e, w
end

function M.update(dt, inOurLevel)
  dt = num(dt, 0)
  clock = clock + dt
  local w = weather()
  local on = inOurLevel and w ~= nil and w.getMode and w.getMode() == 'real'
  if not on then
    if active then M.restore() end
    return
  end
  if not active then active = true; checkAt = 0 end
  local e = weatherNow()
  if not seeded then
    -- from the weather the level starts in (its target: the drops are still fading in), once there is one
    local rain, snow = nil, nil
    if w.targetPrecip then rain, snow = w.targetPrecip() end
    if rain or clock >= M.SEED_WAIT_S then
      local fresh = M.seedSurface({ rain = rain or 0, snow = snow or 0, tempC = e.tempC }, w.snowDepthM and w.snowDepthM())
      for k, v in pairs(fresh) do surface[k] = math.max(surface[k] or 0, v) end
      seeded = true
    end
  end
  if override then
    for k, v in pairs(override) do surface[k] = v end
  else
    M.stepSurface(surface, e, dt)
  end
  local flash = opts.surfaces and updateStorm(w.stormNow and w.stormNow() and e.rain > 0, dt) or updateStorm(false, dt)
  if clock >= checkAt then
    checkAt = clock + M.CHECK_S
    local ok, err = pcall(updateGrip, surface)
    if not ok then log('W', 'beamngEarth', 'weatherfx: grip: ' .. tostring(err)) end
    setPfx(opts.surfaces and (anything(surface) or flash > 0 or storm.nextAt ~= nil))
  end
  if pfxOn then
    local x, y, z = cameraXYZ()
    local pfx = scenetree.findObject(M.PFX)
    if pfx and x then
      local consts = M.shaderConsts(surface, { x, y, z }, clock, e.rain, flash, inTunnel(x, y, z), debugView)
      for k, v in pairs(consts) do pfx:setShaderConst(k, v) end
    end
  end
end

-- Everything back as the level has it (the grip, the pass off); the ground's state is kept for a return to real.
function M.restore()
  active = false
  if grip.written then pcall(writeGrip, nil) end
  setPfx(false)
  updateStorm(false, 0)
end

-- A new level: the ground starts again from the weather there.
function M.onLevel()
  M.restore()
  surface = { wet = 0, puddle = 0, snow = 0, ice = 0 }
  seeded, grip.saved, grip.typeIds, grip.at = false, {}, {}, -math.huge
  storm.thunder = {}
  clock, checkAt = 0, 0
  pfxOn = false                     -- (the level's objects went with it)
end

------------------------------------------------------------------------------------------
-- keys and console

M.NAMES = { clear = 'Clear', cloudy = 'Cloudy', drizzle = 'Drizzle', rain = 'Rain', storm = 'Thunderstorm',
  snow = 'Snow', blizzard = 'Blizzard', fog = 'Fog' }

local function chosen(name)
  local w = weather()
  if not (w and w.force) then return nil end
  local now = w.force(name)
  if now then toast('Weather: ' .. (M.NAMES[now] or now) .. ' (Ctrl+Alt+Shift+W: real weather again)')
  else toast('Weather: real weather where you are') end
  return now
end

-- The next (dir 1) or previous (dir -1) weather in the round, real weather between the last and the first.
function M.cycle(dir)
  local w = weather()
  if not (w and w.cycleNext) then return nil end
  return chosen(w.cycleNext(w.forced and w.forced(), dir or 1))
end
-- A weather directly (a weather.PRESETS name), or nil for real weather.
function M.choose(name) return chosen(name) end
function M.real() return chosen(nil) end

-- Dry roads at once (the snow and the ice gone too); the weather goes on.
function M.dryNow()
  surface = { wet = 0, puddle = 0, snow = 0, ice = 0 }
  toast('Roads dry again')
end

-- A fixed ground state (the look probe's, the console's): { wet, puddle, snow, ice } 0..1, nil = the weather's again.
function M.setSurface(s)
  if s == nil then override = nil; return end
  override = {}
  for _, k in ipairs({ 'wet', 'puddle', 'snow', 'ice' }) do override[k] = clamp(num(s[k], 0), 0, 1) end
end

function M.setDebug(on) debugView = on and true or false end
function M.setGrip(on) opts.grip = on and true or false; checkAt = 0 end
function M.setSurfaces(on) opts.surfaces = on and true or false; checkAt = 0 end
-- A strike now (the console's, the probe's): the flash and its thunder.
function M.strikeNow() if storm then storm.nextAt = clock end end

function M.surface() return { wet = surface.wet, puddle = surface.puddle, snow = surface.snow, ice = surface.ice } end

-- One line for the BeamNG.Earth window.
function M.statusLine()
  local s = surface
  if not anything(s) then return 'Roads: dry' end
  local parts = {}
  if s.snow > 0.01 then parts[#parts + 1] = string.format('snow %d%%', math.floor(s.snow * 100 + 0.5)) end
  if s.ice > 0.01 then parts[#parts + 1] = string.format('ice %d%%', math.floor(s.ice * 100 + 0.5)) end
  if s.wet > 0.01 then parts[#parts + 1] = string.format('wet %d%%', math.floor(s.wet * 100 + 0.5)) end
  if s.puddle > 0.01 then parts[#parts + 1] = string.format('puddles %d%%', math.floor(s.puddle * 100 + 0.5)) end
  return string.format('Roads: %s, grip %d%%%s', table.concat(parts, ', '), math.floor(M.gripShare(s) * 100 + 0.5),
    opts.grip and '' or ' (grip effect off)')
end

function M.status()
  return { surface = M.surface(), override = override, grip = opts.grip, surfaces = opts.surfaces, pfx = pfxOn,
    written = grip.written, storm = storm.nextAt ~= nil, line = M.statusLine() }
end

return M
