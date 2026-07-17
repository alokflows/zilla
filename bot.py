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
import time
import json
from contextlib import aclosing
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
import zilla.core as zcore
from zilla.cli_engine import detect_limit, backend_status
from media import (
    is_audio_capable, get_audio_status, transcribe_audio,
    save_photo, save_voice, save_audio, save_document, save_video,
    get_inbox_stats, get_inbox_items, get_inbox_counts, delete_inbox_file,
    get_outbox_items, get_outbox_counts, delete_outbox_file,
    format_file_size, extract_text,
)
from formatter import format_for_telegram, detect_file_paths
from harness import log_summary
from users import AuthManager
from schedules import ScheduleManager, describe as describe_schedule
from schedule_parse import parse_schedule, parse_schedule_command
import keyboards
from keyboards import (
    kb_menu, kb_sessions, kb_session_delete, kb_model, kb_settings,
    kb_back, kb_error, kb_users, kb_user_detail,
    kb_inbox_categories, kb_inbox_list, kb_outbox_categories, kb_outbox_list,
    kb_schedules, _can_change_model, _fmt_next,
    _IDLE_OPTIONS, INBOX_PAGE, INBOX_CAT_META, OUTBOX_CAT_META,
)

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


def _prune_old_logs(max_age_days: int = 30) -> None:
    """Delete daily log files older than max_age_days. Logs carry no secrets
    (the token is redacted) but do accumulate chat/user/conversation ids, so we
    don't keep them forever. Best-effort; never fatal."""
    try:
        if not os.path.isdir(LOG_DIR):
            return
        cutoff = time.time() - max_age_days * 86400
        for name in os.listdir(LOG_DIR):
            if not (name.startswith("bot_") and name.endswith(".log")):
                continue
            fp = os.path.join(LOG_DIR, name)
            try:
                if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
            except OSError:
                pass
    except OSError:
        pass


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
# The interface-agnostic core (zilla/core.py) — owns the turn pipeline,
# per-user CLI locks, cancel events, and (this seam) the scheduler runtime.
# Created in main().
core: zcore.ZillaCore = None
# Telegram Application handle, set in post_init — the scheduler-event renderers
# (_deliver_scheduled_result/_deliver_alert) and the screenshot fast path need
# bot.send_* but core.py's background broadcast carries no application/bot
# reference (interface-agnostic by design), so bot.py keeps its own.
_application = None
# The core's background-event consumer task (started in post_init, cancelled
# in post_shutdown) and the queue it reads from.
_core_events_queue: asyncio.Queue = None
_core_events_task_handle: asyncio.Task = None

# Per-chat id of the CURRENT live menu message. When a new menu opens we strip
# the previous one's buttons so old menus in the chat history can't be tapped
# again (no stale session/menu collisions). The ✕ Close button clears it too.
_active_menu: dict[int, int] = {}

# ── Floodguard ─────────────────────────────────────────────
# Per-user turns already serialize (one CLI run at a time via the lock above),
# so this only stops a *burst* — a runaway script or a compromised client firing
# hundreds of messages. Generous enough that a fast human never trips it.
_RATE_WINDOW = 10.0     # seconds
_RATE_MAX = 8           # messages allowed per window
_user_msg_times: dict[int, list[float]] = {}
_rate_notice_at: dict[int, float] = {}


def _rate_ok(uid: int) -> bool:
    """True if this user is under the burst limit; records the message if so."""
    now = time.monotonic()
    times = [t for t in _user_msg_times.get(uid, ()) if now - t <= _RATE_WINDOW]
    if len(times) >= _RATE_MAX:
        _user_msg_times[uid] = times
        return False
    times.append(now)
    _user_msg_times[uid] = times
    return True


def _rate_should_notify(uid: int, min_gap: float = _RATE_WINDOW) -> bool:
    """Throttle the 'slow down' notice so we don't spam it during a flood."""
    now = time.monotonic()
    if now - _rate_notice_at.get(uid, 0.0) < min_gap:
        return False
    _rate_notice_at[uid] = now
    return True


async def _notify_if_busy(uid: int, update: Update) -> None:
    """If a previous message is still running (the core's per-user lock is
    held), send one calm heads-up so the new message doesn't feel ignored
    while it waits its turn."""
    if core.is_busy(uid) and update is not None:
        try:
            await update.effective_message.reply_text(
                "⏳ One sec — finishing your previous message first, then I'll get to this."
            )
        except Exception:
            pass


