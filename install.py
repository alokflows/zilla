#!/usr/bin/env python3
# ============================================================
#  Zilla — One-shot installer  (Windows / macOS / Linux)
# ============================================================
#  What it does:
#    1. Checks Python + installs the Python dependencies.
#    2. Detects which backend (agy / Claude Code) is actually installed and
#       uses it automatically; only asks if BOTH are present. Then asks for the
#       bot token, your Telegram ID, and whether to auto-start at login.
#    3. Tells you exactly how to log into your AI CLI (agy or Claude Code).
#    4. Writes the .env file.
#    5. Optionally sets the bot to auto-start at login.
#    6. Starts the bot in the background.
#
#  Run it:
#    Windows : double-click  install.bat   (or: python install.py)
#    macOS   : double-click  install.command   (or: python3 install.py)
#    Linux   : ./install.sh   (or: python3 install.py)
#
#  Self-check anytime:   python install.py --doctor
# ============================================================

import os
import sys
import shutil
import getpass
import platform
import subprocess

# Make emoji/box characters safe on a fresh Windows console (cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE, ".env")
REQS = os.path.join(BASE, "requirements.txt")
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

SYSTEMD_UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
SYSTEMD_UNIT_PATH = os.path.join(SYSTEMD_UNIT_DIR, "zilla.service")


# ── tiny pretty helpers ───────────────────────────────────
def hr():        print("=" * 56)
def ok(m):       print(f"  ✅ {m}")
def bad(m):      print(f"  ❌ {m}")
def info(m):     print(f"  • {m}")
def ask(p, d=""):
    s = input(f"{p}" + (f" [{d}]" if d else "") + ": ").strip()
    return s or d


