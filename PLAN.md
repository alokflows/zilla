# PLAN.md — Zilla: The Complete Blueprint

> **This is the single source of truth for what gets built.** Execution agents:
> read `docs/dev/AI_CONTEXT.md` first (system spec + invariants), then execute
> this plan phase by phase, in order. `HANDOFF.md` tracks status only.
> Architecture decisions here are settled with the owner — do not reopen them.

---

## 1. Product definition

Zilla is a **personal AI harness**: a control plane over rented agentic-CLI
brains (agy / Claude Code / opencode). The CLI does the reasoning and tool
execution; Zilla does everything around it — context shaping, memory, routing,
scheduling, health, and interfaces (Telegram today, terminal app in this plan).

**Product invariants (never violate):**

- **P1 — Knowledge is plain Markdown on the owner's disk.** Memories never
  live inside a database or a model. Databases may *index* Markdown; the
  files are always the truth and the index is always rebuildable.
- **P2 — Brains are swappable.** Nothing user-visible may depend on a single
  backend. Any feature that needs a model call goes through the backend
  contract in `cli_engine._run_blocking`.
- **P3 — Deterministic where possible.** An AI call is spent only where
  judgment is genuinely required. Timers, health checks, routing heuristics,
  review gates: pure code.
- **P4 — Never dead, never noisy.** Every message gets a sub-second
  acknowledgment and live progress. Proactive messages happen only when
  something genuinely needs the owner; everything else is silent.
- **P5 — Safety is enforced by Zilla's code, never by model judgment.**
  Approval gates, credential wiping, allowlists: deterministic.
- **P6 — Preserve the concurrency invariants** I-CONV / I-STEP / I-CANCEL / L
  (defined in `AI_CONTEXT.md`). All CLI execution stays inside the per-user
  lock; conversation ids stay backend-tagged.

**Platforms:** development on macOS; primary runtime is Linux (laptop or
always-on server). Windows keeps working via `platform_compat.py` but is not
the optimization target. All OS divergence stays in `platform_compat.py`.

---

## 2. Target architecture (end state)

```
                      ┌────────────────────────────────────────────┐
                      │                ZILLA CORE                  │
 Telegram ──┐         │  engine.py   — event-stream facade         │
            ├─ conn.  │  router.py   — triage + fallback chain     │
 zilla TUI ─┘ectors   │  harness.py  — instruction & memory inject │
                      │  cli_engine  — backend dispatch (P2)       │
                      │  memory.py   — wiki/journal/core memory    │
                      │  heartbeat.py— proactive loop              │
                      │  skills.py   — skill store + approval      │
                      │  schedules   — timers & recurring jobs     │
                      │  health.py   — probes + assisted re-login  │
                      │  store.py    — SQLite (state + FTS index)  │
                      └───────┬──────────────────┬─────────────────┘
                              │                  │
                     zilla.db (SQLite)   AGI-Brain/Memory/ (Markdown, git)
                     operational state    MEMORY.md  HEARTBEAT.md
                     + FTS5 index         Wiki/  Journal/  Skills/
                              │
                    backends: agy │ claude │ opencode  (rented brains)
```

**The Gateway principle:** `engine.py` is the only place a connector talks to.
Telegram (`bot.py`) and the TUI (`tui/`) are thin: they translate their
surface's I/O into engine calls and render engine events. New surface = new
connector, zero core changes.

**Engine event protocol** (the connector contract, built in Phase G):

```python
# engine.handle_message(uid, text, *, session=None, media=None)
#   -> AsyncIterator[Event]
Event = Ack(ts)                        # emit immediately, always first
      | Progress(text)                 # "⚙️ running command…"
      | Chunk(text)                    # partial/final answer text
      | NeedsInput(kind, prompt)       # OTP/password relay (existing bridge)
      | FileOut(path, caption)
      | Done(meta)                     # backend used, duration, session
      | Error(user_msg)                # already user-friendly (P4)
```