async def _relay_cli_turn(update, uid, chat_id, prompt, *,
                          auto_title=False, skip_permissions=None) -> str:
    """Drive one core turn (zilla.core.handle_message) and return the final
    response text. Shared by the text, voice, photo, document and approval
    paths — the Telegram translation of the core's event stream.

    Progress events are consumed silently this seam: the visible ⏳ Working
    UI stays time-driven (keep_typing, started by each handler), exactly as
    before the extraction. The Response event's text feeds the existing
    send_response path in each handler."""
    await _notify_if_busy(uid, update)
    response = ""
    # aclosing: if we die mid-stream (Telegram error), close the generator so
    # the core releases its lock/cancel bookkeeping deterministically.
    async with aclosing(core.handle_message(
            uid, prompt, chat_key=chat_id,
            auto_title=auto_title, skip_permissions=skip_permissions)) as stream:
        async for event in stream:
            if isinstance(event, zcore.Response):
                response = event.text
    return response


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
    # Flaky links (hotspot, sleep-wake) time out on a single attempt; the CLI's
    # answer must not be lost to one bad send, so retry with backoff.
    for attempt in range(4):
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
            return
        except Exception as e:
            if attempt == 3:
                logger.error(f"Failed to send message after 4 attempts: {e}")
            else:
                logger.warning(f"send_message attempt {attempt + 1} failed ({e}); retrying")
                await asyncio.sleep(2 * (attempt + 1))


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
#  MENU HELPERS  (keyboard builders live in keyboards.py)
# ══════════════════════════════════════════════════════════

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


# ── File auto-delivery limits ─────────────────────────────
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


# ── Schedules panel text ──────────────────────────────────

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


# ── Approval mode: submit / approve / run ──────────────────
# The HOLD (store, TTL/cap) and the approved-turn execution now live in
# core.approvals (docs/dev/CORE_API.md migration step 5, zilla/core.py) —
# these three functions are the Telegram-only remainder: the immediate ack
# to the requester, the owner-DM keyboard/text (rendered from the
# ApprovalRequest event, see _deliver_approval_request below), and
# delivering the eventual result.

async def _submit_for_approval(update, context, uid: int, chat_id: int, prompt: str):
    """Hold a limited user's request and ask the owner to approve it. Thin
    call into core.approvals.submit — core owns the hold and broadcasts
    ApprovalRequest; _deliver_approval_request renders the owner's DM."""
    prompt = (prompt or "").strip()
    if not prompt:
        return
    name = auth._users.get(uid, {}).get("name") or f"User {uid}"
    rid = core.approvals.submit(uid, chat_id, prompt, name)
    if not rid:
        await safe_send(context.bot, chat_id,
                        "⚠️ Too many requests are waiting for approval right now. "
                        "Please try again in a bit.")
        return
    await safe_send(context.bot, chat_id,
                    "📨 Sent to the owner for approval. I'll post the answer here "
                    "once they approve it.")


async def _cb_approvals(query, context, data, uid, chat_id):
    """Owner-only: approve or deny a held limited-user request. Thin calls
    into core.approvals (docs/dev/CORE_API.md migration step 5) — core owns
    the hold and runs an approved turn through the normal pipeline; this
    renders the Telegram-specific bits (typing indicator, message text,
    result delivery) exactly as bot.py's old _run_approved_request did."""
    if not auth.is_owner(uid):
        return
    if data.startswith("appr_ok_"):
        rid = data.removeprefix("appr_ok_")
        pending = {r["id"]: r for r in core.approvals.pending()}
        preview = pending.get(rid)
        if preview is None:
            await query.edit_message_text("⏳ That request expired or was already handled.")
            return
        await query.edit_message_text(f"✅ Approved — running {preview['name']}'s request…")
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(
            keep_typing(context.bot, preview["chat_id"], stop_typing))
        result = None
        response = ""
        try:
            # The owner vetted the whole request, so let the agent act (skip prompts) —
            # core.approvals.approve() runs it through the SAME pipeline handle_message
            # uses for a live turn (per-user lock, session pinning, I-CONV/I-STEP).
            result = await core.approvals.approve(rid)
            if result is not None:
                response = result["response"]
        except Exception as e:
            response = _friendly_error(e)
            logger.error(f"[APPROVAL] run failed: {e}", exc_info=True)
        finally:
            stop_typing.set()
            typing_task.cancel()
        if result is None and not response:
            return  # popped between the peek and approve() (double-tap race) — nothing to deliver
        target_uid = result["uid"] if result else preview["uid"]
        target_chat = result["chat_id"] if result else preview["chat_id"]
        await send_response(None, context, response, target_uid, target_chat)
        try:
            await safe_send(context.bot, OWNER_CHAT_ID, f"✅ Sent {preview['name']} the result.")
        except Exception:
            pass
    elif data.startswith("appr_no_"):
        req = core.approvals.deny(data.removeprefix("appr_no_"))
        if not req:
            await query.edit_message_text("⏳ That request expired or was already handled.")
            return
        await query.edit_message_text(f"❌ Denied {req['name']}'s request.")
        await safe_send(context.bot, req["chat_id"],
                        "🚫 The owner declined your request.")


