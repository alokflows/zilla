"""
Phase B1 — THE BACKGROUND LANE (PLAN.md §9/B1).

Every turn holds the per-uid CLI lock, so a 20-minute research job freezes
the owner's chat for 20 minutes — the deepest possible violation of "never
feels dead". A background task gets its own lane: its own named session
(`task:<id>`), its own conversation, its own cancel event, and a
**task-scoped lock that is never the owner's chat lock**. The chat stays
free while the job runs.

This module is the pure part — no I/O, no asyncio, no Telegram:

  • the `BG_TASK:` marker parse/strip (same family as `RELAY_SEND:`)
  • the owner-facing copy: confirm card, result card, failure line
  • the `/tasks` board text

`core.Tasks` owns the runtime (queue, cap, locks, execution) and
`store.tasks_*` owns the durable rows. Two rules hold here, exactly as they
do in `zilla/relay.py`:

1. **The model can never spawn work on its own.** A marker is a PROPOSAL —
   it becomes a task only after the owner taps ✅, or when the owner types
   `/bg` themselves.
2. **Nothing in here raises.** Malformed model output comes back as a
   dropped marker, never as an error screen (P4).
"""

from __future__ import annotations

import re

# Status vocabulary (PLAN.md §9/B1 step 1). QUEUED/RUNNING are live;
# the rest are terminal.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELED = "canceled"

LIVE_STATUSES = (QUEUED, RUNNING)
TERMINAL_STATUSES = (DONE, FAILED, CANCELED)

# Default concurrency cap — overridable with the `max_bg_tasks` setting.
# Two, because every extra lane is another agentic CLI run against the same
# rented quota (PLAN.md §9/B1 step 1: "quota protection is the cap + usage
# counters").
DEFAULT_MAX_BG_TASKS = 2

# A queued backlog is bounded too: past this, `/bg` says so instead of
# growing the table forever.
MAX_PENDING = 20

# How many finished tasks the board shows (PLAN.md §9/B1 step 4).
BOARD_FINISHED = 5

MAX_TITLE = 48
MAX_PROMPT = 2000

# One line, start-anchored, MULTILINE — same shape as RELAY_SEND:, so the
# model learns one marker convention, not three.
_BG_RE = re.compile(r"^BG_TASK:[ \t]*(.+)$", re.MULTILINE)

# More than this many proposals in one reply is a bug or an injection
# attempt, not a real turn. Extras are stripped and dropped.
MAX_PROPOSALS = 2


# ══════════════════════════════════════════════════════════
#  MARKER PARSING
# ══════════════════════════════════════════════════════════

def parse_markers(text: str) -> tuple[str, list[str]]:
    """Split a model reply into (clean_text, proposed prompts).

    Every `BG_TASK:` line is removed whether or not it carried a usable
    prompt — the owner must never see the raw protocol. An empty payload is
    simply dropped (there is nothing to propose)."""
    if not text or "BG_TASK:" not in text:
        return text, []

    prompts: list[str] = []

    def _take(match: re.Match) -> str:
        payload = (match.group(1) or "").strip()
        if payload:
            prompts.append(payload[:MAX_PROMPT])
        return ""

    clean = _BG_RE.sub(_take, text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, prompts[:MAX_PROPOSALS]


# ══════════════════════════════════════════════════════════
#  COPY  (owner-facing; STYLE.md — one calm sentence, no jargon)
# ══════════════════════════════════════════════════════════

def title_for(prompt: str) -> str:
    """A short, human label for a task, derived from its prompt. Used on the
    board and on the result card, so it must read as a thing, not a slug."""
    words = (prompt or "").strip().split()
    title = " ".join(words)[:MAX_TITLE].strip()
    if len(" ".join(words)) > MAX_TITLE:
        title = title.rstrip() + "…"
    return title or "Background job"


def confirm_card(prompt: str) -> str:
    """Shown when the AGENT proposed the work. The owner sees the exact
    prompt that would run before anything starts."""
    return ("🧵 Run this in the background?\n\n"
            f"“{prompt}”\n\n"
            "Your chat stays free while it runs, and I'll bring you the result.")


def started_line(title: str, queued: bool) -> str:
    if queued:
        return (f"🧵 Queued — {title}\n\n"
                "It starts as soon as a lane is free. /tasks shows where it is.")
    return (f"🧵 Started — {title}\n\n"
            "Keep chatting; I'll bring you the result when it's done.")


def format_duration(seconds: float | None) -> str:
    """'2 min 5 sec' / '45 sec' — spelled out, never 00:02:05 (R3/R21)."""
    if seconds is None or seconds < 0:
        return "unknown"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} sec"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {rest} sec" if rest else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} hr {minutes} min" if minutes else f"{hours} hr"


def result_card(task: dict, duration: float | None) -> dict:
    """The finished task's HEADER as a ZUI `card` block (PLAN.md §9/B1
    step 3) — what ran and how long it took. Returned as a plain dict so
    `zui.validate` applies the same caps it applies to a model-authored
    card.

    The result BODY is not squeezed into card fields (they cap at 200
    characters): it goes out right after the card through the normal
    response pipeline, which already owns chunking and FileOut delivery for
    any absolute path the answer mentions."""
    return {
        "kind": "card",
        "title": f"Done — {task.get('title') or title_for(task.get('prompt', ''))}",
        "subtitle": f"Took {format_duration(duration)}",
        "fields": [],
        "footer": "",
    }


def failure_line(task: dict) -> str:
    """One calm sentence — never a stack trace, never the raw error (R5)."""
    return (f"🧵 {task.get('title') or 'That background job'} didn't finish. "
            "Tap retry to run it again.")


def canceled_line(task: dict) -> str:
    return f"🛑 Stopped — {task.get('title') or 'that background job'}."


def _row_line(task: dict) -> str:
    title = task.get("title") or title_for(task.get("prompt", ""))
    if task.get("status") == RUNNING:
        progress = (task.get("progress") or "").strip().replace("\n", " ")
        return f"{title} — {progress[:60]}" if progress else f"{title} — working"
    return title


def board_text(running: list[dict], queued: list[dict],
               finished: list[dict]) -> str:
    """The `/tasks` board (PLAN.md §9/B1 step 4): running with its live
    progress line, then queued, then the last few finished. One bold title
    line, blocks separated by one blank line (STYLE.md R6/R10)."""
    if not (running or queued or finished):
        return ("🧵 <b>Background jobs</b>\n\n"
                "Nothing running. Start one with /bg and a description of "
                "the work.")

    parts = ["🧵 <b>Background jobs</b>"]
    if running:
        block = ["Running"]
        block += [f"  {_row_line(t)}" for t in running]
        parts.append("\n".join(block))
    if queued:
        block = ["Waiting"]
        block += [f"  {_row_line(t)}" for t in queued]
        parts.append("\n".join(block))
    if finished:
        icons = {DONE: "✓", FAILED: "⚠", CANCELED: "🛑"}
        block = ["Finished"]
        block += [f"  {icons.get(t.get('status'), '•')} {_row_line(t)}"
                  for t in finished[:BOARD_FINISHED]]
        parts.append("\n".join(block))
    return "\n\n".join(parts)
