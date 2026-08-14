"""
Phase S — SKILLS FROM CHAT, ASK-FIRST (PLAN.md §11).

A skill is a folder the owner's agent wrote for itself after solving
something genuinely novel:

    Memory/Skills/<slug>/SKILL.md    frontmatter + when-to-use + steps
    Memory/Skills/<slug>/*           optional scripts beside it

Two things make this safe, and neither of them is the model's cooperation:

1. **Creation is ask-first.** The agent may only PROPOSE, with one
   deterministic marker at the end of a reply —
   `SKILL_PROPOSAL: <name> — <one-liner>`. Zilla strips it (the owner never
   sees the raw protocol), renders ✅/❌, and only a ✅ turns into an
   instruction to write the files.

2. **The index is the enforcement.** The CLI executes tools with full host
   privileges and no in-CLI sandbox exists (docs/dev/AI_CONTEXT.md trust
   model), so Zilla cannot physically stop a running agent from executing a
   file it finds on disk. What Zilla CAN control deterministically is what
   it ADVERTISES: the injected skill index carries only skills with a live
   `skill_approvals` row whose hash still matches the bytes on disk —
   **every** skill, `.md`-only included. A SKILL.md is injected
   instructions; one written out-of-band (by the agent, or by content
   someone injected into it) must never reach the index just because the
   file exists. Same rule decides which skills become slash commands: an
   unapproved skill is never callable.

The approval hash covers `SKILL.md` and every other byte in the folder, so
editing a script after approval revokes the skill (`state_of` returns
`CHANGED`, core drops the row and tells the owner in one line).

This module is the pure part: the marker parse/strip, the slug and command
name derivation, the folder read + hash, the state machine, and the
owner-facing copy. It does no writing and never raises on malformed model
output or malformed owner-authored Markdown (P4 — silent-safe).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re

logger = logging.getLogger(__name__)

# Where managed skills live, relative to Memory/ (zilla.memory.ensure_tree
# already creates it).
SKILLS_DIRNAME = "Skills"
SKILL_FILE = "SKILL.md"

# Telegram command names are 1-32 chars of [a-z0-9_], and a slug becomes a
# command name (PLAN.md §11 step 5), so the slug is capped there too.
MAX_SLUG = 32
MAX_NAME = 48
MAX_DESC = 160

# One reply proposes at most one skill. More than that is a bug or an
# injection attempt, not a genuinely novel task — extras are stripped and
# dropped, same discipline as relay/BG_TASK markers.
MAX_PROPOSALS = 1

# How many skills the injected index will list.
MAX_INDEX = 40

# One line, start-anchored, MULTILINE — the same marker family as
# `RELAY_SEND:` and `BG_TASK:`, so the model learns one convention.
_PROPOSAL_RE = re.compile(r"^SKILL_PROPOSAL:[ \t]*(.+)$", re.MULTILINE)

# `<name> — <one-liner>`. An em dash is what the protocol asks for; the
# other three are tolerated because a model that gets the separator slightly
# wrong should still produce a usable proposal (P4).
_SEP_RE = re.compile(r"\s+(?:—|–|::|-)\s+")

# ── skill states ──────────────────────────────────────────
OK = "ok"                  # approved, and the bytes still match the hash
UNAPPROVED = "unapproved"  # on disk, never approved — NOT indexed, NOT callable
DISABLED = "disabled"      # approved once, switched off by the owner
CHANGED = "changed"        # approved, but the files changed since — auto-revoked


# ══════════════════════════════════════════════════════════
#  MARKER PARSING
# ══════════════════════════════════════════════════════════

def slugify(name: str) -> str:
    """Deterministic slug (PLAN.md §11 step 5): lowercase, every non
    alphanumeric run collapsed to a single `_`, trimmed, capped. '' when
    there is nothing usable left — the caller drops the proposal."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower())
    return slug.strip("_")[:MAX_SLUG].strip("_")


