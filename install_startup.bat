@echo off
title AGY Bot — Install to Startup
echo.
echo Installing AGY Bot to Windows Startup...
echo.

set "VBS_PATH=%~dp0run_bot_hidden.vbs"
set "SHORTCUT_PATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AGY Bot.lnk"

:: Create shortcut using PowerShell (no admin needed)
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = 'wscript.exe'; $s.Arguments = '\"%VBS_PATH%\"'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'AGY Telegram Bot'; $s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo.
    echo [SUCCESS] AGY Bot will start automatically on login!
    echo Shortcut: %SHORTCUT_PATH%
) else (
    echo.
    echo [ERROR] Failed to create startup shortcut.
)
echo.
pause
