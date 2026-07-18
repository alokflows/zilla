# ZILLA — HANDOFF

> **SUPERSEDED PLANNING NOTICE (2026-07-17 night):** the work order is now
> [`PLAN.md`](PLAN.md) — a from-scratch, adversarially-reviewed blueprint
> (Fable + owner, 2026-07-17) covering Phases M (SQLite + Markdown memory
> foundation) → H (heartbeat/health) → R (router/fallback) → S (skills) →
> G/T (engine facade + terminal app) → V (voice). **Read PLAN.md first, then
> come back here for status only** — this file no longer carries the plan
> (§6 below is kept for historical trap/decision context only; do not follow
> its phase list). PLAN.md was written on a branch that forked BEFORE
> everything below §"LIVE STATUS BOARD" landed, so its own phase list
> doesn't know P1.5/TUI/CLI/approvals already shipped — the RECONCILIATION
> note in the status board maps PLAN.md's phases onto what already exists
> here. PLAN.md's own architecture decisions (§1-2, especially adopting
> SQLite) are settled — don't reopen them, including against this file's
> older "SQLite rejected for now" note below (superseded).

> **If you are an AI session reading this: this document is your complete brief.**
> Read it fully, then jump to the [LIVE STATUS BOARD](#live-status-board) and
> continue from the first unchecked item. Do not re-derive anything documented
> here — the codebase analysis below comes from a complete read of all ~10k
> lines and is current as of 2026-07-16.

---

## 1. HOW TO USE THIS DOCUMENT (session protocol)

**On every session start (fresh account, after a limit, after a crash):**

1. Read this whole file.
2. Read the LIVE STATUS BOARD at the bottom — it tells you exactly what is
   done, what is in progress, and what is next.
3. Run the test suites (`python test_fixes.py && python test_interactive.py`)
   to confirm the tree is healthy before touching it.
4. `git log --oneline -10` to see what the previous session actually shipped.
5. Continue from the first unchecked step. Never redo completed items.

**Orchestration hierarchy (fixed, non-negotiable):**

- **Fable 5 = ORCHESTRATOR.** Plans, reviews, decides, talks to the owner.
  Does NOT write implementation code. Has explicit liberty to disagree with
  this document and the owner — it must argue its case plainly before
  proceeding, never agree just to please.
- **Opus 4.8 = RESEARCH ONLY.** Spun up as a subagent only when something
  needs deep investigation (undocumented CLI behavior, security probing,
  library evaluation). Not for routine work.
- **Sonnet 5 = EXECUTOR.** All implementation — code, tests, refactors — via
  clear, self-contained task briefs. The orchestrator reviews everything.
- If the session is running on a weaker model: follow this document
  literally, step by step. It is written so that careful execution of the
  steps produces the right product without improvisation.

**The per-step protocol (mandatory for EVERY step of EVERY phase):**

1. **Before building:** re-read the step's Goal and Acceptance criteria.
2. **Build** the smallest increment that satisfies them.
3. **Verify the output for real** — run it, don't assume. A feature is not
   done until its Verification commands pass AND you have exercised it
   end-to-end at least once (the backends fail silently; never claim a CLI
   feature works without live read-back/transcript proof).
4. **Future-cost check:** ask explicitly — "will this design cost us a month
   later?" (hidden coupling, duplicated logic, a second source of truth,
   anything hardcoded that belongs in config). If yes, fix it now or write
   the concern into the status board under Notes.
5. **Update the LIVE STATUS BOARD** in this file: tick the step, add one
   line to the log (date, what shipped, any warning for the next session).
6. **Commit** with a clear message. Small commits. Tests green before every
   commit. The bot must keep working throughout — the owner demos it.

---

## 2. INTENT — WHY THIS EXISTS

**The one-sentence intent:** a non-technical person should be able to own a
powerful AI assistant on their own computer — their knowledge stored in
portable Markdown on their own disk, the "brain" rented from whatever free
AI CLI is available today and swappable tomorrow.

> THE KNOWLEDGE IS THE USER'S. THE BRAIN IS RENTED.

- The reference user is one real non-technical manager: speaks English, not
  computer-educated, never reads a log, a trace, or an error. But the
  product is **generic for everybody** — zero industry assumptions anywhere
  in code or prompts. A businessman tailors it to his business, a
  headmaster to his school — the difference comes from **conversation**
  (first-run interview → wiki), never from code or config.
- **Zero budget, permanently.** CLI logins only (agy / Claude Code /
  opencode). No API keys, no paid services, no paid dependencies.
- Access to any given model can vanish overnight (login expiry, quota,
  product shutdown). The product must survive that as a config edit. The
  wiki directory IS the product; it outlives every model swap.

**Zilla is a HARNESS, not an agent.** The agentic CLIs already have tool
use, shell access, file access, conversation persistence, skills, and
plugins. Zilla never rebuilds any of that. Its entire job:

```
User (terminal or Telegram) → [shape context, set policy] → agent CLI
                            → [shape output] → back to the user
```

Every time you are tempted to build orchestration, first check whether the
CLI already does it and Zilla just isn't configuring it.

---

## 3. THE VISION — WHAT WE ARE BUILDING

Zilla becomes a **full, standalone, open-source terminal application**:

- **`zilla`** (bare command) → a full-screen terminal UI, opencode-style:
  chat view + input bar at the bottom. Chat with the AI right there.
  Settings screens. Skills viewer. Conversational onboarding — the user can
  literally type "connect to my Telegram" and Zilla asks for the bot token
  and owner ID, then connects.