def parse_markers(text: str) -> tuple[str, list[dict]]:
    """Split a model reply into (clean_text, proposals).

    Every `SKILL_PROPOSAL:` line is removed whether or not it parsed — the
    owner must never see the raw protocol. Each proposal is
    `{"name", "slug", "description"}`, or `{"error": "malformed"}` when the
    payload carried no usable name. Never raises."""
    if not text or "SKILL_PROPOSAL:" not in text:
        return text, []

    proposals: list[dict] = []

    def _take(match: re.Match) -> str:
        payload = (match.group(1) or "").strip()
        if not payload:
            return ""  # nothing was proposed — drop it, don't explain it
        parts = _SEP_RE.split(payload, 1)
        name = parts[0].strip()[:MAX_NAME]
        desc = (parts[1].strip()[:MAX_DESC] if len(parts) > 1 else "")
        slug = slugify(name)
        if not slug:
            proposals.append({"error": "malformed"})
        else:
            proposals.append({"name": name, "slug": slug, "description": desc})
        return ""

    clean = _PROPOSAL_RE.sub(_take, text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, proposals[:MAX_PROPOSALS]


# ══════════════════════════════════════════════════════════
#  ON-DISK SKILLS  (read-only — nothing here writes)
# ══════════════════════════════════════════════════════════

def skills_root(mem_dir: str) -> str:
    return os.path.join(mem_dir, SKILLS_DIRNAME)


def skill_dir(mem_dir: str, slug: str) -> str:
    return os.path.join(skills_root(mem_dir), slug)


def skill_file(mem_dir: str, slug: str) -> str:
    return os.path.join(skill_dir(mem_dir, slug), SKILL_FILE)


def parse_frontmatter(text: str) -> dict:
    """`--- key: value ... ---` at the top of a SKILL.md → dict. Tolerates a
    missing/broken block by returning {} (owner-authored Markdown is never
    trusted to be well-formed — zilla/graph.py's discipline)."""
    out: dict = {}
    if not text or not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    if end < 0:
        return out
    for line in text[3:end].splitlines():
        if ":" not in line or line.startswith(("  ", "\t", "#")):
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key:
            out[key] = value.strip().strip('"').strip("'")
    return out


def skill_files(mem_dir: str, slug: str) -> list[str]:
    """Every file in the skill folder, as '/'-joined relative paths, sorted.
    [] when the folder is missing or unreadable."""
    root = skill_dir(mem_dir, slug)
    found: list[str] = []
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                found.append(rel.replace(os.sep, "/"))
    except OSError as e:
        logger.debug(f"[SKILLS] walk failed for {slug}: {e}")
        return []
    found.sort()
    return found


def has_code(mem_dir: str, slug: str) -> bool:
    """True when the folder holds anything besides SKILL.md — a script, a
    binary, a data file. That is what makes a skill 'code-type', and a
    code-type skill never runs without an explicit owner approval tap."""
    return any(f != SKILL_FILE for f in skill_files(mem_dir, slug))


def code_hash(mem_dir: str, slug: str) -> str | None:
    """sha256 over SKILL.md AND every other byte in the folder, with the
    relative paths folded in so a rename is a change too. None when there is
    no SKILL.md — an incomplete folder is not a skill.

    This is the value stored on the approval row: any later edit to the
    instructions or to a script makes it stop matching, which auto-revokes
    the skill (PLAN.md §11 step 3)."""
    files = skill_files(mem_dir, slug)
    if SKILL_FILE not in files:
        return None
    digest = hashlib.sha256()
    root = skill_dir(mem_dir, slug)
    try:
        for rel in files:
            digest.update(rel.encode("utf-8") + b"\0")
            with open(os.path.join(root, *rel.split("/")), "rb") as f:
                digest.update(f.read())
            digest.update(b"\0")
    except OSError as e:
        logger.debug(f"[SKILLS] hash failed for {slug}: {e}")
        return None
    return digest.hexdigest()


def read_skill(mem_dir: str, slug: str) -> dict | None:
    """One skill as a dict, or None when there is no SKILL.md to read."""
    path = skill_file(mem_dir, slug)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read(20000)
    except OSError:
        return None
    front = parse_frontmatter(text)
    files = skill_files(mem_dir, slug)
    return {
        "slug": slug,
        "name": (front.get("name") or slug.replace("_", " "))[:MAX_NAME],
        "description": (front.get("description") or "")[:MAX_DESC],
        "created": front.get("created", ""),
        "uses": front.get("uses", ""),
        "prompt": front.get("prompt", ""),
        "path": path,
        "files": files,
        "has_code": any(f != SKILL_FILE for f in files),
        "hash": code_hash(mem_dir, slug),
    }


def list_skills(mem_dir: str) -> list[dict]:
    """Every managed skill on disk, slug-sorted. Never raises."""
    root = skills_root(mem_dir)
    out: list[dict] = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        if not os.path.isdir(os.path.join(root, name)):
            continue
        skill = read_skill(mem_dir, name)
        if skill is not None:
            out.append(skill)
    return out


# ══════════════════════════════════════════════════════════
#  THE GATE  (what may be advertised, and what may be called)
# ══════════════════════════════════════════════════════════

def state_of(skill: dict, row: dict | None) -> str:
    """The deterministic verdict for one skill, given its approval row.

    No row ⇒ UNAPPROVED. This is the whole security story (P5): a SKILL.md
    that appeared on disk without the owner ever tapping ✅ is invisible to
    the model and has no slash command, no matter what it says about
    itself."""
    if not row:
        return UNAPPROVED
    # The hash is checked BEFORE the switch, so an edited skill still reads
    # as CHANGED after the auto-revoke switched it off — the owner sees WHY
    # it stopped working, not just that it did.
    if not skill.get("hash") or row.get("code_hash") != skill.get("hash"):
        return CHANGED
    if not row.get("enabled", 1):
        return DISABLED
    return OK


def audit(db, mem_dir: str) -> tuple[list[dict], list[dict]]:
    """Read-only pass over every skill on disk: (approved, newly_revoked).

    `approved` is what may be injected and made callable. `newly_revoked` is
    the skills whose bytes changed while their approval was still live — the
    caller switches those off and tells the owner in one line. A skill
    already switched off is not reported again, so the notice fires exactly
    once per edit. Any store failure returns ([], []) — closed, never
    open."""
    try:
        rows = {r["slug"]: r for r in db.skill_approvals_all()}
    except Exception as e:
        logger.debug(f"[SKILLS] approval read failed: {e}")
        return [], []
    approved, revoked = [], []
    for skill in list_skills(mem_dir):
        row = rows.get(skill["slug"])
        state = state_of(skill, row)
        skill["state"] = state
        if state == OK:
            approved.append(skill)
        elif state == CHANGED and row and row.get("enabled", 1):
            revoked.append(skill)
    return approved, revoked


def listing(db, mem_dir: str) -> list[dict]:
    """Every managed skill with its state attached — the `/skills` view."""
    try:
        rows = {r["slug"]: r for r in db.skill_approvals_all()}
    except Exception:
        rows = {}
    out = []
    for skill in list_skills(mem_dir):
        skill["state"] = state_of(skill, rows.get(skill["slug"]))
        out.append(skill)
    return out


def command_name(slug: str, taken) -> str:
    """The slash command for a skill: the slug itself, suffixed `_2`/`_3`…
    when an existing command or another skill already claimed it (PLAN.md
    §11 step 5). '' when nothing usable is left."""
    base = slugify(slug)
    if not base:
        return ""
    taken = set(taken or ())
    if base not in taken:
        return base
    for n in range(2, 10):
        candidate = f"{base[:MAX_SLUG - 2]}_{n}"
        if candidate not in taken:
            return candidate
    return ""


def commands_for(skills: list[dict], taken) -> list[dict]:
    """[{slug, command, description}] for the given (already approved)
    skills, resolving collisions in slug order so the mapping is stable."""
    claimed = set(taken or ())
    out = []
    for skill in sorted(skills, key=lambda s: s["slug"]):
        name = command_name(skill["slug"], claimed)
        if not name:
            continue
        claimed.add(name)
        out.append({"slug": skill["slug"], "command": name,
                    "description": (skill.get("description")
                                    or skill.get("name") or skill["slug"])[:100]})
    return out


def prompt_for(skill: dict) -> str:
    """What typing `/<skill>` sends as the owner's turn. A skill may state
    its own `prompt:` in frontmatter; otherwise this is derived, so the
    command is always a shortcut for the WORDING — never a different
    execution path (PLAN.md §11 step 5)."""
    explicit = (skill.get("prompt") or "").strip()
    if explicit:
        return explicit[:500]
    name = skill.get("name") or skill["slug"]
    line = f"Use your \"{name}\" skill — read {skill.get('path', '')} and follow it."
    desc = (skill.get("description") or "").strip()
    return f"{line} ({desc})" if desc else line


# ══════════════════════════════════════════════════════════
#  INJECTION  (what the model is told exists)
# ══════════════════════════════════════════════════════════

def index_text(skills: list[dict]) -> str:
    """The skill index injected into the harness, wiki-index style: name,
    one-liner, and the path to read for the steps. '' when nothing is
    approved — an empty heading would be a lie about what's available."""
    lines = []
    for skill in skills[:MAX_INDEX]:
        desc = skill.get("description") or "(no description)"
        lines.append(f"- **{skill['name']}** — {desc} "
                     f"(read Skills/{skill['slug']}/{SKILL_FILE})")
    if len(skills) > MAX_INDEX:
        lines.append("[index truncated — retire skills you no longer use]")
    return "\n".join(lines)


PROPOSAL_PROTOCOL = (
    "- If you just solved something genuinely NEW and multi-step that the owner "
    "is likely to want again, you may end your reply with ONE line — "
    "`SKILL_PROPOSAL: <short name> — <one-line description>`. Only for real, "
    "reusable procedures; never for a one-off answer, and never more than one "
    "per reply. The owner has to tap ✅ before anything is written, so say you've "
    "offered to save it, never that you saved it."
)


def write_instruction(entry: dict, mem_dir: str) -> str:
    """The instruction prepended to the NEXT owner turn after a ✅ (PLAN.md
    §11 step 2). Deterministic text, so the model is told exactly where the
    files go and what the frontmatter must carry."""
    slug = entry.get("slug", "")
    folder = skill_dir(mem_dir, slug)
    name = entry.get("name") or slug
    desc = entry.get("description") or ""
    return (
        "SAVE THIS SKILL FIRST (the owner approved it): write "
        f"{os.path.join(folder, SKILL_FILE)} — a YAML frontmatter block with "
        f"`name: {name}`, `description: {desc or '<one line>'}`, `created: "
        "<today's date>`, `uses: 0` — then, in the body, when to use it and the "
        "exact steps you just worked out. Put any script it needs in the same "
        "folder. Keep it short enough to read in one screen. Then answer the "
        "owner normally."
    )


# ══════════════════════════════════════════════════════════
#  COPY  (owner-facing; STYLE.md — one calm sentence, no jargon)
# ══════════════════════════════════════════════════════════

def proposal_card(name: str, description: str) -> str:
    body = f"“{description}”\n\n" if description else ""
    return (f"🧩 Save this as a skill — {name}?\n\n{body}"
            "I'll write it down so I can do it the same way next time.")


MALFORMED_LINE = ("(I wanted to offer saving that as a skill but got the name "
                  "wrong — ask me again?)")


def saved_line(skill: dict, command: str) -> str:
    """A `.md`-only skill after the write turn: the ✅ tap already approved
    it, so it is live now."""
    tail = f" You can run it any time with /{command}." if command else ""
    return f"🧩 Saved — {skill.get('name') or skill.get('slug')}.{tail}"


def needs_approval_line(skill: dict) -> str:
    """A skill that arrived WITH code. The ✅ tap approved writing it; it
    does not approve running code the owner has not seen (PLAN.md §11 step
    3 — no code-type skill runs without an owner approval tap)."""
    return (f"🧩 Saved — {skill.get('name') or skill.get('slug')}, but it comes "
            "with a script. Open /skills to read it and switch it on; until "
            "then I won't use it.")


def not_written_line(entry: dict) -> str:
    return (f"🧩 I didn't manage to write the {entry.get('name') or 'new'} skill "
            "down. Nothing was saved.")


def revoked_line(skill: dict) -> str:
    """The one-line owner notice on a hash mismatch (PLAN.md §11 step 3)."""
    return (f"🧩 {skill.get('name') or skill.get('slug')} has been edited since "
            "you approved it, so I've switched it off. Open /skills to look at "
            "it and switch it back on.")


def approved_line(skill: dict, command: str) -> str:
    tail = f" — /{command}" if command else ""
    return f"✅ {skill.get('name') or skill.get('slug')} is on{tail}."


def disabled_line(skill: dict) -> str:
    return (f"🚫 {skill.get('name') or skill.get('slug')} is off. I won't use it "
            "and its command is gone.")


_STATE_ICON = {OK: "✅", UNAPPROVED: "⏸", DISABLED: "🚫", CHANGED: "⚠️"}
_STATE_WORD = {OK: "on", UNAPPROVED: "waiting for you",
               DISABLED: "off", CHANGED: "edited — switched off"}


def menu_text(managed: list[dict], legacy: list[str]) -> str:
    """The `/skills` panel. The two sources are labelled distinctly
    (PLAN.md §11 step 4): the ones Zilla manages and gates, and the ones the
    owner installed into the CLI itself, which Zilla only reports."""
    parts = ["🧩 <b>Skills</b>"]
    if managed:
        block = ["Mine — I only use the ones you've switched on"]
        for skill in managed:
            state = skill.get("state", UNAPPROVED)
            block.append(f"  {_STATE_ICON.get(state, '•')} {skill['name']} — "
                         f"{_STATE_WORD.get(state, state)}")
        parts.append("\n".join(block))
    else:
        parts.append("Nothing saved yet. When I work out something new and "
                     "reusable, I'll offer to save it.")
    if legacy:
        block = [f"Installed in the AI itself ({len(legacy)}) — not mine to control"]
        block += [f"  • {name}" for name in legacy[:10]]
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def detail_text(skill: dict) -> str:
    """One skill, opened from the menu — what it is, what it holds, and
    whether it is live."""
    state = skill.get("state", UNAPPROVED)
    lines = [f"🧩 <b>{skill['name']}</b>",
             skill.get("description") or "(no description)",
             f"Status: {_STATE_ICON.get(state, '•')} {_STATE_WORD.get(state, state)}"]
    extras = [f for f in skill.get("files", []) if f != SKILL_FILE]
    if extras:
        lines.append("Includes code: " + ", ".join(extras[:6]))
    lines.append(f"File: {skill.get('path', '')}")
    return "\n\n".join(lines)
