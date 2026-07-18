# ============================================================
#  CONFIGURATION — Zilla Bot
# ============================================================
#  Portable, zero-hardcoded-paths configuration.
#  Copy the folder, set .env, and it works.
# ============================================================

import os
import sys
import json
import time
import logging
import subprocess

from zilla import store

logger = logging.getLogger(__name__)

# --- Dynamic Path Resolution ---
HOME_DIR = os.path.expanduser("~")
BASE_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    # Phase 1 move: this module now lives in zilla/, one level below repo
    # root — go up one more level so .env/settings.json/etc still resolve.
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

# --- ZILLA_HOME (PLAN.md §17/F1 storage constitution) ------
# The one visible, owner-facing data home — "the knowledge is the product",
# not a dot-dir. Every file Zilla writes lives under here (Memory/Media/
# Outbox/Runtime, see below); the backends' OWN conversation stores
# (BRAIN_DIR, AGY_SETTINGS_FILE, skills dirs) are explicitly NOT part of
# this — they stay wherever the backend puts them (F1 step 3: conversations
# belong to brains, knowledge belongs to you).
ZILLA_HOME = os.getenv("ZILLA_HOME") or os.path.join(HOME_DIR, "Zilla")

# --- Media (Inbox / Outbox / Kept, all under ZILLA_HOME) ---
MEDIA_DIR = os.path.join(ZILLA_HOME, "Media")
INBOX_DIR = os.path.join(MEDIA_DIR, "Inbox")
INBOX_IMAGES = os.path.join(INBOX_DIR, "images")
INBOX_AUDIO = os.path.join(INBOX_DIR, "audio")
INBOX_DOCUMENTS = os.path.join(INBOX_DIR, "documents")
MEDIA_KEPT_DIR = os.path.join(MEDIA_DIR, "Kept")  # F3: permanent, sweep-exempt
OUTBOX_DIR = os.path.join(ZILLA_HOME, "Outbox")
OUTBOX_DOCUMENTS = os.path.join(OUTBOX_DIR, "documents")
OUTBOX_IMAGES = os.path.join(OUTBOX_DIR, "images")

# --- Runtime (the machine's business: db, logs, pid, sock — PLAN.md §17) ---
RUNTIME_DIR = os.path.join(ZILLA_HOME, "Runtime")
LOG_DIR = os.path.join(RUNTIME_DIR, "logs")
PID_FILE = os.path.join(RUNTIME_DIR, "zilla.pid")
LOCK_FILE = os.path.join(RUNTIME_DIR, "zilla_bot_instance.lock")
# Human-in-the-loop ask/answer relay files (zilla/interactive.py) — machine
# plumbing, not owner-facing knowledge, so it lives in Runtime, not Memory.
BRIDGE_DIR = os.path.join(RUNTIME_DIR, "Bridge")

# M2's memory.py creates this tree (MEMORY.md, Journal/, etc). Now that F1
# has landed, it lives under ZILLA_HOME, not the repo root (see the
# migration in run_zilla_home_migration() below for owners upgrading from
# M2-M4's repo-root layout).
MEMORY_DIR = os.path.join(ZILLA_HOME, "Memory")

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

# --- Embedded browser (Playwright MCP, Claude backend only) ---
# The browser is loaded ONLY for web/interactive turns (see autoharness.needs_browser)
# so simple turns stay fast. We pin an EXACT version instead of "@latest": @latest
# forces a network version-check on every one-shot `claude -p`, which races the MCP
# startup timeout and makes the browser register only ~⅔ of the time (the bug where the
# bot silently fell back to WebFetch). A pinned version resolves from the npx cache with
# no network → deterministic, fast startup. Bump this string to upgrade.
PLAYWRIGHT_MCP_VERSION = os.getenv("PLAYWRIGHT_MCP_VERSION", "0.0.75")
# Give the (cold) MCP server room to hand-shake before Claude gives up on its tools.
MCP_STARTUP_TIMEOUT_MS = os.getenv("MCP_STARTUP_TIMEOUT_MS", "30000")
# Generated MCP config files are machine business — Runtime, not the repo.
MCP_CONFIG_DIR = os.path.join(RUNTIME_DIR, "cache", "mcp")
MCP_BROWSER_CONFIG = os.path.join(MCP_CONFIG_DIR, "browser.json")
MCP_NONE_CONFIG = os.path.join(MCP_CONFIG_DIR, "none.json")

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

