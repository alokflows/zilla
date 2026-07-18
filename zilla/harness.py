# ============================================================
#  HARNESS — Layer 1 General Harness (foundation)
# ============================================================
#  Zilla is a RELAY: the bot owns the I/O boundary, the CLI owns
#  reasoning + tool execution. The harness is everything the bot
#  can put AROUND a turn — the permanent cockpit every message
#  passes through.
#
#  This module is the PHASE-0 FOUNDATION: just the two primitives
#  every later phase is built on. It is INERT until cli_engine /
#  backends call it — importing it changes no behavior.
#
#    1. build_preamble()  — the operating context injected into the
#       CLI prompt EVERY turn, for BOTH backends, OS-aware. Split
#       into a full ONBOARDING block (new conversations) and a
#       compact OPERATING CONTRACT (continued turns) so the rules
#       stay in force on long conversations without re-bloating the
#       prompt on every message.
#
#    2. log_event()  — append-only structured event sink at
#       logs/trust_log.jsonl. Thread-safe, atomic line writes, and
#       it NEVER raises into the caller (observability must not be
#       able to break a turn).
#
#  What is deliberately NOT here yet (later phases):
#    - output verification / hallucination gate + auto-retry  (Phase 1)
#    - Kimi WebBridge health-check + auto-restart             (Phase 1)
#    - task classifier + AutoHarness execution plans          (Phase 2)
#  The seam for those is already shaped below (the operating
#  contract carries the SEED sourcing rule), so they slot in
#  without churn.
# ============================================================

from __future__ import annotations

import os
import json
import time
import logging
import threading
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Phase 1 move: this module now lives in zilla/, one level below repo root —
# go up one more level so logs/ and bot_instructions.md still resolve there.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ══════════════════════════════════════════════════════════
#  TURN CONTEXT  (PLAN.md §5.M2 step 2)
# ══════════════════════════════════════════════════════════
#  Who this turn is for and why it's running — threaded explicitly through
#  every layer of the turn pipeline (run_cli_async -> _run_blocking ->
#  _dispatch_turn -> run_cli/run_claude -> wrap_prompt/build_preamble).
#  Deliberately NEVER a module-level "current turn" global: with
#  concurrent_updates(True) and a shared thread-pool executor, ambient
#  state would race across turns and could leak the owner's memory into
#  another user's prompt. Pass it as a parameter, every call site, always.

@dataclass(frozen=True)
class TurnContext:
    """uid      — the turn's principal.
    role     — 'owner' | 'admin' | 'limited' (AuthManager.role_of).
    is_owner — convenience/cache of (role == 'owner'). This is what gates
               memory injection (§4 scope guard): memory is the OWNER's,
               never any other principal's — a non-owner turn must contain
               zero MEMORY.md / wiki index / journal-protocol content.
    origin   — why this turn is running: 'user' (live chat), 'schedule',
               'heartbeat' (Phase H, not wired yet), or 'approval' (an
               Approval-mode request the owner just approved).
    """
    uid: int
    role: str
    is_owner: bool
    origin: str = "user"


# ══════════════════════════════════════════════════════════
#  STRUCTURED EVENT LOG  (logs/trust_log.jsonl)
# ══════════════════════════════════════════════════════════
#  One JSON object per line. This is the spine the trust log,
#  self-healing, and the scheduler watchdog will all write to.
#  Append-only, lock-serialized, fsync-free (durability is not
#  worth a per-event fsync stall on the hot path), and it
#  swallows every error: logging must never break a turn.

_LOG_DIR = os.path.join(_HERE, "logs")
_TRUST_LOG = os.path.join(_LOG_DIR, "trust_log.jsonl")
_log_lock = threading.Lock()


