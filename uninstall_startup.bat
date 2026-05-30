@echo off
title AGY Bot — Remove from Startup
echo.
set "SHORTCUT_PATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AGY Bot.lnk"
if exist "%SHORTCUT_PATH%" (
    del "%SHORTCUT_PATH%"
    echo [SUCCESS] AGY Bot removed from startup.
) else (
    echo AGY Bot was not in startup.
)
echo.
pause
