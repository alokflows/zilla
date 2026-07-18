# ============================================================
#  TESTS — Phase M3: FTS5 search + memory git + quiet runs
#  (PLAN.md §5.M3 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/memory.py: reindex() (build + invalidation), search()
#      (finds a planted fact with the correct path:line), and
#      git_autocommit() (fires on change, no-op on no-change, never
#      raises even when the git subprocess itself fails).
#    - zilla/core.py: ZillaCore's memory_autocommit_enabled gate (a
#      turn still delivers its Response even when the underlying git
#      call fails), and the M3.4 quiet-run mechanism scoped to
#      system=1 schedules only, including the negative case (a user
#      schedule whose own output ends with the token is never
#      suppressed).
#
#  Run:  python test_memory_m3.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every test points zilla.memory.MEMORY_DIR at a throwaway tmpdir and
#  zilla.config.DB_FILE at a throwaway sqlite file (same pattern
#  test_harness.py / test_core.py / test_schedules_seam.py use) so a
#  run never reads or writes the real repo Memory/ tree or zilla.db.
# ============================================================

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

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
_tmpdir = tempfile.mkdtemp(prefix="zilla_m3_cfg_")
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
from zilla.core import ZillaCore, Response, _quiet_heartbeat_suppressed, ScheduledResult  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402
from zilla.schedules import ScheduleManager  # noqa: E402

OWNER = 111


def _iso_mem_dir():
    """A fresh throwaway Memory/ dir, and the previous memory.MEMORY_DIR to
    restore (tests must not leak isolation state into each other)."""
    tmp = tempfile.mkdtemp(prefix="zilla_m3_mem_")
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


async def _collect(core, uid, text, **kw):
    events = []
    async for ev in core.handle_message(uid, text, **kw):
        events.append(ev)
    return events


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


# ── 1. reindex() — build, no-op on unchanged, invalidation on delete ──

def test_reindex_build_and_invalidation():
    print("\n[1] memory.reindex() — build, no-op when unchanged, invalidates deletions")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        with open(os.path.join(memory.MEMORY_DIR, "Wiki", "People", "friend.md"),
                  "w", encoding="utf-8") as f:
            f.write("# Friend\nSummary: a test page.\n\nNothing else here.\n")

        touched1 = memory.reindex()
        check("first reindex touches at least the new page", touched1 >= 1, touched1)

        touched2 = memory.reindex()
        check("second reindex (nothing changed) touches nothing", touched2 == 0, touched2)

        results = memory.search("friend")
        check("planted page shows up in search pre-deletion", len(results) >= 1, results)

        os.remove(os.path.join(memory.MEMORY_DIR, "Wiki", "People", "friend.md"))
        memory.reindex()
        results_after = memory.search("friend")
        check("deleted page no longer indexed after reindex",
              all(r[0] != "Wiki/People/friend.md" for r in results_after), results_after)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 2. search() — finds a planted fact with the correct path:line ──

def test_search_finds_planted_fact_with_correct_line():
    print("\n[2] memory.search() — planted fact resolves to the correct path:line")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        page = os.path.join(memory.MEMORY_DIR, "Wiki", "Preferences", "coffee.md")
        # Line 1 = title, line 2 = summary, line 3 = blank, line 4 = the fact.
        with open(page, "w", encoding="utf-8") as f:
            f.write("# Coffee\nSummary: brewing preferences.\n\nAlok takes his espresso with oat milk.\n")

        results = memory.search("espresso")
        check("exactly one match for a rare word", len(results) == 1, results)
        path, line, snippet = results[0]
        check("path is the planted page", path == "Wiki/Preferences/coffee.md", path)
        check("line number matches the actual line the fact is on", line == 4, line)
        check("snippet contains the fact", "espresso" in snippet, snippet)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_search_no_results_is_empty_list():
    print("\n[2b] memory.search() — no match -> [] (memsearch.py prints 'no results')")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        check("nonsense query -> no results", memory.search("zzqxnotarealword") == [])
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 3. git_autocommit() — fires on change, no-op on no-change ──

