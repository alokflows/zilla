# ============================================================
#  agy Telegram Bot v7 — THIN PIPE + INTERACTIVE UI
# ============================================================
#
#  PHILOSOPHY:
#  The bot is a THIN PIPE between Telegram and Antigravity CLI.
#  - User sends message → pass DIRECTLY to agy → send response
#  - Agy handles EVERYTHING: thinking, searching, decomposing
#  - We don't wrap, classify, orchestrate, or modify the message
#  - We just forward it and return the clean result
#
#  NEW in v7:
#  - Interactive button menus (/menu)
#  - Real-time progress via transcript polling
#  - Sub-agent orchestration with async monitoring
#  - Settings panel (model, timeout, progress style)
#  - Skills management
#  - File sending to user (bot → Telegram)
#  - Image analysis (auto-describe photos)
#  - Startup notification
#  - Error recovery with buttons
#  - Conversation dump bug fix
#
#  COMMANDS:
#  /start     — Welcome + status
#  /help      — Full command reference
#  /ping      — Health check
#  /menu      — Master control panel
#  /new       — New session
#  /sessions  — List sessions (button UI)
#  /switch    — Switch session
#  /end       — End current session
#  /model     — Select AI model
#  /agents    — Sub-agent dashboard
#  /sub       — Spawn background task
#  /brain     — AGI-Brain stats
#  /inbox     — Show inbox items
#  /note      — Save a note
#  /web       — Web search via agy
#  /skills    — Manage skills
#  /settings  — Bot settings
#  /service   — Bot status & uptime
#  Send photo — Auto-save + analyze
#  Send voice — Auto-transcribe + respond
#  Send doc   — Auto-save to inbox
# ============================================================

import asyncio
import sys
import os
import logging
import time
from datetime import datetime

from telegram import Update, ForceReply
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    BOT_TOKEN,
    ALLOWED_USER_ID,
    OWNER_CHAT_ID,
    TELEGRAM_MAX_LENGTH,
    TELEGRAM_MAX_SEND_FILE,
    AGI_BRAIN_DIR,
    AGY_TIMEOUT,
    BOT_VERSION,
    SETTINGS_FILE,
    AGENTS_FILE,
    MAX_CONCURRENT_AGENTS,
    SKILLS_DIR,
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
from settings_manager import SettingsManager
from agent_manager import AgentManager
from skills_manager import SkillsManager
from telegram_formatter import format_for_telegram

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

# Track bot start time for uptime
BOT_START_TIME = time.time()


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


async def safe_send(bot, chat_id: int, text: str, parse_mode: str = None):
    """Send a message safely."""
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"Failed to send message: {e}")


async def safe_send_file(bot, chat_id: int, filepath: str, caption: str = None):
    """Send a file to the user if it exists and is within size limits."""
    if not os.path.exists(filepath):
        logger.warning(f"[FILE] Not found: {filepath}")
        return False

    size = os.path.getsize(filepath)
    if size > TELEGRAM_MAX_SEND_FILE:
        logger.warning(f"[FILE] Too large ({size} bytes): {filepath}")
        await safe_send(bot, chat_id, f"⚠️ File too large to send ({format_file_size(size)} > 50MB)")
        return False

    try:
        with open(filepath, "rb") as f:
            await bot.send_document(
                chat_id=chat_id,
                document=f,
                caption=caption or os.path.basename(filepath),
            )
        logger.info(f"[FILE] Sent: {filepath} ({format_file_size(size)})")
        return True
    except Exception as e:
        logger.error(f"[FILE] Failed to send: {e}")
        return False


def is_authorized(update: Update) -> bool:
    if ALLOWED_USER_ID is None:
        return True
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized: user {user_id}")
        return False
    return True


