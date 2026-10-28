-- BeamNG.Earth look probe: the GE side of tools/probe_look.py. Sent through the game's MCP run_lua (no mod
-- file); defines the global BNGE_LOOK and returns 'ok'. Every function takes one JSON string (or nothing) and
-- returns JSON:
--   dump()          the renderer state that decides the look: every field of the ScatterSky, the LevelInfo's fog,
--                   core_environment's state, every PostEffect (class, render time / bin / priority, enabled) and
--                   the HDR objects' fields (combine pass, bloom, local exposure), Engine.Render's functions
--   apply(cfg)      a variant: { sky = {field = value}, level = {...}, objects = { name = {field = value} },
--                   a value "*k" / "+k" scales / offsets the current one,
--                   consts = { object = { ["$name"] = value } }, lua = "chunk" }. Every field is remembered the
--                   first time it is written, so reset() puts the level's own values back
--   reset()         every remembered field back, in reverse order (postApply / applyChanges after)
-- Fields are written with setField (vectors as "r g b"), then the object's postApply.
local L = {}
_G.BNGE_LOOK = L

local function toJson(v) return (rawget(_G, 'jsonEncode') or dumps)(v) end
local function decode(cfg) if type(cfg) == 'string' then return jsonDecode(cfg) end return cfg or {} end

local function firstOfClass(class)
  local names = scenetree.findClassObjects(class)
  return names and names[1] and scenetree.findObject(names[1]) or nil
end
local function sky() return firstOfClass('ScatterSky') end
local function levelInfo() return firstOfClass('LevelInfo') or scenetree.theLevelInfo end

local function fields(o)
  local out = {}
  if not o then return out end
  local ok, list = pcall(function() return o:getFields() end)
  if not ok or type(list) ~= 'table' then return out end
  for k, _ in pairs(list) do
    local name = type(k) == 'string' and k or nil
    if name then
      local okV, v = pcall(function() return o:getField(name, 0) end)
      if okV then out[name] = v end
    end
  end
  return out
end

local function vstr(v)
  if type(v) == 'table' then
    local p = {}
    for i, x in ipairs(v) do p[i] = string.format('%.9g', x) end
    return table.concat(p, ' ')
  end
  return tostring(v)
end

