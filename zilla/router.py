# ============================================================
#  ROUTER — deterministic triage + effort controller (PLAN.md §10 R1)
# ============================================================
#  Runs BEFORE the engine spends a lock, and decides two things about a
#  turn — never with a model call, always with the same answer for the
#  same input:
#
#    1. class   — 'command' (leading /), 'share' (zero-model journal
#                 append), 'clock' (zero-model time/date answer), 'trivial'
#                 (greeting/thanks/ack), 'normal'.
#                 The class patterns themselves live in review.py; this
#                 module is the seam that names them and adds 'command'.
#
#    2. effort  — 'fast' | 'standard' | 'deep', resolved by rules in
#                 priority order (PLAN.md §10 R1.3):
#                   (a) OWNER EMPHASIS WINS ABSOLUTELY — "think hard",
#                       "take your time", a leading `!deep`;
#                   (b) a 'trivial' message ⇒ fast;
#                   (c) everything else ⇒ standard.
#                 The MODEL NEVER DECIDES ITS OWN EFFORT (P3/P5): a model
#                 grading its own homework under-thinks the hard ones.
#
#  Effort becomes a backend+model choice through the `effort_map` setting.
#  Two hard rules hold there, both recorded reality rather than taste:
#
#    - agy's active model is a GLOBAL display string in agy's own settings
#      file, shared with every other agy terminal on the machine. Zilla
#      will not mutate it mid-turn, so EFFORT NEVER NAMES agy: an
#      effort_map entry pointing at agy is rejected at settings-write
#      time (validate_effort_map), not quietly ignored later.
#      Effort routing can change WHICH backend runs a turn; on agy it
#      changes nothing about agy's model.
#
#    - a target whose binary isn't on this machine is not a target. It
#      resolves to None and the turn runs on the session's own backend,
#      exactly as if effort had never been asked for. (Login freshness is
#      R2's chain problem, not this module's.)
#
#  Pure except for reading settings and checking two paths: no network, no
#  model, no writes.
# ============================================================

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from zilla.review import classify_route

# ── classes ──────────────────────────────────────────────────
COMMAND = "command"
SHARE = "share"
CLOCK = "clock"
TRIVIAL = "trivial"
NORMAL = "normal"

# ── efforts ──────────────────────────────────────────────────
FAST = "fast"
STANDARD = "standard"
DEEP = "deep"
EFFORTS = (FAST, STANDARD, DEEP)

# Backends whose model can be chosen per invocation (`--model`). agy is
# deliberately absent — see the header.
MODEL_FLAG_BACKENDS = ("claude", "opencode")

# An effort deliberately pinned to "no target" by the owner — the turn runs
# on the session's own backend. Distinct from an absent key, which means
# "use whatever this machine's default is".
OFF = "off"

# The one visible sign of a deep turn (P4: say what is happening).
DEEP_NOTE = "🧠 Thinking deeply — using the strongest model for this one."

# Owner emphasis. Deliberately a small, explicit set: these are things a
# person says when they mean it, not words that show up mid-sentence by
# accident ("I carefully read it" doesn't ask Zilla for anything).
_EMPHASIS_RE = re.compile(
    r"\b("
    r"think (?:really |very |super )?(?:hard|deeply|carefully|properly)"
    r"|think this through"
    r"|take your time"
    r"|be thorough|do it properly|do this properly"
    r"|deep dive"
    r")\b",
    re.IGNORECASE,
)

# An explicit prefix, stripped before the message reaches the model.
_DEEP_PREFIX_RE = re.compile(r"^\s*!deep\b[:,\-]?\s*", re.IGNORECASE)


@dataclass
class Decision:
    """What the router decided about one incoming message.

    text          — the message with any `!deep` prefix removed (what the
                    model should actually see).
    klass         — command | share | trivial | normal.
    effort        — fast | standard | deep.
    backend/model — the per-turn override, or None meaning "the session's
                    backend, configured as it is". `model` is only ever
                    set for a MODEL_FLAG_BACKENDS backend.
    fast_profile  — lean injection (core MEMORY.md, no wiki index) and a
                    throwaway conversation that must NOT advance the
                    session's conv id (I-CONV).
    reason        — short tag for the log: 'emphasis', 'prefix', 'trivial',
                    'default'.
    ms            — milliseconds spent inside decide() itself (R4a): the
                    proof, forever, that classification costs ~nothing.
    """
    text: str
    klass: str = NORMAL
    effort: str = STANDARD
    backend: str | None = None
    model: str | None = None
    fast_profile: bool = False
    reason: str = "default"
    ms: int | None = None

    def demoted(self) -> "Decision":
        """The same turn, run the ordinary way — what a misclassified fast
        turn becomes on its silent rerun."""
        return Decision(text=self.text, klass=NORMAL, effort=STANDARD,
                        backend=None, model=None, fast_profile=False,
                        reason="rerun")

    def as_log(self) -> dict:
        return {"class": self.klass, "effort": self.effort,
                "why": self.reason, "target": self.backend or "session",
                "model": self.model, "ms": self.ms}


# ══════════════════════════════════════════════════════════
#  CLASS
# ══════════════════════════════════════════════════════════

def classify(text: str) -> str:
    """'command' | 'share' | 'clock' | 'trivial' | 'normal'. 'normal' is the
    safe default for everything that doesn't cleanly match."""
    t = (text or "").strip()
    if t.startswith("/"):
        return COMMAND
    route = classify_route(t)
    if route == "share":
        return SHARE
    if route == "clock":
        return CLOCK
    if route == "smalltalk":
        return TRIVIAL
    return NORMAL


# ══════════════════════════════════════════════════════════
#  EFFORT
# ══════════════════════════════════════════════════════════