def get_uptime_str() -> str:
    """Get bot uptime as human-readable string."""
    elapsed = int(time.time() - BOT_START_TIME)
    days, remainder = divmod(elapsed, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def detect_file_paths(text: str) -> list[str]:
    """Extract Windows file paths from response text."""
    import re
    # Match Windows paths (with \ or /), and allow spaces if they have a file extension at the end
    # Or match quoted paths
    paths = []
    
    # 1. Match paths inside quotes: "C:\path to\file.ext" or 'C:/path to/file.ext'
    quoted_pattern = r'["\']([A-Z]:[\\/][^"\']+?)["\']'
    for match in re.findall(quoted_pattern, text, flags=re.IGNORECASE):
        paths.append(match)
        
    # 2. Match unquoted paths (no spaces): C:\path\file.ext
    # We exclude backticks (`), quotes, and common punctuation that might wrap the path
    unquoted_pattern = r'[A-Z]:[\\/](?:[^\s<>"|?*\n`]+[\\/])*[^\s<>"|?*\n`]+'
    for match in re.findall(unquoted_pattern, text, flags=re.IGNORECASE):
        # Strip trailing punctuation (like commas or periods) that might be attached
        match = match.rstrip(".,;:)'\"]}")
        if match not in paths:
            paths.append(match)
            
    # Normalize slashes and filter to only existing files
    valid_paths = []
    for p in paths:
        normalized = os.path.normpath(p)
        if os.path.isfile(normalized) and normalized not in valid_paths:
            valid_paths.append(normalized)
            
    return valid_paths


# ══════════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════════

sessions: SessionManager = None
settings: SettingsManager = None
agents: AgentManager = None
skills: SkillsManager = None


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
    model = settings.get_model()

    total_inbox = sum(stats["inbox"].values())
    total_knowledge = sum(stats["knowledge"].values())

    await update.message.reply_text(
        f"🧠 AGI Brain — Mother Bot v{BOT_VERSION}\n"
        "═══════════════════════════\n\n"
        f"📌 Session: [{active}] {'(persistent)' if conv_id else '(new)'}\n"
        f"📊 Sessions: {session_count}\n"
        f"🤖 Model: {model}\n\n"
        f"🧠 Brain: {total_inbox} inbox | {total_knowledge} knowledge\n"
        f"🎤 Audio: {'Ready' if audio_ok else 'N/A'}\n\n"
        "Thin pipe to Antigravity CLI.\n"
        "Just type anything — agy handles the rest.\n\n"
        "/menu for control panel • /help for commands."
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
        "  Send photo → auto-analyze\n"
        "  Send doc   → save to inbox\n\n"
        "📁 SESSIONS:\n"
        "  /new <name>     — New session\n"
        "  /sessions       — List all (buttons)\n"
        "  /switch <name>  — Switch\n"
        "  /end            — End current\n\n"
        "🧠 AGI BRAIN:\n"
        "  /brain    — Stats\n"
        "  /inbox    — Inbox items\n"
        "  /note     — Save a note\n"
        "  /web      — Web search\n\n"
        "🤖 AGENTS:\n"
        "  /sub <task> — Run task in background\n"
        "  /agents     — Agent dashboard\n\n"
        "⚙️ SETTINGS:\n"
        "  /menu     — Control panel\n"
        "  /model    — Select AI model\n"
        "  /settings — Bot settings\n"
        "  /skills   — Manage skills\n"
        "  /service  — Bot status\n\n"
        "🔧 UTILITY:\n"
        "  /start /help /ping\n"
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    active = sessions.active_name
    conv_id = sessions.get_conversation_id()
    now = datetime.now().strftime("%H:%M:%S")
    model = settings.get_model()

    await update.message.reply_text(
        f"🏓 Pong! ({now})\n"
        f"Session: [{active}] | Conv: {'active' if conv_id else 'new'}\n"
        f"Model: {model}\n"
        f"Uptime: {get_uptime_str()}\n"
        f"Audio: {get_audio_status()}\n"
        f"Engine: v{BOT_VERSION} Thin Pipe"
    )


# ══════════════════════════════════════════════════════════
#  MENU SYSTEM
# ══════════════════════════════════════════════════════════

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    from ui_buttons import build_menu_keyboard
    keyboard = build_menu_keyboard()
    await update.message.reply_text(
        f"🧠 AGI Brain — Control Panel\n"
        "═════════════════════════════",
        reply_markup=keyboard,
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
    from ui_buttons import build_sessions_list_keyboard
    all_sessions = sessions.list_sessions()
    active = sessions.active_name
    if not all_sessions:
        await update.message.reply_text("No sessions. Send a message to start!")
        return

    lines = [f"📁 Sessions ({len(all_sessions)})\n════════════\n"]
    for name, info in all_sessions.items():
        marker = " ◀" if name == active else ""
        msgs = info.get("messages", 0)
        title = info.get("title", "")
        title_str = f' — "{title}"' if title else ""
        lines.append(f"  {name}{marker}{title_str} — {msgs} msgs")

    keyboard = build_sessions_list_keyboard(all_sessions, active)
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=keyboard,
    )


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

        formatted_text, parse_mode = format_for_telegram(f"🌐 Web: {query}\n{'═' * 25}\n\n{response}")
        chunks = split_message(formatted_text)
        for chunk in chunks:
            await safe_send(context.bot, chat_id, chunk, parse_mode=parse_mode)
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        await update.message.reply_text(f"Web search failed: {str(e)[:300]}")


# ══════════════════════════════════════════════════════════
#  MODEL SELECTOR
# ══════════════════════════════════════════════════════════

async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    from ui_buttons import build_model_keyboard
    current = settings.get_model()
    keyboard = build_model_keyboard(current)
    await update.message.reply_text(
        f"🤖 AI Model Selection\n"
        f"═══════════════════════\n"
        f"Current: {current}\n",
        reply_markup=keyboard,
    )


# ══════════════════════════════════════════════════════════
#  SETTINGS
# ══════════════════════════════════════════════════════════

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    from ui_buttons import build_settings_keyboard
    keyboard = build_settings_keyboard(settings)
    await update.message.reply_text(
        "⚙️ Settings\n"
        "═══════════",
        reply_markup=keyboard,
    )


# ══════════════════════════════════════════════════════════
#  SKILLS
# ══════════════════════════════════════════════════════════

async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    from ui_buttons import build_skills_list_keyboard
    skill_list = skills.list_skills()
    keyboard = build_skills_list_keyboard(skill_list)
    count = len(skill_list)
    await update.message.reply_text(
        f"🔧 Installed Skills ({count})\n"
        "════════════════════",
        reply_markup=keyboard,
    )


# ══════════════════════════════════════════════════════════
#  SERVICE STATUS
# ══════════════════════════════════════════════════════════

async def cmd_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    model = settings.get_model()
    session_count = len(sessions.list_sessions())
    running_agents = len(agents.list_running())

    await update.message.reply_text(
        "🖥️ Bot Status\n"
        "═══════════════\n\n"
        f"⏱️ Uptime: {get_uptime_str()}\n"
        f"🤖 Model: {model}\n"
        f"📁 Sessions: {session_count}\n"
        f"🤖 Agents: {running_agents} running\n"
        f"🔧 Version: v{BOT_VERSION}\n"
    )


# ══════════════════════════════════════════════════════════
#  SUB-AGENT COMMANDS
# ══════════════════════════════════════════════════════════

async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /sub <task>")
        return
    task = " ".join(args)
    chat_id = update.effective_chat.id

    agent_info = agents.launch(task)
    if not agent_info:
        await update.message.reply_text(
            f"⚠️ Max agents reached ({agents.max_agents}). "
            "Use /agents to manage running agents."
        )
        return

    await update.message.reply_text(
        f"🤖 Agent #{agent_info.id} launched!\n"
        f"Task: \"{agent_info.title}\"\n"
        "Result will be sent when done!"
    )

    # Run the agent in the background
    bg_task = asyncio.create_task(
        _run_agent_task(context.bot, chat_id, agent_info.id, task)
    )
    agents.register_task(agent_info.id, bg_task)


async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    from ui_buttons import build_agents_list_keyboard

    running = agents.list_running()
    done = agents.list_done()

    lines = ["🤖 Sub-Agents\n══════════════\n"]

    if running:
        lines.append("🟢 Running:")
        for a in running:
            lines.append(f'  #{a.id} "{a.title}" — {a.elapsed_str()}')
        lines.append("")

    if done:
        lines.append("✅ Done:")
        for a in done[-5:]:  # Show last 5
            lines.append(f'  #{a.id} "{a.title}" — {a.status}')
        lines.append("")

    if not running and not done:
        lines.append("No agents. Use /sub <task> to launch one.")

    keyboard = build_agents_list_keyboard(running, done)
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=keyboard,
    )


async def _run_agent_task(bot, chat_id: int, agent_id: str, task: str):
    """Run a sub-agent task and notify when done."""
    logger.info(f"[AGENT #{agent_id}] Starting: {task[:80]}")
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(bot, chat_id, stop_typing))
    try:
        response, conv_id = await run_agy_async(task, conversation_id=None, timeout=300)

        agents.complete(agent_id, response)
        if conv_id:
            agent = agents.get(agent_id)
            if agent:
                agent.conversation_id = conv_id

        stop_typing.set()
        typing_task.cancel()

        # Notify user
        from ui_buttons import build_agent_detail_keyboard
        keyboard = build_agent_detail_keyboard(agent_id)

        # Send short notification
        preview = response[:200] + "..." if len(response) > 200 else response
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ Agent #{agent_id} finished!\n"
                 f"Task: \"{agents.get(agent_id).title}\"\n"
                 f"Time: {agents.get(agent_id).elapsed_str()}\n\n"
                 f"Preview:\n{preview}",
            reply_markup=keyboard,
        )

    except Exception as e:
        agents.fail(agent_id, str(e))
        stop_typing.set()
        typing_task.cancel()
        logger.error(f"[AGENT #{agent_id}] Error: {e}")
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ Agent #{agent_id} failed: {str(e)[:300]}"
            )
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
            await update.message.reply_text(f'🎤 You said: "{transcript}"')

            conv_id = sessions.get_conversation_id()
            response, detected_id = await run_agy_async(transcript, conv_id)
            if detected_id and detected_id != conv_id:
                sessions.set_conversation_id(detected_id)
            sessions.increment_messages()

            stop_typing.set()
            typing_task.cancel()
            formatted_text, parse_mode = format_for_telegram(response)
            for chunk in split_message(formatted_text):
                await safe_send(context.bot, chat_id, chunk, parse_mode=parse_mode)
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
            await update.message.reply_text(f'🎵 Transcribed:\n"{transcript}"')
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
        auto_describe = settings.get("auto_describe_photos", True)

        if caption:
            # User included a caption — use it as the question
            prompt = (
                f"The user sent this image: {filepath}. "
                f"Their question: {caption}. "
                "Look at the image and respond."
            )
        elif auto_describe:
            # Auto-describe the image
            prompt = (
                f"The user sent this image: {filepath}. "
                "Describe what you see in detail."
            )
        else:
            # Just save, no analysis
            stop_typing.set()
            typing_task.cancel()
            await update.message.reply_text(
                f"📸 Photo saved!\n{os.path.basename(filepath)} "
                f"({format_file_size(os.path.getsize(filepath))})\n"
                "Add a caption to get analysis."
            )
            return

        conv_id = sessions.get_conversation_id()
        response, detected_id = await run_agy_async(prompt, conv_id)
        if detected_id and detected_id != conv_id:
            sessions.set_conversation_id(detected_id)
        sessions.increment_messages()

        stop_typing.set()
        typing_task.cancel()
        formatted_text, parse_mode = format_for_telegram(response)
        for chunk in split_message(formatted_text):
            await safe_send(context.bot, chat_id, chunk, parse_mode=parse_mode)

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
    Now with real-time progress via transcript polling.
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

    # Auto-title the session from the first message
    if sessions.get_session_info() and sessions.get_session_info().get("messages", 0) == 0:
        sessions.auto_title(user_message)

    # Start typing indicator
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    try:
        # Get timeout from settings
        timeout = settings.get_timeout()

        # Direct pass-through — agy does ALL the thinking
        response, detected_id = await run_agy_async(
            user_message,
            conv_id,
            timeout=timeout,
            progress_callback=None,
        )

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

    # Send response
    formatted_text, parse_mode = format_for_telegram(response)
    chunks = split_message(formatted_text)
    for i, chunk in enumerate(chunks):
        try:
            await safe_send(context.bot, chat_id, chunk, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"Send chunk {i + 1} failed: {e}")

    # Check for file paths in the response — auto-send files
    file_paths = detect_file_paths(response)
    for fp in file_paths[:10]:  # Max 10 files per response (acts as a send queue)
        await safe_send_file(context.bot, chat_id, fp)





