# ============================================================
#  DOCTOR — environment report for `zilla doctor` (Phase 2 step 1)
# ============================================================
#  A standalone, read-mostly environment probe for the CLI. Deliberately
#  does NOT instantiate ZillaCore (that needs SessionManager/AuthManager
#  file wiring meant for a running app) — it calls the SAME underlying
#  probe primitives core.health_report() uses (config.agy_reachable(),
#  backends.claude_identity(), shutil.disk_usage) directly, so the two
#  never disagree, without paying for a live core.
#
#  Per HANDOFF §6 Phase 2 step 1 / Phase 6: OS, GUI presence, installed
#  CLIs + login state, ffmpeg, flac (Apple-Silicon voice dependency —
#  explicit HANDOFF note, session log 2026-07-16 night), WebBridge
#  reachability. OS-specific facts (IS_WINDOWS/IS_MAC/IS_LINUX) are read
#  from platform_compat — never re-derived here.
# ============================================================

from __future__ import annotations

import os
import platform
import shutil
import urllib.error
import urllib.request

import zilla.platform_compat as platform_compat
from zilla.backends import claude_identity
from zilla.config import (
    BRAIN_DIR, FFMPEG_PATH, HOME_DIR, KIMI_BRIDGE_URL,
    agy_reachable, agy_models_live, get_backend, get_model,
)


def detect_gui() -> bool:
    """Best-effort GUI-vs-headless detection (Phase 6 seed). macOS and
    Windows are always treated as GUI-present (console-only invocations are
    rare and desktop control still makes sense there); Linux checks the
    standard display env vars."""
    if platform_compat.IS_MAC or platform_compat.IS_WINDOWS:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def check_ffmpeg() -> tuple[bool, str]:
    """ffmpeg presence, via the SAME path config.py already resolves —
    never a second lookup."""
    if FFMPEG_PATH and os.path.exists(FFMPEG_PATH):
        return True, FFMPEG_PATH
    found = shutil.which("ffmpeg")
    if found:
        return True, found
    return False, "not found — voice notes will fail to transcribe"


def check_flac() -> tuple[bool, str]:
    """The `flac` binary. Voice transcription needs it on Apple Silicon
    (HANDOFF session log, 2026-07-16 night: `brew install flac`) — SpeechRecognition
    shells out to it for AIFF/FLAC conversion and silently mis-transcribes
    without it."""
    found = shutil.which("flac")
    if found:
        return True, found
    hint = "brew install flac" if platform_compat.IS_MAC else "install flac from your package manager"
    return False, f"not found — {hint}"


def check_webbridge(timeout: float = 2.0) -> tuple[bool, str]:
    """Best-effort reachability of the optional Kimi WebBridge daemon. Short
    timeout — WebBridge is optional and doctor must stay snappy when it's
    simply not running."""
    url = f"{KIMI_BRIDGE_URL.rstrip('/')}/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read(1)
        return True, url
    except urllib.error.URLError as e:
        return False, f"unreachable ({e.reason}) — optional, only needed for web mode 'my-browser'"
    except Exception as e:
        return False, f"unreachable ({e.__class__.__name__}) — optional, only needed for web mode 'my-browser'"


def environment_report(force: bool = False) -> dict:
    """Point-in-time environment snapshot. Stable, plain-value keys so both
    the text renderer below and a future TUI health screen can consume it.
    force=False never triggers a live network/subprocess probe beyond the
    cheap/cached form (same contract as core.health_report)."""
    if force:
        agy_models_live(force=True)
    agy_ok = agy_reachable()
    claude_status = claude_identity(force=force)
    claude_ok = bool(claude_status.get("loggedIn"))

    ffmpeg_ok, ffmpeg_detail = check_ffmpeg()
    flac_ok, flac_detail = check_flac()
    bridge_ok, bridge_detail = check_webbridge()

    disk_path = BRAIN_DIR if os.path.isdir(BRAIN_DIR) else HOME_DIR
    try:
        usage = shutil.disk_usage(disk_path)
        free_bytes, total_bytes = usage.free, usage.total
    except OSError:
        free_bytes = total_bytes = None

    return {
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
            "gui": detect_gui(),
        },
        "backend": {"active": get_backend(), "model": get_model()},
        "clis": {
            "agy": {"reachable": agy_ok},
            "claude": {"reachable": claude_ok, "logged_in": claude_ok,
                       "auth_error": claude_status.get("error")},
        },
        "ffmpeg": {"ok": ffmpeg_ok, "detail": ffmpeg_detail},
        "flac": {"ok": flac_ok, "detail": flac_detail},
        "webbridge": {"ok": bridge_ok, "detail": bridge_detail},
        "disk": {"path": disk_path, "free_bytes": free_bytes, "total_bytes": total_bytes},
    }


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def format_report(report: dict) -> str:
    """Human-readable rendering of environment_report(), same ok/bad style
    as install.py's doctor()."""
    lines = []
    lines.append("=" * 56)
    lines.append("  Zilla — environment report")
    lines.append("=" * 56)
    osi = report["os"]
    lines.append(f"  • OS: {osi['system']} {osi['release']}  (Python {osi['python']})")
    lines.append(f"  • GUI present: {'yes' if osi['gui'] else 'no (headless)'}")
    lines.append(f"  • Backend: {report['backend']['active']}  (model: {report['backend']['model']})")

    agy = report["clis"]["agy"]
    lines.append(("  ✅ " if agy["reachable"] else "  ❌ ") +
                 f"agy: {'reachable / logged in' if agy['reachable'] else 'not reachable — run `agy` once to log in'}")
    cl = report["clis"]["claude"]
    if cl["reachable"]:
        lines.append("  ✅ claude: reachable / logged in")
    else:
        detail = f" ({cl['auth_error']})" if cl.get("auth_error") else ""
        lines.append(f"  ❌ claude: not reachable{detail} — run `claude` once to log in")

    ff = report["ffmpeg"]
    lines.append(("  ✅ " if ff["ok"] else "  ❌ ") + f"ffmpeg: {ff['detail']}")
    fl = report["flac"]
    lines.append(("  ✅ " if fl["ok"] else "  ❌ ") + f"flac: {fl['detail']}")
    wb = report["webbridge"]
    lines.append(("  ✅ " if wb["ok"] else "  • ") + f"WebBridge: {wb['detail']}")

    disk = report["disk"]
    lines.append(f"  • Disk free: {_fmt_bytes(disk['free_bytes'])} / {_fmt_bytes(disk['total_bytes'])}  ({disk['path']})")
    lines.append("=" * 56)
    return "\n".join(lines)
