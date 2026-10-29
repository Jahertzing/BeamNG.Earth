# Night exposure + headlights slot (about 30 min)

Game already in the level on main 9916f27e or later. Don't relaunch or install. Keep the game window focused (unfocused caps at 30 fps).

1. `cd C:\beamng-earth\BeamNG.Earth; git fetch origin feature/cats-eyes; git worktree add ..\wt-night-exp origin/feature/cats-eyes` (if it exists: `git checkout --detach origin/feature/cats-eyes` there). Use the main `.venv\Scripts\python.exe`. Run tools from the worktree; the game runs main's mod.
2. Build check: `python tools\mcp.py lua "return jsonEncode(require('beamngEarth/nightambient').status())"` > `C:\beamng-earth\night-tune\ne_status0.json`. It must have `fill` and `nightExposure` fields; if not, stop (wrong build).
3. `python tools\probe_look.py run --set tools\night_ambient_bench.json --variants tools\night_exposure_variants.json --out C:\beamng-earth\night-tune\exposure1 --perf 5` (about 15 min; Ctrl-C at 20 min, it restores).
4. Headlights (5 min):
   - `python tools\mcp.py lua "return tostring(require('beamngEarth/core').teleport(40.0995,-104.9375,nil,{record=false}))"`, wait 20 s.
   - `python tools\mcp.py lua "local t=core_environment.getTimeOfDay() t.time=0.5 t.play=false core_environment.setTimeOfDay(t) be:getPlayerVehicle(0):queueLuaCommand('electrics.setLightsState(2)') core_camera.setByName(0,'driver') return 'ok'"`, wait 8 s, `python tools\mcp.py call screenshot "{\"jpg\":true}"` (normal).
   - `python tools\mcp.py lua "require('beamngEarth/nightambient').setMode('off') return 1"`, wait 8 s, screenshot (off).
   - `python tools\mcp.py lua "local na=require('beamngEarth/nightambient') na.setMode('normal') na.NIGHT_BIAS_EV=1.5 return 1"`, wait 8 s, screenshot (ev1.5).
   - `core_camera.setByName(0,'orbit')`, wait 5 s, screenshot.
   - Restore: `local na=require('beamngEarth/nightambient') na.NIGHT_BIAS_EV=0.75 na.setMode('normal') be:getPlayerVehicle(0):queueLuaCommand('electrics.setLightsState(0)')`.
5. Stock sign materials (1 min, for the next round of reflective signs): `python tools\mcp.py lua "local r={} for _,n in ipairs({'signs_usa','ut_roadsigns','bne_rr_crossbuck'}) do local m=scenetree.findObject(n) r[n]=m and {v=m:getField('version',0), ef=m:getField('emissiveFactor',0), em=m:getField('emissive',0), nits=m:getField('emissiveIntensityNits',0)} or 'none' end return jsonEncode(r)"` > `C:\beamng-earth\night-tune\sign_mats.txt`.
6. Report honestly:
   - Per place and view: which variant (off, normal, ev1.5, fill_x3, fill_x0.3, hp0) looks most like a real city or suburb at night? Are walls, yards and trees visible between lamp pools? Is the sky over the city warm or black? Any halos, hard light circles or flicker?
   - Headlights: does the beam visibly light the road 30 to 60 m ahead, comparing off, normal and ev1.5?
   - fps and p95 per variant from run.json rows[].perf, and whether focused was true.
   - All file paths.