---

## 3. Data architecture

### 3.1 SQLite — `zilla.db` (operational truth)

One file next to `settings.json`'s old home. WAL mode,
`PRAGMA busy_timeout=5000`, `foreign_keys=ON`. All access through `store.py`
(sync internals; async callers use `asyncio.to_thread`). Schema versioned via
`meta.schema_version`; migrations are forward-only functions in `store.py`.

```sql
CREATE TABLE meta      (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE settings  (key TEXT PRIMARY KEY, value TEXT);           -- KV, replaces settings.json
CREATE TABLE users     (uid INTEGER PRIMARY KEY,
                        role TEXT NOT NULL CHECK(role IN ('admin','limited')),
                        added_at TEXT, added_by INTEGER);            -- replaces authorized_users.json
CREATE TABLE sessions  (uid INTEGER NOT NULL, name TEXT NOT NULL,
                        conv_id TEXT, conv_backend TEXT,
                        last_seen_step INTEGER DEFAULT 0,
                        auto_title TEXT, is_active INTEGER DEFAULT 0,
                        created_at TEXT, updated_at TEXT,
                        PRIMARY KEY (uid, name));                    -- replaces sessions.json
CREATE TABLE schedules (id TEXT PRIMARY KEY, uid INTEGER NOT NULL,
                        kind TEXT NOT NULL,          -- once|interval|daily|weekly
                        spec TEXT NOT NULL,          -- JSON, same shape as today
                        title TEXT, prompt TEXT,
                        enabled INTEGER DEFAULT 1,
                        system INTEGER DEFAULT 0,    -- system jobs: undeletable via UI, pausable
                        next_run TEXT, last_run TEXT,
                        fail_count INTEGER DEFAULT 0, created_at TEXT); -- replaces schedules.json
CREATE TABLE usage     (day TEXT NOT NULL, backend TEXT NOT NULL,
                        turns INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
                        fallbacks INTEGER DEFAULT 0,
                        PRIMARY KEY (day, backend));
CREATE TABLE skill_approvals (slug TEXT PRIMARY KEY,
                        code_hash TEXT NOT NULL,     -- sha256 over script files at approval
                        approved_at TEXT NOT NULL, approved_by INTEGER NOT NULL);
-- Search index over Markdown (disposable — rebuildable from files, P1):
CREATE VIRTUAL TABLE mem_fts USING fts5(path, title, body, tokenize='porter unicode61');
CREATE TABLE mem_seen  (path TEXT PRIMARY KEY, mtime REAL, size INTEGER);
```

Rules: public APIs of `SessionManager`, `ScheduleManager`, `users`, and the
settings KV **do not change** — only persistence swaps. Every mutation goes
through `store.py` under one lock (this permanently fixes audit finding C5,
the in-memory mutation race). Legacy JSON is imported on first start, then
renamed `*.migrated` (never deleted).

### 3.2 Markdown — `AGI-Brain/Memory/` (knowledge truth, its own git repo)

```
AGI-Brain/Memory/            ← `git init` here; NEVER the Zilla repo
  MEMORY.md                  ← core memory: owner facts, standing prefs. ≤ 2000 chars.
  HEARTBEAT.md               ← agent-owned proactive checklist (see §6)
  Wiki/                      ← archival memory, one page per topic
    People/  Projects/  Preferences/  Places/  Systems/
  Journal/
    2026-07-17.md            ← one file per day, newest entries appended
  Skills/
    <slug>/SKILL.md          ← learned skills (see §8); scripts live beside it
```

Page format: H1 title on line 1, one-line summary on line 2 (the wiki index
shows exactly these two lines), free Markdown below. Journal entries:
`- HH:MM — text`. The **agent** edits these files with its own file tools;
Zilla never parses meaning out of them — it only guarantees existence,
injection, indexing, and git history.

