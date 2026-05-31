@echo off
title Zilla - Stop Background
cd /d "%~dp0"

REM 1) Tell the supervisor to quit on purpose
echo stop> "%~dp0zilla.stop"

REM 2) Kill the bot + the supervisor
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and ($_.CommandLine -like '*bot.py*' -or $_.CommandLine -like '*run_background.pyw*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM 3) Remove auto-start at login
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Zilla Bot.lnk" 2>nul

echo.
echo [STOPPED] Zilla is off and will NOT auto-start.
echo Double-click START_BACKGROUND.bat to turn it back on.
echo.
pause
