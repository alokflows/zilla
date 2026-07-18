# ============================================================
#  SESSIONS — Per-User Session Management
# ============================================================
#  Each user gets isolated sessions mapping to CLI conversation
#  IDs. Supports create, delete, rename, switch.
#
#  Persistence: a thin wrapper over store.py (Phase M1). No in-memory
#  session cache — every read hits the store's read connection directly.
#  The active-session pointer is a per-row is_active flag (store schema
#  §3.1) rather than a separate name map, so renaming the active session
#  carries the flag automatically (same row, new name column).
# ============================================================

from datetime import datetime

from zilla import store


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(row: dict) -> dict:
    """Translate a store sessions row into the pre-M1 JSON shape that
    bot.py / keyboards.py / tests already read (conv_id -> conversation_id,
    created_at -> created, auto_title -> title)."""
    return {
        "name": row["name"],
        "user_id": row["uid"],
        "conversation_id": row.get("conv_id"),
        "conv_backend": row.get("conv_backend"),
        "created": row.get("created_at"),
        "last_used": row.get("last_used"),
        "messages": row.get("messages") or 0,
        "last_seen_step": row.get("last_seen_step") or 0,
        "title": row.get("auto_title"),
    }


class SessionManager:
    """Per-user persistent sessions. Each maps to a CLI conversation ID."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self._store = store.get_store(state_file)

    # ── Active Session ────────────────────────────────────

    def get_active_name(self, user_id: int) -> str:
        return self._store.sessions_active_name(user_id) or "main"

    def set_active_name(self, name: str, user_id: int):
        self._store.sessions_set_active(user_id, name)

    # ── Conversation ID ───────────────────────────────────

    def get_conversation_id(self, user_id: int, session_name: str = None) -> str | None:
        name = session_name or self.get_active_name(user_id)
        row = self._store.sessions_get(user_id, name)
        return row.get("conv_id") if row else None

    def set_conversation_id(self, conv_id: str, user_id: int, session_name: str = None,
                            backend: str = None):
        name = session_name or self.get_active_name(user_id)
        fields = {"conv_id": conv_id}
        if backend is not None:
            # Conversation ids are backend-specific (agy brain dir vs claude session).
            # Remember which backend made this one so we never resume it on the other.
            fields["conv_backend"] = backend
        if self._store.sessions_get(user_id, name) is None:
            fields["created_at"] = _now()
            fields["messages"] = 0
            fields["last_seen_step"] = 0
        self._store.sessions_upsert(user_id, name, **fields)

    def get_conv_backend(self, user_id: int, session_name: str = None) -> str | None:
        name = session_name or self.get_active_name(user_id)
        row = self._store.sessions_get(user_id, name)
        return row.get("conv_backend") if row else None

    def all_conversation_ids(self) -> set:
        """Every non-null conv_id across every user/session/backend — the
        H1 brain-dir GC's 'still referenced' set (PLAN.md §6/H1 step 4)."""
        return self._store.sessions_all_conv_ids()

    # ── Last Seen Step ────────────────────────────────────

    def get_last_seen_step(self, user_id: int) -> int:
        name = self.get_active_name(user_id)
        row = self._store.sessions_get(user_id, name)
        return (row.get("last_seen_step") or 0) if row else 0

    def set_last_seen_step(self, step: int, user_id: int, session_name: str = None):
        name = session_name or self.get_active_name(user_id)
        if self._store.sessions_get(user_id, name) is not None:
            self._store.sessions_upsert(user_id, name, last_seen_step=step)

    # ── Message Count ─────────────────────────────────────

    def increment_messages(self, user_id: int, session_name: str = None):
        name = session_name or self.get_active_name(user_id)
        self._store.sessions_increment_messages(user_id, name, _now())

    # ── Auto-Title ────────────────────────────────────────

    def auto_title(self, first_message: str, user_id: int, session_name: str = None):
        name = session_name or self.get_active_name(user_id)
        row = self._store.sessions_get(user_id, name)
        if not row or row.get("auto_title"):
            return
        words = [w for w in first_message.strip().split() if len(w) > 1][:5]
        if not words:
            words = first_message.strip().split()[:5]
        title = " ".join(words)
        if len(title) > 40:
            title = title[:37] + "..."
        title = title.strip(".,!?;:\"'")
        if title:
            self._store.sessions_upsert(user_id, name, auto_title=title)

    # ── CRUD ──────────────────────────────────────────────

    def create_session(self, name: str, user_id: int) -> bool:
        if self._store.sessions_get(user_id, name) is not None:
            return False
        self._store.sessions_upsert(
            user_id, name, created_at=_now(), messages=0, last_seen_step=0,
        )
        self.set_active_name(name, user_id)
        return True

    def delete_session(self, name: str, user_id: int) -> bool:
        if self._store.sessions_get(user_id, name) is None:
            return False
        was_active = self.get_active_name(user_id) == name
        self._store.sessions_delete(user_id, name)
        if was_active:
            remaining = self.list_sessions(user_id)
            self.set_active_name(next(iter(remaining), "main"), user_id)
        return True

    def rename_session(self, old_name: str, new_name: str, user_id: int) -> bool:
        # A rename is an UPDATE on the same row, so an is_active flag (if
        # set) follows the row to its new name automatically — no separate
        # active-pointer housekeeping needed here.
        return self._store.sessions_rename(user_id, old_name, new_name)

    def list_sessions(self, user_id: int) -> dict:
        """Return {name: info} for all sessions owned by user."""
        rows = self._store.sessions_list(user_id)
        return {row["name"]: _row_to_dict(row) for row in rows}

    def get_session_info(self, user_id: int, session_name: str = None) -> dict | None:
        name = session_name or self.get_active_name(user_id)
        row = self._store.sessions_get(user_id, name)
        if not row:
            return None
        return {
            "name": row["name"],
            "title": row.get("auto_title"),
            "conversation_id": row.get("conv_id"),
            "created": row.get("created_at"),
            "last_used": row.get("last_used"),
            "messages": row.get("messages") or 0,
            "is_active": row["name"] == self.get_active_name(user_id),
        }
