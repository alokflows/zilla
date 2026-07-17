# ============================================================
#  SCHEDULES — Persistent, recurring automation jobs
# ============================================================
#  Each schedule runs a prompt through agy at its scheduled time
#  and DMs the result back. Supports:
#    once     — run at a specific datetime, then done
#    interval — every N seconds (e.g. every 5 hours)
#    daily    — every day at HH:MM (local time)
#    weekly   — chosen weekdays at HH:MM (local time)
#
#  The next-run math (compute_next_run) is pure and unit-tested.
#  Times are local; epoch seconds are stored on disk.
#
#  Persistence: a thin wrapper over store.py (Phase M1). No in-memory
#  schedule cache — every read hits the store's read connection
#  directly. The store's schedules table uses "uid" for the owning
#  user (SQL convention matching users/sessions) and "created_at" for
#  the creation timestamp; ScheduleManager translates these back to
#  the pre-M1 "user_id"/"created" keys that bot.py, core.py and the
#  tests already read.
# ============================================================

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta

from zilla import store

logger = logging.getLogger(__name__)

VALID_KINDS = ("once", "interval", "daily", "weekly")

# payload_type: what firing a schedule actually does.
#   message      — full CLI turn (default; every pre-Part-B schedule is this).
#   system_event — deliver the stored prompt text verbatim, ZERO model call.
#   command      — run the prompt as a subprocess, ZERO model call. Owner-only
#                  at creation (enforced in ScheduleManager.add(), not a
#                  comment — a command schedule is unattended shell execution).
VALID_PAYLOAD_TYPES = ("message", "system_event", "command")

# Self-healing retry backoff: 30s, 1m, 5m, 15m, 60m. mark_failure() walks
# this ladder before giving up on the current occurrence — giving up NEVER
# permanently disables a schedule, it resets and advances to the next normal
# occurrence (a daily job that fails today still runs tomorrow).
RETRY_LADDER = (30, 60, 300, 900, 3600)


def resolve_session_mode(sched: dict) -> str:
    """The conversation-continuity mode a schedule runs with.

    'isolated'   — fresh conversation every run (today's real, discovered
                   default: no existing schedule has ever set session_name).
    'main'       — the user's currently-active named session.
    'named:<x>'  — a specific named session, always the same one.

    An explicit 'session' field always wins. Falls back to the legacy
    'session_name' field (pre-Part-B schedules) mapped to 'named:<x>'.
    Missing both -> 'isolated'.
    """
    session = sched.get("session")
    if session:
        return session
    sname = sched.get("session_name")
    if sname:
        return f"named:{sname}"
    return "isolated"


def backend_pin_mismatch(sched: dict, current_backend: str, current_model) -> bool:
    """True if a schedule's pinned backend/model has drifted from what's
    active right now AND the owner hasn't already been told once.

    No pinned backend (pre-Part-B schedules, or never set) -> never
    mismatches. A pinned model of None means "any model on that backend" —
    only the backend is compared. Once `backend_pin_notified` is set, this
    always returns False (one-time note, never repeats).
    """
    if sched.get("backend_pin_notified"):
        return False
    pinned_backend = sched.get("backend")
    if not pinned_backend:
        return False
    if pinned_backend != current_backend:
        return True
    pinned_model = sched.get("model")
    if pinned_model is None:
        return False
    return pinned_model != current_model


def _slot_today(now_dt: datetime, hh: int, mm: int) -> datetime:
    return now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)


def compute_next_run(kind: str, spec: dict, after: float) -> float | None:
    """
    Return the next run time (epoch seconds) strictly greater than `after`,
    or None if the schedule has no future occurrence (a past one-off).

    - once:     spec{run_at}     → run_at if in the future, else None
    - interval: spec{seconds}    → after + seconds
    - daily:    spec{hh,mm}      → next HH:MM at/after `after`
    - weekly:   spec{days,hh,mm} → next chosen weekday at HH:MM after `after`
    """
    if kind == "once":
        run_at = float(spec.get("run_at", 0))
        return run_at if run_at > after else None

    if kind == "interval":
        seconds = max(1, int(spec.get("seconds", 0)))
        return after + seconds

    after_dt = datetime.fromtimestamp(after)

    if kind == "daily":
        hh, mm = int(spec.get("hh", 0)), int(spec.get("mm", 0))
        slot = _slot_today(after_dt, hh, mm)
        if slot.timestamp() <= after:
            slot += timedelta(days=1)
        return slot.timestamp()

    if kind == "weekly":
        days = sorted({int(d) % 7 for d in spec.get("days", [])})
        if not days:
            return None
        hh, mm = int(spec.get("hh", 0)), int(spec.get("mm", 0))
        # Search the next 8 days for the soonest matching weekday+time.
        for delta in range(0, 8):
            cand = _slot_today(after_dt + timedelta(days=delta), hh, mm)
            if cand.weekday() in days and cand.timestamp() > after:
                return cand.timestamp()
        return None

    return None


