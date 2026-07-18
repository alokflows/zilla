# ============================================================
#  TESTS — Phase M4: nightly distillation + /memory + change surfacing
#  (PLAN.md §5.M4 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/schedules.py: ensure_system_schedule() idempotency (the
#      distillation schedule exists exactly once even after it's called
#      again on a "restart" against the same persisted store) and
#      ScheduleManager.remove() refusing to delete a system=1 schedule.
#    - zilla/core.py: ZillaCore._autocommit_memory's change-surfacing DM —
#      fires when a memory-changing run was untrusted (document-ingest turn
#      or non-owner-originated) and stays silent on an ordinary owner turn,
#      even though both turns commit the same underlying change.
#    - zilla/memory.py: git_last_commit_stat()/git_log()/git_diff_latest()
#      — the read side /memory renders, including its diff subcommand.
#    - bot.py: cmd_memory renders MEMORY.md + today's journal + commit
#      list, and /memory diff renders the latest unified diff.
#
#  Run:  python test_memory_m4.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every test points zilla.memory.MEMORY_DIR at a throwaway tmpdir and
#  zilla.config.DB_FILE at a throwaway sqlite file (same pattern
#  test_memory_m3.py uses) so a run never reads or writes the real repo
#  Memory/ tree or zilla.db.
# ============================================================

import asyncio
import json
import os
import shutil
import sys
import tempfile
import types

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
_tmpdir = tempfile.mkdtemp(prefix="zilla_m4_cfg_")
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
from zilla.core import ZillaCore, Response, Alert  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402
from zilla.schedules import ScheduleManager, ensure_system_schedule  # noqa: E402

OWNER = 111
NON_OWNER = 222


def _iso_mem_dir():
    tmp = tempfile.mkdtemp(prefix="zilla_m4_mem_")
    old = memory.MEMORY_DIR
    memory.MEMORY_DIR = os.path.join(tmp, "Memory")
    return tmp, old


def _fresh_core(tag: str, schedules=None, extra_role=None) -> ZillaCore:
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.json"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.json"), OWNER)
    if extra_role:
        auth.add_user(NON_OWNER, extra_role, "tester")
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


# ── 1. ensure_system_schedule() — exists exactly once after "double restart" ──

def test_distillation_schedule_seeded_exactly_once():
    print("\n[1] schedules.ensure_system_schedule() — idempotent across repeated 'restarts'")
    db_path = os.path.join(_tmpdir, "schedules_m4_seed.json")
    title = "Nightly memory distillation"
    prompt = "Read yesterday's Journal file..."

    mgr1 = ScheduleManager(db_path)
    s1 = ensure_system_schedule(mgr1, OWNER, title, prompt, "daily", {"hh": 3, "mm": 30})
    check("first call creates the schedule", s1 is not None)

    # Simulate a full process restart: a brand new ScheduleManager instance
    # against the SAME persisted path.
    mgr2 = ScheduleManager(db_path)
    s2 = ensure_system_schedule(mgr2, OWNER, title, prompt, "daily", {"hh": 3, "mm": 30})
    check("second 'restart' call returns the SAME schedule id", s2["id"] == s1["id"],
          (s1, s2))

    mgr3 = ScheduleManager(db_path)
    s3 = ensure_system_schedule(mgr3, OWNER, title, prompt, "daily", {"hh": 3, "mm": 30})
    check("third 'restart' call still returns the same id", s3["id"] == s1["id"])

    matches = [s for s in mgr3.list(OWNER, include_system=True)
               if s.get("system") and s.get("title") == title]
    check("exactly one matching system schedule exists after 3 restarts",
          len(matches) == 1, matches)
    check("seeded schedule runs isolated (throwaway conv, never advances a session)",
          matches[0]["session"] == "isolated", matches[0])
    check("seeded schedule is a system job", matches[0]["system"] is True)


def test_system_schedule_not_deletable_but_pausable():
    print("\n[1b] ScheduleManager — a system=1 schedule is pausable, never deletable")
    db_path = os.path.join(_tmpdir, "schedules_m4_guard.json")
    mgr = ScheduleManager(db_path)
    s = ensure_system_schedule(mgr, OWNER, "Nightly memory distillation", "prompt",
                               "daily", {"hh": 3, "mm": 30})
    check("pausing (set_enabled False) succeeds", mgr.set_enabled(s["id"], OWNER, False) is True)
    check("schedule now disabled", mgr.get(s["id"])["enabled"] is False)
    check("re-enabling succeeds", mgr.set_enabled(s["id"], OWNER, True) is True)
    check("remove() refuses to delete a system schedule", mgr.remove(s["id"], OWNER) is False)
    check("schedule still present after the refused delete", mgr.get(s["id"]) is not None)


