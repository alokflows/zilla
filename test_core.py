# ============================================================
#  TESTS — zilla.core turn pipeline (Phase 1, seam 2)
# ============================================================
#  Deterministic, no-network tests for ZillaCore.handle_message:
#  the backend (run_cli_async) is monkeypatched, so these drive
#  the pipeline end-to-end without any CLI installed.
#
#  Covers: event sequence (Progress* then exactly one Response),
#  session bookkeeping (conv id + backend tag + message count),
#  per-user lock serialization, cancel path, and cancel-event
#  identity cleanup. Also (CORE_API migration step 4): the
#  credential/OTP bridge watcher (_bridge_poll_once, pending_ask_for,
#  answer_ask, start()/stop() of the bridge task).
#
#  Run:  python test_core.py
#  Exit code 0 = all passed, 1 = something failed.
# ============================================================

import asyncio
import json
import os
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


# ── Isolate config BEFORE importing it (same pattern as test_fixes.py) ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_core_test_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"
# P1.5 'share' route writes into the wiki journal — point it at the same
# throwaway tmpdir so a test run never touches the real ~/AGI-Brain.
os.environ["WIKI_DIR"] = os.path.join(_tmpdir, "wiki")

import zilla.config as config  # noqa: E402
config.SETTINGS_FILE = os.path.join(_tmpdir, "bot_settings.json")
config._settings_cache = None

import zilla.core as zcore  # noqa: E402
from zilla.core import (  # noqa: E402
    ZillaCore, Progress, Response, Ask, ApprovalRequest,
    BRIDGE_PENDING_TTL, APPROVAL_MAX,
)
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402
import zilla.interactive as interactive  # noqa: E402

OWNER = 111


def _fresh_core(tag: str) -> ZillaCore:
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.json"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.json"), OWNER)
    return ZillaCore(sessions=sessions, auth=auth)


def _fresh_bridge_core(tag: str, owner_chat_id=OWNER) -> tuple:
    """A core wired for the bridge tests, with its own isolated bridge_dir
    (so ask_/answer_ files from one test never leak into another)."""
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.json"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.json"), OWNER)
    bridge_dir = os.path.join(_tmpdir, f"bridge_{tag}")
    core = ZillaCore(sessions=sessions, auth=auth, owner_chat_id=owner_chat_id,
                     bridge_dir=bridge_dir)
    return core, bridge_dir


class _patched:
    """Monkeypatch zilla.core's run_cli_async / get_latest_step for one test."""

    def __init__(self, fake_run, latest_step=7):
        self.fake_run = fake_run
        self.latest_step = latest_step

    def __enter__(self):
        self._run, self._step = zcore.run_cli_async, zcore.get_latest_step
        zcore.run_cli_async = self.fake_run
        zcore.get_latest_step = lambda conv: self.latest_step
        return self

    def __exit__(self, *exc):
        zcore.run_cli_async, zcore.get_latest_step = self._run, self._step
        return False


async def _collect(core, uid, text, **kw):
    events = []
    async for ev in core.handle_message(uid, text, **kw):
        events.append(ev)
    return events


# ── 1. event sequence + response assembly ──────────────────

def test_event_sequence():
    print("\n[1] Event sequence — Progress* then exactly one Response")
    core = _fresh_core("seq")

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        if progress_callback:
            progress_callback("Reading files…")
            progress_callback("Writing answer…")
        return "Hello from the fake backend!", "conv-abc-123"

    async def run():
        with _patched(fake_run):
            # NOT "hi there" — P1.5 triage would fast-path pure smalltalk
            # before this ever reaches the (mocked) full pipeline under test.
            return await _collect(core, OWNER, "read the report please", auto_title=True)

    events = asyncio.run(run())
    responses = [e for e in events if isinstance(e, Response)]
    progresses = [e for e in events if isinstance(e, Progress)]
    check("exactly one Response", len(responses) == 1, f"got {len(responses)}")
    check("only Progress/Response yielded",
          len(events) == len(responses) + len(progresses))
    check("Response is the LAST event",
          bool(events) and isinstance(events[-1], Response))
    check("progress relayed", [p.text for p in progresses] ==
          ["Reading files…", "Writing answer…"], f"{[p.text for p in progresses]}")
    r = responses[0]
    check("response text verbatim", r.text == "Hello from the fake backend!")
    check("no file paths detected", r.files == ())
    check("meta carries conv id", r.meta.get("conv_id") == "conv-abc-123")
    check("meta not canceled", r.meta.get("canceled") is False)


