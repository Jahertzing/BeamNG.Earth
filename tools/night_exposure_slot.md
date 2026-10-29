# Night exposure + headlights slot, round 2 (about 30 min)

Game in the level on a main that has feature/night-exposure2 (nightambient NIGHT_TARGET_X, headlights colour fix). Don't relaunch or install. Keep the game window focused (unfocused caps at 30 fps). Set the time only through `sky.setLocalHour` (live, not saved), never raw `core_environment.setTimeOfDay`, and `sky.restoreLive` at the end.

1. Tools from a worktree on the same main (or the main checkout). Use the main `.venv\Scripts\python.exe`.
2. Build check, saved to `C:\beamng-earth\night-tune\ne2_status0.json`:
   `python tools\mcp.py lua "return jsonEncode({na=require('beamngEarth/nightambient').status(), hl=require('beamngEarth/headlights').status()})"`
   `na.exposure` must be there (it shows the stock target luminance, auto target, manual EV); `hl.lastError` must not mention "expect userdata".
3. `python tools\probe_look.py run --set tools\night_ambient_bench.json --variants tools\night_exposure2_variants.json --out C:\beamng-earth\night-tune\exposure2 --perf 5` (about 18 min; Ctrl-C at 22 min, it restores). Variants: off, normal (sky light now 180/48), tl_x2, tl_x4, tl_x8 (night target luminance x the stock one), manual_m2, manual_m4 (fixed exposure EV -2, -4).
4. Headlights (6 min), at 4th St Frederick:
   - `python tools\mcp.py lua "return tostring(require('beamngEarth/core').teleport(40.0995,-104.9375,nil,{record=false}))"`, wait 20 s.
   - `python tools\mcp.py lua "require('beamngEarth/sky').setLocalHour(0,'night slot') be:getPlayerVehicle(0):queueLuaCommand('electrics.setLightsState(2)') return 'ok'"`, wait 10 s.
   - For each of stock, bright, brighter: `python tools\mcp.py lua "local h=require('beamngEarth/headlights') h.setMode('stock') return jsonEncode(h.status())"` (save the output; lamps should be 2 or more), wait 4 s, `python tools\mcp.py call screenshot "{\"jpg\":true}"`.
   - Then with the best-looking target from step 3 (say tl_x4): `python tools\mcp.py lua "require('beamngEarth/nightambient').NIGHT_TARGET_X=4 return 1"`, wait 10 s, screenshot with bright and brighter.
   - Restore: `python tools\mcp.py lua "local na=require('beamngEarth/nightambient') na.NIGHT_TARGET_X=nil na.NIGHT_PROPS={} require('beamngEarth/headlights').setMode('bright') be:getPlayerVehicle(0):queueLuaCommand('electrics.setLightsState(0)') require('beamngEarth/sky').restoreLive('night slot') return 1"`.
5. Final status (same command as step 2) to `ne2_status1.json`.
6. Report honestly:
   - Per place and view: which variant looks most like a real city street and a real suburb at night (walls, yards, trees between lamp pools; road surface visible; sky)? Is any variant washed out, grey or noisy? Is the suburb still near black in all?
   - Headlights: does the beam now visibly light the road 30 to 60 m ahead (stock vs bright vs brighter, and with the target)? Hard edges or a visible end of the beam?
   - fps and p95 per variant (run.json rows[].perf) and focused.
   - The two status JSONs, verbatim, and all file paths.