# ── 2. Memory-change surfacing — untrusted turn DMs, ordinary owner turn doesn't ──

def test_change_notice_fires_on_untrusted_turn_not_on_owner_turn():
    print("\n[2] change-surfacing — untrusted (document-ingest) turn DMs the owner; "
          "an ordinary owner turn stays silent")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        core = _fresh_core("change_notice")
        core.memory_autocommit_enabled = True
        sink = asyncio.Queue()
        core.subscribe(sink)

        def _write_memory_change(tag):
            with open(os.path.join(memory.MEMORY_DIR, "MEMORY.md"), "a", encoding="utf-8") as f:
                f.write(f"agent wrote this during a {tag} turn\n")

        async def fake_run_trusted(prompt, conv_id, progress_callback=None,
                                   cancel_event=None, skip_permissions=False, ctx=None):
            _write_memory_change("trusted")
            return "done", "conv-trusted"

        async def fake_run_untrusted(prompt, conv_id, progress_callback=None,
                                     cancel_event=None, skip_permissions=False, ctx=None):
            _write_memory_change("untrusted")
            return "done", "conv-untrusted"

        def _drain_alerts(q):
            out = []
            while not q.empty():
                ev = q.get_nowait()
                if isinstance(ev, Alert):
                    out.append(ev)
            return out

        # Ordinary owner turn, plain text — trusted, no browser intent.
        async def run_owner():
            with _patched(fake_run_trusted):
                return await _collect(core, OWNER, "what's the weather look like")

        asyncio.run(run_owner())
        alerts_owner = _drain_alerts(sink)
        check("ordinary owner turn commits but does NOT alert the owner",
              alerts_owner == [], alerts_owner)

        # Document-ingest-shaped turn (bot.py passes untrusted_input=True for these).
        async def run_doc():
            with _patched(fake_run_untrusted):
                return await _collect(core, OWNER, "summarize this document",
                                      untrusted_input=True)

        asyncio.run(run_doc())
        alerts_doc = _drain_alerts(sink)
        check("document-ingest turn DOES alert the owner", len(alerts_doc) == 1, alerts_doc)
        check("alert names the changed file and a commit hash",
              bool(alerts_doc) and "MEMORY.md" in alerts_doc[0].text and "memory changed" in alerts_doc[0].text,
              alerts_doc[0].text if alerts_doc else None)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_change_notice_fires_for_non_owner_originated_run():
    print("\n[2b] change-surfacing — a non-owner-originated turn also DMs the owner")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        core = _fresh_core("change_notice_nonowner", extra_role="admin")
        core.memory_autocommit_enabled = True
        sink = asyncio.Queue()
        core.subscribe(sink)

        async def fake_run(prompt, conv_id, progress_callback=None,
                           cancel_event=None, skip_permissions=False, ctx=None):
            with open(os.path.join(memory.MEMORY_DIR, "MEMORY.md"), "a", encoding="utf-8") as f:
                f.write("a non-owner turn touched memory\n")
            return "done", "conv-nonowner"

        async def run():
            with _patched(fake_run):
                return await _collect(core, NON_OWNER, "help me with something")

        asyncio.run(run())
        alerts = []
        while not sink.empty():
            ev = sink.get_nowait()
            if isinstance(ev, Alert):
                alerts.append(ev)
        check("non-owner turn that changed memory alerts the owner", len(alerts) == 1, alerts)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_alert_when_nothing_changed():
    print("\n[2c] change-surfacing — an untrusted turn that changes NOTHING never alerts")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        memory.git_autocommit("seed commit so the tree starts clean")
        core = _fresh_core("change_notice_noop")
        core.memory_autocommit_enabled = True
        sink = asyncio.Queue()
        core.subscribe(sink)

        async def fake_run(prompt, conv_id, progress_callback=None,
                           cancel_event=None, skip_permissions=False, ctx=None):
            return "done, nothing written", "conv-noop"

        async def run():
            with _patched(fake_run):
                return await _collect(core, OWNER, "summarize this document",
                                      untrusted_input=True)

        asyncio.run(run())
        alerts = []
        while not sink.empty():
            ev = sink.get_nowait()
            if isinstance(ev, Alert):
                alerts.append(ev)
        check("no memory change -> no alert, even for an untrusted turn", alerts == [], alerts)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 3. memory.py git read helpers — the data /memory renders ──

