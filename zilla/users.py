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
#
#  Persistence: a thin wrapper over store.py (Phase M1). No in-memory user
#  cache — every read hits the store's read connection directly, so reload()
#  is a no-op kept only for API compatibility with existing callers.
# ============================================================

import logging
from datetime import datetime

from zilla import store

logger = logging.getLogger(__name__)

# Capability → roles allowed.
#   limited — may CHAT, but every request is held for owner approval (see bot.py
#             Approval mode). Cannot change settings, browse, schedule, etc.
#   admin   — full, unattended access (chat + settings/browse/file_gen/execution).
#   owner   — everything admins can, PLUS user management.
_CAPS = {
    "chat":     {"limited", "admin", "owner"},
    "admin":    {"admin", "owner"},   # settings, browse, file_gen, agy execution
    "users":    {"owner"},            # add/remove admins — owner only
}

# Roles an owner may assign to a stored account.
VALID_ROLES = ("admin", "limited")


class AuthManager:
    def __init__(self, users_file: str, owner_id: int = 0):
        self.users_file = users_file
        self.owner_id = owner_id
        self._store = store.get_store(users_file)
        self._import_legacy_json()

    def _import_legacy_json(self):
        """One-time compat shim: if users_file was a pre-M1 JSON blob
        ({"uid": {"name", "role", "added_at"}}), store.py detected it
        wasn't a SQLite file and stashed its parsed content on
        self._store.legacy_json (moving the original file aside). Import
        it now, normalizing any legacy role (e.g. the old "user" tier,
        removed in favor of admin/owner-only) to "admin" — the same
        normalization the old JSON-backed _load() did on every read."""
        data = self._store.legacy_json
        if not data or self._store.users_count() > 0:
            return
        for uid_str, info in data.items():
            role = info.get("role") if isinstance(info, dict) else None
            if role not in VALID_ROLES:
                role = "admin"
            self._store.users_add(
                int(uid_str),
                (info.get("name", "") if isinstance(info, dict) else ""),
                role,
                (info.get("added_at", "") if isinstance(info, dict) else ""),
            )

    def reload(self):
        """No-op — store reads are always live, there is no manager-level
        cache to invalidate. Kept for API compatibility (bot.py calls it)."""
        pass

    # ── Authorization ─────────────────────────────────────

    def is_authorized(self, user_id: int) -> bool:
        if user_id == self.owner_id:
            return True
        if self._store.users_is_denied(user_id):
            return False
        return self._store.users_get(user_id) is not None

    def is_owner(self, user_id: int) -> bool:
        return user_id == self.owner_id

    def is_admin(self, user_id: int) -> bool:
        return self.can(user_id, "admin")

    def role_of(self, user_id: int) -> str:
        """'owner' | 'admin' | 'limited' | 'none'."""
        if user_id == self.owner_id:
            return "owner"
        row = self._store.users_get(user_id)
        if row is None:
            return "none"
        return row.get("role") or "admin"

    def is_limited(self, user_id: int) -> bool:
        """Authorized, but every request must be approved by the owner."""
        return self.role_of(user_id) == "limited"

    def can(self, user_id: int, capability: str) -> bool:
        """Check if user has the given capability."""
        if user_id == self.owner_id:
            return True
        row = self._store.users_get(user_id)
        if row is None:
            return False
        allowed_roles = _CAPS.get(capability, set())
        role = row.get("role") or "admin"
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
        role = role if role in VALID_ROLES else "admin"
        added_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ok = self._store.users_add(user_id, name, role, added_at)
        if ok:
            logger.info(f"[USERS] Added {user_id} ({name}) as {role}")
        return ok

    def set_role(self, user_id: int, role: str) -> bool:
        """Change a stored user's role between 'admin' and 'limited'."""
        if role not in VALID_ROLES:
            return False
        ok = self._store.users_set_role(user_id, role)
        if ok:
            logger.info(f"[USERS] Set {user_id} role -> {role}")
        return ok

    def remove_user(self, user_id: int) -> bool:
        denied_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ok = self._store.users_remove(user_id, denied_at)
        if ok:
            logger.info(f"[USERS] Removed and denied {user_id}")
        return ok

    def list_users(self) -> dict[int, dict]:
        return self._store.users_list()

    def count(self) -> int:
        return self._store.users_count()
