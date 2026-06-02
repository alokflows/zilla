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

import json
import time
import logging
import threading
import subprocess
from typing import Callable

from config import CLAUDE_PATH, CLI_WORKING_DIR, MAX_TOTAL_RUNTIME, AGI_BRAIN_DIR

logger = logging.getLogger(__name__)


def run_claude(
    prompt: str,
    conversation_id: str = None,
    progress_callback: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    skip_permissions: bool = False,
    model: str | None = None,
) -> tuple[str, str | None]:
    """
    Run one turn through Claude Code and return (response_text, session_id).

    - New conversation: omit --resume; Claude mints a session id and returns it.
    - Continue: pass the stored session id via --resume.
    Mirrors the agy engine's contract so bot.py needs no special-casing.
    """
    cmd = [CLAUDE_PATH, "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if conversation_id:
        cmd += ["--resume", conversation_id]
    if skip_permissions:
        cmd += ["--dangerously-skip-permissions"]
    # Let Claude operate in the same working dir the bot uses.
    cmd += ["--add-dir", CLI_WORKING_DIR]

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
            cmd, cwd=CLI_WORKING_DIR,
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


# Signals that Claude itself is rate-limited / out of quota (in addition to the
# shared cli_engine.detect_limit patterns).
def claude_limit_hint(text: str) -> bool:
    low = (text or "").lower()
    return any(s in low for s in (
        "rate limit", "usage limit", "quota", "overloaded",
        "too many requests", "credit balance", "upgrade",
    ))
