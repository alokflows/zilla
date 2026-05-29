# ============================================================
#  AGY RUNNER v7 — THIN PIPE + SMART PROGRESS
# ============================================================
#
#  PHILOSOPHY: The bot is a THIN PIPE.
#  - User sends message → pass DIRECTLY to agy → send response
#  - We capture intermediate progress from transcript.jsonl
#  - We clean and format the final response for Telegram
#
#  NEW in v7:
#  - Progress callbacks via transcript polling
#  - Conversation dump bug fix (tracks last seen step)
#  - Output sanitization for Telegram
#  - Model selection via settings
# ============================================================

import asyncio
import json
import os
import re
import time
import logging
import threading
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
            if len(cleaned) > 3:
                continue
            if consecutive_thinking <= 20:
                continue
        else:
            consecutive_thinking = 0

        cleaned.append(line)

    text = "\n".join(cleaned)
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


def get_latest_step(conversation_id: str) -> int:
    """Get the latest step index in a conversation transcript."""
    if not conversation_id:
        return 0
    path = os.path.join(
        BRAIN_DIR, conversation_id,
        ".system_generated", "logs", "transcript.jsonl"
    )
    if not os.path.exists(path):
        return 0
    last_step = -1
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if "step_index" in data:
                        last_step = max(last_step, int(data["step_index"]))
                except Exception:
                    pass
    except Exception:
        pass
    return last_step + 1


def get_new_responses(conversation_id: str, starting_step: int) -> str:
    """
    Get only NEW response content from transcript, starting after starting_step.
    This fixes the conversation dump bug — we only read responses we haven't seen.
    """
    if not conversation_id:
        return ""
    path = os.path.join(
        BRAIN_DIR, conversation_id,
        ".system_generated", "logs", "transcript.jsonl"
    )
    if not os.path.exists(path):
        return ""
    responses = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    step = data.get("step_index", -1)
                    # Only get responses AFTER our starting step
                    if step >= starting_step:
                        if data.get("type") == "PLANNER_RESPONSE" and data.get("content"):
                            content = data["content"].strip()
                            # Skip empty or very short thinking-only responses
                            if len(content) > 5:
                                responses.append(content)
                except Exception:
                    pass
    except Exception:
        pass

    if not responses:
        return ""

    # Return only the LAST substantial response to avoid dumping history
    # The last PLANNER_RESPONSE is the final answer
    return responses[-1]


# ══════════════════════════════════════════════════════════
#  PROGRESS TRACKING VIA TRANSCRIPT
# ══════════════════════════════════════════════════════════

# Map tool names to user-friendly progress messages
_TOOL_DISPLAY = {
    "read_url_content": "🌐 Reading web page",
    "search_web": "🔎 Searching the web",
    "view_file": "📄 Reading file",
    "run_command": "⚙️ Running command",
    "grep_search": "🔍 Searching code",
    "write_to_file": "✍️ Writing file",
    "replace_file_content": "✏️ Editing file",
    "multi_replace_file_content": "✏️ Editing file",
    "generate_image": "🎨 Generating image",
    "list_dir": "📂 Browsing directory",
    "read_browser_page": "🌐 Reading browser page",
    "invoke_subagent": "🤖 Launching sub-agent",
    "send_message": "💬 Messaging agent",
}


class TranscriptPoller:
    """
    Polls transcript.jsonl while agy is running to extract real-time progress.
    Calls progress_callback with user-friendly status messages.
    """

    def __init__(
        self,
        conversation_id: str | None,
        starting_step: int,
        progress_callback: Callable[[str], None] | None,
        poll_interval: float = 2.0,
    ):
        self.conversation_id = conversation_id
        self.starting_step = starting_step
        self.callback = progress_callback
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_step_seen = starting_step
        self._last_message = ""

    def start(self):
        if not self.callback or not self.conversation_id:
            return
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def update_conversation_id(self, conv_id: str):
        """Update conversation ID when detected for new conversations."""
        self.conversation_id = conv_id

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                self._check_transcript()
            except Exception as e:
                logger.debug(f"[POLLER] Error: {e}")
            self._stop.wait(self.poll_interval)

    def _check_transcript(self):
        if not self.conversation_id:
            return

        path = os.path.join(
            BRAIN_DIR, self.conversation_id,
            ".system_generated", "logs", "transcript.jsonl"
        )
        if not os.path.exists(path):
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        step = data.get("step_index", -1)
                        if step <= self._last_step_seen:
                            continue

                        self._last_step_seen = step
                        msg = self._extract_progress(data)
                        if msg and msg != self._last_message:
                            self._last_message = msg
                            try:
                                self.callback(msg)
                            except Exception:
                                pass
                    except Exception:
                        pass
        except Exception:
            pass

    def _extract_progress(self, data: dict) -> str | None:
        """Extract a user-friendly progress message from a transcript step."""
        step_type = data.get("type", "")

        # Tool calls — most informative
        tool_calls = data.get("tool_calls", [])
        if tool_calls:
            for tc in tool_calls:
                tool_name = tc.get("name", "")
                args = tc.get("args", {})

                # Get display name for this tool
                display = _TOOL_DISPLAY.get(tool_name, None)
                if display:
                    # Try to get the specific action from args
                    action = args.get("toolAction", "").strip('"')
                    if action:
                        return f"{display}: {action}"
                    return display

            return "🧠 Processing..."

        # Thinking content (from PLANNER_RESPONSE without tool calls)
        if step_type == "PLANNER_RESPONSE":
            content = data.get("content", "")
            thinking = data.get("thinking", "")
            if thinking and len(thinking) > 10:
                # Summarize the thinking into a short status
                short = thinking[:80].split(".")[0].strip()
                if short:
                    return f"🧠 {short}..."
            elif content and len(content) < 100:
                return f"🧠 Thinking..."

        return None