def test_git_autocommit_fires_on_change_not_on_no_change():
    print("\n[3] memory.git_autocommit() — commits on change, no-op when clean")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        committed1 = memory.git_autocommit("first commit")
        check("first call (fresh tree) commits", committed1 is True)
        check(".git dir created", os.path.isdir(os.path.join(memory.MEMORY_DIR, ".git")))
        check(".git dir locked to 0700",
              (os.stat(os.path.join(memory.MEMORY_DIR, ".git")).st_mode & 0o777) == 0o700)

        committed2 = memory.git_autocommit("nothing changed")
        check("second call with no changes is a no-op", committed2 is False)

        with open(os.path.join(memory.MEMORY_DIR, "MEMORY.md"), "a", encoding="utf-8") as f:
            f.write("Alok likes tea.\n")
        committed3 = memory.git_autocommit("second commit")
        check("third call (real edit) commits again", committed3 is True)

        log = subprocess.run(["git", "log", "--oneline"], cwd=memory.MEMORY_DIR,
                             capture_output=True, text=True, timeout=10)
        check("two commits in history", len(log.stdout.strip().splitlines()) == 2, log.stdout)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_git_autocommit_failure_never_raises():
    print("\n[4] memory.git_autocommit() — a failing git subprocess is swallowed, not raised")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        real_run = subprocess.run

        def boom(*a, **kw):
            raise OSError("git binary not found (simulated)")

        subprocess.run = boom
        try:
            result = memory.git_autocommit("should not raise")
            check("git_autocommit swallows the failure and returns False", result is False)
        except Exception as e:
            check("git_autocommit swallows the failure and returns False", False, repr(e))
        finally:
            subprocess.run = real_run
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 5. ZillaCore turn still delivers a reply when autocommit fails ──

def test_turn_still_delivers_when_git_fails():
    print("\n[5] handle_message — Response still delivered even if git_autocommit fails")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        core = _fresh_core("git_fail")
        core.memory_autocommit_enabled = True

        # Inject the failure where a real one would occur — inside the git
        # subprocess call itself — so this exercises memory.git_autocommit's
        # OWN try/except (already proven in [4]) end-to-end through a real
        # ZillaCore turn, rather than bypassing it by replacing the function.
        real_run = subprocess.run

        def boom(*a, **kw):
            raise OSError("git binary not found (simulated)")

        async def fake_run(prompt, conv_id, progress_callback=None,
                           cancel_event=None, skip_permissions=False, ctx=None):
            return "answer despite git trouble", "conv-gitfail"

        async def run():
            with _patched(fake_run):
                # NOT a share/smalltalk-shaped message — must reach the full route.
                return await _collect(core, OWNER, "what's on my calendar today?")

        subprocess.run = boom
        try:
            events = asyncio.run(run())
            responses = [e for e in events if isinstance(e, Response)]
            check("exactly one Response despite the git failure", len(responses) == 1,
                  f"{events}")
            check("response text intact",
                  responses and responses[0].text == "answer despite git trouble",
                  responses[0].text if responses else None)
        finally:
            subprocess.run = real_run
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_autocommit_disabled_by_default():
    print("\n[5b] ZillaCore — memory_autocommit_enabled defaults False (no git spawned)")
    core = _fresh_core("default_flag")
    check("autocommit off by default", core.memory_autocommit_enabled is False)


# ── 6. Quiet-run suppression — system=1 only, incl. negative case ──

def test_quiet_heartbeat_suppressed_pure():
    print("\n[6] _quiet_heartbeat_suppressed() — pure-logic gate cases")
    check("system schedule + exact token -> suppressed",
          _quiet_heartbeat_suppressed({"system": True}, "HEARTBEAT_OK") is True)
    check("system schedule + token case-insensitive -> suppressed",
          _quiet_heartbeat_suppressed({"system": True}, "heartbeat_ok") is True)
    check("system schedule + token as last line of multi-line response -> suppressed",
          _quiet_heartbeat_suppressed({"system": True}, "did the thing\nHEARTBEAT_OK") is True)
    check("system schedule + trailing whitespace/newline -> still suppressed",
          _quiet_heartbeat_suppressed({"system": True}, "HEARTBEAT_OK\n\n") is True)
    check("system schedule + token NOT on the last line -> not suppressed",
          _quiet_heartbeat_suppressed({"system": True}, "HEARTBEAT_OK\nmore output") is False)
    check("system schedule + unrelated response -> not suppressed",
          _quiet_heartbeat_suppressed({"system": True}, "all good, nothing to report") is False)
    check("NEGATIVE CASE: user (non-system) schedule with the exact token -> NOT suppressed",
          _quiet_heartbeat_suppressed({"system": False}, "HEARTBEAT_OK") is False)
    check("NEGATIVE CASE: schedule missing the 'system' key -> NOT suppressed",
          _quiet_heartbeat_suppressed({}, "HEARTBEAT_OK") is False)


