# ============================================================
#  MEMORY — the Markdown knowledge tier (PLAN.md §3.2, Phase M2)
# ============================================================
#  AGI-Brain/Memory/ (today: <repo>/Memory/ — config.MEMORY_DIR, settled by
#  M1's forward-declaration) is the OWNER's knowledge, in plain Markdown,
#  on their own disk:
#
#    MEMORY.md   — core memory, always in context. <= 2000 chars.
#    HEARTBEAT.md — agent-owned proactive checklist (seeded empty; H1 owns
#                   the real template).
#    Wiki/{People,Projects,Preferences,Places,Systems}/*.md — archival
#                   memory, one page per topic. Line 1 = H1 title, line 2 =
#                   one-line summary (that's what the wiki index shows).
#    Journal/YYYY-MM-DD.md — recall buffer, one file per day, appended.
#    Skills/<slug>/SKILL.md — learned skills (Phase S).
#
#  This module only guarantees existence, reads, and index-building. It
#  never parses meaning out of the Markdown — the agent edits these files
#  with its own file tools; Zilla's job is injection + indexing + (M3+)
#  search and git history.
# ============================================================

from __future__ import annotations

import os
from datetime import datetime

from zilla.config import MEMORY_DIR

WIKI_DIRNAME = "Wiki"
JOURNAL_DIRNAME = "Journal"
SKILLS_DIRNAME = "Skills"
MEMORY_FILENAME = "MEMORY.md"
HEARTBEAT_FILENAME = "HEARTBEAT.md"

WIKI_SUBDIRS = ("People", "Projects", "Preferences", "Places", "Systems")

# Exact byte-for-byte seed — is_template() compares against this, so the
# first-run interview line disappears the moment this file diverges even a
# little (the owner answering, or the agent filling it in on their behalf).
MEMORY_TEMPLATE = (
    "# Your memory\n"
    "\n"
    "Nothing here yet — this file is yours to keep updated with durable\n"
    "facts about the owner: who they are, standing preferences, routines,\n"
    "anything worth remembering across conversations. Keep it under 2000\n"
    "characters; move detail into a Wiki page instead.\n"
)

_STARTER_PAGES = {
    os.path.join("People", "owner.md"): (
        "# Owner\n"
        "Summary: who the owner is — fill in from the first-run interview.\n"
        "\n"
        "(nothing recorded yet)\n"
    ),
    os.path.join("Projects", "zilla.md"): (
        "# The Zilla project\n"
        "Summary: this assistant itself — what it's for, how it's set up.\n"
        "\n"
        "Zilla is a terminal-first AI assistant. Knowledge lives here in "
        "Markdown on the owner's own disk; the model answering questions "
        "can change without losing anything.\n"
    ),
    os.path.join("Systems", "zilla-howto.md"): (
        "# Using Zilla\n"
        "Summary: quick reference for how this assistant works.\n"
        "\n"
        "- Tell me things to remember — I keep MEMORY.md and this Wiki up "
        "to date.\n"
        "- Ask me to recall something and I'll search these pages.\n"
    ),
}


def _mem_dir(base: str | None = None) -> str:
    return base or MEMORY_DIR


def ensure_tree(base: str | None = None) -> None:
    """Create the Memory/ tree and seed templates, if missing. Idempotent
    and safe to call on every start: never overwrites a file that already
    exists (an owner's edits, or a MEMORY.md that has already diverged from
    the template, must never be clobbered by a restart)."""
    mem_dir = _mem_dir(base)
    wiki_dir = os.path.join(mem_dir, WIKI_DIRNAME)
    os.makedirs(mem_dir, exist_ok=True)
    for sub in WIKI_SUBDIRS:
        os.makedirs(os.path.join(wiki_dir, sub), exist_ok=True)
    os.makedirs(os.path.join(mem_dir, JOURNAL_DIRNAME), exist_ok=True)
    os.makedirs(os.path.join(mem_dir, SKILLS_DIRNAME), exist_ok=True)

    memory_md = os.path.join(mem_dir, MEMORY_FILENAME)
    if not os.path.exists(memory_md):
        with open(memory_md, "w", encoding="utf-8") as f:
            f.write(MEMORY_TEMPLATE)

    heartbeat_md = os.path.join(mem_dir, HEARTBEAT_FILENAME)
    if not os.path.exists(heartbeat_md):
        # Empty on purpose — H1 owns the real seeded checklist template.
        open(heartbeat_md, "w", encoding="utf-8").close()

    for rel, content in _STARTER_PAGES.items():
        path = os.path.join(wiki_dir, rel)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)


def read_core(base: str | None = None) -> str:
    """MEMORY.md's text, verbatim. '' if missing/unreadable."""
    path = os.path.join(_mem_dir(base), MEMORY_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def is_template(text: str | None = None, base: str | None = None) -> bool:
    """True while MEMORY.md is still exactly the seeded template — gates
    the first-run interview line (PLAN.md §4)."""
    if text is None:
        text = read_core(base)
    return text == MEMORY_TEMPLATE


def _page_title_summary(path: str) -> tuple[str, str]:
    """(title, summary) from a Wiki page's first two lines — H1 title on
    line 1, one-line summary on line 2 (PLAN.md §3.2 page format)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            line1 = f.readline().rstrip("\n")
            line2 = f.readline().rstrip("\n")
    except OSError:
        return ("", "")
    return (line1.lstrip("#").strip(), line2.strip())


def wiki_index(base: str | None = None) -> list[tuple[str, str]]:
    """[(relpath, summary), ...] for every page under Wiki/, sorted by
    path. relpath always uses '/' regardless of OS so injected text is
    stable across platforms."""
    wiki_dir = os.path.join(_mem_dir(base), WIKI_DIRNAME)
    pages: list[tuple[str, str]] = []
    if not os.path.isdir(wiki_dir):
        return pages
    for dirpath, _dirnames, filenames in os.walk(wiki_dir):
        for name in filenames:
            if not name.endswith(".md"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, wiki_dir).replace(os.sep, "/")
            _title, summary = _page_title_summary(full)
            pages.append((rel, summary))
    pages.sort(key=lambda p: p[0])
    return pages


def wiki_index_text(base: str | None = None, max_index_lines: int = 100) -> str:
    """Bullet list for harness injection: 'path — summary' per page,
    line-capped at max_index_lines with a visible truncation marker
    (PLAN.md §5.M2 step 3) so the agent notices and consolidates."""
    pages = wiki_index(base)
    lines = [f"- Wiki/{rel} — {summary}" if summary else f"- Wiki/{rel}"
             for rel, summary in pages]
    if len(lines) > max_index_lines:
        lines = lines[:max_index_lines]
        lines.append("[index truncated — consolidate pages]")
    return "\n".join(lines)


def journal_path(date: datetime | None = None, base: str | None = None) -> str:
    """Path to the given (default: today's) day's Journal file."""
    d = date or datetime.now()
    return os.path.join(_mem_dir(base), JOURNAL_DIRNAME, d.strftime("%Y-%m-%d.md"))


def append_journal(text: str, date: datetime | None = None,
                   base: str | None = None) -> str:
    """Append one timestamped line to today's Journal file (creating the
    tree first if needed). Returns the path written to."""
    ensure_tree(base)
    d = date or datetime.now()
    path = journal_path(d, base)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {d.strftime('%H:%M')} — {text}\n")
    return path
