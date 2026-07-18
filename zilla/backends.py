# ============================================================
#  BACKENDS — which AI CLI actually answers messages
# ============================================================
#  The bot can run on either of two backends:
#
#    "agy"    — the antigravity CLI (Gemini). Lives in cli_engine.run_cli
#               (needs a pseudo-terminal; see platform_compat.PtyProcess).
#    "claude" — Claude Code. Implemented here in run_claude(): it shells out to
#               `claude -p <prompt> --output-format json`, which returns the
#               answer + a session id as clean JSON over a normal pipe — no PTY,
#               so it works the same on Windows / macOS / Linux.
#
#  ┌──────────────────────────────────────────────────────────────────────┐
#  │  HOW TO SWITCH BACKEND:                                                │
#  │    • Edit .env:  BACKEND=claude   (or  BACKEND=agy), then restart.     │
#  │    • Or from Telegram:  /settings → "Backend".                        │
#  │  HOW TO ADD A NEW BACKEND:                                             │
#  │    1. Write a run_<name>(...) with the SAME signature as run_claude.   │
#  │    2. Register it in dispatch() below.                                 │
#  │    3. Add its model list to config.model_catalog().                   │
#  └──────────────────────────────────────────────────────────────────────┘
# ============================================================

import os
import json
import time
import logging
import threading
import subprocess
from typing import Callable

from zilla.config import CLAUDE_PATH, CLI_WORKING_DIR, MAX_TOTAL_RUNTIME

logger = logging.getLogger(__name__)


def _ensure_mcp_configs() -> None:
    """Write the two pinned MCP config files (browser + none) into the cache dir.
    Idempotent and cheap. We always pass an EXPLICIT --mcp-config so the turn is
    deterministic: 'none' = no servers (fast simple turns), 'browser' = the pinned
    Playwright server. This replaces inheriting the flaky user-scope @latest config."""
    from zilla.config import (MCP_CONFIG_DIR, MCP_BROWSER_CONFIG, MCP_NONE_CONFIG,
                        PLAYWRIGHT_MCP_VERSION)
    os.makedirs(MCP_CONFIG_DIR, exist_ok=True)
    browser = {
        "mcpServers": {
            "playwright": {
                "type": "stdio",
                "command": "npx",
                "args": [f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}", "--headless"],
                "env": {},
            }
        }
    }
    none = {"mcpServers": {}}
    for path, data in ((MCP_BROWSER_CONFIG, browser), (MCP_NONE_CONFIG, none)):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"[CLAUDE] could not write MCP config {path}: {e}")


