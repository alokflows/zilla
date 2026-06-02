@echo off
REM Zilla installer (Windows) — just double-click this file.
title Zilla Installer
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py install.py
) else (
  python install.py
)
echo.
pause
