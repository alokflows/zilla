# ============================================================
#  USERS — Authorization (two-tier: owner + admin)
# ============================================================
#  Roles:
#    admin — full use: chat, media, model*, settings, browse, files, agy
#    owner — everything admins can do, PLUS user management, and the
#            owner decides (via a setting) whether admins may change the model
#    (*model change for admins is gated by the owner — see can_change_model)
#
#  There is intentionally no untrusted "user" tier: agy executes tools in
#  headless mode regardless of any permission flag, so anyone who can reach
#  agy effectively runs code on this machine. Only people the owner trusts
#  (and adds) get in, and they are all admins.
# ============================================================

import json
import os
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# Capability → roles allowed. Every authorized non-owner is an admin.
_CAPS = {
    "chat":     {"admin", "owner"},
    "admin":    {"admin", "owner"},   # settings, browse, file_gen, agy execution
    "users":    {"owner"},            # add/remove admins — owner only
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
                # Migrate: the old "user" tier no longer exists — every stored
                # (owner-added) account is an admin now.
                for info in self._users.values():
                    if info.get("role") != "admin":
                        info["role"] = "admin"
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
        # Not owner and not a stored account → no capabilities at all.
        if user_id not in self._users or user_id in self._denied:
            return False
        allowed_roles = _CAPS.get(capability, set())
        # Any authorized (stored) account is an admin.
        role = self._users[user_id].get("role", "admin")
        return role in allowed_roles

    def can_change_model(self, user_id: int, admins_allowed: bool) -> bool:
        """
        Owner may always change the model. Admins may only if the owner has
        enabled it (admins_allowed). Unauthorized users never can.
        """
        if user_id == self.owner_id:
            return True
        if not self.can(user_id, "admin"):
            return False
        return bool(admins_allowed)

    # ── CRUD ──────────────────────────────────────────────

    def add_user(self, user_id: int, name: str = "", role: str = "admin") -> bool:
        # Only admins exist now (besides the owner).
        role = "admin"
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

    def list_users(self) -> dict[int, dict]:
        return dict(self._users)

    def count(self) -> int:
        return len(self._users)