def log_event(event: str, **fields) -> None:
    """Append one structured event to logs/trust_log.jsonl. Never raises."""
    try:
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "epoch": round(time.time(), 3),
            "event": event,
        }
        # Caller fields win, but can't clobber the three reserved keys above.
        for k, v in fields.items():
            if k not in record:
                record[k] = v
        line = json.dumps(record, ensure_ascii=False)
        with _log_lock:
            os.makedirs(_LOG_DIR, exist_ok=True)
            with open(_TRUST_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:  # observability must not be able to break a turn
        logger.debug(f"[HARNESS] log_event failed: {e}")


def log_summary(limit: int = 5000) -> dict:
    """Tally recent events from trust_log.jsonl → {event_name: count}. Cheap,
    bounded read; never raises. Powers the /menu → 🩺 Health view."""
    counts: dict[str, int] = {}
    try:
        if not os.path.exists(_TRUST_LOG):
            return counts
        with open(_TRUST_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        for line in lines:
            try:
                ev = json.loads(line).get("event")
                if ev:
                    counts[ev] = counts.get(ev, 0) + 1
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[HARNESS] log_summary failed: {e}")
    return counts


# ══════════════════════════════════════════════════════════
#  SKILLS SUMMARY
# ══════════════════════════════════════════════════════════
#  Reads {SKILLS_DIR}/<name>/SKILL.md frontmatter and returns a
#  short bullet list the model can use to decide what to load.
#  (The previous _get_skills_summary in cli_engine was never
#  called — skills never actually reached the model. This is the
#  single source of truth going forward.)

# Cache the skills scan (per backend dir) so new conversations don't re-walk the
# skills folder every time — keeps prompt assembly effectively free.
_skills_cache: dict = {"key": None, "val": "", "ts": 0.0}
_SKILLS_TTL = 60.0


def skills_summary(backend: str | None = None) -> str:
    """Bullet list of the ACTIVE backend's skills. agy and claude keep skills in
    different dirs, so this changes when the backend ('mode') changes. Cached."""
    from zilla.config import get_skills_dir
    skills_dir = get_skills_dir(backend)
    now = time.time()
    if _skills_cache["key"] == skills_dir and now - _skills_cache["ts"] < _SKILLS_TTL:
        return _skills_cache["val"]
    lines = []
    try:
        if os.path.isdir(skills_dir):
            for name in sorted(os.listdir(skills_dir)):
                skill_md = os.path.join(skills_dir, name, "SKILL.md")
                if os.path.isfile(skill_md):
                    desc = _parse_skill_description(skill_md)
                    lines.append(f"- **{name}**: {desc}")
    except Exception as e:
        logger.debug(f"[HARNESS] skills_summary failed: {e}")
    result = "\n".join(lines)
    _skills_cache.update(key=skills_dir, val=result, ts=now)
    return result


def _parse_skill_description(skill_md: str) -> str:
    """Pull the `description:` from a SKILL.md YAML frontmatter block.
    Handles both inline (`description: foo`) and folded/multiline values."""
    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read(2000)
    except Exception:
        return "(no description)"
    if not content.startswith("---"):
        return "(no description)"
    end = content.find("---", 3)
    if end < 0:
        return "(no description)"
    frontmatter = content[3:end]
    idx = frontmatter.find("description:")
    if idx < 0:
        return "(no description)"
    # Inline value on the same line?
    first_line = frontmatter[idx:].split("\n", 1)[0]
    inline = first_line.split(":", 1)[1].strip().strip('"').strip("'")
    if inline and not inline.startswith("|") and not inline.startswith(">"):
        return inline[:100]
    # Folded/literal block: gather subsequent indented lines.
    desc_lines = []
    for dl in frontmatter[idx:].split("\n")[1:]:
        if dl.startswith(("  ", "\t")):
            desc_lines.append(dl.strip())
        else:
            break
    return " ".join(desc_lines)[:100] if desc_lines else "(no description)"


# ══════════════════════════════════════════════════════════
#  OPERATING CONTRACT  (the always-on rules)
# ══════════════════════════════════════════════════════════
#  The minimum set of rules that must govern EVERY turn — short
#  enough to re-send on every continued message without bloat.
#  The TRUST block here is the seed of the anti-hallucination
#  system: Phase 1's verification gate enforces in Python what
#  this asks the model to do in prose.

_TRUST_CONTRACT = """\
TRUST CONTRACT (always in force):
• Never fabricate, estimate, or guess facts, numbers, dates, quotes, or URLs.
• Every factual or numeric claim must trace to a tool result or the user — not to memory.
• If something can't be verified, say so plainly. Do NOT fill the gap with a plausible value.
• If a tool or source fails, report that it failed and what you tried — never substitute invented data.
• FAILED is a signal to try another way, not a final answer."""

_STYLE_CONTRACT = """\
STYLE: Answer directly and concisely for mobile (Telegram). Use • bullets and **bold**.
To deliver a file, output its ABSOLUTE path in your reply — the bot auto-sends it."""

# In-loop self-heal directive (Layer A — docs/dev/RESEARCH_ORCHESTRATION_REVIEW.md
# §4.1). No new code path: this is prose injected into every turn so the CLI's
# own tool loop recovers from missing dependencies/setup on its own, the way
# OpenClaw/Hermes do it — Zilla's Layer B (zilla/review.py) is the deterministic
# backstop, not a substitute for this.
_SELF_HEAL = """\
SELF-HEAL: If a tool fails because something is missing or not set up — a \
missing package, an uninitialized directory, a missing config file, a dependency \
that isn't installed — install the dependency or create what's missing yourself, \
then retry the original step. Fix silently; report only the outcome, not the \
repair. Only if the fix itself fails, or the fix would be destructive, \
irreversible, or cost money, stop and tell the user in plain language exactly \
what is missing and what you tried. \
Before changing config or scheduling behavior, inspect existing state \
first (what's already there, why it might be that way) rather than assuming. \
Prefer existing tools/libraries already in this project over building custom \
ones from scratch."""

# Backend display names (the "mode" the model is running as).
_BACKEND_LABEL = {
    "claude": "Claude Code (Anthropic)",
    "agy": "Antigravity CLI (Gemini)",
}


def _platform_name() -> str:
    import platform
    sysname = platform.system()
    return {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(sysname, sysname or "unknown")


def engine_context(backend: str | None = None) -> str:
    """One-line header so the model always knows which engine + OS it is. Sent
    every turn — this is what makes 'switch to Claude mode' real to the model."""
    from zilla.config import get_backend, get_model
    b = (backend or get_backend()).strip().lower()
    label = _BACKEND_LABEL.get(b, b)
    return f"ENGINE: {label} · MODEL: {get_model()} · OS: {_platform_name()}"


def operating_contract(backend: str | None = None) -> str:
    """The compact, always-on rule block (sent every turn, both backends)."""
    return f"{engine_context(backend)}\n\n{_TRUST_CONTRACT}\n\n{_STYLE_CONTRACT}\n\n{_SELF_HEAL}"


# ══════════════════════════════════════════════════════════
#  ONBOARDING INSTRUCTIONS  (bot_instructions.md, resolved)
# ══════════════════════════════════════════════════════════

_instructions_cache: str | None = None
_INSTRUCTIONS_FILE = os.path.join(_HERE, "bot_instructions.md")


def _raw_instructions() -> str:
    """bot_instructions.md, cached. '' if absent/unreadable."""
    global _instructions_cache
    if _instructions_cache is not None:
        return _instructions_cache
    try:
        with open(_INSTRUCTIONS_FILE, "r", encoding="utf-8") as f:
            _instructions_cache = f.read().strip()
    except Exception:
        _instructions_cache = ""
    return _instructions_cache


def reload_instructions() -> None:
    """Drop the cache so the next build re-reads bot_instructions.md from disk."""
    global _instructions_cache
    _instructions_cache = None


def _resolve_placeholders(text: str, conv_dir: str, backend: str | None = None) -> str:
    """Replace {CONV_DIR}/{AGI_BRAIN_DIR}/{HOME_DIR}/{SKILLS_DIR}/{PLATFORM} with
    real, OS-native, backend-correct values. Centralized so injection is
    identical everywhere."""
    from zilla.config import AGI_BRAIN_DIR, HOME_DIR, get_skills_dir
    return (
        text.replace("{CONV_DIR}", conv_dir)
            .replace("{AGI_BRAIN_DIR}", AGI_BRAIN_DIR)
            .replace("{HOME_DIR}", HOME_DIR)
            .replace("{SKILLS_DIR}", get_skills_dir(backend))
            .replace("{PLATFORM}", _platform_name())
    )


# ══════════════════════════════════════════════════════════
#  MEMORY INJECTION  (PLAN.md §4/§5.M2 step 3 — owner-only)
# ══════════════════════════════════════════════════════════

_MEMORY_SOFT_CAP = 2400   # warn in log past this — MEMORY.md should stay lean
_MEMORY_HARD_CAP = 4000   # truncate the whole injected block past this
_MEMORY_INDEX_MAX_LINES = 100


def _memory_block(ctx: "TurnContext | None") -> str:
    """The 'Your memory' block appended to every OWNER turn, built fresh
    each time (a few file reads + one index scan — cheap). '' for any
    non-owner turn or when ctx is None: memory is the owner's, and this is
    the single gate that keeps it out of every other principal's prompt."""
    if ctx is None or not ctx.is_owner:
        return ""

    from zilla import memory as _memory
    from zilla.config import MEMORY_DIR

    # Phase M3: keep the FTS5 search index current before the turn starts —
    # cheap when nothing changed (mtime+size stat per file), and this is the
    # one place every owner turn passes through.
    _memory.reindex()

    core_text = _memory.read_core()
    was_template = _memory.is_template(core_text)
    if len(core_text) > _MEMORY_SOFT_CAP:
        logger.warning(
            f"[HARNESS] MEMORY.md is {len(core_text)} chars "
            f"(soft cap {_MEMORY_SOFT_CAP}) — nudge the agent to trim it"
        )

    wiki_text = _memory.wiki_index_text(max_index_lines=_MEMORY_INDEX_MAX_LINES)

    recall_line = f"- To recall details: read/grep files under {MEMORY_DIR}"
    memsearch_path = os.path.join(_HERE, "memsearch.py")
    if os.path.exists(memsearch_path):
        recall_line += f', or run `python {memsearch_path} "query"` for ranked full-text results.'
    else:
        recall_line += "."

    parts = [
        "## Your memory (persistent, yours to maintain)",
        core_text.strip() or "(empty)",
        "",
        "## Wiki index (read pages with your file tools when you need details)",
        wiki_text or "(no wiki pages yet)",
        "",
        "## Memory protocol",
        recall_line,
        "- To remember something durable: edit MEMORY.md (keep it under 2000 "
        "characters — move detail to a Wiki page) or the right Wiki page.",
        "- When the owner shares anything about their life, plans, or "
        "preferences, append one line to today's Journal file: `- HH:MM — fact`.",
        "- When the owner asks you to keep an eye on / remind / follow up on "
        "something recurring, add it to HEARTBEAT.md.",
        "- Never store credentials, OTPs, or tokens in any memory file.",
    ]
    if was_template:
        parts.append(
            "\nMEMORY.md is still empty — briefly interview the owner "
            "(3-4 questions max: who they are, what they want help with, any "
            "standing preferences) and fill it in before moving on."
        )

    block = "\n".join(parts)
    if len(block) > _MEMORY_HARD_CAP:
        block = block[:_MEMORY_HARD_CAP] + "\n[truncated — trim me]"
    return block


# ══════════════════════════════════════════════════════════
#  PREAMBLE ASSEMBLY  (the public entry point)
# ══════════════════════════════════════════════════════════

def build_preamble(*, is_new: bool, backend: str | None = None,
                   conv_dir: str | None = None,
                   ctx: "TurnContext | None" = None) -> str:
    """
    The operating context to prepend to the CLI prompt for THIS turn.

      is_new=True  → full onboarding: engine/OS header + bot_instructions.md
                     + the ACTIVE backend's skills list + operating contract.
      is_new=False → just the compact operating contract (engine header + trust
                     + style), so the rules stay in force on every continued
                     turn without re-sending the whole onboarding block.

    Backend-aware: agy and claude get the same trust rules but their own
    engine label, skills, and resolved paths — this is what makes a backend
    switch a real "mode" change to the model. Returns "" only if there is
    genuinely nothing to inject.
    """
    from zilla.config import AGI_BRAIN_DIR

    if conv_dir is None:
        conv_dir = os.path.join(AGI_BRAIN_DIR, "Outbox")

    relay = _relay_protocol(os.path.join(AGI_BRAIN_DIR, "Bridge"))
    memory_block = _memory_block(ctx)

    if not is_new:
        parts = [operating_contract(backend), relay]
        if memory_block:
            parts.append(memory_block)
        return "\n\n".join(parts)

    parts: list[str] = [engine_context(backend)]
    instructions = _raw_instructions()
    if instructions:
        parts.append(_resolve_placeholders(instructions, conv_dir, backend))

    skills = skills_summary(backend)
    if skills:
        parts.append("AVAILABLE SKILLS (load only when the task needs one):\n" + skills)

    parts.append(f"{_TRUST_CONTRACT}\n\n{_STYLE_CONTRACT}\n\n{_SELF_HEAL}")
    parts.append(relay)
    if memory_block:
        parts.append(memory_block)
    return "\n\n".join(parts)


def _relay_protocol(bridge_dir: str) -> str:
    """Instruction block teaching the agent the human-in-the-loop file bridge.
    This is what lets an autonomous login/checkout pause for an OTP, phone
    number, password, or final confirmation and resume with the owner's reply
    delivered through Telegram. Kept short so it costs little per turn."""
    return (
        "HUMAN-IN-THE-LOOP (credentials / OTP / confirmations):\n"
        "When you need something only the human can provide — a phone number, an "
        "OTP/2FA code, a password, or a final yes/no before an irreversible action "
        "(placing an order, spending money, deleting data) — DO NOT guess, invent, "
        "or give up. Ask the human through this file bridge and wait:\n"
        "  1. Pick a random 16-hex id, e.g. via `openssl rand -hex 8`.\n"
        f"  2. Write {bridge_dir}/ask_<id>.json containing exactly:\n"
        '     {"id":"<id>","kind":"otp|text|password|confirm",'
        '"prompt":"<your question>","chat_id":0,"created":<unix_seconds>}\n'
        f"  3. Poll for {bridge_dir}/answer_<id>.json every ~3s (up to ~10 min). "
        'When it appears, read its JSON "value" field — that is the human\'s reply.\n'
        "  4. Delete both files, then continue using the value.\n"
        "Use kind=otp for one-time codes, password for secrets (both are masked in "
        "chat), text for things like a phone number or address, confirm for yes/no. "
        "The bot relays your prompt to the owner's Telegram and writes their answer."
    )


def wrap_prompt(user_message: str, *, is_new: bool, backend: str | None = None,
                conv_dir: str | None = None,
                ctx: "TurnContext | None" = None) -> str:
    """
    Convenience: return the full prompt string (preamble + user message) with a
    clear boundary, so the model never confuses framing for the request. If
    there's no preamble, returns the user_message unchanged.
    """
    preamble = build_preamble(is_new=is_new, backend=backend, conv_dir=conv_dir, ctx=ctx)
    # Layer-2 AutoHarness: add the senior-engineer execution directive only when
    # the task is complex (simple tasks stay lean = fast).
    from zilla.autoharness import plan_directive
    directive = plan_directive(user_message)
    blocks = [b for b in (preamble, directive) if b]
    if not blocks:
        return user_message
    return (
        "\n\n".join(blocks)
        + "\n\nUSER MESSAGE (answer THIS — everything above is operating context):\n"
        + user_message
    )
