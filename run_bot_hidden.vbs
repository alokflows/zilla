' ============================================================
'  Zilla Bot — Persistent Hidden Launcher ("parasite mode")
' ============================================================
'  Runs the bot with pythonw.exe so there is NO console window
'  and NO black flash, ever.
'
'  It RESTARTS the bot within a few seconds on ANY exit
'  (crash, error, even a clean exit) — UNLESS you stopped it on
'  purpose with "Stop Zilla.vbs".
'
'  Layer 2 (the Task Scheduler watchdog from install_startup.bat)
'  restarts THIS launcher if it ever gets killed, and starts it
'  at every login. Together: very hard to keep dead.
' ============================================================

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir
stopFlag = scriptDir & "\zilla.stop"

' Fresh start: clear any leftover stop flag from a previous run.
If fso.FileExists(stopFlag) Then fso.DeleteFile(stopFlag)

Do
    ' Did someone ask us to stop? Quit for good (clean exit = watchdog leaves us alone).
    If fso.FileExists(stopFlag) Then
        fso.DeleteFile(stopFlag)
        Exit Do
    End If

    ' Run the bot hidden (window style 0), and wait here until it exits.
    WshShell.Run "pythonw.exe bot.py", 0, True

    ' The bot exited. If a stop was requested, quit; otherwise restart fast.
    If fso.FileExists(stopFlag) Then
        fso.DeleteFile(stopFlag)
        Exit Do
    End If

    ' Short cooldown so an instant-crash loop doesn't peg the CPU.
    WScript.Sleep 7000
Loop
