@echo off
title AGY Bot — Add to Startup
echo.
echo ============================================
echo   Adding AGY Bot to Windows Startup
echo   (No admin required)
echo ============================================
echo.

set VBS_PATH=%~dp0run_bot_hidden.vbs
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT=%STARTUP_DIR%\AGYTelegramBot.lnk

REM Create the VBS launcher if it doesn't exist
if not exist "%VBS_PATH%" (
    echo Creating hidden launcher...
    (
        echo Set WshShell = CreateObject("WScript.Shell"^)
        echo WshShell.CurrentDirectory = "%~dp0"
        echo WshShell.Run "pythonw.exe bot.py", 0, False
    ) > "%VBS_PATH%"
)

REM Create shortcut in startup folder
echo Creating startup shortcut...
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%VBS_PATH%'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'AGY Telegram Bot'; $s.Save()"

echo.
echo ============================================
echo   Done! Bot will start when you log in.
echo   To start now: double-click run_bot_hidden.vbs
echo ============================================
pause
