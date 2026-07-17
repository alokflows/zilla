# ============================================================
#  TESTS — scheduler runtime seam (zilla.core + zilla.schedules)
# ============================================================
#  Covers HANDOFF P1 scheduler-seam PART A (runtime moved into
#  ZillaCore, ScheduledResult/Alert broadcast via subscribe()) and
#  PART B (schema upgrades: payload_type, session mode, backend/
#  model pin, retry ladder, recursion guard).
#
#  Pure-logic pieces (resolve_session_mode, backend_pin_mismatch,
#  RETRY_LADDER via mark_failure) are exercised directly against
#  zilla.schedules; the runtime pieces go through a real ZillaCore
#  with run_cli_async monkeypatched, same pattern as test_core.py.
#
#  Run:  python test_schedules_seam.py
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


# ── Isolate config BEFORE importing it (same pattern as test_core.py) ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_sched_test_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.SETTINGS_FILE = os.path.join(_tmpdir, "bot_settings.json")
config._settings_cache = None

import zilla.core as zcore  # noqa: E402
from zilla.core import ZillaCore, ScheduledResult, Alert  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402
from zilla.schedules import (  # noqa: E402
    ScheduleManager, resolve_session_mode, backend_pin_mismatch, RETRY_LADDER,
)

OWNER = 111


def _fresh(tag: str):
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.json"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.json"), OWNER)
    schedules = ScheduleManager(os.path.join(_tmpdir, f"schedules_{tag}.json"))
    core = ZillaCore(sessions=sessions, auth=auth, schedules=schedules)
    return core, schedules, auth


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


# ── 1. resolve_session_mode (pure) ──────────────────────────

def test_resolve_session_mode():
    print("\n[1] resolve_session_mode — default is 'isolated' (today's real behavior)")
    check("no session/session_name -> isolated",
          resolve_session_mode({}) == "isolated")
    check("explicit session honored",
          resolve_session_mode({"session": "main"}) == "main")
    check("named mode honored",
          resolve_session_mode({"session": "named:weekly"}) == "named:weekly")
    check("legacy session_name -> named:<x> back-compat",
          resolve_session_mode({"session_name": "reports"}) == "named:reports")
    check("explicit session wins over legacy session_name",
          resolve_session_mode({"session": "isolated", "session_name": "reports"})
          == "isolated")


# ── 2. backend_pin_mismatch (pure) ──────────────────────────

def test_backend_pin_mismatch():
    print("\n[2] backend_pin_mismatch — one-time note gating")
    check("no pinned backend -> never mismatches (pre-seam schedules)",
          backend_pin_mismatch({}, "agy", "modelX") is False)
    check("same backend+model -> no mismatch",
          backend_pin_mismatch({"backend": "agy", "model": "m"}, "agy", "m") is False)
    check("different backend -> mismatch",
          backend_pin_mismatch({"backend": "claude", "model": "m"}, "agy", "m") is True)
    check("different model, same backend -> mismatch",
          backend_pin_mismatch({"backend": "agy", "model": "old"}, "agy", "new") is True)
    check("already notified -> suppressed",
          backend_pin_mismatch(
              {"backend": "claude", "model": "m", "backend_pin_notified": True},
              "agy", "m") is False)
    check("pinned model None (legacy) -> backend match only, no mismatch",
          backend_pin_mismatch({"backend": "agy", "model": None}, "agy", "anything")
          is False)


# ── 3. ScheduleManager.add — payload_type gating, defaults ──

