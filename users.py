# ============================================================
#  USERS — Multi-User Authorization (Three-tier)
# ============================================================
#  Roles:
#    user  — chat, voice, photo, document
#    admin — + model/settings change, /browse, file delivery
#    owner — + full user management (set via TELEGRAM_OWNER_ID in .env)
# ============================================================

import json
import os
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# Capability → minimum role required
_CAPS = {
    "chat":     {"user", "admin", "owner"},
    "admin":    {"admin", "owner"},   # model, settings, browse, file_gen, skip_permissions
    "users":    {"owner"},
}


class AuthManager:
    def __init__(self, users_file: str, owner_id: int = 0):
        self.users_file = users_file
        self.denied_file = os.path.join(os.path.dirname(users_file), "denied_users.json")
        self.owner_id = owner_id
        self._lock = threading.Lock()
        self._users: dict[int, dict] = {}
        self._denied: set[int] = set()
        self._mtime_users: float = 0.0
        self._load()

    def _load(self):
        with self._lock:
            try:
                with open(self.users_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._users = {int(k): v for k, v in data.items()}
                try:
                    self._mtime_users = os.path.getmtime(self.users_file)
                except OSError:
                    self._mtime_users = 0.0
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
            try:
                self._mtime_users = os.path.getmtime(self.users_file)
            except OSError:
                pass
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
        """Re-read from disk — but only if the file actually changed."""
        try:
            current_mtime = os.path.getmtime(self.users_file)
        except OSError:
            current_mtime = 0.0
        if current_mtime != self._mtime_users:
            self._load()

    # ── Authorization ─────────────────────────────────────

    def is_authorized(self, user_id: int) -> bool:
        if user_id == self.owner_id:
            return True
        if user_id in self._denied:
            return False
        return user_id in self._users

    def is_owner(self, user_id: int) -> bool:
        return user_id == self.owner_id

    def is_admin(self, user_id: int) -> bool:
        return self.can(user_id, "admin")

    def can(self, user_id: int, capability: str) -> bool:
        """Check if user has the given capability."""
        if user_id == self.owner_id:
            return True
        allowed_roles = _CAPS.get(capability, set())
        role = self._users.get(user_id, {}).get("role", "user")
        return role in allowed_roles

    # ── CRUD ──────────────────────────────────────────────

    def add_user(self, user_id: int, name: str = "", role: str = "user") -> bool:
        if role not in ("user", "admin"):
            role = "user"
        with self._lock:
            if user_id in self._users:
                return False
            self._users[user_id] = {
                "name": name,
                "role": role,
                "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._denied.discard(user_id)
            self._save()
            self._save_denied()
        logger.info(f"[USERS] Added {user_id} ({name}) as {role}")
        return True

    def remove_user(self, user_id: int) -> bool:
        with self._lock:
            if user_id not in self._users:
                return False
            del self._users[user_id]
            self._denied.add(user_id)
            self._save()
            self._save_denied()
        logger.info(f"[USERS] Removed and denied {user_id}")
        return True

    def set_role(self, user_id: int, role: str) -> bool:
        if role not in ("user", "admin"):
            return False
        with self._lock:
            if user_id not in self._users:
                return False
            self._users[user_id]["role"] = role
            self._save()
        logger.info(f"[USERS] Role of {user_id} → {role}")
        return True

    def list_users(self) -> dict[int, dict]:
        return dict(self._users)

    def count(self) -> int:
        return len(self._users)
