# ============================================================
#  USERS — Multi-User Authorization
# ============================================================
#  Manages Telegram user access with roles (owner/admin/user).
#  /adduser and /removeuser via Telegram. Deny-list for security.
# ============================================================

import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages authorized Telegram users with role-based access."""

    def __init__(self, users_file: str, owner_id: int = 0):
        self.users_file = users_file
        self.denied_file = os.path.join(os.path.dirname(users_file), "denied_users.json")
        self.owner_id = owner_id
        self._users: dict[int, dict] = {}
        self._denied: set[int] = set()
        self._load()

    def _load(self):
        """Load users and deny-list from disk."""
        try:
            with open(self.users_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._users = {int(k): v for k, v in data.items()}
        except (FileNotFoundError, json.JSONDecodeError):
            self._users = {}

        try:
            with open(self.denied_file, "r", encoding="utf-8") as f:
                self._denied = set(int(uid) for uid in json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self._denied = set()

    def _save(self):
        try:
            tmp = f"{self.users_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in self._users.items()}, f, indent=2)
            os.replace(tmp, self.users_file)
        except Exception as e:
            logger.error(f"[USERS] Save failed: {e}")

    def _save_denied(self):
        try:
            tmp = f"{self.denied_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(list(self._denied), f, indent=2)
            os.replace(tmp, self.denied_file)
        except Exception as e:
            logger.error(f"[USERS] Save denied failed: {e}")

    def reload(self):
        """Re-read from disk for instant revocation detection."""
        self._load()

    def is_authorized(self, user_id: int) -> bool:
        """Check if authorized. Deny-list wins (except owner)."""
        if user_id != self.owner_id and user_id in self._denied:
            return False
        if user_id == self.owner_id:
            return True
        return user_id in self._users

    def is_owner(self, user_id: int) -> bool:
        return user_id == self.owner_id

    def is_admin(self, user_id: int) -> bool:
        if user_id == self.owner_id:
            return True
        role = self._users.get(user_id, {}).get("role", "user")
        return role in ("owner", "admin")

    def add_user(self, user_id: int, name: str = "", role: str = "user") -> bool:
        """Add an authorized user. Returns True if newly added."""
        if user_id in self._users:
            return False
        self._users[user_id] = {
            "name": name,
            "role": role,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        # Remove from deny-list if present
        self._denied.discard(user_id)
        self._save()
        self._save_denied()
        logger.info(f"[USERS] Added {user_id} ({name}) as {role}")
        return True

    def remove_user(self, user_id: int) -> bool:
        """Remove user and auto-deny."""
        if user_id not in self._users:
            return False
        del self._users[user_id]
        self._denied.add(user_id)
        self._save()
        self._save_denied()
        logger.info(f"[USERS] Removed and denied {user_id}")
        return True

    def list_users(self) -> dict[int, dict]:
        return dict(self._users)

    def count(self) -> int:
        return len(self._users)
