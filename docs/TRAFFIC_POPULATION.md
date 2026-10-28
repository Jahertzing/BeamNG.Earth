# The persistent traffic population

`mod/.../beamngEarth/population.lua`, the setting **Seamless traffic (test)** (`trafficPopulation`, off by default).

The game's traffic is a handful of physics cars (7 on Jeff's PC at "normal", 11 at "busy"), and our world's collision
ground is a 2 km square that follows the player. Without this module the cars are recycled: the game's own logic moves
a car once it is 250-450 m away, and navgraph.lua's guard moves cars near the ground's edge, heading for a cut or left
behind at speed, 150-350 m ahead of the player. At 75 mph the player passes the same few cars over and over, each one
vanishing and reappearing.

With the population on:

- About a hundred **ghosts** live on the server's whole road graph (`navgraph.py` WINDOW_M, ~1.1 km around the player),
  each with an edge, a place on it, a lane, a speed and a pace (about +-6 %). Every 0.25 s they move at their road's
  speed times their pace, behind the car ahead in their lane (IDM), and at forks pick their way as the stock AI does
  (drivability ratio squared, straightness squared, lanes): alleys almost never, exits sometimes.
- Density is by road: cars per lane-km by the road's speed (`DENSITY`), times the AI traffic setting's share, at most
  `MAX_GHOSTS`. No ghost on a road of drivability under 0.5.
- The **nearest K ghosts on the ground** (K: the game's traffic cars) are real. A car swaps between ghost and real where
  it is: a demoted car's ghost carries on from the car's place and speed; a promoted ghost's car is put on its spot
  (`gameplay_traffic.respawnVehicle`) and pushed gently to its speed (navgraph `pushLua`). Hysteresis (`KEEP_FACTOR`)
  stops cars flickering; at most `SWAPS_PER` swaps every `SWAP_S`.
- A ghost that drives off the graph's edge, or ends up `BEHIND_M` behind, comes back at an entry of the graph (a road
  driving in from its edge) at least `ENTRY_MIN_M` away. A real car left off the ground with no ghost wanting it gets a
  spare ghost behind the camera.
- While it runs, the game's own recycling is off (`spawnValue = 0`) and navgraph's guard moves no car.
- New server graphs keep the ghosts whose edges they still have (node names are stable); a rebase moves the graph and
  so the ghosts with it; a teleport seeds them again.

Not yet: ghosts do not stop for signals or collide, and a car still turns real in view at the edge of the real zone
(typically 300-600 m). Showing far ghosts as simple moving models would hide that (the design doc's option E).

Measure: `tools/traffic_bench.py ... --population` (report: `pop` promoted / demoted / recycled / spares; ahead and cut
respawns should be zero), and `tools/traffic_probe.py --watch`. In the game: `navgraph.status().population`.
