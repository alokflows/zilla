# ============================================================
#  ZILLA CORE — interface-agnostic turn pipeline (Phase 1)
# ============================================================
#
#  Implements docs/dev/CORE_API.md, migration steps 2-6: the TURN
#  PIPELINE, the SCHEDULER RUNTIME, the credential/OTP BRIDGE WATCHER,
#  APPROVAL MODE, and the health_report() snapshot, all extracted
#  from bot.py. Frontends (Telegram
#  bot, TUI) are thin translators between their medium and this API —
#  they preprocess input, then render the events this module
#  yields/broadcasts.
#
#  This seam owns: per-user CLI serialization, session/conv-id
#  pinning, the run_cli_async invocation (harness wrapping happens
#  inside the backend), verify/corrective-retry (inside
#  cli_engine._run_blocking), session bookkeeping, response +
#  file-path assembly, the scheduler tick loop / catch-up /
#  retry-and-record semantics, the bridge-file poll loop, and the
#  Approval-mode hold + execution (see the Approvals class), and the
#  health_report() snapshot (step 6 STUB — the self-healing health LOOP
#  is Phase 7). Attachment preprocessing remains in the frontends.
#
#  Invariants carried over UNCHANGED from bot.py (see
#  docs/dev/AI_CONTEXT.md — violating them reintroduces response
#  bleed): the per-user asyncio.Lock wraps the whole CLI run;
#  conv_id is re-read and the active session name pinned INSIDE
#  the lock; session writes thread session_name + backend; the
#  cancel event is registered inside the lock and popped only if
#  identity-matched. Scheduled "message" runs AND owner-approved
#  Approval-mode runs go through this SAME lock (see _execute_schedule
#  / Approvals.approve) — a live chat turn, a scheduled job, and an
#  approved request for the same user still never overlap.
# ============================================================

import asyncio
import logging
import os
import secrets
import shutil
import threading
import time
import time as _time
from dataclasses import dataclass, field

import zilla.interactive as interactive
from zilla.backends import claude_identity
from zilla.cli_engine import run_cli_async, get_latest_step
from zilla.config import (
    get_backend, get_model, get_setting,
    agy_reachable, agy_models_live, BRAIN_DIR, HOME_DIR,
    WIKI_JOURNAL_DIR,
)
from zilla.formatter import detect_file_paths
from zilla.harness import log_event
from zilla.review import review, classify_route
from zilla.schedules import resolve_session_mode, backend_pin_mismatch

logger = logging.getLogger(__name__)

# How long a chat stays bound to one outstanding bridge ask (see
# ZillaCore.pending_ask_for). After this, an unanswered (orphaned) ask must
# NOT keep swallowing the user's next unrelated message. Same value/semantics
# as bot.py's old _BRIDGE_PENDING_TTL.
BRIDGE_PENDING_TTL = 900.0

# Approval mode (limited users, docs/dev/CORE_API.md migration step 5): how
# long a held request waits for the owner before it's forgotten, and the
# hard cap on how many can be queued at once so a spammer can't grow the
# store unbounded. Same values/semantics as bot.py's old _APPROVAL_TTL/_MAX.
APPROVAL_TTL = 3600.0
APPROVAL_MAX = 50


# ══════════════════════════════════════════════════════════
#  P1.5 TRIAGE ROUTER — deterministic, zero-model-call classification
#  BEFORE the heavy CLI turn (HANDOFF.md P1.5;
#  docs/dev/RESEARCH_ORCHESTRATION_REVIEW.md §4.3). classify_route()
#  itself is pure (zilla/review.py); the two helpers below are the
#  actual route ACTIONS, called from handle_message.
# ══════════════════════════════════════════════════════════

# Cheapest working Claude CLI model — live-verified (docs/dev/PHASE0_FINDINGS.md):
# `claude -p ... --model haiku` resolves to claude-haiku-4-5 and returns clean
# JSON in ~4s, dramatically faster than a full CLI turn. Fixed, not config-
# driven: the fast path's whole point is a cheap, predictable turn — if the
# owner wants a different model here later this becomes a config knob then.
_FAST_MODEL = "haiku"

