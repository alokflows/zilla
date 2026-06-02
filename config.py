# ============================================================
#  CONFIGURATION — Zilla Bot
# ============================================================
#  Portable, zero-hardcoded-paths configuration.
#  Copy the folder, set .env, and it works.
# ============================================================

import os
import sys
import json
import logging

logger = logging.getLogger(__name__)

# --- Dynamic Path Resolution ---
HOME_DIR = os.path.expanduser("~")
BASE_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)

# --- Load .env (zero-dependency) ---
_env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if "=" in _line and not _line.startswith("#"):
                _key, _val = _line.split("=", 1)
                if " # " in _val:
                    _val = _val.split(" # ", 1)[0]
                os.environ.setdefault(_key.strip(), _val.strip().strip("\"'"))

# --- Telegram ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OWNER_CHAT_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0") or "0")

# --- Backend selection -------------------------------------
# Which AI CLI powers the bot:
#   agy    — the antigravity CLI (Gemini models)   [default]
#   claude — Claude Code (Opus / Sonnet / Haiku)
#
# >>> TO SWITCH BACKEND: set BACKEND=claude (or agy) in your .env, or change
#     it from Telegram → /settings, then restart the bot. <<<
BACKEND = os.getenv("BACKEND", "agy").strip().lower()


def get_backend() -> str:
    """Active backend: 'agy' or 'claude' (live from settings, falling back to .env)."""
    return (get_setting("backend", None) or BACKEND or "agy").strip().lower()


def set_backend(name: str):
    set_setting("backend", name.strip().lower())


import shutil as _shutil  # noqa: E402


def _find_exe(name: str, *candidates: str) -> str:
    """First of: PATH lookup, then any candidate that exists, else the
    platform-appropriate default (so .env can still override)."""
    found = _shutil.which(name)
    if found:
        return found
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return candidates[0] if candidates else name


_IS_WIN = sys.platform == "win32"

# --- agy CLI (default backend) ---
CLI_PATH = os.getenv("CLI_PATH") or _find_exe(
    "agy",
    os.path.join(HOME_DIR, "AppData", "Local", "agy", "bin", "agy.exe") if _IS_WIN
    else os.path.join(HOME_DIR, ".local", "bin", "agy"),
)
CLI_WORKING_DIR = os.getenv("CLI_WORKING_DIR", HOME_DIR)

# --- Claude Code CLI (alternate backend) ---
# >>> To point at a different claude binary, set CLAUDE_PATH in .env. <<<
CLAUDE_PATH = os.getenv("CLAUDE_PATH") or _find_exe(
    "claude",
    os.path.join(HOME_DIR, ".local", "bin", "claude.exe") if _IS_WIN
    else os.path.join(HOME_DIR, ".local", "bin", "claude"),
)

# --- Idle reaper: kill CLI only after this many seconds of silence ---
# "Silence" = no PTY bytes AND no new transcript step.
# 0 = never kill (wait forever). Overridden at runtime via Settings panel.
IDLE_KILL_AFTER = int(os.getenv("IDLE_KILL_AFTER", "180"))  # 3 min default

# --- Catastrophic safety net: absolute max runtime regardless of activity ---
# Catches genuinely hung CLIs that keep producing garbage forever.
MAX_TOTAL_RUNTIME = int(os.getenv("MAX_TOTAL_RUNTIME", "3600"))  # 1 hour

# --- Brain Directory (where CLI stores conversations) ---
BRAIN_DIR = os.getenv(
    "BRAIN_DIR",
    os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "brain"),
)

# --- agy's OWN settings file (where the model REALLY lives) ---
# The agy CLI reads its active model from here, under the "model" key, as a
# display string like "Gemini 3.1 Pro (High)". This is the ONLY thing that
# actually changes the model — there is no --model flag and no env var the CLI
# honours. The bot must read/write THIS file, not its own settings.json.
AGY_SETTINGS_FILE = os.getenv(
    "AGY_SETTINGS_FILE",
    os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "settings.json"),
)

