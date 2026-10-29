# Night slot, round 3 (about 25 min): the night exposure by hand, headlights

Game in the level on a main that has feature/night-exposure3 (nightambient NIGHT_EV, headlights x2.5/x2). Don't relaunch or install. Keep the game window focused. Set the time only through `sky.setLocalHour` (live, not saved) and `sky.restoreLive` at the end, never raw `core_environment.setTimeOfDay`.

0. If the game still has round 2's stuck target: `python tools\mcp.py lua "local o=scenetree.findObject('PostEffectLocalExposureObject') o.autoTargetLuminance=true o.targetLuminance=0.7 return 1"` (a level reload also clears it).
1. Tools from the main checkout (or a worktree on that main), main `.venv\Scripts\python.exe`.
2. Build check to `C:\beamng-earth\night-tune\ne3_status0.json`:
   `python tools\mcp.py lua "return jsonEncode({na=require('beamngEarth/nightambient').status(), hl=require('beamngEarth/headlights').status()})"`
   It must show `wantEV` (a number at night) and `hl.lamps` >= 1.
3. `python tools\probe_look.py run --set tools\night_ambient_bench.json --variants tools\night_exposure3_variants.json --out C:\beamng-earth\night-tune\exposure3 --perf 5` (about 14 min). Variants: auto (stock auto exposure, as before), ev_default (EV 5.0 city / 1.5 country by the city share), ev_brighter (4.5 / 0.5), ev_darker (5.5 / 2.5), ev_bright_setting (default with the Night ambient setting on Bright, 0.75 EV brighter).
4. Headlights (5 min), 4th St Frederick, with the default night EV:
   - `python tools\mcp.py lua "return tostring(require('beamngEarth/core').teleport(40.0995,-104.9375,nil,{record=false}))"`, wait 20 s.
   - `python tools\mcp.py lua "require('beamngEarth/sky').setLocalHour(0,'night slot') be:getPlayerVehicle(0):queueLuaCommand('electrics.setLightsState(2)') return 'ok'"`, wait 12 s.
   - For stock, bright, brighter: `python tools\mcp.py lua "local h=require('beamngEarth/headlights') h.setMode('stock') return jsonEncode(h.status())"`, wait 4 s, `python tools\mcp.py call screenshot "{\"jpg\":true}"`.
   - Status to `ne3_status_fred.json` (same command as step 2): note heldEV and wantEV.
   - Restore: `python tools\mcp.py lua "require('beamngEarth/headlights').setMode('bright') be:getPlayerVehicle(0):queueLuaCommand('electrics.setLightsState(0)') require('beamngEarth/sky').restoreLive('night slot') return 1"`.
5. Report honestly:
   - Per place and view: which variant looks most like a real night, in the city (16th, Broadway) and in the suburb (Frederick)? Walls, yards, trees, road surface visible? Washed out, flat or grey? Is the city blown out in any?
   - Headlights: how far does the lit road reach now with stock, bright, brighter?
   - fps/p95 per variant and focused; the status JSONs verbatim; file paths.
