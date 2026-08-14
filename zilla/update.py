"""
Phase H4 — SELF-UPDATE WITH DOCTOR-GATED ROLLBACK (PLAN.md §8/H4).

One deterministic pipeline, owner-triggered only (`/update` in chat,
`zilla update` in a terminal). Nothing here ever runs on its own — a beat
may *mention* that an update is available, and that is the whole of the
automatic behaviour.

    record commit → git fetch → git pull --ff-only → pip install
    → back up zilla.db (VACUUM INTO) → migrations → restart → doctor

**The doctor gate.** If the post-restart checks fail, the pipeline puts the
previous version back — checkout the recorded commit, reinstall, restore the
database from the backup taken moments earlier, restart, check again — and
reports ONE calm line. The owner never sees a step name they can't act on,
never a traceback.

The gate is deliberately two signals, not one:

  · **smoke** — does the new code import and does the store open? A failure
    here is unambiguously the update's fault and always rolls back.
  · **environment** — `zilla doctor`'s exit code. This one only rolls back
    on *degradation* (it passed before the update, it fails after). A box
    that was already missing `flac` must never have a good release reverted
    out from under it because of something the update did not cause.

Everything the pipeline touches the outside world with is injected
(`runner`, `backup`, `restore`, `migrate`, `restart`, `doctor`), so the
whole thing — including a bad migration and its rollback — is testable
without a network, a service manager, or a second checkout.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

STEP_TIMEOUT = 900.0          # seconds per subprocess step (pip is the slow one)
RESTART_WAIT = 90.0           # seconds to wait for the bot to come back
UPDATE_CHECK_TTL = 24 * 3600  # PLAN.md §8/H4: `git fetch --dry-run` 1x/day

CHECK_TS_KEY = "last_update_check_ts"
CHECK_RESULT_KEY = "update_available"

# The import smoke — the same modules the session gate imports. Deliberately
# a plain string run under the NEW code in a fresh interpreter: our own
# process still holds the old modules in sys.modules.
IMPORT_SMOKE = (
    "import bot, zilla.core, zilla.cli, zilla.zui, zilla.presence, keyboards"
)
_MIGRATE_CODE = "import sys, zilla.store as st; st.get_store(sys.argv[1])"

BACKUP_SUFFIX = ".pre-update"


@dataclass(frozen=True)
class Step:
    """One pipeline step, in the order it ran. `name` is owner-safe English
    (see _report) — never a command line."""
    name: str
    ok: bool
    detail: str = ""


# ── plumbing ────────────────────────────────────────────────

def _sh(cmd: list[str], cwd: str, timeout: float) -> tuple[int, str]:
    """Run a command, return (exit code, combined output). Never raises —
    a missing binary or a timeout is a normal, reportable pipeline failure."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} timed out"
    except Exception as e:  # pragma: no cover — defensive
        return 1, str(e)[:400]
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out


def _repo_dir(repo_dir: str | None) -> str:
    if repo_dir:
        return repo_dir
    from zilla.config import BASE_DIR
    return BASE_DIR


def _short(sha: str | None) -> str:
    return (sha or "")[:7]


def current_commit(repo_dir: str | None = None, runner=_sh,
                   timeout: float = 30.0) -> str | None:
    """The checked-out commit, or None if this isn't a git checkout (a zip
    download, say) — in which case there is nothing to update or roll back
    to and the pipeline refuses to start."""
    rc, out = runner(["git", "rev-parse", "HEAD"], _repo_dir(repo_dir), timeout)
    if rc != 0:
        return None
    sha = out.strip().splitlines()[0].strip() if out.strip() else ""
    return sha or None


# ── "an update is available" (checked at most once a day) ───

