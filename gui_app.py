# ============================================================
#  Zilla — Universal AI Agent Engine v4.0
# ============================================================
#
#  A premium Windows desktop application for the Zilla AI
#  Agent with:
#  - Dashboard with embedded chat interface
#  - Telegram user selector (chat as any user)
#  - Skills management page
#  - Integrations hub (Google, OneDrive, Slack, Notion)
#  - macOS-style settings
#  - Agent monitoring & orchestrator view
#  - Glassmorphism dark theme
#  - Multi-CLI backend support
#
#  Inspired by OpenClaw — reimagined for desktop.
#  Usage: python gui_app.py
# ============================================================

import os
import sys
import json
import time
import queue
import logging
import threading
import asyncio
import subprocess
from datetime import datetime
from collections import deque

import pystray
from PIL import Image as PILImage, ImageDraw
import keyboard
from telegram.ext import TypeHandler, ApplicationHandlerStop
from telegram import Update
import customtkinter as ctk
from tkinter import font as tkfont

# ── Resolve project directory ──────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# Fix Windows console encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── CustomTkinter config ───────────────────────────────────
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


# ══════════════════════════════════════════════════════════
#  DESIGN SYSTEM — Colors, Fonts, Dimensions
# ══════════════════════════════════════════════════════════

class DS:
    """Design System constants — single source of truth for theming."""
    # Background layers (dark → light)
    BG_DEEP     = "#080810"
    BG_BASE     = "#0c0c18"
    BG_SURFACE  = "#141425"
    BG_ELEVATED = "#1a1a30"
    BG_CARD     = "#1e1e35"
    BG_HOVER    = "#252545"

    # Accent colors
    ACCENT_PRIMARY   = "#6366f1"  # Indigo
    ACCENT_HOVER     = "#818cf8"
    ACCENT_MUTED     = "#4338ca"
    ACCENT_SUCCESS   = "#22c55e"
    ACCENT_WARNING   = "#f59e0b"
    ACCENT_DANGER    = "#ef4444"
    ACCENT_INFO      = "#3b82f6"
    ACCENT_TEAL      = "#14b8a6"

    # Text
    TEXT_PRIMARY   = "#f1f5f9"
    TEXT_SECONDARY = "#94a3b8"
    TEXT_MUTED     = "#64748b"
    TEXT_FAINT     = "#475569"

    # Borders & Glass
    BORDER        = "#1e293b"
    BORDER_HOVER  = "#334155"
    GLASS_BORDER  = "#1a1a2e"
    GLASS_BG      = "#12121e"

    # Chat bubbles
    BUBBLE_USER   = "#312e81"
    BUBBLE_BOT    = "#1e1e35"
    BUBBLE_SYSTEM = "#1a1a2e"

    # Sidebar
    SIDEBAR_BG    = "#0a0a16"
    SIDEBAR_ACTIVE = "#1c1c3a"

    # Dimensions
    SIDEBAR_W     = 230
    CORNER_R      = 12
    CORNER_R_SM   = 8
    CORNER_R_LG   = 16

    # Status colors
    ONLINE_COLOR  = "#22c55e"
    OFFLINE_COLOR = "#ef4444"
    TYPING_COLOR  = "#6366f1"


# ══════════════════════════════════════════════════════════
#  CONSTANTS & FILE PATHS
# ══════════════════════════════════════════════════════════

ENV_FILE       = os.path.join(PROJECT_DIR, ".env")
SETTINGS_JSON  = os.path.join(PROJECT_DIR, "settings.json")
SESSIONS_JSON  = os.path.join(PROJECT_DIR, "sessions.json")
MODEL_TXT      = os.path.join(PROJECT_DIR, "selected_model.txt")
AGENTS_FILE    = os.path.join(PROJECT_DIR, "agents.json")

AVAILABLE_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "claude-opus-4.6",
    "claude-sonnet-4.6",
]

TIMEOUT_OPTIONS  = {"5 min": 300, "10 min": 600, "15 min": 900, "20 min": 1200}
PROGRESS_OPTIONS = ["detailed", "minimal", "silent"]
MAX_AGENT_OPTIONS = ["3", "5", "10", "15"]

# Skills directory
SKILLS_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "skills")


# ══════════════════════════════════════════════════════════
#  THREAD-SAFE LOG HANDLER
# ══════════════════════════════════════════════════════════

class QueueLogHandler(logging.Handler):
    """Logging handler that puts formatted log records into a queue."""
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_queue.put_nowait(msg)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
#  FILE HELPERS (no heavy imports)
# ══════════════════════════════════════════════════════════

def read_env() -> dict:
    result = {}
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        key, val = line.split("=", 1)
                        result[key.strip()] = val.strip().strip("\"'")
        except Exception:
            pass
    return result


def write_env(token: str, owner_id: str):
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(f'TELEGRAM_BOT_TOKEN="{token}"\n')
        f.write(f'TELEGRAM_OWNER_ID="{owner_id}"\n')


def read_settings_json() -> dict:
    if os.path.exists(SETTINGS_JSON):
        try:
            with open(SETTINGS_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def write_settings_json(data: dict):
    with open(SETTINGS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def read_sessions_json() -> dict:
    if os.path.exists(SESSIONS_JSON):
        try:
            with open(SESSIONS_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"active": "main", "sessions": {}}


def write_model_txt(model: str):
    try:
        with open(MODEL_TXT, "w", encoding="utf-8") as f:
            f.write(model)
    except Exception:
        pass


def read_agents_json() -> dict:
    if os.path.exists(AGENTS_FILE):
        try:
            with open(AGENTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"agents": {}}


def read_users_json() -> dict:
    path = os.path.join(PROJECT_DIR, "authorized_users.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def write_users_json(data: dict):
    path = os.path.join(PROJECT_DIR, "authorized_users.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════
#  BOT CONTROLLER — Runs bot.py on a background thread
# ══════════════════════════════════════════════════════════

class BotController:
    """Manages the Telegram bot lifecycle on a background thread."""

    def __init__(self, log_queue: queue.Queue):
        self.log_queue = log_queue
        self.bot_thread: threading.Thread | None = None
        self.application = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.is_running = False
        self.start_time: float | None = None
        self._stop_event = threading.Event()
        self.chat_bus = None  # Injected after import

    def start(self):
        if self.is_running:
            self.log_queue.put("[SYSTEM] Bot is already running.")
            return
        self._stop_event.clear()
        self.bot_thread = threading.Thread(
            target=self._run_bot_thread, daemon=True, name="Zilla-Bot-Thread",
        )
        self.bot_thread.start()

    def stop(self):
        if not self.is_running:
            self.log_queue.put("[SYSTEM] Bot is not running.")
            return
        self.log_queue.put("[SYSTEM] Sending stop signal to bot...")
        self._stop_event.set()
        if self.application and self.loop and self.loop.is_running():
            try:
                self.loop.call_soon_threadsafe(self._request_stop)
            except Exception as e:
                self.log_queue.put(f"[SYSTEM] Error stopping: {e}")

    def _request_stop(self):
        try:
            if self.application and self.application.running:
                self.application.stop_running()
        except Exception:
            pass

    def get_uptime(self) -> str:
        if not self.start_time:
            return "—"
        elapsed = int(time.time() - self.start_time)
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {seconds}s"

    def _run_bot_thread(self):
        self.log_queue.put("[SYSTEM] Bot thread starting...")
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            # Install GUI log handler
            root_logger = logging.getLogger()
            gui_handler = QueueLogHandler(self.log_queue)
            gui_handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
            )
            gui_handler.setLevel(logging.INFO)
            root_logger.addHandler(gui_handler)
            root_logger.setLevel(logging.INFO)

            self.log_queue.put("[SYSTEM] Loading bot modules...")

            from telegram import Update
            from telegram.ext import (
                Application, CommandHandler, MessageHandler,
                filters, CallbackQueryHandler,
            )
            from config import (
                BOT_TOKEN, OWNER_CHAT_ID, USERS_FILE,
                SETTINGS_FILE as CFG_SETTINGS_FILE, AGENTS_FILE as CFG_AGENTS_FILE,
                MAX_CONCURRENT_AGENTS, SKILLS_DIR as CFG_SKILLS_DIR, BOT_VERSION, STATE_FILE,
            )
            from sessions import SessionManager
            from settings_manager import SettingsManager
            from agent_manager import AgentManager
            from skills_manager import SkillsManager
            from brain_manager import ensure_brain_structure
            from chat_bus import chat_bus, MessageRole, MessageStatus
            from user_manager import AuthorizedUsersManager

            self.chat_bus = chat_bus

            if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
                self.log_queue.put(
                    "[ERROR] No Telegram bot token configured! "
                    "Go to Settings and enter your token."
                )
                self.is_running = False
                return

            self.log_queue.put("[SYSTEM] Initializing managers...")
            ensure_brain_structure()
            sessions = SessionManager(STATE_FILE)
            settings = SettingsManager(CFG_SETTINGS_FILE)
            agents = AgentManager(CFG_AGENTS_FILE, MAX_CONCURRENT_AGENTS)
            skills = SkillsManager(CFG_SKILLS_DIR)

            import bot as bot_module
            bot_module.sessions = sessions
            bot_module.settings = settings
            bot_module.agents = agents
            bot_module.skills = skills
            bot_module.auth_manager = AuthorizedUsersManager(USERS_FILE, OWNER_CHAT_ID)

            # Inject chat_bus into bot module for message capture
            bot_module.chat_bus = chat_bus

            self.log_queue.put(f"[SYSTEM] Session: [{sessions.active_name}]")
            self.log_queue.put(f"[SYSTEM] Model: {settings.get_model()}")
            self.log_queue.put(f"[SYSTEM] Skills: {len(skills.list_skills())}")

            self.log_queue.put("[SYSTEM] Building Telegram application...")

            app = Application.builder().token(BOT_TOKEN).post_init(
                bot_module.post_init
            ).build()

            # Register all handlers
            # Global auth middleware — MUST be first
            app.add_handler(TypeHandler(Update, bot_module.auth_middleware), group=-1)
            app.add_handler(CommandHandler("start", bot_module.cmd_start))
            app.add_handler(CommandHandler("help", bot_module.cmd_help))
            app.add_handler(CommandHandler("ping", bot_module.cmd_ping))
            app.add_handler(CommandHandler("menu", bot_module.cmd_menu))
            app.add_handler(CommandHandler("new", bot_module.cmd_new))
            app.add_handler(CommandHandler("sessions", bot_module.cmd_sessions))
            app.add_handler(CommandHandler("switch", bot_module.cmd_switch))
            app.add_handler(CommandHandler("end", bot_module.cmd_end))
            app.add_handler(CommandHandler("sub", bot_module.cmd_sub))
            app.add_handler(CommandHandler("agents", bot_module.cmd_agents))
            app.add_handler(CommandHandler("brain", bot_module.cmd_brain))
            app.add_handler(CommandHandler("inbox", bot_module.cmd_inbox))
            app.add_handler(CommandHandler("note", bot_module.cmd_note))
            app.add_handler(CommandHandler("model", bot_module.cmd_model))
            app.add_handler(CommandHandler("web", bot_module.cmd_web))
            app.add_handler(CommandHandler("settings", bot_module.cmd_settings))
            app.add_handler(CommandHandler("skills", bot_module.cmd_skills))
            app.add_handler(CommandHandler("service", bot_module.cmd_service))
            app.add_handler(CallbackQueryHandler(bot_module.handle_callback))
            app.add_handler(MessageHandler(filters.VOICE, bot_module.handle_voice))
            app.add_handler(MessageHandler(filters.AUDIO, bot_module.handle_audio))
            app.add_handler(MessageHandler(filters.PHOTO, bot_module.handle_photo))
            app.add_handler(MessageHandler(filters.Document.ALL, bot_module.handle_document))
            app.add_handler(MessageHandler(filters.VIDEO, bot_module.handle_video))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_module.handle_message))
            app.add_error_handler(bot_module.error_handler)

            self.application = app
            self.is_running = True
            self.start_time = time.time()

            self.log_queue.put(
                f"[SYSTEM] ✅ Zilla v{BOT_VERSION} is ONLINE! "
                f"Polling for Telegram messages..."
            )

            chat_bus.post_system("🟢 Zilla is online and ready.")

            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )

        except Exception as e:
            self.log_queue.put(f"[ERROR] Bot crashed: {e}")
            import traceback
            self.log_queue.put(traceback.format_exc())
        finally:
            self.is_running = False
            self.start_time = None
            self.application = None
            self.log_queue.put("[SYSTEM] Bot thread has exited.")


# ══════════════════════════════════════════════════════════
#  DESKTOP RUNNER — Sends messages directly to Zilla
# ══════════════════════════════════════════════════════════

class DesktopRunner:
    """Runs Zilla commands directly from the desktop GUI (bypasses Telegram)."""

    def __init__(self, log_queue: queue.Queue):
        self.log_queue = log_queue
        self._running = False

    def send_message(self, text: str, callback=None, source="desktop"):
        """Send a message to Zilla in a background thread."""
        if self._running:
            self.log_queue.put("[SYSTEM] A desktop request is already running...")
            return
        self._running = True
        thread = threading.Thread(
            target=self._run, args=(text, callback, source), daemon=True,
            name="Desktop-Runner",
        )
        thread.start()

    def _run(self, text: str, callback=None, source="desktop"):
        try:
            from agy_runner import run_agy_pty
            from chat_bus import chat_bus
            from sessions import SessionManager
            from config import STATE_FILE

            sessions = SessionManager(STATE_FILE)
            
            user_id = 0
            display_source = source
            user_name = "Desktop"
            if source.startswith("telegram:"):
                try:
                    user_id = int(source.split(":")[1])
                except ValueError:
                    user_id = 0
                display_source = "telegram"
                user_name = "Owner (via Desktop)"

            conv_id = sessions.get_conversation_id(user_id=user_id)

            chat_bus.post_user(text, user_id=user_id, user_name=user_name, source=display_source)
            chat_bus.set_typing(user_id)

            self.log_queue.put(f"[DESKTOP to {user_id}] Sending: {text[:80]}...")

            # Read settings for timeout and model
            settings = read_settings_json()
            timeout = settings.get("timeout", 600)

            response, detected_id = run_agy_pty(
                text, conv_id, timeout=timeout, progress_callback=None,
            )

            chat_bus.clear_typing(user_id)

            if detected_id and detected_id != conv_id:
                sessions.set_conversation_id(detected_id, user_id=user_id)

            sessions.increment_messages(user_id=user_id)

            # Format based on source
            if source == "desktop":
                try:
                    from desktop_formatter import format_for_desktop
                    display_response = format_for_desktop(response)
                except ImportError:
                    display_response = response
            else:
                display_response = response

            chat_bus.post_bot(display_response, user_id=user_id, session_name=sessions.get_active_name(user_id))

            # Detect file paths and post them as media to chat_bus
            from telegram_formatter import detect_file_paths
            import os
            file_paths = detect_file_paths(response)
            for fp in file_paths[:3]:
                media_type = "image" if fp.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')) else "document"
                chat_bus.post_bot("", user_id=user_id, session_name=sessions.get_active_name(user_id), file_path=fp, media_type=media_type, file_name=os.path.basename(fp))

            if callback:
                callback(display_response)

        except Exception as e:
            self.log_queue.put(f"[DESKTOP] Error: {e}")
            from chat_bus import chat_bus
            chat_bus.clear_typing(0)
            chat_bus.post_bot(f"Error: {e}", user_id=0)
        finally:
            self._running = False

    @property
    def is_busy(self):
        return self._running


