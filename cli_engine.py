# ============================================================
#  CLI ENGINE — Thin Wrapper Around the AI CLI
# ============================================================
#  Runs the CLI (default: Antigravity) via Windows ConPTY,
#  captures output, and extracts clean responses from the
#  transcript.jsonl file for reliable delivery.
#
#  Backend-swappable: change CLI_PATH in config to use
#  any CLI that accepts text in and produces text out.
# ============================================================

import asyncio
import json
import os
import re
import time
import logging
import threading
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import winpty

from config import CLI_PATH, CLI_WORKING_DIR, CLI_TIMEOUT, BRAIN_DIR, SKILLS_DIR

logger = logging.getLogger(__name__)

executor = ThreadPoolExecutor(max_workers=4)

# ConPTY backend
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
    r"|\]0;.*?(?:\x07|\\)"
    r"|\r"
)

_THINKING_PATTERNS = re.compile(
    r"^(?:"
    r"Let me (?:grab|look|search|check|find|get|try|see|extract|read|also|quickly).*"
    r"|Now (?:let me|I (?:can|have|will|need|should)).*"
    r"|I (?:can see|will|need to|should|'ll|notice|'m going).*"
    r"|(?:Searching|Looking|Checking|Reading|Fetching|Grabbing|Extracting|Navigating|Processing|Analyzing).*"
    r"|(?:OK|Okay|Alright|Right|Good|Great|Sure|Got it)(?:,|\.).*"
    r")$",
    re.MULTILINE | re.IGNORECASE,
)

_HISTORY_LINE_RE = re.compile(r"^\s*(?:User|Assistant|Human|System|AI|Bot)\s*:", re.IGNORECASE)
_DIR_LISTING_RE = re.compile(r"(?:^[A-Za-z]:\\|^/)[^\n]{5,}$", re.MULTILINE)
_METADATA_LINE_RE = re.compile(
    r"(?:conversation_id|step_index|user_id|session_name|source.*?telegram|source.*?desktop)",
    re.IGNORECASE,
)
_JSON_DEBUG_BLOCK_RE = re.compile(
    r"\{[\s\S]{50,}?(?:\"tool_calls\"|\"step_index\"|\"type\":\s*\"PLANNER)[\s\S]*?\}",
    re.MULTILINE,
)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def clean_response(text: str) -> str:
    """Clean PTY output: strip ANSI, thinking patterns, collapse blanks."""
    text = strip_ansi(text)
    lines = text.split("\n")
    cleaned = []
    consecutive_thinking = 0

    for line in lines:
        stripped = line.strip()
        if not stripped and not cleaned:
            continue
        if stripped and _THINKING_PATTERNS.match(stripped):
            consecutive_thinking += 1
            if len(cleaned) > 3 or consecutive_thinking <= 20:
                continue
        else:
            consecutive_thinking = 0
        cleaned.append(line)

    text = "\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)
    result = text.strip()

    if len(result) < 20 and len(strip_ansi(text)) > 50:
        return re.sub(r"\n{3,}", "\n\n", strip_ansi(text)).strip()
    return result


def sanitize_response(text: str) -> str:
    """Strip internal/debug content that should never reach the user."""
    if not text:
        return text

    lines = text.split("\n")
    cleaned = []
    history_streak = 0
    dir_path_streak = 0

    for line in lines:
        if _HISTORY_LINE_RE.match(line):
            history_streak += 1
            if history_streak >= 3:
                while cleaned and _HISTORY_LINE_RE.match(cleaned[-1]):
                    cleaned.pop()
                continue
        else:
            history_streak = 0

        if _DIR_LISTING_RE.match(line.strip()):
            dir_path_streak += 1
            if dir_path_streak > 3:
                continue
        else:
            dir_path_streak = 0

        if _METADATA_LINE_RE.search(line):
            continue

        cleaned.append(line)

    text = "\n".join(cleaned)
    text = _JSON_DEBUG_BLOCK_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ══════════════════════════════════════════════════════════
#  BRAIN / TRANSCRIPT TRACKING
# ══════════════════════════════════════════════════════════

