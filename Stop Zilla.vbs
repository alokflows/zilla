' ============================================================
'  Stop Zilla — double-click to kill the bot completely
' ============================================================
'  Runs fully hidden (no black console flash).
'  1) Drops a stop-flag so the auto-restart launcher won't
'     bring the bot back to life.
'  2) Kills the recorded PID and its whole process tree
'     (this includes the agy.exe CLI children it spawned).
'  3) Safety-net sweep: kills any python/pythonw still
'     running bot.py, plus the wscript launcher itself.
' ============================================================

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

' --- 1) Tell the launcher to stop restarting ---
stopFlag = scriptDir & "\zilla.stop"
Set sf = fso.CreateTextFile(stopFlag, True)
sf.WriteLine "stop"
sf.Close

' --- 2) Kill by recorded PID (whole tree, incl. agy.exe children) ---
pidFile = scriptDir & "\zilla.pid"
If fso.FileExists(pidFile) Then
    Set f = fso.OpenTextFile(pidFile, 1)
    pid = Trim(f.ReadAll)
    f.Close
    If pid <> "" Then
        WshShell.Run "taskkill /F /T /PID " & pid, 0, True
    End If
    On Error Resume Next
    fso.DeleteFile(pidFile)
    On Error GoTo 0
End If

' --- 3) Safety-net sweep (hidden): python(w) running bot.py + the launcher ---
psInner = "Get-CimInstance Win32_Process | " & _
    "Where-Object { " & _
    "(($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.CommandLine -like '*bot.py*') " & _
    "-or (($_.Name -eq 'wscript.exe' -or $_.Name -eq 'cscript.exe') -and $_.CommandLine -like '*Start Zilla*') " & _
    "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
psCmd = "powershell -NoProfile -ExecutionPolicy Bypass -Command " & Chr(34) & psInner & Chr(34)
WshShell.Run psCmd, 0, True

' Give the kills a moment, then clean up the stop flag
WScript.Sleep 1500
On Error Resume Next
If fso.FileExists(stopFlag) Then fso.DeleteFile(stopFlag)
On Error GoTo 0

MsgBox "Zilla bot has been stopped.", 64, "Zilla"
