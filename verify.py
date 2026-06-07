# ============================================================
#  VERIFY — Anti-Hallucination Enforcement Gate (Python layer)
# ============================================================
#  The harness TRUST CONTRACT *asks* the model not to fabricate.
#  This module *enforces* it: after a turn, it heuristically
#  detects answers that look like invented data and triggers ONE
#  corrective retry, logging every flag to logs/trust_log.jsonl.
#
#  Honest about its limits: no heuristic catches every lie. This
#  is tuned for PRECISION, not recall — a false flag wastes a
#  retry and annoys the user, so the bar to flag is deliberately
#  high. It targets the common, expensive failure mode:
#
#     user asks for real/external data ("get the 1500 bookings")
#     → model emits a plausible, number-dense dataset with ZERO
#       sourcing and no admission that it couldn't fetch it.
#
#  Everything is pure + regex-based (fast, testable, no network),
#  so it can run inline on every turn without latency cost. The
#  expensive part (the retry) only happens when a turn is flagged.
# ============================================================

from __future__ import annotations

import re

# Did the user ask for real/external data (vs a creative or general task)?
_DATA_REQUEST = re.compile(
    r"\b(how many|how much|count|number of|list (?:all|the|every)|"
    r"fetch|get me|pull|scrape|retriev\w*|look up|look it up|"
    r"data|records?|bookings?|orders?|customers?|users?|stats?|statistics|"
    r"prices?|figures?|metrics?|latest|current|real[- ]?time|today'?s|"
    r"how much is|what is the (?:price|count|number|total))\b",
    re.IGNORECASE,
)

# The user supplied the data themselves → grounded, never flag.
_GROUNDED = re.compile(
    r"\b(this file|attached|uploaded|the document|above|pasted|i gave you|"
    r"the following|here is the|here'?s the)\b",
    re.IGNORECASE,
)

# Evidence the answer is grounded in a real source/tool/file/url.
_SOURCE = re.compile(
    r"(https?://|www\.|"
    r"\.(?:csv|json|pdf|xlsx?|db|sqlite|txt)\b|"
    r"according to|source:|sources?:|cited|reference:|"
    r"\b(?:fetched|queried|searched|scraped|ran the|via the|from the api|"
    r"tool result|search result|web search)\b)",
    re.IGNORECASE,
)

# Honest failure — the model did the RIGHT thing. Never flag these.
_HONEST_FAIL = re.compile(
    r"(can'?t verify|cannot verify|couldn'?t (?:find|fetch|access|retrieve|get)|"
    r"unable to (?:find|fetch|access|retrieve|verify|get)|"
    r"no (?:access|source|data|way to)|i don'?t have (?:access|the data|that data)|"
    r"don'?t have access|failed to (?:fetch|find|retrieve|access)|not able to|"
    r"\bFAILED\b|i (?:can'?t|cannot) (?:access|fetch|browse|reach))",
    re.IGNORECASE,
)

# Hedge words the user explicitly called out — a SOFT corroborating signal.
_HEDGES = re.compile(
    r"\b(approximately|estimated?|typically|i believe|around|roughly|"
    r"should be|presumably|likely|in general|i think it'?s|probably)\b",
    re.IGNORECASE,
)

_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")


def looks_like_data_request(user_message: str) -> bool:
    u = user_message or ""
    return bool(_DATA_REQUEST.search(u)) and not _GROUNDED.search(u)


def assess(user_message: str, response: str) -> list[str] | None:
    """Return a list of reasons if the response looks fabricated, else None.

    Conservative gate, evaluated in order:
      • honest admission of failure  → None (good behavior, never flag)
      • not a data request           → None (creative/general task)
      • user supplied the data       → None (grounded)
      • response cites a source/tool → None (grounded)
      • otherwise, FLAG if the answer has a fabrication shape: dense concrete
        specifics (many numbers / many rows) with no sourcing, optionally with
        hedge language.
    """
    r = response or ""
    u = user_message or ""

    if _HONEST_FAIL.search(r):
        return None
    if not looks_like_data_request(u):
        return None
    if _SOURCE.search(r):
        return None

    numbers = len(_NUMBER.findall(r))
    rows = r.count("\n")
    has_hedge = bool(_HEDGES.search(r))

    reasons: list[str] = []
    # Fabrication shape: lots of concrete numbers, or a table-ish block of them.
    if numbers >= 8 or (numbers >= 3 and rows >= 6):
        reasons.append(
            f"data request answered with {numbers} numeric values across "
            f"{rows + 1} lines and no cited source or tool"
        )
    if has_hedge and (numbers >= 1 or not reasons):
        reasons.append("hedge/estimation language on an unsourced data request")

    return reasons or None


def correction_prompt(original_user_message: str) -> str:
    """The corrective re-ask used when a turn is flagged."""
    return (
        "⚠️ SELF-CHECK: Your previous answer may contain unverified or fabricated "
        "data, which violates the trust contract. Do NOT invent numbers, records, "
        "or facts. Re-answer the request below using ONLY data you can actually "
        "fetch with a tool or that the user provided. If you cannot obtain the real "
        "data, state exactly what you tried, that it FAILED, and ask for what you "
        "need — never substitute plausible-looking values.\n\n"
        f"Original request: {original_user_message}"
    )
