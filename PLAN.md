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
- **P7 — Headless-first: 100% operable from Telegram.** The runtime is a
  display-less server. EVERY operation, error, and recovery path must be
  performable from Telegram (with the TUI-over-SSH as the second surface).
  No feature may assume a local display, and nothing may be console-only:
  any state a console would show must be reachable remotely (`/health` runs
  the doctor probes on demand). A feature whose failure mode ends with
  "SSH in and look at the logs" is incomplete — the error must arrive in
  Telegram as one calm sentence with its recovery action.

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
                      │  tasks.py    — background task lane        │
                      │  health.py   — probes + re-login + update  │
                      │  connectors  — per-backend MCP/native mgmt │
                      │  voice/      — wake-word satellite (V3)    │
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
                        session_name TEXT,           -- optional session binding (bot.py reads it)
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
  HEARTBEAT.md               ← agent-owned proactive checklist (see §8)
  Wiki/                      ← archival memory, one page per topic
    People/  Projects/  Preferences/  Places/  Systems/
  Journal/
    2026-07-17.md            ← one file per day, newest entries appended
  Skills/
    <slug>/SKILL.md          ← learned skills (see §11); scripts live beside it
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
2. **Memory-change surfacing (the §16.9 injection-surface mitigation):**
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

## 6. Phase K — Relational graph memory (executes after M4, before Phase H)

The wiki grows a **property-graph layer**: entities are wiki pages, typed
relations are lines inside those pages, and a deterministic indexer derives a
queryable graph in `zilla.db`. Zep/Graphiti-class capability — typed edges,
temporal validity, entity resolution, provenance — with zero new
dependencies, zero extra AI calls in the pipeline, and P1 fully intact (the
graph tables are disposable; the pages are the truth).

**Ontology (full, from day one — owner decision):** node types `person`,
`org`, `place`, `project`, `topic`. Seed relation verbs: `knows`,
`family_of`, `works_at`, `member_of`, `located_in`, `part_of`,
`involved_in`, `supplies` — free verbs are allowed (normalized to
`lower_snake`); the indexer NEVER fails on an unknown verb.

**Entity page format** (extends §3.2; parse rules are exact):

```
# Ramesh Kumar
Cousin; the person to call for anything passport-related.   ← line 2 = bio line
- type:: person
- aliases:: Ramesh, my cousin, passport guy
- phone:: +91 …                                             ← any other key:: value = attribute
## Relations
- works_at:: [[Passport Office]] (since 2024-01)
- family_of:: [[Suresh]]
- worked_at:: [[XYZ Corp]] (2020 .. 2023-06)                ← closed interval = superseded fact
```

`key:: value` lines and `verb:: [[Target]] (dates?)` lines are the entire
grammar. `[[Wiki-links]]` anywhere in prose also count as untyped `mentions`
edges (Obsidian semantics). A `[[Target]]` with no page yet becomes a
**ghost node** — rendered hollow in views, and a curiosity trigger.

### K1 — Graph schema + indexer
1. Schema (in `zilla.db`, rebuildable):
   `nodes(id, path UNIQUE, type, title, bio, is_ghost)` ·
   `aliases(alias, node_id)` ·
   `edges(src, rel, dst, valid_from, valid_to, provenance)` — provenance =
   `path:line` of the source relation line; open `valid_to` = currently
   true (bi-temporal: facts are superseded by closing the interval, never
   deleted — M3's indexer extends to parse the grammar above on the same
   mtime-diff cycle).
2. `memgraph.py` CLI (agent-callable, like memsearch): `neighbors <name>
   [--hops 2]`, `path <a> <b>` (how are these connected), `find <type>
   [--near <name>]`. Traversal = recursive CTEs; current-facts-only by
   default, `--history` includes closed intervals.
   **Accept:** parser golden tests (grammar above, incl. ghost nodes, date
   intervals, alias multi-match); index rebuild-from-scratch equals
   incremental result; CTE traversal tests (2-hop, path, cycles safe);
   unknown verbs indexed not rejected.

### K2 — Turn-time entity linking + neighborhood injection
1. Deterministic alias scan of each owner message (longest-match against
   `aliases`, case-insensitive, word-bounded). For each hit (cap 3 nodes),
   inject a compact **local graph card** into the turn: bio line + current
   edges 1 hop out (2 hops for the single strongest hit), ≤ 25 lines
   total, with a `[via graph]` header. This is how "I need to renew my
   passport" surfaces `Passport Office —[works_at]— Ramesh` *before* the
   agent even reasons.