def describe(kind: str, spec: dict) -> str:
    """Human-readable summary of a schedule's timing."""
    if kind == "once":
        return "once at " + datetime.fromtimestamp(
            float(spec.get("run_at", 0))).strftime("%Y-%m-%d %H:%M")
    if kind == "interval":
        secs = int(spec.get("seconds", 0))
        if secs % 3600 == 0:
            return f"every {secs // 3600}h"
        if secs % 60 == 0:
            return f"every {secs // 60}m"
        return f"every {secs}s"
    if kind == "daily":
        return f"daily at {int(spec.get('hh', 0)):02d}:{int(spec.get('mm', 0)):02d}"
    if kind == "weekly":
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        days = ",".join(names[int(d) % 7] for d in sorted(spec.get("days", [])))
        return f"{days} at {int(spec.get('hh', 0)):02d}:{int(spec.get('mm', 0)):02d}"
    return kind


def _to_dict(row: dict) -> dict:
    """Translate a store schedules row into the pre-M1 shape (uid ->
    user_id, created_at -> created; enabled/backend_pin_notified forced
    to real bool since SQLite round-trips them as 0/1 ints and tests use
    strict `is True`/`is False` checks against these two fields)."""
    return {
        "id": row["id"],
        "user_id": row["uid"],
        "chat_id": row["chat_id"],
        "prompt": row.get("prompt"),
        "title": row.get("title"),
        "kind": row["kind"],
        "spec": row["spec"],
        "enabled": bool(row.get("enabled")),
        "created": row.get("created_at"),
        "last_run": row.get("last_run"),
        "next_run": row.get("next_run"),
        "session_name": row.get("session_name"),
        "session": row.get("session"),
        "payload_type": row.get("payload_type") or "message",
        "backend": row.get("backend"),
        "model": row.get("model"),
        "backend_pin_notified": bool(row.get("backend_pin_notified")),
        "fail_count": row.get("fail_count") or 0,
    }


