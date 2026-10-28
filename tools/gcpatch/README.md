# gcpatch: keep the grass when the physics block moves (optional; Linux, and Windows via gcpatchwin.py)

BeamNG.Earth moves its hidden physics TerrainBlock under the player every ~128 m. The engine
then frees every grass cell on that block. The mod hides this by regenerating the grass in the
same frame, at the cost of one slower frame per move (docs/ARCHITECTURE.md "Ground surfaces").
This preload library removes the cause instead. It changes one byte of the engine's GroundCover
terrain-update handler in the game's memory at startup, so that height and layer updates no longer
free grass. The mod still rebuilds the grass itself where the world really moves: teleports and
floating-origin rebases.

- **Written for BeamNG.drive 0.39.4, Linux x86-64.** It checks the handler's first 54 bytes at its
  0.39.4 address. In another build (a game update) it searches the game's code for the same bytes, with
  the frame size and the two jump distances masked, and patches only a single match. If the handler
  changed, it does nothing (the status file says so).
- Only on the BeamNG.Earth map. The mod creates `temp/beamngearth_gcpatch_on` in the game's user folder when its
  level starts and removes it when the level ends; the library looks for that file every 250 ms and puts the byte
  back to stock without it, so other maps (a terrain edited in the World Editor) keep the stock behaviour.
- Nothing on disk changes. Without the launch option the game is stock.
- It acts only in the main `BeamNG.drive.x64` process, not in the CEF helpers or Steam.
- Unsupported by BeamNG. A wrong patch crashes the game, which a restart without the launch
  option fixes.

## Install
The Linux download (the AppImage) and `python3 launcher.py` start the game with this library already preloaded:
nothing to install. By hand, for a game started from Steam:
```sh
tools/gcpatch/build.sh
```
Then go to Steam > BeamNG.drive > Properties > Launch options and enter the line that
`build.sh` prints:
```
LD_PRELOAD="/path/to/tools/gcpatch/build/libbngearth_gcpatch.so:$LD_PRELOAD" %command%
```
Then restart the game.

## Check
After the game starts, look at
`~/.local/share/BeamNG/BeamNG.drive/current/temp/beamngEarth_gcpatch.txt`.
It contains one line, such as `applied: grass kept across terrain block moves (BeamNG.Earth level)`,
`stock: another level`, or the reason it did nothing.

## Turn off
Remove the launch option, or add `BNGEARTH_GCPATCH_OFF=1` in front of it.

## Windows (gcpatchwin.py)
Windows has no `LD_PRELOAD`, so `launcher.py` patches the running game from outside instead: `gcpatchwin.py` finds
`BeamNG.drive.x64.exe`'s process, looks up the same handler in the executable's code (MSVC build: `test dl, 1; je;
<epilogue>; jmp <free all>; test dl, 0Eh; je <return>`, one match only, jumps and epilogue masked; 0.40.3: RVA
0x9d77a7), checks those bytes in the game's memory and writes the 0Eh -> 00h with `WriteProcessMemory`, toggled by
the same level flag file every 250 ms and put back to stock when the launcher stops first. Same status file. It also
patches a game that was already running or that Steam started, as long as the launcher runs. Off with
`launcher.py --no-gcpatch` or `BNGEARTH_GCPATCH_OFF=1`; by hand: `python gcpatchwin.py --user-dir <user folder>\current`;
`python gcpatchwin.py --find <BeamNG.drive.x64.exe>` prints where the byte is without a game.
