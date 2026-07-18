# ============================================================
#  MIGRATE — first-start import of the pre-M1 JSON files into zilla.db
# ============================================================
#  PLAN.md §3.1 / §5 M1 step 3: on first start, import ALL legacy files
#  (sessions.json, schedules.json, authorized_users.json,
#  denied_users.json, settings.json) into the new shared zilla.db in ONE
#  transaction, then rename each to "<name>.migrated" — never delete the
#  original. Every insert is a keyed upsert (ON CONFLICT DO NOTHING), so:
#    - re-running against files that were already imported is a no-op
#      (the store already has those rows; nothing changes).
#    - a crash mid-import never partially commits (single transaction),
#      so the retry on next start just redoes the whole import safely.
#  Files are renamed ONLY after a successful commit, so an interrupted
#  run leaves the *.json originals exactly where the next start's retry
#  expects to find them.
#
#  This module knows the JSON *shapes* (sessions.json's
#  {"sessions": {...}, "active_per_user": {...}} nesting, schedules.json's
#  flat {sid: {...}} map, etc.) — knowledge that belongs here, not in
#  store.py, which stays a generic SQL layer with no opinion on legacy
#  file formats.
# ============================================================

from __future__ import annotations

import json
import logging
import os
import shutil

from zilla.store import Store

logger = logging.getLogger(__name__)

# Mirrors zilla/users.py's VALID_ROLES — any legacy/unknown role (e.g. the
# retired "user" tier) normalizes to "admin", matching the old JSON-backed
# AuthManager._load()'s behavior.
_VALID_ROLES = ("admin", "limited")


def _read_json(path: str | None):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"[MIGRATE] Failed to read {path}: {e}")
        return None


def migrate_legacy_json(
    db: Store,
    *,
    sessions_file: str | None = None,
    schedules_file: str | None = None,
    users_file: str | None = None,
    denied_file: str | None = None,
    settings_file: str | None = None,
) -> dict:
    """Import any of the five legacy JSON files that still exist at the
    given paths into db. Returns a dict of rows-attempted per table (0 for
    any file that was absent or already migrated). Safe to call on every
    startup — see module docstring for the idempotency/atomicity story."""
    stats = {"sessions": 0, "schedules": 0, "users": 0, "denied": 0, "settings": 0}

    sessions_data = _read_json(sessions_file)
    schedules_data = _read_json(schedules_file)
    users_data = _read_json(users_file)
    denied_data = _read_json(denied_file)
    settings_data = _read_json(settings_file)

    if not any(d is not None for d in
               (sessions_data, schedules_data, users_data, denied_data, settings_data)):
        return stats

    def _do(conn):
        if isinstance(sessions_data, dict):
            active = sessions_data.get("active_per_user", {}) or {}
            for s in (sessions_data.get("sessions", {}) or {}).values():
                if not isinstance(s, dict):
                    continue
                uid, name = s.get("user_id"), s.get("name")
                if uid is None or not name:
                    continue
                is_active = 1 if active.get(str(uid)) == name else 0
                conn.execute(
                    "INSERT INTO sessions (uid, name, conv_id, conv_backend, "
                    "last_seen_step, auto_title, is_active, messages, last_used, "
                    "created_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(uid, name) DO NOTHING",
                    (uid, name, s.get("conversation_id"), s.get("conv_backend"),
                     s.get("last_seen_step", 0), s.get("title"), is_active,
                     s.get("messages", 0), s.get("last_used"), s.get("created")),
                )
                stats["sessions"] += 1

        if isinstance(schedules_data, dict):
            for sid, s in schedules_data.items():
                if not isinstance(s, dict) or not s.get("id"):
                    continue
                conn.execute(
                    "INSERT INTO schedules (id, uid, chat_id, kind, spec, title, "
                    "prompt, session_name, session, payload_type, backend, model, "
                    "backend_pin_notified, enabled, next_run, last_run, fail_count, "
                    "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO NOTHING",
                    (s["id"], s.get("user_id"), s.get("chat_id"), s.get("kind"),
                     json.dumps(s.get("spec", {})), s.get("title"), s.get("prompt"),
                     s.get("session_name"), s.get("session"),
                     s.get("payload_type", "message"), s.get("backend"), s.get("model"),
                     1 if s.get("backend_pin_notified") else 0,
                     1 if s.get("enabled", True) else 0,
                     s.get("next_run"), s.get("last_run"), s.get("fail_count", 0),
                     s.get("created")),
                )
                stats["schedules"] += 1

        if isinstance(users_data, dict):
            for uid_str, info in users_data.items():
                if not isinstance(info, dict):
                    continue
                role = info.get("role")
                if role not in _VALID_ROLES:
                    role = "admin"
                conn.execute(
                    "INSERT INTO users (uid, name, role, added_at, added_by) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(uid) DO NOTHING",
                    (int(uid_str), info.get("name", ""), role,
                     info.get("added_at", ""), info.get("added_by")),
                )
                stats["users"] += 1

        if isinstance(denied_data, list):
            for uid in denied_data:
                conn.execute(
                    "INSERT INTO denied (uid, denied_at) VALUES (?, ?) "
                    "ON CONFLICT(uid) DO NOTHING",
                    (int(uid), None),
                )
                stats["denied"] += 1

        if isinstance(settings_data, dict):
            for key, value in settings_data.items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (key, json.dumps(value)),
                )
                stats["settings"] += 1

    db.transaction(_do)

    # Rename only after a successful commit — never delete originals.
    for path, data in (
        (sessions_file, sessions_data), (schedules_file, schedules_data),
        (users_file, users_data), (denied_file, denied_data),
        (settings_file, settings_data),
    ):
        if path and data is not None and os.path.exists(path):
            try:
                os.replace(path, path + ".migrated")
            except OSError as e:
                logger.error(f"[MIGRATE] Failed to rename {path}: {e}")

    imported = {k: v for k, v in stats.items() if v}
    if imported:
        logger.info(f"[MIGRATE] Imported legacy JSON into {db.db_path}: {imported}")
    return stats