# ── 2. session bookkeeping ──────────────────────────────────

def test_session_bookkeeping():
    print("\n[2] Session bookkeeping — conv id, backend tag, counters, title")
    core = _fresh_core("book")
    uid = OWNER

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        return "done", "conv-new-1"

    async def run():
        with _patched(fake_run, latest_step=42):
            return await _collect(core, uid, "summarise my week please",
                                  auto_title=True)

    asyncio.run(run())
    s = core.sessions
    sname = s.get_active_name(uid)
    check("conversation id stored", s.get_conversation_id(user_id=uid) == "conv-new-1")
    check("conv tagged with backend", s.get_conv_backend(uid, sname) == "agy",
          f"{s.get_conv_backend(uid, sname)}")
    info = s.get_session_info(user_id=uid)
    check("message count incremented", info and info.get("messages") == 1,
          f"{info}")
    check("last_seen_step recorded", s.get_last_seen_step(uid) == 42,
          f"{s.get_last_seen_step(uid)}")

    # Second turn resumes the SAME conversation (id passed back in).
    seen = {}

    async def fake_run2(prompt, conv_id, progress_callback=None,
                        cancel_event=None, skip_permissions=False):
        seen["conv"] = conv_id
        return "again", "conv-new-1"

    async def run2():
        with _patched(fake_run2, latest_step=43):
            return await _collect(core, uid, "and again")

    asyncio.run(run2())
    check("second turn resumes conv", seen.get("conv") == "conv-new-1", f"{seen}")
    check("count now 2", s.get_session_info(user_id=uid).get("messages") == 2)


# ── 3. per-user lock serialization ──────────────────────────

def test_lock_serialization():
    print("\n[3] Lock — two concurrent turns for one user run sequentially")
    core = _fresh_core("lock")
    running = {"now": 0, "max": 0, "order": []}

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        running["order"].append(f"start:{prompt}")
        await asyncio.sleep(0.05)
        running["now"] -= 1
        running["order"].append(f"end:{prompt}")
        return f"answer to {prompt}", "conv-x"

    async def run():
        with _patched(fake_run):
            await asyncio.gather(
                _collect(core, OWNER, "first"),
                _collect(core, OWNER, "second"),
            )

    asyncio.run(run())
    check("same-user turns never overlap", running["max"] == 1,
          f"max concurrency {running['max']} order={running['order']}")
    check("both turns actually ran", len(running["order"]) == 4)

    # Different users ARE concurrent (locks are per-user).
    running2 = {"now": 0, "max": 0}

    async def fake_run2(prompt, conv_id, progress_callback=None,
                        cancel_event=None, skip_permissions=False):
        running2["now"] += 1
        running2["max"] = max(running2["max"], running2["now"])
        await asyncio.sleep(0.05)
        running2["now"] -= 1
        return "ok", None

    async def run2():
        with _patched(fake_run2):
            await asyncio.gather(
                _collect(core, OWNER, "a"),
                # second user: pass skip_permissions so auth isn't consulted
                _collect(core, 333, "b", skip_permissions=True),
            )

    asyncio.run(run2())
    check("different users run concurrently", running2["max"] == 2,
          f"max concurrency {running2['max']}")


# ── 4. cancel path ──────────────────────────────────────────

