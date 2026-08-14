"""
Phase K5 — TEAM RELAY (PLAN.md §6/K5).

The owner talks only to their own Zilla; Zilla reaches other people on the
owner's behalf ("tell Priya to send the report", "remind Rahul every Monday
at 9"). The model never sends anything itself — it PROPOSES a relay action
with a deterministic marker at the end of its reply:

    RELAY_SEND: <alias> :: <message>
    RELAY_SCHEDULE: <alias> :: <kind> :: <spec-json> :: <text>

`zilla/core.py` strips the marker off the owner-facing reply, resolves the
alias against the K1 graph, and renders a ✅/❌ confirm card. **No confirm ⇒
nothing sends, ever** (owner decision 2026-07-18: always-confirm, no
trusted-contact bypass) — a relay action is a real message to a real person
in the owner's name, so a misresolved alias or an injected instruction must
never fire unattended.

This module is the pure part: marker parsing, alias→person resolution, and
the human-readable lines. It does no I/O beyond reading the entity page it
was asked to resolve, and it never raises on malformed model output or
malformed owner-authored Markdown (P4 — silent-safe).
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# Same marker family as F4's OWNER_ALERT: — one line, start-anchored,
# MULTILINE so it can sit at the end of an otherwise normal reply.
_SEND_RE = re.compile(r"^RELAY_SEND:[ \t]*(.+)$", re.MULTILINE)
_SCHEDULE_RE = re.compile(r"^RELAY_SCHEDULE:[ \t]*(.+)$", re.MULTILINE)

# Belt-and-braces cap: one reply proposing a dozen relays is a bug or an
# injection attempt, not a real turn. Extra markers are stripped and dropped.
MAX_ACTIONS = 3

VALID_SCHEDULE_KINDS = ("once", "interval", "daily", "weekly")


# ══════════════════════════════════════════════════════════
#  MARKER PARSING
# ══════════════════════════════════════════════════════════

def _split_fields(payload: str, count: int) -> list[str] | None:
    """`a :: b :: c` -> [a, b, c], keeping every remaining `::` inside the
    LAST field (a relayed message may legitimately contain '::'). None if
    there aren't enough fields or one of them is empty."""
    parts = [p.strip() for p in payload.split("::", count - 1)]
    if len(parts) != count or any(not p for p in parts):
        return None
    return parts


def parse_markers(text: str) -> tuple[str, list[dict]]:
    """Split a model reply into (clean_text, actions).

    Every marker line is removed from the text whether or not it parsed —
    the owner must never see the raw protocol, and a half-written marker
    must not leak into the chat. Each action is:

        {"kind": "send", "alias": str, "message": str}
        {"kind": "schedule", "alias": str, "sched_kind": str,
         "spec": dict, "text": str}
        {"kind": ..., "error": "malformed"}   # unparseable payload

    Never raises: bad JSON, missing fields, an unknown schedule kind all
    come back as an `error` action for the caller to surface as one calm
    line (P4)."""
    if not text or ("RELAY_SEND:" not in text and "RELAY_SCHEDULE:" not in text):
        return text, []

    actions: list[dict] = []

    def _take_send(m: re.Match) -> str:
        fields = _split_fields(m.group(1), 2)
        if fields is None:
            actions.append({"kind": "send", "error": "malformed"})
        else:
            actions.append({"kind": "send", "alias": fields[0], "message": fields[1]})
        return ""

    def _take_schedule(m: re.Match) -> str:
        fields = _split_fields(m.group(1), 4)
        if fields is None:
            actions.append({"kind": "schedule", "error": "malformed"})
            return ""
        alias, sched_kind, spec_raw, body = fields
        sched_kind = sched_kind.lower()
        try:
            spec = json.loads(spec_raw)
        except (ValueError, TypeError):
            spec = None
        if sched_kind not in VALID_SCHEDULE_KINDS or not isinstance(spec, dict):
            actions.append({"kind": "schedule", "error": "malformed"})
            return ""
        actions.append({"kind": "schedule", "alias": alias, "sched_kind": sched_kind,
                        "spec": spec, "text": body})
        return ""

    clean = _SEND_RE.sub(_take_send, text)
    clean = _SCHEDULE_RE.sub(_take_schedule, clean)
    # Markers normally sit on their own trailing lines; collapse the blank
    # run they leave behind so the owner's reply doesn't end in whitespace.
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, actions[:MAX_ACTIONS]


# ══════════════════════════════════════════════════════════
#  TARGET RESOLUTION  (alias -> person page -> telegram_uid::)
# ══════════════════════════════════════════════════════════
#
#  Presence of `telegram_uid:: <int>` on a person page IS the authorization
#  (PLAN.md §6/K5 step 1): only the owner can write Memory, so recording it
#  is the owner vouching for the mapping. There is no separate "registered
#  relay user" concept, and Telegram's own send API is the backstop — a
#  person who never started a chat with the bot simply can't receive one.