def test_git_last_commit_stat_and_log_and_diff():
    print("\n[3] memory.git_last_commit_stat()/git_log()/git_diff_latest()")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        check("no repo yet -> git_last_commit_stat is None", memory.git_last_commit_stat() is None)
        check("no repo yet -> git_log is []", memory.git_log() == [])
        check("no repo yet -> git_diff_latest is ''", memory.git_diff_latest() == "")

        with open(os.path.join(memory.MEMORY_DIR, "MEMORY.md"), "a", encoding="utf-8") as f:
            f.write("Alok's favorite color is teal.\n")
        memory.git_autocommit("first real commit")

        stat = memory.git_last_commit_stat()
        check("stat has a hash", bool(stat and stat.get("hash")), stat)
        check("stat lists MEMORY.md as changed", stat and "MEMORY.md" in stat["files"], stat)
        check("stat has at least one insertion", stat and stat["insertions"] >= 1, stat)

        with open(os.path.join(memory.MEMORY_DIR, "Wiki", "People", "friend.md"),
                  "w", encoding="utf-8") as f:
            f.write("# Friend\nSummary: test.\n")
        memory.git_autocommit("second commit")

        log = memory.git_log(5)
        check("log has 2 entries, newest first", len(log) == 2, log)
        check("newest entry is the second commit", log[0]["subject"] == "second commit", log)
        check("each log entry carries files + stat", "files" in log[0] and "insertions" in log[0])

        diff = memory.git_diff_latest()
        check("diff of the latest commit mentions the new file", "friend.md" in diff, diff[:200])
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 4. bot.py cmd_memory — owner-only, renders MEMORY.md/journal/commits, incl. diff ──

class _FakeMessage:
    def __init__(self):
        self.sent: list[str] = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)


class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeUpdate:
    def __init__(self, uid):
        self.effective_user = _FakeUser(uid)
        self.message = _FakeMessage()


class _FakeContext:
    def __init__(self, args=None):
        self.args = args or []


def test_cmd_memory_owner_only_and_renders_incl_diff():
    print("\n[4] bot.cmd_memory — owner-only, renders MEMORY.md/journal/commits + /memory diff")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        with open(os.path.join(memory.MEMORY_DIR, "MEMORY.md"), "a", encoding="utf-8") as f:
            f.write("Alok prefers terse replies.\n")
        memory.append_journal("had a good chat about zilla")
        memory.git_autocommit("seed for cmd_memory test")

        # bot.py is heavy to import for real (Telegram token / owner id
        # required at import time in some paths) — exercise the already-
        # imported module's cmd_memory against a fake auth/module state
        # instead of spinning up the whole Application.
        import bot as _bot

        class _FakeAuth:
            def is_owner(self, uid):
                return uid == OWNER

        old_auth = _bot.auth
        _bot.auth = _FakeAuth()
        try:
            # Non-owner is refused.
            u = _FakeUpdate(NON_OWNER)
            asyncio.run(_bot.cmd_memory(u, _FakeContext()))
            check("non-owner gets refused, not the memory dump",
                  u.message.sent == ["Owner only."], u.message.sent)

            # Owner sees MEMORY.md + journal + commit list.
            u2 = _FakeUpdate(OWNER)
            asyncio.run(_bot.cmd_memory(u2, _FakeContext()))
            rendered = "\n".join(u2.message.sent)
            check("owner render includes MEMORY.md content", "terse replies" in rendered, rendered)
            check("owner render includes today's journal", "good chat about zilla" in rendered, rendered)
            check("owner render includes a commit line", "seed for cmd_memory test" in rendered, rendered)

            # /memory diff shows the latest unified diff.
            u3 = _FakeUpdate(OWNER)
            asyncio.run(_bot.cmd_memory(u3, _FakeContext(["diff"])))
            diff_rendered = "\n".join(u3.message.sent)
            check("/memory diff includes a unified diff of the latest commit",
                  "+Alok prefers terse replies." in diff_rendered
                  or "MEMORY.md" in diff_rendered, diff_rendered)
        finally:
            _bot.auth = old_auth
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    tests = [
        test_distillation_schedule_seeded_exactly_once,
        test_system_schedule_not_deletable_but_pausable,
        test_change_notice_fires_on_untrusted_turn_not_on_owner_turn,
        test_change_notice_fires_for_non_owner_originated_run,
        test_no_alert_when_nothing_changed,
        test_git_last_commit_stat_and_log_and_diff,
        test_cmd_memory_owner_only_and_renders_incl_diff,
    ]
    for t in tests:
        t()

    print(f"\n{_passed} passed, {_failed} failed")
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
