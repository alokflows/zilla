# ============================================================
#  HEARTBEAT — the proactive beat loop (PLAN.md §6, Phase H1)
# ============================================================
#  Design (owner-confirmed): ONE agent-owned file, Memory/HEARTBEAT.md,
#  holds everything — briefings, watches, follow-ups, notes-to-self. The
#  agent reads it each beat, does what's due, edits the file itself.
#  Zilla's code stays dumb: it fires the beat on a `system` schedule
#  (core._run_and_record_system — the try-acquire/skip, no-retry-ladder
#  runner), injects time context into the prompt, and enforces quiet runs
#  (core._quiet_heartbeat_suppressed, already shared with M4's distillation).
#  This module owns only: the deterministic zero-AI-call skip check, the
#  per-fire prompt text, and idempotent schedule seeding.
# ============================================================

from __future__ import annotations

from datetime import datetime

from zilla.schedules import ScheduleManager, ensure_system_schedule

# Title is the match key ensure_system_schedule uses to find-or-create —
# must stay stable across releases (same convention as bot.py's
# DISTILLATION_TITLE).
HEARTBEAT_TITLE = "Heartbeat beat"

# Setting key (minutes). 0 = off. Read once at seed time — like M4's fixed
# 03:30 distillation slot, changing this after the schedule already exists
# does not retroactively reschedule it (ensure_system_schedule matches by
# title and returns the existing row unchanged); a future session can add
# live-reconfiguration if the owner ever needs it.
HEARTBEAT_INTERVAL_SETTING = "heartbeat_interval"
DEFAULT_HEARTBEAT_MINUTES = 30


def has_actionable_content(text: str) -> bool:
    """Deterministic pre-check (PLAN.md §6/H1 step 2): False if `text` is
    missing/empty or reduced to bare Markdown headers with nothing under
    them — genuinely nothing to check, so the beat must make ZERO AI calls.
    The seeded HEARTBEAT_TEMPLATE (zilla/memory.py) always has real content
    under '## Daily' (the morning-brief item), so a freshly seeded file is
    NOT skipped — only a missing/emptied-out file is."""
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return True
    return False


def should_skip(base: str | None = None) -> bool:
    """True if this beat should fire zero AI calls this tick — see
    has_actionable_content."""
    from zilla import memory
    return not has_actionable_content(memory.read_heartbeat(base))


def build_beat_prompt(now: datetime, last_run: float | None, tz_name: str,
                      flags: list[str] | None = None) -> str:
    """The exact per-fire beat prompt (PLAN.md §6/H1 step 3). `flags` are
    H2's already-DM'd health-probe lines (PLAN.md §6/H2 step 3) prepended
    so the agent doesn't duplicate an alert Zilla's own health loop
    already sent."""
    last = (
        datetime.fromtimestamp(last_run).strftime("%Y-%m-%d %H:%M")
        if last_run else "never"
    )
    prefix = "".join(f"{line}\n" for line in (flags or []))
    return (
        f"{prefix}It is {now.strftime('%Y-%m-%d %H:%M')} ({tz_name}). Last beat: {last}. "
        "Read HEARTBEAT.md. Do anything due; update the file (stamps, checkoffs, "
        "prune stale items). If nothing needs the owner, reply HEARTBEAT_OK."
    )


def prepare_beat(s: dict, base: str | None = None) -> dict | None:
    """Called from core._run_and_record_system for every system schedule,
    BEFORE it runs. Any schedule other than the heartbeat itself (M4's
    distillation, in particular) passes through unchanged. For the
    heartbeat: returns None if the deterministic pre-check says skip (zero
    AI calls this tick), otherwise a COPY of `s` with a freshly built
    per-fire prompt (time context Zilla's code injects, per the design
    note above) — never mutates the caller's dict."""
    if s.get("title") != HEARTBEAT_TITLE:
        return s
    if should_skip(base):
        return None
    now = datetime.now().astimezone()
    tz_name = now.tzname() or "local"
    from zilla import health as _health
    flags = _health.beat_flag_lines()
    s = dict(s)
    s["prompt"] = build_beat_prompt(now, s.get("last_run"), tz_name, flags=flags)
    return s


def ensure_heartbeat_schedule(schedules_mgr: ScheduleManager, owner_chat_id: int,
                              get_setting) -> None:
    """Idempotently seed the beat schedule for the owner (PLAN.md §6/H1
    step 2) — a no-op after the first successful start, same pattern as
    bot.py's ensure_distillation_schedule. heartbeat_interval=0 disables:
    if no schedule exists yet, none is created; if one already exists, it
    is paused (system schedules are never deletable, only pausable — see
    ScheduleManager.remove). A nonzero interval (re)enables it.

    `get_setting` is passed in (rather than imported) so this stays
    testable against an isolated settings store, matching the rest of this
    module's dependency-injection-via-parameter style for `base`."""
    minutes = get_setting(HEARTBEAT_INTERVAL_SETTING, DEFAULT_HEARTBEAT_MINUTES)
    existing = None
    for row in schedules_mgr.list(owner_chat_id):
        if row.get("system") and row.get("title") == HEARTBEAT_TITLE:
            existing = row
            break

    if not minutes or minutes <= 0:
        if existing and existing.get("enabled"):
            schedules_mgr.set_enabled(existing["id"], owner_chat_id, False)
        return

    if existing:
        if not existing.get("enabled"):
            schedules_mgr.set_enabled(existing["id"], owner_chat_id, True)
        return

    # A beat is periodic — a missed one (bot was off) is worthless, unlike
    # a missed nightly distillation. _catchup="skip" overrides the global
    # schedule_catchup setting for this row only (ScheduleManager.
    # reconcile_startup).
    ensure_system_schedule(
        schedules_mgr, owner_chat_id, HEARTBEAT_TITLE,
        # Placeholder prompt — prepare_beat() replaces it with a fresh,
        # time-stamped prompt on every actual fire.
        "Read HEARTBEAT.md and act on what's due.",
        "interval", {"seconds": int(minutes) * 60, "_catchup": "skip"},
    )