- **`zilla config`** → interactive settings menu (plain, SSH-friendly).
- **`zilla doctor` / `start` / `stop` / `status` / `logs`** → operations.
- **Telegram is an optional connector**, not the center. It is frontend #2
  over the same core the TUI uses.

Settings the app must expose (single source of truth = the same
`.env`/`settings.json` the core reads — never two settings systems):

| Setting | Values |
|---|---|
| Backend priority order | agy / claude / opencode, ordered |
| Model per backend | from each CLI's live catalog |
| Fallback chain | on error/empty/limit, next backend in priority |
| Voice mode | offline (local Whisper) / online (Google) |
| Web mode | headless (Playwright) / my-browser (WebBridge, uses real cookies) / off |
| Health & alert policy | silent self-heal; alert only when a human must act |
| Telegram connector | off / token + owner ID |
| Autostart | on/off |

**Explicit non-goals (owner decision 2026-07-16, each backed by a real
OpenClaw incident — see `docs/dev/RESEARCH_OPENCLAW_HERMES.md` §5):** no web
UI (their 1-click-RCE CVE), no listening network gateway (40k exposed
instances), no skills marketplace / auto-install (341 malware skills). If any
of these is ever proposed, that section is the counterargument. Any socket
Zilla ever does open: auth required + loopback bind from day one.

**Environment adaptation:** at startup Zilla detects OS (macOS / Linux /
Windows / headless server), GUI presence, which CLIs are installed and
logged in, ffmpeg, WebBridge reachability — and adapts. GUI present →
desktop control allowed. Headless → shell only. Windows → clean stub
errors ("not supported yet"), never crashes.