def ask_secret(p, d=""):
    """Like ask(), but the typed value is NOT echoed to the terminal — for the
    bot token, which must not end up in scrollback or a screen recording. Press
    Enter to keep the existing value (if any)."""
    suffix = " [press Enter to keep the current value]" if d else ""
    try:
        s = getpass.getpass(f"{p}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception:
        # No TTY (e.g. piped input) — fall back to a visible prompt.
        s = input(f"{p}: ").strip()
    return s or d


def validate_token(token: str, timeout: float = 8.0):
    """Ask Telegram whether the bot token works (getMe). Stdlib only — no deps.
    Returns (ok: bool, detail: str). detail is the bot's @username on success,
    or a human-readable reason on failure."""
    import json as _json
    import urllib.request
    import urllib.error
    token = (token or "").strip()
    if not token or ":" not in token:
        return False, "that doesn't look like a bot token (it should contain a ':')"
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = _json.load(r)
        if data.get("ok"):
            u = data.get("result", {})
            return True, f"@{u.get('username', '?')}"
        return False, data.get("description", "Telegram rejected the token")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Telegram says this token is invalid (401) — re-copy it from @BotFather"
        return False, f"Telegram returned error {e.code}"
    except Exception as e:
        return False, f"couldn't reach Telegram ({e.__class__.__name__}) — check your internet"


def read_env() -> dict:
    data = {}
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def find_cli(name: str) -> str | None:
    return shutil.which(name)


def detect_backend(name: str) -> str | None:
    """Path to an installed backend CLI ('agy' or 'claude'), or None if absent.
    Mirrors config.py's resolution (PATH + the OS-specific install location) so
    the installer and the running bot agree on what's actually present."""
    found = shutil.which(name)
    if found:
        return found
    home = os.path.expanduser("~")
    if name == "agy":
        cands = ([os.path.join(home, "AppData", "Local", "agy", "bin", "agy.exe")]
                 if IS_WIN else [os.path.join(home, ".local", "bin", "agy")])
    else:  # claude
        cands = ([os.path.join(home, ".local", "bin", "claude.exe")] if IS_WIN
                 else [os.path.join(home, ".local", "bin", "claude"),
                       os.path.join(home, ".claude", "local", "claude")])
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


# ── doctor: DB checks (PLAN.md M1 step 4: exists, schema version, WAL,
#    write probe) ───────────────────────────────────────────
def doctor_db() -> int:
    """Returns the number of problems found (0 = DB OK)."""
    problems = 0
    db_path = os.path.join(BASE, "zilla.db")
    if not os.path.exists(db_path):
        info("zilla.db not created yet (first bot start will create it)")
        return 0

    try:
        sys.path.insert(0, BASE)
        from zilla.store import get_store
        db = get_store(db_path)

        ver = db.schema_version()
        if ver is None:
            bad("zilla.db schema_version missing"); problems += 1
        else:
            ok(f"zilla.db schema version {ver}")

        if db.is_wal_mode():
            ok("zilla.db journal_mode = WAL")
        else:
            bad("zilla.db not in WAL mode"); problems += 1

        if db.write_probe():
            ok("zilla.db write probe succeeded")
        else:
            bad("zilla.db write probe failed"); problems += 1
    except Exception as e:
        bad(f"zilla.db check failed: {e}"); problems += 1

    return problems


# ── doctor (self-check) ───────────────────────────────────
def doctor() -> int:
    hr(); print("  Zilla — environment check"); hr()
    problems = 0

    if sys.version_info < (3, 10):
        bad(f"Python {platform.python_version()} — need 3.10+"); problems += 1
    else:
        ok(f"Python {platform.python_version()}")

    for mod in ("telegram", "speech_recognition"):
        try:
            __import__(mod); ok(f"dependency '{mod}' installed")
        except Exception:
            bad(f"dependency '{mod}' missing — run the installer"); problems += 1
    if IS_WIN:
        try:
            import winpty  # noqa: F401
            ok("pywinpty installed (needed for agy backend on Windows)")
        except Exception:
            bad("pywinpty missing — `pip install pywinpty`"); problems += 1

    env = read_env()
    backend = (env.get("BACKEND") or "agy").lower()
    info(f"backend = {backend}")
    if env.get("TELEGRAM_BOT_TOKEN", "").startswith("paste") or not env.get("TELEGRAM_BOT_TOKEN"):
        bad("TELEGRAM_BOT_TOKEN not set in .env"); problems += 1
    else:
        ok("bot token set")
    if not env.get("TELEGRAM_OWNER_ID", "").isdigit():
        bad("TELEGRAM_OWNER_ID not a number in .env"); problems += 1
    else:
        ok(f"owner id = {env['TELEGRAM_OWNER_ID']}")

    cli = "claude" if backend == "claude" else "agy"
    path = env.get("CLAUDE_PATH") if cli == "claude" else env.get("CLI_PATH")
    path = path or detect_backend(cli)
    if path and os.path.exists(path):
        ok(f"{cli} CLI found: {path}")
    elif detect_backend(cli):
        ok(f"{cli} CLI found on PATH")
    else:
        bad(f"{cli} CLI not found — install it and run `{cli}` once to log in"); problems += 1

    problems += doctor_db()

    hr()
    if problems == 0:
        print("  All good ✅  Start with:  python install.py")
    else:
        print(f"  {problems} problem(s) found ⚠️  Fix the ❌ lines above.")
    hr()
    return 1 if problems else 0


# ── install ───────────────────────────────────────────────
def pip_install():
    info("Installing Python dependencies…")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", REQS], check=True)
        ok("dependencies installed")
    except subprocess.CalledProcessError:
        bad("pip install failed — check your internet / Python, then re-run.")
        sys.exit(1)


def write_env(values: dict):
    existing = read_env()
    existing.update(values)
    lines = [
        "# Zilla configuration (generated by install.py)",
        f'TELEGRAM_BOT_TOKEN="{existing.get("TELEGRAM_BOT_TOKEN","")}"',
        f'TELEGRAM_OWNER_ID="{existing.get("TELEGRAM_OWNER_ID","")}"',
        f'BACKEND={existing.get("BACKEND","agy")}',
    ]
    for k in ("CLI_PATH", "CLAUDE_PATH", "CLI_WORKING_DIR", "BRAIN_DIR", "FFMPEG_PATH"):
        if existing.get(k):
            lines.append(f'{k}={existing[k]}')
    lines += [
        "IDLE_KILL_AFTER=600",
        "MAX_TOTAL_RUNTIME=3600",
        "KIMI_BRIDGE_URL=http://127.0.0.1:10086",
        "",
    ]
    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    # Lock it down BEFORE it lands at its final name, so the token is never
    # world-readable — not even for the moment before the bot's own startup
    # hardening runs. No-op on Windows (which lacks Unix perms).
    if os.name != "nt":
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, ENV_PATH)
    if os.name != "nt":
        try:
            os.chmod(ENV_PATH, 0o600)
        except OSError:
            pass
    ok(f".env written ({ENV_PATH})")


