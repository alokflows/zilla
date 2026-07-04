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
        else:  # Linux
            unit_dir = os.path.expanduser("~/.config/systemd/user")
            os.makedirs(unit_dir, exist_ok=True)
            unit = os.path.join(unit_dir, "zilla.service")
            py = sys.executable
            open(unit, "w").write(
                "[Unit]\nDescription=Zilla Telegram bot\n\n"
                "[Service]\n"
                f"ExecStart={py} {os.path.join(BASE,'run_background.py')}\n"
                f"WorkingDirectory={BASE}\nRestart=always\n\n"
                "[Install]\nWantedBy=default.target\n"
            )
            subprocess.run(["systemctl", "--user", "enable", "--now", "zilla.service"], check=False)
            ok(f"Autostart systemd unit installed: {unit}")
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
        token = ask_secret("  Paste your bot token from @BotFather (hidden as you type)", env.get("TELEGRAM_BOT_TOKEN", ""))
        owner = ask("  Paste your Telegram numeric ID from @userinfobot", env.get("TELEGRAM_OWNER_ID", ""))
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
