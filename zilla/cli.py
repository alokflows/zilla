# ============================================================
#  CLI — the `zilla` console entrypoint (Phase 2 steps 1-2)
# ============================================================
#  Subcommands: config / doctor / start / stop / status / logs.
#  These PROMOTE what already exists — install.py --doctor, start.sh /
#  stop.sh, the pid/lock files — never duplicate their logic; every
#  subcommand here is a thin wrapper that imports and calls the real
#  implementation (install.py, zilla.doctor, zilla.security, zilla.config,
#  zilla.configmenu). `python install.py` keeps working unchanged.
#
#  Bare `zilla` (no subcommand): try to launch the full-screen TUI
#  (zilla/tui/app.py, built separately this round); if it isn't there yet,
#  print a friendly one-liner and fall back to `status`.
# ============================================================

from __future__ import annotations

import argparse
import glob
import os
import sys
import time

import install
import zilla.config as config
import zilla.configmenu as configmenu
import zilla.doctor as zdoctor
import zilla.security as zsecurity


def _logs_dir() -> str:
    return config.LOG_DIR


# ── subcommands ──────────────────────────────────────────────

def cmd_config(_args) -> int:
    configmenu.run_menu()
    return 0


def cmd_doctor(args) -> int:
    if args.security:
        findings = zsecurity.run_security_checks(
            base_dir=config.BASE_DIR,
            logs_dir=_logs_dir(),
            skills_dir=config.get_skills_dir(),
            bridge_url=config.KIMI_BRIDGE_URL,
            owner_chat_id=config.OWNER_CHAT_ID,
        )
        if args.fix:
            findings = zsecurity.apply_fixes(findings)
        print(zsecurity.format_findings(findings))
        return 1 if any(not f.ok for f in findings) else 0

    report = zdoctor.environment_report(force=args.force)
    print(zdoctor.format_report(report))
    problems = 0
    if not (report["clis"]["agy"]["reachable"] or report["clis"]["claude"]["reachable"]):
        problems += 1
    if not report["ffmpeg"]["ok"]:
        problems += 1
    if not report["flac"]["ok"]:
        problems += 1
    return 1 if problems else 0


def cmd_start(_args) -> int:
    if install.is_running():
        print("  Zilla is already running.")
        return 0
    install.start_bot()
    return 0


def cmd_stop(_args) -> int:
    if not install.is_running():
        print("  Zilla is not running.")
        return 0
    install.stop_bot()
    return 0


def cmd_status(_args) -> int:
    running = install.is_running()
    pid = install.read_pid()
    print("=" * 56)
    print("  Zilla — status")
    print("=" * 56)
    if running:
        print(f"  ✅ running" + (f"  (pid {pid})" if pid else ""))
    else:
        print("  ⚪ not running")
    print(f"  • backend: {config.get_backend()}  (model: {config.get_model()})")
    env = install.read_env()
    print(f"  • Telegram: {'configured' if env.get('TELEGRAM_BOT_TOKEN') else 'not configured'}")
    print("=" * 56)
    return 0


def cmd_logs(args) -> int:
    pattern = os.path.join(_logs_dir(), "bot_*.log")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"  No log files found under {_logs_dir()}")
        return 1
    path = files[-1]
    print(f"==> {path} <==")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in lines[-args.lines:]:
            print(line, end="")
    except OSError as e:
        print(f"  Couldn't read {path}: {e}")
        return 1

    if args.follow:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, os.SEEK_END)
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_bare(_args) -> int:
    """Bare `zilla`: launch the TUI if it exists, else a friendly fallback."""
    try:
        from zilla.tui.app import run as tui_run
    except ImportError:
        print("  The full-screen Zilla app is coming soon — here's the current status:\n")
        return cmd_status(_args)
    tui_run()
    return 0


# ── argument parsing ─────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zilla", description="Zilla — terminal-first AI harness")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("config", help="interactive settings editor (SSH-friendly)")

    p_doctor = sub.add_parser("doctor", help="environment report")
    p_doctor.add_argument("--security", action="store_true", help="run security checks instead")
    p_doctor.add_argument("--fix", action="store_true", help="auto-remediate safe items (with --security)")
    p_doctor.add_argument("--force", action="store_true", help="bypass caches, probe live")

    sub.add_parser("start", help="start the bot in the background")
    sub.add_parser("stop", help="stop the bot")
    sub.add_parser("status", help="is the bot running")

    p_logs = sub.add_parser("logs", help="tail the bot log")
    p_logs.add_argument("-n", "--lines", type=int, default=50, help="lines to show (default 50)")
    p_logs.add_argument("-f", "--follow", action="store_true", help="keep tailing")

    return parser


def main(argv: list[str] | None = None) -> int:
    # Must run before ANY subcommand touches DB_FILE/RUNTIME_DIR (e.g. `doctor`
    # reading settings via get_backend()/get_model()) — those lazily create
    # the new-layout files on first access, which would make ZILLA_HOME
    # "already exist" and silently skip the real migration (PLAN.md §17/F1).
    config.run_zilla_home_migration()

    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "config": cmd_config,
        "doctor": cmd_doctor,
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "logs": cmd_logs,
    }
    handler = handlers.get(args.command, cmd_bare)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
