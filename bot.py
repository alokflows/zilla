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

import winhide  # noqa: F401 — MUST be first: suppresses all child console windows
import asyncio
import atexit
import sys
import os
import logging
import threading
import time
import json
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
)
from sessions import SessionManager
from cli_engine import run_cli_async, get_latest_step
from media import (
    is_audio_capable, get_audio_status, transcribe_audio,
    save_photo, save_voice, save_audio, save_document, save_video,
    get_inbox_stats, get_inbox_items, format_file_size, extract_text,
)
from formatter import format_for_telegram, detect_file_paths
from users import AuthManager

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
logger = logging.getLogger(__name__)

BOT_START_TIME = time.time()

# ── Global State ───────────────────────────────────────────
sessions: SessionManager = None
auth: AuthManager = None

# Per-chat cancel events — set to cancel the active CLI request for that chat
_active_cancel: dict[int, threading.Event] = {}

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

def kb_menu(uid: int = 0):
    rows = [
        [InlineKeyboardButton("📁 Sessions", callback_data="menu_sessions"),
         InlineKeyboardButton("📥 Inbox", callback_data="menu_inbox")],
        [InlineKeyboardButton("🖥️ Status", callback_data="menu_status")],
    ]
    if auth and auth.can(uid, "admin"):
        rows[0].append(InlineKeyboardButton("🤖 Model", callback_data="menu_model"))
        rows.insert(1, [
            InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
            InlineKeyboardButton("🌐 Browse", callback_data="menu_browse"),
        ])
    if auth and auth.is_owner(uid):
        rows.append([InlineKeyboardButton("👥 Users", callback_data="menu_users")])
    return InlineKeyboardMarkup(rows)


def kb_sessions(all_sessions: dict, active: str):
    buttons = []
    for name, info in all_sessions.items():
        marker = " ◀" if name == active else ""
        msgs = info.get("messages", 0)
        buttons.append([InlineKeyboardButton(
            f"{name}{marker} ({msgs} msgs)",
            callback_data=f"sess_switch_{name}",
        )])
    buttons.append([
        InlineKeyboardButton("➕ New", callback_data="sess_new"),
        InlineKeyboardButton("◀ Menu", callback_data="menu_back"),
    ])
    return InlineKeyboardMarkup(buttons)


def kb_session_delete(name: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, delete", callback_data=f"sess_confirm_del_{name}"),
         InlineKeyboardButton("❌ Cancel", callback_data="sess_list")],
    ])


def kb_model(current: str):
    models = [
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
        ("Gemini 2.0 Flash", "gemini-2.0-flash"),
        ("Gemini 3.1 Pro", "gemini-3.1-pro"),
        ("Claude Sonnet 4", "claude-sonnet-4"),
        ("Claude Opus 4", "claude-opus-4"),
    ]
    buttons = []
    for display, model_id in models:
        marker = " ✓" if model_id == current else ""
        buttons.append([InlineKeyboardButton(
            f"{display}{marker}", callback_data=f"model_{model_id}",
        )])
    buttons.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back")])
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
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back")])
    return InlineKeyboardMarkup(rows)


def kb_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀ Menu", callback_data="menu_back")],
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
    ])
    return InlineKeyboardMarkup(buttons)


def kb_user_detail(target_id: int, role: str):
    toggle_label = "👑 Make Admin" if role == "user" else "👤 Make User"
    toggle_role = "admin" if role == "user" else "user"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data=f"user_set_role_{toggle_role}_{target_id}")],
        [InlineKeyboardButton("🗑️ Remove", callback_data=f"user_remove_{target_id}"),
         InlineKeyboardButton("◀ Back", callback_data="user_list")],
    ])


# ══════════════════════════════════════════════════════════
#  RESPONSE PIPELINE
# ══════════════════════════════════════════════════════════