def test_cancel():
    print("\n[4] Cancel — core.cancel() aborts the run; canceled Response delivered")
    core = _fresh_core("cancel")
    uid = OWNER

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        # Behave like the engine: run until canceled, then hand back the
        # transcript-only partial with a status header (I-CANCEL shape).
        for _ in range(200):
            if cancel_event.is_set():
                return "🛑 Canceled — partial result so far.", None
            await asyncio.sleep(0.01)
        return "should have been canceled", None

    results = {}

    async def run():
        with _patched(fake_run):
            async def consume():
                results["events"] = await _collect(core, uid, "long task")

            task = asyncio.create_task(consume())
            await asyncio.sleep(0.05)          # let the run start
            results["busy"] = core.is_busy(uid)
            results["cancel1"] = core.cancel(uid)   # chat_key defaults to uid
            results["cancel2"] = core.cancel(uid)   # already set → False
            await task
            results["cancel_after"] = core.cancel(uid)  # nothing running → False
            results["busy_after"] = core.is_busy(uid)

    asyncio.run(run())
    resp = [e for e in results["events"] if isinstance(e, Response)]
    check("busy while running", results["busy"] is True)
    check("cancel() found a live event", results["cancel1"] is True)
    check("second cancel() is a no-op", results["cancel2"] is False)
    check("canceled-style Response delivered",
          len(resp) == 1 and resp[0].text.startswith("🛑 Canceled"),
          f"{[r.text for r in resp]}")
    check("meta flags canceled", resp[0].meta.get("canceled") is True)
    check("cancel event cleaned up (identity-matched pop)",
          core._active_cancel == {}, f"{core._active_cancel}")
    check("no longer busy", results["busy_after"] is False)
    check("cancel after finish → False", results["cancel_after"] is False)


# ── 5. failure path — cancel bookkeeping never leaks ────────

def test_error_cleanup():
    print("\n[5] Errors — backend exception propagates, state stays clean")
    core = _fresh_core("err")

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        raise RuntimeError("CLI exploded")

    async def run():
        with _patched(fake_run):
            try:
                await _collect(core, OWNER, "boom")
                return None
            except RuntimeError as e:
                return e

    err = asyncio.run(run())
    check("exception propagates to the frontend", isinstance(err, RuntimeError),
          f"{err!r}")
    check("no stale cancel event", core._active_cancel == {})
    check("lock released", core.is_busy(OWNER) is False)
    info = core.sessions.get_session_info(user_id=OWNER)
    check("failed turn not counted", (info or {}).get("messages", 0) == 0,
          f"{info}")


# ── 6. bridge — one ask announced exactly once ──────────────

def test_bridge_announce_once():
    print("\n[6] Bridge — a written ask is announced exactly once")
    core, bridge_dir = _fresh_bridge_core("announce")
    q = asyncio.Queue()
    core.subscribe(q)

    ask = interactive.make_ask("otp", "code?", chat_id=555)
    interactive.write_ask(ask, bridge_dir=bridge_dir)

    async def run():
        await core._bridge_poll_once()
        first_events = []
        while not q.empty():
            first_events.append(q.get_nowait())
        await core._bridge_poll_once()  # second poll — must not re-announce
        second_events = []
        while not q.empty():
            second_events.append(q.get_nowait())
        return first_events, second_events

    first, second = asyncio.run(run())
    check("exactly one Ask on first poll",
          len(first) == 1 and isinstance(first[0], Ask), f"{first}")
    ev = first[0]
    check("id matches", ev.id == ask.id)
    check("kind matches", ev.kind == "otp")
    check("prompt matches", ev.prompt == "code?")
    check("chat_id matches", ev.chat_id == 555)
    check("is_secret True for otp", ev.is_secret is True)
    check("no re-announce on second poll", second == [], f"{second}")


# ── 7. bridge — no subscribers means nothing announced ──────

def test_bridge_no_subscribers():
    print("\n[7] Bridge — no subscribers ⇒ no announce; subscribing unblocks it")
    core, bridge_dir = _fresh_bridge_core("nosub")
    ask = interactive.make_ask("text", "name?", chat_id=222)
    interactive.write_ask(ask, bridge_dir=bridge_dir)

    async def run():
        await core._bridge_poll_once()
        not_announced = ask.id not in core._bridge_announced
        q = asyncio.Queue()
        core.subscribe(q)
        await core._bridge_poll_once()
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return not_announced, events

    not_announced, events = asyncio.run(run())
    check("not announced while unsubscribed", not_announced)
    check("announced once a subscriber exists",
          len(events) == 1 and isinstance(events[0], Ask), f"{events}")


# ── 8. bridge — chat_id fallback to owner; no target ⇒ skipped ──

