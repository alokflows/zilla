# ============================================================
#  WINHIDE — Suppress all child-process console windows
# ============================================================
#  Importing this module patches subprocess so that EVERY child
#  process (ffmpeg via pydub, taskkill, anything shelled out)
#  is launched without flashing a black console window.
#
#  Import this FIRST, before anything that spawns subprocesses.
# ============================================================

import sys
import subprocess

if sys.platform == "win32":
    CREATE_NO_WINDOW = 0x08000000
    CREATE_NEW_CONSOLE = 0x00000010
    DETACHED_PROCESS = 0x00000008

    _orig_popen_init = subprocess.Popen.__init__

    def _silent_popen_init(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        # Respect callers that explicitly want their own console/detached process
        if not (flags & (CREATE_NEW_CONSOLE | DETACHED_PROCESS)):
            kwargs["creationflags"] = flags | CREATE_NO_WINDOW

        # Also suppress the window via STARTUPINFO as a belt-and-suspenders measure
        si = kwargs.get("startupinfo")
        if si is None:
            si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = si

        _orig_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _silent_popen_init
