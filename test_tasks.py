# ============================================================
#  TESTS — Phase B1/B2: background lane + incognito (PLAN.md §9 "Accept:")
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/tasks.py: BG_TASK: marker parse/strip, the owner-facing copy
#      (title, duration, confirm card, board text).
#    - store.tasks_*: the durable row, status transitions, queue order.
#    - zilla/core.py Tasks: LOCK INDEPENDENCE (a chat turn completes while a
#      background job is mid-run for the SAME user), the max_bg_tasks cap +
#      queue, cancel of a running and of a queued task, retry, the
#      startup reconcile, the marker hold (owner-only) and the fact that a
#      task's own output can neither relay nor spawn more work.
#    - Phase B2 incognito: the session flag, injection ABSENCE (no memory
#      block, no graph card, no curiosity), CODE enforcement (a memory write
#      during an incognito turn is reverted from the memory repo and the
#      owner is told), and /end deleting the conversation directory.
#    - bot.py: /bg, /tasks, the confirm/stop/retry taps, result delivery,
#      /new incognito.
#
#  Run:  .venv/bin/python test_tasks.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Config is isolated to a tmpdir BEFORE zilla is imported, and every test
#  points zilla.memory.MEMORY_DIR / zilla.config.MEMORY_DIR / BRAIN_DIR at
#  throwaway directories, so a run never touches the real ~/Zilla runtime.
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
_tmpdir = tempfile.mkdtemp(prefix="zilla_b_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.core as zcore  # noqa: E402
import zilla.memory as memory  # noqa: E402
from zilla import store as _store  # noqa: E402
from zilla import tasks as ztasks  # noqa: E402
from zilla.core import TaskProposal, TaskResult, Response, Alert, ZillaCore  # noqa: E402
from zilla.harness import TurnContext  # noqa: E402
from zilla.schedules import ScheduleManager  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402

OWNER = 111
NON_OWNER = 999


# ══════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════

class _CollectingQueue:
    """Stands in for the asyncio.Queue a real frontend subscribes with —
    _broadcast() only ever calls put_nowait()."""

    def __init__(self, sink):
        self._sink = sink

    def put_nowait(self, ev):
        self._sink.append(ev)


def _iso(tag: str):
    """A throwaway Memory/ tree and a clean tasks table."""
    tmp = tempfile.mkdtemp(prefix=f"zilla_b_{tag}_")
    olds = (memory.MEMORY_DIR, config.MEMORY_DIR)
    memory.MEMORY_DIR = config.MEMORY_DIR = os.path.join(tmp, "Memory")
    db = _store.get_store(config.DB_FILE)
    db._write(lambda conn: conn.execute("DELETE FROM tasks"))
    return tmp, olds, db


def _restore(tmp, olds):
    memory.MEMORY_DIR, config.MEMORY_DIR = olds
    shutil.rmtree(tmp, ignore_errors=True)


def _fresh_core(tag: str, subscribe=True):
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.db"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.db"), OWNER)
    sched = ScheduleManager(os.path.join(_tmpdir, f"schedules_{tag}.db"))
    core = ZillaCore(sessions=sessions, auth=auth, schedules=sched)
    events = []
    if subscribe:
        core._subscribers.append(_CollectingQueue(events))
    return core, events


def _ctx(uid=OWNER, is_owner=True, incognito=False):
    return TurnContext(uid=uid, role="owner" if is_owner else "admin",
                       is_owner=is_owner, incognito=incognito)


class _Gate:
    """A loop-agnostic asyncio.Event. A real Event binds to the loop it is
    first awaited on, and these tests call asyncio.run() several times per
    scenario, so the gate has to survive a loop swap."""

    def __init__(self):
        self.open = False

    def set(self):
        self.open = True

    async def wait(self, timeout=5.0):
        end = time.monotonic() + timeout
        while not self.open and time.monotonic() < end:
            await asyncio.sleep(0)


def _fake_runner(answers: dict, block: dict = None):
    """A stand-in for cli_engine.run_cli_async. `answers` maps a substring of
    the prompt to the response text; `block` maps a substring to a _Gate the
    run waits on, so a test can hold a job mid-flight."""
    async def _run(prompt, conv_id=None, progress_callback=None, cancel_event=None,
                   skip_permissions=False, ctx=None):
        _run.calls.append({"prompt": prompt, "conv_id": conv_id, "ctx": ctx})
        for needle, gate in (block or {}).items():
            if needle in prompt:
                await gate.wait()
        for needle, answer in answers.items():
            if needle in prompt:
                return answer, f"conv-{len(_run.calls)}"
        return "Done.", f"conv-{len(_run.calls)}"
    _run.calls = []
    return _run


# ============================================================
#  1. tasks.parse_markers — detect, strip, never leak
# ============================================================

def _run_parse_tests():
    print("\n[1] tasks.parse_markers — detected, always stripped, capped")

    clean, prompts = ztasks.parse_markers(
        "I'll dig into that.\n\nBG_TASK: research three rice suppliers in Kerala")
    check("marker parsed", prompts == ["research three rice suppliers in Kerala"], prompts)
    check("marker never survives into the owner-facing text",
          "BG_TASK" not in clean and clean == "I'll dig into that.", repr(clean))

    clean, prompts = ztasks.parse_markers("no markers here")
    check("a plain reply is returned untouched",
          clean == "no markers here" and prompts == [])

    clean, prompts = ztasks.parse_markers("ok\nBG_TASK:   ")
    check("an empty payload is dropped, not proposed", prompts == [], prompts)
    check("an empty marker line is still stripped", "BG_TASK" not in clean, repr(clean))

    many = "ok\n" + "\n".join(f"BG_TASK: job {i}" for i in range(6))
    clean, prompts = ztasks.parse_markers(many)
    check(f"no more than MAX_PROPOSALS ({ztasks.MAX_PROPOSALS}) are taken",
          len(prompts) == ztasks.MAX_PROPOSALS, prompts)
    check("every marker line is stripped even past the cap",
          "BG_TASK" not in clean, repr(clean))

    check("None/'' never raise", ztasks.parse_markers("") == ("", [])
          and ztasks.parse_markers(None) == (None, []))

    long_prompt = "x" * (ztasks.MAX_PROMPT + 500)
    _, prompts = ztasks.parse_markers(f"BG_TASK: {long_prompt}")
    check("an absurdly long prompt is capped, not rejected",
          len(prompts[0]) == ztasks.MAX_PROMPT, len(prompts[0]))