# ══════════════════════════════════════════════════════════
#  INSTRUCTION FILE
# ══════════════════════════════════════════════════════════

_INSTRUCTIONS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "bot_instructions.md"
)

_cached_instructions: str | None = None


def get_instructions() -> str | None:
    """Read the bot_instructions.md file (cached)."""
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
#  MODEL SELECTION
# ══════════════════════════════════════════════════════════

def get_selected_model() -> str | None:
    """Read the selected model from settings or file."""
    # Try settings_manager first
    try:
        from settings_manager import SettingsManager
        from config import SETTINGS_FILE
        sm = SettingsManager(SETTINGS_FILE)
        model = sm.get_model()
        if model:
            return model
    except Exception:
        pass

    # Fallback to selected_model.txt
    model_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "selected_model.txt"
    )
    if os.path.exists(model_file):
        try:
            with open(model_file, "r", encoding="utf-8") as f:
                return f.read().strip() or None
        except Exception:
            pass
    return None


# ══════════════════════════════════════════════════════════
#  CORE — Run agy via ConPTY
# ══════════════════════════════════════════════════════════

def run_agy_pty(
    prompt: str,
    conversation_id: str = None,
    timeout: int = None,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[str, str | None]:
    """
    Run agy with the user's EXACT message. No wrapping. No orchestration.

    The bot is a thin pipe:
    - User message goes directly to agy
    - agy does all the thinking, searching, decomposition
    - We just capture and clean the output
    - NEW: progress_callback gets called with status updates

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

    cmd_parts.append("--dangerously-skip-permissions")

    timeout_minutes = max(1, timeout // 60)
    cmd_parts.append(f"--print-timeout {timeout_minutes}m")

    cmd_parts.append(f'--print "{safe_prompt}"')

    command = " ".join(cmd_parts)
    logger.info(f"[AGY] Command: {command[:200]}...")

    # Snapshot brain directory BEFORE (for new conversation detection)
    before_convs = get_brain_conversations() if not conversation_id else set()
    starting_step = get_latest_step(conversation_id) if conversation_id else 0

    # Start progress poller
    poller = TranscriptPoller(conversation_id, starting_step, progress_callback)

    try:
        # Set up environment with model selection
        custom_env = os.environ.copy()
        model_id = get_selected_model()
        if model_id:
            custom_env["ANTIGRAVITY_MODEL"] = model_id
            custom_env["GEMINI_API_MODEL"] = model_id
            custom_env["MODEL"] = model_id
            logger.info(f"[AGY] Using model: {model_id}")

        # winpty.PTY.spawn expects env as a string of null-separated KEY=VALUE pairs, terminated by two null bytes.
        env_str = '\0'.join(f'{k}={v}' for k, v in custom_env.items()) + '\0\0'

        pty = winpty.PTY(
            200, 1000,
            backend=CONPTY_BACKEND,
            agent_config=COLOR_ESCAPES,
        )
        pty.spawn(command, cwd=AGY_WORKING_DIR, env=env_str)

        # Send initial progress
        if progress_callback:
            progress_callback("🧠 Processing your request...")

        output_chunks = []
        start_time = time.time()

        # For new conversations, check for conversation ID early
        detected_id = conversation_id

        while True:
            elapsed = time.time() - start_time

            # Hard timeout
            if elapsed > timeout + 30:
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
            except Exception:
                pass

            # Detect new conversation ID early (for progress polling)
            if not detected_id and elapsed > 3:
                after_convs = get_brain_conversations()
                new_ids = after_convs - before_convs
                if new_ids:
                    detected_id = new_ids.pop()
                    poller.update_conversation_id(detected_id)
                    if not poller._thread:
                        poller.start()
                    logger.info(f"[AGY] Early detected conversation: {detected_id}")

            # Start poller once we have a conversation ID
            if detected_id and not poller._thread and poller.callback:
                poller.update_conversation_id(detected_id)
                poller.start()

            time.sleep(0.15)

        # Stop the progress poller
        poller.stop()

        raw_output = "".join(output_chunks)

        # Debug logging
        raw_stripped = strip_ansi(raw_output)
        logger.info(
            f"[AGY] Raw output: {len(raw_output)} bytes, "
            f"stripped: {len(raw_stripped)} chars"
        )

        response = clean_response(raw_output)

        # Final conversation ID detection
        if not detected_id:
            after_convs = get_brain_conversations()
            new_ids = after_convs - before_convs
            if new_ids:
                detected_id = new_ids.pop()
                logger.info(f"[AGY] Detected new conversation: {detected_id}")

        # Get REAL response from transcript — this is the reliable path
        if detected_id:
            real_response = get_new_responses(detected_id, starting_step)
            if real_response:
                response = real_response

        if not response:
            response = (
                "agy returned an empty response. "
                "Try rephrasing your question."
            )

        logger.info(f"[AGY] Response length: {len(response)} chars")
        return response, detected_id

    except Exception as e:
        poller.stop()
        logger.error(f"[AGY] Error: {e}", exc_info=True)
        return f"Error running agy: {str(e)}", conversation_id


async def run_agy_async(
    prompt: str,
    conversation_id: str = None,
    timeout: int = None,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[str, str | None]:
    """Async wrapper — runs blocking PTY call in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        run_agy_pty,
        prompt,
        conversation_id,
        timeout,
        progress_callback,
    )
