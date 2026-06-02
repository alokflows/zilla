#!/usr/bin/env python3
# ============================================================
#  Zilla — Background Supervisor (cross-platform)
# ============================================================
#  Runs bot.py and restarts it ~7s after any exit, UNLESS a
#  "zilla.stop" file is present (you stopped it on purpose).
#
#  Windows : launched via pythonw.exe (no console) by the .bat / installer.
#  macOS   : launched by a LaunchAgent or `nohup python3 run_background.py &`.
#  Linux   : launched by a systemd --user unit or nohup.
#
#  Same stop mechanism on every OS: create the file `zilla.stop`
#  (the stop scripts do this) and the supervisor exits cleanly.
# ============================================================

import os
import sys
import time
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(BASE, "zilla.stop")
BOT = os.path.join(BASE, "bot.py")

# Prefer a no-console interpreter on Windows (pythonw); plain python elsewhere.
PY = sys.executable
if sys.platform == "win32" and PY.lower().endswith("python.exe"):
    cand = os.path.join(os.path.dirname(PY), "pythonw.exe")
    if os.path.exists(cand):
        PY = cand

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Clear any stale stop flag from a previous run.
try:
    if os.path.exists(STOP):
        os.remove(STOP)
except OSError:
    pass

while True:
    if os.path.exists(STOP):
        try:
            os.remove(STOP)
        except OSError:
            pass
        break

    try:
        kwargs = {"cwd": BASE}
        if sys.platform == "win32":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        subprocess.run([PY, BOT], **kwargs)
    except Exception:
        pass

    if os.path.exists(STOP):
        try:
            os.remove(STOP)
        except OSError:
            pass
        break

    time.sleep(7)