# ============================================================
#  2. Owner-facing copy
# ============================================================

def _run_copy_tests():
    print("\n[2] tasks copy — a title, a spelled-out duration, calm failure")

    check("title is the prompt, trimmed to something readable",
          ztasks.title_for("compare three suppliers") == "compare three suppliers")
    long_title = ztasks.title_for("word " * 40)
    check("a long prompt becomes a short title",
          len(long_title) <= ztasks.MAX_TITLE + 1 and long_title.endswith("…"), long_title)
    check("an empty prompt still gets a name",
          ztasks.title_for("") == "Background job")

    check("seconds", ztasks.format_duration(45) == "45 sec")
    check("minutes and seconds", ztasks.format_duration(125) == "2 min 5 sec")
    check("whole minutes drop the seconds", ztasks.format_duration(120) == "2 min")
    check("hours", ztasks.format_duration(3600 * 2 + 60) == "2 hr 1 min")
    check("an unknown duration says so", ztasks.format_duration(None) == "unknown")

    card = ztasks.confirm_card("count the stock")
    check("the confirm card shows the exact prompt that would run",
          "count the stock" in card, card)
    check("the confirm card says the chat stays free", "free" in card, card)

    check("queued and started read differently",
          "Queued" in ztasks.started_line("job", True)
          and "Started" in ztasks.started_line("job", False))

    fail = ztasks.failure_line({"title": "Stock count"})
    check("failure is one calm sentence with one action",
          "Stock count" in fail and "retry" in fail.lower() and "!" not in fail, fail)

    from zilla import zui as _zui
    block = _zui.validate(ztasks.result_card({"title": "Stock count"}, 125))
    check("the result header is a valid ZUI card",
          block is not None and block["kind"] == "card", block)
    check("the header carries what ran and how long it took",
          "Stock count" in block["title"] and "2 min 5 sec" in block["subtitle"], block)


def _run_board_tests():
    print("\n[3] tasks.board_text — running with progress, waiting, finished")

    empty = ztasks.board_text([], [], [])
    check("an empty board explains how to start one", "/bg" in empty, empty)

    running = [{"id": "a", "title": "Supplier研究", "status": ztasks.RUNNING,
                "progress": "Reading page 3"}]
    queued = [{"id": "b", "title": "Stock count", "status": ztasks.QUEUED}]
    finished = [{"id": "c", "title": "Old job", "status": ztasks.DONE},
                {"id": "d", "title": "Broken job", "status": ztasks.FAILED}]
    text = ztasks.board_text(running, queued, finished)
    check("the running job shows its live progress line",
          "Reading page 3" in text, text)
    check("queued work is listed separately", "Waiting" in text and "Stock count" in text, text)
    check("finished jobs carry an outcome glyph", "✓ Old job" in text and "⚠ Broken job" in text, text)
    check("exactly one bold title line", text.count("<b>") == 1, text)
    check("no double blank lines (STYLE R10)", "\n\n\n" not in text, repr(text))

    lots = [{"id": str(i), "title": f"job {i}", "status": ztasks.DONE} for i in range(12)]
    text2 = ztasks.board_text([], [], lots)
    check(f"the finished list is capped at {ztasks.BOARD_FINISHED}",
          text2.count("job ") == ztasks.BOARD_FINISHED, text2)


# ============================================================
#  4. store.tasks_* — the durable row
# ============================================================

def _run_store_tests():
    print("\n[4] store.tasks_* — durable rows, queue order, status filters")
    tmp, olds, db = _iso("store")
    try:
        db.tasks_add(tid="t1", uid=OWNER, chat_id=5, prompt="one", title="one",
                     status=ztasks.QUEUED, created_at="2026-08-14 10:00:00")
        db.tasks_add(tid="t2", uid=OWNER, chat_id=5, prompt="two", title="two",
                     status=ztasks.QUEUED, created_at="2026-08-14 10:01:00")
        row = db.tasks_get("t1")
        check("a task round-trips", row and row["prompt"] == "one"
              and row["status"] == ztasks.QUEUED, row)
        check("an unknown id is None, not an error", db.tasks_get("nope") is None)

        order = [r["id"] for r in db.tasks_by_status((ztasks.QUEUED,))]
        check("queued tasks come back in queue order (oldest first)",
              order == ["t1", "t2"], order)

        db.tasks_update("t1", status=ztasks.RUNNING, progress="working")
        check("status and progress update in place",
              db.tasks_get("t1")["progress"] == "working"
              and db.tasks_get("t1")["status"] == ztasks.RUNNING)
        check("the running count is what the cap reads",
              db.tasks_count_by_status((ztasks.RUNNING,)) == 1)

        db.tasks_update("t1", status=ztasks.DONE, finished_at="2026-08-14 10:05:00")
        newest = db.tasks_by_status(ztasks.TERMINAL_STATUSES, newest_first=True, limit=1)
        check("finished tasks come back newest first", [r["id"] for r in newest] == ["t1"])

        bad = False
        try:
            db.tasks_update("t1", uid=42)
        except ValueError:
            bad = True
        check("an unknown column is rejected, never silently written", bad)

        db.tasks_add(tid="t3", uid=NON_OWNER, chat_id=9, prompt="theirs", title="theirs",
                     status=ztasks.QUEUED, created_at="2026-08-14 10:02:00")
        mine = [r["id"] for r in db.tasks_by_status((ztasks.QUEUED,), uid=OWNER)]
        check("a per-user view only shows that user's jobs", mine == ["t2"], mine)
    finally:
        _restore(tmp, olds)


# ============================================================
#  5. THE ACCEPT CRITERION — lock independence
# ============================================================

