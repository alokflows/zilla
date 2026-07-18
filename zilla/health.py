# ============================================================
#  HEALTH — deterministic probes + cooldown-gated alerts (PLAN.md §6, H2)
# ============================================================
#  Design (PLAN.md §6/H2): probes run on their OWN asyncio timer in core.py,
#  independent of heartbeat_interval (heartbeat 0=off must never silence
#  the probes — a future R2 fallback chain's eligibility depends on probe
#  freshness). This module owns only the deterministic, side-effect-light
#  parts: what each probe checks, how long its result is cached, the
#  per-kind alert cooldown, and the plain-language recovery instructions.
#  core.py owns the timer, the self-heal actions that need the running
#  ZillaCore (e.g. brain-dir GC needs `self.sessions`), and broadcasting
#  the actual Alert event.
#
#  Honest ceiling (PLAN.md §6/H2 step 2, owner-confirmed): both agy and
#  claude authenticate via browser OAuth with no verified token-paste path
#  on the installed CLI versions. The default and ONLY deliverable here is
#  detect + precise plain-language recovery steps — never speculative
#  login automation (no scripted OAuth flow, no keychain token injection).
# ============================================================

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

MIN_DISK_FREE_MB = 500

# claude has no cheap "am I still logged in" signal the way agy's cached
# `agy models` call gives us (claude_identity() only checks `claude auth
# status`, which can say loggedIn=True for a session that no longer
# actually generates) — so this probe makes a REAL `claude -p "ping"` call.
# Capped hard at once per 6h regardless of how often the health loop ticks.
CLAUDE_PING_TTL = 6 * 3600.0

# Cheap probes (disk/db/binary-on-PATH) — short cache, harmless to redo.
_CHEAP_TTL = 300.0
# agy's login probe shells out to `agy models` — cheap but not free; a
# middle-ground TTL keeps a 5-minute health tick from hammering it.
_AGY_LOGIN_TTL = 300.0

# "needs a human" DM cooldown — never repeat while the SAME kind stays
# broken; a recovery clears it so the next NEW failure alerts promptly.
ALERT_COOLDOWN = 6 * 3600.0

_cache: dict[str, dict] = {}
_alerted: dict[str, float] = {}


def _cached(kind: str, ttl: float, force: bool, compute) -> dict:
    now = time.time()
    prev = _cache.get(kind)
    if not force and prev is not None and now - prev["ts"] < ttl:
        return prev
    result = dict(compute())
    result["ts"] = now
    _cache[kind] = result
    return result


def reset_cache() -> None:
    """Test-only: clear every cached probe result and alert cooldown."""
    _cache.clear()
    _alerted.clear()


# ── Individual probes ──────────────────────────────────────

def probe_disk(base: str | None = None, min_free_mb: float = MIN_DISK_FREE_MB,
               force: bool = False) -> dict:
    def compute():
        path = base or os.getcwd()
        try:
            free = shutil.disk_usage(path).free
        except OSError as e:
            return {"ok": False, "detail": f"disk_usage failed: {e}"}
        free_mb = free / (1024 * 1024)
        ok = free_mb >= min_free_mb
        return {"ok": ok, "detail": f"{free_mb:.0f} MB free (need {min_free_mb:.0f})"}
    return _cached("disk", _CHEAP_TTL, force, compute)


def probe_db_writable(db_path: str, force: bool = False) -> dict:
    def compute():
        d = os.path.dirname(db_path) or "."
        probe_file = os.path.join(d, ".zilla_health_probe")
        try:
            with open(probe_file, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe_file)
            return {"ok": True, "detail": "writable"}
        except OSError as e:
            return {"ok": False, "detail": f"not writable: {e}"}
    return _cached("db_writable", _CHEAP_TTL, force, compute)


def probe_backend_path(name: str, path: str | None, force: bool = False) -> dict:
    def compute():
        ok = bool(path) and os.path.exists(path)
        detail = path if ok else f"{name} not found at {path or '(unset)'}"
        return {"ok": ok, "detail": detail}
    return _cached(f"{name}_path", _CHEAP_TTL, force, compute)


def probe_agy_login(force: bool = False) -> dict:
    def compute():
        from zilla.config import agy_reachable, agy_models_live
        if force:
            agy_models_live(force=True)
        ok = agy_reachable()
        detail = ("agy models reachable" if ok else
                  "agy installed but not responding — may be logged out (Google OAuth)")
        return {"ok": ok, "detail": detail}
    return _cached("agy_login", _AGY_LOGIN_TTL, force, compute)


