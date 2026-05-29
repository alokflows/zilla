# ============================================================
#  UI BUTTONS — Interactive Inline Keyboard System
# ============================================================
#
#  Complete button UI for the AGY Telegram Bot.
#  Every feature has two functions:
#    build_<name>_keyboard(...)  → returns InlineKeyboardMarkup
#    handle_<name>_callback(...) → handles the callback query
#
#  Callback handlers do NOT import managers directly.
#  They pull them from context.bot_data or accept them as args.
#
#  Callback data patterns:
#    menu_<action>              — Main menu
#    sess_<action>[_<name>]     — Sessions
#    model_<id>                 — Model selector
#    agt_<action>[_<id>]        — Agents
#    set_<key>_<value>          — Settings
#    skill_<action>[_<name>]    — Skills
#    err_<action>               — Error recovery
#    svc_<action>               — Service / status
# ============================================================

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════

AVAILABLE_MODELS: list[dict[str, str]] = [
    {"id": "gemini-3.5-flash",   "name": "Gemini 3.5 Flash"},
    {"id": "gemini-3.1-pro",     "name": "Gemini 3.1 Pro"},
    {"id": "claude-opus-4.6",    "name": "Claude Opus 4.6"},
    {"id": "claude-sonnet-4.6",  "name": "Claude Sonnet 4.6"},
]

TIMEOUT_OPTIONS: list[dict[str, Any]] = [
    {"value": "300",  "label": "5m"},
    {"value": "600",  "label": "10m"},
    {"value": "900",  "label": "15m"},
]

PROGRESS_OPTIONS: list[dict[str, str]] = [
    {"value": "detailed", "label": "Detailed"},
    {"value": "minimal",  "label": "Minimal"},
    {"value": "silent",   "label": "Silent"},
]

AUTO_DESCRIBE_OPTIONS: list[dict[str, str]] = [
    {"value": "on",  "label": "On"},
    {"value": "off", "label": "Off"},
]

MAX_AGENTS_OPTIONS: list[dict[str, Any]] = [
    {"value": "3",  "label": "3"},
    {"value": "5",  "label": "5"},
    {"value": "10", "label": "10"},
]


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════

def _row(*buttons: InlineKeyboardButton) -> list[InlineKeyboardButton]:
    """Convenience: wrap buttons into a row (list)."""
    return list(buttons)


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    """Shorthand for InlineKeyboardButton."""
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def _radio_row(
    options: list[dict[str, Any]],
    current_value: str,
    callback_prefix: str,
) -> list[list[InlineKeyboardButton]]:
    """Build a single row of radio-style buttons with ✅ on current."""
    row: list[InlineKeyboardButton] = []
    for opt in options:
        marker = "✅ " if str(opt["value"]) == str(current_value) else ""
        row.append(
            _btn(f"{marker}{opt['label']}", f"{callback_prefix}{opt['value']}")
        )
    return [row]


# ══════════════════════════════════════════════════════════
#  1. MAIN MENU
# ══════════════════════════════════════════════════════════


def build_back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Build a simple keyboard with just a 'Back to Menu' button."""
    return InlineKeyboardMarkup([[_btn("🔙 Back to Menu", "menu_back")]])


def build_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Build the main /menu inline keyboard.
    2-column grid of primary bot actions.
    """
    buttons = [
        ("📂 Sessions",    "menu_sessions"),
        ("🤖 AI Model",    "menu_model"),
        ("📥 Inbox",       "menu_inbox"),
        ("🧠 Brain Stats", "menu_brain"),
        ("👥 Sub-Agents",  "menu_agents"),
        ("⚡ Skills",      "menu_skills"),
        ("🌐 Web Search",  "menu_web"),
        ("📝 Quick Note",  "menu_note"),
        ("⚙️ Settings",    "menu_settings"),
        ("📊 Bot Status",  "menu_status"),
    ]
    # Arrange into 2-column rows
    keyboard: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        row = [_btn(buttons[i][0], buttons[i][1])]
        if i + 1 < len(buttons):
            row.append(_btn(buttons[i + 1][0], buttons[i + 1][1]))
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)