def _run_lock_independence_test():
    print("\n[5] lock independence — the chat answers while a job is mid-run")
    tmp, olds, db = _iso("lock")
    core, events = _fresh_core("lock")
    gate = _Gate()
    original = zcore.run_cli_async
    zcore.run_cli_async = _fake_runner(
        {"slow job": "the long answer", "quick question": "the quick answer"},
        block={"slow job": gate})
    try:
        async def _scenario():
            row = await core.tasks.submit(OWNER, 5, "slow job")
            await asyncio.sleep(0)  # let the runner reach its gate
            running_before = db.tasks_get(row["id"])["status"]

            # THE test: the same uid sends a chat message while the
            # background job is still executing. It must NOT wait for it.
            chat = []
            async def _chat():
                async for ev in core.handle_message(OWNER, "quick question"):
                    if isinstance(ev, Response):
                        chat.append(ev)
            await asyncio.wait_for(_chat(), timeout=5)

            still_running = db.tasks_get(row["id"])["status"]
            gate.set()
            for _ in range(50):
                await asyncio.sleep(0)
                if db.tasks_get(row["id"])["status"] == ztasks.DONE:
                    break
            return running_before, chat, still_running, db.tasks_get(row["id"])

        before, chat, during, final = asyncio.run(_scenario())
        check("the job is running as soon as it is submitted",
              before == ztasks.RUNNING, before)
        check("the chat turn completed while the job was still running",
              len(chat) == 1 and chat[0].text == "the quick answer"
              and during == ztasks.RUNNING, (chat, during))
        check("the background job finishes on its own afterwards",
              final["status"] == ztasks.DONE, final)
        check("the result is stored on the row",
              final["result"] == "the long answer", final)
        check("a TaskResult was broadcast for the finished job",
              any(isinstance(e, TaskResult) and e.status == ztasks.DONE
                  for e in events), events)
        check("the per-user chat lock is free once the chat turn is done",
              not core.get_user_lock(OWNER).locked())
    finally:
        zcore.run_cli_async = original
        _restore(tmp, olds)


def _run_session_isolation_test():
    print("\n[6] a job runs in its OWN session, never the owner's active one")
    tmp, olds, db = _iso("sess")
    core, _ = _fresh_core("sess")
    original = zcore.run_cli_async
    runner = _fake_runner({"research": "answer"})
    zcore.run_cli_async = runner
    try:
        async def _scenario():
            core.sessions.set_active_name("main", OWNER)
            core.sessions.set_conversation_id("chat-conv", user_id=OWNER,
                                              session_name="main", backend="agy")
            row = await core.tasks.submit(OWNER, 5, "research the market")
            for _ in range(200):
                await asyncio.sleep(0)
                if db.tasks_get(row["id"])["status"] != ztasks.RUNNING:
                    break
            return row["id"]

        tid = asyncio.run(_scenario())
        check("the job started a FRESH conversation, not the chat's",
              runner.calls and runner.calls[0]["conv_id"] is None, runner.calls)
        check("the owner's own session is untouched",
              core.sessions.get_conversation_id(user_id=OWNER, session_name="main")
              == "chat-conv")
        check("the task's session row is released so H1's sweep can reclaim it",
              f"task:{tid}" not in core.sessions.list_sessions(OWNER),
              list(core.sessions.list_sessions(OWNER)))
        ctx = runner.calls[0]["ctx"]
        check("the job carries a task-origin TurnContext for the owner",
              ctx is not None and ctx.origin == "task" and ctx.is_owner, ctx)
    finally:
        zcore.run_cli_async = original
        _restore(tmp, olds)


# ============================================================
#  7. Cap + queue
# ============================================================

def _run_cap_tests():
    print("\n[7] max_bg_tasks — over the cap, work queues instead of piling on")
    tmp, olds, db = _iso("cap")
    core, _ = _fresh_core("cap")
    gate = _Gate()
    original = zcore.run_cli_async
    zcore.run_cli_async = _fake_runner({}, block={"job": gate})
    config.set_setting("max_bg_tasks", 2)
    try:
        async def _scenario():
            rows = [await core.tasks.submit(OWNER, 5, f"job {i}") for i in range(3)]
            await asyncio.sleep(0)
            statuses = [db.tasks_get(r["id"])["status"] for r in rows]
            queued_flag = rows[2]["queued"]
            gate.set()
            for _ in range(400):
                await asyncio.sleep(0)
                if all(db.tasks_get(r["id"])["status"] == ztasks.DONE for r in rows):
                    break
            return statuses, queued_flag, [db.tasks_get(r["id"])["status"] for r in rows]

        statuses, queued_flag, after = asyncio.run(_scenario())
        check("exactly max_bg_tasks jobs run at once",
              statuses.count(ztasks.RUNNING) == 2, statuses)
        check("the third one waits its turn",
              statuses[2] == ztasks.QUEUED and queued_flag is True, statuses)
        check("the queued job starts once a lane frees up",
              after == [ztasks.DONE] * 3, after)

        check("the cap setting is read live", core.tasks.max_concurrent() == 2)
        config.set_setting("max_bg_tasks", 0)
        check("a nonsense cap of 0 floors at 1 instead of stalling the lane",
              core.tasks.max_concurrent() == 1)
        config.set_setting("max_bg_tasks", "two")
        check("an unparseable cap falls back to the default",
              core.tasks.max_concurrent() == ztasks.DEFAULT_MAX_BG_TASKS)
    finally:
        config.set_setting("max_bg_tasks", None)
        zcore.run_cli_async = original
        _restore(tmp, olds)


def _run_backlog_cap_test():
    print("\n[8] the backlog itself is bounded")
    tmp, olds, db = _iso("backlog")
    core, _ = _fresh_core("backlog")
    gate = _Gate()
    original = zcore.run_cli_async
    zcore.run_cli_async = _fake_runner({}, block={"job": gate})
    try:
        async def _scenario():
            rows = [await core.tasks.submit(OWNER, 5, f"job {i}")
                    for i in range(ztasks.MAX_PENDING)]
            over = await core.tasks.submit(OWNER, 5, "job over the line")
            empty = await core.tasks.submit(OWNER, 5, "   ")
            gate.set()
            return rows, over, empty

        rows, over, empty = asyncio.run(_scenario())
        check(f"the lane accepts up to MAX_PENDING ({ztasks.MAX_PENDING}) live jobs",
              all(rows) and len(rows) == ztasks.MAX_PENDING)
        check("past that, submit refuses instead of growing the table", over is None)
        check("an empty prompt is never a task", empty is None)
    finally:
        zcore.run_cli_async = original
        _restore(tmp, olds)


# ============================================================
#  9. Cancel
# ============================================================

