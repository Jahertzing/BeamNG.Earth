-- luajit mod/tests/test_look.lua  (distance haze, haze.lua; colour grade, grade.lua)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local H = require('beamngEarth/haze')
local G = require('beamngEarth/grade')

-- haze: the aerosol at the player's ground is BG + s * HAZE (green = 550 nm), whatever the ground height
local function atGround(f, groundM) return f.atmoMieScattering[2] * math.exp(-(groundM / 1000) / f.atmoMieScaleHeight) end
T.near(atGround(H.fieldsFor(0, 0), 0), H.BG_KM, 1e-12, 'strength 0 at sea level: the background')
T.near(atGround(H.fieldsFor(0.5, 1600), 1600), H.BG_KM + 0.5 * H.HAZE_KM, 1e-12, 'Denver: compensated for the height')
T.near(atGround(H.fieldsFor(1, 3000), 3000), H.BG_KM + H.HAZE_KM, 1e-12, 'a pass at 3 km')
local hi = H.fieldsFor(1, 9000)
T.near(atGround(hi, H.GROUND_MAX_KM * 1000), H.BG_KM + H.HAZE_KM, 1e-12, 'compensation capped at GROUND_MAX_KM')
T.check(H.fieldsFor(2, 0).atmoMieScattering[2] == H.fieldsFor(1, 0).atmoMieScattering[2], 'strength clamped to 1')
T.check(H.fieldsFor(-1, 0).atmoMieScattering[2] == H.fieldsFor(0, 0).atmoMieScattering[2], 'strength clamped to 0')
local f = H.fieldsFor(0.5, 1600)
T.check(f.atmoMieScattering[3] > f.atmoMieScattering[2] and f.atmoMieScattering[2] > f.atmoMieScattering[1],
  'blue scatters more than red (Angstrom)')
T.near(f.atmoMieExtinction[2], f.atmoMieScattering[2] / H.SSA, 1e-15, 'extinction = scattering / SSA')
T.check(not H.differs({ 1, 2, 3 }, { 1.001, 2, 3 }) and H.differs({ 1, 2, 3 }, { 1.1, 2, 3 }), 'relative tolerance')
T.check(H.differs(1, nil), 'a missing value differs')

-- haze against a stub sky: written once, the level's values remembered and put back
local fields = { atmoMieScattering = '0.003996 0.003996 0.003996', atmoMieExtinction = '0.00444 0.00444 0.00444',
  atmoMieScaleHeight = '1.2', atmoMiePhaseG = '0.8' }
local writes, posts = 0, 0
local skyObj = { getID = function() return 7 end, getField = function(_, k) return fields[k] end,
  setField = function(_, k, _, v) fields[k] = v; writes = writes + 1 end, postApply = function() posts = posts + 1 end }
scenetree = { findClassObjects = function(c) return c == 'ScatterSky' and { 'sunsky' } or {} end,
  findObject = function(n) return n == 'sunsky' and skyObj or nil end,
  findObjectById = function(id) return id == 7 and skyObj or nil end }
local clock = 0
os.clock = function() return clock end
H.setStrength(0.5)
H.update(false, 1600)
T.check(writes == 0, 'other levels: untouched')
H.update(true, 1600)
T.check(writes == 4 and posts == 1, 'our level: four fields, one postApply')
clock = 0.5; H.update(true, 1600)
T.check(writes == 4, 'not again before CHECK_S')
clock = 5; H.update(true, 1610)
T.check(writes == 4, '10 m higher: within the tolerance, nothing written')
T.check(H.restore() and fields.atmoMieScattering == '0.003996 0.003996 0.003996' and fields.atmoMiePhaseG == '0.8',
  'restore: the level\'s values back')
T.check(not H.restore(), 'restore twice: nothing to do')

-- grade: every preset has its five constants; off has none
for _, name in ipairs(G.NAMES) do
  local c = G.constsFor(name)
  if name == 'off' then T.check(c == nil, 'off: no constants') else
    local n = 0
    for k, v in pairs(c) do n = n + 1; T.check(select(2, v:gsub('%S+', '')) == 4, name .. ' ' .. k .. ': four numbers') end
    T.check(n == 5, name .. ': five constants')
  end
end
T.check(G.setMode('vivid') and G.getMode() == 'vivid' and not G.setMode('neon') and G.getMode() == 'vivid', 'modes')

-- grade against stubs: created once, enabled in our level with the constants, disabled after
local objects, consts, enabled = {}, {}, false
local function obj(class)
  local o = { class = class, fields = {} }
  function o:setField(k, _, v) self.fields[k] = v end
  function o:registerObject(n) objects[n] = self end
  function o:setShaderConst(k, v) consts[k] = v end
  function o:isEnabled() return enabled end
  function o:enable() enabled = true end
  function o:disable() enabled = false end
  return setmetatable(o, { __newindex = function(t, k, v) rawget(t, 'fields')[k] = v end })
end
createObject = obj
scenetree.findObject = function(n) return objects[n] end
log = function() end
G.setMode('colorado')
G.update(false)
T.check(next(objects) == nil, 'other levels: nothing created')
clock = 100; G.update(true)
local pfx = objects[G.PFX_NAME]
T.check(pfx and pfx.fields.renderBin == 'AfterPostFX' and pfx.fields.texture == '$backBuffer', 'post effect after tone mapping')
T.check(objects.BeamNGEarthGradeShader and objects.BeamNGEarthGradeShader.fields.DXComputShaderName == G.SHADER, 'compute shader')
T.check(enabled and G.isActive() and consts['$gradeTone'] == G.constsFor('colorado')['$gradeTone'], 'on with the constants')
G.update(false)
T.check(not enabled and not G.isActive(), 'leaving the level: off')
clock = 200; G.setMode('off'); G.update(true)
T.check(not enabled, 'mode off: stays off')
T.done('test_look')
