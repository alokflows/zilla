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
import re
import secrets
import shutil
import threading
import time
import time as _time
from dataclasses import dataclass, field
from datetime import datetime

import zilla.interactive as interactive
from zilla.autoharness import needs_browser
from zilla.backends import claude_identity
from zilla.cli_engine import run_cli_async, get_latest_step
from zilla.config import (
    get_backend, get_model, get_setting,
    agy_reachable, agy_models_live, BRAIN_DIR, HOME_DIR,
)
from zilla.formatter import detect_file_paths
from zilla.harness import log_event, TurnContext
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

# Team relay (Phase K5, PLAN.md §6/K5): a proposed relay waits this long for
# the owner's ✅ before it is forgotten — an un-confirmed proposal is NOT a
# pending message, nothing is queued anywhere, it simply expires. The cap
# bounds the dict the same way APPROVAL_MAX does.
RELAY_TTL = 3600.0
RELAY_MAX = 20

# Background tasks (Phase B1, PLAN.md §9/B1): a proposal the model made and
# the owner hasn't tapped yet expires like a relay proposal does — nothing is
# queued anywhere, it simply stops being offered. The task rows themselves are
# durable (store.tasks_*); only the un-confirmed PROPOSAL is in memory.
BG_PROPOSAL_TTL = 3600.0
BG_PROPOSAL_MAX = 20


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
    to today's Memory/Journal/ file (PLAN.md §3.2) — the owner's own recall
    buffer. Returns the one-line ack to show the user."""
    from zilla import memory
    memory.append_journal(text)
    return "📝 Noted."


def _quiet_heartbeat_suppressed(s: dict, response: str) -> bool:
    """Phase M3.4: a system=1 schedule (H1's heartbeat beat, M4's nightly
    distillation — never a user's own schedule) whose response is or ends
    with a line reading exactly HEARTBEAT_OK (case-insensitive) delivers
    nothing; the tick still counts as a success, just quietly. A user
    schedule is never suppressed, even if its own legitimate output happens
    to end with that exact token — the `system` gate gets checked first."""
    if not s.get("system"):
        return False
    stripped = (response or "").rstrip()
    if not stripped:
        return False
    return stripped.splitlines()[-1].strip().casefold() == "heartbeat_ok"


# Phase F4 (PLAN.md §17): the ONE way a system job's output can reach the
# owner's chat (see ZillaCore._maybe_alert_owner_from_system_job below) —
# everything else a system job writes is log-only by design.
_OWNER_ALERT_RE = re.compile(r"^OWNER_ALERT:\s*(.+)$", re.MULTILINE)


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
class RelayRequest:
    """Phase K5 (PLAN.md §6/K5): the model proposed reaching a real person
    on the owner's behalf, the alias resolved to a person page carrying a
    `telegram_uid::`, and the action is now held pending the owner's ✅.
    Broadcast the moment core.relay.submit() registers the hold; a frontend
    renders `card` with confirm/cancel buttons and later resolves `id` with
    core.relay.confirm(id) / .cancel(id).

    **Nothing has been sent when this event fires** — no confirm ⇒ nothing
    sends, ever (owner decision 2026-07-18: always-confirm, no
    trusted-contact bypass).

    kind — "send" (deliver now) or "schedule" (create a recurring/one-off
    delivery). name/target_uid — the RESOLVED person, never the alias the
    model used. card — the owner-facing confirm text (resolved name + the
    exact text about to go out). summary — one-line version for logs."""
    id: str
    kind: str
    alias: str
    name: str
    target_uid: int
    card: str
    summary: str = ""


@dataclass
class TaskProposal:
    """Phase B1 (PLAN.md §9/B1 step 2): the model ended an OWNER turn with a
    `BG_TASK:` marker — it wants work moved to the background lane. Nothing
    is queued when this fires: a frontend renders `card` with a confirm tap
    and resolves `id` with core.tasks.accept(id) / .decline(id). The model
    cannot spawn work without that tap (or the owner typing /bg).
    """
    id: str
    uid: int
    prompt: str
    card: str
    chat_id: int = None


@dataclass
class TaskResult:
    """Phase B1 (PLAN.md §9/B1 step 3): a background task reached a terminal
    state. Telegram renders the header card + the result body; the TUI gets
    its own Tasks screen in Phase T.

    status — 'done' | 'failed' | 'canceled'. response is the answer text for
    a completed task (and whatever partial output there was for a failed
    one). duration is wall-clock seconds, or None if the row lost its start
    time. card is the validated ZUI header block (zilla/tasks.result_card).
    """
    id: str
    uid: int
    title: str
    status: str
    response: str = ""
    chat_id: int = None
    duration: float = None
    card: dict = None


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
                uid, prompt, chat_key=chat_id, auto_title=True, skip_permissions=True,
                origin="approval"):
            if isinstance(ev, Response):
                response = ev.text
        return {**req, "id": rid, "response": response}

    def deny(self, rid: str) -> dict | None:
        """Discard a held request without running it. Returns the request
        (so the frontend can tell the requester it was declined), or None if
        the id is unknown/already resolved."""
        return self._core._pending_approvals.pop(rid, None)


# ══════════════════════════════════════════════════════════
#  TEAM RELAY  (Phase K5 — PLAN.md §6/K5)
# ══════════════════════════════════════════════════════════
#
#  Same shape as Approvals above (id-keyed dict on ZillaCore + a broadcast
#  event), for the same reason: the pending action is born inside core.py
#  (marker-parsed out of a model reply), so it can't live in a frontend's
#  per-chat state. It is deliberately in-memory — an un-confirmed relay
#  proposal is not a queued message, and a restart forgetting it is the
#  correct, safe behavior.
#
#  The audit trail (store.relay_log) records only CONFIRMED actions: a
#  proposal that was denied or expired never left this machine.

class Relay:
    def __init__(self, core: "ZillaCore"):
        self._core = core

    def _db(self):
        from zilla import store as _store
        from zilla.config import DB_FILE
        return _store.get_store(DB_FILE)

    def _prune(self) -> None:
        """Forget proposals older than RELAY_TTL — lazily, on the next
        submit (same policy as Approvals; no timer loop)."""
        now = time.time()
        store = self._core._pending_relays
        for rid in [r for r, v in store.items() if now - v.get("ts", 0) > RELAY_TTL]:
            store.pop(rid, None)

    def pending(self) -> list:
        return [{"id": rid, **entry} for rid, entry in self._core._pending_relays.items()]

    def peek(self, rid: str) -> dict | None:
        entry = self._core._pending_relays.get(rid)
        return {"id": rid, **entry} if entry else None

    def submit(self, action: dict, target: dict, owner_uid: int) -> str | None:
        """Hold a resolved relay action and broadcast RelayRequest so a
        frontend can ask the owner. Returns the id, or None if the queue is
        full (the frontend says so in one line)."""
        from zilla import relay as _relay
        self._prune()
        store = self._core._pending_relays
        if len(store) >= RELAY_MAX:
            return None
        rid = secrets.token_hex(6)
        summary = _relay.summarize(action)
        store[rid] = {"action": action, "owner_uid": owner_uid,
                      "name": target.get("name") or target["alias"],
                      "alias": target["alias"], "target_uid": target["uid"],
                      "summary": summary, "ts": time.time()}
        self._core._broadcast(RelayRequest(
            id=rid, kind=action["kind"], alias=target["alias"],
            name=store[rid]["name"], target_uid=target["uid"],
            card=_relay.confirm_card(action, target), summary=summary,
        ))
        return rid

    def confirm(self, rid: str) -> dict | None:
        """Pop a held proposal and ACT on the owner's ✅.

        `RELAY_SCHEDULE` is completed here — core owns the scheduler, so the
        row is created (uid = owner, chat_id = the target, payload_type =
        system_event: verbatim delivery, zero model call, no re-generation
        drift) and logged. `RELAY_SEND` is a Telegram send, which core does
        not do — the entry comes back with ok=True for the frontend to
        deliver, which then calls mark_sent(). Returns None if the id is
        unknown/expired/already handled (double-tap)."""
        entry = self._core._pending_relays.pop(rid, None)
        if entry is None:
            return None
        entry = {"id": rid, **entry}
        action = entry["action"]
        if action["kind"] != "schedule":
            entry["ok"] = True
            return entry
        row = None
        try:
            row = self._core.schedules.add(
                user_id=entry["owner_uid"], chat_id=entry["target_uid"],
                prompt=action["text"], kind=action["sched_kind"], spec=action["spec"],
                title=f"→ {entry['name']}: {action['text']}",
                payload_type="system_event", is_owner=True,
            )
        except Exception as e:
            logger.error(f"[RELAY] schedule create failed: {e}", exc_info=True)
        entry["ok"] = row is not None
        entry["schedule"] = row
        self._log(entry, "scheduled" if row else "failed")
        return entry

    def cancel(self, rid: str) -> dict | None:
        """Discard a proposal without acting. Nothing is logged — nothing
        happened."""
        entry = self._core._pending_relays.pop(rid, None)
        return {"id": rid, **entry} if entry else None

    def mark_sent(self, entry: dict, ok: bool) -> None:
        """Record the outcome of a confirmed RELAY_SEND after the frontend
        actually tried to deliver it."""
        self._log(entry, "sent" if ok else "failed")

    def _log(self, entry: dict, status: str) -> None:
        try:
            self._db().relay_log_add(
                ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                kind=entry["action"]["kind"], alias=entry.get("alias", ""),
                name=entry.get("name", ""), target_uid=entry.get("target_uid"),
                summary=entry.get("summary", ""), status=status,
            )
        except Exception as e:  # the audit write must never break delivery
            logger.error(f"[RELAY] audit log write failed: {e}")
        log_event("relay", status=status, kind=entry["action"]["kind"],
                  target=entry.get("target_uid"))

    def recent(self, limit: int = 20) -> list[dict]:
        """The `/relay log` audit trail — newest first."""
        try:
            return self._db().relay_log_recent(limit)
        except Exception as e:
            logger.error(f"[RELAY] audit log read failed: {e}")
            return []


# ══════════════════════════════════════════════════════════
#  BACKGROUND TASKS  (Phase B1 — PLAN.md §9/B1)
# ══════════════════════════════════════════════════════════
#
#  The lane that keeps the chat free. The ONE invariant this class exists to
#  hold, and the reason it is not just "a scheduled job that runs now":
#
#      A BACKGROUND TASK NEVER TOUCHES THE PER-USER CHAT LOCK.
#
#  Every other run path in this file (handle_message, _execute_message_
#  schedule, approvals) acquires `get_user_lock(uid)` — correct there,
#  because those all write back to the user's ACTIVE session and would
#  otherwise interleave on one conversation (docs/dev/AI_CONTEXT.md I-CONV).
#  A task writes to its OWN session (`task:<id>`) and its own fresh
#  conversation, so there is nothing to serialize against the chat: it takes
#  a task-scoped lock instead, and the owner can keep talking while it runs.
#  (The agy global new-conv detection lock still applies for the moment the
#  conversation is created — unavoidable, and brief.)
#
#  Durability split: the row is in SQLite (survives a restart, so a crashed
#  job leaves evidence), the cancel event and the asyncio task are in memory.
#  A row still marked `running` at boot is reconciled to `failed`, never
#  resurrected — silently re-running an agentic prompt nobody is watching is
#  not a safe default.

class Tasks:
    def __init__(self, core: "ZillaCore"):
        self._core = core

    def _db(self):
        from zilla import store as _store
        from zilla.config import DB_FILE
        return _store.get_store(DB_FILE)

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def max_concurrent(self) -> int:
        """`max_bg_tasks` (default 2). Coerced and floored at 1 — a setting
        typed as "two" or 0 must not silently stop the lane forever."""
        from zilla.tasks import DEFAULT_MAX_BG_TASKS
        try:
            return max(1, int(get_setting("max_bg_tasks", DEFAULT_MAX_BG_TASKS)))
        except (TypeError, ValueError):
            return DEFAULT_MAX_BG_TASKS

    # ── proposals (model asked; owner hasn't tapped) ───────

    def _prune_proposals(self) -> None:
        now = time.time()
        store = self._core._pending_bg
        for pid in [p for p, v in store.items() if now - v.get("ts", 0) > BG_PROPOSAL_TTL]:
            store.pop(pid, None)

    def pending(self) -> list:
        return [{"id": pid, **entry} for pid, entry in self._core._pending_bg.items()]

    def propose(self, prompt: str, uid: int, chat_id: int = None) -> str | None:
        """Hold a `BG_TASK:` proposal and broadcast TaskProposal so a
        frontend can ask for the tap. Returns the proposal id, or None if
        too many are already waiting."""
        from zilla import tasks as _tasks
        self._prune_proposals()
        store = self._core._pending_bg
        if len(store) >= BG_PROPOSAL_MAX:
            return None
        pid = secrets.token_hex(6)
        store[pid] = {"prompt": prompt, "uid": uid, "chat_id": chat_id,
                      "ts": time.time()}
        self._core._broadcast(TaskProposal(id=pid, uid=uid, prompt=prompt,
                                           chat_id=chat_id,
                                           card=_tasks.confirm_card(prompt)))
        return pid

    async def accept(self, pid: str) -> dict | None:
        """The owner tapped ✅ on a proposal — create the task. None if the
        id is unknown/expired/already handled (double-tap)."""
        entry = self._core._pending_bg.pop(pid, None)
        if entry is None:
            return None
        return await self.submit(entry["uid"], entry.get("chat_id"), entry["prompt"])

    def decline(self, pid: str) -> dict | None:
        """Discard a proposal. Nothing was created, so nothing is recorded."""
        entry = self._core._pending_bg.pop(pid, None)
        return {"id": pid, **entry} if entry else None

    # ── the lane ───────────────────────────────────────────

    async def submit(self, uid: int, chat_id: int | None, prompt: str) -> dict | None:
        """Create a task and start it if a lane is free, else queue it.
        Returns the stored row (with "queued": True when it had to wait), or
        None if the backlog is already at MAX_PENDING."""
        from zilla import tasks as _tasks
        db = self._db()
        prompt = (prompt or "").strip()[:_tasks.MAX_PROMPT]
        if not prompt:
            return None
        if db.tasks_count_by_status(_tasks.LIVE_STATUSES) >= _tasks.MAX_PENDING:
            return None
        tid = secrets.token_hex(4)
        db.tasks_add(tid=tid, uid=uid, chat_id=chat_id, prompt=prompt,
                     title=_tasks.title_for(prompt), status=_tasks.QUEUED,
                     created_at=self._now())
        log_event("bg_task_created", id=tid, user=uid)
        await self.pump()
        row = db.tasks_get(tid) or {}
        return {**row, "queued": row.get("status") == _tasks.QUEUED}

    async def pump(self) -> None:
        """Start queued tasks while the concurrency cap allows. The row is
        claimed (status -> running) BEFORE the coroutine is spawned, so two
        pumps racing on the same tick can never start the same task twice."""
        from zilla import tasks as _tasks
        db = self._db()
        while db.tasks_count_by_status((_tasks.RUNNING,)) < self.max_concurrent():
            waiting = db.tasks_by_status((_tasks.QUEUED,), limit=1)
            if not waiting:
                return
            tid = waiting[0]["id"]
            db.tasks_update(tid, status=_tasks.RUNNING, started_at=self._now(),
                            progress="")
            self._core._bg_runners[tid] = asyncio.create_task(self._run(tid))

    def _lock_for(self, tid: str) -> asyncio.Lock:
        lock = self._core._bg_locks.get(tid)
        if lock is None:
            lock = asyncio.Lock()
            self._core._bg_locks[tid] = lock
        return lock

    async def _run(self, tid: str) -> None:
        """Execute one claimed task. Deliberately mirrors
        _execute_message_schedule's shape — same review()-based failure
        classification, same conv-id bookkeeping — with the per-user lock
        swapped for a task-scoped one."""
        from zilla import tasks as _tasks
        db = self._db()
        row = db.tasks_get(tid)
        if row is None:
            return
        uid = row["uid"]
        sname = f"task:{tid}"
        started = time.time()
        cancel_event = threading.Event()
        self._core._bg_cancels[tid] = cancel_event

        last_write = [0.0]

        def _on_progress(step: str) -> None:
            # Called from the backend's worker thread. Throttled: a step a
            # second is normal and the board only ever shows the latest one.
            now = time.time()
            if now - last_write[0] < 2.0:
                return
            last_write[0] = now
            try:
                db.tasks_update(tid, progress=(step or "")[:200])
            except Exception:
                pass

        ok, response, detail = True, "", ""
        try:
            async with self._lock_for(tid):
                ctx = TurnContext(
                    uid=uid, role=self._core.auth.role_of(uid) if self._core.auth else "admin",
                    is_owner=bool(self._core.auth and self._core.auth.is_owner(uid)),
                    origin="task",
                )
                response, detected = await run_cli_async(
                    row["prompt"], None,
                    progress_callback=_on_progress,
                    cancel_event=cancel_event,
                    skip_permissions=(self._core.auth.can(uid, "admin")
                                      if self._core.auth else False),
                    ctx=ctx,
                )
                if detected:
                    self._core.sessions.set_conversation_id(
                        detected, user_id=uid, session_name=sname,
                        backend=get_backend())
        except Exception as e:
            ok, detail, response = False, str(e), ""
            logger.error(f"[TASK] {tid} failed: {e}", exc_info=True)

        canceled = cancel_event.is_set()
        if ok and not canceled:
            result = review(row["prompt"], response)
            if result.verdict != "deliver":
                ok = False
                detail = (result.user_note or result.reason or "failed")[:200]

        # A background job may not act on the owner's behalf and may not
        # spawn more work: strip both marker families from its output and
        # honor neither. The owner never asked for a relay from something
        # they aren't watching (PLAN.md §9 — nothing outward-facing without
        # a confirm tap).
        response = self._strip_markers(response)

        status = (_tasks.CANCELED if canceled
                  else _tasks.DONE if ok else _tasks.FAILED)
        duration = time.time() - started
        try:
            db.tasks_update(tid, status=status, progress="",
                            result=(response or detail or "")[:20000],
                            finished_at=self._now())
        except Exception as e:
            logger.error(f"[TASK] {tid} could not be recorded: {e}")
        self._record_usage(ok and not canceled)
        log_event("bg_task_end", id=tid, user=uid, status=status,
                  secs=round(duration, 1), detail=detail[:200])

        # The session row existed only so H1's sweep could see this
        # conversation as referenced while it ran. Dropping it now hands the
        # brain dir back to that sweep (PLAN.md §9/B1 step 1).
        try:
            self._core.sessions.delete_session(sname, uid)
        except Exception:
            pass
        self._core._bg_cancels.pop(tid, None)
        self._core._bg_locks.pop(tid, None)
        self._core._bg_runners.pop(tid, None)

        final = db.tasks_get(tid) or row
        self._core._broadcast(TaskResult(
            id=tid, uid=uid, chat_id=row.get("chat_id"),
            title=final.get("title") or _tasks.title_for(row["prompt"]),
            status=status, response=response or "", duration=duration,
            card=self._header_card(final, duration),
        ))
        await self.pump()

    @staticmethod
    def _strip_markers(text: str) -> str:
        try:
            from zilla import relay as _relay
            from zilla import tasks as _tasks
            clean, actions = _relay.parse_markers(text or "")
            clean, proposals = _tasks.parse_markers(clean)
            if actions or proposals:
                log_event("bg_task_markers_dropped",
                          relays=len(actions), tasks=len(proposals))
            return clean
        except Exception:
            return text

    @staticmethod
    def _header_card(row: dict, duration: float | None) -> dict | None:
        from zilla import tasks as _tasks
        from zilla import zui as _zui
        return _zui.validate(_tasks.result_card(row, duration))

    def _record_usage(self, ok: bool) -> None:
        """A background run spends the same rented quota a chat turn does —
        count it, so the usage view stays honest (PLAN.md §9/B1 step 1)."""
        try:
            self._db().usage_bump(datetime.now().strftime("%Y-%m-%d"),
                                  get_backend(), turns=1,
                                  errors=0 if ok else 1)
        except Exception as e:
            logger.debug(f"[TASK] usage bump failed: {e}")

    # ── control surface (/tasks) ───────────────────────────

    def get(self, tid: str) -> dict | None:
        try:
            return self._db().tasks_get(tid)
        except Exception:
            return None

    def cancel(self, tid: str) -> dict | None:
        """I-CANCEL semantics: a RUNNING task's cancel event is set and the
        backend stops; a QUEUED task never starts. Returns the row, or None
        if the id is unknown or the task already finished."""
        from zilla import tasks as _tasks
        db = self._db()
        row = db.tasks_get(tid)
        if row is None or row.get("status") not in _tasks.LIVE_STATUSES:
            return None
        event = self._core._bg_cancels.get(tid)
        if event is not None and not event.is_set():
            event.set()
            log_event("bg_task_cancel", id=tid, was="running")
            return row
        db.tasks_update(tid, status=_tasks.CANCELED, finished_at=self._now(),
                        progress="")
        log_event("bg_task_cancel", id=tid, was=row.get("status"))
        return db.tasks_get(tid)

    async def retry(self, tid: str) -> dict | None:
        """Run a finished task's prompt again as a NEW task (the old row
        stays as history). None if the id is unknown."""
        row = self.get(tid)
        if row is None:
            return None
        return await self.submit(row["uid"], row.get("chat_id"), row["prompt"])

    def board(self, uid: int | None = None) -> dict:
        """The `/tasks` view: running, queued, and the last few finished."""
        from zilla import tasks as _tasks
        db = self._db()
        return {
            "running": db.tasks_by_status((_tasks.RUNNING,), uid=uid),
            "queued": db.tasks_by_status((_tasks.QUEUED,), uid=uid),
            "finished": db.tasks_by_status(_tasks.TERMINAL_STATUSES, uid=uid,
                                           limit=_tasks.BOARD_FINISHED,
                                           newest_first=True),
        }

    def reconcile_startup(self) -> int:
        """Rows left `running` by a process that died: mark them failed so
        the board tells the truth and the cap isn't held by ghosts. Returns
        how many were reconciled."""
        from zilla import tasks as _tasks
        db = self._db()
        stale = db.tasks_by_status((_tasks.RUNNING,))
        for row in stale:
            db.tasks_update(row["id"], status=_tasks.FAILED,
                            finished_at=self._now(), progress="")
            log_event("bg_task_orphaned", id=row["id"])
        return len(stale)


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

        # Team relay (Phase K5): relay actions the model proposed on an
        # owner turn, held by short random id until the owner confirms.
        # In-memory on purpose — see the Relay class above.
        self._pending_relays: dict[str, dict] = {}
        self.relay = Relay(self)

        # Background lane (Phase B1). The rows live in SQLite; these three
        # maps are the live half — the un-confirmed proposals, the cancel
        # event per running task, its asyncio task, and its OWN lock (never
        # the per-user chat lock — see the Tasks class docstring).
        self._pending_bg: dict[str, dict] = {}
        self._bg_cancels: dict[str, threading.Event] = {}
        self._bg_runners: dict[str, asyncio.Task] = {}
        self._bg_locks: dict[str, asyncio.Lock] = {}
        self.tasks = Tasks(self)

        # Per-(chat, user) cancel events — set to cancel the active CLI
        # request for that user in that chat. Keyed by a (chat_key, user_id)
        # tuple, NOT chat_key alone: a group chat's chat_key is shared by
        # every sender, so chat_key-only keying let one user's /cancel stop
        # a DIFFERENT user's run in the same group (STATUS.md audit
        # finding). chat_key defaults to user_id for frontends without a
        # separate chat concept (TUI/CLI), which still keys uniquely.
        self._active_cancel: dict[tuple[int, int], threading.Event] = {}

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

        # Phase M3.3: git-autocommit Memory/ after a turn/scheduled run that
        # changed it. Defaults OFF (unlike every other seam here, which is
        # safe-by-construction) because test_schedules_seam.py is a frozen
        # acceptance spec that constructs ZillaCore directly and calls
        # _run_and_record/run_schedule_now WITHOUT isolating zilla.memory's
        # MEMORY_DIR — turning this on unconditionally would make running
        # that file `git init`/commit into the real repo's Memory/ tree.
        # bot.py's real startup sets this True right after construction; every
        # test (including the ones this session adds for M3 itself) opts in
        # explicitly against an isolated MEMORY_DIR instead.
        self.memory_autocommit_enabled = False

        self._sched_task: asyncio.Task | None = None

        # Recursion guard: uids whose CURRENT turn was started by a schedule.
        # bot.py's NL schedule-detection checks is_scheduled_run() so a
        # schedule-triggered turn can never create more schedules.
        self._scheduled_running: set[int] = set()

        # Phase H2 (PLAN.md §6): the health probe loop is OFF by default —
        # same opt-in pattern as memory_autocommit_enabled above. Every
        # test-constructed ZillaCore (including ones that call start()) must
        # NOT spawn real `agy models`/`claude -p ping` subprocesses on a
        # background timer; only bot.py's real main() turns this on.
        self.health_probes_enabled = False
        self._health_task: asyncio.Task | None = None

        # F3 (PLAN.md §17): the media retention sweep is OFF by default —
        # same opt-in pattern as health_probes_enabled/memory_autocommit_enabled
        # above, for the same reason: a test-constructed ZillaCore must never
        # delete real Inbox/Outbox files on a background timer.
        self.media_sweep_enabled = False
        self._media_sweep_task: asyncio.Task | None = None

    # ── lifecycle ───────────────────────────────────────────

    async def start(self):
        """Start background runtime: the scheduler loop (only if a
        ScheduleManager was provided), the bridge watcher (CORE_API
        migration step 4 — always started; it is independent of the
        scheduler), and — only if health_probes_enabled — the H2 health
        probe loop (see HANDOFF.md). Step 6 originally only added the
        point-in-time health_report() snapshot; H2 adds the periodic,
        self-healing, alert-on-human-needed loop on top of it."""
        if self.schedules is not None and self._sched_task is None:
            self._sched_task = asyncio.create_task(self._scheduler_loop())
        # Phase B1: rows a previous process left mid-run are failed, not
        # resurrected; anything still queued picks up where it left off.
        try:
            self.tasks.reconcile_startup()
            await self.tasks.pump()
        except Exception as e:
            logger.error(f"[TASK] startup reconcile failed: {e}")
        interactive.ensure_bridge_dir(self._bridge_dir)
        if self._bridge_task is None:
            self._bridge_task = asyncio.create_task(self._bridge_watcher_loop())
        if self.health_probes_enabled and self._health_task is None:
            self._health_task = asyncio.create_task(self._health_loop())
        if self.media_sweep_enabled and self._media_sweep_task is None:
            self._media_sweep_task = asyncio.create_task(self._media_sweep_loop())

    async def stop(self):
        """Stop the scheduler loop, the bridge watcher, the health
        probe loop, and the media sweep loop, cleanly."""
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
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # pragma: no cover - defensive
                logger.error(f"[HEALTH] stop() cleanup error: {e}")
            self._health_task = None
        # Phase B1: stop the backends of any running task before the loop
        # goes away. The row stays `running` and the next start reconciles
        # it to failed — a job the owner can re-run beats a ghost lane.
        for tid, runner in list(self._bg_runners.items()):
            event = self._bg_cancels.get(tid)
            if event is not None:
                event.set()
            runner.cancel()
        self._bg_runners.clear()
        if self._media_sweep_task is not None:
            self._media_sweep_task.cancel()
            try:
                await self._media_sweep_task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # pragma: no cover - defensive
                logger.error(f"[MEDIA] stop() cleanup error: {e}")
            self._media_sweep_task = None

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

    def cancel(self, chat_key: int, user_id: int) -> bool:
        """Cancel the active CLI run for this (chat, user) pair. Returns
        True if a live (not yet set) cancel event was found and set. Both
        must match — see _active_cancel's comment for why chat_key alone
        isn't enough in group chats."""
        cancel_ev = self._active_cancel.get((chat_key, user_id))
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
                             auto_title: bool = False, skip_permissions: bool = None,
                             origin: str = "user", untrusted_input: bool = False):
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
        origin: why this turn is running ('user' live chat, 'approval') — feeds
        the TurnContext that gates memory injection (harness.py, PLAN.md §4).
        Schedule-triggered turns are handled by a separate method
        (_execute_message_schedule) that does not build a TurnContext yet —
        a known, deliberate gap (frozen test_schedules_seam.py fakes can't
        accept a new kwarg; see HANDOFF.md).
        untrusted_input: True when the frontend already knows this turn's
        prompt carries untrusted content beyond plain typed text (e.g. an
        uploaded document's extracted text) — combined with a non-owner
        principal and browser-intent detection to decide whether a memory
        write this turn triggers gets surfaced to the owner (PLAN.md §5.M4
        step 2). Never gates anything else — plain visibility.
        """
        # ── P1.5 triage: deterministic, zero-model-call route decision BEFORE
        # the heavy CLI turn / lock (HANDOFF.md P1.5). 'full' is the safe
        # default and falls straight through to the unchanged pipeline below.
        route = classify_route(text)

        # Journal is the OWNER's memory (PLAN.md §4 scope guard) — any other
        # principal's "share"-shaped message falls through to the full route
        # instead of silently writing into it.
        if route == "share" and not self.auth.is_owner(user_id):
            route = "full"

        # Phase B2: the share route writes the message verbatim into the
        # journal and commits it — the one thing a private session must never
        # do. It runs before the lock, so it has to be gated here rather than
        # by the post-turn enforcement below: fall through to the full route,
        # which records nothing.
        if route == "share" and self._is_incognito(
                user_id, self.sessions.get_active_name(user_id)):
            route = "full"

        if route == "share":
            ack = _append_to_journal(text)
            log_event("route", route="share", user=user_id)
            yield Response(text=ack, files=(),
                           meta={"session": None, "conv_id": None, "canceled": False})
            await self._autocommit_memory(f"journal entry — uid {user_id}")
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
        key = (user_id if chat_key is None else chat_key, user_id)
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
        incognito = False
        mem_before = None
        try:
            async with self.get_user_lock(user_id):
                # Pin the session to whatever is active the moment WE start running, and
                # write every result back to that same session — never the now-active one.
                self._active_cancel[key] = cancel_event
                sname = self.sessions.get_active_name(user_id)
                conv_id = self._conv_for_run(user_id, sname)

                # Phase B2 (PLAN.md §9/B2): pinned with the session, so a
                # /switch mid-queue can't turn a private turn into a
                # recorded one or the reverse. The snapshot is taken inside
                # the lock, immediately before the run, so the comparison
                # afterwards can only see what THIS turn changed.
                incognito = self._is_incognito(user_id, sname)
                if incognito:
                    from zilla import memory as _memory
                    mem_before = await asyncio.to_thread(_memory.tree_snapshot)

                if auto_title:
                    info = self.sessions.get_session_info(user_id=user_id, session_name=sname)
                    if info and info.get("messages", 0) == 0:
                        self.sessions.auto_title(text, user_id=user_id, session_name=sname)

                ctx = TurnContext(
                    uid=user_id, role=self.auth.role_of(user_id),
                    is_owner=self.auth.is_owner(user_id), origin=origin,
                    incognito=incognito,
                )
                run_task = loop.create_task(run_cli_async(
                    text, conv_id,
                    progress_callback=_on_progress,
                    cancel_event=cancel_event,
                    skip_permissions=skip_permissions,
                    ctx=ctx,
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

            # Phase K5: strip any relay marker the model proposed and turn it
            # into a confirm card. Runs after review() so the owner-facing
            # text is final, and before Response is yielded so the raw
            # protocol never reaches a chat.
            final_text = self._process_relay_markers(final_text, ctx)

            # Phase B1: a `BG_TASK:` marker becomes a confirm card, never a
            # running job — same strip-then-hold discipline as the relay
            # markers above.
            final_text = self._process_bg_markers(final_text, ctx, chat_key)

            yield Response(
                text=final_text,
                files=tuple(detect_file_paths(final_text or "")),
                meta={"session": sname, "conv_id": final_conv,
                      "canceled": cancel_event.is_set(),
                      "incognito": incognito},
            )
            if incognito:
                # Phase B2: an incognito turn is never committed to the
                # memory repo — it is checked against it, and anything the
                # model wrote anyway is put back.
                await self._enforce_incognito(mem_before)
            else:
                untrusted = untrusted_input or not ctx.is_owner or needs_browser(text)
                await self._autocommit_memory(f"chat turn — uid {user_id}",
                                              untrusted=untrusted)
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

    async def _autocommit_memory(self, context: str, *, untrusted: bool = False) -> None:
        """Phase M3.3: git-commit Memory/ if this turn/run changed it. A
        no-op unless memory_autocommit_enabled (see __init__ for why it
        defaults off). Runs the git subprocess calls off the event loop —
        memory.git_autocommit() itself never raises, so this can't break a
        reply/delivery either way.

        Phase M4 step 2 (PLAN.md §5.M4, the §12.9 injection-surface
        mitigation): when `untrusted` is True (this run's inputs included
        untrusted content — a document-ingest or browser-bearing turn — or
        the run was non-owner-originated) AND a commit actually happened,
        DM the owner one line surfacing the change. Deterministic,
        code-level: detection and visibility, not prevention."""
        if not self.memory_autocommit_enabled:
            return
        from zilla import memory as _memory
        committed = await asyncio.to_thread(_memory.git_autocommit, context)
        if not (committed and untrusted):
            return
        stat = await asyncio.to_thread(_memory.git_last_commit_stat)
        if not stat:
            return
        files = ", ".join(stat["files"][:5])
        if len(stat["files"]) > 5:
            files += f" (+{len(stat['files']) - 5} more)"
        self._broadcast(Alert(
            text=f"🔏 memory changed during this run: {files} ({stat['hash']})"
        ))

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
        if s.get("system") and mode == "isolated" and get_backend() == "agy":
            # Phase H1 step 3 (PLAN.md §6): a fresh agy conversation holds
            # the GLOBAL new-conv detection lock (30s, serializes ALL
            # users' new conversations) and gets the full onboarding
            # preamble — 48 beats/day of that is the slowest possible
            # design. Reuse one persistent scratch conversation per system
            # schedule instead. claude/opencode fresh conversations are
            # cheap, so isolated mode is left alone there (a persistent
            # conv there would only grow unboundedly for no benefit).
            mode = f"named:__scratch_{s['id']}"
        ok, detail, response, conv_id = True, "", "", None
        self._scheduled_running.add(uid)
        try:
            async with self.get_user_lock(uid):
                sname = self._sname_for_mode(uid, mode)
                if sname:
                    conv_id = self._conv_for_run(uid, sname)
                # No ctx=/TurnContext here (M2 known gap, owner-confirmed deferral):
                # test_schedules_seam.py is a frozen acceptance spec whose fake_run
                # mocks have fixed signatures and would TypeError on a new kwarg.
                # Schedule-triggered turns get no memory injection until a later
                # phase. See HANDOFF.md.
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

    async def _run_and_record_system(self, s: dict) -> None:
        """Phase H1 step 1 (PLAN.md §6/H1): the tick-loop path for a
        system=1 schedule (H1's heartbeat beat, M4's nightly distillation) —
        deliberately NOT the user-schedule path below. The blocking-acquire
        + RETRY_LADDER + give-up DM in _run_and_record is correct for a
        user's own job; it is wrong here (a backend outage would DM the
        owner hourly all night, and a slept-through beat catching up would
        hold the owner's lock during their first morning message). Instead:
        try-acquire the per-uid lock and skip this tick entirely if busy (no
        blocking wait, no queueing), no retry ladder on failure, no give-up
        DM — a system job always advances to its next occurrence regardless
        of outcome. Failures are only logged; H2's cooldown-gated alerts are
        the sole surfacing mechanism for a persistently failing system job."""
        sid = s["id"]
        title = s.get("title", "")
        uid = s["user_id"]

        from zilla import heartbeat as _heartbeat
        prepared = _heartbeat.prepare_beat(s)
        if prepared is None:
            # Deterministic pre-check said skip (PLAN.md §6/H1 step 2):
            # HEARTBEAT.md is missing/empty — zero AI calls this tick.
            log_event("schedule_system_skip_empty", id=sid, title=title[:40])
            self.schedules.touch_run(sid)
            return
        s = prepared

        if s.get("payload_type", "message") == "message" and self.get_user_lock(uid).locked():
            # Only "message" payloads touch the per-uid lock at all —
            # system_event/command schedules never contend it, so they are
            # never skipped for being "busy".
            log_event("schedule_system_skip_busy", id=sid, title=title[:40])
            self.schedules.touch_run(sid)
            return
        ok, response, detail, meta = await self._execute_schedule(s)
        if meta.get("pin_mismatch"):
            self._maybe_notify_backend_pin(s)
        sched_untrusted = not (self.auth and self.auth.is_owner(s.get("user_id")))
        await self._autocommit_memory(f"schedule — {title}"[:80], untrusted=sched_untrusted)
        self.schedules.touch_run(sid)
        if ok:
            # Phase F4 (PLAN.md §17) output contract: a system job never
            # announces itself — no "⏰ Scheduled — <title>" header, no
            # result DM, ever (this SUPERSEDES the old HEARTBEAT_OK
            # quiet-run distinction for system jobs: EVERY successful
            # system-job output is silent by default now, not just the
            # ones ending in that exact token). The full output still
            # goes to the log; the only thing that can reach the owner's
            # chat is a cooldown-gated OWNER_ALERT: line.
            log_event("schedule_ok", id=sid, title=title[:40], response=(response or "")[:500])
            self._maybe_alert_owner_from_system_job(sid, response)
            return
        log_event("schedule_failed", id=sid, title=title[:40], detail=(detail or "")[:200])
        # No retry ladder, no give-up DM — see docstring above.

    def _maybe_alert_owner_from_system_job(self, sid: str, response: str) -> None:
        """Phase F4 (PLAN.md §17): extract the first OWNER_ALERT: line (if
        any) from a system job's response and DM just that — one calm
        line, never the raw output. Cooldown-gated per schedule, reusing
        H2's should_alert/mark_alerted (health.py), so a job that keeps
        finding the same thing worth flagging pings once per cooldown
        window instead of every tick."""
        match = _OWNER_ALERT_RE.search(response or "")
        if not match:
            return
        from zilla import health as _health
        kind = f"schedule_alert:{sid}"
        if not _health.should_alert(kind):
            return
        self._broadcast(Alert(text=match.group(1).strip()))
        _health.mark_alerted(kind)
        log_event("schedule_owner_alert", id=sid)

    def _process_relay_markers(self, text: str, ctx) -> str:
        """Phase K5 (PLAN.md §6/K5): pull any RELAY_SEND:/RELAY_SCHEDULE:
        marker off this turn's reply and hold it for the owner's ✅.

        Returns the owner-facing text with every marker removed, plus one
        plain-language line per action that could NOT be offered (unknown
        person, no `telegram_uid::` on their page, malformed marker). The
        reply itself always still delivers — a relay problem is never an
        error screen (P4).

        Markers are honored on OWNER turns only: on any other principal's
        turn they are stripped and dropped, so a non-owner (or an injected
        instruction inside a document a non-owner sent) can never even
        propose reaching a third party in the owner's name.

        Any unexpected failure in here degrades to "deliver the reply as
        it was" — a relay bug must never cost the owner their answer."""
        try:
            from zilla import relay as _relay
            clean, actions = _relay.parse_markers(text or "")
            if not actions:
                return text
            if ctx is None or not ctx.is_owner:
                log_event("relay_blocked", user=getattr(ctx, "uid", None),
                          count=len(actions))
                return clean

            from zilla import store as _store
            from zilla.config import DB_FILE, MEMORY_DIR
            db = _store.get_store(DB_FILE)

            notes: list[str] = []
            for action in actions:
                if action.get("error"):
                    log_event("relay_malformed", user=ctx.uid)
                    notes.append(_relay.MALFORMED_LINE)
                    continue
                target = _relay.resolve_target(db, action["alias"], MEMORY_DIR)
                if target["uid"] is None:
                    log_event("relay_unresolved", user=ctx.uid,
                              alias=action["alias"][:40], reason=target["reason"])
                    notes.append(_relay.failure_line(target))
                    continue
                if self.relay.submit(action, target, owner_uid=ctx.uid) is None:
                    notes.append("(Too many relays are waiting for your ✅ right now — "
                                 "clear a few and ask me again.)")
            return "\n\n".join([clean, *notes]) if notes else clean
        except Exception as e:
            logger.error(f"[RELAY] marker processing failed: {e}", exc_info=True)
            return text

    def _process_bg_markers(self, text: str, ctx, chat_key: int | None) -> str:
        """Phase B1 step 2 (PLAN.md §9/B1): pull any `BG_TASK:` marker off
        this turn's reply and hold it for the owner's tap.

        Same three properties as the relay markers: the raw protocol never
        reaches a chat, the marker is honored on OWNER turns only (an
        injected instruction inside someone else's document can't queue work
        on this machine), and any unexpected failure degrades to delivering
        the reply unchanged."""
        try:
            from zilla import tasks as _tasks
            clean, prompts = _tasks.parse_markers(text or "")
            if not prompts:
                return text
            if ctx is None or not ctx.is_owner:
                log_event("bg_task_blocked", user=getattr(ctx, "uid", None),
                          count=len(prompts))
                return clean
            notes: list[str] = []
            for prompt in prompts:
                if self.tasks.propose(prompt, ctx.uid,
                                      chat_key if chat_key is not None else ctx.uid) is None:
                    notes.append("(Too many background jobs are waiting for your "
                                 "go-ahead — clear a few and ask me again.)")
                    break
            return "\n\n".join([clean, *notes]) if notes else clean
        except Exception as e:
            logger.error(f"[TASK] marker processing failed: {e}", exc_info=True)
            return text

    def _is_incognito(self, uid: int, sname: str) -> bool:
        """Phase B2: does this session carry the incognito flag? Any failure
        reads as False — a broken lookup must not silently turn a private
        session into a recorded one OR a normal one into a session whose
        memory writes get reverted."""
        try:
            return bool(self.sessions.is_incognito(uid, sname))
        except Exception as e:
            logger.debug(f"[INCOGNITO] flag lookup failed: {e}")
            return False

    async def _enforce_incognito(self, before: dict | None) -> None:
        """Phase B2 step 1 (PLAN.md §9/B2): CODE enforcement, not a model
        promise. Compare the Memory tree against the snapshot taken before
        the turn; if anything was written, restore it from the memory repo
        and tell the owner in one line.

        The notice fires on a detected write whether or not the restore
        worked — the owner needs to know either way, and the wording says
        which happened (R5: one calm sentence, one thing to do)."""
        if before is None:
            return
        from zilla import memory as _memory
        try:
            after = await asyncio.to_thread(_memory.tree_snapshot)
        except Exception as e:
            logger.error(f"[INCOGNITO] could not re-check memory: {e}")
            return
        if after == before:
            return
        changed = sorted(set(before) ^ set(after)) or sorted(
            p for p in after if before.get(p) != after.get(p))
        restored = await asyncio.to_thread(_memory.git_restore)
        log_event("incognito_write_reverted", restored=restored,
                  files=len(changed))
        if restored:
            self._broadcast(Alert(
                text="🕶 That was a private chat, so I undid the note it tried "
                     "to save. Nothing was kept."))
        else:
            self._broadcast(Alert(
                text="🕶 That was a private chat, but something was written to "
                     "your memory and I couldn't undo it — check /memory."))

    async def _run_and_record(self, s: dict) -> None:
        """Tick-loop path: run a due schedule, broadcast the result, and
        record the outcome with retry. A failed run is retried along
        RETRY_LADDER before the schedule advances — and the owner's chat is
        told if it ultimately couldn't complete. system=1 schedules never
        reach this body — see _run_and_record_system."""
        if s.get("system"):
            await self._run_and_record_system(s)
            return
        sid = s["id"]
        title = s.get("title", "")
        ok, response, detail, meta = await self._execute_schedule(s)
        if meta.get("pin_mismatch"):
            self._maybe_notify_backend_pin(s)
        sched_untrusted = not (self.auth and self.auth.is_owner(s.get("user_id")))
        await self._autocommit_memory(f"schedule — {title}"[:80], untrusted=sched_untrusted)
        if ok:
            self.schedules.mark_success(sid)
            log_event("schedule_ok", id=sid, title=title[:40])
            if _quiet_heartbeat_suppressed(s, response):
                log_event("schedule_quiet", id=sid, title=title[:40])
                return
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
        sched_untrusted = not (self.auth and self.auth.is_owner(s.get("user_id")))
        await self._autocommit_memory(f"schedule — {s.get('title', '')}"[:80], untrusted=sched_untrusted)
        if ok and _quiet_heartbeat_suppressed(s, response):
            log_event("schedule_quiet", id=sid, title=s.get("title", "")[:40])
            return
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

    # ── H2: health probe loop (PLAN.md §6/H2) ───────────────
    #
    #  Its OWN timer, independent of _SCHED_TICK/heartbeat_interval — a
    #  heartbeat_interval=0 (beat disabled) must never silence the probes.
    #  Cheap probes (disk/db/binary-on-PATH/agy-login) are safe to redo every
    #  tick; health.py's own per-kind TTL caching keeps the tick itself cheap
    #  even so. The expensive one (claude ping) self-limits to 1x/6h inside
    #  health.probe_claude_login regardless of tick frequency.

    _HEALTH_TICK = 300  # 5 minutes between probe rounds

    async def _self_heal_disk(self) -> bool:
        """The only probe with a known silent fix today (PLAN.md §6/H2 step
        2): prune agy brain dirs harder than H1's normal 7-day startup sweep
        (1 day here, since a health-triggered heal means space is needed
        NOW). Returns whether disk clears the threshold after the sweep —
        the caller decides whether that still needs a human DM."""
        try:
            from zilla.cli_engine import gc_orphaned_conv_dirs
            referenced = self.sessions.all_conversation_ids() if self.sessions else set()
            removed = await asyncio.to_thread(gc_orphaned_conv_dirs, referenced, 1)
            if removed:
                logger.info(f"[HEALTH] disk self-heal: removed {removed} orphaned brain dir(s)")
        except Exception as e:
            logger.warning(f"[HEALTH] disk self-heal (brain-dir GC) failed: {e}")
        from zilla import health as _health
        return _health.probe_disk(force=True)["ok"]

    async def _health_tick(self) -> None:
        """One round of probes → silent self-heal where possible → a single
        cooldown-gated Alert DM for anything that still needs a human."""
        from zilla import config as _config
        from zilla import health as _health
        try:
            results = await asyncio.to_thread(
                _health.run_probes, get_backend(), _config.DB_FILE)
        except Exception as e:
            logger.error(f"[HEALTH] probe round failed: {e}", exc_info=True)
            return
        for kind, res in results.items():
            if res.get("ok"):
                _health.clear_alert(kind)
                continue
            if kind == "disk" and await self._self_heal_disk():
                _health.clear_alert(kind)
                continue
            if _health.should_alert(kind):
                self._broadcast(Alert(
                    text=f"⚠️ {kind}: {res.get('detail', '')}\n"
                         f"{_health.recovery_instructions(kind)}"
                ))
                _health.mark_alerted(kind)
                log_event("health_alert", kind=kind, detail=(res.get("detail") or "")[:200])

        # H4 (PLAN.md §8): "is a newer version available" — a `git fetch
        # --dry-run` that self-limits to 1x/day and only ever writes a cached
        # flag a beat may mention. It is NOT a probe: an available update is
        # not a fault and never alerts.
        from zilla import update as _update
        await asyncio.to_thread(_update.refresh_update_check)

    async def _health_loop(self) -> None:
        logger.info("[HEALTH] probe loop started")
        while True:
            try:
                await self._health_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[HEALTH] loop error: {e}", exc_info=True)
            await asyncio.sleep(self._HEALTH_TICK)

    # ── media retention sweep (F3, PLAN.md §17) ────────────

    _MEDIA_SWEEP_TICK = 3600  # 1 hour — retention is in days, no need to poll faster

    async def _media_sweep_tick(self) -> None:
        """Delete stale Inbox/Outbox files per the owner's configured
        retention window. Reads the setting fresh every tick so a change
        via /settings takes effect on the next hour, no restart needed.
        Media/Kept is structurally exempt (media.sweep_stale_media never
        scans it) — see F3 spec."""
        from zilla import config as _config
        from zilla import media as _media
        try:
            days = await asyncio.to_thread(_config.get_media_retention_days)
            removed = await asyncio.to_thread(_media.sweep_stale_media, days)
            if removed:
                log_event("media_swept", removed=removed, retention_days=days)
        except Exception as e:
            logger.error(f"[MEDIA] sweep tick failed: {e}", exc_info=True)

    async def _media_sweep_loop(self) -> None:
        logger.info("[MEDIA] retention sweep loop started")
        while True:
            try:
                await self._media_sweep_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[MEDIA] sweep loop error: {e}", exc_info=True)
            await asyncio.sleep(self._MEDIA_SWEEP_TICK)
