# ============================================================
#  SESSIONS — Per-User Session Management
# ============================================================
#  Each user gets isolated sessions mapping to CLI conversation
#  IDs. Supports create, delete, rename, switch.
# ============================================================

import json
import os
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)


class SessionManager:
    """Per-user persistent sessions. Each maps to a CLI conversation ID."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self._lock = threading.Lock()
        self.state = self._load()

    def _load(self) -> dict:
        with self._lock:
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "sessions" not in data:
                    data["sessions"] = {}
                if "active_per_user" not in data:
                    data["active_per_user"] = {}
                for key, session in data["sessions"].items():
                    if "original_name" in session and "name" not in session:
                        session["name"] = session.pop("original_name")
                return data
            except (FileNotFoundError, json.JSONDecodeError):
                return {"sessions": {}, "active_per_user": {}}

    def _save(self):
        with self._lock:
            try:
                tmp = f"{self.state_file}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.state_file)
            except Exception as e:
                logger.error(f"[SESSION] Save failed: {e}")

    def _key(self, name: str, user_id: int) -> str:
        return f"{user_id}_{name}"

    def _find(self, name: str, user_id: int) -> tuple[str, dict | None]:
        """Find a session by name for a user. Returns (key, session_data)."""
        key = self._key(name, user_id)
        session = self.state["sessions"].get(key)
        if session and session.get("user_id", 0) == user_id:
            return key, session
        # Fallback: try bare name
        session = self.state["sessions"].get(name)
        if session and session.get("user_id", 0) == user_id:
            return name, session
        return key, None

    # ── Active Session ────────────────────────────────────

    def get_active_name(self, user_id: int) -> str:
        return self.state.get("active_per_user", {}).get(str(user_id), "main")

    def set_active_name(self, name: str, user_id: int):
        self.state.setdefault("active_per_user", {})[str(user_id)] = name
        self._save()

    # ── Conversation ID ───────────────────────────────────

    def get_conversation_id(self, user_id: int, session_name: str = None) -> str | None:
        name = session_name or self.get_active_name(user_id)
        _, session = self._find(name, user_id)
        return session.get("conversation_id") if session else None

    def set_conversation_id(self, conv_id: str, user_id: int, session_name: str = None,
                            backend: str = None):
        name = session_name or self.get_active_name(user_id)
        key, session = self._find(name, user_id)
        if not session:
            self.state["sessions"][key] = {
                "name": name, "user_id": user_id,
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "messages": 0, "last_seen_step": 0, "title": None,
            }
        self.state["sessions"][key]["conversation_id"] = conv_id
        # Conversation ids are backend-specific (agy brain dir vs claude session).
        # Remember which backend made this one so we never resume it on the other.
        if backend is not None:
            self.state["sessions"][key]["conv_backend"] = backend
        self._save()

    def get_conv_backend(self, user_id: int, session_name: str = None) -> str | None:
        name = session_name or self.get_active_name(user_id)
        _, session = self._find(name, user_id)
        return session.get("conv_backend") if session else None

    # ── Last Seen Step ────────────────────────────────────

    def get_last_seen_step(self, user_id: int) -> int:
        name = self.get_active_name(user_id)
        _, session = self._find(name, user_id)
        return session.get("last_seen_step", 0) if session else 0

    def set_last_seen_step(self, step: int, user_id: int, session_name: str = None):
        name = session_name or self.get_active_name(user_id)
        key, session = self._find(name, user_id)
        if session:
            session["last_seen_step"] = step
            self._save()

    # ── Message Count ─────────────────────────────────────

    def increment_messages(self, user_id: int, session_name: str = None):
        name = session_name or self.get_active_name(user_id)
        key, session = self._find(name, user_id)
        if session:
            session["messages"] = session.get("messages", 0) + 1
            session["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save()

    # ── Auto-Title ────────────────────────────────────────

    def auto_title(self, first_message: str, user_id: int, session_name: str = None):
        name = session_name or self.get_active_name(user_id)
        _, session = self._find(name, user_id)
        if not session or session.get("title"):
            return
        words = [w for w in first_message.strip().split() if len(w) > 1][:5]
        if not words:
            words = first_message.strip().split()[:5]
        title = " ".join(words)
        if len(title) > 40:
            title = title[:37] + "..."
        title = title.strip(".,!?;:\"'")
        if title:
            session["title"] = title
            self._save()

    # ── CRUD ──────────────────────────────────────────────

    def create_session(self, name: str, user_id: int) -> bool:
        key = self._key(name, user_id)
        if key in self.state["sessions"]:
            return False
        self.state["sessions"][key] = {
            "name": name, "user_id": user_id,
            "conversation_id": None,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "messages": 0, "last_seen_step": 0, "title": None,
        }
        self.set_active_name(name, user_id)
        self._save()
        return True

    def delete_session(self, name: str, user_id: int) -> bool:
        key, session = self._find(name, user_id)
        if not session:
            return False
        del self.state["sessions"][key]
        # Switch active if needed
        if self.get_active_name(user_id) == name:
            remaining = self.list_sessions(user_id)
            self.set_active_name(next(iter(remaining), "main"), user_id)
        self._save()
        return True

    def rename_session(self, old_name: str, new_name: str, user_id: int) -> bool:
        old_key, session = self._find(old_name, user_id)
        if not session:
            return False
        new_key = self._key(new_name, user_id)
        if new_key in self.state["sessions"]:
            return False
        session["name"] = new_name
        self.state["sessions"][new_key] = self.state["sessions"].pop(old_key)
        if self.get_active_name(user_id) == old_name:
            self.set_active_name(new_name, user_id)
        self._save()
        return True

    def list_sessions(self, user_id: int) -> dict:
        """Return {name: info} for all sessions owned by user."""
        result = {}
        for key, s in self.state["sessions"].items():
            if s.get("user_id") == user_id:
                display_name = s.get("name", key)
                result[display_name] = s
        return result

    def get_session_info(self, user_id: int, session_name: str = None) -> dict | None:
        name = session_name or self.get_active_name(user_id)
        _, session = self._find(name, user_id)
        if not session:
            return None
        return {
            "name": session.get("name", name),
            "title": session.get("title"),
            "conversation_id": session.get("conversation_id"),
            "created": session.get("created"),
            "last_used": session.get("last_used"),
            "messages": session.get("messages", 0),
            "is_active": session.get("name", name) == self.get_active_name(user_id),
        }