def test_add_payload_gating():
    print("\n[3] ScheduleManager.add — payload_type + owner-only 'command'")
    schedules = ScheduleManager(os.path.join(_tmpdir, "schedules_add.json"))
    s = schedules.add(OWNER, OWNER, "hello", "daily", {"hh": 9, "mm": 0})
    check("default payload_type is 'message'", s["payload_type"] == "message")
    check("default session is 'isolated'", s["session"] == "isolated")
    check("backend/model default to None (unpinned)",
          s["backend"] is None and s["model"] is None)
    check("backend_pin_notified starts False", s["backend_pin_notified"] is False)

    ok_owner = schedules.add(OWNER, OWNER, "rm -rf /tmp/x", "daily", {"hh": 9, "mm": 0},
                              payload_type="command", is_owner=True)
    check("owner CAN create a command schedule", ok_owner is not None)

    denied = schedules.add(222, 222, "rm -rf /tmp/x", "daily", {"hh": 9, "mm": 0},
                            payload_type="command", is_owner=False)
    check("non-owner CANNOT create a command schedule (enforced in code)",
          denied is None)

    bad_type = schedules.add(OWNER, OWNER, "x", "daily", {"hh": 9, "mm": 0},
                              payload_type="not_a_real_type")
    check("unknown payload_type refused", bad_type is None)

    pinned = schedules.add(OWNER, OWNER, "x", "daily", {"hh": 9, "mm": 0},
                            backend="agy", model="Gemini 3.1 Pro (High)")
    check("backend/model pin stored", pinned["backend"] == "agy"
          and pinned["model"] == "Gemini 3.1 Pro (High)")


# ── 4. mark_failure ladder (already covered in test_fixes.py; a light
#      cross-check here that RETRY_LADDER is what the seam promised) ──

def test_retry_ladder_shape():
    print("\n[4] RETRY_LADDER — 30s/60s/5m/15m/60m, ascending")
    check("ladder is the 5 documented rungs",
          RETRY_LADDER == (30, 60, 300, 900, 3600), f"{RETRY_LADDER}")


# ── 5. is_scheduled_run recursion guard ─────────────────────

def test_recursion_guard_flag():
    print("\n[5] is_scheduled_run — recursion guard flag")
    core, schedules, auth = _fresh("guard")
    check("not scheduled initially", core.is_scheduled_run(OWNER) is False)
    core._scheduled_running.add(OWNER)
    check("flagged while 'running'", core.is_scheduled_run(OWNER) is True)
    core._scheduled_running.discard(OWNER)
    check("cleared after discard", core.is_scheduled_run(OWNER) is False)


# ── 6. system_event payload — zero CLI invocation ───────────

def test_system_event_zero_cli():
    print("\n[6] system_event payload — delivers text verbatim, ZERO CLI call")
    core, schedules, auth = _fresh("sysevent")
    called = {"n": 0}

    async def fake_run(*a, **kw):
        called["n"] += 1
        return "SHOULD NOT BE CALLED", None

    s = schedules.add(OWNER, OWNER, "Reminder: take a break", "once",
                       {"run_at": time.time() - 1}, payload_type="system_event",
                       now=time.time() - 10)
    # force it due regardless of the "once already past" refusal path in add()
    if s is None:
        s = {"id": "se1", "user_id": OWNER, "chat_id": OWNER, "prompt":
             "Reminder: take a break", "title": "Reminder", "payload_type":
             "system_event", "session": "isolated", "backend": None, "model": None,
             "backend_pin_notified": False}
        schedules.schedules["se1"] = s

    async def run():
        with _patched(fake_run):
            return await core._execute_schedule(s)

    ok, response, detail, meta = asyncio.run(run())
    check("system_event succeeds", ok is True, f"{ok} {detail}")
    check("response is the stored text verbatim", response == "Reminder: take a break")
    check("zero CLI invocations", called["n"] == 0)
    check("meta has no conv_id (no CLI conversation)", meta.get("conv_id") is None)


# ── 7. command payload — subprocess, no model call ──────────

def test_command_payload_subprocess():
    print("\n[7] command payload — runs via subprocess, NO model call")
    core, schedules, auth = _fresh("cmdpayload")
    called = {"n": 0}

    async def fake_run(*a, **kw):
        called["n"] += 1
        return "SHOULD NOT BE CALLED", None

    s = {"id": "c1", "user_id": OWNER, "chat_id": OWNER,
         "prompt": "echo hello-from-command-payload",
         "title": "echo test", "payload_type": "command", "session": "isolated",
         "backend": None, "model": None, "backend_pin_notified": False}
    schedules.schedules["c1"] = s

    async def run():
        with _patched(fake_run):
            return await core._execute_schedule(s)

    ok, response, detail, meta = asyncio.run(run())
    check("command succeeds", ok is True, f"{ok} {detail}")
    check("output captured", "hello-from-command-payload" in response, f"{response!r}")
    check("zero CLI invocations", called["n"] == 0)

    # failing command
    s2 = {"id": "c2", "user_id": OWNER, "chat_id": OWNER,
          "prompt": "exit 7", "title": "fail test", "payload_type": "command",
          "session": "isolated", "backend": None, "model": None,
          "backend_pin_notified": False}
    schedules.schedules["c2"] = s2

    async def run2():
        with _patched(fake_run):
            return await core._execute_schedule(s2)

    ok2, response2, detail2, meta2 = asyncio.run(run2())
    check("nonzero exit classified as failure", ok2 is False)