async def handle_menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle main menu button presses.
    Routes to sub-menus or triggers quick actions.
    """
    query = update.callback_query
    await query.answer()
    data: str = query.data  # e.g. "menu_sessions"
    action = data.removeprefix("menu_")

    logger.info(f"Menu action: {action}")

    if action == "sessions":
        sm = context.bot_data.get("session_manager")
        kb = build_sessions_list_keyboard(sm)
        await query.edit_message_text("📂 Sessions", reply_markup=kb)

    elif action == "model":
        settings = context.bot_data.get("settings", {})
        current = settings.get("model", "gemini-3.5-flash")
        kb = build_model_keyboard(current)
        await query.edit_message_text(
            f"🤖 AI Model\nCurrent: *{current}*",
            reply_markup=kb,
            parse_mode="Markdown",
        )

    elif action == "inbox":
        brain_mgr = context.bot_data.get("brain_manager")
        if brain_mgr and hasattr(brain_mgr, "get_inbox_pending"):
            items = brain_mgr.get_inbox_pending()
            count = len(items) if isinstance(items, list) else items
            await query.edit_message_text(f"📥 Inbox — {count} pending items")
        else:
            await query.edit_message_text("📥 Inbox — use /inbox for details")

    elif action == "brain":
        brain_mgr = context.bot_data.get("brain_manager")
        if brain_mgr and hasattr(brain_mgr, "get_brain_stats"):
            stats = brain_mgr.get_brain_stats()
            total_inbox = sum(stats.get("inbox", {}).values())
            total_know = sum(stats.get("knowledge", {}).values())
            await query.edit_message_text(
                f"🧠 Brain Stats\n"
                f"═══════════════\n"
                f"📥 Inbox: {total_inbox} items\n"
                f"📚 Knowledge: {total_know} items"
            )
        else:
            await query.edit_message_text("🧠 Brain — use /brain for details")

    elif action == "agents":
        agents = context.bot_data.get("agents", {})
        kb = build_agents_list_keyboard(agents)
        await query.edit_message_text("👥 Sub-Agents", reply_markup=kb)

    elif action == "skills":
        skills = context.bot_data.get("skills", {})
        kb = build_skills_list_keyboard(skills)
        await query.edit_message_text("⚡ Skills", reply_markup=kb)

    elif action == "web":
        await query.edit_message_text(
            "🌐 Web Search\nSend a message starting with /web to search."
        )

    elif action == "note":
        await query.edit_message_text(
            "📝 Quick Note\nSend a message starting with /note to save."
        )

    elif action == "settings":
        settings = context.bot_data.get("settings", {})
        kb = build_settings_keyboard(settings)
        await query.edit_message_text("⚙️ Settings", reply_markup=kb)

    elif action == "status":
        kb = build_status_keyboard()
        await query.edit_message_text("📊 Bot Status", reply_markup=kb)

    else:
        logger.warning(f"Unknown menu action: {action}")
        await query.edit_message_text(f"Unknown action: {action}")


# ══════════════════════════════════════════════════════════
#  2. SESSIONS
# ══════════════════════════════════════════════════════════

def build_sessions_list_keyboard(
    session_manager: Any = None,
) -> InlineKeyboardMarkup:
    """
    Build the session list keyboard.
    Each session shown as a button with message count.
    Active session marked with ◀.
    """
    keyboard: list[list[InlineKeyboardButton]] = []

    if session_manager:
        active = session_manager.active_name
        all_sessions = session_manager.list_sessions()

        for name, info in all_sessions.items():
            msg_count = info.get("messages", 0)
            marker = " ◀" if name == active else ""
            label = f"{name} ({msg_count} msgs){marker}"
            keyboard.append([_btn(label, f"sess_detail_{name}")])

    # Bottom actions
    keyboard.append([
        _btn("➕ New Session", "sess_new"),
        _btn("🔙 Back", "menu_back"),
    ])

    return InlineKeyboardMarkup(keyboard)


def build_session_detail_keyboard(session_name: str) -> InlineKeyboardMarkup:
    """Build detail/action keyboard for a specific session."""
    keyboard = [
        [
            _btn("🔄 Switch Here", f"sess_switch_{session_name}"),
            _btn("✏️ Rename", f"sess_rename_{session_name}"),
        ],
        [
            _btn("🗑️ Delete", f"sess_delete_{session_name}"),
            _btn("🔙 Back", "sess_list"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_session_delete_confirm_keyboard(
    session_name: str,
) -> InlineKeyboardMarkup:
    """Build delete confirmation keyboard: Are you sure? [Yes] [No]."""
    keyboard = [
        [
            _btn("✅ Yes, Delete", f"sess_confirm_del_{session_name}"),
            _btn("❌ No, Cancel", "sess_cancel_del"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def handle_sessions_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle all session-related callbacks.

    Patterns:
      sess_list                — refresh session list
      sess_detail_<name>       — show session detail
      sess_switch_<name>       — switch to session
      sess_rename_<name>       — prompt rename (text-based)
      sess_delete_<name>       — show delete confirmation
      sess_confirm_del_<name>  — confirm delete
      sess_cancel_del          — cancel delete
      sess_new                 — prompt for new session name
    """
    query = update.callback_query
    await query.answer()
    data: str = query.data
    sm = context.bot_data.get("session_manager")

    if not sm:
        logger.error("SessionManager not found in bot_data")
        await query.edit_message_text("❌ Session manager unavailable.")
        return

    # ── List sessions ──────────────────────────────────────
    if data == "sess_list":
        kb = build_sessions_list_keyboard(sm)
        await query.edit_message_text("📂 Sessions", reply_markup=kb)

    # ── Session detail ─────────────────────────────────────
    elif data.startswith("sess_detail_"):
        name = data.removeprefix("sess_detail_")
        all_sessions = sm.list_sessions()
        info = all_sessions.get(name, {})
        msg_count = info.get("messages", 0)
        created = info.get("created", "Unknown")
        last_used = info.get("last_used", "Never")
        is_active = name == sm.active_name
        conv_id = info.get("conversation_id", "None")

        text = (
            f"📂 Session: {name}\n"
            f"═══════════════════\n"
            f"{'🟢 Active' if is_active else '⚪ Inactive'}\n"
            f"💬 Messages: {msg_count}\n"
            f"📅 Created: {created}\n"
            f"🕐 Last used: {last_used}\n"
            f"🔗 Conv ID: {conv_id[:16] + '...' if conv_id and len(conv_id) > 16 else conv_id}"
        )
        kb = build_session_detail_keyboard(name)
        await query.edit_message_text(text, reply_markup=kb)

    # ── Switch session ─────────────────────────────────────
    elif data.startswith("sess_switch_"):
        name = data.removeprefix("sess_switch_")
        sm.active_name = name
        logger.info(f"Switched to session: {name}")
        await query.edit_message_text(f"✅ Switched to session: *{name}*", parse_mode="Markdown")

    # ── Rename (text prompt, can't do inline) ──────────────
    elif data.startswith("sess_rename_"):
        name = data.removeprefix("sess_rename_")
        context.user_data["awaiting_rename"] = name
        await query.edit_message_text(
            f"✏️ Rename session *{name}*\n\n"
            f"Send the new name as your next message.",
            parse_mode="Markdown",
        )

    # ── Delete — show confirmation ─────────────────────────
    elif data.startswith("sess_delete_") and not data.startswith("sess_confirm_del_"):
        name = data.removeprefix("sess_delete_")
        kb = build_session_delete_confirm_keyboard(name)
        await query.edit_message_text(
            f"⚠️ Are you sure you want to delete session *{name}*?",
            reply_markup=kb,
            parse_mode="Markdown",
        )

    # ── Confirm delete ─────────────────────────────────────
    elif data.startswith("sess_confirm_del_"):
        name = data.removeprefix("sess_confirm_del_")
        success = sm.delete_session(name)
        if success:
            logger.info(f"Deleted session: {name}")
            await query.edit_message_text(f"🗑️ Session *{name}* deleted.", parse_mode="Markdown")
        else:
            await query.edit_message_text(f"❌ Session *{name}* not found.", parse_mode="Markdown")

    # ── Cancel delete ──────────────────────────────────────
    elif data == "sess_cancel_del":
        kb = build_sessions_list_keyboard(sm)
        await query.edit_message_text("📂 Sessions", reply_markup=kb)

    # ── New session ────────────────────────────────────────
    elif data == "sess_new":
        context.user_data["awaiting_new_session"] = True
        await query.edit_message_text(
            "➕ New Session\n\nSend the session name as your next message."
        )

    else:
        logger.warning(f"Unknown session callback: {data}")


