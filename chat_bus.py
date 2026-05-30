# ============================================================
#  CHAT BUS — Thread-Safe Message Event System
# ============================================================
#  Captures all messages flowing through the bot so the GUI
#  can display them in a formatted chat interface.
#
#  Architecture:
#  - bot.py calls chat_bus.post() for every user/bot message
#  - GUI polls chat_bus.get_new() to render chat bubbles
#  - Desktop direct-input posts go through the same bus
#  - Thread-safe: bot thread writes, GUI thread reads
# ============================================================

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum


class MessageRole(Enum):
    USER = "user"
    BOT = "bot"
    SYSTEM = "system"


class MessageStatus(Enum):
    SENT = "sent"
    THINKING = "thinking"
    DONE = "done"
    ERROR = "error"


@dataclass
class ChatMessage:
    """A single chat message in the bus."""
    role: MessageRole
    text: str
    timestamp: float = field(default_factory=time.time)
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    session_name: Optional[str] = None
    status: MessageStatus = MessageStatus.SENT
    message_id: int = 0  # auto-assigned by bus
    source: str = "telegram"  # "telegram" or "desktop"
    # Media support — allows chat bubbles to render images/documents inline
    file_path: Optional[str] = None      # Path to attached file on disk
    media_type: Optional[str] = None     # "image", "video", "audio", "document"
    file_name: Optional[str] = None      # Original filename for display


class ChatBus:
    """
    Thread-safe message bus for chat events.
    
    The bot thread posts messages here; the GUI thread reads them.
    Messages are stored per-user so the GUI can show per-user chat views.
    """

    def __init__(self, max_messages: int = 500):
        self._lock = threading.Lock()
        self._messages: deque[ChatMessage] = deque(maxlen=max_messages)
        self._counter = 0
        self._listeners: list[Callable[[ChatMessage], None]] = []
        self._typing_users: dict[int, float] = {}  # user_id -> timestamp

    def post(self, msg: ChatMessage) -> ChatMessage:
        """Post a message to the bus. Thread-safe."""
        with self._lock:
            self._counter += 1
            msg.message_id = self._counter
            self._messages.append(msg)

        # Notify listeners (non-blocking)
        for listener in self._listeners:
            try:
                listener(msg)
            except Exception:
                pass

        return msg

    def post_user(self, text: str, user_id: int = 0, user_name: str = "You",
                  session_name: str = None, source: str = "telegram",
                  file_path: str = None, media_type: str = None, file_name: str = None) -> ChatMessage:
        """Convenience: post a user message."""
        return self.post(ChatMessage(
            role=MessageRole.USER,
            text=text,
            user_id=user_id,
            user_name=user_name,
            session_name=session_name,
            source=source,
            file_path=file_path,
            media_type=media_type,
            file_name=file_name,
        ))

    def post_bot(self, text: str, user_id: int = 0,
                 session_name: str = None, status: MessageStatus = MessageStatus.DONE,
                 file_path: str = None, media_type: str = None, file_name: str = None) -> ChatMessage:
        """Convenience: post a bot response."""
        return self.post(ChatMessage(
            role=MessageRole.BOT,
            text=text,
            user_id=user_id,
            session_name=session_name,
            status=status,
            source="bot",
            file_path=file_path,
            media_type=media_type,
            file_name=file_name,
        ))

    def post_system(self, text: str) -> ChatMessage:
        """Convenience: post a system message."""
        return self.post(ChatMessage(
            role=MessageRole.SYSTEM,
            text=text,
            source="system",
        ))

    def set_typing(self, user_id: int):
        """Mark that the bot is 'typing' for a user."""
        with self._lock:
            self._typing_users[user_id] = time.time()

    def clear_typing(self, user_id: int):
        """Clear typing status for a user."""
        with self._lock:
            self._typing_users.pop(user_id, None)

    def is_typing(self, user_id: int = None) -> bool:
        """Check if bot is currently typing (for any or specific user)."""
        with self._lock:
            if user_id is not None:
                return user_id in self._typing_users
            return len(self._typing_users) > 0

    def get_all(self) -> list[ChatMessage]:
        """Get all messages. Thread-safe."""
        with self._lock:
            return list(self._messages)

    def get_since(self, after_id: int) -> list[ChatMessage]:
        """Get messages with message_id > after_id. Thread-safe."""
        with self._lock:
            return [m for m in self._messages if m.message_id > after_id]

    def get_for_user(self, user_id: int) -> list[ChatMessage]:
        """Get messages for a specific Telegram user."""
        with self._lock:
            return [m for m in self._messages
                    if m.user_id == user_id or m.role == MessageRole.SYSTEM]

    def get_for_source(self, source: str) -> list[ChatMessage]:
        """Get messages filtered by source type (e.g. 'telegram', 'desktop')."""
        with self._lock:
            return [m for m in self._messages if m.source == source]

    def get_user_ids(self) -> list[int]:
        """Get unique user IDs that have sent messages."""
        with self._lock:
            return list(set(m.user_id for m in self._messages
                           if m.role == MessageRole.USER and m.user_id))

    def add_listener(self, callback: Callable[[ChatMessage], None]):
        """Register a callback for new messages (called from poster's thread)."""
        self._listeners.append(callback)

    def clear(self):
        """Clear all messages."""
        with self._lock:
            self._messages.clear()
            self._counter = 0
            self._typing_users.clear()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._messages)

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._counter


# ── Global singleton ──────────────────────────────────────
chat_bus = ChatBus()