def test_run_and_record_suppresses_system_schedule():
    print("\n[7] core._run_and_record — system=1 schedule + HEARTBEAT_OK delivers nothing")
    sessions = SessionManager(os.path.join(_tmpdir, "sessions_quiet_sys.json"))
    auth = AuthManager(os.path.join(_tmpdir, "users_quiet_sys.json"), OWNER)
    schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_quiet_sys.json"))
    core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
    sink = asyncio.Queue()
    core.subscribe(sink)

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False, ctx=None):
        return "did the nightly thing\nHEARTBEAT_OK", "conv-quiet"

    s = schedules.add(OWNER, OWNER, "heartbeat prompt", "interval", {"seconds": 60},
                      title="Heartbeat", system=True)
    check("schedule persisted as system=1 (fixture sanity)", s["system"] is True)

    async def run():
        with _patched(fake_run):
            await core._run_and_record(s)
        return await _drain(sink, 1, timeout=0.5)

    events = asyncio.run(run())
    check("nothing broadcast for a suppressed system run", events == [], f"{events}")
    check("schedule still marked successful (tick counted, just quiet)",
          schedules.get(s["id"])["fail_count"] == 0)


def test_run_and_record_negative_case_user_schedule_still_delivers():
    print("\n[7b] NEGATIVE CASE — user (system=0) schedule with the token still delivers")
    sessions = SessionManager(os.path.join(_tmpdir, "sessions_quiet_user.json"))
    auth = AuthManager(os.path.join(_tmpdir, "users_quiet_user.json"), OWNER)
    schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_quiet_user.json"))
    core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
    sink = asyncio.Queue()
    core.subscribe(sink)

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False, ctx=None):
        # A user's own legitimate output that happens to end with the exact
        # token the quiet-run mechanism looks for.
        return "final status: HEARTBEAT_OK", "conv-userquiet"

    s = schedules.add(OWNER, OWNER, "my own job", "interval", {"seconds": 60},
                      title="My Job")  # system defaults False
    check("schedule persisted as system=0 (fixture sanity)", s["system"] is False)

    async def run():
        with _patched(fake_run):
            await core._run_and_record(s)
        return await _drain(sink, 1)

    events = asyncio.run(run())
    check("user schedule's output IS delivered despite the token", len(events) == 1, f"{events}")
    check("delivered event carries the real response",
          events and isinstance(events[0], ScheduledResult)
          and events[0].response == "final status: HEARTBEAT_OK",
          events)


def test_run_schedule_now_also_suppresses_system_schedule():
    print("\n[8] core.run_schedule_now — manual trigger honors the same quiet-run gate")
    sessions = SessionManager(os.path.join(_tmpdir, "sessions_quiet_now.json"))
    auth = AuthManager(os.path.join(_tmpdir, "users_quiet_now.json"), OWNER)
    schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_quiet_now.json"))
    core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
    sink = asyncio.Queue()
    core.subscribe(sink)

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False, ctx=None):
        return "HEARTBEAT_OK", "conv-manual-quiet"

    s = schedules.add(OWNER, OWNER, "heartbeat prompt", "daily", {"hh": 9, "mm": 0},
                      title="Heartbeat", system=True)

    async def run():
        with _patched(fake_run):
            await core.run_schedule_now(s["id"])
        return await _drain(sink, 1, timeout=0.5)

    events = asyncio.run(run())
    check("manual run of a system schedule is also suppressed", events == [], f"{events}")


if __name__ == "__main__":
    tests = [
        test_reindex_build_and_invalidation,
        test_search_finds_planted_fact_with_correct_line,
        test_search_no_results_is_empty_list,
        test_git_autocommit_fires_on_change_not_on_no_change,
        test_git_autocommit_failure_never_raises,
        test_turn_still_delivers_when_git_fails,
        test_autocommit_disabled_by_default,
        test_quiet_heartbeat_suppressed_pure,
        test_run_and_record_suppresses_system_schedule,
        test_run_and_record_negative_case_user_schedule_still_delivers,
        test_run_schedule_now_also_suppresses_system_schedule,
    ]
    for t in tests:
        t()

    print(f"\n{_passed} passed, {_failed} failed")
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