2. Harness protocol addition (owner turns): *"You have a relational memory.
   To answer 'whom do I know at/for X' or plan anything involving people,
   places, or organizations, run memgraph.py. When the owner shares a new
   fact about an entity, update that entity's page (create it from the
   template if missing — every person gets a bio line); record relations
   as `verb:: [[Target]]` lines; close an interval when a fact is
   superseded, never delete the line."*
   **Accept:** alias-scan unit tests (word boundary, longest match, cap);
   injection golden test; live smoke — mention a stored person by a
   nickname alias, the reply reflects graph knowledge without an explicit
   memory question.

### K3 — Curiosity loop (one question, when relevant — owner decision)
1. Deterministic gap detection at index time (zero AI): `person` node with
   no contact attribute; ghost node referenced from ≥ 2 pages; `org`/
   `place` with no `located_in`. Gaps land in a `curiosity(node_id, gap,
   asked_at)` table.
2. Enforcement is code, not model judgment (P5): the harness includes AT
   MOST ONE pending curiosity question per conversation, and only when the
   gap's node was activated by K2's alias scan in the current turn
   (relevance gate). Phrasing is the agent's; the *permission to ask* is
   Zilla's. Owner answers flow through the normal memory protocol (agent
   updates the page; next index cycle clears the gap). A question asked
   and unanswered is cooled down 7 days.
   **Accept:** gap-detection tests; one-question-per-conversation and
   relevance-gate tests; cooldown test; live smoke — mention a new person
   twice, get exactly one polite "should I save his contact?" follow-up.

### K4 — Graph views (the flabbergast moment)
1. `/graph` (Telegram, owner): generates a **self-contained single-file
   HTML** (inline JS/CSS, no CDN — must open offline on a phone) rendering
   the current graph from a JSON snapshot embedded in the file. Obsidian-
   grade features, specified: canvas force-directed simulation (repulsion +
   spring + centering, ~60 fps for ≤ 2k nodes); node size ∝ degree; color
   by node type (legend); ghost nodes hollow; **global view + local view**
   (tap a node → its N-hop neighborhood, N slider 1–3); filters (by type,
   by search box, orphans on/off); tap a node → side panel with bio,
   attributes, current relations, and "superseded" history collapsed.
   Sent via `safe_send_file` to the Outbox. `/graph <name>` opens directly
   in local view on that node.
2. TUI: Phase T's screen list gains **Graph** — a local-graph explorer
   (adjacency tree around a chosen node, arrow-key navigation, enter →
   open the page); the full visual stays HTML (terminals can't do force
   layouts honestly).
   **Accept:** HTML generation golden test (valid, self-contained, embeds
   N nodes); renders offline in a plain browser (live smoke on phone);
   local-view + filter behavior verified in smoke; 2k-node synthetic graph
   stays interactive.

**Phase K definition of done:** live smoke demonstrating the full loop —
owner mentions a new person + workplace in normal chat → pages appear with
bio lines and typed relations (M-git commits show it) → days later, owner
states an intent ("I need to sort out my passport") → the reply proactively
surfaces the right person → `/graph` on the phone shows the connection
visually → exactly one curiosity question was asked along the way.

## 7. Phase U — Generative UI & design system (executes after K, before H)

**The idea:** the agent should be able to answer with *interface*, not just
text — cards, tables, tappable buttons, real contact cards — the way cloud
chat UIs do. **The mechanism (P5-compatible):** formatting is never
hard-coded per feature and never left to raw model output. The agent emits a
small declarative block; Zilla validates it against a strict schema and
renders it with native Telegram widgets. The agent decides *when* and *what*;
Zilla's code decides *how* and enforces *limits*. Invalid block → the block
is stripped and the text still delivers (P4: a bad card never kills a reply).

