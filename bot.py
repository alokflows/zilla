# ============================================================
#  Zilla Bot v1.0 — THIN PIPE TO CLI
# ============================================================
#
#  The bot is a THIN WRAPPER around the AI CLI.
#  User sends message → CLI → bot relays response.
#  CLI does ALL the thinking. Bot handles Telegram UX.
#
#  Roles:
#    user  — chat, voice, media
#    admin — + model/settings, /browse, file delivery
#    owner — + full user management
#
#  Commands:
#  /start     — Welcome + status
#  /help      — Full command reference
#  /ping      — Health check
#  /menu      — Master control panel
#  /cancel    — Cancel running request
#  /new       — New session
#  /sessions  — List sessions
#  /switch    — Switch session
#  /end       — End current session
#  /model     — Select AI model (admin+)
#  /settings  — Bot settings (admin+)
#  /browse    — Browser control (admin+)
#  /adduser   — Add user (owner only)
#  /removeuser — Remove user (owner only)
#  /listusers — Manage users (owner only)
#  /brain     — Inbox stats
#  Voice → transcribe → CLI
#  Photo → analyze (if caption) or save
#  Document → analyze (if caption) or save
# ============================================================

from platform_compat import (
    apply_window_hiding, acquire_instance_lock, release_instance_lock, IS_WINDOWS,
)
apply_window_hiding()  # MUST be early: hides child console windows on Windows (no-op elsewhere)
import asyncio
import atexit
import sys
import os
import re
import logging
import threading
import time
import json
from datetime import datetime

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
    TypeHandler,
    ApplicationHandlerStop,
)

from config import (
    BOT_TOKEN, OWNER_CHAT_ID, USERS_FILE, SESSIONS_FILE,
    TELEGRAM_MAX_LENGTH, TELEGRAM_MAX_SEND_FILE, BOT_VERSION,
    AGI_BRAIN_DIR, HOME_DIR, ensure_dirs, KIMI_BRIDGE_URL,
    get_model, set_model, get_idle_kill_after, get_setting, set_setting,
    OUTBOX_DIR, OUTBOX_DOCUMENTS, OUTBOX_IMAGES, BRAIN_DIR,
    SCHEDULES_FILE, agy_models_live,
    model_catalog, get_backend, set_backend,
)
from sessions import SessionManager
from cli_engine import run_cli_async, get_latest_step, detect_limit, backend_status
from media import (
    is_audio_capable, get_audio_status, transcribe_audio,
    save_photo, save_voice, save_audio, save_document, save_video,
    get_inbox_stats, get_inbox_items, get_inbox_counts, delete_inbox_file,
    get_outbox_items, get_outbox_counts, delete_outbox_file,
    format_file_size, extract_text,
)
from formatter import format_for_telegram, detect_file_paths
from harness import log_event, log_summary
import interactive
from users import AuthManager
from schedules import ScheduleManager, describe as describe_schedule
from schedule_parse import parse_schedule, parse_schedule_command

# ── Logging ────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Under pythonw.exe (hidden launcher) there is no console — sys.stdout is None.
# Only attach the console handler when a real stdout exists; always log to file.
_log_handlers = [
    logging.FileHandler(
        os.path.join(LOG_DIR, f"bot_{datetime.now().strftime('%Y%m%d')}.log"),
        encoding="utf-8",
    ),
]
if sys.stdout is not None:
    _log_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_log_handlers,
)


class _TokenRedactFilter(logging.Filter):
    """Strip the bot token out of any log record. python-telegram-bot/httpx log
    full request URLs of the form api.telegram.org/bot<TOKEN>/... — without this
    the live token lands in plaintext log files."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            tok = BOT_TOKEN
            if tok:
                msg = record.getMessage()
                if tok in msg:
                    record.msg = msg.replace(tok, "bot<REDACTED>")
                    record.args = ()
        except Exception:
            pass
        return True


# Attach to the root + the noisy HTTP libraries so it covers every handler.
for _ln in ("", "httpx", "telegram", "telegram.ext", "telegram.request"):
    logging.getLogger(_ln).addFilter(_TokenRedactFilter())

logger = logging.getLogger(__name__)


def _harden_file_perms() -> None:
    """Best-effort chmod 600 on secret/state files (no-op on Windows)."""
    if os.name == "nt":
        return
    base = os.path.dirname(os.path.abspath(__file__))
    for name in (".env", "sessions.json", "settings.json", "schedules.json",
                 "authorized_users.json", "denied_users.json"):
        p = os.path.join(base, name)
        try:
            if os.path.exists(p):
                os.chmod(p, 0o600)
        except OSError:
            pass
    try:
        if os.path.isdir(LOG_DIR):
            os.chmod(LOG_DIR, 0o700)
            for f in os.listdir(LOG_DIR):
                fp = os.path.join(LOG_DIR, f)
                if os.path.isfile(fp):
                    os.chmod(fp, 0o600)
    except OSError:
        pass

BOT_START_TIME = time.time()

# ── Global State ───────────────────────────────────────────
sessions: SessionManager = None
auth: AuthManager = None
schedules_mgr: ScheduleManager = None

# Per-chat cancel events — set to cancel the active CLI request for that chat
_active_cancel: dict[int, threading.Event] = {}

# Human-in-the-loop credential/OTP bridge: chat_id -> ask_id currently awaiting
# that chat's reply, plus the set of ask ids we've already DMed (so the watcher
# doesn't re-prompt). See interactive.py for the file protocol.
_pending_bridge: dict[int, tuple[str, float]] = {}
_bridge_announced: set[str] = set()
# How long a chat stays bound to one ask. After this, an unanswered (orphaned)
# ask must NOT keep swallowing the user's next unrelated message.
_BRIDGE_PENDING_TTL = 900.0

# Per-chat id of the CURRENT live menu message. When a new menu opens we strip
# the previous one's buttons so old menus in the chat history can't be tapped
# again (no stale session/menu collisions). The ✕ Close button clears it too.
_active_menu: dict[int, int] = {}

# Per-user CLI serialization. The agy CLI keeps ONE conversation per user, and
# running two invocations against the same conversation at once corrupts its
# transcript and makes each handler scoop up the other turn's steps (responses
# bleed into the wrong reply). With concurrent_updates(True) the event loop can
# enter several handlers for one user at once, so we gate every CLI run behind a
# per-user asyncio.Lock — a user's messages run one at a time, different users
# stay fully concurrent. Created lazily on the single-threaded event loop, so
# get-or-create needs no lock of its own.
_user_cli_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(uid: int) -> asyncio.Lock:
    lock = _user_cli_locks.get(uid)
    if lock is None:
        lock = asyncio.Lock()
        _user_cli_locks[uid] = lock
    return lock


def _conv_for_run(uid: int, sname: str):
    """The conversation id to resume — but only if it was created by the CURRENT
    backend. agy brain-dir ids and claude session ids aren't interchangeable, so
    after switching backend we start a fresh conversation instead of mismatching."""
    cid = sessions.get_conversation_id(user_id=uid, session_name=sname)
    if cid and sessions.get_conv_backend(uid, sname) != get_backend():
        return None
    return cid


async def _acquire_turn(uid: int, update: Update) -> asyncio.Lock:
    """Return this user's CLI lock, ready to enter with `async with`. If a
    previous message is still running, send one calm heads-up so the new message
    doesn't feel ignored while it waits its turn."""
    lock = _get_user_lock(uid)
    if lock.locked():
        try:
            await update.effective_message.reply_text(
                "⏳ One sec — finishing your previous message first, then I'll get to this."
            )
        except Exception:
            pass
    return lock

# Idle-reaper cycle options shown in Settings
_IDLE_OPTIONS = [
    (120, "2 min — Fast"),
    (180, "3 min — Default"),
    (300, "5 min — Patient"),
    (0, "No reaper"),
]


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════

def split_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    if len(text) <= max_length:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        sp = text.rfind("\n", 0, max_length)
        if sp == -1 or sp < max_length // 2:
            sp = max_length
        chunks.append(text[:sp])
        text = text[sp:].lstrip("\n")
    return chunks


async def keep_typing(bot, chat_id: int, stop_event: asyncio.Event):
    """
    Non-spammy progress indicator.
    - 0–60s: native typing bubble only.
    - 60s: send ONE status message with a Cancel button.
    - Every 60s: EDIT that same message (never spam new messages).
    - On stop: delete the status message silently.
    """
    start = asyncio.get_event_loop().time()
    status_msg = None
    last_edit_elapsed = 0.0

    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass

        elapsed = asyncio.get_event_loop().time() - start
        m, s = divmod(int(elapsed), 60)
        elapsed_str = f"{m}m {s}s" if m else f"{s}s"

        if elapsed >= 60:
            cancel_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🛑 Cancel", callback_data="cancel_active")]
            ])
            status_text = f"⏳ Working… {elapsed_str}"

            if status_msg is None:
                try:
                    status_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=status_text,
                        reply_markup=cancel_kb,
                    )
                    last_edit_elapsed = elapsed
                except Exception:
                    pass
            elif elapsed - last_edit_elapsed >= 60:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                        text=status_text,
                        reply_markup=cancel_kb,
                    )
                    last_edit_elapsed = elapsed
                except Exception:
                    pass

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
            break
        except asyncio.TimeoutError:
            continue

    # Clean up status message
    if status_msg:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=status_msg.message_id)
        except Exception:
            pass


async def safe_send(bot, chat_id: int, text: str, parse_mode: str = None):
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"Failed to send message: {e}")


async def safe_send_file(bot, chat_id: int, filepath: str, caption: str = None,
                         conv_id: str = None, user_id: int = None):
    """Send a file — validates against a strict allowlist."""
    if user_id is not None and not auth.is_authorized(user_id):
        return False
    if not os.path.exists(filepath):
        return False

    # Resolve symlinks to prevent junction/symlink escape
    abs_path = os.path.realpath(filepath)

    # Allowlist: AGI-Brain (Outbox lives here) + this conversation's CLI brain folder
    safe_prefixes = [
        os.path.realpath(AGI_BRAIN_DIR),
    ]
    if conv_id:
        # conv_id is a UUID — join directly without basename to avoid stripping
        safe_prefixes.append(
            os.path.realpath(os.path.join(BRAIN_DIR, conv_id))
        )

    abs_lower = abs_path.lower()
    if not any(
        abs_lower == p.lower() or abs_lower.startswith(p.lower() + os.sep)
        for p in safe_prefixes
    ):
        logger.warning(f"[FILE] BLOCKED path outside allowlist: {filepath}")
        return False

    size = os.path.getsize(abs_path)
    if size > TELEGRAM_MAX_SEND_FILE:
        await safe_send(bot, chat_id, f"⚠️ File too large ({format_file_size(size)} > 50 MB)")
        return False

    try:
        with open(abs_path, "rb") as f:
            await bot.send_document(
                chat_id=chat_id, document=f,
                caption=caption or os.path.basename(abs_path),
            )
        logger.info(f"[FILE] Sent: {abs_path} ({format_file_size(size)})")
        return True
    except Exception as e:
        logger.error(f"[FILE] Send failed: {e}")
        return False


