# Ponytail Audit — 2026-07-19

Whole-repo over-engineering scan (`/ponytail-audit`), run at commit
`ce7ac0f` on `main`. **Audit only — no code was changed.** Scope was
complexity/bloat only (dead code, reinvented stdlib, speculative
abstractions); correctness, security, and performance were explicitly
out of scope for this pass.

## Verdict

**Good. No structural problems.** This is a lean codebase — the
findings below are minor cleanup items, not red flags. Safe to keep
building on top of as-is.

## Method

- Cloned/pulled `alokflows/zilla` fresh, confirmed clean + up to date.
- Enumerated every real source file (excluded `.venv/`, `__pycache__/`,
  `.git/`) — 112 git-tracked files total.
- Checked: declared vs. actually-imported dependencies, class list
  across `zilla/*.py` for speculative interfaces (ABC/Protocol —
  none found), hand-rolled retry/JSON/config code vs. stdlib
  equivalents, dead-module check (grepped every caller of `migrate.py`,
  `review.py`, `heartbeat.py`, `security.py`, `backend_registry.py` —
  all have live callers + tests), `.gitignore` coverage of runtime
  state (`zilla.db`, `logs/`, `cache/`, `.venv/` — none tracked).

## What's already good (why the verdict is "good")

- Only 3 real runtime dependencies: `python-telegram-bot`,
  `SpeechRecognition`, `pydub` (+ `pywinpty`/`tzdata`, both
  Windows-only). Everything else is stdlib.
- `.env` loading (`zilla/config.py:29-39`) is a ~10-line
  zero-dependency parser — correctly avoids pulling in
  `python-dotenv` for a trivial job.
- `zilla/platform_compat.py` (Windows `msvcrt` lock vs. Unix `fcntl`,
  `winpty` vs. stdlib `pty`) is genuine, unavoidable OS-branching, not
  a reinvented wheel.
- `zilla/backend_registry.py`'s adapter-registry pattern currently has
  2 real implementations (`agy`, `claude`) with genuinely different
  logic each, plus a documented near-term 3rd backend (R3/opencode) —
  not speculative.
- No ABC/Protocol classes anywhere in `zilla/*.py` — no
  single-implementation interfaces.
- Runtime state (`zilla.db`, `logs/`, `cache/`, `sessions.json`, etc.)
  is fully covered by `.gitignore` and confirmed NOT git-tracked.
- `docs/dev/*` and `HANDOFF.md`/`PLAN.md` already show the team
  rejecting speculative additions on their own (e.g. `psutil`,
  Instructor/Pydantic auto-retry) — the discipline is already in
  place, not something this audit needs to introduce.

## Findings (ranked, biggest first)

### 1. [yagni] 14 legacy top-level import-shim files — known, tracked debt

**What:** 14 files at repo root are 4-line redirect shims into `zilla/`:

```
backends.py, cli_engine.py, config.py, formatter.py, harness.py,
interactive.py, media.py, platform_compat.py, schedule_parse.py,
schedules.py, sessions.py, users.py, verify.py, autoharness.py
```

Each is literally:
```python
"""Legacy import shim — module moved to zilla/ (Phase 1). Delete when nothing imports the old name."""
import sys as _sys
import zilla.<name> as _mod
_sys.modules[__name__] = _mod
```
(56 lines total across all 14 files.)

**Why it's still there (not a mistake):** `pyproject.toml`'s
`[tool.setuptools]` comment calls this a documented "Transitional
layout" and points to HANDOFF.md's planned Phase 3 for the real
cleanup — this is known, already-ticketed debt, not an oversight.

**What's blocking deletion — exact callers, verified by grep:**
- `bot.py:36,69,82,86,94-98` — imports `platform_compat`, `config`,
  `sessions`, `media`, `formatter`, `harness`, `users`, `schedules`,
  `schedule_parse` via bare names.
- `keyboards.py:18,22` — imports `config`, `media` via bare names.
- `test_fixes.py` — 15+ bare imports (`config`, `sessions`, `users`,
  `media`, `formatter`, `schedules`, `schedule_parse`, `cli_engine`,
  `verify`, `autoharness`, `backends`, `platform_compat` — see lines
  56, 62, 240, 325, 605, 622-626, 824, 934, 954, 983, 1001, 1027, 1053,
  1054, 1394).
- `test_interactive.py:10` — `import interactive as I`.

**Cut path (not code — just noting the shape of the fix for later):**
repoint those 4 files to `zilla.<name>` imports, then delete all 14
shim files. Net -56 lines once done. **Do not do this now — audit
only.**

### 2. [shrink] Duplicated constant: `WIKI_DIRNAME`

`WIKI_DIRNAME = "Wiki"` is independently defined in two places instead
of one shared constant:
- `zilla/memory.py:33`
- `zilla/graph.py:41`

Two sources of truth for the same literal. Low risk today (both hard-code
the same string), but a future rename only needs to catch both spots
if someone remembers to grep for it.

### 3. [shrink] Four names alias the same file

`zilla/config.py:205-208`:
```python
SESSIONS_FILE = DB_FILE
SETTINGS_FILE = DB_FILE
USERS_FILE = DB_FILE
SCHEDULES_FILE = DB_FILE
```
Leftover from the pre-SQLite era when each feature had its own JSON
file (M1 migrated everything to one SQLite `DB_FILE`, per HANDOFF).
Four names now point at one value — cosmetic, not a bug, but a
callback should decide if any consumer still cares about the distinct
names or if these can collapse to just `DB_FILE`.

## Net

`-56 lines from the shim cleanup (once the 4 blocking files move) + a
few more from the two shrink items above. 0 dependencies to cut —
none were found.`

## Not flagged (checked, ruled out)

- `zilla/security.py`, `zilla/doctor.py` — real, scoped tools with
  live callers (`zilla doctor`, `zilla doctor --security`), not
  speculative.
- `zilla/backend_registry.py` — 2 live implementations + a named 3rd
  on the roadmap, not a premature abstraction.
- `test_fixes.py` (1586 lines, 73 test functions) — large but real,
  non-duplicated coverage; flagged only insofar as its imports feed
  Finding #1.
- `.venv`, `zilla.db`, `logs/`, `cache/`, `__pycache__/` — confirmed
  not git-tracked (`git ls-files` returned 0 matches for `.venv/`).