# ══════════════════════════════════════════════════════════
#  REUSABLE UI COMPONENTS
# ══════════════════════════════════════════════════════════

class GlassCard(ctk.CTkFrame):
    """A card with glassmorphism-style styling."""
    def __init__(self, master, **kwargs):
        defaults = {
            "fg_color": DS.BG_CARD,
            "corner_radius": DS.CORNER_R,
            "border_width": 1,
            "border_color": DS.BORDER,
        }
        defaults.update(kwargs)
        super().__init__(master, **defaults)


class StatCard(GlassCard):
    """A compact stat display card."""
    def __init__(self, master, title: str, value: str, icon: str = "", **kwargs):
        super().__init__(master, **kwargs)

        self.grid_columnconfigure(0, weight=1)

        # Icon + title
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 2))

        if icon:
            ctk.CTkLabel(
                header, text=icon, font=ctk.CTkFont(size=16),
                text_color=DS.TEXT_MUTED,
            ).pack(side="left")

        ctk.CTkLabel(
            header, text=title, font=ctk.CTkFont(size=11),
            text_color=DS.TEXT_MUTED,
        ).pack(side="left", padx=(6, 0))

        # Value
        self.value_label = ctk.CTkLabel(
            self, text=value,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        )
        self.value_label.pack(padx=14, pady=(0, 12), anchor="w")

    def set_value(self, value: str):
        self.value_label.configure(text=value)


class NavButton(ctk.CTkButton):
    """Sidebar navigation button with active state."""
    def __init__(self, master, text, icon="", **kwargs):
        display = f"  {icon}  {text}" if icon else f"  {text}"
        defaults = {
            "corner_radius": DS.CORNER_R_SM,
            "height": 42,
            "font": ctk.CTkFont(size=13),
            "anchor": "w",
            "fg_color": "transparent",
            "text_color": DS.TEXT_SECONDARY,
            "hover_color": DS.BG_HOVER,
        }
        defaults.update(kwargs)
        super().__init__(master, text=display, **defaults)
        self._is_active = False

    def set_active(self, active: bool):
        self._is_active = active
        if active:
            self.configure(fg_color=DS.SIDEBAR_ACTIVE, text_color=DS.TEXT_PRIMARY)
        else:
            self.configure(fg_color="transparent", text_color=DS.TEXT_SECONDARY)


# ══════════════════════════════════════════════════════════
#  CHAT BUBBLE WIDGET
# ══════════════════════════════════════════════════════════

class ChatBubble(ctk.CTkFrame):
    """A single chat message bubble."""

    def __init__(self, master, text: str, role: str = "bot",
                 sender_name: str = "", timestamp: str = "", source: str = "",
                 file_path: str = None, media_type: str = None, file_name: str = None):
        is_user = (role == "user")
        is_system = (role == "system")

        if is_system:
            bg = DS.BUBBLE_SYSTEM
            border = DS.BORDER
        elif is_user:
            bg = DS.BUBBLE_USER
            border = "#4338ca"
        else:
            bg = DS.BUBBLE_BOT
            border = DS.BORDER

        super().__init__(
            master, fg_color=bg, corner_radius=DS.CORNER_R,
            border_width=1, border_color=border,
        )

        # Container for alignment
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=8)

        # Header (sender + time)
        if sender_name or timestamp:
            header = ctk.CTkFrame(inner, fg_color="transparent")
            header.pack(fill="x")

            if sender_name:
                name_color = DS.ACCENT_PRIMARY if is_user else DS.ACCENT_SUCCESS
                ctk.CTkLabel(
                    header, text=sender_name,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color=name_color,
                ).pack(side="left")

            if source and source != "telegram":
                src_badge = "💻" if source == "desktop" else f"[{source}]"
                ctk.CTkLabel(
                    header, text=f"  {src_badge}",
                    font=ctk.CTkFont(size=9),
                    text_color=DS.TEXT_FAINT,
                ).pack(side="left")

            if timestamp:
                ctk.CTkLabel(
                    header, text=timestamp,
                    font=ctk.CTkFont(size=9),
                    text_color=DS.TEXT_FAINT,
                ).pack(side="right")

        # Message text
        if text:
            msg_label = ctk.CTkLabel(
                inner, text=text,
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color=DS.TEXT_PRIMARY,
                wraplength=550,
                justify="left",
                anchor="w",
            )
            msg_label.pack(fill="x", pady=(4, 0), anchor="w")

        # Media Rendering
        if file_path and os.path.isfile(file_path):
            if media_type == "image":
                try:
                    img = PILImage.open(file_path)
                    # Scale to fit max 300px width
                    ratio = min(300 / img.width, 300 / img.height)
                    if ratio < 1:
                        new_size = (int(img.width * ratio), int(img.height * ratio))
                        img = img.resize(new_size, PILImage.LANCZOS)
                    else:
                        new_size = (img.width, img.height)
                    ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=new_size)
                    img_label = ctk.CTkLabel(inner, image=ctk_img, text="")
                    img_label.pack(padx=12, pady=(4, 0), anchor="w")
                except Exception as e:
                    logging.error(f"Failed to render image {file_path}: {e}")
            else:
                fname = file_name or os.path.basename(file_path)
                file_label = ctk.CTkLabel(
                    inner, text=f"📎 {fname}", 
                    font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                    text_color=DS.ACCENT_PRIMARY,
                    cursor="hand2"
                )
                file_label.pack(padx=12, pady=(4, 0), anchor="w")
                file_label.bind("<Button-1>", lambda e, p=file_path: os.startfile(p))


class TypingIndicator(ctk.CTkFrame):
    """Animated typing indicator dots."""
    def __init__(self, master):
        super().__init__(master, fg_color=DS.BUBBLE_BOT, corner_radius=DS.CORNER_R,
                         border_width=1, border_color=DS.BORDER)
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(padx=12, pady=8)

        ctk.CTkLabel(
            inner, text="Zilla",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=DS.ACCENT_SUCCESS,
        ).pack(side="left")

        self._dots_label = ctk.CTkLabel(
            inner, text="  is thinking ●○○",
            font=ctk.CTkFont(size=11),
            text_color=DS.TEXT_MUTED,
        )
        self._dots_label.pack(side="left")

        self._dot_state = 0
        self._animate()

    def _animate(self):
        patterns = ["●○○", "○●○", "○○●", "○●○"]
        self._dot_state = (self._dot_state + 1) % len(patterns)
        self._dots_label.configure(text=f"  is thinking {patterns[self._dot_state]}")
        self.after(400, self._animate)


# ══════════════════════════════════════════════════════════
#  SETTINGS CATEGORY BUTTON (macOS-style)
# ══════════════════════════════════════════════════════════

class SettingsCategoryBtn(ctk.CTkFrame):
    """macOS System Preferences-style category button."""
    def __init__(self, master, icon: str, label: str, command=None, **kwargs):
        super().__init__(master, fg_color=DS.BG_CARD, corner_radius=DS.CORNER_R,
                         border_width=1, border_color=DS.BORDER,
                         cursor="hand2", width=120, height=90, **kwargs)
        self.grid_propagate(False)
        self._command = command
        self._is_active = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure((0, 1), weight=1)

        self._icon_label = ctk.CTkLabel(
            self, text=icon, font=ctk.CTkFont(size=28),
            text_color=DS.TEXT_SECONDARY,
        )
        self._icon_label.grid(row=0, column=0, pady=(12, 0))

        self._text_label = ctk.CTkLabel(
            self, text=label, font=ctk.CTkFont(size=11),
            text_color=DS.TEXT_SECONDARY,
        )
        self._text_label.grid(row=1, column=0, pady=(0, 10))

        # Bind click to entire frame and children
        for widget in [self, self._icon_label, self._text_label]:
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    def _on_click(self, event=None):
        if self._command:
            self._command()

    def _on_enter(self, event=None):
        if not self._is_active:
            self.configure(fg_color=DS.BG_HOVER, border_color=DS.BORDER_HOVER)

    def _on_leave(self, event=None):
        if not self._is_active:
            self.configure(fg_color=DS.BG_CARD, border_color=DS.BORDER)

    def set_active(self, active: bool):
        self._is_active = active
        if active:
            self.configure(fg_color=DS.ACCENT_MUTED, border_color=DS.ACCENT_PRIMARY)
            self._icon_label.configure(text_color=DS.TEXT_PRIMARY)
            self._text_label.configure(text_color=DS.TEXT_PRIMARY)
        else:
            self.configure(fg_color=DS.BG_CARD, border_color=DS.BORDER)
            self._icon_label.configure(text_color=DS.TEXT_SECONDARY)
            self._text_label.configure(text_color=DS.TEXT_SECONDARY)


# ══════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════════════════

