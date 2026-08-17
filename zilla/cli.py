# ============================================================
#  CLI — the `zilla` console entrypoint (Phase 2 steps 1-2)
# ============================================================
#  Subcommands: config / doctor / start / stop / status / update /
#  export / import / logs.
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
    if not any(cli.get("ok") for cli in report["clis"].values()):
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


def cmd_update(args) -> int:
    """PLAN.md §8/H4. Typing `zilla update` IS the confirmation — nothing
    below runs on its own. `--check` only looks; `--announce <chat id>`
    delivers the single result line to Telegram (how the chat-triggered run
    reports back, since it restarts the bot mid-pipeline)."""
    import zilla.update as zupdate

    if args.check:
        state = zupdate.update_available(force=args.force)
        print("  An update is available — run `zilla update` to install it."
              if state["available"] else "  Zilla is up to date.")
        return 0

    print("  Updating Zilla…")
    result = zupdate.run_update()
    print(zupdate.format_steps(result))
    if args.announce:
        zupdate.notify(args.announce, result["message"])
    return 0 if result["ok"] else 1


def _ask_passphrase(confirm: bool) -> str:
    """Read a passphrase from the terminal without echoing it. Never taken
    from argv — `ps` is public (PLAN.md §15)."""
    import getpass

    while True:
        first = getpass.getpass("  Passphrase (nothing is shown as you type): ")
        if not first:
            return ""
        if not confirm:
            return first
        again = getpass.getpass("  Type it again: ")
        if first == again:
            return first
        print("  Those didn't match — try again.")


def cmd_export(args) -> int:
    """PLAN.md §12/C1. One archive with everything that's the owner's:
    memory, settings, schedules, people, and small media."""
    import zilla.brain as brain

    passphrase = _ask_passphrase(confirm=True) if args.encrypt else None
    if args.encrypt and not passphrase:
        print("  Cancelled — an encrypted export needs a passphrase.")
        return 1
    print("  Saving your brain…")
    result = brain.export_brain(args.path, encrypt=args.encrypt, passphrase=passphrase)
    print(brain.format_steps(result))
    return 0 if result["ok"] else 1


def cmd_import(args) -> int:
    """PLAN.md §12/C1. Restore from an archive or an unpacked folder; the
    search index and the entity graph are rebuilt from the Markdown, never
    carried, which is the whole point of the format."""
    import zilla.brain as brain

    src = os.path.expanduser(args.path)
    passphrase = (_ask_passphrase(confirm=False)
                  if src.endswith(brain.ENCRYPTED_SUFFIX) else None)
    print("  Restoring…")
    result = brain.import_brain(src, passphrase=passphrase)
    print(brain.format_steps(result))
    return 0 if result["ok"] else 1


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

    p_update = sub.add_parser("update", help="update Zilla, with automatic rollback")
    p_update.add_argument("--check", action="store_true", help="only report whether one is available")
    p_update.add_argument("--force", action="store_true", help="with --check, bypass the daily cache")
    p_update.add_argument("--announce", type=int, default=0, metavar="CHAT_ID",
                          help="send the one-line result to this Telegram chat")

    p_export = sub.add_parser("export", help="save your whole brain to one file")
    p_export.add_argument("path", nargs="?", default=None,
                          help="where to write it (default: ~/Zilla/Runtime/Exports/)")
    p_export.add_argument("--encrypt", action="store_true",
                          help="lock the file with a passphrase (AES-256)")

    p_import = sub.add_parser("import", help="restore a brain from a backup")
    p_import.add_argument("path", help="the backup file or unpacked folder")

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
        "update": cmd_update,
        "export": cmd_export,
        "import": cmd_import,
        "logs": cmd_logs,
    }
    handler = handlers.get(args.command, cmd_bare)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