# --- AGI Brain (Inbox / Outbox on disk) ---
AGI_BRAIN_DIR = os.path.join(HOME_DIR, "AGI-Brain")
INBOX_DIR = os.path.join(AGI_BRAIN_DIR, "Inbox")
INBOX_IMAGES = os.path.join(INBOX_DIR, "images")
INBOX_AUDIO = os.path.join(INBOX_DIR, "audio")
INBOX_DOCUMENTS = os.path.join(INBOX_DIR, "documents")
OUTBOX_DIR = os.path.join(AGI_BRAIN_DIR, "Outbox")
OUTBOX_DOCUMENTS = os.path.join(OUTBOX_DIR, "documents")
OUTBOX_IMAGES = os.path.join(OUTBOX_DIR, "images")

# --- ffmpeg (voice-note transcription) ---
# Windows: bundled copy under AGI-Brain\Tools. Unix: system ffmpeg (brew/apt).
FFMPEG_PATH = os.getenv("FFMPEG_PATH") or _find_exe(
    "ffmpeg",
    os.path.join(AGI_BRAIN_DIR, "Tools", "ffmpeg", "ffmpeg.exe") if _IS_WIN else "ffmpeg",
)

# --- Skills ---
SKILLS_DIR = os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "skills")

# --- Kimi WebBridge ---
KIMI_BRIDGE_URL = os.getenv("KIMI_BRIDGE_URL", "http://127.0.0.1:10086")

