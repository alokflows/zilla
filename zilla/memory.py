# ============================================================
#  MEMORY — the Markdown knowledge tier (PLAN.md §3.2, Phase M2)
# ============================================================
#  ~/Zilla/Memory/ (config.MEMORY_DIR — PLAN.md §17/F1's ZILLA_HOME storage
#  constitution) is the OWNER's knowledge, in plain Markdown, on their own
#  disk:
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

import logging
import os
from datetime import datetime

from zilla.config import MEMORY_DIR

logger = logging.getLogger(__name__)

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

# Phase H1 (PLAN.md §6): the agent's own proactive checklist — Zilla's code
# never parses this for meaning, the beat prompt just tells the agent to
# read it, act on what's due, and edit it. Seeded once (see ensure_tree);
# the owner/agent's edits from then on are never overwritten.
HEARTBEAT_TEMPLATE = (
    "# Heartbeat — I read this every 30 minutes and act on what's due.\n"
    "## Daily\n"
    "- 08:30 morning brief: today's schedules, anything in Watching/Follow-ups\n"
    "  that needs the owner. (last run: never)\n"
    "## Watching\n"
    "(nothing yet — when the owner says \"keep an eye on X\", add it here)\n"
    "## Follow-ups\n"
    "(open loops from conversations worth a nudge)\n"
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
    # Seed if missing OR empty (an empty file carries no owner/agent edits to
    # protect — the M2/M3 sessions left it empty on purpose pending H1; this
    # is H1 promoting that placeholder to the real template exactly once).
    # Any non-empty content, from here on, is never touched.
    if not os.path.exists(heartbeat_md) or os.path.getsize(heartbeat_md) == 0:
        with open(heartbeat_md, "w", encoding="utf-8") as f:
            f.write(HEARTBEAT_TEMPLATE)

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


def read_heartbeat(base: str | None = None) -> str:
    """HEARTBEAT.md's text, verbatim. '' if missing/unreadable."""
    path = os.path.join(_mem_dir(base), HEARTBEAT_FILENAME)
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


# ══════════════════════════════════════════════════════════
#  SEARCH INDEX  (mem_fts / mem_seen — Phase M3)
# ══════════════════════════════════════════════════════════
#  reindex() keeps the SQLite FTS5 index (store.py's mem_fts/mem_seen
#  tables, seeded by M1) in sync with the Markdown files on disk, diffed
#  by mtime+size rather than content — a same-second edit that happens to
#  land at the same byte size is a known, accepted blind spot (a full
#  content hash would close it but costs a read on every scan for a case
#  that hasn't mattered in practice). search() is the read side both
#  memsearch.py and harness.py's memory block (via that script) use.

def reindex(base: str | None = None) -> int:
    """Scan Memory/**/*.md, diff against mem_seen, upsert changed docs into
    mem_fts and drop entries for files that no longer exist. Cheap when
    nothing changed (one stat() per file). Never raises — a broken index
    must not break a turn or a search. Returns the count of docs (re)indexed."""
    try:
        from zilla.config import DB_FILE
        from zilla import store as _store
        db = _store.get_store(DB_FILE)
        mem_dir = _mem_dir(base)
        on_disk: set[str] = set()
        touched = 0
        for dirpath, _dirnames, filenames in os.walk(mem_dir):
            for name in filenames:
                if not name.endswith(".md"):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, mem_dir).replace(os.sep, "/")
                on_disk.add(rel)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                seen = db.mem_seen_get(rel)
                if seen and seen["mtime"] == st.st_mtime and seen["size"] == st.st_size:
                    continue
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        body = f.read()
                except OSError:
                    continue
                title = next(
                    (ln.lstrip("#").strip() for ln in body.splitlines() if ln.strip()), rel
                )
                db.fts_index(rel, title, body)
                db.mem_seen_set(rel, st.st_mtime, st.st_size)
                touched += 1
        for seen_row in db.mem_seen_all():
            if seen_row["path"] not in on_disk:
                db.fts_delete(seen_row["path"])
        try:
            from zilla import graph as _graph
            _graph.reindex_graph(db, mem_dir)
        except Exception as e:
            logger.debug(f"[MEMORY] graph reindex failed: {e}")
        return touched
    except Exception as e:
        logger.debug(f"[MEMORY] reindex failed: {e}")
        return 0