### U1 — The ZUI protocol (declarative block → native widgets)
1. Grammar: the agent may embed at most 2 fenced blocks per reply:
   ````
   ```zui
   {"kind": "buttons", "items": [
     {"label": "Book the ticket", "say": "book the 6pm ticket"},
     {"label": "Open site", "url": "https://…"}]}
   ```
   ````
   Kinds (v1, exhaustive): `card` (title, subtitle, fields[], footer —
   rendered as clean HTML with consistent typography), `table` (headers +
   rows — monospace `<pre>` with column alignment; auto-degrades to
   field-per-line beyond phone width), `contacts` (refs to graph entities —
   rendered via Telegram `send_contact`, i.e. REAL contact cards with
   tappable numbers and "save to contacts"; numbers resolved from the
   entity page's `phone::` attribute, never free-typed by the model),
   `buttons` (rows of inline buttons), `location` (lat/lon or a place
   entity → Telegram venue card).
2. Button verbs (whitelist, exhaustive): `say` (tap ⇒ the text is
   submitted as the tapping owner's next message through the normal
   pipeline — this is what makes replies feel alive: the agent offers next
   actions as taps), `url` (http/https only — existing href guard),
   `copy` (tap ⇒ value sent back as monospace for long-press copy).
   No other verb exists; unknown verbs are dropped at validation.
3. Deterministic validation in `formatter.py`: JSON schema, caps (≤ 2
   blocks, ≤ 8 buttons, label ≤ 32 chars, table ≤ 8×20), scheme
   whitelist, identity check on `say` callbacks (only the addressed uid's
   taps are accepted — reuse the existing callback-identity pattern),
   `contacts` only resolvable via graph nodes. Everything else stripped.
   **Accept:** schema/caps/verb-whitelist tests incl. malicious blocks
   (javascript: url, forged callback uid, free-typed phone number — all
   rejected); golden renders for each kind; invalid-JSON block → text
   still delivered.

### U2 — Teach the agent (protocol, not hard-coding)
1. Harness gains a compact ZUI reference with 3 worked examples and usage
   guidance: options/next-steps → `buttons`; a person's reachable info →
   `contacts`; comparisons/lists of records → `table`; a structured
   answer (booking, plan, summary of an entity) → `card`. Plain prose
   stays plain — no widget for widget's sake.
2. The "get me contacts" loop lands here end-to-end: owner asks for a
   person/plumber/whoever → agent runs memgraph/memsearch → replies with
   `contacts` block(s) → owner taps → phone's native call/save sheet.
   **Accept:** live smoke — "send me Ramesh's contact" yields a real
   tappable contact card; "what should we do about X" yields tappable
   next-step buttons that actually submit.

### U3 — Design system (professional, Apple-grade restraint)
1. `docs/dev/STYLE.md` — the visual constitution for every surface
   (Telegram menus, ZUI renders, TUI later): typography hierarchy (bold
   title / plain body / italic captions), ONE accent emoji per screen as
   an icon — never emoji confetti, sentence case everywhere, primary
   action first and `✕ Close` always last-row-right, consistent spacing
   lines, no exclamation marks in UI copy, error copy = one calm sentence
   + one action. Numbers right-aligned in tables. Every menu fits one
   phone screen without scrolling.
2. Refactor pass: existing `/settings`, `/sessions`, `/schedules`,
   `/skills`, `/memory` menus and all ZUI renderers audited against
   STYLE.md; deviations fixed. STYLE.md is binding for every later phase
   (T inherits it wholesale).
   **Accept:** style-lint checklist applied to every menu (documented in
   STATUS.md with before/after screenshots in the live smoke).

### U4 — Presence (kill the startup blast)
1. The current `⚡ Zilla is online (vX) / Model / Time` message on every
   start (bot.py post_init) is REMOVED. Replacement — a **pinned status
   card**: one message in the owner chat, pinned once, then **edited in
   place** (edits generate no notifications): `● Online · <backend> ·
   v<X>` / `○ Offline since <t>` (best-effort edit on clean shutdown),
   plus last-heartbeat time. Glanceable always, noisy never.
2. An actual new message is sent ONLY when it carries information: first
   install ("Hi — I'm here."), after `/update` ("Updated to vX ✓", one
   line), or on recovery from unexpected downtime > `downtime_notify_min`
   (default 60 min; the catch-up summary rides the same single message).
   Routine restarts are silent. `/status` shows the card's content on
   demand.
   **Accept:** no-message-on-clean-restart test; single-message-after-
   downtime test; pinned-card edit (mocked bot) test; STYLE.md-compliant
   copy.

## 8. Phase H — Heartbeat & self-healing

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
4. **GC & retention sweeps** (one housekeeping pass, deterministic):
   (a) throwaway/scratch convs — every such conv id is recorded; after
   the run (or on a startup sweep) agy brain dirs unreferenced by any
   session and older than 7 days are deleted. Without this, beats +
   distillation + fallback turns leak ~1,500 orphaned brain dirs/month
   and progressively slow agy's snapshot-diff conv detection.
   (b) **media retention** — Inbox/Outbox files older than
   `media_retention_days` (default 30, 0 = keep forever) are deleted by
   the same sweep; the deletion is logged, never announced (P4). Anything
   worth keeping graduates out of Inbox: the agent (on request, "keep
   this") copies it to `Memory/Media/`, which is retention-exempt,
   git-tracked, and rides C3's cloud backup.
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
4. **Login console relay (P7 — checked against current code: does NOT
   exist yet; the interactive.py bridge is agent-protocol-level and
   cannot drive a CLI login prompt).** Build it deterministically:
   `PtyProcess` gains a `write()` (os.write to the PTY master on POSIX /
   winpty write). `/login <backend>` (owner, also offered as the button
   on a login-expired alert) spawns that backend's login command under a
   PTY, streams its output into the chat (URLs arrive as tappable links),
   and forwards the owner's next reply into the PTY + Enter — token
   pasted, authenticated, no display needed. Replies in this mode get
   OTP-grade handling: deleted from chat after use, never logged,
   redaction filter active. Timeout + cancel button; zero model
   involvement end to end.
5. `/health` (owner): runs the full probe suite + doctor checks on demand
   and renders one ZUI card — the remote equivalent of
   `install.py --doctor`, per P7.
   **Accept:** probe unit tests with injected failures; cooldown test;
   PTY write + relay tests (mocked login binary: URL streamed, token
   forwarded + newline, chat message deleted, nothing logged); /health
   card renders; live smoke of one full agy re-login round-trip via
   Telegram only — no SSH, no display (documented in STATUS.md).

### H3 — Linux service deployment
1. `install.py --service`: writes + enables a systemd **user** unit
   (`~/.config/systemd/user/zilla.service`, `Restart=on-failure`,
   `WantedBy=default.target`, lingering hint printed). Mac dev keeps
   `./start.sh`; nothing Windows breaks.
2. Doctor: service status check on Linux.
   **Accept:** unit file golden test; doctor reports service state; live:
   reboot → bot up, missed schedules caught up (existing reconcile).

### H4 — Self-update with rollback
1. `/update` (owner) + `zilla update`: deterministic pipeline — record
   current commit → `git fetch && git pull --ff-only` on the Zilla repo →
   `pip install -r requirements.txt` (venv) → run store.py migrations →
   restart service → post-restart `--doctor`. **Doctor fails ⇒ automatic
   rollback**: checkout recorded commit, reinstall, restart, doctor again,
   and DM the owner one line with what failed. DB is backed up
   (`VACUUM INTO`, M1.6) immediately before migrations. Never auto-updates
   on its own — owner-triggered only (a heartbeat line may *mention* that
   an update is available; H2 probe checks `git fetch --dry-run` 1×/day).
   **Accept:** update pipeline test with a simulated bad migration →
   rollback restores prior commit + DB; doctor-gate test; live smoke of
   one full update on the Linux service.

## 9. Phase B — Background tasks & incognito (executes after H)

**The gap this closes:** every turn holds the per-uid lock, so a 20-minute
research job freezes the owner's chat for 20 minutes — the deepest possible
violation of "never feels dead". Background tasks get their own lane.

### B1 — Background lane
1. `tasks.py` + table: `tasks(id, uid, prompt, status
   queued|running|done|failed|canceled, progress, result, created_at,
   started_at, finished_at)`. Each task runs in its OWN named session
   (`task:<id>`, backend-tagged per I-CONV, GC'd with H1's sweep) under a
   task-scoped lock — NOT the owner's chat lock. The chat stays free. The
   agy global new-conv lock still applies at spawn (unavoidable, brief).
   Concurrency cap `max_bg_tasks` (default 2, setting) — queued beyond
   that; quota protection is the cap + usage counters.
2. Creation, deterministic (P5): `/bg <prompt>` command; or the agent —
   when the owner *asks* for background work conversationally — ends its
   reply with `BG_TASK: <prompt>` (marker detected/stripped exactly like
   `SKILL_PROPOSAL`, rendered as a ZUI confirm button; owner taps ⇒ task
   created). The model cannot spawn work without a command or a tap.
3. Completion: result DM'd as a ZUI card (title, duration, result body /
   FileOut); failure = one calm sentence + a "retry" button. Cancel via
   `/tasks` buttons (per-task `cancel_event`, I-CANCEL semantics).
4. `/tasks` board (ZUI): running (with live progress line + cancel),
   queued, last 5 finished. TUI gets a Tasks screen in Phase T.
   **Accept:** lock-independence test (bg task running, chat turn completes
   concurrently); cap/queue test; cancel test; marker detect/strip/confirm
   test; live smoke — start a long bg task, keep chatting, get the result
   card.

### B2 — Incognito sessions
1. `/new incognito` (session flag in `sessions`): the harness omits the
   journal/memory instructions AND the memory protocol for those turns;
   **code enforcement**, not model promise: Memory-tree mtime snapshot
   around each incognito turn — any change ⇒ `git restore` from the memory
   repo + one-line owner notice. No graph cards injected, no curiosity
   questions, no FTS of anything said.
2. Honest ceiling, documented in MANUAL.md: the backend CLI still keeps
   its own conversation transcript (agy brain dir / claude session) — 
   incognito guarantees *Zilla's memory* is untouched, and the session's
   conv dir is deleted on `/close` (or H1 GC).
   **Accept:** enforcement test (simulated memory write during incognito ⇒
   reverted + notice); injection-absence test; close-deletes-conv test.

---

## 10. Phase R — Router & fallback

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
3. **Adaptive effort controller (owner-directed, never model-trusted):**
   per-turn effort ∈ {fast, standard, deep}, resolved by deterministic
   rules in priority order — the model NEVER decides its own effort
   (P3/P5; a model grading its own homework under-thinks hard problems):
   (a) **owner emphasis wins absolutely**: markers like "think hard /
   deeply / carefully / properly", "take your time", or an explicit
   `!deep` prefix ⇒ `deep`; (b) `trivial` class ⇒ `fast`; (c) everything
   else ⇒ `standard`. Effort maps to backend+model per a `effort_map`
   setting (defaults: fast = cheapest chain model, e.g. claude haiku;
   standard = session backend as configured; deep = the strongest model
   among per-invocation-flag backends ONLY, e.g. claude opus — an
   `effort_map` entry naming an agy model is invalid and rejected at
   settings-write time, per rule 4 below). `deep` turns get a "thinking
   deeply…" progress note (P4).
4. **agy model-switching constraint (recorded reality):** agy's active
   model is a GLOBAL display string in its settings file — per-turn
   switching would race with every other agy terminal on the machine (the
   owner has personally hit this headache). Rule: effort-based model
   switching happens ONLY on backends with a per-invocation model flag
   (claude `--model`, opencode). On agy, effort routing changes *which
   backend* runs the turn, never the agy model mid-session; agy model
   changes remain an explicit owner action in `/settings` (existing
   atomic write + read-back). Document in MANUAL.md that external agy
   terminals share agy's model setting by agy's design — Zilla won't
   silently mutate it.
   **Accept:** classifier table-driven tests (≥ 30 cases); effort-
   priority tests (emphasis beats trivial-class; `!deep` on a one-word
   message still goes deep); effort_map dispatch test; fast-profile turn
   does not mutate session conv id; rerun-on-empty test; NO test may
   assert an agy model write during routing.

### R2 — Fallback chain
1. Setting `backend_chain` — ordered; **default order is the owner's
   declared priority: `agy → opencode → claude`**, filtered to what is
   detected on PATH (the active backend, whatever it is, always leads its
   own session's turns). `effort_map` defaults follow the same reality:
   fast/deep pick the cheapest/strongest model among the per-invocation-
   flag backends actually present (opencode first, claude when enabled).
   **A chain entry is eligible only if its last health
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

## 11. Phase S — Skills from chat (ask-first)

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

## 12. Phase C — Connectors & the portable brain (executes after S)

### C1 — Brain export / import
1. `zilla export [--encrypt] [path]` → one archive: `Memory/` (incl. its
   `.git`), a `System/state-snapshot.json` (settings/schedules/users/
   curiosity dumped from the DB — NOT the DB file; the DB is
   operational+index and rebuilds), `.env.template` (structure, NO
   secrets), and `Media/` files under `export_media_max_mb` (default 10)
   each. `--encrypt` = AES-256 via openssl with a relay-prompted
   passphrase.
2. `zilla import <archive|dir>` (and installer/TUI onboarding step
   "Restore from a backup?"): restore files → import snapshot →
   rebuild FTS + graph indexes from the files (this is P1's proof — the
   entire brain reconstitutes from Markdown + one JSON).
   **Accept:** export→wipe→import round-trip equals original (graph query
   results identical); encrypted round-trip; snapshot excludes secrets.

### C2 — Connectors screen (MCP + native, per-backend)
1. Reality, recorded: connectors live at the BACKEND level and differ —
   agy ships native connectors (Google Workspace etc.) plus MCP config;
   claude manages MCP via `claude mcp add/list/remove` / `.mcp.json`;
   opencode declares MCP servers in its JSON config. Zilla does not proxy
   or re-implement any of this — it MANAGES the per-backend configs
   deterministically (write + read-back, like `set_model`; exact
   file/flag shapes verified against installed versions, evidence-first).
2. `/settings → Connectors`: an availability matrix (connector × backend:
   native / via MCP / unavailable), add/remove/enable per backend, secrets
   (MCP server keys) collected via the interactive relay — written only
   to the backend's own config, never logged, never in memory files.
   **Owner-approval gate (P5):** adding any connector/MCP server requires
   an explicit ✅ confirm card showing exactly what will run — MCP servers
   are third-party code and a prompt-injection/supply-chain surface
   (→ §16.11).
3. Router awareness (small, later-proof): `connector_hints` map — a turn
   that clearly needs a connector only one backend has (e.g. Workspace on
   agy) is routed to that backend for that turn, same throwaway-conv rules
   as fallback.
   **Accept:** config write+read-back tests per backend (mocked binaries);
   matrix renders truthfully from configs; approval-gate test (no write
   without confirm); secrets never appear in logs test.

### C3 — Cloud backup + bootstrap-from-cloud
1. **GitHub is the canonical cloud backup** (recorded decision): the
   memory repo (M3) gains an optional remote — a PRIVATE repo the owner
   supplies (URL + PAT via relay; PAT into `.env`, 0600). Auto-push after
   autocommit, rate-limited (≥ 10 min apart) + always after nightly
   distillation. C1's `state-snapshot.json` is committed nightly into the
   repo → **the entire brain is restorable from the repo alone**. Media
   under the size cap included; oversize files listed in a
   `Media/SKIPPED.md` (LFS = conscious deferral). Plaintext-in-private-
   repo vs encrypted-archive tradeoff documented in MANUAL.md; the
   `--encrypt` export (C1) is the answer for the cautious.
2. Bootstrap-from-cloud: onboarding (installer + TUI) offers "Restore
   from GitHub" → URL + PAT → clone → C1 import path → reindex → the new
   machine wakes up with the owner's full memory, graph, and schedules.
3. Google Drive / Workspace: **not** the canonical backup path (no
   deterministic tool without new deps) — recorded decision. Drive is
   reachable for file operations through C2 connectors on backends that
   have it; a `drive_backup` skill can exist later as an agent-level
   convenience, never as the integrity-bearing path.
   **Accept:** push rate-limit test; nightly snapshot commit test; live
   smoke — push to a real private repo, clone on a clean dir, import,
   graph query matches; PAT never logged.

## 13. Phase G — Gateway extraction, then Phase T — Terminal app

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
   **Schedules**, **Memory** (MEMORY.md + journal + commits),
   **Graph** (K4's local-graph explorer), **Skills**
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

## 14. Phase V — Voice (offline STT → voice replies → wake-word satellite)

### V1 — Offline transcription
1. `faster-whisper` optional dependency (`pip install zilla[voice]` /
   requirements-voice.txt). Setting `transcribe = auto|local|online`
   (default `auto` = local if importable, else current online path).
2. `media.transcribe_audio` branches: local path uses model `small`
   (int8, CPU) — lazy first-use download with a one-time progress note;
   result format identical to online path. Doctor reports voice mode.
   **Accept:** branch tests with mocked model; live smoke: voice note on
   Linux transcribed offline (network blocked) and answered.

### V2 — Voice replies (local TTS)
1. Piper TTS (local, fast, no keys) as another `zilla[voice]` extra.
   Setting `voice_replies = auto|always|off` (default `auto`: voice note
   in ⇒ voice reply out, plus the text). Rendered from the reply text
   AFTER ZUI blocks are stripped; long replies voice only a spoken-length
   summary line + full text below (setting `tts_max_secs`, default 45).
   **Accept:** mode-matrix tests; strip-before-speak test; live smoke —
   voice note in, voice answer back in Telegram.

### V3 — Wake-word satellite (`zilla-voice`, on the Linux box)
1. Always-on local loop, zero cloud: **openWakeWord** with a wake word
   trained on the OWNER's voice — guided enrollment (`zilla voice-train`
   or via Telegram: "record your wake phrase N times", accepts voice
   notes), threshold tunable, chime on wake so false accepts are audible,
   not silent. Silero VAD for end-pointing.
2. Loop: wake → chime → capture until silence → V1 STT → engine as an
   owner turn on the active session (normal pipeline: memory, graph,
   router — the voice is just another connector, per the Gateway
   principle) → V2 TTS out through the speaker.
3. Half-duplex, honestly specified: mic input is ignored while speaking
   EXCEPT the wake-word detector stays live — saying the wake word
   interrupts playback (barge-in). No full-duplex conversation claim.
4. Priority: its own systemd unit with `Nice=-5` + high `CPUWeight` (and
   rtkit for the audio thread when available) so wake→response stays
   snappy under load; the main Zilla service is NOT reprioritized.
   Runs only where a mic/speaker exists; auto-disabled headless.
   **Accept:** wake-detector unit tests on recorded fixtures (owner
   samples + negatives); barge-in test; end-pointing test; live smoke —
   wake word from across the room, spoken answer, chat transcript of the
   exchange visible in Telegram session history.

---

## 15. Cross-cutting engineering rules

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

## 16. Risk register (with mitigations already in the plan)

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
7. **TUI↔daemon transport under-engineered** → §13 specifies the Unix-socket
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
   MANUAL.md. With C3's remote, purge must force-push and the MANUAL
   documents that a leaked-then-pushed secret is rotate-not-purge.
11. **MCP servers / connectors are third-party code** — a supply-chain and
   prompt-injection surface inside the backends. Mitigation: C2's
   owner-confirm gate on every addition, per-backend config isolation,
   secrets via relay only, and M4's memory-change surfacing already
   covers the "connector content writes memory" path.
12. **Background tasks can burn quota unattended** → `max_bg_tasks` cap,
   queueing, usage counters per backend, and creation only via command or
   explicit owner tap (no model-initiated spawning).
13. **Wake-word false accepts** (satellite hears TV, triggers a turn) →
   owner-trained model + tunable threshold + audible chime + everything it
   heard lands visibly in the session transcript — never a silent action.

## 17. Execution order & progress

Execute strictly top-to-bottom. Check items off here (this file) as they land.

- [ ] M1 SQLite store + migration
- [ ] M2 Memory layout + injection
- [ ] M3 FTS5 + memory git + quiet runs
- [ ] M4 Nightly distillation + /memory + change surfacing
- [ ] K1 Graph schema + indexer
- [ ] K2 Entity linking + neighborhood injection
- [ ] K3 Curiosity loop
- [ ] K4 Graph views (/graph HTML)
- [ ] U1 ZUI protocol (cards/tables/contacts/buttons)
- [ ] U2 Agent ZUI education + contacts loop
- [ ] U3 Design system (STYLE.md + menu refactor)
- [ ] U4 Presence (pinned status card, silent restarts)
- [ ] H1 Heartbeat loop
- [ ] H2 Health probes + assisted re-login
- [ ] H3 systemd service
- [ ] H4 Self-update with rollback
- [ ] B1 Background task lane + /tasks
- [ ] B2 Incognito sessions
- [ ] R1 Triage router + effort controller
- [ ] R2 Fallback chain
- [ ] R3 opencode adapter
- [ ] S  Skills from chat
- [ ] C1 Brain export/import
- [ ] C2 Connectors screen (MCP/native)
- [ ] C3 Cloud backup + bootstrap-from-cloud
- [ ] G1 Engine facade extraction
- [ ] T1 Terminal app
- [ ] V1 Offline transcription
- [ ] V2 Voice replies (local TTS)
- [ ] V3 Wake-word satellite
