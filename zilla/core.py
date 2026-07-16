# ============================================================
#  ZILLA CORE — interface-agnostic turn pipeline (Phase 1)
# ============================================================
#
#  Implements docs/dev/CORE_API.md, migration steps 2 + 3: the TURN
#  PIPELINE and the SCHEDULER RUNTIME extracted from bot.py.
#  Frontends (Telegram bot, TUI) are thin translators between their
#  medium and this API — they preprocess input, then render the
#  events this module yields/broadcasts.
#
#  This seam owns: per-user CLI serialization, session/conv-id
#  pinning, the run_cli_async invocation (harness wrapping happens
#  inside the backend), verify/corrective-retry (inside
#  cli_engine._run_blocking), session bookkeeping, response +
#  file-path assembly, and (step 3) the scheduler tick loop /
#  catch-up / retry-and-record semantics. Approval hold, attachment
#  preprocessing, bridge watcher and health remain in the frontends /
#  later seams.
#
#  Invariants carried over UNCHANGED from bot.py (see
#  docs/dev/AI_CONTEXT.md — violating them reintroduces response
#  bleed): the per-user asyncio.Lock wraps the whole CLI run;
#  conv_id is re-read and the active session name pinned INSIDE
#  the lock; session writes thread session_name + backend; the
#  cancel event is registered inside the lock and popped only if
#  identity-matched. Scheduled "message" runs go through this SAME
#  lock (see _execute_schedule) — a live chat turn and a scheduled
#  job for the same user still never overlap.
# ============================================================

import asyncio
import logging
import threading
import time as _time
from dataclasses import dataclass, field

from zilla.cli_engine import run_cli_async, get_latest_step, detect_limit
from zilla.config import get_backend, get_model, get_setting
from zilla.formatter import detect_file_paths
from zilla.harness import log_event
from zilla.schedules import resolve_session_mode, backend_pin_mismatch

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  EVENTS — the one vocabulary every frontend speaks
#  (docs/dev/CORE_API.md). Turn events stream from
#  handle_message(); background events (ScheduledResult now;
#  more in later seams) go through subscribe().
# ══════════════════════════════════════════════════════════

@dataclass
class Progress:
    """Agent is working; latest step. Telegram: editable ⏳ message. TUI: status line."""
    text: str


@dataclass
class Ask:
    """Agent needs a human (otp/password/text/confirm). Placeholder — the
    bridge watcher moves here in a later seam (CORE_API migration step 4)."""
    id: str
    kind: str
    prompt: str


@dataclass
class Response:
    """Final answer for one turn.

    text  — the backend's response, verbatim (rendering/chunking is frontend work).
    files — file paths detected in the response (frontends decide delivery policy).
    meta  — session bookkeeping: {"session": name, "conv_id": id, "canceled": bool}.
    """
    text: str
    files: tuple = ()
    meta: dict = field(default_factory=dict)


@dataclass
class ApprovalRequest:
    """Limited user waiting for owner approval. Placeholder — the approval
    flow moves here in a later seam (CORE_API migration step 5)."""
    id: str
    user: int
    prompt: str


@dataclass
class Alert:
    """Human-required health problem. Placeholder — health loop is a later
    seam (CORE_API migration step 6 / Phase 7)."""
    text: str
    runbook: str = ""


@dataclass
class ScheduledResult:
    """A schedule fired. Telegram renders '⏰ Scheduled — <title>' + response
    (bot.py owns all Telegram-specific rendering: chunking, the model-switch
    suggestion, file delivery — this event just carries the data).

    chat_id/user_id: WHERE and for WHOM this fired (the schedule's own chat —
    not necessarily the owner).
    warning: set only on a "gave up after the retry ladder" occurrence — the
    old bot.py behavior of a separate '⚠️ couldn't complete' notice, carried
    as one event instead of two so delivery order can't race. response is ""
    when the failed run produced no usable output at all (warning-only
    delivery); otherwise it carries whatever partial output there was.
    session/conv_id: carried for a future "continue this conversation" UX —
    no reply-routing is built on top of them yet.
    """
    title: str
    response: str
    chat_id: int = None
    user_id: int = None
    schedule_id: str = None
    warning: str = ""
    session: str = None
    conv_id: str = None