def test_bridge_chat_id_fallback():
    print("\n[8] Bridge — falsy ask.chat_id falls back to owner_chat_id")
    core, bridge_dir = _fresh_bridge_core("fallback", owner_chat_id=999)
    q = asyncio.Queue()
    core.subscribe(q)
    ask = interactive.make_ask("confirm", "proceed?", chat_id=0)
    interactive.write_ask(ask, bridge_dir=bridge_dir)

    async def poll_and_drain():
        await core._bridge_poll_once()
        out = []
        while not q.empty():
            out.append(q.get_nowait())
        return out

    events = asyncio.run(poll_and_drain())
    check("falls back to owner_chat_id",
          len(events) == 1 and events[0].chat_id == 999, f"{events}")

    # Neither ask.chat_id nor owner_chat_id ⇒ skipped, never announced.
    core2, bridge_dir2 = _fresh_bridge_core("fallback_none", owner_chat_id=None)
    q2 = asyncio.Queue()
    core2.subscribe(q2)
    ask2 = interactive.make_ask("confirm", "proceed?", chat_id=0)
    interactive.write_ask(ask2, bridge_dir=bridge_dir2)

    async def run2():
        await core2._bridge_poll_once()

    asyncio.run(run2())
    check("no target ⇒ not announced", ask2.id not in core2._bridge_announced)
    check("no target ⇒ queue empty", q2.empty())


# ── 9. bridge — one outstanding ask per chat ─────────────────

def test_bridge_one_outstanding_per_chat():
    print("\n[9] Bridge — one outstanding ask per chat; next announces after answer")
    core, bridge_dir = _fresh_bridge_core("onepc")
    q = asyncio.Queue()
    core.subscribe(q)
    chat = 777
    ask1 = interactive.make_ask("otp", "first code?", chat_id=chat)
    interactive.write_ask(ask1, bridge_dir=bridge_dir)

    async def poll_and_drain():
        await core._bridge_poll_once()
        out = []
        while not q.empty():
            out.append(q.get_nowait())
        return out

    events1 = asyncio.run(poll_and_drain())
    check("first ask announced", len(events1) == 1 and events1[0].id == ask1.id,
          f"{events1}")

    ask2 = interactive.make_ask("otp", "second code?", chat_id=chat)
    interactive.write_ask(ask2, bridge_dir=bridge_dir)
    events2 = asyncio.run(poll_and_drain())
    check("second ask NOT announced while first pending", events2 == [], f"{events2}")

    # Answer the first — it disappears from the pending set (has an answer
    # file) — one more poll both cleans it up and frees the chat for ask2.
    core.answer_ask(ask1.id, "123456")
    events3 = asyncio.run(poll_and_drain())
    check("second ask announces once first is answered",
          len(events3) == 1 and events3[0].id == ask2.id, f"{events3}")


# ── 10. pending_ask_for / answer_ask ─────────────────────────

def test_pending_ask_for_and_answer():
    print("\n[10] pending_ask_for returns (id, is_secret); answer_ask records + clears")
    core, bridge_dir = _fresh_bridge_core("pending")
    q = asyncio.Queue()
    core.subscribe(q)
    chat = 42
    ask = interactive.make_ask("password", "pw?", chat_id=chat)
    interactive.write_ask(ask, bridge_dir=bridge_dir)
    asyncio.run(core._bridge_poll_once())

    pending = core.pending_ask_for(chat)
    check("pending_ask_for returns (ask_id, is_secret)",
          pending == (ask.id, True), f"{pending}")

    core.answer_ask(ask.id, "hunter2")
    check("answer recorded on disk",
          interactive.read_answer(ask.id, bridge_dir=bridge_dir) == "hunter2")
    check("pending entry cleared", core.pending_ask_for(chat) is None)


# ── 11. TTL — stale pending ask is released ──────────────────

def test_pending_ask_ttl():
    print("\n[11] pending_ask_for — TTL releases a stale ask and clears its files")
    core, bridge_dir = _fresh_bridge_core("ttl")
    q = asyncio.Queue()
    core.subscribe(q)
    chat = 88
    ask = interactive.make_ask("otp", "code?", chat_id=chat)
    interactive.write_ask(ask, bridge_dir=bridge_dir)
    asyncio.run(core._bridge_poll_once())

    ask_id, announced_ts, is_secret = core._pending_asks[chat]
    core._pending_asks[chat] = (ask_id, announced_ts - BRIDGE_PENDING_TTL - 1, is_secret)

    check("stale ask releases (returns None)", core.pending_ask_for(chat) is None)
    check("chat no longer owes an answer", chat not in core._pending_asks)
    check("ask file cleared",
          interactive.read_pending_asks(bridge_dir=bridge_dir) == [])