def _friendly_error(e: Exception) -> str:
    """Turn an internal exception into a calm, plain-language message for the
    user. The full traceback still goes to the logs — this is just what they see."""
    msg = str(e).lower()
    if "timed out" in msg or "timeout" in msg:
        return ("⏱️ That took too long, so I stopped it. Try again, or break it "
                "into a smaller step.")
    if "not logged in" in msg or "not installed" in msg or "auth" in msg:
        return ("🔌 I couldn't reach the AI on this computer. Make sure your CLI "
                "is installed and logged in (run it once in a terminal), then try "
                "again. /help has the setup steps.")
    return ("⚠️ Something went wrong while running that. Please try again — if it "
            "keeps happening, check your AI CLI is set up and logged in (/help).")


async def _block_media_for_limited(update, context) -> bool:
    """Limited users route through text approval; politely refuse media for now."""
    if auth.is_limited(update.effective_user.id):
        try:
            await update.message.reply_text(
                "🔒 You're in Approval mode — please send your request as text so "
                "the owner can approve it. (Media isn't supported yet.)")
        except Exception:
            pass
        return True
    return False


# ══════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # First time the owner ever runs /start: a warm, plain-language welcome
    # instead of the status dashboard. Shown once, then we flip a flag.
    if auth.is_owner(uid) and not get_setting("owner_welcomed", False):
        set_setting("owner_welcomed", True)
        await update.message.reply_text(
            "👋 Welcome to Zilla — you're the owner.\n"
            "══════════════════════\n\n"
            "I connect this Telegram chat to the AI on your computer. "
            "Just type what you want in plain English and I'll do it.\n\n"
            "Try one now, for example:\n"
            "  • “what files are on my desktop?”\n"
            "  • “summarise the PDF I'm about to send”\n\n"
            "A few things to know:\n"
            "  • /menu — buttons for sessions, model, settings, schedules\n"
            "  • You can send voice notes, photos and files too.\n"
            "  • Adding people: anyone you add as *admin* can run anything on this "
            "computer — only do that for people you fully trust. Not sure? Add them "
            "in *Approval mode* so every request waits for your ✅.\n\n"
            "Send /help anytime. Go ahead — type your first message. 🚀"
        )
        return

    active = sessions.get_active_name(uid)
    conv_id = sessions.get_conversation_id(user_id=uid)
    session_count = len(sessions.list_sessions(uid))
    model = get_model()
    inbox = get_inbox_stats()
    role = "owner" if auth.is_owner(uid) else auth._users.get(uid, {}).get("role", "admin")

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
    if core.cancel(chat_id):
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
    """One-off reminders are created instantly, Siri-style (owner decree
    2026-07-17: asking permission for a 2-minute timer is worse than useless).
    Only RECURRING schedules — which keep firing until removed — get a
    confirm step."""
    if parsed["kind"] == "once":
        uid = update.effective_user.id
        chat_id = update.effective_chat.id
        _make_schedule(uid, chat_id, parsed)
        await update.effective_message.reply_text(
            f"⏰ {describe_schedule(parsed['kind'], parsed['spec'])} — "
            f"{parsed['title']} ✓"
        )
        return
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
    # Pin the backend+model active at creation time (HANDOFF P1 scheduler-seam
    # item 3) — see core._maybe_notify_backend_pin for what happens if it has
    # drifted by fire time. session defaults to "isolated": every schedule
    # made through this path always has, and keeps having, a fresh
    # conversation each run.
    payload_type = parsed.get("payload_hint", "message")
    prompt = parsed["prompt"]
    if payload_type == "system_event":
        # Delivered verbatim at fire time (no model call) — phrase it as the
        # reminder the user will read, not as an instruction.
        prompt = f"Reminder: {parsed['title']}"
    return schedules_mgr.add(
        user_id=uid, chat_id=chat_id, prompt=prompt,
        kind=parsed["kind"], spec=parsed["spec"], title=parsed["title"],
        payload_type=payload_type,
        backend=get_backend(), model=get_model(),
        is_owner=auth.is_owner(uid) if auth else False,
    )