**Memory tiers (MemGPT model, mapped to files):**
core = `MEMORY.md` (always in context) · archival = `Wiki/` (searched on
demand) · recall buffer = `Journal/` (distilled nightly into Wiki, §5.M3).

---

## 4. Harness injection spec (exact contract)

**Scope guard (privacy-critical):** memory is the OWNER's. The block below is
injected **only for turns initiated by the owner** (including owner-created
schedules, heartbeat, distillation). Turns from any other principal
(admin/limited/approval-mode users) get NO memory injection, no memory
protocol, and no journal instruction — their prompts must never contain the
owner's MEMORY.md, wiki index, or skills index. Enforced deterministically in
`harness.py` by uid, with a test proving a non-owner turn contains none of it.

`harness.py` appends one block to every owner turn's instructions, built fresh
each turn (cheap: file reads + one index scan):

```
## Your memory (persistent, yours to maintain)
[full text of MEMORY.md]

## Wiki index (read pages with your file tools when you need details)
- Wiki/People/alok.md — Owner: preferences, contacts, routines
- Wiki/Projects/zilla.md — The Zilla project itself
  … (path + line-2 summary for every page)

## Memory protocol
- To recall details: read/grep files under {MEM_DIR}, or run
  `python {ZILLA_DIR}/memsearch.py "query"` for ranked full-text results.
- To remember something durable: edit MEMORY.md (keep it under 2000
  characters — move detail to a Wiki page) or the right Wiki page.
- When the owner shares anything about their life, plans, or preferences,
  append one line to today's Journal file: `- HH:MM — fact`.
- Never store credentials, OTPs, or tokens in any memory file.
```

Deterministic guards in `harness.py`: warn in log when MEMORY.md exceeds
2400 chars (soft cap); truncate injection at 4000 chars (hard cap) with a
visible `[truncated — trim me]` marker so the agent fixes it. **First-run
interview:** while MEMORY.md is still the seeded template, append one line:
*"MEMORY.md is empty — briefly interview the owner (3-4 questions max) and
fill it in."* The line disappears once the file diverges from the template.

---

## 5. Phase M — Memory foundation

### M1 — `store.py` + migration
1. Implement `store.py` per §3.1 (connection mgmt, lock, schema, migrations,
   typed accessors: `get_setting/set_setting`, `sessions_*`, `schedules_*`,
   `users_*`, `usage_bump`, `fts_*`, `skill_approval_*`).
2. Swap persistence in `sessions.py`, `schedules.py`, `users.py`,
   `config.py` settings KV. Public APIs unchanged. Threading rule (realistic,
   not dogmatic): **reads** may run inline on the event loop (WAL reads are
   microseconds; existing call sites like `get_setting`/`get_backend` are
   sync and stay sync); **writes** go through `asyncio.to_thread` from async
   contexts — this kills the `fsync`-on-event-loop finding without a
   whole-codebase call-site refactor.
3. First-start migration from JSON + rename to `*.migrated`.
4. `install.py --doctor`: add DB checks (exists, schema version, WAL, write
   probe).
5. **Audit-debt burn-down** (open findings from `docs/dev/STATUS.md`, fixed
   here while these modules are open anyway): `compute_next_run` becomes
   timezone-aware via `zoneinfo` (local tz; DST-transition tests both
   directions); `_active_cancel` keyed by `(chat_id, uid)` instead of
   `chat_id` (kills cross-user cancel in groups); media ingest size cap
   (setting `max_media_mb`, default 50, oversize → one friendly refusal).
   **Accept:** full suite green; migration round-trip test (seed JSON →
   migrate → API-identical reads); concurrent-mutation test (two tasks
   mutate sessions 100×, no loss); DST tests pass; cancel-keying and
   media-cap tests pass; doctor reports DB OK.