# ── 12. cleanup — externally cleared ask is forgotten ────────

def test_bridge_external_clear_cleanup():
    print("\n[12] Bridge — an ask cleared externally is forgotten after one poll")
    core, bridge_dir = _fresh_bridge_core("extclear")
    q = asyncio.Queue()
    core.subscribe(q)
    chat = 63
    ask = interactive.make_ask("text", "name?", chat_id=chat)
    interactive.write_ask(ask, bridge_dir=bridge_dir)
    asyncio.run(core._bridge_poll_once())
    check("announced before clear", ask.id in core._bridge_announced)

    interactive.clear_ask(ask.id, bridge_dir=bridge_dir)
    asyncio.run(core._bridge_poll_once())
    check("forgotten from _bridge_announced", ask.id not in core._bridge_announced)
    check("forgotten from _pending_asks", chat not in core._pending_asks)


# ── 13. start()/stop() — bridge task without a ScheduleManager ──

def test_bridge_start_stop_without_scheduler():
    print("\n[13] start()/stop() — bridge watcher runs even with no ScheduleManager")
    sessions = SessionManager(os.path.join(_tmpdir, "sessions_bstart.json"))
    auth = AuthManager(os.path.join(_tmpdir, "users_bstart.json"), OWNER)
    bridge_dir = os.path.join(_tmpdir, "bridge_bstart")
    core = ZillaCore(sessions=sessions, auth=auth, owner_chat_id=OWNER,
                     bridge_dir=bridge_dir)  # schedules=None

    async def run():
        await core.start()
        check("no scheduler task (no ScheduleManager)", core._sched_task is None)
        check("bridge task created", core._bridge_task is not None)
        await asyncio.sleep(0.05)
        check("bridge task alive", not core._bridge_task.done())
        await core.stop()
        check("bridge task cleared after stop", core._bridge_task is None)

    asyncio.run(run())


# ── 14. health_report — stable keys, no ScheduleManager ──────

class _patched_health:
    """Monkeypatch zilla.core's agy_reachable / agy_models_live /
    claude_identity for one test, and record how each was called."""

    def __init__(self, agy_ok=True, claude_status=None):
        self.calls = {"agy_reachable": 0, "agy_models_live": 0, "claude_identity": []}
        self._agy_ok = agy_ok
        self._claude_status = claude_status or {"loggedIn": True}

    def _fake_agy_reachable(self):
        self.calls["agy_reachable"] += 1
        return self._agy_ok

    def _fake_agy_models_live(self, force=False):
        self.calls["agy_models_live"] += 1
        return []

    def _fake_claude_identity(self, force=False):
        self.calls["claude_identity"].append(force)
        return self._claude_status

    def __enter__(self):
        self._orig = (zcore.agy_reachable, zcore.agy_models_live, zcore.claude_identity)
        zcore.agy_reachable = self._fake_agy_reachable
        zcore.agy_models_live = self._fake_agy_models_live
        zcore.claude_identity = self._fake_claude_identity
        return self

    def __exit__(self, *exc):
        zcore.agy_reachable, zcore.agy_models_live, zcore.claude_identity = self._orig
        return False


def test_health_report_stable_keys():
    print("\n[14] health_report — stable keys, no ScheduleManager attached")
    core = _fresh_core("health_keys")  # schedules=None (built without a ScheduleManager)

    with _patched_health(agy_ok=True, claude_status={"loggedIn": False, "error": "not logged in"}) as p:
        report = core.health_report()

    check("top-level keys present",
          set(report.keys()) == {"backend", "model", "clis", "disk", "scheduler", "bridge"},
          f"got {sorted(report.keys())}")
    check("backend is the configured one", report["backend"] == config.get_backend())
    check("model is a plain string", isinstance(report["model"], str) and report["model"])
    check("clis has agy + claude", set(report["clis"].keys()) == {"agy", "claude"})
    check("agy reachable reflects probe", report["clis"]["agy"]["reachable"] is True)
    check("claude reachable reflects probe", report["clis"]["claude"]["reachable"] is False)
    check("claude carries auth_error", report["clis"]["claude"]["auth_error"] == "not logged in")
    check("disk has path/free_bytes/total_bytes",
          set(report["disk"].keys()) == {"path", "free_bytes", "total_bytes"})
    check("disk free_bytes is a plain int (or None)",
          report["disk"]["free_bytes"] is None or isinstance(report["disk"]["free_bytes"], int))
    check("scheduler not attached (built without a ScheduleManager)",
          report["scheduler"] == {"attached": False, "schedule_count": 0},
          f"got {report['scheduler']}")
    check("bridge dir + exists reported",
          set(report["bridge"].keys()) == {"dir", "exists"} and isinstance(report["bridge"]["exists"], bool))


