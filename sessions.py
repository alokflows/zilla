# ============================================================
#  SESSION MANAGER — Persistent Named Sessions
# ============================================================
#  Tracks named sessions and their agy conversation IDs.
#  State is saved to a JSON file so it survives bot restarts.
# ============================================================

import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages persistent sessions. Each session maps to an agy
    conversation ID, so agy remembers the full history.

    State is saved to a JSON file so it survives bot restarts.
    """

    def __init__(self, state_file: str):
        self.state_file = state_file
        self.state = self._load()

    def _load(self) -> dict:
        """Load state from JSON file, or create default."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
        return {"active": "main", "sessions": {}}

    def _save(self):
        """Save state to JSON file."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    @property
    def active_name(self) -> str:
        return self.state.get("active", "main")

    @active_name.setter
    def active_name(self, name: str):
        self.state["active"] = name
        self._save()

    def get_conversation_id(self, session_name: str = None) -> str | None:
        """Get the agy conversation ID for a session."""
        name = session_name or self.active_name
        session = self.state["sessions"].get(name)
        return session["conversation_id"] if session else None

    def set_conversation_id(self, conv_id: str, session_name: str = None):
        """Store a conversation ID for a session."""
        name = session_name or self.active_name
        if name not in self.state["sessions"]:
            self.state["sessions"][name] = {
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "messages": 0,
            }
        self.state["sessions"][name]["conversation_id"] = conv_id
        self._save()

    def increment_messages(self, session_name: str = None):
        """Track message count for a session."""
        name = session_name or self.active_name
        if name in self.state["sessions"]:
            self.state["sessions"][name]["messages"] = (
                self.state["sessions"][name].get("messages", 0) + 1
            )
            self.state["sessions"][name]["last_used"] = (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            self._save()

    def create_session(self, name: str) -> bool:
        """Create a new session. Returns False if it already exists."""
        if name in self.state["sessions"]:
            return False
        self.state["sessions"][name] = {
            "conversation_id": None,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "messages": 0,
        }
        self.state["active"] = name
        self._save()
        return True

    def delete_session(self, name: str) -> bool:
        """Delete a session."""
        if name not in self.state["sessions"]:
            return False
        del self.state["sessions"][name]
        if self.state["active"] == name:
            if self.state["sessions"]:
                self.state["active"] = next(iter(self.state["sessions"]))
            else:
                self.state["active"] = "main"
        self._save()
        return True

    def list_sessions(self) -> dict:
        """Return all sessions."""
        return self.state["sessions"]
