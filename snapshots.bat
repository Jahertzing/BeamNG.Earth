@echo off
rem BeamNG.Earth: the drive snapshot catalog (tools/snapshots.py) in your browser: the snapshots taken with
rem Ctrl+Alt+S (or the Stream Deck's Snapshot key), to go through after a drive. Keep this window open while you use it.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (".venv\Scripts\python.exe" tools\snapshots.py %*) else (py -3 tools\snapshots.py %*)
if errorlevel 1 pause