**Deployment posture:** all development and testing happens on the owner's
MacBook (fast). The client's Ubuntu laptop is a later deployment over
SSH/Tailscale. Once stable on Linux: dedicated `zilla` user + systemd
hardening (see Trap #2 below — that is the real security boundary).

---

## 4. CURRENT STATE — COMPLETE CODE ANALYSIS

Repo: `alokflows/zilla`. ~10k lines Python on `python-telegram-bot`.
192 tests green (`python test_fixes.py` + `python test_interactive.py`).

### What already exists and works (DO NOT reinvent)

| Concern | Where | Notes |
|---|---|---|
| agy execution | `cli_engine.py` | Runs agy under a real PTY (`platform_compat.PtyProcess`). The answer is read from agy's OWN `transcript.jsonl` under `~/.gemini/antigravity-cli/brain/<conv>/.system_generated/logs/` — NOT stdout. stdout in print mode is untrustworthy; this is already solved, never "fix" it with `script -qec`. |
| Anti-bleed invariants | `cli_engine.py`, `docs/dev/AI_CONTEXT.md` | I-STEP / I-CONV / I-CANCEL + per-user lock + global new-conv detection lock. **Violating any of these reintroduces response bleed.** Read AI_CONTEXT.md before touching the engine. |
| Hang protection | `cli_engine.py` | Idle reaper is ACTIVITY-based (a new transcript step = alive), plus a hard runtime ceiling. Do not add wall-clock timeouts. |
| Claude backend | `backends.py` | `claude -p --output-format json --resume <id> --model <alias>`; pinned Playwright MCP attached only on web-intent turns (`autoharness.needs_browser`). `claude_identity()` reads auth status. |
| Backend contract | `backends.py` + `cli_engine._run_blocking` | `run(prompt, conversation_id, *, progress_callback, cancel_event, skip_permissions[, model]) -> (response, conversation_id)`. New backends implement this + register in `_run_blocking`. |
| Per-turn harness | `harness.py` | Trust contract (anti-fabrication), style rules, engine/OS header injected every turn; full onboarding only on new conversations. |
| Skills index | `harness.skills_summary()` | One line per skill from `SKILL.md` frontmatter (agy: `~/.gemini/antigravity-cli/skills`, claude: `~/.claude/skills`). Bodies load on demand. Token discipline is already correct. |
| Event log | `harness.log_event()` | Structured `logs/trust_log.jsonl` on every turn (backend, model, task class, duration). Quota instrumentation is 80% done. |
| Human-in-the-loop | `interactive.py` + `bot.py bridge_watcher` | Agent pauses mid-task by writing `ask_<id>.json`; owner is DM'd; reply written as `answer_<id>.json`; secrets masked and wiped. Pure core, tested. Kinds: otp/password/text/confirm. |
| Anti-hallucination | `verify.py` | Precision-tuned regex gate + ONE corrective retry in the same conversation. Already the seed of "self-correct, never loop silently". |
| Auth tiers | `users.py` | owner / admin / limited. **Approval mode**: limited users' requests held until owner taps ✅. Reuse this UI for skill approval. |
| Sessions | `sessions.py` | Per-user named sessions; conversation ids tagged with the backend that created them (never resumed cross-backend). |
| Scheduler | `schedules.py` + `schedule_parse.py` | Self-healing (retry → give-up → still fires next occurrence), NL parsing ("every day at 9am…"), catch-up after downtime. |
| OS divergence | `platform_compat.py` | The ONLY file with OS-specific code (locks, PTY, Windows window-hiding). Keep it that way. |
| Config | `config.py` | `.env` + `settings.json` (mtime-cached), path autodetection, backend-aware model layer with atomic write + read-back. |
| Installer | `install.py` | Interactive setup + `--doctor` self-check. This is the seed of `zilla config`. |
| Voice | `media.py` | Transcription via GOOGLE CLOUD speech API (free, online) — **NOT local Whisper**, despite what older notes claim. |
| Browser bridge | `bot.py` (`/browse`, `KIMI_BRIDGE_URL`) | External localhost service ("Kimi WebBridge"). Currently assumed present; becomes the optional "my-browser" web mode. |

### The problem child

`bot.py` (2,872 lines) is the app today: Telegram handlers + scheduler
runtime + approval flow + delivery + menus + lifecycle, all tangled
together. The core-extraction refactor (Phase 1) mostly means carving this
file up. This is the largest single risk in the plan — migrate
incrementally (strangler pattern); the Telegram bot must keep working at
every commit.

### Known traps (verify in Phase 0 — do not trust)

1. **agy model handling contradicts itself in-repo.** `config.py` says agy
   has NO `--model` flag (model = display string like
   `"Gemini 3.1 Pro (High)"` written into
   `~/.gemini/antigravity-cli/settings.json`); `cli_engine.py` passes
   `--model` "(agy v1.0.6+)". agy **silently ignores unknown model
   strings** — a typo means the wrong model with zero error. Find the
   installed truth; keep the read-back verification either way.
2. **No in-CLI sandbox (probably).** `docs/dev/AI_CONTEXT.md` records an
   empirical finding (older build): headless `--print` executes tools
   REGARDLESS of `--sandbox`/permission flags. Re-verify on the installed
   versions. If still true, the only real security boundary is OS-level
   (dedicated user + systemd hardening on the Linux deployment; never run
   the agent as root) — and anything "deterministic security" Zilla adds
   must be enforced by Zilla itself, never judged by the model.
3. **agy auth expires silently** (login token in the OS keychain — the
   "3am problem"). The probe primitive exists (`config.agy_reachable()`:
   `agy models` returning real data implies logged in) but nothing calls
   it proactively.
4. **opencode is not integrated at all yet.**
5. **`~/AGI-Brain` (Inbox/Outbox/Bridge) is legacy layout** — owner wants
   one clean Zilla home directory instead (Phase 3).
6. Older docs (`README.md`, `docs/dev/STATUS.md`) predate this vision —
   where they conflict with this document, THIS DOCUMENT WINS.

---

## 5. OWNER DECISIONS ALREADY MADE (do not re-ask)

- Terminal-first full application; Telegram is an optional connector.
- TUI style: opencode-like full-screen chat (recommended lib: Textual —
  free, open source, pure Python). Orchestrator may propose an alternative
  with reasons, once, in Phase 2 planning.
- Core extraction (Phase 1) comes BEFORE the TUI. Owner accepts it takes
  time; the goal is "not suffering a month later".
- `~/AGI-Brain` layout is replaced by one clean, portable, git-init'd
  Zilla home.
- Health is SILENT self-healing: check quietly, fix autonomously, log
  everything; alert the owner ONLY when a human must act (e.g. re-login),
  with a plain-language runbook. No hourly status spam, ever.
- Fallback fires on error / empty output / limit-detected only — NOT on
  long runtime (the idle reaper already separates working from stuck).
- Voice: both engines implemented; a setting chooses offline (Whisper) vs
  online (Google).
- WebBridge: kept, demoted to optional "my-browser" web mode with
  reachability auto-detection and silent degradation.
- Skills: instruction-type auto-approve; code-type need ONE owner approval
  tap before first run (deterministic, enforced by Zilla, not the model).
- No vector DB — grep + the agent reading files.
- Zero budget. No API keys. No paid anything.
- Windows: stubbed with clean errors, not implemented now.
- Dev machine: owner's MacBook. Deployment: client's Ubuntu laptop later.

---

## 6. THE PLAN — SUPERSEDED, SEE PLAN.md

The phase list that used to live here (P0-P10) is retired. **`PLAN.md`
is the current work order** (Phases M/H/R/S/G/T/V, §5-10 there). What
remains useful from the old P0-P10 plan — traps found, owner decisions,
and dead ends — is preserved below as historical record; none of it is a
todo list anymore.

**Known traps carried forward (still true, still worth knowing):**
1. agy has no `--model` flag in some builds; silently ignores unknown model
   strings — always keep the read-back verification (`docs/dev/PHASE0_FINDINGS.md`).
2. No in-CLI sandbox: headless runs execute tools regardless of permission
   flags — the only real security boundary is OS-level (H3 in PLAN.md).
3. agy/claude auth expires silently (the "3am problem") — PLAN.md §H2 gives
   the honest, adversarially-reviewed version of assisted re-login (default
   deliverable is detect + precise instructions; relay-assisted login only
   where the executor verifies the CLI actually supports it — **do not
   build speculative login automation**, a call already validated once by
   this session pausing on exactly that before PLAN.md confirmed it).
4. `~/AGI-Brain` legacy layout — PLAN.md's `AGI-Brain/Memory/` (§3.2) is the
   new, git-init'd home for the Markdown knowledge tier; not a full replace
   of the old layout discussion, just the memory subtree.
5. Older docs (`README.md` on `main`, `docs/dev/STATUS.md`) may still
   reflect the pre-PLAN.md vision — PLAN.md wins on any conflict.

## 7. WORKING AGREEMENTS (always in force)

- Plan → owner approval → execute. Small reviewed increments.
- The bot/app must keep working at every commit (the owner demos it).
- Preserve the `docs/dev/AI_CONTEXT.md` invariants (I-CONV / I-STEP /
  I-CANCEL, per-user lock, global new-conv lock).
- OS-specific code lives ONLY in `platform_compat.py`.
- Tests green before every commit; new pure logic gets tests.
- No hardcoded models/paths outside config. No industry vocabulary in core
  prompts or code. Secrets never in argv. No paid dependencies.
- Security decisions are deterministic (enforced by Zilla), never
  model-judged — untrusted text talks to the model.
- Never claim a CLI feature works without live proof — backends fail
  silently.
- Keep replies to the owner SHORT and point-wise; they are often on a
  phone. Plain language, no jargon.

---

## LIVE STATUS BOARD

> **Update this section and commit after EVERY completed step.** Keep it
> LIGHT (owner decree 2026-07-17): current state, one line per session,
> only notes a future session actually needs. History lives in git log.

**Current phase:** Phase 1 + P1.5 + P2 (entrypoint, TUI) are **DONE and
merged** on this branch — that shipped, tested, live-running code is not
in question. **The forward plan is now PLAN.md's M → H → R → S → G → T →
V phase order (see the notice at the top of this file), not the old
P0-P10 list.** PLAN.md was written on a branch that forked before this
code existed, so its phase list doesn't know P1.5/CLI/TUI/approvals are
already done — the reconciliation checklist below maps PLAN.md's phases
onto that reality. **M1 (`store.py` + migration) is COMPLETE — every
step and every accept-criteria test from PLAN.md §5.M1 is committed and
green.** **M2 (Memory layout + injection + `TurnContext` threading) is
COMPLETE** (PLAN.md §5.M2) — see checklist + session log below.
**M3 (FTS5 search + memory git + quiet-run mode) is COMPLETE** (PLAN.md
§5.M3) — see checklist + session log below.
**NEXT UNIT OF WORK: a small pre-F1 quick fix (owner-reported
2026-07-18 pm, see Checklist + Notes below), THEN Phase F (F1→F4) —
foundation cleanup (PLAN.md §17, owner-ordered 2026-07-18): F1
ZILLA_HOME storage layout, F2 dynamic backend registry (now includes
the slash-command registry, see PLAN.md §17 F2 item 3), F3 media
importance+retention, F4 system jobs invisible+silent. THEN M4.** Full
test gate before and after.
**Working branch (source of truth): `main`** (branches consolidated
2026-07-18 — the old planning + execution branches were fully merged into
main; if any machine still has local commits from them, rebase onto main
and push. PUSH TO MAIN EVERY SESSION, no exceptions).

**ONE-TIME CHORE (do this FIRST, before F1, from the owner's terminal —
takes 30 seconds):** the two stale remote branches still exist as empty
merged shells (the remote refused deletion from the planning session).
Run:
`git push origin --delete claude/python-cli-bot-planning-80x8a3 claude/zilla-harness-review-0v96bs`
then verify with `git ls-remote --heads origin` (only `main` should
remain). **After verified deletion, edit this file: remove this entire
chore block AND every remaining mention of the old branch names in this
file, so the consolidation leaves no residue. Commit that cleanup with
the F1 session.**
**Tests:** 260+16+116+57 core + 71 review + 17 tui + 69 cli + 46 harness +
34 memory_m3 = **686 green** — `.venv/bin/python test_fixes.py /
test_interactive.py / test_core.py / test_schedules_seam.py /
test_review.py / test_tui.py / test_zilla_cli.py / test_harness.py /
test_memory_m3.py` (test_schedules_seam.py is a frozen acceptance spec —
never edit it) + `import bot; import zilla.core; import zilla.cli; import
zilla.tui.app`.
M3 note: `memory.git_autocommit`/reindex touching the real repo's
`Memory/` tree is gated behind `ZillaCore.memory_autocommit_enabled`
(default `False`, same opt-in pattern as `schedule_pre_run`) — only
`bot.py`'s real `main()` turns it on, so `test_schedules_seam.py` (frozen,
does not isolate `MEMORY_DIR`) stays a safe no-op. Verified after every
test run in this session: no `.git` created inside the real `Memory/`, no
stray real `zilla.db`.
M1 note: importing `bot` now safely exercises `_harden_file_perms`/
`_maybe_backup_db` against tmp paths (see `bot._harden_file_perms`'s
`base=` param) — never the real repo's `.env`/`sessions.json`/`zilla.db`.
M2 note: `test_harness.py` isolates `zilla.memory.MEMORY_DIR` AND
`config.DB_FILE`/`SETTINGS_FILE` before calling `build_preamble` — the
latter is a real trap: `operating_contract()`/`get_backend()` read
`get_setting()` which lazily creates the real repo `zilla.db` on first
touch if `SETTINGS_FILE` isn't redirected first (caught live during this
session — a stray real `zilla.db` got created and had to be deleted;
confirmed empty, no migration ran, safe).
**Bot:** live on the owner's MacBook (@Mangomangos_bot; `.env` exists here,
git-ignored). After changing `bot.py`: `zilla stop` + `zilla start` (or
`.venv/bin/python -m zilla.cli stop/start`), confirm "Application started"
in its log. **M2 has not yet been live-smoked against the real bot process**
(that needs the owner's Telegram token/session — starting it is the
owner's call, not something to trigger unattended); do that before or
during the M3 session — first message after restart should show the
first-run interview line if `Memory/MEMORY.md` is still the template.

### Checklist

**Shipped, pre-PLAN.md (kept — not reopened):**
- [x] **P0** Verify reality (flags, GEMINI.md/AGENTS.md, sandbox test, logins, tests on macOS) → `docs/dev/PHASE0_FINDINGS.md`
- [x] **P1** Core extraction: `zilla/core.py` (`ZillaCore` — turn pipeline, scheduler, bridge, approvals, health snapshot) — this IS most of PLAN.md's G1 engine-facade target already; G1 below is the delta, not a from-scratch build.
- [x] **P1.5** Orchestration router — `zilla/review.py` (`review()` gate + `classify_route()` triage: smalltalk→haiku fast path, share→wiki journal, every route logged to `trust_log.jsonl`) — this covers most of PLAN.md's R1 triage router; R1 below is refinement, not a from-scratch build.
- [x] **P2** `zilla` entrypoint (`zilla/cli.py`, `doctor.py`, `security.py`, `configmenu.py`) — `config`/`doctor`/`start`/`stop`/`status`/`logs` all live-verified.
- [x] **P2** Full-screen TUI (`zilla/tui/`) — chat/settings/skills/health screens exist; this is most of PLAN.md's T1 target. Missing for T1: Sessions/Schedules/Memory screens, Unix-socket IPC daemon-attach model, conversational onboarding.
- [ ] **P2** Conversational onboarding + Telegram-as-connector unification — folds into PLAN.md's T1.

**PLAN.md phases (strict order, §13 — do not skip ahead):**
- [x] **M1** `store.py` (SQLite+WAL) + first-start migration from the 5 JSON files — DONE 2026-07-18 (6 commits, `store.py`/thin wrappers/migration/doctor DB checks/audit-debt burn-down/secrets hygiene+backup/acceptance tests). 606 green.
- [x] **M2** Memory layout (`Memory/` — `config.MEMORY_DIR`, repo root, per M1's forward-declaration, not literal `~/AGI-Brain/Memory`) + owner-only injection + `TurnContext` threading — DONE 2026-07-18. 652 green.
- [x] **M3** FTS5 search + memory git + quiet-run mode — DONE 2026-07-18. 686 green.
- [ ] **Quick fix (owner-reported 2026-07-18 pm)** Menu Close button:
  delete the message instead of editing it to "✓ Closed" text; and fix
  the silent-second-`answer()` bug so a failed callback is never
  indistinguishable from a successful one (P4). Exact spec in Notes
  below. — NEXT, before F1.
- [ ] **F1** ZILLA_HOME storage layout replaces AGI-Brain (PLAN §17).
- [ ] **F2** Dynamic backend registry — no hard-coded backend buttons (PLAN §17).
- [ ] **F3** Media importance + retention controls (PLAN §17).
- [ ] **F4** System jobs invisible + silent — fixes the live heartbeat noise the owner screenshotted (PLAN §17).
- [ ] **M4** Nightly distillation + `/memory` command + change surfacing — genuinely new.
- [ ] **K1-K4** Relational graph memory (PLAN §6): schema+indexer, entity linking, curiosity loop, /graph views.
- [ ] **U1-U4** Generative UI + design system + presence (PLAN §7): ZUI protocol, agent education, STYLE.md, pinned status card.
- [ ] **H1** Heartbeat loop — genuinely new.
- [ ] **H2** Health probes + assisted re-login — PARTIAL: earlier P7 health-loop WIP exists (stashed, `git stash list` → "P7 health-loop WIP"), built before PLAN.md was found; PLAN.md's H2 spec is more precise (explicit "do not build speculative login automation" ceiling) — treat the stash as reference only, re-derive from PLAN.md's spec rather than popping it verbatim.
- [ ] **H3** systemd Linux service — genuinely new (this is P10 Ubuntu hardening's old slot, now precisely specified here).
- [ ] **H4** Self-update with doctor-gated rollback (PLAN §8).
- [ ] **B1-B2** Background task lane + /tasks; incognito sessions (PLAN §9).
- [ ] **R1** Triage router refinement — MOSTLY DONE via `zilla/review.py` (P1.5 above); confirm against PLAN.md's exact spec before marking done, don't rebuild.
- [ ] **R2** Fallback chain — genuinely new.
- [ ] **R3** opencode adapter — genuinely new (was P8).
- [ ] **S** Skills from chat, ask-first approval — genuinely new (was P5).
- [ ] **C1-C3** Brain export/import; connectors screen (MCP/native, per-backend); GitHub cloud backup + bootstrap-from-cloud (PLAN §12).
- [ ] **G1** Engine facade extraction — PARTIAL via existing `zilla/core.py` (P1 above); the new part is the Unix-socket IPC daemon-attach model. PLAN.md flags this as the riskiest refactor in the plan — do it alone, no parallel fan-out.
- [ ] **T1** Terminal app (Textual, daemon-attach via IPC) — MOSTLY DONE via existing `zilla/tui/` (P2 above); missing pieces listed there.
- [ ] **V1** Offline voice (faster-whisper — already pip-installed, salvaged from GOD MODE round 2) — genuinely new (was P9).
- [ ] **V2** Voice replies via local TTS (Piper) (PLAN §14).
- [ ] **V3** Owner-trained wake-word satellite (PLAN §14).

### Session log (one line per session — details in git log)

| Date | What shipped |
|---|---|
| 2026-07-16 | Full codebase analysis + this handoff; Phase 0 findings (`docs/dev/PHASE0_FINDINGS.md`); modules moved into `zilla/` package with shims. |
| 2026-07-16 | Turn-pipeline seam → `core.handle_message` (+`test_core.py`); scheduler seam Parts A+B → payload types, session modes, backend pins, retry ladder (+frozen `test_schedules_seam.py`). |
| 2026-07-16 night | Live smoke: text/photo/doc/cancel ✅; `safe_send` 4× retry + raised PTB timeouts; voice fixed (`brew install flac` on Apple Silicon — add a doctor check in P2); reminder parser broadened; one-off reminders instant, `system_event` payloads (zero model call at fire), exact-time scheduler tick. |
| 2026-07-17 | Bridge seam → core (`Ask` events over `subscribe()`, `pending_ask_for`/`answer_ask`; bot.py renders only). 334 green; bot restarted live. |
| 2026-07-17 | `docs/dev/RESEARCH_ORCHESTRATION_REVIEW.md` — verdict: OpenClaw/Hermes have NO reviewer LLM; "effortless" = in-loop tool self-heal + persistence system prompt + deterministic delivery filter. Zilla plan: harness self-heal clause, unify scattered checks into one `review()` seam at both delivery points, surface existing `Progress` events into the ⏳ bubble (free "feels alive" win), steal-list #31–40. |
| 2026-07-17 | Health stub → `core.health_report(force=False)` snapshot from existing probes (agy/claude reachability, disk, scheduler/bridge attachment); loop itself stays Phase 7. 352 green (test_core 75). |
| 2026-07-17 | Approvals seam → `core.approvals` (`submit`/`pending`/`approve`/`deny`, `ApprovalRequest` events; approved runs share the per-user lock via `handle_message`). **Phase 1 extraction COMPLETE — 379 green**, bot restarted live. Known deviation: owner-DM delivery of approval cards is fire-and-forget (logged on failure), same as the bridge seam. |
| 2026-07-17 | README rewritten to the full vision (effortless orchestration, terminal-first, assisted re-login) and pushed to `main` so the GitHub front page shows it. Assisted re-login decree written into Phase 7 step 3. Session ended deliberately before P1.5 (owner: fresh session next). |
| 2026-07-17 pm | GOD MODE round 1: TUI landed (`zilla/tui/`, Textual, +17 tests = 396 green, no existing files touched; needs a real-terminal launch by owner). P1.5 router + `zilla` CLI entrypoint executors running in parallel worktrees. Owner Q&A: OAuth≠replacement for CLI (CLI login IS OAuth; Hermes OAuth = their hosted paid inference) — stay the course, replica-of-Hermes rejected, steal-list stands. |
| 2026-07-17 pm | `zilla` CLI landed (+69 tests = 465 green). Found the bot DEAD since 08:22 (httpx ConnectError killed PTB, no auto-restart — P7 evidence); restarted live via `zilla start` ✅. config.py gained per-backend model helpers (`get/set_model_for`, `model_catalog_for`). |
| 2026-07-17 pm | P1.5 router merged (built in parallel worktree): `zilla/review.py` gate + triage, harness `_SELF_HEAL`, smalltalk fast path (`claude --model haiku`), share→wiki journal, steal #36, 👀 ack + Progress→⏳-bubble. Orchestrator patched `_SELF_HEAL` post-merge to restore the spec's destructive/irreversible/costs-money stop-condition. Bot restarted on merged code. Awaiting owner live smoke. |
| 2026-07-17 eve | GOD MODE round 2 FAILED: 5 parallel Sonnet executors (P7/P2-onboarding/P8/P9/P6) killed by the shared usage limit in ~5 min, zero commits; worktrees deleted (only scrap: partial tui/wizard.py, discarded). Salvage: `faster-whisper` is already pip-installed in `.venv` (P9/V can skip that step). Owner decree: parallel fan-out BANNED → serial execution protocol. Antigravity suggestions reviewed → verdicts in Notes; P11 WhatsApp connector parked. |
| 2026-07-18 | **M1 COMPLETE** (6 commits): `store.py` (SQLite+WAL, typed accessors); sessions/schedules/users/config swapped to thin store wrappers; first-start migration of the 5 legacy JSON files (idempotent, rename-after-commit); `install.py --doctor` DB checks; audit-debt burn-down (tz-aware `compute_next_run` via `zoneinfo`, `_active_cancel` keyed `(chat_id, uid)`, `max_media_mb` ingest cap); secrets hygiene (`_harden_file_perms` covers `zilla.db*`/`Memory/`, nightly `VACUUM INTO` → `zilla.db.bak`+`.bak.1` rotation via a new `_backup_loop` task); every PLAN.md accept-criteria test committed (concurrent-mutation, reader-never-blocks, DST both directions, cancel-keying, media-cap, perms, doctor-OK). 606 green. Production `sessions.json`/`schedules.json` confirmed untouched throughout — no real `zilla.db` was ever created by a test run. |
| 2026-07-18 | **M2 COMPLETE**: `zilla/memory.py` (Markdown knowledge tier — `ensure_tree`/`read_core`/`is_template`/`wiki_index_text`/`append_journal`, idempotent, never clobbers an owner's edits); `TurnContext` dataclass (`uid`/`role`/`is_owner`/`origin`) threaded explicitly (never a module-level global — an adversarially-reviewed constraint, since the 4-thread executor pool would race an ambient global) through `handle_message` → `run_cli_async` → `_run_blocking` → `_dispatch_turn` → `run_cli`/`run_claude` → `wrap_prompt`/`build_preamble`; owner-only "Your memory" block injected every owner turn (MEMORY.md + wiki index + memory protocol), soft-cap warning (2400 chars) + hard-cap truncation (4000 chars) + first-run interview line while MEMORY.md is still the template + a memsearch.py line that self-activates once M3 ships it (checks the file exists, no code change needed then); P1.5 'share' route redirected from the old `WIKI_JOURNAL_DIR` to `Memory/Journal/` and gated owner-only (a non-owner's "share"-shaped message now falls through to the full route instead of writing into the owner's journal); retired `WIKI_DIR`/`WIKI_JOURNAL_DIR` from `config.py` (only consumer was the route just redirected). New `test_harness.py` (46 tests: tree/idempotency, template detection, wiki index format+cap, journal append, TurnContext shape, injection gating, first-run line, caps, memsearch conditional, and a real-thread concurrent two-principal isolation test proving an owner turn and a non-owner turn interleaved on the executor never cross-contaminate). 652 green. Production `sessions.json`/`schedules.json` confirmed untouched; a stray real `zilla.db` got created mid-session by an under-isolated `test_harness.py` draft (traced to `get_setting()` touching `config.DB_FILE` before it was redirected) — deleted (confirmed empty, no migration ran) and the test fixed. **Known gap, owner-confirmed deferral:** `_execute_message_schedule`'s `run_cli_async` call is NOT wired with `ctx=` — `test_schedules_seam.py` is a frozen acceptance spec whose `fake_run` mocks have fixed signatures with no room for a new kwarg. Schedule-triggered turns get no memory injection until a later phase revisits the frozen spec.
| 2026-07-17 night | Paused mid-build on a P7 health-loop (stashed, uncommitted — `zilla/core.py` health task + `bot.py` alert-runbook rendering) when the owner surfaced `PLAN.md`: a separate, from-scratch, adversarially-reviewed plan (Fable + owner) found on remote branch `claude/python-cli-bot-planning-80x8a3` (not yet fetched locally before this session — discovered via `git fetch --prune`). That branch forked at `85d5893`, before P1.5/CLI/TUI/approvals existed, and has no code changes of its own — docs only. Owner decision (asked directly): bring PLAN.md onto this shipped-code branch rather than switch branches or discard either plan. PLAN.md copied here as a new file; this file's old §6 (P0-P10) and status board reconciled to point at PLAN.md's M/H/R/S/G/T/V order (see notice at top). Old antigravity verdict rejecting SQLite (below) is now superseded — PLAN.md's adoption of SQLite+WAL for M1 is the settled decision. |
| 2026-07-18 | **M3 COMPLETE**: indexer — `memory.reindex()` scans `Memory/**/*.md`, diffs against `mem_seen` (mtime+size), upserts into the M1-seeded `mem_fts` FTS5 table, drops entries for deleted files; called on `bot.py` startup and on every owner-turn `harness._memory_block()` injection. `memory.search()` (FTS5 `MATCH`, reindexes first) + a post-match per-file line scan (`_locate()`, FTS5 carries no line numbers) → `memsearch.py` CLI at repo root (`python memsearch.py "query"` → top-8 `path:line` + 2-line snippet, plain text, exit 0 + "no results" on empty) — this is what the M2-seeded forward-compat line in the memory block now actually invokes. `memory.git_autocommit(context)`: `git init` on first call (author "Zilla <zilla@local>", `.git` locked 0700), `git add -A && git commit -m <context>` only if `git status --porcelain` shows changes; wrapped in a broad try/except so a git failure (missing binary, locked file, disk full) is logged and swallowed, never breaks a reply. Quiet-run mechanism: `system` flag threaded through `ScheduleManager.add()`/`_to_dict()` (DB column already existed from M1); `_quiet_heartbeat_suppressed(s, response)` in `core.py` — a `system=1` schedule whose stripped response is/ends with a line reading exactly `HEARTBEAT_OK` (case-insensitive) delivers nothing (still counts as success, logged as `schedule_quiet`); a user (`system=0`) schedule is never suppressed even if its own legitimate output ends with that token — checked and wired into both `_run_and_record` and `run_schedule_now`. **Critical safety design**: `git_autocommit`/`reindex` touching the real repo `Memory/` tree is gated behind a new `ZillaCore.memory_autocommit_enabled` flag (default `False`, same opt-in pattern as `schedule_pre_run`) — only `bot.py`'s real `main()` sets it `True`; every test-constructed `ZillaCore` (including the frozen `test_schedules_seam.py`, which does not isolate `MEMORY_DIR`/`DB_FILE`) leaves it off, so the new autocommit code path is a safe no-op there. Caught proactively before writing any code: a real `Memory/` tree with the owner's actual data already exists at the repo root (created by the live bot) — confirmed after every test run this session that no `.git` appeared inside it and no stray real `zilla.db` was created. New `test_memory_m3.py` (34 tests, all 5 of PLAN.md §5.M3's Accept criteria): index build + no-op-when-unchanged + invalidation-on-delete; search resolves a planted fact to the exact `path:line`; `git_autocommit` fires on change and is a no-op on no-change (verified via `git log` commit count); a git failure injected at the `subprocess.run` level (not by replacing `git_autocommit` itself, which would bypass its own try/except) still delivers the turn's `Response` through a real `ZillaCore.handle_message` call; full quiet-run suppression matrix including the negative case (`system=0` + token still delivers) for both `_run_and_record` and `run_schedule_now`. 686 green (260+16+116+57+71+17+69+46+34). |

### Notes (only what a future session needs)

- **Quick fix spec (owner-reported 2026-07-18 pm, do this FIRST, before
  F1):**
  1. `bot.py` `_cb_misc`, the `menu_close` branch (~line 2013-2020):
     currently `await query.edit_message_text("✓ Closed. Send /menu to
     reopen.")`. Change to `await query.message.delete()` (bots can
     always delete their own outgoing messages in a private chat, no 48h
     limit — same precedent already used for the OTP/password wipe at
     `bot.py:1920`, `await update.message.delete()`). Keep a fallback:
     if delete raises, fall back to `edit_message_reply_markup(reply_markup=None)`
     silently (strip the buttons, no confirmation text either way — the
     owner does not want a "Closed" message, they want the message gone).
  2. `handle_callback` (~line 2586-2617): the outer `except Exception`
     tries a second `query.answer(f"Error: ...")` to report a failure,
     but Telegram rejects a second `answer()` on the same callback query,
     so that call raises and is swallowed by its own bare
     `except Exception: pass` — a failing button tap currently looks
     IDENTICAL to a working one (tap registers, spinner clears, nothing
     else happens). This is a real P4 violation, not cosmetic, and is
     the likely cause of "buttons feel unresponsive" reports beyond
     Close specifically. Fix: on that exception path, since `answer()`
     is already spent, surface the failure a different way — edit the
     original message (or send a new one if edit fails) with one calm
     line, e.g. `⚠️ That didn't go through — try again.` (P4/STYLE.md
     tone: no stack traces, one sentence, no exclamation-mark pile-up).
     **Accept:** unit test simulating a `_cb_*` helper raising mid-way
     confirms the chat receives a visible failure notice, not silence;
     menu_close unit test confirms `delete()` is called and no text
     message is sent; live smoke — tap Close, message vanishes with no
     new message; force an error in a callback handler, confirm a
     failure line appears instead of silence.
- **Operational note (owner-reported 2026-07-18 pm):** a chunk of the
  "unresponsive" reports traced to the dev MacBook's battery dying,
  killing the bot process — not a code bug. PLAN.md's H3 (systemd on
  the always-on Ubuntu server, per P7 headless-first) is the structural
  fix and stays in its planned phase order; until H3 lands, keep the
  dev Mac plugged in / prevent sleep during active use.
- **Aesthetics note (owner-reported 2026-07-18 pm):** "stray lines and
  symbols" in the menus is exactly PLAN.md's U3 (Design System /
  STYLE.md) scope — already planned, no new spec needed, executes in
  its existing phase slot.

- **Latency is the owner's #1 complaint** — every turn pays a full CLI call
  (17s–2m34s observed live). The P1.5 orchestration router is the fix.
- `_execute_command_schedule` = unattended shell, owner-only at creation —
  never loosen that gate. No UI yet sets `payload_type`/`session`/`backend`/
  `model` at creation (schema-only). Bridge answer-capture is text-only.
- Scheduling policy (owner): Zilla's scheduler is the ONLY scheduling
  authority — the agent must never create OS timers; a schedule-request
  bridge (agent writes request → owner one-tap confirm) folds into P5.
- Reference designs: OpenClaw + Hermes — steal list in
  `docs/dev/RESEARCH_OPENCLAW_HERMES.md` §7; consult at each phase start.
- Orchestrator liberty: if findings contradict this plan, argue it with the
  owner before proceeding — never silently comply with a stale plan.
- **Loop protocol (owner decree 2026-07-17 evening — REPLACES GOD MODE):
  SERIAL ONLY.** Parallel fan-out is BANNED: round 2 launched 5 parallel
  Sonnet worktree executors and the shared 5-hour usage window died in ~5
  minutes with ZERO commits (the account limit counts every agent; 5×
  repo-reading in parallel = instant burn). Still true under PLAN.md: work
  PLAN.md's phase list top to bottom, one phase at a time, full test gate
  before and after each, small phase-prefixed commits (`feat(M1): …`), no
  parallel worktree fan-out. PLAN.md itself is the brief now — it replaces
  the old Fable-writes-briefs/Sonnet-executes handoff dance described in
  earlier session-log entries above; that protocol is retired, not this
  serial-only discipline.
- **Antigravity-CLI suggestions reviewed (2026-07-17, orchestrator verdicts
  — SUPERSEDED 2026-07-17 night where noted, see PLAN.md):**
  1. *SQLite WAL pragmas* — original verdict was "wrong time, adopt only
     when a webhook connector creates real write concurrency."
     **SUPERSEDED:** PLAN.md's M1 adopts SQLite+WAL now, as the
     operational-truth store for sessions/schedules/users/settings, not
     gated on a future connector. Don't reopen this — it's the settled
     decision behind M1.
  2. *MemGPT-style core memory* — good cheap steal: our CLIs already edit
     files, so "core memory" = a wiki page the harness preamble tells the
     agent to keep updated. PLAN.md's M2-M4 (`AGI-Brain/Memory/`, memory
     tiers, nightly distillation) is the fuller realization of this idea.
  3. *FastAPI webhook + asyncio.Queue* — accurate and REQUIRED for a
     future WhatsApp connector (Meta webhooks demand <3s ack). Parked —
     not in PLAN.md's phase list; revisit after V, needs Meta business
     app + number, check free-tier limits first.
  4. *Instructor/Pydantic auto-retry* — REJECTED: we don't force JSON out
     of CLIs (review() is deterministic on plain text), and silent model
     retries burn the usage budget we just learned is scarce.
- **M2 known gap (owner-confirmed 2026-07-18, via explicit AskUserQuestion):
  schedule-triggered turns get no memory injection.** `_execute_message_schedule`
  in `core.py` calls `run_cli_async` without a `ctx=`/`TurnContext` — wiring
  it would require `test_schedules_seam.py`'s frozen `fake_run` mocks to
  accept a new kwarg, and that file is marked "never edit it." Live chat
  and Approval-mode runs (the actual PLAN.md §5.M2 accept-criteria paths)
  are fully wired. Revisit only if a future phase either needs schedule-path
  memory or gets explicit owner sign-off to touch the frozen spec.
