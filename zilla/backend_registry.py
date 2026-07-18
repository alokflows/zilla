# ============================================================
#  BACKEND REGISTRY — dynamic backend adapters (PLAN.md §17/F2)
# ============================================================
#  Every backend (agy, claude, and whatever R3's opencode or a future
#  backend adds) self-describes ONE adapter here: binary lookup, a
#  login/identity probe, its model catalog, and how to actually dispatch
#  a turn. Every UI surface (settings buttons, the `zilla config` chain
#  editor, doctor, backend_status()) reads this registry at render time
#  instead of hard-coding backend names.
#
#  TO ADD A BACKEND: write the five small functions below (binary/
#  identity/models/dispatch/hint) and call register(BackendAdapter(...))
#  once, near the bottom of this file. No other file needs an edit —
#  every menu/probe/validator picks it up automatically.
#
#  All adapter functions do their real imports INSIDE the closure (not at
#  module load time) so this module never triggers a circular import with
#  cli_engine.py / backends.py / config.py, which themselves may want to
#  read the registry.
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class BackendAdapter:
    name: str
    label: str
    login_cmd: str
    model_flag: bool
    hint: str
    binary: Callable[[], str | None]
    identity: Callable[[bool], dict]
    models: Callable[[], list[tuple[str, str]]]
    dispatch: Callable[..., tuple[str, str | None]]


_REGISTRY: dict[str, BackendAdapter] = {}


def register(adapter: BackendAdapter) -> BackendAdapter:
    _REGISTRY[adapter.name] = adapter
    return adapter


def all_backends() -> list[BackendAdapter]:
    return list(_REGISTRY.values())


def names() -> list[str]:
    return list(_REGISTRY.keys())


def get(name: str) -> BackendAdapter | None:
    return _REGISTRY.get((name or "").strip().lower())


def installed_backends() -> list[BackendAdapter]:
    """Adapters whose binary actually exists on this machine (PATH probe,
    not login) — the set that gets a button at all."""
    return [a for a in _REGISTRY.values() if a.binary()]


def status_all(force: bool = False) -> dict[str, dict]:
    """name -> {installed, path, ok, detail} for every registered adapter —
    the one shared shape doctor.py and any future connectors matrix read."""
    out: dict[str, dict] = {}
    for a in _REGISTRY.values():
        path = a.binary()
        installed = bool(path)
        ident = a.identity(force) if installed else {}
        ok = bool(ident.get("logged_in")) if installed else False
        if installed:
            detail = ident.get("error") or ("logged in" if ok else "not logged in")
        else:
            detail = f"{a.name} not found — install it and log in once"
        out[a.name] = {"installed": installed, "path": path, "ok": ok, "detail": detail}
    return out


# ── agy adapter ──────────────────────────────────────────────

def _agy_binary() -> str | None:
    from zilla.config import CLI_PATH
    return CLI_PATH if CLI_PATH and os.path.exists(CLI_PATH) else None


def _agy_identity(force: bool = False) -> dict:
    from zilla.config import agy_reachable, agy_models_live, get_model
    installed = bool(_agy_binary())
    if force:
        agy_models_live(force=True)
    reachable = agy_reachable() if installed else False
    err = None
    if not installed:
        err = "agy not installed on this machine"
    elif not reachable:
        err = "agy installed but not responding — may be logged out (Google OAuth)"
    return {
        "logged_in": reachable, "account": None, "plan": None,
        "auth_method": "Google OAuth" if installed else None,
        "model": get_model() if installed else None, "error": err,
    }


def _agy_models() -> list[tuple[str, str]]:
    from zilla.config import model_catalog_for
    return model_catalog_for("agy")


def _agy_dispatch(prompt, conversation_id, progress_callback, cancel_event,
                  skip_permissions, use_browser=False, ctx=None):
    from zilla.cli_engine import run_cli
    return run_cli(prompt, conversation_id, progress_callback, cancel_event,
                   skip_permissions, ctx=ctx)


register(BackendAdapter(
    name="agy", label="agy (Antigravity / Gemini)", login_cmd="agy",
    model_flag=False,
    hint=("ℹ️ Backend: agy. This is the LIVE list from your Antigravity account "
          "(via `agy models`). Tap one to switch — it applies to your next "
          "message. ✏️ Custom takes any exact name agy accepts."),
    binary=_agy_binary, identity=_agy_identity, models=_agy_models,
    dispatch=_agy_dispatch,
))


# ── claude adapter ───────────────────────────────────────────

def _claude_binary() -> str | None:
    from zilla.config import CLAUDE_PATH
    return CLAUDE_PATH if CLAUDE_PATH and os.path.exists(CLAUDE_PATH) else None


def _claude_identity(force: bool = False) -> dict:
    from zilla.backends import claude_identity
    from zilla.config import get_model
    ident = claude_identity(force=force)
    return {
        "logged_in": bool(ident.get("loggedIn")),
        "account": ident.get("email") or ident.get("orgName"),
        "plan": ident.get("subscriptionType"),
        "auth_method": ident.get("authMethod"),
        "model": get_model(), "error": ident.get("error"),
    }


def _claude_models() -> list[tuple[str, str]]:
    from zilla.config import model_catalog_for
    return model_catalog_for("claude")


def _claude_dispatch(prompt, conversation_id, progress_callback, cancel_event,
                     skip_permissions, use_browser=False, ctx=None):
    from zilla.backends import run_claude
    from zilla.config import get_model
    return run_claude(
        prompt, conversation_id,
        progress_callback=progress_callback, cancel_event=cancel_event,
        skip_permissions=skip_permissions, model=get_model(),
        use_browser=use_browser, ctx=ctx,
    )


register(BackendAdapter(
    name="claude", label="Claude Code (Anthropic)", login_cmd="claude",
    model_flag=True,
    hint=("ℹ️ Backend: Claude Code. Pick Opus/Sonnet/Haiku, or ✏️ Custom for "
          "an exact model name. (Switch backend in /settings.)"),
    binary=_claude_binary, identity=_claude_identity, models=_claude_models,
    dispatch=_claude_dispatch,
))
