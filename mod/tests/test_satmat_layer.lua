-- luajit mod/tests/test_satmat_layer.lua  (the near tiles' asphalt layer: satmat setTexture, 2026-10-22)
-- A tile whose road mask exists beside its texture gets the second layer (the mask as its opacity); a pooled material
-- moved on to a tile without one goes back to one layer, its base map following the texture.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
dofile(dir .. '/stubs.lua')
local satmat = require('beamngEarth/satmat')

local exists = {}
FS = { fileExists = function(_, p) return exists[p] == true end }
local function mat()
  local f = {}
  return { f = f,
    setField = function(self, k, l, v) f[k .. '/' .. l] = v end,
    getField = function(self, k, l) return f[k .. '/' .. l] end }
end

T.check(satmat.maskOf('/temp/s/far_3_-1_0_-1.dds') == '/temp/s/far_3_-1_0_-1_road.dds', 'mask beside the texture')
T.check(satmat.maskOf('/temp/s/far_3_-1_0_-1_r2.dds') == '/temp/s/far_3_-1_0_-1_road.dds', 'a revision: the same mask')

local m = mat()
satmat._setTexture(m, '/temp/s/far_3_0_0_0.dds')
T.check(m.f['colorMap/0'] == '/temp/s/far_3_0_0_0.dds' and m.f['activeLayers/0'] == nil, 'no mask: one plain layer')

exists['/temp/s/far_3_-1_0_-1_road.dds'] = true
satmat._setTexture(m, '/temp/s/far_3_-1_0_-1.dds')
T.check(m.f['activeLayers/0'] == '2' and m.f['version/0'] == '1.5', 'a mask: two layers')
T.check(m.f['opacityMap/1'] == '/temp/s/far_3_-1_0_-1_road.dds', 'the mask is the second layer\'s opacity')
T.check(m.f['baseColorMap/0'] == '/temp/s/far_3_-1_0_-1.dds', 'the photo underneath')
T.check(m.f['detailMap/1'] ~= nil and m.f['detailMapUseUV/1'] == '1', 'the asphalt layer tiled on UV1')
T.check(m.f['detailMap/1'] ~= nil, 'the asphalt grain on the second layer')

satmat._setTexture(m, '/temp/s/far_3_1_0_0.dds')       -- the pooled material moves on to a tile without a mask
T.check(m.f['activeLayers/0'] == '1', 'back to one layer')
T.check(m.f['baseColorMap/0'] == '/temp/s/far_3_1_0_0.dds', 'its base map follows the texture')

-- the grass layer (2026-10-28): a grass mask beside the texture adds the photo again with the grass grain, under the
-- asphalt layer when there is a road mask too
T.check(satmat.grassMaskOf('/temp/s/far_3_2_0_0_r3.dds') == '/temp/s/far_3_2_0_0_grass.dds', 'grass mask beside the texture')
exists[satmat.GRASS_DETAIL_MAP] = true
exists['/temp/s/far_3_2_0_0_grass.dds'] = true
local g = mat()
satmat._setTexture(g, '/temp/s/far_3_2_0_0.dds')
T.check(g.f['activeLayers/0'] == '2' and g.f['opacityMap/1'] == '/temp/s/far_3_2_0_0_grass.dds', 'a grass mask: two layers')
T.check(g.f['baseColorMap/1'] == '/temp/s/far_3_2_0_0.dds' and g.f['detailMap/1'] == satmat.GRASS_DETAIL_MAP,
  'the grass layer: the photo with the grass grain')
exists['/temp/s/far_3_2_0_0_road.dds'] = true
satmat._setTexture(g, '/temp/s/far_3_2_0_0_r1.dds')
T.check(g.f['activeLayers/0'] == '3' and g.f['opacityMap/2'] == '/temp/s/far_3_2_0_0_road.dds'
  and g.f['baseColorMap/2'] == '', 'grass and roads: the asphalt on top')
T.check(g.f['baseColorMap/1'] == '/temp/s/far_3_2_0_0_r1.dds', 'a revision: the grass layer follows the photo')
satmat._setTexture(g, '/temp/s/far_3_-1_0_-1.dds')      -- pooled: on to a tile with roads only
T.check(g.f['activeLayers/0'] == '2' and g.f['opacityMap/1'] == '/temp/s/far_3_-1_0_-1_road.dds'
  and g.f['baseColorMap/1'] == '' and g.f['detailMap/1'] ~= satmat.GRASS_DETAIL_MAP, 'layer 1 back to asphalt, every field')
satmat._setTexture(g, '/temp/s/far_3_2_0_0.dds', false)
T.check(g.f['activeLayers/0'] == '1', 'no UV1 (detail false): no layers')
local keys = {}
for _, f in ipairs(satmat.ASPHALT_FIELDS) do keys[f[1]] = (keys[f[1]] or 0) + 1 end
for _, f in ipairs(satmat.GRASS_FIELDS) do keys[f[1]] = (keys[f[1]] or 0) + 2 end
local same = true
for k, v in pairs(keys) do same = same and v == 3 end
T.check(same, 'the grass and asphalt layers set the same fields (a pooled layer moves between them)')

T.done('test_satmat_layer')
