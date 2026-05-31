@echo off
title Zilla Bot - Install Auto-Start (Persistent Watchdog)
echo.
echo ==========================================================
echo   Making Zilla start automatically and stay alive.
echo   If Windows asks for permission, click YES.
echo ==========================================================
echo.

set "VBS=%~dp0run_bot_hidden.vbs"
set "DIR=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "try {" ^
 "  $a = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('\"' + '%VBS%' + '\"') -WorkingDirectory '%DIR%';" ^
 "  $t = New-ScheduledTaskTrigger -AtLogOn;" ^
 "  $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero);" ^
 "  Register-ScheduledTask -TaskName 'ZillaBot' -Action $a -Trigger $t -Settings $s -Force | Out-Null;" ^
 "  Write-Host '[SUCCESS] Watchdog installed. Zilla starts at login and restarts itself if it ever dies.';" ^
 "} catch {" ^
 "  Write-Host '[INFO] Scheduled task blocked - using a simple Startup shortcut instead.';" ^
 "  $ws = New-Object -ComObject WScript.Shell;" ^
 "  $lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Zilla Bot.lnk';" ^
 "  $sc = $ws.CreateShortcut($lnk);" ^
 "  $sc.TargetPath = 'wscript.exe';" ^
 "  $sc.Arguments = ('\"' + '%VBS%' + '\"');" ^
 "  $sc.WorkingDirectory = '%DIR%';" ^
 "  $sc.Save();" ^
 "  Write-Host '[SUCCESS] Startup shortcut created (starts Zilla at login).';" ^
 "}"

echo.
echo Tip: to start it RIGHT NOW without rebooting, double-click run_bot_hidden.vbs
echo      (stop the current copy first with "Stop Zilla.vbs" to avoid duplicates).
echo.
pause