# ── H3 (PLAN.md §6): systemd --user service, Linux only ───
def systemd_unit_content(py_path: str, base_dir: str) -> str:
    """The exact zilla.service unit text (PLAN.md §6/H3 step 1). Restart=
    on-failure — NOT `always` — so an intentional `zilla stop` (which makes
    run_background.py exit 0 cleanly via its zilla.stop check) is honored;
    only a crash (non-zero exit / killed by a signal) triggers a systemd
    restart, layered on top of run_background.py's own ~7s internal
    restart-on-exit loop for bot.py itself. Pure function (no I/O) so it's
    golden-testable without touching a real systemd."""
    return (
        "[Unit]\n"
        "Description=Zilla Telegram bot\n"
        "\n"
        "[Service]\n"
        f"ExecStart={py_path} {os.path.join(base_dir, 'run_background.py')}\n"
        f"WorkingDirectory={base_dir}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def write_service() -> int:
    """Write + enable the systemd --user unit (PLAN.md §6/H3 step 1).
    Linux only — caller must check IS_LINUX first. Returns 0 on success,
    1 if `systemctl` itself is missing or the enable step failed (the unit
    file is still written either way, so a later manual `systemctl --user
    enable --now zilla.service` can recover without re-running this)."""
    os.makedirs(SYSTEMD_UNIT_DIR, exist_ok=True)
    content = systemd_unit_content(sys.executable, BASE)
    tmp = SYSTEMD_UNIT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, SYSTEMD_UNIT_PATH)
    ok(f"systemd unit written: {SYSTEMD_UNIT_PATH}")

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "zilla.service"], check=True)
        ok("zilla.service enabled and started (systemctl --user)")
    except FileNotFoundError:
        bad("systemctl not found — is this a systemd-based Linux system?")
        return 1
    except subprocess.CalledProcessError as e:
        bad(f"systemctl failed: {e}")
        return 1

    # A --user unit only survives past logout if lingering is enabled for
    # this account; NOT auto-run (needs no sudo on most systems, but is a
    # login/session-policy change we don't make silently on the owner's
    # behalf) — printed as a precise next step instead (PLAN.md §6/H3
    # step 1: "lingering hint printed").
    info(f"To keep Zilla running after you log out / across reboots, run:")
    info(f"    loginctl enable-linger {getpass.getuser()}")
    return 0