# ── 15. health_report — force=False never forces a live probe ──

def test_health_report_force_false_is_cached():
    print("\n[15] health_report — force=False uses cached/cheap probe forms only")
    core = _fresh_core("health_cache")

    with _patched_health() as p:
        core.health_report()  # force=False (default)
    check("agy_models_live NOT called (agy_reachable's cached form used)",
          p.calls["agy_models_live"] == 0, f"calls={p.calls}")
    check("agy_reachable called once",
          p.calls["agy_reachable"] == 1, f"calls={p.calls}")
    check("claude_identity called with force=False (its own cached form)",
          p.calls["claude_identity"] == [False], f"calls={p.calls}")

    with _patched_health() as p:
        core.health_report(force=True)
    check("force=True DOES refresh agy's live cache",
          p.calls["agy_models_live"] == 1, f"calls={p.calls}")
    check("force=True passes through to claude_identity",
          p.calls["claude_identity"] == [True], f"calls={p.calls}")


# ── 16. health_report — with a ScheduleManager attached ─────

def test_health_report_with_schedule_manager():
    print("\n[16] health_report — scheduler attached + schedule_count reflects it")
    from zilla.schedules import ScheduleManager
    sched = ScheduleManager(os.path.join(_tmpdir, "schedules_health.json"))
    sched.add(OWNER, OWNER, "a scheduled prompt", "daily", {"hh": 9, "mm": 0})
    sessions = SessionManager(os.path.join(_tmpdir, "sessions_health_sm.json"))
    auth = AuthManager(os.path.join(_tmpdir, "users_health_sm.json"), OWNER)
    core = ZillaCore(sessions=sessions, auth=auth, schedules=sched)

    with _patched_health():
        report = core.health_report()
    check("scheduler attached", report["scheduler"]["attached"] is True)
    check("schedule_count reflects the manager", report["scheduler"]["schedule_count"] == 1,
          f"got {report['scheduler']}")

# ── 15. approvals — submit() broadcasts ApprovalRequest ──────

def test_approval_submit_broadcast():
    print("\n[14] Approvals — submit() registers the hold and broadcasts ApprovalRequest")
    core = _fresh_core("appr_submit")
    q = asyncio.Queue()
    core.subscribe(q)

    rid = core.approvals.submit(uid=222, chat_id=222, prompt="do a thing", name="Alice")
    check("submit returns an id", bool(rid), f"{rid}")
    check("queue has exactly one event", q.qsize() == 1, f"{q.qsize()}")
    ev = q.get_nowait()
    check("event is ApprovalRequest", isinstance(ev, ApprovalRequest), f"{ev!r}")
    check("id matches", ev.id == rid)
    check("user matches", ev.user == 222)
    check("prompt matches", ev.prompt == "do a thing")
    check("chat_id matches", ev.chat_id == 222)
    check("name matches", ev.name == "Alice")


# ── 15. approvals — pending() listing ────────────────────────

def test_approval_pending_listing():
    print("\n[15] Approvals — pending() lists every held request")
    core = _fresh_core("appr_pending")
    r1 = core.approvals.submit(uid=1, chat_id=1, prompt="first", name="A")
    r2 = core.approvals.submit(uid=2, chat_id=2, prompt="second", name="B")

    pending = core.approvals.pending()
    ids = {r["id"] for r in pending}
    check("both requests listed", ids == {r1, r2}, f"{ids}")
    by_id = {r["id"]: r for r in pending}
    check("fields carried for first", by_id[r1]["uid"] == 1 and
          by_id[r1]["chat_id"] == 1 and by_id[r1]["prompt"] == "first" and
          by_id[r1]["name"] == "A", f"{by_id[r1]}")

    # Hard cap: once at APPROVAL_MAX, submit() refuses (mirrors bot.py's old
    # "too many requests waiting" notice on None).
    core2 = _fresh_core("appr_cap")
    for i in range(APPROVAL_MAX):
        assert core2.approvals.submit(uid=i, chat_id=i, prompt="x", name="n") is not None
    check("queue full -> submit returns None",
          core2.approvals.submit(uid=999, chat_id=999, prompt="one too many", name="n") is None)


