# ============================================================
#  CLI ENGINE — Thin Wrapper Around the AI CLI
# ============================================================
#  Runs the CLI via Windows ConPTY, delivers whatever it
#  produces. No wall-clock timeout — we wait for the CLI.
#  Only killed by: idle silence, explicit cancel, or the
#  MAX_TOTAL_RUNTIME catastrophic ceiling.
# ============================================================

import winhide  # noqa: F401 — MUST be first: suppresses all child console windows
import asyncio
import json
import os
import re
import time
import logging
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import winpty

from config import (
    CLI_PATH, CLI_WORKING_DIR, BRAIN_DIR, SKILLS_DIR,
    IDLE_KILL_AFTER, MAX_TOTAL_RUNTIME,
    get_idle_kill_after,
)

logger = logging.getLogger(__name__)

executor = ThreadPoolExecutor(max_workers=4)
_pool_semaphore = threading.Semaphore(4)  # mirrors max_workers for queue-depth tracking

# New-conversation creation is detected by snapshot-diffing BRAIN_DIR (shared by
# ALL users). If two users start a fresh session at once, each diff could pick up
# the OTHER user's brand-new dir and bind the wrong conversation. The per-user
# lock in bot.py can't help (it's cross-user), so serialize just the short
# creation+detection window globally: only one new conversation is ever "in
# flight", so each diff sees exactly its own new dir.
_new_conv_lock = threading.Lock()
_NEW_CONV_DETECT_TIMEOUT = 30.0  # release anyway if a new dir never appears

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
_CLI_WARN_RE = re.compile(
    r'^Warning: conversation "[^"]*" not found\.\s*\n?',
    re.MULTILINE,
)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def clean_response(text: str) -> str:
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
    text = _CLI_WARN_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ══════════════════════════════════════════════════════════
#  BRAIN / TRANSCRIPT TRACKING
# ══════════════════════════════════════════════════════════

def get_latest_step(conversation_id: str) -> int:
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
    if not conversation_id:
        return ""
    path = os.path.join(
        BRAIN_DIR, conversation_id, ".system_generated", "logs", "transcript.jsonl"
    )
    if not os.path.exists(path):
        return ""
    # Parse every transcript entry at or after this turn's floor. We track two
    # things: the planner responses (candidate answers) and the boundary — the
    # step of the LAST USER_INPUT, which is the exact point where the CURRENT
    # message begins. Everything before that boundary is "the previous thing"
    # and must never be returned.
    #
    # Two independent guards keep turns separate:
    #   1. starting_step — captured before the CLI ran (= prev turn's last step
    #      + 1). Per-user serialization in bot.py guarantees nothing else writes
    #      this conversation meanwhile, so this floor is exact.
    #   2. the USER_INPUT boundary — the structural marker the CLI itself writes
    #      for the current request. Even if (1) were ever off, the answer is
    #      always the planner response that follows the user's own message.
    planner = []        # (step, content) candidate answers, floored by starting_step
    boundary = -1       # step of the most recent USER_INPUT at/after the floor
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    step = data.get("step_index", -1)
                    if step < starting_step:
                        continue
                    dtype = data.get("type")
                    if dtype == "USER_INPUT":
                        boundary = max(boundary, step)
                    elif dtype == "PLANNER_RESPONSE" and data.get("content"):
                        content = data["content"].strip()
                        if len(content) > 5:
                            planner.append((step, content))
                except Exception:
                    pass
    except Exception:
        pass
    if not planner:
        return ""
    # Keep only answers AFTER the current message's USER_INPUT. If no boundary
    # was found in range (older CLI layout / no re-logged input), the
    # starting_step floor already scopes us to this turn, so fall back to all.
    after_boundary = [c for (step, c) in planner if step > boundary]
    candidates = after_boundary if after_boundary else [c for _, c in planner]
    # The agent's FINAL planner response is the answer. Earlier ones in the turn
    # are intermediate reasoning between tool calls; returning the last avoids
    # dumping the whole train of thought.
    result = candidates[-1]
    if len(_DIR_LISTING_RE.findall(result)) > 3:
        result = sanitize_response(result)
    if len(result) > 10000:
        result = result[:10000] + "\n\n_(Response truncated)_"
    return result


