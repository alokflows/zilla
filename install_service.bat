@echo off
title AGY Bot — Install Service
echo.
echo ============================================
echo   Installing AGY Telegram Bot as Service
echo ============================================
echo.
echo This requires Administrator privileges.
echo.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Please run as Administrator!
    echo Right-click this file and select "Run as administrator"
    pause
    exit /b 1
)

set NSSM_PATH=C:\Users\Isha\AGI-Brain\Tools\nssm\nssm.exe
set PYTHON_PATH=C:\Users\Isha\AppData\Local\Programs\Python\Python312\pythonw.exe
set BOT_PATH=C:\Users\Isha\agy-telegram-bot-dev\bot.py
set BOT_DIR=C:\Users\Isha\agy-telegram-bot-dev
set SVC_NAME=AGYTelegramBotDev

if not exist "%NSSM_PATH%" (
    echo NSSM not found at %NSSM_PATH%
    echo Please download NSSM from https://nssm.cc/download
    echo and place nssm.exe in C:\Users\Isha\AGI-Brain\Tools\nssm\
    pause
    exit /b 1
)

echo Installing service: %SVC_NAME%
"%NSSM_PATH%" install %SVC_NAME% "%PYTHON_PATH%" "%BOT_PATH%"
"%NSSM_PATH%" set %SVC_NAME% AppDirectory "%BOT_DIR%"
"%NSSM_PATH%" set %SVC_NAME% DisplayName "AGY Telegram Bot (Dev)"
"%NSSM_PATH%" set %SVC_NAME% Description "AGI Brain Telegram Bot - Development Instance"
"%NSSM_PATH%" set %SVC_NAME% Start SERVICE_AUTO_START
"%NSSM_PATH%" set %SVC_NAME% AppStdout "%BOT_DIR%\logs\service_stdout.log"
"%NSSM_PATH%" set %SVC_NAME% AppStderr "%BOT_DIR%\logs\service_stderr.log"
"%NSSM_PATH%" set %SVC_NAME% AppStdoutCreationDisposition 4
"%NSSM_PATH%" set %SVC_NAME% AppStderrCreationDisposition 4
"%NSSM_PATH%" set %SVC_NAME% AppRotateFiles 1
"%NSSM_PATH%" set %SVC_NAME% AppRotateBytes 5000000
"%NSSM_PATH%" set %SVC_NAME% AppRestartDelay 5000
"%NSSM_PATH%" set %SVC_NAME% AppExit Default Restart

echo.
echo Starting service...
"%NSSM_PATH%" start %SVC_NAME%

echo.
echo ============================================
echo   Service installed and started!
echo   Name: %SVC_NAME%
echo   Auto-starts on boot.
echo   Auto-restarts on crash.
echo ============================================
echo.
pause