def run_claude(
    prompt: str,
    conversation_id: str = None,
    progress_callback: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    skip_permissions: bool = False,
    model: str | None = None,
    use_browser: bool = False,
    ctx=None,
) -> tuple[str, str | None]:
    """
    Run one turn through Claude Code and return (response_text, session_id).

    - New conversation: omit --resume; Claude mints a session id and returns it.
    - Continue: pass the stored session id via --resume.
    Mirrors the agy engine's contract so bot.py needs no special-casing.
    """
    # Layer-1 harness: inject the operating context (full onboarding on a new
    # conversation, the compact trust/style contract on continued ones). Claude
    # previously received NO instructions at all — this fixes that.
    from zilla.harness import wrap_prompt
    prompt = wrap_prompt(prompt, is_new=not conversation_id, backend="claude", ctx=ctx)

    cmd = [CLAUDE_PATH, "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if conversation_id:
        cmd += ["--resume", conversation_id]
    if skip_permissions:
        cmd += ["--dangerously-skip-permissions"]
    # Let Claude operate in the same working dir the bot uses.
    cmd += ["--add-dir", CLI_WORKING_DIR]

    # Deterministic MCP: attach the embedded browser ONLY for web/interactive
    # turns, an explicit empty config otherwise. --strict-mcp-config ignores the
    # ambient user-scope config so simple turns never pay the ~2s browser startup
    # and the flaky @latest race can't strand the tools.
    from zilla.config import MCP_BROWSER_CONFIG, MCP_NONE_CONFIG, MCP_STARTUP_TIMEOUT_MS
    _ensure_mcp_configs()
    cfg = MCP_BROWSER_CONFIG if use_browser else MCP_NONE_CONFIG
    cmd += ["--strict-mcp-config", "--mcp-config", cfg]
    env = os.environ.copy()
    if use_browser:
        env["MCP_TIMEOUT"] = MCP_STARTUP_TIMEOUT_MS
        logger.info("[CLAUDE] embedded browser attached for this turn")

    if progress_callback:
        try:
            progress_callback("🤖 Claude is thinking…")
        except Exception:
            pass

    conv_label = conversation_id[:8] if conversation_id else "new"
    logger.info(f"[CLAUDE] run conv={conv_label} model={model or 'default'}")

    max_runtime = MAX_TOTAL_RUNTIME if MAX_TOTAL_RUNTIME > 0 else 3600
    try:
        proc = subprocess.Popen(
            cmd, cwd=CLI_WORKING_DIR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return (f"Claude Code not found at: {CLAUDE_PATH}\n"
                f"Install it and run `claude` once to log in, or set CLAUDE_PATH in .env.", None)
    except Exception as e:
        logger.error(f"[CLAUDE] spawn failed: {e}")
        return (f"Could not start Claude Code: {e}", None)

    # Wait with cancel + hard-ceiling support (no idle reaper needed: -p is one-shot).
    start = time.time()
    while True:
        if proc.poll() is not None:
            break
        if cancel_event and cancel_event.is_set():
            _kill(proc)
            return ("🛑 Canceled.", conversation_id)
        if time.time() - start > max_runtime:
            _kill(proc)
            logger.warning("[CLAUDE] max runtime hit")
            return ("⏱️ Timed out.", conversation_id)
        time.sleep(0.2)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0 and not (stdout or "").strip():
        msg = (stderr or "").strip() or f"claude exited {proc.returncode}"
        logger.warning(f"[CLAUDE] error: {msg[:300]}")
        return (f"Claude error: {msg[:500]}", conversation_id)

    return _parse_claude_json(stdout, conversation_id)


def _parse_claude_json(stdout: str, conversation_id: str | None) -> tuple[str, str | None]:
    """Claude --output-format json prints one JSON object: {result, session_id, ...}."""
    text = (stdout or "").strip()
    if not text:
        return ("No response from Claude.", conversation_id)
    # Be tolerant: find the last JSON object in the stream.
    obj = None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    obj = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    if not isinstance(obj, dict):
        # Not JSON — return raw text (still useful) and keep the old conv id.
        return (text[:8000], conversation_id)

    session_id = obj.get("session_id") or conversation_id
    result = obj.get("result")
    if obj.get("is_error"):
        result = result or obj.get("error") or "Claude reported an error."
    if result is None:
        result = obj.get("error") or "No response from Claude."
    return (str(result), session_id)


def _kill(proc):
    try:
        proc.kill()
    except Exception:
        pass


# ── Live identity: which account is logged in, plan, current auth ──
#  `claude auth status` prints JSON: {loggedIn, authMethod, email, orgName,
#  subscriptionType, ...}. We surface this in the bot so the model picker shows
#  the REAL account/plan instead of pretending. Cached briefly (it shells out).
_identity_cache = {"data": None, "ts": 0.0}
_IDENTITY_TTL = 60.0


def claude_identity(timeout: int = 8, force: bool = False) -> dict:
    """Return Claude Code's auth status as a dict. Honest on failure:
    {loggedIn: False, error: ...}. Cached for _IDENTITY_TTL seconds."""
    now = time.time()
    if not force and _identity_cache["data"] is not None and now - _identity_cache["ts"] < _IDENTITY_TTL:
        return _identity_cache["data"]
    data: dict = {"loggedIn": False, "error": None}
    try:
        proc = subprocess.run(
            [CLAUDE_PATH, "auth", "status"],
            capture_output=True, text=True, timeout=timeout,
        )
        parsed = json.loads((proc.stdout or "").strip() or "{}")
        if isinstance(parsed, dict):
            data = parsed
    except FileNotFoundError:
        data = {"loggedIn": False, "error": f"claude not found at {CLAUDE_PATH}"}
    except subprocess.TimeoutExpired:
        data = {"loggedIn": False, "error": "claude auth status timed out"}
    except Exception as e:
        data = {"loggedIn": False, "error": str(e)[:200]}
    _identity_cache["data"] = data
    _identity_cache["ts"] = now
    return data


# Signals that Claude itself is rate-limited / out of quota (in addition to the
# shared cli_engine.detect_limit patterns).
def claude_limit_hint(text: str) -> bool:
    low = (text or "").lower()
    return any(s in low for s in (
        "rate limit", "usage limit", "quota", "overloaded",
        "too many requests", "credit balance", "upgrade",
    ))
