# Build ahead: the near tier built live along the route (2026-10-29)

Jeff, 2026-10-29: eliminate the route bake if it can be helped; keep the procedural world, drive anywhere, keep the far
view. The bake drove a simulated car along a planned route so the server built, into its revisit cache, exactly the
blocks and road tiles the mod asks for along it (tools/headless_bake.py, 1.5-2.5 h for the Denver corridor). This moves
that cursor into the live server, a few km ahead of the real car: `server/buildahead.py`, wired into `earthd.py`.

## What it does

- **The line.** The GPS route the mod set (`route` -> `Connection.route`; a client may send its own `points`,
  headless_bake does), sampled every 128 m from the car's place on it (within 300 m of the route; else off-route). With
  no route: the car's heading, a straight line 4 km long, planned again after a 20 degree turn or 500 m.
- **The frames.** Items live in anchor frames, and the anchor the mod picks depends on where the car is when it crosses
  the rebase distance. `frames()` walks the line under the mod's own rules: the distance rule first (a new anchor at
  `rebaseM - 300 m`, landing on a known pin within the pin distance else the snapped grid point: `rebase.lua`,
  `core.lua rebaseAnchor`), then, with a mod that applies the pinned-anchor rule, the pinned walk over the pins and the
  anchors just planned (`routepins.want`: the frame moves to a planned anchor as soon as it is 300 m nearer than the
  current one). The planned anchors go to the mod as `route_anchors` (`routepins.set`, kept with the bake file's), so
  the car re-centres exactly where the server built. A mod that sends `pins: true` with its route gets the pinned
  plan; an older one is planned under the distance rule alone.
- **The items.** Road tiles: `roads.tiles_near` within `SEND_M` of every sample (what the tracker wants when the car
  is there), due at the first sample that wants them. Blocks: terrain-follow simulated along the line every 16 m at the
  car's speed (`earthd.predict_centre` when the car leaves the prepare box: the same rule the live prefetch predicts
  with), each centre once. Ordered by due distance, blocks before tiles at the same distance.
- **The horizon.** Items due within `speed x (4 x latency + 45 s)`, 2.5-15 km (`--ahead-km`), are started, nearest
  first, 1 block + 2 road tiles at a time (`--ahead-jobs`). `latency` is the exponential average of an item's queued-
  to-done time (8 s before anything is measured): slow builds push the horizon out. Items the live path already has
  (the tracker sent or is building the tile, the block is in the heightmap cache) are counted done, not built again.
- **The builds.** In *phantom anchors* (`Server.phantom_anchor`: an Anchor at the planned place with an id from
  1,000,000; the revisit cache keys on the place, not the id, so the mod's anchor there finds everything).
  `build_ahead_block` = `make_heightmap` at the prefetch lane (block + base photo into blockcache), the session copies
  deleted; `build_ahead_roads` = `build_roads_tile` in the road processes (`roads_tile_cached` first, through
  `blockworker.roads_key`), the tile's JSON and shapes deleted again. Both skip what the cache has. Road tiles are only
  built ahead when the server has road processes (the in-process road path does not cache).
- **The measure.** The *ready front* is the route distance up to which every item is done. While the cursor has work
  queued it accounts the time and the front's advance: `rate` = metres of route made ready per second = the top speed
  at which the build keeps up. The log says every 30 s: `build ahead: 6.2 km ready of 7.5 km ahead (29 m/s, 310 items
  planned, 3 building, 140 built, 22 cached, 0 failed; latency 1.8 s; build rate 1.9 km/min = keeps up to 71 mph)`.
  `load_progress` carries `ahead: [m ready, horizon m]`.

## Not in the first version

Trees (not in the revisit cache: built live, cheap), the near far-field ring (L-1/L0 tiles within 400 m; `far` was a
quarter of the bake's waiting time at 80 m/s, so it comes next if the numbers say so), the AI road graph. A car that
leaves the predicted block (a different speed or line than planned) falls back to the live prefetch, as today.

## Measuring it

`tools/headless_bake.py --cold` runs the server with its revisit cache in a throwaway subdir and no baked items or
planned anchors (`BNE_REVISIT_SUBDIR`, `BNE_REVISIT_PIN_DIR`): a build-cold, data-warm drive, what a fresh area costs
with the elevation, imagery and map data on disk. `--ahead off` is the baseline (the live path alone), `--ahead on` the
build-ahead; `--speed N --fly N` holds the pace. The headline comparison on the I-25 corridor, then the speeds:

    python tools\headless_bake.py --routes i25 --near 39.60,-104.87 --km 10 --cold --ahead off --speed 30 --fly 30 --mode alone
    python tools\headless_bake.py --routes i25 --near 39.60,-104.87 --km 10 --cold --ahead on  --speed 30 --fly 30 --mode alone
    python tools\headless_bake.py --routes i25 --near 39.60,-104.87 --km 10 --cold --ahead on  --speed 60 --fly 60 --mode alone
    python tools\headless_bake.py --routes i25 --near 39.60,-104.87 --km 10 --cold --ahead on  --speed 90 --fly 90 --mode alone

Read each run's `summary.json` (`seconds_by`, `waits`, `ahead: {min_ready_m, starved_s}`) and the `build ahead:` lines
of its `server*.log`. In the game: set a GPS destination and watch the server log.

## Switches

Server `--build-ahead on|off` (env `BNE_BUILD_AHEAD=off`), `--ahead-km` (horizon cap, 15), `--ahead-jobs` (road tiles
at once, 2). Constants in `buildahead.py`: `MIN_M`, `MAX_M`, `MARGIN_S`, `LATENCY_K`, `BLOCK_JOBS`, `HEADING_MAX_M`.