### M2 — Memory layout + injection
1. `memory.py`: ensure-tree-on-start with seeded templates (MEMORY.md
   template, empty HEARTBEAT.md, starter Wiki pages `People/owner.md`,
   `Projects/zilla.md`, `Systems/zilla-howto.md`); `read_core()`,
   `wiki_index()`, `journal_path(date)`.
2. Harness injection exactly per §4, including caps and first-run interview.
   **Accept:** injection unit tests (content, caps, interview line
   appears/disappears); live smoke — tell the bot a fact, `/new` session,
   ask it back: answered from MEMORY.md, and a Journal line exists.

### M3 — FTS5 search + nightly distillation
1. Indexer in `memory.py`: scan `Memory/**/*.md`, diff against `mem_seen`
   (mtime+size), upsert changed docs into `mem_fts`. Runs at start and
   before each harness injection (the scan is ~ms; reindex only on change).
2. `memsearch.py` CLI: `python memsearch.py "query"` → top 8: `path:line`
   + 2-line snippet, plain text. Exit 0 with "no results" when empty.
3. Nightly distillation: `system` schedule (daily 03:30, owner uid) with
   prompt: *"Read yesterday's Journal file. Move durable facts into the
   right Wiki pages (create pages as needed), update MEMORY.md if a
   standing fact changed, then rewrite the journal entry down to its
   essentials. Reply HEARTBEAT_OK when done."* Created idempotently at
   start; pausable in `/settings`, not deletable. Runs in a **throwaway
   conversation** (fresh conv id, discarded after the run — never advances
   any session's conv id); raw pre-distillation journal text stays
   recoverable via memory git history (M4).
   **Accept:** index build/invalidation tests; memsearch finds a planted
   fact; distillation schedule exists exactly once after double restart.

### M4 — Git-backed memory + quiet runs
1. `memory.git_autocommit(context)`: if the Memory repo has changes after a
   CLI run / scheduled run, `git add -A && git commit -m "<context>"`.
   Failures log and never break replies. `git init` on first start if absent
   (author "Zilla <zilla@local>").
2. **Quiet-run mechanism** in the scheduled-run delivery path: if the
   stripped response **is or ends with** a line equal to `HEARTBEAT_OK`
   (case-insensitive) → deliver nothing, log only. (Exact-match-only is too
   brittle — models pad. Used by distillation now, heartbeat in Phase H.)
3. `/memory` command (owner): shows MEMORY.md, today's journal, last 5
   memory commits. Read-only.
   **Accept:** autocommit fires on change and not on no-change; git failure
   injected → reply still delivered; HEARTBEAT_OK suppression test; /memory
   renders.

---

## 6. Phase H — Heartbeat & self-healing

**Design (owner-confirmed):** ONE agent-owned file, `HEARTBEAT.md`, holds
everything — briefings, watches, follow-ups, notes-to-self. The agent reads
it each beat, does what's due, edits the file itself (checking things off,
adding stamps, adding new items the owner asked for). Zilla's code stays
dumb: it fires the beat, injects time context, and enforces quiet runs.

Seeded template:

```
# Heartbeat — I read this every 30 minutes and act on what's due.
## Daily
- 08:30 morning brief: today's schedules, anything in Watching/Follow-ups
  that needs the owner. (last run: never)
## Watching
(nothing yet — when the owner says "keep an eye on X", add it here)
## Follow-ups
(open loops from conversations worth a nudge)
```

### H1 — Beat loop
1. `heartbeat.py`: `system` interval schedule (default 30 min, setting
   `heartbeat_interval`, 0 = off). Deterministic pre-check: file missing /
   only template headers → **skip entirely, zero AI calls**.
2. Beat prompt: *"It is {now} ({tz}). Last beat: {last}. Read HEARTBEAT.md.
   Do anything due; update the file (stamps, checkoffs, prune stale items).
   If nothing needs the owner, reply HEARTBEAT_OK."* Beats **try-acquire**
   the owner's uid lock and skip the beat if it's busy — proactive work
   never queues behind (or delays) a live owner conversation; a skipped
   beat just waits for the next tick. Beats run in throwaway conversations
   (same rule as distillation). Quiet-run suppresses OK beats; memory
   autocommit picks up file edits.
