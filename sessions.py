# ============================================================
#  SESSION MANAGER v8 — Per-User Scoped Sessions
# ============================================================
#  Tracks named sessions PER USER. Each user has their own
#  isolated session namespace so conversations don't bleed.
#
#  State structure:
#  {
#    "sessions": {
#      "<session_name>": {
#        "user_id": <int>,
#        "source": "telegram" | "desktop",
#        "conversation_id": "...",
#        "created": "...",
#        "messages": 0,
#        "last_seen_step": 0,
#        "title": null
#      }
#    },
#    "active_per_user": {
#      "<user_id>": "<session_name>"
#    }
#  }
#
#  Migration: On first load, existing sessions (from v7) are
#  assigned to owner_id=0 (desktop) so nothing breaks.
# ============================================================

import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages per-user persistent sessions. Each session maps to an
    agy conversation ID, scoped to a specific user.

    State is saved to a JSON file so it survives bot restarts.
    """

    def __init__(self, state_file: str):
        self.state_file = state_file
        self.state = self._load()
        self._migrate_if_needed()
        self._migrate_sources()

    def _load(self) -> dict:
        """Load state from JSON file, or create default."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
        return {"sessions": {}, "active_per_user": {}}

    def _save(self):
        """Save state to JSON file."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _migrate_if_needed(self):
        """One-time migration from v7 (global sessions) to v8 (per-user).
        Assigns all existing sessions to user_id=0 (desktop/owner)."""
        if "active" in self.state:
            logger.info("[SESSION] Migrating v7 → v8 (per-user scoping)...")
            old_active = self.state.pop("active", "main")
            old_sessions = self.state.get("sessions", {})

            # Ensure active_per_user exists
            if "active_per_user" not in self.state:
                self.state["active_per_user"] = {}

            # Tag all existing sessions with user_id=0 (desktop/owner)
            for name, session in old_sessions.items():
                if "user_id" not in session:
                    session["user_id"] = 0

            # Set the old active session as active for desktop user
            self.state["active_per_user"]["0"] = old_active
            self._save()
            logger.info(
                f"[SESSION] Migration done. {len(old_sessions)} sessions → user_id=0. "
                f"Active: {old_active}"
            )

    def _migrate_sources(self):
        """One-time migration: assign 'source' field to existing sessions.
        Sessions owned by user_id=0 are tagged as 'desktop',
        all others are tagged as 'telegram'."""
        migrated = 0
        for name, session in self.state.get("sessions", {}).items():
            if "source" not in session:
                if session.get("user_id", 0) == 0:
                    session["source"] = "desktop"
                else:
                    session["source"] = "telegram"
                migrated += 1
        if migrated:
            self._save()
            logger.info(f"[SESSION] Source migration: tagged {migrated} sessions")

    # ── Active Session (per-user) ─────────────────────────

    def get_active_name(self, user_id: int = 0) -> str:
        """Get active session name for a specific user."""
        return self.state.get("active_per_user", {}).get(str(user_id), "main")

    def set_active_name(self, name: str, user_id: int = 0):
        """Set active session name for a specific user."""
        if "active_per_user" not in self.state:
            self.state["active_per_user"] = {}
        self.state["active_per_user"][str(user_id)] = name
        self._save()

    # Legacy property for backward compatibility (desktop = user_id 0)
    @property
    def active_name(self) -> str:
        return self.get_active_name(0)

    @active_name.setter
    def active_name(self, name: str):
        self.set_active_name(name, 0)

    # ── Session ↔ Conversation ID ─────────────────────────

    def get_conversation_id(
        self, session_name: str = None, user_id: int = 0, source: str = None
    ) -> str | None:
        """Get the agy conversation ID for a user's session.
        If source is specified, also checks that the session's source matches
        to prevent cross-source (telegram↔desktop) session access."""
        name = session_name or self.get_active_name(user_id)
        session = self.state["sessions"].get(name)
        if session and session.get("user_id", 0) == user_id:
            # Source filtering: prevent cross-source session bleed
            if source and session.get("source") != source:
                logger.warning(
                    f"[SESSION] Blocked cross-source access: session '{name}' "
                    f"is source='{session.get('source')}' but requested source='{source}'"
                )
                return None
            return session.get("conversation_id")
        return None

    def set_conversation_id(self, conv_id: str, session_name: str = None, user_id: int = 0):
        """Store a conversation ID for a user's session."""
        name = session_name or self.get_active_name(user_id)
        if name in self.state["sessions"]:
            # SECURITY: Verify ownership before modifying
            session = self.state["sessions"][name]
            if session.get("user_id", 0) != user_id:
                logger.warning(
                    f"SECURITY: User {user_id} tried to modify session '{name}' "
                    f"owned by {session.get('user_id', 0)}"
                )
                return
        else:
            # Create new session entry for this user
            self.state["sessions"][name] = {
                "user_id": user_id,
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "messages": 0,
                "last_seen_step": 0,
            }
        self.state["sessions"][name]["conversation_id"] = conv_id
        self._save()

    # ── Last Seen Step (History Dump Fix) ──────────────────

    def get_last_seen_step(self, session_name: str = None, user_id: int = 0) -> int:
        """Get the last step index we've read from the transcript."""
        name = session_name or self.get_active_name(user_id)
        session = self.state["sessions"].get(name)
        if session and session.get("user_id", 0) == user_id:
            return session.get("last_seen_step", 0)
        return 0

    def set_last_seen_step(self, step: int, session_name: str = None, user_id: int = 0):
        """Update the last step index we've processed."""
        name = session_name or self.get_active_name(user_id)
        if name in self.state["sessions"]:
            # SECURITY: Verify ownership before modifying
            session = self.state["sessions"][name]
            if session.get("user_id", 0) != user_id:
                logger.warning(
                    f"SECURITY: User {user_id} tried to modify session '{name}' "
                    f"owned by {session.get('user_id', 0)}"
                )
                return
            self.state["sessions"][name]["last_seen_step"] = step
            self._save()

    # ── Message Counting ──────────────────────────────────

    def increment_messages(self, session_name: str = None, user_id: int = 0):
        """Track message count for a session."""
        name = session_name or self.get_active_name(user_id)
        if name in self.state["sessions"]:
            # SECURITY: Verify ownership before modifying
            session = self.state["sessions"][name]
            if session.get("user_id", 0) != user_id:
                logger.warning(
                    f"SECURITY: User {user_id} tried to modify session '{name}' "
                    f"owned by {session.get('user_id', 0)}"
                )
                return
            self.state["sessions"][name]["messages"] = (
                self.state["sessions"][name].get("messages", 0) + 1
            )
            self.state["sessions"][name]["last_used"] = (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            self._save()

    # ── Auto-Titles ───────────────────────────────────────

    def get_title(self, session_name: str = None, user_id: int = 0) -> str | None:
        """Get the auto-generated title for a session."""
        name = session_name or self.get_active_name(user_id)
        session = self.state["sessions"].get(name)
        if session:
            return session.get("title")
        return None

    def set_title(self, title: str, session_name: str = None, user_id: int = 0):
        """Set the title for a session."""
        name = session_name or self.get_active_name(user_id)
        if name in self.state["sessions"]:
            # SECURITY: Verify ownership before modifying
            session = self.state["sessions"][name]
            if session.get("user_id", 0) != user_id:
                logger.warning(
                    f"SECURITY: User {user_id} tried to modify session '{name}' "
                    f"owned by {session.get('user_id', 0)}"
                )
                return
            self.state["sessions"][name]["title"] = title
            self._save()

    def auto_title(self, first_message: str, session_name: str = None, user_id: int = 0):
        """
        Generate a short title from the first user message.
        Only sets the title if one doesn't already exist.
        """
        name = session_name or self.get_active_name(user_id)
        session = self.state["sessions"].get(name)
        if not session or session.get("title"):
            return  # Already has a title

        # SECURITY: Verify ownership before modifying
        if session.get("user_id", 0) != user_id:
            logger.warning(
                f"SECURITY: User {user_id} tried to auto-title session '{name}' "
                f"owned by {session.get('user_id', 0)}"
            )
            return

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

    def create_session(
        self, name: str, user_id: int = 0, source: str = "desktop"
    ) -> bool:
        """Create a new session for a specific user. Returns False if it already exists.
        source should be 'telegram' or 'desktop'."""
        if name in self.state["sessions"]:
            return False
        self.state["sessions"][name] = {
            "user_id": user_id,
            "source": source,
            "conversation_id": None,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "messages": 0,
            "last_seen_step": 0,
            "title": None,
        }
        self.set_active_name(name, user_id)
        self._save()
        return True

    def delete_session(self, name: str, user_id: int = 0) -> bool:
        """Delete a session (only if owned by user)."""
        session = self.state["sessions"].get(name)
        if not session:
            return False
        # Only allow deleting own sessions (user_id 0 = desktop/owner can delete any)
        if user_id != 0 and session.get("user_id", 0) != user_id:
            return False
        del self.state["sessions"][name]
        # If this was the active session, switch to another
        active = self.get_active_name(user_id)
        if active == name:
            user_sessions = self.list_sessions(user_id)
            if user_sessions:
                self.set_active_name(next(iter(user_sessions)), user_id)
            else:
                self.set_active_name("main", user_id)
        self._save()
        return True

    def rename_session(self, old_name: str, new_name: str, user_id: int = 0) -> bool:
        """Rename a session."""
        if old_name not in self.state["sessions"]:
            return False
        if new_name in self.state["sessions"]:
            return False
        session = self.state["sessions"][old_name]
        if user_id != 0 and session.get("user_id", 0) != user_id:
            return False
        self.state["sessions"][new_name] = self.state["sessions"].pop(old_name)
        # Update active if needed
        active = self.get_active_name(user_id)
        if active == old_name:
            self.set_active_name(new_name, user_id)
        self._save()
        return True

    def list_sessions(self, user_id: int = None, source: str = None) -> dict:
        """Return sessions, optionally filtered by user_id and/or source.
        If user_id is None and source is None, returns ALL sessions (for desktop admin view).
        source can be 'telegram' or 'desktop'."""
        sessions = self.state["sessions"]
        if user_id is not None:
            sessions = {
                name: s for name, s in sessions.items()
                if s.get("user_id", 0) == user_id
            }
        if source is not None:
            sessions = {
                name: s for name, s in sessions.items()
                if s.get("source") == source
            }
        return sessions

    def get_session_info(self, session_name: str = None, user_id: int = 0) -> dict | None:
        """Get detailed info for a specific session."""
        name = session_name or self.get_active_name(user_id)
        session = self.state["sessions"].get(name)
        if not session:
            return None
        return {
            "name": name,
            "user_id": session.get("user_id", 0),
            "source": session.get("source", "desktop"),
            "title": session.get("title"),
            "conversation_id": session.get("conversation_id"),
            "created": session.get("created"),
            "last_used": session.get("last_used"),
            "messages": session.get("messages", 0),
            "last_seen_step": session.get("last_seen_step", 0),
            "is_active": name == self.get_active_name(user_id),
        }