# ══════════════════════════════════════════════════════════
#  CALLBACK HANDLER (Master Router)
# ══════════════════════════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route all callback queries to the appropriate handler."""
    query = update.callback_query
    data = query.data

    try:
        if data.startswith("menu_"):
            await _handle_menu_callback(update, context)
        elif data.startswith("sess_"):
            await _handle_sessions_callback(update, context)
        elif data.startswith("model_"):
            await _handle_model_callback(update, context)
        elif data.startswith("agt_"):
            await _handle_agents_callback(update, context)
        elif data.startswith("set_"):
            await _handle_settings_callback(update, context)
        elif data.startswith("skill_"):
            await _handle_skills_callback(update, context)
        elif data.startswith("err_"):
            await _handle_error_callback(update, context)
        elif data.startswith("svc_"):
            await _handle_service_callback(update, context)
        else:
            await query.answer("Unknown action")
    except Exception as e:
        logger.error(f"Callback error: {e}", exc_info=True)
        await query.answer(f"Error: {str(e)[:100]}")


async def _handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.replace("menu_", "")

    if action == "sessions":
        from ui_buttons import build_sessions_list_keyboard
        all_sessions = sessions.list_sessions()
        active = sessions.active_name
        keyboard = build_sessions_list_keyboard(all_sessions, active)
        lines = [f"📁 Sessions ({len(all_sessions)})\n════════════\n"]
        for name, info in all_sessions.items():
            marker = " ◀" if name == active else ""
            msgs = info.get("messages", 0)
            title = info.get("title", "")
            title_str = f' — "{title}"' if title else ""
            lines.append(f"  {name}{marker}{title_str} — {msgs} msgs")
        await query.edit_message_text("\n".join(lines), reply_markup=keyboard)

    elif action == "model":
        from ui_buttons import build_model_keyboard
        current = settings.get_model()
        keyboard = build_model_keyboard(current)
        await query.edit_message_text(
            f"🤖 AI Model Selection\n═══════════════════════\nCurrent: {current}\n",
            reply_markup=keyboard,
        )

    elif action == "agents":
        from ui_buttons import build_agents_list_keyboard
        running = agents.list_running()
        done = agents.list_done()
        lines = ["🤖 Sub-Agents\n══════════════\n"]
        if running:
            lines.append("🟢 Running:")
            for a in running:
                lines.append(f'  #{a.id} "{a.title}" — {a.elapsed_str()}')
        if done:
            lines.append("\n✅ Done:")
            for a in done[-5:]:
                lines.append(f'  #{a.id} "{a.title}"')
        if not running and not done:
            lines.append("No agents.")
        keyboard = build_agents_list_keyboard(running, done)
        await query.edit_message_text("\n".join(lines), reply_markup=keyboard)

    elif action == "settings":
        from ui_buttons import build_settings_keyboard
        keyboard = build_settings_keyboard(settings)
        await query.edit_message_text("⚙️ Settings\n═══════════", reply_markup=keyboard)

    elif action == "skills":
        from ui_buttons import build_skills_list_keyboard
        skill_list = skills.list_skills()
        keyboard = build_skills_list_keyboard(skill_list)
        await query.edit_message_text(
            f"🔧 Installed Skills ({len(skill_list)})\n════════════════════",
            reply_markup=keyboard,
        )

    elif action == "brain":
        stats = get_brain_stats()
        inbox = stats["inbox"]
        knowledge = stats["knowledge"]
        from ui_buttons import build_back_to_menu_keyboard
        keyboard = build_back_to_menu_keyboard()
        await query.edit_message_text(
            "🧠 AGI Brain\n════════════\n\n"
            f"📥 Inbox: {inbox['images']}img {inbox['audio']}aud "
            f"{inbox['documents']}doc {inbox['telegram']}tg\n\n"
            f"📚 Knowledge: {knowledge['notes']}notes "
            f"{knowledge['transcripts']}trans "
            f"{knowledge['summaries']}sum {knowledge['research']}res\n\n"
            f"📁 Projects: {stats['projects']}",
            reply_markup=keyboard,
        )

    elif action == "inbox":
        pending = get_inbox_pending()
        from ui_buttons import build_back_to_menu_keyboard
        keyboard = build_back_to_menu_keyboard()
        if not pending:
            await query.edit_message_text("📥 Inbox empty!", reply_markup=keyboard)
        else:
            lines = [f"📥 Inbox: {len(pending)} items\n"]
            for item in pending[:15]:
                size = format_file_size(item["size"])
                lines.append(f"  [{item['category']}] {item['name']} ({size})")
            if len(pending) > 15:
                lines.append(f"  ... +{len(pending) - 15} more")
            await query.edit_message_text("\n".join(lines), reply_markup=keyboard)

    elif action == "status":
        model = settings.get_model()
        session_count = len(sessions.list_sessions())
        running_agents = len(agents.list_running())
        from ui_buttons import build_back_to_menu_keyboard
        keyboard = build_back_to_menu_keyboard()
        await query.edit_message_text(
            "🖥️ Bot Status\n═══════════════\n\n"
            f"⏱️ Uptime: {get_uptime_str()}\n"
            f"🤖 Model: {model}\n"
            f"📁 Sessions: {session_count}\n"
            f"🤖 Agents: {running_agents} running\n"
            f"🔧 Version: v{BOT_VERSION}",
            reply_markup=keyboard,
        )

    elif action == "web":
        from ui_buttons import build_back_to_menu_keyboard
        keyboard = build_back_to_menu_keyboard()
        await query.edit_message_text(
            "🔎 Web Search\n═══════════════\n\n"
            "Use /web <query> to search the web.",
            reply_markup=keyboard,
        )

    elif action == "note":
        from ui_buttons import build_back_to_menu_keyboard
        keyboard = build_back_to_menu_keyboard()
        await query.edit_message_text(
            "📝 Quick Note\n═══════════════\n\n"
            "Use /note <text> to save a note.",
            reply_markup=keyboard,
        )

    elif action == "back":
        from ui_buttons import build_menu_keyboard
        keyboard = build_menu_keyboard()
        await query.edit_message_text(
            "🧠 AGI Brain — Control Panel\n═════════════════════════════",
            reply_markup=keyboard,
        )


