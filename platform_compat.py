# ============================================================
#  PLATFORM COMPAT — the ONLY place with OS-specific code
# ============================================================
#  Everything that differs between Windows / macOS / Linux lives
#  here so the rest of the bot is platform-agnostic:
#    - IS_WINDOWS / IS_MAC / IS_LINUX flags
#    - single-instance lock        (msvcrt   vs  fcntl)
#    - hidden child windows        (Windows-only; no-op elsewhere)
#    - PtyProcess                  (winpty   vs  stdlib pty)
#
#  The PTY abstraction is ONLY used by the agy backend (its CLI is a
#  TUI that needs a real terminal). The Claude Code backend uses plain
#  pipes and works on every OS without any of this.
# ============================================================

import os
import sys
import logging

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


# ── Single-instance lock ──────────────────────────────────
#  Returns an opaque handle if WE got the lock, or None if another
#  instance already holds it. Cross-platform: msvcrt on Windows,
#  fcntl on Unix.

def acquire_instance_lock(lock_path: str):
    try:
        fh = open(lock_path, "w")
    except OSError as e:
        logger.warning(f"[LOCK] cannot open {lock_path}: {e}")
        return None
    try:
        if IS_WINDOWS:
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fh.write(str(os.getpid()))
            fh.flush()
        except OSError:
            pass
        return fh
    except OSError:
        # Already locked by another live instance.
        try:
            fh.close()
        except OSError:
            pass
        return None


def release_instance_lock(handle, lock_path: str):
    if not handle:
        return
    try:
        if IS_WINDOWS:
            import msvcrt
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
    except OSError:
        pass
    try:
        os.remove(lock_path)
    except OSError:
        pass


# ── Hidden child-process windows (Windows only) ───────────
#  On Windows we suppress the black console flash from every child
#  (ffmpeg, taskkill, the CLI). On macOS/Linux there are no console
#  windows, so this is a no-op. winhide.py already guards itself by
#  platform; we just call its import here for one entry point.

def apply_window_hiding():
    if IS_WINDOWS:
        import winhide  # noqa: F401  (importing applies the subprocess patch)


# ── PtyProcess — run a TUI CLI in a real pseudo-terminal ──
#  Windows  → winpty (ConPTY).
#  Unix     → stdlib pty.openpty + a child process in its own session.
#  Methods mirror the original winpty usage so the engine loop is
#  identical on every OS: spawn(cmd_parts, cwd, env) / isalive() /
#  read() / pid / terminate().

class PtyProcess:
    def __init__(self, cols: int = 200, rows: int = 1000):
        self._cols = cols
        self._rows = rows
        self._win = None        # winpty.PTY
        self._proc = None       # subprocess.Popen (Unix)
        self._master_fd = None  # Unix master fd
        self._pid = None

    def spawn(self, cmd_parts: list, cwd: str, env: dict):
        if IS_WINDOWS:
            import winpty
            import subprocess
            command = subprocess.list2cmdline(cmd_parts)
            env_str = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0\0"
            self._win = winpty.PTY(
                self._cols, self._rows,
                backend=winpty.Backend.ConPTY,
                agent_config=winpty.AgentConfig.WINPTY_FLAG_COLOR_ESCAPES,
            )
            self._win.spawn(command, cwd=cwd, env=env_str)
            self._pid = self._win.pid
        else:
            import pty
            import subprocess
            master_fd, slave_fd = pty.openpty()
            try:
                import termios
                attrs = termios.tcgetattr(slave_fd)
                attrs[3] = attrs[3] & ~termios.ECHO  # no echo
                termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
            except Exception:
                pass
            self._proc = subprocess.Popen(
                cmd_parts, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                cwd=cwd, env=env, close_fds=True, start_new_session=True,
            )
            os.close(slave_fd)
            self._master_fd = master_fd
            self._pid = self._proc.pid

    @property
    def pid(self):
        return self._pid

    def isalive(self) -> bool:
        if IS_WINDOWS:
            return bool(self._win and self._win.isalive())
        return bool(self._proc and self._proc.poll() is None)

    def read(self, blocking: bool = False) -> str:
        """Return any available output as text (never blocks on Unix)."""
        if IS_WINDOWS:
            try:
                return self._win.read(blocking=blocking) or ""
            except Exception:
                return ""
        # Unix: non-blocking drain of the master fd.
        if self._master_fd is None:
            return ""
        import select
        out = []
        try:
            while True:
                r, _, _ = select.select([self._master_fd], [], [], 0)
                if not r:
                    break
                try:
                    chunk = os.read(self._master_fd, 65536)
                except OSError:
                    break  # EIO = child closed the pty
                if not chunk:
                    break
                out.append(chunk.decode("utf-8", errors="replace"))
        except Exception:
            pass
        return "".join(out)

    def terminate(self):
        if IS_WINDOWS:
            import subprocess
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self._pid)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                pass
        else:
            import signal
            try:
                os.killpg(os.getpgid(self._pid), signal.SIGKILL)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        # Close the Unix master fd if open.
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