def _run_cancel_tests():
    print("\n[9] cancel — a running job stops, a queued job never starts")
    tmp, olds, db = _iso("cancel")
    core, events = _fresh_core("cancel")
    gate = _Gate()
    seen = {}
    original = zcore.run_cli_async

    async def _run(prompt, conv_id=None, progress_callback=None, cancel_event=None,
                   skip_permissions=False, ctx=None):
        seen["cancel_event"] = cancel_event
        if progress_callback:
            progress_callback("Reading the first source")
        await gate.wait()
        return ("🛑 Canceled." if cancel_event.is_set() else "finished"), "conv-x"

    zcore.run_cli_async = _run
    config.set_setting("max_bg_tasks", 1)
    try:
        async def _scenario():
            running = await core.tasks.submit(OWNER, 5, "long job")
            queued = await core.tasks.submit(OWNER, 5, "second job")
            await asyncio.sleep(0)
            progress_row = db.tasks_get(running["id"])
            cancelled_running = core.tasks.cancel(running["id"])
            event_set = seen["cancel_event"].is_set()
            cancelled_queued = core.tasks.cancel(queued["id"])
            gate.set()
            for _ in range(400):
                await asyncio.sleep(0)
                if db.tasks_get(running["id"])["status"] in ztasks.TERMINAL_STATUSES:
                    break
            return (running["id"], queued["id"], progress_row, cancelled_running,
                    event_set, cancelled_queued)

        rid, qid, progress_row, cr, event_set, cq = asyncio.run(_scenario())
        check("a running job records its latest progress line",
              progress_row["progress"] == "Reading the first source", progress_row)
        check("cancelling a running job sets its cancel event (I-CANCEL)",
              cr is not None and event_set)
        check("the canceled job ends up canceled, not done",
              db.tasks_get(rid)["status"] == ztasks.CANCELED, db.tasks_get(rid))
        check("cancelling a queued job stops it before it ever starts",
              cq is not None and db.tasks_get(qid)["status"] == ztasks.CANCELED,
              db.tasks_get(qid))
        check("a canceled job is never started later",
              db.tasks_count_by_status((ztasks.RUNNING, ztasks.QUEUED)) == 0)
        check("cancelling a finished job is a no-op, not an error",
              core.tasks.cancel(rid) is None)
        check("cancelling an unknown id is a no-op", core.tasks.cancel("nope") is None)
        check("a canceled job broadcasts its outcome",
              any(isinstance(e, TaskResult) and e.status == ztasks.CANCELED
                  for e in events), events)
    finally:
        config.set_setting("max_bg_tasks", None)
        zcore.run_cli_async = original
        _restore(tmp, olds)


def _run_failure_and_retry_tests():
    print("\n[10] failure classification, retry, and the startup reconcile")
    tmp, olds, db = _iso("fail")
    core, events = _fresh_core("fail")
    original = zcore.run_cli_async
    zcore.run_cli_async = _fake_runner({"broken": "", "boom": "Error: it broke",
                                        "fine": "all good"})
    try:
        async def _drain(prompt):
            row = await core.tasks.submit(OWNER, 5, prompt)
            for _ in range(400):
                await asyncio.sleep(0)
                if db.tasks_get(row["id"])["status"] in ztasks.TERMINAL_STATUSES:
                    break
            return db.tasks_get(row["id"])

        empty = asyncio.run(_drain("broken run"))
        check("an empty answer is a failure, not a silent success",
              empty["status"] == ztasks.FAILED, empty)
        errored = asyncio.run(_drain("boom run"))
        check("an error-shaped answer is a failure",
              errored["status"] == ztasks.FAILED, errored)
        good = asyncio.run(_drain("fine run"))
        check("a real answer is done", good["status"] == ztasks.DONE, good)

        check("a failed job broadcasts a failure the frontend can offer a retry on",
              any(isinstance(e, TaskResult) and e.status == ztasks.FAILED for e in events))

        again = asyncio.run(core.tasks.retry(good["id"]))
        check("retry creates a NEW job with the same prompt",
              again is not None and again["id"] != good["id"]
              and again["prompt"] == good["prompt"], again)
        check("the original row survives as history", db.tasks_get(good["id"]) is not None)
        check("retrying an unknown id is a no-op",
              asyncio.run(core.tasks.retry("nope")) is None)

        usage = db.usage_for_day(time.strftime("%Y-%m-%d"))
        check("background runs count towards usage",
              any(u["turns"] for u in usage), usage)

        # A row a dead process left `running` must not hold a lane forever.
        db.tasks_add(tid="ghost", uid=OWNER, chat_id=5, prompt="p", title="p",
                     status=ztasks.RUNNING, created_at="2026-08-14 09:00:00")
        n = core.tasks.reconcile_startup()
        check("a job orphaned by a restart is failed, never resurrected",
              n >= 1 and db.tasks_get("ghost")["status"] == ztasks.FAILED)
    finally:
        zcore.run_cli_async = original
        _restore(tmp, olds)


# ============================================================
#  11. The marker path — propose, gate, confirm
# ============================================================