async def _handle_sessions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "sess_list":
        from ui_buttons import build_sessions_list_keyboard
        all_sessions = sessions.list_sessions()
        active = sessions.active_name
        keyboard = build_sessions_list_keyboard(all_sessions, active)
        lines = [f"📁 Sessions ({len(all_sessions)})\n"]
        for name, info in all_sessions.items():
            marker = " ◀" if name == active else ""
            msgs = info.get("messages", 0)
            lines.append(f"  {name}{marker} — {msgs} msgs")
        await query.edit_message_text("\n".join(lines), reply_markup=keyboard)

    elif data.startswith("sess_switch_"):
        name = data.replace("sess_switch_", "")
        sessions.active_name = name
        await query.edit_message_text(f"✅ Switched to [{name}].")

    elif data.startswith("sess_delete_"):
        name = data.replace("sess_delete_", "")
        from ui_buttons import build_session_delete_confirm_keyboard
        keyboard = build_session_delete_confirm_keyboard(name)
        await query.edit_message_text(
            f"🗑️ Delete session [{name}]?\n\nThis cannot be undone.",
            reply_markup=keyboard,
        )

    elif data.startswith("sess_confirm_del_"):
        name = data.replace("sess_confirm_del_", "")
        sessions.delete_session(name)
        await query.edit_message_text(f"🗑️ Session [{name}] deleted.\nActive: [{sessions.active_name}]")

    elif data == "sess_cancel_del":
        from ui_buttons import build_sessions_list_keyboard
        all_sessions = sessions.list_sessions()
        active = sessions.active_name
        keyboard = build_sessions_list_keyboard(all_sessions, active)
        await query.edit_message_text("📁 Sessions", reply_markup=keyboard)

    elif data == "sess_new":
        existing = sessions.list_sessions()
        i = 1
        while True:
            name = f"session-{i}"
            if name not in existing:
                break
            i += 1
        sessions.create_session(name)
        await query.edit_message_text(f"📁 New session [{name}] created!\nNext message starts fresh.")

    elif data.startswith("sess_detail_"):
        name = data.replace("sess_detail_", "")
        info = sessions.get_session_info(name)
        if info:
            from ui_buttons import build_session_detail_keyboard
            keyboard = build_session_detail_keyboard(name)
            title = info.get("title") or "(no title)"
            await query.edit_message_text(
                f"📁 Session: {name}\n═══════════════════\n\n"
                f'Title: "{title}"\n'
                f"Created: {info.get('created', 'unknown')}\n"
                f"Messages: {info.get('messages', 0)}\n"
                f"Last used: {info.get('last_used', 'never')}\n"
                f"Active: {'Yes ◀' if info.get('is_active') else 'No'}",
                reply_markup=keyboard,
            )