# --- State Files ---
SESSIONS_FILE = os.path.join(BASE_DIR, "sessions.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
USERS_FILE = os.path.join(BASE_DIR, "authorized_users.json")
SCHEDULES_FILE = os.path.join(BASE_DIR, "schedules.json")

# ┌─────────────────────────────────────────────────────────┐
# │  USER MANAGEMENT                                        │
# │                                                         │
# │  From Telegram (owner only):                            │
# │    /adduser  — interactive panel via /menu > Users      │
# │    /listusers — list + manage from buttons              │
# │                                                         │
# │  Or edit authorized_users.json directly:                │
# │    {                                                     │
# │      "TELEGRAM_USER_ID": {                               │
# │        "name": "Alice",                                  │
# │        "role": "user",     ← or "admin"                 │
# │        "added_at": "2026-01-01 00:00:00"                │
# │      }                                                   │
# │    }                                                     │
# │                                                         │
# │  Roles:                                                 │
# │    user  — chat, voice, media                           │
# │    admin — + model/settings change, /browse, file gen   │
# │    owner — + user management (set in .env)              │
# │                                                         │
# │  Get a Telegram ID: message @userinfobot                │
# └─────────────────────────────────────────────────────────┘

# --- Telegram Limits ---
TELEGRAM_MAX_LENGTH = 4000
TELEGRAM_MAX_SEND_FILE = 50 * 1024 * 1024  # 50 MB

# --- Bot ---
BOT_VERSION = "4.1.0"


# ── Settings (simple JSON dict) ───────────────────────────

_settings_cache: dict | None = None


def _load_settings() -> dict:
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            _settings_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _settings_cache = {}
    return _settings_cache


def _save_settings(data: dict):
    global _settings_cache
    _settings_cache = data
    try:
        tmp = f"{SETTINGS_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SETTINGS_FILE)
    except Exception as e:
        logger.error(f"[CONFIG] Failed to save settings: {e}")


def get_setting(key: str, default=None):
    return _load_settings().get(key, default)


def set_setting(key: str, value):
    data = _load_settings()
    data[key] = value
    _save_settings(data)


# ── Model: read/write agy's REAL settings file ────────────
#
# agy stores the active model in AGY_SETTINGS_FILE under "model" as a display
# string ("Gemini 3.1 Pro (High)"). We read/write THAT file so a model change
# actually takes effect. We never touch the other keys agy keeps there
# (toolPermission, trustedWorkspaces, ...).

_AGY_MODEL_FALLBACK = "Gemini 3.1 Pro (High)"

# Real agy models. Base names are exactly the display strings agy builds from
# its own internal model keys (Gemini31Pro, Gemini3Flash, Gemini25Pro,
# Gemini25Flash, Gemini31FlashLite). The effort suffix uses agy's "%s (%s)"
# format with its Low/Medium/High thinking levels. The full display string
# (e.g. "Gemini 3.1 Pro (High)") is written verbatim into agy's settings.json.
# Edit here if agy adds or renames a model.
AGY_MODELS = [
    ("3.1 Pro", "Gemini 3.1 Pro"),
    ("3 Flash", "Gemini 3 Flash"),
    ("2.5 Pro", "Gemini 2.5 Pro"),
    ("2.5 Flash", "Gemini 2.5 Flash"),
    ("3.1 Lite", "Gemini 3.1 Flash Lite"),
]
AGY_EFFORTS = ["Low", "Medium", "High"]


def model_display(base: str, effort: str) -> str:
    """The exact string agy stores: 'Gemini 3.1 Pro (High)'."""
    return f"{base} ({effort})"


# mtime-gated cache so get_model() doesn't hit disk on every call.
_agy_cache: dict | None = None
_agy_cache_mtime: float = -1.0


def _read_agy_settings() -> dict:
    global _agy_cache, _agy_cache_mtime
    try:
        mtime = os.path.getmtime(AGY_SETTINGS_FILE)
    except OSError:
        mtime = -1.0
    if _agy_cache is not None and mtime == _agy_cache_mtime:
        return _agy_cache
    try:
        with open(AGY_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            _agy_cache = data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _agy_cache = {}
    _agy_cache_mtime = mtime
    return _agy_cache


# Claude Code models (aliases accepted by `claude --model`).
CLAUDE_MODELS = [
    ("Opus", "opus"),
    ("Sonnet", "sonnet"),
    ("Haiku", "haiku"),
]
_CLAUDE_MODEL_FALLBACK = "sonnet"


def get_model() -> str:
    """Active model for the CURRENT backend.
    - agy:    read (cached) from agy's own settings.json.
    - claude: stored in the bot's settings ('claude_model'), passed via --model.
    """
    if get_backend() == "claude":
        return get_setting("claude_model", _CLAUDE_MODEL_FALLBACK)
    return _read_agy_settings().get("model") or _AGY_MODEL_FALLBACK


def model_catalog() -> list[tuple[str, str]]:
    """(button_label, value) pairs for the current backend's model picker."""
    if get_backend() == "claude":
        return [(label, val) for label, val in CLAUDE_MODELS]
    # agy: 5 Gemini families × Low/Med/High thinking levels
    out = []
    for tag, base in AGY_MODELS:
        for eff in AGY_EFFORTS:
            out.append((f"{tag}·{eff[:3]}", model_display(base, eff)))
    return out


def _agy_set_model(model_name: str) -> str:
    """
    Write the model into agy's real settings file (atomically, preserving every
    other key) and return the value as it is now stored — so callers can show
    the user the *actual* persisted value, not a hopeful echo.
    """
    data = _read_agy_settings()
    data["model"] = model_name
    try:
        os.makedirs(os.path.dirname(AGY_SETTINGS_FILE), exist_ok=True)
        tmp = f"{AGY_SETTINGS_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, AGY_SETTINGS_FILE)
    except OSError as e:
        logger.error(f"[CONFIG] Failed to write agy model: {e}")
        return get_model()
    global _agy_cache_mtime
    _agy_cache_mtime = -1.0  # force re-read on the next get_model()
    # Read back from disk: this is the source of truth the CLI will load.
    return _read_agy_settings().get("model") or _AGY_MODEL_FALLBACK


def set_model(model_name: str) -> str:
    """Set the active model for the CURRENT backend; return the stored value."""
    if get_backend() == "claude":
        set_setting("claude_model", model_name)
        return model_name
    return _agy_set_model(model_name)


def get_idle_kill_after() -> int:
    """Idle reaper threshold in seconds. 0 = never kill."""
    return get_setting("idle_kill_after", IDLE_KILL_AFTER)


def ensure_dirs():
    for d in [
        INBOX_DIR, INBOX_IMAGES, INBOX_AUDIO, INBOX_DOCUMENTS,
        OUTBOX_DIR, OUTBOX_DOCUMENTS, OUTBOX_IMAGES,
    ]:
        os.makedirs(d, exist_ok=True)
