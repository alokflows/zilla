@echo off
title Zilla - Start in Background (no window)
cd /d "%~dp0"

REM Find pythonw.exe (no-console Python)
set "PYW="
for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%P"
if not defined PYW (
  echo [ERROR] pythonw.exe not found. Install Python and tick "Add to PATH".
  pause
  exit /b 1
)

REM 1) Start the hidden supervisor now (no window; restarts the bot if it dies)
start "" "%PYW%" "%~dp0run_background.pyw"

REM 2) Auto-start at every login (your own Startup folder; no admin needed)
powershell -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $lnk=Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Zilla Bot.lnk'; $s=$ws.CreateShortcut($lnk); $s.TargetPath='%PYW%'; $s.Arguments='\"%~dp0run_background.pyw\"'; $s.WorkingDirectory='%~dp0'; $s.Save()"

echo.
echo [SUCCESS] Zilla is running in the background with NO window.
echo  - It restarts itself within ~10s if it ever crashes.
echo  - It starts automatically every time you log in.
echo To stop it: double-click STOP_BACKGROUND.bat
echo.
pause
