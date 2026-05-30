# ============================================================
#  AGY Telegram Bot v8 — THIN PIPE TO CLI
# ============================================================
#
#  The bot is a THIN WRAPPER around the AI CLI.
#  - User sends message → pass DIRECTLY to CLI → send response
#  - CLI does ALL the thinking. Bot just relays.
#  - Bot's unique value: Telegram UI + file bridge + media
#
#  Commands:
#  /start     — Welcome + status
#  /help      — Full command reference
#  /ping      — Health check
#  /menu      — Master control panel (inline buttons)
#  /new       — New session
#  /sessions  — List sessions
#  /switch    — Switch session
#  /end       — End current session
#  /model     — Select AI model
#  /settings  — Bot settings
#  /adduser   — Add authorized user (owner only)
#  /removeuser — Remove user (owner only)
#  /brain     — Inbox stats
#  Send voice — Transcribe first, then CLI
#  Send photo — Save + analyze
#  Send doc   — Save to inbox
# ============================================================

import asyncio
import sys
import os
import logging
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
    get_model, set_model, get_timeout, get_setting, set_setting,
)
from sessions import SessionManager
from cli_engine import run_cli_async, get_latest_step
from media import (
    is_audio_capable, get_audio_status, transcribe_audio,
    save_photo, save_voice, save_audio, save_document, save_video,
    get_inbox_stats, get_inbox_items, format_file_size,
)
from formatter import format_for_telegram, detect_file_paths
from users import AuthManager

# ── Logging ────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(LOG_DIR, f"bot_{datetime.now().strftime('%Y%m%d')}.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

BOT_START_TIME = time.time()

# ── Global State ───────────────────────────────────────────
sessions: SessionManager = None
auth: AuthManager = None


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
    Show Telegram typing indicator. Rules:
    - First 60 seconds: just typing bubble, NO messages
    - After 60s: send "⏳ Still working on it..."
    - Then every 30s: send "⏳ Still processing..." so user knows bot is alive
    """
    start = asyncio.get_event_loop().time()
    notified_60 = False
    last_alive_msg = 0.0

    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass

        elapsed = asyncio.get_event_loop().time() - start

        # After 60s: first "still working" message
        if elapsed >= 60 and not notified_60:
            notified_60 = True
            last_alive_msg = elapsed
            try:
                await bot.send_message(chat_id=chat_id, text="⏳ Still working on it...")
            except Exception:
                pass

        # After that: every 30s send alive ping
        elif notified_60 and (elapsed - last_alive_msg) >= 30:
            last_alive_msg = elapsed
            minutes = int(elapsed) // 60
            secs = int(elapsed) % 60
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"⏳ Still processing... ({minutes}m {secs}s)"
                )
            except Exception:
                pass

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
            break
        except asyncio.TimeoutError:
            continue


async def safe_send(bot, chat_id: int, text: str, parse_mode: str = None):
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"Failed to send: {e}")


async def safe_send_file(bot, chat_id: int, filepath: str, caption: str = None,
                         conv_id: str = None, user_id: int = None):
    """Send a file to the user if it passes security checks."""
    if user_id is not None and not auth.is_authorized(user_id):
        return False

    if not os.path.exists(filepath):
        return False

    # Security: validate against allowlist
    abs_path = os.path.abspath(filepath).lower()
    safe_prefixes = [
        os.path.abspath(HOME_DIR).lower(),
        os.path.abspath(AGI_BRAIN_DIR).lower(),
    ]
    # Allow temp directory (for screenshots etc.)
    temp_dir = os.environ.get("TEMP", os.environ.get("TMP", ""))
    if temp_dir:
        safe_prefixes.append(os.path.abspath(temp_dir).lower())
    if conv_id:
        from config import BRAIN_DIR
        safe_prefixes.append(
            os.path.abspath(os.path.join(BRAIN_DIR, os.path.basename(conv_id))).lower()
        )

    if not any(abs_path == p or abs_path.startswith(p + os.sep) for p in safe_prefixes):
        logger.warning(f"[FILE] BLOCKED: {filepath}")
        return False

    size = os.path.getsize(filepath)
    if size > TELEGRAM_MAX_SEND_FILE:
        await safe_send(bot, chat_id, f"⚠️ File too large ({format_file_size(size)} > 50MB)")
        return False

    try:
        with open(filepath, "rb") as f:
            await bot.send_document(
                chat_id=chat_id, document=f,
                caption=caption or os.path.basename(filepath),
            )
        logger.info(f"[FILE] Sent: {filepath} ({format_file_size(size)})")
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


async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global auth gate — runs before ALL handlers."""
    if not update.effective_user:
        raise ApplicationHandlerStop()
    auth.reload()
    if not auth.is_authorized(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer()
        raise ApplicationHandlerStop()


# ══════════════════════════════════════════════════════════
#  KEYBOARDS (inline buttons — all in one place)
# ══════════════════════════════════════════════════════════

def kb_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Sessions", callback_data="menu_sessions"),
         InlineKeyboardButton("🤖 Model", callback_data="menu_model")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
         InlineKeyboardButton("📥 Inbox", callback_data="menu_inbox")],
        [InlineKeyboardButton("🌐 Browse", callback_data="menu_browse"),
         InlineKeyboardButton("🖥️ Status", callback_data="menu_status")],
    ])


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


