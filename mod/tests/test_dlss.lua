-- luajit mod/tests/test_dlss.lua  (the DLAA key, Ctrl+Alt+U: the game's DLSS at native resolution, or back)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local D = require('beamngEarth/dlss')

local values, toasts, modes = {}, {}, { 'off', 'fxaa', 'smaa', 'dlss' }
settings = { getValue = function(k) return values[k] end, setValue = function(k, v) values[k] = v end }
ui_message = function(text) toasts[#toasts + 1] = text end
core_settings_graphic = { getOptions = function(name)
  if name == 'GraphicAntialiasType' then return { getModes = function() return { keys = modes } end } end
end }

do -- DLSS at a lower scale: DLAA, then the same scale back
  values = { GraphicAntialias = 3, GraphicAntialiasType = 'dlss', GraphicUpscalingQuality = 0.77 }
  T.check(D.state() == 'dlss', 'DLSS at 77%: dlss')
  T.check(D.toggleDlaa() == 'dlaa' and values.GraphicUpscalingQuality == 1 and values.GraphicAntialiasType == 'dlss',
    'DLAA: DLSS at a scale of 1')
  T.check(D.state() == 'dlaa' and toasts[#toasts]:find('DLAA on', 1, true), 'state dlaa, a toast')
  T.check(D.toggleDlaa() == 'dlss' and values.GraphicUpscalingQuality == 0.77, 'again: the 77% back')
end
do -- already DLAA from the menu, nothing saved: back to the game's default scale
  values = { GraphicAntialias = 3, GraphicAntialiasType = 'dlss', GraphicUpscalingQuality = 1 }
  T.check(D.toggleDlaa() == 'dlss' and math.abs(values.GraphicUpscalingQuality - D.DEFAULT_SCALE) < 1e-9,
    'DLAA with nothing to go back to: DLSS Quality')
end
do -- another anti-aliasing: DLAA, then that one back at its own scale
  values = { GraphicAntialias = 4, GraphicAntialiasType = 'smaa', GraphicUpscalingQuality = 1 }
  T.check(D.state() == nil, 'SMAA: not DLSS')
  T.check(D.toggleDlaa() == 'dlaa' and values.GraphicAntialiasType == 'dlss' and values.GraphicUpscalingQuality == 1,
    'SMAA to DLAA')
  T.check(D.toggleDlaa() == 'smaa' and values.GraphicAntialiasType == 'smaa' and values.GraphicUpscalingQuality == 1,
    'back to SMAA, native (not stretched from the DLSS default)')
  T.check(values[D.BACK_TYPE] == '', 'the type to go back to cleared')
  T.check(D.toggleDlaa() == 'dlaa' and D.toggleDlaa() == 'smaa', 'and again')
end
do -- anti-aliasing off: DLAA, then off again
  values = { GraphicAntialias = 0, GraphicAntialiasType = 'dlss', GraphicUpscalingQuality = 1 }
  T.check(D.state() == nil, 'anti-aliasing off: not DLSS, even with dlss saved')
  T.check(D.toggleDlaa() == 'dlaa' and values[D.BACK_TYPE] == 'off', 'off to DLAA, off remembered')
end
do -- no DLSS on this machine (DX11, another card): nothing changed
  modes = { 'off', 'fxaa', 'smaa' }
  values = { GraphicAntialias = 4, GraphicAntialiasType = 'smaa', GraphicUpscalingQuality = 1 }
  T.check(D.toggleDlaa() == nil and values.GraphicAntialiasType == 'smaa', 'no DLSS: nothing')
  T.check(toasts[#toasts]:find('not available', 1, true), 'and a toast that says why')
  modes = { 'off', 'dlss' }
end
settings = nil
T.check(D.toggleDlaa() == nil and D.state() == nil, 'no game settings: nothing')
T.done('test_dlss')