# ══════════════════════════════════════════════════════════
#  3. MODEL SELECTOR
# ══════════════════════════════════════════════════════════

def build_model_keyboard(current_model: str = "") -> InlineKeyboardMarkup:
    """
    Build the AI model selection keyboard.
    Current model is marked with ✅.
    """
    keyboard: list[list[InlineKeyboardButton]] = []

    for model in AVAILABLE_MODELS:
        marker = "✅ " if model["id"] == current_model else ""
        keyboard.append([
            _btn(f"{marker}{model['name']}", f"model_{model['id']}")
        ])

    keyboard.append([_btn("🔙 Back", "menu_back")])
    return InlineKeyboardMarkup(keyboard)


async def handle_model_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle model selection callback.
    Stores the selected model in context.bot_data['settings']['model'].
    """
    query = update.callback_query
    await query.answer()
    data: str = query.data  # e.g. "model_gemini-3.5-flash"
    model_id = data.removeprefix("model_")

    # Validate model ID
    valid_ids = {m["id"] for m in AVAILABLE_MODELS}
    if model_id not in valid_ids:
        logger.warning(f"Unknown model selected: {model_id}")
        await query.edit_message_text(f"❌ Unknown model: {model_id}")
        return

    # Get display name
    display_name = next(
        (m["name"] for m in AVAILABLE_MODELS if m["id"] == model_id),
        model_id,
    )

    # Store in settings
    settings = context.bot_data.setdefault("settings", {})
    settings["model"] = model_id

    # Persist if a settings saver is available
    settings_saver = context.bot_data.get("save_settings")
    if settings_saver and callable(settings_saver):
        try:
            settings_saver(settings)
        except Exception as e:
            logger.error(f"Failed to persist settings: {e}")

    logger.info(f"Model changed to: {model_id}")

    # Re-show the model keyboard with updated selection
    kb = build_model_keyboard(model_id)
    await query.edit_message_text(
        f"🤖 AI Model\nCurrent: *{display_name}* ✅",
        reply_markup=kb,
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════
#  4. AGENTS
# ══════════════════════════════════════════════════════════

def build_agents_list_keyboard(
    agents: dict[str, dict] | None = None,
) -> InlineKeyboardMarkup:
    """
    Build the agents list keyboard.
    Running agents shown with 🟢, done agents with ✅.
    """
    keyboard: list[list[InlineKeyboardButton]] = []

    if agents:
        for agent_id, info in agents.items():
            status = info.get("status", "unknown")
            if status == "running":
                icon = "🟢"
            elif status in ("done", "completed"):
                icon = "✅"
            else:
                icon = "⚪"
            label = info.get("name", agent_id)
            keyboard.append([
                _btn(f"{icon} {label}", f"agt_detail_{agent_id}")
            ])

    # Bottom actions
    keyboard.append([
        _btn("🚀 Launch New Agent", "agt_launch"),
        _btn("🧹 Clear Done", "agt_clear"),
    ])
    keyboard.append([_btn("🔙 Back", "menu_back")])

    return InlineKeyboardMarkup(keyboard)


def build_agent_detail_keyboard(
    agent_id: str,
    status: str = "running",
) -> InlineKeyboardMarkup:
    """
    Build detail/action keyboard for a specific agent.
    Shows Chat, View Output, Stop (if running), and Back.
    """
    keyboard: list[list[InlineKeyboardButton]] = []

    row_1: list[InlineKeyboardButton] = [
        _btn("💬 Chat", f"agt_chat_{agent_id}"),
        _btn("📄 View Output", f"agt_output_{agent_id}"),
    ]
    keyboard.append(row_1)

    row_2: list[InlineKeyboardButton] = []
    if status == "running":
        row_2.append(_btn("🛑 Stop", f"agt_stop_{agent_id}"))
    row_2.append(_btn("🔙 Back", "agt_list"))
    keyboard.append(row_2)

    return InlineKeyboardMarkup(keyboard)


async def handle_agents_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle all agent-related callbacks.

    Patterns:
      agt_list              — refresh agent list
      agt_detail_<id>       — show agent detail
      agt_chat_<id>         — start chatting with agent
      agt_stop_<id>         — stop a running agent
      agt_output_<id>       — view agent output
      agt_launch            — prompt to launch a new agent
      agt_clear             — clear completed agents
    """
    query = update.callback_query
    await query.answer()
    data: str = query.data
    agent_mgr = context.bot_data.get("agent_manager")
    agents: dict = agent_mgr.list_all() if agent_mgr else {}

    # ── List agents ────────────────────────────────────────
    if data == "agt_list":
        kb = build_agents_list_keyboard(agents)
        await query.edit_message_text("👥 Sub-Agents", reply_markup=kb)

    # ── Agent detail ───────────────────────────────────────
    elif data.startswith("agt_detail_"):
        agent_id = data.removeprefix("agt_detail_")
        info = agents.get(agent_id, {})
        name = info.get("name", agent_id)
        status = info.get("status", "unknown")
        task = info.get("task", "N/A")
        started = info.get("started", "Unknown")

        status_icon = {"running": "🟢", "done": "✅", "completed": "✅"}.get(
            status, "⚪"
        )

        text = (
            f"👤 Agent: {name}\n"
            f"═══════════════════\n"
            f"Status: {status_icon} {status}\n"
            f"📋 Task: {task}\n"
            f"🕐 Started: {started}"
        )
        kb = build_agent_detail_keyboard(agent_id, status)
        await query.edit_message_text(text, reply_markup=kb)

    # ── Chat with agent ────────────────────────────────────
    elif data.startswith("agt_chat_"):
        agent_id = data.removeprefix("agt_chat_")
        context.user_data["chatting_with_agent"] = agent_id
        info = agents.get(agent_id, {})
        name = info.get("name", agent_id)
        await query.edit_message_text(
            f"💬 Now chatting with agent *{name}*.\n"
            f"Send messages directly. Use /menu to exit.",
            parse_mode="Markdown",
        )

    # ── Stop agent ─────────────────────────────────────────
    elif data.startswith("agt_stop_"):
        agent_id = data.removeprefix("agt_stop_")
        agent_stopper = context.bot_data.get("stop_agent")
        if agent_stopper and callable(agent_stopper):
            try:
                await agent_stopper(agent_id)
                logger.info(f"Stopped agent: {agent_id}")
                await query.edit_message_text(f"🛑 Agent *{agent_id}* stopped.", parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to stop agent {agent_id}: {e}")
                await query.edit_message_text(f"❌ Failed to stop agent: {e}")
        else:
            # Mark as stopped in local state
            if agent_id in agents:
                agents[agent_id]["status"] = "stopped"
            await query.edit_message_text(f"🛑 Agent *{agent_id}* marked as stopped.", parse_mode="Markdown")

    # ── View output ────────────────────────────────────────
    elif data.startswith("agt_output_"):
        agent_id = data.removeprefix("agt_output_")
        info = agents.get(agent_id, {})
        output = info.get("output", "No output captured yet.")
        name = info.get("name", agent_id)

        # Truncate long output for Telegram
        if len(output) > 3500:
            output = output[:3500] + "\n\n… (truncated)"

        await query.edit_message_text(
            f"📄 Output — {name}\n"
            f"═══════════════════\n\n"
            f"{output}",
        )

    # ── Launch new agent ───────────────────────────────────
    elif data == "agt_launch":
        context.user_data["awaiting_agent_task"] = True
        await query.edit_message_text(
            "🚀 Launch New Agent\n\n"
            "Send the task description as your next message."
        )

    # ── Clear done agents ──────────────────────────────────
    elif data == "agt_clear":
        to_remove = [
            aid for aid, info in agents.items()
            if info.get("status") in ("done", "completed", "stopped", "error")
        ]
        for aid in to_remove:
            del agents[aid]
        logger.info(f"Cleared {len(to_remove)} completed agent(s)")

        kb = build_agents_list_keyboard(agents)
        await query.edit_message_text(
            f"🧹 Cleared {len(to_remove)} completed agent(s).",
            reply_markup=kb,
        )

    else:
        logger.warning(f"Unknown agent callback: {data}")


# ══════════════════════════════════════════════════════════
#  5. SETTINGS
# ══════════════════════════════════════════════════════════

def build_settings_keyboard(
    settings: dict[str, Any] | None = None,
) -> InlineKeyboardMarkup:
    """
    Build the settings keyboard with radio-button rows.

    Sections:
      - Timeout:            5m / 10m / 15m
      - Progress:           Detailed / Minimal / Silent
      - Auto-describe:      On / Off
      - Max agents:         3 / 5 / 10
    """
    if settings is None:
        settings = {}

    current_timeout = str(settings.get("timeout", "600"))
    current_progress = settings.get("progress_style", "detailed")
    
    # Handle boolean for auto_describe
    auto_desc_val = settings.get("auto_describe_photos", True)
    if isinstance(auto_desc_val, str):
        current_auto_desc = auto_desc_val
    else:
        current_auto_desc = "on" if auto_desc_val else "off"
        
    current_max_agents = str(settings.get("max_agents", "5"))

    keyboard: list[list[InlineKeyboardButton]] = []

    # Section: Timeout
    keyboard.append([_btn("⏱️ Timeout:", "set_noop")])
    keyboard.extend(
        _radio_row(TIMEOUT_OPTIONS, current_timeout, "set_timeout_")
    )

    # Section: Progress
    keyboard.append([_btn("📊 Progress:", "set_noop")])
    keyboard.extend(
        _radio_row(PROGRESS_OPTIONS, current_progress, "set_progress_")
    )

    # Section: Auto-describe photos
    keyboard.append([_btn("📸 Auto-describe photos:", "set_noop")])
    keyboard.extend(
        _radio_row(AUTO_DESCRIBE_OPTIONS, current_auto_desc, "set_auto_describe_")
    )

    # Section: Max agents
    keyboard.append([_btn("👥 Max agents:", "set_noop")])
    keyboard.extend(
        _radio_row(MAX_AGENTS_OPTIONS, current_max_agents, "set_max_agents_")
    )

    # Back
    keyboard.append([_btn("🔙 Back", "menu_back")])

    return InlineKeyboardMarkup(keyboard)


async def handle_settings_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle settings radio-button callbacks.
    Pattern: set_<key>_<value>
    """
    query = update.callback_query
    await query.answer()
    data: str = query.data

    # Ignore label-only buttons
    if data == "set_noop":
        return

    # Parse: set_<key>_<value>
    # Keys can contain underscores (e.g. auto_describe, max_agents),
    # so we use known prefixes to parse correctly.
    settings = context.bot_data.setdefault("settings", {})

    known_keys = {
        "set_timeout_":       "timeout",
        "set_progress_":      "progress_style",
        "set_auto_describe_": "auto_describe_photos",
        "set_max_agents_":    "max_agents",
    }

    key: str | None = None
    value: str | None = None

    for prefix, setting_key in known_keys.items():
        if data.startswith(prefix):
            key = setting_key
            value = data.removeprefix(prefix)
            break

    if key is None or value is None:
        logger.warning(f"Unknown settings callback: {data}")
        return

    # Type conversions
    if key == "timeout" or key == "max_agents":
        value = int(value)
    elif key == "auto_describe_photos":
        value = (value == "on")

    # Update via SettingsManager
    settings_mgr = context.bot_data.get("settings_manager")
    if settings_mgr:
        settings_mgr.set(key, value)
        logger.info(f"Setting changed: {key} = {value}")
        # Re-render settings keyboard with updated values
        kb = build_settings_keyboard(settings_mgr)
        await query.edit_message_text("⚙️ Settings", reply_markup=kb)
    else:
        logger.error("SettingsManager not found in bot_data")
        await query.answer("Error: Settings manager missing", show_alert=True)


# ══════════════════════════════════════════════════════════
#  6. SKILLS
# ══════════════════════════════════════════════════════════

def build_skills_list_keyboard(
    skills: dict[str, dict] | None = None,
) -> InlineKeyboardMarkup:
    """
    Build the skills list keyboard.
    Each skill shown with its status icon.
    """
    keyboard: list[list[InlineKeyboardButton]] = []

    if skills:
        for name, info in skills.items():
            status = info.get("status", "installed")
            icon = {"installed": "✅", "disabled": "⚪", "error": "❌"}.get(
                status, "⚪"
            )
            keyboard.append([
                _btn(f"{icon} {name}", f"skill_detail_{name}")
            ])

    if not skills:
        keyboard.append([_btn("📭 No skills installed", "skill_noop")])

    keyboard.append([_btn("🔙 Back", "menu_back")])
    return InlineKeyboardMarkup(keyboard)


def build_skill_detail_keyboard(skill_name: str) -> InlineKeyboardMarkup:
    """Build detail/action keyboard for a specific skill."""
    keyboard = [
        [
            _btn("🗑️ Remove", f"skill_remove_{skill_name}"),
            _btn("🔙 Back", "skill_list"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_skill_remove_confirm_keyboard(
    skill_name: str,
) -> InlineKeyboardMarkup:
    """Build remove confirmation keyboard for a skill."""
    keyboard = [
        [
            _btn("✅ Yes, Remove", f"skill_confirm_rm_{skill_name}"),
            _btn("❌ No, Cancel", "skill_list"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def handle_skills_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle all skill-related callbacks.

    Patterns:
      skill_list              — refresh skill list
      skill_detail_<name>     — show skill detail
      skill_remove_<name>     — show remove confirmation
      skill_confirm_rm_<name> — confirm removal
    """
    query = update.callback_query
    await query.answer()
    data: str = query.data
    
    skills_mgr = context.bot_data.get("skills_manager")
    skills: dict = skills_mgr.list_skills() if skills_mgr else {}

    # Ignore label-only buttons
    if data == "skill_noop":
        return

    # ── List skills ────────────────────────────────────────
    if data == "skill_list":
        kb = build_skills_list_keyboard(skills)
        await query.edit_message_text("⚡ Skills", reply_markup=kb)

    # ── Skill detail ───────────────────────────────────────
    elif data.startswith("skill_detail_"):
        name = data.removeprefix("skill_detail_")
        info = skills.get(name, {})
        description = info.get("description", "No description available.")
        status = info.get("status", "installed")
        version = info.get("version", "Unknown")

        text = (
            f"⚡ Skill: {name}\n"
            f"═══════════════════\n"
            f"📝 {description}\n"
            f"📌 Status: {status}\n"
            f"🔢 Version: {version}"
        )
        kb = build_skill_detail_keyboard(name)
        await query.edit_message_text(text, reply_markup=kb)

    # ── Remove — show confirmation ─────────────────────────
    elif data.startswith("skill_remove_") and not data.startswith("skill_confirm_rm_"):
        name = data.removeprefix("skill_remove_")
        kb = build_skill_remove_confirm_keyboard(name)
        await query.edit_message_text(
            f"⚠️ Remove skill *{name}*?",
            reply_markup=kb,
            parse_mode="Markdown",
        )

    # ── Confirm remove ─────────────────────────────────────
    elif data.startswith("skill_confirm_rm_"):
        name = data.removeprefix("skill_confirm_rm_")
        skill_remover = context.bot_data.get("remove_skill")
        if skill_remover and callable(skill_remover):
            try:
                await skill_remover(name)
                logger.info(f"Removed skill: {name}")
            except Exception as e:
                logger.error(f"Failed to remove skill {name}: {e}")
                await query.edit_message_text(f"❌ Failed to remove skill: {e}")
                return
        else:
            # Remove from local state
            if name in skills:
                del skills[name]

        await query.edit_message_text(f"🗑️ Skill *{name}* removed.", parse_mode="Markdown")

    else:
        logger.warning(f"Unknown skill callback: {data}")


# ══════════════════════════════════════════════════════════
#  7. ERROR RECOVERY
# ══════════════════════════════════════════════════════════

def build_error_keyboard() -> InlineKeyboardMarkup:
    """
    Build error recovery keyboard.
    Shown when agy returns an error to give the user quick recovery options.
    """
    keyboard = [
        [
            _btn("🔄 Retry", "err_retry"),
            _btn("🤖 Try Different Model", "err_model"),
            _btn("❌ Cancel", "err_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def handle_error_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle error recovery callbacks.

    Patterns:
      err_retry  — retry the last failed message
      err_model  — switch model and retry
      err_cancel — dismiss the error
    """
    query = update.callback_query
    await query.answer()
    data: str = query.data

    if data == "err_retry":
        last_message = context.user_data.get("last_failed_message")
        if last_message:
            logger.info("Retrying last failed message")
            await query.edit_message_text("🔄 Retrying…")
            # The actual retry is handled by the caller — we store
            # a flag that the main message handler can pick up.
            context.user_data["retry_pending"] = True
            context.user_data["retry_message"] = last_message
        else:
            await query.edit_message_text("❌ No message to retry.")

    elif data == "err_model":
        settings = context.bot_data.get("settings", {})
        current = settings.get("model", "gemini-3.5-flash")
        kb = build_model_keyboard(current)
        await query.edit_message_text(
            "🤖 Select a different model, then retry.\n"
            f"Current: *{current}*",
            reply_markup=kb,
            parse_mode="Markdown",
        )
        # Set flag so after model change, we auto-retry
        context.user_data["retry_after_model_change"] = True

    elif data == "err_cancel":
        await query.edit_message_text("❌ Cancelled.")
        # Clear error state
        context.user_data.pop("last_failed_message", None)
        context.user_data.pop("retry_pending", None)

    else:
        logger.warning(f"Unknown error callback: {data}")


# ══════════════════════════════════════════════════════════
#  8. SERVICE / STATUS
# ══════════════════════════════════════════════════════════

def build_status_keyboard() -> InlineKeyboardMarkup:
    """
    Build the bot status / service keyboard.
    Actions: restart bot, view logs.
    """
    keyboard = [
        [
            _btn("🔄 Restart Bot", "svc_restart"),
            _btn("📋 View Logs", "svc_logs"),
        ],
        [_btn("🔙 Back", "menu_back")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def handle_status_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle service/status callbacks.

    Patterns:
      svc_restart — restart the bot process
      svc_logs    — show recent log entries
    """
    query = update.callback_query
    await query.answer()
    data: str = query.data

    if data == "svc_restart":
        restarter = context.bot_data.get("restart_bot")
        if restarter and callable(restarter):
            await query.edit_message_text("🔄 Restarting bot…")
            try:
                await restarter()
            except Exception as e:
                logger.error(f"Failed to restart: {e}")
                await query.edit_message_text(f"❌ Restart failed: {e}")
        else:
            await query.edit_message_text(
                "🔄 Restart not available.\n"
                "Use the system service to restart the bot."
            )

    elif data == "svc_logs":
        log_reader = context.bot_data.get("read_logs")
        if log_reader and callable(log_reader):
            try:
                logs = log_reader(lines=30)
                if len(logs) > 3500:
                    logs = logs[-3500:]
                await query.edit_message_text(
                    f"📋 Recent Logs\n═══════════════\n\n{logs}"
                )
            except Exception as e:
                logger.error(f"Failed to read logs: {e}")
                await query.edit_message_text(f"❌ Failed to read logs: {e}")
        else:
            await query.edit_message_text("📋 Log reader not configured.")

    else:
        logger.warning(f"Unknown status callback: {data}")


# ══════════════════════════════════════════════════════════
#  MASTER CALLBACK ROUTER
# ══════════════════════════════════════════════════════════

async def route_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Master callback router — dispatches every callback query
    to the correct handler based on its data prefix.

    Register this with:
        app.add_handler(CallbackQueryHandler(route_callback))
    """
    query = update.callback_query
    if not query or not query.data:
        return

    data: str = query.data

    logger.debug(f"Callback received: {data}")

    try:
        # ── Back to main menu ─────────────────────────────
        if data == "menu_back":
            await query.answer()
            kb = build_menu_keyboard()
            await query.edit_message_text("🧠 AGI Brain — Menu", reply_markup=kb)

        # ── Menu actions ───────────────────────────────────
        elif data.startswith("menu_"):
            await handle_menu_callback(update, context)

        # ── Session actions ────────────────────────────────
        elif data.startswith("sess_"):
            await handle_sessions_callback(update, context)

        # ── Model selection ────────────────────────────────
        elif data.startswith("model_"):
            await handle_model_callback(update, context)

        # ── Agent actions ──────────────────────────────────
        elif data.startswith("agt_"):
            await handle_agents_callback(update, context)

        # ── Settings ───────────────────────────────────────
        elif data.startswith("set_"):
            await handle_settings_callback(update, context)

        # ── Skills ─────────────────────────────────────────
        elif data.startswith("skill_"):
            await handle_skills_callback(update, context)

        # ── Error recovery ─────────────────────────────────
        elif data.startswith("err_"):
            await handle_error_callback(update, context)

        # ── Service / status ───────────────────────────────
        elif data.startswith("svc_"):
            await handle_status_callback(update, context)

        # ── Unknown ────────────────────────────────────────
        else:
            logger.warning(f"Unhandled callback data: {data}")
            await query.answer(text="Unknown action", show_alert=True)

    except Exception as e:
        logger.error(f"Error handling callback '{data}': {e}", exc_info=True)
        try:
            await query.answer(text="Something went wrong.", show_alert=True)
        except Exception:
            pass