def _locate(full_path: str, query: str, fallback_snippet: str) -> tuple[int, str]:
    """FTS5 ranks matches but carries no line numbers — find the first line
    containing a query word and return it plus the line after it as a
    2-line snippet. Falls back to line 1 + the FTS-generated snippet if the
    file can't be read or no line matches (tokenization/stemming means a
    literal substring scan sometimes won't line up with the FTS hit)."""
    words = [w.lower() for w in query.split() if w]
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return (1, fallback_snippet)
    for i, line in enumerate(lines):
        if any(w in line.lower() for w in words):
            snippet = "\n".join(ln.rstrip("\n") for ln in lines[i:i + 2])
            return (i + 1, snippet or fallback_snippet)
    return (1, fallback_snippet)


def search(query: str, base: str | None = None, limit: int = 8) -> list[tuple[str, int, str]]:
    """Full-text search over Memory/**/*.md (reindexes first, so results
    reflect the latest edits — reindex is cheap when nothing changed).
    Returns up to `limit` (relpath, line, 2-line snippet) tuples, best
    match first. [] on no results or on any failure — a broken search
    degrades to 'nothing found', not a crash."""
    try:
        reindex(base)
        from zilla.config import DB_FILE
        from zilla import store as _store
        db = _store.get_store(DB_FILE)
        rows = db.fts_search(query, limit=limit)
        mem_dir = _mem_dir(base)
        results = []
        for row in rows:
            full = os.path.join(mem_dir, row["path"])
            line, snippet = _locate(full, query, row.get("snippet") or "")
            results.append((row["path"], line, snippet))
        return results
    except Exception as e:
        logger.debug(f"[MEMORY] search failed: {e}")
        return []


# ══════════════════════════════════════════════════════════
#  GIT HISTORY  (Phase M3)
# ══════════════════════════════════════════════════════════
#  Memory/ is its own git repo, one commit per turn/scheduled run that
#  actually changed something — the corruption/mistake recovery story for
#  the owner's knowledge, and (from M4 on) what `/memory diff` reads.
#  Zilla is the only writer, so a plain local repo with no remote is enough.

def git_autocommit(context: str, base: str | None = None) -> bool:
    """If Memory/ has uncommitted changes, `git add -A && git commit`.
    Initializes the repo on first call if absent (author "Zilla
    <zilla@local>", .git locked to 0700). Never raises — memory bookkeeping
    must not be able to break a reply; any failure (git missing, a locked
    file, disk full) is logged and swallowed. Returns True iff a commit was
    actually made."""
    import subprocess
    mem_dir = _mem_dir(base)
    git_dir = os.path.join(mem_dir, ".git")
    try:
        if not os.path.isdir(mem_dir):
            return False
        if not os.path.isdir(git_dir):
            subprocess.run(["git", "init"], cwd=mem_dir,
                           capture_output=True, text=True, timeout=10)
            subprocess.run(["git", "config", "user.name", "Zilla"], cwd=mem_dir,
                           capture_output=True, text=True, timeout=10)
            subprocess.run(["git", "config", "user.email", "zilla@local"], cwd=mem_dir,
                           capture_output=True, text=True, timeout=10)
            if os.path.isdir(git_dir):
                os.chmod(git_dir, 0o700)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=mem_dir,
                                capture_output=True, text=True, timeout=10)
        if status.returncode != 0 or not status.stdout.strip():
            return False
        subprocess.run(["git", "add", "-A"], cwd=mem_dir,
                       capture_output=True, text=True, timeout=10)
        commit = subprocess.run(["git", "commit", "-m", context], cwd=mem_dir,
                                capture_output=True, text=True, timeout=10)
        return commit.returncode == 0
    except Exception as e:
        logger.debug(f"[MEMORY] git_autocommit failed: {e}")
        return False