function L.dump()
  local out = { sky = fields(sky()), level = {}, env = nil, postEffects = {}, objects = {}, render = {} }
  local li = levelInfo()
  if li then
    for _, k in ipairs({ 'fogDensity', 'fogDensityOffset', 'fogAtmosphereHeight', 'fogColor', 'visibleDistance',
        'canvasClearColor', 'globalEnviromentMap', 'nearClip' }) do
      local ok, v = pcall(function() return li:getField(k, 0) end)
      if ok then out.level[k] = v end
    end
  end
  local env = rawget(_G, 'core_environment')
  if env and env.getState then pcall(function() out.env = env.getState() end) end
  local names = scenetree.findClassObjects('PostEffect') or {}
  for _, cls in ipairs({ 'PostEffectBrightPass', 'PostEffectDownScale', 'PostEffectLuminance',
      'PostEffectLocalExposure', 'PostEffectCombinePass', 'PostEffectBloom', 'PostEffectMaskedBlur' }) do
    for _, n in ipairs(scenetree.findClassObjects(cls) or {}) do names[#names + 1] = n end
  end
  for _, n in ipairs(names) do
    local o = scenetree.findObject(n)
    if o then
      local rec = { class = o:getClassName() }
      for _, k in ipairs({ 'renderTime', 'renderBin', 'renderPriority', 'isEnabled', 'shader', 'target' }) do
        local ok, v = pcall(function() return o:getField(k, 0) end)
        if ok then rec[k] = v end
      end
      pcall(function() rec.enabled = o:isEnabled() end)
      out.postEffects[n] = rec
    end
  end
  for _, n in ipairs({ 'PostEffectCombinePassObject', 'PostEffectBloomObject', 'PostEffectLocalExposureObject',
      'PostEffectBrightPassObject', 'PostEffectLuminanceObject', 'FogPostFx', 'DOFPostEffect' }) do
    out.objects[n] = fields(scenetree.findObject(n))
  end
  local R = Engine and Engine.Render
  if type(R) == 'table' then
    for k, v in pairs(R) do out.render[#out.render + 1] = k .. ':' .. type(v) end
    table.sort(out.render)
  end
  for _, v in ipairs({ '$atmosphericFogEnabled', '$fogScatterParams', '$applyHeightFog' }) do
    local okG, val = pcall(function() return getConsoleVariable(v) end)
    out.render[#out.render + 1] = v .. '=' .. tostring(okG and val or '?')
  end
  return toJson(out)
end

local saved = {}          -- { {obj id, field, value} } in write order
local touched = {}        -- id .. field -> true
local function remember(o, k)
  local key = o:getID() .. '|' .. k
  if touched[key] then return end
  touched[key] = true
  saved[#saved + 1] = { id = o:getID(), field = k, value = o:getField(k, 0) }
end

local function post(o)
  if o.postApply then pcall(function() o:postApply() end) end
  if o.applyChanges then pcall(function() o:applyChanges() end) end
end

-- "*2" / "+0.5": the field's current value (every component) times / plus the number
local function relative(cur, v)
  local op, n = tostring(v):match('^([%*%+])(%-?[%d%.eE%-]+)$')
  n = tonumber(n)
  if not op or not n then return v end
  local out = {}
  for x in tostring(cur):gmatch('[-%d%.eE]+') do
    local c = tonumber(x) or 0
    out[#out + 1] = string.format('%.9g', op == '*' and c * n or c + n)
  end
  return table.concat(out, ' ')
end

local function write(o, t, errors, label)
  if not o then errors[#errors + 1] = label .. ': not found' return end
  for k, v in pairs(t) do
    local ok, err = pcall(function() remember(o, k); o:setField(k, 0, vstr(relative(o:getField(k, 0), v))) end)
    if not ok then errors[#errors + 1] = label .. '.' .. k .. ': ' .. tostring(err) end
  end
  post(o)
end

function L.apply(cfg)
  cfg = decode(cfg)
  local errors = {}
  if cfg.sky then write(sky(), cfg.sky, errors, 'sky') end
  if cfg.level then write(levelInfo(), cfg.level, errors, 'level') end
  for name, t in pairs(cfg.objects or {}) do write(scenetree.findObject(name), t, errors, name) end
  for name, t in pairs(cfg.consts or {}) do
    local o = scenetree.findObject(name)
    if not o then errors[#errors + 1] = name .. ': not found' else
      for k, v in pairs(t) do
        local ok, err = pcall(function() o:setShaderConst(k, vstr(v)) end)
        if not ok then errors[#errors + 1] = name .. k .. ': ' .. tostring(err) end
      end
    end
  end
  if cfg.lua and cfg.lua ~= '' then
    local f, err = load(cfg.lua)
    if not f then errors[#errors + 1] = 'lua: ' .. tostring(err) else
      local ok, r = pcall(f)
      if not ok then errors[#errors + 1] = 'lua: ' .. tostring(r) end
    end
  end
  return toJson({ ok = #errors == 0, errors = errors })
end

function L.reset()
  local errors, objs = {}, {}
  for i = #saved, 1, -1 do
    local s = saved[i]
    local o = scenetree.findObjectById(s.id)
    if o then
      local ok, err = pcall(function() o:setField(s.field, 0, s.value) end)
      if not ok then errors[#errors + 1] = s.field .. ': ' .. tostring(err) end
      objs[s.id] = o
    end
  end
  for _, o in pairs(objs) do post(o) end
  saved, touched = {}, {}
  return toJson({ ok = #errors == 0, errors = errors })
end

return 'ok'
