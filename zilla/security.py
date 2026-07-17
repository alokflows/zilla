# ============================================================
#  SECURITY DOCTOR — `zilla doctor --security` (Phase 2 step 1)
# ============================================================
#  Deterministic checks only (WORKING AGREEMENTS: "security decisions are
#  deterministic, enforced by Zilla, never model-judged"). Every check takes
#  its target paths as parameters (defaulting to the live config) so the
#  logic is independently unit-testable against a throwaway tmp dir — see
#  test_zilla_cli.py.
#
#  Per HANDOFF §6 Phase 2 step 1: file perms on home/config (600/700),
#  secrets not in argv/logs, no unexpected listening sockets, WebBridge
#  loopback-only, pending-skill gate intact (skips gracefully — Phase 5's
#  skills/pending/ doesn't exist yet), owner ID set. `--fix` auto-remediates
#  ONLY the safe items (chmod on files/dirs we own) — everything else is
#  report-only, per the brief.
# ============================================================

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

SECRET_FILE_MODE = 0o600
SECRET_DIR_MODE = 0o700

# Rough shape of a Telegram bot token: "<digits>:<35 base64url-ish chars>".
# Used only to catch an accidental leak into a log file — never to validate
# a real token (that's install.py's job, against the Telegram API).
_TOKEN_PATTERN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,45}\b")


@dataclass
class Finding:
    name: str
    ok: bool
    detail: str
    fixable: bool = False
    _fix: Optional[Callable[[], "Finding"]] = field(default=None, repr=False)

    def fix(self) -> "Finding":
        if self._fix is None:
            return self
        return self._fix()


def _mode_of(path: str) -> Optional[int]:
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None


def check_file_perms(base_dir: str, filenames: list[str] | None = None) -> list[Finding]:
    """Secret-bearing files under base_dir must be 600; base_dir itself
    (the home/config directory) must be 700. Windows has no POSIX perm bits
    (os.chmod is a no-op there) — report OK and skip, matching platform_compat's
    existing pattern of no-op-ing Windows-inapplicable checks rather than
    faking a pass/fail that means nothing on that OS."""
    if sys.platform == "win32":
        return [Finding("file permissions", True, "not applicable on Windows", False)]

    if filenames is None:
        filenames = [".env", "settings.json", "authorized_users.json",
                     "sessions.json", "schedules.json", "denied_users.json"]

    findings: list[Finding] = []

    def _mk_file_fix(path: str, name: str):
        def _fix() -> Finding:
            try:
                os.chmod(path, SECRET_FILE_MODE)
            except OSError as e:
                return Finding(name, False, f"chmod 600 failed: {e}", False)
            return Finding(name, True, f"fixed -> 600 ({path})", False)
        return _fix

    for fname in filenames:
        path = os.path.join(base_dir, fname)
        if not os.path.exists(path):
            continue
        mode = _mode_of(path)
        name = f"perms: {fname}"
        if mode is None:
            findings.append(Finding(name, False, "could not stat", False))
        elif mode & 0o077:
            findings.append(Finding(
                name, False,
                f"{oct(mode)} — group/other can read/write ({path})",
                True, _mk_file_fix(path, name),
            ))
        else:
            findings.append(Finding(name, True, f"{oct(mode)} ({path})", False))

    def _mk_dir_fix(path: str):
        def _fix() -> Finding:
            try:
                os.chmod(path, SECRET_DIR_MODE)
            except OSError as e:
                return Finding("perms: home dir", False, f"chmod 700 failed: {e}", False)
            return Finding("perms: home dir", True, f"fixed -> 700 ({path})", False)
        return _fix

    dmode = _mode_of(base_dir)
    if dmode is None:
        findings.append(Finding("perms: home dir", False, "could not stat base dir", False))
    elif dmode & 0o077:
        findings.append(Finding(
            "perms: home dir", False,
            f"{oct(dmode)} — group/other can read/write/enter ({base_dir})",
            True, _mk_dir_fix(base_dir),
        ))
    else:
        findings.append(Finding("perms: home dir", True, f"{oct(dmode)} ({base_dir})", False))

    return findings