def _run_marker_hold_tests():
    print("\n[11] core._process_bg_markers — owner-only hold, nothing runs without a tap")
    tmp, olds, db = _iso("marker")
    core, events = _fresh_core("marker")
    original = zcore.run_cli_async
    zcore.run_cli_async = _fake_runner({"": "done"})
    try:
        out = core._process_bg_markers(
            "That'll take a while.\n\nBG_TASK: compare every supplier", _ctx(), 5)
        check("owner turn: the marker is stripped", "BG_TASK" not in out, out)
        check("owner turn: the reply text survives", out == "That'll take a while.", repr(out))
        check("owner turn: one TaskProposal broadcast",
              len(events) == 1 and isinstance(events[0], TaskProposal), events)
        check("the proposal card shows the exact prompt",
              "compare every supplier" in events[0].card, events[0].card)
        check("NOTHING is queued before the tap",
              db.tasks_count_by_status(ztasks.LIVE_STATUSES) == 0)

        # The tap.
        row = asyncio.run(core.tasks.accept(events[0].id))
        check("the tap creates the task", row is not None
              and db.tasks_get(row["id"]) is not None, row)
        check("a second tap on the same offer does nothing (double-tap)",
              asyncio.run(core.tasks.accept(events[0].id)) is None)

        # Decline.
        core2, events2 = _fresh_core("marker_no")
        core2._process_bg_markers("ok\nBG_TASK: something", _ctx(), 5)
        pid = events2[0].id
        check("decline pops the proposal", core2.tasks.decline(pid) is not None)
        check("declining twice is a no-op", core2.tasks.decline(pid) is None)
        check("a declined proposal never became a task",
              asyncio.run(core2.tasks.accept(pid)) is None)

        # Injection guard.
        core3, events3 = _fresh_core("marker_nonowner")
        out3 = core3._process_bg_markers("ok\nBG_TASK: wipe the disk",
                                         _ctx(NON_OWNER, is_owner=False), 5)
        check("non-owner turn: the marker is stripped", "BG_TASK" not in out3, out3)
        check("non-owner turn: nothing is proposed", events3 == [], events3)
        out4 = core3._process_bg_markers("ok\nBG_TASK: hi", None, 5)
        check("ctx=None (unknown principal): stripped, nothing proposed",
              "BG_TASK" not in out4 and core3.tasks.pending() == [])

        # The proposal queue is bounded and expires.
        core4, events4 = _fresh_core("marker_cap")
        for _ in range(zcore.BG_PROPOSAL_MAX):
            core4.tasks.propose("job", OWNER, 5)
        check(f"proposals fill to BG_PROPOSAL_MAX ({zcore.BG_PROPOSAL_MAX})",
              len(core4.tasks.pending()) == zcore.BG_PROPOSAL_MAX)
        check("past the cap, propose returns None instead of growing unbounded",
              core4.tasks.propose("one more", OWNER, 5) is None)
        out5 = core4._process_bg_markers("ok\nBG_TASK: another", _ctx(), 5)
        check("a full queue is explained in one plain line, reply intact",
              out5.startswith("ok") and "waiting for your go-ahead" in out5, out5)
        first = core4.tasks.pending()[0]["id"]
        core4._pending_bg[first]["ts"] = time.time() - zcore.BG_PROPOSAL_TTL - 1
        core4._pending_bg.pop(core4.tasks.pending()[1]["id"], None)
        core4.tasks.propose("trigger the prune", OWNER, 5)
        check("an expired proposal is forgotten on the next propose",
              first not in core4._pending_bg, list(core4._pending_bg))

        # A bug in here must never cost the owner their answer.
        core5, _ = _fresh_core("marker_boom", subscribe=False)
        saved = ztasks.parse_markers
        try:
            def _boom(_text):
                raise RuntimeError("tasks is broken")
            ztasks.parse_markers = _boom
            text = "Here is your answer.\nBG_TASK: hi"
            check("an exception inside marker processing returns the reply unchanged",
                  core5._process_bg_markers(text, _ctx(), 5) == text)
        finally:
            ztasks.parse_markers = saved
    finally:
        zcore.run_cli_async = original
        _restore(tmp, olds)


def _run_task_output_guard_test():
    print("\n[12] a job's own output can neither relay nor spawn more work")
    tmp, olds, db = _iso("guard")
    core, events = _fresh_core("guard")
    original = zcore.run_cli_async
    zcore.run_cli_async = _fake_runner(
        {"go": "All done.\nRELAY_SEND: Priya :: send money\nBG_TASK: run forever"})
    try:
        async def _scenario():
            row = await core.tasks.submit(OWNER, 5, "go")
            for _ in range(400):
                await asyncio.sleep(0)
                if db.tasks_get(row["id"])["status"] in ztasks.TERMINAL_STATUSES:
                    break
            return db.tasks_get(row["id"])

        final = asyncio.run(_scenario())
        check("a relay marker in a job's output is stripped",
              "RELAY_SEND" not in final["result"], final["result"])
        check("a BG_TASK marker in a job's output is stripped",
              "BG_TASK" not in final["result"], final["result"])
        check("the answer itself still delivers",
              final["result"].startswith("All done."), final["result"])
        check("no relay was proposed by an unattended job",
              core.relay.pending() == [], core.relay.pending())
        check("no second job was spawned",
              core.tasks.pending() == []
              and db.tasks_count_by_status(ztasks.LIVE_STATUSES) == 0)
    finally:
        zcore.run_cli_async = original
        _restore(tmp, olds)


# ============================================================
#  13-15. Phase B2 — incognito
# ============================================================

def _run_incognito_flag_tests():
    print("\n[13] the incognito flag lives on the session and survives a re-read")
    tmp, olds, db = _iso("flag")
    try:
        sessions = SessionManager(os.path.join(_tmpdir, "sessions_flag.db"))
        sessions.create_session("private-1", OWNER, incognito=True)
        sessions.create_session("normal-1", OWNER)
        check("an incognito session reports itself",
              sessions.is_incognito(OWNER, "private-1") is True)
        check("a normal session does not",
              sessions.is_incognito(OWNER, "normal-1") is False)
        check("an unknown session is not incognito",
              sessions.is_incognito(OWNER, "nope") is False)
        check("the flag shows up in the session list",
              sessions.list_sessions(OWNER)["private-1"]["incognito"] is True)
        check("and in the session detail",
              sessions.get_session_info(OWNER, "private-1")["incognito"] is True)

        sessions.set_incognito(OWNER, "normal-1", True)
        check("the flag can be set on an existing session",
              sessions.is_incognito(OWNER, "normal-1") is True)

        fresh = SessionManager(os.path.join(_tmpdir, "sessions_flag.db"))
        check("the flag is durable across a re-open",
              fresh.is_incognito(OWNER, "private-1") is True)
    finally:
        _restore(tmp, olds)