def kb_session_detail(name: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔀 Switch", callback_data=f"sess_switch_{name}"),
         InlineKeyboardButton("🗑️ Delete", callback_data=f"sess_delete_{name}")],
        [InlineKeyboardButton("◀ Sessions", callback_data="sess_list")],
    ])


def kb_delete_confirm(name: str):
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


def kb_settings():
    auto_photo = get_setting("auto_describe_photos", True)
    timeout = get_timeout()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📸 Auto-analyze photos: {'ON' if auto_photo else 'OFF'}",
            callback_data="set_toggle_photo",
        )],
        [InlineKeyboardButton(
            f"⏱️ Timeout: {timeout}s",
            callback_data="set_cycle_timeout",
        )],
        [InlineKeyboardButton("◀ Menu", callback_data="menu_back")],
    ])


def kb_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀ Menu", callback_data="menu_back")],
    ])


def kb_error():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Retry", callback_data="err_retry"),
         InlineKeyboardButton("🤖 Change Model", callback_data="err_model")],
    ])


# ══════════════════════════════════════════════════════════
#  RESPONSE PIPELINE
# ══════════════════════════════════════════════════════════

async def send_response(update, context, response: str, user_id: int, chat_id: int):
    """Format, split, send text response + auto-deliver any files."""
    formatted_text, parse_mode = format_for_telegram(response)
    chunks = split_message(formatted_text)
    for chunk in chunks:
        await safe_send(context.bot, chat_id, chunk, parse_mode=parse_mode)

    # Auto-deliver files mentioned in response
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

    await update.message.reply_text(
        f"🧠 AGY Bot v{BOT_VERSION}\n"
        "═══════════════════════════\n\n"
        f"📌 Session: [{active}] {'(active)' if conv_id else '(new)'}\n"
        f"📊 Sessions: {session_count}\n"
        f"🤖 Model: {model}\n"
        f"📥 Inbox: {inbox['images']}img {inbox['audio']}aud {inbox['documents']}doc\n"
        f"🎤 Audio: {'Ready' if is_audio_capable() else 'N/A'}\n\n"
        "Just type anything — the CLI handles everything.\n"
        "/menu for control panel • /help for commands."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 AGY Bot Commands\n"
        "═════════════════════\n\n"
        "💬 CHAT:\n"
        "  Just type anything — goes straight to CLI\n\n"
        "📎 MEDIA:\n"
        "  Voice → transcribe → respond\n"
        "  Photo → analyze (add caption for specific questions)\n"
        "  Document → save to inbox\n\n"
        "📁 SESSIONS:\n"
        "  /new <name> — New session\n"
        "  /sessions — List all\n"
        "  /switch <name> — Switch\n"
        "  /end — End current\n\n"
        "⚙️ SETTINGS:\n"
        "  /menu — Control panel\n"
        "  /model — Select AI model\n"
        "  /settings — Bot settings\n"
        "  /brain — Inbox stats\n\n"
        "🌐 BROWSER:\n"
        "  /browse <url> — Open URL\n"
        "  /browse status — WebBridge health\n"
        "  /browse screenshot — Take screenshot\n"
        "  /browse tabs — List open tabs\n\n"
        "👥 ADMIN (owner only):\n"
        "  /adduser <id> — Add user\n"
        "  /removeuser <id> — Remove user\n"
    )


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
        f"Engine: v{BOT_VERSION}"
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 AGY Bot — Control Panel\n═════════════════════════════",
        reply_markup=kb_menu(),
    )


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

    if sessions.create_session(name, uid):
        await update.message.reply_text(
            f"📁 New session [{name}] created!\nNext message starts fresh."
        )
    else:
        sessions.set_active_name(name, uid)
        await update.message.reply_text(f"Switched to [{name}].")