3. Harness gains one protocol line: *"When the owner asks you to keep an
   eye on / remind / follow up on something recurring, add it to
   HEARTBEAT.md."*
   **Accept:** empty-file skip test; beat fires and edits file in live
   smoke; OK beats silent; "watch X" via chat lands in HEARTBEAT.md.

### H2 — Health probes + assisted re-login
1. `health.py`: deterministic probes each beat tick (before any AI call):
   disk ≥ 500 MB free, `zilla.db` writable, backend binaries on PATH,
   backend login freshness (agy: probe settings file / known error
   patterns; claude: `claude -p "ping"` cheap probe at most 1×/6 h;
   patterns centralized in `health.py`).
2. On failure: fix silently if code can (rebuild index, rotate logs). If a
   human is needed (login expired): ONE DM — what broke, the exact login
   link/steps — then use the existing interactive relay to accept the
   pasted token/code, run the login, verify, confirm. Per-kind cooldown
   6 h; never repeats while unresolved-and-acknowledged.
3. Failed probes also prepend one line to the next beat prompt
   ("System flag: agy login expired — already DM'd owner") so the agent
   doesn't duplicate alerts.
   **Accept:** probe unit tests with injected failures; cooldown test;
   live smoke of one full re-login round-trip (documented in STATUS.md).

### H3 — Linux service deployment
1. `install.py --service`: writes + enables a systemd **user** unit
   (`~/.config/systemd/user/zilla.service`, `Restart=on-failure`,
   `WantedBy=default.target`, lingering hint printed). Mac dev keeps
   `./start.sh`; nothing Windows breaks.
2. Doctor: service status check on Linux.
   **Accept:** unit file golden test; doctor reports service state; live:
   reboot → bot up, missed schedules caught up (existing reconcile).

---

## 7. Phase R — Router & fallback

