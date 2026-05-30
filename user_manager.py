# ============================================================
#  AUTHORIZED USERS MANAGER
# ============================================================
#  Manages multi-user access control for the bot.
#  
#  - Owner: full control (settings, model changes, user mgmt)
#  - Users: chat-only access (can use the bot, no admin)
#  
#  State persisted to authorized_users.json
# ============================================================

import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "authorized_users.json")
DENIED_USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "denied_users.json")


class UserRole:
    OWNER = "owner"
    USER = "user"


class AuthorizedUsersManager:
    """Manages authorized Telegram users with role-based access."""

    def __init__(self, users_file: str = None, owner_id: int = 0):
        self.users_file = users_file or USERS_FILE
        self.denied_file = os.path.join(os.path.dirname(self.users_file), "denied_users.json")
        self.owner_id = owner_id
        self.users: dict[int, dict] = self._load()
        self.denied_users: set[int] = self._load_denied()

    def _load(self) -> dict[int, dict]:
        """Load authorized users from JSON."""
        if os.path.exists(self.users_file):
            try:
                with open(self.users_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Convert string keys to int
                return {int(k): v for k, v in data.items()}
            except Exception as e:
                logger.error(f"[USERS] Failed to load: {e}")
        return {}

    def _save(self):
        """Persist users to JSON."""
        try:
            with open(self.users_file, "w", encoding="utf-8") as f:
                json.dump(
                    {str(k): v for k, v in self.users.items()},
                    f, indent=2, ensure_ascii=False,
                )
        except Exception as e:
            logger.error(f"[USERS] Failed to save: {e}")

    def _load_fresh(self):
        """Re-read users from disk for instant revocation detection.
        Called on every auth check so removals take effect immediately."""
        self.users = self._load()

    def is_authorized(self, user_id: int) -> bool:
        """Check if a user is authorized (owner or added user).
        SECURITY: Deny-list is checked BEFORE allow-list."""
        # Deny-list always wins (except for owner)
        if user_id != self.owner_id and self.is_denied(user_id):
            return False
        if user_id == self.owner_id:
            return True
        return user_id in self.users

    def is_owner(self, user_id: int) -> bool:
        """Check if a user is the owner."""
        return user_id == self.owner_id

    def get_role(self, user_id: int) -> Optional[str]:
        """Get user's role, or None if not authorized."""
        if user_id == self.owner_id:
            return UserRole.OWNER
        if user_id in self.users:
            return self.users[user_id].get("role", UserRole.USER)
        return None

    def add_user(self, user_id: int, name: str = "", role: str = UserRole.USER) -> bool:
        """Add an authorized user. Returns True if newly added."""
        if user_id in self.users:
            return False
        self.users[user_id] = {
            "name": name,
            "role": role,
            "added_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save()
        logger.info(f"[USERS] Added user {user_id} ({name}) as {role}")
        return True

    def remove_user(self, user_id: int) -> bool:
        """Remove an authorized user and add to deny-list. Returns True if removed."""
        if user_id in self.users:
            del self.users[user_id]
            self._save()
            # SECURITY: Auto-deny removed users to prevent re-authorization races
            self.deny_user(user_id)
            logger.info(f"[USERS] Removed and denied user {user_id}")
            return True
        return False

    def list_users(self) -> dict[int, dict]:
        """Return all authorized users (not including owner)."""
        return dict(self.users)

    def get_all_authorized_ids(self) -> list[int]:
        """Return all authorized user IDs including owner."""
        ids = list(self.users.keys())
        if self.owner_id and self.owner_id not in ids:
            ids.insert(0, self.owner_id)
        return ids

    def count(self) -> int:
        """Return the number of added users (not including owner)."""
        return len(self.users)

    # ── Deny-List ─────────────────────────────────────────

    def _load_denied(self) -> set[int]:
        """Load denied user IDs from JSON."""
        if os.path.exists(self.denied_file):
            try:
                with open(self.denied_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return set(int(uid) for uid in data)
            except Exception as e:
                logger.error(f"[USERS] Failed to load deny list: {e}")
        return set()

    def _save_denied(self):
        """Persist denied user IDs to JSON."""
        try:
            with open(self.denied_file, "w", encoding="utf-8") as f:
                json.dump(list(self.denied_users), f, indent=2)
        except Exception as e:
            logger.error(f"[USERS] Failed to save deny list: {e}")

    def deny_user(self, user_id: int):
        """Add a user to the deny-list."""
        self.denied_users.add(user_id)
        self._save_denied()
        logger.info(f"[USERS] Denied user {user_id}")

    def undeny_user(self, user_id: int):
        """Remove a user from the deny-list (allows re-authorization)."""
        self.denied_users.discard(user_id)
        self._save_denied()
        logger.info(f"[USERS] Un-denied user {user_id}")

    def is_denied(self, user_id: int) -> bool:
        """Check if a user is on the deny-list."""
        return user_id in self.denied_users
