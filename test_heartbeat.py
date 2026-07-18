# ============================================================
#  TESTS — Phase H1: heartbeat beat loop (PLAN.md §6/H1 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/heartbeat.py: has_actionable_content/should_skip (the
#      zero-AI-call empty-file skip check), build_beat_prompt, prepare_beat
#      (passthrough for non-heartbeat schedules, skip, prompt injection),
#      ensure_heartbeat_schedule idempotency + 0=off pause/resume.
#    - zilla/memory.py: HEARTBEAT_TEMPLATE seeding (missing/empty -> seeded
#      exactly once; non-empty content, incl. the agent's own edits, is
#      never overwritten) and read_heartbeat().
#    - zilla/schedules.py: reconcile_startup's per-row _catchup="skip"
#      override for system=1 schedules (a beat), vs. a system schedule
#      without the override (distillation) keeping today's catchup=True
#      behavior.
#    - zilla/cli_engine.py: gc_orphaned_conv_dirs (referenced dirs kept
#      regardless of age; unreferenced dirs kept if young, removed if old).
#    - zilla/sessions.py + zilla/store.py: all_conversation_ids() /
#      sessions_all_conv_ids() — the GC's "still referenced" set.
#    - zilla/core.py: ZillaCore._run_and_record_system end-to-end — an
#      empty HEARTBEAT.md makes ZERO AI calls; an actionable one calls the
#      CLI with the injected time-stamped beat prompt; HEARTBEAT_OK is
#      suppressed (delivers nothing) while any other beat response
#      delivers normally — using the REAL heartbeat title/schedule, not a
#      generic system=1 fixture (test_memory_m3.py already covers the
#      generic quiet-run gate).
#    - zilla/harness.py: the H1 protocol line is present in the owner
#      memory-injection block.
#
#  Run:  python test_heartbeat.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every test points zilla.memory.MEMORY_DIR / zilla.config.DB_FILE at
#  throwaway tmpdirs (same pattern as test_memory_m3.py / test_memory_m4.py)
#  so a run never reads or writes the real repo's Memory/ tree, zilla.db,
#  or agy BRAIN_DIR.
# ============================================================

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


# ── Isolate config BEFORE anything touches the store ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_h1_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.memory as memory  # noqa: E402
import zilla.core as zcore  # noqa: E402
import zilla.cli_engine as cli_engine  # noqa: E402
import zilla.heartbeat as heartbeat  # noqa: E402
from zilla.core import ZillaCore, ScheduledResult, Alert  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402
from zilla.schedules import ScheduleManager, ensure_system_schedule  # noqa: E402
from zilla.harness import build_preamble, TurnContext  # noqa: E402

OWNER = 111


def _iso_mem_dir():
    tmp = tempfile.mkdtemp(prefix="zilla_h1_mem_")
    old = memory.MEMORY_DIR
    memory.MEMORY_DIR = os.path.join(tmp, "Memory")
    return tmp, old


def _fresh_core(tag: str, schedules=None) -> ZillaCore:
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.json"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.json"), OWNER)
    return ZillaCore(sessions=sessions, auth=auth, schedules=schedules)


class _patched:
    """Monkeypatch zilla.core's run_cli_async for one test."""

    def __init__(self, fake_run):
        self.fake_run = fake_run

    def __enter__(self):
        self._run = zcore.run_cli_async
        zcore.run_cli_async = self.fake_run
        return self

    def __exit__(self, *exc):
        zcore.run_cli_async = self._run
        return False