def _page_attrs(mem_dir: str, node: dict) -> dict:
    """Re-read and re-parse a node's page for its `key:: value` attrs. The
    `nodes` table deliberately doesn't store attrs (K1 keeps the pages as
    the truth), so this is a fresh read of one small file. `node["path"]`
    is relative to Memory/Wiki (graph.reindex_graph's convention)."""
    path = (node or {}).get("path")
    if not path:
        return {}
    try:
        from zilla import graph as _graph
        full = os.path.join(mem_dir, _graph.WIKI_DIRNAME, path)
        with open(full, encoding="utf-8") as f:
            return _graph.parse_entity_page(f.read()).get("attrs", {})
    except Exception as e:  # missing/renamed page, unreadable file
        logger.debug(f"[RELAY] could not read page for {path}: {e}")
        return {}


def _coerce_uid(raw: str | None) -> int | None:
    """`telegram_uid::` is owner-typed Markdown — never trust it to be an
    int (graph.py's _parse_dates discipline: tolerate, never raise)."""
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def resolve_target(db, alias: str, mem_dir: str) -> dict:
    """Resolve one bare alias to a relay target.

    Returns {"alias", "name", "uid": int | None, "reason": None | "no_node"
    | "no_uid"} — `uid` is set only when the relay can actually go out."""
    result = {"alias": alias, "name": alias, "uid": None, "reason": "no_node"}
    try:
        node_id = db.graph_alias_lookup(alias)
        node = db.graph_node_get(node_id) if node_id is not None else None
    except Exception as e:
        logger.debug(f"[RELAY] alias lookup failed for {alias!r}: {e}")
        return result
    if node is None:
        return result

    result["name"] = node.get("title") or alias
    uid = _coerce_uid(_page_attrs(mem_dir, node).get("telegram_uid"))
    if uid is None:
        result["reason"] = "no_uid"
        return result
    result["uid"] = uid
    result["reason"] = None
    return result


def find_person_by_uid(db, uid: int, mem_dir: str) -> dict | None:
    """Reverse lookup for K5 step 5 (inbound relay replies): which known
    person page carries this `telegram_uid::`? Attrs aren't indexed, so this
    is a linear scan over real (non-ghost) pages — the person-page count is
    realistically small and this only runs for a message from someone who is
    NOT an authorized user. Returns the node dict, or None."""
    try:
        nodes = db.graph_nodes_all()
    except Exception as e:
        logger.debug(f"[RELAY] node scan failed: {e}")
        return None
    for node in nodes:
        if node.get("is_ghost") or not node.get("path"):
            continue
        if node.get("type") not in (None, "person"):
            continue
        if _coerce_uid(_page_attrs(mem_dir, node).get("telegram_uid")) == uid:
            return node
    return None


# ══════════════════════════════════════════════════════════
#  HUMAN-READABLE LINES  (owner-facing; no jargon, no stack traces)
# ══════════════════════════════════════════════════════════

def failure_line(target: dict) -> str:
    """The one explanatory line appended to the owner's reply when a
    proposed relay can't be offered at all (PLAN.md §6/K5 step 3)."""
    name = target.get("name") or target.get("alias") or "them"
    if target.get("reason") == "no_uid":
        return (f"(I don't have a way to reach {name} yet — add their Telegram ID "
                f"to their page as `telegram_uid:: <number>` first.)")
    return (f"(I don't know who {name} is yet — I'd need a page for them, with their "
            f"Telegram ID as `telegram_uid:: <number>`, before I can reach them.)")


MALFORMED_LINE = "(I tried to set up a relay to someone, but got it wrong — say it again?)"


def summarize(action: dict) -> str:
    """One-line summary of what this action would do — used on the confirm
    card and in the `/relay log` audit trail."""
    if action.get("kind") == "schedule":
        try:
            from zilla.schedules import describe
            when = describe(action.get("sched_kind", ""), action.get("spec") or {})
        except Exception:
            when = action.get("sched_kind", "")
        return f"{when}: {action.get('text', '')}"
    return action.get("message", "")


def confirm_card(action: dict, target: dict) -> str:
    """The owner-facing confirm card — always shows the RESOLVED person and
    the EXACT text about to go out, never the alias the model used."""
    name = target.get("name") or target.get("alias")
    if action.get("kind") == "schedule":
        try:
            from zilla.schedules import describe
            when = describe(action.get("sched_kind", ""), action.get("spec") or {})
        except Exception:
            when = action.get("sched_kind", "")
        return (f"📨 Send to {name}, {when}?\n\n"
                f"“{action.get('text', '')}”\n\n"
                f"They'll get this in their own chat, from you.")
    return (f"📨 Send to {name}?\n\n"
            f"“{action.get('message', '')}”\n\n"
            f"They'll get this in their own chat, from you.")
