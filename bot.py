# ============================================================
#  agy Telegram Bot v6 — THIN PIPE
# ============================================================
#
#  PHILOSOPHY:
#  The bot is a THIN PIPE between Telegram and Antigravity CLI.
#  - User sends message → pass DIRECTLY to agy → send response
#  - Agy handles EVERYTHING: thinking, searching, decomposing
#  - We don't wrap, classify, orchestrate, or modify the message
#  - We just forward it and return the clean result
#
#  COMMANDS:
#  /start     — Welcome + status
#  /help      — Full command reference
#  /ping      — Health check
#  /new       — New session
#  /sessions  — List sessions
#  /switch    — Switch session
#  /end       — End session
#  /sub       — Spawn background task
#  /brain     — AGI-Brain stats
#  /inbox     — Show inbox items
#  /note      — Save a note
#  /web       — Web search via agy
#  Send photo — Auto-save + analyze
#  Send voice — Auto-transcribe + respond
#  Send doc   — Auto-save to inbox
# ============================================================

import asyncio
import sys
import os
import logging
from datetime import datetime

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import (
    BOT_TOKEN,
    ALLOWED_USER_ID,
    TELEGRAM_MAX_LENGTH,
    AGI_BRAIN_DIR,
    AGY_TIMEOUT,
)

from sessions import SessionManager
from agy_runner import run_agy_async
from brain_manager import (
    ensure_brain_structure,
    save_note,
    save_research,
    save_transcript,
    get_brain_stats,
    get_inbox_pending,
)
from audio_handler import transcribe_audio, is_audio_capable, get_audio_status
from file_handler import (
    save_photo,
    save_voice,
    save_audio,
    save_document,
    save_video,
    format_file_size,
)

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


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════