def _run_injection_absence_tests():
    print("\n[14] incognito injects nothing — no memory, no graph, no curiosity")
    tmp, olds, db = _iso("inject")
    try:
        from zilla import graph as _graph
        from zilla import harness as _harness
        memory.ensure_tree()
        os.makedirs(os.path.join(memory.MEMORY_DIR, "Wiki", "People"), exist_ok=True)
        with open(os.path.join(memory.MEMORY_DIR, "Wiki", "People", "priya.md"),
                  "w", encoding="utf-8") as f:
            f.write("# Priya\nOperations lead.\n- type:: person\n")
        db.graph_clear()
        _graph.reindex_graph(db, memory.MEMORY_DIR)

        normal = _harness._memory_block(_ctx())
        private = _harness._memory_block(_ctx(incognito=True))
        check("a normal owner turn gets the memory block", len(normal) > 100)
        check("an incognito turn gets NO memory block at all", private == "", private)
        check("the memory protocol is absent too — nothing tells it to journal",
              "Journal" not in private and "MEMORY.md" not in private)

        prompt_normal = _harness.wrap_prompt("what do I know about Priya",
                                             is_new=False, ctx=_ctx())
        prompt_private = _harness.wrap_prompt("what do I know about Priya",
                                              is_new=False, ctx=_ctx(incognito=True))
        check("a normal turn gets the graph card for a named person",
              "[via graph]" in prompt_normal, prompt_normal[:200])
        check("an incognito turn gets no graph card",
              "[via graph]" not in prompt_private)
        check("an incognito turn gets no curiosity question",
              "[curiosity]" not in prompt_private)
        check("the user's own message still reaches the model",
              "what do I know about Priya" in prompt_private)

        db_hits, hits = _harness._graph_hits("Priya", _ctx(incognito=True))
        check("the graph is not even queried on an incognito turn",
              db_hits is None and hits == [])
    finally:
        _restore(tmp, olds)


def _git(mem_dir, *args):
    return subprocess.run(["git", *args], cwd=mem_dir, capture_output=True, text=True)


def _run_enforcement_tests():
    print("\n[15] incognito is ENFORCED — a memory write during the turn is reverted")
    tmp, olds, db = _iso("enforce")
    core, events = _fresh_core("enforce")
    original = zcore.run_cli_async
    try:
        memory.ensure_tree()
        memory.git_autocommit("baseline")
        mem_dir = memory.MEMORY_DIR
        check("the memory repo exists for the restore to read from",
              os.path.isdir(os.path.join(mem_dir, ".git")))

        journal = memory.journal_path()
        core_md = os.path.join(mem_dir, "MEMORY.md")
        before_core = open(core_md, encoding="utf-8").read()

        # The model writes to memory anyway, mid-turn.
        async def _leaky(prompt, conv_id=None, progress_callback=None, cancel_event=None,
                         skip_permissions=False, ctx=None):
            os.makedirs(os.path.dirname(journal), exist_ok=True)
            with open(journal, "a", encoding="utf-8") as f:
                f.write("- 10:00 — the secret\n")
            with open(core_md, "a", encoding="utf-8") as f:
                f.write("\nthe secret again\n")
            return "Noted privately.", "conv-priv"

        zcore.run_cli_async = _leaky
        core.sessions.create_session("private-1", OWNER, incognito=True)

        replies = []

        async def _turn(text="remember this private thing"):
            async for ev in core.handle_message(OWNER, text):
                if isinstance(ev, Response):
                    replies.append(ev)
        asyncio.run(_turn())

        check("the owner still gets their answer", len(replies) == 1
              and replies[0].text == "Noted privately.", replies)
        check("the zero-model 'share' route never fires in a private session — "
              "it would write the message verbatim into the journal",
              replies[0].text != "📝 Noted.", replies)
        check("the reply is marked as an incognito turn",
              replies[0].meta.get("incognito") is True, replies[0].meta)
        check("a file the turn created is gone again",
              not os.path.exists(journal) or "the secret" not in
              open(journal, encoding="utf-8").read())
        check("a file the turn edited is back to what it was",
              open(core_md, encoding="utf-8").read() == before_core)
        check("nothing was committed to the memory repo",
              "the secret" not in _git(mem_dir, "log", "-p").stdout)
        notices = [e for e in events if isinstance(e, Alert)]
        check("the owner is told, in one line, that it was undone",
              len(notices) == 1 and "private" in notices[0].text
              and "undid" in notices[0].text, notices)

        # A private turn that writes NOTHING must say nothing.
        events.clear()
        zcore.run_cli_async = _fake_runner({"": "Fine."})
        asyncio.run(_turn())
        check("a clean private turn produces no notice",
              [e for e in events if isinstance(e, Alert)] == [])

        # A NORMAL session is unaffected — memory still works.
        core.sessions.create_session("normal-1", OWNER)
        core.memory_autocommit_enabled = True
        zcore.run_cli_async = _leaky
        events.clear()
        asyncio.run(_turn("draft a plan for the week"))
        check("a normal turn's memory write is KEPT, not reverted",
              "the secret" in open(core_md, encoding="utf-8").read())
        check("no incognito notice on a normal turn",
              not any(isinstance(e, Alert) and "private" in e.text for e in events))
    finally:
        core.memory_autocommit_enabled = False
        zcore.run_cli_async = original
        _restore(tmp, olds)


def _run_snapshot_and_restore_tests():
    print("\n[16] memory.tree_snapshot / git_restore — the primitives underneath")
    tmp, olds, db = _iso("snap")
    try:
        memory.ensure_tree()
        mem_dir = memory.MEMORY_DIR
        snap = memory.tree_snapshot()
        check("the snapshot sees the tree's files", "MEMORY.md" in snap, list(snap)[:5])
        check("the snapshot ignores .git",
              not any(p.startswith(".git") for p in snap))

        check("restore with no repo reports failure instead of pretending",
              memory.git_restore() is False)

        memory.git_autocommit("baseline")
        with open(os.path.join(mem_dir, "new-note.md"), "w", encoding="utf-8") as f:
            f.write("leak")
        with open(os.path.join(mem_dir, "MEMORY.md"), "a", encoding="utf-8") as f:
            f.write("\nleak\n")
        after = memory.tree_snapshot()
        check("a new file shows up as a difference", after != snap)

        check("restore succeeds against a real repo", memory.git_restore() is True)
        check("the created file is gone",
              not os.path.exists(os.path.join(mem_dir, "new-note.md")))
        check("the edited file is back",
              "leak" not in open(os.path.join(mem_dir, "MEMORY.md"),
                                 encoding="utf-8").read())
        check("a missing tree never raises",
              isinstance(memory.tree_snapshot(base=os.path.join(tmp, "nope")), dict))
    finally:
        _restore(tmp, olds)