def _extract_file_paths(conversation_id: str, starting_step: int) -> list[str]:
    if not conversation_id:
        return []
    path = os.path.join(
        BRAIN_DIR, conversation_id, ".system_generated", "logs", "transcript.jsonl"
    )
    if not os.path.exists(path):
        return []
    # Same boundary logic as get_new_responses: only deliver files produced
    # after the CURRENT message's USER_INPUT, so we never re-send a file from a
    # previous turn. First pass finds the boundary, second pass collects files.
    boundary = starting_step
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    step = data.get("step_index", -1)
                    if step >= starting_step and data.get("type") == "USER_INPUT":
                        boundary = max(boundary, step)
                except Exception:
                    pass
    except Exception:
        pass
    file_paths = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if data.get("step_index", -1) < boundary:
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
                                    candidate = os.path.join(
                                        BRAIN_DIR, conversation_id, f"{img_name}{ext}"
                                    )
                                    if os.path.isfile(candidate):
                                        file_paths.append(candidate)
                except Exception:
                    pass
    except Exception:
        pass
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
            return "🧠 Processing…"
        if data.get("type") == "PLANNER_RESPONSE":
            thinking = data.get("thinking", "")
            if thinking and len(thinking) > 10:
                short = thinking[:80].split(".")[0].strip()
                if short:
                    return f"🧠 {short}…"
        return None


# ══════════════════════════════════════════════════════════
#  INSTRUCTIONS
# ══════════════════════════════════════════════════════════

_INSTRUCTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_instructions.md")
_cached_instructions: str | None = None


def get_instructions() -> str | None:
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
    return _cached_instructions


def reload_instructions():
    global _cached_instructions
    _cached_instructions = None
    return get_instructions()


def _get_skills_summary() -> str:
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
    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read(2000)
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                frontmatter = content[3:end]
                for line in frontmatter.split("\n"):
                    if line.strip().startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                        if desc and not desc.startswith("|"):
                            return desc[:100]
                        idx = frontmatter.find("description:")
                        after = frontmatter[idx:]
                        desc_lines = []
                        for dl in after.split("\n")[1:]:
                            if dl.startswith("  ") or dl.startswith("\t"):
                                desc_lines.append(dl.strip())
                            else:
                                break
                        return " ".join(desc_lines)[:100] if desc_lines else "(no description)"
    except Exception:
        pass
    return "(no description)"


# ══════════════════════════════════════════════════════════
#  MODEL SELECTION
# ══════════════════════════════════════════════════════════

def get_selected_model() -> str | None:
    from config import get_model
    return get_model() or None


# ══════════════════════════════════════════════════════════
#  CONVERSATION DETECTION HELPERS
# ══════════════════════════════════════════════════════════

def _get_conv_dirs_snapshot() -> set:
    """Return names of all directories currently in BRAIN_DIR."""
    if not os.path.isdir(BRAIN_DIR):
        return set()
    result = set()
    try:
        for name in os.listdir(BRAIN_DIR):
            if os.path.isdir(os.path.join(BRAIN_DIR, name)):
                result.add(name)
    except Exception:
        pass
    return result


def _find_new_conv(snapshot_before: set) -> str | None:
    """Return the conversation ID the CLI just created (new dir not in snapshot_before)."""
    if not os.path.isdir(BRAIN_DIR):
        return None
    try:
        new_dirs = [
            name for name in os.listdir(BRAIN_DIR)
            if name not in snapshot_before
            and os.path.isdir(os.path.join(BRAIN_DIR, name))
        ]
        if not new_dirs:
            return None
        if len(new_dirs) == 1:
            return new_dirs[0]
        def _mtime(n):
            try:
                return os.path.getmtime(os.path.join(BRAIN_DIR, n))
            except Exception:
                return 0
        return max(new_dirs, key=_mtime)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════
#  CORE — Run CLI via ConPTY
# ══════════════════════════════════════════════════════════

