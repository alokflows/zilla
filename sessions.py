# ============================================================
#  SESSION MANAGER v7 — Persistent Named Sessions
# ============================================================
#  Tracks named sessions and their agy conversation IDs.
#  State is saved to a JSON file so it survives bot restarts.
#
#  NEW in v7:
#  - Auto-generated session titles from first message
#  - last_seen_step tracking (fixes conversation dump bug)
#  - Session metadata (title, created, last_used, messages)
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

    # ── Last Seen Step (Conversation Dump Fix) ────────────

    def get_last_seen_step(self, session_name: str = None) -> int:
        """Get the last step index we've read from the transcript."""
        name = session_name or self.active_name
        session = self.state["sessions"].get(name)
        if session:
            return session.get("last_seen_step", 0)
        return 0

    def set_last_seen_step(self, step: int, session_name: str = None):
        """Update the last step index we've processed."""
        name = session_name or self.active_name
        if name in self.state["sessions"]:
            self.state["sessions"][name]["last_seen_step"] = step
            self._save()

    # ── Message Counting ──────────────────────────────────

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

    # ── Auto-Titles ───────────────────────────────────────

    def get_title(self, session_name: str = None) -> str | None:
        """Get the auto-generated title for a session."""
        name = session_name or self.active_name
        session = self.state["sessions"].get(name)
        if session:
            return session.get("title")
        return None

    def set_title(self, title: str, session_name: str = None):
        """Set the title for a session."""
        name = session_name or self.active_name
        if name in self.state["sessions"]:
            self.state["sessions"][name]["title"] = title
            self._save()

    def auto_title(self, first_message: str, session_name: str = None):
        """
        Generate a short title from the first user message.
        Only sets the title if one doesn't already exist.
        """
        name = session_name or self.active_name
        session = self.state["sessions"].get(name)
        if not session or session.get("title"):
            return  # Already has a title

        # Take first 5 meaningful words, clean up
        words = first_message.strip().split()
        # Filter out very short words at the start
        meaningful = [w for w in words if len(w) > 1][:5]
        if not meaningful:
            meaningful = words[:5]

        title = " ".join(meaningful)
        if len(title) > 40:
            title = title[:37] + "..."

        # Clean up
        title = title.strip(".,!?;:\"'")
        if title:
            session["title"] = title
            self._save()
            logger.info(f"[SESSION] Auto-titled [{name}]: \"{title}\"")

    # ── Session CRUD ──────────────────────────────────────

    def create_session(self, name: str) -> bool:
        """Create a new session. Returns False if it already exists."""
        if name in self.state["sessions"]:
            return False
        self.state["sessions"][name] = {
            "conversation_id": None,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "messages": 0,
            "last_seen_step": 0,
            "title": None,
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

    def rename_session(self, old_name: str, new_name: str) -> bool:
        """Rename a session."""
        if old_name not in self.state["sessions"]:
            return False
        if new_name in self.state["sessions"]:
            return False
        self.state["sessions"][new_name] = self.state["sessions"].pop(old_name)
        if self.state["active"] == old_name:
            self.state["active"] = new_name
        self._save()
        return True

    def list_sessions(self) -> dict:
        """Return all sessions."""
        return self.state["sessions"]

    def get_session_info(self, session_name: str = None) -> dict | None:
        """Get detailed info for a specific session."""
        name = session_name or self.active_name
        session = self.state["sessions"].get(name)
        if not session:
            return None
        return {
            "name": name,
            "title": session.get("title"),
            "conversation_id": session.get("conversation_id"),
            "created": session.get("created"),
            "last_used": session.get("last_used"),
            "messages": session.get("messages", 0),
            "last_seen_step": session.get("last_seen_step", 0),
            "is_active": name == self.active_name,
        }
