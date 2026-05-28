# ============================================================
#  AGY RUNNER v6 — THIN PIPE
# ============================================================
#
#  PHILOSOPHY: The bot is a THIN PIPE.
#  - User sends message → pass DIRECTLY to agy → send response
#  - NO orchestration prompts, NO wrapping, NO "smart" routing
#  - Agy (Antigravity CLI) handles EVERYTHING internally:
#    web search, thinking, decomposition, tool use
#  - We just need to capture the FINAL response cleanly
#
#  WHAT THIS FILE DOES:
#  1. Spawns agy with the user's EXACT message via ConPTY
#  2. Reads the output, cleans ANSI codes
#  3. Strips agy's internal "thinking" logs to get clean output
#  4. Returns the response to the bot for sending to Telegram
#
#  WHAT THIS FILE DOES NOT DO:
#  - No orchestration prompts
#  - No task classification
#  - No wrapping the user's message in anything
#  - No trying to be smarter than agy
# ============================================================

import asyncio
import os
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import winpty

from config import (
    AGY_PATH,
    AGY_WORKING_DIR,
    AGY_TIMEOUT,
    BRAIN_DIR,
)

logger = logging.getLogger(__name__)

executor = ThreadPoolExecutor(max_workers=4)

# ConPTY backend — runs inside this process, invisible
CONPTY_BACKEND = winpty.Backend.ConPTY
COLOR_ESCAPES = winpty.AgentConfig.WINPTY_FLAG_COLOR_ESCAPES


# ══════════════════════════════════════════════════════════
#  TEXT CLEANING
# ══════════════════════════════════════════════════════════

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;]*[a-zA-Z]"
    r"|\x1b\][^\x07]*\x07"
    r"|\x1b\[.*?[@-~]"
    r"|\x1b[()][AB012]"
    r"|\x1b[>=<]"
    r"|\r"
)

# Patterns that indicate agy's internal reasoning/thinking
# These get stripped from the final response
_THINKING_PATTERNS = re.compile(
    r"^(?:"
    r"Let me (?:grab|look|search|check|find|get|try|see|extract|read|also|quickly).*"
    r"|Now (?:let me|I (?:can|have|will|need|should)).*"
    r"|I (?:can see|will|need to|should|'ll|notice|'m going).*"
    r"|The (?:HTML|page|content|data|actual|response|remaining|output) (?:is|are|has|have|contains|shows).*"
    r"|(?:Searching|Looking|Checking|Reading|Fetching|Grabbing|Extracting|Navigating|Processing|Analyzing).*"
    r"|(?:OK|Okay|Alright|Right|Good|Great|Sure|Got it)(?:,|\.).*"
    r"|.*(?:Let me (?:also |quickly |now |try to |look |search |check |get |extract )).*"
    r"|.*(?:further (?:down|into|in) the (?:page|document|file|content)).*"
    r")$",
    re.MULTILINE | re.IGNORECASE,
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


def clean_response(text: str) -> str:
    """
    Clean PTY output into readable text.
    
    1. Strip ANSI escape codes
    2. Strip agy's chain-of-thought reasoning
    3. Collapse excessive blank lines
    4. Trim whitespace
    """
    text = strip_ansi(text)
    
    # Strip agy's reasoning lines (e.g. "Let me grab...", "The HTML is...")
    lines = text.split("\n")
    cleaned = []
    consecutive_thinking = 0
    
    for line in lines:
        stripped = line.strip()
        
        # Skip empty lines at the start
        if not stripped and not cleaned:
            continue
        
        # Check if this line is agy's internal thinking
        if stripped and _THINKING_PATTERNS.match(stripped):
            consecutive_thinking += 1
            # If we already have substantial content, skip thinking lines
            if len(cleaned) > 3:
                continue
            # At the start, skip up to 20 thinking lines
            if consecutive_thinking <= 20:
                continue
        else:
            consecutive_thinking = 0
        
        cleaned.append(line)
    
    text = "\n".join(cleaned)
    
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    
    result = text.strip()
    
    # Safety: if we stripped too much, return the ANSI-cleaned original
    if len(result) < 20 and len(strip_ansi(text)) > 50:
        return re.sub(r"\n{3,}", "\n\n", strip_ansi(text)).strip()
    
    return result


# ══════════════════════════════════════════════════════════
#  BRAIN CONVERSATION TRACKING
# ══════════════════════════════════════════════════════════

def get_brain_conversations() -> set[str]:
    """List all conversation UUIDs in the brain directory."""
    try:
        return set(os.listdir(BRAIN_DIR))
    except Exception:
        return set()

import json
def get_latest_step(conversation_id: str) -> int:
    if not conversation_id: return 0
    path = os.path.join(BRAIN_DIR, conversation_id, ".system_generated", "logs", "transcript.jsonl")
    if not os.path.exists(path): return 0
    last_step = -1
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if "step_index" in data:
                        last_step = max(last_step, int(data["step_index"]))
                except: pass
    except: pass
    return last_step + 1

def get_new_responses(conversation_id: str, starting_step: int) -> str:
    if not conversation_id: return ""
    path = os.path.join(BRAIN_DIR, conversation_id, ".system_generated", "logs", "transcript.jsonl")
    if not os.path.exists(path): return ""
    responses = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if data.get("step_index", -1) >= starting_step:
                        if data.get("type") == "PLANNER_RESPONSE" and data.get("content"):
                            responses.append(data["content"].strip())
                except: pass
    except: pass
    return "\n\n".join(responses)


# ══════════════════════════════════════════════════════════
#  INSTRUCTION FILE — Read once at startup
# ══════════════════════════════════════════════════════════

_INSTRUCTIONS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "bot_instructions.md"
)

_cached_instructions: str | None = None


