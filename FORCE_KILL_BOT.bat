@echo off
title Force Kill AGY Bot
echo ========================================================
echo 🛑 Forcefully terminating all AGY Bot background processes...
echo ========================================================
powershell -Command "Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match 'bot.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo.
echo ✅ All bot instances killed successfully!
echo You can now use START_BOT.bat or install_startup.bat cleanly.
echo.
pause