async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    all_sessions = sessions.list_sessions(uid)
    active = sessions.get_active_name(uid)
    if not all_sessions:
        await update.message.reply_text("No sessions. Send a message to start!")
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
        await update.message.reply_text(f"[{name}] not found.")
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
    current = get_model()
    await update.message.reply_text(
        f"🤖 AI Model Selection\n═══════════════════════\nCurrent: {current}\n",
        reply_markup=kb_model(current),
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ Settings\n═══════════", reply_markup=kb_settings(),
    )


async def cmd_brain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_inbox_stats()
    await update.message.reply_text(
        "🧠 AGI Brain\n════════════\n\n"
        f"📥 Inbox: {stats['images']}img {stats['audio']}aud {stats['documents']}doc\n"
        f"🎤 Audio: {get_audio_status()}"
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
        await update.message.reply_text(f"✅ User {new_id} ({name}) added!")
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
        await update.message.reply_text(f"🗑️ User {target_id} removed and denied.")
    else:
        await update.message.reply_text(f"User {target_id} not found.")


# ══════════════════════════════════════════════════════════
#  MEDIA HANDLERS
# ══════════════════════════════════════════════════════════

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        filepath = await save_voice(context.bot, update.message.voice)

        if is_audio_capable():
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(None, transcribe_audio, filepath)
        else:
            transcript = None

        if transcript and not transcript.startswith("["):
            # Show transcription first (user visibility)
            await update.message.reply_text(f'🎤 You said: "{transcript}"')

            conv_id = sessions.get_conversation_id(user_id=uid)
            starting_step = get_latest_step(conv_id) if conv_id else 0

            response, detected_id = await run_cli_async(
                transcript, conv_id,
                skip_permissions=auth.is_admin(uid),
            )
            if detected_id and detected_id != conv_id:
                sessions.set_conversation_id(detected_id, user_id=uid)

            if detected_id or conv_id:
                sessions.set_last_seen_step(get_latest_step(detected_id or conv_id), user_id=uid)
            sessions.increment_messages(user_id=uid)

            stop_typing.set()
            typing_task.cancel()
            await send_response(update, context, response, uid, chat_id)
        else:
            stop_typing.set()
            typing_task.cancel()
            msg = transcript if transcript else "Transcription not available."
            await update.message.reply_text(f"🎤 Voice saved.\n{msg}")
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        await update.message.reply_text(f"Voice error: {str(e)[:300]}")


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
        await update.message.reply_text(f"Audio error: {str(e)[:300]}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    caption = update.message.caption or ""
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        filepath = await save_photo(context.bot, update.message.photo)
        auto_describe = get_setting("auto_describe_photos", True)

        if caption:
            prompt = f"The user sent this image: {filepath}. Their question: {caption}. Look at the image and respond."
        elif auto_describe:
            prompt = f"The user sent this image: {filepath}. Describe what you see in detail."
        else:
            stop_typing.set()
            typing_task.cancel()
            await update.message.reply_text(
                f"📸 Photo saved!\n{os.path.basename(filepath)} "
                f"({format_file_size(os.path.getsize(filepath))})\n"
                "Add a caption to get analysis."
            )
            return

        conv_id = sessions.get_conversation_id(user_id=uid)
        response, detected_id = await run_cli_async(
            prompt, conv_id, skip_permissions=auth.is_admin(uid),
        )
        if detected_id and detected_id != conv_id:
            sessions.set_conversation_id(detected_id, user_id=uid)
        sessions.increment_messages(user_id=uid)

        stop_typing.set()
        typing_task.cancel()
        await send_response(update, context, response, uid, chat_id)
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        await update.message.reply_text(f"Photo error: {str(e)[:300]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        filepath = await save_document(context.bot, update.message.document)
        await update.message.reply_text(
            f"📄 Document saved!\n{update.message.document.file_name or 'unknown'} "
            f"({format_file_size(os.path.getsize(filepath))})"
        )
    except Exception as e:
        await update.message.reply_text(f"Document error: {str(e)[:300]}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        filepath = await save_video(context.bot, update.message.video)
        await update.message.reply_text(
            f"🎬 Video saved! ({format_file_size(os.path.getsize(filepath))})"
        )
    except Exception as e:
        await update.message.reply_text(f"Video error: {str(e)[:300]}")


# ══════════════════════════════════════════════════════════
#  MAIN TEXT HANDLER — Pure pass-through to CLI
# ══════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    active_session = sessions.get_active_name(uid)
    conv_id = sessions.get_conversation_id(user_id=uid)

    logger.info(f"Message in [{active_session}] (conv: {conv_id[:12] if conv_id else 'new'})")

    # Auto-title from first message
    info = sessions.get_session_info(user_id=uid)
    if info and info.get("messages", 0) == 0:
        sessions.auto_title(user_message, user_id=uid)

    starting_step = get_latest_step(conv_id) if conv_id else 0

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        timeout = get_timeout()
        response, detected_id = await run_cli_async(
            user_message, conv_id, timeout=timeout,
            skip_permissions=auth.is_admin(uid),
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

    await send_response(update, context, response, uid, chat_id)


# ══════════════════════════════════════════════════════════
#  CALLBACK HANDLER (Single Router — no duplication)
# ══════════════════════════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = update.effective_user.id

    try:
        await query.answer()

        # ── Menu ──
        if data == "menu_back":
            await query.edit_message_text(
                "🧠 AGY Bot — Control Panel\n═════════════════════════════",
                reply_markup=kb_menu(),
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
            current = get_model()
            await query.edit_message_text(
                f"🤖 AI Model Selection\n═══════════════════════\nCurrent: {current}\n",
                reply_markup=kb_model(current),
            )

        elif data == "menu_settings":
            await query.edit_message_text(
                "⚙️ Settings\n═══════════", reply_markup=kb_settings(),
            )

        elif data == "menu_inbox":
            items = get_inbox_items()
            if not items:
                await query.edit_message_text("📥 Inbox empty!", reply_markup=kb_back())
            else:
                lines = [f"📥 Inbox: {len(items)} items\n"]
                for item in items[:15]:
                    lines.append(f"  [{item['category']}] {item['name']} ({format_file_size(item['size'])})")
                if len(items) > 15:
                    lines.append(f"  ... +{len(items) - 15} more")
                await query.edit_message_text("\n".join(lines), reply_markup=kb_back())

        elif data == "menu_browse":
            import urllib.request
            import urllib.error
            try:
                req = urllib.request.Request(f"{KIMI_BRIDGE_URL}/status", method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    bdata = json.loads(resp.read())
                running = bdata.get("running", False)
                ext = bdata.get("extension_connected", False)
                status_text = (
                    f"🌐 Kimi WebBridge\n═══════════════════\n\n"
                    f"Daemon: {'🟢 Running' if running else '🔴 Stopped'}\n"
                    f"Extension: {'🟢 Connected' if ext else '🔴 Disconnected'}\n\n"
                    "Use /browse <url> to open pages."
                )
            except Exception:
                status_text = (
                    "🌐 Kimi WebBridge\n═══════════════════\n\n"
                    "🔴 WebBridge not reachable.\n\n"
                    "Make sure the daemon is running."
                )
            await query.edit_message_text(status_text, reply_markup=kb_back())

        elif data == "menu_status":
            session_count = len(sessions.list_sessions(uid))
            await query.edit_message_text(
                "🖥️ Bot Status\n═══════════════\n\n"
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
            name = data[12:]  # "sess_switch_" is 12 chars
            sessions.set_active_name(name, uid)
            await query.edit_message_text(f"✅ Switched to [{name}].")

        elif data.startswith("sess_delete_"):
            name = data[12:]
            await query.edit_message_text(
                f"🗑️ Delete session [{name}]?\n\nThis cannot be undone.",
                reply_markup=kb_delete_confirm(name),
            )

        elif data.startswith("sess_confirm_del_"):
            name = data[17:]
            sessions.delete_session(name, uid)
            await query.edit_message_text(
                f"🗑️ Session [{name}] deleted.\nActive: [{sessions.get_active_name(uid)}]"
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
            await query.edit_message_text(f"📁 New session [{name}] created!")

        elif data.startswith("sess_detail_"):
            name = data[12:]
            info = sessions.get_session_info(user_id=uid, session_name=name)
            if info:
                title = info.get("title") or "(no title)"
                await query.edit_message_text(
                    f"📁 Session: {name}\n═══════════════════\n\n"
                    f'Title: "{title}"\n'
                    f"Created: {info.get('created', 'unknown')}\n"
                    f"Messages: {info.get('messages', 0)}\n"
                    f"Active: {'Yes ◀' if info.get('is_active') else 'No'}",
                    reply_markup=kb_session_detail(name),
                )

        # ── Model ──
        elif data.startswith("model_"):
            model_id = data[6:]
            set_model(model_id)
            await query.edit_message_text(
                f"✅ Model switched to: {model_id}\n\nNext message will use this model."
            )

        # ── Settings ──
        elif data == "set_toggle_photo":
            current = get_setting("auto_describe_photos", True)
            set_setting("auto_describe_photos", not current)
            await query.edit_message_text(
                "⚙️ Settings\n═══════════", reply_markup=kb_settings(),
            )

        elif data == "set_cycle_timeout":
            current = get_timeout()
            cycle = [120, 300, 600, 900, 60]
            try:
                idx = cycle.index(current)
                new_val = cycle[(idx + 1) % len(cycle)]
            except ValueError:
                new_val = 600
            set_setting("timeout", new_val)
            await query.edit_message_text(
                "⚙️ Settings\n═══════════", reply_markup=kb_settings(),
            )

        # ── Error recovery ──
        elif data == "err_retry":
            await query.edit_message_text("🔄 Send your message again.")

        elif data == "err_model":
            current = get_model()
            await query.edit_message_text(
                f"🤖 Try a different model?\nCurrent: {current}",
                reply_markup=kb_model(current),
            )

        else:
            await query.edit_message_text("Unknown action.")

    except Exception as e:
        logger.error(f"Callback error: {e}", exc_info=True)
        try:
            await query.answer(f"Error: {str(e)[:100]}")
        except Exception:
            pass

# ══════════════════════════════════════════════════════════
#  KIMI WEBBRIDGE — /browse command
# ══════════════════════════════════════════════════════════

async def cmd_browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Browse a URL or interact with the browser via Kimi WebBridge."""
    chat_id = update.effective_chat.id
    uid = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "🌐 Kimi WebBridge\n═══════════════════\n\n"
            "Usage:\n"
            "  /browse <url> — Open URL in browser\n"
            "  /browse status — Check WebBridge health\n"
            "  /browse screenshot — Take a screenshot\n"
            "  /browse tabs — List open tabs\n"
            "  /browse close — Close all browser tabs\n\n"
            "Or just tell me \"browse google.com\" in chat!"
        )
        return

    subcmd = context.args[0].lower()

    try:
        import urllib.request
        import urllib.error

        if subcmd == "status":
            try:
                req = urllib.request.Request(
                    f"{KIMI_BRIDGE_URL}/status",
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                running = data.get("running", False)
                ext = data.get("extension_connected", False)
                version = data.get("version", "unknown")
                await update.message.reply_text(
                    f"🌐 WebBridge Status\n"
                    f"Daemon: {'🟢 Running' if running else '🔴 Stopped'}\n"
                    f"Extension: {'🟢 Connected' if ext else '🔴 Disconnected'}\n"
                    f"Version: {version}"
                )
            except Exception as e:
                await update.message.reply_text(
                    f"🔴 WebBridge not reachable.\n{str(e)[:200]}"
                )

        elif subcmd == "screenshot":
            stop_typing = asyncio.Event()
            typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))
            try:
                payload = json.dumps({"action": "screenshot", "args": {}, "session": "telegram"}).encode()
                req = urllib.request.Request(
                    f"{KIMI_BRIDGE_URL}/command",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read())
                stop_typing.set()
                typing_task.cancel()
                screenshot_path = result.get("data", {}).get("path", "")
                if screenshot_path and os.path.isfile(screenshot_path):
                    await safe_send_file(context.bot, chat_id, screenshot_path,
                                        caption="📸 Browser screenshot", user_id=uid)
                else:
                    await update.message.reply_text(f"Screenshot result: {json.dumps(result, indent=2)[:500]}")
            except Exception as e:
                stop_typing.set()
                typing_task.cancel()
                await update.message.reply_text(f"Screenshot error: {str(e)[:300]}")

        elif subcmd == "tabs":
            payload = json.dumps({"action": "list_tabs", "args": {}, "session": "telegram"}).encode()
            req = urllib.request.Request(
                f"{KIMI_BRIDGE_URL}/command",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
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
                f"{KIMI_BRIDGE_URL}/command",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
            closed = result.get("data", {}).get("closed", 0)
            await update.message.reply_text(f"🗑️ Closed {closed} tab(s).")

        else:
            # Treat as URL to navigate
            url = subcmd if subcmd.startswith(("http://", "https://")) else f"https://{subcmd}"
            # If there are more args, it's the full URL
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
                f"{KIMI_BRIDGE_URL}/command",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
            if result.get("data", {}).get("success"):
                await update.message.reply_text(f"🌐 Opened: {url}")
            else:
                await update.message.reply_text(f"Failed to open: {url}\n{json.dumps(result)[:300]}")

    except Exception as e:
        await update.message.reply_text(f"WebBridge error: {str(e)[:300]}")



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
            await update.message.reply_text("⚠ An error occurred.", reply_markup=kb_error())
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
                    f"🟢 AGY Bot is online! (v{BOT_VERSION})\n"
                    f"Model: {get_model()}\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                ),
            )
        except Exception as e:
            logger.warning(f"[STARTUP] Failed to notify owner: {e}")


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

def main():
    global sessions, auth

    # Single instance lock (no admin needed)
    if sys.platform == "win32":
        import msvcrt
        global _lock_file_handle
        try:
            _lock_file_handle = open("agy_bot_instance.lock", "w")
            msvcrt.locking(_lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("❌ Another instance is already running!")
            sys.exit(1)

    # Fix Windows console encoding
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Initialize
    ensure_dirs()
    sessions = SessionManager(SESSIONS_FILE)
    auth = AuthManager(USERS_FILE, OWNER_CHAT_ID)

    model = get_model()
    session_count = len(sessions.list_sessions(OWNER_CHAT_ID))
    audio_ok = is_audio_capable()

    print("=" * 50)
    print(f"  AGY Telegram Bot v{BOT_VERSION}")
    print("  Thin pipe to CLI — it does the thinking")
    print("=" * 50)
    print(f"  Owner: {OWNER_CHAT_ID}")
    print(f"  Users: {auth.count()} + owner")
    print(f"  Model: {model}")
    print(f"  Sessions: {session_count}")
    print(f"  Audio: {'Ready' if audio_ok else 'N/A'}")
    print(f"  Timeout: {get_timeout()}s")
    print("  Starting... Press Ctrl+C to stop.")
    print()

    # Build bot
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Auth middleware (group -1 = runs before all handlers)
    app.add_handler(TypeHandler(Update, auth_middleware), group=-1)

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("menu", cmd_menu))
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

    # Callback handler — ONE router for ALL buttons
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Media handlers
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))

    # Text handler (must be last)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Error handler
    app.add_error_handler(error_handler)

    # Run
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