# ── Scheduler runtime ──
#
#  The tick loop, retry/self-heal semantics and ScheduleManager mutation live
#  in zilla/core.py now (CORE_API migration step 3) — bot.py just renders the
#  ScheduledResult/Alert events the core broadcasts, and supplies the one
#  Telegram-specific fast path (screenshot-via-bridge) as core.schedule_pre_run.

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


async def _screenshot_via_bridge(s: dict) -> tuple[bool, str, str]:
    """Take a screenshot through KimiWebBridge directly (the path that actually
    works) and stash it in the Outbox, so delivery + the send allowlist pass.
    Returns the (ok, response, detail) triple core.schedule_pre_run promises."""
    import shutil
    if _application is not None:
        try:
            await _application.bot.send_chat_action(chat_id=s["chat_id"], action=ChatAction.TYPING)
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


async def _schedule_pre_run_hook(s: dict):
    """Wired to core.schedule_pre_run — the only frontend-specific fast path a
    'message'-payload schedule needs today. None means 'no special-case, run
    the schedule normally'."""
    if _is_simple_screenshot(s.get("prompt", "")):
        return await _screenshot_via_bridge(s)
    return None


async def _send_screenshot_now(update, context, uid: int, chat_id: int) -> bool:
    """On-demand 'send me a screenshot': take it through the fast bridge and
    deliver it, skipping the agent (which can spin for minutes on this).

    Returns True if a screenshot was delivered. On any bridge failure it returns
    False WITHOUT replying, so the caller can fall back to the normal agent path
    — this fast path is a pure optimization, never a regression.
    """
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))
    try:
        result = await bridge_command("screenshot", {}, timeout=30)
        path = (result.get("data") or {}).get("path", "")
        if path and os.path.isfile(path):
            ok = await safe_send_file(context.bot, chat_id, path,
                                      caption="📸 Screenshot", user_id=uid)
            return bool(ok)
        return False
    except Exception as e:
        logger.info(f"[SHOT] on-demand bridge failed, falling back to agent: {e}")
        return False
    finally:
        stop_typing.set()
        typing_task.cancel()


async def _deliver_scheduled_result(ev) -> None:
    """Render a core.ScheduledResult event exactly as the old in-bot.py
    scheduler used to: '⏰ Scheduled — <title>' + response, the rate-limit
    model-switch helper, and any files the job produced. ev.warning (set only
    on a give-up-after-retries occurrence) is sent first, same ordering as the
    old two-message sequence."""
    if _application is None:
        return
    bot = _application.bot
    chat_id, uid = ev.chat_id, ev.user_id
    if ev.warning:
        try:
            await safe_send(bot, chat_id, ev.warning, parse_mode="HTML")
        except Exception:
            pass
        if not (ev.response and ev.response.strip()):
            return  # gave-up-with-no-partial-output: warning is the whole message
    response = ev.response
    header = f"⏰ Scheduled — {ev.title}\n"
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
        # Attach any files the job produced.
        if auth and auth.can(uid, "admin"):
            paths = _fresh_files(detect_file_paths(response or ""))
            sent = 0
            for fp in paths[:MAX_AUTO_DELIVER]:
                if await safe_send_file(bot, chat_id, fp, user_id=uid):
                    sent += 1
            if sent:
                await safe_send(bot, chat_id, f"📎 {sent} file(s) delivered.")
    except Exception as e:
        logger.error(f"[SCHED] deliver {ev.schedule_id} failed: {e}")


async def _deliver_alert(ev) -> None:
    """Render a core.Alert event — owner-scoped operational notes (currently:
    the one-time backend/model pin-mismatch note, HANDOFF P1 item 3)."""
    if _application is None or not OWNER_CHAT_ID:
        return
    try:
        await safe_send(_application.bot, OWNER_CHAT_ID, f"ℹ️ {ev.text}", parse_mode=None)
    except Exception as e:
        logger.error(f"[SCHED] alert deliver failed: {e}")


_BRIDGE_KIND_LABEL = {"otp": "🔐 One-time code", "password": "🔑 Password",
                      "text": "✍️ Input needed", "confirm": "❓ Please confirm"}