# --- ffmpeg (voice-note transcription) ---
# Windows: bundled copy under ZILLA_HOME\Tools. Unix: system ffmpeg (brew/apt).
FFMPEG_PATH = os.getenv("FFMPEG_PATH") or _find_exe(
    "ffmpeg",
    os.path.join(ZILLA_HOME, "Tools", "ffmpeg", "ffmpeg.exe") if _IS_WIN else "ffmpeg",
)

# --- Skills (backend-aware: each "mode" has its own skill set) ---
# agy reads skills from its antigravity dir; Claude Code from ~/.claude/skills.
# Switching backend therefore swaps the active skill set — see get_skills_dir().
SKILLS_DIR = os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "skills")
CLAUDE_SKILLS_DIR = os.path.join(HOME_DIR, ".claude", "skills")


def get_skills_dir(backend: str | None = None) -> str:
    """Skills directory for the given backend (or the active one)."""
    b = (backend or get_backend()).strip().lower()
    return CLAUDE_SKILLS_DIR if b == "claude" else SKILLS_DIR

# --- Kimi WebBridge ---
KIMI_BRIDGE_URL = os.getenv("KIMI_BRIDGE_URL", "http://127.0.0.1:10086")

# --- State Files ---
# One shared SQLite database (PLAN.md §3.1, Phase M1) backs sessions,
# settings, users and schedules — all four constants alias the same path
# so every manager constructed in bot.py / core_setup.py naturally shares
# one zilla.db. Kept as four names (not one) because tests reassign these
# individually to distinct tmp paths for isolation (test_core.py) and
# production code still imports/passes them by their original names.
DB_FILE = os.path.join(RUNTIME_DIR, "zilla.db")
SESSIONS_FILE = DB_FILE
SETTINGS_FILE = DB_FILE
USERS_FILE = DB_FILE
SCHEDULES_FILE = DB_FILE

# Original pre-M1 locations, kept ONLY so run_first_start_migration() (below)
# knows where to look for data to import. Never assign these to the *_FILE
# names above — those all point at the new shared DB_FILE now.
_LEGACY_SESSIONS_FILE = os.path.join(BASE_DIR, "sessions.json")
_LEGACY_SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
_LEGACY_USERS_FILE = os.path.join(BASE_DIR, "authorized_users.json")
_LEGACY_DENIED_FILE = os.path.join(BASE_DIR, "denied_users.json")
_LEGACY_SCHEDULES_FILE = os.path.join(BASE_DIR, "schedules.json")

# Pre-F1 (M1-M4) locations for zilla.db/Memory/AGI-Brain, kept ONLY so
# run_zilla_home_migration() below knows where to look. F1 (PLAN.md §17)
# was written assuming these still lived under ~/AGI-Brain; M1-M4 shipped
# first and anchored zilla.db/Memory at the repo root instead — this is
# the migration path off THAT reality, not the literal AGI-Brain-only
# spec text.
_LEGACY_AGI_BRAIN_DIR = os.path.join(HOME_DIR, "AGI-Brain")
_LEGACY_DB_FILE = os.path.join(BASE_DIR, "zilla.db")
_LEGACY_MEMORY_DIR = os.path.join(BASE_DIR, "Memory")


def run_zilla_home_migration() -> dict:
    """One-time move onto ZILLA_HOME (PLAN.md §17/F1) — legacy ~/AGI-Brain
    Inbox/Outbox/Bridge plus the repo-root Memory/zilla.db that M1-M4
    created before F1 existed. No-op once ZILLA_HOME already exists.
    Must run before ensure_dirs()/memory.ensure_tree()/
    run_first_start_migration() ever touch the new paths, so the
    'ZILLA_HOME missing' gate is still true when this checks it."""
    from zilla.migrate import migrate_zilla_home
    return migrate_zilla_home(
        zilla_home=ZILLA_HOME,
        legacy_agi_brain_dir=_LEGACY_AGI_BRAIN_DIR,
        legacy_memory_dir=_LEGACY_MEMORY_DIR,
        legacy_db_file=_LEGACY_DB_FILE,
    )


