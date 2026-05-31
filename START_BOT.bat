@echo off
title Zilla Bot (Debug / Visible)
echo.
echo ==========================================
echo    Zilla Telegram Bot — DEBUG MODE
echo ==========================================
echo.
echo  This window is for watching logs / debugging.
echo  For normal silent use, double-click instead:
echo     "Start Zilla (Invisible).vbs"
echo  To stop the bot at any time:
echo     "Stop Zilla.vbs"
echo.
cd /d "%~dp0"
python bot.py
echo.
echo Bot stopped. Press any key to close.
pause >nul