async def _handle_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    model_id = query.data.replace("model_", "")
    settings.set_model(model_id)

    # Also write to selected_model.txt for backward compat
    model_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selected_model.txt")
    try:
        with open(model_file, "w", encoding="utf-8") as f:
            f.write(model_id)
    except Exception:
        pass

    await query.edit_message_text(
        f"✅ Model switched to: {model_id}\n\n"
        "The next message will use this engine."
    )


async def _handle_agents_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "agt_list":
        from ui_buttons import build_agents_list_keyboard
        running = agents.list_running()
        done = agents.list_done()
        keyboard = build_agents_list_keyboard(running, done)
        lines = ["🤖 Sub-Agents\n"]
        if running:
            for a in running:
                lines.append(f'🟢 #{a.id} "{a.title}" — {a.elapsed_str()}')
        if done:
            for a in done[-5:]:
                lines.append(f'✅ #{a.id} "{a.title}"')
        if not running and not done:
            lines.append("No agents.")
        await query.edit_message_text("\n".join(lines), reply_markup=keyboard)

    elif data.startswith("agt_output_"):
        agent_id = data.replace("agt_output_", "")
        agent = agents.get(agent_id)
        if agent and agent.result:
            chunks = split_message(f"📄 Agent #{agent_id} Output:\n{'═' * 25}\n\n{agent.result}")
            for chunk in chunks:
                await safe_send(context.bot, query.message.chat_id, chunk)
        else:
            await query.edit_message_text(f"No output available for agent #{agent_id}.")

    elif data.startswith("agt_stop_"):
        agent_id = data.replace("agt_stop_", "")
        agents.stop(agent_id)
        await query.edit_message_text(f"🛑 Agent #{agent_id} stopped.")

    elif data == "agt_clear":
        count = agents.clear_done()
        await query.edit_message_text(f"🗑️ Cleared {count} completed agents.")

    elif data == "agt_launch":
        await query.edit_message_text(
            "🚀 Launch Agent\n\n"
            "Use /sub <task> to launch a new agent.\n"
            "Example: /sub Research quantum computing papers"
        )


