@echo off
title AGI Brain - Telegram Bot v4
echo.
echo ==========================================
echo    Starting AGI Brain - Mother Bot v4
echo ==========================================
echo.
cd /d "%~dp0"
python bot.py
echo.
echo Bot stopped. Press any key to close.
pause >nul