async def send_response(update, context, response: str, user_id: int, chat_id: int):
    formatted_text, parse_mode = format_for_telegram(response)
    chunks = split_message(formatted_text)
    for chunk in chunks:
        await safe_send(context.bot, chat_id, chunk, parse_mode=parse_mode)

    # Auto-deliver files mentioned in response (admin+ only)
    if auth.can(user_id, "admin"):
        file_paths = detect_file_paths(response)
        conv_id = sessions.get_conversation_id(user_id=user_id)
        files_sent = 0
        for fp in file_paths[:3]:
            if await safe_send_file(context.bot, chat_id, fp, conv_id=conv_id, user_id=user_id):
                files_sent += 1
        if files_sent:
            await safe_send(context.bot, chat_id, f"📎 {files_sent} file(s) delivered.")


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
            "  /model — select AI model",
            "  /settings — bot settings",
            "  /browse <url> — browser control\n",
        ]
    if auth.is_owner(uid):
        lines += [
            "👥 OWNER:",
            "  /adduser <id> [name] — add user",
            "  /removeuser <id> — remove user",
            "  /listusers — manage users\n",
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
    await update.message.reply_text(
        "⚡ Zilla — Control Panel\n════════════════════════",
        reply_markup=kb_menu(uid),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
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
    await update.message.reply_text(
        "\n".join(lines), reply_markup=kb_sessions(all_sessions, active),
    )


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
    if not auth.can(uid, "admin"):
        await update.message.reply_text("Model selection requires admin access.")
        return
    current = get_model()
    await update.message.reply_text(
        f"🤖 Model Selection\n════════════════\nCurrent: {current}",
        reply_markup=kb_model(current),
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth.can(uid, "admin"):
        await update.message.reply_text("Settings require admin access.")
        return
    await update.message.reply_text(
        "⚙️ Settings\n═══════════", reply_markup=kb_settings(uid),
    )


async def cmd_brain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_inbox_stats()
    await update.message.reply_text(
        "🧠 Inbox\n════════\n\n"
        f"📥 {stats['images']} images | {stats['audio']} audio | {stats['documents']} docs\n"
        f"🎤 Audio engine: {get_audio_status()}"
    )


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
        import urllib.request
        import urllib.error

        if subcmd == "status":
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(f"{KIMI_BRIDGE_URL}/status"), timeout=5
                ) as resp:
                    data = json.loads(resp.read())
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
                payload = json.dumps({"action": "screenshot", "args": {}, "session": "telegram"}).encode()
                req = urllib.request.Request(
                    f"{KIMI_BRIDGE_URL}/command", data=payload,
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read())
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
            payload = json.dumps({"action": "list_tabs", "args": {}, "session": "telegram"}).encode()
            req = urllib.request.Request(
                f"{KIMI_BRIDGE_URL}/command", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
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
            payload = json.dumps({"action": "close_session", "args": {}, "session": "telegram"}).encode()
            req = urllib.request.Request(
                f"{KIMI_BRIDGE_URL}/command", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
            closed = result.get("data", {}).get("closed", 0)
            await update.message.reply_text(f"🗑️ Closed {closed} tab(s).")

        else:
            url = subcmd if subcmd.startswith(("http://", "https://")) else f"https://{subcmd}"
            if len(context.args) > 1:
                url = " ".join(context.args)
                if not url.startswith(("http://", "https://")):
                    url = f"https://{url}"
            payload = json.dumps({
                "action": "navigate",
                "args": {"url": url, "newTab": True},
                "session": "telegram",
            }).encode()
            req = urllib.request.Request(
                f"{KIMI_BRIDGE_URL}/command", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
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
    _active_cancel[chat_id] = cancel_event
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
            conv_id = sessions.get_conversation_id(user_id=uid)
            info = sessions.get_session_info(user_id=uid)
            if info and info.get("messages", 0) == 0:
                sessions.auto_title(transcript, user_id=uid)
            response, detected_id = await run_cli_async(
                transcript, conv_id,
                cancel_event=cancel_event,
                skip_permissions=auth.can(uid, "admin"),
            )
            if detected_id and detected_id != conv_id:
                sessions.set_conversation_id(detected_id, user_id=uid)
            final_conv = detected_id or conv_id
            if final_conv:
                sessions.set_last_seen_step(get_latest_step(final_conv), user_id=uid)
            sessions.increment_messages(user_id=uid)
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
    _active_cancel[chat_id] = cancel_event
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

        conv_id = sessions.get_conversation_id(user_id=uid)
        response, detected_id = await run_cli_async(
            prompt, conv_id,
            cancel_event=cancel_event,
            skip_permissions=auth.can(uid, "admin"),
        )
        if detected_id and detected_id != conv_id:
            sessions.set_conversation_id(detected_id, user_id=uid)
        final_conv = detected_id or conv_id
        if final_conv:
            sessions.set_last_seen_step(get_latest_step(final_conv), user_id=uid)
        sessions.increment_messages(user_id=uid)
        stop_typing.set()
        typing_task.cancel()
        await send_response(update, context, response, uid, chat_id)
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        logger.error(f"Photo error: {e}", exc_info=True)
        await update.message.reply_text(f"Photo error: {str(e)[:200]}")
    finally:
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
        _active_cancel[chat_id] = cancel_event
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

            conv_id = sessions.get_conversation_id(user_id=uid)
            response, detected_id = await run_cli_async(
                prompt, conv_id,
                cancel_event=cancel_event,
                skip_permissions=auth.can(uid, "admin"),
            )
            if detected_id and detected_id != conv_id:
                sessions.set_conversation_id(detected_id, user_id=uid)
            final_conv = detected_id or conv_id
            if final_conv:
                sessions.set_last_seen_step(get_latest_step(final_conv), user_id=uid)
            sessions.increment_messages(user_id=uid)
            stop_typing.set()
            typing_task.cancel()
            await send_response(update, context, response, uid, chat_id)
        except Exception as e:
            stop_typing.set()
            typing_task.cancel()
            logger.error(f"Document analysis error: {e}", exc_info=True)
            await update.message.reply_text(f"Analysis error: {str(e)[:200]}")
        finally:
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
        flow["target_name"] = name
        flow["step"] = "awaiting_role"
        context.user_data["adduser_flow"] = flow
        await update.message.reply_text(
            f"Name: {name or '(blank)'}\n\nChoose role:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 User", callback_data="user_add_role_user"),
                 InlineKeyboardButton("👑 Admin", callback_data="user_add_role_admin")],
                [InlineKeyboardButton("❌ Cancel", callback_data="user_add_cancel")],
            ]),
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

    # Owner add-user flow intercept
    if auth.is_owner(uid) and "adduser_flow" in context.user_data:
        await _handle_adduser_flow(update, context, context.user_data["adduser_flow"])
        return

    user_message = update.message.text
    active_session = sessions.get_active_name(uid)
    conv_id = sessions.get_conversation_id(user_id=uid)

    logger.info(f"Message in [{active_session}] (conv: {conv_id[:12] if conv_id else 'new'})")

    info = sessions.get_session_info(user_id=uid)
    if info and info.get("messages", 0) == 0:
        sessions.auto_title(user_message, user_id=uid)

    stop_typing = asyncio.Event()
    cancel_event = threading.Event()
    _active_cancel[chat_id] = cancel_event
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        response, detected_id = await run_cli_async(
            user_message, conv_id,
            cancel_event=cancel_event,
            skip_permissions=auth.can(uid, "admin"),
        )

        if detected_id and detected_id != conv_id:
            sessions.set_conversation_id(detected_id, user_id=uid)

        final_conv = detected_id or conv_id
        if final_conv:
            sessions.set_last_seen_step(get_latest_step(final_conv), user_id=uid)
        sessions.increment_messages(user_id=uid)

    except Exception as e:
        response = f"Error: {str(e)}"
        logger.error(f"Handler error: {e}", exc_info=True)
    finally:
        stop_typing.set()
        typing_task.cancel()
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

        # ── Menu ──
        if data == "menu_back":
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
            if not auth.can(uid, "admin"):
                await query.answer("Admin access required.", show_alert=True)
                return
            current = get_model()
            await query.edit_message_text(
                f"🤖 Model Selection\n════════════════\nCurrent: {current}",
                reply_markup=kb_model(current),
            )

        elif data == "menu_settings":
            if not auth.can(uid, "admin"):
                await query.answer("Admin access required.", show_alert=True)
                return
            await query.edit_message_text("⚙️ Settings\n═══════════", reply_markup=kb_settings(uid))

        elif data == "menu_inbox":
            items = get_inbox_items()
            if not items:
                await query.edit_message_text("📥 Inbox empty.", reply_markup=kb_back())
            else:
                lines = [f"📥 Inbox: {len(items)} items\n"]
                for item in items[:15]:
                    lines.append(f"  [{item['category']}] {item['name']} ({format_file_size(item['size'])})")
                if len(items) > 15:
                    lines.append(f"  …+{len(items) - 15} more")
                await query.edit_message_text("\n".join(lines), reply_markup=kb_back())

        elif data == "menu_browse":
            if not auth.can(uid, "admin"):
                await query.answer("Admin access required.", show_alert=True)
                return
            try:
                import urllib.request
                with urllib.request.urlopen(
                    urllib.request.Request(f"{KIMI_BRIDGE_URL}/status"), timeout=5
                ) as resp:
                    bdata = json.loads(resp.read())
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
                f"⏱️ Uptime: {get_uptime_str()}\n"
                f"🤖 Model: {get_model()}\n"
                f"📁 Sessions: {session_count}\n"
                f"🔧 Version: v{BOT_VERSION}",
                reply_markup=kb_back(),
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
            await query.edit_message_text(f"✅ Switched to [{name}].")

        elif data.startswith("sess_delete_"):
            name = data.removeprefix("sess_delete_")
            await query.edit_message_text(
                f"🗑️ Delete session [{name}]?\n\nThis cannot be undone.",
                reply_markup=kb_session_delete(name),
            )

        elif data.startswith("sess_confirm_del_"):
            name = data.removeprefix("sess_confirm_del_")
            sessions.delete_session(name, uid)
            await query.edit_message_text(
                f"🗑️ [{name}] deleted.\nActive: [{sessions.get_active_name(uid)}]"
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
            await query.edit_message_text(f"📁 Session [{name}] created.")

        # ── Model ──
        elif data.startswith("model_"):
            if not auth.can(uid, "admin"):
                await query.answer("Admin access required.", show_alert=True)
                return
            model_id = data.removeprefix("model_")
            set_model(model_id)
            await query.edit_message_text(
                f"✅ Model: {model_id}\n\nNext message will use this model."
            )

        # ── Settings ──
        elif data == "set_toggle_photo":
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

        elif data.startswith("user_set_role_"):
            if not auth.is_owner(uid):
                return
            # format: user_set_role_{role}_{id}
            rest = data.removeprefix("user_set_role_")
            parts = rest.split("_", 1)
            if len(parts) == 2:
                new_role, target_str = parts
                target_id = int(target_str)
                if auth.set_role(target_id, new_role):
                    users = auth.list_users()
                    info = users.get(target_id, {})
                    name = info.get("name") or f"User {target_id}"
                    await query.edit_message_text(
                        f"✅ {name} is now [{new_role}].",
                        reply_markup=kb_user_detail(target_id, new_role),
                    )
                else:
                    await query.edit_message_text("Failed to set role.")

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

        elif data == "user_add_cancel":
            context.user_data.pop("adduser_flow", None)
            users = auth.list_users()
            await query.edit_message_text(
                f"👥 Users ({len(users)})",
                reply_markup=kb_users(users),
            )

        elif data.startswith("user_add_role_"):
            if not auth.is_owner(uid):
                return
            role = data.removeprefix("user_add_role_")
            flow = context.user_data.get("adduser_flow", {})
            target_id = flow.get("target_id")
            target_name = flow.get("target_name", "")
            if not target_id:
                await query.edit_message_text("Flow expired. Use /menu > Users to start again.")
                return
            if auth.add_user(target_id, target_name, role):
                context.user_data.pop("adduser_flow", None)
                await query.edit_message_text(
                    f"✅ Added {target_name or target_id} as [{role}].",
                    reply_markup=kb_users(auth.list_users()),
                )
            else:
                context.user_data.pop("adduser_flow", None)
                await query.edit_message_text(f"User {target_id} already exists.")

        # ── Error recovery ──
        elif data == "err_retry":
            await query.edit_message_text(
                "🔄 Send your message again.",
                reply_markup=kb_back(),
            )

        elif data == "err_model":
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

async def post_init(application):
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


def _release_lock():
    global _lock_file_handle
    _remove_pid_file()
    if _lock_file_handle:
        try:
            import msvcrt
            msvcrt.locking(_lock_file_handle.fileno(), msvcrt.LK_UNLCK, 1)
            _lock_file_handle.close()
        except Exception:
            pass
        lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zilla_bot_instance.lock")
        try:
            os.remove(lock_path)
        except Exception:
            pass
        _lock_file_handle = None


def main():
    global sessions, auth, _lock_file_handle

    if sys.platform == "win32":
        import msvcrt
        bot_dir = os.path.dirname(os.path.abspath(__file__))
        lock_path = os.path.join(bot_dir, "zilla_bot_instance.lock")
        # Remove any stale lock files from old naming schemes
        for stale in ["agy_bot_instance.lock"]:
            stale_path = os.path.join(bot_dir, stale)
            if os.path.exists(stale_path):
                try:
                    os.remove(stale_path)
                except OSError:
                    pass
        try:
            _lock_file_handle = open(lock_path, "w")
            msvcrt.locking(_lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            _cout("❌ Another instance is already running.")
            _cout("   Double-click 'Stop Zilla.bat' to kill it first.")
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

    ensure_dirs()
    sessions = SessionManager(SESSIONS_FILE)
    auth = AuthManager(USERS_FILE, OWNER_CHAT_ID)

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