def run_first_start_migration() -> dict:
    """Import the five legacy JSON files (if any still exist) into the
    shared zilla.db. PLAN.md §3.1/§5 M1 step 3 — idempotent, single
    transaction, renames originals to *.migrated only after a successful
    commit, never deletes them. Deliberately NOT called at module import
    time (this file is imported by nearly every test and by bot.py itself
    just to read constants) — the bot's own startup path calls this once,
    explicitly, before constructing any manager."""
    from zilla.migrate import migrate_legacy_json
    return migrate_legacy_json(
        store.get_store(DB_FILE),
        sessions_file=_LEGACY_SESSIONS_FILE,
        schedules_file=_LEGACY_SCHEDULES_FILE,
        users_file=_LEGACY_USERS_FILE,
        denied_file=_LEGACY_DENIED_FILE,
        settings_file=_LEGACY_SETTINGS_FILE,
    )

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
BOT_VERSION = "4.7.0"


# ── Settings (SQLite KV via store.py, Phase M1) ───────────
#
# No in-memory cache here — the store's own read connection IS the cache
# (a local read, not a network round-trip). get_store(SETTINGS_FILE) is
# called fresh on every access (not hoisted to import time) so tests that
# reassign config.SETTINGS_FILE to a tmp path after import still get an
# isolated database, matching the old per-test JSON-file isolation.


def get_setting(key: str, default=None):
    return store.get_store(SETTINGS_FILE).get_setting(key, default)


def set_setting(key: str, value):
    store.get_store(SETTINGS_FILE).set_setting(key, value)


# ── Model: read/write agy's REAL settings file ────────────
#
# agy stores the active model in AGY_SETTINGS_FILE under "model" as a display
# string ("Gemini 3.1 Pro (High)"). We read/write THAT file so a model change
# actually takes effect. We never touch the other keys agy keeps there
# (toolPermission, trustedWorkspaces, ...).

_AGY_MODEL_FALLBACK = "Gemini 3.1 Pro (High)"

# agy's model list is NOT a uniform "5 families × Low/Med/High" grid — each model
# exposes its OWN set of thinking levels (3.1 Pro is Low/High only; the Claude
# models are "(Thinking)" only; GPT-OSS is "(Medium)" only). So we DON'T build a
# cartesian product (that invented combos that don't exist — the "fake models"
# bug). The live truth comes from `agy models`; this is only the offline fallback,
# kept as the exact display strings agy itself prints. Update if agy changes.
AGY_MODELS_FALLBACK = [
    "Gemini 3.5 Flash (Low)",
    "Gemini 3.5 Flash (Medium)",
    "Gemini 3.5 Flash (High)",
    "Gemini 3.1 Pro (Low)",
    "Gemini 3.1 Pro (High)",
    "Claude Sonnet 4.6 (Thinking)",
    "Claude Opus 4.6 (Thinking)",
    "GPT-OSS 120B (Medium)",
]

# Live `agy models` cache (the binary call is ~0.3s; cache so the picker is snappy).
# "live" records whether the last fetch came from the binary (True) or fell back
# to the offline list (False) — used as an honest "agy reachable/logged-in" signal.
_agy_models_cache: dict = {"val": None, "ts": 0.0, "live": False}
_AGY_MODELS_TTL = 300.0


def _run_agy_models(timeout: float = 8.0) -> str | None:
    """Raw stdout of `agy models`, or None on any failure. Isolated so tests can
    monkeypatch it without invoking the real binary."""
    try:
        r = subprocess.run([CLI_PATH, "models"], capture_output=True, text=True,
                           timeout=timeout)
        if r.returncode == 0 and (r.stdout or "").strip():
            return r.stdout
    except Exception as e:
        logger.debug(f"[CONFIG] `agy models` failed: {e}")
    return None


def _parse_agy_models(raw: str) -> list[str]:
    """Pull display strings ('Name (Effort)') out of `agy models` output."""
    out = []
    for line in (raw or "").splitlines():
        s = line.strip()
        # Real model lines look like "Gemini 3.1 Pro (High)" — name + (level).
        if s and s.endswith(")") and "(" in s and not s.lower().startswith(("usage", "flags", "available")):
            if s not in out:
                out.append(s)
    return out


def agy_models_live(force: bool = False) -> list[str]:
    """The REAL models agy offers right now (cached). Falls back to the offline
    list if the binary can't be reached, so the picker never shows fakes."""
    now = time.time()
    if (not force and _agy_models_cache["val"] is not None
            and now - _agy_models_cache["ts"] < _AGY_MODELS_TTL):
        return _agy_models_cache["val"]
    parsed = _parse_agy_models(_run_agy_models() or "")
    val = parsed if parsed else list(AGY_MODELS_FALLBACK)
    _agy_models_cache.update(val=val, ts=now, live=bool(parsed))
    return val


