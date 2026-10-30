-- The graphics keys (the user, 2026-10-30: "its own stream deck page with many controls for various options (including a
-- fully open the settings menu if i need)"): render scale and sharpening steps, the anti-aliasing / upscaler in turn,
-- motion blur, depth of field, reflections and ambient occlusion on / off, and the game's Options (or its Graphics tab).
-- Each one goes through the game's own setting (core_settings_graphic applies it live, saved like the Options menu
-- saves it) and says what it did in a toast. The DLAA key is dlss.lua.
local M = {}

M.SCALE_STEP, M.MIN_SCALE, M.MAX_SCALE = 0.05, 0.33, 1   -- (the game's bounds: core_settings_graphic minRenderScale)
M.DEFAULT_SCALE = 1 / 1.5
M.SHARP_STEP = 0.1

-- the anti-aliasing names in toasts
M.AA_NAMES = { dlss = 'DLSS', fsr3 = 'FSR 3', fsr4 = 'FSR 4', xess = 'XeSS', fsrCustom = 'FSR (custom)',
  smaaT2x = 'SMAA T2x', smaa = 'SMAA', fxaa = 'FXAA', pssr = 'PSSR', off = 'off' }
-- the upscalers the sharpening applies to (RenderView.isUpscaleSharpeningSupported)
local SHARPENED = { dlss = true, fsr3 = true, fsr4 = true, xess = true, fsrCustom = true, pssr = true }

-- the post effects with a key: the game's setting, the toast's name, its value when the settings have none
M.POSTFX = {
  motionBlur = { key = 'PostFXMotionBlurEnabled', name = 'Motion blur', default = false },
  dof = { key = 'PostFXDOFGeneralEnabled', name = 'Depth of field', default = true },
  ssr = { key = 'PostFXSSREnabled', name = 'Screen-space reflections', default = true },
  ssao = { key = 'PostFXSSAOGeneralEnabled', name = 'Ambient occlusion', default = true },
}

local function gameSettings()
  local st = rawget(_G, 'settings')
  if st and st.getValue and st.setValue then return st end
end

local function toast(text)
  local m = rawget(_G, 'ui_message')
  if m then pcall(m, text, 3, 'beamngEarthGfx') end
end

local function notify(st)
  if st.notifyUI then pcall(st.notifyUI) end
end

local function pct(v) return math.floor(v * 100 + 0.5) end

local function antialiasOn(st)
  local a = st.getValue('GraphicAntialias')
  return not (a == false or a == nil or tonumber(a) == 0)
end

local function currentAA(st)
  if not antialiasOn(st) then return 'off' end
  local t = st.getValue('GraphicAntialiasType')
  return type(t) == 'string' and t or 'off'
end

-- The render scale a step up (dir 1) or down (-1), on the 5 % grid (0.77 goes to 0.80 or 0.75). Returns the new scale,
-- or nil without the game's settings.
function M.stepScale(dir)
  local st = gameSettings()
  if not st then return nil end
  local now = tonumber(st.getValue('GraphicUpscalingQuality')) or M.DEFAULT_SCALE
  local grid = now / M.SCALE_STEP
  local n = dir > 0 and math.floor(grid + 1e-6) + 1 or math.ceil(grid - 1e-6) - 1
  local scale = math.max(M.MIN_SCALE, math.min(M.MAX_SCALE, n * M.SCALE_STEP))
  scale = math.floor(scale * 1000 + 0.5) / 1000
  pcall(st.setValue, 'GraphicUpscalingQuality', scale)
  local aa = currentAA(st)
  if aa == 'off' then
    toast(string.format('Render scale %d%% (anti-aliasing is off, so it renders native)', pct(scale)))
  elseif scale >= 0.999 and aa == 'dlss' then
    toast('Render scale 100%: DLAA')
  else
    toast(string.format('Render scale %d%% (%s)', pct(scale), M.AA_NAMES[aa] or aa))
  end
  notify(st)
  return scale
end

-- The upscaler's sharpening a step up or down (0 off to 1). Returns the new value, or nil without the game's settings.
function M.stepSharpness(dir)
  local st = gameSettings()
  if not st then return nil end
  local now = tonumber(st.getValue('GraphicUpscalingSharpness')) or 0
  local v = math.floor((now + (dir > 0 and M.SHARP_STEP or -M.SHARP_STEP)) * 10 + 0.5) / 10
  v = math.max(0, math.min(1, v))
  pcall(st.setValue, 'GraphicUpscalingSharpness', v)
  local aa = currentAA(st)
  if SHARPENED[aa] then
    toast(v == 0 and 'Sharpening off' or string.format('Sharpening %d%%', pct(v)))
  else
    toast(string.format('Sharpening %d%% (only DLSS, FSR and XeSS use it; now %s)', pct(v), M.AA_NAMES[aa] or aa))
  end
  notify(st)
  return v
end

-- The anti-aliasing modes the game offers here, in the Options menu's order, without off.
function M.availableAA()
  local g = rawget(_G, 'core_settings_graphic')
  local opt = g and g.getOptions and g.getOptions('GraphicAntialiasType')
  if not (opt and opt.getModes) then return {} end
  local ok, modes = pcall(opt.getModes)
  local out = {}
  if ok and modes and modes.keys then
    for _, k in ipairs(modes.keys) do if k ~= 'off' then out[#out + 1] = k end end
  end
  return out
end

-- The next anti-aliasing / upscaler the game offers (DLSS, FSR, XeSS, SMAA T2x, SMAA, FXAA, ...), round again after the
-- last. Returns its key, or nil when there is nothing to switch to.
function M.cycleAntialias()
  local st = gameSettings()
  if not st then return nil end
  local list = M.availableAA()
  if #list == 0 then return nil end
  local now, nextKey = currentAA(st), list[1]
  for i, k in ipairs(list) do
    if k == now then nextKey = list[i % #list + 1] break end
  end
  if nextKey == now then return nil end
  pcall(st.setValue, 'GraphicAntialiasType', nextKey)
  toast('Anti-aliasing: ' .. (M.AA_NAMES[nextKey] or nextKey))
  notify(st)
  return nextKey
end

-- One of M.POSTFX on or off. Returns the state now, or nil (no game settings, or no such effect).
function M.togglePostFx(which)
  local st, fx = gameSettings(), M.POSTFX[which]
  if not (st and fx) then return nil end
  local v = st.getValue(fx.key)
  if v == nil then v = fx.default end
  local on = not (v == true or v == 1 or v == 'true')
  pcall(st.setValue, fx.key, on)
  toast(fx.name .. (on and ' on' or ' off'))
  notify(st)
  return on
end

-- The game's Options, on one tab when category is given ('graphics', 'display', ...: the categoryId values of the
-- game's ui/ui-vue/src/modules/options/runtime/layout.json). In a level that is the pause menu's Options route; the
-- stock "options" action's way (ui_menuManager.MenuOpenModule) when that fails or without a category.
function M.openOptions(category)
  local r = rawget(_G, 'ui_router')
  if category and r and r.navigate then
    local ok = pcall(r.navigate, 'pause.options.category', { category = category })
    if ok then return true end
  end
  local mm = rawget(_G, 'ui_menuManager')
  if mm and mm.MenuOpenModule then
    return (pcall(mm.MenuOpenModule, 'options'))
  end
  local gh = rawget(_G, 'guihooks')
  if gh then gh.trigger('MenuOpenModule', 'options') return true end
  return false
end

return M
