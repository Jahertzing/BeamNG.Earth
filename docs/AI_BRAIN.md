# An outside brain for the AI cars

`beamngEarth/aibrain.lua` (mod) streams the AI traffic cars' state to a program on this PC over UDP, 10 times a second,
and applies the actions it answers with. `tools/aibrain.py` is that program: a rule-based highway driver to start from,
three demo policies, a recorder and a plug-in point for your own model. Nothing runs until you start it, and it is not in
`modScript.lua`, so normal play never loads it.

BeamNGpy is not an option on a Steam copy of BeamNG.drive: it needs BeamNG.tech's research server (`tech/techCore.lua`),
which the consumer build does not ship. This bridge uses only what every copy has: luasocket and the vehicles' Lua.

## Run it

1. Start a brain (the repo's venv, any Python 3.9+ with numpy for the fit):

   ```
   python tools/aibrain.py                 # IDM + MOBIL, tactical level
   ```

2. In the game, with BeamNG.Earth loaded and AI traffic on, open the console (`~`) and run the line it printed:

   ```
   extensions.load('beamngEarth_aibrain'); beamngEarth_aibrain.start({ level = 'tactical' })
   ```

   `beamngEarth_aibrain.status()` shows datagrams sent and received, actions applied, the round trip and the last error.
   `beamngEarth_aibrain.stop()` hands every car back to the stock AI (also on level end).

`start` options: `level` (below), `player = true` to drive your own car too (target, raw or observe levels only),
`max = n` cars, `ids = { ... }` exact vehicles, `port`.

## The three levels

| level | the brain sends | the stock ai.lua still does | policy |
| --- | --- | --- | --- |
| tactical | `{lane: -1/0/1, speed: m/s or null}` | route, junctions, signals, car following, steering, pedals | `idm` (default) |
| target | `{ahead: m, left: m, speed: m/s}` in the car's frame | steering and pedals toward that point (`slotTraffic`), ABS and traction control | `lanekeep` |
| raw | `{steer: -1..1, throttle, brake}` (steer positive = right) | nothing | `steer`, `linear:<model>` |
| observe | nothing | everything | `stock` |

Safety: an action older than 0.5 s is not applied. A tactical car goes back to the legal speed, a raw car brakes fully,
a target car stops by itself (slotTraffic's own timeout). A car changes lane at most once every 4 s.

## What a car sees

One datagram per tick, all cars: `{seq, t, level, driveOnLeft, cars: [...]}`, each car:

- `speed` (m/s), `len` (m), `inputs` (`steer`, `throttle`, `brake`: what the car itself did last frame, the stock AI's or
  yours: the labels for learning from it)
- `road` (absent off the graph): `limit` (m/s, the graph's target), `oneWay`, `lanes` (my direction), `lane`
  (1 = leftmost), `laneWidth`, `laneErr` (m right of the lane centre), `heading` (sine of the road's direction to the left)
- `near`: up to 8 vehicles within 150 m, nearest first: `fwd`, `left` (m, my frame), `vFwd`, `vLeft` (m/s), `same`
  (going my way), `len`, `player`

Everything is relative to the car, so the 15 km floating-origin rebases change nothing for a brain.

## Your own model

`--policy mypkg.mymod:MyPolicy` loads any class with a `level` attribute and `act(car, msg) -> action | None`.
The learning loop that ships:

```
python tools/aibrain.py --policy stock --record runs/i25.jsonl       # game: start({ level = 'observe' })
python tools/aibrain_fit.py runs/i25.jsonl -o runs/model.json         # a linear behaviour clone
python tools/aibrain.py --policy linear:runs/model.json               # game: start({ level = 'raw' })
```

A fast structured-decision model (a "System One" model such as typesafe.ai's Jev, 70-500 ms a call) fits the tactical
level: the observation is the state, the lane and a speed band are two choice questions. It is too slow for raw
control, which needs an answer every frame or two.

## Tests

`luajit mod/tests/test_aibrain.lua`; `cd server && python -m unittest tests.test_aibrain_tool`.
