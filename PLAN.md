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
CREATE TABLE users     (uid INTEGER PRIMARY KEY, name TEXT,
                        role TEXT NOT NULL CHECK(role IN ('admin','limited')),
                        added_at TEXT, added_by INTEGER);            -- replaces authorized_users.json
CREATE TABLE denied    (uid INTEGER PRIMARY KEY, denied_at TEXT);    -- replaces denied_users.json;
                        -- deny-list is checked BEFORE membership (users.py semantics)
CREATE TABLE sessions  (uid INTEGER NOT NULL, name TEXT NOT NULL,
                        conv_id TEXT, conv_backend TEXT,
                        last_seen_step INTEGER DEFAULT 0,
                        auto_title TEXT, is_active INTEGER DEFAULT 0,
                        messages INTEGER DEFAULT 0,  -- read by auto-title gate + sessions UI
                        last_used TEXT,
                        created_at TEXT, updated_at TEXT,
                        PRIMARY KEY (uid, name));                    -- replaces sessions.json
CREATE TABLE schedules (id TEXT PRIMARY KEY, uid INTEGER NOT NULL,
                        chat_id INTEGER NOT NULL,    -- delivery target (bot._deliver_schedule_result)
                        kind TEXT NOT NULL,          -- once|interval|daily|weekly
                        spec TEXT NOT NULL,          -- JSON, same shape as today
                        title TEXT, prompt TEXT,
                        session_name TEXT,           -- legacy pre-Part-B binding (back-compat read only)
                        session TEXT,                 -- 'isolated'|'main'|'named:<x>' (resolve_session_mode)
                        payload_type TEXT DEFAULT 'message',  -- message|system_event|command
                        backend TEXT, model TEXT,     -- pin (None = any); see backend_pin_mismatch
                        backend_pin_notified INTEGER DEFAULT 0,  -- one-time pin-drift note, never repeats
                        enabled INTEGER DEFAULT 1,
                        system INTEGER DEFAULT 0,    -- system jobs: undeletable via UI, pausable
                        next_run REAL, last_run REAL, -- epoch seconds (compute_next_run's native unit)
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

**M1 grep-enumeration correction (2026-07-17 night, per the step-2 mandate
below):** `users.name` and `schedules.session`/`payload_type`/`backend`/
`model`/`backend_pin_notified` were read/written by live code
(`zilla/users.py`, `zilla/schedules.py`) but absent from the schema as
first drafted — added above. Confirmed against source, not just grep:
`resolve_session_mode()` and `backend_pin_mismatch()` in
`zilla/schedules.py` depend on all five schedule fields; `add_user()` in
`zilla/users.py` always writes `name`. Also corrected:
`schedules.next_run`/`last_run` are epoch-second floats in every real
write path (`compute_next_run`, `touch_run`, `mark_failure`), not the
TEXT datetime strings first drafted — now REAL. `schedules.system` and
`users.added_by` remain in the schema unused by current code (forward-
looking, harmless). `usage`/`skill_approvals` have no migration source
data (later phases populate them).

**Threading/locking model (one coherent story — do not improvise another):**
- Managers (`SessionManager`, `ScheduleManager`, `users`, settings KV) become
  **thin wrappers over store.py queries — no in-memory dict caches**. The DB
  is the cache. With no shared mutable dicts, audit finding C5 (in-memory
  mutation race) ceases to exist rather than being "locked around".
- **Reads** use a dedicated read-only connection and take **no store lock** —
  WAL gives reader/writer isolation; reads never block on writers. Sync read
  APIs (`get_setting`, `get_backend`, …) stay sync and may run on the event
  loop (microseconds).
- **Writes** are short single transactions serialized by one writer lock.
  Sync mutator APIs stay sync ("fast transaction, may run on loop" — a
  single-row SQLite commit is milliseconds, unlike the old whole-file
  JSON+fsync rewrites). Bulk work (FTS reindex, migration) runs in
  `asyncio.to_thread` on its own connection and must commit in batches so
  the writer lock is never held for the duration of a bulk job.
- Persistence-layer APIs are unchanged **in M1 only**; §5.M2 makes one
  explicit, enumerated contract change (TurnContext) — that qualifier does
  not license any other API drift.

**Migration** (first start): import ALL legacy files — `sessions.json`,
`schedules.json`, `authorized_users.json`, `denied_users.json`,
`settings.json` — in **one transaction**; rename each to `*.migrated` only
after commit; import is idempotent (keyed upserts) so a crash mid-migration
is retried safely on next start. Never delete originals.

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
demand) · recall buffer = `Journal/` (distilled nightly into Wiki, §5.M4).

**Recorded decision — MemGPT/Letta as a library: adopted as a pattern,
rejected as a dependency.** The MemGPT codebase (and forks of it) is its own
agent runtime: API-key-driven, vector-DB-backed, with its own loop — each of
which contradicts a product invariant (CLI logins not API keys; P1 knowledge
in plain Markdown; "harness, not another agent"). Zilla implements the
paper's tier architecture natively, above. Known trade-off, consciously
deferred: FTS5 is keyword search, not semantic search. If recall quality
proves insufficient in practice, the upgrade path is a local embedding model
indexing the same Markdown into the same disposable `zilla.db` (P1 intact) —
a new sub-phase to be specced then, not speculatively now.

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
   `config.py` settings KV to thin store.py wrappers, per the §3.1
   threading/locking model (no in-memory caches; read-only connection for
   reads; short serialized write transactions). Public APIs unchanged in
   this sub-phase. Before writing the schema, **grep-enumerate every field
   currently read anywhere** (`bot.py`, `keyboards.py`, managers) and check
   the list against §3.1 — a field read by live code but absent from the
   schema is a blocking defect, not a cleanup.
   Import direction: `store.py` imports nothing from `config.py`;
   `config.py` imports `store.py` (prevents the circular import).
3. First-start migration per §3.1 (single transaction, idempotent upserts,
   rename after commit).
4. `install.py --doctor`: add DB checks (exists, schema version, WAL, write
   probe).
5. **Audit-debt burn-down** (open findings from `docs/dev/STATUS.md`, fixed
   here while these modules are open anyway): `compute_next_run` becomes
   timezone-aware via `zoneinfo` (local tz; DST-transition tests both
   directions; add `tzdata; platform_system=="Windows"` to requirements —
   stdlib zoneinfo has no IANA db on Windows); `_active_cancel` keyed by
   `(chat_id, uid)` instead of `chat_id` (kills cross-user cancel in
   groups); media ingest size cap (setting `max_media_mb`, default 50,
   oversize → one friendly refusal).
6. **Secrets hygiene for the new stores:** extend `_harden_file_perms` to
   `zilla.db*` (0600) and the `Memory/` tree (0700 dirs) — the DB carries
   full memory text in `mem_fts` and the Journal is intimate data. Nightly
   `VACUUM INTO` backup (`zilla.db.bak`, rotated, same perms) as the
   corruption-recovery story.
   **Accept:** full suite green; migration round-trip test (seed all five
   JSON files → migrate → API-identical reads, deny-list semantics intact);
   interrupted-migration test (kill mid-import → clean retry); concurrent-
   mutation test (two tasks mutate sessions 100×, no loss); reader-never-
   blocks test (bulk FTS write in flight, `get_setting` returns in < 50 ms);
   DST tests; cancel-keying and media-cap tests; perms test; doctor reports
   DB OK.

### M2 — Memory layout + injection
1. `memory.py`: ensure-tree-on-start with seeded templates (MEMORY.md
   template, empty HEARTBEAT.md, starter Wiki pages `People/owner.md`,
   `Projects/zilla.md`, `Systems/zilla-howto.md`); `read_core()`,
   `wiki_index()`, `journal_path(date)`.
2. **TurnContext plumbing (explicit contract change — the §4 scope guard is
   impossible without it):** `harness.wrap_prompt`/`build_preamble` take no
   uid today and are called from executor threads inside the backends. Add
   a `TurnContext` dataclass (`uid`, `role`, `is_owner`, `origin:
   user|schedule|heartbeat|approval`) threaded through the whole chain:
   `run_cli_async → _run_blocking → _dispatch_turn → run_cli/run_claude →
   wrap_prompt/build_preamble`, updating every call site — including the
   anti-hallucination retry, `_execute_schedule`, and
   `_run_approved_request`. P2's backend contract gains this one parameter,
   permanently. **Never** a module-level "current turn" global — with
   `concurrent_updates(True)` + a 4-thread executor pool, ambient state
   races and leaks the owner's memory into another user's prompt.
3. Harness injection exactly per §4 (gated on `ctx.is_owner`), including
   caps and first-run interview. Wiki index and skills index injections are
   line-capped (`max_index_lines`, default 100) with a
   `[index truncated — consolidate pages]` marker.
   **Accept:** injection unit tests (content, caps, interview line
   appears/disappears); **concurrent two-principal test** — owner turn and
   limited-user turn running simultaneously, limited user's prompt contains
   zero memory content (asserted against the actual wrapped prompt, not the
   handler input); live smoke — tell the bot a fact, `/new` session, ask it
   back: answered from MEMORY.md, and a Journal line exists.

### M3 — FTS5 search + memory git + quiet runs
(Ordering is deliberate: git history and quiet-run MUST exist before any
destructive automated job — the distillation lands in M4, not here.)
1. Indexer in `memory.py`: scan `Memory/**/*.md`, diff against `mem_seen`
   (mtime+size; a same-second same-size edit is a known blind spot — note
   it in code, or add a cheap content hash). Upsert changed docs into
   `mem_fts`. Runs at start and before each harness injection (the scan is
   ~ms; reindex only on change).
2. `memsearch.py` CLI: `python memsearch.py "query"` → top 8: `path:line` +
   2-line snippet, plain text (FTS5 has no line numbers — resolve `:line`
   with a post-match scan of the file). Exit 0 with "no results" when empty.
3. `memory.git_autocommit(context)`: if the Memory repo has changes after a
   CLI run / scheduled run, `git add -A && git commit -m "<context>"`.
   Failures log and never break replies. `git init` on first start if
   absent (author "Zilla <zilla@local>"; `.git` dir 0700).
4. **Quiet-run mechanism**, scoped to `system = 1` schedules ONLY (a user
   schedule whose legitimate output ends with the token must not be
   swallowed): if the stripped response **is or ends with** a line equal to
   `HEARTBEAT_OK` (case-insensitive) → deliver nothing, log only.
   **Accept:** index build/invalidation tests; memsearch finds a planted
   fact with correct `path:line`; autocommit fires on change and not on
   no-change; git failure injected → reply still delivered; suppression
   test incl. negative case (user schedule with the token still delivers).

### M4 — Nightly distillation + /memory + memory-change surfacing
1. Nightly distillation: `system` schedule (daily 03:30, owner uid) with
   prompt: *"Read yesterday's Journal file. Move durable facts into the
   right Wiki pages (create pages as needed), update MEMORY.md if a
   standing fact changed, then rewrite the journal entry down to its
   essentials. Reply HEARTBEAT_OK when done."* Created idempotently at
   start; pausable in `/settings`, not deletable. Runs in a **throwaway
   conversation** (fresh conv id, discarded after the run — never advances
   any session's conv id); raw pre-distillation journal text stays
   recoverable via memory git history (M3 — already live by now).
2. **Memory-change surfacing (the §12.9 injection-surface mitigation):**
   `git_autocommit` computes the per-run diff stat. When a run's inputs
   included untrusted content (document-ingest turn, browser-bearing turn)
   or the run was non-owner-originated, DM the owner one line: *"memory
   changed during this run: <files> (<commit>)"*. Deterministic, code-level
   (P5) — detection and visibility, not prevention.
3. `/memory` command (owner): MEMORY.md, today's journal, last 5 memory
   commits **with diff stats**, and `/memory diff` for the latest change.
   Read-only.
   **Accept:** distillation schedule exists exactly once after double
   restart; change-notice fires on a simulated document-turn memory write
   and NOT on an ordinary owner turn; /memory renders incl. diffs.

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
1. **`system`-schedule runner first:** the existing scheduler path
   BLOCKING-acquires the uid lock and applies a retry-ladder plus a
   "couldn't complete" DM on give-up — correct for user jobs, wrong for
   system jobs (a backend outage would DM the owner hourly all night, and
   a slept-through beat would catch up at wake and hold the owner's lock
   during their first morning message). `system = 1` schedules get a
   distinct runner: **try-acquire with skip**, NO retry ladder, NO give-up
   DM (failures are logged and surfaced only via H2's cooldown-gated
   alerts), and catch-up policy = skip (beats are periodic; a missed beat
   is worthless). Distillation (M4) migrates onto this runner; its
   catch-up = run once (a missed distillation is not worthless).
2. `heartbeat.py`: `system` interval schedule (default 30 min, setting
   `heartbeat_interval`, 0 = off). Deterministic pre-check: file missing /
   only template headers → **skip entirely, zero AI calls**.
3. Beat prompt: *"It is {now} ({tz}). Last beat: {last}. Read HEARTBEAT.md.
   Do anything due; update the file (stamps, checkoffs, prune stale items).
   If nothing needs the owner, reply HEARTBEAT_OK."* Beats run on the
   `system` runner (try-acquire/skip, no retry ladder, catch-up = skip).
   **On agy, beats reuse a persistent per-purpose "scratch" conversation**
   instead of minting a fresh one — a fresh agy conversation holds the
   GLOBAL new-conv lock (30 s bound, serializes ALL users' new
   conversations) and gets the full onboarding preamble; 48 beats/day of
   that is the slowest possible design. On claude/opencode (cheap fresh
   sessions) throwaway convs are fine. Quiet-run suppresses OK beats;
   memory autocommit picks up file edits.
4. **Throwaway-conv GC:** every throwaway/scratch conv id is recorded;
   after the run (or on a startup sweep) agy brain dirs unreferenced by
   any session and older than 7 days are deleted. Without this, beats +
   distillation + fallback turns leak ~1,500 orphaned brain dirs/month and
   progressively slow agy's snapshot-diff conv detection.
5. Harness gains one protocol line: *"When the owner asks you to keep an
   eye on / remind / follow up on something recurring, add it to
   HEARTBEAT.md."*
   **Accept:** empty-file skip test; beat fires and edits file in live
   smoke; OK beats silent; "watch X" via chat lands in HEARTBEAT.md.

### H2 — Health probes + assisted re-login
1. `health.py`: deterministic probes on their **own asyncio timer,
   independent of `heartbeat_interval`** (heartbeat 0 = off must not kill
   the probes — R2's fallback eligibility depends on probe freshness):
   disk ≥ 500 MB free, `zilla.db` writable, backend binaries on PATH,
   backend login freshness (agy: probe settings file / known error
   patterns; claude: `claude -p "ping"` cheap probe at most 1×/6 h;
   patterns centralized in `health.py`). Probe results cached with
   timestamps; a stale/missing result triggers an on-demand probe when R2
   needs it.
2. On failure: fix silently if code can (rebuild index, rotate logs). If a
   human is needed (login expired): ONE DM — what broke, the **exact
   recovery steps** — per-kind cooldown 6 h; never repeats while
   unresolved-and-acknowledged. **Honest ceiling on "assisted" re-login:**
   both agy and claude authenticate via browser OAuth with no verified
   token-paste path — so the DEFAULT deliverable is detect + precise
   instructions. Relay-assisted login (paste a token/code in chat) is
   implemented ONLY for backends where the executor verifies such a path
   exists on the installed version; otherwise document the ceiling in
   STATUS.md and move on. Do not build speculative login automation.
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
   prefer the fastest backend in the chain for this one turn — never
   advancing the session's conv id (I-CONV). **agy is excluded from the
   fast profile**: a fresh agy conversation costs the global new-conv lock
   plus the full onboarding preamble, making the "fast" path slower than
   just continuing the session. Fast profile = claude/opencode throwaway
   conv, or on an agy-only chain, a persistent per-uid scratch
   conversation (H1's GC covers both).
   `normal` — everything else, full injection, session backend.
2. Misclassification safety: if a `trivial` reply comes back empty/error,
   silently rerun as `normal`. Router decisions logged.
   **Accept:** classifier table-driven tests (≥ 30 cases); fast-profile
   turn does not mutate session conv id; rerun-on-empty test.

### R2 — Fallback chain
1. Setting `backend_chain` (ordered, default = active backend + others
   detected on PATH). **A chain entry is eligible only if its last health
   probe (H2) showed a fresh login** — a binary on PATH that isn't logged
   in would burn the retry or hang on a login prompt; skip it and log why
   (probe freshness per H2 — stale probe ⇒ on-demand probe, never a dead
   chain). **Trigger discipline — fallback fires ONLY on error channels:**
   (a) backend-reported errors (claude `is_error`/non-zero exit, spawn
   failure) or an empty response after the existing retry; (b)
   `detect_limit` ONLY when the response is error-shaped (short/structured
   error), NEVER pattern-matched against arbitrary answer text — today's
   `detect_limit` substring-matches "quota"/"429"/"overloaded" anywhere,
   and under R2 a correct long answer *about* rate limits would be thrown
   away and re-answered context-free on another backend. Required negative
   test: a long answer containing "quota" and "429" does not trigger
   fallback. On a qualifying trigger:
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
3. **Approval gate — honest mechanics:** the CLI executes tools with full
   host privileges and no in-CLI sandbox exists (AI_CONTEXT trust model),
   so Zilla cannot *physically* stop a running agent from executing a
   file. What Zilla CAN deterministically control is **what it
   advertises**: the skill index injects ONLY skills present in
   `skill_approvals` — **every** skill, `.md`-only included (a SKILL.md is
   injected instructions; an unapproved one written out-of-band — by the
   agent, or by injected content — must never reach the index just because
   the file exists). The approval hash covers `SKILL.md` + all script
   bytes; hash mismatch ⇒ auto-revoke (drops from index) + a one-line
   owner notice. The ✅ proposal flow (item 2) is the UI for creating an
   approval row; the index gate is the enforcement (P5) — the model's
   cooperation is not part of the security story. Approval UI: `/skills`
   menu (list, view, approve, disable).
4. **Legacy skills path — settled:** the existing
   `harness.skills_summary` injection (backend-native `~/.claude/skills` /
   agy skill dirs) **coexists** — those are backend-level, owner-installed
   artifacts outside Zilla's management, and Zilla does not gate them. Two
   changes only: it becomes owner-turn-only (rides M2's TurnContext, same
   scope guard as memory), and `/skills` labels the two sources
   distinctly. `Memory/Skills/` is the managed, gated system going
   forward.
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
   otherwise start the engine in-process. **Lifecycle, specified:** on
   engine start, if the socket file exists, try connect — connection
   refused ⇒ stale socket ⇒ unlink and bind (a live daemon answers and the
   new process attaches instead). The single-instance lock **moves from
   `bot.main()` into the engine facade during G1** — today a TUI-started
   in-process engine would never touch it; after G1 exactly-one-engine
   holds for every entry point. The existing WebBridge shims are NOT the
   transport — they're status-only and stay untouched. Windows keeps
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
   gated on H2 login-freshness probes (own timer, on-demand refresh).
9. **RECORDED DECISION — memory files are a prompt-injection surface.**
   The agent maintains memory with the same tools it uses on untrusted
   content (web pages, ingested documents); a malicious page can instruct
   it to write a persistent "standing instruction" that is then injected
   into every future owner turn, surviving /new, backend switches, and
   restarts. Given the trust model (no in-CLI sandbox), **prevention is
   out of scope; the mitigation is deterministic detection + visibility**:
   M4's memory-change surfacing (DM on memory writes during untrusted-
   input or non-owner runs) and `/memory diff`. The owner reviews; git
   history makes reverts one command.
10. **Memory git makes deletions permanent history** — an accidentally
   journaled secret survives editing. Mitigation: the memory protocol
   already forbids secrets in memory files; additionally `/memory purge
   <pattern>` (owner-only) is specified in M4's scope as a documented
   `git filter-repo` wrapper — destructive, confirm-gated, exact steps in
   MANUAL.md.

## 13. Execution order & progress

Execute strictly top-to-bottom. Check items off here (this file) as they land.

- [ ] M1 SQLite store + migration
- [ ] M2 Memory layout + injection
- [ ] M3 FTS5 + memory git + quiet runs
- [ ] M4 Nightly distillation + /memory + change surfacing
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
