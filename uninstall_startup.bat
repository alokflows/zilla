@echo off
title Zilla Bot - Remove Auto-Start
echo.
echo Removing Zilla auto-start (watchdog + any startup shortcut)...
echo.

powershell -NoProfile -Command "Unregister-ScheduledTask -TaskName 'ZillaBot' -Confirm:$false -ErrorAction SilentlyContinue"

del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Zilla Bot.lnk" 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AGY Bot.lnk" 2>nul

echo [DONE] Zilla will no longer start automatically.
echo        If it is running right now, double-click "Stop Zilla.vbs" to stop it.
echo.
pause