# ── 16. approvals — approve() runs the held turn and clears it ──

def test_approval_approve_runs_and_clears():
    print("\n[16] Approvals — approve() runs the held turn (monkeypatched backend) and clears it")
    core = _fresh_core("appr_run")
    uid = 555

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        check("skip_permissions passed through (owner already vetted)",
              skip_permissions is True)
        return f"ran: {prompt}", "conv-appr-1"

    rid = core.approvals.submit(uid=uid, chat_id=777, prompt="book a flight", name="Bob")

    async def run():
        with _patched(fake_run, latest_step=9):
            return await core.approvals.approve(rid)

    result = asyncio.run(run())
    check("approve() returns the resolved request", result is not None)
    check("response is the turn's output", result["response"] == "ran: book a flight",
          f"{result}")
    check("uid/chat_id/name carried through", result["uid"] == uid and
          result["chat_id"] == 777 and result["name"] == "Bob", f"{result}")
    check("cleared from pending()", core.approvals.pending() == [])
    info = core.sessions.get_session_info(user_id=uid)
    check("the run went through normal session bookkeeping",
          info and info.get("messages") == 1, f"{info}")

    # Re-approving the same (now-gone) id is a safe no-op.
    async def run_again():
        return await core.approvals.approve(rid)

    check("re-approve of an already-run id returns None",
          asyncio.run(run_again()) is None)


# ── 17. approvals — deny() discards + clears ─────────────────

def test_approval_deny_clears():
    print("\n[17] Approvals — deny() discards without running, and clears it")
    core = _fresh_core("appr_deny")
    rid = core.approvals.submit(uid=333, chat_id=444, prompt="rm -rf something scary",
                                name="Eve")

    req = core.approvals.deny(rid)
    check("deny() returns the discarded request", req is not None)
    check("uid/chat_id/name/prompt carried through",
          req["uid"] == 333 and req["chat_id"] == 444 and req["name"] == "Eve" and
          req["prompt"] == "rm -rf something scary", f"{req}")
    check("cleared from pending()", core.approvals.pending() == [])
    check("denying again is a safe no-op", core.approvals.deny(rid) is None)


# ── 18. approvals — an approved turn serializes with a live turn ──

def test_approval_shares_per_user_lock():
    print("\n[18] Approvals — an approved turn serializes with a live turn for the same uid")
    core = _fresh_core("appr_lock")
    uid = 888
    running = {"now": 0, "max": 0, "order": []}

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        running["order"].append(f"start:{prompt}")
        await asyncio.sleep(0.05)
        running["now"] -= 1
        running["order"].append(f"end:{prompt}")
        return f"answer to {prompt}", "conv-shared"

    rid = core.approvals.submit(uid=uid, chat_id=uid, prompt="approved task", name="Carl")

    async def run():
        with _patched(fake_run):
            await asyncio.gather(
                _collect(core, uid, "live message", skip_permissions=True),
                core.approvals.approve(rid),
            )

    asyncio.run(run())
    check("same-uid live turn and approved turn never overlap", running["max"] == 1,
          f"max concurrency {running['max']} order={running['order']}")
    check("both turns actually ran", len(running["order"]) == 4, f"{running['order']}")


# ── 19. approvals — unknown id is a safe no-op ───────────────

def test_approval_unknown_id_noop():
    print("\n[19] Approvals — approve()/deny() on an unknown id is a safe no-op")
    core = _fresh_core("appr_unknown")

    async def run():
        return await core.approvals.approve("does-not-exist")

    check("approve() of unknown id -> None", asyncio.run(run()) is None)
    check("deny() of unknown id -> None", core.approvals.deny("does-not-exist") is None)
    check("pending() still empty", core.approvals.pending() == [])


# ── 20. P1.5 triage — share route: zero-model journal append ──