async def _deliver_ask(ev) -> None:
    """Render a core.Ask event — the human-in-the-loop credential/OTP bridge
    (docs/dev/CORE_API.md migration step 4). DMs ev.chat_id the same message
    the old in-bot.py bridge_watcher used to; the reply is captured back in
    handle_message via core.pending_ask_for()/core.answer_ask()."""
    if _application is None:
        return
    hint = "\n\n<i>Reply with the value — used once.</i>" if ev.is_secret else ""
    try:
        await safe_send(
            _application.bot, ev.chat_id,
            f"{_BRIDGE_KIND_LABEL.get(ev.kind, '✍️ Input needed')}\n\n"
            f"{format_for_telegram(ev.prompt)[0]}{hint}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"[BRIDGE] could not DM ask {ev.id}: {e}")


async def _deliver_approval_request(ev) -> None:
    """Render a core.ApprovalRequest event — DM the owner the same
    'Approval needed' card with ✅/❌ buttons bot.py's old
    _submit_for_approval used to send directly (docs/dev/CORE_API.md
    migration step 5). The approve()/deny() calls that resolve ev.id live
    in _cb_approvals."""
    if _application is None or not OWNER_CHAT_ID:
        return
    preview = ev.prompt if len(ev.prompt) <= 500 else ev.prompt[:500] + "…"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve & run", callback_data=f"appr_ok_{ev.id}"),
        InlineKeyboardButton("❌ Deny", callback_data=f"appr_no_{ev.id}"),
    ]])
    try:
        await _application.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=(f"🔔 Approval needed\n\n"
                  f"{ev.name} (limited) wants to run:\n\n"
                  f"“{preview}”\n\n"
                  f"Approving runs it on THIS computer and sends them the result."),
            reply_markup=kb,
        )
    except Exception as e:
        logger.error(f"[APPROVAL] could not notify owner: {e}")


async def _core_events_task(core: "zcore.ZillaCore", sink: asyncio.Queue) -> None:
    """Consume core's background event broadcast and render each event the
    way the old in-bot.py scheduler/bridge_watcher/approval flow used to.
    Runs for the app's lifetime; cancelled in post_shutdown."""
    while True:
        ev = await sink.get()
        try:
            if isinstance(ev, zcore.ScheduledResult):
                await _deliver_scheduled_result(ev)
            elif isinstance(ev, zcore.Alert):
                await _deliver_alert(ev)
            elif isinstance(ev, zcore.Ask):
                await _deliver_ask(ev)
            elif isinstance(ev, zcore.ApprovalRequest):
                await _deliver_approval_request(ev)
        except Exception as e:
            logger.error(f"[SCHED] event render failed: {e}", exc_info=True)


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
    if new_id in auth.list_users():
        await update.message.reply_text(f"User {new_id} already exists.")
        return
    # Don't add on the spot — require an explicit YES after the danger warning
    # (handled by _handle_adduser_flow's awaiting_confirm step).
    context.user_data["adduser_flow"] = {
        "step": "awaiting_confirm", "target_id": new_id, "name": name,
    }
    await update.message.reply_text(_adduser_warning(name or f"ID {new_id}"))


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
        role = info.get("role", "admin")
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

