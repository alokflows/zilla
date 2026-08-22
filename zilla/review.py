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
from datetime import datetime, tzinfo

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

# A response long enough to be a real answer is never read as an error
# signal, whatever words it contains (PLAN.md §10 R2.1). detect_limit()
# substring-matches "quota"/"429"/"overloaded" ANYWHERE, so without this
# gate a correct answer ABOUT rate limits would be replaced by a
# "you're rate-limited" notice — and, under R2, thrown away and re-asked
# on another backend context-free.
ERROR_SHAPE_MAX_CHARS = 300


def is_error_shaped(text: str) -> bool:
    """Short and structured like a failure, rather than prose. The only
    shape in which a limit signal is allowed to mean 'rate-limited'."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if len(stripped) > ERROR_SHAPE_MAX_CHARS:
        return False
    if stripped.startswith(FAIL_PREFIXES):
        return True
    # A short stub with no sentence structure: a CLI's own error line, not
    # somebody's answer.
    return stripped.count(".") <= 2 and "\n\n" not in stripped


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

    # 2. limit — but ONLY in something shaped like an error. A long answer
    # that merely discusses quotas and 429s is an answer.
    limit_reason = detect_limit(text) if is_error_shaped(text) else None
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

# ── clock/date questions (R4c) — the zero-model clock route ──
# Anchored over NORMALIZED text only, and deliberately narrow: a "what
# time/date/day" question with nothing else attached. Any extra words ("in
# London", "is the meeting") break the anchor and fall to the full path,
# because a wrong instant answer is worse than a slow right one. A trailing
# "?" is allowed through (it IS a question) — but only trailing.
_CLOCK_RE = re.compile(
    r"^what(?:'?s| is)?(?: the| todays| today's)?"
    r" (?:time|date|day)"
    r"(?: is it| it is| today| now| right now| tonight)*$"
)


def _is_clock_question(text: str) -> bool:
    probe = _normalize(text).rstrip("?").rstrip()
    if not probe:
        return False
    return bool(_CLOCK_RE.match(probe))


def clock_answer(now: datetime, zone: tzinfo | None = None) -> str:
    """One plain-language sentence answering time, day AND date together —
    so one matcher can serve all three phrasings without plumbing the kind
    through. Pure given its inputs; with zone=None the local zone comes
    from schedules._local_zone() (the scheduler's own DST-aware resolver),
    resolved lazily so this module stays import-light."""
    if zone is None:
        from zilla.schedules import _local_zone
        zone = _local_zone()
    local = now.astimezone(zone)
    hour = local.strftime("%I").lstrip("0")
    return (f"It's {hour}:{local:%M} {local.strftime('%p').lower()}, "
            f"{local:%A} {local.day} {local:%B %Y}.")

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


_TRIVIAL_MAX_TOKENS = 4

# Closed token vocabularies for the widened matcher. A message passes the
# TOKENS matcher only if EVERY token is in this vocabulary AND at least one
# comes from a required set (ack / greeting / thanks / yes-no) — so "ok cool"
# is trivial, but "ok where is the invoice" is not, because "where"/"invoice"
# are not words this matcher has ever heard of. Unknown word ⇒ full path.
_ACK_TOKENS = {
    "ok", "okay", "kk", "k", "alright", "right", "cool", "nice", "great",
    "awesome", "perfect", "good", "got", "noted", "fine", "done",
}
_GREETING_TOKENS = {
    "hi", "hii", "hiya", "hello", "hey", "yo", "gm", "gn", "morning",
    "afternoon", "evening", "night", "bye", "goodbye", "later", "cya",
}
_THANKS_TOKENS = {"thanks", "thank", "ty", "tysm", "thx", "appreciated"}
_YESNO_TOKENS = {"yes", "no", "yep", "yup", "nope", "sure"}
_TAIL_TOKENS = {
    # Function-word tails only — glue that turns one required token into a
    # natural phrase ("thanks so much"), never content.
    "there", "you", "so", "much", "a", "the", "then", "too", "all", "lot",
    "welcome", "guys", "man",
}
_REQUIRED_TOKENS = (_ACK_TOKENS | _GREETING_TOKENS | _THANKS_TOKENS
                    | _YESNO_TOKENS)
_VOCAB = _REQUIRED_TOKENS | _TAIL_TOKENS


def _matches_exact(norm: str) -> bool:
    return norm in _SMALLTALK_PHRASES


def _matches_tokens(norm: str) -> bool:
    tokens = norm.split()
    if not 1 <= len(tokens) <= _TRIVIAL_MAX_TOKENS:
        return False
    if not any(tok in _REQUIRED_TOKENS for tok in tokens):
        return False
    return all(tok in _VOCAB for tok in tokens)


def _matches_emoji(norm: str) -> bool:
    glyphs = [ch for ch in norm if not ch.isspace()]
    if not 1 <= len(glyphs) <= 8:
        return False
    return all(not ch.isalnum() for ch in glyphs)


def is_trivial(text: str) -> bool:
    """True when the message is pure small talk and can take the fast path.

    R4b widening of R1's whitelist: three deterministic matchers behind the
    same shared guards (no '?', ≤40 chars normalized, exactly one
    _normalize pass):

      EXACT   — today's closed phrase table, unchanged.
      TOKENS  — 1–4 tokens, every token from a closed vocabulary, at least
                one an ack/greeting/thanks/yes-no; tails limited to function
                words. Catches "ok cool" and "great thanks" without ever
                letting a content word through.
      EMOJI   — 1–8 emoji/punctuation/symbol glyphs; ANY alphanumeric
                anywhere means it isn't trivial.

    Deliberately still a whitelist at heart: unknown words fall to 'full'.
    """
    if "?" in text:
        return False
    norm = _normalize(text)
    if not norm or len(norm) > 40:
        return False
    return (_matches_exact(norm) or _matches_tokens(norm)
            or _matches_emoji(norm))


def classify_route(text: str) -> str:
    """Deterministic, zero-model-call route for an incoming message.
    Returns 'share', 'clock', 'smalltalk', or 'full' (the safe default for
    anything that doesn't cleanly match one of the narrow patterns above)."""
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
    # R4c: a bare clock/date question — after share (an explicit "remember
    # what time it is" is a journal entry), before smalltalk.
    if _is_clock_question(t):
        return "clock"
    if is_trivial(t):
        return "smalltalk"
    return "full"
