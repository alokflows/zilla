"""
Phase U4 — PRESENCE (PLAN.md §7/U4).

Zilla used to announce itself on every single start ("⚡ Zilla is online
(v2.2) / Model / Time"), which meant a battery-flat MacBook or a routine
restart pushed a notification the owner had no use for. This replaces that
with one **pinned status card**: a single message in the owner's chat,
pinned once, then edited in place forever after — Telegram sends no
notification for an edit, so it is glanceable always and noisy never.

A genuinely NEW message is sent only when it carries information:

  · first install            "Hi — I'm here."
  · after an update          "Updated to v2.3."
  · recovery from real downtime (> `downtime_notify_min`, default 60)

Everything else is silent. `/status` shows the card's content on demand.

This module is the decision logic and the copy — no Telegram calls, so the
rules are testable without a bot. State lives in the same settings store
everything else uses (`presence_card_id`, `presence_last_seen`,
`presence_version`), so a restart keeps editing the same card.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

CARD_ID_KEY = "presence_card_id"
LAST_SEEN_KEY = "presence_last_seen"
VERSION_KEY = "presence_version"
DOWNTIME_KEY = "downtime_notify_min"

DEFAULT_DOWNTIME_NOTIFY_MIN = 60


def _fmt_time(epoch: float | None) -> str:
    if not epoch:
        return "unknown"
    return datetime.fromtimestamp(epoch).strftime("%d %b %H:%M")


def card_text(*, online: bool, backend: str, version: str,
              last_beat: float | None = None,
              since: float | None = None) -> str:
    """The pinned card. One accent glyph, sentence case, no exclamation
    marks (docs/dev/STYLE.md). Online reads as a state, not an event."""
    if online:
        head = f"● Online · {backend} · v{version}"
    else:
        head = f"○ Offline since {_fmt_time(since)}"
    if last_beat:
        return f"{head}\nLast check {_fmt_time(last_beat)}"
    return head


def decide_startup(*, now: float, last_seen: float | None,
                   stored_version: str | None, version: str,
                   card_id: int | None,
                   downtime_notify_min: int = DEFAULT_DOWNTIME_NOTIFY_MIN) -> dict:
    """What a start should actually do.

    Returns {"pin": bool, "message": str | None, "reason": str} —
    `message` is a NEW message to send (and pin, when `pin` is True); None
    means edit the existing card silently. `reason` is for the log.

    The order matters: a first install introduces itself, an update reports
    the version once, real downtime explains the gap, and a routine restart
    says nothing at all."""
    if card_id is None:
        return {"pin": True, "reason": "first_install",
                "message": "Hi — I'm here."}

    if stored_version and stored_version != version:
        return {"pin": False, "reason": "updated",
                "message": f"Updated to v{version}."}

    gap_min = ((now - last_seen) / 60.0) if last_seen else 0.0
    if last_seen and gap_min > downtime_notify_min:
        hours = gap_min / 60.0
        gap = f"{hours:.0f} hours" if hours >= 1.5 else f"{gap_min:.0f} minutes"
        return {"pin": False, "reason": "downtime",
                "message": (f"I was offline for about {gap} — back now. "
                            f"Anything scheduled in that window runs next.")}

    return {"pin": False, "message": None, "reason": "routine_restart"}


# ══════════════════════════════════════════════════════════
#  STATE  (settings-backed, so it survives a restart)
# ══════════════════════════════════════════════════════════

def read_state() -> dict:
    from zilla.config import get_setting
    card_id = get_setting(CARD_ID_KEY, None)
    try:
        card_id = int(card_id) if card_id is not None else None
    except (TypeError, ValueError):
        card_id = None
    last_seen = get_setting(LAST_SEEN_KEY, None)
    try:
        last_seen = float(last_seen) if last_seen is not None else None
    except (TypeError, ValueError):
        last_seen = None
    return {"card_id": card_id, "last_seen": last_seen,
            "version": get_setting(VERSION_KEY, None)}


def write_state(*, card_id: int | None = None, last_seen: float | None = None,
                version: str | None = None) -> None:
    """Persist whichever pieces were given. Never raises — presence
    bookkeeping must not be able to break a start or a shutdown."""
    from zilla.config import set_setting
    try:
        if card_id is not None:
            set_setting(CARD_ID_KEY, int(card_id))
        if last_seen is not None:
            set_setting(LAST_SEEN_KEY, float(last_seen))
        if version is not None:
            set_setting(VERSION_KEY, str(version))
    except Exception as e:
        logger.debug(f"[PRESENCE] state write failed: {e}")


def touch_seen(now: float | None = None) -> None:
    """Record that Zilla was alive just now — this is what makes the next
    start able to tell a routine restart from real downtime."""
    write_state(last_seen=now if now is not None else time.time())


def downtime_notify_min() -> int:
    from zilla.config import get_setting
    try:
        return int(get_setting(DOWNTIME_KEY, DEFAULT_DOWNTIME_NOTIFY_MIN))
    except (TypeError, ValueError):
        return DEFAULT_DOWNTIME_NOTIFY_MIN