def agy_reachable() -> bool:
    """True if `agy models` last returned real data (binary present + logged in).
    Refreshes the cache if it's empty so the first call is meaningful."""
    if _agy_models_cache["val"] is None:
        agy_models_live()
    return bool(_agy_models_cache.get("live"))


def _agy_label(display: str) -> str:
    """Compact button label from a full display string, e.g.
    'Gemini 3.5 Flash (Medium)' → '3.5 Flash·Med', 'Claude Opus 4.6 (Thinking)'
    → 'Opus 4.6·Think'. Keeps inline buttons short on a phone."""
    name, _, eff = display.partition(" (")
    name = name.replace("Gemini ", "").replace("Claude ", "").strip()
    eff = eff.rstrip(")").strip()
    eff_short = {"Low": "Low", "Medium": "Med", "High": "High",
                 "Thinking": "Think"}.get(eff, eff[:4])
    return f"{name}·{eff_short}" if eff_short else name


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


# Claude Code models. Values are the aliases `claude --model` accepts; they
# always resolve to the LATEST model in each family (so this never goes stale).
# Labels show the current underlying version for clarity. ✏️ Custom still lets
# you type an exact id like `claude-opus-4-8`.
CLAUDE_MODELS = [
    ("Opus 4.8", "opus"),
    ("Sonnet 4.6", "sonnet"),
    ("Haiku 4.5", "haiku"),
]
_CLAUDE_MODEL_FALLBACK = "sonnet"


def get_model_for(backend: str) -> str:
    """Active model for a GIVEN backend (agy/claude), independent of which
    backend is currently selected — lets a settings UI show/edit every
    backend's model without switching the active one."""
    b = (backend or "").strip().lower()
    if b == "claude":
        return get_setting("claude_model", _CLAUDE_MODEL_FALLBACK)
    return _read_agy_settings().get("model") or _AGY_MODEL_FALLBACK


def model_catalog_for(backend: str) -> list[tuple[str, str]]:
    """(button_label, value) pairs for a GIVEN backend's model picker,
    independent of the currently active backend."""
    b = (backend or "").strip().lower()
    if b == "claude":
        return [(label, val) for label, val in CLAUDE_MODELS]
    if b == "agy":
        return [(_agy_label(m), m) for m in agy_models_live()]
    return []


def get_model() -> str:
    """Active model for the CURRENT backend.
    - agy:    read (cached) from agy's own settings.json.
    - claude: stored in the bot's settings ('claude_model'), passed via --model.
    """
    return get_model_for(get_backend())


def model_catalog() -> list[tuple[str, str]]:
    """(button_label, value) pairs for the current backend's model picker.
    agy's list is the REAL `agy models` output (no invented combos)."""
    return model_catalog_for(get_backend())


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


def set_model_for(backend: str, model_name: str) -> str:
    """Set the model for a GIVEN backend (agy/claude); return the stored
    value. Independent of which backend is currently active."""
    b = (backend or "").strip().lower()
    if b == "claude":
        set_setting("claude_model", model_name)
        return model_name
    if b == "agy":
        return _agy_set_model(model_name)
    return model_name


def set_model(model_name: str) -> str:
    """Set the active model for the CURRENT backend; return the stored value."""
    return set_model_for(get_backend(), model_name)


def get_idle_kill_after() -> int:
    """Idle reaper threshold in seconds. 0 = never kill."""
    return get_setting("idle_kill_after", IDLE_KILL_AFTER)


def ensure_dirs():
    """Every directory in the ZILLA_HOME storage constitution (PLAN.md §17)
    that isn't owned by a more specific ensure-tree (Memory/ is
    memory.ensure_tree()'s job, so an owner's template edits are never
    raced/clobbered by this generic makedirs)."""
    for d in [
        INBOX_DIR, INBOX_IMAGES, INBOX_AUDIO, INBOX_DOCUMENTS, MEDIA_KEPT_DIR,
        OUTBOX_DIR, OUTBOX_DOCUMENTS, OUTBOX_IMAGES,
        RUNTIME_DIR, BRIDGE_DIR,
    ]:
        os.makedirs(d, exist_ok=True)