def update_available(*, repo_dir: str | None = None, runner=_sh,
                     force: bool = False, now: float | None = None,
                     ttl: float = UPDATE_CHECK_TTL,
                     timeout: float = 60.0) -> dict:
    """`git fetch --dry-run`, capped at once per `ttl` (PLAN.md §8/H4).
    The answer is cached in settings so a beat can read it for free.
    Never raises: a network-less laptop simply reports "not available"."""
    from zilla.config import get_setting, set_setting
    now = now if now is not None else time.time()
    try:
        last = float(get_setting(CHECK_TS_KEY, 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    cached = bool(get_setting(CHECK_RESULT_KEY, False))
    if not force and last and (now - last) < ttl:
        return {"available": cached, "checked_at": last, "fresh": False}

    rc, out = runner(["git", "fetch", "--dry-run"], _repo_dir(repo_dir), timeout)
    # --dry-run prints one "  abc..def  main -> origin/main" line per ref that
    # WOULD be updated, and prints nothing at all when there is nothing new.
    available = rc == 0 and any("->" in line for line in out.splitlines())
    try:
        set_setting(CHECK_TS_KEY, now)
        set_setting(CHECK_RESULT_KEY, bool(available))
    except Exception as e:  # pragma: no cover — settings write is best effort
        logger.debug(f"[UPDATE] could not cache the check: {e}")
    return {"available": available, "checked_at": now, "fresh": True}


def refresh_update_check() -> None:
    """Called from the health loop's tick. Self-limiting to 1x/day, silent,
    never raises — this must never be able to disturb a probe round."""
    try:
        update_available()
    except Exception as e:
        logger.debug(f"[UPDATE] availability check skipped: {e}")


def beat_flag_lines() -> list[str]:
    """A heartbeat may MENTION an available update (PLAN.md §8/H4) — it may
    never install one. Reads the cache only; the fetch itself happens on the
    health timer, so a beat never shells out to git."""
    from zilla.config import get_setting
    try:
        if not get_setting(CHECK_RESULT_KEY, False):
            return []
    except Exception:
        return []
    return ["System note: a newer version of Zilla is available — the owner "
            "can install it with /update. Mention it once, only if it fits."]


# ── the real-world step implementations (all injectable) ────

def default_backup(db_path: str) -> str:
    """VACUUM INTO snapshot immediately before migrations (PLAN.md §8/H4,
    reusing M1.6's primitive). Its own filename, so it can never clobber the
    nightly `.bak` generation."""
    from zilla.store import get_store
    dest = db_path + BACKUP_SUFFIX
    get_store(db_path).backup_to(dest)
    return dest


def default_restore(backup_path: str, db_path: str) -> None:
    """Put the pre-update database back. close_all() first: this process took
    the snapshot, so it holds an open connection, and the stale -wal/-shm of
    the replaced file would otherwise be replayed over the restored copy."""
    from zilla import store
    try:
        store.close_all()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug(f"[UPDATE] close_all before restore: {e}")
    shutil.copy2(backup_path, db_path)
    for suffix in ("-wal", "-shm"):
        stale = db_path + suffix
        if os.path.exists(stale):
            try:
                os.remove(stale)
            except OSError:
                pass


def default_migrate(python: str, repo_dir: str, db_path: str, runner=_sh,
                    timeout: float = STEP_TIMEOUT) -> dict:
    """Apply the NEW code's schema to the existing database, in a fresh
    interpreter so it is genuinely the new store module doing it."""
    rc, out = runner([python, "-c", _MIGRATE_CODE, db_path], repo_dir, timeout)
    return {"ok": rc == 0, "detail": out[-400:]}


def default_restart(wait: float = RESTART_WAIT) -> dict:
    """Restart whatever is actually supervising Zilla here: the systemd user
    unit on Linux when it is installed, otherwise the stop/start pair the
    `zilla` CLI already uses. A Zilla that was NOT running stays not running —
    an update must not silently start a bot the owner had stopped."""
    import install
    import zilla.platform_compat as platform_compat

    if not install.is_running():
        return {"ok": True, "detail": "was not running — left stopped"}

    if platform_compat.IS_LINUX:
        rc, out = _sh(["systemctl", "--user", "restart", "zilla.service"],
                      _repo_dir(None), 60.0)
        if rc == 0:
            return {"ok": _wait_for(install.is_running, True, wait),
                    "detail": "systemd service restarted"}
        logger.info(f"[UPDATE] systemctl restart unavailable ({out[:120]}) — "
                    "falling back to stop/start")

    install.stop_bot()
    if not _wait_for(install.is_running, False, wait):
        return {"ok": False, "detail": "the old instance would not stop"}
    install.start_bot()
    if not _wait_for(install.is_running, True, wait):
        return {"ok": False, "detail": "it did not come back up"}
    return {"ok": True, "detail": "restarted"}


def _wait_for(predicate, want: bool, timeout: float, poll: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if bool(predicate()) is want:
                return True
        except Exception:  # pragma: no cover — defensive
            pass
        time.sleep(poll)
    return False


def default_doctor(python: str, repo_dir: str, runner=_sh,
                   timeout: float = 300.0) -> dict:
    """The gate's two signals (see the module docstring): does the code
    import, and does `zilla doctor` pass. Reported separately so the caller
    can treat a pre-existing environment problem differently from an
    update that broke Zilla."""
    rc_smoke, out_smoke = runner([python, "-c", IMPORT_SMOKE], repo_dir, timeout)
    rc_env, out_env = runner([python, "-m", "zilla.cli", "doctor"], repo_dir, timeout)
    smoke_ok = rc_smoke == 0
    return {
        "ok": smoke_ok and rc_env == 0,
        "smoke_ok": smoke_ok,
        "env_ok": rc_env == 0,
        "detail": (out_smoke[-400:] if not smoke_ok else out_env[-400:]),
    }


# ── the pipeline ────────────────────────────────────────────

def _result(*, ok: bool, steps: list[Step], message: str,
            rolled_back: bool = False, needs_human: bool = False,
            failed_step: str | None = None, from_commit: str | None = None,
            to_commit: str | None = None, changed: bool = False,
            backup: str | None = None) -> dict:
    return {"ok": ok, "rolled_back": rolled_back, "needs_human": needs_human,
            "failed_step": failed_step, "from_commit": from_commit,
            "to_commit": to_commit, "changed": changed, "backup": backup,
            "steps": steps, "message": message}


def run_update(*, repo_dir: str | None = None, python: str | None = None,
               db_path: str | None = None, runner=_sh, migrate=None,
               backup=None, restore=None, restart=None, doctor=None,
               timeout: float = STEP_TIMEOUT) -> dict:
    """The whole of H4. Returns a result dict whose `message` is the ONE
    line the owner sees; `steps` is for the log and the terminal."""
    repo = _repo_dir(repo_dir)
    python = python or sys.executable
    if db_path is None:
        from zilla.config import DB_FILE
        db_path = DB_FILE

    migrate = migrate or (lambda: default_migrate(python, repo, db_path, runner, timeout))
    backup = backup or (lambda: default_backup(db_path))
    restore = restore or (lambda path: default_restore(path, db_path))
    restart = restart or (lambda: default_restart())
    doctor = doctor or (lambda: default_doctor(python, repo, runner))

    steps: list[Step] = []

    def add(name: str, ok: bool, detail: str = "") -> bool:
        steps.append(Step(name, ok, detail[-400:]))
        if not ok:
            logger.warning(f"[UPDATE] step '{name}' failed: {detail[:200]}")
        return ok

    before = current_commit(repo, runner=runner)
    if not before:
        add("check", False, "not a git checkout")
        return _result(ok=False, steps=steps, failed_step="check",
                       message="I can't update this copy of Zilla on my own — "
                               "it wasn't installed from the repository.")

    # Baseline BEFORE anything changes: an environment problem that already
    # existed must not be blamed on (or roll back) the new version.
    base = doctor()
    add("baseline checks", True,
        f"smoke={base.get('smoke_ok')} environment={base.get('env_ok')}")

    rc, out = runner(["git", "fetch", "--quiet"], repo, timeout)
    if not add("download", rc == 0, out):
        return _result(ok=False, steps=steps, failed_step="download",
                       from_commit=before,
                       message="I couldn't reach the update server, so nothing "
                               "changed. Try again later.")

    rc, out = runner(["git", "pull", "--ff-only"], repo, timeout)
    if not add("apply", rc == 0, out):
        return _result(ok=False, steps=steps, failed_step="apply",
                       from_commit=before,
                       message="The update wouldn't apply cleanly, so I left "
                               "everything as it was.")

    after = current_commit(repo, runner=runner)
    if after == before:
        return _result(ok=True, steps=steps, from_commit=before, to_commit=after,
                       changed=False, message="Already up to date.")

    def rollback(failed: str, message: str, backup_path: str | None) -> dict:
        """Put the recorded commit — and the pre-update database — back."""
        rc, out = runner(["git", "-c", "advice.detachedHead=false", "checkout",
                          "--force", before], repo, timeout)
        restored = add("put the old version back", rc == 0, out)
        rc, out = runner([python, "-m", "pip", "install", "-q", "-r",
                          "requirements.txt"], repo, timeout)
        restored = add("reinstall", rc == 0, out) and restored
        if backup_path:
            try:
                restore(backup_path)
                restored = add("restore data", True, backup_path) and restored
            except Exception as e:
                restored = add("restore data", False, str(e)) and restored
        r = restart()
        restored = add("restart", bool(r.get("ok")), r.get("detail", "")) and restored
        d = doctor()
        restored = add("recheck", bool(d.get("smoke_ok")), d.get("detail", "")) and restored
        if restored:
            return _result(ok=False, rolled_back=True, steps=steps,
                           failed_step=failed, from_commit=before,
                           to_commit=after, changed=False, backup=backup_path,
                           message=message)
        return _result(ok=False, rolled_back=True, needs_human=True, steps=steps,
                       failed_step=failed, from_commit=before, to_commit=after,
                       backup=backup_path,
                       message="The update failed and I couldn't fully put the "
                               "old version back — Zilla needs a hand on the "
                               "computer.")

    rc, out = runner([python, "-m", "pip", "install", "-q", "-r",
                      "requirements.txt"], repo, timeout)
    if not add("install", rc == 0, out):
        return rollback("install", "The update couldn't finish installing, so I "
                                   "put the previous version back.", None)

    try:
        backup_path = backup()
        add("back up data", True, backup_path)
    except Exception as e:
        add("back up data", False, str(e))
        return rollback("back up data",
                        "I couldn't safely back up your data first, so I put "
                        "the previous version back and stopped.", None)

    m = migrate()
    if not add("database", bool(m.get("ok")), m.get("detail", "")):
        return rollback("database", "The update couldn't prepare your data, so "
                                    "I put the previous version back.",
                        backup_path)

    r = restart()
    if not add("restart", bool(r.get("ok")), r.get("detail", "")):
        return rollback("restart", "The new version wouldn't start, so I put "
                                   "the previous version back.", backup_path)

    d = doctor()
    degraded = (not d.get("smoke_ok")) or (not d.get("env_ok") and base.get("env_ok"))
    add("checks", not degraded, d.get("detail", ""))
    if degraded:
        return rollback("checks", "The new version didn't pass its checks, so I "
                                  "put the previous version back.", backup_path)

    return _result(ok=True, steps=steps, from_commit=before, to_commit=after,
                   changed=True, backup=backup_path,
                   message=f"Updated and running ({_short(after)}). "
                           "Everything checks out.")


# ── owner notification (used by the chat-triggered run) ─────

def notify(chat_id: int, text: str, token: str | None = None,
           timeout: float = 15.0) -> bool:
    """Send ONE line to a Telegram chat over plain HTTPS. The chat-triggered
    update runs as a detached process — it restarts the bot, so it cannot ask
    the bot to deliver its own result. Never raises."""
    import json
    import urllib.request
    from zilla.config import BOT_TOKEN
    token = token or BOT_TOKEN
    if not token or not chat_id:
        return False
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning(f"[UPDATE] could not deliver the result line: {e}")
        return False


def format_steps(result: dict) -> str:
    """Terminal rendering — one line per step, then the owner's line."""
    lines = []
    for step in result.get("steps", []):
        mark = "✅" if step.ok else "❌"
        detail = f"  ({step.detail.splitlines()[-1][:80]})" if step.detail and not step.ok else ""
        lines.append(f"  {mark} {step.name}{detail}")
    lines.append("")
    lines.append(f"  {result.get('message', '')}")
    return "\n".join(lines)