def split_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split long messages for Telegram's 4096-char limit."""
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
    """Send typing indicator every 4 seconds until stopped."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
            break
        except asyncio.TimeoutError:
            continue


async def safe_send(bot, chat_id: int, text: str):
    """Send a message safely."""
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.error(f"Failed to send message: {e}")


def is_authorized(update: Update) -> bool:
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized: user {user_id}")
        return False
    return True


# ══════════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════════

sessions: SessionManager = None


# ══════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    active = sessions.active_name
    conv_id = sessions.get_conversation_id()
    session_count = len(sessions.list_sessions())
    stats = get_brain_stats()
    audio_ok = is_audio_capable()

    total_inbox = sum(stats["inbox"].values())
    total_knowledge = sum(stats["knowledge"].values())

    await update.message.reply_text(
        "🧠 AGI Brain — Mother Bot v6\n"
        "═══════════════════════════\n\n"
        f"📌 Session: [{active}] {'(persistent)' if conv_id else '(new)'}\n"
        f"📊 Sessions: {session_count}\n\n"
        f"🧠 Brain: {total_inbox} inbox | {total_knowledge} knowledge\n"
        f"🎤 Audio: {'Ready' if audio_ok else 'N/A'}\n\n"
        "Thin pipe to Antigravity CLI.\n"
        "Just type anything — agy handles the rest.\n\n"
        "/help for commands."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    await update.message.reply_text(
        "🧠 AGI Brain Commands\n"
        "═════════════════════\n\n"
        "💬 CHAT:\n"
        "  Just type anything — goes straight to agy\n"
        "  Agy handles everything internally\n\n"
        "📎 MEDIA:\n"
        "  Send voice → auto-transcribe → respond\n"
        "  Send photo → save + analyze (add caption)\n"
        "  Send doc   → save to inbox\n\n"
        "📁 SESSIONS:\n"
        "  /new <name>     — New session\n"
        "  /sessions       — List all\n"
        "  /switch <name>  — Switch\n"
        "  /end            — End current\n\n"
        "🧠 AGI BRAIN:\n"
        "  /brain    — Stats\n"
        "  /inbox    — Inbox items\n"
        "  /note     — Save a note\n"
        "  /web      — Web search\n\n"
        "🤖 BACKGROUND:\n"
        "  /sub <task> — Run task in background\n\n"
        "🔧 UTILITY:\n"
        "  /start /help /ping\n"
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    active = sessions.active_name
    conv_id = sessions.get_conversation_id()
    now = datetime.now().strftime("%H:%M:%S")

    await update.message.reply_text(
        f"🏓 Pong! ({now})\n"
        f"Session: [{active}] | Conv: {'active' if conv_id else 'new'}\n"
        f"Audio: {get_audio_status()}\n"
        f"Engine: v6 Thin Pipe"
    )


# ── Session Commands ──────────────────────────────────────

async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    args = context.args
    if args:
        name = "-".join(args).lower().strip()
    else:
        existing = sessions.list_sessions()
        i = 1
        while True:
            name = f"session-{i}"
            if name not in existing:
                break
            i += 1
    if sessions.create_session(name):
        await update.message.reply_text(
            f"📁 New session [{name}] created!\n"
            "Next message starts a fresh conversation."
        )
    else:
        sessions.active_name = name
        await update.message.reply_text(
            f"Switched to [{name}]."
        )


async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    all_sessions = sessions.list_sessions()
    active = sessions.active_name
    if not all_sessions:
        await update.message.reply_text("No sessions. Send a message to start!")
        return
    lines = ["📁 Sessions\n════════════\n"]
    for name, info in all_sessions.items():
        marker = " ◀ ACTIVE" if name == active else ""
        msgs = info.get("messages", 0)
        has_conv = "persistent" if info.get("conversation_id") else "new"
        lines.append(f"  {name}{marker} — {msgs} msgs ({has_conv})")
    lines.append(f"\n/switch <name> to change.")
    await update.message.reply_text("\n".join(lines))


async def cmd_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /switch <name>\nUse /sessions to see list.")
        return
    name = "-".join(args).lower().strip()
    all_sessions = sessions.list_sessions()
    if name not in all_sessions:
        await update.message.reply_text(
            f"[{name}] not found. Available: " + ", ".join(all_sessions.keys())
        )
        return
    sessions.active_name = name
    await update.message.reply_text(f"Switched to [{name}].")


async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    name = sessions.active_name
    sessions.delete_session(name)
    await update.message.reply_text(
        f"Session [{name}] ended. Active: [{sessions.active_name}]."
    )


# ── AGI Brain Commands ────────────────────────────────────

async def cmd_brain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    stats = get_brain_stats()
    inbox = stats["inbox"]
    knowledge = stats["knowledge"]
    await update.message.reply_text(
        "🧠 AGI Brain\n"
        "════════════\n\n"
        f"📥 Inbox: {inbox['images']}img {inbox['audio']}aud "
        f"{inbox['documents']}doc {inbox['telegram']}tg\n\n"
        f"📚 Knowledge: {knowledge['notes']}notes "
        f"{knowledge['transcripts']}trans "
        f"{knowledge['summaries']}sum {knowledge['research']}res\n\n"
        f"📁 Projects: {stats['projects']}\n"
        f"🎤 Audio: {get_audio_status()}"
    )


async def cmd_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    pending = get_inbox_pending()
    if not pending:
        await update.message.reply_text("📥 Inbox empty!")
        return
    lines = [f"📥 Inbox: {len(pending)} items\n"]
    for item in pending[:20]:
        size = format_file_size(item["size"])
        lines.append(f"  [{item['category']}] {item['name']} ({size})")
    if len(pending) > 20:
        lines.append(f"  ... +{len(pending) - 20} more")
    await update.message.reply_text("\n".join(lines))


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /note <text>")
        return
    text = " ".join(args)
    filepath = save_note(text)
    await update.message.reply_text(f"📝 Note saved: {os.path.basename(filepath)}")


async def cmd_web(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /web <query>")
        return
    query = " ".join(args)
    chat_id = update.effective_chat.id

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        response, _ = await run_agy_async(
            f"Search the web and summarize: {query}",
            conversation_id=None,
            timeout=300,
        )
        save_research(response, query)

        stop_typing.set()
        typing_task.cancel()

        chunks = split_message(f"🌐 Web: {query}\n{'═' * 25}\n\n{response}")
        for chunk in chunks:
            await safe_send(context.bot, chat_id, chunk)
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        await update.message.reply_text(f"Web search failed: {str(e)[:300]}")


# ── Sub-agent Command ─────────────────────────────────────

async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /sub <task>")
        return
    task = " ".join(args)
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        f"🤖 Sub-agent launched!\n"
        f"Task: \"{task[:80]}...\"\n"
        "Result will be sent when done!"
    )
    asyncio.create_task(_run_subagent(context.bot, chat_id, task))


async def _run_subagent(bot, chat_id: int, task: str):
    logger.info(f"[SUB-AGENT] Starting: {task[:80]}")
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(bot, chat_id, stop_typing))
    try:
        response, _ = await run_agy_async(task, conversation_id=None, timeout=180)
        stop_typing.set()
        typing_task.cancel()

        chunks = split_message(f"🤖 Sub-agent done!\n{'═' * 25}\n\n{response}")
        for chunk in chunks:
            await safe_send(bot, chat_id, chunk)
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        logger.error(f"[SUB-AGENT] Error: {e}")
        try:
            await bot.send_message(chat_id=chat_id, text=f"Task failed: {str(e)[:300]}")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
#  MEDIA HANDLERS
# ══════════════════════════════════════════════════════════

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    voice = update.message.voice
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        filepath = await save_voice(context.bot, voice)

        if is_audio_capable():
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(None, transcribe_audio, filepath)
        else:
            transcript = None

        if transcript and not transcript.startswith("["):
            save_transcript(transcript, os.path.basename(filepath))
            await update.message.reply_text(f"🎤 You said: \"{transcript}\"\n\nProcessing...")

            conv_id = sessions.get_conversation_id()
            response, detected_id = await run_agy_async(transcript, conv_id)
            if detected_id and detected_id != conv_id:
                sessions.set_conversation_id(detected_id)
            sessions.increment_messages()

            stop_typing.set()
            typing_task.cancel()
            for chunk in split_message(response):
                await safe_send(context.bot, chat_id, chunk)
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
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    audio = update.message.audio
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        filepath = await save_audio(context.bot, audio)
        if is_audio_capable():
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(None, transcribe_audio, filepath)
        else:
            transcript = None
        stop_typing.set()
        typing_task.cancel()
        if transcript and not transcript.startswith("["):
            save_transcript(transcript, os.path.basename(filepath))
            await update.message.reply_text(f"🎵 Transcribed:\n\"{transcript}\"")
        else:
            await update.message.reply_text(f"🎵 Audio saved: {os.path.basename(filepath)}")
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        await update.message.reply_text(f"Audio error: {str(e)[:300]}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    photos = update.message.photo
    caption = update.message.caption or ""
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        filepath = await save_photo(context.bot, photos)
        if caption:
            conv_id = sessions.get_conversation_id()
            response, detected_id = await run_agy_async(
                f"Photo saved to {filepath}. {caption}", conv_id
            )
            if detected_id and detected_id != conv_id:
                sessions.set_conversation_id(detected_id)
            sessions.increment_messages()
            stop_typing.set()
            typing_task.cancel()
            for chunk in split_message(response):
                await safe_send(context.bot, chat_id, chunk)
        else:
            stop_typing.set()
            typing_task.cancel()
            await update.message.reply_text(
                f"📸 Photo saved!\n{os.path.basename(filepath)} ({format_file_size(os.path.getsize(filepath))})\n"
                "Add a caption to get analysis."
            )
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        await update.message.reply_text(f"Photo error: {str(e)[:300]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    try:
        filepath = await save_document(context.bot, update.message.document)
        await update.message.reply_text(
            f"📄 Document saved!\n{update.message.document.file_name or 'unknown'} "
            f"({format_file_size(os.path.getsize(filepath))})"
        )
    except Exception as e:
        await update.message.reply_text(f"Document error: {str(e)[:300]}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    try:
        filepath = await save_video(context.bot, update.message.video)
        await update.message.reply_text(
            f"🎬 Video saved! ({format_file_size(os.path.getsize(filepath))})"
        )
    except Exception as e:
        await update.message.reply_text(f"Video error: {str(e)[:300]}")


# ══════════════════════════════════════════════════════════
#  MAIN TEXT HANDLER — Pure pass-through to agy
# ══════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main handler — thin pipe to agy.

    Takes the user's EXACT message, passes it directly to agy CLI.
    agy does all the thinking. We just relay the response.
    """
    if not is_authorized(update):
        return

    user_message = update.message.text
    chat_id = update.effective_chat.id
    active_session = sessions.active_name
    conv_id = sessions.get_conversation_id()

    logger.info(
        f"Message in [{active_session}]: {user_message[:80]}..."
        f" (conv: {conv_id[:12] if conv_id else 'new'})"
    )

    # Start typing indicator
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    # Simple progress updates
    progress_task = asyncio.create_task(
        _send_progress_updates(context.bot, chat_id, stop_typing)
    )

    try:
        # Direct pass-through — agy does ALL the thinking
        response, detected_id = await run_agy_async(user_message, conv_id)

        if detected_id and detected_id != conv_id:
            sessions.set_conversation_id(detected_id)
            logger.info(f"Stored conversation ID: {detected_id[:12]}...")

        sessions.increment_messages()

    except Exception as e:
        response = f"Error: {str(e)}"
        logger.error(f"Handler error: {e}", exc_info=True)
    finally:
        stop_typing.set()
        typing_task.cancel()
        progress_task.cancel()

    # Send response
    chunks = split_message(response)
    for i, chunk in enumerate(chunks):
        try:
            await safe_send(context.bot, chat_id, chunk)
        except Exception as e:
            logger.error(f"Send chunk {i + 1} failed: {e}")


