@echo off
title AGY Bot — Remove Service
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Run as Administrator!
    pause
    exit /b 1
)
set NSSM_PATH=C:\Users\Isha\AGI-Brain\Tools\nssm\nssm.exe
set SVC_NAME=AGYTelegramBotDev
echo Stopping service...
"%NSSM_PATH%" stop %SVC_NAME%
echo Removing service...
"%NSSM_PATH%" remove %SVC_NAME% confirm
echo Service removed.
pause