def get_uptime_str() -> str:
    elapsed = int(time.time() - BOT_START_TIME)
    d, r = divmod(elapsed, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def _elapsed_str(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}m {s}s" if m else f"{s}s"


# ══════════════════════════════════════════════════════════
#  AUTH MIDDLEWARE
# ══════════════════════════════════════════════════════════

async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        raise ApplicationHandlerStop()
    auth.reload()  # only re-reads if file changed (mtime check)
    uid = update.effective_user.id
    if not auth.is_authorized(uid):
        if update.callback_query:
            await update.callback_query.answer()
        logger.info(f"[AUTH] Denied: {uid}")
        raise ApplicationHandlerStop()


# ══════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════

def _close_btn():
    return InlineKeyboardButton("✕ Close", callback_data="menu_close")


async def _open_menu(update, context, text, reply_markup, parse_mode=None):
    """Open a NEW menu from a command: strip the previous menu's buttons (so old
    menus can't be re-tapped) and remember this one as the live menu."""
    chat_id = update.effective_chat.id
    prev = _active_menu.get(chat_id)
    msg = await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    if prev and prev != msg.message_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=prev, reply_markup=None)
        except Exception:
            pass
    _active_menu[chat_id] = msg.message_id
    return msg


def _can_change_model(uid: int) -> bool:
    """Owner always; admins only if the owner has enabled it."""
    if not auth:
        return False
    return auth.can_change_model(uid, get_setting("admins_can_change_model", True))


async def _backend_panel() -> str:
    """Live identity of the active backend (account, plan, current model).
    Runs the (cached) status lookup off the event loop so menus stay instant."""
    st = await asyncio.to_thread(backend_status)
    lines = [f"🧠 {st['label']}"]
    if st["backend"] == "claude":
        if st.get("logged_in"):
            acct = st.get("account") or "unknown"
            plan = (st.get("plan") or "").capitalize()
            lines.append(f"✅ Logged in: {acct}" + (f" · {plan}" if plan else ""))
        else:
            err = f" ({st['error']})" if st.get("error") else ""
            lines.append(f"🔴 Not logged in{err} — run: claude auth login")
    else:  # agy
        if not st.get("installed"):
            lines.append("🔴 Not installed on this Mac yet (setup pending)")
        elif st.get("logged_in"):
            lines.append(f"✅ Logged in · {st.get('auth_method') or 'Google OAuth'}")
        else:
            err = f" ({st['error']})" if st.get("error") else ""
            lines.append(f"🟡 Installed but not responding{err}")
    cur = st.get("model")
    if cur:
        label = next((lbl for lbl, val in model_catalog() if val == cur), cur)
        lines.append(f"🤖 Model: {label}  ({cur})")
    return "\n".join(lines)


async def _health_panel() -> str:
    """Diagnostics from the structured trust log — shows the trust gate and the
    self-healing scheduler actually working. Read off the event loop."""
    counts = await asyncio.to_thread(log_summary)
    turns = counts.get("turn_end", 0)
    flagged = counts.get("hallucination_flagged", 0)
    retries = counts.get("hallucination_retry", 0)
    sched_ok = counts.get("schedule_ok", 0)
    sched_fail = counts.get("schedule_failed", 0)
    lines = [
        "🩺 Health & Diagnostics",
        "═══════════════════",
        "",
        await _backend_panel(),
        "",
        f"⏱️ Uptime: {get_uptime_str()}",
        f"💬 Turns handled: {turns}",
        f"🛡️ Hallucinations caught: {flagged}"
        + (f" · {retries} re-checked" if retries else ""),
        f"⏰ Scheduled runs: {sched_ok} ok · {sched_fail} failed",
        "",
        f"🔧 Zilla v{BOT_VERSION}",
        "Trust log: logs/trust_log.jsonl",
    ]
    return "\n".join(lines)


def _model_note() -> str:
    """Backend-specific hint shown under the model picker."""
    if get_backend() == "claude":
        return ("ℹ️ Backend: Claude Code. Pick Opus/Sonnet/Haiku, or ✏️ Custom for "
                "an exact model name. (Switch backend in /settings.)")
    return ("ℹ️ Backend: agy. This is the LIVE list from your Antigravity account "
            "(via `agy models`). Tap one to switch — it applies to your next "
            "message. ✏️ Custom takes any exact name agy accepts.")


def kb_menu(uid: int = 0):
    is_admin = bool(auth and auth.can(uid, "admin"))
    rows = [
        [InlineKeyboardButton("📁 Sessions", callback_data="menu_sessions"),
         InlineKeyboardButton("📥 Inbox", callback_data="menu_inbox")],
        [InlineKeyboardButton("📤 Outbox", callback_data="menu_outbox")],
    ]
    if is_admin:
        # ⏰ Schedules previously had NO menu entry (command-only) — added here.
        rows.append([
            InlineKeyboardButton("⏰ Schedules", callback_data="menu_schedules"),
            InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
        ])
        model_row = [InlineKeyboardButton("🌐 Browse", callback_data="menu_browse")]
        if _can_change_model(uid):
            model_row.insert(0, InlineKeyboardButton("🤖 Model", callback_data="menu_model"))
        rows.append(model_row)
    rows.append([
        InlineKeyboardButton("🖥️ Status", callback_data="menu_status"),
        InlineKeyboardButton("🩺 Health", callback_data="menu_health"),
    ])
    if auth and auth.is_owner(uid):
        rows.append([InlineKeyboardButton("👥 Users", callback_data="menu_users")])
    rows.append([_close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_sessions(all_sessions: dict, active: str):
    buttons = []
    for name, info in all_sessions.items():
        marker = " ◀" if name == active else ""
        msgs = info.get("messages", 0)
        # Switch on the left, delete (🗑) on the right of the same row.
        buttons.append([
            InlineKeyboardButton(
                f"{name}{marker} ({msgs} msgs)",
                callback_data=f"sess_switch_{name}",
            ),
            InlineKeyboardButton("🗑", callback_data=f"sess_delete_{name}"),
        ])
    buttons.append([
        InlineKeyboardButton("➕ New", callback_data="sess_new"),
        InlineKeyboardButton("◀ Menu", callback_data="menu_back"),
        _close_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def kb_session_delete(name: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, delete", callback_data=f"sess_confirm_del_{name}"),
         InlineKeyboardButton("❌ Cancel", callback_data="sess_list")],
    ])


def kb_model(current: str):
    """Model picker for the ACTIVE backend (agy=Gemini×effort, claude=Opus/Sonnet/Haiku).
    ✓ marks the live value. Catalog comes from config.model_catalog()."""
    catalog = model_catalog()        # list of (label, value)
    buttons, row = [], []
    per_row = 3 if len(catalog) > 4 else 1
    for label, value in catalog:
        mark = "✓ " if value == current else ""
        row.append(InlineKeyboardButton(f"{mark}{label}", callback_data=f"model_{value}"))
        if len(row) == per_row:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    other = "claude" if get_backend() == "agy" else "agy"
    buttons.append([
        InlineKeyboardButton("✏️ Custom…", callback_data="model_custom"),
        InlineKeyboardButton(f"🧠 Use {other}", callback_data="model_switch_backend"),
    ])
    buttons.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(buttons)


def _idle_label(val: int) -> str:
    for v, label in _IDLE_OPTIONS:
        if v == val:
            return label
    return f"{val}s"


def kb_settings(uid: int = 0):
    auto_photo = get_setting("auto_describe_photos", False)
    rows = [
        [InlineKeyboardButton(
            f"📸 Auto-analyze photos: {'ON' if auto_photo else 'OFF'}",
            callback_data="set_toggle_photo",
        )],
    ]
    if auth and auth.can(uid, "admin"):
        idle_kill = get_idle_kill_after()
        rows.append([InlineKeyboardButton(
            f"⏱️ Idle reaper: {_idle_label(idle_kill)}",
            callback_data="set_cycle_idle",
        )])
    if auth and auth.can(uid, "admin"):
        catchup = get_setting("schedule_catchup", True)
        rows.append([InlineKeyboardButton(
            f"⏰ Catch up missed schedules: {'ON' if catchup else 'OFF'}",
            callback_data="set_toggle_catchup",
        )])
    if auth and auth.is_owner(uid):
        admins_model = get_setting("admins_can_change_model", True)
        rows.append([InlineKeyboardButton(
            f"🤖 Admins can change model: {'ON' if admins_model else 'OFF'}",
            callback_data="set_toggle_admin_model",
        )])
        rows.append([InlineKeyboardButton(
            f"🧠 Backend: {get_backend()}  (tap to switch)",
            callback_data="set_toggle_backend",
        )])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()],
    ])


def kb_error():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Retry", callback_data="err_retry"),
         InlineKeyboardButton("🤖 Change Model", callback_data="err_model")],
    ])


def kb_users(users: dict):
    buttons = []
    for uid_int, info in users.items():
        name = info.get("name") or f"User {uid_int}"
        role = info.get("role", "user")
        buttons.append([InlineKeyboardButton(
            f"[{role}] {name}", callback_data=f"user_detail_{uid_int}",
        )])
    buttons.append([
        InlineKeyboardButton("➕ Add User", callback_data="user_add_start"),
        InlineKeyboardButton("◀ Menu", callback_data="menu_back"),
        _close_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def kb_user_detail(target_id: int, role: str = "admin"):
    # Everyone added is an admin — no role toggle anymore.
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Remove", callback_data=f"user_remove_{target_id}"),
         InlineKeyboardButton("◀ Back", callback_data="user_list")],
    ])


# ── Inbox (drill-down: categories → paginated list → send) ───
INBOX_PAGE = 10
# Max files auto-attached per response. Beyond this, the rest stay in the
# Outbox and the user pulls them via 📤 Outbox (kept sane so one huge result
# can't spam the chat with dozens of uploads).
MAX_AUTO_DELIVER = 10
# Only auto-attach files the agent JUST produced. A continued conversation can
# echo old file paths in its context; without this gate the bot re-delivers
# (vomits) files from earlier turns. Files made this turn are seconds old.
FRESH_DELIVER_WINDOW = 300  # seconds


def _fresh_files(paths: list, window: int = FRESH_DELIVER_WINDOW) -> list:
    """Keep only paths modified within `window` seconds — deliver what was just
    created, never re-send old files a response happens to mention."""
    now = time.time()
    out = []
    for p in paths:
        try:
            if now - os.path.getmtime(p) <= window:
                out.append(p)
        except OSError:
            pass
    return out
INBOX_CAT_META = [
    ("images", "📷 Images"),
    ("audio", "🎵 Audio"),
    ("video", "🎬 Video"),
    ("documents", "📄 Documents"),
]


def kb_inbox_categories(counts: dict):
    rows = []
    for cat, label in INBOX_CAT_META:
        n = counts.get(cat, 0)
        if n:
            rows.append([InlineKeyboardButton(
                f"{label} ({n})", callback_data=f"ibx_cat_{cat}_0",
            )])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_inbox_list(category: str, items: list, offset: int):
    """One row per file in this page: [ name (size) | 📤 ]; both send it."""
    rows = []
    page = items[offset:offset + INBOX_PAGE]
    for i, item in enumerate(page):
        idx = offset + i
        name = item["name"]
        label = name if len(name) <= 24 else name[:21] + "…"
        send_cb = f"ibx_send_{category}_{idx}"
        rows.append([
            InlineKeyboardButton(
                f"{label} ({format_file_size(item['size'])})", callback_data=send_cb),
            InlineKeyboardButton("📤", callback_data=send_cb),
            InlineKeyboardButton("🗑", callback_data=f"ibx_del_{category}_{idx}"),
        ])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(
            "⬅️ Prev", callback_data=f"ibx_cat_{category}_{max(0, offset - INBOX_PAGE)}"))
    if offset + INBOX_PAGE < len(items):
        nav.append(InlineKeyboardButton(
            "More ➡️", callback_data=f"ibx_cat_{category}_{offset + INBOX_PAGE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀ Categories", callback_data="menu_inbox"), _close_btn()])
    return InlineKeyboardMarkup(rows)


