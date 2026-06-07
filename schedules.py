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
# ============================================================

from __future__ import annotations

import json
import os
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

VALID_KINDS = ("once", "interval", "daily", "weekly")


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


class ScheduleManager:
    """Persistent store of automation jobs (one JSON file, atomic writes)."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self._lock = threading.Lock()
        self.schedules = self._load()

    def _load(self) -> dict:
        with self._lock:
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except (FileNotFoundError, json.JSONDecodeError):
                return {}

    def _save(self):
        with self._lock:
            try:
                tmp = f"{self.state_file}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.schedules, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.state_file)
            except Exception as e:
                logger.error(f"[SCHED] Save failed: {e}")

    # ── CRUD ──────────────────────────────────────────────

    def add(self, user_id: int, chat_id: int, prompt: str, kind: str, spec: dict,
            title: str = "", session_name: str | None = None,
            now: float | None = None) -> dict | None:
        if kind not in VALID_KINDS:
            return None
        now = now if now is not None else time.time()
        next_run = compute_next_run(kind, spec, now)
        if next_run is None:
            return None  # nothing in the future (e.g. a one-off in the past)
        sid = uuid.uuid4().hex[:8]
        sched = {
            "id": sid, "user_id": user_id, "chat_id": chat_id,
            "prompt": prompt, "title": (title or prompt)[:60],
            "kind": kind, "spec": spec, "enabled": True,
            "created": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "last_run": None, "next_run": next_run, "session_name": session_name,
        }
        self.schedules[sid] = sched
        self._save()
        return sched

    def get(self, sid: str) -> dict | None:
        return self.schedules.get(sid)

    def remove(self, sid: str, user_id: int) -> bool:
        s = self.schedules.get(sid)
        if not s or s.get("user_id") != user_id:
            return False
        del self.schedules[sid]
        self._save()
        return True

    def set_enabled(self, sid: str, user_id: int, enabled: bool,
                    now: float | None = None) -> bool:
        s = self.schedules.get(sid)
        if not s or s.get("user_id") != user_id:
            return False
        s["enabled"] = enabled
        if enabled and not s.get("next_run"):
            now = now if now is not None else time.time()
            s["next_run"] = compute_next_run(s["kind"], s["spec"], now)
        self._save()
        return True

    def list(self, user_id: int) -> list[dict]:
        items = [s for s in self.schedules.values() if s.get("user_id") == user_id]
        items.sort(key=lambda s: (not s.get("enabled"), s.get("next_run") or 1e18))
        return items

    def touch_run(self, sid: str, now: float | None = None):
        """Mark a schedule as just-run and advance to its next occurrence."""
        s = self.schedules.get(sid)
        if not s:
            return
        now = now if now is not None else time.time()
        s["last_run"] = now
        nxt = compute_next_run(s["kind"], s["spec"], now)
        if nxt is None:                 # one-off finished
            s["enabled"] = False
            s["next_run"] = None
        else:
            s["next_run"] = nxt
        self._save()

    # ── Run outcome (self-healing: retry on failure, never silently skip) ──

    def mark_success(self, sid: str, now: float | None = None):
        """A run succeeded: clear the failure counter and advance normally."""
        s = self.schedules.get(sid)
        if s is not None:
            s["fail_count"] = 0
        self.touch_run(sid, now)

    def mark_failure(self, sid: str, retry_delay: float, max_retries: int,
                     now: float | None = None) -> tuple[str, int]:
        """A run failed. Returns (outcome, attempt):
          • ('retry', n)  — schedule a soon retry (next_run = now + retry_delay)
                            while attempts ≤ max_retries.
          • ('gaveup', n) — exhausted retries for THIS occurrence; reset the
                            counter and advance to the next normal occurrence
                            (so a daily job that fails today still runs tomorrow).
          • ('gone', 0)   — schedule no longer exists.
        The schedule is NEVER silently dropped: it always has a future next_run
        (unless it's a finished one-off), so a missed/failed job recovers.
        """
        s = self.schedules.get(sid)
        if not s:
            return ("gone", 0)
        now = now if now is not None else time.time()
        attempt = int(s.get("fail_count", 0)) + 1
        s["fail_count"] = attempt
        s["last_run"] = now
        if attempt <= max_retries:
            s["next_run"] = now + retry_delay
            self._save()
            return ("retry", attempt)
        # Exhausted: reset and move to the next normal occurrence.
        s["fail_count"] = 0
        nxt = compute_next_run(s["kind"], s["spec"], now)
        if nxt is None:                 # finished one-off that kept failing
            s["enabled"] = False
            s["next_run"] = None
        else:
            s["next_run"] = nxt
        self._save()
        return ("gaveup", attempt)

    # ── Runtime selection ─────────────────────────────────

    def due(self, now: float | None = None) -> list[dict]:
        now = now if now is not None else time.time()
        return [s for s in self.schedules.values()
                if s.get("enabled") and s.get("next_run") and s["next_run"] <= now]

    def reconcile_startup(self, now: float | None = None, catchup: bool = True):
        """
        At boot, decide what to do with schedules whose time passed while the
        bot was off. catchup=True: leave them due (they run once on the next
        tick). catchup=False: advance past now without running.
        """
        now = now if now is not None else time.time()
        if catchup:
            return  # due() will pick them up and run each once
        changed = False
        for s in self.schedules.values():
            if s.get("enabled") and s.get("next_run") and s["next_run"] <= now:
                nxt = compute_next_run(s["kind"], s["spec"], now)
                if nxt is None:
                    s["enabled"] = False
                    s["next_run"] = None
                else:
                    s["next_run"] = nxt
                changed = True
        if changed:
            self._save()
