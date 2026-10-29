-- Where the Lua heap goes (2026-10-29: "LuaGC 144 ms" hitches while driving, the heap at 220-233 MB). LuaJIT's
-- incremental collector does most of its work in small per-frame steps, but its atomic phase is one step: it goes
-- again through every table written to during the cycle, whole. Big tables the mod keeps writing (and a big heap) make
-- that step long. This walks what the mod's modules hold (their tables and their functions' upvalues: most state is
-- in chunk locals) and estimates the bytes each module keeps, with the largest tables by entries, for the log:
--   "Lua heap 231 MB, the mod's ~120 MB: trees 61 MB (210k tables), roads 30 MB, ... | biggest tables: trees.tiles 1.2M ..."
-- Estimates only (a table's header, array and hash slots; strings once each; cdata by ffi.sizeof), to rank the owners.
-- Once a session, after the first LuaGC hitch of TRIGGER_MS (core perfFrame); console: require('beamngEarth/census').run()
local M = {}

M.TRIGGER_MS = 100
M.MAX_ITEMS = 6000000      -- (a stop: the walk visits at most this many values)
M.PATH_DEPTH = 3           -- table paths are named this deep ("trees.tiles.<k>"), deeper ones by that prefix

local TAB, SLOT_A, SLOT_H, STR, FN, UPV = 64, 8, 24, 24, 40, 32
local done = false

local function pow2(n)
  local p = 1
  while p < n do p = p * 2 end
  return p
end

local function keyName(k)
  local tk = type(k)
  if tk == 'string' then return #k <= 24 and k or (k:sub(1, 21) .. '...') end
  if tk == 'number' then return '<n>' end
  return '<' .. tk .. '>'
end

-- roots: { {name, value}, ... } (a value: the module's table). Returns { total = bytes, byRoot = {{name, bytes,
-- tables}}, biggest = {{path, entries}}, items = n, truncated = bool }.
function M.measure(roots, opts)
  opts = opts or {}
  local maxItems = opts.maxItems or M.MAX_ITEMS
  local depthMax = opts.pathDepth or M.PATH_DEPTH
  local getup = rawget(_G, 'debug') and debug.getupvalue
  local okF, ffi = pcall(require, 'ffi')
  local seen = {}
  for _, x in ipairs(opts.exclude or {}) do seen[x] = true end
  local items, total, truncated = 0, 0, false
  local byRoot, biggest = {}, {}
  local minBig = opts.minBig or 20000

  for _, root in ipairs(roots) do
    local name, bytes, tables = root[1], 0, 0
    local stack, sp = { root[2], name, 0 }, 3        -- (value, path, depth) triples
    while sp > 0 and not truncated do
      local v, path, depth = stack[sp - 2], stack[sp - 1], stack[sp]
      stack[sp - 2], stack[sp - 1], stack[sp] = nil, nil, nil
      sp = sp - 3
      local tv = type(v)
      if (tv == 'table' or tv == 'function' or tv == 'userdata' or tv == 'cdata' or tv == 'thread'
          or (tv == 'string' and #v >= 32)) and not seen[v] then
        seen[v] = true
        items = items + 1
        if items > maxItems then truncated = true break end
        if tv == 'string' then
          bytes = bytes + STR + #v
        elseif tv == 'table' then
          tables = tables + 1
          local n, a = 0, #v
          for k, x in next, v do
            n = n + 1
            local tk, tx = type(k), type(x)
            if tx == 'string' then
              if #x < 32 then bytes = bytes + STR + #x / 2 else
                sp = sp + 3; stack[sp - 2], stack[sp - 1], stack[sp] = x, path, depth + 1 end
            elseif tx ~= 'number' and tx ~= 'boolean' then
              sp = sp + 3
              stack[sp - 2], stack[sp - 1] = x, depth < depthMax and (path .. '.' .. keyName(k)) or path
              stack[sp] = depth + 1
            end
            if tk ~= 'number' and tk ~= 'string' and tk ~= 'boolean' then
              sp = sp + 3; stack[sp - 2], stack[sp - 1], stack[sp] = k, path, depth + 1
            end
          end
          if a > n then a = n end
          bytes = bytes + TAB + a * SLOT_A + (n > a and pow2(n - a) * SLOT_H or 0)
          if n >= minBig then biggest[#biggest + 1] = { path, n } end
          local mt = getmetatable(v)
          if type(mt) == 'table' then sp = sp + 3; stack[sp - 2], stack[sp - 1], stack[sp] = mt, path, depth + 1 end
        elseif tv == 'function' then
          bytes = bytes + FN
          if getup then
            for i = 1, 255 do
              local okU, un, uv = pcall(getup, v, i)
              if not okU or un == nil then break end
              bytes = bytes + UPV
              local tu = type(uv)
              if tu ~= 'number' and tu ~= 'boolean' and tu ~= 'nil' then
                sp = sp + 3
                stack[sp - 2], stack[sp - 1] = uv, depth < depthMax and (path .. ':' .. tostring(un)) or path
                stack[sp] = depth + 1
              end
            end
          end
        elseif tv == 'cdata' then
          local okS, sz = okF and pcall(ffi.sizeof, v)
          bytes = bytes + 16 + ((okS and type(sz) == 'number') and sz or 0)
        else
          bytes = bytes + 32
        end
      end
    end
    total = total + bytes
    byRoot[#byRoot + 1] = { name, bytes, tables }
    if truncated then break end
  end
  table.sort(byRoot, function(a, b) return a[2] > b[2] end)
  table.sort(biggest, function(a, b) return a[2] > b[2] end)
  return { total = total, byRoot = byRoot, biggest = biggest, items = items, truncated = truncated }
end

-- The mod's modules: package.loaded['beamngEarth/...'] and the extensions' globals beamngEarth_*.
function M.roots()
  local out, have = {}, {}
  for k, v in pairs(package.loaded) do
    if type(k) == 'string' and type(v) == 'table' and k:find('^beamngEarth/') and not have[v] then
      have[v] = true
      out[#out + 1] = { k:sub(13), v }
    end
  end
  for k, v in pairs(_G) do
    if type(k) == 'string' and type(v) == 'table' and k:find('^beamngEarth_') and not have[v] then
      have[v] = true
      out[#out + 1] = { k, v }
    end
  end
  table.sort(out, function(a, b) return a[1] < b[1] end)
  return out
end

local function mb(b) return b / 1048576 end
local function count(n)
  if n >= 1e6 then return string.format('%.1fM', n / 1e6) end
  if n >= 1e3 then return string.format('%.0fk', n / 1e3) end
  return tostring(n)
end

-- What the game holds itself (its globals and other modules), not walked: a module's `local extensions = extensions`
-- would otherwise count the whole game as the mod's.
function M.excluded(roots)
  local mine, out = {}, { _G, package, package.loaded }
  for _, r in ipairs(roots) do mine[r[2]] = true end
  for _, src in ipairs({ _G, package.loaded }) do
    for _, v in pairs(src) do
      local tv = type(v)
      if (tv == 'table' or tv == 'function' or tv == 'userdata') and not mine[v] then out[#out + 1] = v end
    end
  end
  return out
end

-- One log line (and the result).
function M.run(opts)
  local t0 = os.clock()
  local roots = M.roots()
  opts = opts or {}
  opts.exclude = opts.exclude or M.excluded(roots)
  local r = M.measure(roots, opts)
  local parts = {}
  for i = 1, math.min(10, #r.byRoot) do
    local e = r.byRoot[i]
    if e[2] >= 262144 then parts[#parts + 1] = string.format('%s %.1f MB (%s tables)', e[1], mb(e[2]), count(e[3])) end
  end
  local big = {}
  for i = 1, math.min(8, #r.biggest) do big[i] = r.biggest[i][1] .. ' ' .. count(r.biggest[i][2]) end
  local line = string.format('Lua heap %.0f MB, the mod\'s ~%.0f MB%s: %s | biggest tables: %s | (walk %.0f ms)',
    collectgarbage('count') / 1024, mb(r.total), r.truncated and ' (walk cut short)' or '',
    table.concat(parts, ', '), #big > 0 and table.concat(big, ', ') or 'none over 20k entries', (os.clock() - t0) * 1000)
  r.line = line
  return r
end

-- core: after a hitch, its engine parts; the first LuaGC one of TRIGGER_MS gets the census (once a session).
function M.afterHitch(engine)
  if done or type(engine) ~= 'table' or (tonumber(engine.LuaGC) or 0) < M.TRIGGER_MS then return nil end
  done = true
  local ok, r = pcall(M.run)
  return ok and r.line or ('Lua heap census failed: ' .. tostring(r))
end

function M.reset() done = false end

return M