def probe_claude_login(force: bool = False, claude_path: str | None = None,
                       timeout: int = 15) -> dict:
    """Cheap LIVE generation probe (`claude -p "ping"`), capped at 1x/6h
    (PLAN.md §6/H2 step 1) — deliberately distinct from backends.claude_identity()
    (`claude auth status`), which can report loggedIn=True for a cached
    session that no longer actually generates."""
    def compute():
        from zilla.config import CLAUDE_PATH
        path = claude_path or CLAUDE_PATH
        try:
            proc = subprocess.run(
                [path, "-p", "ping", "--output-format", "json"],
                capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError:
            return {"ok": False, "detail": f"claude not found at {path}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "detail": "claude ping timed out"}
        except Exception as e:
            return {"ok": False, "detail": str(e)[:200]}
        out = (proc.stdout or "").strip()
        try:
            obj = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            obj = None
        if isinstance(obj, dict) and not obj.get("is_error") and obj.get("result"):
            return {"ok": True, "detail": "ping ok"}
        if isinstance(obj, dict):
            detail = obj.get("error") or obj.get("result") or "claude reported an error"
        else:
            detail = (proc.stderr or out or f"claude exited {proc.returncode}")
        return {"ok": False, "detail": str(detail)[:200]}
    return _cached("claude_login", CLAUDE_PING_TTL, force, compute)


def run_probes(active_backend: str, db_path: str, force: bool = False) -> dict:
    """The full probe set for one health tick (PLAN.md §6/H2 step 1): disk,
    db writability, both backend binaries on PATH (cheap, always checked),
    and login freshness for ONLY the currently active backend (probing the
    inactive one's login would burn a real claude ping for no benefit until
    R2's fallback chain needs it)."""
    from zilla.config import CLI_PATH, CLAUDE_PATH
    results = {
        "disk": probe_disk(force=force),
        "db_writable": probe_db_writable(db_path, force=force),
        "agy_path": probe_backend_path("agy", CLI_PATH, force=force),
        "claude_path": probe_backend_path("claude", CLAUDE_PATH, force=force),
    }
    if active_backend == "agy":
        results["agy_login"] = probe_agy_login(force=force)
    elif active_backend == "claude":
        results["claude_login"] = probe_claude_login(force=force)
    return results


# ── Alert cooldown ──────────────────────────────────────────

def should_alert(kind: str, now: float | None = None) -> bool:
    """True on first failure, or once ALERT_COOLDOWN has elapsed while the
    same kind is still broken. False while the last DM for this kind is
    still within its cooldown window."""
    now = now if now is not None else time.time()
    last = _alerted.get(kind)
    return last is None or (now - last) >= ALERT_COOLDOWN


def mark_alerted(kind: str, now: float | None = None) -> None:
    _alerted[kind] = now if now is not None else time.time()


def clear_alert(kind: str) -> None:
    """Call once a probe recovers — the next NEW failure of this kind
    alerts immediately instead of waiting out a stale cooldown."""
    _alerted.pop(kind, None)


def is_alerted(kind: str) -> bool:
    return kind in _alerted


# ── Recovery instructions — the honest ceiling ──────────────

_RECOVERY = {
    "agy_login": (
        "Antigravity (agy) looks logged out. On this computer, open a "
        "terminal, run `agy`, and complete the Google sign-in prompt. Then "
        "send /doctor to confirm."
    ),
    "claude_login": (
        "Claude Code looks logged out or its session expired. On this "
        "computer, open a terminal, run `claude`, and complete sign-in. "
        "Then send /doctor to confirm."
    ),
    "agy_path": (
        "The agy CLI isn't installed (or not found) on this machine. "
        "Reinstall Antigravity CLI, then send /doctor to confirm."
    ),
    "claude_path": (
        "The claude CLI isn't installed (or not found) on this machine. "
        "Reinstall Claude Code, then send /doctor to confirm."
    ),
    "db_writable": (
        "Zilla's database file isn't writable. Check disk permissions on "
        "the Zilla folder on this computer."
    ),
    "disk": (
        "Disk space is critically low and Zilla couldn't free enough "
        "automatically. Please free up space on this computer."
    ),
}


def recovery_instructions(kind: str) -> str:
    return _RECOVERY.get(kind, f"{kind} needs attention — check `zilla doctor`.")


# ── Beat-prompt flags (PLAN.md §6/H2 step 3) ────────────────

def beat_flag_lines() -> list[str]:
    """One line per currently-unresolved, already-DM'd probe — prepended to
    the next heartbeat beat prompt so the agent knows not to raise it again
    itself (PLAN.md §6/H2 step 3: 'System flag: agy login expired — already
    DM'd owner.')."""
    return [f"System flag: {kind} — already DM'd owner." for kind in sorted(_alerted)]