def _run_conv_delete_tests():
    print("\n[17] closing a private session deletes the conversation")
    tmp, olds, db = _iso("conv")
    from zilla import cli_engine as _engine
    old_brain = _engine.BRAIN_DIR
    try:
        _engine.BRAIN_DIR = os.path.join(tmp, "brain")
        conv = os.path.join(_engine.BRAIN_DIR, "conv-private")
        os.makedirs(conv)
        with open(os.path.join(conv, "transcript.jsonl"), "w", encoding="utf-8") as f:
            f.write("{}\n")

        check("the conversation directory is deleted",
              _engine.delete_conv_dir("conv-private") is True
              and not os.path.exists(conv))
        check("deleting it twice is a no-op",
              _engine.delete_conv_dir("conv-private") is False)
        check("a missing id is a no-op", _engine.delete_conv_dir(None) is False)

        outside = os.path.join(tmp, "not-the-brain")
        os.makedirs(outside)
        check("a path-traversing id can never delete anything else",
              _engine.delete_conv_dir("../not-the-brain") is False
              and os.path.isdir(outside))
        check("the brain dir itself is never deleted",
              _engine.delete_conv_dir(".") is False
              and os.path.isdir(_engine.BRAIN_DIR))
    finally:
        _engine.BRAIN_DIR = old_brain
        _restore(tmp, olds)


# ============================================================
#  18. bot.py — commands, taps, delivery
# ============================================================

class _FakeMessage:
    def __init__(self):
        self.sent: list[str] = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)
        return _FakeSentMessage()


class _FakeSentMessage:
    message_id = 1


class _FakeQuery:
    def __init__(self, data):
        self.data = data
        self.edited_texts = []

    async def answer(self, *a, **kw):
        pass

    async def edit_message_text(self, text, **kw):
        self.edited_texts.append(text)


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return _FakeSentMessage()

    async def edit_message_reply_markup(self, **kw):
        pass


class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class _FakeUpdate:
    def __init__(self, uid, chat_id=None):
        self.effective_user = _FakeUser(uid)
        self.effective_chat = _FakeChat(chat_id if chat_id is not None else uid)
        self.message = _FakeMessage()
        self.effective_message = self.message
        self.callback_query = None


class _FakeContext:
    def __init__(self, args=None, bot=None):
        self.args = args or []
        self.bot = bot if bot is not None else _FakeBot()


class _FakeAuth:
    def is_owner(self, uid):
        return uid == OWNER

    def can(self, uid, role):
        return uid == OWNER

    def role_of(self, uid):
        return "owner" if uid == OWNER else "admin"

    def is_authorized(self, uid):
        return uid == OWNER


def _run_bot_tests():
    print("\n[18] bot.py — /bg, /tasks, the taps, and result delivery")
    tmp, olds, db = _iso("bot")
    import bot as _bot
    core, events = _fresh_core("bot")
    old = (_bot.auth, _bot.core, _bot.OWNER_CHAT_ID, _bot.sessions, _bot.MEMORY_DIR)
    original = zcore.run_cli_async
    gate = _Gate()
    zcore.run_cli_async = _fake_runner({}, block={"job": gate})
    try:
        _bot.auth = _FakeAuth()
        _bot.core = core
        _bot.sessions = core.sessions
        _bot.OWNER_CHAT_ID = OWNER
        _bot.MEMORY_DIR = memory.MEMORY_DIR

        # /bg with no argument explains itself instead of erroring.
        u = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_bg(u, _FakeContext()))
        check("/bg with no text says how to use it",
              u.message.sent and "/bg" in u.message.sent[0], u.message.sent)

        u2 = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_bg(u2, _FakeContext(args=["job", "one"])))
        check("/bg starts a job and says so",
              u2.message.sent and "Started" in u2.message.sent[0], u2.message.sent)
        check("the job is really in the lane",
              db.tasks_count_by_status(ztasks.LIVE_STATUSES) == 1)

        u3 = _FakeUpdate(NON_OWNER)
        asyncio.run(_bot.cmd_bg(u3, _FakeContext(args=["job", "two"])))
        check("a non-admin can't start background work",
              "admin" in u3.message.sent[0], u3.message.sent)
        check("and nothing was created for them",
              db.tasks_count_by_status(ztasks.LIVE_STATUSES) == 1)

        # /tasks board.
        u4 = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_tasks(u4, _FakeContext()))
        check("/tasks renders the board", u4.message.sent
              and "Background jobs" in u4.message.sent[0], u4.message.sent)

        # Stop tap.
        tid = db.tasks_by_status((ztasks.RUNNING,))[0]["id"]
        q = _FakeQuery(f"task_stop_{tid}")
        asyncio.run(_bot._cb_tasks(q, _FakeContext(), q.data, OWNER, OWNER))
        check("the stop tap reports the job stopped",
              q.edited_texts and "Stopped" in q.edited_texts[0], q.edited_texts)

        q2 = _FakeQuery("task_stop_nosuch")
        asyncio.run(_bot._cb_tasks(q2, _FakeContext(), q2.data, OWNER, OWNER))
        check("stopping an unknown job says so calmly",
              q2.edited_texts and "finished" in q2.edited_texts[0], q2.edited_texts)

        q3 = _FakeQuery(f"task_stop_{tid}")
        asyncio.run(_bot._cb_tasks(q3, _FakeContext(), q3.data, NON_OWNER, NON_OWNER))
        check("a non-admin tap does nothing at all", q3.edited_texts == [])

        # Proposal taps.
        core._process_bg_markers("ok\nBG_TASK: proposed work", _ctx(), OWNER)
        proposal = [e for e in events if isinstance(e, TaskProposal)][-1]
        q4 = _FakeQuery(f"bgt_no_{proposal.id}")
        asyncio.run(_bot._cb_tasks(q4, _FakeContext(), q4.data, OWNER, OWNER))
        check("declining a proposal confirms nothing is running",
              q4.edited_texts and "nothing is running" in q4.edited_texts[0],
              q4.edited_texts)

        core._process_bg_markers("ok\nBG_TASK: job accepted", _ctx(), OWNER)
        proposal2 = [e for e in events if isinstance(e, TaskProposal)][-1]
        q5 = _FakeQuery(f"bgt_ok_{proposal2.id}")
        asyncio.run(_bot._cb_tasks(q5, _FakeContext(), q5.data, OWNER, OWNER))
        check("accepting a proposal starts the job",
              q5.edited_texts and ("Started" in q5.edited_texts[0]
                                   or "Queued" in q5.edited_texts[0]),
              q5.edited_texts)
        q6 = _FakeQuery(f"bgt_ok_{proposal2.id}")
        asyncio.run(_bot._cb_tasks(q6, _FakeContext(), q6.data, OWNER, OWNER))
        check("a double-tap on the same offer says it was already handled",
              q6.edited_texts and "expired" in q6.edited_texts[0], q6.edited_texts)

        # The proposal card itself.
        bot_app = _FakeBot()
        _bot._application = type("A", (), {"bot": bot_app})()
        asyncio.run(_bot._deliver_task_proposal(proposal))
        check("the proposal card is DMd with the prompt in it",
              bot_app.sent and "proposed work" in bot_app.sent[0][1], bot_app.sent)

        # Result delivery.
        bot2 = _FakeBot()
        _bot._application = type("A", (), {"bot": bot2})()
        done = TaskResult(id="x", uid=OWNER, chat_id=OWNER, title="Stock count",
                          status=ztasks.DONE, response="Here are the numbers.",
                          duration=61,
                          card=core.tasks._header_card({"title": "Stock count"}, 61))
        asyncio.run(_bot._deliver_task_result(done))
        texts = " ".join(t for _c, t in bot2.sent)
        check("a finished job delivers a header card with the duration",
              "Stock count" in texts and "1 min" in texts, bot2.sent)
        check("and the answer itself", "Here are the numbers." in texts, bot2.sent)

        bot3 = _FakeBot()
        _bot._application = type("A", (), {"bot": bot3})()
        asyncio.run(_bot._deliver_task_result(TaskResult(
            id="y", uid=OWNER, chat_id=OWNER, title="Broken", status=ztasks.FAILED)))
        check("a failed job is one calm sentence, no stack trace",
              len(bot3.sent) == 1 and "didn't finish" in bot3.sent[0][1], bot3.sent)

        bot4 = _FakeBot()
        _bot._application = type("A", (), {"bot": bot4})()
        asyncio.run(_bot._deliver_task_result(TaskResult(
            id="z", uid=OWNER, chat_id=OWNER, title="Stopped one",
            status=ztasks.CANCELED)))
        check("a canceled job sends nothing — the tap already said so",
              bot4.sent == [], bot4.sent)
    finally:
        gate.set()
        zcore.run_cli_async = original
        (_bot.auth, _bot.core, _bot.OWNER_CHAT_ID, _bot.sessions,
         _bot.MEMORY_DIR) = old
        _restore(tmp, olds)


