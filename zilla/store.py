# ============================================================
#  STORE — SQLite operational truth (PLAN.md §3.1, Phase M1)
# ============================================================
#  One file (zilla.db) backs sessions, schedules, users, denied users,
#  the settings KV, usage counters, skill approvals, and the Markdown
#  search index. WAL mode gives readers their own isolation level that
#  never blocks on a writer, so each Store instance keeps two kinds of
#  connections: a read-only one per thread (PRAGMA query_only=ON — a
#  real, connection-enforced guarantee, not just convention) used
#  directly by sync getters, and one write connection whose every
#  transaction is serialized by an instance-owned lock. Bulk work
#  (migration, FTS reindex) opens its OWN short-lived connection from
#  inside asyncio.to_thread and commits in batches, so the writer lock
#  is never held for the duration of a bulk job.
#
#  Store is a class, not a module singleton, because the existing test
#  suite's isolation mechanism is "construct a fresh SessionManager /
#  ScheduleManager / AuthManager pointed at a distinct tmp file per
#  test, within the same process" (test_core.py, test_schedules_seam.py
#  — the latter frozen, never edit it). A single global connection
#  would make every manager instance in a test process share one
#  database regardless of the path nominally passed to it, silently
#  breaking that isolation. get_store(path) below caches one Store per
#  resolved path instead: distinct paths (distinct tests) get fully
#  independent databases; the same path (production's one zilla.db,
#  shared by all managers in one process) gets exactly one shared
#  connection pair and lock, not a wasteful duplicate per manager.
#
#  Import direction (load-bearing): this module imports nothing from
#  config.py. config.py imports this module and calls get_store() once
#  at startup with the DB path — prevents a circular import.
# ============================================================

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users (
    uid INTEGER PRIMARY KEY, name TEXT,
    role TEXT NOT NULL CHECK(role IN ('admin','limited')),
    added_at TEXT, added_by INTEGER
);
CREATE TABLE IF NOT EXISTS denied (uid INTEGER PRIMARY KEY, denied_at TEXT);
CREATE TABLE IF NOT EXISTS sessions (
    uid INTEGER NOT NULL, name TEXT NOT NULL,
    conv_id TEXT, conv_backend TEXT,
    last_seen_step INTEGER DEFAULT 0,
    auto_title TEXT, is_active INTEGER DEFAULT 0,
    messages INTEGER DEFAULT 0,
    last_used TEXT,
    created_at TEXT, updated_at TEXT,
    PRIMARY KEY (uid, name)
);
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY, uid INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    spec TEXT NOT NULL,
    title TEXT, prompt TEXT,
    session_name TEXT,
    session TEXT,
    payload_type TEXT DEFAULT 'message',
    backend TEXT, model TEXT,
    backend_pin_notified INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    system INTEGER DEFAULT 0,
    next_run REAL, last_run REAL,
    fail_count INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL, backend TEXT NOT NULL,
    turns INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
    fallbacks INTEGER DEFAULT 0,
    PRIMARY KEY (day, backend)
);
CREATE TABLE IF NOT EXISTS skill_approvals (
    slug TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    approved_at TEXT NOT NULL, approved_by INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
    path, title, body, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS mem_seen (path TEXT PRIMARY KEY, mtime REAL, size INTEGER);
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE,
    type TEXT, title TEXT, bio TEXT,
    is_ghost INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_nodes_title ON nodes(title);
CREATE TABLE IF NOT EXISTS aliases (
    alias TEXT NOT NULL, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias);
CREATE INDEX IF NOT EXISTS idx_aliases_node ON aliases(node_id);
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY,
    src INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    rel TEXT NOT NULL,
    dst INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    valid_from TEXT, valid_to TEXT,
    provenance TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
CREATE INDEX IF NOT EXISTS idx_edges_provenance ON edges(provenance);
CREATE TABLE IF NOT EXISTS curiosity (
    node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    gap TEXT NOT NULL,
    asked_at TEXT,
    PRIMARY KEY (node_id, gap)
);
CREATE INDEX IF NOT EXISTS idx_curiosity_node ON curiosity(node_id);
"""


def _configure(conn: sqlite3.Connection, *, read_only: bool) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    if read_only:
        conn.execute("PRAGMA query_only=ON")


class Store:
    """One SQLite database (one db_path) and all its connections. Use
    get_store(path) below rather than constructing this directly, so
    repeated calls with the same path share one instance."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._write_lock = threading.Lock()
        # Python's sqlite3 module explicitly disclaims thread-safety for
        # concurrent use of ONE Connection object from multiple threads
        # even with check_same_thread=False (that flag only disables the
        # sanity check, it adds no synchronization) — confirmed by a
        # concurrent-read smoke test that corrupted rows under a single
        # shared read connection. WAL is designed for many independent
        # reader connections, so each thread gets its own, lazily opened.
        self._read_local = threading.local()

        # Compatibility shim (distinct from the production first-start
        # migration in PLAN.md step 3, which imports the 5 well-known
        # legacy paths into ONE new zilla.db): a caller may still point a
        # manager directly at a pre-M1 JSON file that happens to already
        # exist at this exact path (e.g. a manager constructed against an
        # old "authorized_users.json"-style path). sqlite3 can't open that
        # file, so detect it, stash its parsed content on self.legacy_json
        # for the wrapper class to import on first use, and move it aside
        # so a fresh database can be created at the same path.
        self.legacy_json: Any = None
        if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
            with open(db_path, "rb") as f:
                header = f.read(16)
            if header[:16] != b"SQLite format 3\x00":
                try:
                    with open(db_path, "r", encoding="utf-8") as f:
                        self.legacy_json = json.load(f)
                except (json.JSONDecodeError, OSError):
                    self.legacy_json = None
                os.replace(db_path, db_path + ".pre-sqlite-migration.json")

        self._write_conn = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None
        )
        _configure(self._write_conn, read_only=False)
        self._write_conn.execute("PRAGMA journal_mode=WAL")
        with self._write_lock:
            self._write_conn.executescript(_SCHEMA)
            cur = self._write_conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            )
            if cur.fetchone() is None:
                self._write_conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )

    def close(self) -> None:
        """Release the write connection and drop this thread's read
        connection. Test-only (a fresh process never calls this in
        production) — lets test fixtures tear down a tmp DB between
        runs. Other threads' read connections, if any were opened, are
        simply abandoned — fine for tests, which run store lifecycles on
        a single thread."""
        self._write_conn.close()
        if getattr(self._read_local, "conn", None) is not None:
            self._read_local.conn.close()
            self._read_local.conn = None

    def _w(self) -> sqlite3.Connection:
        return self._write_conn

    def _r(self) -> sqlite3.Connection:
        conn = getattr(self._read_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.db_path, check_same_thread=False, isolation_level=None
            )
            _configure(conn, read_only=True)
            self._read_local.conn = conn
        return conn

    def connect_bulk(self) -> sqlite3.Connection:
        """A dedicated connection for bulk/long-running work (migration,
        FTS reindex) that a caller runs inside asyncio.to_thread. Never
        touches the shared write lock — callers must commit in batches
        themselves so no single transaction holds a lock for the job's
        full duration."""
        conn = sqlite3.connect(
            self.db_path, check_same_thread=False, isolation_level=None
        )
        _configure(conn, read_only=False)
        return conn

    def transaction(self, fn):
        """Public entry point for callers outside store.py (e.g.
        zilla/migrate.py's first-start import) that need several
        statements to commit as one atomic transaction — the same
        writer-lock mechanism every mutator method above uses via
        _write. Migration data is small (one owner's sessions/schedules/
        users), so one transaction is correct and simpler than batching;
        large bulk work (FTS reindex) should use connect_bulk() and
        commit in batches instead, so it never holds this lock for long."""
        return self._write(fn)

    def _write(self, fn):
        """Run fn(conn) inside the writer lock as one transaction."""
        with self._write_lock:
            conn = self._w()
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn(conn)
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return result

    # ── settings KV ────────────────────────────────────────────

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._r().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def set_setting(self, key: str, value: Any) -> None:
        payload = json.dumps(value)

        def _do(conn):
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, payload),
            )
        self._write(_do)

    def all_settings(self) -> dict:
        rows = self._r().execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    # ── users / denied ─────────────────────────────────────────

    def users_get(self, uid: int) -> dict | None:
        row = self._r().execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(row) if row else None

    def users_list(self) -> dict[int, dict]:
        rows = self._r().execute("SELECT * FROM users").fetchall()
        return {row["uid"]: dict(row) for row in rows}

    def users_count(self) -> int:
        return self._r().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    def users_add(self, uid: int, name: str, role: str, added_at: str, added_by: int | None = None) -> bool:
        def _do(conn):
            existing = conn.execute("SELECT 1 FROM users WHERE uid=?", (uid,)).fetchone()
            if existing:
                return False
            conn.execute(
                "INSERT INTO users (uid, name, role, added_at, added_by) VALUES (?, ?, ?, ?, ?)",
                (uid, name, role, added_at, added_by),
            )
            conn.execute("DELETE FROM denied WHERE uid=?", (uid,))
            return True
        return self._write(_do)

    def users_set_role(self, uid: int, role: str) -> bool:
        def _do(conn):
            cur = conn.execute("UPDATE users SET role=? WHERE uid=?", (role, uid))
            return cur.rowcount > 0
        return self._write(_do)

    def users_remove(self, uid: int, denied_at: str) -> bool:
        def _do(conn):
            cur = conn.execute("DELETE FROM users WHERE uid=?", (uid,))
            if cur.rowcount == 0:
                return False
            conn.execute(
                "INSERT INTO denied (uid, denied_at) VALUES (?, ?) "
                "ON CONFLICT(uid) DO UPDATE SET denied_at=excluded.denied_at",
                (uid, denied_at),
            )
            return True
        return self._write(_do)

    def users_is_denied(self, uid: int) -> bool:
        row = self._r().execute("SELECT 1 FROM denied WHERE uid=?", (uid,)).fetchone()
        return row is not None

    def users_denied_list(self) -> list[int]:
        rows = self._r().execute("SELECT uid FROM denied").fetchall()
        return [row["uid"] for row in rows]

    # ── sessions ────────────────────────────────────────────────

    def sessions_get(self, uid: int, name: str) -> dict | None:
        row = self._r().execute(
            "SELECT * FROM sessions WHERE uid=? AND name=?", (uid, name)
        ).fetchone()
        return dict(row) if row else None

    def sessions_list(self, uid: int) -> list[dict]:
        rows = self._r().execute(
            "SELECT * FROM sessions WHERE uid=? ORDER BY created_at", (uid,)
        ).fetchall()
        return [dict(row) for row in rows]

    def sessions_all_conv_ids(self) -> set:
        """Every non-null conv_id across ALL users/sessions/backends — the
        H1 brain-dir GC's 'still referenced' set (PLAN.md §6/H1 step 4)."""
        rows = self._r().execute(
            "SELECT conv_id FROM sessions WHERE conv_id IS NOT NULL"
        ).fetchall()
        return {r["conv_id"] for r in rows}

    def sessions_active_name(self, uid: int) -> str | None:
        """Raw active-flag lookup — None if no session is flagged active
        for this uid (caller applies the 'main' default, matching the
        old active_per_user.get(uid, 'main') fallback)."""
        row = self._r().execute(
            "SELECT name FROM sessions WHERE uid=? AND is_active=1", (uid,)
        ).fetchone()
        return row["name"] if row else None

    def sessions_set_active(self, uid: int, name: str) -> None:
        def _do(conn):
            conn.execute("UPDATE sessions SET is_active=0 WHERE uid=?", (uid,))
            conn.execute(
                "UPDATE sessions SET is_active=1 WHERE uid=? AND name=?", (uid, name)
            )
        self._write(_do)

    def sessions_upsert(self, uid: int, name: str, **fields) -> None:
        """Create the row if absent, else patch only the given columns.
        fields may include any of: conv_id, conv_backend, last_seen_step,
        auto_title, messages, last_used, created_at, updated_at."""
        allowed = {
            "conv_id", "conv_backend", "last_seen_step", "auto_title",
            "messages", "last_used", "created_at", "updated_at",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"sessions_upsert: unknown fields {bad}")

        def _do(conn):
            existing = conn.execute(
                "SELECT 1 FROM sessions WHERE uid=? AND name=?", (uid, name)
            ).fetchone()
            if existing is None:
                cols = ["uid", "name"] + list(fields.keys())
                vals = [uid, name] + list(fields.values())
                placeholders = ", ".join("?" for _ in cols)
                conn.execute(
                    f"INSERT INTO sessions ({', '.join(cols)}) VALUES ({placeholders})",
                    vals,
                )
            elif fields:
                set_clause = ", ".join(f"{k}=?" for k in fields)
                conn.execute(
                    f"UPDATE sessions SET {set_clause} WHERE uid=? AND name=?",
                    list(fields.values()) + [uid, name],
                )
        self._write(_do)

    def sessions_increment_messages(self, uid: int, name: str, last_used: str) -> bool:
        """Atomic `messages += 1` — a plain read-then-sessions_upsert from
        the caller would race under concurrent callers (lost-update: two
        readers fetch the same count before either writes back). This
        does the increment inside the row itself, in one write
        transaction."""
        def _do(conn):
            cur = conn.execute(
                "UPDATE sessions SET messages = messages + 1, last_used = ? "
                "WHERE uid=? AND name=?",
                (last_used, uid, name),
            )
            return cur.rowcount > 0
        return self._write(_do)

    def sessions_delete(self, uid: int, name: str) -> bool:
        def _do(conn):
            cur = conn.execute("DELETE FROM sessions WHERE uid=? AND name=?", (uid, name))
            return cur.rowcount > 0
        return self._write(_do)

    def sessions_rename(self, uid: int, old_name: str, new_name: str) -> bool:
        def _do(conn):
            existing = conn.execute(
                "SELECT 1 FROM sessions WHERE uid=? AND name=?", (uid, old_name)
            ).fetchone()
            if existing is None:
                return False
            clash = conn.execute(
                "SELECT 1 FROM sessions WHERE uid=? AND name=?", (uid, new_name)
            ).fetchone()
            if clash is not None:
                return False
            conn.execute(
                "UPDATE sessions SET name=? WHERE uid=? AND name=?",
                (new_name, uid, old_name),
            )
            return True
        return self._write(_do)

    # ── schedules ───────────────────────────────────────────────

    _SCHEDULE_JSON_FIELDS = {"spec"}

    def _schedule_row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        if d.get("spec") is not None:
            d["spec"] = json.loads(d["spec"])
        return d

    def schedules_get(self, sid: str) -> dict | None:
        row = self._r().execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
        return self._schedule_row_to_dict(row) if row else None

    def schedules_list(self, uid: int) -> list[dict]:
        rows = self._r().execute("SELECT * FROM schedules WHERE uid=?", (uid,)).fetchall()
        return [self._schedule_row_to_dict(row) for row in rows]

    def schedules_all(self) -> list[dict]:
        rows = self._r().execute("SELECT * FROM schedules").fetchall()
        return [self._schedule_row_to_dict(row) for row in rows]

    def schedules_insert(self, sched: dict) -> None:
        cols = list(sched.keys())
        vals = [json.dumps(v) if k in self._SCHEDULE_JSON_FIELDS else v for k, v in sched.items()]
        placeholders = ", ".join("?" for _ in cols)

        def _do(conn):
            conn.execute(
                f"INSERT INTO schedules ({', '.join(cols)}) VALUES ({placeholders})", vals
            )
        self._write(_do)

    def schedules_update(self, sid: str, **fields) -> bool:
        if not fields:
            return self.schedules_get(sid) is not None
        vals = [json.dumps(v) if k in self._SCHEDULE_JSON_FIELDS else v for k, v in fields.items()]
        set_clause = ", ".join(f"{k}=?" for k in fields)

        def _do(conn):
            cur = conn.execute(
                f"UPDATE schedules SET {set_clause} WHERE id=?", vals + [sid]
            )
            return cur.rowcount > 0
        return self._write(_do)

    def schedules_delete(self, sid: str, uid: int) -> bool:
        def _do(conn):
            cur = conn.execute("DELETE FROM schedules WHERE id=? AND uid=?", (sid, uid))
            return cur.rowcount > 0
        return self._write(_do)

    # ── usage ───────────────────────────────────────────────────

    def usage_bump(self, day: str, backend: str, *, turns: int = 0, errors: int = 0, fallbacks: int = 0) -> None:
        def _do(conn):
            conn.execute(
                "INSERT INTO usage (day, backend, turns, errors, fallbacks) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(day, backend) DO UPDATE SET "
                "turns=turns+excluded.turns, errors=errors+excluded.errors, "
                "fallbacks=fallbacks+excluded.fallbacks",
                (day, backend, turns, errors, fallbacks),
            )
        self._write(_do)

    def usage_for_day(self, day: str) -> list[dict]:
        rows = self._r().execute("SELECT * FROM usage WHERE day=?", (day,)).fetchall()
        return [dict(row) for row in rows]

    # ── skill approvals ─────────────────────────────────────────

    def skill_approval_get(self, slug: str) -> dict | None:
        row = self._r().execute(
            "SELECT * FROM skill_approvals WHERE slug=?", (slug,)
        ).fetchone()
        return dict(row) if row else None

    def skill_approval_set(self, slug: str, code_hash: str, approved_at: str, approved_by: int) -> None:
        def _do(conn):
            conn.execute(
                "INSERT INTO skill_approvals (slug, code_hash, approved_at, approved_by) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(slug) DO UPDATE SET "
                "code_hash=excluded.code_hash, approved_at=excluded.approved_at, "
                "approved_by=excluded.approved_by",
                (slug, code_hash, approved_at, approved_by),
            )
        self._write(_do)

    # ── mem_fts / mem_seen (Markdown search index — Phase M3 wires this in) ──

    def fts_index(self, path: str, title: str, body: str) -> None:
        def _do(conn):
            conn.execute("DELETE FROM mem_fts WHERE path=?", (path,))
            conn.execute(
                "INSERT INTO mem_fts (path, title, body) VALUES (?, ?, ?)",
                (path, title, body),
            )
        self._write(_do)

    def fts_delete(self, path: str) -> None:
        def _do(conn):
            conn.execute("DELETE FROM mem_fts WHERE path=?", (path,))
            conn.execute("DELETE FROM mem_seen WHERE path=?", (path,))
        self._write(_do)

    def fts_search(self, query: str, limit: int = 10) -> list[dict]:
        rows = self._r().execute(
            "SELECT path, title, snippet(mem_fts, 2, '[', ']', '...', 12) AS snippet "
            "FROM mem_fts WHERE mem_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def mem_seen_get(self, path: str) -> dict | None:
        row = self._r().execute("SELECT * FROM mem_seen WHERE path=?", (path,)).fetchone()
        return dict(row) if row else None

    def mem_seen_set(self, path: str, mtime: float, size: int) -> None:
        def _do(conn):
            conn.execute(
                "INSERT INTO mem_seen (path, mtime, size) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size",
                (path, mtime, size),
            )
        self._write(_do)

    def mem_seen_all(self) -> list[dict]:
        """Every indexed path (Phase M3's reindex() diffs this against what's
        actually on disk to find deletions)."""
        rows = self._r().execute("SELECT path, mtime, size FROM mem_seen").fetchall()
        return [dict(row) for row in rows]

    # ── graph (nodes / aliases / edges — Phase K1) ───────────────────────
    # Disposable, rebuildable from Memory/Wiki/**.md — the pages are the
    # truth (PLAN.md §6). zilla/graph.py owns parsing + indexing; these are
    # thin table-shaped primitives only.

    def graph_node_get(self, node_id: int) -> dict | None:
        row = self._r().execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        return dict(row) if row else None

    def graph_node_get_by_path(self, path: str) -> dict | None:
        row = self._r().execute("SELECT * FROM nodes WHERE path=?", (path,)).fetchone()
        return dict(row) if row else None

    def graph_node_get_by_title(self, title: str) -> dict | None:
        """Case-insensitive exact title match — used both to resolve a
        [[Wiki-link]] to an existing node and to find a ghost eligible for
        promotion when its real page is indexed."""
        row = self._r().execute(
            "SELECT * FROM nodes WHERE title=? COLLATE NOCASE LIMIT 1", (title,)
        ).fetchone()
        return dict(row) if row else None

    def graph_node_insert(self, *, path: str | None, type: str | None,
                          title: str, bio: str | None, is_ghost: bool) -> int:
        def _do(conn):
            cur = conn.execute(
                "INSERT INTO nodes (path, type, title, bio, is_ghost) VALUES (?, ?, ?, ?, ?)",
                (path, type, title, bio, 1 if is_ghost else 0),
            )
            return cur.lastrowid
        return self._write(_do)

    def graph_node_update(self, node_id: int, *, type: str | None, title: str,
                          bio: str | None, is_ghost: bool) -> None:
        def _do(conn):
            conn.execute(
                "UPDATE nodes SET type=?, title=?, bio=?, is_ghost=? WHERE id=?",
                (type, title, bio, 1 if is_ghost else 0, node_id),
            )
        self._write(_do)

    def graph_node_promote(self, node_id: int, *, path: str, type: str | None,
                           title: str, bio: str | None) -> None:
        """A ghost node's real page has appeared — stamp its path and
        clear is_ghost in one call (graph_node_update never touches path,
        so a plain update alone can't do this)."""
        def _do(conn):
            conn.execute(
                "UPDATE nodes SET path=?, type=?, title=?, bio=?, is_ghost=0 WHERE id=?",
                (path, type, title, bio, node_id),
            )
        self._write(_do)

    def graph_node_demote_to_ghost(self, node_id: int) -> None:
        """A real page was deleted but other pages still reference this
        node — keep it as a hollow ghost rather than orphaning edges."""
        def _do(conn):
            conn.execute(
                "UPDATE nodes SET path=NULL, type=NULL, bio=NULL, is_ghost=1 WHERE id=?",
                (node_id,),
            )
        self._write(_do)

    def graph_node_delete(self, node_id: int) -> None:
        def _do(conn):
            conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        self._write(_do)

    def graph_nodes_all(self) -> list[dict]:
        rows = self._r().execute("SELECT * FROM nodes").fetchall()
        return [dict(row) for row in rows]

    def graph_clear(self) -> None:
        """Wipe the entire graph — the rebuild-from-scratch path. Disposable
        by design; the Markdown pages are re-parsed right after this."""
        def _do(conn):
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM aliases")
            conn.execute("DELETE FROM nodes")
        self._write(_do)

    def graph_aliases_set(self, node_id: int, aliases: list[str]) -> None:
        """Replace this node's alias set wholesale."""
        def _do(conn):
            conn.execute("DELETE FROM aliases WHERE node_id=?", (node_id,))
            for alias in aliases:
                conn.execute(
                    "INSERT INTO aliases (alias, node_id) VALUES (?, ?)", (alias, node_id)
                )
        self._write(_do)

    def graph_aliases_all(self) -> list[dict]:
        """Every (alias, node_id) pair — the candidate set K2's turn-time
        alias scan matches owner messages against."""
        rows = self._r().execute("SELECT alias, node_id FROM aliases").fetchall()
        return [dict(row) for row in rows]

    def graph_alias_lookup(self, name: str) -> int | None:
        """Resolve a bare name to a node id via aliases OR the node's own
        title (case-insensitive exact match), aliases first."""
        row = self._r().execute(
            "SELECT node_id FROM aliases WHERE alias=? COLLATE NOCASE LIMIT 1", (name,)
        ).fetchone()
        if row:
            return row["node_id"]
        row = self._r().execute(
            "SELECT id FROM nodes WHERE title=? COLLATE NOCASE LIMIT 1", (name,)
        ).fetchone()
        return row["id"] if row else None

    def graph_edges_replace_for_path(self, path: str, edges: list[dict]) -> None:
        """Drop every edge previously contributed by this page (provenance
        prefix 'path:') and insert the freshly parsed set — re-indexing one
        page never touches edges another page contributed."""
        prefix = f"{path}:%"

        def _do(conn):
            conn.execute("DELETE FROM edges WHERE provenance LIKE ?", (prefix,))
            for e in edges:
                conn.execute(
                    "INSERT INTO edges (src, rel, dst, valid_from, valid_to, provenance) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (e["src"], e["rel"], e["dst"], e.get("valid_from"), e.get("valid_to"),
                     e.get("provenance")),
                )
        self._write(_do)

    def graph_edges_all(self, *, history: bool = False) -> list[dict]:
        """Every edge, current-facts-only unless history=True."""
        if history:
            rows = self._r().execute("SELECT * FROM edges").fetchall()
        else:
            rows = self._r().execute("SELECT * FROM edges WHERE valid_to IS NULL").fetchall()
        return [dict(row) for row in rows]

    def graph_edges_incoming_count(self, node_id: int) -> int:
        return self._r().execute(
            "SELECT COUNT(*) AS n FROM edges WHERE dst=?", (node_id,)
        ).fetchone()["n"]

    # ── curiosity (Phase K3) ──────────────────────────────────────────────
    # Deterministic gap-detection results, one row per (node, gap kind).
    # asked_at is the only state that ISN'T re-derivable from the wiki on
    # the next reindex (it's the cooldown clock) — curiosity_sync_node
    # diffs against the gap set currently detected for one node so a gap
    # that's still open keeps its asked_at, and a gap that's been closed
    # (owner filled in the missing fact) simply disappears.

    def curiosity_sync_node(self, node_id: int, gaps: list[str]) -> None:
        gap_set = set(gaps)

        def _do(conn):
            existing = {
                r["gap"] for r in conn.execute(
                    "SELECT gap FROM curiosity WHERE node_id=?", (node_id,)
                ).fetchall()
            }
            for gap in gap_set - existing:
                conn.execute(
                    "INSERT OR IGNORE INTO curiosity (node_id, gap, asked_at) "
                    "VALUES (?, ?, NULL)", (node_id, gap),
                )
            for gap in existing - gap_set:
                conn.execute(
                    "DELETE FROM curiosity WHERE node_id=? AND gap=?", (node_id, gap)
                )
        self._write(_do)

    def curiosity_pending(self, node_ids: list[int], *, cooldown_before: str) -> list[dict]:
        """Rows for `node_ids` that are askable now: never asked, or asked
        before `cooldown_before` (ISO timestamps sort lexically). []
        for an empty node_ids list."""
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        rows = self._r().execute(
            f"SELECT * FROM curiosity WHERE node_id IN ({placeholders}) "
            f"AND (asked_at IS NULL OR asked_at < ?) ORDER BY node_id",
            (*node_ids, cooldown_before),
        ).fetchall()
        return [dict(row) for row in rows]

    def curiosity_mark_asked(self, node_id: int, gap: str, asked_at: str) -> None:
        def _do(conn):
            conn.execute(
                "UPDATE curiosity SET asked_at=? WHERE node_id=? AND gap=?",
                (asked_at, node_id, gap),
            )
        self._write(_do)

    def curiosity_all(self) -> list[dict]:
        rows = self._r().execute("SELECT * FROM curiosity").fetchall()
        return [dict(row) for row in rows]

    # ── introspection (used by install.py --doctor, Phase M1 step 4) ────────

    def schema_version(self) -> int | None:
        row = self._r().execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        return int(row["value"]) if row else None

    def is_wal_mode(self) -> bool:
        mode = self._w().execute("PRAGMA journal_mode").fetchone()[0]
        return str(mode).lower() == "wal"

    def write_probe(self) -> bool:
        """Doctor check: prove the writer lock + a real transaction works."""
        try:
            def _do(conn):
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('_write_probe', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value='1'"
                )
            self._write(_do)
            return True
        except sqlite3.Error:
            return False

    def backup_to(self, dest_path: str) -> None:
        """Snapshot this database to dest_path via VACUUM INTO — one
        consistent, defragmented copy, safe to take while the bot keeps
        running (PLAN.md §5 M1 step 6 corruption-recovery story). VACUUM
        INTO refuses to write over an existing file, so remove dest_path
        first; held under the write lock so no writer runs concurrently
        with the snapshot."""
        if os.path.exists(dest_path):
            os.remove(dest_path)
        with self._write_lock:
            self._write_conn.execute("VACUUM INTO ?", (dest_path,))


# ── path-keyed cache — see module docstring for why this exists ─────────

_stores: dict[str, Store] = {}
_stores_lock = threading.Lock()


def get_store(db_path: str) -> Store:
    """Return the Store for this path, creating it on first use. Callers
    that pass the same resolved path (e.g. every manager in production,
    all pointed at the one zilla.db) share one Store; callers that pass
    distinct paths (e.g. each test's own tmp file) get fully independent
    databases — matching the pre-SQLite JSON-file isolation model with
    no test-file changes required."""
    path = os.path.abspath(db_path)
    with _stores_lock:
        store = _stores.get(path)
        if store is None:
            store = Store(path)
            _stores[path] = store
        return store


def close_all() -> None:
    """Close and drop every cached Store. Test-only — lets a test run
    close out its tmp databases; production never calls this."""
    with _stores_lock:
        for store in _stores.values():
            store.close()
        _stores.clear()