# ── 8. message payload — ScheduledResult broadcast, session/conv_id carried ──

def test_message_payload_broadcast():
    print("\n[8] message payload — ScheduledResult broadcast via subscribe()")
    core, schedules, auth = _fresh("bcast")
    sink = asyncio.Queue()
    core.subscribe(sink)

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        return "the scheduled answer", "conv-sched-1"

    s = schedules.add(OWNER, OWNER, "do the thing", "interval", {"seconds": 60},
                       title="My Job")

    async def run():
        with _patched(fake_run):
            await core._run_and_record(s)
        return await _drain(sink, 1)

    events = asyncio.run(run())
    check("exactly one event broadcast", len(events) == 1, f"{events}")
    ev = events[0]
    check("event is a ScheduledResult", isinstance(ev, ScheduledResult))
    check("title carried", ev.title == "My Job")
    check("response carried", ev.response == "the scheduled answer")
    check("chat_id/user_id carried", ev.chat_id == OWNER and ev.user_id == OWNER)
    check("no warning on success", ev.warning == "")
    check("recursion guard cleared after run",
          core.is_scheduled_run(OWNER) is False)


# ── 9. give-up path — warning + schedule still fires next occurrence ────

def test_giveup_path_keeps_firing():
    print("\n[9] give-up after ladder — warning event, schedule NOT disabled")
    core, schedules, auth = _fresh("giveup")
    sink = asyncio.Queue()
    core.subscribe(sink)

    async def always_fail(prompt, conv_id, progress_callback=None,
                          cancel_event=None, skip_permissions=False):
        raise RuntimeError("boom")

    s = schedules.add(OWNER, OWNER, "flaky job", "daily", {"hh": 9, "mm": 0},
                       title="Flaky")
    sid = s["id"]

    async def run():
        with _patched(always_fail):
            for _ in range(len(RETRY_LADDER) + 1):  # exhaust the ladder
                await core._run_and_record(schedules.get(sid))
        return await _drain(sink, 1)

    events = asyncio.run(run())
    check("exactly one give-up event broadcast", len(events) == 1, f"{events}")
    ev = events[0]
    check("give-up event carries a warning", bool(ev.warning), f"{ev.warning!r}")
    s_after = schedules.get(sid)
    check("still enabled (never permanently disabled)", s_after["enabled"] is True)
    check("advanced to a real future next_run",
          s_after["next_run"] is not None and s_after["next_run"] > time.time())
    check("fail_count reset after giving up", s_after["fail_count"] == 0)


# ── 10. backend pin mismatch -> one-time Alert ──────────────

def test_backend_pin_alert_once():
    print("\n[10] backend/model pin mismatch — one-time Alert, run still proceeds")
    core, schedules, auth = _fresh("pinalert")
    sink = asyncio.Queue()
    core.subscribe(sink)

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        return "ran anyway", "conv-x"

    s = schedules.add(OWNER, OWNER, "job", "interval", {"seconds": 60},
                       title="Pinned Job", backend="claude", model="opus")
    # current backend/model (from isolated config) differs from the pin
    check("current backend differs from pin (fixture sanity)",
          config.get_backend() != "claude")

    async def run():
        with _patched(fake_run):
            await core._run_and_record(s)
        return await _drain(sink, 2)

    events = asyncio.run(run())
    alerts = [e for e in events if isinstance(e, Alert)]
    results = [e for e in events if isinstance(e, ScheduledResult)]
    check("one Alert broadcast", len(alerts) == 1, f"{events}")
    check("run still completed (current backend used, not blocked)",
          len(results) == 1 and results[0].response == "ran anyway")
    check("owner marked notified (no repeat)", schedules.get(s["id"])["backend_pin_notified"])

    # Second run: no further Alert.
    async def run2():
        with _patched(fake_run):
            await core._run_and_record(schedules.get(s["id"]))
        return await _drain(sink, 1)

    events2 = asyncio.run(run2())
    alerts2 = [e for e in events2 if isinstance(e, Alert)]
    check("no repeat Alert on second fire", len(alerts2) == 0, f"{events2}")


