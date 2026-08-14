# ============================================================
#  CHAIN — backend fallback, error channels only (PLAN.md §10 R2)
# ============================================================
#  When a turn fails on the backend that ran it, the owner should get an
#  answer from the next backend instead of an apology. That is the whole
#  feature, and every rule here exists to stop it from firing when it
#  shouldn't:
#
#  TRIGGER DISCIPLINE — fallback fires ONLY on error channels:
#    - the backend reported an error (non-zero exit, spawn failure, a
#      known failure prefix), or
#    - the response was empty after cli_engine's own retry, or
#    - a rate-limit/quota signal appeared IN AN ERROR-SHAPED RESPONSE.
#
#  That last clause is the point of this module. `detect_limit()` substring-
#  matches "quota" / "429" / "overloaded" ANYWHERE in the text, so a long,
#  correct answer *about* rate limits looks identical to being rate-limited.
#  Without the shape gate, asking "what does HTTP 429 mean?" would throw the
#  right answer away and re-ask a second backend with no context. So a limit
#  signal only counts inside something short and error-shaped — a real limit
#  message is a stub, never three paragraphs of prose.
#
#  ELIGIBILITY: a chain entry is eligible only if its binary is present AND
#  its last health probe showed a live login (health.login_ok — a stale
#  probe is re-run on demand). A CLI that is installed but logged out would
#  burn the retry or hang on a login prompt.
#
#  WHAT A FALLBACK TURN IS: a throwaway conversation on the next backend,
#  carrying ONE primer line so the answer isn't context-free, delivered as
#  one clean answer with a footnote naming the backend that produced it.
#  The session keeps its own backend and conversation (I-CONV) — a fallback
#  is a rescue, not a migration.
# ============================================================

from __future__ import annotations

from zilla.review import is_error_shaped  # re-exported: the shape gate lives with review()

# The owner's declared priority, filtered to what's actually installed.
DEFAULT_ORDER = ("agy", "opencode", "claude")

# How much of the owner's message the next backend is told about.
PRIMER_MAX_CHARS = 400

# Probe results older than this are re-run before the chain trusts them.
PROBE_MAX_AGE = 15 * 60.0

_FOOTNOTE = "↷ answered via {backend}"

_EXHAUSTED = (
    "I couldn't get an answer from any of the AI backends just now — "
    "{tried} all failed. Nothing is lost; try again in a few minutes."
)


def order(installed: dict[str, bool] | None = None) -> list[str]:
    """The chain, in the owner's declared priority, filtered to backends
    whose binary is on this machine. `installed` is injectable so this stays
    testable without a filesystem."""
    from zilla import config
    raw = config.get_setting("backend_chain", None) or list(DEFAULT_ORDER)
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.replace(",", " ").split()]
    seen, out = set(), []
    for name in raw:
        b = str(name).strip().lower()
        if not b or b in seen or b not in DEFAULT_ORDER:
            continue
        seen.add(b)
        if installed is None or installed.get(b):
            out.append(b)
    return out


def should_fallback(review_result, response: str) -> tuple[bool, str]:
    """Given review()'s verdict for this turn, should the chain try the next
    backend? Returns (yes/no, reason-tag for the log)."""
    if review_result is None or review_result.verdict != "stop":
        return False, ""
    reason = review_result.reason
    if reason in ("empty", "error"):
        return True, reason
    if reason == "limit":
        # ONLY when the response itself is error-shaped. A long answer that
        # merely discusses quotas or 429s is a correct answer.
        if is_error_shaped(response):
            return True, "limit"
        return False, "limit_in_prose"
    return False, reason


def primer(user_message: str) -> str:
    """One line of context so the rescue answer isn't blind. Deliberately
    the owner's own words and nothing else — no transcript, no memory: a
    fallback turn runs on a backend the session never chose."""
    msg = (user_message or "").strip()
    if len(msg) > PRIMER_MAX_CHARS:
        msg = msg[:PRIMER_MAX_CHARS].rstrip() + "…"
    return f"Context: the owner was just asking about: {msg}"


def with_footnote(text: str, backend: str) -> str:
    """One clean answer, plus the one fact the owner needs about it."""
    body = (text or "").rstrip()
    return f"{body}\n\n{_FOOTNOTE.format(backend=backend)}"


def exhausted_note(tried: list[str]) -> str:
    """Honest, plain-language stop (P4) — no stack trace, no blame."""
    names = ", ".join(tried) if tried else "the backend"
    return _EXHAUSTED.format(tried=names)
