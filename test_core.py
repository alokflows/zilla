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
#  identity cleanup.
#
#  Run:  python test_core.py
#  Exit code 0 = all passed, 1 = something failed.
# ============================================================

import asyncio
import json
import os
import sys
import tempfile

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

import zilla.config as config  # noqa: E402
config.SETTINGS_FILE = os.path.join(_tmpdir, "bot_settings.json")
config._settings_cache = None

import zilla.core as zcore  # noqa: E402
from zilla.core import ZillaCore, Progress, Response  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402

OWNER = 111


def _fresh_core(tag: str) -> ZillaCore:
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.json"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.json"), OWNER)
    return ZillaCore(sessions=sessions, auth=auth)


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
            return await _collect(core, OWNER, "hi there", auto_title=True)

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


def main():
    tests = [
        test_event_sequence,
        test_session_bookkeeping,
        test_lock_serialization,
        test_cancel,
        test_error_cleanup,
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
