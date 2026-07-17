# HANDOFF — Zilla Advanced Build (for Sonnet execution agents)

> You are a Sonnet execution agent. This document is your work order. The
> planning is DONE — architecture decisions below are settled with the owner.
> Do not relitigate them. Your job is to execute the current phase precisely,
> verify, commit, and update `docs/dev/STATUS.md`.

## Read first, in this order

1. `docs/dev/AI_CONTEXT.md` — dense system spec. Read it fully. It defines the
   invariants (I-CONV, I-STEP, I-CANCEL, L) that you MUST NOT break.
2. `docs/dev/STATUS.md` — what the last session finished and what's next.
3. This file — the phase plan.

## How to work (non-negotiable)

- **One phase (or sub-phase) per session.** Finish it, verify it, commit it.
- **Tests before and after.** `python test_fixes.py` and
  `python test_interactive.py` must pass (176 total at time of writing).
  Every new module ships with deterministic, no-network tests added to the
  suite. A phase is not done if the count went down.
- **Evidence, not claims.** The CLI backends fail silently (see AI_CONTEXT
  "Mutation guidance"). Never report a feature working without a read-back,
  transcript, or test proving it.
- **Small commits, clear messages**, prefixed with the phase id
  (e.g. `feat(M1): ...`).
- **Preserve the invariants.** All CLI execution stays inside the per-user
  lock; conversation ids stay backend-tagged; OS-specific code stays in
  `platform_compat.py`.
- **Update `docs/dev/STATUS.md`** at the end of your session: what you did,
  what's verified vs. shipped-untested, what's next.
- When something is ambiguous, prefer the smallest reversible implementation
  and leave a note in STATUS.md rather than inventing scope.

---

## Settled architecture decisions (do not reopen)

1. **Markdown is the truth for knowledge; SQLite is the truth for operational
   state.** The owner's memories, wiki, and journal are plain `.md` files on
   disk — human-readable, agent-editable, portable across backends. SQLite
   (`zilla.db`) replaces the fragile JSON state files (sessions, schedules,
   users, settings, usage) and provides an FTS5 search index *over* the
   Markdown. The index is disposable: delete it and it rebuilds from the
   files. Never move knowledge into the database.
2. **MemGPT-style memory tiers, mapped to files.**
   - *Core memory* = `MEMORY.md`, small (≤ ~2000 chars), injected into every
     turn's instructions.
   - *Archival memory* = the Wiki, searched on demand (agent greps, or uses
     the FTS helper).
   - *Recall buffer* = the daily Journal, distilled nightly into the Wiki.
3. **Skill creation is ask-first.** After a hard/novel task the agent may
   *propose* saving a skill; nothing is saved without an owner tap. Code-
   bearing skills additionally require owner approval before first run —
   enforced by Zilla's code, never by model judgment.
4. **Build order: Memory → Heartbeat/self-healing → Router/fallback →
   Terminal app.** Foundation-up; each phase uses the previous one.
5. **Gateway principle.** Telegram, the future TUI, and any other surface are
   thin connectors over one core. When Phase M forces you to touch `bot.py`,
   pull the logic *out* into a surface-agnostic module rather than adding
   more into `bot.py`.

---

## Phase M — Memory foundation

### M1 — SQLite operational store (`store.py`)

Replace JSON-file state with one `zilla.db` (SQLite, WAL mode,
`busy_timeout`), accessed via a new `store.py` module.

- Tables: `sessions`, `schedules`, `users`, `settings`, `usage`
  (per-day, per-backend turn counts — feeds the future usage screen).
- All access through `store.py`; sync internals, called from async code via
  `asyncio.to_thread` (this also retires the "fsync on the event loop"
  audit finding). Writes are transactional — the corrupted-JSON failure
  mode disappears.
- **Migration:** on first start, if `zilla.db` is absent and the legacy JSON
  files exist, import them, then rename the JSON files to `*.migrated`
  (never delete). `install.py --doctor` learns to report DB health.
- `SessionManager`, `ScheduleManager`, `users.py`, and `config.py`'s
  settings KV keep their public APIs — only their persistence swaps. The
  in-memory mutation races flagged in STATUS.md (finding C5) get fixed here
  for free by routing mutations through `store.py` with a lock.
- Tests: migration round-trip, concurrent write safety, API equivalence.

### M2 — Memory layout + core memory injection

Create the on-disk memory home under `BRAIN_DIR`:

```
AGI-Brain/Memory/
  MEMORY.md          # core memory — identity, owner facts, standing prefs
  Wiki/              # topic pages: People/, Projects/, Preferences/, ...
  Journal/           # one file per day: 2026-07-17.md
```

- `memory.py`: create-on-missing with seeded templates; `read_core()`
  (capped, warns in log if over budget); `wiki_index()` → list of pages with
  first-line summaries; `journal_append(text)`.
- **Harness injection:** `harness.py` appends to every turn's instructions:
  the full `MEMORY.md`, the wiki index (paths + one-liners, not contents),
  and a short protocol: *"To recall details, grep/read files under
  AGI-Brain/Memory/Wiki. To remember something durable, edit MEMORY.md (keep
  it under 2000 chars) or the right Wiki page. Log day-to-day facts the owner
  shares to today's Journal file."*