def resolve_effort(text: str, klass: str | None = None) -> tuple[str, str, str]:
    """Returns (effort, cleaned_text, reason). Priority is fixed and the
    owner is at the top of it: an explicit ask for depth beats the
    classifier, even on a one-word message."""
    t = text or ""
    m = _DEEP_PREFIX_RE.match(t)
    if m:
        return DEEP, t[m.end():].strip() or t.strip(), "prefix"
    if _EMPHASIS_RE.search(t):
        return DEEP, t, "emphasis"
    if klass == TRIVIAL:
        return FAST, t, "trivial"
    return STANDARD, t, "default"


# ══════════════════════════════════════════════════════════
#  EFFORT MAP  (setting: effort_map)
# ══════════════════════════════════════════════════════════

def _installed(backend: str) -> bool:
    from zilla import config
    path = {"claude": config.CLAUDE_PATH,
            "opencode": config.OPENCODE_PATH}.get(backend)
    return bool(path and os.path.exists(path))


def default_effort_map() -> dict:
    """Cheapest and strongest among the per-invocation-flag backends that
    are actually on this machine, following the chain's own priority
    (PLAN.md §10 R2.1): opencode's free namespace is the cheapest thing
    here, so it takes `fast` when present; claude's opus is the strongest,
    so it takes `deep`. A missing entry means "no target" — the turn runs
    on the session's backend, which is always a valid answer."""
    from zilla import config
    out: dict[str, dict] = {}
    if _installed("opencode"):
        out[FAST] = {"backend": "opencode", "model": config.get_model_for("opencode")}
    elif _installed("claude"):
        out[FAST] = {"backend": "claude", "model": "haiku"}
    if _installed("claude"):
        out[DEEP] = {"backend": "claude", "model": "opus"}
    return out


def validate_effort_map(raw) -> dict:
    """Normalize and check an effort_map before it is stored. Accepts
    {"fast": "claude:haiku"} or {"fast": {"backend": ..., "model": ...}}.
    Raises ValueError with a plain-language message — the caller shows it
    to the owner. Rejecting agy here is the whole point: a bad entry must
    fail at write time, where someone is looking, not mid-turn."""
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("effort_map must be a mapping of effort → backend:model.")
    out: dict[str, dict] = {}
    for key, value in raw.items():
        effort = str(key).strip().lower()
        if effort not in EFFORTS:
            raise ValueError(f"'{key}' is not an effort — use fast, standard or deep.")
        if value in (None, "", {}):
            continue
        # An explicit "run it on the session's backend" — distinct from an
        # absent key, which just means "use the default for this machine".
        if isinstance(value, str) and value.strip().lower() in ("off", "none", "session"):
            out[effort] = OFF
            continue
        if isinstance(value, str):
            backend, _, model = value.partition(":")
        elif isinstance(value, dict):
            backend, model = value.get("backend", ""), value.get("model", "")
        else:
            raise ValueError(f"effort_map['{effort}'] must be 'backend:model'.")
        backend = (backend or "").strip().lower()
        model = (model or "").strip()
        if backend == "agy":
            raise ValueError(
                "effort_map can't name agy: agy's model is one global setting "
                "shared with every agy terminal on this machine, so Zilla won't "
                "switch it per turn. Use claude or opencode, or change agy's "
                "model in /settings."
            )
        if backend not in MODEL_FLAG_BACKENDS:
            raise ValueError(
                f"'{backend or value}' is not a backend that can pick a model per "
                f"turn — use {' or '.join(MODEL_FLAG_BACKENDS)}."
            )
        if not model:
            raise ValueError(f"effort_map['{effort}'] needs a model, e.g. '{backend}:haiku'.")
        out[effort] = {"backend": backend, "model": model}
    return out


def effort_map() -> dict:
    """The live map: this machine's defaults, with whatever the owner set on
    top of them per effort — so naming `deep` doesn't silently cost you the
    `fast` target. A stored map that has since gone invalid (a backend
    uninstalled, a hand-edited row) degrades to the defaults rather than
    breaking every turn."""
    from zilla import config
    try:
        stored = validate_effort_map(config.get_setting("effort_map", None))
    except ValueError:
        stored = {}
    merged = dict(default_effort_map())
    merged.update(stored)
    return merged


def target_for(effort: str) -> tuple[str | None, str | None]:
    """(backend, model) for this effort, or (None, None) meaning "run it on
    the session's own backend". 'standard' is always the session's backend
    — that is what standard means."""
    if effort not in (FAST, DEEP):
        return None, None
    entry = effort_map().get(effort)
    if not entry or entry == OFF:
        return None, None
    backend, model = entry.get("backend"), entry.get("model")
    if not backend or backend == "agy" or not _installed(backend):
        return None, None
    return backend, model


# ══════════════════════════════════════════════════════════
#  THE DECISION
# ══════════════════════════════════════════════════════════

def decide(text: str) -> Decision:
    """One call, everything the turn needs to know about how to run."""
    t0 = time.monotonic()
    klass = classify(text)
    effort, cleaned, reason = resolve_effort(text, klass)
    backend, model = target_for(effort)
    return Decision(
        text=cleaned, klass=klass, effort=effort,
        backend=backend, model=model,
        # The fast profile needs somewhere fast to go. With no eligible
        # target the chain is agy-only, and a fresh agy conversation costs
        # the global new-conv lock plus the whole onboarding preamble —
        # slower than just continuing the session. So: no target, no fast
        # profile, and the turn runs exactly as it always did.
        fast_profile=(effort == FAST and backend is not None),
        reason=reason,
        ms=int((time.monotonic() - t0) * 1000),
    )