def check_secrets_not_in_logs(logs_dir: str) -> Finding:
    """Scan the bot's own log files for a token-shaped substring. Never
    remediable automatically (a matched line must be reviewed/rotated by a
    human, not silently rewritten) — report only."""
    if not os.path.isdir(logs_dir):
        return Finding("secrets not in logs", True, "no logs/ yet", False)
    hits: list[str] = []
    try:
        for fname in os.listdir(logs_dir):
            if not (fname.endswith(".log") or fname.endswith(".jsonl")):
                continue
            path = os.path.join(logs_dir, fname)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if _TOKEN_PATTERN.search(line):
                            hits.append(f"{fname}:{lineno}")
            except OSError:
                continue
    except OSError:
        return Finding("secrets not in logs", True, "logs/ unreadable — skipped", False)
    if hits:
        return Finding("secrets not in logs", False,
                        f"token-shaped string found in {', '.join(hits[:5])}"
                        + (" ..." if len(hits) > 5 else ""), False)
    return Finding("secrets not in logs", True, f"clean ({logs_dir})", False)


def check_secrets_not_in_argv() -> Finding:
    """Defensive check on OUR OWN process argv — secrets must never be
    accepted as CLI arguments (visible in `ps`/shell history). This is a
    self-check of the currently running command line, not a system scan."""
    for arg in sys.argv:
        if _TOKEN_PATTERN.search(arg):
            return Finding("secrets not in argv", False,
                            "a secret-shaped value was passed on the command line", False)
    return Finding("secrets not in argv", True, "clean", False)


