# ============================================================
#  REVIEW — deterministic outbound gate + triage router (P1.5)
# ============================================================
#  Implements docs/dev/RESEARCH_ORCHESTRATION_REVIEW.md §4: the
#  response-review seam ("Layer B") and the front-half triage
#  classifier that HANDOFF.md's P1.5 checklist item calls for.
#  Both halves are 100% deterministic (regex/string only, zero
#  model calls) so they can run inline on every turn for free.
#
#  review()  — unifies the three scattered "did this turn really
#              succeed?" checks (cli_engine.detect_limit, the
#              _SCHED_FAIL_PREFIXES error-garbage check that used
#              to live only in core._execute_message_schedule, and
#              verify.assess's fabrication heuristic) into one
#              pure function, called from BOTH core.handle_message
#              (live chat + approvals) and
#              core._execute_message_schedule (scheduled runs) —
#              steal-list #31.
#
#  classify_route() — the P1.5 triage pass: decides, BEFORE the
#              heavy CLI turn, whether an incoming message is pure
#              small talk (fast path), an explicit "remember this"
#              share (zero-model journal append), or needs the full
#              agent turn. Deliberately conservative on both
#              patterns — a false positive here either wastes a
#              cheap model call on something that needed the real
#              agent (caught by review()'s fallback) or silently
#              drops a real request into the journal, which is why
#              "share" requires an explicit leading verb and
#              "smalltalk" is a closed whitelist, not a vibe.
# ============================================================

from __future__ import annotations

import re
from dataclasses import dataclass

from zilla.cli_engine import detect_limit
from zilla import verify

# ══════════════════════════════════════════════════════════
#  LAYER B — deterministic outbound gate
#  (docs/dev/RESEARCH_ORCHESTRATION_REVIEW.md §4.1)
# ══════════════════════════════════════════════════════════

# Response shapes that mean "the run did not really succeed" — single source
# of truth; core._execute_message_schedule used to define this itself.
FAIL_PREFIXES = ("Error:", "Claude error:", "⏱️", "⚠️ Stopped")

# Generic "nothing to show" strings the backends themselves emit on a
# normal exit with no real answer (cli_engine.run_cli / backends._parse_claude_json)
# — functionally equivalent to an empty response, just not literally "".
_GENERIC_EMPTY = (
    "no response from cli. try rephrasing.",
    "no response from claude.",
)

_EMPTY_NOTE = "I didn't get any output back — try rephrasing?"


@dataclass
class ReviewResult:
    """verdict: 'deliver' | 'retry' | 'stop'.
    reason: short machine tag for trust_log (e.g. 'empty', 'limit', 'error',
    'fabrication').
    user_note: plain-language text to show the user when verdict == 'stop'.
    retry_prompt: the corrective re-ask when verdict == 'retry' (mirrors
    verify.correction_prompt — callers that don't own a retry loop, i.e.
    core.handle_message's live-chat path, just treat 'retry' as 'deliver'
    since cli_engine._run_blocking already ran the ONE corrective retry
    before core ever sees the response)."""
    verdict: str
    reason: str = ""
    user_note: str = ""
    retry_prompt: str = ""


