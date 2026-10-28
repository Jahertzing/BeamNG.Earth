-- The AI road graph (server/navgraph.py, --traffic on) handed to the game's own navgraph with
-- map.reset(customNodes) (docs/research/2026-10-10-navgraph-traffic-signals.md §2.4-2.8, the plan in §8): BeamNG's
-- traffic and AI then drive our streamed roads. The server sends a graph around the player every ~400 m; this
-- module clips it to the terrain block (the only collidable ground, minus EDGE_M) and applies it whenever a new
-- graph arrives or the block moved, at most every MIN_GAP_S.
--
-- The custom map path's pitfalls (research §2.7), each handled here: vehicles are told to fetch the new graph
-- (mapmgr.requestMap); the node table object stays the same one across resets (gameplay_traffic caches it); every
-- edge carries its speed limit and lanes; the road rules come from a file of ours (the level's info.json has none);
-- a reset by someone else (map.reset() with no graph rebuilds an empty one from our level) is undone next frame.
local M = {}

M.EDGE_M = 60           -- graph nodes this far inside the block's ground box only
M.MIN_GAP_S = 1.0
-- An apply (or its signals' second step) waits for a light frame (governor roomFor: at most APPLY_ROOM_MS of streaming
-- and other work spent in it, not a terrain commit's), APPLY_WAIT_FRAMES at most
M.APPLY_ROOM_MS = 4.0
M.APPLY_WAIT_FRAMES = 30
-- The block moved this far from where the graph was last clipped: clipped again to the new block (the server's graph
-- reaches WINDOW_M = 1100 m, the block ~450 m; without it the graph ran out ahead of a car driving on before the next
-- graph came, every 400 m: the GPS arrows ended and the autodrive stopped, the user's Cannonball run 2026-10-14)
M.RECLIP_M = 150
M.UI_MAP = false        -- send our graph to the UI (the big map) on every apply
M.RULES_FILE = '/temp/beamngEarth/roadRules.json'
M.rules = { rightHandDrive = false, turnOnRed = true }    -- US; `rightHandDrive = true` means driving on the left
M.enabled = true
-- Traffic (gameplay_traffic, the player's traffic amount): started once the first graph is in the map; a car
-- within GUARD_M of the block's edge (or past it, where there is no ground) is respawned on the graph
-- (gameplay_traffic.forceTeleport), checked every GUARD_S; after a teleport every car is, once the new
-- place's graph is in.
M.GUARD_M = 40
M.GUARD_S = 0.5
-- A car standing still this long (longer than a signal cycle), this far from the player, is respawned too: stock
-- traffic respawns only cars far away and out of sight, so one wedged on a corner the map's road cuts through a
-- building, or queued behind the player's parked car in a one-lane street, stood for good (Frederick, Fells Point
-- 2026-10-14)
M.STUCK_S = 45
M.STUCK_MS = 0.5
M.STUCK_FAR_M = 40
M.RESPAWN_M = { 60, 300 }   -- a respawned car goes this far (min, max) behind the camera
-- A car heading for a graph node whose road the clip cut off (within 45 degrees, CUT_M or CUT_S of driving away) is
-- respawned too: to the AI the cut is a dead end, and a car spawned facing one made a three-point turn on the spot
-- (GitHub #72, "spawns the wrong way and tries to turn around immediately"; Bathurst 2026-10-21: 20 m from a cut)
-- A car that has just appeared (FRESH_S) turns round for a cut much farther ahead (Bathurst: 89 m): CUT_FRESH_M.
M.CUT_M = 60
M.CUT_S = 4
M.CUT_FRESH_M = 250
-- ... but not a car this module put ahead along the player's way at motorway speed (aheadSpot): it faces the way the
-- player drives, toward the graph's far end, which moves on with the player's block. With CUT_FRESH_M the 150-350 m
-- spots ahead were all within 250 m of that end, and each such car was respawned again at once: on I-25 one car every
-- ~2 s went round that loop (the traffic bench, 2026-10-28: cut and ahead respawns rising together).
M.FRESH_S = 5
M.JUMP_M = 30           -- a car this much farther than at the last check was respawned (stock or ours)
-- At speed a car respawned behind the camera is never seen again: on a motorway every car ahead reached the block's
-- edge within ~30 s and reappeared behind the player, who drove alone (GitHub #353, Albany to New York). Faster than
-- AHEAD_MS, a respawned car goes ahead along the player's way instead, AHEAD_M (min, max) on, short of the block's
-- edge guard (in town it still goes behind). 150 m on: at 100 m one popped in where the player saw it on an open
-- Interstate, and 350 m keeps it near enough to be caught up with or to pass (I-25, 2026-10-28).
M.AHEAD_MS = 20         -- (45 mph)
M.AHEAD_M = { 150, 350 }
-- ... and starts at speed: gameplay_traffic.forceTeleport leaves a car standing (only the stock respawn gives it a
-- speed, at most 14 m/s), so on I-25 each car put ahead was a standing car 150-350 m in front of a player at 75 mph,
-- the traffic ran at 40-55 mph and the player's autodrive behind it averaged 46 (the traffic bench, 2026-10-28)
M.AHEAD_PACE = 0.85     -- of the player's speed
M.AHEAD_V_MAX = 31      -- m/s (70 mph)
-- (gameplay_walk: 0.40's traffic.lua calls gameplay_walk.addVehicleToBlacklist when it activates its cars, and this
-- level had none loaded: the cars spawned and never drove, 2026-10-19)
M.EXTENSIONS = { 'core_multiSpawn', 'gameplay_traffic', 'gameplay_walk' }

local graph = nil       -- the server's last graph {anchor, nodes, edges}
local applied = nil     -- {anchor, serial, box} of the graph in the game's map now
local dirty = false
local lastApply = -math.huge
local persistent = nil  -- the node table handed out once (research §2.7.2)
local ours = false
local foreign = false
local stats = { applies = 0, lastMs = 0, nodes = 0, edges = 0 }
local pendingSignals = nil -- the clipped nodes whose signals go in next frame
local started = false
local respawnAll = false
-- The traffic that went off on its own soon after we started it or the player teleported is set up once again: on
-- 2026-10-28 a teleport 0.4 s after the cars spawned left gameplay_traffic 'off' with its cars dropped from its list,
-- and its own amount (the setting less the vehicles about) then 0, so the drive had no traffic.
M.WATCH_S = 90
local watch = nil       -- { until os.clock(), seenOn, done } while the traffic is watched
local lastGuard = -math.huge
local cuts = {}         -- { x, y } of the applied graph's nodes that lost a road to the clip
local cutNames = {}     -- their names
-- The merge assist (mergeassist.lua: ramp cars find a gap in the motorway's traffic), every MERGE_S on the applied graph
M.MERGE = true
M.MERGE_S = 0.5           -- (it runs with the guard, every GUARD_S at most)
local merge = require('beamngEarth/mergeassist')
local lastNodes = nil   -- the node table of the graph applied last (toNodes': positions, links, edges with ramp flags)
local capped = {}       -- traffic car id -> the merge assist's speed cap on it
local lastMerge = -math.huge

-- Pure: point inside box {x0, y0, x1, y1} shrunk by m (no box: anywhere).
function M.inside(b, x, y, m)
  return not b or (x >= b[1] + m and x <= b[3] - m and y >= b[2] + m and y <= b[4] - m)
end

-- Pure: the lanes string of an edge (one char per lane, '+' along in -> out, '-' against; research §2.2), the
-- lanes against the direction first for right-hand traffic.
function M.lanes(out, back, leftHand)
  local plus, minus = string.rep('+', out), string.rep('-', back)
  return leftHand and (plus .. minus) or (minus .. plus)
end

-- A link steeper than this (rise over run) is left out of the graph: no car drives it, and the AI given a path over
-- one circled under it for ever (GitHub #131: Baltimore's East Eager Street, a bridge whose first node stood 6.4 m
-- over its junction with Greenmount Avenue 8 m away; the steepest real streets are ~0.37, San Francisco's).
M.MAX_GRADE = 0.45

-- Pure: is the link between graph nodes p and q ({x, y, z}) too steep to drive (MAX_GRADE)?
function M.tooSteep(p, q)
  local run = math.sqrt((p[1] - q[1]) ^ 2 + (p[2] - q[2]) ^ 2)
  return math.abs(p[3] - q[3]) > M.MAX_GRADE * math.max(run, 1.0)
end

-- Pure-ish (needs vec3): the stock map's node table of graph g clipped to box b: {name = {pos, radius,
-- links = {other = edge}}}, every edge shared by both ends (double-sided) with in/out nodes, positions and radii.
-- Returns nodes, node count, edge count and the cuts: { x, y } of each kept node with a road to a node left out.
function M.toNodes(g, b, edgeM, leftHand, yieldEvery)
  local nodes, nn, ne = {}, 0, 0
  local keep = {}
  local steps = 0
  local function step()       -- (in a coroutine: a pause every yieldEvery items, buildNodes)
    if yieldEvery then
      steps = steps + 1
      if steps % yieldEvery == 0 then coroutine.yield() end
    end
  end
  for name, n in pairs(g.nodes) do
    if M.inside(b, n[1], n[2], edgeM) then keep[name] = n end
    step()
  end
  local function node(name)
    local t = nodes[name]
    if not t then
      local n = keep[name]
      t = { pos = vec3(n[1], n[2], n[3]), radius = n[4], links = {} }
      nodes[name] = t
      nn = nn + 1
    end
    return t
  end
  local cut, cutList = {}, {}
  for _, e in ipairs(g.edges) do
    step()
    local a, bb = e[1], e[2]
    for _, pair in ipairs({ { a, bb }, { bb, a } }) do
      local k = pair[1]
      if keep[k] and not keep[pair[2]] and g.nodes[pair[2]] and not cut[k] then
        cut[k] = true
        cutList[#cutList + 1] = { keep[k][1], keep[k][2], name = k }
      end
    end
    if keep[a] and keep[bb] and a ~= bb and not M.tooSteep(keep[a], keep[bb]) then
      local A, B = node(a), node(bb)
      if not A.links[bb] then
        local edge = { drivability = e[7], oneWay = e[3] == 1, lanes = M.lanes(e[4], e[5], leftHand),
          speedLimit = e[6], inNode = a, outNode = bb, inPos = A.pos, outPos = B.pos,
          inRadius = e[8], outRadius = e[8], ramp = e[9] == 1 or nil }
        A.links[bb] = edge
        B.links[a] = edge
        ne = ne + 1
      end
    end
  end
  return nodes, nn, ne, cutList
end

-- Pure: does the road ahead of a car at (px, py) moving along unit (dx, dy) on the edge a - b of node table nodes run
-- into a cut (cutSet: name -> true) within reach m: following the straightest way on at each node (the one-way edges
-- only their way), a node with no way on that is a cut, i.e. a dead end the clip made? nil when the edge is not in nodes.
-- The cone test (headingToCut) respawned cars for any cut node ahead within 45 degrees: a side street cut at the block's
-- edge beside the motorway, or a motorway junction whose cross street was cut while the motorway went on. On I-25 that
-- was one respawn every ~2 s (bench4, 2026-10-28: 163 in 4 min), each car popping out and in again ahead.
function M.cutAhead(nodes, cutSet, a, b, px, py, dx, dy, reach)
  local na, nb = nodes[a], nodes[b]
  local e = na and nb and na.links[b]
  if not e then return nil end
  local from, to
  if e.oneWay then
    from, to = e.inNode, e.outNode
  else
    local ahead = (nb.pos.x - na.pos.x) * dx + (nb.pos.y - na.pos.y) * dy >= 0
    from, to = ahead and a or b, ahead and b or a
  end
  local d = math.sqrt((nodes[to].pos.x - px) ^ 2 + (nodes[to].pos.y - py) ^ 2)
  for _ = 1, 128 do
    if d > reach then return false end
    local f, t = nodes[from].pos, nodes[to].pos
    local ux, uy = t.x - f.x, t.y - f.y
    local ul = math.sqrt(ux * ux + uy * uy)
    if ul > 1e-6 then ux, uy = ux / ul, uy / ul end
    local best, bestDot = nil, -math.huge
    for o, eo in pairs(nodes[to].links) do
      if o ~= from and (not eo.oneWay or eo.inNode == to) then
        local q = nodes[o].pos
        local vx, vy = q.x - t.x, q.y - t.y
        local vl = math.sqrt(vx * vx + vy * vy)
        local dot = vl > 1e-6 and (vx * ux + vy * uy) / vl or -1
        if dot > bestDot then best, bestDot = o, dot end
      end
    end
    if not best then return cutSet[to] == true end
    d = d + math.sqrt((nodes[best].pos.x - t.x) ^ 2 + (nodes[best].pos.y - t.y) ^ 2)
    from, to = to, best
  end
  return false
end

-- Pure: node table nodes (toNodes') as { name = { pos, radius, links } } sharing its links tables: map.reset swaps
-- each node's links for a single-sided one, these stay as they were.
function M.adjacency(nodes)
  local out = {}
  for name, n in pairs(nodes) do out[name] = { pos = n.pos, radius = n.radius, links = n.links } end
  return out
end

-- The graph applied last, both ways round (adjacency), or nil.
function M.graphNodes() return lastNodes end

-- Pure: the names of the cuts in list (toNodes' cutList) as a set.
function M.cutSet(list)
  local set = {}
  for _, c in ipairs(list) do if c.name then set[c.name] = true end end
  return set
end

-- Pure: is a car at (px, py) moving along unit (dx, dy) heading for one of the cuts within reach m (45 degrees)?
-- (the fallback where the car is not on the graph)
function M.headingToCut(px, py, dx, dy, list, reach)
  for _, c in ipairs(list) do
    local vx, vy = c[1] - px, c[2] - py
    local d = math.sqrt(vx * vx + vy * vy)
    if d <= reach and (d < 1 or (vx * dx + vy * dy) >= 0.7071 * d) then return true end
  end
  return false
end

function M.onMessage(msg, currentAnchor)
  if not M.enabled or msg.anchorId ~= currentAnchor then return false end
  local ok, g = pcall(jsonDecode, readFile(msg.path) or '')
  if not ok or type(g) ~= 'table' or type(g.nodes) ~= 'table' or type(g.edges) ~= 'table' then return false end
  graph = { anchor = msg.anchorId, serial = msg.serial, nodes = g.nodes, edges = g.edges,
            signals = type(g.signals) == 'table' and g.signals or {}, rules = type(g.rules) == 'table' and g.rules or nil }
  dirty = true
  return true
end

-- Traffic signals and stop signs (core_trafficSignals, research §5): the server's controlled junctions whose
-- node and approach nodes are in the clipped graph. A signalled junction is one sequence of two phases (its
-- approaches split by axis, US lightsBasic timings from the approach speed), a stop junction shares one signStop
-- controller; an instance's pos is the stop point on its approach's right half, dir the travel direction.
M.SIGNAL_TYPE = 'lightsBasic'
-- the switchable head (the stock boom, instanced_trafficlight material): one TSStatic per approach where the
-- road tile's static boom stands (trees.lua's signal group is hidden while these exist)
M.SIGNAL_SHAPE = '/art/shapes/objects/s_trafficlight_boom_sn.dae'
local heads = {}        -- instance name -> {id, x, y}: the heads in the scene (kept across applies)
local seenHeads = {}

local function clearHeads(keep)
  for name, h in pairs(heads) do
    if not (keep and keep[name]) then
      local o = scenetree.findObjectById(h.id)
      if o then o:delete() end
      heads[name] = nil
    end
  end
end

-- A head for instance inst at a's prop transform: the one already standing there is kept (it is linked by
-- the instance's name, which stays the same: setupSignals finds it again), so a new graph costs only the new ones.
local function makeHead(inst, a)
  if not a[8] or not inst.createSignalObject then return end
  seenHeads[inst.name] = true
  local h = heads[inst.name]
  if h and math.abs(h.x - a[8]) < 0.05 and math.abs(h.y - a[9]) < 0.05 and scenetree.findObjectById(h.id) then return end
  if h then
    local old = scenetree.findObjectById(h.id)
    if old then old:delete() end
  end
  local o = inst:createSignalObject(M.SIGNAL_SHAPE, vec3(a[8], a[9], a[10]), quat(0, 0, 0, 1))
  if not o then return end
  local m = MatrixF(true)
  m:setFromEuler(vec3(0, 0, -math.rad(a[11] or 0)))     -- as trees.lua turns the static boom (negated: clockwise)
  m:setPosition(vec3(a[8], a[9], a[10]))
  o:setTransform(m)
  heads[inst.name] = { id = o:getID(), x = a[8], y = a[9] }
end

local function signalData(nodes)
  local ts = core_trafficSignals
  local inst, ctrls, seqs = {}, {}, {}
  local stop = nil
  local ns = 0
  for _, j in ipairs(graph.signals or {}) do
    local jn = nodes[j.j]
    if jn and type(j.app) == 'table' then
      local apps = {}
      for _, a in ipairs(j.app) do if nodes[a[1]] then apps[#apps + 1] = a end end
      if #apps > 0 then
        ns = ns + 1
        local cs = {}
        if j.kind == 'signal' then
          local speed = 13
          for _, l in pairs(jn.links) do speed = math.max(speed, l.speedLimit or 0) end
          for ph = 0, 1 do
            local c = ts.newController({ name = 'bne_' .. j.j .. '_' .. ph })
            c:applyDefinition(M.SIGNAL_TYPE)
            if c.autoSetTimings then c:autoSetTimings(speed, 2 * jn.radius + 6) end
            ctrls[#ctrls + 1] = c
            cs[ph] = c
          end
          local seq = ts.newSequence({ name = 'bne_' .. j.j, phases = { { controllerIds = { cs[0].id } },
            { controllerIds = { cs[1].id } } } })
          seqs[#seqs + 1] = seq
          for i, a in ipairs(apps) do
            local s = ts.newSignal({ name = 'bne_' .. j.j .. '_' .. i, pos = vec3(a[2], a[3], a[4]),
              dir = vec3(a[5], a[6], 0), controllerId = cs[a[7] == 1 and 1 or 0].id, sequenceId = seq.id })
            inst[#inst + 1] = s
            local okH, errH = pcall(makeHead, s, a)
            if not okH then log('W', 'beamngEarth', 'navgraph: signal head: ' .. tostring(errH)) end
          end
        else
          if not stop then
            stop = ts.newController({ name = 'bne_stop' })
            stop:applyDefinition('signStop')
            ctrls[#ctrls + 1] = stop
          end
          for i, a in ipairs(apps) do
            inst[#inst + 1] = ts.newSignal({ name = 'bne_' .. j.j .. '_' .. i, pos = vec3(a[2], a[3], a[4]),
              dir = vec3(a[5], a[6], 0), controllerId = stop.id })
          end
        end
      end
    end
  end
  return { instances = inst, controllers = ctrls, sequences = seqs }, ns
end

local function signalsSystem()
  if not core_trafficSignals and extensions and extensions.load then pcall(extensions.load, 'core_trafficSignals') end
  return core_trafficSignals
end

local function setRules()
  -- the country's rules (server navgraph.road_rules): driving side, turn on red, red+yellow before green
  local r = graph.rules
  if r then
    M.rules = { rightHandDrive = r.rightHandDrive and true or false, turnOnRed = r.turnOnRed and true or false }
    M.SIGNAL_TYPE = r.redYellow and 'lightsBasicEU' or 'lightsBasic'
  end
end

-- pre: toNodes' results built ahead (buildNodes), else made here
local function apply(b, pre)
  setRules()
  local tN = os.clock()
  local nodes, nn, ne, cl
  if pre then nodes, nn, ne, cl = pre[1], pre[2], pre[3], pre[4]
  else nodes, nn, ne, cl = M.toNodes(graph, b, M.EDGE_M, M.rules.rightHandDrive) end
  stats.nodesMs = (os.clock() - tN) * 1000         -- (the parts of an apply's frame: the hitch hunt, 2026-10-23)
  local ts = signalsSystem()
  -- before the reset: its onNavgraphReloaded would place the old instances on the new graph (research §2.8)
  if ts then pcall(ts.resetSignals) end
  writeFile(M.RULES_FILE, jsonEncode({ roadRules = M.rules }))
  local orig = path.getPathLevelInfo
  path.getPathLevelInfo = function() return M.RULES_FILE end
  -- the reset sends the whole map to the UI (NavigationMapChanged, research §2.3): only the big map uses it,
  -- and it is a good part of the reset's frame; dropped during ours (M.UI_MAP = true keeps it)
  local trig = guihooks and guihooks.trigger
  if trig and not M.UI_MAP then
    guihooks.trigger = function(name, ...) if name ~= 'NavigationMapChanged' then return trig(name, ...) end end
  end
  local okG, gps = pcall(require, 'beamngEarth/gps')
  local mOld = map.getMap and map.getMap()
  local tG = os.clock()
  local keep = okG and gps.beforeGraph and gps.beforeGraph(mOld and mOld.nodes) or nil
  stats.gpsMs = (os.clock() - tG) * 1000
  local t0 = os.clock()
  -- both ways round, for the guard's and the merge assist's walks: map.reset makes the links single-sided (map.lua
  -- convertToSingleSided gives each node a new links table with only the edges coming into it), so keep the tables
  lastNodes = M.adjacency(nodes)
  ours = true
  local ok, err = pcall(map.reset, nodes)
  ours = false
  if trig then guihooks.trigger = trig end
  path.getPathLevelInfo = orig
  if not ok then
    log('E', 'beamngEarth', 'navgraph: map.reset: ' .. tostring(err))
    return false
  end
  stats.resetMs = (os.clock() - t0) * 1000
  local m = map.getMap()
  if persistent and persistent ~= m.nodes then
    table.clear(persistent)
    for k, v in pairs(m.nodes) do persistent[k] = v end
    m.nodes = persistent
  else
    persistent = m.nodes
  end
  local tA = os.clock()
  if keep then pcall(gps.afterGraph, keep, m.nodes) end    -- before the vehicles' new map
  stats.gpsMs = stats.gpsMs + (os.clock() - tA) * 1000
  be:queueAllObjectLua('mapmgr.requestMap()')
  -- the minimap (the navigation app, in 0.40's freeroam layout and ours) indexes the map's roads once and keeps them:
  -- it showed the first graph's streets, or none, as the player drove on (the user, 2026-10-22)
  local mr = rawget(_G, 'ui_apps_minimap_roads')
  if mr and mr.reset then pcall(mr.reset) end
  pendingSignals = ts and nodes or nil     -- next frame: a second, smaller hitch instead of one big one
  cuts = cl
  cutNames = M.cutSet(cl)
  stats.applies, stats.lastMs, stats.nodes, stats.edges = stats.applies + 1, (os.clock() - t0) * 1000, nn, ne
  stats.applyMs = (os.clock() - tN) * 1000
  stats.cuts = #cl
  return true
end

-- The vehicle Lua that hands a vehicle the signals (a serialized table). A traffic car keeps the junction it
-- is approaching across map changes (ai.lua's trafficStates.intersection; stock maps never change) and
-- reads signalsData[node][nextNode][idx] without a check: a new graph without that junction (a teleport,
-- or the junction left the window) disabled every car near one (in game 2026-10-14, "attempt to index a
-- nil value" at ai.lua trafficActions). So the new table keeps the old entries it lacks: their nodes are
-- not on the new graph's paths (ai.lua's other reads check first), only a remembered junction resolves.
function M.setSignalsLua(data)
  return 'do local old, new = mapmgr.signalsData, ' .. data .. '\n'
    .. 'if old and new then for a, t in pairs(old) do local n = new[a] if not n then new[a] = t else '
    .. 'for b, u in pairs(t) do if n[b] == nil then n[b] = u end end end end end\n'
    .. 'mapmgr.setSignals(new) end'
end

-- The signals of the graph applied last frame (core_trafficSignals resolves them on the new map).
local function applySignals(nodes)
  local ts = core_trafficSignals
  if not ts then return end
  local t1 = os.clock()
  local okS, err2 = pcall(function()
    seenHeads = {}
    local data, ns = signalData(nodes)
    clearHeads(seenHeads)                   -- the heads of junctions no longer in the graph
    ts.setupSignals(data, false, true)
    -- a vehicle holding signals reads the state-change mailbox every frame and dies on one never written
    -- ("lpack: unexpected end of buffer", in game 2026-10-14): write an empty update before handing them out
    be:sendToMailbox('trafficSignalUpdates', lpack.encodeBin({}))
    be:queueAllObjectLua(M.setSignalsLua(serialize(ts.getMapNodeSignals())))
    stats.junctions = ns
    -- the road tiles' static booms stand where the switchable heads are now
    local trees = require('beamngEarth/trees')
    local grid = {}
    for _, h in pairs(heads) do grid[math.floor(h.x / 4) .. ',' .. math.floor(h.y / 4)] = true end
    trees.suppress = function(g, x, y)
      if g ~= 'signal' then return false end
      local cx, cy = math.floor(x / 4), math.floor(y / 4)
      for dx = -1, 1 do for dy = -1, 1 do if grid[(cx + dx) .. ',' .. (cy + dy)] then return true end end end
      return false
    end
    trees.refresh()
  end)
  stats.signalsMs = (os.clock() - t1) * 1000
  if not okS then log('W', 'beamngEarth', 'navgraph: signals: ' .. tostring(err2)) end
end

local function trafficSystem()
  if not (gameplay_traffic and core_multiSpawn and gameplay_walk) and extensions and extensions.load then
    pcall(extensions.load, unpack(M.EXTENSIONS))
  end
  return gameplay_traffic
end

local function playerId() return be and be.getPlayerVehicleID and be:getPlayerVehicleID(0) or -1 end

-- Pure: where a respawned car goes, (x, y, dirX, dirY, min m, max m) for gameplay_traffic.forceTeleport, or nil for
-- behind the camera: ahead of a player at (px, py) moving (vx, vy) m/s faster than AHEAD_MS, AHEAD_M along the way but
-- short of the block b's edge by aheadRoom: the clip's EDGE_M, the cut reach of a car put there at aheadSpeed, and a
-- margin. (It used to stop at the edge guard, GUARD_M + 20 m, which is about where the clip's cut nodes are: a car put
-- there was within its cut reach at once and respawned again, the traffic bench on I-25, 2026-10-28: 20 cut and 22
-- ahead respawns in 164 s, each of the 7 cars moved every ~27 s.) Less room than AHEAD_M[1]: from AHEAD_TIGHT_M; less
-- than that: nil, behind the camera.
M.AHEAD_TIGHT_M = 90
M.AHEAD_MARGIN_M = 20

-- Pure: the room (m) a car put ahead of a player moving at psp m/s needs before the block's edge.
function M.aheadRoom(psp)
  return M.EDGE_M + M.cutReach(math.huge, M.aheadSpeed(psp), true) + M.AHEAD_MARGIN_M
end

function M.aheadSpot(px, py, vx, vy, b)
  local sp = math.sqrt(vx * vx + vy * vy)
  if sp < M.AHEAD_MS then return nil end
  local dx, dy = vx / sp, vy / sp
  local reach = math.huge
  if b then
    if dx > 1e-6 then reach = math.min(reach, (b[3] - px) / dx) elseif dx < -1e-6 then reach = math.min(reach, (b[1] - px) / dx) end
    if dy > 1e-6 then reach = math.min(reach, (b[4] - py) / dy) elseif dy < -1e-6 then reach = math.min(reach, (b[2] - py) / dy) end
  end
  local hi = math.min(M.AHEAD_M[2], reach - M.aheadRoom(sp))
  local lo = hi < M.AHEAD_M[1] and M.AHEAD_TIGHT_M or M.AHEAD_M[1]
  if hi < lo then return nil end
  return px, py, dx, dy, lo, hi
end

-- Pure: the speed (m/s) a car put ahead of a player moving at psp m/s starts at.
function M.aheadSpeed(psp)
  return math.max(0, math.min(M.AHEAD_V_MAX, psp * M.AHEAD_PACE))
end

-- Pure: how near a cut a car heading for it is respawned: CUT_FRESH_M while fresh (age s since it appeared), unless
-- this module put it ahead along the player's way (ahead); else CUT_M or CUT_S seconds at its speed sp.
function M.cutReach(age, sp, ahead)
  if age < M.FRESH_S and not ahead then return M.CUT_FRESH_M end
  return math.max(M.CUT_M, sp * M.CUT_S)
end

-- Pure: is a car that has stood still since `since` (nil: moving) at distance d from the player stuck at `now`?
function M.stuck(since, now, d)
  return since ~= nil and now - since >= M.STUCK_S and d >= M.STUCK_FAR_M
end

-- Each traffic car's own pace on fast roads: the stock AI's "legal" mode drives every edge's speed exactly, so on I-25
-- every car went the same speed, nobody passed anybody (the user's drive, 2026-10-28). Each car gets a factor on the
-- edges of at least FAST_MS (45 mph: motorways, trunks, fast arterials; town streets and their signals keep the
-- limit), about normal around 1 with SPREAD, clipped to 1 +- SPREAD_MAX: 62-78 mph where the AI's target is 70 (a 65
-- zone, navgraph.py FLOW_MPH). It is set once in the car's own Lua (its mapmgr scales every new map it is handed).
M.FAST_MS = 20
M.SPREAD = 0.06
M.SPREAD_MAX = 0.12

-- Pure: a car's pace factor from three uniform [0, 1) draws (their sum: about normal).
function M.paceFactor(u1, u2, u3)
  local z = (u1 + u2 + u3 - 1.5) * 2        -- (sd 1)
  return math.max(1 - M.SPREAD_MAX, math.min(1 + M.SPREAD_MAX, 1 + z * M.SPREAD))
end

-- The vehicle Lua giving a car pace factor f: every edge of its map at least FAST_MS fast scaled from its own limit
-- (kept as _bneLimit: a map scaled again, or handed again, is not scaled twice), now and on every new map (mapmgr.setMap
-- wrapped once per car; graphpath shares an edge's table between its two directions: scaled once, from its in node).
function M.paceLua(f)
  return string.format([[do
local f, fast = %.4f, %.2f
local function scale(md)
  if not (md and md.graph) or md._bnePace == f then return end
  for a, links in pairs(md.graph) do
    for _, e in pairs(links) do
      if e.inNode == a and e.speedLimit then
        e._bneLimit = e._bneLimit or e.speedLimit
        if e._bneLimit >= fast then e.speedLimit = e._bneLimit * f end
      end
    end
  end
  md._bnePace = f
end
mapmgr._bnePace = scale
if not mapmgr._bneSetMap then
  mapmgr._bneSetMap = mapmgr.setMap
  mapmgr.setMap = function(...) local r = mapmgr._bneSetMap(...) pcall(mapmgr._bnePace, mapmgr.mapData) return r end
end
pcall(scale, mapmgr.mapData)
end]], f, M.FAST_MS)
end

-- Every apply hands each car a new map object (mapmgr.setMap builds a new graphpath), and the stock ai.lua drops its
-- route whenever the object changes (updateGFX: `if mapData ~= mapmgr.mapData then currentRoute = nil`): every car
-- re-picked a road, a lane and a random way on at each apply, one every ~10 s on I-25 (the traffic bench, 2026-10-28),
-- abandoning lane changes and passes and swerving for exits. Our graphs name their nodes the same in every graph
-- (navgraph.py: connectors, shape points), so a route whose every node is in the new map, at the same place, with the
-- same links between them, is still good: the car's ai.lua is told the map did not change (its mapData upvalue set to
-- the new map, read with the debug library as ai.lua's own inspect_upvalues does) and keeps it. Anything else (a
-- rebase moves every node, a clip cut the route) drops it as before. The car reports kept / dropped (routeKept).
-- Only the route from the car's plan on counts (the stock route keeps up to ten nodes behind the car, which the block's
-- move had clipped away: on 420983b7 every route was dropped, bench4), and it is checked against the map the route was
-- made on (ai.lua's own, which may be older than the map just replaced: a car respawned since the last map has none).
-- Heights may differ a little (the ground under a node is sampled again as the terrain streams in): KEEP_DZ_M.
M.KEEP_ROUTE = true
M.KEEP_TOL_M = 0.5
M.KEEP_DZ_M = 3

function M.keepRouteLua()
  return string.format([[do
local tol2, dz = %.4f, %.2f
local function upvalues(f)
  local im, ir
  for i = 1, 255 do
    local n = debug.getupvalue(f, i)
    if not n then break end
    if n == 'mapData' then im = i elseif n == 'currentRoute' then ir = i end
  end
  return im, ir
end
local function keepable(route, old, new)
  local path = route.path
  if not (path and path[1] and old and new and old.positions and new.positions and new.graph) then return 'nopath' end
  local plan = route.plan
  local k0 = math.max(1, (plan and plan[1] and plan[1].pathidx or 1) - 1)
  if not path[k0] then return 'nopath' end
  for i = k0, #path do
    local a = path[i]
    local g, p, q = new.graph[a], new.positions[a], old.positions[a]
    if not (g and p and q) then return 'missing' end
    if (p.x - q.x) ^ 2 + (p.y - q.y) ^ 2 > tol2 or math.abs(p.z - q.z) > dz then return 'moved' end
    local b = path[i + 1]
    if b and not g[b] then return new.graph[b] and 'link' or 'missing' end
  end
  return 'kept'
end
mapmgr._bneKeepRoute = function()
  if not (ai and ai.mode == 'traffic' and type(ai.updateGFX) == 'function') then return end
  local f = ai.updateGFX
  local im, ir = upvalues(f)
  if not (im and ir) then return end
  local _, aiMap = debug.getupvalue(f, im)
  local _, route = debug.getupvalue(f, ir)
  if not route or not aiMap or aiMap == mapmgr.mapData then return end
  local why = keepable(route, aiMap, mapmgr.mapData)
  if why == 'kept' then debug.setupvalue(f, im, mapmgr.mapData) end
  obj:queueGameEngineLua(string.format('local n = package.loaded["beamngEarth/navgraph"] if n and n.routeKept then n.routeKept(%%d, %%q) end', objectId, why))
end
if not mapmgr._bneKeepSetMap then
  mapmgr._bneKeepSetMap = mapmgr.setMap
  mapmgr.setMap = function(...)
    local r = mapmgr._bneKeepSetMap(...)
    pcall(mapmgr._bneKeepRoute)
    return r
  end
end
end]], M.KEEP_TOL_M * M.KEEP_TOL_M, M.KEEP_DZ_M)
end

-- A car's report after a new map (keepRouteLua): 'kept', or why its route was dropped ('missing': a node of it is not in
-- the new map, 'moved', 'link', 'nopath').
function M.routeKept(id, why)
  if why == 'kept' or why == true then
    stats.routesKept = (stats.routesKept or 0) + 1
  else
    stats.routesDropped = (stats.routesDropped or 0) + 1
    stats.routeDrops = stats.routeDrops or {}
    local k = tostring(why)
    stats.routeDrops[k] = (stats.routeDrops[k] or 0) + 1
  end
end

local paced = {}        -- car id -> its pace factor (set in its Lua)

-- Traffic cars the block left behind (or all, after a teleport), and stuck ones, are respawned on the graph.
local stillSince = {}   -- car id -> os.clock() it stopped moving
local seenAt = {}       -- car id -> { x, y, os.clock() it appeared there }
local putAhead = {}     -- car id -> true: its last respawn was ours, ahead along the player's way (aheadSpot)
local function guard(b, all)
  local gt = gameplay_traffic
  if not gt or gt.getState() ~= 'on' then return end
  local me = playerId()
  local pv = me >= 0 and getObjectByID(me) or nil
  local pp = pv and pv:getPosition() or nil
  local now = os.clock()
  -- paused: nothing moves, nobody is stuck (the clocks start again after it)
  local paused = simTimeAuthority and simTimeAuthority.getPause and simTimeAuthority.getPause()
  if paused then table.clear(stillSince) end
  for id in pairs(gt.getTrafficData() or {}) do
    if id ~= me then
      local o = getObjectByID(id)
      if o and not paced[id] and o.queueLuaCommand then
        paced[id] = M.paceFactor(math.random(), math.random(), math.random())
        o:queueLuaCommand(M.paceLua(paced[id]))
        if M.KEEP_ROUTE then o:queueLuaCommand(M.keepRouteLua()) end
      end
      if o and o:getActive() then
        local p = o:getPosition()
        local u = o:getVelocity()
        local sp = math.sqrt(u.x * u.x + u.y * u.y)
        local dir = sp > 1 and { x = u.x / sp, y = u.y / sp } or o:getDirectionVector()
        local dl = math.sqrt(dir.x * dir.x + dir.y * dir.y)
        local s0 = seenAt[id]
        if not s0 or (p.x - s0[1]) ^ 2 + (p.y - s0[2]) ^ 2 > M.JUMP_M ^ 2 then
          putAhead[id] = putAhead[id] == 'next' or nil   -- (ours just now, or moved by the stock respawn since)
          s0 = { p.x, p.y, now }
        end
        seenAt[id] = { p.x, p.y, s0[3] }
        local reach = M.cutReach(now - s0[3], sp, putAhead[id])
        local toCut = false
        if dl > 1e-6 then
          local ux, uy = dir.x / dl, dir.y / dl
          local n1, n2 = nil, nil
          if lastNodes and map and map.findBestRoad then n1, n2 = map.findBestRoad(p, vec3(ux, uy, 0)) end
          local ahead = n1 and n2 and M.cutAhead(lastNodes, cutNames, n1, n2, p.x, p.y, ux, uy, reach)
          if ahead == nil then ahead = M.headingToCut(p.x, p.y, ux, uy, cuts, reach) end
          toCut = ahead
        end
        if toCut then stats.cutRespawns = (stats.cutRespawns or 0) + 1 end
        if not paused and o:getVelocity():length() < M.STUCK_MS then
          stillSince[id] = stillSince[id] or now
        else
          stillSince[id] = nil
        end
        local d = pp and (p - pp):length() or math.huge
        if all or toCut or not M.inside(b, p.x, p.y, M.GUARD_M) or M.stuck(stillSince[id], now, d) then
          stillSince[id] = nil
          -- behind the camera: the stock default is 100-500 m in front of it, where the player sees the car
          -- pop in and its tyres smoke as it lands (the photo comparisons, 2026-10-14)
          local cp, cf = core_camera and core_camera.getPosition(), core_camera and core_camera.getForward()
          local pu = pv and pv:getVelocity()
          local ax, ay, adx, ady, amin, amax = nil
          if pp and pu and not all then ax, ay, adx, ady, amin, amax = M.aheadSpot(pp.x, pp.y, pu.x, pu.y, b) end
          if ax then
            stats.aheadRespawns = (stats.aheadRespawns or 0) + 1
            putAhead[id] = 'next'       -- (its jump, seen at the next check, is ours)
            pcall(gt.forceTeleport, id, vec3(ax, ay, pp.z), vec3(adx, ady, 0), amin, amax)
            -- (the stock speed boost: its traffic vehicle pushes the car along its heading on its next update)
            local td = gt.getTrafficData and gt.getTrafficData()[id]
            if td and not td.hasTrailer then td.respawnSpeed = M.aheadSpeed(pu:length()) end
          elseif cp and cf then
            pcall(gt.forceTeleport, id, cp, -cf, M.RESPAWN_M[1], M.RESPAWN_M[2])
          else
            pcall(gt.forceTeleport, id)
          end
        end
      else
        stillSince[id] = nil
      end
    end
  end
end

-- The player's traffic choice (the user, 2026-10-14: "if a player has them on then yes, if not then no"): the
-- level-start configurator's (Disabled / Parked only / Normal / Police; freeroam's spawningOptionsHelper), taken at
-- mission start by M.takeChoice. The radial menu's traffic entries work at any time as on stock maps.
M.choice = { traffic = false, parked = false, police = false, trafficAmount = -1, parkedAmount = -1 }

-- With no choice made there (freeroam's own default, "fromSetting", which spawns none) the AI traffic setting
-- decides (settings.lua aiTraffic: the pause menu's BeamNG.Earth page): off, light, normal (the game's own amount for
-- this PC) or busy, as a share of the game's amount. Normal by default: the drive from Frederick to Castle Rock had
-- an empty I-25 unless traffic was picked at every level start (the user, 2026-10-28: "make I-25 feel alive").
M.MODE = 'normal'
M.MODES = { off = 0, light = 0.5, normal = 1, busy = 1.5 }
M.LIGHT_MIN = 3
M.BUSY_MAX = 24         -- every traffic car is a full physics car: a cap on the CPU it takes
M.BUSY_MORE = 4         -- busy is at least this many more than normal

function M.setMode(v)
  if M.MODES[v] then M.MODE = v end
  return M.MODE
end

-- ... and the stock parked cars (parking.lua) the same way (settings.lua parkedCars): with no choice made at level start
-- the drive's streets and lots were empty, the stock system never started (2026-10-28, "make the drive feel lived-in").
-- They are the light simple_traffic cars, engines off, moved to spots ahead as the player drives (gameplay/parking.lua).
M.PARKED = 'normal'
M.PARKED_MODES = { off = 0, few = 0.5, normal = 1, many = 2 }
M.PARKED_MAX = 32
M.PARKED_FEW_MIN = 4

function M.setParked(v)
  if M.PARKED_MODES[v] then M.PARKED = v end
  return M.PARKED
end

-- Pure: the player's own parked-car amount (nil: automatic) from Options > Gameplay's setting, trafficParkingAmount
-- since 0.40 (default 5; 0: automatic, the stock setupVehicles' clamp(getIdealSpawnAmount, 4, 16)), and the older
-- trafficParkedAmount it replaced, which 0.40 ignores but a player's settings.json still holds (Jeff's PC: 16 there, 5
-- read): the larger.
function M.ownParked(new, old)
  local a, b = tonumber(new) or 0, tonumber(old) or 0
  local n = math.max(a, b)
  return n >= 1 and n or nil
end

-- Pure: the parked-car amount for gameplay_parking.setupVehicles in parked mode `mode`, given the player's own amount
-- `own` (M.ownParked; nil: the game's automatic amount): 0 none, -1 the game's own (automatic).
function M.parkedAmount(mode, own)
  local f = M.PARKED_MODES[mode]
  if not f or f <= 0 then return 0 end
  if not own or own < 1 then return -1 end
  if f == 1 then return own end
  local n = math.floor(own * f + 0.5)
  if f < 1 then return math.min(own, math.max(M.PARKED_FEW_MIN, n)) end
  return math.min(M.PARKED_MAX, math.max(own, n))
end

-- Pure: the number of cars for gameplay_traffic.setupTraffic in traffic mode `mode`, given the game's own amount
-- `auto` (gameplay_traffic.getIdealSpawnAmount; nil unknown): 0 none, -1 the game's own (with its extra pooled cars).
function M.modeAmount(mode, auto)
  local f = M.MODES[mode]
  if not f or f <= 0 then return 0 end
  if f == 1 or not auto or auto < 1 then return -1 end
  local n = math.floor(auto * f + 0.5)
  if f < 1 then return math.min(auto, math.max(M.LIGHT_MIN, n)) end
  return math.min(M.BUSY_MAX, math.max(auto + M.BUSY_MORE, n))
end

-- Pure: does the watched traffic (w: { untilT, seenOn, done }, nil: not watched) want setting up again at `now`, in
-- gameplay_traffic state `state`? Once it has run ('on') and gone 'off' within the watch, once.
function M.shouldRestart(w, now, state)
  return w ~= nil and not w.done and w.seenOn and state == 'off' and now <= w.untilT
end

-- Pure: the explicit number of cars to set up again (the game's own amount `auto` with nothing subtracted: the cars
-- it lost may still be about) for the choice c; 0 none.
function M.restartAmount(c, auto)
  if not (c and c.traffic) then return 0 end
  auto = tonumber(auto) or 0
  if c.mode then
    local n = M.modeAmount(c.mode, auto)
    return n < 0 and math.max(0, auto) or n
  end
  return (c.trafficAmount or -1) > 0 and c.trafficAmount or math.max(0, auto)
end

-- Pure on its argument: the choice in freeroam's spawning options `o`, which are then set to disabled: freeroam would
-- spawn at camera-ready, before our first road graph is in the map; we start them once it is.
local heldOpts = nil    -- { options table, its traffic values } until freeroam's spawn time has passed

-- The player's options back as they chose them (the level-start configurator shows them as the current choice next
-- time): once our cars started, and when the level ends.
function M.restoreChoice()
  if not heldOpts then return end
  local o, v = heldOpts[1], heldOpts[2]
  o.trafficMode, o.trafficParked, o.trafficPolice = v[1], v[2], v[3]
  heldOpts = nil
end

function M.takeChoice(o)
  -- level start runs twice (seen 2026-10-14): the second would read the options we just disabled
  if heldOpts and heldOpts[1] == o then return M.choice end
  heldOpts = nil
  if type(o) ~= 'table' then
    M.choice = { traffic = false, parked = false, police = false, trafficAmount = -1, parkedAmount = -1 }
    return M.choice
  end
  local fromSetting = o.trafficMode ~= 'enabled' and o.trafficMode ~= 'disabled'
  -- The parked cars' option is only ever "enabled" or "disabled", "disabled" by default (stock freeroam.lua's
  -- spawningOptionsHelper): with traffic left to the settings (the configurator not applied) it was never chosen, so
  -- the parkedCars setting decides it too. Picked at level start with the amount on automatic (the configurator never
  -- sets one): the setting's amount (0.40 reads only trafficParkingAmount, 5 by default).
  local parkedFromSetting = fromSetting and o.trafficParked ~= 'enabled'
  local st0 = rawget(_G, 'settings')
  local own = st0 and st0.getValue and M.ownParked(st0.getValue('trafficParkingAmount'), st0.getValue('trafficParkedAmount'))
  M.choice = { traffic = o.trafficMode == 'enabled' or (fromSetting and (M.MODES[M.MODE] or 0) > 0),
               parked = o.trafficParked == 'enabled' or (parkedFromSetting and (M.PARKED_MODES[M.PARKED] or 0) > 0),
               police = o.trafficPolice == 'enabled', trafficAmount = tonumber(o.trafficAmount) or -1,
               -- (0.40 renamed trafficParkedAmount to trafficParkingAmount)
               parkedAmount = tonumber(o.trafficParkingAmount or o.trafficParkedAmount) or -1,
               mode = fromSetting and M.MODE or nil, parkedMode = parkedFromSetting and M.PARKED or nil }
  if M.choice.parkedMode then
    M.choice.parkedAmount = M.parkedAmount(M.PARKED, own)
  elseif M.choice.parked and M.choice.parkedAmount < 0 then
    local a = M.parkedAmount(M.PARKED, own)
    if a > 0 then M.choice.parkedAmount = a end
  end
  -- (as stock freeroam: with traffic and parked cars both chosen, Options > Gameplay's parked vehicles setting off
  -- means none; GitHub #320 / #145: parked cars came with the setting off)
  local st = rawget(_G, 'settings')
  if M.choice.traffic and M.choice.parked and st and st.getValue and st.getValue('trafficParkedVehicles') == false then
    M.choice.parked = false
  end
  if log then
    log('I', 'beamngEarth', string.format('traffic choice: traffic %s, parked %s, police %s (freeroam %s / %s%s%s)',
      tostring(M.choice.traffic), tostring(M.choice.parked), tostring(M.choice.police), tostring(o.trafficMode),
      tostring(o.trafficParked), M.choice.mode and (', AI traffic setting ' .. M.choice.mode) or '',
      M.choice.parkedMode and (', parked cars setting ' .. M.choice.parkedMode) or ''))
  end
  heldOpts = { o, { o.trafficMode, o.trafficParked, o.trafficPolice } }
  o.trafficMode, o.trafficParked, o.trafficPolice = 'disabled', 'disabled', 'disabled'
  return M.choice
end

-- The game's own traffic amount here: the setting's (gameplay_traffic.getIdealSpawnAmount without its subtraction) less
-- the player's other cars. The stock subtraction counts every active vehicle that is not flagged parked, and parked
-- cars still spawning are not flagged yet: at a dense spot on I-25 (16 of them) the amount was -9 and no traffic came
-- (2026-10-28, a level started at the player's last spot).
local function ownAmount(gt)
  local okB, base = pcall(gt.getIdealSpawnAmount, nil, false)
  local okV, vs = pcall(function() return require('beamngEarth/collisionfoci').vehicles() end)
  return M.autoAmount(okB and tonumber(base) or nil, okV and vs or {}, playerId())
end

-- Pure: base (the setting's amount; nil unknown) less the 'player' vehicles (collisionfoci.vehicles) other than me.
function M.autoAmount(base, vehicles, me)
  if not base then return nil end
  local others = 0
  for _, v in ipairs(vehicles) do
    if v.kind == 'player' and v.id ~= me then others = others + 1 end
  end
  return math.max(0, base - others)
end

-- Set up n traffic cars, plus the setting's extra pooled ones (as the stock automatic amount does).
local function setupCars(gt, n, police)
  local st = rawget(_G, 'settings')
  local extra = st and st.getValue and st.getValue('trafficExtraVehicles') and math.max(0, tonumber(st.getValue('trafficExtraAmount')) or 0) or 0
  local ok, err = pcall(gt.setupTraffic, n + extra, { police = police, activeAmount = n })
  if not ok then log('W', 'beamngEarth', 'navgraph: setupTraffic: ' .. tostring(err)) end
end

local function startTraffic()
  started = true
  M.restoreChoice()       -- freeroam's own spawn time has passed
  local c = M.choice
  local gt = c.traffic and trafficSystem() or nil
  if gt and gt.getState() ~= 'on' then
    local auto = ownAmount(gt)
    local amount = c.trafficAmount ~= 0 and c.trafficAmount or -1
    if c.mode then amount = M.modeAmount(c.mode, auto) end
    if amount < 0 then amount = auto or -1 end
    log('I', 'beamngEarth', string.format('AI traffic %s: %s cars (the game\'s own amount here: %s)', tostring(c.mode or 'chosen'),
      amount < 0 and 'the game\'s' or tostring(amount), tostring(auto or '?')))
    if amount > 0 then
      setupCars(gt, amount, c.police)
      watch = { untilT = os.clock() + M.WATCH_S, seenOn = false, done = false }
    elseif amount < 0 then
      local ok, err = pcall(gt.setupTraffic, -1, { police = c.police })
      if not ok then log('W', 'beamngEarth', 'navgraph: setupTraffic: ' .. tostring(err)) end
      watch = { untilT = os.clock() + M.WATCH_S, seenOn = false, done = false }
    end
  end
end

-- The watched traffic went off: the traffic cars it dropped (flagged traffic, not in its table) deleted, and the
-- choice's cars set up again.
local function restartTraffic()
  local gt = gameplay_traffic
  if not gt then return end
  local td = gt.getTrafficData and gt.getTrafficData() or {}
  local me, gone = playerId(), 0
  for i = (be and be.getObjectCount and be:getObjectCount() or 0) - 1, 0, -1 do
    local o = be:getObject(i)
    if o and o:getId() ~= me and require('beamngEarth/collisionfoci').flagKind(o) == 'traffic' and not td[o:getId()] then
      o:delete()
      gone = gone + 1
    end
  end
  local n = M.restartAmount(M.choice, ownAmount(gt))
  log('W', 'beamngEarth', string.format('navgraph: the traffic went off on its own: %d dropped cars deleted, %d set up again', gone, n))
  if n > 0 then setupCars(gt, n, M.choice.police) end
end

-- The player teleported: the traffic cars go inactive (no physics: their old positions have no ground) and come
-- back on the new place's graph once it is in.
local heldActive = nil
function M.onTeleport()
  respawnAll = true
  if started and M.choice.traffic then  -- (each teleport: watched again, one more restart allowed)
    local gt0 = gameplay_traffic
    watch = { untilT = os.clock() + M.WATCH_S, seenOn = gt0 ~= nil and gt0.getState() == 'on', done = false }
  end
  local gt = gameplay_traffic
  if gt and gt.getState() == 'on' and not heldActive then
    local vars = gt.getTrafficVars and gt.getTrafficVars() or {}
    heldActive = vars.activeAmount or math.huge
    pcall(gt.setActiveAmount, 0)
  end
end

-- Every frame: a new graph, a moved block or a foreign reset applies the graph again; the traffic guard.
-- Pure: how far the centre of ground box b {x0, y0, x1, y1} is from that of box a (0 when either is missing).
function M.boxMoved(a, b)
  if not a or not b or not a[4] or not b[4] then return 0 end
  local dx = (b[1] + b[3]) / 2 - (a[1] + a[3]) / 2
  local dy = (b[2] + b[4]) / 2 - (a[2] + a[4]) / 2
  return math.sqrt(dx * dx + dy * dy)
end

local waited = 0
local stopwatch = require('beamngEarth/stopwatch')

-- The node tables an apply hands map.reset, built over several frames (toNodes in a coroutine, PREP_MS a frame; ~20 ms in
-- one frame on a Steam Deck), for this graph and block box; nil until done.
M.PREP_MS = 2.0
M.PREP_YIELD = 256
local prep = nil        -- { graph, box, co, result }

local function buildNodes(b)
  if not prep or prep.graph ~= graph or not (prep.box == b or (prep.box and b and prep.box[1] == b[1] and prep.box[2] == b[2]
      and prep.box[3] == b[3] and prep.box[4] == b[4])) then
    setRules()
    local g, box, rh = graph, b and { b[1], b[2], b[3], b[4] } or nil, M.rules.rightHandDrive
    prep = { graph = g, box = box, co = coroutine.create(function()
      return { M.toNodes(g, box, M.EDGE_M, rh, M.PREP_YIELD) }
    end) }
  end
  if prep.result then return prep.result end
  local sw = stopwatch.new()
  repeat
    local ok, res = coroutine.resume(prep.co)
    if not ok then
      log('W', 'beamngEarth', 'navgraph: nodes: ' .. tostring(res))
      prep = nil
      return nil
    end
    if coroutine.status(prep.co) == 'dead' then prep.result = res end
  until prep.result or sw() >= M.PREP_MS
  return prep.result
end

-- May the apply run in this frame (gov: the frame governor; nil: always)?
local function room(gov)
  if not (gov and gov.roomFor) or waited >= M.APPLY_WAIT_FRAMES or gov:roomFor(M.APPLY_ROOM_MS) then
    waited = 0
    return true
  end
  waited = waited + 1
  return false
end

function M.update(aid, b, gov)
  if not M.enabled or not graph then return end
  local t0 = os.clock()
  if applied and t0 - lastGuard >= M.GUARD_S then
    lastGuard = t0
    local ok, err = pcall(guard, b, false)
    if not ok then log('W', 'beamngEarth', 'navgraph guard: ' .. tostring(err)) end
    local g = (os.clock() - t0) * 1000
    stats.guardMs = g
    stats.guardMaxMs = math.max(stats.guardMaxMs or 0, g)
    local gt = watch and gameplay_traffic
    if M.MERGE and t0 - lastMerge >= M.MERGE_S then
      lastMerge = t0
      local okM, errM = pcall(function()
        local ids, me = {}, playerId()
        local tr = gameplay_traffic
        if tr and tr.getState() == 'on' then
          for id in pairs(tr.getTrafficData() or {}) do if id ~= me then ids[#ids + 1] = id end end
        end
        local brain = package.loaded['beamngEarth/aibrain']
        local n, new = merge.update(ids, lastNodes, capped, map.findBestRoad,
          brain and brain.controls and function(id) return brain.controls(id) end)
        stats.merging = n
        stats.mergeAssists = (stats.mergeAssists or 0) + new
      end)
      if not okM then log('W', 'beamngEarth', 'navgraph merge: ' .. tostring(errM)) end
    end
    if gt then
      local st = gt.getState()
      if st == 'on' then watch.seenOn = true end
      if M.shouldRestart(watch, t0, st) then
        watch.done = true
        stats.restarts = (stats.restarts or 0) + 1
        local okR, errR = pcall(restartTraffic)
        if not okR then log('W', 'beamngEarth', 'navgraph restart: ' .. tostring(errR)) end
      elseif t0 > watch.untilT then
        watch = nil
      end
    end
  end
  if graph.anchor ~= aid then
    graph = nil
    return
  end
  if pendingSignals then
    if not room(gov) then return end
    local nodes = pendingSignals
    pendingSignals = nil
    local sw = stopwatch.new()
    applySignals(nodes)
    if gov and gov.noteFrame then gov:noteFrame(sw()) end
    return
  end
  -- a small block move alone does not re-apply (each apply is a ~40 ms frame); one of RECLIP_M does
  if not (dirty or foreign) and applied and M.boxMoved(applied.box, b) >= M.RECLIP_M then dirty = true end
  if not (dirty or foreign) then return end
  local t = os.clock()
  if t - lastApply < M.MIN_GAP_S and not foreign then return end
  local pre = buildNodes(b)
  if not pre or not room(gov) then return end
  prep = nil
  lastApply = t
  dirty, foreign = false, false
  local sw = stopwatch.new()
  local okA = apply(b, pre)
  if gov and gov.noteFrame then gov:noteFrame(sw()) end
  if okA then
    applied = { anchor = aid, serial = graph.serial, box = b and { b[1], b[2], b[3], b[4] } or {} }
    if not started then
      startTraffic()
      respawnAll = false
    elseif respawnAll then
      respawnAll = false
      local gt = gameplay_traffic
      if gt and heldActive then
        pcall(gt.setActiveAmount, heldActive)
        heldActive = nil
        if gt.forceTeleportAll then pcall(gt.forceTeleportAll) end
      else
        pcall(guard, b, true)
      end
    end
  end
end

-- Pure-ish (xf: a geodesy Transform): graph g in the new frame of a rebase, as a new table for anchor aid: its nodes,
-- its signals' stop points, directions and heads (signal approaches {node, x, y, z, dx, dy, phase, hx, hy, hz,
-- heading deg}), the rest shared.
function M.moved(g, xf, aid)
  local nodes = {}
  for name, n in pairs(g.nodes) do
    local x, y, z = xf:apply(n[1], n[2], n[3])
    nodes[name] = { x, y, z, n[4] }
  end
  local rx, ry = xf:rotate(1, 0, 0)
  local turn = math.deg(math.atan2(ry, rx))      -- (counter-clockwise; a head's heading turns clockwise)
  local signals = {}
  for i, j in ipairs(g.signals or {}) do
    local app = {}
    for k, a in ipairs(type(j.app) == 'table' and j.app or {}) do
      local b = { unpack(a) }
      if a[2] then b[2], b[3], b[4] = xf:apply(a[2], a[3], a[4]) end
      if a[5] then
        local dx, dy = xf:rotate(a[5], a[6], 0)
        local l = math.sqrt(dx * dx + dy * dy)
        if l > 1e-9 then b[5], b[6] = dx / l, dy / l end
      end
      if a[8] then b[8], b[9], b[10] = xf:apply(a[8], a[9], a[10]) end
      if a[11] then b[11] = a[11] - turn end
      app[k] = b
    end
    local c = {}
    for key, v in pairs(j) do c[key] = v end
    c.app = app
    signals[i] = c
  end
  return { anchor = aid, serial = g.serial, nodes = nodes, edges = g.edges, signals = signals, rules = g.rules }
end

-- The world origin moved (core.lua finishRebase, xf old frame -> new, aid the new anchor): the graph moves with it and
-- goes into the map again at once. Until then the map held the old frame's roads ~15 km off, and the new anchor's
-- own graph comes only after half its road tiles (earthd nav_may_start): the traffic's paths and respawns pointed into
-- the void after every rebase on a long drive (I-25, 2026-10-28).
function M.transform(xf, aid)
  if not graph or not xf then return false end
  graph = M.moved(graph, xf, aid)
  for _, c in ipairs(cuts) do c[1], c[2] = xf:apply(c[1], c[2], 0) end
  for _, s in pairs(seenAt) do s[1], s[2] = xf:apply(s[1], s[2], 0) end
  pendingSignals, prep = nil, nil
  lastNodes = nil         -- (the old frame's: the guard's cut test falls back to the cone until the apply)
  dirty, lastApply = true, -math.huge
  return true
end

-- onNavgraphReloaded (core forwards the hook): someone else rebuilt the map; ours again next frame.
function M.onReloaded()
  if not ours and graph then foreign = true end
end

-- How many times a graph went into the game's map (gps.lua: the map's node table stays the same table across them).
function M.applies() return stats.applies end

function M.status()
  return { graph = graph and graph.serial or nil, applies = stats.applies, lastMs = stats.lastMs,
           nodes = stats.nodes, edges = stats.edges, junctions = stats.junctions, started = started,
           restarts = stats.restarts, cuts = stats.cuts, cutRespawns = stats.cutRespawns, aheadRespawns = stats.aheadRespawns,
           merging = stats.merging, mergeAssists = stats.mergeAssists,
           resetMs = stats.resetMs, signalsMs = stats.signalsMs, nodesMs = stats.nodesMs, gpsMs = stats.gpsMs,
           applyMs = stats.applyMs, guardMs = stats.guardMs, guardMaxMs = stats.guardMaxMs,
           routesKept = stats.routesKept, routesDropped = stats.routesDropped, routeDrops = stats.routeDrops,
           traffic = gameplay_traffic and gameplay_traffic.getState() or nil }
end

function M.reset()
  heads = {}            -- (the level took them with it)
  graph, applied, dirty, lastApply, persistent, ours, foreign = nil, nil, false, -math.huge, nil, false, false
  started, respawnAll, lastGuard, heldActive, watch = false, false, -math.huge, nil, nil
  pendingSignals = nil
  stillSince = {}
  seenAt = {}
  putAhead = {}
  paced = {}
  cuts = {}
  cutNames = {}
  lastNodes, capped, lastMerge = nil, {}, -math.huge
  M.restoreChoice()
  stats = { applies = 0, lastMs = 0, nodes = 0, edges = 0 }
  prep, waited = nil, 0
end

return M
