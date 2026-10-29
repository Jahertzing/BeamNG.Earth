-- luajit mod/tests/test_farghosts.lua  (the population's far ghosts drawn as pooled car models)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
function vec3(x, y, z) return { x = x, y = y, z = z } end
function log() end
local F = require('beamngEarth/farghosts')
local P = require('beamngEarth/population')

-- the car shapes from trees' catalog: car_* groups only, one per model with ONE_COLOUR
local catalog = { car_midsize = { { '/cars/bne_car_midsize_white', 1.4 }, { '/cars/bne_car_midsize_black', 1.4 } },
                  car_pickup = { { '/cars/bne_car_pickup_white', 1.9 } }, deciduous = { { '/trees/oak', 9 } } }
local one = F.shapesFrom(catalog, true)
T.check(#one == 2 and one[1].path == '/cars/bne_car_midsize_white.dae', 'shapesFrom: one per model, .dae: ' .. #one)
T.check(#F.shapesFrom(catalog, false) == 3, 'shapesFrom: every colour without ONE_COLOUR')
T.check(F.shapeFor(7, one) == F.shapeFor(7, one) and F.shapeFor(nil, {}) == nil, 'shapeFor: stable per ghost id')

-- the quaternion turning a model's +x (its front) to the ghost's heading
local qx, qy, qz, qw = F.yawQuat(1, 0)
T.check(qx == 0 and qy == 0 and math.abs(qz) < 1e-12 and math.abs(qw - 1) < 1e-12, 'yawQuat: east: no turn')
local _, _, qz2, qw2 = F.yawQuat(0, 1)
T.near(qz2, -math.sin(math.pi / 4), 1e-12, 'yawQuat: north: a quarter turn (conjugated, as setPosRot takes it)')
T.near(qw2, math.cos(math.pi / 4), 1e-12, '... its w')

-- which ghosts are drawn: not real, within FAR_M, the MAX nearest
local gs = {}
for i = 1, 10 do gs[i] = { id = i, x = i * 200 } end
gs[2].real = 55
local function poseOf(g) return g.x, 0 end
local old = F.MAX
F.MAX = 4
local ch = F.choose(gs, poseOf, 0, 0)
T.check(#ch == 4 and ch[1].g.id == 1 and ch[2].g.id == 3, 'choose: nearest first, the real one left out')
F.MAX = old
local all = F.choose(gs, poseOf, 0, 0)
T.check(#all == 5, 'choose: within FAR_M (1300 m: 200..1200, less the real one): ' .. #all)

-- the game side: models placed for the ghosts, hidden when a ghost turns real, pooled and reused
do
  local made, objs = 0, {}
  function createObject()
    made = made + 1
    local o = { id = made, hidden = true, fields = {} }
    function o:setField(k, _, v) self.fields[k] = v end
    function o:registerObject() objs[self.id] = self end
    function o:getID() return self.id end
    function o:setPosRot(x, y, z) self.pos = { x, y, z } end
    return o
  end
  scenetree = { findObjectById = function(id) return objs[id] end, MissionGroup = { addObject = function() end } }
  package.loaded['beamngEarth/trees'] = { CATALOG = catalog }
  local g = { nodes = { a = { 0, 0, 10, 5 }, b = { 2000, 0, 10, 5 } }, edges = { { 'a', 'b', 1, 2, 0, 30, 1, 5 } } }
  P.reset()
  P.setGraph(g)
  local l = P.edge(P.state.adj, 'a', 'b')
  P.state.ghosts = { { id = 1, from = 'a', link = l, s = 300, v = 30, lane = 1 },
                     { id = 2, from = 'a', link = l, s = 900, v = 30, lane = 1 },
                     { id = 3, from = 'a', link = l, s = 1900, v = 30, lane = 1 } }    -- (1.9 km off: not drawn)
  P.state.lastTick = 100
  F.reset()
  F.setEnabled(true)
  F.update(P, 100.5, 0, 0)
  local st = F.status()
  T.check(st.shown == 2 and made == 2, 'update: the two ghosts within FAR_M drawn, two models made: ' .. st.shown)
  T.check(F.showing(P.state.ghosts[1]) and not F.showing(P.state.ghosts[3]), 'showing: by ghost')
  local o1 = scenetree.findObjectById(1)
  T.check(not o1.hidden and o1.pos and math.abs(o1.pos[1] - 315) < 1e-6, 'update: moved on at its speed between ticks')
  T.check(o1.fields.collisionType == 'None' and o1.fields.dynamic == 'true', 'the models never collide, are dynamic')
  P.state.ghosts[1].real = 42                -- ghost 1 turned real: its model hidden and pooled
  F.update(P, 100.6, 0, 0)
  T.check(not F.showing(P.state.ghosts[1]) and F.status().shown == 1 and F.status().pooled == 1, 'turned real: pooled')
  P.state.ghosts[1].real = nil               -- and a ghost again: the pooled model reused
  F.update(P, 100.7, 0, 0)
  T.check(made == 2 and F.status().shown == 2, 'a ghost again: the pooled model reused, none made')
  F.setEnabled(false)
  T.check(F.status().shown == 0 and F.status().pooled == 2, 'off: every model back in the pool')
  package.loaded['beamngEarth/trees'] = nil
  createObject, scenetree = nil, nil
end

T.done('test_farghosts')