async def _handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ui_buttons import handle_settings_callback
    await handle_settings_callback(update, context)


async def _handle_skills_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ui_buttons import handle_skills_callback
    await handle_skills_callback(update, context)


async def _handle_error_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "err_retry":
        await query.edit_message_text("🔄 Retrying... Send your message again.")
    elif data == "err_model":
        from ui_buttons import build_model_keyboard
        current = settings.get_model()
        keyboard = build_model_keyboard(current)
        await query.edit_message_text(
            f"🤖 Try a different model?\nCurrent: {current}",
            reply_markup=keyboard,
        )
    elif data == "err_cancel":
        await query.edit_message_text("❌ Cancelled.")


async def _handle_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Service-related callbacks handled here if needed


# ══════════════════════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Bot error: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        try:
            from ui_buttons import build_error_keyboard
            keyboard = build_error_keyboard()
            await update.message.reply_text(
                "⚠ An error occurred.",
                reply_markup=keyboard,
            )
        except Exception:
            try:
                await update.message.reply_text("⚠ An error occurred. Check bot logs.")
            except Exception:
                pass


# ══════════════════════════════════════════════════════════
#  STARTUP NOTIFICATION
# ══════════════════════════════════════════════════════════

async def post_init(application):
    """Send startup notification to owner."""
    if OWNER_CHAT_ID:
        try:
            model = settings.get_model()
            session_count = len(sessions.list_sessions())
            skill_count = len(skills.list_skills())
            await application.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=(
                    f"🟢 AGY Bot is online! (v{BOT_VERSION})\n"
                    f"Model: {model}\n"
                    f"Sessions: {session_count}\n"
                    f"Skills: {skill_count}\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                ),
            )
            logger.info("[STARTUP] Notification sent to owner.")
        except Exception as e:
            logger.warning(f"[STARTUP] Failed to notify owner: {e}")

    # Inject global managers into bot_data so callbacks can access them
    application.bot_data["settings_manager"] = settings
    application.bot_data["skills_manager"] = skills
    application.bot_data["agent_manager"] = agents


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