class ScheduleManager:
    """Persistent store of automation jobs."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self._store = store.get_store(state_file)
        # Back-compat only: pre-M1 tests occasionally poke a schedule dict
        # in directly (schedules.schedules[sid] = s) to hand a one-off dict
        # straight to core._execute_schedule() without going through add().
        # Nothing in this class reads from it — the store is the only
        # source of truth for every real method above.
        self.schedules: dict = {}

    # ── CRUD ──────────────────────────────────────────────

    def add(self, user_id: int, chat_id: int, prompt: str, kind: str, spec: dict,
            title: str = "", session_name: str | None = None,
            session: str | None = None, payload_type: str = "message",
            backend: str | None = None, model: str | None = None,
            is_owner: bool = False, now: float | None = None) -> dict | None:
        if kind not in VALID_KINDS:
            return None
        if payload_type not in VALID_PAYLOAD_TYPES:
            return None
        if payload_type == "command" and not is_owner:
            # Unattended shell execution: creation is owner-only, enforced
            # here (not left to a UI-level comment).
            return None
        now = now if now is not None else time.time()
        next_run = compute_next_run(kind, spec, now)
        if next_run is None:
            return None  # nothing in the future (e.g. a one-off in the past)
        sid = uuid.uuid4().hex[:8]
        row = {
            "id": sid, "uid": user_id, "chat_id": chat_id,
            "prompt": prompt, "title": (title or prompt)[:60],
            "kind": kind, "spec": spec, "enabled": 1,
            "created_at": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "last_run": None, "next_run": next_run,
            # Legacy field, kept for back-compat reads of pre-Part-B schedules
            # (see resolve_session_mode). New code should use "session".
            "session_name": session_name,
            "session": session if session is not None else "isolated",
            "payload_type": payload_type,
            "backend": backend, "model": model,
            "backend_pin_notified": 0,
            "fail_count": 0,
        }
        self._store.schedules_insert(row)
        return _to_dict(row)

    def get(self, sid: str) -> dict | None:
        row = self._store.schedules_get(sid)
        return _to_dict(row) if row else None

    def count(self) -> int:
        """Total schedules across all users (health report)."""
        return len(self._store.schedules_all())

    def remove(self, sid: str, user_id: int) -> bool:
        row = self._store.schedules_get(sid)
        if not row or row["uid"] != user_id:
            return False
        return self._store.schedules_delete(sid, user_id)

    def set_enabled(self, sid: str, user_id: int, enabled: bool,
                    now: float | None = None) -> bool:
        row = self._store.schedules_get(sid)
        if not row or row["uid"] != user_id:
            return False
        fields = {"enabled": 1 if enabled else 0}
        if enabled and not row.get("next_run"):
            now = now if now is not None else time.time()
            fields["next_run"] = compute_next_run(row["kind"], row["spec"], now)
        self._store.schedules_update(sid, **fields)
        return True

    def list(self, user_id: int) -> list[dict]:
        rows = self._store.schedules_list(user_id)
        items = [_to_dict(r) for r in rows]
        items.sort(key=lambda s: (not s.get("enabled"), s.get("next_run") or 1e18))
        return items

    def touch_run(self, sid: str, now: float | None = None):
        """Mark a schedule as just-run and advance to its next occurrence."""
        row = self._store.schedules_get(sid)
        if not row:
            return
        now = now if now is not None else time.time()
        nxt = compute_next_run(row["kind"], row["spec"], now)
        if nxt is None:                 # one-off finished
            self._store.schedules_update(sid, last_run=now, enabled=0, next_run=None)
        else:
            self._store.schedules_update(sid, last_run=now, next_run=nxt)

    # ── Run outcome (self-healing: retry on failure, never silently skip) ──

    def mark_success(self, sid: str, now: float | None = None):
        """A run succeeded: clear the failure counter and advance normally."""
        row = self._store.schedules_get(sid)
        if row is not None:
            self._store.schedules_update(sid, fail_count=0)
        self.touch_run(sid, now)

    def mark_failure(self, sid: str, now: float | None = None) -> tuple[str, int]:
        """A run failed. Walks RETRY_LADDER. Returns (outcome, attempt):
          • ('retry', n)  — schedule a retry at now + RETRY_LADDER[n-1] while
                            attempts ≤ len(RETRY_LADDER).
          • ('gaveup', n) — exhausted the ladder for THIS occurrence; reset the
                            counter and advance to the next normal occurrence
                            (so a daily job that fails today still runs tomorrow —
                            giving up NEVER permanently disables a schedule,
                            except a one-off with no future occurrence).
          • ('gone', 0)   — schedule no longer exists.
        The schedule is NEVER silently dropped: it always has a future next_run
        (unless it's a finished one-off), so a missed/failed job recovers.
        """
        row = self._store.schedules_get(sid)
        if not row:
            return ("gone", 0)
        now = now if now is not None else time.time()
        attempt = int(row.get("fail_count") or 0) + 1
        if attempt <= len(RETRY_LADDER):
            self._store.schedules_update(
                sid, fail_count=attempt, last_run=now,
                next_run=now + RETRY_LADDER[attempt - 1],
            )
            return ("retry", attempt)
        # Ladder exhausted: reset and move to the next normal occurrence.
        nxt = compute_next_run(row["kind"], row["spec"], now)
        if nxt is None:                 # finished one-off that kept failing
            self._store.schedules_update(
                sid, fail_count=0, last_run=now, enabled=0, next_run=None,
            )
        else:
            self._store.schedules_update(sid, fail_count=0, last_run=now, next_run=nxt)
        return ("gaveup", attempt)

    def mark_backend_pin_notified(self, sid: str):
        """Record that the owner was told once about a backend/model pin
        drift (see backend_pin_mismatch) — suppresses further Alerts."""
        row = self._store.schedules_get(sid)
        if row is not None:
            self._store.schedules_update(sid, backend_pin_notified=1)

    # ── Runtime selection ─────────────────────────────────

    def due(self, now: float | None = None) -> list[dict]:
        now = now if now is not None else time.time()
        rows = self._store.schedules_all()
        return [_to_dict(r) for r in rows
                if r.get("enabled") and r.get("next_run") and r["next_run"] <= now]

    def next_due_at(self) -> float | None:
        """Earliest next_run among enabled schedules (None if none pending)."""
        rows = self._store.schedules_all()
        pending = [r["next_run"] for r in rows if r.get("enabled") and r.get("next_run")]
        return min(pending) if pending else None

    def reconcile_startup(self, now: float | None = None, catchup: bool = True):
        """
        At boot, decide what to do with schedules whose time passed while the
        bot was off. catchup=True: leave them due (they run once on the next
        tick). catchup=False: advance past now without running.
        """
        now = now if now is not None else time.time()
        if catchup:
            return  # due() will pick them up and run each once
        rows = self._store.schedules_all()
        for row in rows:
            if row.get("enabled") and row.get("next_run") and row["next_run"] <= now:
                nxt = compute_next_run(row["kind"], row["spec"], now)
                if nxt is None:
                    self._store.schedules_update(row["id"], enabled=0, next_run=None)
                else:
                    self._store.schedules_update(row["id"], next_run=nxt)