# ── Outbox (agent-produced files) ─────────────────────────
# Same UX as the Inbox, but over ~/AGI-Brain/Outbox. Outbox has no audio.
OUTBOX_CAT_META = [
    ("images", "📷 Images"),
    ("video", "🎬 Video"),
    ("documents", "📄 Documents"),
]


def kb_outbox_categories(counts: dict):
    rows = []
    for cat, label in OUTBOX_CAT_META:
        n = counts.get(cat, 0)
        if n:
            rows.append([InlineKeyboardButton(
                f"{label} ({n})", callback_data=f"obx_cat_{cat}_0",
            )])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_outbox_list(category: str, items: list, offset: int):
    """One row per file: [ name (size) | 📤 send | 🗑 delete ]."""
    rows = []
    page = items[offset:offset + INBOX_PAGE]
    for i, item in enumerate(page):
        idx = offset + i
        name = item["name"]
        label = name if len(name) <= 24 else name[:21] + "…"
        send_cb = f"obx_send_{category}_{idx}"
        rows.append([
            InlineKeyboardButton(
                f"{label} ({format_file_size(item['size'])})", callback_data=send_cb),
            InlineKeyboardButton("📤", callback_data=send_cb),
            InlineKeyboardButton("🗑", callback_data=f"obx_del_{category}_{idx}"),
        ])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(
            "⬅️ Prev", callback_data=f"obx_cat_{category}_{max(0, offset - INBOX_PAGE)}"))
    if offset + INBOX_PAGE < len(items):
        nav.append(InlineKeyboardButton(
            "More ➡️", callback_data=f"obx_cat_{category}_{offset + INBOX_PAGE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀ Categories", callback_data="menu_outbox"), _close_btn()])
    return InlineKeyboardMarkup(rows)


# ── Schedules ─────────────────────────────────────────────