### R1 — Triage router
`router.py`, deterministic only (P3), runs before the engine spends a lock:
1. Classes: `command` (leading `/`) → existing handlers. `trivial` —
   matches a small pattern set (greeting/thanks/ack/emoji-only, < 40 chars,
   no question about state) → still answered by the agent but with a
   **fast profile**: skip wiki index injection (core MEMORY.md only) and
   prefer the fastest backend in the chain for this one turn (fresh
   throwaway conv; do NOT advance the session's conv id — I-CONV).
   `normal` — everything else, full injection, session backend.
2. Misclassification safety: if a `trivial` reply comes back empty/error,
   silently rerun as `normal`. Router decisions logged.
   **Accept:** classifier table-driven tests (≥ 30 cases); fast-profile
   turn does not mutate session conv id; rerun-on-empty test.

### R2 — Fallback chain
1. Setting `backend_chain` (ordered, default = active backend + others
   detected on PATH). **A chain entry is eligible only if its last health
   probe (H2) showed a fresh login** — a binary on PATH that isn't logged
   in would burn the retry or hang on a login prompt; skip it and log why.
   On `detect_limit`, hard error, or empty-after-retry:
   move to next chain entry, fresh conversation (I-CONV), prepend one
   primer line ("Context: the owner was just asking about: <last user
   message>"), deliver ONE clean answer with footnote `↷ answered via
   <backend>`. `usage.fallbacks` bumped. Chain exhausted → honest
   plain-language stop (P4).
2. Session stays tagged to its original backend; fallback turns are
   throwaway convs unless the owner switches backends properly.
   **Accept:** simulated-limit test walks the chain exactly once per
   backend; footnote present; session conv id untouched.

### R3 — opencode backend adapter
1. `backends.run_opencode()` implementing the run-contract
   (`opencode run` non-interactive mode with JSON output + session
   continuation per current opencode CLI docs — executor verifies flags
   against the installed version, evidence-first). Register in
   `_run_blocking`, `model_catalog`, installer detection, doctor.
   **Accept:** contract tests with mocked binary; live round-trip +
   resume-continuity smoke on a machine with opencode logged in; chain
   `agy → claude → opencode` exercised.

---

## 8. Phase S — Skills from chat (ask-first)

1. Format: `Memory/Skills/<slug>/SKILL.md` — frontmatter (`name`,
   `description`, `created`, `uses`) + body (when to use, steps); optional
   scripts beside it. Skill index (name + description) injected into the
   harness like the wiki index.
2. **Creation is ask-first (owner decision):** harness protocol — after
   solving a genuinely novel multi-step task, the agent may end with
   `SKILL_PROPOSAL: <name> — <one-liner>`. Zilla detects the marker
   (deterministic), strips it from the reply, renders ✅/❌ buttons. ✅ →
   next turn instructs the agent to write the skill files. ❌ → dropped,
   never re-proposed for that session-task.
3. **Code approval gate — honest mechanics:** the CLI executes tools with
   full host privileges and no in-CLI sandbox exists (AI_CONTEXT trust
   model), so Zilla cannot *physically* stop a running agent from executing
   a file. What Zilla CAN deterministically control is **what it
   advertises**: skills whose dir contains non-`.md` files are simply **not
   injected into the skill index at all** until approved — the agent is
   never told they exist. Approval stores `sha256(sorted script bytes)` in
   `skill_approvals`; hash mismatch ⇒ auto-revoke (drops from index) + a
   one-line owner notice. Approved skills are listed in the index normally.
   This is real enforcement of visibility, honestly documented as such —
   not a pretend sandbox. Approval UI: `/skills` menu (list, view, approve,
   disable).
   **Accept:** marker detect/strip tests; approval hash lifecycle tests
   (approve → run allowed; edit script → revoked); /skills flows; live
   smoke: solve task → proposal → ✅ → skill file exists, indexed,
   committed to memory git.

---

## 9. Phase G — Gateway extraction, then Phase T — Terminal app

### G1 — Engine facade (prerequisite for T, pure refactor)
1. Extract from `bot.py` into `engine.py`: the run pipeline (lock →
   router → harness → cli_engine → review gate → events) emitting the §2
   event protocol. `bot.py` becomes a consumer: maps events to Telegram
   sends (ack → typing, Progress → editable status msg, NeedsInput →
   bridge DM…). Behavior identical.
   **Accept:** full suite green, zero user-visible change in live smoke
   (message, cancel, schedule fire, OTP relay all behave exactly as
   before). This is the riskiest refactor in the plan — do it alone, in
   small commits, nothing else in the session.

### T1 — `zilla` TUI (Textual)
1. `tui/` package, `zilla` entry point (console script). Screens:
   **Chat** (stream engine events; Esc = cancel), **Sessions** sidebar,
   **Schedules**, **Memory** (MEMORY.md + journal + commits), **Skills**
   (approve/disable), **Health** (probe results, usage counters from
   `usage`), **Settings** (backend/model/chain/heartbeat interval —
   same setters as Telegram menus).
2. Conversational onboarding: no `.env` → Chat opens with setup dialogue
   ("connect Telegram?" → token prompt → writes config → doctor).
   Telegram becomes optional: engine runs with zero connectors + TUI.
3. **Process model (specified, not hand-waved):** the engine process
   exposes a local IPC endpoint — a Unix domain socket
   (`AGI-Brain/zilla.sock`, mode 600) speaking newline-delimited JSON:
   client sends `{op: "message"|"cancel"|"status"|..., ...}`, server
   streams the §2 events serialized as JSON lines. `zilla` (TUI) is a thin
   client: if the socket answers, attach to the running daemon/service;
   otherwise start the engine in-process (single-instance lock still
   guarantees exactly one engine). The existing WebBridge shims are NOT
   the transport — they're status-only and stay untouched. Windows keeps
   in-process mode only (no socket) — acceptable, Linux is the runtime
   target.
   **Accept:** each screen has a snapshot/behavior test (Textual pilot);
   live smoke on Mac + Linux: chat round-trip with live progress, cancel,
   settings change reflected in Telegram side too.

---

## 10. Phase V — Offline voice

1. `faster-whisper` optional dependency (`pip install zilla[voice]` /
   requirements-voice.txt). Setting `transcribe = auto|local|online`
   (default `auto` = local if importable, else current online path).
2. `media.transcribe_audio` branches: local path uses model `small`
   (int8, CPU) — lazy first-use download with a one-time progress note;
   result format identical to online path. Doctor reports voice mode.
   **Accept:** branch tests with mocked model; live smoke: voice note on
   Linux transcribed offline (network blocked) and answered.

---

## 11. Cross-cutting engineering rules

- **Testing:** every phase adds deterministic no-network tests to the suite
  (`test_fixes.py` / new `test_<module>.py` files wired into it). Suite
  count only goes up. Live smokes are listed per phase and their results
  recorded in `docs/dev/STATUS.md` — a feature is "shipped-untested" until
  its smoke is logged (the backends lie silently; evidence or it didn't
  happen).
- **Commits:** small, phase-prefixed (`feat(M1): …`, `fix(H2): …`).
  One sub-phase per session. Update `HANDOFF.md` checkboxes + STATUS.md
  every session.
- **Concurrency:** every AI call — user turns, schedules, distillation,
  heartbeat — runs under the per-uid `asyncio.Lock`. New long-running
  deterministic work (indexing, git) uses `asyncio.to_thread`.
- **Errors:** the owner never sees a stack trace (P4). Log verbosely,
  deliver one plain sentence, degrade gracefully (memory git down ≠ reply
  down; FTS down ⇒ grep still works; heartbeat failure ⇒ next beat).
- **Secrets:** existing redaction filter covers new logs; memory protocol
  forbids credentials in Markdown; relay answers keep their wipe behavior.

## 12. Risk register (with mitigations already in the plan)

1. **G1 refactor breaks invariants** → isolated phase, behavior-freeze
   smoke checklist, small commits.
2. **agy silent fallback / flag drift** → evidence-first rule; read-backs;
   R3 requires live verification before claiming opencode works.
3. **SQLite lock contention (WAL + FTS writes during beats)** → single
   `store.py` lock, busy_timeout, all writes short transactions.
4. **Agent bloats MEMORY.md / wiki** → hard injection caps + visible
   truncation marker + distillation job actively compacts.
5. **Heartbeat spam** → quiet-run default, per-kind alert cooldowns,
   deterministic empty-file skip, try-acquire (never blocks the owner).
6. **Memory privacy leak to non-owner users** → §4 scope guard: injection
   is owner-turn-only, enforced by uid with a negative test.
7. **TUI↔daemon transport under-engineered** → §9 specifies the Unix-socket
   JSONL protocol up front; WebBridge explicitly ruled out as transport.
8. **Fallback to present-but-unauthenticated backend** → chain eligibility
   gated on H2 login-freshness probes.

## 13. Execution order & progress

Execute strictly top-to-bottom. Check items off here (this file) as they land.

- [ ] M1 SQLite store + migration
- [ ] M2 Memory layout + injection
- [ ] M3 FTS5 + nightly distillation
- [ ] M4 Memory git + quiet runs
- [ ] H1 Heartbeat loop
- [ ] H2 Health probes + assisted re-login
- [ ] H3 systemd service
- [ ] R1 Triage router
- [ ] R2 Fallback chain
- [ ] R3 opencode adapter
- [ ] S  Skills from chat
- [ ] G1 Engine facade extraction
- [ ] T1 Terminal app
- [ ] V  Offline voice
