' AGY Bot v8 — Persistent Hidden Launcher
' Runs the bot with pythonw.exe (no console window)
' Auto-restarts on crash after 10-second cooldown

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

Dim exitCode
Do
    exitCode = WshShell.Run("pythonw.exe bot.py", 0, True)
    ' If exit code is 0 (clean shutdown), stop
    If exitCode = 0 Then Exit Do
    ' Otherwise wait 10 seconds and restart
    WScript.Sleep 10000
Loop