def review(user_message: str, response: str, *, exit_reason: str | None = None) -> ReviewResult:
    """Deterministic outbound gate — no model call, no I/O. Checks in order:

      1. empty            — blank, or one of the backends' own generic
                             "nothing to show" strings.
      2. limit             — cli_engine.detect_limit() fires (rate-limited /
                             quota / overloaded).
      3. error-garbage      — response starts with a known failure prefix, or
                             the caller reports a non-normal exit_reason.
      4. fabrication        — verify.assess() flags an unsourced data
                             request (the existing hallucination heuristic) —
                             ONE bounded retry, never a loop.
      5. else               — deliver.

    Pure and total: never raises, never touches the network/filesystem/model.
    """
    text = response or ""
    stripped = text.strip()

    # 1. empty (steal-list #36: prefer whatever real content the backend
    # already captured over inventing a message — this branch only fires
    # when there truly is nothing, or the backend's own generic filler).
    if not stripped or stripped.lower() in _GENERIC_EMPTY:
        return ReviewResult(verdict="stop", reason="empty", user_note=_EMPTY_NOTE)

    # 2. limit
    limit_reason = detect_limit(text)
    if limit_reason:
        return ReviewResult(
            verdict="stop", reason="limit",
            user_note=f"⚠️ Looks rate-limited/blocked ({limit_reason}). Try switching models.",
        )

    # 3. error-garbage / non-normal exit — the response text itself already
    # carries whatever partial/status info cli_engine could assemble (the
    # 🛑/⏱️/⚠️ headers), so the note is the response verbatim, never a
    # separately-invented generic line (steal-list #36).
    if stripped.startswith(FAIL_PREFIXES) or (exit_reason not in (None, "normal")):
        return ReviewResult(verdict="stop", reason="error", user_note=stripped)

    # 4. fabrication — precision-tuned, already shipped (zilla/verify.py).
    reasons = verify.assess(user_message, text)
    if reasons:
        return ReviewResult(
            verdict="retry", reason="fabrication",
            retry_prompt=verify.correction_prompt(user_message),
        )

    return ReviewResult(verdict="deliver")


# ══════════════════════════════════════════════════════════
#  P1.5 TRIAGE — front-half classifier
#  (HANDOFF.md P1.5; RESEARCH_ORCHESTRATION_REVIEW.md §4.3)
# ══════════════════════════════════════════════════════════

# Explicit share verbs ONLY — the message must START with one of these
# (optionally after a hyphen/colon separator). Deliberately narrow: a
# false positive here would silently swallow a real request into the
# journal instead of running it.
_SHARE_RE = re.compile(
    r"^\s*(remember|note down|note that|fyi|for your reference)\b[:,\-]?\s*",
    re.IGNORECASE,
)

# CONSERVATIVE closed whitelist: pure greetings / thanks / acknowledgments.
# Normalized (stripped, lowercased, trailing punctuation removed) before
# matching. Anything with a '?' or that doesn't fully match one of these is
# NOT smalltalk — it falls through to the full path, which is the safe
# default. This is intentionally a whitelist, not a heuristic: a false
# positive means a real question gets a cheap-model reply instead of the
# full agent turn, so the bar to match is "obviously and only" smalltalk.
_SMALLTALK_PHRASES = {
    "hi", "hii", "hiya", "hello", "hey", "yo",
    "hi there", "hello there", "hey there",
    "good morning", "good afternoon", "good evening", "good night",
    "gm", "gn", "morning", "night",
    "thanks", "thank you", "thanks a lot", "thank you so much",
    "thanks so much", "ty", "tysm", "much appreciated",
    "ok", "okay", "kk", "k", "alright", "all right",
    "cool", "nice", "great", "awesome", "perfect", "sounds good",
    "got it", "noted", "no worries", "np", "all good",
    "yes", "no", "yep", "yup", "nope", "sure",
    "bye", "goodbye", "see you", "see ya", "later", "cya",
    "welcome", "you're welcome", "youre welcome",
}

_TRAILING_PUNCT = re.compile(r"[!.,;:]+$")
_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = _TRAILING_PUNCT.sub("", t).strip()
    t = _WS.sub(" ", t)
    return t


def _is_smalltalk(text: str) -> bool:
    if "?" in text:
        return False
    norm = _normalize(text)
    if not norm or len(norm) > 40:
        return False
    return norm in _SMALLTALK_PHRASES


def classify_route(text: str) -> str:
    """Deterministic, zero-model-call route for an incoming message.
    Returns 'share', 'smalltalk', or 'full' (the safe default for anything
    that doesn't cleanly match one of the narrow patterns above)."""
    t = text or ""
    if not t.strip():
        return "full"
    m = _SHARE_RE.match(t)
    if m:
        payload = t[m.end():].strip()
        # Require an actual payload after the verb, and not a bare "?" —
        # "remember?" (nothing to note) is a question, not a share.
        if payload and payload.strip("?!.,; ") != "":
            return "share"
    if _is_smalltalk(t):
        return "smalltalk"
    return "full"
