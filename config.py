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

# --- CLI Backend ---
CLI_PATH = os.getenv(
    "CLI_PATH",
    os.path.join(HOME_DIR, "AppData", "Local", "agy", "bin", "agy.exe"),
)
CLI_WORKING_DIR = os.getenv("CLI_WORKING_DIR", HOME_DIR)

# --- Idle reaper: kill CLI only after this many seconds of silence ---
# "Silence" = no PTY bytes AND no new transcript step.
# 0 = never kill (wait forever). Overridden at runtime via Settings panel.
IDLE_KILL_AFTER = int(os.getenv("IDLE_KILL_AFTER", "600"))  # 10 min default

# --- Catastrophic safety net: absolute max runtime regardless of activity ---
# Catches genuinely hung CLIs that keep producing garbage forever.
MAX_TOTAL_RUNTIME = int(os.getenv("MAX_TOTAL_RUNTIME", "3600"))  # 1 hour

# --- Brain Directory (where CLI stores conversations) ---
BRAIN_DIR = os.getenv(
    "BRAIN_DIR",
    os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "brain"),
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

# --- Portable ffmpeg ---
FFMPEG_PATH = os.getenv(
    "FFMPEG_PATH",
    os.path.join(AGI_BRAIN_DIR, "Tools", "ffmpeg", "ffmpeg.exe"),
)

# --- Skills ---
SKILLS_DIR = os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "skills")

# --- Kimi WebBridge ---
KIMI_BRIDGE_URL = os.getenv("KIMI_BRIDGE_URL", "http://127.0.0.1:10086")

# --- State Files ---
SESSIONS_FILE = os.path.join(BASE_DIR, "sessions.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
USERS_FILE = os.path.join(BASE_DIR, "authorized_users.json")

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
BOT_VERSION = "1.0"


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


def get_model() -> str:
    return get_setting("model", "gemini-2.5-pro")


def set_model(model_id: str):
    set_setting("model", model_id)


def get_idle_kill_after() -> int:
    """Idle reaper threshold in seconds. 0 = never kill."""
    return get_setting("idle_kill_after", IDLE_KILL_AFTER)


def ensure_dirs():
    for d in [
        INBOX_DIR, INBOX_IMAGES, INBOX_AUDIO, INBOX_DOCUMENTS,
        OUTBOX_DIR, OUTBOX_DOCUMENTS, OUTBOX_IMAGES,
    ]:
        os.makedirs(d, exist_ok=True)