class ZillaApp(ctk.CTk):

    def __init__(self):
        super().__init__()

        # ── Window config ──
        self.title("Zilla — Universal AI Engine")
        self.geometry("1200x800")
        self.minsize(1000, 650)

        # Make the window look more premium on Windows
        try:
            self.iconbitmap(default="")  # Remove default icon
        except Exception:
            pass

        # ── Core state ──
        self.log_queue = queue.Queue()
        self.bot = BotController(self.log_queue)
        self.desktop_runner = DesktopRunner(self.log_queue)
        self.active_view = "dashboard"
        self._chat_last_id = 0
        self._log_messages = deque(maxlen=200)
        self._show_activity_log = False
        self._active_settings_cat = "ai_model"
        self._chat_source = "desktop"  # "desktop" or telegram user id

        # ── Grid layout: sidebar + content ──
        self.grid_columnconfigure(0, weight=0, minsize=DS.SIDEBAR_W)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── Build all views ──
        self._build_sidebar()
        self._build_dashboard()
        self._build_skills()
        self._build_agents()
        self._build_sessions()
        self._build_settings()
        self._build_integrations()
        self._build_users()

        # ── Show default view ──
        self._show_view("dashboard")

        # ── Restore persisted backend ──
        try:
            saved_backend = read_settings_json().get("cli_backend")
            if saved_backend:
                from cli_router import cli_router
                cli_router.active_id = saved_backend
        except Exception:
            pass

        # ── Start polling ──
        self._poll_logs()
        self._poll_chat()
        self._poll_agents()
        self._update_stats()

        # ── Keyboard shortcuts ──
        self.bind("<Control-n>", lambda e: self._new_session())
        self.bind("<Control-slash>", lambda e: self._show_about())
        self.bind("<Escape>", lambda e: self._show_view("dashboard"))

        # ── Window close handler ──
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Stealth / Tray Setup ──
        self._setup_tray()
        self.bind_all("<Control-Alt-Shift-z>", self._toggle_stealth)
        try:
            keyboard.add_hotkey('ctrl+alt+shift+z', self._toggle_stealth_from_global)
        except Exception as e:
            print(f"Warning: Failed to bind global hotkey: {e}")

    def _create_tray_icon_image(self):
        """Create a cool geometric cube icon."""
        size = 64
        img = PILImage.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Front face
        draw.polygon([(16, 24), (32, 16), (48, 24), (32, 32)], fill='#6366f1')
        # Top face  
        draw.polygon([(16, 24), (32, 32), (32, 48), (16, 40)], fill='#818cf8')
        # Right face
        draw.polygon([(32, 32), (48, 24), (48, 40), (32, 48)], fill='#4f46e5')
        return img

    def _setup_tray(self):
        self._tray_icon = pystray.Icon(
            "Zilla",
            self._create_tray_icon_image(),
            "Zilla Engine",
            menu=pystray.Menu(
                pystray.MenuItem("Show", self._show_from_tray),
                pystray.MenuItem("Exit", self._exit_from_tray)
            )
        )
        self._is_hidden = False

    def _hide_to_tray(self):
        self.withdraw()
        self._is_hidden = True
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _show_from_tray(self, icon, item):
        self._tray_icon.stop()
        self.after(100, self.deiconify)
        self._is_hidden = False

    def _exit_from_tray(self, icon, item):
        self._tray_icon.stop()
        self.after(100, self._on_close)

    def _toggle_stealth(self, event=None):
        if self._is_hidden:
            if hasattr(self, '_tray_icon') and getattr(self._tray_icon, 'visible', False):
                self._tray_icon.stop()
            self.after(100, self.deiconify)
            self._is_hidden = False
        else:
            self.withdraw()
            self._is_hidden = True

    def _toggle_stealth_from_global(self):
        self.after(0, self._toggle_stealth)

    # ══════════════════════════════════════════════════════
    #  SIDEBAR
    # ══════════════════════════════════════════════════════

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(
            self, width=DS.SIDEBAR_W, corner_radius=0, fg_color=DS.SIDEBAR_BG,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        # Logo
        logo_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        logo_frame.pack(fill="x", padx=16, pady=(24, 0))

        ctk.CTkLabel(
            logo_frame, text="🦖 Zilla",
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=DS.ACCENT_PRIMARY,
        ).pack(anchor="w")

        ctk.CTkLabel(
            logo_frame, text="Universal AI Engine v4.0",
            font=ctk.CTkFont(size=10),
            text_color=DS.TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 4))

        # Separator
        ctk.CTkFrame(
            self.sidebar, height=1, fg_color=DS.BORDER,
        ).pack(fill="x", padx=16, pady=(12, 12))

        # Nav buttons — UPDATED: No separate Chat, added Skills & Integrations
        self.nav_buttons = {}
        nav_items = [
            ("dashboard",    "Dashboard",    "◉"),
            ("skills",       "Skills",       "⚡"),
            ("sessions",     "Sessions",     "◇"),
            ("agents",       "Agents",       "◎"),
            ("integrations", "Integrations", "🔗"),
            ("settings",     "Settings",     "◆"),
            ("users",        "Users",        "◊"),
        ]

        for view_id, label, icon in nav_items:
            btn = NavButton(
                self.sidebar, text=label, icon=icon,
                command=lambda v=view_id: self._show_view(v),
            )
            btn.pack(fill="x", padx=10, pady=1)
            self.nav_buttons[view_id] = btn

        # Spacer
        ctk.CTkFrame(self.sidebar, fg_color="transparent", height=1).pack(
            fill="both", expand=True,
        )

        # Status area
        status_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        status_frame.pack(fill="x", padx=16, pady=(0, 8))

        dot_row = ctk.CTkFrame(status_frame, fg_color="transparent")
        dot_row.pack(fill="x")

        self.status_dot = ctk.CTkLabel(
            dot_row, text="●", font=ctk.CTkFont(size=14),
            text_color=DS.OFFLINE_COLOR,
        )
        self.status_dot.pack(side="left")

        self.status_text = ctk.CTkLabel(
            dot_row, text="OFFLINE",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=DS.OFFLINE_COLOR,
        )
        self.status_text.pack(side="left", padx=6)

        self.uptime_label_sidebar = ctk.CTkLabel(
            status_frame, text="",
            font=ctk.CTkFont(size=10),
            text_color=DS.TEXT_FAINT,
        )
        self.uptime_label_sidebar.pack(anchor="w", pady=(2, 0))

        # Version
        ctk.CTkLabel(
            self.sidebar, text="Powered by Zilla Engine",
            font=ctk.CTkFont(size=9),
            text_color=DS.TEXT_FAINT,
        ).pack(pady=(0, 12))

    # ══════════════════════════════════════════════════════
    #  VIEW SWITCHING
    # ══════════════════════════════════════════════════════

    def _show_view(self, view_name: str):
        all_frames = {
            "dashboard":    self.dashboard_frame,
            "skills":       self.skills_frame,
            "agents":       self.agents_frame,
            "sessions":     self.sessions_frame,
            "settings":     self.settings_frame,
            "integrations": self.integrations_frame,
            "users":        self.users_frame,
        }

        for frame in all_frames.values():
            frame.grid_forget()

        for btn in self.nav_buttons.values():
            btn.set_active(False)

        if view_name in all_frames:
            all_frames[view_name].grid(row=0, column=1, sticky="nsew")
            self.nav_buttons[view_name].set_active(True)

        self.active_view = view_name

        # Refresh data when switching views
        if view_name == "settings":
            self._reload_settings_ui()
        elif view_name == "sessions":
            self._reload_sessions_ui()
        elif view_name == "users":
            self._reload_users_ui()
        elif view_name == "agents":
            self._refresh_agents()
        elif view_name == "skills":
            self._reload_skills_ui()

    # ══════════════════════════════════════════════════════
    #  DASHBOARD VIEW (Combined: Stats + Chat + Activity)
    # ══════════════════════════════════════════════════════

    def _build_dashboard(self):
        self.dashboard_frame = ctk.CTkFrame(self, fg_color=DS.BG_BASE, corner_radius=0)
        self.dashboard_frame.grid_columnconfigure(0, weight=1)
        self.dashboard_frame.grid_rowconfigure(3, weight=1)  # Chat area expands

        # ── Row 0: Control bar ──
        control_bar = GlassCard(self.dashboard_frame, height=70)
        control_bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        control_bar.grid_propagate(False)

        self.btn_start = ctk.CTkButton(
            control_bar, text="▶  Start Bot",
            fg_color=DS.ACCENT_SUCCESS, hover_color="#1a9f4a",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40, width=130, corner_radius=DS.CORNER_R_SM,
            text_color="#ffffff",
            command=self._start_bot,
        )
        self.btn_start.pack(side="left", padx=(14, 8), pady=14)

        self.btn_stop = ctk.CTkButton(
            control_bar, text="■  Stop Bot",
            fg_color="#9f1239", hover_color="#881337",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            height=40, width=130, corner_radius=DS.CORNER_R_SM,
            text_color="#fecdd3",
            state="disabled",
            command=self._stop_bot,
        )
        self.btn_stop.pack(side="left", padx=4, pady=14)

        # Activity log toggle
        self.btn_toggle_logs = ctk.CTkButton(
            control_bar, text="📋 Activity",
            width=90, height=32, corner_radius=6,
            font=ctk.CTkFont(size=11),
            fg_color=DS.BG_ELEVATED, hover_color=DS.BG_HOVER,
            text_color=DS.TEXT_SECONDARY,
            command=self._toggle_activity_log,
        )
        self.btn_toggle_logs.pack(side="left", padx=(16, 4), pady=14)

        # Info on right
        info_right = ctk.CTkFrame(control_bar, fg_color="transparent")
        info_right.pack(side="right", padx=14)

        self.model_label = ctk.CTkLabel(
            info_right,
            text=f"Model: {read_settings_json().get('model', '—')}",
            font=ctk.CTkFont(size=11), text_color=DS.TEXT_SECONDARY,
        )
        self.model_label.pack(anchor="e")

        self.uptime_label = ctk.CTkLabel(
            info_right, text="Uptime: —",
            font=ctk.CTkFont(size=11), text_color=DS.TEXT_SECONDARY,
        )
        self.uptime_label.pack(anchor="e")

        # ── Row 1: Stats row ──
        stats_row = ctk.CTkFrame(self.dashboard_frame, fg_color="transparent")
        stats_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        stats_row.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.stat_uptime = StatCard(stats_row, "Uptime", "—", icon="⏱")
        self.stat_uptime.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.stat_messages = StatCard(stats_row, "Messages", "0", icon="✉")
        self.stat_messages.grid(row=0, column=1, sticky="ew", padx=4)

        self.stat_sessions = StatCard(stats_row, "Sessions", "0", icon="◇")
        self.stat_sessions.grid(row=0, column=2, sticky="ew", padx=4)

        self.stat_agents = StatCard(stats_row, "Agents", "0", icon="◎")
        self.stat_agents.grid(row=0, column=3, sticky="ew", padx=(4, 0))

        # ── Row 2: Chat header with source dropdown ──
        chat_header = GlassCard(self.dashboard_frame, height=48)
        chat_header.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 4))
        chat_header.grid_propagate(False)

        ctk.CTkLabel(
            chat_header, text="💬 Chat",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(side="left", padx=(14, 10), pady=8)

        # Source dropdown — select Desktop or a Telegram user
        self._chat_source_options = ["💻 Desktop (Direct)"]
        self._update_chat_source_options()

        self.chat_source_var = ctk.StringVar(value=self._chat_source_options[0])
        self.chat_source_dropdown = ctk.CTkOptionMenu(
            chat_header, variable=self.chat_source_var,
            values=self._chat_source_options,
            height=30, width=220, corner_radius=6,
            font=ctk.CTkFont(size=11),
            fg_color=DS.BG_SURFACE, button_color=DS.ACCENT_PRIMARY,
            button_hover_color=DS.ACCENT_HOVER,
            text_color=DS.TEXT_PRIMARY,
            command=self._on_chat_source_changed,
        )
        self.chat_source_dropdown.pack(side="left", padx=4, pady=8)

        # Refresh button
        ctk.CTkButton(
            chat_header, text="↻ Refresh", width=60, height=30,
            corner_radius=6, font=ctk.CTkFont(size=11),
            fg_color=DS.BG_DEEP, hover_color=DS.BG_SURFACE,
            command=self._poll_chat
        ).pack(side="left", padx=4, pady=8)

        self.chat_source_label = ctk.CTkLabel(
            chat_header, text="Source: Desktop (Auto-refresh: 200ms)",
            font=ctk.CTkFont(size=10),
            text_color=DS.ACCENT_TEAL,
        )
        self.chat_source_label.pack(side="right", padx=14, pady=8)

        # ── Row 3: Chat area (scrollable) ──
        self.chat_scroll = ctk.CTkScrollableFrame(
            self.dashboard_frame, fg_color=DS.BG_DEEP,
            corner_radius=DS.CORNER_R_SM,
            border_width=1,
            border_color=DS.BORDER,
        )
        self.chat_scroll.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 4))
        self.chat_scroll.grid_columnconfigure(0, weight=1)

        # Typing indicator (hidden by default)
        self.typing_indicator = None

        # ── Row 4: Input bar ──
        input_bar = GlassCard(self.dashboard_frame, height=60)
        input_bar.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 16))
        input_bar.grid_propagate(False)

        self.chat_input = ctk.CTkEntry(
            input_bar,
            placeholder_text="Message Zilla...",
            height=38, corner_radius=DS.CORNER_R_SM,
            font=ctk.CTkFont(size=13),
            fg_color=DS.BG_SURFACE,
            border_color=DS.BORDER,
            text_color=DS.TEXT_PRIMARY,
        )
        self.chat_input.pack(side="left", fill="x", expand=True, padx=(12, 8), pady=10)
        self.chat_input.bind("<Return>", self._on_chat_send)

        self.btn_send = ctk.CTkButton(
            input_bar, text="Send ▸",
            width=80, height=38, corner_radius=DS.CORNER_R_SM,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
            text_color="#ffffff",
            command=self._on_chat_send,
        )
        self.btn_send.pack(side="right", padx=(0, 12), pady=10)

        # ── Activity Log overlay (hidden by default) ──
        self.activity_log_frame = GlassCard(
            self.dashboard_frame, height=200,
            border_color=DS.ACCENT_MUTED,
        )
        self.activity_log_visible = False

        self.log_display = ctk.CTkTextbox(
            self.activity_log_frame,
            font=ctk.CTkFont(family="Cascadia Code, Consolas, monospace", size=11),
            fg_color=DS.BG_SURFACE,
            border_color=DS.BORDER,
            border_width=1,
            corner_radius=DS.CORNER_R_SM,
            wrap="word",
            text_color=DS.TEXT_SECONDARY,
        )
        self.log_display.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_display.insert("0.0",
            "Zilla Engine v4.0\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Ready. Click ▶ Start Bot to connect.\n\n"
        )

    def _update_chat_source_options(self):
        """Build the chat source dropdown options from authorized users."""
        options = ["💻 Desktop (Direct)"]
        try:
            users = read_users_json()
            env = read_env()
            owner_id = env.get("TELEGRAM_OWNER_ID", "")
            if owner_id:
                options.append(f"📱 Owner ({owner_id})")
            for uid, info in users.items():
                name = info.get("name", f"User {uid}")
                options.append(f"📱 {name} ({uid})")
        except Exception:
            pass
        self._chat_source_options = options

    def _on_chat_source_changed(self, choice):
        """Handle chat source dropdown change."""
        if "Desktop" in choice:
            self._chat_source = "desktop"
            self.chat_source_label.configure(text="Source: Desktop", text_color=DS.ACCENT_TEAL)
        else:
            # Extract user ID from "📱 Name (12345)"
            import re
            match = re.search(r'\((\d+)\)', choice)
            if match:
                self._chat_source = f"telegram:{match.group(1)}"
                self.chat_source_label.configure(
                    text=f"Source: Telegram → {match.group(1)}",
                    text_color=DS.ACCENT_INFO,
                )

    def _toggle_activity_log(self):
        """Toggle the activity log overlay on the dashboard."""
        if self.activity_log_visible:
            self.activity_log_frame.grid_forget()
            self.activity_log_visible = False
            self.btn_toggle_logs.configure(
                text="📋 Activity", fg_color=DS.BG_ELEVATED,
            )
        else:
            # Show between stats and chat
            self.activity_log_frame.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 8))
            self.activity_log_visible = True
            self.btn_toggle_logs.configure(
                text="✕ Hide Log", fg_color=DS.ACCENT_MUTED,
            )

    def _on_chat_send(self, event=None):
        """Send a message from the chat input."""
        text = self.chat_input.get().strip()
        if not text:
            return

        self.chat_input.delete(0, "end")
        source = "desktop" if self._chat_source == "desktop" else self._chat_source
        self.desktop_runner.send_message(text, source=source)

    def _poll_chat(self):
        """Poll chat_bus for new messages and render them."""
        try:
            try:
                from chat_bus import chat_bus
            except ImportError:
                self.after(500, self._poll_chat)
                return

            new_msgs = chat_bus.get_since(self._chat_last_id)
            
            from chat_bus import MessageRole
            if self._chat_source == "desktop":
                filtered = [m for m in new_msgs if m.source == "desktop" or (m.role == MessageRole.BOT and m.user_id == 0) or m.source == "system"]
            elif self._chat_source.startswith("telegram:"):
                uid = int(self._chat_source.split(":")[1])
                filtered = [m for m in new_msgs if (m.source == "telegram" and m.user_id == uid) or (m.role == MessageRole.BOT and m.user_id == uid) or m.source == "system"]
            else:
                filtered = new_msgs
                
            for msg in filtered:
                self._render_chat_message(msg)
                
            if new_msgs:
                self._chat_last_id = max(m.message_id for m in new_msgs)

            # Typing indicator
            if chat_bus.is_typing():
                if not self.typing_indicator or not self.typing_indicator.winfo_exists():
                    self.typing_indicator = TypingIndicator(self.chat_scroll)
                    self.typing_indicator.pack(fill="x", padx=8, pady=4, anchor="w")
            else:
                if self.typing_indicator and self.typing_indicator.winfo_exists():
                    self.typing_indicator.destroy()
                    self.typing_indicator = None

        except Exception:
            pass

        self.after(200, self._poll_chat)

    def _render_chat_message(self, msg):
        """Render a ChatMessage as a bubble in the chat view."""
        from chat_bus import MessageRole

        timestamp = datetime.fromtimestamp(msg.timestamp).strftime("%H:%M:%S")

        if msg.role == MessageRole.USER:
            sender = msg.user_name or "User"
            role = "user"
        elif msg.role == MessageRole.BOT:
            sender = "Zilla"
            role = "bot"
        else:
            sender = "System"
            role = "system"

        # Truncate very long messages for display
        display_text = msg.text
        if len(display_text) > 2000:
            display_text = display_text[:2000] + "\n\n... [truncated]"

        bubble = ChatBubble(
            self.chat_scroll,
            text=display_text,
            role=role,
            sender_name=sender,
            timestamp=timestamp,
            source=msg.source,
            file_path=msg.file_path,
            media_type=msg.media_type,
            file_name=msg.file_name,
        )

        # Alignment
        if msg.role == MessageRole.USER:
            bubble.pack(fill="x", padx=(80, 8), pady=3, anchor="e")
        elif msg.role == MessageRole.SYSTEM:
            bubble.pack(fill="x", padx=40, pady=3)
        else:
            bubble.pack(fill="x", padx=(8, 80), pady=3, anchor="w")

        # Remove typing indicator if bot responded
        if msg.role == MessageRole.BOT:
            if self.typing_indicator and self.typing_indicator.winfo_exists():
                self.typing_indicator.destroy()
                self.typing_indicator = None

        # Auto-scroll
        self.chat_scroll._parent_canvas.yview_moveto(1.0)

    # ══════════════════════════════════════════════════════
    #  SKILLS VIEW (NEW)
    # ══════════════════════════════════════════════════════

    def _build_skills(self):
        self.skills_frame = ctk.CTkFrame(self, fg_color=DS.BG_BASE, corner_radius=0)
        self.skills_frame.grid_columnconfigure(0, weight=1)
        self.skills_frame.grid_rowconfigure(1, weight=1)

        # Header
        header = GlassCard(self.skills_frame, height=56)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        header.grid_propagate(False)

        ctk.CTkLabel(
            header, text="⚡ Skills Manager",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(side="left", padx=14, pady=12)

        ctk.CTkButton(
            header, text="+ Create Skill", width=120, height=32,
            corner_radius=6, font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=DS.ACCENT_SUCCESS, hover_color="#1a9f4a",
            text_color="#ffffff",
            command=self._create_skill,
        ).pack(side="right", padx=14)

        ctk.CTkButton(
            header, text="↻ Refresh", width=80, height=32,
            corner_radius=6, font=ctk.CTkFont(size=11),
            fg_color=DS.BG_ELEVATED, hover_color=DS.BG_HOVER,
            text_color=DS.TEXT_SECONDARY,
            command=self._reload_skills_ui,
        ).pack(side="right", padx=4)

        # Skills list
        self.skills_list_frame = ctk.CTkScrollableFrame(
            self.skills_frame, fg_color=DS.BG_DEEP,
            corner_radius=DS.CORNER_R_SM,
            border_width=1, border_color=DS.BORDER,
        )
        self.skills_list_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.skills_list_frame.grid_columnconfigure(0, weight=1)

    def _reload_skills_ui(self):
        """Reload the skills list from disk."""
        for w in self.skills_list_frame.winfo_children():
            w.destroy()

        try:
            from skills_manager import SkillsManager
            sm = SkillsManager(SKILLS_DIR)
            skills = sm.list_skills()
        except Exception:
            skills = []

        if not skills:
            ctk.CTkLabel(
                self.skills_list_frame,
                text="No skills installed.\n\nClick '+ Create Skill' to make one,\nor install skills to:\n"
                     + SKILLS_DIR,
                font=ctk.CTkFont(size=13), text_color=DS.TEXT_MUTED,
                justify="center",
            ).pack(pady=60)
            return

        for skill in skills:
            self._build_skill_card(skill)

    def _build_skill_card(self, skill):
        """Build a single skill card in the skills list."""
        card = GlassCard(self.skills_list_frame)
        card.pack(fill="x", pady=3)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=12)
        inner.grid_columnconfigure(1, weight=1)

        # Icon
        ctk.CTkLabel(
            inner, text="🧩", font=ctk.CTkFont(size=20),
        ).grid(row=0, column=0, rowspan=2, padx=(0, 10), sticky="n")

        # Name
        ctk.CTkLabel(
            inner, text=skill.name,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).grid(row=0, column=1, sticky="w")

        # Description
        desc = skill.description or "No description"
        ctk.CTkLabel(
            inner, text=desc,
            font=ctk.CTkFont(size=11),
            text_color=DS.TEXT_MUTED,
            wraplength=450,
        ).grid(row=1, column=1, sticky="w", pady=(2, 0))

        # Path label
        ctk.CTkLabel(
            inner, text=f"📂 {skill.folder_name}",
            font=ctk.CTkFont(size=9),
            text_color=DS.TEXT_FAINT,
        ).grid(row=2, column=1, sticky="w", pady=(4, 0))

        # Buttons
        btn_frame = ctk.CTkFrame(inner, fg_color="transparent")
        btn_frame.grid(row=0, column=2, rowspan=3, padx=(8, 0), sticky="e")

        ctk.CTkButton(
            btn_frame, text="View", width=55, height=28,
            corner_radius=6, font=ctk.CTkFont(size=10),
            fg_color=DS.ACCENT_INFO, hover_color="#2563eb",
            text_color="#ffffff",
            command=lambda s=skill: self._view_skill(s),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btn_frame, text="Edit", width=55, height=28,
            corner_radius=6, font=ctk.CTkFont(size=10),
            fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
            text_color="#ffffff",
            command=lambda s=skill: self._view_skill(s, edit_mode=True),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btn_frame, text="Remove", width=65, height=28,
            corner_radius=6, font=ctk.CTkFont(size=10),
            fg_color=DS.ACCENT_DANGER, hover_color="#b91c1c",
            text_color="#ffffff",
            command=lambda s=skill: self._remove_skill(s),
        ).pack(side="left", padx=2)

    def _view_skill(self, skill, edit_mode=False):
        """Open a modal to view/edit the SKILL.md content."""
        try:
            from skills_manager import SkillsManager
            sm = SkillsManager(SKILLS_DIR)
            content = sm.get_skill_content(skill.folder_name)
        except Exception as e:
            content = f"Error reading SKILL.md: {e}"

        if not content:
            content = "SKILL.md not found or empty."

        # Create modal window
        modal = ctk.CTkToplevel(self)
        title_prefix = "Edit" if edit_mode else "View"
        modal.title(f"{title_prefix} Skill: {skill.name}")
        modal.geometry("800x600")
        modal.transient(self)
        modal.grab_set()
        modal.configure(fg_color=DS.BG_BASE)

        # Header bar
        header = ctk.CTkFrame(modal, fg_color=DS.BG_SURFACE, height=44, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text=f"{'✏️' if edit_mode else '📄'} {skill.name}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(side="left", padx=14, pady=8)

        path_label = ctk.CTkLabel(
            header, text=f"📂 {skill.folder_name}/SKILL.md",
            font=ctk.CTkFont(size=10), text_color=DS.TEXT_FAINT,
        )
        path_label.pack(side="right", padx=14)

        # Editor
        textbox = ctk.CTkTextbox(
            modal, font=ctk.CTkFont(family="Cascadia Code, Consolas", size=12),
            fg_color=DS.BG_SURFACE, text_color=DS.TEXT_PRIMARY,
            wrap="word", border_width=1, border_color=DS.BORDER,
            corner_radius=DS.CORNER_R_SM,
        )
        textbox.pack(fill="both", expand=True, padx=16, pady=12)
        textbox.insert("0.0", content)

        if not edit_mode:
            textbox.configure(state="disabled")

        # Status + buttons bar
        bottom_bar = ctk.CTkFrame(modal, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=16, pady=(0, 12))

        status_label = ctk.CTkLabel(
            bottom_bar, text="", font=ctk.CTkFont(size=11),
            text_color=DS.ACCENT_SUCCESS,
        )
        status_label.pack(side="left")

        ctk.CTkButton(
            bottom_bar, text="Close", width=90, height=36,
            corner_radius=DS.CORNER_R_SM,
            fg_color=DS.BG_ELEVATED, hover_color=DS.BG_HOVER,
            text_color=DS.TEXT_SECONDARY,
            command=modal.destroy,
        ).pack(side="right", padx=4)

        if edit_mode:
            def save_skill():
                new_content = textbox.get("0.0", "end").strip()
                try:
                    skill_path = os.path.join(SKILLS_DIR, skill.folder_name, "SKILL.md")
                    with open(skill_path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    status_label.configure(
                        text="✓ Saved successfully!", text_color=DS.ACCENT_SUCCESS,
                    )
                    self.log_queue.put(f"[SYSTEM] Updated skill: {skill.name}")
                    self._reload_skills_ui()
                except Exception as e:
                    status_label.configure(
                        text=f"✗ Error: {e}", text_color=DS.ACCENT_DANGER,
                    )

            ctk.CTkButton(
                bottom_bar, text="💾 Save", width=100, height=36,
                corner_radius=DS.CORNER_R_SM,
                font=ctk.CTkFont(weight="bold"),
                fg_color=DS.ACCENT_SUCCESS, hover_color="#1a9f4a",
                text_color="#ffffff",
                command=save_skill,
            ).pack(side="right", padx=4)
        else:
            # Toggle to edit mode button
            def switch_to_edit():
                modal.destroy()
                self._view_skill(skill, edit_mode=True)

            ctk.CTkButton(
                bottom_bar, text="✏️ Edit", width=100, height=36,
                corner_radius=DS.CORNER_R_SM,
                fg_color=DS.ACCENT_INFO, hover_color="#2563eb",
                text_color="#ffffff",
                command=switch_to_edit,
            ).pack(side="right", padx=4)

    def _remove_skill(self, skill):
        """Remove a skill after logging it."""
        try:
            from skills_manager import SkillsManager
            sm = SkillsManager(SKILLS_DIR)
            if sm.remove_skill(skill.folder_name):
                self.log_queue.put(f"[SYSTEM] Removed skill: {skill.name}")
            else:
                self.log_queue.put(f"[SYSTEM] Failed to remove skill: {skill.name}")
        except Exception as e:
            self.log_queue.put(f"[SYSTEM] Error removing skill: {e}")
        self._reload_skills_ui()

    def _create_skill(self):
        """Create a new skill with a template SKILL.md."""
        modal = ctk.CTkToplevel(self)
        modal.title("Create New Skill")
        modal.geometry("500x350")
        modal.transient(self)
        modal.grab_set()

        # Skill name
        ctk.CTkLabel(
            modal, text="Skill Name:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(anchor="w", padx=20, pady=(20, 4))

        name_entry = ctk.CTkEntry(
            modal, placeholder_text="e.g. web-scraper",
            height=38, corner_radius=6,
            fg_color=DS.BG_SURFACE, border_color=DS.BORDER,
            text_color=DS.TEXT_PRIMARY,
        )
        name_entry.pack(fill="x", padx=20, pady=(0, 12))

        # Description
        ctk.CTkLabel(
            modal, text="Description:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(anchor="w", padx=20, pady=(0, 4))

        desc_entry = ctk.CTkEntry(
            modal, placeholder_text="What does this skill do?",
            height=38, corner_radius=6,
            fg_color=DS.BG_SURFACE, border_color=DS.BORDER,
            text_color=DS.TEXT_PRIMARY,
        )
        desc_entry.pack(fill="x", padx=20, pady=(0, 20))

        status_label = ctk.CTkLabel(
            modal, text="", font=ctk.CTkFont(size=11),
            text_color=DS.ACCENT_SUCCESS,
        )
        status_label.pack(padx=20)

        def do_create():
            name = name_entry.get().strip()
            desc = desc_entry.get().strip()
            if not name:
                status_label.configure(text="Please enter a skill name.", text_color=DS.ACCENT_DANGER)
                return

            folder_name = name.lower().replace(" ", "-").replace("_", "-")
            skill_dir = os.path.join(SKILLS_DIR, folder_name)

            if os.path.exists(skill_dir):
                status_label.configure(text=f"Skill '{folder_name}' already exists!", text_color=DS.ACCENT_DANGER)
                return

            try:
                os.makedirs(skill_dir, exist_ok=True)
                os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
                os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)

                skill_md = f"""---
name: {name}
description: {desc or 'A custom Zilla skill'}
---

# {name}

## Instructions
[Add detailed instructions for this skill here]

## Inputs
[What information does this skill need?]

## Output Format
[How should results be formatted?]

## Examples
[Add usage examples here]
"""
                with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                    f.write(skill_md)

                self.log_queue.put(f"[SYSTEM] Created skill: {name} at {skill_dir}")
                modal.destroy()
                self._reload_skills_ui()
            except Exception as e:
                status_label.configure(text=f"Error: {e}", text_color=DS.ACCENT_DANGER)

        ctk.CTkButton(
            modal, text="Create Skill", width=160, height=42,
            corner_radius=DS.CORNER_R_SM,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=DS.ACCENT_SUCCESS, hover_color="#1a9f4a",
            text_color="#ffffff",
            command=do_create,
        ).pack(pady=16)

    # ══════════════════════════════════════════════════════
    #  AGENTS VIEW
    # ══════════════════════════════════════════════════════

    def _build_agents(self):
        self.agents_frame = ctk.CTkFrame(self, fg_color=DS.BG_BASE, corner_radius=0)
        self.agents_frame.grid_columnconfigure(0, weight=1)
        self.agents_frame.grid_rowconfigure(1, weight=1)

        # Header
        header = GlassCard(self.agents_frame, height=56)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        header.grid_propagate(False)

        ctk.CTkLabel(
            header, text="◎  Agent Orchestrator",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(side="left", padx=14, pady=12)

        ctk.CTkButton(
            header, text="↻ Refresh", width=90, height=32,
            corner_radius=6, font=ctk.CTkFont(size=11),
            fg_color=DS.BG_ELEVATED, hover_color=DS.BG_HOVER,
            text_color=DS.TEXT_SECONDARY,
            command=self._refresh_agents,
        ).pack(side="right", padx=14)

        # Agent list
        self.agents_list = ctk.CTkScrollableFrame(
            self.agents_frame, fg_color=DS.BG_DEEP,
            corner_radius=DS.CORNER_R_SM,
            border_width=1, border_color=DS.BORDER,
        )
        self.agents_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.agents_list.grid_columnconfigure(0, weight=1)

    def _refresh_agents(self):
        """Refresh the agents view."""
        for w in self.agents_list.winfo_children():
            w.destroy()

        agents_data = read_agents_json()
        agents = agents_data.get("agents", {})

        if not agents:
            ctk.CTkLabel(
                self.agents_list,
                text="No active agents.\n\nUse /sub <task> in Telegram to spawn a background agent.",
                font=ctk.CTkFont(size=13),
                text_color=DS.TEXT_MUTED,
                justify="center",
            ).pack(pady=60)
            return

        for agent_id, info in agents.items():
            self._build_agent_card(agent_id, info)

    def _build_agent_card(self, agent_id: str, info: dict):
        """Build a single agent status card."""
        status = info.get("status", "unknown")
        status_colors = {
            "running": DS.ACCENT_SUCCESS,
            "done":    DS.ACCENT_INFO,
            "failed":  DS.ACCENT_DANGER,
            "stopped": DS.ACCENT_WARNING,
        }
        status_color = status_colors.get(status, DS.TEXT_MUTED)

        card = GlassCard(self.agents_list)
        card.pack(fill="x", pady=3)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)
        inner.grid_columnconfigure(1, weight=1)

        # Status dot
        ctk.CTkLabel(
            inner, text="●", font=ctk.CTkFont(size=14),
            text_color=status_color,
        ).grid(row=0, column=0, padx=(0, 8), sticky="w")

        # Title and task
        info_frame = ctk.CTkFrame(inner, fg_color="transparent")
        info_frame.grid(row=0, column=1, sticky="ew")

        title = info.get("title", agent_id[:12])
        ctk.CTkLabel(
            info_frame, text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(anchor="w")

        task = info.get("task", "")
        if task:
            ctk.CTkLabel(
                info_frame, text=task[:100],
                font=ctk.CTkFont(size=11),
                text_color=DS.TEXT_MUTED,
            ).pack(anchor="w")

        # Status badge + time
        badge_frame = ctk.CTkFrame(inner, fg_color="transparent")
        badge_frame.grid(row=0, column=2, padx=(8, 0))

        ctk.CTkLabel(
            badge_frame, text=status.upper(),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=status_color,
        ).pack()

        elapsed = info.get("elapsed", "")
        if elapsed:
            ctk.CTkLabel(
                badge_frame, text=elapsed,
                font=ctk.CTkFont(size=9), text_color=DS.TEXT_FAINT,
            ).pack()

    def _poll_agents(self):
        """Periodically update agent count for stats."""
        try:
            agents_data = read_agents_json()
            agents = agents_data.get("agents", {})
            running = sum(1 for a in agents.values() if a.get("status") == "running")
            self.stat_agents.set_value(str(running))
        except Exception:
            pass
        self.after(5000, self._poll_agents)

    # ══════════════════════════════════════════════════════
    #  SETTINGS VIEW — macOS-Style Categories
    # ══════════════════════════════════════════════════════

    def _build_settings(self):
        self.settings_frame = ctk.CTkFrame(self, fg_color=DS.BG_BASE, corner_radius=0)
        self.settings_frame.grid_columnconfigure(0, weight=1)
        self.settings_frame.grid_rowconfigure(2, weight=1)

        # Title
        ctk.CTkLabel(
            self.settings_frame, text="◆  Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(20, 12))

        # ── Category grid (macOS-style) ──
        cat_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        cat_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 12))

        self._settings_categories = {}
        categories = [
            ("ai_model",  "🤖", "AI Model"),
            ("telegram",  "📡", "Telegram"),
            ("engine",    "⚙️",  "Engine"),
            ("backend",   "🔗", "Backend"),
            ("brain",     "🧠", "Brain"),
            ("display",   "🎨", "Display"),
        ]

        for i, (cat_id, icon, label) in enumerate(categories):
            btn = SettingsCategoryBtn(
                cat_frame, icon=icon, label=label,
                command=lambda c=cat_id: self._switch_settings_category(c),
            )
            btn.grid(row=0, column=i, padx=4, pady=4)
            self._settings_categories[cat_id] = btn

        # ── Content area for selected category ──
        self.settings_content = ctk.CTkScrollableFrame(
            self.settings_frame, fg_color=DS.BG_BASE, corner_radius=0,
        )
        self.settings_content.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 16))
        self.settings_content.grid_columnconfigure(0, weight=1)

        # Build all category panels (will show/hide)
        self._build_settings_ai_model()
        self._build_settings_telegram()
        self._build_settings_engine()
        self._build_settings_backend()
        self._build_settings_brain()
        self._build_settings_display()

        # Settings status
        self.settings_status = ctk.CTkLabel(
            self.settings_frame, text="", font=ctk.CTkFont(size=11),
            text_color=DS.ACCENT_SUCCESS,
        )
        self.settings_status.grid(row=3, column=0, sticky="w", padx=20, pady=(0, 8))

    def _build_settings_ai_model(self):
        """AI Model settings panel."""
        self.settings_panel_ai = GlassCard(self.settings_content)
        self._card_header(self.settings_panel_ai, "AI Model Configuration", row=0)

        ctk.CTkLabel(self.settings_panel_ai, text="Primary Model:",
                     font=ctk.CTkFont(size=12), text_color=DS.TEXT_SECONDARY).grid(
            row=1, column=0, sticky="w", padx=16, pady=(10, 2))
        self.model_var = ctk.StringVar()
        self.model_menu = ctk.CTkOptionMenu(
            self.settings_panel_ai, variable=self.model_var, values=AVAILABLE_MODELS,
            height=38, corner_radius=6, font=ctk.CTkFont(size=13),
            fg_color=DS.BG_SURFACE, button_color=DS.ACCENT_PRIMARY,
            button_hover_color=DS.ACCENT_HOVER,
            text_color=DS.TEXT_PRIMARY,
        )
        self.model_menu.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 16))

    def _build_settings_telegram(self):
        """Telegram settings panel."""
        self.settings_panel_tg = GlassCard(self.settings_content)
        self.settings_panel_tg.grid_columnconfigure(0, weight=1)
        self._card_header(self.settings_panel_tg, "Telegram Configuration", row=0)

        ctk.CTkLabel(self.settings_panel_tg, text="Bot Token:", font=ctk.CTkFont(size=12),
                     text_color=DS.TEXT_SECONDARY).grid(
            row=1, column=0, sticky="w", padx=16, pady=(8, 2))

        # Token entry row with show/hide toggle
        token_row = ctk.CTkFrame(self.settings_panel_tg, fg_color="transparent")
        token_row.grid(row=2, column=0, sticky="ew", padx=16, pady=2)
        token_row.grid_columnconfigure(0, weight=1)

        self._token_visible = False
        self.entry_token = ctk.CTkEntry(
            token_row, height=38, corner_radius=6,
            placeholder_text="Paste your Telegram bot token",
            fg_color=DS.BG_SURFACE, border_color=DS.BORDER,
            text_color=DS.TEXT_PRIMARY, show="\u2022",
        )
        self.entry_token.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_toggle_token = ctk.CTkButton(
            token_row, text="\U0001f441 Show", width=80, height=38,
            corner_radius=6, font=ctk.CTkFont(size=11),
            fg_color=DS.BG_ELEVATED, hover_color=DS.BG_HOVER,
            text_color=DS.TEXT_SECONDARY,
            command=self._toggle_token_visibility,
        )
        self.btn_toggle_token.grid(row=0, column=1)

        ctk.CTkLabel(self.settings_panel_tg, text="Owner Chat ID:", font=ctk.CTkFont(size=12),
                     text_color=DS.TEXT_SECONDARY).grid(
            row=3, column=0, sticky="w", padx=16, pady=(8, 2))
        self.entry_owner = ctk.CTkEntry(
            self.settings_panel_tg, height=38, corner_radius=6,
            placeholder_text="Your numeric Telegram user ID",
            fg_color=DS.BG_SURFACE, border_color=DS.BORDER,
            text_color=DS.TEXT_PRIMARY,
        )
        self.entry_owner.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 12))

        # ── Authorized Users Section ──
        sep_frame = ctk.CTkFrame(self.settings_panel_tg, fg_color=DS.BORDER, height=1)
        sep_frame.grid(row=5, column=0, sticky="ew", padx=16, pady=(4, 8))

        ctk.CTkLabel(
            self.settings_panel_tg, text="Authorized Users",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).grid(row=6, column=0, sticky="w", padx=16, pady=(0, 2))

        ctk.CTkLabel(
            self.settings_panel_tg,
            text="Add users who can interact with your bot via Telegram.",
            font=ctk.CTkFont(size=11), text_color=DS.TEXT_MUTED,
        ).grid(row=7, column=0, sticky="w", padx=16, pady=(0, 6))

        # Inline add-user form
        add_row = ctk.CTkFrame(self.settings_panel_tg, fg_color=DS.BG_SURFACE, corner_radius=8)
        add_row.grid(row=8, column=0, sticky="ew", padx=16, pady=(0, 6))
        add_row.grid_columnconfigure(0, weight=1)
        add_row.grid_columnconfigure(1, weight=1)

        self.settings_entry_uid = ctk.CTkEntry(
            add_row, placeholder_text="User ID", height=34, corner_radius=6,
            fg_color=DS.BG_ELEVATED, border_color=DS.BORDER,
            text_color=DS.TEXT_PRIMARY,
        )
        self.settings_entry_uid.grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=6)

        self.settings_entry_uname = ctk.CTkEntry(
            add_row, placeholder_text="Name", height=34, corner_radius=6,
            fg_color=DS.BG_ELEVATED, border_color=DS.BORDER,
            text_color=DS.TEXT_PRIMARY,
        )
        self.settings_entry_uname.grid(row=0, column=1, sticky="ew", padx=4, pady=6)

        ctk.CTkButton(
            add_row, text="+ Add", width=70, height=34,
            corner_radius=6, font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=DS.ACCENT_SUCCESS, hover_color="#1a9f4a",
            text_color="#ffffff",
            command=self._add_user_from_settings,
        ).grid(row=0, column=2, padx=(4, 8), pady=6)

        # Mini user list
        self.settings_users_list = ctk.CTkFrame(
            self.settings_panel_tg, fg_color="transparent",
        )
        self.settings_users_list.grid(row=9, column=0, sticky="ew", padx=16, pady=(0, 16))
        self.settings_users_list.grid_columnconfigure(0, weight=1)

    def _build_settings_engine(self):
        """Engine settings panel."""
        self.settings_panel_engine = GlassCard(self.settings_content)
        self._card_header(self.settings_panel_engine, "Engine Configuration", row=0)

        # Timeout
        ctk.CTkLabel(self.settings_panel_engine, text="Request Timeout:",
                     font=ctk.CTkFont(size=12), text_color=DS.TEXT_SECONDARY).grid(
            row=1, column=0, sticky="w", padx=16, pady=(8, 2))
        self.timeout_var = ctk.StringVar()
        ctk.CTkOptionMenu(
            self.settings_panel_engine, variable=self.timeout_var,
            values=list(TIMEOUT_OPTIONS.keys()),
            height=36, corner_radius=6,
            fg_color=DS.BG_SURFACE, button_color=DS.ACCENT_PRIMARY,
            button_hover_color=DS.ACCENT_HOVER,
            text_color=DS.TEXT_PRIMARY,
        ).grid(row=2, column=0, sticky="ew", padx=16, pady=2)

        # Progress style
        ctk.CTkLabel(self.settings_panel_engine, text="Progress Reporting:",
                     font=ctk.CTkFont(size=12), text_color=DS.TEXT_SECONDARY).grid(
            row=3, column=0, sticky="w", padx=16, pady=(8, 2))
        self.progress_var = ctk.StringVar()
        ctk.CTkOptionMenu(
            self.settings_panel_engine, variable=self.progress_var,
            values=PROGRESS_OPTIONS,
            height=36, corner_radius=6,
            fg_color=DS.BG_SURFACE, button_color=DS.ACCENT_PRIMARY,
            button_hover_color=DS.ACCENT_HOVER,
            text_color=DS.TEXT_PRIMARY,
        ).grid(row=4, column=0, sticky="ew", padx=16, pady=2)

        # Auto-describe
        ctk.CTkLabel(self.settings_panel_engine, text="Auto-Describe Photos:",
                     font=ctk.CTkFont(size=12), text_color=DS.TEXT_SECONDARY).grid(
            row=5, column=0, sticky="w", padx=16, pady=(8, 2))
        self.auto_describe_var = ctk.BooleanVar()
        ctk.CTkSwitch(
            self.settings_panel_engine, text="", variable=self.auto_describe_var,
            onvalue=True, offvalue=False,
            progress_color=DS.ACCENT_PRIMARY,
        ).grid(row=6, column=0, sticky="w", padx=16, pady=2)

        # Max agents
        ctk.CTkLabel(self.settings_panel_engine, text="Max Concurrent Agents:",
                     font=ctk.CTkFont(size=12), text_color=DS.TEXT_SECONDARY).grid(
            row=7, column=0, sticky="w", padx=16, pady=(8, 2))
        self.max_agents_var = ctk.StringVar()
        ctk.CTkOptionMenu(
            self.settings_panel_engine, variable=self.max_agents_var,
            values=MAX_AGENT_OPTIONS,
            height=36, corner_radius=6,
            fg_color=DS.BG_SURFACE, button_color=DS.ACCENT_PRIMARY,
            button_hover_color=DS.ACCENT_HOVER,
            text_color=DS.TEXT_PRIMARY,
        ).grid(row=8, column=0, sticky="ew", padx=16, pady=(2, 16))

    def _build_settings_backend(self):
        """CLI Backend settings panel."""
        self.settings_panel_backend = GlassCard(self.settings_content)
        self._card_header(self.settings_panel_backend, "CLI Backend", row=0)

        ctk.CTkLabel(
            self.settings_panel_backend,
            text="Select which AI CLI backend to use for processing messages.\n"
                 "Backends are auto-detected on your system.",
            font=ctk.CTkFont(size=11), text_color=DS.TEXT_MUTED,
            wraplength=500, justify="left",
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(4, 8))

        # Backend list
        self.backend_list_frame = ctk.CTkFrame(
            self.settings_panel_backend, fg_color="transparent",
        )
        self.backend_list_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        self.backend_list_frame.grid_columnconfigure(0, weight=1)

    def _build_settings_brain(self):
        """Brain settings panel."""
        self.settings_panel_brain = GlassCard(self.settings_content)
        self.settings_panel_brain.grid_columnconfigure(0, weight=1)
        self._card_header(self.settings_panel_brain, "Brain & Knowledge", row=0)

        ctk.CTkLabel(
            self.settings_panel_brain,
            text="Brain directory manages your knowledge base, inbox, notes, and research.\n"
                 "Path: C:\\Users\\Isha\\AGI-Brain",
            font=ctk.CTkFont(size=11), text_color=DS.TEXT_MUTED,
            wraplength=500, justify="left",
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(8, 4))

        # Subfolder descriptions
        folders_desc = ctk.CTkFrame(self.settings_panel_brain, fg_color=DS.BG_SURFACE, corner_radius=8)
        folders_desc.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 8))

        folder_info = [
            ("\U0001f4e5 Inbox", "Incoming files, images, audio, and Telegram media"),
            ("\U0001f4da Knowledge", "Notes, transcripts, summaries, and research"),
            ("\U0001f4c1 Projects", "Organized project folders and workspaces"),
            ("\U0001f527 Tools", "Custom tools and automation scripts"),
            ("\U0001f4cb Logs", "Activity logs and conversation history"),
        ]
        for i, (folder_name, folder_desc) in enumerate(folder_info):
            row_frame = ctk.CTkFrame(folders_desc, fg_color="transparent")
            row_frame.pack(fill="x", padx=12, pady=(6 if i == 0 else 2, 6 if i == len(folder_info) - 1 else 2))
            ctk.CTkLabel(
                row_frame, text=folder_name,
                font=ctk.CTkFont(size=11, weight="bold"), text_color=DS.TEXT_PRIMARY,
            ).pack(side="left")
            ctk.CTkLabel(
                row_frame, text=f"  —  {folder_desc}",
                font=ctk.CTkFont(size=11), text_color=DS.TEXT_MUTED,
            ).pack(side="left")

        # Brain stats
        self.brain_stats_label = ctk.CTkLabel(
            self.settings_panel_brain,
            text="Loading brain stats...",
            font=ctk.CTkFont(size=12), text_color=DS.TEXT_SECONDARY,
            justify="left", anchor="w",
        )
        self.brain_stats_label.grid(row=3, column=0, sticky="w", padx=16, pady=(4, 8))

        # Action buttons
        btn_row = ctk.CTkFrame(self.settings_panel_brain, fg_color="transparent")
        btn_row.grid(row=4, column=0, sticky="w", padx=16, pady=(0, 16))

        ctk.CTkButton(
            btn_row, text="\U0001f4c2 Open Brain Folder", width=160, height=34,
            corner_radius=6, font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
            text_color="#ffffff",
            command=lambda: self._open_directory(r"C:\Users\Isha\AGI-Brain"),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row, text="\U0001f4e5 Open Inbox", width=130, height=34,
            corner_radius=6, font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=DS.ACCENT_INFO, hover_color="#2563eb",
            text_color="#ffffff",
            command=lambda: self._open_directory(r"C:\Users\Isha\AGI-Brain\Inbox"),
        ).pack(side="left")

    def _build_settings_display(self):
        """Display settings panel."""
        self.settings_panel_display = GlassCard(self.settings_content)
        self.settings_panel_display.grid_columnconfigure(1, weight=1)
        self._card_header(self.settings_panel_display, "Display & Appearance", row=0)

        # Font size selector
        ctk.CTkLabel(
            self.settings_panel_display, text="Font Size:",
            font=ctk.CTkFont(size=12), text_color=DS.TEXT_SECONDARY,
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(10, 2))

        self.font_size_var = ctk.StringVar(value="Medium")
        ctk.CTkSegmentedButton(
            self.settings_panel_display,
            values=["Small", "Medium", "Large"],
            variable=self.font_size_var,
            font=ctk.CTkFont(size=11),
            fg_color=DS.BG_SURFACE, selected_color=DS.ACCENT_PRIMARY,
            selected_hover_color=DS.ACCENT_HOVER,
            unselected_color=DS.BG_ELEVATED, unselected_hover_color=DS.BG_HOVER,
            text_color=DS.TEXT_PRIMARY,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 10))

        # Theme info
        ctk.CTkLabel(
            self.settings_panel_display, text="Current Theme:",
            font=ctk.CTkFont(size=12), text_color=DS.TEXT_SECONDARY,
        ).grid(row=3, column=0, sticky="w", padx=16, pady=(4, 2))

        theme_info = ctk.CTkFrame(self.settings_panel_display, fg_color=DS.BG_SURFACE, corner_radius=8)
        theme_info.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 10))

        ctk.CTkLabel(
            theme_info, text="\U0001f3a8  Glassmorphism Dark",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=DS.TEXT_PRIMARY,
        ).pack(side="left", padx=12, pady=10)

        ctk.CTkLabel(
            theme_info, text="Optimized for focus & low eye strain",
            font=ctk.CTkFont(size=10), text_color=DS.TEXT_MUTED,
        ).pack(side="left", padx=(4, 12), pady=10)

        # Accent color
        ctk.CTkLabel(
            self.settings_panel_display, text="Accent Color:",
            font=ctk.CTkFont(size=12), text_color=DS.TEXT_SECONDARY,
        ).grid(row=5, column=0, sticky="w", padx=16, pady=(4, 2))

        accent_row = ctk.CTkFrame(self.settings_panel_display, fg_color=DS.BG_SURFACE, corner_radius=8)
        accent_row.grid(row=6, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 10))

        accent_swatch = ctk.CTkFrame(
            accent_row, fg_color=DS.ACCENT_PRIMARY,
            width=20, height=20, corner_radius=4,
        )
        accent_swatch.pack(side="left", padx=(12, 8), pady=10)
        accent_swatch.pack_propagate(False)

        ctk.CTkLabel(
            accent_row, text="Indigo  #6366f1",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=DS.ACCENT_PRIMARY,
        ).pack(side="left", pady=10)

        # Chat bubble style
        ctk.CTkLabel(
            self.settings_panel_display, text="Chat Bubbles:",
            font=ctk.CTkFont(size=12), text_color=DS.TEXT_SECONDARY,
        ).grid(row=7, column=0, sticky="w", padx=16, pady=(4, 2))

        bubble_row = ctk.CTkFrame(self.settings_panel_display, fg_color=DS.BG_SURFACE, corner_radius=8)
        bubble_row.grid(row=8, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 16))

        user_swatch = ctk.CTkFrame(bubble_row, fg_color=DS.BUBBLE_USER, width=14, height=14, corner_radius=3)
        user_swatch.pack(side="left", padx=(12, 4), pady=10)
        user_swatch.pack_propagate(False)
        ctk.CTkLabel(bubble_row, text="You", font=ctk.CTkFont(size=11), text_color=DS.TEXT_SECONDARY).pack(side="left", padx=(0, 12), pady=10)

        bot_swatch = ctk.CTkFrame(bubble_row, fg_color=DS.BUBBLE_BOT, width=14, height=14, corner_radius=3)
        bot_swatch.pack(side="left", padx=(0, 4), pady=10)
        bot_swatch.pack_propagate(False)
        ctk.CTkLabel(bubble_row, text="Zilla", font=ctk.CTkFont(size=11), text_color=DS.TEXT_SECONDARY).pack(side="left", padx=(0, 12), pady=10)

        sys_swatch = ctk.CTkFrame(bubble_row, fg_color=DS.BUBBLE_SYSTEM, width=14, height=14, corner_radius=3)
        sys_swatch.pack(side="left", padx=(0, 4), pady=10)
        sys_swatch.pack_propagate(False)
        ctk.CTkLabel(bubble_row, text="System", font=ctk.CTkFont(size=11), text_color=DS.TEXT_SECONDARY).pack(side="left", pady=10)

    def _switch_settings_category(self, cat_id: str):
        """Switch the visible settings category panel."""
        # Update buttons
        for cid, btn in self._settings_categories.items():
            btn.set_active(cid == cat_id)

        # Hide all panels
        panels = {
            "ai_model": self.settings_panel_ai,
            "telegram":  self.settings_panel_tg,
            "engine":    self.settings_panel_engine,
            "backend":   self.settings_panel_backend,
            "brain":     self.settings_panel_brain,
            "display":   self.settings_panel_display,
        }

        for panel in panels.values():
            panel.grid_forget()

        # Show selected
        panels[cat_id].grid(row=0, column=0, sticky="ew", padx=12, pady=4)

        self._active_settings_cat = cat_id

        # Load backend info
        if cat_id == "backend":
            self._reload_backend_list()
        elif cat_id == "brain":
            self._reload_brain_stats()

        # Show save button
        self._ensure_save_button()

    def _ensure_save_button(self):
        """Ensure the save button is visible."""
        if not hasattr(self, '_save_btn_exists'):
            ctk.CTkButton(
                self.settings_content, text="💾 Save All Settings",
                height=46, corner_radius=DS.CORNER_R_SM,
                font=ctk.CTkFont(size=14, weight="bold"),
                fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
                text_color="#ffffff",
                command=self._save_settings,
            ).grid(row=10, column=0, sticky="ew", padx=16, pady=(16, 4))
            self._save_btn_exists = True

    def _reload_backend_list(self):
        """Reload CLI backend cards."""
        for w in self.backend_list_frame.winfo_children():
            w.destroy()

        try:
            from cli_router import cli_router
            backends = cli_router.list_available()
        except ImportError:
            backends = [{"id": "agy", "name": "Antigravity CLI", "available": True, "active": True}]

        for bk in backends:
            card = ctk.CTkFrame(self.backend_list_frame, fg_color=DS.BG_SURFACE,
                                corner_radius=8, border_width=1,
                                border_color=DS.ACCENT_PRIMARY if bk["active"] else DS.BORDER)
            card.pack(fill="x", pady=3)

            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=8)

            status_icon = "✅" if bk["available"] else "❌"
            active_badge = " (Active)" if bk["active"] else ""

            ctk.CTkLabel(
                inner, text=f"{status_icon} {bk['name']}{active_badge}",
                font=ctk.CTkFont(size=13, weight="bold" if bk["active"] else "normal"),
                text_color=DS.TEXT_PRIMARY if bk["available"] else DS.TEXT_MUTED,
            ).pack(side="left")

            if bk["available"] and not bk["active"]:
                ctk.CTkButton(
                    inner, text="Activate", width=70, height=26,
                    corner_radius=6, font=ctk.CTkFont(size=10),
                    fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
                    text_color="#ffffff",
                    command=lambda bid=bk["id"]: self._activate_backend(bid),
                ).pack(side="right")

    def _activate_backend(self, backend_id):
        """Activate a CLI backend and persist the choice."""
        try:
            from cli_router import cli_router
            cli_router.active_id = backend_id
            # Persist to settings
            settings_data = read_settings_json()
            settings_data["cli_backend"] = backend_id
            write_settings_json(settings_data)
            self.log_queue.put(f"[SYSTEM] Switched CLI backend to: {cli_router.get_name_by_id(backend_id)}")
            self._reload_backend_list()
        except Exception as e:
            self.log_queue.put(f"[SYSTEM] Error switching backend: {e}")

    def _reload_brain_stats(self):
        """Load brain directory stats."""
        try:
            from brain_manager import get_brain_stats
            stats = get_brain_stats()
            inbox = stats.get("inbox", {})
            knowledge = stats.get("knowledge", {})
            projects = stats.get("projects", 0)
            inbox_total = sum(inbox.values()) if isinstance(inbox, dict) else 0
            knowledge_total = sum(knowledge.values()) if isinstance(knowledge, dict) else 0
            lines = []
            lines.append(f"\U0001f4e5 Inbox: {inbox_total} items  (images: {inbox.get('images', 0)}, audio: {inbox.get('audio', 0)}, docs: {inbox.get('documents', 0)}, telegram: {inbox.get('telegram', 0)})")
            lines.append(f"\U0001f4da Knowledge: {knowledge_total} items  (notes: {knowledge.get('notes', 0)}, transcripts: {knowledge.get('transcripts', 0)}, summaries: {knowledge.get('summaries', 0)}, research: {knowledge.get('research', 0)})")
            lines.append(f"\U0001f4c1 Projects: {projects} project folders")
            self.brain_stats_label.configure(text="\n".join(lines))
        except Exception:
            self.brain_stats_label.configure(text="Could not load brain stats.")

    def _open_directory(self, path):
        """Open a directory in the system file explorer."""
        try:
            if os.path.isdir(path):
                os.startfile(path)
            else:
                self.log_queue.put(f"[SYSTEM] Directory not found: {path}")
        except Exception as e:
            self.log_queue.put(f"[SYSTEM] Error opening directory: {e}")

    def _toggle_token_visibility(self):
        """Toggle bot token entry between hidden and visible."""
        self._token_visible = not self._token_visible
        if self._token_visible:
            self.entry_token.configure(show="")
            self.btn_toggle_token.configure(text="\U0001f512 Hide")
        else:
            self.entry_token.configure(show="\u2022")
            self.btn_toggle_token.configure(text="\U0001f441 Show")

    def _add_user_from_settings(self):
        """Add a user from the inline settings form."""
        uid = self.settings_entry_uid.get().strip()
        name = self.settings_entry_uname.get().strip()

        if not uid or not uid.isdigit():
            self.log_queue.put("[SYSTEM] Please enter a valid numeric Telegram User ID.")
            return

        users = read_users_json()
        if uid in users:
            self.log_queue.put(f"[SYSTEM] User {uid} is already authorized.")
            return

        users[uid] = {
            "name": name or f"User {uid}",
            "role": "user",
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        write_users_json(users)

        self.settings_entry_uid.delete(0, "end")
        self.settings_entry_uname.delete(0, "end")
        self.log_queue.put(f"[SYSTEM] Added user {uid} ({name or 'unnamed'})")
        self._reload_users_ui()
        self._reload_settings_users_list()

    def _reload_settings_users_list(self):
        """Refresh the mini user list in Telegram settings."""
        for w in self.settings_users_list.winfo_children():
            w.destroy()

        users = read_users_json()
        if not users:
            ctk.CTkLabel(
                self.settings_users_list,
                text="No authorized users yet.",
                font=ctk.CTkFont(size=11), text_color=DS.TEXT_MUTED,
            ).pack(pady=6)
            return

        for uid_str, info in users.items():
            row = ctk.CTkFrame(self.settings_users_list, fg_color=DS.BG_SURFACE, corner_radius=6)
            row.pack(fill="x", pady=2)
            row.grid_columnconfigure(1, weight=1)

            name = info.get("name", "") or f"User {uid_str}"
            ctk.CTkLabel(
                row, text=f"\U0001f464 {name}",
                font=ctk.CTkFont(size=11), text_color=DS.TEXT_PRIMARY,
            ).grid(row=0, column=0, sticky="w", padx=(10, 4), pady=6)
            ctk.CTkLabel(
                row, text=f"ID: {uid_str}",
                font=ctk.CTkFont(size=10), text_color=DS.TEXT_MUTED,
            ).grid(row=0, column=1, sticky="w", padx=4, pady=6)
            ctk.CTkButton(
                row, text="\u2715", width=28, height=28,
                corner_radius=6, font=ctk.CTkFont(size=11),
                fg_color=DS.ACCENT_DANGER, hover_color="#b91c1c",
                text_color="#ffffff",
                command=lambda u=uid_str: self._remove_user_from_settings(u),
            ).grid(row=0, column=2, padx=(4, 8), pady=6)

    def _remove_user_from_settings(self, uid_str):
        """Remove a user from settings mini list."""
        users = read_users_json()
        if uid_str in users:
            del users[uid_str]
            write_users_json(users)
            self.log_queue.put(f"[SYSTEM] Removed user {uid_str}")
        self._reload_users_ui()
        self._reload_settings_users_list()

    def _card_header(self, parent, text, row=0):
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).grid(row=row, column=0, sticky="w", padx=16, pady=(12, 0))

    def _reload_settings_ui(self):
        env = read_env()
        self.entry_token.delete(0, "end")
        self.entry_token.insert(0, env.get("TELEGRAM_BOT_TOKEN", ""))
        self.entry_owner.delete(0, "end")
        self.entry_owner.insert(0, env.get("TELEGRAM_OWNER_ID", ""))

        s = read_settings_json()
        self.model_var.set(s.get("model", AVAILABLE_MODELS[0]))

        timeout_val = s.get("timeout", 600)
        for label, val in TIMEOUT_OPTIONS.items():
            if val == timeout_val:
                self.timeout_var.set(label)
                break
        else:
            self.timeout_var.set("10 min")

        self.progress_var.set(s.get("progress_style", "detailed"))
        self.auto_describe_var.set(s.get("auto_describe_photos", True))
        self.max_agents_var.set(str(s.get("max_agents", 5)))
        self.settings_status.configure(text="")

        # Default to showing AI Model category
        self._switch_settings_category(self._active_settings_cat)

    def _save_settings(self):
        try:
            token = self.entry_token.get().strip()
            owner = self.entry_owner.get().strip()
            write_env(token, owner)

            model = self.model_var.get()
            timeout_label = self.timeout_var.get()
            timeout_val = TIMEOUT_OPTIONS.get(timeout_label, 600)

            settings_data = read_settings_json()
            settings_data["model"] = model
            settings_data["timeout"] = timeout_val
            settings_data["progress_style"] = self.progress_var.get()
            settings_data["auto_describe_photos"] = self.auto_describe_var.get()
            settings_data["max_agents"] = int(self.max_agents_var.get())
            write_settings_json(settings_data)
            write_model_txt(model)

            self.model_label.configure(text=f"Model: {model}")
            self.settings_status.configure(
                text="✓ Settings saved! Restart bot to apply changes.",
                text_color=DS.ACCENT_SUCCESS,
            )
            self.log_queue.put(f"[SYSTEM] Settings saved. Model: {model}")

        except Exception as e:
            self.settings_status.configure(
                text=f"✗ Error: {e}", text_color=DS.ACCENT_DANGER,
            )

    # ══════════════════════════════════════════════════════
    #  SESSIONS VIEW
    # ══════════════════════════════════════════════════════

    def _build_sessions(self):
        self.sessions_frame = ctk.CTkFrame(self, fg_color=DS.BG_BASE, corner_radius=0)
        self.sessions_frame.grid_columnconfigure(0, weight=1)
        self.sessions_frame.grid_rowconfigure(1, weight=1)

        # Header
        header = GlassCard(self.sessions_frame, height=56)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        header.grid_propagate(False)

        ctk.CTkLabel(
            header, text="◇  Session Manager",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(side="left", padx=14, pady=12)

        ctk.CTkButton(
            header, text="+ New Session", width=110, height=32,
            corner_radius=6, font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=DS.ACCENT_SUCCESS, hover_color="#1a9f4a",
            text_color="#ffffff",
            command=self._new_session,
        ).pack(side="right", padx=14)

        ctk.CTkButton(
            header, text="↻ Refresh", width=80, height=32,
            corner_radius=6, font=ctk.CTkFont(size=11),
            fg_color=DS.BG_ELEVATED, hover_color=DS.BG_HOVER,
            text_color=DS.TEXT_SECONDARY,
            command=self._reload_sessions_ui,
        ).pack(side="right", padx=4)

        # Session list
        self.session_list_frame = ctk.CTkScrollableFrame(
            self.sessions_frame, fg_color=DS.BG_DEEP,
            corner_radius=DS.CORNER_R_SM,
            border_width=1, border_color=DS.BORDER,
        )
        self.session_list_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.session_list_frame.grid_columnconfigure(0, weight=1)

    def _reload_sessions_ui(self):
        for widget in self.session_list_frame.winfo_children():
            widget.destroy()

        data = read_sessions_json()
        active = data.get("active", "main")
        sessions = data.get("sessions", {})

        # Update stats
        total_msgs = sum(s.get("messages", 0) for s in sessions.values())
        self.stat_sessions.set_value(str(len(sessions)))
        self.stat_messages.set_value(str(total_msgs))

        if not sessions:
            ctk.CTkLabel(
                self.session_list_frame,
                text="No sessions yet. Start chatting with the bot!",
                font=ctk.CTkFont(size=13), text_color=DS.TEXT_MUTED,
            ).pack(pady=40)
            return

        for name, info in sessions.items():
            is_active = (name == active)
            self._build_session_card(name, info, is_active)

    def _build_session_card(self, name: str, info: dict, is_active: bool):
        border_color = DS.ACCENT_PRIMARY if is_active else DS.BORDER
        card = GlassCard(
            self.session_list_frame,
            border_color=border_color,
            border_width=2 if is_active else 1,
        )
        card.pack(fill="x", pady=3)
        card.grid_columnconfigure(1, weight=1)

        # Status dot
        dot_color = DS.ACCENT_SUCCESS if is_active else DS.TEXT_FAINT
        ctk.CTkLabel(
            card, text="●", font=ctk.CTkFont(size=14),
            text_color=dot_color,
        ).grid(row=0, column=0, padx=(14, 6), pady=12)

        # Info
        title = info.get("title") or "(untitled)"
        msgs = info.get("messages", 0)
        created = info.get("created", "—")

        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.grid(row=0, column=1, sticky="ew", padx=4, pady=10)

        ctk.CTkLabel(
            info_frame, text=name,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(anchor="w")

        ctk.CTkLabel(
            info_frame,
            text=f"{title}  •  {msgs} msgs  •  {created}",
            font=ctk.CTkFont(size=10),
            text_color=DS.TEXT_MUTED,
        ).pack(anchor="w")

        # Buttons
        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.grid(row=0, column=2, padx=10, pady=10)

        if not is_active:
            ctk.CTkButton(
                btn_frame, text="Switch", width=65, height=28,
                corner_radius=6, font=ctk.CTkFont(size=10),
                fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
                text_color="#ffffff",
                command=lambda n=name: self._switch_session(n),
            ).pack(side="left", padx=2)

        ctk.CTkButton(
            btn_frame, text="Rename", width=65, height=28,
            corner_radius=6, font=ctk.CTkFont(size=10),
            fg_color=DS.ACCENT_TEAL, hover_color="#0d9488",
            text_color="#ffffff",
            command=lambda n=name: self._rename_session(n),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btn_frame, text="Delete", width=65, height=28,
            corner_radius=6, font=ctk.CTkFont(size=10),
            fg_color=DS.ACCENT_DANGER, hover_color="#b91c1c",
            text_color="#ffffff",
            command=lambda n=name: self._delete_session(n),
        ).pack(side="left", padx=2)

    def _switch_session(self, name: str):
        data = read_sessions_json()
        data["active"] = name
        with open(SESSIONS_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.log_queue.put(f"[SYSTEM] Switched to session [{name}]")
        self._reload_sessions_ui()

    def _rename_session(self, old_name: str):
        """Show a rename dialog for the session."""
        modal = ctk.CTkToplevel(self)
        modal.title("Rename Session")
        modal.geometry("400x200")
        modal.transient(self)
        modal.grab_set()
        modal.configure(fg_color=DS.BG_BASE)

        ctk.CTkLabel(
            modal, text=f"Rename '{old_name}' to:",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(padx=20, pady=(20, 8))

        name_entry = ctk.CTkEntry(
            modal, placeholder_text="New session name...",
            height=38, corner_radius=6,
            fg_color=DS.BG_SURFACE, border_color=DS.BORDER,
            text_color=DS.TEXT_PRIMARY,
        )
        name_entry.pack(fill="x", padx=20, pady=(0, 8))
        name_entry.insert(0, old_name)
        name_entry.select_range(0, "end")
        name_entry.focus_set()

        status_label = ctk.CTkLabel(
            modal, text="", font=ctk.CTkFont(size=11),
            text_color=DS.ACCENT_DANGER,
        )
        status_label.pack(padx=20)

        def do_rename():
            new_name = name_entry.get().strip()
            if not new_name:
                status_label.configure(text="Name cannot be empty.")
                return
            if new_name == old_name:
                modal.destroy()
                return

            data = read_sessions_json()
            sessions = data.get("sessions", {})

            if new_name in sessions:
                status_label.configure(text=f"Session '{new_name}' already exists!")
                return

            # Rename the key
            sessions[new_name] = sessions.pop(old_name)
            data["sessions"] = sessions
            if data.get("active") == old_name:
                data["active"] = new_name
            with open(SESSIONS_JSON, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            self.log_queue.put(f"[SYSTEM] Renamed session [{old_name}] → [{new_name}]")
            modal.destroy()
            self._reload_sessions_ui()

        name_entry.bind("<Return>", lambda e: do_rename())

        btn_frame = ctk.CTkFrame(modal, fg_color="transparent")
        btn_frame.pack(pady=12)

        ctk.CTkButton(
            btn_frame, text="Cancel", width=80, height=36,
            corner_radius=DS.CORNER_R_SM,
            fg_color=DS.BG_ELEVATED, hover_color=DS.BG_HOVER,
            text_color=DS.TEXT_SECONDARY,
            command=modal.destroy,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="Rename", width=100, height=36,
            corner_radius=DS.CORNER_R_SM,
            font=ctk.CTkFont(weight="bold"),
            fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
            text_color="#ffffff",
            command=do_rename,
        ).pack(side="left", padx=4)

    def _delete_session(self, name: str):
        data = read_sessions_json()
        if name in data.get("sessions", {}):
            del data["sessions"][name]
            if data.get("active") == name:
                remaining = list(data["sessions"].keys())
                data["active"] = remaining[0] if remaining else "main"
            with open(SESSIONS_JSON, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.log_queue.put(f"[SYSTEM] Deleted session [{name}]")
        self._reload_sessions_ui()

    def _new_session(self):
        data = read_sessions_json()
        sessions = data.get("sessions", {})
        i = 1
        while f"session-{i}" in sessions:
            i += 1
        name = f"session-{i}"
        sessions[name] = {
            "conversation_id": None,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "messages": 0,
            "last_seen_step": 0,
            "title": None,
        }
        data["sessions"] = sessions
        data["active"] = name
        with open(SESSIONS_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.log_queue.put(f"[SYSTEM] Created session [{name}]")
        self._reload_sessions_ui()

    # ══════════════════════════════════════════════════════
    #  INTEGRATIONS VIEW (NEW)
    # ══════════════════════════════════════════════════════

    def _build_integrations(self):
        self.integrations_frame = ctk.CTkFrame(self, fg_color=DS.BG_BASE, corner_radius=0)
        self.integrations_frame.grid_columnconfigure(0, weight=1)
        self.integrations_frame.grid_rowconfigure(1, weight=1)

        # Header
        header = GlassCard(self.integrations_frame, height=56)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        header.grid_propagate(False)

        ctk.CTkLabel(
            header, text="🔗  Integrations Hub",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(side="left", padx=14, pady=12)

        # Integrations list
        integrations_scroll = ctk.CTkScrollableFrame(
            self.integrations_frame, fg_color=DS.BG_DEEP,
            corner_radius=DS.CORNER_R_SM,
            border_width=1, border_color=DS.BORDER,
        )
        integrations_scroll.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        integrations_scroll.grid_columnconfigure(0, weight=1)

        # Integration cards
        integrations = [
            ("📧", "Google Workspace", "Calendar, Drive, Docs, Gmail",
             "Connect your Google account for calendar events, document access, and email.",
             "google"),
            ("📁", "Microsoft OneDrive", "OneDrive, Office 365, Outlook",
             "Access your OneDrive files and Office 365 documents.",
             "microsoft"),
            ("💬", "Slack", "Channels, Messages, Files",
             "Connect to Slack workspaces for channel messaging and file sharing.",
             "slack"),
            ("📝", "Notion", "Pages, Databases, Wikis",
             "Access your Notion workspace for notes and knowledge management.",
             "notion"),
            ("🐙", "GitHub", "Repositories, Issues, PRs",
             "Connect to GitHub for code management and issue tracking.",
             "github"),
        ]

        for icon, name, features, description, provider_id in integrations:
            self._build_integration_card(
                integrations_scroll, icon, name, features, description, provider_id,
            )

    def _build_integration_card(self, parent, icon, name, features, description, provider_id):
        """Build a single integration card."""
        card = GlassCard(parent)
        card.pack(fill="x", pady=4)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=14)
        inner.grid_columnconfigure(1, weight=1)

        # Icon
        ctk.CTkLabel(
            inner, text=icon, font=ctk.CTkFont(size=28),
        ).grid(row=0, column=0, rowspan=3, padx=(0, 14), sticky="n")

        # Name
        ctk.CTkLabel(
            inner, text=name,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).grid(row=0, column=1, sticky="w")

        # Features
        ctk.CTkLabel(
            inner, text=features,
            font=ctk.CTkFont(size=11),
            text_color=DS.ACCENT_TEAL,
        ).grid(row=1, column=1, sticky="w", pady=(2, 0))

        # Description
        ctk.CTkLabel(
            inner, text=description,
            font=ctk.CTkFont(size=11),
            text_color=DS.TEXT_MUTED,
            wraplength=450,
        ).grid(row=2, column=1, sticky="w", pady=(4, 0))

        # Status + Connect button
        status_frame = ctk.CTkFrame(inner, fg_color="transparent")
        status_frame.grid(row=0, column=2, rowspan=3, padx=(12, 0), sticky="e")

        ctk.CTkLabel(
            status_frame, text="○ Not Connected",
            font=ctk.CTkFont(size=10),
            text_color=DS.TEXT_FAINT,
        ).pack(pady=(0, 6))

        btn_text = "Connect" if provider_id != "google" else "Connect Google"
        ctk.CTkButton(
            status_frame, text=btn_text, width=110, height=32,
            corner_radius=6, font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
            text_color="#ffffff",
            command=lambda p=provider_id: self._connect_integration(p),
        ).pack()

    def _connect_integration(self, provider_id):
        """Handle integration connection with a modal dialog."""
        # Map provider IDs to display info
        provider_icons = {
            "google": "\U0001f4e7", "microsoft": "\U0001f4c1",
            "slack": "\U0001f4ac", "notion": "\U0001f4dd", "github": "\U0001f419",
        }
        provider_names = {
            "google": "Google Workspace", "microsoft": "Microsoft OneDrive",
            "slack": "Slack", "notion": "Notion", "github": "GitHub",
        }

        modal = ctk.CTkToplevel(self)
        modal.transient(self)
        modal.grab_set()
        modal.configure(fg_color=DS.BG_BASE)
        modal.resizable(False, False)

        icon = provider_icons.get(provider_id, "\U0001f517")
        name = provider_names.get(provider_id, provider_id.title())

        if provider_id == "google":
            modal.title("Connect Google Workspace")
            modal.geometry("480x380")

            header = GlassCard(modal)
            header.pack(fill="x", padx=20, pady=(20, 12))
            ctk.CTkLabel(
                header, text=f"{icon}  {name}",
                font=ctk.CTkFont(size=20, weight="bold"), text_color=DS.TEXT_PRIMARY,
            ).pack(pady=(14, 4))
            ctk.CTkLabel(
                header, text="OAuth Setup Required",
                font=ctk.CTkFont(size=12), text_color=DS.ACCENT_WARNING,
            ).pack(pady=(0, 12))

            steps = (
                "To connect Google Workspace:\n\n"
                "1.  Go to Google Cloud Console and create a project\n"
                "2.  Enable Calendar, Drive, and Gmail APIs\n"
                "3.  Create OAuth 2.0 credentials (Desktop app)\n"
                "4.  Download the credentials.json file\n"
                "5.  Place it in the project directory below\n"
                "6.  Restart Zilla and click Connect again"
            )
            ctk.CTkLabel(
                modal, text=steps,
                font=ctk.CTkFont(size=11), text_color=DS.TEXT_SECONDARY,
                justify="left", wraplength=430,
            ).pack(padx=24, pady=(8, 12), anchor="w")

            btn_row = ctk.CTkFrame(modal, fg_color="transparent")
            btn_row.pack(pady=(0, 16))

            ctk.CTkButton(
                btn_row, text="\U0001f4c2 Open Project Folder", width=170, height=36,
                corner_radius=DS.CORNER_R_SM,
                fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
                text_color="#ffffff", font=ctk.CTkFont(size=11, weight="bold"),
                command=lambda: self._open_directory(PROJECT_DIR),
            ).pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                btn_row, text="Close", width=90, height=36,
                corner_radius=DS.CORNER_R_SM,
                fg_color=DS.BG_ELEVATED, hover_color=DS.BG_HOVER,
                text_color=DS.TEXT_SECONDARY,
                command=modal.destroy,
            ).pack(side="left")
        else:
            modal.title(f"{name} Integration")
            modal.geometry("400x280")

            header = GlassCard(modal)
            header.pack(fill="x", padx=20, pady=(20, 12))
            ctk.CTkLabel(
                header, text=icon,
                font=ctk.CTkFont(size=40),
            ).pack(pady=(16, 4))
            ctk.CTkLabel(
                header, text=name,
                font=ctk.CTkFont(size=18, weight="bold"), text_color=DS.TEXT_PRIMARY,
            ).pack()
            ctk.CTkLabel(
                header, text="Coming Soon",
                font=ctk.CTkFont(size=13, weight="bold"), text_color=DS.ACCENT_WARNING,
            ).pack(pady=(4, 14))

            ctk.CTkLabel(
                modal,
                text=f"{name} integration is under development.\n"
                     "Stay tuned for updates in a future release!",
                font=ctk.CTkFont(size=12), text_color=DS.TEXT_MUTED,
                justify="center",
            ).pack(padx=24, pady=(8, 16))

            ctk.CTkButton(
                modal, text="Close", width=100, height=36,
                corner_radius=DS.CORNER_R_SM,
                fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
                text_color="#ffffff",
                command=modal.destroy,
            ).pack(pady=(0, 16))

    # ══════════════════════════════════════════════════════
    #  USERS VIEW
    # ══════════════════════════════════════════════════════

    def _build_users(self):
        self.users_frame = ctk.CTkFrame(self, fg_color=DS.BG_BASE, corner_radius=0)
        self.users_frame.grid_columnconfigure(0, weight=1)
        self.users_frame.grid_rowconfigure(2, weight=1)

        # Header
        header = GlassCard(self.users_frame, height=56)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        header.grid_propagate(False)

        ctk.CTkLabel(
            header, text="◊  User Management",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(side="left", padx=14, pady=12)

        # Add user bar — prominent with accent border
        add_bar = GlassCard(self.users_frame, border_color=DS.ACCENT_PRIMARY, border_width=2)
        add_bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

        # Description row
        desc_row = ctk.CTkFrame(add_bar, fg_color="transparent")
        desc_row.pack(fill="x", padx=14, pady=(10, 0))

        ctk.CTkLabel(
            desc_row, text="Add New User",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=DS.TEXT_PRIMARY,
        ).pack(side="left")

        ctk.CTkLabel(
            desc_row,
            text="  \u2022  Add a new authorized user by entering their Telegram User ID",
            font=ctk.CTkFont(size=11), text_color=DS.TEXT_MUTED,
        ).pack(side="left")

        # Input row
        input_row = ctk.CTkFrame(add_bar, fg_color="transparent")
        input_row.pack(fill="x", padx=14, pady=(6, 4))

        self.entry_user_id = ctk.CTkEntry(
            input_row, placeholder_text="Telegram User ID",
            width=170, height=36, corner_radius=6,
            fg_color=DS.BG_SURFACE, border_color=DS.BORDER,
            text_color=DS.TEXT_PRIMARY,
        )
        self.entry_user_id.pack(side="left", padx=(0, 6))

        self.entry_user_name = ctk.CTkEntry(
            input_row, placeholder_text="Display Name (optional)",
            width=200, height=36, corner_radius=6,
            fg_color=DS.BG_SURFACE, border_color=DS.BORDER,
            text_color=DS.TEXT_PRIMARY,
        )
        self.entry_user_name.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            input_row, text="+ Add User", width=100, height=36,
            corner_radius=6, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=DS.ACCENT_SUCCESS, hover_color="#1a9f4a",
            text_color="#ffffff",
            command=self._add_user,
        ).pack(side="left")

        # Info note
        ctk.CTkLabel(
            add_bar,
            text="\U0001f4a1 Users can chat with the bot using your Antigravity CLI account or configure their own.",
            font=ctk.CTkFont(size=10), text_color=DS.TEXT_MUTED,
        ).pack(anchor="w", padx=14, pady=(2, 10))

        # User list
        self.users_list = ctk.CTkScrollableFrame(
            self.users_frame, fg_color=DS.BG_DEEP,
            corner_radius=DS.CORNER_R_SM,
            border_width=1, border_color=DS.BORDER,
        )
        self.users_list.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.users_list.grid_columnconfigure(0, weight=1)

    def _reload_users_ui(self):
        for w in self.users_list.winfo_children():
            w.destroy()

        # Owner card
        env = read_env()
        owner_id = env.get("TELEGRAM_OWNER_ID", "Not set")

        owner_card = GlassCard(self.users_list, border_color=DS.ACCENT_PRIMARY, border_width=2)
        owner_card.pack(fill="x", pady=3)
        owner_inner = ctk.CTkFrame(owner_card, fg_color="transparent")
        owner_inner.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(
            owner_inner, text="👑", font=ctk.CTkFont(size=16),
        ).pack(side="left")

        owner_info = ctk.CTkFrame(owner_inner, fg_color="transparent")
        owner_info.pack(side="left", padx=10)

        ctk.CTkLabel(
            owner_info, text="Owner",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(anchor="w")

        ctk.CTkLabel(
            owner_info, text=f"ID: {owner_id}  •  Full Control",
            font=ctk.CTkFont(size=10), text_color=DS.TEXT_MUTED,
        ).pack(anchor="w")

        # Authorized users
        users = read_users_json()
        if not users:
            ctk.CTkLabel(
                self.users_list,
                text="No additional users authorized.\nAdd users by their Telegram ID above.",
                font=ctk.CTkFont(size=12), text_color=DS.TEXT_MUTED,
                justify="center",
            ).pack(pady=30)
            return

        for uid_str, info in users.items():
            self._build_user_card(uid_str, info)

        # Update chat source dropdown with new users
        self._update_chat_source_options()
        self.chat_source_dropdown.configure(values=self._chat_source_options)

    def _build_user_card(self, uid_str: str, info: dict):
        card = GlassCard(self.users_list)
        card.pack(fill="x", pady=3)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)
        inner.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            inner, text="👤", font=ctk.CTkFont(size=14),
        ).grid(row=0, column=0, padx=(0, 8))

        info_frame = ctk.CTkFrame(inner, fg_color="transparent")
        info_frame.grid(row=0, column=1, sticky="ew")

        name = info.get("name", "") or f"User {uid_str}"
        ctk.CTkLabel(
            info_frame, text=name,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=DS.TEXT_PRIMARY,
        ).pack(anchor="w")

        role = info.get("role", "user")
        added = info.get("added_at", "")
        ctk.CTkLabel(
            info_frame, text=f"ID: {uid_str}  •  {role}  •  Added: {added}",
            font=ctk.CTkFont(size=10), text_color=DS.TEXT_MUTED,
        ).pack(anchor="w")

        ctk.CTkButton(
            inner, text="Remove", width=70, height=28,
            corner_radius=6, font=ctk.CTkFont(size=10),
            fg_color=DS.ACCENT_DANGER, hover_color="#b91c1c",
            text_color="#ffffff",
            command=lambda u=uid_str: self._remove_user(u),
        ).grid(row=0, column=2, padx=(8, 0))

    def _add_user(self):
        uid = self.entry_user_id.get().strip()
        name = self.entry_user_name.get().strip()

        if not uid or not uid.isdigit():
            self.log_queue.put("[SYSTEM] Please enter a valid numeric Telegram User ID.")
            return

        users = read_users_json()
        if uid in users:
            self.log_queue.put(f"[SYSTEM] User {uid} is already authorized.")
            return

        users[uid] = {
            "name": name or f"User {uid}",
            "role": "user",
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        write_users_json(users)

        self.entry_user_id.delete(0, "end")
        self.entry_user_name.delete(0, "end")
        self.log_queue.put(f"[SYSTEM] Added user {uid} ({name or 'unnamed'})")
        self._reload_users_ui()

    def _remove_user(self, uid_str: str):
        users = read_users_json()
        if uid_str in users:
            del users[uid_str]
            write_users_json(users)
            self.log_queue.put(f"[SYSTEM] Removed user {uid_str}")
        self._reload_users_ui()

    # ══════════════════════════════════════════════════════
    #  ABOUT DIALOG
    # ══════════════════════════════════════════════════════

    def _show_about(self):
        """Show the About Zilla dialog."""
        modal = ctk.CTkToplevel(self)
        modal.title("About Zilla")
        modal.geometry("420x320")
        modal.transient(self)
        modal.grab_set()
        modal.configure(fg_color=DS.BG_BASE)
        modal.resizable(False, False)

        # Logo area
        logo_frame = GlassCard(modal)
        logo_frame.pack(fill="x", padx=20, pady=(20, 12))

        ctk.CTkLabel(
            logo_frame, text="🦖 Zilla",
            font=ctk.CTkFont(size=32, weight="bold"),
            text_color=DS.ACCENT_PRIMARY,
        ).pack(pady=(16, 2))

        ctk.CTkLabel(
            logo_frame, text="Universal AI Agent Engine",
            font=ctk.CTkFont(size=13),
            text_color=DS.TEXT_SECONDARY,
        ).pack()

        ctk.CTkLabel(
            logo_frame, text="v4.0",
            font=ctk.CTkFont(size=11),
            text_color=DS.TEXT_MUTED,
        ).pack(pady=(2, 14))

        # Info
        info_text = (
            "A premium desktop application for AI agent orchestration.\n"
            "Supports Telegram bot, desktop chat, multi-CLI backends,\n"
            "skills management, and integrations.\n\n"
            "Keyboard shortcuts:\n"
            "  Ctrl+N — New Session   |   Esc — Dashboard\n"
            "  Ctrl+/ — About Dialog"
        )

        ctk.CTkLabel(
            modal, text=info_text,
            font=ctk.CTkFont(size=11),
            text_color=DS.TEXT_MUTED,
            justify="center",
        ).pack(padx=20, pady=(0, 12))

        ctk.CTkButton(
            modal, text="Close", width=100, height=36,
            corner_radius=DS.CORNER_R_SM,
            fg_color=DS.ACCENT_PRIMARY, hover_color=DS.ACCENT_HOVER,
            text_color="#ffffff",
            command=modal.destroy,
        ).pack(pady=(0, 16))

    # ══════════════════════════════════════════════════════
    #  BOT CONTROL
    # ══════════════════════════════════════════════════════

    def _start_bot(self):
        env = read_env()
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        if not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
            self.log_queue.put("[ERROR] No Telegram bot token! Go to Settings first.")
            self._show_view("settings")
            return

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._set_status(True)
        self.bot.start()

    def _stop_bot(self):
        self.bot.stop()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self._set_status(False)

    def _set_status(self, online: bool):
        if online:
            self.status_dot.configure(text_color=DS.ONLINE_COLOR)
            self.status_text.configure(text="ONLINE", text_color=DS.ONLINE_COLOR)
        else:
            self.status_dot.configure(text_color=DS.OFFLINE_COLOR)
            self.status_text.configure(text="OFFLINE", text_color=DS.OFFLINE_COLOR)

    # ══════════════════════════════════════════════════════
    #  LOG POLLING
    # ══════════════════════════════════════════════════════

    def _poll_logs(self):
        try:
            count = 0
            while count < 50:
                try:
                    msg = self.log_queue.get_nowait()
                    self._log_messages.append(msg)
                    self.log_display.insert("end", msg + "\n")
                    count += 1
                except queue.Empty:
                    break

            if count > 0:
                self.log_display.see("end")

            # Update uptime
            if self.bot.is_running:
                uptime = self.bot.get_uptime()
                self.uptime_label.configure(text=f"Uptime: {uptime}")
                self.uptime_label_sidebar.configure(text=f"Uptime: {uptime}")
                self.stat_uptime.set_value(uptime)
            else:
                if self.btn_stop.cget("state") == "normal":
                    self._stop_bot()

        except Exception:
            pass

        self.after(100, self._poll_logs)

    def _update_stats(self):
        """Periodically update dashboard stats."""
        try:
            data = read_sessions_json()
            sessions = data.get("sessions", {})
            total_msgs = sum(s.get("messages", 0) for s in sessions.values())
            self.stat_sessions.set_value(str(len(sessions)))
            self.stat_messages.set_value(str(total_msgs))
        except Exception:
            pass
        self.after(10000, self._update_stats)

    # ══════════════════════════════════════════════════════
    #  CLEANUP
    # ══════════════════════════════════════════════════════

    def _on_close(self):
        if self.bot.is_running:
            self.bot.stop()
            self.after(500, self.destroy)
        else:
            self.destroy()


# ══════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = ZillaApp()
    app.mainloop()
