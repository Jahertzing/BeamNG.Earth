-- luajit mod/tests/test_navbuild.lua  (navbuild.lua: the game's map.reset(customNodes) over several frames)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/stubs.lua')
local NB = require('beamngEarth/navbuild')
-- the game's sandbox refuses debug.setupvalue ("function not allowed by sandbox", in game 2026-10-29)
debug.setupvalue = function() error('function not allowed by sandbox') end
local vmt = getmetatable(vec3(0, 0, 0))
local vidx = type(vmt.__index) == 'table' and vmt.__index or vmt
vidx.distance = vidx.distance or function(a, b) return math.sqrt((a.x - b.x) ^ 2 + (a.y - b.y) ^ 2 + (a.z - b.z) ^ 2) end

-- A stand-in for the game's map.lua with loadMap's locals by the same names (checked against the real 0.40 file
-- offline, 2026-10-29: byte-identical mailbox data and normals for 2.4k and 12k node towns)
local calls = { drv = 0, normal = 0, mail = {}, hook = 0, rules = 0 }
be = { getTerrainDrivability = function() calls.drv = calls.drv + 1; return 1 end,
  sendToMailbox = function(_, k, v) calls.mail[#calls.mail + 1] = k end }
lpack = { encodeBinWorkBuffer = function(t) return t end }
extensions = { hook = function(n) if n == 'onNavgraphReloaded' then calls.hook = calls.hook + 1 end end }
local function fakeMapModule()
  local M = {}
  local map = { nodes = { old = { pos = vec3(0, 0, 0), radius = 1, links = {} } } }
  local gp, edgeKdTree, nodeKdTree, maxRadius, rules = { old = true }, { old = true }, { old = true }, 10, nil
  local isEditorEnabled = 'editor state'
  local buildSerial = 3
  local trafficExclusionZones = {}
  local graphpath = { newGraphpath = function(n)
    local g = { graph = {}, n = n, edges = 0 }
    function g:setPointPositionRadius(id) self.graph[id] = self.graph[id] or {} end
    function g:bidiEdge(a, b)
      self.edges = self.edges + 1
      self.graph[a] = self.graph[a] or {}; self.graph[a][b] = { eId = self.edges }
    end
    function g:generateEdgeIndex() self.indexed = true end
    function g:export(n2) return { edges = n2 } end
    return g
  end }
  local kdTreeBox2D = { new = function(n)
    local k = { n = n, items = 0 }
    function k:preLoad() self.items = self.items + 1 end
    function k:build() self.built = true end
    function k:export() return { items = self.items } end
    return k
  end }
  local quadtree = { lineBBox = function() return 0, 0, 1, 1 end }
  local pointBBox = function() return 0, 0, 1, 1 end
  local function setRoadRules() calls.rules = calls.rules + 1; rules = { rightHandDrive = false } end
  local function surfaceNormal() calls.normal = calls.normal + 1; return vec3(0, 0, 1) end
  local function loadMap(custom)        -- (references every local navbuild sets or reads, as the real one does)
    local _ = { map, gp, edgeKdTree, nodeKdTree, maxRadius, buildSerial, rules, setRoadRules, surfaceNormal, graphpath,
      kdTreeBox2D, quadtree, pointBBox, trafficExclusionZones, custom }
  end
  M.reset = loadMap
  M.onSerialize = function() return { isEditorEnabled, buildSerial } end
  M.onDeserialize = function(st) isEditorEnabled, buildSerial = st[1], st[2] end
  M.editor = function() return isEditorEnabled end
  M.getMap = function() return map end
  M.state = function() return gp, edgeKdTree, nodeKdTree, maxRadius, buildSerial, rules end
  M.createTrafficExclusionArea = function(z) M.zonesGiven = z end
  return M
end

local function town(n)
  local nodes = {}
  local function node(name, x, y)
    nodes[name] = nodes[name] or { pos = vec3(x, y, 0), radius = 4, links = {} }
    return nodes[name]
  end
  for i = 0, n - 1 do
    local a, b = node('n' .. i, i * 30, 0), node('n' .. (i + 1), (i + 1) * 30, 0)
    local e = { drivability = 1, inNode = 'n' .. i, outNode = 'n' .. (i + 1), inPos = a.pos, outPos = b.pos,
      inRadius = 4, outRadius = 4, lanes = '-+', speedLimit = 11 }
    a.links['n' .. (i + 1)], b.links['n' .. i] = e, e
  end
  return nodes
end

local mm = fakeMapModule()
local I, miss = NB.internals(mm)
T.check(I ~= nil, 'the internals found: ' .. tostring(miss))
local oldNodes = mm.getMap().nodes
local gpObj, ekObj, nkObj = mm.state()
local nodes = town(200)
local job = NB.new(nodes, I)
local steps = 0
local function stepper()      -- (a fake clock: each step's budget ends after one resume)
  return function() return 1e9 end
end
local done
local err
repeat done, err = NB.step(job, 1, stepper()); steps = steps + 1 until done ~= false or steps > 1000
T.check(err == nil, 'no error: ' .. tostring(err))
T.check(done == true and steps > 5, 'built over several steps: ' .. steps)
T.check(calls.drv == 201 and calls.normal == 201, 'one drivability query and one normal a node')
local gp0 = mm.state()
T.check(gp0.old and mm.getMap().nodes == oldNodes and #calls.mail == 0 and calls.hook == 0,
  'the game\'s map left as it was until the commit')
local single = 0
for _, n in pairs(nodes) do for _ in pairs(n.links) do single = single + 1 end end
T.check(single == 200, 'links single-sided, as map.reset makes them: ' .. single)
T.check(NB.commit(job) == true, 'committed')
local gp, ek, nk, mr, serial, rules = mm.state()
T.check(gp == gpObj and ek == ekObj and nk == nkObj and gp.indexed and not gp.old and ek.built and nk.built and mr == 10,
  'the new graph and trees in map.lua\'s own tables (filled in place)')
T.check(gp:generateEdgeIndex() == nil and gp.n == 201, '... with their methods')
T.check(mm.editor() == 'editor state', 'the editor state kept by the serial\'s setter')
T.check(mm.getMap().nodes == nodes and next(oldNodes) == nil and mm.getMap().buildSerial == 4 and serial == 4,
  'the new nodes, the old table emptied, the serial on')
T.check(calls.mail[1] == 'mapData' and calls.mail[2] == 'updateDrivabilities' and calls.hook == 1 and rules,
  'the vehicles\' copy sent, the road rules set, onNavgraphReloaded fired')
T.check(mm.zonesGiven ~= nil, 'the traffic exclusion zones made again')

-- a graph with a wider road than the radius map.lua has: not this time, nothing changed (map.reset)
local wide = town(3)
wide.n0.radius = 20
local job2 = NB.new(wide, I)
repeat done = NB.step(job2, 1, stepper()) until done ~= false
local ok2, why2 = NB.commit(job2)
T.check(ok2 == false and why2:find('radius') and mm.getMap().nodes == nodes, 'a larger radius: map.reset this once')

-- a map.lua without those locals: nothing (navgraph keeps map.reset)
local other = { reset = function() return 1 end }
local I2, miss2 = NB.internals(other)
T.check(I2 == nil and miss2 == 'map' and NB.new(town(2), I2 or false) == nil, 'another map.lua: no job, ' .. tostring(miss2))
T.done('test_navbuild')
