# ============================================================
#  interactive.py — credential / OTP relay (human-in-the-loop)
# ============================================================
#  THE PROBLEM
#  An autonomous agent that logs into accounts or places orders will hit walls
#  only a human can pass: a phone number, an OTP/2FA code, a password, a final
#  "yes, place this $42 order" confirmation. The agent must be able to PAUSE,
#  ask the owner through Telegram, and resume with the answer.
#
#  THE MECHANISM (file bridge over the existing Brain dir)
#  The agentic CLI runs as a one-shot `--print` turn, so there is no live stdin.
#  Instead we use a tiny file protocol the agent is taught (see harness preamble):
#
#    1. Agent needs input  → writes  Bridge/ask_<id>.json
#    2. Agent then POLLS    for       Bridge/answer_<id>.json   (sleep + retry)
#    3. Bot watcher sees the ask → DMs the owner the prompt
#    4. Owner replies in Telegram → bot writes answer_<id>.json
#    5. Agent reads the answer and continues; bot cleans both files up
#
#  This module is the PURE, TESTABLE core: it only encodes/decodes the request
#  and answer files and validates them. All Telegram I/O lives in bot.py.
#
#  SECURITY: prompts are length-capped and control-char-stripped; the answer
#  value is never logged; OTP/password kinds are flagged so the bot can mask
#  them in chat history. ask ids are random tokens (no path components).
# ============================================================

from __future__ import annotations

import os
import re
import json
import time
import secrets
from dataclasses import dataclass, asdict

try:
    from zilla.config import BRIDGE_DIR
except Exception:  # keep the module importable in isolation / tests
    BRIDGE_DIR = os.path.join(os.path.expanduser("~"), "Zilla", "Runtime", "Bridge")

# Allowed request kinds. 'otp'/'password' are secret → bot masks the reply.
KINDS = ("otp", "text", "password", "confirm")
SECRET_KINDS = ("otp", "password")

_MAX_PROMPT = 500            # chars; longer prompts are truncated
_MAX_VALUE = 4000            # chars; longer answers are rejected
_ID_RE = re.compile(r"^[a-f0-9]{16}$")   # ask ids we will accept off disk
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_controls(s: str) -> str:
    return _CONTROL_RE.sub("", s or "")


@dataclass(frozen=True)
class Ask:
    """One pending request for human input."""
    id: str
    kind: str
    prompt: str
    chat_id: int
    created: float

    @property
    def is_secret(self) -> bool:
        return self.kind in SECRET_KINDS


def ensure_bridge_dir(bridge_dir: str = BRIDGE_DIR) -> str:
    os.makedirs(bridge_dir, exist_ok=True)
    try:
        os.chmod(bridge_dir, 0o700)   # may hold OTPs in transit
    except OSError:
        pass
    return bridge_dir


def make_ask(kind: str, prompt: str, chat_id: int) -> Ask:
    """Build a validated Ask. Raises ValueError on a bad kind."""
    k = (kind or "").strip().lower()
    if k not in KINDS:
        raise ValueError(f"unknown ask kind: {kind!r} (allowed: {KINDS})")
    p = _strip_controls((prompt or "").strip())[:_MAX_PROMPT] or "(input requested)"
    return Ask(id=secrets.token_hex(8), kind=k, prompt=p,
               chat_id=int(chat_id), created=time.time())


def _ask_path(bridge_dir: str, ask_id: str) -> str:
    return os.path.join(bridge_dir, f"ask_{ask_id}.json")


def _answer_path(bridge_dir: str, ask_id: str) -> str:
    return os.path.join(bridge_dir, f"answer_{ask_id}.json")


def write_ask(ask: Ask, bridge_dir: str = BRIDGE_DIR) -> str:
    """Persist an Ask atomically. Returns the file path."""
    ensure_bridge_dir(bridge_dir)
    path = _ask_path(bridge_dir, ask.id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(ask), f)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def read_pending_asks(bridge_dir: str = BRIDGE_DIR) -> list[Ask]:
    """All ask_*.json on disk that have no matching answer yet, oldest first."""
    if not os.path.isdir(bridge_dir):
        return []
    out: list[Ask] = []
    for name in os.listdir(bridge_dir):
        if not (name.startswith("ask_") and name.endswith(".json")):
            continue
        ask_id = name[len("ask_"):-len(".json")]
        if not _ID_RE.match(ask_id):
            continue
        if os.path.exists(_answer_path(bridge_dir, ask_id)):
            continue   # already answered
        try:
            with open(os.path.join(bridge_dir, name), encoding="utf-8") as f:
                d = json.load(f)
            if d.get("kind") in KINDS and "prompt" in d:
                out.append(Ask(id=ask_id, kind=d["kind"],
                               prompt=str(d.get("prompt", ""))[:_MAX_PROMPT],
                               chat_id=int(d.get("chat_id", 0)),
                               created=float(d.get("created", 0.0))))
        except (OSError, ValueError, KeyError):
            continue
    out.sort(key=lambda a: a.created)
    return out


def write_answer(ask_id: str, value: str, bridge_dir: str = BRIDGE_DIR) -> str:
    """Record the owner's reply for a pending ask. Raises on bad id / oversize."""
    if not _ID_RE.match(ask_id or ""):
        raise ValueError("invalid ask id")
    if value is not None and len(value) > _MAX_VALUE:
        raise ValueError("answer too large")
    ensure_bridge_dir(bridge_dir)
    path = _answer_path(bridge_dir, ask_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"id": ask_id, "value": value, "ts": time.time()}, f)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def read_answer(ask_id: str, bridge_dir: str = BRIDGE_DIR) -> str | None:
    """The recorded answer value, or None if not answered yet."""
    if not _ID_RE.match(ask_id or ""):
        return None
    path = _answer_path(bridge_dir, ask_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("value")
    except (OSError, ValueError):
        return None


def clear_ask(ask_id: str, bridge_dir: str = BRIDGE_DIR) -> None:
    """Remove both the ask and answer files for a completed exchange."""
    for p in (_ask_path(bridge_dir, ask_id), _answer_path(bridge_dir, ask_id)):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def expire_stale(max_age: float = 1800.0, bridge_dir: str = BRIDGE_DIR) -> int:
    """Delete ask/answer pairs older than max_age seconds. Returns count cleared."""
    if not os.path.isdir(bridge_dir):
        return 0
    now = time.time()
    cleared = 0
    for a in read_pending_asks(bridge_dir):
        if now - a.created > max_age:
            clear_ask(a.id, bridge_dir)
            cleared += 1
    return cleared