# ── 11. run_schedule_now — manual trigger never advances the schedule ──

def test_run_schedule_now_never_advances():
    print("\n[11] run_schedule_now — broadcasts but does NOT touch_run")
    core, schedules, auth = _fresh("runnow")
    sink = asyncio.Queue()
    core.subscribe(sink)

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False):
        return "manual answer", "conv-y"

    s = schedules.add(OWNER, OWNER, "job", "daily", {"hh": 9, "mm": 0}, title="Manual")
    before = schedules.get(s["id"])["next_run"]

    async def run():
        with _patched(fake_run):
            await core.run_schedule_now(s["id"])
        return await _drain(sink, 1)

    events = asyncio.run(run())
    check("broadcast happened", len(events) == 1 and isinstance(events[0], ScheduledResult))
    after = schedules.get(s["id"])["next_run"]
    check("next_run unchanged (manual run never advances)", before == after,
          f"{before} vs {after}")


# ── 12. deauthorized owner -> schedule disabled, no run ─────

def test_deauthorized_user_disabled():
    print("\n[12] deauthorized schedule owner -> disabled, never runs")
    core, schedules, auth = _fresh("deauth")
    called = {"n": 0}

    async def fake_run(*a, **kw):
        called["n"] += 1
        return "should not run", None

    s = schedules.add(555, 555, "job", "daily", {"hh": 9, "mm": 0}, title="Ghost")

    async def run():
        with _patched(fake_run):
            return await core._execute_schedule(s)

    ok, response, detail, meta = asyncio.run(run())
    check("execution refused", ok is False)
    check("zero CLI invocations", called["n"] == 0)
    check("schedule disabled as a side effect", schedules.get(s["id"])["enabled"] is False)


# ── 13. core.start()/stop() lifecycle with a real ScheduleManager ──

def test_start_stop_lifecycle():
    print("\n[13] core.start()/stop() — scheduler task starts and stops cleanly")
    core, schedules, auth = _fresh("lifecycle")

    async def run():
        await core.start()
        check("sched task created", core._sched_task is not None)
        await asyncio.sleep(0.05)
        check("sched task alive", not core._sched_task.done())
        await core.stop()
        check("sched task cleared after stop", core._sched_task is None)

    asyncio.run(run())


def test_start_stop_noop_without_schedules():
    print("\n[13b] core.start()/stop() — no-op when built without a ScheduleManager")
    sessions = SessionManager(os.path.join(_tmpdir, "sessions_noop.json"))
    auth = AuthManager(os.path.join(_tmpdir, "users_noop.json"), OWNER)
    core = ZillaCore(sessions=sessions, auth=auth)  # schedules=None (default)

    async def run():
        await core.start()
        check("no task created without a ScheduleManager", core._sched_task is None)
        await core.stop()  # must not raise

    asyncio.run(run())


def main():
    tests = [
        test_resolve_session_mode,
        test_backend_pin_mismatch,
        test_add_payload_gating,
        test_retry_ladder_shape,
        test_recursion_guard_flag,
        test_system_event_zero_cli,
        test_command_payload_subprocess,
        test_message_payload_broadcast,
        test_giveup_path_keeps_firing,
        test_backend_pin_alert_once,
        test_run_schedule_now_never_advances,
        test_deauthorized_user_disabled,
        test_start_stop_lifecycle,
        test_start_stop_noop_without_schedules,
    ]
    print("Running scheduler-seam tests...\n")
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