# ══════════════════════════════════════════════════════════
#  CORE
# ══════════════════════════════════════════════════════════

class ZillaCore:
    """Owns everything that is not interface I/O. This seam: the turn
    pipeline + scheduler runtime. Later seams add bridge watcher, approvals
    and health (see CORE_API.md).

    Shares the frontend's SessionManager/AuthManager instances so there is
    exactly one source of truth while bot.py still holds its own references.
    """

    def __init__(self, sessions, auth, schedules=None):
        self.sessions = sessions
        self.auth = auth
        # ScheduleManager, optional. None ⇒ this core runs no scheduler (used
        # by tests that only exercise the turn pipeline — start()/stop() are
        # then no-ops). bot.py always passes the real one.
        self.schedules = schedules

        # Per-chat cancel events — set to cancel the active CLI request for
        # that chat. Keyed by the frontend's chat key (Telegram: chat_id;
        # defaults to user_id for frontends without a separate chat concept).
        self._active_cancel: dict[int, threading.Event] = {}

        # Per-user CLI serialization. The agy CLI keeps ONE conversation per
        # user, and running two invocations against the same conversation at
        # once corrupts its transcript and makes each handler scoop up the
        # other turn's steps (responses bleed into the wrong reply). With
        # concurrent frontends the event loop can enter several handlers for
        # one user at once, so we gate every CLI run behind a per-user
        # asyncio.Lock — a user's messages run one at a time, different users
        # stay fully concurrent. Created lazily on the single-threaded event
        # loop, so get-or-create needs no lock of its own. Scheduled "message"
        # runs share this SAME map (see _execute_schedule) — a live chat and
        # a scheduled job for the same user still never overlap.
        self._user_cli_locks: dict[int, asyncio.Lock] = {}

        # Out-of-turn event broadcast (docs/dev/CORE_API.md: "an async-queue
        # broadcast"). Frontends register a queue via subscribe(); every
        # background event (ScheduledResult now; Ask/Alert/ApprovalRequest
        # join in later seams) is pushed onto every registered queue.
        self._subscribers: list[asyncio.Queue] = []

        # Optional frontend-supplied fast path for a schedule's run, checked
        # BEFORE the normal CLI turn (e.g. Telegram's screenshot-via-WebBridge
        # shortcut, which needs the bridge — a frontend/connector concern the
        # core doesn't own yet, CORE_API migration step 4). Signature:
        # async (schedule: dict) -> (ok, response, detail) | None; None means
        # "no special-case, run the schedule normally."
        self.schedule_pre_run = None

        self._sched_task: asyncio.Task | None = None

        # Recursion guard: uids whose CURRENT turn was started by a schedule.
        # bot.py's NL schedule-detection checks is_scheduled_run() so a
        # schedule-triggered turn can never create more schedules.
        self._scheduled_running: set[int] = set()

    # ── lifecycle ───────────────────────────────────────────

    async def start(self):
        """Start background runtime. This seam: the scheduler loop. Bridge
        watcher and health loop land here in later seams (CORE_API migration
        steps 4/6). No-op if built without a ScheduleManager."""
        if self.schedules is not None and self._sched_task is None:
            self._sched_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self):
        if self._sched_task is not None:
            self._sched_task.cancel()
            try:
                await self._sched_task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # pragma: no cover - defensive
                logger.error(f"[SCHED] stop() cleanup error: {e}")
            self._sched_task = None

    # ── background event broadcast ─────────────────────────

    def subscribe(self, sink: asyncio.Queue) -> None:
        """Register a frontend's queue for out-of-turn events. Every
        broadcast event is pushed with put_nowait — queues are unbounded, so
        a slow or dead frontend can never stall the scheduler."""
        self._subscribers.append(sink)

    def unsubscribe(self, sink: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(sink)
        except ValueError:
            pass

    def _broadcast(self, event) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except Exception:  # pragma: no cover - defensive
                pass

    # ── locks / cancel ─────────────────────────────────────

    def get_user_lock(self, uid: int) -> asyncio.Lock:
        lock = self._user_cli_locks.get(uid)
        if lock is None:
            lock = asyncio.Lock()
            self._user_cli_locks[uid] = lock
        return lock

    def is_busy(self, uid: int) -> bool:
        """True if this user's CLI lock is currently held (a turn is running
        or queued). Frontends use this for the 'one sec' heads-up."""
        lock = self._user_cli_locks.get(uid)
        return bool(lock and lock.locked())

    def is_scheduled_run(self, uid: int) -> bool:
        """True while a schedule's own turn is executing for this uid — the
        recursion guard that stops schedule-triggered turns creating more
        schedules (bot.py checks it in NL schedule-detection)."""
        return uid in self._scheduled_running

    def cancel(self, key: int) -> bool:
        """Cancel the active CLI run for this chat key. Returns True if a
        live (not yet set) cancel event was found and set."""
        cancel_ev = self._active_cancel.get(key)
        if cancel_ev and not cancel_ev.is_set():
            cancel_ev.set()
            return True
        return False

    # ── conversation pinning ───────────────────────────────

    def _conv_for_run(self, uid: int, sname: str):
        """The conversation id to resume — but only if it was created by the CURRENT
        backend. agy brain-dir ids and claude session ids aren't interchangeable, so
        after switching backend we start a fresh conversation instead of mismatching."""
        cid = self.sessions.get_conversation_id(user_id=uid, session_name=sname)
        if cid and self.sessions.get_conv_backend(uid, sname) != get_backend():
            return None
        return cid

    # ── THE turn pipeline ──────────────────────────────────

    async def handle_message(self, user_id: int, text: str, *, chat_key: int = None,
                             auto_title: bool = False, skip_permissions: bool = None):
        """Run one CLI turn against the user's active session, yielding events.

        Async generator: yields zero-or-more Progress events while the backend
        works, then exactly one Response. Acquires the per-user lock, pins the
        session that is active the moment we start (the user may /switch while
        queued), resumes/tracks its conversation, optionally auto-titles a
        fresh session, and keeps the message bookkeeping in sync. Shared by
        every frontend input path (text, voice, photo, document, approvals).

        chat_key: the frontend's cancel key (Telegram chat_id); defaults to
        user_id. cancel(chat_key) aborts the run.
        skip_permissions: None → derive from the user's role (admins skip
        prompts). Owner-approved Approval-mode runs pass True explicitly (the
        owner already vetted the whole request).
        """
        if skip_permissions is None:
            skip_permissions = self.auth.can(user_id, "admin")
        key = user_id if chat_key is None else chat_key
        cancel_event = threading.Event()

        # Progress events arrive from the backend's worker thread; relay them
        # onto the event loop through a queue so this generator can yield them.
        loop = asyncio.get_running_loop()
        progress_q: asyncio.Queue = asyncio.Queue()

        def _on_progress(step: str) -> None:
            try:
                loop.call_soon_threadsafe(progress_q.put_nowait, Progress(text=step))
            except RuntimeError:
                pass  # loop closed (shutdown) — progress is best-effort

        response = ""
        final_conv = None
        sname = None
        try:
            async with self.get_user_lock(user_id):
                # Pin the session to whatever is active the moment WE start running, and
                # write every result back to that same session — never the now-active one.
                self._active_cancel[key] = cancel_event
                sname = self.sessions.get_active_name(user_id)
                conv_id = self._conv_for_run(user_id, sname)

                if auto_title:
                    info = self.sessions.get_session_info(user_id=user_id, session_name=sname)
                    if info and info.get("messages", 0) == 0:
                        self.sessions.auto_title(text, user_id=user_id, session_name=sname)

                run_task = loop.create_task(run_cli_async(
                    text, conv_id,
                    progress_callback=_on_progress,
                    cancel_event=cancel_event,
                    skip_permissions=skip_permissions,
                ))
                try:
                    while not run_task.done():
                        getter = loop.create_task(progress_q.get())
                        await asyncio.wait({run_task, getter},
                                           return_when=asyncio.FIRST_COMPLETED)
                        if getter.done():
                            yield getter.result()
                        else:
                            getter.cancel()
                    while not progress_q.empty():
                        yield progress_q.get_nowait()
                    response, detected_id = run_task.result()
                finally:
                    # Consumer closed us mid-run (frontend died): stop the
                    # backend instead of leaving it running unobserved.
                    if not run_task.done():
                        cancel_event.set()
                        run_task.cancel()

                if detected_id and detected_id != conv_id:
                    self.sessions.set_conversation_id(detected_id, user_id=user_id,
                                                      session_name=sname, backend=get_backend())

                final_conv = detected_id or conv_id
                if final_conv:
                    self.sessions.set_last_seen_step(get_latest_step(final_conv),
                                                     user_id=user_id, session_name=sname)
                self.sessions.increment_messages(user_id=user_id, session_name=sname)

            # Lock released — deliver outside it (matches the old bot.py shape:
            # send_response ran after _run_cli_turn returned).
            yield Response(
                text=response,
                files=tuple(detect_file_paths(response or "")),
                meta={"session": sname, "conv_id": final_conv,
                      "canceled": cancel_event.is_set()},
            )
        finally:
            if self._active_cancel.get(key) is cancel_event:
                self._active_cancel.pop(key, None)

    # ══════════════════════════════════════════════════════
    #  SCHEDULER RUNTIME  (docs/dev/CORE_API.md migration step 3)
    # ══════════════════════════════════════════════════════
    #
    #  Moved from bot.py's scheduler_loop/_execute_schedule/_run_and_record/
    #  _run_now — tick cadence, catch-up, touch_run, and the self-healing
    #  retry model are UNCHANGED. Result delivery is now a ScheduledResult
    #  event broadcast through subscribe(); Telegram is a pure renderer of
    #  it (bot.py), same rendering as before.
    #
    #  Self-healing model (fixes the old silent-failure bug where touch_run
    #  advanced the schedule even when the run errored, losing the job
    #  forever):
    #    _execute_schedule  → runs, classifies ok/failure, NO delivery.
    #    _run_and_record    → tick-loop path: broadcasts on success/give-up,
    #                         records the outcome, RETRIES a failed run a
    #                         few times before the schedule advances.
    #    run_schedule_now   → manual ▶️ Run now: run + broadcast, never
    #                         advances the schedule.

    _SCHED_TICK = 20          # seconds between due-checks

    # Response shapes that mean "the run did not really succeed".
    _SCHED_FAIL_PREFIXES = ("Error:", "Claude error:", "⏱️", "⚠️ Stopped")

    def _sname_for_mode(self, uid: int, mode: str) -> str | None:
        """Map a resolved session mode (see zilla.schedules.resolve_session_mode)
        to the session name to run under. 'isolated' -> None (fresh
        conversation every run — today's discovered default behavior)."""
        if mode.startswith("named:"):
            return mode.split("named:", 1)[1]
        if mode == "main":
            return "main"
        return None  # "isolated" (or any unrecognized mode, safest default)

    async def _execute_message_schedule(self, s: dict) -> tuple:
        """payload_type == 'message': a full CLI turn, same as a live chat
        turn — pinned session, per-user lock, response-level failure
        classification. Returns (ok, response, detail, meta)."""
        uid = s["user_id"]

        # A frontend-supplied fast path gets first refusal (e.g. Telegram's
        # screenshot-via-WebBridge shortcut, which must bypass the CLI agent
        # entirely — see bot.py's schedule_pre_run wiring).
        if self.schedule_pre_run is not None:
            hook_result = await self.schedule_pre_run(s)
            if hook_result is not None:
                ok, response, detail = hook_result
                return ok, response, detail, {"conv_id": None}

        pin_mismatch = backend_pin_mismatch(s, get_backend(), get_model())
        mode = resolve_session_mode(s)
        ok, detail, response, conv_id = True, "", "", None
        self._scheduled_running.add(uid)
        try:
            async with self.get_user_lock(uid):
                sname = self._sname_for_mode(uid, mode)
                if sname:
                    conv_id = self._conv_for_run(uid, sname)
                response, detected = await run_cli_async(
                    s["prompt"], conv_id,
                    skip_permissions=self.auth.can(uid, "admin") if self.auth else False,
                )
                if sname and detected and detected != conv_id:
                    self.sessions.set_conversation_id(
                        detected, user_id=uid, session_name=sname, backend=get_backend())
                conv_id = detected or conv_id
        except Exception as e:
            ok, detail, response = False, str(e), f"Error: {e}"
            logger.error(f"[SCHED] run {s['id']} failed: {e}", exc_info=True)
        finally:
            self._scheduled_running.discard(uid)

        # Response-level failure detection (empty / rate-limited / error text).
        if ok:
            if not (response and response.strip()):
                ok, detail = False, "empty response"
            elif detect_limit(response):
                ok, detail = False, f"model limited: {detect_limit(response)}"
            elif response.lstrip().startswith(self._SCHED_FAIL_PREFIXES):
                ok, detail = False, response.strip()[:200]
        return ok, response, detail, {
            "conv_id": conv_id, "session": mode, "pin_mismatch": pin_mismatch,
        }

    async def _execute_command_schedule(self, s: dict) -> tuple:
        """payload_type == 'command': run the stored prompt as a subprocess.
        ZERO model call — owner-only at creation (ScheduleManager.add()).
        Returns (ok, response, detail, meta)."""
        try:
            proc = await asyncio.create_subprocess_shell(
                s["prompt"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            text = (out or b"").decode("utf-8", errors="replace")
            if proc.returncode == 0:
                return True, text, "", {"conv_id": None}
            return False, text, f"exit {proc.returncode}", {"conv_id": None}
        except Exception as e:
            logger.error(f"[SCHED] command {s['id']} failed: {e}", exc_info=True)
            return False, "", str(e), {"conv_id": None}

    async def _execute_schedule(self, s: dict) -> tuple:
        """Run one schedule's prompt according to its payload_type. Returns
        (ok, response, detail, meta). No delivery, no schedule mutation
        (touch_run/mark_*) — pure execution + outcome classification.

        meta is at least {"conv_id": ...}; message-payload runs also carry
        "session" (the resolved mode) and "pin_mismatch" (bool)."""
        uid = s["user_id"]

        # SECURITY: a schedule is a stored prompt that can run with full
        # host privileges (message → agentic CLI, command → raw shell). If
        # the owning user was de-authorized after creating it, the schedule
        # must NOT keep firing (otherwise removal isn't really revocation —
        # it's a persistent backdoor). Disable + skip.
        if not (self.auth and (self.auth.is_owner(uid) or self.auth.is_authorized(uid))):
            logger.warning(f"[SCHED] skip {s['id']}: user {uid} no longer authorized — disabling")
            try:
                self.schedules.set_enabled(s["id"], uid, False)
            except Exception:
                pass
            return False, "", "owner deauthorized", {"conv_id": None}

        payload_type = s.get("payload_type", "message")

        if payload_type == "system_event":
            # Deliver the stored text verbatim. ZERO CLI/model call.
            return True, s.get("prompt", ""), "", {"conv_id": None}

        if payload_type == "command":
            return await self._execute_command_schedule(s)

        return await self._execute_message_schedule(s)

    def _maybe_notify_backend_pin(self, s: dict) -> None:
        """One-time owner Alert when a schedule's pinned backend/model has
        drifted from what's active at fire time. Runs on the CURRENT
        backend regardless — no per-call backend override exists in
        cli_engine, so this is an FYI, never a block. Tracked via
        backend_pin_notified so it fires at most once per schedule."""
        current_backend, current_model = get_backend(), get_model()
        text = (
            f"Scheduled job \"{s.get('title', '')}\" was pinned to "
            f"{s.get('backend')}/{s.get('model')} but ran on "
            f"{current_backend}/{current_model} (backend/model changed since "
            f"creation). It will keep using whatever backend is active."
        )
        self._broadcast(Alert(text=text))
        try:
            self.schedules.mark_backend_pin_notified(s["id"])
        except Exception:
            pass

    async def _run_and_record(self, s: dict) -> None:
        """Tick-loop path: run a due schedule, broadcast the result, and
        record the outcome with retry. A failed run is retried along
        RETRY_LADDER before the schedule advances — and the owner's chat is
        told if it ultimately couldn't complete."""
        sid = s["id"]
        title = s.get("title", "")
        ok, response, detail, meta = await self._execute_schedule(s)
        if meta.get("pin_mismatch"):
            self._maybe_notify_backend_pin(s)
        if ok:
            self.schedules.mark_success(sid)
            log_event("schedule_ok", id=sid, title=title[:40])
            self._broadcast(ScheduledResult(
                title=title, response=response, chat_id=s["chat_id"], user_id=s["user_id"],
                schedule_id=sid, session=meta.get("session"), conv_id=meta.get("conv_id"),
            ))
            return
        outcome, attempt = self.schedules.mark_failure(sid)
        log_event("schedule_failed", id=sid, title=title[:40],
                  attempt=attempt, outcome=outcome, detail=(detail or "")[:200])
        if outcome == "gaveup":
            # Never silent: tell the schedule's chat what happened + hand
            # over any partial output.
            warning = (
                f"⚠️ Scheduled job couldn't complete: <b>{title}</b>\n"
                f"Tried {attempt}× over the retry window. I'll run it again at "
                f"its next scheduled time.\nLast issue: {(detail or 'unknown')[:200]}"
            )
            self._broadcast(ScheduledResult(
                title=title, response=(response if response and response.strip() else ""),
                chat_id=s["chat_id"], user_id=s["user_id"], schedule_id=sid, warning=warning,
                session=meta.get("session"), conv_id=meta.get("conv_id"),
            ))
        # 'retry' / 'gone' → stay quiet; it will run again on its own.

    async def run_schedule_now(self, sid: str) -> None:
        """Manual ▶️ Run now: execute + broadcast, WITHOUT advancing the
        schedule (no touch_run/mark_success/mark_failure)."""
        s = self.schedules.get(sid) if self.schedules else None
        if not s:
            return
        ok, response, detail, meta = await self._execute_schedule(s)
        if meta.get("pin_mismatch"):
            self._maybe_notify_backend_pin(s)
        text = response if (response and response.strip()) else (detail or "(no output)")
        self._broadcast(ScheduledResult(
            title=s.get("title", ""), response=text, chat_id=s["chat_id"], user_id=s["user_id"],
            schedule_id=sid, session=meta.get("session"), conv_id=meta.get("conv_id"),
        ))

    async def _scheduler_loop(self) -> None:
        """Background loop: catch up missed jobs at boot, then run due jobs.
        Due jobs run concurrently (one slow job no longer blocks the others);
        the per-user lock still serializes a single user's runs."""
        try:
            self.schedules.reconcile_startup(
                now=_time.time(), catchup=get_setting("schedule_catchup", True))
        except Exception as e:
            logger.error(f"[SCHED] reconcile failed: {e}")
        logger.info("[SCHED] scheduler loop started")
        while True:
            try:
                due = self.schedules.due()
                if due:
                    for s in due:
                        logger.info(f"[SCHED] running {s['id']} ({s.get('title', '')[:30]})")
                    await asyncio.gather(
                        *[self._run_and_record(s) for s in due],
                        return_exceptions=True,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[SCHED] loop error: {e}", exc_info=True)
            # Sleep only until the next pending job (capped at the tick, so a
            # job added mid-sleep waits at most one tick) — a 2-minute timer
            # fires at 2:00, not 2:00 + tick drift.
            delay = self._SCHED_TICK
            try:
                soonest = self.schedules.next_due_at()
                if soonest is not None:
                    delay = max(0.5, min(self._SCHED_TICK, soonest - _time.time()))
            except Exception:
                pass
            await asyncio.sleep(delay)