def _fmt_next(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%a %d %b %H:%M")


def kb_schedules(items: list):
    """Row per schedule: [▶ toggle/title · next] then [▶️ run | 🗑] underneath."""
    rows = []
    for s in items:
        state = "✅" if s.get("enabled") else "⏸"
        title = s.get("title", "")[:24]
        rows.append([InlineKeyboardButton(
            f"{state} {title} · {_fmt_next(s.get('next_run'))}",
            callback_data=f"sched_toggle_{s['id']}",
        )])
        rows.append([
            InlineKeyboardButton("▶️ Run now", callback_data=f"sched_run_{s['id']}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"sched_del_{s['id']}"),
        ])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def _schedule_panel_text(items: list) -> str:
    if not items:
        return ("⏰ Schedules\n═══════════\n\n"
                "No schedules yet.\n\n"
                "Add one with:\n"
                "  /schedule daily 09:00 <task>\n"
                "  /schedule every 5h <task>\n"
                "  /schedule once 2026-06-10 18:30 <task>\n"
                "  /schedule mon,wed,fri 09:00 <task>\n\n"
                "Or just say it: “every day at 9am summarise my inbox”.")
    lines = [f"⏰ Schedules ({len(items)})\n═══════════"]
    for s in items:
        state = "✅" if s.get("enabled") else "⏸"
        lines.append(f"{state} {s.get('title','')[:40]}")
        lines.append(f"    {describe_schedule(s['kind'], s['spec'])} · next {_fmt_next(s.get('next_run'))}")
    lines.append("\nTap a row to pause/resume.")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#  RESPONSE PIPELINE
# ══════════════════════════════════════════════════════════

async def send_response(update, context, response: str, user_id: int, chat_id: int):
    formatted_text, parse_mode = format_for_telegram(response)
    chunks = split_message(formatted_text)
    for chunk in chunks:
        await safe_send(context.bot, chat_id, chunk, parse_mode=parse_mode)

    # Model rate-limited? Tell the user which model is blocked and let them
    # switch right here (only if they're allowed to change the model).
    reason = detect_limit(response)
    if reason and _can_change_model(user_id):
        current = get_model()
        await safe_send(
            context.bot, chat_id,
            f"⚠️ <b>{current}</b> looks blocked ({reason}).\n"
            f"Pick another model below, then resend your message:",
            parse_mode="HTML",
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id, text="🤖 Switch model:",
                reply_markup=kb_model(current),
            )
        except Exception:
            pass

    # Auto-deliver files mentioned in response (admin+ only).
    # A Telegram user can't open a server-side path, so we attach the actual
    # files. Cap is generous (was 3 — which silently dropped the rest of a
    # multi-file result); anything beyond the cap stays in 📤 Outbox.
    if auth.can(user_id, "admin"):
        file_paths = _fresh_files(detect_file_paths(response))
        conv_id = sessions.get_conversation_id(user_id=user_id)
        files_sent = 0
        for fp in file_paths[:MAX_AUTO_DELIVER]:
            if await safe_send_file(context.bot, chat_id, fp, conv_id=conv_id, user_id=user_id):
                files_sent += 1
        if files_sent:
            extra = len(file_paths) - files_sent
            note = f"📎 {files_sent} file(s) delivered."
            if extra > 0:
                note += f" {extra} more in 📤 Outbox (/menu → Outbox)."
            await safe_send(context.bot, chat_id, note)


# ══════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    active = sessions.get_active_name(uid)
    conv_id = sessions.get_conversation_id(user_id=uid)
    session_count = len(sessions.list_sessions(uid))
    model = get_model()
    inbox = get_inbox_stats()
    role = "owner" if auth.is_owner(uid) else auth._users.get(uid, {}).get("role", "user")

    await update.message.reply_text(
        f"⚡ Zilla v{BOT_VERSION}\n"
        "══════════════════════\n\n"
        f"📌 Session: [{active}] {'(active)' if conv_id else '(new)'}\n"
        f"📊 Sessions: {session_count}\n"
        f"🤖 Model: {model}\n"
        f"📥 Inbox: {inbox['images']}img {inbox['audio']}aud {inbox['documents']}doc\n"
        f"🎤 Audio: {'Ready' if is_audio_capable() else 'N/A'}\n"
        f"👤 Role: {role}\n\n"
        "Type anything — it goes straight to the CLI.\n"
        "/menu for control panel • /help for commands."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lines = [
        "⚡ Zilla Commands\n══════════════════\n",
        "💬 CHAT:",
        "  Just type — goes straight to CLI",
        "  /cancel — stop a running request\n",
        "📎 MEDIA:",
        "  Voice → transcribe → respond",
        "  Photo/Doc + caption → analyze",
        "  Photo/Doc alone → save to Inbox\n",
        "📁 SESSIONS:",
        "  /new <name> — new session",
        "  /sessions — list all",
        "  /switch <name> — switch",
        "  /end — end current\n",
        "🧠 INBOX:",
        "  /brain — inbox stats\n",
    ]
    if auth.can(uid, "admin"):
        lines += [
            "⚙️ ADMIN:",
            "  /model — select AI model (owner may disable)",
            "  /settings — bot settings",
            "  /browse <url> — browser control\n",
        ]
    if auth.is_owner(uid):
        lines += [
            "👥 OWNER:",
            "  /adduser <id> [name] — add an admin",
            "  /removeuser <id> — remove an admin",
            "  /listusers — manage admins\n",
        ]
    await update.message.reply_text("\n".join(lines))


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    active = sessions.get_active_name(uid)
    conv_id = sessions.get_conversation_id(user_id=uid)
    await update.message.reply_text(
        f"🏓 Pong!\n"
        f"Session: [{active}] | Conv: {'active' if conv_id else 'new'}\n"
        f"Model: {get_model()}\n"
        f"Uptime: {get_uptime_str()}\n"
        f"Audio: {get_audio_status()}\n"
        f"Version: v{BOT_VERSION}"
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await _open_menu(
        update, context,
        "⚡ Zilla — Control Panel\n════════════════════════",
        kb_menu(uid),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.user_data.pop("awaiting_custom_model", None):
        await update.message.reply_text("Custom model entry cancelled.")
        return
    cancel_ev = _active_cancel.get(chat_id)
    if cancel_ev and not cancel_ev.is_set():
        cancel_ev.set()
        await update.message.reply_text("🛑 Canceling…")
    else:
        await update.message.reply_text("Nothing is running right now.")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if context.args:
        name = "-".join(context.args).lower().strip()
        name = "".join(c for c in name if c.isalnum() or c in "-_")
    else:
        existing = sessions.list_sessions(uid)
        i = 1
        while True:
            name = f"session-{i}"
            if name not in existing:
                break
            i += 1

    created = sessions.create_session(name, uid)
    if created:
        await update.message.reply_text(
            f"📁 Session [{name}] created. Next message starts fresh."
        )
    else:
        existing = sessions.list_sessions(uid)
        if name in existing:
            sessions.set_active_name(name, uid)
            await update.message.reply_text(f"📁 Session [{name}] already exists — switched to it.")
        else:
            await update.message.reply_text(f"📁 Could not create [{name}].")


async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    all_sessions = sessions.list_sessions(uid)
    active = sessions.get_active_name(uid)
    if not all_sessions:
        await update.message.reply_text("No sessions yet. Send a message to start!")
        return
    lines = [f"📁 Sessions ({len(all_sessions)})\n"]
    for name, info in all_sessions.items():
        marker = " ◀" if name == active else ""
        msgs = info.get("messages", 0)
        title = info.get("title", "")
        title_str = f' — "{title}"' if title else ""
        lines.append(f"  {name}{marker}{title_str} — {msgs} msgs")
    await _open_menu(update, context, "\n".join(lines), kb_sessions(all_sessions, active))


async def cmd_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /switch <name>")
        return
    name = "-".join(context.args).lower().strip()
    if name not in sessions.list_sessions(uid):
        await update.message.reply_text(f"Session [{name}] not found.")
        return
    sessions.set_active_name(name, uid)
    await update.message.reply_text(f"Switched to [{name}].")


async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = sessions.get_active_name(uid)
    sessions.delete_session(name, uid)
    await update.message.reply_text(
        f"Session [{name}] ended. Active: [{sessions.get_active_name(uid)}]."
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _can_change_model(uid):
        if auth.can(uid, "admin"):
            await update.message.reply_text("Model changes are disabled by the owner.")
        else:
            await update.message.reply_text("Model selection requires admin access.")
        return
    current = get_model()
    await _open_menu(
        update, context,
        f"🤖 Model Selection\n════════════════\n{await _backend_panel()}\n\n{_model_note()}",
        kb_model(current),
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth.can(uid, "admin"):
        await update.message.reply_text("Settings require admin access.")
        return
    await _open_menu(update, context, "⚙️ Settings\n═══════════", kb_settings(uid))


async def cmd_brain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_inbox_stats()
    await update.message.reply_text(
        "🧠 Inbox\n════════\n\n"
        f"📥 {stats['images']} images | {stats['audio']} audio | {stats['documents']} docs\n"
        f"🎤 Audio engine: {get_audio_status()}"
    )


# ══════════════════════════════════════════════════════════
#  SCHEDULES
# ══════════════════════════════════════════════════════════

def _kb_confirm_schedule():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Create", callback_data="sched_confirm"),
         InlineKeyboardButton("❌ Cancel", callback_data="sched_cancel")],
    ])


async def _offer_schedule(update, context, parsed: dict):
    """Stash a parsed schedule and ask the user to confirm creating it."""
    context.user_data["pending_schedule"] = parsed
    await update.effective_message.reply_text(
        "📅 Create this schedule?\n"
        f"  • {describe_schedule(parsed['kind'], parsed['spec'])}\n"
        f"  • Task: {parsed['title']}",
        reply_markup=_kb_confirm_schedule(),
    )


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth.can(uid, "admin"):
        await update.message.reply_text("Schedules require admin access.")
        return
    if context.args:
        parsed = parse_schedule_command(" ".join(context.args))
        if parsed:
            await _offer_schedule(update, context, parsed)
        else:
            await update.message.reply_text(
                "Couldn't read that schedule. Examples:\n"
                "  /schedule daily 09:00 summarise my inbox\n"
                "  /schedule every 5h check the news\n"
                "  /schedule once 2026-06-10 18:30 wish happy birthday\n"
                "  /schedule mon,wed,fri 09:00 stand-up notes"
            )
        return
    items = schedules_mgr.list(uid)
    await _open_menu(
        update, context,
        _schedule_panel_text(items),
        kb_schedules(items) if items else kb_back(),
    )


def _make_schedule(uid: int, chat_id: int, parsed: dict):
    return schedules_mgr.add(
        user_id=uid, chat_id=chat_id, prompt=parsed["prompt"],
        kind=parsed["kind"], spec=parsed["spec"], title=parsed["title"],
    )


# ── Scheduler runtime (custom asyncio loop; no APScheduler dependency) ──
#
#  Self-healing model (fixes the old silent-failure bug where touch_run advanced
#  the schedule even when the run errored, losing the job forever):
#    _execute_schedule  → runs the CLI, classifies ok/failure, NO delivery.
#    _deliver_*         → sends the result to Telegram.
#    _run_and_record    → loop path: deliver on success/give-up, mark outcome,
#                         RETRY a failed run a few times before advancing.
#    _run_now           → manual ▶️ trigger: run + deliver, never advance.

_SCHED_TICK = 20          # seconds between due-checks
_SCHED_RETRY_DELAY = 120  # wait this long before retrying a failed run
_SCHED_MAX_RETRIES = 3    # attempts per occurrence before giving up (and notifying)

# Response shapes that mean "the run did not really succeed".
_SCHED_FAIL_PREFIXES = ("Error:", "Claude error:", "⏱️", "⚠️ Stopped")

# A bare "take a screenshot" intent — short, no chaining. Such schedules must
# NOT go through the CLI agent: on the scheduler path the agent's browser tools
# aren't wired up, so it errors with "Kimi WebBridge not working" even though a
# one-off /browse screenshot works. We route these straight to the bridge.
_SCREENSHOT_RE = re.compile(r"\bscreen\s?shot\b", re.IGNORECASE)


def _is_simple_screenshot(prompt: str) -> bool:
    p = (prompt or "").strip()
    if not _SCREENSHOT_RE.search(p):
        return False
    return len(p.split()) <= 8 and not re.search(r"\b(then|after|and)\b", p, re.IGNORECASE)


async def _screenshot_via_bridge(application, s: dict) -> tuple[bool, str, str]:
    """Take a screenshot through KimiWebBridge directly (the path that actually
    works) and stash it in the Outbox, so delivery + the send allowlist pass.
    Returns the (ok, response, detail) triple _execute_schedule promises."""
    import shutil
    bot = application.bot
    try:
        await bot.send_chat_action(chat_id=s["chat_id"], action=ChatAction.TYPING)
    except Exception:
        pass
    try:
        result = await bridge_command("screenshot", {}, timeout=30)
    except Exception as e:
        return False, f"Error: screenshot bridge unreachable: {e}", str(e)
    src = (result.get("data") or {}).get("path", "")
    if not (src and os.path.isfile(src)):
        return False, f"Error: screenshot failed: {json.dumps(result)[:200]}", "no path"
    try:
        os.makedirs(OUTBOX_IMAGES, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = os.path.splitext(src)[1] or ".png"
        dest = os.path.join(OUTBOX_IMAGES, f"screenshot_{ts}{ext}")
        shutil.copyfile(src, dest)
    except Exception as e:
        return False, f"Error: could not save screenshot: {e}", str(e)
    return True, f"📸 Screenshot saved to {dest}", ""


async def _execute_schedule(application, s: dict) -> tuple[bool, str, str]:
    """Run one schedule's prompt. Returns (ok, response, detail). No delivery,
    no schedule mutation — pure execution + outcome classification."""
    uid = s["user_id"]
    chat_id = s["chat_id"]
    bot = application.bot

    # SECURITY: a schedule is a stored prompt that runs the agentic CLI with full
    # host privileges. If the owning user was de-authorized after creating it, the
    # schedule must NOT keep firing (otherwise removal isn't really revocation —
    # it's a persistent backdoor). Disable + skip any schedule whose owner is no
    # longer authorized.
    if not (auth and (auth.is_owner(uid) or auth.is_authorized(uid))):
        logger.warning(f"[SCHED] skip {s['id']}: user {uid} no longer authorized — disabling")
        try:
            schedules_mgr.set_enabled(s["id"], uid, False)
        except Exception:
            pass
        return False, "", "owner deauthorized"

    # Screenshot schedules bypass the CLI agent entirely (see _screenshot_via_bridge).
    if _is_simple_screenshot(s.get("prompt", "")):
        return await _screenshot_via_bridge(application, s)

    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass

    ok, detail, response = True, "", ""
    try:
        async with _get_user_lock(uid):
            conv_id = None
            sname = s.get("session_name")
            if sname:
                conv_id = _conv_for_run(uid, sname)
            response, detected = await run_cli_async(
                s["prompt"], conv_id, skip_permissions=auth.can(uid, "admin"),
            )
            if sname and detected and detected != conv_id:
                sessions.set_conversation_id(detected, user_id=uid, session_name=sname, backend=get_backend())
    except Exception as e:
        ok, detail, response = False, str(e), f"Error: {e}"
        logger.error(f"[SCHED] run {s['id']} failed: {e}", exc_info=True)

    # Response-level failure detection (empty / rate-limited / error text).
    if ok:
        if not (response and response.strip()):
            ok, detail = False, "empty response"
        elif detect_limit(response):
            ok, detail = False, f"model limited: {detect_limit(response)}"
        elif response.lstrip().startswith(_SCHED_FAIL_PREFIXES):
            ok, detail = False, response.strip()[:200]
    return ok, response, detail


async def _deliver_schedule_result(application, s: dict, response: str):
    """Send a schedule's output to its chat, with the rate-limit switch helper."""
    bot = application.bot
    chat_id = s["chat_id"]
    uid = s["user_id"]
    header = f"⏰ Scheduled — {s.get('title','')}\n"
    try:
        formatted, parse_mode = format_for_telegram(response or "(no output)")
        for chunk in split_message(header + formatted):
            await safe_send(bot, chat_id, chunk, parse_mode=parse_mode)
        reason = detect_limit(response or "")
        if reason and _can_change_model(uid):
            await safe_send(bot, chat_id,
                            f"⚠️ <b>{get_model()}</b> looks blocked ({reason}). Switch model:",
                            parse_mode="HTML")
            await bot.send_message(chat_id=chat_id, text="🤖 Switch model:",
                                   reply_markup=kb_model(get_model()))
        # Attach any files the job produced. Scheduled jobs never did this
        # before — a schedule that made a chart/sheet/screenshot delivered only
        # the text path, never the file.
        if auth and auth.can(uid, "admin"):
            paths = _fresh_files(detect_file_paths(response or ""))
            sent = 0
            for fp in paths[:MAX_AUTO_DELIVER]:
                if await safe_send_file(bot, chat_id, fp, user_id=uid):
                    sent += 1
            if sent:
                await safe_send(bot, chat_id, f"📎 {sent} file(s) delivered.")
    except Exception as e:
        logger.error(f"[SCHED] deliver {s['id']} failed: {e}")


async def _run_and_record(application, s: dict):
    """Loop path: run a due schedule, deliver, and record the outcome with retry.
    A failed run is retried (_SCHED_MAX_RETRIES × _SCHED_RETRY_DELAY) before the
    schedule advances — and the user is told if it ultimately couldn't complete."""
    sid = s["id"]
    title = s.get("title", "")
    ok, response, detail = await _execute_schedule(application, s)
    if ok:
        schedules_mgr.mark_success(sid)
        log_event("schedule_ok", id=sid, title=title[:40])
        await _deliver_schedule_result(application, s, response)
        return
    outcome, attempt = schedules_mgr.mark_failure(
        sid, _SCHED_RETRY_DELAY, _SCHED_MAX_RETRIES)
    log_event("schedule_failed", id=sid, title=title[:40],
              attempt=attempt, outcome=outcome, detail=(detail or "")[:200])
    if outcome == "gaveup":
        # Never silent: tell the owner what happened + hand over any partial output.
        try:
            await safe_send(
                application.bot, s["chat_id"],
                f"⚠️ Scheduled job couldn't complete: <b>{title}</b>\n"
                f"Tried {attempt}× over a few minutes. I'll run it again at its next "
                f"scheduled time.\nLast issue: {(detail or 'unknown')[:200]}",
                parse_mode="HTML",
            )
        except Exception:
            pass
        if response and response.strip():
            await _deliver_schedule_result(application, s, response)
    # 'retry' / 'gone' → stay quiet; it will run again on its own.


async def _run_now(application, s: dict):
    """Manual ▶️ Run now: execute + deliver, WITHOUT advancing the schedule."""
    ok, response, detail = await _execute_schedule(application, s)
    await _deliver_schedule_result(
        application, s,
        response if (response and response.strip()) else (detail or "(no output)"))


async def scheduler_loop(application):
    """Background loop: catch up missed jobs at boot, then run due jobs.
    Due jobs run concurrently (one slow job no longer blocks the others); the
    per-user lock still serializes a single user's runs."""
    import time as _t
    try:
        schedules_mgr.reconcile_startup(
            now=_t.time(), catchup=get_setting("schedule_catchup", True))
    except Exception as e:
        logger.error(f"[SCHED] reconcile failed: {e}")
    logger.info("[SCHED] scheduler loop started")
    while True:
        try:
            due = schedules_mgr.due()
            if due:
                for s in due:
                    logger.info(f"[SCHED] running {s['id']} ({s.get('title','')[:30]})")
                await asyncio.gather(
                    *[_run_and_record(application, s) for s in due],
                    return_exceptions=True,
                )
        except Exception as e:
            logger.error(f"[SCHED] loop error: {e}", exc_info=True)
        await asyncio.sleep(_SCHED_TICK)


async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth.is_owner(uid):
        return
    if not context.args:
        await update.message.reply_text("Usage: /adduser <telegram_id> [name]")
        return
    try:
        new_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    name = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    if auth.add_user(new_id, name):
        await update.message.reply_text(f"✅ User {new_id} ({name or 'unnamed'}) added as user.")
    else:
        await update.message.reply_text(f"User {new_id} already exists.")


async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth.is_owner(uid):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeuser <telegram_id>")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    if auth.remove_user(target_id):
        await update.message.reply_text(f"🗑️ User {target_id} removed.")
    else:
        await update.message.reply_text(f"User {target_id} not found.")


async def cmd_listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth.is_owner(uid):
        return
    users = auth.list_users()
    if not users:
        await update.message.reply_text(
            "👥 No users yet.\nUse the Users panel in /menu to add one.",
        )
        return
    lines = [f"👥 Users ({len(users)})\n"]
    for u_id, info in users.items():
        name = info.get("name") or f"User {u_id}"
        role = info.get("role", "user")
        added = info.get("added_at", "")[:10]
        lines.append(f"  [{role}] {name} — {u_id} ({added})")
    await update.message.reply_text("\n".join(lines), reply_markup=kb_users(users))


# ══════════════════════════════════════════════════════════
#  BROWSE COMMAND (admin+)
# ══════════════════════════════════════════════════════════

# ── WebBridge HTTP (runs OFF the event loop so it never freezes the bot) ──

def _bridge_status_blocking(timeout: int = 5) -> dict:
    import urllib.request
    with urllib.request.urlopen(
        urllib.request.Request(f"{KIMI_BRIDGE_URL}/status"), timeout=timeout
    ) as resp:
        return json.loads(resp.read())


def _bridge_command_blocking(action: str, args: dict | None, timeout: int) -> dict:
    import urllib.request
    payload = json.dumps(
        {"action": action, "args": args or {}, "session": "telegram"}).encode()
    req = urllib.request.Request(
        f"{KIMI_BRIDGE_URL}/command", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


async def bridge_status(timeout: int = 5) -> dict:
    return await asyncio.to_thread(_bridge_status_blocking, timeout)


async def bridge_command(action: str, args: dict | None = None, timeout: int = 15) -> dict:
    return await asyncio.to_thread(_bridge_command_blocking, action, args, timeout)


async def cmd_browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth.can(uid, "admin"):
        await update.message.reply_text("Browser control requires admin access.")
        return

    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text(
            "🌐 Zilla WebBridge\n══════════════════\n\n"
            "Usage:\n"
            "  /browse <url> — open URL\n"
            "  /browse status — health check\n"
            "  /browse screenshot — take screenshot\n"
            "  /browse tabs — list open tabs\n"
            "  /browse close — close all tabs"
        )
        return

    subcmd = context.args[0].lower()
    try:
        if subcmd == "status":
            try:
                data = await bridge_status()
                running = data.get("running", False)
                ext = data.get("extension_connected", False)
                await update.message.reply_text(
                    f"🌐 WebBridge\n"
                    f"Daemon: {'🟢 Running' if running else '🔴 Stopped'}\n"
                    f"Extension: {'🟢 Connected' if ext else '🔴 Disconnected'}\n"
                    f"Version: {data.get('version', 'unknown')}"
                )
            except Exception as e:
                await update.message.reply_text(f"🔴 WebBridge unreachable.\n{str(e)[:200]}")

        elif subcmd == "screenshot":
            stop_typing = asyncio.Event()
            typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))
            try:
                result = await bridge_command("screenshot", {}, timeout=30)
                stop_typing.set()
                typing_task.cancel()
                screenshot_path = result.get("data", {}).get("path", "")
                if screenshot_path and os.path.isfile(screenshot_path):
                    await safe_send_file(context.bot, chat_id, screenshot_path,
                                        caption="📸 Screenshot", user_id=uid)
                else:
                    await update.message.reply_text(f"Screenshot: {json.dumps(result)[:300]}")
            except Exception as e:
                stop_typing.set()
                typing_task.cancel()
                await update.message.reply_text(f"Screenshot error: {str(e)[:300]}")

        elif subcmd == "tabs":
            result = await bridge_command("list_tabs", {}, timeout=10)
            tabs = result.get("data", {}).get("tabs", [])
            if tabs:
                lines = [f"🌐 Open Tabs ({len(tabs)}):\n"]
                for t in tabs[:10]:
                    active = " ◀" if t.get("active") else ""
                    lines.append(f"  {t.get('title', 'Untitled')[:40]}{active}")
                    lines.append(f"    {t.get('url', '')[:60]}")
                await update.message.reply_text("\n".join(lines))
            else:
                await update.message.reply_text("No open tabs.")

        elif subcmd == "close":
            result = await bridge_command("close_session", {}, timeout=10)
            closed = result.get("data", {}).get("closed", 0)
            await update.message.reply_text(f"🗑️ Closed {closed} tab(s).")

        else:
            url = subcmd if subcmd.startswith(("http://", "https://")) else f"https://{subcmd}"
            if len(context.args) > 1:
                url = " ".join(context.args)
                if not url.startswith(("http://", "https://")):
                    url = f"https://{url}"
            # SECURITY: only allow real web schemes. Block file://, javascript:,
            # data:, chrome:// etc. that could read local files or run script in
            # the browser session the bot drives.
            _low = url.strip().lower()
            if not (_low.startswith("http://") or _low.startswith("https://")):
                await update.message.reply_text("Only http:// and https:// URLs are allowed.")
                return
            result = await bridge_command(
                "navigate", {"url": url, "newTab": True}, timeout=15)
            if result.get("data", {}).get("success"):
                await update.message.reply_text(f"🌐 Opened: {url}")
            else:
                await update.message.reply_text(f"Failed: {url}\n{json.dumps(result)[:200]}")

    except Exception as e:
        await update.message.reply_text(f"WebBridge error: {str(e)[:300]}")


# ══════════════════════════════════════════════════════════
#  MEDIA HANDLERS
# ══════════════════════════════════════════════════════════

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    stop_typing = asyncio.Event()
    cancel_event = threading.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        filepath = await save_voice(context.bot, update.message.voice)

        if is_audio_capable():
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(None, transcribe_audio, filepath)
        else:
            transcript = None

        if transcript and not transcript.startswith("["):
            await update.message.reply_text(f'🎤 "{transcript}"')
            async with await _acquire_turn(uid, update):
                _active_cancel[chat_id] = cancel_event
                sname = sessions.get_active_name(uid)
                conv_id = _conv_for_run(uid, sname)
                info = sessions.get_session_info(user_id=uid, session_name=sname)
                if info and info.get("messages", 0) == 0:
                    sessions.auto_title(transcript, user_id=uid, session_name=sname)
                response, detected_id = await run_cli_async(
                    transcript, conv_id,
                    cancel_event=cancel_event,
                    skip_permissions=auth.can(uid, "admin"),
                )
                if detected_id and detected_id != conv_id:
                    sessions.set_conversation_id(detected_id, user_id=uid, session_name=sname, backend=get_backend())
                final_conv = detected_id or conv_id
                if final_conv:
                    sessions.set_last_seen_step(get_latest_step(final_conv), user_id=uid, session_name=sname)
                sessions.increment_messages(user_id=uid, session_name=sname)
            stop_typing.set()
            typing_task.cancel()
            await send_response(update, context, response, uid, chat_id)
        else:
            stop_typing.set()
            typing_task.cancel()
            msg = transcript if transcript else "Transcription unavailable."
            await update.message.reply_text(f"🎤 Voice saved.\n{msg}")
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        logger.error(f"Voice error: {e}", exc_info=True)
        await update.message.reply_text(f"Voice error: {str(e)[:200]}")
    finally:
        if _active_cancel.get(chat_id) is cancel_event:
            _active_cancel.pop(chat_id, None)


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))
    try:
        filepath = await save_audio(context.bot, update.message.audio)
        if is_audio_capable():
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(None, transcribe_audio, filepath)
        else:
            transcript = None
        stop_typing.set()
        typing_task.cancel()
        if transcript and not transcript.startswith("["):
            await update.message.reply_text(f'🎵 Transcribed:\n"{transcript}"')
        else:
            await update.message.reply_text(f"🎵 Audio saved: {os.path.basename(filepath)}")
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        await update.message.reply_text(f"Audio error: {str(e)[:200]}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    caption = update.message.caption or ""
    stop_typing = asyncio.Event()
    cancel_event = threading.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        filepath = await save_photo(context.bot, update.message.photo)
        auto_describe = get_setting("auto_describe_photos", False)

        if caption:
            prompt = f"Image at {filepath}. User asks: {caption}"
        elif auto_describe:
            prompt = f"Image at {filepath}. Describe what you see."
        else:
            stop_typing.set()
            typing_task.cancel()
            await update.message.reply_text(
                f"📸 Photo saved.\n{os.path.basename(filepath)} "
                f"({format_file_size(os.path.getsize(filepath))})\n"
                "Add a caption to get analysis."
            )
            return

        async with await _acquire_turn(uid, update):
            _active_cancel[chat_id] = cancel_event
            sname = sessions.get_active_name(uid)
            conv_id = _conv_for_run(uid, sname)
            response, detected_id = await run_cli_async(
                prompt, conv_id,
                cancel_event=cancel_event,
                skip_permissions=auth.can(uid, "admin"),
            )
            if detected_id and detected_id != conv_id:
                sessions.set_conversation_id(detected_id, user_id=uid, session_name=sname, backend=get_backend())
            final_conv = detected_id or conv_id
            if final_conv:
                sessions.set_last_seen_step(get_latest_step(final_conv), user_id=uid, session_name=sname)
            sessions.increment_messages(user_id=uid, session_name=sname)
        stop_typing.set()
        typing_task.cancel()
        await send_response(update, context, response, uid, chat_id)
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        logger.error(f"Photo error: {e}", exc_info=True)
        await update.message.reply_text(f"Photo error: {str(e)[:200]}")
    finally:
        if _active_cancel.get(chat_id) is cancel_event:
            _active_cancel.pop(chat_id, None)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save document. If caption provided, analyze it via CLI."""
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    caption = update.message.caption or ""

    try:
        filepath = await save_document(context.bot, update.message.document)
        fname = update.message.document.file_name or os.path.basename(filepath)
        fsize = format_file_size(os.path.getsize(filepath))

        if not caption:
            await update.message.reply_text(
                f"📄 Saved: {fname} ({fsize})\n"
                "Add a caption with your question to analyze it."
            )
            return

        # Caption present — extract text and send to CLI
        stop_typing = asyncio.Event()
        cancel_event = threading.Event()
        typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

        try:
            extracted = extract_text(filepath)
            if extracted:
                prompt = (
                    f"User uploaded document: {fname}\n"
                    f"Their request: {caption}\n\n"
                    f"--- Document Content ---\n{extracted}"
                )
            else:
                # Fallback: tell CLI where the file is
                prompt = (
                    f"User uploaded document at {filepath} ({fname}).\n"
                    f"Their request: {caption}\n"
                    "Read the file and respond."
                )

            async with await _acquire_turn(uid, update):
                _active_cancel[chat_id] = cancel_event
                sname = sessions.get_active_name(uid)
                conv_id = _conv_for_run(uid, sname)
                response, detected_id = await run_cli_async(
                    prompt, conv_id,
                    cancel_event=cancel_event,
                    skip_permissions=auth.can(uid, "admin"),
                )
                if detected_id and detected_id != conv_id:
                    sessions.set_conversation_id(detected_id, user_id=uid, session_name=sname, backend=get_backend())
                final_conv = detected_id or conv_id
                if final_conv:
                    sessions.set_last_seen_step(get_latest_step(final_conv), user_id=uid, session_name=sname)
                sessions.increment_messages(user_id=uid, session_name=sname)
            stop_typing.set()
            typing_task.cancel()
            await send_response(update, context, response, uid, chat_id)
        except Exception as e:
            stop_typing.set()
            typing_task.cancel()
            logger.error(f"Document analysis error: {e}", exc_info=True)
            await update.message.reply_text(f"Analysis error: {str(e)[:200]}")
        finally:
            if _active_cancel.get(chat_id) is cancel_event:
                _active_cancel.pop(chat_id, None)

    except Exception as e:
        logger.error(f"Document save error: {e}", exc_info=True)
        await update.message.reply_text(f"Document error: {str(e)[:200]}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        filepath = await save_video(context.bot, update.message.video)
        await update.message.reply_text(
            f"🎬 Video saved. ({format_file_size(os.path.getsize(filepath))})"
        )
    except Exception as e:
        await update.message.reply_text(f"Video error: {str(e)[:200]}")


# ══════════════════════════════════════════════════════════
#  ADD-USER INLINE FLOW (owner only)
# ══════════════════════════════════════════════════════════

async def _handle_adduser_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, flow: dict):
    """Process multi-step add-user input from owner."""
    text = update.message.text.strip()
    step = flow.get("step")

    if text.lower() in ("/cancel", "cancel"):
        context.user_data.pop("adduser_flow", None)
        await update.message.reply_text("Add-user canceled.")
        return

    if step == "awaiting_id":
        try:
            target_id = int(text)
        except ValueError:
            await update.message.reply_text("That doesn't look like a Telegram ID. Send a number, or /cancel.")
            return
        flow["target_id"] = target_id
        flow["step"] = "awaiting_name"
        context.user_data["adduser_flow"] = flow
        await update.message.reply_text(
            f"ID: {target_id}\n\nNow send their name (or /skip to leave blank):"
        )

    elif step == "awaiting_name":
        name = "" if text.lower() == "/skip" else text
        target_id = flow.get("target_id")
        context.user_data.pop("adduser_flow", None)
        # Everyone added is an admin (full access, owner-trusted).
        if target_id and auth.add_user(target_id, name, "admin"):
            await update.message.reply_text(
                f"✅ Added {name or target_id} as [admin].\n"
                f"They have full access (chat, sessions, media, files). "
                f"Only you (owner) can manage users.",
                reply_markup=kb_users(auth.list_users()),
            )
        else:
            await update.message.reply_text(
                f"User {target_id} already exists or could not be added.",
                reply_markup=kb_users(auth.list_users()),
            )

    else:
        # Unexpected — reset
        context.user_data.pop("adduser_flow", None)
        await update.message.reply_text("Add-user flow reset. Use /menu > Users to start again.")


# ══════════════════════════════════════════════════════════
#  MAIN TEXT HANDLER
# ══════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    # Human-in-the-loop bridge: if the agent asked this chat for an OTP / phone /
    # password / confirmation, this message IS the answer — hand it back to the
    # waiting CLI turn instead of starting a new one.
    pending = _pending_bridge.get(chat_id)
    if pending:
        ask_id, ann_ts = pending
        if time.time() - ann_ts > _BRIDGE_PENDING_TTL:
            # Orphaned/stale ask — release the chat and process this message
            # normally instead of swallowing it as an answer.
            _pending_bridge.pop(chat_id, None)
            interactive.clear_ask(ask_id)
        else:
            text = update.message.text or ""
            try:
                interactive.write_answer(ask_id, text)
                _pending_bridge.pop(chat_id, None)
                await update.message.reply_text("✅ Got it — continuing.")
            except Exception as e:
                await update.message.reply_text(f"Couldn't record that: {str(e)[:120]}")
            return

    # Owner add-user flow intercept
    if auth.is_owner(uid) and "adduser_flow" in context.user_data:
        await _handle_adduser_flow(update, context, context.user_data["adduser_flow"])
        return

    # Custom-model capture intercept (admin only — the button is admin-gated)
    if context.user_data.get("awaiting_custom_model"):
        context.user_data.pop("awaiting_custom_model", None)
        chosen = (update.message.text or "").strip()
        if not chosen or chosen.startswith("/"):
            await update.message.reply_text("Custom model cancelled.")
            return
        stored = set_model(chosen)
        ok = stored == chosen
        head = "✅ Model changed" if ok else "⚠️ Stored, but readback differs"
        src = "Claude Code" if get_backend() == "claude" else "agy's settings.json"
        await update.message.reply_text(
            f"{head}\n{get_backend()} will now use: {stored}\n(via {src})"
        )
        return

    user_message = update.message.text

    # Natural-language schedule? Offer to create it instead of running it now.
    if auth.can(uid, "admin"):
        parsed = parse_schedule(user_message or "")
        if parsed:
            await _offer_schedule(update, context, parsed)
            return

    logger.info(f"Message in [{sessions.get_active_name(uid)}]")

    stop_typing = asyncio.Event()
    cancel_event = threading.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    # Bind before the try so a BaseException (e.g. asyncio.CancelledError on
    # shutdown, which `except Exception` does NOT catch) can't leave `response`
    # unbound at the send_response call below.
    response = ""
    try:
        async with await _acquire_turn(uid, update):
            # Pin the session to whatever is active the moment WE start running.
            # The user may /switch while this message was queued or running;
            # commands bypass this lock, so capture the target once and write
            # every result back to that same session — never the now-active one.
            _active_cancel[chat_id] = cancel_event
            sname = sessions.get_active_name(uid)
            conv_id = _conv_for_run(uid, sname)

            info = sessions.get_session_info(user_id=uid, session_name=sname)
            if info and info.get("messages", 0) == 0:
                sessions.auto_title(user_message, user_id=uid, session_name=sname)

            response, detected_id = await run_cli_async(
                user_message, conv_id,
                cancel_event=cancel_event,
                skip_permissions=auth.can(uid, "admin"),
            )

            if detected_id and detected_id != conv_id:
                sessions.set_conversation_id(detected_id, user_id=uid, session_name=sname, backend=get_backend())

            final_conv = detected_id or conv_id
            if final_conv:
                sessions.set_last_seen_step(get_latest_step(final_conv), user_id=uid, session_name=sname)
            sessions.increment_messages(user_id=uid, session_name=sname)

    except Exception as e:
        response = f"Error: {str(e)}"
        logger.error(f"Handler error: {e}", exc_info=True)
    finally:
        stop_typing.set()
        typing_task.cancel()
        if _active_cancel.get(chat_id) is cancel_event:
            _active_cancel.pop(chat_id, None)

    await send_response(update, context, response, uid, chat_id)


# ══════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    try:
        await query.answer()

        # ── Close: collapse this menu so it can't be re-tapped later ──
        if data == "menu_close":
            _active_menu.pop(chat_id, None)
            try:
                await query.edit_message_text("✓ Closed. Send /menu to reopen.")
            except Exception:
                await query.edit_message_reply_markup(reply_markup=None)
            return

        # ── Menu ──
        if data == "menu_back":
            _active_menu[chat_id] = query.message.message_id
            await query.edit_message_text(
                "⚡ Zilla — Control Panel\n════════════════════════",
                reply_markup=kb_menu(uid),
            )

        elif data == "menu_sessions":
            all_sessions = sessions.list_sessions(uid)
            active = sessions.get_active_name(uid)
            lines = [f"📁 Sessions ({len(all_sessions)})\n"]
            for name, info in all_sessions.items():
                marker = " ◀" if name == active else ""
                lines.append(f"  {name}{marker} — {info.get('messages', 0)} msgs")
            await query.edit_message_text(
                "\n".join(lines), reply_markup=kb_sessions(all_sessions, active),
            )

        elif data == "menu_model":
            if not _can_change_model(uid):
                await query.answer("Model changes are disabled by the owner.", show_alert=True)
                return
            current = get_model()
            await query.edit_message_text(
                f"🤖 Model Selection\n════════════════\n{await _backend_panel()}\n\n{_model_note()}",
                reply_markup=kb_model(current),
            )

        elif data == "menu_settings":
            if not auth.can(uid, "admin"):
                await query.answer("Admin access required.", show_alert=True)
                return
            await query.edit_message_text("⚙️ Settings\n═══════════", reply_markup=kb_settings(uid))

        elif data == "menu_inbox":
            counts = get_inbox_counts()
            total = sum(counts.values())
            if not total:
                await query.edit_message_text("📥 Inbox empty.", reply_markup=kb_back())
            else:
                await query.edit_message_text(
                    f"📥 Inbox — {total} item(s)\nPick a category:",
                    reply_markup=kb_inbox_categories(counts),
                )

        elif data.startswith("ibx_cat_"):
            # ibx_cat_{category}_{offset}
            rest = data.removeprefix("ibx_cat_")
            cat, _, off_str = rest.rpartition("_")
            offset = int(off_str) if off_str.isdigit() else 0
            items = get_inbox_items(cat)
            if not items:
                await query.edit_message_text(
                    "📥 Nothing here anymore.",
                    reply_markup=kb_inbox_categories(get_inbox_counts()),
                )
            else:
                label = dict(INBOX_CAT_META).get(cat, cat)
                shown = min(offset + INBOX_PAGE, len(items))
                await query.edit_message_text(
                    f"{label} — {len(items)} file(s)\nShowing {offset + 1}–{shown}. "
                    f"Tap a file or 📤 to send it here.",
                    reply_markup=kb_inbox_list(cat, items, offset),
                )

        elif data.startswith("ibx_send_"):
            # ibx_send_{category}_{index}
            rest = data.removeprefix("ibx_send_")
            cat, _, idx_str = rest.rpartition("_")
            idx = int(idx_str) if idx_str.isdigit() else -1
            items = get_inbox_items(cat)
            if 0 <= idx < len(items):
                item = items[idx]
                ok = await safe_send_file(
                    context.bot, chat_id, item["path"],
                    caption=item["name"], user_id=uid,
                )
                await query.answer("📤 Sent" if ok else "⚠️ Could not send", show_alert=not ok)
            else:
                await query.answer("File no longer available.", show_alert=True)

        elif data.startswith("ibx_del_"):
            # ibx_del_{category}_{index}
            rest = data.removeprefix("ibx_del_")
            cat, _, idx_str = rest.rpartition("_")
            idx = int(idx_str) if idx_str.isdigit() else -1
            items = get_inbox_items(cat)
            if 0 <= idx < len(items):
                gone = delete_inbox_file(items[idx]["path"])
                await query.answer("🗑 Deleted" if gone else "Couldn't delete.", show_alert=not gone)
            else:
                await query.answer("File no longer available.", show_alert=True)
            # Refresh the list in place (re-read, clamp offset to a valid page).
            items = get_inbox_items(cat)
            if not items:
                await query.edit_message_text(
                    "📥 Empty now.", reply_markup=kb_inbox_categories(get_inbox_counts()))
            else:
                off = (idx // INBOX_PAGE) * INBOX_PAGE
                if off >= len(items):
                    off = max(0, off - INBOX_PAGE)
                label = dict(INBOX_CAT_META).get(cat, cat)
                shown = min(off + INBOX_PAGE, len(items))
                await query.edit_message_text(
                    f"{label} — {len(items)} file(s)\nShowing {off + 1}–{shown}.",
                    reply_markup=kb_inbox_list(cat, items, off),
                )

        elif data == "menu_outbox":
            counts = get_outbox_counts()
            total = sum(counts.values())
            if not total:
                await query.edit_message_text(
                    "📤 Outbox empty.\nFiles you ask me to create land here.",
                    reply_markup=kb_back())
            else:
                await query.edit_message_text(
                    f"📤 Outbox — {total} item(s)\nPick a category:",
                    reply_markup=kb_outbox_categories(counts),
                )

        elif data.startswith("obx_cat_"):
            # obx_cat_{category}_{offset}
            rest = data.removeprefix("obx_cat_")
            cat, _, off_str = rest.rpartition("_")
            offset = int(off_str) if off_str.isdigit() else 0
            items = get_outbox_items(cat)
            if not items:
                await query.edit_message_text(
                    "📤 Nothing here anymore.",
                    reply_markup=kb_outbox_categories(get_outbox_counts()),
                )
            else:
                label = dict(OUTBOX_CAT_META).get(cat, cat)
                shown = min(offset + INBOX_PAGE, len(items))
                await query.edit_message_text(
                    f"{label} — {len(items)} file(s)\nShowing {offset + 1}–{shown}. "
                    f"Tap a file or 📤 to send it here.",
                    reply_markup=kb_outbox_list(cat, items, offset),
                )

        elif data.startswith("obx_send_"):
            # obx_send_{category}_{index}
            rest = data.removeprefix("obx_send_")
            cat, _, idx_str = rest.rpartition("_")
            idx = int(idx_str) if idx_str.isdigit() else -1
            items = get_outbox_items(cat)
            if 0 <= idx < len(items):
                item = items[idx]
                ok = await safe_send_file(
                    context.bot, chat_id, item["path"],
                    caption=item["name"], user_id=uid,
                )
                await query.answer("📤 Sent" if ok else "⚠️ Could not send", show_alert=not ok)
            else:
                await query.answer("File no longer available.", show_alert=True)

        elif data.startswith("obx_del_"):
            # obx_del_{category}_{index}
            rest = data.removeprefix("obx_del_")
            cat, _, idx_str = rest.rpartition("_")
            idx = int(idx_str) if idx_str.isdigit() else -1
            items = get_outbox_items(cat)
            if 0 <= idx < len(items):
                gone = delete_outbox_file(items[idx]["path"])
                await query.answer("🗑 Deleted" if gone else "Couldn't delete.", show_alert=not gone)
            else:
                await query.answer("File no longer available.", show_alert=True)
            # Refresh the list in place (re-read, clamp offset to a valid page).
            items = get_outbox_items(cat)
            if not items:
                await query.edit_message_text(
                    "📤 Empty now.", reply_markup=kb_outbox_categories(get_outbox_counts()))
            else:
                off = (idx // INBOX_PAGE) * INBOX_PAGE
                if off >= len(items):
                    off = max(0, off - INBOX_PAGE)
                label = dict(OUTBOX_CAT_META).get(cat, cat)
                shown = min(off + INBOX_PAGE, len(items))
                await query.edit_message_text(
                    f"{label} — {len(items)} file(s)\nShowing {off + 1}–{shown}.",
                    reply_markup=kb_outbox_list(cat, items, off),
                )

        elif data == "menu_browse":
            if not auth.can(uid, "admin"):
                await query.answer("Admin access required.", show_alert=True)
                return
            try:
                bdata = await bridge_status()
                running = bdata.get("running", False)
                ext = bdata.get("extension_connected", False)
                status_text = (
                    f"🌐 WebBridge\n══════════\n\n"
                    f"Daemon: {'🟢 Running' if running else '🔴 Stopped'}\n"
                    f"Extension: {'🟢 Connected' if ext else '🔴 Disconnected'}\n\n"
                    "Use /browse <url> to open pages."
                )
            except Exception:
                status_text = "🌐 WebBridge\n\n🔴 Not reachable. Start the daemon first."
            await query.edit_message_text(status_text, reply_markup=kb_back())

        elif data == "menu_status":
            session_count = len(sessions.list_sessions(uid))
            await query.edit_message_text(
                "🖥️ Status\n═════════\n\n"
                f"{await _backend_panel()}\n\n"
                f"⏱️ Uptime: {get_uptime_str()}\n"
                f"📁 Sessions: {session_count}\n"
                f"🔧 Version: v{BOT_VERSION}",
                reply_markup=kb_back(),
            )

        elif data == "menu_health":
            await query.edit_message_text(await _health_panel(), reply_markup=kb_back())

        elif data == "menu_schedules":
            if not auth.can(uid, "admin"):
                await query.answer("Admin access required.", show_alert=True)
                return
            items = schedules_mgr.list(uid)
            await query.edit_message_text(
                _schedule_panel_text(items),
                reply_markup=kb_schedules(items) if items else kb_back(),
            )

        # ── Sessions ──
        elif data == "sess_list":
            all_sessions = sessions.list_sessions(uid)
            active = sessions.get_active_name(uid)
            await query.edit_message_text(
                "📁 Sessions", reply_markup=kb_sessions(all_sessions, active),
            )

        elif data.startswith("sess_switch_"):
            name = data.removeprefix("sess_switch_")
            sessions.set_active_name(name, uid)
            active = sessions.get_active_name(uid)
            await query.edit_message_text(
                f"✅ Switched to [{name}].",
                reply_markup=kb_sessions(sessions.list_sessions(uid), active),
            )

        elif data.startswith("sess_delete_"):
            name = data.removeprefix("sess_delete_")
            await query.edit_message_text(
                f"🗑️ Delete session [{name}]?\n\nThis cannot be undone.",
                reply_markup=kb_session_delete(name),
            )

        elif data.startswith("sess_confirm_del_"):
            name = data.removeprefix("sess_confirm_del_")
            removed = sessions.delete_session(name, uid)
            active = sessions.get_active_name(uid)
            head = f"🗑️ [{name}] deleted." if removed else f"[{name}] not found."
            await query.edit_message_text(
                f"{head}\nActive: [{active}]",
                reply_markup=kb_sessions(sessions.list_sessions(uid), active),
            )

        elif data == "sess_new":
            existing = sessions.list_sessions(uid)
            i = 1
            while True:
                name = f"session-{i}"
                if name not in existing:
                    break
                i += 1
            sessions.create_session(name, uid)
            active = sessions.get_active_name(uid)
            await query.edit_message_text(
                f"📁 Session [{name}] created — next message starts fresh.",
                reply_markup=kb_sessions(sessions.list_sessions(uid), active),
            )

        # ── Model ──
        elif data == "model_switch_backend":
            if not auth.is_owner(uid):
                await query.answer("Only the owner can switch backend.", show_alert=True)
                return
            new_backend = "claude" if get_backend() == "agy" else "agy"
            set_backend(new_backend)
            current = get_model()
            await query.edit_message_text(
                f"🤖 Model Selection\n════════════════\nBackend: {new_backend}\n"
                f"Current: {current}\n\n{_model_note()}",
                reply_markup=kb_model(current),
            )

        elif data == "model_custom":
            if not _can_change_model(uid):
                await query.answer("Model changes are disabled by the owner.", show_alert=True)
                return
            context.user_data["awaiting_custom_model"] = True
            await query.edit_message_text(
                "✏️ Send the exact model string as it appears in agy's own "
                "\"Switch Model\" screen,\ne.g. <code>Gemini 3.1 Pro (High)</code>\n\n"
                "Send /cancel to abort.",
                parse_mode="HTML",
            )

        elif data.startswith("model_"):
            if not _can_change_model(uid):
                await query.answer("Model changes are disabled by the owner.", show_alert=True)
                return
            chosen = data.removeprefix("model_")
            # set_model persists for the active backend and returns the stored
            # value (agy: read back from its settings.json; claude: the alias).
            stored = set_model(chosen)
            ok = stored == chosen
            head = "✅ Model changed" if ok else "⚠️ Stored, but readback differs"
            src = "--model alias" if get_backend() == "claude" else "agy --model flag"
            await query.edit_message_text(
                f"{head}\n════════════════\n"
                f"<b>{get_backend()}</b> will now use: <b>{stored}</b>\n"
                f"(via {src})\n\nTakes effect on your next message.",
                parse_mode="HTML",
                reply_markup=kb_model(stored),
            )

        # ── Settings ──
        elif data == "set_toggle_photo":
            if not auth.can(uid, "admin"):
                await query.answer("Admin access required.", show_alert=True)
                return
            current = get_setting("auto_describe_photos", False)
            set_setting("auto_describe_photos", not current)
            await query.edit_message_text("⚙️ Settings\n═══════════", reply_markup=kb_settings(uid))

        elif data == "set_cycle_idle":
            if not auth.can(uid, "admin"):
                await query.answer("Admin access required.", show_alert=True)
                return
            current = get_idle_kill_after()
            vals = [v for v, _ in _IDLE_OPTIONS]
            try:
                idx = vals.index(current)
                new_val = vals[(idx + 1) % len(vals)]
            except ValueError:
                new_val = 600
            set_setting("idle_kill_after", new_val)
            await query.edit_message_text("⚙️ Settings\n═══════════", reply_markup=kb_settings(uid))

        elif data == "set_toggle_admin_model":
            if not auth.is_owner(uid):
                await query.answer("Owner only.", show_alert=True)
                return
            current = get_setting("admins_can_change_model", True)
            set_setting("admins_can_change_model", not current)
            await query.edit_message_text("⚙️ Settings\n═══════════", reply_markup=kb_settings(uid))

        elif data == "set_toggle_catchup":
            if not auth.can(uid, "admin"):
                await query.answer("Admin access required.", show_alert=True)
                return
            current = get_setting("schedule_catchup", True)
            set_setting("schedule_catchup", not current)
            await query.edit_message_text("⚙️ Settings\n═══════════", reply_markup=kb_settings(uid))

        elif data == "set_toggle_backend":
            if not auth.is_owner(uid):
                await query.answer("Owner only.", show_alert=True)
                return
            new_backend = "claude" if get_backend() == "agy" else "agy"
            set_backend(new_backend)
            await query.edit_message_text(
                f"🧠 Backend switched to: {new_backend}\n"
                f"Takes effect on your next message. Model: {get_model()}",
                reply_markup=kb_settings(uid),
            )

        # ── Schedules ──
        elif data == "sched_confirm":
            parsed = context.user_data.pop("pending_schedule", None)
            if not parsed or not auth.can(uid, "admin"):
                await query.edit_message_text("Nothing to create.")
            else:
                s = _make_schedule(uid, chat_id, parsed)
                if s:
                    await query.edit_message_text(
                        f"✅ Scheduled: {s['title']}\n"
                        f"{describe_schedule(s['kind'], s['spec'])} · next {_fmt_next(s['next_run'])}",
                    )
                else:
                    await query.edit_message_text("Couldn't create that schedule (time already past?).")

        elif data == "sched_cancel":
            context.user_data.pop("pending_schedule", None)
            await query.edit_message_text("Cancelled — no schedule created.")

        elif data == "sched_list":
            items = schedules_mgr.list(uid)
            await query.edit_message_text(
                _schedule_panel_text(items),
                reply_markup=kb_schedules(items) if items else kb_back(),
            )

        elif data.startswith("sched_toggle_"):
            sid = data.removeprefix("sched_toggle_")
            s = schedules_mgr.get(sid)
            if s and s.get("user_id") == uid:
                schedules_mgr.set_enabled(sid, uid, not s.get("enabled"))
            items = schedules_mgr.list(uid)
            await query.edit_message_text(
                _schedule_panel_text(items),
                reply_markup=kb_schedules(items) if items else kb_back(),
            )

        elif data.startswith("sched_del_"):
            sid = data.removeprefix("sched_del_")
            schedules_mgr.remove(sid, uid)
            items = schedules_mgr.list(uid)
            await query.answer("🗑 Deleted")
            await query.edit_message_text(
                _schedule_panel_text(items),
                reply_markup=kb_schedules(items) if items else kb_back(),
            )

        elif data.startswith("sched_run_"):
            sid = data.removeprefix("sched_run_")
            s = schedules_mgr.get(sid)
            if s and s.get("user_id") == uid:
                await query.answer("▶️ Running now…")
                asyncio.create_task(_run_now(context.application, s))
            else:
                await query.answer("Not found.", show_alert=True)

        # ── Cancel active request ──
        elif data == "cancel_active":
            cancel_ev = _active_cancel.get(chat_id)
            if cancel_ev and not cancel_ev.is_set():
                cancel_ev.set()
                await query.edit_message_text("🛑 Canceling…")
            else:
                await query.edit_message_text("Nothing to cancel.")

        # ── Users (owner only) ──
        elif data == "menu_users":
            if not auth.is_owner(uid):
                await query.answer("Owner only.", show_alert=True)
                return
            users = auth.list_users()
            await query.edit_message_text(
                f"👥 Users ({len(users)})\nTap to manage.",
                reply_markup=kb_users(users),
            )

        elif data == "user_list":
            if not auth.is_owner(uid):
                return
            users = auth.list_users()
            await query.edit_message_text(
                f"👥 Users ({len(users)})",
                reply_markup=kb_users(users),
            )

        elif data.startswith("user_detail_"):
            if not auth.is_owner(uid):
                return
            target_id = int(data.removeprefix("user_detail_"))
            users = auth.list_users()
            info = users.get(target_id, {})
            name = info.get("name") or f"User {target_id}"
            role = info.get("role", "user")
            added = info.get("added_at", "unknown")
            await query.edit_message_text(
                f"👤 {name}\n══════════════════\n"
                f"ID: {target_id}\n"
                f"Role: {role}\n"
                f"Added: {added}",
                reply_markup=kb_user_detail(target_id, role),
            )

        elif data.startswith("user_remove_"):
            if not auth.is_owner(uid):
                return
            target_id = int(data.removeprefix("user_remove_"))
            users = auth.list_users()
            info = users.get(target_id, {})
            name = info.get("name") or f"User {target_id}"
            await query.edit_message_text(
                f"🗑️ Remove {name} ({target_id}) and deny their access?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, remove", callback_data=f"user_confirm_remove_{target_id}"),
                     InlineKeyboardButton("❌ Cancel", callback_data=f"user_detail_{target_id}")],
                ]),
            )

        elif data.startswith("user_confirm_remove_"):
            if not auth.is_owner(uid):
                return
            target_id = int(data.removeprefix("user_confirm_remove_"))
            if auth.remove_user(target_id):
                users = auth.list_users()
                await query.edit_message_text(
                    f"🗑️ User {target_id} removed.",
                    reply_markup=kb_users(users),
                )
            else:
                await query.edit_message_text("User not found.")

        elif data == "user_add_start":
            if not auth.is_owner(uid):
                return
            context.user_data["adduser_flow"] = {"step": "awaiting_id"}
            await query.edit_message_text(
                "➕ Add User\n══════════\n\n"
                "Send the new user's Telegram ID (a number).\n"
                "Type /cancel to abort.",
                reply_markup=None,
            )

        # ── Error recovery ──
        elif data == "err_retry":
            await query.edit_message_text(
                "🔄 Send your message again.",
                reply_markup=kb_back(),
            )

        elif data == "err_model":
            if not _can_change_model(uid):
                await query.answer("Model changes are disabled by the owner.", show_alert=True)
                return
            current = get_model()
            await query.edit_message_text(
                f"🤖 Try a different model?\nCurrent: {current}",
                reply_markup=kb_model(current),
            )

        else:
            # Unknown — restore the menu rather than replacing with an error
            await query.edit_message_text(
                "⚡ Zilla — Control Panel\n════════════════════════",
                reply_markup=kb_menu(uid),
            )

    except Exception as e:
        logger.error(f"Callback error: {e}", exc_info=True)
        try:
            await query.answer(f"Error: {str(e)[:100]}")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Bot error: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_user:
        if not auth.is_authorized(update.effective_user.id):
            return
    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text("⚠ Something went wrong.", reply_markup=kb_error())
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════

async def bridge_watcher(application):
    """Relay the agent's credential/OTP/confirm requests to the owner's Telegram.

    Polls the Bridge dir; for each new ask it DMs the prompt and records that the
    chat owes an answer (captured by handle_message). Cleans up answered/stale
    asks. Inert when the agent isn't asking for anything.
    """
    _KIND_LABEL = {"otp": "🔐 One-time code", "password": "🔑 Password",
                   "text": "✍️ Input needed", "confirm": "❓ Please confirm"}
    logger.info("[BRIDGE] credential/OTP watcher started")
    while True:
        try:
            for ask in interactive.read_pending_asks():
                if ask.id in _bridge_announced:
                    continue
                target = ask.chat_id or OWNER_CHAT_ID
                if not target:
                    continue
                cur = _pending_bridge.get(target)
                if cur and cur[0] != ask.id:
                    continue  # one outstanding ask per chat at a time
                hint = "\n\n<i>Reply with the value — used once.</i>" if ask.is_secret else ""
                try:
                    await application.bot.send_message(
                        chat_id=target,
                        text=f"{_KIND_LABEL.get(ask.kind, '✍️ Input needed')}\n\n"
                             f"{format_for_telegram(ask.prompt)[0]}{hint}",
                        parse_mode="HTML",
                    )
                    _bridge_announced.add(ask.id)
                    _pending_bridge[target] = (ask.id, time.time())
                    log_event("bridge_ask", kind=ask.kind, chat=target)
                except Exception as e:
                    logger.error(f"[BRIDGE] could not DM ask {ask.id}: {e}")
            interactive.expire_stale()
            # Forget announced asks that are gone (answered+cleared) so the maps
            # don't grow unbounded.
            live = {a.id for a in interactive.read_pending_asks()}
            for aid in list(_bridge_announced):
                if aid not in live:
                    _bridge_announced.discard(aid)
                    for cid, pv in list(_pending_bridge.items()):
                        if pv[0] == aid:
                            _pending_bridge.pop(cid, None)
        except Exception as e:
            logger.error(f"[BRIDGE] watcher error: {e}", exc_info=True)
        await asyncio.sleep(2)


async def post_init(application):
    # Register the native Telegram slash-command menu (the "/" autocomplete).
    await _register_commands(application)

    # Start the background scheduler (custom asyncio loop).
    application.create_task(scheduler_loop(application))

    # Start the human-in-the-loop credential/OTP relay watcher.
    interactive.ensure_bridge_dir()
    application.create_task(bridge_watcher(application))

    if OWNER_CHAT_ID:
        try:
            await application.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=(
                    f"⚡ Zilla is online (v{BOT_VERSION})\n"
                    f"Model: {get_model()}\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                ),
            )
        except Exception as e:
            logger.warning(f"[STARTUP] Owner notify failed: {e}")


# Base commands every admin/owner sees in the "/" menu.
_BASE_COMMANDS = [
    ("menu", "Open the control panel"),
    ("schedule", "Add / manage scheduled jobs"),
    ("model", "Select the AI model"),
    ("settings", "Bot settings"),
    ("sessions", "List your sessions"),
    ("new", "Start a new session"),
    ("switch", "Switch session"),
    ("end", "End the current session"),
    ("brain", "Inbox stats"),
    ("browse", "Browser control (/browse <url>)"),
    ("ping", "Status check"),
    ("cancel", "Stop a running request"),
    ("help", "Show all commands"),
]
# Extra commands only the owner needs.
_OWNER_COMMANDS = [
    ("adduser", "Add an admin"),
    ("removeuser", "Remove an admin"),
    ("listusers", "Manage admins"),
]


async def _register_commands(application):
    """Push the slash-command list to Telegram (best effort; never blocks)."""
    try:
        base = [BotCommand(c, d) for c, d in _BASE_COMMANDS]
        await application.bot.set_my_commands(base, scope=BotCommandScopeDefault())
        if OWNER_CHAT_ID:
            owner_cmds = [BotCommand(c, d) for c, d in (_BASE_COMMANDS + _OWNER_COMMANDS)]
            await application.bot.set_my_commands(
                owner_cmds, scope=BotCommandScopeChat(chat_id=OWNER_CHAT_ID),
            )
        logger.info("[STARTUP] Slash-command menu registered")
    except Exception as e:
        logger.warning(f"[STARTUP] set_my_commands failed: {e}")


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

# Module-level lock file handle (for single-instance guard)
_lock_file_handle = None

_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(_BOT_DIR, "zilla.pid")


def _cout(msg: str = ""):
    """Print only when a real console exists (no-op under pythonw.exe)."""
    if sys.stdout is not None:
        try:
            print(msg)
        except Exception:
            pass


def _write_pid_file():
    """Write our PID so the Stop script can kill us (and our CLI children) reliably."""
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logger.warning(f"[PID] Could not write pid file: {e}")


def _remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zilla_bot_instance.lock")


def _release_lock():
    global _lock_file_handle
    _remove_pid_file()
    if _lock_file_handle:
        release_instance_lock(_lock_file_handle, _LOCK_PATH)
        _lock_file_handle = None


def main():
    global sessions, auth, schedules_mgr, _lock_file_handle

    # Single-instance guard — cross-platform (msvcrt on Windows, fcntl on Unix).
    bot_dir = os.path.dirname(os.path.abspath(__file__))
    for stale in ["agy_bot_instance.lock"]:  # clean old naming schemes
        sp = os.path.join(bot_dir, stale)
        if os.path.exists(sp):
            try:
                os.remove(sp)
            except OSError:
                pass
    _lock_file_handle = acquire_instance_lock(_LOCK_PATH)
    if _lock_file_handle is None:
        _cout("❌ Another instance is already running. Stop it first.")
        sys.exit(1)
    atexit.register(_release_lock)

    # Record our PID so 'Stop Zilla.bat' can terminate us + CLI children
    _write_pid_file()

    if sys.platform == "win32" and sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # SECURITY: refuse to start ownerless. With OWNER_CHAT_ID unset it defaults
    # to 0, is_owner(0) is True, and the bot becomes unmanageable (nobody can
    # add/remove users or revoke a compromised admin). Fail loud instead.
    if not OWNER_CHAT_ID:
        _cout("❌ TELEGRAM_OWNER_ID is not set. Refusing to start without an owner.")
        _cout("   Set it in .env (get your numeric id from @userinfobot), then restart.")
        logger.critical("[STARTUP] TELEGRAM_OWNER_ID unset — aborting.")
        sys.exit(1)

    ensure_dirs()
    # SECURITY: keep secrets/state owner-only (the bot token lives in .env; state
    # files carry conversation ids + auto-titled message snippets).
    _harden_file_perms()
    sessions = SessionManager(SESSIONS_FILE)
    auth = AuthManager(USERS_FILE, OWNER_CHAT_ID)
    schedules_mgr = ScheduleManager(SCHEDULES_FILE)

    model = get_model()
    session_count = len(sessions.list_sessions(OWNER_CHAT_ID))
    audio_ok = is_audio_capable()
    idle_kill = get_idle_kill_after()

    logger.info(f"Zilla Bot v{BOT_VERSION} starting (PID {os.getpid()})")
    _cout("=" * 52)
    _cout(f"  Zilla Bot v{BOT_VERSION}")
    _cout(f"  Thin pipe to CLI — it does the thinking.")
    _cout("=" * 52)
    _cout(f"  Owner: {OWNER_CHAT_ID}")
    _cout(f"  Users: {auth.count()} authorized")
    _cout(f"  Model: {model}")
    _cout(f"  Sessions: {session_count}")
    _cout(f"  Audio: {'Ready' if audio_ok else 'N/A'}")
    _cout(f"  Idle reaper: {idle_kill}s (0=disabled)")
    _cout(f"  PID: {os.getpid()} (double-click 'Stop Zilla.bat' to kill)")
    _cout("  Starting… Ctrl+C to stop.")
    _cout()

    # concurrent_updates: serve many users AT THE SAME TIME instead of one-by-one.
    # Without this, one user's long CLI task blocks everyone else (incl. the owner).
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )

    # Auth gate — runs before ALL handlers
    app.add_handler(TypeHandler(Update, auth_middleware), group=-1)

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("switch", cmd_switch))
    app.add_handler(CommandHandler("end", cmd_end))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("brain", cmd_brain))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("schedules", cmd_schedule))
    app.add_handler(CommandHandler("browse", cmd_browse))
    app.add_handler(CommandHandler("adduser", cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("listusers", cmd_listusers))

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Media
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))

    # Text (must be last)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(error_handler)

    app.run_polling(
        allowed_updates=["message", "callback_query", "edited_message"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