- The agent edits memory with its own file tools — Zilla writes no
  extraction code. Zilla only guarantees the files exist, get injected, and
  get indexed.
- **First-run interview:** if `MEMORY.md` is still the seed template, the
  harness adds one line asking the agent to interview the owner briefly and
  fill it in. (This implements the README's onboarding promise.)
- Tests: injection content, cap warning, index generation, template seeding.

### M3 — FTS5 search + nightly distillation

- FTS5 table in `zilla.db` indexing every `Memory/**/*.md` (path, title,
  body, mtime). Reindex lazily: on bot start and whenever a file's mtime
  changed (cheap scan before injection, or on a ~5 min tick).
- `memsearch.py` — tiny CLI: `python memsearch.py "query"` → top-8 snippets
  with paths. The harness protocol tells the agent to use it when grep is
  too blunt or the wiki has grown. (Agent-callable tooling, zero extra AI
  calls — this is the "Active Memory" pattern at zero marginal cost.)
- **Nightly distillation:** a built-in system schedule (owner-visible,
  pausable, created via `ScheduleManager` with a `system` flag so it
  survives and can't be accidentally deleted) that runs the agent once at
  ~03:30: *"Read yesterday's Journal. Move durable facts into the right Wiki
  pages, update MEMORY.md if a standing fact changed, then compact the
  journal entry to a few lines."* Delivery is silent on success (see M4's
  quiet-run mechanism).
- Tests: index build/rebuild, mtime invalidation, memsearch output shape,
  system-schedule creation idempotency.

### M4 — Git-backed memory + quiet runs

- `git init` inside `AGI-Brain/Memory/` (its own repo — never the Zilla
  repo). After any run in which memory files changed (mtime scan), auto-
  commit with a one-line message including session name + date. Failure to
  commit must never break a reply — log and continue.
- `/memory` command in Telegram: show MEMORY.md, recent journal, last 5
  memory commits (read-only v1).
- **Quiet-run mechanism** (needed by distillation now, heartbeat in Phase H):
  if a scheduled run's response is exactly `HEARTBEAT_OK` (or empty after
  the review gate), deliver nothing. Anything else is delivered as usual.
- Tests: auto-commit trigger, no-crash-on-git-failure, quiet-run
  suppression.

**Phase M definition of done:** all four sub-phases merged, test suite grown
and green, a live smoke on the owner's machine shows: bot remembers a fact
across a fresh conversation via MEMORY.md; a Journal line appears after the
owner shares something personal; `memsearch.py` finds a planted wiki fact;
memory repo shows commits.

---

## Phase H — Heartbeat & self-healing (build after M)

- `HEARTBEAT.md` in `Memory/` — an agent-owned checklist of things to watch.
  A `system` schedule fires every 30 min (owner-tunable): agent reads the
  file, checks what's due, replies `HEARTBEAT_OK` if nothing needs the owner.
  Uses M4's quiet-run. The agent may edit its own HEARTBEAT.md when the
  owner asks it to "keep an eye on" something.
- Deterministic pre-check before spawning the agent: if `HEARTBEAT.md` is
  empty/absent, skip the run entirely (zero AI calls when idle).
- **Self-healing:** extend `--doctor` checks into a background probe
  (disk space, CLI binary present, login freshness). On backend auth
  failure: detect (`detect_limit` / login-error patterns), DM the owner the
  login link + exact steps, accept the pasted token/code via the existing
  interactive relay, retry, confirm. One alert, no spam (cooldown per
  failure kind).
- Usage counters from M1's `usage` table surface in `/settings`.

## Phase R — Router & fallback (build after H)

- **Triage pass, deterministic first:** heuristics route trivial cases
  (greetings, single-emoji, "thanks") to an instant canned/small path and
  detect "life-fact" statements to also journal them. Everything else goes
  to the full agent. Only add an AI triage call if heuristics prove
  insufficient — and then use the cheapest backend model, never agy
  (~13 s handshake).
- **Fallback chain:** on `detect_limit` or hard backend error, retry the
  same prompt once on the next configured backend. Honor I-CONV (fresh
  conversation on the fallback backend); inject a brief "context: the user
  was just asking about X" line so the answer isn't amnesiac. One clean
  answer, one small "answered via <backend>" footnote.

## Phase T — Terminal app (build after R)

- Textual-based `zilla` TUI: chat pane with live progress, sessions sidebar,
  settings/health/skills screens, conversational onboarding ("connect my
  Telegram" → asks for token → wires it).
- Precondition: by this phase, core logic must already live outside
  `bot.py` (the Gateway principle above). The TUI is a second connector to
  the same core — budget the extraction work into the phase.
- Detailed task breakdown to be written at phase start, in this file, by the
  planning session that kicks it off.

---

## Current state pointer

Phases H, R, T are intentionally sketched, not specified — they get their M1-
style task breakdowns when their turn comes, informed by what Phase M taught
us. Start at **M1**. Check `docs/dev/STATUS.md` for what's already landed.