def get_latest_step(conversation_id: str) -> int:
    """Get the latest step index in a conversation transcript."""
    if not conversation_id:
        return 0
    path = os.path.join(
        BRAIN_DIR, conversation_id, ".system_generated", "logs", "transcript.jsonl"
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
    """Get only NEW response content from transcript after starting_step."""
    if not conversation_id:
        return ""
    path = os.path.join(
        BRAIN_DIR, conversation_id, ".system_generated", "logs", "transcript.jsonl"
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
                    if step >= starting_step:
                        if data.get("type") == "PLANNER_RESPONSE" and data.get("content"):
                            content = data["content"].strip()
                            if len(content) > 5:
                                responses.append(content)
                except Exception:
                    pass
    except Exception:
        pass

    if not responses:
        return ""

    result = "\n\n".join(responses)

    # Cap at 3500 for Telegram
    if len(result) > 3500:
        result = result[:3500] + "\n\n⚠️ _(Response was truncated due to length)_"

    # Sanitize directory listings
    if len(_DIR_LISTING_RE.findall(result)) > 3:
        result = sanitize_response(result)

    # Hard cap
    if len(result) > 10000:
        result = result[:10000] + "\n\n⚠️ _(Response was truncated due to length)_"

    return result


def _extract_file_paths(conversation_id: str, starting_step: int) -> list[str]:
    """Scan transcript tool calls for file paths created by the agent."""
    if not conversation_id:
        return []
    path = os.path.join(
        BRAIN_DIR, conversation_id, ".system_generated", "logs", "transcript.jsonl"
    )
    if not os.path.exists(path):
        return []

    file_paths = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if data.get("step_index", -1) < starting_step:
                        continue
                    for tc in data.get("tool_calls", []):
                        tool_name = tc.get("name", "")
                        args = tc.get("args", {})
                        if tool_name == "write_to_file":
                            target = args.get("TargetFile", "")
                            if target and os.path.isfile(target):
                                file_paths.append(target)
                        elif tool_name == "generate_image":
                            img_name = args.get("ImageName", "")
                            if img_name:
                                for ext in [".png", ".jpg", ".jpeg", ".webp"]:
                                    candidate = os.path.join(BRAIN_DIR, conversation_id, f"{img_name}{ext}")
                                    if os.path.isfile(candidate):
                                        file_paths.append(candidate)
                except Exception:
                    pass
    except Exception:
        pass

    # Deduplicate, cap at 3
    seen = set()
    unique = []
    for fp in file_paths:
        norm = os.path.normpath(fp)
        if norm not in seen:
            seen.add(norm)
            unique.append(norm)
        if len(unique) >= 3:
            break
    return unique


# ══════════════════════════════════════════════════════════
#  PROGRESS TRACKING
# ══════════════════════════════════════════════════════════

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
    """Polls transcript.jsonl for real-time progress updates."""

    def __init__(self, conversation_id: str | None, starting_step: int,
                 progress_callback: Callable[[str], None] | None, poll_interval: float = 2.0):
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

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                self._check()
            except Exception:
                pass
            self._stop.wait(self.poll_interval)

    def _check(self):
        if not self.conversation_id:
            return
        path = os.path.join(
            BRAIN_DIR, self.conversation_id, ".system_generated", "logs", "transcript.jsonl"
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
                        msg = self._progress_msg(data)
                        if msg and msg != self._last_message:
                            self._last_message = msg
                            self.callback(msg)
                    except Exception:
                        pass
        except Exception:
            pass

    def _progress_msg(self, data: dict) -> str | None:
        tool_calls = data.get("tool_calls", [])
        if tool_calls:
            for tc in tool_calls:
                display = _TOOL_DISPLAY.get(tc.get("name", ""))
                if display:
                    action = tc.get("args", {}).get("toolAction", "").strip('"')
                    return f"{display}: {action}" if action else display
            return "🧠 Processing..."
        if data.get("type") == "PLANNER_RESPONSE":
            thinking = data.get("thinking", "")
            if thinking and len(thinking) > 10:
                short = thinking[:80].split(".")[0].strip()
                if short:
                    return f"🧠 {short}..."
        return None


# ══════════════════════════════════════════════════════════
#  INSTRUCTIONS
# ══════════════════════════════════════════════════════════

_INSTRUCTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_instructions.md")
_cached_instructions: str | None = None


def get_instructions() -> str | None:
    """Read bot_instructions.md (cached) and inject skills summary."""
    global _cached_instructions
    if _cached_instructions is not None:
        return _cached_instructions

    if not os.path.exists(_INSTRUCTIONS_FILE):
        return None

    try:
        with open(_INSTRUCTIONS_FILE, "r", encoding="utf-8") as f:
            _cached_instructions = f.read().strip()
    except Exception:
        return None

    # Inject skills summary
    try:
        skills_summary = _get_skills_summary()
        if skills_summary:
            _cached_instructions += "\n\n### Available Skills:\n" + skills_summary
    except Exception as e:
        logger.error(f"[ENGINE] Failed to inject skills: {e}")

    return _cached_instructions


def reload_instructions():
    global _cached_instructions
    _cached_instructions = None
    return get_instructions()


def _get_skills_summary() -> str:
    """Scan SKILLS_DIR for installed skills."""
    if not os.path.isdir(SKILLS_DIR):
        return ""
    lines = []
    try:
        for name in sorted(os.listdir(SKILLS_DIR)):
            skill_md = os.path.join(SKILLS_DIR, name, "SKILL.md")
            if os.path.isfile(skill_md):
                desc = _parse_skill_description(skill_md)
                lines.append(f"- **{name}**: {desc}")
    except Exception:
        pass
    return "\n".join(lines) if lines else ""


def _parse_skill_description(skill_md: str) -> str:
    """Parse description from SKILL.md YAML frontmatter."""
    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read(2000)
        # Simple YAML frontmatter parse
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                frontmatter = content[3:end]
                for line in frontmatter.split("\n"):
                    if line.strip().startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                        if desc and not desc.startswith("|"):
                            return desc[:100]
                        # Multi-line description
                        idx = frontmatter.find("description:")
                        after = frontmatter[idx:]
                        desc_lines = []
                        for dl in after.split("\n")[1:]:
                            if dl.startswith("  ") or dl.startswith("\t"):
                                desc_lines.append(dl.strip())
                            else:
                                break
                        return " ".join(desc_lines)[:100] if desc_lines else name
    except Exception:
        pass
    return "(no description)"


# ══════════════════════════════════════════════════════════
#  MODEL SELECTION
# ══════════════════════════════════════════════════════════

def get_selected_model() -> str | None:
    """Read the selected model from config settings."""
    from config import get_model
    return get_model() or None


# ══════════════════════════════════════════════════════════
#  CORE — Run CLI via ConPTY
# ══════════════════════════════════════════════════════════

def run_cli(
    prompt: str,
    conversation_id: str = None,
    timeout: int = None,
    progress_callback: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    skip_permissions: bool = False,
) -> tuple[str, str | None]:
    """
    Run the CLI with the user's message. Returns (response, conversation_id).
    The bot is a thin pipe — message goes directly to CLI, we just relay.
    """
    from config import get_timeout, AGI_BRAIN_DIR, HOME_DIR
    timeout = timeout or get_timeout()

    is_new = False
    if not conversation_id:
        is_new = True
        conversation_id = str(uuid.uuid4())
    else:
        transcript_path = os.path.join(
            BRAIN_DIR, conversation_id, ".system_generated", "logs", "transcript.jsonl"
        )
        if not os.path.exists(transcript_path):
            is_new = True

    # Prepend instructions for new conversations
    if is_new:
        log_dir = os.path.join(BRAIN_DIR, conversation_id, ".system_generated", "logs")
        os.makedirs(log_dir, exist_ok=True)
        transcript_path = os.path.join(log_dir, "transcript.jsonl")
        if not os.path.exists(transcript_path):
            open(transcript_path, "a", encoding="utf-8").close()

        conv_dir = os.path.join(BRAIN_DIR, conversation_id, "out")
        os.makedirs(conv_dir, exist_ok=True)

        instructions = get_instructions()
        if instructions:
            instructions = instructions.replace("{CONV_DIR}", conv_dir)
            instructions = instructions.replace("{AGI_BRAIN_DIR}", AGI_BRAIN_DIR)
            instructions = instructions.replace("{HOME_DIR}", HOME_DIR)
            instructions = instructions.replace("{SKILLS_DIR}", SKILLS_DIR)
            prompt = (
                f"{instructions}\n"
                f"USER MESSAGE (answer THIS — everything above is just formatting context):\n"
                f"{prompt}"
            )

    # Build command
    cmd_parts = [CLI_PATH, "--conversation", conversation_id]
    if skip_permissions:
        cmd_parts.append("--dangerously-skip-permissions")
    timeout_minutes = max(1, timeout // 60)
    cmd_parts.extend(["--print-timeout", f"{timeout_minutes}m", "--print", prompt])

    command = subprocess.list2cmdline(cmd_parts)
    logger.info(f"[ENGINE] {'New' if is_new else 'Continuing'} conversation {conversation_id[:12]}...")

    starting_step = get_latest_step(conversation_id)
    poller = TranscriptPoller(conversation_id, starting_step, progress_callback)

    try:
        # Set up environment with model
        custom_env = os.environ.copy()
        model_id = get_selected_model()
        if model_id:
            custom_env["ANTIGRAVITY_MODEL"] = model_id
            custom_env["GEMINI_API_MODEL"] = model_id
            custom_env["MODEL"] = model_id
            logger.info(f"[ENGINE] Using model: {model_id}")

        env_str = '\0'.join(f'{k}={v}' for k, v in custom_env.items()) + '\0\0'

        pty = winpty.PTY(200, 1000, backend=CONPTY_BACKEND, agent_config=COLOR_ESCAPES)
        pty.spawn(command, cwd=CLI_WORKING_DIR, env=env_str)

        poller.start()

        output_chunks = []
        start_time = time.time()

        while True:
            if cancel_event and cancel_event.is_set():
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pty.pid)], capture_output=True)
                except Exception:
                    pass
                break

            elapsed = time.time() - start_time

            if elapsed > timeout + 30:
                logger.warning(f"[ENGINE] Hard timeout after {elapsed:.0f}s")
                try:
                    remaining = pty.read()
                    if remaining:
                        output_chunks.append(remaining)
                except Exception:
                    pass
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pty.pid)], capture_output=True)
                except Exception:
                    pass
                break

            if not pty.isalive():
                try:
                    remaining = pty.read()
                    if remaining:
                        output_chunks.append(remaining)
                except Exception:
                    pass
                logger.info(f"[ENGINE] Process exited after {elapsed:.1f}s")
                break

            try:
                data = pty.read(blocking=False)
                if data:
                    output_chunks.append(data)
            except Exception:
                pass

            time.sleep(0.15)

        poller.stop()

        raw_output = "".join(output_chunks)
        if len(raw_output) > 5000:
            raw_output = raw_output[:5000]

        response = clean_response(raw_output)

        # Get REAL response from transcript (reliable path)
        if conversation_id:
            real_response = get_new_responses(conversation_id, starting_step)
            tool_file_paths = _extract_file_paths(conversation_id, starting_step)

            if real_response or tool_file_paths:
                if tool_file_paths:
                    path_lines = "\n".join(
                        f"\nFile saved to {fp}" for fp in tool_file_paths
                        if fp not in (real_response or "")
                    )
                    if path_lines:
                        real_response = (real_response + "\n" + path_lines).strip()
                response = real_response
            elif not is_new:
                logger.warning(f"[ENGINE] Continuing conv but transcript empty. Refusing raw fallback.")
                response = "⚠️ Response could not be extracted. Please try again."

        if not response:
            response = "The CLI returned an empty response. Try rephrasing your question."

        response = sanitize_response(response)
        logger.info(f"[ENGINE] Response: {len(response)} chars")
        return response, conversation_id

    except Exception as e:
        poller.stop()
        logger.error(f"[ENGINE] Error: {e}", exc_info=True)
        return f"Error running CLI: {str(e)}", conversation_id


async def run_cli_async(
    prompt: str,
    conversation_id: str = None,
    timeout: int = None,
    progress_callback: Callable[[str], None] | None = None,
    skip_permissions: bool = False,
) -> tuple[str, str | None]:
    """Async wrapper — runs blocking PTY call in thread pool."""
    loop = asyncio.get_event_loop()
    cancel_event = threading.Event()
    task = loop.run_in_executor(
        executor, run_cli, prompt, conversation_id, timeout,
        progress_callback, cancel_event, skip_permissions,
    )
    try:
        return await task
    except asyncio.CancelledError:
        cancel_event.set()
        raise
