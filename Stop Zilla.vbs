' ============================================================
'  Stop Zilla — double-click to stop the bot now
' ============================================================
'  Runs fully hidden (no black console flash).
'
'  It stops the bot until the next time you log in / reboot
'  (the auto-start watchdog brings it back then). To remove
'  auto-start permanently, run "uninstall_startup.bat".
'
'  How it stops cleanly WITHOUT the watchdog reviving it:
'    1) Drop a "zilla.stop" flag so the restart loop quits on purpose.
'    2) Kill the bot's recorded PID (and its agy.exe children).
'  The hidden launcher then exits BY ITSELF via that flag — the
'  watchdog sees a clean stop and does not restart it.
' ============================================================

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

' --- 1) Tell the restart loop to quit on purpose ---
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

' --- 3) Safety net: kill any python(w) still running bot.py (PID file may be stale).
'        NOTE: we deliberately do NOT kill the wscript launcher — it must exit on
'        its own via the stop flag so the watchdog treats it as a clean stop. ---
psInner = "Get-CimInstance Win32_Process | " & _
    "Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.CommandLine -like '*bot.py*' } | " & _
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
psCmd = "powershell -NoProfile -ExecutionPolicy Bypass -Command " & Chr(34) & psInner & Chr(34)
WshShell.Run psCmd, 0, True

WScript.Sleep 1500
MsgBox "Zilla has been stopped." & vbCrLf & _
       "It will start again next time you log in." & vbCrLf & _
       "(To stop that too, run uninstall_startup.bat.)", 64, "Zilla"