async def _send_progress_updates(
    bot, chat_id: int, stop_event: asyncio.Event
):
    """
    Simple progress updates.
    First update after 30s, then every 60s.
    """
    messages = [
        "⏳ Still working on it...",
        "⏳ Processing, almost there...",
        "⏳ Still going...",
        "⏳ Hang on, making progress...",
    ]

    # First update after 30 seconds
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=30.0)
        return
    except asyncio.TimeoutError:
        pass

    for msg in messages:
        if stop_event.is_set():
            return
        try:
            await bot.send_message(chat_id=chat_id, text=msg)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60.0)
            return
        except asyncio.TimeoutError:
            continue


# ══════════════════════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Bot error: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text("⚠ An error occurred. Check bot logs.")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

def main():
    global sessions

    # Fix Windows console encoding
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Ensure AGI-Brain directory structure exists
    ensure_brain_structure()

    # Initialize session manager
    from config import STATE_FILE
    sessions = SessionManager(STATE_FILE)

    active = sessions.active_name
    session_count = len(sessions.list_sessions())
    stats = get_brain_stats()
    audio_ok = is_audio_capable()

    print("=" * 55)
    print("  [MOTHER]  AGI Brain — Telegram Bot v6")
    print("  Direct Pass-Through — agy does the thinking")
    print("=" * 55)
    print(f"  Active Session:  [{active}]")
    print(f"  Total Sessions:  {session_count}")
    print(f"  User ID:         {ALLOWED_USER_ID}")
    print(f"  AGI-Brain:       {AGI_BRAIN_DIR}")
    print(f"  Audio:           {'Ready' if audio_ok else 'N/A'}")
    print(f"  Inbox Items:     {sum(stats['inbox'].values())}")
    print(f"  Knowledge:       {sum(stats['knowledge'].values())}")
    print(f"  Timeout:         {AGY_TIMEOUT}s")
    print("  Bot starting... Press Ctrl+C to stop.")
    print()

    # Build bot
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("switch", cmd_switch))
    app.add_handler(CommandHandler("end", cmd_end))
    app.add_handler(CommandHandler("sub", cmd_sub))
    app.add_handler(CommandHandler("brain", cmd_brain))
    app.add_handler(CommandHandler("inbox", cmd_inbox))
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(CommandHandler("web", cmd_web))

    # Media handlers
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))

    # Text handler (last)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Error handler
    app.add_error_handler(error_handler)

    # Start polling
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