# Minimal preamble: persona + style ONLY, deliberately NOT the full onboarding
# (bot_instructions.md + skills + trust contract) smalltalk doesn't need any
# of that, and backends.run_claude() can't be reused here because it always
# forces harness.wrap_prompt's full onboarding when there's no conversation_id.
_FAST_PREAMBLE = (
    "You are Zilla, a terse personal assistant reachable over Telegram. "
    "This message is pure small talk (a greeting/thanks/acknowledgment) — "
    "reply in ONE short, warm sentence. No bullets, no lists, no follow-up "
    "question."
)


def _run_fast_claude(prompt: str) -> str | None:
    """Blocking (run via asyncio.to_thread). A dedicated, lightweight one-shot
    Claude Code call for the smalltalk fast path — no --resume (always a fresh
    turn; smalltalk carries no state worth keeping), pinned to _FAST_MODEL.
    Returns the response text, or None if Claude Code could not be reached at
    all (spawn failure, timeout, non-zero exit with no output) — the caller
    falls back to the full path transparently on None."""
    import subprocess
    from zilla.config import CLAUDE_PATH, CLI_WORKING_DIR
    full_prompt = f"{_FAST_PREAMBLE}\n\nUser: {prompt}"
    cmd = [CLAUDE_PATH, "-p", full_prompt, "--output-format", "json", "--model", _FAST_MODEL]
    try:
        proc = subprocess.run(
            cmd, cwd=CLI_WORKING_DIR, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=25,
        )
    except Exception as e:
        logger.warning(f"[TRIAGE] fast-path claude unreachable: {e}")
        return None
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        logger.warning(f"[TRIAGE] fast-path claude exit {proc.returncode}: "
                       f"{(proc.stderr or '')[:200]}")
        return None
    from zilla.backends import _parse_claude_json
    text, _sid = _parse_claude_json(proc.stdout, None)
    return text


def _append_to_journal(text: str) -> str:
    """Zero-model-call 'share' route: append the message verbatim, timestamped,
    to today's wiki journal file. Path comes from config ONLY
    (zilla.config.WIKI_JOURNAL_DIR) — no hardcoded path here. Returns the
    one-line ack to show the user."""
    import datetime
    now = datetime.datetime.now()
    os.makedirs(WIKI_JOURNAL_DIR, exist_ok=True)
    path = os.path.join(WIKI_JOURNAL_DIR, now.strftime("%Y-%m-%d.md"))
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- [{now.strftime('%H:%M')}] {text}\n")
    return "📝 Noted."


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
    """Agent needs a human (otp/password/text/confirm) — the credential/OTP
    bridge (docs/dev/CORE_API.md migration step 4). Broadcast by the core's
    bridge watcher (_bridge_watcher_loop) via subscribe() when the agent
    writes a Bridge/ask_*.json file (see zilla/interactive.py for the file
    protocol); a frontend renders it (Telegram: DM with the prompt) and later
    hands the human's reply to core.answer_ask(). Also yieldable from
    handle_message per CORE_API, for a future in-turn ask path.

    chat_id    — which chat/user this ask targets (falls back to the core's
                 owner_chat_id when the ask itself carries none).
    is_secret  — True for otp/password kinds; frontends should mask/delete
                 the reply rather than leave it sitting in chat history.
    """
    id: str
    kind: str
    prompt: str
    chat_id: int = None
    is_secret: bool = False


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
    """A "limited" user's request, held for the owner to approve or deny
    (docs/dev/CORE_API.md migration step 5 — Approval mode, users.py role
    "limited"). Broadcast via subscribe() the moment ZillaCore.approvals.submit()
    registers the hold; a frontend renders it (Telegram: DM the owner the
    prompt preview + ✅/❌ buttons, same card bot.py's old
    _submit_for_approval used to send directly) and later resolves it with
    core.approvals.approve(id) / .deny(id).

    user/chat_id — who asked and which chat gets the result once approved
    (Telegram: same value today, kept distinct for frontends where they
    could differ). name — display name for the owner-facing card (mirrors
    bot.py's old auth._users[...]['name'] fallback)."""
    id: str
    user: int
    prompt: str
    chat_id: int = None
    name: str = ""


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
#  APPROVALS  (docs/dev/CORE_API.md migration step 5)
# ══════════════════════════════════════════════════════════
#
#  Moved from bot.py's module-level _pending_approvals/_make_approval/
#  _prune_approvals/_run_approved_request/_cb_approvals — the HOLD, the
#  TTL/cap policy, and the execution of an approved turn all live here now.
#  bot.py keeps only what genuinely needs Telegram: the ✅/❌ button
#  keyboard, the "Approval needed" card text, and delivering the result
#  (send_response) — everything that isn't interface I/O moved.
#
#  A small wrapper object rather than flat ZillaCore methods (matching
#  core.sessions / core.schedules) so the CORE_API vocabulary
#  ("core.approvals.pending()/.approve(id)/.deny(id)") reads exactly as
#  documented; the state itself lives on ZillaCore (_pending_approvals) —
#  this is a thin view over it, not a second source of truth.

