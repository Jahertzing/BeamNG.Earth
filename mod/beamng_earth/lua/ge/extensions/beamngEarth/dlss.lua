-- The DLSS / DLAA key (the user, 2026-10-29: "experiment with DLSS 5 ... a Stream Deck button to toggle it"). DLSS 5
-- (NVIDIA's neural rendering) only runs in games that build it in, and BeamNG 0.40 has not: its DLSS is Super Resolution
-- (Bin64/nvngx_dlss.dll 310.9). The nearest thing the game does live is that DLSS at a render scale of 1, which is DLAA
-- (native resolution, DLSS only for the anti-aliasing). This flips between DLAA and what the player had, through the
-- game's own options (Options > Graphics > Anti-aliasing and Resolution scaling), saved like the menu saves them; what
-- to go back to is saved in the game's settings too, so a restart in DLAA still finds the way back.
local M = {}

M.BACK_SCALE = 'beamngEarthDlssBackScale'
M.BACK_TYPE = 'beamngEarthDlssBackType'
M.DEFAULT_SCALE = 1 / 1.5        -- the game's own default render scale (DLSS Quality)
M.MIN_SCALE = 0.33               -- the game's bounds (core_settings_graphic minRenderScale)
M.DLAA_FROM = 0.999              -- a scale at least this is native: DLAA

local function antialiasOn(st)
  local a = st.getValue('GraphicAntialias')
  return not (a == false or a == nil or tonumber(a) == 0)
end

-- Whether the game offers DLSS now (an NVIDIA card on Direct3D 12, lighting above Lowest); true when it cannot say.
local function dlssAvailable()
  local g = rawget(_G, 'core_settings_graphic')
  local opt = g and g.getOptions and g.getOptions('GraphicAntialiasType')
  if not (opt and opt.getModes) then return true end
  local ok, modes = pcall(opt.getModes)
  if not (ok and modes and modes.keys) then return true end
  for _, k in ipairs(modes.keys) do if k == 'dlss' then return true end end
  return false
end

local function toast(text)
  local m = rawget(_G, 'ui_message')
  if m then pcall(m, text, 4, 'beamngEarthDlss') end
end

-- 'dlaa' when the game renders DLSS at native resolution now, 'dlss' at a lower scale, else nil
function M.state()
  local st = rawget(_G, 'settings')
  if not (st and st.getValue) then return nil end
  if not antialiasOn(st) or st.getValue('GraphicAntialiasType') ~= 'dlss' then return nil end
  local scale = tonumber(st.getValue('GraphicUpscalingQuality')) or M.DEFAULT_SCALE
  return scale >= M.DLAA_FROM and 'dlaa' or 'dlss'
end

-- DLAA on, or back to what the player had. Returns the state now ('dlaa', 'dlss' or the anti-aliasing gone back to),
-- or nil when nothing changed (no game settings, or no DLSS on this machine).
function M.toggleDlaa()
  local st = rawget(_G, 'settings')
  if not (st and st.getValue and st.setValue) then return nil end
  local now = M.state()
  if now == 'dlaa' then
    local backType = st.getValue(M.BACK_TYPE)
    local other = type(backType) == 'string' and backType ~= '' and backType ~= 'dlss' and backType or nil
    local scale = math.max(M.MIN_SCALE, math.min(1, tonumber(st.getValue(M.BACK_SCALE)) or M.DEFAULT_SCALE))
    if not other and scale >= M.DLAA_FROM then scale = M.DEFAULT_SCALE end     -- (DLSS back at a real upscale)
    if other then
      pcall(st.setValue, 'GraphicAntialiasType', other)
      pcall(st.setValue, M.BACK_TYPE, '')
    end
    pcall(st.setValue, 'GraphicUpscalingQuality', scale)
    if other then
      toast(string.format('DLAA off: back to %s at %d%% render scale', other, math.floor(scale * 100 + 0.5)))
      if st.notifyUI then pcall(st.notifyUI) end
      return other
    end
    toast(string.format('DLAA off: DLSS at %d%% render scale', math.floor(scale * 100 + 0.5)))
    if st.notifyUI then pcall(st.notifyUI) end
    return 'dlss'
  end
  if not dlssAvailable() then
    toast('DLSS is not available here (it needs an NVIDIA RTX card on Direct3D 12)')
    return nil
  end
  pcall(st.setValue, M.BACK_SCALE, tonumber(st.getValue('GraphicUpscalingQuality')) or M.DEFAULT_SCALE)
  if now == 'dlss' then
    pcall(st.setValue, M.BACK_TYPE, '')
  else                               -- another anti-aliasing (or none): DLAA, and that one back after
    local t = st.getValue('GraphicAntialiasType')
    pcall(st.setValue, M.BACK_TYPE, antialiasOn(st) and type(t) == 'string' and t or 'off')
    pcall(st.setValue, 'GraphicAntialiasType', 'dlss')
  end
  pcall(st.setValue, 'GraphicUpscalingQuality', 1)
  toast('DLAA on: DLSS at native resolution (DLSS 5 is not in BeamNG yet)')
  if st.notifyUI then pcall(st.notifyUI) end
  return 'dlaa'
end

return M
