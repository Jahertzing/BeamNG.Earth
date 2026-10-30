-- luajit mod/tests/test_gfxkeys.lua  (the graphics keys, Ctrl+Alt+1..0: render scale, sharpening, anti-aliasing,
-- post effects, the game's Options)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local G = require('beamngEarth/gfxkeys')

local values, toasts, modes = {}, {}, { 'off', 'fxaa', 'smaa', 'smaaT2x', 'dlss' }
settings = { getValue = function(k) return values[k] end, setValue = function(k, v) values[k] = v end }
ui_message = function(text) toasts[#toasts + 1] = text end
core_settings_graphic = { getOptions = function(name)
  if name == 'GraphicAntialiasType' then return { getModes = function() return { keys = modes } end } end
end }
local function near(a, b) return a and math.abs(a - b) < 1e-9 end

do -- render scale: on the 5 % grid, inside the game's bounds
  values = { GraphicAntialias = 3, GraphicAntialiasType = 'dlss', GraphicUpscalingQuality = 0.77 }
  T.check(near(G.stepScale(1), 0.8) and near(values.GraphicUpscalingQuality, 0.8), '0.77 up: 0.80')
  values.GraphicUpscalingQuality = 0.77
  T.check(near(G.stepScale(-1), 0.75), '0.77 down: 0.75')
  T.check(near(G.stepScale(-1), 0.7), '0.75 down: 0.70 (on the grid, a whole step)')
  values.GraphicUpscalingQuality = 0.95
  T.check(near(G.stepScale(1), 1) and toasts[#toasts]:find('DLAA', 1, true), 'up to 100 % with DLSS: DLAA')
  T.check(near(G.stepScale(1), 1), 'no higher than 1')
  values.GraphicUpscalingQuality = 0.35
  T.check(near(G.stepScale(-1), G.MIN_SCALE), 'no lower than the game\'s 0.33')
  values.GraphicUpscalingQuality = nil
  T.check(near(G.stepScale(1), 0.7), 'unset (the game\'s 2/3): up to 0.70')
  values.GraphicAntialias = 0
  G.stepScale(-1)
  T.check(toasts[#toasts]:find('native', 1, true), 'anti-aliasing off: the toast says the scale does nothing')
end
do -- sharpening: 10 % steps, 0 to 1, a word when the mode ignores it
  values = { GraphicAntialias = 3, GraphicAntialiasType = 'dlss' }
  T.check(near(G.stepSharpness(1), 0.1), 'unset up: 10 %')
  T.check(near(G.stepSharpness(-1), 0) and toasts[#toasts] == 'Sharpening off', 'down to off')
  T.check(near(G.stepSharpness(-1), 0), 'no lower than off')
  values.GraphicUpscalingSharpness = 0.95
  T.check(near(G.stepSharpness(1), 1), 'no higher than 1')
  values.GraphicAntialiasType = 'smaa'
  G.stepSharpness(-1)
  T.check(toasts[#toasts]:find('only DLSS', 1, true), 'SMAA: the toast says it does not sharpen')
end
do -- anti-aliasing in turn, the game's list without off
  values = { GraphicAntialias = 3, GraphicAntialiasType = 'smaaT2x' }
  T.check(G.cycleAntialias() == 'dlss' and values.GraphicAntialiasType == 'dlss', 'SMAA T2x: DLSS next')
  T.check(G.cycleAntialias() == 'fxaa', 'after the last: the first again')
  values = { GraphicAntialias = 0, GraphicAntialiasType = 'dlss' }
  T.check(G.cycleAntialias() == 'fxaa', 'off: the first')
  modes = { 'off', 'fxaa' }
  values = { GraphicAntialias = 3, GraphicAntialiasType = 'fxaa' }
  T.check(G.cycleAntialias() == nil, 'only one mode: nothing to switch to')
  modes = { 'off' }
  T.check(G.cycleAntialias() == nil, 'no modes: nothing')
end
do -- the post effects
  values = {}
  T.check(G.togglePostFx('motionBlur') == true and values.PostFXMotionBlurEnabled == true, 'motion blur unset (off): on')
  T.check(G.togglePostFx('motionBlur') == false, 'and off again')
  T.check(G.togglePostFx('dof') == false and values.PostFXDOFGeneralEnabled == false, 'depth of field unset (on): off')
  values.PostFXSSREnabled = true
  T.check(G.togglePostFx('ssr') == false and toasts[#toasts] == 'Screen-space reflections off', 'reflections off')
  T.check(G.togglePostFx('ssao') == false, 'ambient occlusion unset (on): off')
  T.check(G.togglePostFx('bloom') == nil, 'no such effect: nothing')
end
do -- the Options: the Graphics tab through the router, else the stock way
  local routed, opened = {}, {}
  ui_router = { navigate = function(name, params) routed[#routed + 1] = { name, params } end }
  ui_menuManager = { MenuOpenModule = function(d) opened[#opened + 1] = d end }
  T.check(G.openOptions('graphics') and routed[1][1] == 'pause.options.category' and routed[1][2].category == 'graphics',
    'the Graphics tab through the router')
  T.check(G.openOptions() and opened[1] == 'options' and #routed == 1, 'all the Options: the stock way')
  ui_router = { navigate = function() error('no such route') end }
  T.check(G.openOptions('graphics') and opened[2] == 'options', 'the router fails: the stock way')
  ui_router, ui_menuManager = nil, nil
end
settings = nil
T.check(G.stepScale(1) == nil and G.stepSharpness(1) == nil and G.cycleAntialias() == nil and G.togglePostFx('dof') == nil,
  'no game settings: nothing')
T.done('test_gfxkeys')