class Approvals:
    def __init__(self, core: "ZillaCore"):
        self._core = core

    def _prune(self) -> None:
        """Forget un-actioned requests older than APPROVAL_TTL (mirrors
        bot.py's old _prune_approvals — called lazily on submit, same as
        before, not on a timer)."""
        now = time.time()
        store = self._core._pending_approvals
        for rid in [r for r, v in store.items() if now - v.get("ts", 0) > APPROVAL_TTL]:
            store.pop(rid, None)

    def pending(self) -> list:
        """Snapshot of every held request as {id, uid, chat_id, prompt, name, ts}."""
        return [{"id": rid, **req} for rid, req in self._core._pending_approvals.items()]

    def submit(self, uid: int, chat_id: int, prompt: str, name: str) -> str | None:
        """Register a limited user's request and broadcast ApprovalRequest so
        a frontend can notify the owner. Returns the request id, or None if
        the queue is already at APPROVAL_MAX (mirrors bot.py's old
        _make_approval — the frontend shows its 'too many requests waiting'
        notice on None, same as before)."""
        self._prune()
        store = self._core._pending_approvals
        if len(store) >= APPROVAL_MAX:
            return None
        rid = secrets.token_hex(6)
        store[rid] = {"uid": uid, "chat_id": chat_id, "prompt": prompt,
                      "name": name, "ts": time.time()}
        self._core._broadcast(ApprovalRequest(id=rid, user=uid, prompt=prompt,
                                              chat_id=chat_id, name=name))
        return rid

    async def approve(self, rid: str) -> dict | None:
        """Pop the held request and run it through the SAME turn pipeline a
        live chat message uses (core.handle_message) — same per-user lock,
        session pinning, and I-CONV/I-STEP handling; skip_permissions=True
        because the owner already vetted the whole request (mirrors bot.py's
        old _run_approved_request). Returns
        {id, uid, chat_id, prompt, name, ts, response} for the frontend to
        deliver, or None if the id is unknown/already resolved — the
        frontend then shows 'expired or already handled', same text as
        before. Exceptions from the turn propagate; the frontend applies its
        own friendly-error text and still delivers a reply, same as before."""
        req = self._core._pending_approvals.pop(rid, None)
        if req is None:
            return None
        uid, chat_id, prompt = req["uid"], req["chat_id"], req["prompt"]
        response = ""
        async for ev in self._core.handle_message(
                uid, prompt, chat_key=chat_id, auto_title=True, skip_permissions=True):
            if isinstance(ev, Response):
                response = ev.text
        return {**req, "id": rid, "response": response}

    def deny(self, rid: str) -> dict | None:
        """Discard a held request without running it. Returns the request
        (so the frontend can tell the requester it was declined), or None if
        the id is unknown/already resolved."""
        return self._core._pending_approvals.pop(rid, None)


# ══════════════════════════════════════════════════════════
#  CORE
# ══════════════════════════════════════════════════════════