def _numstat_to_files(numstat_output: str) -> tuple[list[str], int, int]:
    """Parse `git ... --numstat` output into (files, insertions, deletions).
    A binary file reports '-' for both counts (git convention) — counted as
    a touched file with 0/0 rather than crashing on int()."""
    files: list[str] = []
    insertions = deletions = 0
    for line in numstat_output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        files.append(path)
        insertions += int(added) if added.isdigit() else 0
        deletions += int(removed) if removed.isdigit() else 0
    return files, insertions, deletions


def git_last_commit_stat(base: str | None = None) -> dict | None:
    """{'hash', 'files', 'insertions', 'deletions'} for HEAD — read right
    after a git_autocommit() that returned True, for the M4 change-
    surfacing DM. None if there is no repo, no commit, or on any failure
    (never raises — this feeds a best-effort notification, not a core
    reply)."""
    import subprocess
    mem_dir = _mem_dir(base)
    if not os.path.isdir(os.path.join(mem_dir, ".git")):
        return None
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=mem_dir,
                           capture_output=True, text=True, timeout=10)
        if h.returncode != 0:
            return None
        numstat = subprocess.run(["git", "show", "--numstat", "--format=", "HEAD"],
                                 cwd=mem_dir, capture_output=True, text=True, timeout=10)
        files, insertions, deletions = _numstat_to_files(numstat.stdout)
        return {"hash": h.stdout.strip(), "files": files,
                "insertions": insertions, "deletions": deletions}
    except Exception as e:
        logger.debug(f"[MEMORY] git_last_commit_stat failed: {e}")
        return None


def git_log(limit: int = 5, base: str | None = None) -> list[dict]:
    """Last `limit` Memory/ commits, newest first: {'hash', 'date',
    'subject', 'files', 'insertions', 'deletions'}. [] if there is no repo,
    no commits, or on any failure — powers the M4 `/memory` command."""
    import subprocess
    mem_dir = _mem_dir(base)
    if not os.path.isdir(os.path.join(mem_dir, ".git")):
        return []
    try:
        log = subprocess.run(
            ["git", "log", f"-{max(1, int(limit))}", "--format=%h%x1f%ad%x1f%s", "--date=short"],
            cwd=mem_dir, capture_output=True, text=True, timeout=10,
        )
        if log.returncode != 0:
            return []
        entries = []
        for line in log.stdout.splitlines():
            if not line.strip():
                continue
            fields = line.split("\x1f")
            if len(fields) != 3:
                continue
            commit_hash, date, subject = fields
            numstat = subprocess.run(
                ["git", "show", "--numstat", "--format=", commit_hash],
                cwd=mem_dir, capture_output=True, text=True, timeout=10,
            )
            files, insertions, deletions = _numstat_to_files(numstat.stdout)
            entries.append({"hash": commit_hash, "date": date, "subject": subject,
                            "files": files, "insertions": insertions, "deletions": deletions})
        return entries
    except Exception as e:
        logger.debug(f"[MEMORY] git_log failed: {e}")
        return []


def git_diff_latest(base: str | None = None) -> str:
    """Full unified diff of the most recent Memory/ commit (for `/memory
    diff`). '' if there is no repo, no commits, or on any failure."""
    import subprocess
    mem_dir = _mem_dir(base)
    if not os.path.isdir(os.path.join(mem_dir, ".git")):
        return ""
    try:
        r = subprocess.run(["git", "show", "--format=", "HEAD"], cwd=mem_dir,
                           capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except Exception as e:
        logger.debug(f"[MEMORY] git_diff_latest failed: {e}")
        return ""
