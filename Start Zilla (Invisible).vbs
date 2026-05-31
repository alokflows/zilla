' ============================================================
'  Start Zilla (Invisible) — double-click to run the bot
' ============================================================
'  Launches the bot with pythonw.exe so there is NO console
'  window and NO black flash, ever. Runs persistently and
'  auto-restarts if it crashes (10s cooldown).
'
'  To stop it: double-click "Stop Zilla.vbs".
' ============================================================

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

stopFlag = scriptDir & "\zilla.stop"

' Clear any leftover stop flag from a previous run
If fso.FileExists(stopFlag) Then fso.DeleteFile(stopFlag)

Dim exitCode
Do
    ' Stop requested? bail out before (re)starting
    If fso.FileExists(stopFlag) Then
        fso.DeleteFile(stopFlag)
        Exit Do
    End If

    ' window style 0 = hidden, bWaitOnReturn = True
    exitCode = WshShell.Run("pythonw.exe bot.py", 0, True)

    ' Clean shutdown → don't restart
    If exitCode = 0 Then Exit Do

    ' Killed by the Stop script → don't restart
    If fso.FileExists(stopFlag) Then
        fso.DeleteFile(stopFlag)
        Exit Do
    End If

    WScript.Sleep 10000
Loop
