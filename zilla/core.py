# ============================================================
#  ZILLA CORE — interface-agnostic turn pipeline (Phase 1)
# ============================================================
#
#  Implements docs/dev/CORE_API.md, migration step 2: the TURN
#  PIPELINE extracted from bot.py. Frontends (Telegram bot, TUI)
#  are thin translators between their medium and this API — they
#  preprocess input, then render the events this module yields.
#
#  This seam owns: per-user CLI serialization, session/conv-id
#  pinning, the run_cli_async invocation (harness wrapping happens
#  inside the backend), verify/corrective-retry (inside
#  cli_engine._run_blocking), session bookkeeping, and response +
#  file-path assembly. Approval hold, attachment preprocessing,
#  scheduler runtime, bridge watcher and health remain in the
#  frontends / later seams.
#
#  Invariants carried over UNCHANGED from bot.py (see
#  docs/dev/AI_CONTEXT.md — violating them reintroduces response
#  bleed): the per-user asyncio.Lock wraps the whole CLI run;
#  conv_id is re-read and the active session name pinned INSIDE
#  the lock; session writes thread session_name + backend; the
#  cancel event is registered inside the lock and popped only if
#  identity-matched.
# ============================================================

import asyncio
import threading
from dataclasses import dataclass, field

from zilla.cli_engine import run_cli_async, get_latest_step
from zilla.config import get_backend
from zilla.formatter import detect_file_paths


# ══════════════════════════════════════════════════════════
#  EVENTS — the one vocabulary every frontend speaks
#  (docs/dev/CORE_API.md). Turn events stream from
#  handle_message(); background events (later seams) will go
#  through subscribe().
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
    """A schedule fired. Placeholder — scheduler runtime moves here in a
    later seam (CORE_API migration step 3)."""
    title: str
    response: str


# ══════════════════════════════════════════════════════════
#  CORE
# ══════════════════════════════════════════════════════════

class ZillaCore:
    """Owns everything that is not interface I/O. This seam: the turn
    pipeline. Later seams add scheduler runtime, bridge watcher, approvals
    and health (see CORE_API.md).

    Shares the frontend's SessionManager/AuthManager instances so there is
    exactly one source of truth while bot.py still holds its own references.
    """

    def __init__(self, sessions, auth):
        self.sessions = sessions
        self.auth = auth

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
        # loop, so get-or-create needs no lock of its own.
        self._user_cli_locks: dict[int, asyncio.Lock] = {}

    # ── lifecycle (filled by later seams: scheduler, bridge, health) ──

    async def start(self):  # pragma: no cover - placeholder
        pass

    async def stop(self):  # pragma: no cover - placeholder
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
