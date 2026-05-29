@echo off
set SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AGYTelegramBot.lnk
if exist "%SHORTCUT%" del "%SHORTCUT%"
echo Startup shortcut removed.
pause
