"""Builds a standalone ZillaCore for the TUI frontend.

The TUI is just another thin frontend over zilla.core.ZillaCore (see
docs/dev/CORE_API.md) — it opens the SAME state files bot.py uses
(zilla/config.py is the single source of truth for paths: sessions.json,
authorized_users.json, .env, settings.json), so switching between Telegram
and the terminal never forks state.

No ScheduleManager is attached here: a scheduler ticking in two frontends
against the same schedules.json at once would double-fire jobs. Wiring the
TUI and the Telegram connector into one shared core/process is later
plumbing (HANDOFF.md Phase 2 step 5 — "Telegram becomes a connector"), not
this step's job; this module only guarantees the chat/settings/skills/health
screens work standalone.

Nothing here may raise on a fresh, unconfigured machine (HANDOFF.md quality
bar: "must never crash on startup even if no config exists yet") — every
public function returns a friendly string instead.
"""

from __future__ import annotations

import os
import shutil

from zilla import config
from zilla.core import ZillaCore
from zilla.sessions import SessionManager
from zilla.users import AuthManager


def cli_hint() -> str | None:
    """A one-line friendly hint if neither AI CLI appears to be installed at
    all. Deliberately cheap: PATH/file-existence checks only, no subprocess
    call — so it never slows down the home screen's first paint. Login
    state (which DOES need a subprocess probe) shows up later, in the
    Health screen, not here. None means nothing to warn about."""
    agy_found = bool(shutil.which("agy")) or bool(
        config.CLI_PATH and os.path.exists(config.CLI_PATH))
    claude_found = bool(shutil.which("claude")) or bool(
        config.CLAUDE_PATH and os.path.exists(config.CLAUDE_PATH))
    if agy_found or claude_found:
        return None
    return ("No AI CLI found (agy or claude). Install one and log in, "
            "then relaunch — see MANUAL.md.")


def build_core() -> tuple[ZillaCore | None, int, str | None]:
    """(core, local_user_id, error).

    error is a friendly, non-crashing hint string if construction failed for
    any reason — the app shows it instead of a traceback.

    local_user_id is the configured owner id (0 if no .env/
    TELEGRAM_OWNER_ID is set yet). AuthManager.can()/is_owner() both treat
    user_id == owner_id as the owner regardless of its value — including 0
    — so a completely fresh install can still chat locally before Telegram
    is ever configured.
    """
    try:
        sessions = SessionManager(config.SESSIONS_FILE)
        auth = AuthManager(config.USERS_FILE, config.OWNER_CHAT_ID)
        core = ZillaCore(
            sessions=sessions,
            auth=auth,
            schedules=None,
            owner_chat_id=config.OWNER_CHAT_ID or None,
        )
        return core, auth.owner_id, None
    except Exception as e:  # pragma: no cover - defensive, quality bar
        return None, 0, f"Could not start Zilla's core: {e}"
