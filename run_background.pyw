# ============================================================
#  Zilla — Background Supervisor (no window, no admin, no .vbs)
# ============================================================
#  Launched with pythonw.exe, so it has NO console window at all.
#  It runs bot.py and restarts it ~7s after any exit, UNLESS a
#  "zilla.stop" file is present (that means you stopped it on
#  purpose with STOP_BACKGROUND.bat).
# ============================================================

import os
import sys
import time
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(BASE, "zilla.stop")
BOT = os.path.join(BASE, "bot.py")

# Prefer pythonw.exe so the bot child also has no console window.
PYW = sys.executable
if PYW.lower().endswith("python.exe"):
    _cand = os.path.join(os.path.dirname(PYW), "pythonw.exe")
    if os.path.exists(_cand):
        PYW = _cand

CREATE_NO_WINDOW = 0x08000000

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

    # Run the bot and wait for it to exit.
    try:
        subprocess.run([PYW, BOT], cwd=BASE, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass

    # Stopped on purpose? quit. Otherwise restart after a short cooldown.
    if os.path.exists(STOP):
        try:
            os.remove(STOP)
        except OSError:
            pass
        break

    time.sleep(7)