def test_triage_share_route_journals_and_zero_model_calls():
    print("\n[20] Triage — 'share' route journals verbatim + acks, no CLI call")
    core = _fresh_core("triage_share")
    called = {"n": 0}

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        called["n"] += 1
        return "SHOULD NOT BE CALLED", None

    async def run():
        with _patched(fake_run):
            return await _collect(core, OWNER, "remember the wifi password is hunter2")

    events = asyncio.run(run())
    check("zero CLI invocations", called["n"] == 0)
    responses = [e for e in events if isinstance(e, Response)]
    check("exactly one Response", len(responses) == 1, f"{events}")
    check("ack text", responses and responses[0].text == "📝 Noted.",
          responses[0].text if responses else None)

    journal_path = os.path.join(config.WIKI_JOURNAL_DIR,
                                time.strftime("%Y-%m-%d.md"))
    check("journal file created", os.path.isfile(journal_path), journal_path)
    with open(journal_path, encoding="utf-8") as f:
        content = f.read()
    check("message appended verbatim", "remember the wifi password is hunter2" in content,
          content)


# ── 21. P1.5 triage — smalltalk fast path (mocked fast-claude) ──

def test_triage_smalltalk_fast_path():
    print("\n[21] Triage — 'smalltalk' route uses the fast path, not the full pipeline")
    core = _fresh_core("triage_fast")
    called = {"n": 0}

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        called["n"] += 1
        return "SHOULD NOT BE CALLED", None

    orig_fast = zcore._run_fast_claude
    zcore._run_fast_claude = lambda prompt: "Hey! 👋"
    try:
        async def run():
            with _patched(fake_run):
                return await _collect(core, OWNER, "hey there")
        events = asyncio.run(run())
    finally:
        zcore._run_fast_claude = orig_fast

    check("full pipeline NOT invoked", called["n"] == 0)
    responses = [e for e in events if isinstance(e, Response)]
    check("exactly one Response", len(responses) == 1, f"{events}")
    check("fast-path text delivered", responses and responses[0].text == "Hey! 👋",
          responses[0].text if responses else None)


# ── 22. P1.5 triage — smalltalk fast path falls back transparently ──

def test_triage_smalltalk_fallback_to_full_path():
    print("\n[22] Triage — smalltalk falls back to the full path when fast-claude fails")
    core = _fresh_core("triage_fallback")
    called = {"n": 0}

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        called["n"] += 1
        return "full pipeline answered", "conv-fallback-1"

    # Unreachable: _run_fast_claude returns None.
    orig_fast = zcore._run_fast_claude
    zcore._run_fast_claude = lambda prompt: None
    try:
        async def run():
            with _patched(fake_run):
                return await _collect(core, OWNER, "thanks")
        events = asyncio.run(run())
    finally:
        zcore._run_fast_claude = orig_fast

    check("fell back to the full pipeline", called["n"] == 1)
    responses = [e for e in events if isinstance(e, Response)]
    check("full-pipeline response delivered",
          responses and responses[0].text == "full pipeline answered",
          responses[0].text if responses else None)


def main():
    tests = [
        test_event_sequence,
        test_session_bookkeeping,
        test_lock_serialization,
        test_cancel,
        test_error_cleanup,
        test_bridge_announce_once,
        test_bridge_no_subscribers,
        test_bridge_chat_id_fallback,
        test_bridge_one_outstanding_per_chat,
        test_pending_ask_for_and_answer,
        test_pending_ask_ttl,
        test_bridge_external_clear_cleanup,
        test_bridge_start_stop_without_scheduler,
        test_health_report_stable_keys,
        test_health_report_force_false_is_cached,
        test_health_report_with_schedule_manager,
        test_approval_submit_broadcast,
        test_approval_pending_listing,
        test_approval_approve_runs_and_clears,
        test_approval_deny_clears,
        test_approval_shares_per_user_lock,
        test_approval_unknown_id_noop,
        test_triage_share_route_journals_and_zero_model_calls,
        test_triage_smalltalk_fast_path,
        test_triage_smalltalk_fallback_to_full_path,
    ]
    print("Running zilla.core turn-pipeline tests...\n")
    for t in tests:
        try:
            t()
        except Exception as e:
            global _failed
            _failed += 1
            print(f"  ERROR {getattr(t, '__name__', t)}: {e!r}")
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