class ZillaCore:
    """Owns everything that is not interface I/O. This seam: the turn
    pipeline + scheduler runtime + credential/OTP bridge + approvals. Health
    is the last seam left (see CORE_API.md).

    Shares the frontend's SessionManager/AuthManager instances so there is
    exactly one source of truth while bot.py still holds its own references.
    """

    def __init__(self, sessions, auth, schedules=None, owner_chat_id: int = None,
                 bridge_dir: str = None):
        self.sessions = sessions
        self.auth = auth
        # ScheduleManager, optional. None ⇒ this core runs no scheduler (used
        # by tests that only exercise the turn pipeline — start()/stop() are
        # then no-ops). bot.py always passes the real one.
        self.schedules = schedules

        # Human-in-the-loop credential/OTP bridge (docs/dev/CORE_API.md
        # migration step 4; file protocol in zilla/interactive.py).
        # owner_chat_id is the fallback target for an ask that carries no
        # chat_id of its own (e.g. an ask written by a scheduled/background
        # run rather than a live chat turn).
        self.owner_chat_id = owner_chat_id
        self._bridge_dir = bridge_dir or interactive.BRIDGE_DIR
        # Ask ids already broadcast via subscribe() — so the watcher never
        # re-announces the same ask (bot.py used to DM it once and remember).
        self._bridge_announced: set[str] = set()
        # Which ask each chat currently owes an answer for: chat key ->
        # (ask_id, announced_ts, is_secret). One outstanding ask per chat.
        self._pending_asks: dict[int, tuple[str, float, bool]] = {}
        self._bridge_task: asyncio.Task | None = None

        # Approval mode (docs/dev/CORE_API.md migration step 5; users.py role
        # "limited"): a held request, keyed by a short random id, until the
        # owner approves or denies it. See the Approvals class above for the
        # public surface (core.approvals.pending()/.submit()/.approve()/.deny()).
        self._pending_approvals: dict[str, dict] = {}
        self.approvals = Approvals(self)

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
        """Start background runtime: the scheduler loop (only if a
        ScheduleManager was provided) and the bridge watcher (CORE_API
        migration step 4 — always started; it is independent of the
        scheduler). The silent self-healing HEALTH LOOP (periodic re-check,
        self-heal, Alert only when a human must act) is Phase 7
        (see HANDOFF.md) — deliberately NOT started here yet. Step 6 only
        adds the point-in-time health_report() snapshot below."""
        if self.schedules is not None and self._sched_task is None:
            self._sched_task = asyncio.create_task(self._scheduler_loop())
        interactive.ensure_bridge_dir(self._bridge_dir)
        if self._bridge_task is None:
            self._bridge_task = asyncio.create_task(self._bridge_watcher_loop())

    async def stop(self):
        """Stop the scheduler loop and the bridge watcher, cleanly."""
        if self._sched_task is not None:
            self._sched_task.cancel()
            try:
                await self._sched_task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # pragma: no cover - defensive
                logger.error(f"[SCHED] stop() cleanup error: {e}")
            self._sched_task = None
        if self._bridge_task is not None:
            self._bridge_task.cancel()
            try:
                await self._bridge_task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # pragma: no cover - defensive
                logger.error(f"[BRIDGE] stop() cleanup error: {e}")
            self._bridge_task = None

    # ── health snapshot (CORE_API migration step 6 — STUB) ─
    #
    #  A point-in-time doctor snapshot assembled from EXISTING probe
    #  primitives only (config.agy_reachable/agy_models_live,
    #  backends.claude_identity, shutil.disk_usage) — no new probe logic.
    #  Deliberately a stub: the silent self-healing HEALTH LOOP (periodic
    #  re-check, self-heal, Alert events only when a human must act) is
    #  Phase 7 (see HANDOFF.md) and is NOT built here. Stable, plain-value
    #  keys so a future doctor command / TUI health screen can render this
    #  dict directly.

    def health_report(self, force: bool = False) -> dict:
        """Snapshot of: configured backend/model, per-CLI reachability/login
        state, disk free space (brain dir, falling back to home dir), and
        whether a scheduler/bridge are attached.

        force=False (default) uses each probe's cheap/cached form — this
        must NEVER trigger a live network/subprocess probe on its own (e.g.
        a TUI health screen rendering on every keystroke). force=True passes
        through to the probes that support it (claude_identity's own
        `force` kwarg; agy's `agy_models_live(force=True)` refreshes the
        cache that `agy_reachable()` then reads)."""
        backend = get_backend()
        model = get_model()

        if force:
            agy_models_live(force=True)
        agy_ok = agy_reachable()

        claude_status = claude_identity(force=force)
        claude_ok = bool(claude_status.get("loggedIn"))

        disk_path = BRAIN_DIR if os.path.isdir(BRAIN_DIR) else HOME_DIR
        try:
            usage = shutil.disk_usage(disk_path)
            free_bytes, total_bytes = usage.free, usage.total
        except OSError:
            free_bytes = total_bytes = None

        return {
            "backend": backend,
            "model": model,
            "clis": {
                "agy": {"reachable": agy_ok},
                "claude": {"reachable": claude_ok, "logged_in": claude_ok,
                           "auth_error": claude_status.get("error")},
            },
            "disk": {"path": disk_path, "free_bytes": free_bytes,
                     "total_bytes": total_bytes},
            "scheduler": {
                "attached": self.schedules is not None,
                "schedule_count": (self.schedules.count()
                                   if self.schedules is not None else 0),
            },
            "bridge": {"dir": self._bridge_dir,
                       "exists": os.path.isdir(self._bridge_dir)},
        }

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

    # ── credential/OTP bridge (CORE_API migration step 4) ──

    def pending_ask_for(self, chat_key: int) -> tuple | None:
        """If this chat currently owes an answer to a bridge ask, return
        (ask_id, is_secret) so the frontend can treat its next message as
        that answer instead of a normal turn. Returns None if there is
        nothing pending — including when the pending ask has gone stale
        (announced more than BRIDGE_PENDING_TTL seconds ago): the entry is
        popped and the ask file cleared here, exactly the old bot.py
        stale-release behavior, so the caller's next message flows on as a
        normal turn."""
        entry = self._pending_asks.get(chat_key)
        if not entry:
            return None
        ask_id, announced_ts, is_secret = entry
        if time.time() - announced_ts > BRIDGE_PENDING_TTL:
            self._pending_asks.pop(chat_key, None)
            interactive.clear_ask(ask_id, bridge_dir=self._bridge_dir)
            return None
        return ask_id, is_secret

    def answer_ask(self, ask_id: str, text: str) -> None:
        """Record the human's reply for a pending bridge ask and release the
        chat that owed it. Exceptions from interactive.write_answer (bad id,
        oversize value) propagate — the frontend renders the failure."""
        interactive.write_answer(ask_id, text, bridge_dir=self._bridge_dir)
        for key, (aid, _ts, _secret) in list(self._pending_asks.items()):
            if aid == ask_id:
                self._pending_asks.pop(key, None)

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
        # ── P1.5 triage: deterministic, zero-model-call route decision BEFORE
        # the heavy CLI turn / lock (HANDOFF.md P1.5). 'full' is the safe
        # default and falls straight through to the unchanged pipeline below.
        route = classify_route(text)

        if route == "share":
            ack = _append_to_journal(text)
            log_event("route", route="share", user=user_id)
            yield Response(text=ack, files=(),
                           meta={"session": None, "conv_id": None, "canceled": False})
            return

        if route == "smalltalk":
            fast_text = await asyncio.to_thread(_run_fast_claude, text)
            if fast_text is not None:
                result = review(text, fast_text)
                if result.verdict != "stop":
                    log_event("route", route="smalltalk", user=user_id, verdict=result.verdict)
                    yield Response(
                        text=fast_text,
                        files=tuple(detect_file_paths(fast_text or "")),
                        meta={"session": None, "conv_id": None, "canceled": False},
                    )
                    return
                log_event("route", route="smalltalk_reviewed_out", user=user_id,
                          reason=result.reason)
            else:
                log_event("route", route="smalltalk_unreachable", user=user_id)
            # Fast path failed review or Claude was unreachable — fall back to
            # the full path transparently (route falls through below).

        log_event("route", route="full", user=user_id)

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
            #
            # Outbound gate (Layer B — zilla/review.py). Only 'stop' changes
            # what ships: the fabrication retry already happened inline inside
            # cli_engine._run_blocking, so 'retry' here means that retry still
            # didn't resolve it — ship the (already-retried) text as-is rather
            # than looping again. A user-canceled turn's own "🛑 Canceled…"
            # text passes review() untouched (not empty, not a fail-prefix).
            final_text = response
            if not cancel_event.is_set():
                result = review(text, response)
                if result.verdict == "stop":
                    final_text = result.user_note or response
                log_event("review", verdict=result.verdict, reason=result.reason, user=user_id)

            yield Response(
                text=final_text,
                files=tuple(detect_file_paths(final_text or "")),
                meta={"session": sname, "conv_id": final_conv,
                      "canceled": cancel_event.is_set()},
            )
        finally:
            if self._active_cancel.get(key) is cancel_event:
                self._active_cancel.pop(key, None)

    # ══════════════════════════════════════════════════════
    #  CREDENTIAL/OTP BRIDGE WATCHER  (docs/dev/CORE_API.md migration step 4)
    # ══════════════════════════════════════════════════════
    #
    #  Moved from bot.py's bridge_watcher — polls the Bridge dir (file
    #  protocol in zilla/interactive.py) for asks the agent has written and
    #  broadcasts each one as an Ask event via subscribe(); Telegram is a
    #  pure renderer of it (bot.py's _deliver_ask). pending_ask_for/
    #  answer_ask (above) close the loop: a frontend checks whether a chat
    #  owes a reply, then hands the human's answer back through answer_ask.

    async def _bridge_poll_once(self) -> None:
        """One poll pass over the Bridge dir — factored out of the loop so
        tests can drive it deterministically without sleeping.

        If nobody is subscribed, skip announcing entirely: an ask must never
        be marked announced while no frontend can hear it (that would lose
        it forever instead of retrying next pass — the old bot.py behavior
        of "retry until deliverable")."""
        if self._subscribers:
            for ask in interactive.read_pending_asks(bridge_dir=self._bridge_dir):
                if ask.id in self._bridge_announced:
                    continue
                target = ask.chat_id or self.owner_chat_id
                if not target:
                    continue
                cur = self._pending_asks.get(target)
                if cur and cur[0] != ask.id:
                    continue  # one outstanding ask per chat at a time
                self._broadcast(Ask(id=ask.id, kind=ask.kind, prompt=ask.prompt,
                                     chat_id=target, is_secret=ask.is_secret))
                self._bridge_announced.add(ask.id)
                self._pending_asks[target] = (ask.id, time.time(), ask.is_secret)
                log_event("bridge_ask", kind=ask.kind, chat=target)

        interactive.expire_stale(bridge_dir=self._bridge_dir)
        # Forget announced asks that are gone (answered+cleared) so the maps
        # don't grow unbounded.
        live = {a.id for a in interactive.read_pending_asks(bridge_dir=self._bridge_dir)}
        for aid in list(self._bridge_announced):
            if aid not in live:
                self._bridge_announced.discard(aid)
                for cid, pv in list(self._pending_asks.items()):
                    if pv[0] == aid:
                        self._pending_asks.pop(cid, None)

    async def _bridge_watcher_loop(self) -> None:
        """Background loop: poll the Bridge dir every 2s, same cadence and
        error-swallowing as the old bot.py bridge_watcher. Inert when the
        agent isn't asking for anything."""
        logger.info("[BRIDGE] credential/OTP watcher started")
        while True:
            try:
                await self._bridge_poll_once()
            except Exception as e:
                logger.error(f"[BRIDGE] watcher error: {e}", exc_info=True)
            await asyncio.sleep(2)

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

        # Response-level failure detection (empty / rate-limited / error text) —
        # same deterministic gate live chat uses (zilla/review.py). A schedule
        # treats BOTH 'stop' and 'retry' verdicts as failure (feeding the retry
        # ladder in mark_failure); live chat's handle_message treats only
        # 'stop' that way since the fabrication retry already ran inline.
        if ok:
            result = review(s["prompt"], response)
            if result.verdict != "deliver":
                ok = False
                detail = (result.user_note or result.reason or "failed")[:200]
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
