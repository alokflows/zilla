@echo off
title AGY Bot v8
echo.
echo ==========================================
echo    AGY Telegram Bot v8 — Thin Pipe to CLI
echo ==========================================
echo.
cd /d "%~dp0"
python bot.py
echo.
echo Bot stopped. Press any key to close.
pause >nul
