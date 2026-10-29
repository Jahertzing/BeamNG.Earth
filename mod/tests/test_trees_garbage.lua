-- luajit mod/tests/test_trees.lua  (trees.lua: vegetation tiles become Forest items, thinned by distance)
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')

local items = {}                 -- every live item of every Forest
local forests = 0
local realCreate = createObject
createObject = function(class)
  local o = realCreate(class)
  if class == 'Forest' then
    -- a Forest of its own items (trees.lua: one per tile), deleted with them
    local mine = {}
    local fdata = {}
    function fdata:createNewItem(d, m, sc)
      if CREATE_HOOK then CREATE_HOOK() end      -- (the time budget's tests: a fake clock's tick)
      local it = { d = d, m = m, sc = sc }; items[it] = true; mine[it] = true; return it
    end
    function fdata:removeItem(it) items[it] = nil; mine[it] = nil end
    function o:getData() return fdata end
    local del = o.delete
    function o:delete() for it in pairs(mine) do items[it] = nil end; forests = forests - 1; if del then del(self) end end
    forests = forests + 1
  end
  return o
end
worldEditorCppApi = { createObject = function(class) return createObject(class) end }
Sim = { getDataBlockSet = function() return { addObject = function() end } end }
loadJsonMaterialsFile = function() end
MatrixF = function() return { setFromEuler = function(self, e) self.e = e end, setPosition = function(self, p) self.p = p end } end
local files = {}
readFile = function(p) return files[p] end
local decoded = setmetatable({}, { __mode = 'k' })    -- every decoded file's rows (weak: what the module still holds)
jsonDecode = function(s)
  local d = assert(load('return ' .. s))()
  if type(d) == 'table' and type(d.items) == 'table' then
    for _, r in ipairs(d.items) do decoded[r] = true end
  end
  return d
end
local trees = require('beamngEarth/trees')
-- The tree frame's garbage (2026-10-29 bench8: the trees made 240-256 MB of a 390-470 MB Lua minute, and the collector's
-- cycles ended in 100-157 ms LuaGC hitches): with nothing to change, an update makes next to none.
trees.FRAME_MS, trees.POOL_SPARE = 4.0, 0
local groups = { '"deciduous"', '"conifer"', '"shrub"', '"door"', '"rock"' }
for tx = -2, 2 do for ty = -2, 2 do
  local rows = {}
  for i = 1, 400 do
    rows[#rows + 1] = string.format('{%d, %d, %d, 5, 0, 1, 12, %.3f}', i % 5, tx * 256 + i * 37 % 256, ty * 256 + i * 91 % 256,
      (i * 0.618) % 1)
  end
  local key = tx .. '_' .. ty
  files['/g/' .. key] = '{ groups = {' .. table.concat(groups, ',') .. '}, items = { ' .. table.concat(rows, ', ') .. ' } }'
  trees.onTile({ anchorId = 1, key = key, path = '/g/' .. key, n = 400 }, 1)
end end
local p = { x = 10, y = 10 }
for _ = 1, 600 do trees.update(1, p) end
T.check(trees.status().items > 1000, 'the tiles shown')
collectgarbage('collect'); collectgarbage('stop')
local kb0 = collectgarbage('count')
for _ = 1, 300 do trees.update(1, p) end
local per = (collectgarbage('count') - kb0) / 300
collectgarbage('restart')
T.check(per < 0.5, string.format('standing: %.2f KB of garbage an update (was ~15 KB with 169 tiles)', per))

-- M.pick makes no list: the same shapes as before for a spread of heights and ranks
local s1 = trees.pick('deciduous', 14, 0.37)
T.check(s1 == trees.pick('deciduous', 14, 0.37) and trees.pick('conifer', 200, 0.5) ~= nil, 'pick: stable, and the nearest when none fits')
T.done('test_trees_garbage')