def get_instructions() -> str | None:
    """
    Read the bot_instructions.md file (cached).
    This file tells the bot HOW to behave when processing messages.
    Returns None if file doesn't exist.
    """
    global _cached_instructions
    if _cached_instructions is not None:
        return _cached_instructions
    
    if os.path.exists(_INSTRUCTIONS_FILE):
        try:
            with open(_INSTRUCTIONS_FILE, "r", encoding="utf-8") as f:
                _cached_instructions = f.read().strip()
            logger.info(f"[AGY] Loaded instructions from {_INSTRUCTIONS_FILE}")
            return _cached_instructions
        except Exception as e:
            logger.error(f"[AGY] Failed to load instructions: {e}")
    
    return None


def reload_instructions():
    """Force reload of instructions file."""
    global _cached_instructions
    _cached_instructions = None
    return get_instructions()


# ══════════════════════════════════════════════════════════
#  CORE — Run agy via ConPTY
# ══════════════════════════════════════════════════════════

def run_agy_pty(
    prompt: str,
    conversation_id: str = None,
    timeout: int = None,
) -> tuple[str, str | None]:
    """
    Run agy with the user's EXACT message. No wrapping. No orchestration.
    
    The bot is a thin pipe:
    - User message goes directly to agy
    - agy does all the thinking, searching, decomposition
    - We just capture and clean the output
    
    Returns: (response_text, conversation_id)
    """
    timeout = timeout or AGY_TIMEOUT
    
    # Prepend instructions for new conversations
    instructions = get_instructions()
    if not conversation_id and instructions:
        prompt = f"{instructions}\n\nUser Message:\n{prompt}"
        
    # Escape quotes in the prompt for shell safety
    safe_prompt = prompt.replace('"', '\\"')

    # Build command — pass message DIRECTLY to agy
    cmd_parts = [f'"{AGY_PATH}"']

    if conversation_id:
        cmd_parts.append(f"--conversation {conversation_id}")
        logger.info(f"[AGY] Continuing conversation {conversation_id[:12]}...")
    else:
        logger.info("[AGY] Starting new conversation...")

    # Auto-approve tool permissions — without this, agy can't use
    # any tools (web search, file ops, etc.) in non-interactive mode
    # and just returns a generic greeting
    cmd_parts.append("--dangerously-skip-permissions")

    # Use --print-timeout to match our timeout
    # This tells agy's built-in print mode how long to wait
    timeout_minutes = max(1, timeout // 60)
    cmd_parts.append(f"--print-timeout {timeout_minutes}m")
    
    # Pass the user's message EXACTLY as-is
    cmd_parts.append(f'--print "{safe_prompt}"')
    
    command = " ".join(cmd_parts)
    logger.info(f"[AGY] Command: {command[:200]}...")

    # Snapshot brain directory BEFORE (for new conversation detection)
    before_convs = get_brain_conversations() if not conversation_id else set()
    starting_step = get_latest_step(conversation_id) if conversation_id else 0

    try:
        pty = winpty.PTY(
            200,                    # cols
            1000,                   # rows
            backend=CONPTY_BACKEND,
            agent_config=COLOR_ESCAPES,
        )
        pty.spawn(
            command,
            cwd=AGY_WORKING_DIR,
        )

        output_chunks = []
        last_chunk_time = time.time()
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time

            # Hard timeout (our own, separate from agy's --print-timeout)
            if elapsed > timeout + 30:  # Give 30s grace beyond our timeout
                logger.warning(f"[AGY] Hard timeout after {elapsed:.0f}s")
                try:
                    remaining = pty.read()
                    if remaining:
                        output_chunks.append(remaining)
                except Exception:
                    pass
                break

            # Process exited naturally
            if not pty.isalive():
                # Drain remaining output
                try:
                    remaining = pty.read()
                    if remaining:
                        output_chunks.append(remaining)
                except Exception:
                    pass
                logger.info(f"[AGY] Process exited after {elapsed:.1f}s")
                break

            # Read available output
            try:
                data = pty.read(blocking=False)
                if data:
                    output_chunks.append(data)
                    last_chunk_time = time.time()
            except Exception:
                pass

            time.sleep(0.15)

        raw_output = "".join(output_chunks)
        
        # Debug: log raw output length vs cleaned
        raw_stripped = strip_ansi(raw_output)
        logger.info(f"[AGY] Raw output: {len(raw_output)} bytes, stripped: {len(raw_stripped)} chars")
        if len(raw_stripped) < 200:
            logger.info(f"[AGY] Raw (stripped): {repr(raw_stripped[:500])}")
        
        response = clean_response(raw_output)

        # Detect new conversation ID
        detected_id = conversation_id
        if not conversation_id:
            after_convs = get_brain_conversations()
            new_ids = after_convs - before_convs
            if new_ids:
                detected_id = new_ids.pop()
                logger.info(f"[AGY] Detected new conversation: {detected_id}")
                
        # Get REAL response from transcript to bypass stdout history issues
        if detected_id:
            real_response = get_new_responses(detected_id, starting_step)
            if real_response:
                response = real_response

        if not response:
            response = "agy returned an empty response. Try rephrasing your question."

        logger.info(f"[AGY] Response length: {len(response)} chars")
        if len(response) < 200:
            logger.info(f"[AGY] Response: {repr(response[:500])}")
        return response, detected_id

    except Exception as e:
        logger.error(f"[AGY] Error: {e}", exc_info=True)
        return f"Error running agy: {str(e)}", conversation_id


async def run_agy_async(
    prompt: str,
    conversation_id: str = None,
    timeout: int = None,
) -> tuple[str, str | None]:
    """Async wrapper — runs blocking PTY call in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        run_agy_pty,
        prompt,
        conversation_id,
        timeout,
    )
