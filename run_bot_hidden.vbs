' Zilla Bot — Persistent Hidden Launcher (used by Windows startup)
' Runs the bot with pythonw.exe (no console window, no black flash).
' Auto-restarts on crash after a 10-second cooldown.
' Respects "zilla.stop" so the Stop script can shut it down for good.

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

stopFlag = scriptDir & "\zilla.stop"
If fso.FileExists(stopFlag) Then fso.DeleteFile(stopFlag)

Dim exitCode
Do
    If fso.FileExists(stopFlag) Then
        fso.DeleteFile(stopFlag)
        Exit Do
    End If

    exitCode = WshShell.Run("pythonw.exe bot.py", 0, True)
    If exitCode = 0 Then Exit Do

    If fso.FileExists(stopFlag) Then
        fso.DeleteFile(stopFlag)
        Exit Do
    End If

    WScript.Sleep 10000
Loop