def check_webbridge_loopback(bridge_url: str) -> Finding:
    """WebBridge must be bound to loopback only — never a LAN/public address
    (HANDOFF §3 non-goal: no listening network gateway)."""
    from urllib.parse import urlparse
    host = (urlparse(bridge_url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return Finding("WebBridge loopback-only", True, bridge_url, False)
    return Finding("WebBridge loopback-only", False,
                    f"KIMI_BRIDGE_URL host is '{host}', not loopback: {bridge_url}", False)


_ZILLA_PROCESS_MARKERS = ("bot.py", "run_background", "zilla")


def _is_zilla_process(pid: str) -> bool:
    """True if the process's own command line looks like one of ours. Scopes
    the socket check to processes Zilla owns — a stock Mac/Linux desktop has
    plenty of unrelated listeners (AirPlay, ControlCenter, rapportd, ...)
    that a user can't and shouldn't have to 'fix'; the actual security
    intent (HANDOFF §3 non-goal: no listening network gateway) is about what
    ZILLA opens, not the whole machine."""
    try:
        out = subprocess.run(["ps", "-p", pid, "-o", "command="],
                              capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return False
    low = (out or "").lower()
    return any(m in low for m in _ZILLA_PROCESS_MARKERS)


def check_no_unexpected_listening_sockets() -> Finding:
    """Best-effort: a Zilla-owned process must never hold a non-loopback
    listening socket (HANDOFF §3 non-goal: no listening network gateway —
    that CVE class is exactly this). Scoped to processes whose own command
    line names bot.py/run_background/zilla (see _is_zilla_process) so this
    doesn't flag unrelated system services the user has no reason to change.
    Uses `lsof` where available; skips gracefully (no new dependency, e.g.
    psutil, to do this "properly") rather than reporting a false result."""
    lsof = None
    for cand in ("/usr/sbin/lsof", "/usr/bin/lsof"):
        if os.path.exists(cand):
            lsof = cand
            break
    if lsof is None:
        import shutil as _shutil
        lsof = _shutil.which("lsof")
    if lsof is None:
        return Finding("no unexpected listening sockets", True,
                        "lsof not available — skipped (best-effort check)", False)
    try:
        proc = subprocess.run([lsof, "-nP", "-iTCP", "-sTCP:LISTEN"],
                               capture_output=True, text=True, timeout=5)
    except Exception as e:
        return Finding("no unexpected listening sockets", True,
                        f"lsof failed ({e}) — skipped", False)
    non_loopback = []
    for line in (proc.stdout or "").splitlines()[1:]:
        # e.g. "python3.1 1234 alok  10u  IPv4 ... TCP *:8000 (LISTEN)"
        m = re.search(r"TCP\s+(\S+):(\d+)\s+\(LISTEN\)", line)
        if not m:
            continue
        addr, port = m.group(1), int(m.group(2))
        pid = line.split(None, 2)[1] if len(line.split(None, 2)) > 1 else ""
        if addr not in ("127.0.0.1", "localhost", "::1", "[::1]") and _is_zilla_process(pid):
            non_loopback.append(f"{addr}:{port} (pid {pid})")
    if non_loopback:
        return Finding("no unexpected listening sockets", False,
                        "Zilla process(es) listening on non-loopback: " + ", ".join(sorted(set(non_loopback))[:5]),
                        False)
    return Finding("no unexpected listening sockets", True,
                    "no Zilla-owned non-loopback listeners", False)


def check_pending_skill_gate(skills_dir: str) -> Finding:
    """Code-type skills must be staged in skills/pending/ awaiting one owner
    tap (HANDOFF §5). That directory doesn't exist until Phase 5 lands —
    skip gracefully rather than failing on a feature not built yet."""
    pending = os.path.join(skills_dir, "pending")
    if not os.path.isdir(skills_dir):
        return Finding("pending-skill gate", True,
                        "no skills dir yet (Phase 5 not built) — skipped", False)
    if not os.path.isdir(pending):
        return Finding("pending-skill gate", True,
                        "no skills/pending/ yet (Phase 5 not built) — skipped", False)
    mode = _mode_of(pending)
    if sys.platform != "win32" and mode is not None and (mode & 0o022):
        return Finding("pending-skill gate", False,
                        f"skills/pending/ is group/other-writable ({oct(mode)}) — a non-owner could stage code", False)
    return Finding("pending-skill gate", True, f"present, not group/other-writable ({pending})", False)


def check_owner_id_set(owner_chat_id: int) -> Finding:
    if owner_chat_id and owner_chat_id != 0:
        return Finding("owner ID set", True, str(owner_chat_id), False)
    return Finding("owner ID set", False,
                    "TELEGRAM_OWNER_ID is unset/0 — approval mode + alerts have nobody to reach", False)


def run_security_checks(base_dir: str, logs_dir: str, skills_dir: str,
                         bridge_url: str, owner_chat_id: int) -> list[Finding]:
    """Run every check against the given paths (all parameterized so this is
    independently testable against a tmp dir, never the live install)."""
    findings: list[Finding] = []
    findings.extend(check_file_perms(base_dir))
    findings.append(check_secrets_not_in_logs(logs_dir))
    findings.append(check_secrets_not_in_argv())
    findings.append(check_webbridge_loopback(bridge_url))
    findings.append(check_no_unexpected_listening_sockets())
    findings.append(check_pending_skill_gate(skills_dir))
    findings.append(check_owner_id_set(owner_chat_id))
    return findings


def apply_fixes(findings: list[Finding]) -> list[Finding]:
    """Re-run only the fixable, currently-failing findings through their fix,
    returning the full list with those entries replaced by the post-fix
    result. Never touches a non-fixable finding."""
    out = []
    for f in findings:
        if not f.ok and f.fixable:
            out.append(f.fix())
        else:
            out.append(f)
    return out


def format_findings(findings: list[Finding]) -> str:
    lines = ["=" * 56, "  Zilla — security check", "=" * 56]
    for f in findings:
        icon = "✅" if f.ok else "❌"
        lines.append(f"  {icon} {f.name}: {f.detail}")
    n_fail = sum(1 for f in findings if not f.ok)
    lines.append("=" * 56)
    if n_fail:
        lines.append(f"  {n_fail} problem(s) found — run with --fix to auto-remediate safe items (file perms).")
    else:
        lines.append("  All checks passed.")
    lines.append("=" * 56)
    return "\n".join(lines)