# _run_cli_turn moved to zilla/core.py (ZillaCore.handle_message) — the
# handlers below drive it through _relay_cli_turn.


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _block_media_for_limited(update, context):
        return
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
            await update.message.reply_text(f'🎤 "{transcript}"')
            response = await _relay_cli_turn(
                update, uid, chat_id, transcript, auto_title=True)
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


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _block_media_for_limited(update, context):
        return
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
    if await _block_media_for_limited(update, context):
        return
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    caption = update.message.caption or ""
    stop_typing = asyncio.Event()
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

        response = await _relay_cli_turn(update, uid, chat_id, prompt)
        stop_typing.set()
        typing_task.cancel()
        await send_response(update, context, response, uid, chat_id)
    except Exception as e:
        stop_typing.set()
        typing_task.cancel()
        logger.error(f"Photo error: {e}", exc_info=True)
        await update.message.reply_text(f"Photo error: {str(e)[:200]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save document. If caption provided, analyze it via CLI."""
    if await _block_media_for_limited(update, context):
        return
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
        typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

        try:
            extracted = await asyncio.to_thread(extract_text, filepath)
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

            response = await _relay_cli_turn(update, uid, chat_id, prompt)
            stop_typing.set()
            typing_task.cancel()
            await send_response(update, context, response, uid, chat_id)
        except Exception as e:
            stop_typing.set()
            typing_task.cancel()
            logger.error(f"Document analysis error: {e}", exc_info=True)
            await update.message.reply_text(f"Analysis error: {str(e)[:200]}")

    except Exception as e:
        logger.error(f"Document save error: {e}", exc_info=True)
        await update.message.reply_text(f"Document error: {str(e)[:200]}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _block_media_for_limited(update, context):
        return
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

def _adduser_warning(who: str, role: str = "admin") -> str:
    """The confirmation notice shown before anyone is granted access. For an
    admin this is an unmissable danger warning; for a limited user it explains
    that every request is owner-approved."""
    if role == "limited":
        return (
            f"You're adding {who} in Approval mode.\n\n"
            "They can send requests, but NOTHING runs until you approve each one. "
            "They can't change settings, browse, schedule, or add users.\n\n"
            "Type YES to add them, or /cancel to stop."
        )
    return (
        f"⚠️ Read this before adding {who} with full access.\n\n"
        "This makes them an ADMIN. Through the bot they can run ANY command on "
        "THIS computer, read and change your files, and use apps you're already "
        "logged into — unattended, no approval needed.\n\n"
        "Only do this for someone you'd hand your unlocked laptop to.\n"
        "(For less trust, cancel and choose Approval mode instead.)\n\n"
        "Type YES to add them, or /cancel to stop."
    )


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
        flow["name"] = name
        flow["step"] = "awaiting_role"
        context.user_data["adduser_flow"] = flow
        await update.message.reply_text(
            "What access should they have?\n\n"
            "1 — Full access (admin): full control, unattended. Only for people "
            "you fully trust.\n"
            "2 — Approval mode (limited): they can chat, but you approve every "
            "request first.\n\n"
            "Send 1 or 2 (or /cancel)."
        )

    elif step == "awaiting_role":
        choice = text.strip().lower()
        if choice in ("1", "admin", "full"):
            role = "admin"
        elif choice in ("2", "limited", "approval"):
            role = "limited"
        else:
            await update.message.reply_text("Please send 1 (full) or 2 (approval), or /cancel.")
            return
        flow["role"] = role
        flow["step"] = "awaiting_confirm"
        context.user_data["adduser_flow"] = flow
        who = flow.get("name") or f"ID {flow.get('target_id')}"
        await update.message.reply_text(_adduser_warning(who, role))

    elif step == "awaiting_confirm":
        target_id = flow.get("target_id")
        name = flow.get("name", "")
        role = flow.get("role", "admin")
        context.user_data.pop("adduser_flow", None)
        if text.strip().upper() != "YES":
            await update.message.reply_text(
                "Add-user canceled — nobody was added.",
                reply_markup=kb_users(auth.list_users()),
            )
            return
        if target_id and auth.add_user(target_id, name, role):
            if role == "limited":
                blurb = ("in Approval mode — they can chat, but every request "
                         "waits for your approval.")
            else:
                blurb = ("with full access (chat, sessions, media, files). "
                         "Only you (owner) can manage users.")
            await update.message.reply_text(
                f"✅ Added {name or target_id} as [{role}] — {blurb}",
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

    # Floodguard: drop bursts (runaway script / compromised client). A human
    # never trips 8 msgs / 10s; when tripped, say so at most once per window.
    if not _rate_ok(uid):
        if _rate_should_notify(uid):
            await safe_send(context.bot, chat_id,
                            "🐢 Slow down a moment — too many messages at once. "
                            "I'll catch up; try again in a few seconds.")
        return

    # Human-in-the-loop bridge: if the agent asked this chat for an OTP / phone /
    # password / confirmation, this message IS the answer — hand it back to the
    # waiting CLI turn instead of starting a new one. core.pending_ask_for()
    # also handles TTL release: a stale (orphaned) ask returns None here and
    # the message falls through to be processed as a normal turn.
    pending = core.pending_ask_for(chat_id)
    if pending:
        ask_id, is_secret = pending
        text = update.message.text or ""
        try:
            core.answer_ask(ask_id, text)
            if is_secret:
                # An OTP / password must not linger in the chat. In a private
                # chat a bot may delete the user's own incoming message; if
                # that's ever refused, tell them to remove it themselves.
                try:
                    await update.message.delete()
                    await safe_send(
                        context.bot, chat_id,
                        "✅ Got it — continuing. I removed that message so the "
                        "code isn't left in the chat.")
                except Exception:
                    await update.message.reply_text(
                        "✅ Got it — continuing.\n"
                        "⚠️ Please delete your last message — it holds a "
                        "sensitive value and I wasn't allowed to remove it.")
            else:
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

    # Approval mode: a limited user's request is held for the owner to approve —
    # nothing runs until they tap Approve.
    if auth.is_limited(uid):
        await _submit_for_approval(update, context, uid, chat_id, user_message or "")
        return

    # Natural-language schedule? Offer to create it instead of running it now.
    # Recursion guard (HANDOFF P1 scheduler-seam item 7): a turn executed BY a
    # schedule must not be able to create MORE schedules via this NL path.
    if auth.can(uid, "admin") and not core.is_scheduled_run(uid):
        parsed = parse_schedule(user_message or "")
        if parsed:
            await _offer_schedule(update, context, parsed)
            return

        # Bare "send me a screenshot": route to the fast bridge instead of the
        # agent, which can spin for minutes on this. Falls back to the agent if
        # the bridge isn't reachable, so it never makes things worse.
        if _is_simple_screenshot(user_message or ""):
            if await _send_screenshot_now(update, context, uid, chat_id):
                return

    logger.info(f"Message in [{sessions.get_active_name(uid)}]")

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_typing))

    # Bind before the try so a BaseException (e.g. asyncio.CancelledError on
    # shutdown, which `except Exception` does NOT catch) can't leave `response`
    # unbound at the send_response call below.
    response = ""
    try:
        response = await _relay_cli_turn(
            update, uid, chat_id, user_message, auto_title=True)
    except Exception as e:
        response = _friendly_error(e)
        logger.error(f"Handler error: {e}", exc_info=True)
    finally:
        stop_typing.set()
        typing_task.cancel()

    await send_response(update, context, response, uid, chat_id)


# ══════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ──────────────────────────────────────────────────────────
#  handle_callback() is a thin dispatcher: it answers the tap and
#  routes by callback_data prefix to one focused _cb_* helper per
#  feature (menus, sessions, model, settings, inbox, outbox,
#  schedules, users). Each helper owns its own if/elif sub-chain.
# ══════════════════════════════════════════════════════════

async def _cb_misc(query, context, data, uid, chat_id):
    if data == "menu_close":
        _active_menu.pop(chat_id, None)
        try:
            await query.edit_message_text("✓ Closed. Send /menu to reopen.")
        except Exception:
            await query.edit_message_reply_markup(reply_markup=None)
        return

    if data == "menu_back":
        _active_menu[chat_id] = query.message.message_id
        await query.edit_message_text(
            "⚡ Zilla — Control Panel\n════════════════════════",
            reply_markup=kb_menu(uid),
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

    elif data == "cancel_active":
        if core.cancel(chat_id):
            await query.edit_message_text("🛑 Canceling…")
        else:
            await query.edit_message_text("Nothing to cancel.")

    else:
        # Unknown — restore the menu rather than replacing with an error
        await query.edit_message_text(
            "⚡ Zilla — Control Panel\n════════════════════════",
            reply_markup=kb_menu(uid),
        )


async def _cb_sessions(query, context, data, uid, chat_id):
    if data == "menu_sessions":
        all_sessions = sessions.list_sessions(uid)
        active = sessions.get_active_name(uid)
        lines = [f"📁 Sessions ({len(all_sessions)})\n"]
        for name, info in all_sessions.items():
            marker = " ◀" if name == active else ""
            lines.append(f"  {name}{marker} — {info.get('messages', 0)} msgs")
        await query.edit_message_text(
            "\n".join(lines), reply_markup=kb_sessions(all_sessions, active),
        )

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


async def _cb_model(query, context, data, uid, chat_id):
    if data == "menu_model":
        if not _can_change_model(uid):
            await query.answer("Model changes are disabled by the owner.", show_alert=True)
            return
        current = get_model()
        await query.edit_message_text(
            f"🤖 Model Selection\n════════════════\n{await _backend_panel()}\n\n{_model_note()}",
            reply_markup=kb_model(current),
        )

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


async def _cb_settings(query, context, data, uid, chat_id):
    if data == "menu_settings":
        if not auth.can(uid, "admin"):
            await query.answer("Admin access required.", show_alert=True)
            return
        await query.edit_message_text("⚙️ Settings\n═══════════", reply_markup=kb_settings(uid))

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


async def _cb_inbox(query, context, data, uid, chat_id):
    if data == "menu_inbox":
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


async def _cb_outbox(query, context, data, uid, chat_id):
    if data == "menu_outbox":
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


async def _cb_schedules(query, context, data, uid, chat_id):
    if data == "menu_schedules":
        if not auth.can(uid, "admin"):
            await query.answer("Admin access required.", show_alert=True)
            return
        items = schedules_mgr.list(uid)
        await query.edit_message_text(
            _schedule_panel_text(items),
            reply_markup=kb_schedules(items) if items else kb_back(),
        )

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
            asyncio.create_task(core.run_schedule_now(sid))
        else:
            await query.answer("Not found.", show_alert=True)


async def _cb_users(query, context, data, uid, chat_id):
    if data == "menu_users":
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
        role = info.get("role", "admin")
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

    elif data.startswith("user_role_"):
        if not auth.is_owner(uid):
            return
        rest = data.removeprefix("user_role_")   # "admin_<id>" or "limited_<id>"
        new_role, _, tid = rest.partition("_")
        try:
            target_id = int(tid)
        except ValueError:
            return
        auth.set_role(target_id, new_role)
        users = auth.list_users()
        info = users.get(target_id, {})
        name = info.get("name") or f"User {target_id}"
        role = info.get("role", "admin")
        await query.edit_message_text(
            f"👤 {name}\n══════════════════\n"
            f"ID: {target_id}\n"
            f"Role: {role}\n"
            f"Added: {info.get('added_at','unknown')}",
            reply_markup=kb_user_detail(target_id, role),
        )

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


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    try:
        await query.answer()
        if data == "menu_sessions" or data.startswith("sess_"):
            await _cb_sessions(query, context, data, uid, chat_id)
        elif data == "menu_model" or data.startswith("model_") or data.startswith("err_"):
            await _cb_model(query, context, data, uid, chat_id)
        elif data == "menu_settings" or data.startswith("set_"):
            await _cb_settings(query, context, data, uid, chat_id)
        elif data == "menu_inbox" or data.startswith("ibx_"):
            await _cb_inbox(query, context, data, uid, chat_id)
        elif data == "menu_outbox" or data.startswith("obx_"):
            await _cb_outbox(query, context, data, uid, chat_id)
        elif data == "menu_schedules" or data.startswith("sched_"):
            await _cb_schedules(query, context, data, uid, chat_id)
        elif data == "menu_users" or data.startswith("user_"):
            await _cb_users(query, context, data, uid, chat_id)
        elif data.startswith("appr_"):
            await _cb_approvals(query, context, data, uid, chat_id)
        else:
            await _cb_misc(query, context, data, uid, chat_id)
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
    global _application, _core_events_queue, _core_events_task_handle
    _application = application

    # Register the native Telegram slash-command menu (the "/" autocomplete).
    await _register_commands(application)

    # Wire the one Telegram-specific fast path the scheduler needs, then start
    # the core's scheduler runtime and bridge watcher (zilla/core.py — CORE_API
    # migration steps 3/4) and subscribe to render its ScheduledResult/Alert/
    # Ask events.
    core.schedule_pre_run = _schedule_pre_run_hook
    _core_events_queue = asyncio.Queue()
    core.subscribe(_core_events_queue)
    _core_events_task_handle = application.create_task(
        _core_events_task(core, _core_events_queue))
    await core.start()

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


async def post_shutdown(application):
    """Mirror of post_init's startup wiring: stop the core's scheduler task
    and the event-render consumer cleanly on shutdown."""
    global _core_events_task_handle
    try:
        await core.stop()
    except Exception as e:
        logger.warning(f"[SHUTDOWN] core.stop() failed: {e}")
    if _core_events_task_handle is not None:
        _core_events_task_handle.cancel()
        try:
            await _core_events_task_handle
        except (asyncio.CancelledError, Exception):
            pass
        _core_events_task_handle = None


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
    global sessions, auth, schedules_mgr, core, _lock_file_handle

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
    _prune_old_logs()
    sessions = SessionManager(SESSIONS_FILE)
    auth = AuthManager(USERS_FILE, OWNER_CHAT_ID)
    schedules_mgr = ScheduleManager(SCHEDULES_FILE)
    # the turn pipeline + scheduler runtime + bridge watcher (CORE_API
    # migration steps 2 + 3 + 4)
    core = zcore.ZillaCore(sessions=sessions, auth=auth, schedules=schedules_mgr,
                          owner_chat_id=OWNER_CHAT_ID)
    keyboards.auth = auth  # the keyboard builders read auth for role-gated menus

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
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        # PTB defaults are 5s — too tight on slow links (hotspot); a timed-out
        # send here is how replies silently vanish.
        .connect_timeout(15)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(10)
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