def _run_bot_incognito_tests():
    print("\n[19] bot.py — /new incognito and /end deleting the conversation")
    tmp, olds, db = _iso("botincog")
    import bot as _bot
    from zilla import cli_engine as _engine
    core, _ = _fresh_core("botincog")
    old = (_bot.auth, _bot.core, _bot.sessions, _bot.MEMORY_DIR)
    old_brain = _engine.BRAIN_DIR
    try:
        _bot.auth = _FakeAuth()
        _bot.core = core
        _bot.sessions = core.sessions
        _bot.MEMORY_DIR = memory.MEMORY_DIR
        _engine.BRAIN_DIR = os.path.join(tmp, "brain")

        u = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_new(u, _FakeContext(args=["incognito"])))
        check("/new incognito says what private means",
              u.message.sent and "Private session" in u.message.sent[0]
              and "won't remember" in u.message.sent[0], u.message.sent)
        name = core.sessions.get_active_name(OWNER)
        check("the new session is named for what it is and is active",
              name.startswith("incognito-"), name)
        check("and it carries the flag", core.sessions.is_incognito(OWNER, name))

        u2 = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_new(u2, _FakeContext(args=["notes"])))
        check("a normal /new is unchanged",
              "Session [notes] created" in u2.message.sent[0], u2.message.sent)
        check("and is NOT incognito", not core.sessions.is_incognito(OWNER, "notes"))

        # /end on the private session deletes its conversation directory.
        core.sessions.set_active_name(name, OWNER)
        core.sessions.set_conversation_id("conv-private", user_id=OWNER,
                                          session_name=name, backend="agy")
        conv = os.path.join(_engine.BRAIN_DIR, "conv-private")
        os.makedirs(conv, exist_ok=True)
        u3 = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_end(u3, _FakeContext()))
        check("/end on a private session says the conversation is deleted",
              "deleted" in u3.message.sent[0], u3.message.sent)
        check("the conversation directory really is gone", not os.path.exists(conv))
        check("the session is gone too", name not in core.sessions.list_sessions(OWNER))

        # /end on a normal session leaves the conversation alone.
        core.sessions.set_active_name("notes", OWNER)
        core.sessions.set_conversation_id("conv-normal", user_id=OWNER,
                                          session_name="notes", backend="agy")
        conv2 = os.path.join(_engine.BRAIN_DIR, "conv-normal")
        os.makedirs(conv2, exist_ok=True)
        u4 = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_end(u4, _FakeContext()))
        check("/end on a normal session keeps its conversation",
              os.path.isdir(conv2), conv2)
        check("and reports the ordinary ending",
              "ended" in u4.message.sent[0], u4.message.sent)

        check("/close is registered as an alias of /end",
              any("close" in spec.aliases for spec in _bot.COMMAND_REGISTRY
                  if spec.name == "end"))
        names = {spec.name for spec in _bot.COMMAND_REGISTRY}
        check("/bg and /tasks are real registered commands",
              {"bg", "tasks"} <= names, names)
    finally:
        _engine.BRAIN_DIR = old_brain
        (_bot.auth, _bot.core, _bot.sessions, _bot.MEMORY_DIR) = old
        _restore(tmp, olds)


if __name__ == "__main__":
    print("=" * 60)
    print("  PHASE B1/B2 — BACKGROUND LANE + INCOGNITO TESTS")
    print("=" * 60)
    _run_parse_tests()
    _run_copy_tests()
    _run_board_tests()
    _run_store_tests()
    _run_lock_independence_test()
    _run_session_isolation_test()
    _run_cap_tests()
    _run_backlog_cap_test()
    _run_cancel_tests()
    _run_failure_and_retry_tests()
    _run_marker_hold_tests()
    _run_task_output_guard_test()
    _run_incognito_flag_tests()
    _run_injection_absence_tests()
    _run_enforcement_tests()
    _run_snapshot_and_restore_tests()
    _run_conv_delete_tests()
    _run_bot_tests()
    _run_bot_incognito_tests()
    print("\n" + "=" * 60)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 60)
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