async def _drain(q: asyncio.Queue, n: int, timeout: float = 2.0):
    out = []

    async def go():
        while len(out) < n:
            out.append(await q.get())
    try:
        await asyncio.wait_for(go(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    return out


# ── 1. has_actionable_content / should_skip — the zero-AI-call gate ──

def test_has_actionable_content():
    print("\n[1] has_actionable_content() — deterministic pre-check")
    check("empty string -> no actionable content", heartbeat.has_actionable_content("") is False)
    check("whitespace only -> no actionable content",
          heartbeat.has_actionable_content("   \n\n  ") is False)
    check("headers only -> no actionable content",
          heartbeat.has_actionable_content("# Heartbeat\n## Daily\n## Watching\n") is False)
    check("the real seeded template HAS actionable content (morning-brief item)",
          heartbeat.has_actionable_content(memory.HEARTBEAT_TEMPLATE) is True)
    check("a bullet under a header IS actionable",
          heartbeat.has_actionable_content("## Watching\n- keep an eye on the invoice\n") is True)


def test_should_skip_reads_real_file():
    print("\n[2] should_skip() — reads Memory/HEARTBEAT.md via memory.read_heartbeat")
    tmp, old = _iso_mem_dir()
    try:
        check("missing file -> skip", heartbeat.should_skip() is True)
        memory.ensure_tree()
        check("freshly seeded template -> NOT skipped", heartbeat.should_skip() is False)
        hb_path = os.path.join(memory.MEMORY_DIR, "HEARTBEAT.md")
        with open(hb_path, "w", encoding="utf-8") as f:
            f.write("")
        check("emptied-out file -> skip", heartbeat.should_skip() is True)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 2. build_beat_prompt / prepare_beat ──

def test_build_beat_prompt_format():
    print("\n[3] build_beat_prompt() — exact PLAN.md §6/H1 step 3 text")
    now = datetime(2026, 7, 18, 9, 5)
    p_never = heartbeat.build_beat_prompt(now, None, "PDT")
    check("includes formatted 'now'", "2026-07-18 09:05" in p_never)
    check("includes tz name", "(PDT)" in p_never)
    check("last=never when no prior run", "Last beat: never." in p_never)
    check("instructs reading HEARTBEAT.md", "Read HEARTBEAT.md" in p_never)
    check("instructs the HEARTBEAT_OK ack", "reply HEARTBEAT_OK" in p_never)
    check("F4: teaches the OWNER_ALERT: escape hatch as the only way out",
          'OWNER_ALERT: <one calm sentence>' in p_never, p_never)

    last_ts = datetime(2026, 7, 18, 8, 35).timestamp()
    p_prior = heartbeat.build_beat_prompt(now, last_ts, "PDT")
    check("prior run timestamp is formatted into the prompt",
          "Last beat: 2026-07-18 08:35." in p_prior, p_prior)


def test_prepare_beat_passthrough_skip_and_inject():
    print("\n[4] prepare_beat() — passthrough / skip / prompt injection")
    tmp, old = _iso_mem_dir()
    try:
        other = {"id": "x1", "title": "Some other system job", "prompt": "unchanged"}
        out = heartbeat.prepare_beat(dict(other))
        check("non-heartbeat schedule passes through unchanged", out == other, out)

        # No HEARTBEAT.md yet at all -> deterministic skip.
        beat = {"id": "hb1", "title": heartbeat.HEARTBEAT_TITLE, "prompt": "placeholder"}
        check("missing HEARTBEAT.md -> prepare_beat returns None (skip)",
              heartbeat.prepare_beat(dict(beat)) is None)

        memory.ensure_tree()  # seeds the real template -> actionable
        prepared = heartbeat.prepare_beat(dict(beat))
        check("actionable HEARTBEAT.md -> prepare_beat returns a dict", prepared is not None)
        check("injected prompt differs from the placeholder",
              prepared["prompt"] != "placeholder", prepared)
        check("injected prompt still tells the agent to read HEARTBEAT.md",
              "Read HEARTBEAT.md" in prepared["prompt"], prepared)
        check("original dict is never mutated (copy, not in-place)",
              beat["prompt"] == "placeholder", beat)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 3. ensure_heartbeat_schedule — idempotent seeding + 0=off ──

def test_ensure_heartbeat_schedule_idempotent_and_toggle():
    print("\n[5] ensure_heartbeat_schedule() — idempotent across 'restarts', 0=off pause/resume")
    db_path = os.path.join(_tmpdir, "schedules_h1_seed.json")

    def get_setting_30(key, default=None):
        return 30

    mgr1 = ScheduleManager(db_path)
    heartbeat.ensure_heartbeat_schedule(mgr1, OWNER, get_setting_30)
    matches = [s for s in mgr1.list(OWNER, include_system=True)
               if s.get("system") and s.get("title") == heartbeat.HEARTBEAT_TITLE]
    check("first call creates exactly one heartbeat schedule", len(matches) == 1, matches)
    sid = matches[0]["id"]
    check("seeded as a system job", matches[0]["system"] is True)
    check("interval kind with 30*60 second spec",
          matches[0]["kind"] == "interval" and matches[0]["spec"].get("seconds") == 1800,
          matches[0])
    check("catch-up policy is 'skip' (a missed beat is worthless)",
          matches[0]["spec"].get("_catchup") == "skip", matches[0])

    # Simulate two more "restarts" against the same persisted store.
    mgr2 = ScheduleManager(db_path)
    heartbeat.ensure_heartbeat_schedule(mgr2, OWNER, get_setting_30)
    mgr3 = ScheduleManager(db_path)
    heartbeat.ensure_heartbeat_schedule(mgr3, OWNER, get_setting_30)
    matches3 = [s for s in mgr3.list(OWNER, include_system=True)
                if s.get("system") and s.get("title") == heartbeat.HEARTBEAT_TITLE]
    check("still exactly one after two more restarts", len(matches3) == 1, matches3)
    check("same schedule id preserved", matches3[0]["id"] == sid)

    # heartbeat_interval=0 -> pauses the existing schedule, creates nothing new.
    def get_setting_0(key, default=None):
        return 0

    mgr4 = ScheduleManager(db_path)
    heartbeat.ensure_heartbeat_schedule(mgr4, OWNER, get_setting_0)
    row = mgr4.get(sid)
    check("interval=0 pauses (never deletes) the existing schedule",
          row is not None and row["enabled"] is False, row)

    # Re-enabling with a nonzero interval flips it back on, same id.
    mgr5 = ScheduleManager(db_path)
    heartbeat.ensure_heartbeat_schedule(mgr5, OWNER, get_setting_30)
    row5 = mgr5.get(sid)
    check("nonzero interval re-enables the same schedule",
          row5["id"] == sid and row5["enabled"] is True, row5)


def test_ensure_heartbeat_schedule_zero_from_the_start_creates_nothing():
    print("\n[5b] ensure_heartbeat_schedule() — interval=0 from a clean store creates nothing")
    db_path = os.path.join(_tmpdir, "schedules_h1_zero.json")

    def get_setting_0(key, default=None):
        return 0

    mgr = ScheduleManager(db_path)
    heartbeat.ensure_heartbeat_schedule(mgr, OWNER, get_setting_0)
    matches = [s for s in mgr.list(OWNER)
               if s.get("system") and s.get("title") == heartbeat.HEARTBEAT_TITLE]
    check("no schedule created when interval is 0 from the start", matches == [], matches)


# ── 4. memory.py — HEARTBEAT_TEMPLATE seeding, never clobbers ──

def test_heartbeat_seeding_never_clobbers():
    print("\n[6] memory.ensure_tree() — seeds HEARTBEAT.md once, never overwrites real content")
    tmp, old = _iso_mem_dir()
    try:
        hb_path = os.path.join(memory.MEMORY_DIR, "HEARTBEAT.md")
        check("does not exist yet", not os.path.exists(hb_path))
        memory.ensure_tree()
        check("seeded on first ensure_tree()", os.path.exists(hb_path))
        seeded = memory.read_heartbeat()
        check("seeded content matches HEARTBEAT_TEMPLATE", seeded == memory.HEARTBEAT_TEMPLATE)

        # Simulate the agent editing the file.
        edited = seeded + "\n- 09:00 called the owner about the invoice (done)\n"
        with open(hb_path, "w", encoding="utf-8") as f:
            f.write(edited)
        memory.ensure_tree()  # a second call must never clobber the edit
        check("agent's edit survives a second ensure_tree() call",
              memory.read_heartbeat() == edited)

        # An emptied-out file is treated like "missing" and re-seeded once.
        with open(hb_path, "w", encoding="utf-8") as f:
            f.write("")
        memory.ensure_tree()
        check("an emptied file gets re-seeded with the template",
              memory.read_heartbeat() == memory.HEARTBEAT_TEMPLATE)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_read_heartbeat_missing_is_empty():
    print("\n[6b] memory.read_heartbeat() — '' when the file/dir doesn't exist")
    tmp, old = _iso_mem_dir()
    try:
        check("missing Memory/ dir entirely -> ''", memory.read_heartbeat() == "")
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 5. schedules.py — reconcile_startup per-row _catchup override ──

def test_reconcile_startup_catchup_skip_override():
    print("\n[7] reconcile_startup() — system schedule's _catchup='skip' overrides global catchup=True")
    db_path = os.path.join(_tmpdir, "schedules_h1_catchup.json")
    mgr = ScheduleManager(db_path)

    now = time.time()
    past = now - 3600  # one hour overdue

    beat = ensure_system_schedule(mgr, OWNER, "Heartbeat beat", "placeholder",
                                  "interval", {"seconds": 60, "_catchup": "skip"})
    distill = ensure_system_schedule(mgr, OWNER, "Nightly memory distillation", "placeholder",
                                     "daily", {"hh": 3, "mm": 30})
    # Force both overdue, as if the process was off for an hour.
    mgr._store.schedules_update(beat["id"], next_run=past)
    mgr._store.schedules_update(distill["id"], next_run=past)

    mgr.reconcile_startup(now=now, catchup=True)

    beat_after = mgr.get(beat["id"])
    distill_after = mgr.get(distill["id"])
    check("beat (_catchup=skip) is advanced PAST now, not left due",
          beat_after["next_run"] > now, beat_after)
    check("distillation (no override) is LEFT due under global catchup=True — unchanged behavior",
          distill_after["next_run"] == past, distill_after)


def test_reconcile_startup_user_schedule_always_follows_global():
    print("\n[7b] reconcile_startup() — a user (system=0) schedule ignores _catchup entirely")
    db_path = os.path.join(_tmpdir, "schedules_h1_catchup_user.json")
    mgr = ScheduleManager(db_path)
    now = time.time()
    past = now - 3600

    s = mgr.add(OWNER, OWNER, "my reminder", "interval", {"seconds": 60, "_catchup": "skip"},
               title="Not a system job")
    check("fixture is a user schedule", s["system"] is False)
    mgr._store.schedules_update(s["id"], next_run=past)

    mgr.reconcile_startup(now=now, catchup=False)
    after = mgr.get(s["id"])
    check("a user schedule's own '_catchup' key is ignored; global catchup=False advances it",
          after["next_run"] > now, after)


# ── 6. cli_engine.py — gc_orphaned_conv_dirs ──

def test_gc_orphaned_conv_dirs():
    print("\n[8] gc_orphaned_conv_dirs() — referenced kept, old+unreferenced removed, young kept")
    tmp = tempfile.mkdtemp(prefix="zilla_h1_brain_")
    old_brain = cli_engine.BRAIN_DIR
    cli_engine.BRAIN_DIR = tmp
    try:
        old_day = 10 * 86400
        young = 1 * 86400

        def mk(name, age_seconds):
            d = os.path.join(tmp, name)
            os.makedirs(d)
            ts = time.time() - age_seconds
            os.utime(d, (ts, ts))

        mk("referenced-old", old_day)
        mk("referenced-young", young)
        mk("orphan-old", old_day)
        mk("orphan-young", young)

        referenced = {"referenced-old", "referenced-young"}
        removed = cli_engine.gc_orphaned_conv_dirs(referenced, max_age_days=7)

        check("removed exactly one dir (orphan-old)", removed == 1, removed)
        check("referenced-old survives (still referenced, despite age)",
              os.path.isdir(os.path.join(tmp, "referenced-old")))
        check("referenced-young survives", os.path.isdir(os.path.join(tmp, "referenced-young")))
        check("orphan-old is gone", not os.path.isdir(os.path.join(tmp, "orphan-old")))
        check("orphan-young survives (unreferenced but too young)",
              os.path.isdir(os.path.join(tmp, "orphan-young")))
    finally:
        cli_engine.BRAIN_DIR = old_brain
        shutil.rmtree(tmp, ignore_errors=True)


def test_gc_orphaned_conv_dirs_missing_brain_dir_is_safe_noop():
    print("\n[8b] gc_orphaned_conv_dirs() — missing BRAIN_DIR is a safe no-op")
    old_brain = cli_engine.BRAIN_DIR
    cli_engine.BRAIN_DIR = os.path.join(tempfile.gettempdir(), "zilla_h1_never_exists_xyz")
    try:
        check("returns 0, never raises", cli_engine.gc_orphaned_conv_dirs(set()) == 0)
    finally:
        cli_engine.BRAIN_DIR = old_brain


# ── 7. sessions.py / store.py — all_conversation_ids ──

def test_all_conversation_ids():
    print("\n[9] SessionManager.all_conversation_ids() — every non-null conv_id, all users")
    sessions = SessionManager(os.path.join(_tmpdir, "sessions_h1_allconv.json"))
    check("empty store -> empty set", sessions.all_conversation_ids() == set())
    sessions.set_conversation_id("conv-a", OWNER, session_name="main")
    sessions.set_conversation_id("conv-b", OWNER, session_name="scratch")
    sessions.set_conversation_id("conv-c", 999, session_name="main")
    ids = sessions.all_conversation_ids()
    check("collects conv ids across sessions and users",
          ids == {"conv-a", "conv-b", "conv-c"}, ids)


# ── 8. core.py — _run_and_record_system, real heartbeat title, end-to-end ──

def test_run_and_record_system_skips_when_heartbeat_empty():
    print("\n[10] _run_and_record_system — empty HEARTBEAT.md -> ZERO AI calls")
    tmp, old = _iso_mem_dir()
    try:
        sessions = SessionManager(os.path.join(_tmpdir, "sessions_h1_skip.json"))
        auth = AuthManager(os.path.join(_tmpdir, "users_h1_skip.json"), OWNER)
        schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_h1_skip.json"))
        core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
        sink = asyncio.Queue()
        core.subscribe(sink)

        calls = []

        async def fake_run(prompt, conv_id, progress_callback=None,
                           cancel_event=None, skip_permissions=False, ctx=None):
            calls.append(prompt)
            return "should never run", "conv-x"

        s = schedules.add(OWNER, OWNER, "placeholder", "interval",
                          {"seconds": 1800, "_catchup": "skip"},
                          title=heartbeat.HEARTBEAT_TITLE, system=True)
        # No memory.ensure_tree() call -> HEARTBEAT.md does not exist -> skip.

        async def run():
            with _patched(fake_run):
                await core._run_and_record(s)
            return await _drain(sink, 1, timeout=0.5)

        events = asyncio.run(run())
        check("the CLI backend was never invoked", calls == [], calls)
        check("nothing broadcast", events == [], events)
        check("schedule still marked as run (advances to next tick)",
              schedules.get(s["id"])["last_run"] is not None)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_and_record_system_fires_with_injected_prompt_and_stays_silent():
    print("\n[11] _run_and_record_system — actionable file -> injected prompt, "
         "non-OWNER_ALERT response stays silent (F4: no more heartbeat noise)")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()  # real template -> actionable
        sessions = SessionManager(os.path.join(_tmpdir, "sessions_h1_fire.json"))
        auth = AuthManager(os.path.join(_tmpdir, "users_h1_fire.json"), OWNER)
        schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_h1_fire.json"))
        core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
        sink = asyncio.Queue()
        core.subscribe(sink)

        seen_prompts = []

        async def fake_run(prompt, conv_id, progress_callback=None,
                           cancel_event=None, skip_permissions=False, ctx=None):
            seen_prompts.append(prompt)
            return "called the owner about the invoice, done", "conv-y"

        s = schedules.add(OWNER, OWNER, "placeholder prompt never sent as-is", "interval",
                          {"seconds": 1800, "_catchup": "skip"},
                          title=heartbeat.HEARTBEAT_TITLE, system=True)

        async def run():
            with _patched(fake_run):
                await core._run_and_record(s)
            return await _drain(sink, 1, timeout=0.5)

        events = asyncio.run(run())
        check("the CLI WAS invoked (file had actionable content)", len(seen_prompts) == 1)
        check("the placeholder prompt was replaced with the real beat prompt",
              seen_prompts and seen_prompts[0] != "placeholder prompt never sent as-is",
              seen_prompts)
        check("beat prompt tells the agent to read HEARTBEAT.md",
              seen_prompts and "Read HEARTBEAT.md" in seen_prompts[0], seen_prompts)
        check("beat prompt teaches the OWNER_ALERT: escape hatch",
              seen_prompts and "OWNER_ALERT:" in seen_prompts[0], seen_prompts)
        check("F4: a plain non-OK response with no OWNER_ALERT: line delivers NOTHING "
              "(no more '⏰ Scheduled — Heartbeat beat' noise)", events == [], events)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_and_record_system_owner_alert_line_delivers_as_alert():
    print("\n[11b] _run_and_record_system — OWNER_ALERT: line -> one calm Alert, "
         "rest of the response never leaves the log")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        sessions = SessionManager(os.path.join(_tmpdir, "sessions_h1_alert.json"))
        auth = AuthManager(os.path.join(_tmpdir, "users_h1_alert.json"), OWNER)
        schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_h1_alert.json"))
        core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
        sink = asyncio.Queue()
        core.subscribe(sink)

        async def fake_run(prompt, conv_id, progress_callback=None,
                           cancel_event=None, skip_permissions=False, ctx=None):
            return ("checked the invoice thread, nothing new\n"
                    "OWNER_ALERT: the electricity bill is 3x normal this month\n"
                    "updated HEARTBEAT.md"), "conv-y2"

        s = schedules.add(OWNER, OWNER, "placeholder", "interval",
                          {"seconds": 1800, "_catchup": "skip"},
                          title=heartbeat.HEARTBEAT_TITLE, system=True)

        async def run():
            with _patched(fake_run):
                await core._run_and_record(s)
            return await _drain(sink, 1, timeout=0.5)

        events = asyncio.run(run())
        check("exactly one event reached the owner", len(events) == 1, events)
        check("it's an Alert, not a ScheduledResult (no '⏰ Scheduled —' header)",
              events and isinstance(events[0], Alert), events)
        check("it carries ONLY the OWNER_ALERT line, not the whole response",
              events and events[0].text == "the electricity bill is 3x normal this month",
              events)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_and_record_system_owner_alert_is_cooldown_gated():
    print("\n[11c] _run_and_record_system — a repeated OWNER_ALERT: for the same "
         "schedule only DMs once per cooldown window")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        from zilla import health as health_mod
        health_mod.reset_cache()
        sessions = SessionManager(os.path.join(_tmpdir, "sessions_h1_cd.json"))
        auth = AuthManager(os.path.join(_tmpdir, "users_h1_cd.json"), OWNER)
        schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_h1_cd.json"))
        core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
        sink = asyncio.Queue()
        core.subscribe(sink)

        async def fake_run(prompt, conv_id, progress_callback=None,
                           cancel_event=None, skip_permissions=False, ctx=None):
            return "OWNER_ALERT: same thing again", "conv-y3"

        s = schedules.add(OWNER, OWNER, "placeholder", "interval",
                          {"seconds": 1800, "_catchup": "skip"},
                          title=heartbeat.HEARTBEAT_TITLE, system=True)

        async def run_twice():
            with _patched(fake_run):
                await core._run_and_record(s)
                await core._run_and_record(s)
            return await _drain(sink, 2, timeout=0.5)

        events = asyncio.run(run_twice())
        check("only the FIRST fire's alert made it through the cooldown",
              len(events) == 1, events)
    finally:
        health_mod.reset_cache()
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_and_record_system_silence_is_not_title_specific():
    print("\n[11d] _run_and_record_system — the silence contract applies to ANY "
         "system=1 job, not just the heartbeat (a future 'snapshot' job etc.)")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        sessions = SessionManager(os.path.join(_tmpdir, "sessions_h1_generic.json"))
        auth = AuthManager(os.path.join(_tmpdir, "users_h1_generic.json"), OWNER)
        schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_h1_generic.json"))
        core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
        sink = asyncio.Queue()
        core.subscribe(sink)

        async def fake_run(prompt, conv_id, progress_callback=None,
                           cancel_event=None, skip_permissions=False, ctx=None):
            return "did some routine internal upkeep, nothing owner-facing", conv_id

        # Deliberately NOT heartbeat.HEARTBEAT_TITLE — prepare_beat() passes
        # any other title through unchanged, so this exercises the plain
        # message-payload path a hypothetical future system job would use.
        s = schedules.add(OWNER, OWNER, "do the routine thing", "interval",
                          {"seconds": 3600}, title="Some future system job", system=True)

        async def run():
            with _patched(fake_run):
                await core._run_and_record(s)
            return await _drain(sink, 1, timeout=0.5)

        events = asyncio.run(run())
        check("no ScheduledResult (or anything else) for a non-heartbeat "
              "system job's plain output", events == [], events)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_and_record_system_suppresses_heartbeat_ok():
    print("\n[12] _run_and_record_system — HEARTBEAT_OK response delivers nothing")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        sessions = SessionManager(os.path.join(_tmpdir, "sessions_h1_ok.json"))
        auth = AuthManager(os.path.join(_tmpdir, "users_h1_ok.json"), OWNER)
        schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_h1_ok.json"))
        core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
        sink = asyncio.Queue()
        core.subscribe(sink)

        async def fake_run(prompt, conv_id, progress_callback=None,
                           cancel_event=None, skip_permissions=False, ctx=None):
            return "HEARTBEAT_OK", "conv-z"

        s = schedules.add(OWNER, OWNER, "placeholder", "interval",
                          {"seconds": 1800, "_catchup": "skip"},
                          title=heartbeat.HEARTBEAT_TITLE, system=True)

        async def run():
            with _patched(fake_run):
                await core._run_and_record(s)
            return await _drain(sink, 1, timeout=0.5)

        events = asyncio.run(run())
        check("HEARTBEAT_OK delivers nothing", events == [], events)
        check("still counted as a successful tick",
              schedules.get(s["id"])["fail_count"] == 0)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_and_record_system_busy_lock_skips_without_running():
    print("\n[13] _run_and_record_system — per-uid lock held -> try-acquire skip, no blocking wait")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        sessions = SessionManager(os.path.join(_tmpdir, "sessions_h1_busy.json"))
        auth = AuthManager(os.path.join(_tmpdir, "users_h1_busy.json"), OWNER)
        schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_h1_busy.json"))
        core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
        sink = asyncio.Queue()
        core.subscribe(sink)

        calls = []

        async def fake_run(prompt, conv_id, progress_callback=None,
                           cancel_event=None, skip_permissions=False, ctx=None):
            calls.append(prompt)
            return "should not run while busy", "conv-busy"

        s = schedules.add(OWNER, OWNER, "placeholder", "interval",
                          {"seconds": 1800, "_catchup": "skip"},
                          title=heartbeat.HEARTBEAT_TITLE, system=True)

        async def run():
            async with core.get_user_lock(OWNER):
                with _patched(fake_run):
                    await core._run_and_record(s)
            return await _drain(sink, 1, timeout=0.5)

        events = asyncio.run(run())
        check("CLI never invoked while the owner's lock was held", calls == [], calls)
        check("nothing broadcast", events == [], events)
        check("schedule still marked as run (tick advances, no queueing/blocking)",
              schedules.get(s["id"])["last_run"] is not None)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 9. harness.py — H1 protocol line present for the owner ──

def test_harness_has_heartbeat_protocol_line():
    print("\n[14] harness.build_preamble — H1 protocol line reaches the owner's memory block")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        ctx = TurnContext(uid=OWNER, role="owner", is_owner=True, origin="chat")
        preamble = build_preamble(is_new=False, ctx=ctx)
        check("owner preamble tells the agent to add recurring asks to HEARTBEAT.md",
              "add it to HEARTBEAT.md" in preamble, preamble[:2000])
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    tests = [
        test_has_actionable_content,
        test_should_skip_reads_real_file,
        test_build_beat_prompt_format,
        test_prepare_beat_passthrough_skip_and_inject,
        test_ensure_heartbeat_schedule_idempotent_and_toggle,
        test_ensure_heartbeat_schedule_zero_from_the_start_creates_nothing,
        test_heartbeat_seeding_never_clobbers,
        test_read_heartbeat_missing_is_empty,
        test_reconcile_startup_catchup_skip_override,
        test_reconcile_startup_user_schedule_always_follows_global,
        test_gc_orphaned_conv_dirs,
        test_gc_orphaned_conv_dirs_missing_brain_dir_is_safe_noop,
        test_all_conversation_ids,
        test_run_and_record_system_skips_when_heartbeat_empty,
        test_run_and_record_system_fires_with_injected_prompt_and_stays_silent,
        test_run_and_record_system_owner_alert_line_delivers_as_alert,
        test_run_and_record_system_owner_alert_is_cooldown_gated,
        test_run_and_record_system_silence_is_not_title_specific,
        test_run_and_record_system_suppresses_heartbeat_ok,
        test_run_and_record_system_busy_lock_skips_without_running,
        test_harness_has_heartbeat_protocol_line,
    ]
    for t in tests:
        t()

    print(f"\n{_passed} passed, {_failed} failed")
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