def run_cli(
    prompt: str,
    conversation_id: str = None,
    progress_callback: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    skip_permissions: bool = False,
) -> tuple[str, str | None]:
    """
    Run the CLI. Returns (response, conversation_id).

    Never killed by wall-clock. Killed only by:
      - cancel_event set (user cancels)
      - idle silence > IDLE_KILL_AFTER seconds
      - total runtime > MAX_TOTAL_RUNTIME seconds (safety net)
      - natural process exit
    """
    from config import get_idle_kill_after, AGI_BRAIN_DIR, HOME_DIR

    idle_kill_after = get_idle_kill_after()
    max_total_runtime = MAX_TOTAL_RUNTIME  # from env, not overridable at runtime

    is_new = False
    snapshot_before: set = set()
    _holding_new_conv_lock = False

    def _release_new_conv_lock():
        nonlocal _holding_new_conv_lock
        if _holding_new_conv_lock:
            _holding_new_conv_lock = False
            try:
                _new_conv_lock.release()
            except Exception:
                pass

    if not conversation_id:
        is_new = True
    else:
        transcript_path = os.path.join(
            BRAIN_DIR, conversation_id, ".system_generated", "logs", "transcript.jsonl"
        )
        if not os.path.exists(transcript_path):
            # Stale conv_id (e.g. brain dir was cleared) — start fresh
            is_new = True
            conversation_id = None

    if is_new:
        # Hold the global lock across snapshot → spawn → detection so no other
        # user can create a competing dir while we're diffing. Bounded acquire:
        # if a previous holder is wedged we proceed unprotected rather than block
        # an executor worker forever (no worse than pre-fix behaviour).
        _holding_new_conv_lock = _new_conv_lock.acquire(timeout=_NEW_CONV_DETECT_TIMEOUT)
        snapshot_before = _get_conv_dirs_snapshot()
        instructions = get_instructions()
        if instructions:
            conv_dir = os.path.join(AGI_BRAIN_DIR, "Outbox")
            instructions = instructions.replace("{CONV_DIR}", conv_dir)
            instructions = instructions.replace("{AGI_BRAIN_DIR}", AGI_BRAIN_DIR)
            instructions = instructions.replace("{HOME_DIR}", HOME_DIR)
            instructions = instructions.replace("{SKILLS_DIR}", SKILLS_DIR)
            prompt = (
                f"{instructions}\n"
                f"USER MESSAGE (answer THIS — everything above is formatting context):\n"
                f"{prompt}"
            )

    # Build command — no --conversation for new sessions; let CLI create its own ID
    cmd_parts = [CLI_PATH]
    if conversation_id:
        cmd_parts.extend(["--conversation", conversation_id])
    if skip_permissions:
        cmd_parts.append("--dangerously-skip-permissions")
    # Use a generous print-timeout so the CLI itself never fires before our idle reaper
    print_timeout_min = max(1, (max_total_runtime or 3600) // 60)
    cmd_parts.extend(["--print-timeout", f"{print_timeout_min}m", "--print", prompt])
    command = subprocess.list2cmdline(cmd_parts)

    conv_label = conversation_id[:12] if conversation_id else "new"
    logger.info(
        f"[ENGINE] {'New' if is_new else 'Continuing'} conv {conv_label}... "
        f"idle_kill={idle_kill_after}s max={max_total_runtime}s"
    )

    starting_step = get_latest_step(conversation_id)
    last_transcript_step = starting_step
    poller = TranscriptPoller(conversation_id, starting_step, progress_callback)

    try:
        custom_env = os.environ.copy()
        # The model is NOT passed via env or flag — agy reads it from its own
        # settings.json (config.set_model writes it there). We only log what the
        # CLI will load, for traceability.
        logger.info(f"[ENGINE] Model (from agy settings): {get_selected_model()}")

        env_str = '\0'.join(f'{k}={v}' for k, v in custom_env.items()) + '\0\0'
        pty = winpty.PTY(200, 1000, backend=CONPTY_BACKEND, agent_config=COLOR_ESCAPES)
        pty.spawn(command, cwd=CLI_WORKING_DIR, env=env_str)

        poller.start()

        output_chunks = []
        start_time = time.time()
        last_activity_ts = start_time
        last_clean_output = ""
        detect_check_ts = 0.0
        exit_reason = "normal"

        while True:
            now = time.time()
            total_elapsed = now - start_time
            idle_elapsed = now - last_activity_ts

            # User cancel
            if cancel_event and cancel_event.is_set():
                exit_reason = "canceled"
                break

            # Hard ceiling (catastrophic safety net)
            if max_total_runtime > 0 and total_elapsed > max_total_runtime:
                exit_reason = "max_runtime"
                logger.warning(f"[ENGINE] Max runtime {max_total_runtime}s hit")
                break

            # Idle reaper — only fires on silence
            if idle_kill_after > 0 and idle_elapsed > idle_kill_after:
                exit_reason = "idle"
                logger.warning(f"[ENGINE] Idle reaper fired after {idle_elapsed:.0f}s silence")
                break

            # Natural exit
            if not pty.isalive():
                exit_reason = "normal"
                break

            # Read PTY output. Only *meaningful* output counts as activity:
            # a hung CLI that just animates a spinner/cursor emits bytes forever,
            # which would otherwise keep resetting the idle clock and never reap.
            try:
                data = pty.read(blocking=False)
                if data:
                    output_chunks.append(data)
                    clean = strip_ansi(data).strip()
                    if clean and clean != last_clean_output:
                        last_clean_output = clean
                        last_activity_ts = now
            except Exception:
                pass

            # For brand-new sessions the CLI creates its own conversation dir.
            # Detect it DURING the run (not just after) so we can track real
            # progress — and so a new session that stalls still gets reaped.
            if is_new and not conversation_id and now - detect_check_ts > 1.0:
                detect_check_ts = now
                detected = _find_new_conv(snapshot_before)
                if detected:
                    conversation_id = detected
                    starting_step = get_latest_step(conversation_id)
                    last_transcript_step = starting_step
                    logger.info(f"[ENGINE] Detected new conv mid-run: {conversation_id[:12]}...")
                    _release_new_conv_lock()  # our dir exists now — others may proceed
                elif _holding_new_conv_lock and total_elapsed > _NEW_CONV_DETECT_TIMEOUT:
                    # No dir after the timeout — stop blocking everyone else.
                    logger.warning("[ENGINE] New-conv not detected in time; releasing lock")
                    _release_new_conv_lock()

            # A new transcript step = the agent actually did something = real
            # progress. This is the authoritative activity signal.
            if conversation_id:
                current_step = get_latest_step(conversation_id)
                if current_step > last_transcript_step:
                    last_transcript_step = current_step
                    last_activity_ts = now

            time.sleep(0.15)

        # Drain remaining output
        try:
            remaining = pty.read()
            if remaining:
                output_chunks.append(remaining)
        except Exception:
            pass

        # Kill process if we stopped it (winhide makes this windowless)
        if exit_reason != "normal":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pty.pid)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                pass

        poller.stop()

        # For new sessions the CLI created its own conversation ID — discover it
        if is_new and not conversation_id:
            detected = _find_new_conv(snapshot_before)
            if detected:
                conversation_id = detected
                logger.info(f"[ENGINE] Detected new conv: {conversation_id[:12]}...")
        _release_new_conv_lock()  # detection window over (success or not)

        raw_output = "".join(output_chunks)
        # The --print answer is at the END of the stream; keep the tail (not the
        # head, which is just startup banners) for the PTY fallback below.
        if len(raw_output) > 5000:
            raw_output = raw_output[-5000:]
        response = clean_response(raw_output)

        # Always try transcript first (authoritative, clean)
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
            # If transcript is empty, fall through to PTY output — never discard

        if not response:
            response = "No response from CLI. Try rephrasing."

        response = sanitize_response(response)

        # Prefix for non-normal exits — user sees what happened
        total_elapsed_int = int(time.time() - start_time)
        m, s = divmod(total_elapsed_int, 60)
        elapsed_str = f"{m}m {s}s" if m else f"{s}s"

        if exit_reason == "canceled":
            header = f"🛑 Canceled after {elapsed_str}."
            response = f"{header}\n\n{response}" if response else header
        elif exit_reason == "idle":
            idle_m = idle_kill_after // 60
            header = f"⏱️ No activity for {idle_m}m — delivered what was ready."
            response = f"{header}\n\n{response}" if response else f"⏱️ No response after {idle_m}m of silence."
        elif exit_reason == "max_runtime":
            header = f"⚠️ Stopped after {elapsed_str} (safety ceiling)."
            response = f"{header}\n\n{response}" if response else header

        logger.info(f"[ENGINE] Done ({exit_reason}, {elapsed_str}): {len(response)} chars")
        return response, conversation_id

    except Exception as e:
        poller.stop()
        logger.error(f"[ENGINE] Error: {e}", exc_info=True)
        return f"Error running CLI: {str(e)}", conversation_id
    finally:
        # Belt-and-suspenders: never leak the global lock on any exit path.
        _release_new_conv_lock()


async def run_cli_async(
    prompt: str,
    conversation_id: str = None,
    progress_callback: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    skip_permissions: bool = False,
) -> tuple[str, str | None]:
    """Async wrapper — runs blocking PTY call in thread pool."""
    loop = asyncio.get_event_loop()
    if cancel_event is None:
        cancel_event = threading.Event()
    task = loop.run_in_executor(
        executor, run_cli, prompt, conversation_id,
        progress_callback, cancel_event, skip_permissions,
    )
    try:
        return await task
    except asyncio.CancelledError:
        cancel_event.set()
        raise