def setup_autostart():
    """Per-OS auto-start at login. Best-effort; prints what it did."""
    try:
        if IS_WIN:
            pyw = shutil.which("pythonw") or sys.executable
            target = os.path.join(BASE, "run_background.pyw")
            ps = (
                "$ws=New-Object -ComObject WScript.Shell;"
                "$lnk=Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Zilla Bot.lnk';"
                f"$s=$ws.CreateShortcut($lnk);$s.TargetPath='{pyw}';"
                f"$s.Arguments='\"{target}\"';$s.WorkingDirectory='{BASE}';$s.Save()"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
            ok("Autostart added to your Startup folder.")
        elif IS_MAC:
            plist = os.path.expanduser("~/Library/LaunchAgents/com.zilla.bot.plist")
            os.makedirs(os.path.dirname(plist), exist_ok=True)
            py = sys.executable
            content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.zilla.bot</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{os.path.join(BASE,'run_background.py')}</string></array>
  <key>WorkingDirectory</key><string>{BASE}</string>
  <key>RunAtLoad</key><true/>
</dict></plist>"""
            open(plist, "w").write(content)
            subprocess.run(["launchctl", "load", plist], check=False)
            ok(f"Autostart LaunchAgent installed: {plist}")
        else:  # Linux — single source of truth is write_service() (H3)
            write_service()
    except Exception as e:
        bad(f"Couldn't set autostart automatically: {e}")
        info("You can still start it manually (see below).")


def start_bot():
    info("Starting Zilla in the background…")
    try:
        if IS_WIN:
            pyw = shutil.which("pythonw") or sys.executable
            subprocess.Popen([pyw, os.path.join(BASE, "run_background.pyw")],
                             cwd=BASE, creationflags=0x08000000)
        else:
            subprocess.Popen(
                [sys.executable, os.path.join(BASE, "run_background.py")],
                cwd=BASE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        ok("Zilla is running. Check Telegram — you should get an 'online' message.")
    except Exception as e:
        bad(f"Couldn't start automatically: {e}")


def stop_bot():
    """Stop the background supervisor + bot, mirroring stop.sh /
    STOP_BACKGROUND.bat (same stop-file + best-effort process kill), but as
    plain importable Python so `zilla stop` doesn't need a shell. Never
    raises — every step is best-effort, matching the shell scripts."""
    info("Stopping Zilla…")
    try:
        open(os.path.join(BASE, "zilla.stop"), "w", encoding="utf-8").write("stop")
    except OSError as e:
        bad(f"Couldn't write zilla.stop: {e}")
    if IS_WIN:
        ps = (
            "Get-CimInstance Win32_Process | Where-Object { "
            "($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and "
            "($_.CommandLine -like '*bot.py*' -or $_.CommandLine -like "
            "'*run_background.pyw*') } | ForEach-Object { Stop-Process -Id "
            "$_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
    else:
        subprocess.run(["pkill", "-f", "run_background.py"], check=False)
        subprocess.run(["pkill", "-f", os.path.join(BASE, "bot.py")], check=False)
        if not IS_MAC:  # check=False still raises FileNotFoundError if the
            try:        # binary itself is absent — and macOS has no systemctl
                subprocess.run(["systemctl", "--user", "stop", "zilla.service"],
                               check=False)
            except FileNotFoundError:
                pass
    ok("Zilla stopped.")


def disable_autostart():
    """Undo setup_autostart() — inverse per OS. Best-effort; never raises."""
    try:
        if IS_WIN:
            shortcut = os.path.join(
                os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                "Start Menu", "Programs", "Startup", "Zilla Bot.lnk",
            )
            if os.path.exists(shortcut):
                os.remove(shortcut)
            ok("Autostart shortcut removed.")
        elif IS_MAC:
            plist = os.path.expanduser("~/Library/LaunchAgents/com.zilla.bot.plist")
            subprocess.run(["launchctl", "unload", plist], check=False)
            if os.path.exists(plist):
                os.remove(plist)
            ok("Autostart LaunchAgent removed.")
        else:  # Linux
            subprocess.run(["systemctl", "--user", "disable", "--now", "zilla.service"],
                            check=False)
            if os.path.exists(SYSTEMD_UNIT_PATH):
                os.remove(SYSTEMD_UNIT_PATH)
            ok("Autostart systemd unit removed.")
    except Exception as e:
        bad(f"Couldn't remove autostart automatically: {e}")


def is_running() -> bool:
    """True if a Zilla bot instance currently holds the single-instance lock.
    Reuses the SAME cross-platform lock primitive bot.py itself uses (never a
    second liveness mechanism) — try to acquire it; if we succeed, nobody
    else held it, so release immediately and report not-running."""
    import zilla.platform_compat as platform_compat
    lock_path = os.path.join(BASE, "zilla_bot_instance.lock")
    handle = platform_compat.acquire_instance_lock(lock_path)
    if handle is None:
        return True
    platform_compat.release_instance_lock(handle, lock_path)
    return False


def read_pid() -> int | None:
    """Best-effort PID of the running bot.py, for display only (status uses
    is_running() — the lock, not this file — as the source of truth)."""
    pid_path = os.path.join(BASE, "zilla.pid")
    try:
        with open(pid_path, encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _arg(name: str):
    """Read --name=value or --name value from argv (for non-interactive/AI setup)."""
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def main():
    if "--doctor" in sys.argv:
        sys.exit(doctor())

    if "--service" in sys.argv:
        # PLAN.md §6/H3: Linux-only systemd --user unit. Mac dev keeps
        # ./start.sh; on any other OS this is a clean, informative no-op —
        # "nothing Windows breaks" (H3 accept criteria).
        if not IS_LINUX:
            info("--service is Linux-only (systemd --user). "
                 "macOS: use ./start.sh or the interactive installer's autostart "
                 "prompt (a LaunchAgent). Windows: use the interactive installer.")
            sys.exit(0)
        hr(); print("  Zilla — systemd --user service"); hr()
        sys.exit(write_service())

    # Non-interactive flags (used by the AI setup file and any script):
    #   --token <T> --owner <ID> [--backend agy|claude] [--no-autostart] [--no-start]
    arg_token = _arg("--token")
    arg_owner = _arg("--owner")
    arg_backend = (_arg("--backend") or "").strip().lower()
    non_interactive = bool(arg_token and arg_owner)

    hr(); print("  Zilla installer"); hr()
    if sys.version_info < (3, 10):
        bad(f"Python {platform.python_version()} is too old — install Python 3.10+ first.")
        sys.exit(1)
    ok(f"Python {platform.python_version()} on {platform.system()}")

    pip_install()

    env = read_env()

    # Detect which backends are actually installed on THIS machine. We never
    # write a config that points at a CLI that isn't here — on office PCs only
    # one of the two is usually present, so the choice should follow reality.
    paths = {"agy": detect_backend("agy"), "claude": detect_backend("claude")}
    present = [b for b, p in paths.items() if p]
    label = {"agy": "agy (antigravity CLI / Gemini)",
             "claude": "Claude Code (Opus/Sonnet/Haiku)"}

    print()
    if not present:
        bad("Neither backend is installed on this computer.")
        info("Install ONE of these, run it once to log in, then re-run this installer:")
        info("  • agy     — the antigravity CLI (Gemini)")
        info("  • claude  — Claude Code")
        # Fall back to the requested/previous backend so .env is still written;
        # --doctor will keep flagging it until a CLI is present.
        backend = (arg_backend if arg_backend in ("agy", "claude")
                   else env.get("BACKEND", "agy"))
    elif len(present) == 1:
        backend = present[0]
        ok(f"Detected one backend: {label[backend]} — using it.")
        if non_interactive and arg_backend in ("agy", "claude") and arg_backend != backend:
            info(f"(You asked for '{arg_backend}', but only '{backend}' is installed here.)")
    else:  # both installed → genuinely ask
        if non_interactive:
            backend = arg_backend if arg_backend in present else env.get("BACKEND", present[0])
            if backend not in present:
                backend = present[0]
        else:
            print("  Both backends are installed. Which should power the bot?")
            print(f"    1) {label['agy']}")
            print(f"    2) {label['claude']}")
            default = "2" if env.get("BACKEND") == "claude" else "1"
            choice = ask("  Enter 1 or 2", default)
            backend = "claude" if choice.strip() == "2" else "agy"

    cli = "claude" if backend == "claude" else "agy"
    cli_path = paths.get(cli)
    if cli_path:
        ok(f"{cli} found: {cli_path}")
        info(f"Make sure you've logged in: run  `{cli}`  once and sign in, then close it.")
    else:
        bad(f"{cli} is not installed / not on PATH.")
        info(f"Install {cli}, run it once to log in, then re-run this installer.")

    if non_interactive:
        token, owner = arg_token, arg_owner
        auto = "--no-autostart" not in sys.argv
    else:
        print()
        # Ask for the token and check it against Telegram, up to 2 tries.
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        for _attempt in range(2):
            token = ask_secret("  Paste your bot token from @BotFather (hidden as you type)", token)
            info("Checking the token with Telegram…")
            okt, detail = validate_token(token)
            if okt:
                ok(f"Token works — your bot is {detail}")
                break
            bad(f"Token check failed: {detail}")
            if not ask("  Try a different token? (y/n)", "y").lower().startswith("y"):
                info("Continuing with the token as entered — you can fix it in .env later.")
                break
        owner = ask("  Paste your Telegram numeric ID from @userinfobot", env.get("TELEGRAM_OWNER_ID", ""))
        if owner and not owner.strip().isdigit():
            bad("Your Telegram ID should be just numbers (get it from @userinfobot).")
        auto = ask("  Start automatically every time you log in? (y/n)", "y").lower().startswith("y")

    vals = {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_OWNER_ID": owner, "BACKEND": backend}
    if cli_path:
        vals["CLAUDE_PATH" if cli == "claude" else "CLI_PATH"] = cli_path
    write_env(vals)

    if auto:
        setup_autostart()
    if "--no-start" not in sys.argv:
        start_bot()

    hr()
    print("  Done! In Telegram, message your bot and send /menu.")
    print("  Re-check anytime:  python install.py --doctor")
    hr()


if __name__ == "__main__":
    main()