def main():
    global sessions, settings, agents, skills

    # 1. ENFORCE SINGLE INSTANCE (Kill Switch mechanism)
    if sys.platform == "win32":
        import msvcrt
        global _lock_file_handle
        try:
            _lock_file_handle = open("agy_bot_instance.lock", "w")
            msvcrt.locking(_lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("❌ ERROR: Another instance of the bot is already running!")
            print("Please close it before starting a new one. (Use Task Manager to kill pythonw.exe if hidden).")
            sys.exit(1)

    # Fix Windows console encoding
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Ensure AGI-Brain directory structure exists
    ensure_brain_structure()

    # Initialize managers
    from config import STATE_FILE
    sessions = SessionManager(STATE_FILE)
    settings = SettingsManager(SETTINGS_FILE)
    agents = AgentManager(AGENTS_FILE, MAX_CONCURRENT_AGENTS)
    skills = SkillsManager(SKILLS_DIR)

    active = sessions.active_name
    session_count = len(sessions.list_sessions())
    stats = get_brain_stats()
    audio_ok = is_audio_capable()
    model = settings.get_model()
    skill_count = len(skills.list_skills())

    print("=" * 55)
    print(f"  [MOTHER]  AGI Brain — Telegram Bot v{BOT_VERSION}")
    print("  Direct Pass-Through — agy does the thinking")
    print("=" * 55)
    print(f"  Active Session:  [{active}]")
    print(f"  Total Sessions:  {session_count}")
    print(f"  User ID:         {ALLOWED_USER_ID}")
    print(f"  Model:           {model}")
    print(f"  AGI-Brain:       {AGI_BRAIN_DIR}")
    print(f"  Audio:           {'Ready' if audio_ok else 'N/A'}")
    print(f"  Inbox Items:     {sum(stats['inbox'].values())}")
    print(f"  Knowledge:       {sum(stats['knowledge'].values())}")
    print(f"  Skills:          {skill_count}")
    print(f"  Timeout:         {settings.get_timeout()}s")
    print("  Bot starting... Press Ctrl+C to stop.")
    print()

    # Build bot
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("switch", cmd_switch))
    app.add_handler(CommandHandler("end", cmd_end))
    app.add_handler(CommandHandler("sub", cmd_sub))
    app.add_handler(CommandHandler("agents", cmd_agents))
    app.add_handler(CommandHandler("brain", cmd_brain))
    app.add_handler(CommandHandler("inbox", cmd_inbox))
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("web", cmd_web))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("skills", cmd_skills))
    app.add_handler(CommandHandler("service", cmd_service))

    # Callback handler — routes ALL button presses
    app.add_handler(CallbackQueryHandler(handle_callback))

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
