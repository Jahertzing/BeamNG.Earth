-- The game's map.reset(customNodes) over several frames (Jeff's drive 2026-10-29: "definitely some stutters"; 28 frames
-- of 100-872 ms in 16 min round Frederick, every one navgraph's apply). The game's map.lua loadMap builds the AI road
-- graph in one go: for every node an engine drivability query and a surface normal from three raycasts down onto the
-- static collision (~25k engine calls for a town's ~6k nodes), then the path graph and two k-d trees. Its Lua alone is
-- ~35 ms for 6k nodes offline; the raycasts onto a block's roads, buildings and trees are the rest.
--
-- This does the same steps on the new nodes in a coroutine, a few ms a frame (navgraph.lua steps it with the clip),
-- while the old graph stays the game's, then puts the result in at once, as loadMap does at its end: the vehicles'
-- copy into the mailbox and onNavgraphReloaded fired. map.lua's own locals are reached through its loadMap's upvalues,
-- read only (the game's Lua sandbox refuses debug.setupvalue: "function not allowed by sandbox", 2026-10-29):
--   the map table, the path graph and the two k-d trees   filled in place (the same tables, the new contents)
--   the road rules                                       map.lua's own setRoadRules sets them
--   the build serial                                     map.onDeserialize({editor state, serial}), its public setter
--   the largest node radius (findClosestRoad's search)   no setter: kept when the new graph's is no larger (a wider
--                                                         search finds the same roads); larger, map.reset this once
-- map.lua 0.40's names are checked first (M.internals); a game whose loadMap has other locals keeps map.reset.
local M = {}

local clear = table.clear or (function()
  local ok, f = pcall(require, 'table.clear')
  return ok and f or function(t) for k in pairs(t) do t[k] = nil end end
end)()

M.YIELD_EVERY = 32        -- nodes between the coroutine's pauses (the caller checks its time at each)
M.NEED = { 'map', 'gp', 'edgeKdTree', 'nodeKdTree', 'maxRadius', 'buildSerial', 'rules', 'setRoadRules',
  'surfaceNormal', 'graphpath', 'kdTreeBox2D', 'quadtree', 'pointBBox' }

local function upvalueIndex(f)
  local idx = {}
  if type(f) ~= 'function' or not (rawget(_G, 'debug') and debug.getupvalue) then return idx end
  for i = 1, 255 do
    local name = debug.getupvalue(f, i)
    if name == nil then break end
    idx[name] = i
  end
  return idx
end

-- map.lua's loadMap upvalues by name, or nil and the first missing name.
function M.internals(mapmod)
  mapmod = mapmod or rawget(_G, 'map')
  local f = mapmod and mapmod.reset
  local idx = upvalueIndex(f)
  for _, n in ipairs(M.NEED) do
    if not idx[n] then return nil, n end
  end
  local get = function(n) return select(2, debug.getupvalue(f, idx[n])) end
  if type(get('map')) ~= 'table' or type(get('graphpath')) ~= 'table' or type(get('surfaceNormal')) ~= 'function'
      or type(mapmod.onSerialize) ~= 'function' or type(mapmod.onDeserialize) ~= 'function' then
    return nil, 'types'
  end
  return { f = f, idx = idx, get = get, mapmod = mapmod, zones = idx.trafficExclusionZones and
    function() return get('trafficExclusionZones') end or function() return nil end }
end

-- A job building nodes (toNodes' table: { name = { pos, radius, links both ways } }) for the game's map. Its links are
-- made single-sided as map.reset makes them (a copy kept both ways must be taken before: navgraph's adjacency).
function M.new(nodes, I)
  I = I or M.internals()
  if not I then return nil end
  local job = { nodes = nodes, I = I }
  job.co = coroutine.create(function()
    local min, max = math.min, math.max
    local yieldN = 0
    local function tick()
      yieldN = yieldN + 1
      if yieldN % M.YIELD_EVERY == 0 then coroutine.yield() end
    end
    -- convertToSingleSided
    local edgeCount, nodeCount = 0, 0
    for _, n in pairs(nodes) do
      nodeCount = nodeCount + 1
      local newLinks = {}
      for lid, data in pairs(n.links) do
        if data.inNode == lid then
          edgeCount = edgeCount + 1
          newLinks[lid] = data
        end
      end
      n.links = newLinks
      n.manual = nil
    end
    coroutine.yield()
    local drv = {}
    for k, n in pairs(nodes) do
      local d = be:getTerrainDrivability(n.pos, n.radius)
      drv[k] = d <= 0 and 1 or d
      tick()
    end
    local graphpath, kd, quadtree, pointBBox = I.get('graphpath'), I.get('kdTreeBox2D'), I.get('quadtree'), I.get('pointBBox')
    local surfaceNormal = I.get('surfaceNormal')
    local maxRadius = 4
    local gp = graphpath.newGraphpath(nodeCount)
    local edgeKdTree = kd.new(edgeCount)
    local nodeKdTree = kd.new(nodeCount)
    for nid, n in pairs(nodes) do
      local nPos = n.pos
      local radius = n.radius
      gp:setPointPositionRadius(nid, nPos, radius)
      nodeKdTree:preLoad(nid, pointBBox(nPos.x, nPos.y, radius))
      maxRadius = max(maxRadius, radius)
      local nidDrivability = drv[nid]
      for lid, data in pairs(n.links) do
        local edgeDrivability = min(1, max(1e-30, (drv[lid] + nidDrivability) * 0.5 * data.drivability))
        local distanceConst = data.gatedRoad and 10000 or 0
        local inNodeId, outNodeId = data.inNode, data.outNode
        gp:bidiEdge(inNodeId, outNodeId,
          nPos:distance(nodes[lid].pos) / edgeDrivability + distanceConst,
          data.drivability, data.speedLimit, data.lanes, data.oneWay, distanceConst,
          nodes[inNodeId].pos ~= data.inPos and data.inPos or nil,
          nodes[inNodeId].radius ~= data.inRadius and data.inRadius or nil,
          nodes[outNodeId].pos ~= data.outPos and data.outPos or nil,
          nodes[outNodeId].radius ~= data.ouRadius and data.outRadius or nil)     -- (map.lua's own spelling: the same result)
        edgeKdTree:preLoad(gp.graph[inNodeId][outNodeId].eId,
          quadtree.lineBBox(data.inPos.x, data.inPos.y, data.outPos.x, data.outPos.y, min(data.inRadius, data.outRadius)))
      end
      n.normal = surfaceNormal(nPos, radius * 0.5)
      tick()
    end
    gp:generateEdgeIndex()
    coroutine.yield()
    edgeKdTree:build()
    coroutine.yield()
    nodeKdTree:build()
    coroutine.yield()
    local graphData = gp:export(edgeCount)
    coroutine.yield()
    local kdData = edgeKdTree:export()
    job.out = { gp = gp, edgeKdTree = edgeKdTree, nodeKdTree = nodeKdTree, maxRadius = min(15, maxRadius),
      graphData = graphData, kdData = kdData, edges = edgeCount, count = nodeCount }
  end)
  return job
end

-- Steps the job for at most ms (clock(): a stopwatch, ms since it started). Returns true when built, false while not,
-- nil and the error when it failed.
function M.step(job, ms, clock)
  if job.out then return true end
  local sw = clock or (function() local t0 = os.clock(); return function() return (os.clock() - t0) * 1000 end end)()
  repeat
    local ok, err = coroutine.resume(job.co)
    if not ok then return nil, err end
    if job.out then return true end
    if coroutine.status(job.co) == 'dead' then return nil, 'ended without a graph' end
  until sw() >= ms
  return false
end

-- old takes new's contents (and metatable): every holder of old sees the new graph, as with a new local.
local function into(old, new)
  clear(old)
  for k, v in pairs(new) do old[k] = v end
  local mt = getmetatable(new)
  if getmetatable(old) ~= mt then setmetatable(old, mt) end
end

-- Puts the built graph in as map.reset(nodes) would have at its end (the caller has set up the road rules' file,
-- as for map.reset). Returns true; or false and why when it cannot this time, nothing changed (the caller runs
-- map.reset). Errors propagate (the caller pcalls).
function M.commit(job)
  local I, o = job.I, assert(job.out, 'not built')
  local mapmod, m = I.mapmod, I.get('map')
  local gp, ek, nk = I.get('gp'), I.get('edgeKdTree'), I.get('nodeKdTree')
  if type(gp) ~= 'table' or type(ek) ~= 'table' or type(nk) ~= 'table' then return false, 'no map to fill' end
  local r = I.get('maxRadius')
  if type(r) ~= 'number' or o.maxRadius > r then return false, 'a larger road radius' end
  I.get('setRoadRules')()           -- (first: what can fail before the old map is touched)
  mapmod.objects = {}
  mapmod.objectNames = {}
  local old = m.nodes
  if old then clear(old) end
  clear(m)
  m.nodes = job.nodes
  into(gp, o.gp)
  into(ek, o.edgeKdTree)
  into(nk, o.nodeKdTree)
  m.nodeAliases = {}
  if mapmod.createTrafficExclusionArea then mapmod.createTrafficExclusionArea(I.zones()) end
  local serial = (I.get('buildSerial') or -1) + 1
  local ser = mapmod.onSerialize()
  mapmod.onDeserialize({ type(ser) == 'table' and ser[1] or nil, serial })
  m.buildSerial = serial
  be:sendToMailbox('mapData', lpack.encodeBinWorkBuffer({
    nodeAliases = m.nodeAliases, maxRadius = o.maxRadius, graphData = o.graphData, edgeKdTree = o.kdData,
    rules = I.get('rules'), buildSerial = serial }))
  be:sendToMailbox('updateDrivabilities', lpack.encodeBinWorkBuffer(nil))
  if guihooks and guihooks.trigger then guihooks.trigger('NavigationMapChanged', m) end
  if extensions and extensions.hook then extensions.hook('onNavgraphReloaded') end
  return true
end

return M