def _move_once(src: str | None, dst: str) -> bool:
    """Move src -> dst if src exists and dst doesn't yet (never clobbers a
    destination, never deletes src on failure — shutil.move only removes the
    source once the copy to dst has fully succeeded)."""
    if not src or not (os.path.exists(src) or os.path.islink(src)):
        return False
    if os.path.exists(dst):
        logger.warning(f"[MIGRATE] {dst} already exists — leaving {src} in place")
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.move(src, dst)
        return True
    except OSError as e:
        logger.error(f"[MIGRATE] Failed to move {src} -> {dst}: {e}")
        return False


def migrate_zilla_home(
    *,
    zilla_home: str,
    legacy_agi_brain_dir: str | None = None,
    legacy_memory_dir: str | None = None,
    legacy_db_file: str | None = None,
) -> dict:
    """One-time move onto the ZILLA_HOME storage constitution (PLAN.md §17
    F1): legacy ~/AGI-Brain's Inbox/Outbox/Bridge, PLUS the repo-root
    Memory/ tree and zilla.db that M1-M4 already created there before F1
    existed (F1 was written assuming AGI-Brain still held Memory/state;
    those phases shipped first and anchored them at the repo root instead —
    documented deviation, not a silent rewrite of the accepted M1-M4 work).

    No-op if `zilla_home` already exists (already migrated, or a fresh
    install with nothing to bring over). Idempotent and non-destructive:
    each item only moves if its new-layout destination doesn't already
    exist; nothing is ever deleted, only moved once."""
    moved = {
        "inbox": False, "outbox": False, "bridge": False,
        "memory": False, "db": False, "agi_brain_symlink": False,
    }
    if os.path.isdir(zilla_home) or os.path.islink(zilla_home):
        return moved

    had_agi_brain = bool(
        legacy_agi_brain_dir and os.path.isdir(legacy_agi_brain_dir)
        and not os.path.islink(legacy_agi_brain_dir)
    )

    if had_agi_brain:
        moved["inbox"] = _move_once(
            os.path.join(legacy_agi_brain_dir, "Inbox"),
            os.path.join(zilla_home, "Media", "Inbox"),
        )
        moved["outbox"] = _move_once(
            os.path.join(legacy_agi_brain_dir, "Outbox"),
            os.path.join(zilla_home, "Outbox"),
        )
        moved["bridge"] = _move_once(
            os.path.join(legacy_agi_brain_dir, "Bridge"),
            os.path.join(zilla_home, "Runtime", "Bridge"),
        )

    if legacy_memory_dir and os.path.isdir(legacy_memory_dir):
        moved["memory"] = _move_once(
            legacy_memory_dir, os.path.join(zilla_home, "Memory")
        )

    if legacy_db_file and os.path.exists(legacy_db_file):
        dst_db = os.path.join(zilla_home, "Runtime", "zilla.db")
        moved["db"] = _move_once(legacy_db_file, dst_db)
        if moved["db"]:
            for suffix in ("-wal", "-shm", ".bak", ".bak.1"):
                _move_once(legacy_db_file + suffix, dst_db + suffix)

    if had_agi_brain:
        try:
            leftovers = [n for n in os.listdir(legacy_agi_brain_dir) if n != ".DS_Store"]
            if not leftovers:
                ds_store = os.path.join(legacy_agi_brain_dir, ".DS_Store")
                if os.path.exists(ds_store):
                    os.remove(ds_store)
                os.rmdir(legacy_agi_brain_dir)
                os.symlink(zilla_home, legacy_agi_brain_dir)
                moved["agi_brain_symlink"] = True
            else:
                logger.warning(
                    f"[MIGRATE] {legacy_agi_brain_dir} still has {leftovers} — "
                    "not replacing with a symlink"
                )
        except OSError as e:
            logger.error(f"[MIGRATE] Could not symlink {legacy_agi_brain_dir}: {e}")

    os.makedirs(zilla_home, exist_ok=True)
    if any(moved.values()):
        logger.info(f"[MIGRATE] ZILLA_HOME migration -> {zilla_home}: {moved}")
    return moved
