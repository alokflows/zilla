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
7. **Relay to a fresh session, then stop** (owner decree 2026-07-18 night;
   revised same night — owner wants to SEE the window, not a headless
   background agent): once — and only once — the full test gate is green,
   HANDOFF.md/PLAN.md are updated, and the commit is pushed to `origin
   main`, open a NEW, VISIBLE Terminal.app window running the next
   session, from the repo root:
   ```
   osascript -e 'tell application "Terminal" to activate' \
             -e 'tell application "Terminal" to do script "cd '"$(pwd)"' && claude --permission-mode bypassPermissions \"read HANDOFF.md and continue\""'
   ```
   Then end your own turn immediately after (no further phase work in this
   session) — do not close your own window (the owner can see and close it
   themselves; forcing it closed from inside a headless Bash tool call is
   not reliable). This is the whole point: each session stays small
   (cheap, fast, no giant accumulated context) instead of one session
   dragging through every remaining phase, AND the owner can watch each
   one land in its own window. **Do NOT relay if either is true:** (a) the
   test gate did not go fully green — stop and leave the failure in the
   LIVE STATUS BOARD for a human to look at, never auto-relay onto a
   broken tree; (b) PLAN.md's checklist has nothing left unchecked — the
   work is actually done, say so, don't spawn a session with nothing to
   do. The chain also just naturally stops whenever the owner's usage
   limit is hit (the new window's session simply fails/errors — that is
   fine, expected, not a bug to chase). Still SERIAL ONLY (see Working
   Agreements below) — one dispatched session at a time, never fan out
   several in parallel.
   **Known gotcha (hit once, 2026-07-18, now fixed for this path):** the
   very first `do script`-launched window sits stuck on Claude Code's
   "Do you trust this folder?" dialog forever — `do script` has no one
   there to press Enter. Fix if it ever recurs (e.g. a different clone
   path, a different machine/account): bring the window forward and send
   one Return keystroke —
   `osascript -e 'tell application "Terminal" to set index of window id <ID> to 1' -e 'tell application "Terminal" to activate' -e 'tell application "System Events" to key code 36'`
   — accepting it once persists `hasTrustDialogAccepted: true` for that
   exact path in `~/.claude.json`, so it never prompts again for repeat
   relays into the same directory.

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
**M4 (Nightly distillation + `/memory` command + change surfacing) is
COMPLETE** (PLAN.md §5.M4) — see checklist + session log below.
**H1 (Beat loop) is COMPLETE** (PLAN.md §6/H1) — see checklist + session
log below.
**H2 (Health probes + assisted re-login) is COMPLETE** (PLAN.md §6/H2) —
see checklist + session log below.
**H3 (systemd Linux service deployment) is COMPLETE** (PLAN.md §6/H3) —
see checklist + session log below.
**RECONCILED 2026-07-18 night:** this branch (`claude/zilla-harness-review-0v96bs`,
7 commits: M4/H1/H2/H3) was merged with `main`, which had independently
grown PLAN.md by ~630 lines (Phase F, K, U, H4, B inserted; R1 expanded
into "triage router + effort controller") plus an owner-reported quick
fix, while this branch shipped M4/H1/H2/H3 built-reality that main's
checklist didn't yet know about. Built reality (M4/H1/H2/H3 DONE) wins;
main's newer PLAN.md content wins. See Checklist below for the corrected,
single, non-duplicated phase order.
**Pre-F1 quick fix (Menu Close + silent-second-answer() bug) is COMPLETE**
— `test_quickfix.py` (10 checks), 868 green. See checklist + session log.
**F1 (ZILLA_HOME storage layout replaces AGI-Brain) is COMPLETE** (PLAN.md
§17/F1) — see checklist + session log below. Live cutover of the owner's
real `~/AGI-Brain`/repo `Memory/`/`zilla.db` is deferred to the owner's
next `zilla start`/any `zilla`/`install.py` invocation (migration is
wired in, idempotent, and covers all three entrypoints).
**F2 (dynamic backend + slash-command registry) is COMPLETE** — see
checklist + session log below.
**F3 (media importance + retention controls) is COMPLETE** — see checklist
+ session log below. This also built the retention-sweep mechanism itself
(H1.4b), which F3's own spec text assumed already existed but never
actually shipped during H1 — discovered and closed this session.
**F4 (system jobs invisible + silent) is COMPLETE** (PLAN.md §17) — see
checklist + session log below. Fixed the actual live heartbeat-noise bug
the owner screenshotted: `/schedules` never shows `system=1` rows anymore
(moved to `/health → System jobs`, pausable/never-deletable), and system
jobs no longer broadcast their full raw response — only an explicit
`OWNER_ALERT:` line goes out as a DM, cooldown-gated via H2's
`should_alert`/`mark_alerted`.
**NEXT UNIT OF WORK: K1-K4 (graph memory)** — PLAN.md's phase order.
THEN U1-U4, H4, B1-B2, THEN the existing R1→...→V1-V3 tail
(unchanged). Full test gate before and after every step — the gate is now
15 files: test_fixes/test_interactive/test_core/test_schedules_seam/
test_review/test_tui/test_zilla_cli/test_memory_m3/test_memory_m4/
test_harness/test_health/test_heartbeat/test_quickfix/test_service/
test_zilla_home, plus `import bot; import zilla.core; import zilla.cli;
import zilla.tui.app`. (The 9-file list in older session notes was stale —
H1-H3/F1 each added a file that was never folded into "the gate" name; a
fresh count of ALL test_*.py this session found 15, not 9 — run `ls
test_*.py` to reconfirm before trusting either number in a future
session.)
**Working branch (source of truth): `main`.** Branches consolidated
2026-07-18 night: `claude/zilla-harness-review-0v96bs`'s 7 commits are
merged into `main` and pushed; the planning branch
(`claude/python-cli-bot-planning-80x8a3`) and this execution branch are
both now fully merged, superseded shells — safe to delete. **PUSH TO
MAIN EVERY SESSION, no exceptions** (an unpushed session nearly caused
divergence before — this reconciliation is the proof of why).
**Tests:** 260+16+116+57 core + 71 review + 17 tui + 70 cli + 46 harness +
34 memory_m3 + 31 memory_m4 + 61 heartbeat + 57 health + 23 service +
10 quickfix + 25 zilla_home = **894 green** — `.venv/bin/python test_fixes.py /
test_interactive.py / test_core.py / test_schedules_seam.py /
test_review.py / test_tui.py / test_zilla_cli.py / test_harness.py /
test_memory_m3.py / test_memory_m4.py / test_heartbeat.py / test_health.py
/ test_service.py / test_quickfix.py / test_zilla_home.py`
(test_schedules_seam.py is a frozen acceptance spec — never edit it) +
`import bot; import zilla.core; import zilla.doctor; import install`.
H1 note: `_run_and_record_system` (new) replaces `_run_and_record`'s
retry-ladder/give-up-DM path for every `system=1` schedule (the heartbeat
beat AND M4's distillation) — try-acquire the per-uid lock and skip the
tick if busy, no retry, no DM; failures only logged, H2's alerts are the
intended surfacing mechanism. `reconcile_startup` now honors a per-row
`spec["_catchup"]` override ("skip" for the beat, unset/"run_once" for
distillation, which is unchanged behavior). `Memory/HEARTBEAT.md` is
real owner data (gitignored) — a previously-empty placeholder (left by
M2/M3, pending H1) gets promoted to the real template exactly once by
`memory.ensure_tree()`; any non-empty content from then on, including
the agent's own beat edits, is never touched again. **Not yet done as
part of H1 (owner/live-only, correctly deferred by design):** the brain-dir
GC and `ensure_heartbeat_schedule` are wired into `bot.py main()` but
NOT live-smoked against the real bot process — same category as M2's
live-smoke deferral. `heartbeat_interval` setting is unset today, so
`ensure_heartbeat_schedule` will seed the schedule at its 30-minute
default the next time the owner actually restarts the live bot; nothing
fires until that restart happens.
M4 note: the distillation schedule is seeded idempotently at `bot.py`
startup via `ensure_distillation_schedule` → `schedules.ensure_system_schedule`
(matches by title, `system=1`, `session="isolated"` — a throwaway
conversation every run, never written back to any session); `remove()`
now refuses to delete any `system=1` schedule (pause via `set_enabled`
instead) — a single enforcement point in `ScheduleManager` that protects
every current and future frontend. Change-surfacing DM
(`ZillaCore._autocommit_memory`) fires only when a commit actually
happened AND the turn/run was untrusted (`untrusted_input=True` from
`bot.py`'s document-ingest path, or `needs_browser(text)`, or
non-owner-originated) — an ordinary owner text turn that also changes
memory stays silent by design (§12.9 is detect-and-surface, not gate
every write). `/memory` and `/memory diff` are owner-only, reading
`memory.git_last_commit_stat`/`git_log`/`git_diff_latest` (new, purely
additive — `git_autocommit`'s existing bool-return contract used by
`test_memory_m3.py` was left untouched).
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
- [x] **M4** Nightly distillation + `/memory` command + change surfacing — DONE 2026-07-18. 717 green.
- [x] **H1** Heartbeat loop — DONE 2026-07-18. 778 green.
- [x] **H2** Health probes + assisted re-login — DONE 2026-07-18. 835 green. (Earlier P7 health-loop stash was NOT popped — re-derived clean from PLAN.md's more precise spec; stash still sits in `git stash list` as dead reference, safe to drop whenever.)
- [x] **H3** systemd Linux service — DONE 2026-07-18. 858 green.
- [x] **Quick fix (owner-reported 2026-07-18 pm)** Menu Close button:
  delete the message instead of editing it to "✓ Closed" text; and fix
  the silent-second-`answer()` bug so a failed callback is never
  indistinguishable from a successful one (P4). DONE 2026-07-18 night.
  868 green. Live smoke NOT done (owner's call, see Notes below).
- [x] **F1** ZILLA_HOME storage layout replaces AGI-Brain (PLAN §17) — DONE 2026-07-18. 894 green. Live cutover of the owner's real `~/AGI-Brain`/repo `Memory/`/`zilla.db` NOT done by me — deferred to the owner's next `zilla start`/`zilla doctor`/any `zilla` CLI invocation (migration is wired in and idempotent; see Notes below).
- [x] **F2** Dynamic backend registry — no hard-coded backend buttons, includes slash-command registry (PLAN §17). DONE 2026-07-18. 950 green. See session log below for full detail.
- [x] **F3** Media importance + retention controls (PLAN §17). DONE 2026-07-18. 980 green (full 15-file gate, freshly recounted — see note above). Built the retention-sweep mechanism itself (H1.4b) as a prerequisite it turned out was never actually shipped. See session log below for full detail.
- [x] **F4** System jobs invisible + silent — fixes the live heartbeat noise the owner screenshotted (PLAN §17). DONE 2026-07-18. 1000 green (full 15-file gate). See session log below for full detail.
- [ ] **K1-K4** Relational graph memory (PLAN §6): schema+indexer, entity linking, curiosity loop, /graph views.
- [ ] **U1-U4** Generative UI + design system + presence (PLAN §7): ZUI protocol, agent education, STYLE.md, pinned status card.
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

| 2026-07-18 | **M4 COMPLETE**: nightly distillation `system` schedule seeded idempotently at `bot.py` startup (`ensure_distillation_schedule` → new `schedules.ensure_system_schedule`, matches by title, daily 03:30, `session="isolated"` so it never advances any real conversation) — `ScheduleManager.remove()` now refuses to delete any `system=1` row (pause via existing `set_enabled` instead), a single enforcement point covering every current/future frontend. Change-surfacing DM (§12.9 injection-surface mitigation, detect-not-prevent): `core._autocommit_memory` gained an `untrusted` kwarg — when a commit actually happened AND the turn/run was untrusted (`handle_message`'s new `untrusted_input` kwarg, wired from `bot.py`'s document-ingest path; or `autoharness.needs_browser(text)`; or non-owner-originated for both live turns and scheduled runs) it broadcasts an `Alert` naming the changed files + commit hash; an ordinary owner text turn that also changes memory stays silent by design. New read-side helpers in `memory.py` (`git_last_commit_stat`, `git_log`, `git_diff_latest` — purely additive, `git_autocommit`'s existing bool-return contract untouched) back the new owner-only `/memory` command (MEMORY.md + today's journal + last 5 commits) and `/memory diff` (latest unified diff, chunked/fenced). New `test_memory_m4.py` (31 tests, all 3 of PLAN.md §5.M4's Accept criteria): distillation schedule survives 3 simulated "restarts" (fresh `ScheduleManager` against the same persisted path) as exactly one row, and is pausable-not-deletable; change-notice fires for a document-ingest turn and for a non-owner-originated turn but NOT for an ordinary owner turn (even though both commit the identical change) and NOT when nothing actually changed; git read-helpers verified against a real 2-commit history; `/memory` and `/memory diff` exercised against `bot.cmd_memory` directly via minimal duck-typed fake `Update`/`Context` objects (no existing fake-Telegram-Update precedent in this repo, so this establishes the pattern) — owner-only gate confirmed, live-Telegram smoke remains the owner's call per the M2 precedent. 717 green (686 + 31, zero regressions).
| 2026-07-18 | **H1 COMPLETE** (PLAN.md §6/H1, all 5 steps, one commit `02d983b`): `zilla/heartbeat.py` — deterministic zero-AI-call skip check (`has_actionable_content`/`should_skip`, reads `Memory/HEARTBEAT.md`), per-fire beat prompt (`build_beat_prompt`: "It is {now} ({tz}). Last beat: {last}. Read HEARTBEAT.md…"), `prepare_beat()` injects that fresh prompt into the heartbeat schedule only and returns `None` to signal skip (every other system job, incl. distillation, passes through unchanged), `ensure_heartbeat_schedule()` idempotently seeds/pauses/resumes from the `heartbeat_interval` setting (0=off, default 30 min). `zilla/schedules.py`: `reconcile_startup()` now honors a per-row `spec["_catchup"]` override for `system=1` rows — `"skip"` (the beat: a missed beat is worthless) vs. unset (distillation: unchanged run-once-on-catch-up behavior); a `system=0` row's own `_catchup` key is never read. `zilla/core.py`: new `_run_and_record_system` — try-acquire the per-uid lock (skip the tick if busy, no blocking wait), no retry ladder, no give-up DM, failures only logged (H2's job to surface); `_run_and_record` now routes every `system=1` schedule here instead of the old retry/DM path. On agy, an isolated `system=1` schedule reuses one persistent `named:__scratch_<id>` conversation instead of minting a fresh one every fire, avoiding the global new-conversation lock + full onboarding preamble 48x/day (claude/opencode fresh conversations are cheap, left alone). `zilla/cli_engine.py`: `gc_orphaned_conv_dirs()` — a startup-only sweep (not per-beat) deleting agy `BRAIN_DIR` subdirs unreferenced by any session's `conv_id` and older than 7 days by mtime; `zilla/sessions.py` + `zilla/store.py` gained `all_conversation_ids()`/`sessions_all_conv_ids()` to supply the "still referenced" set. `zilla/memory.py`: `HEARTBEAT_TEMPLATE` (the PLAN.md-specified Daily/Watching/Follow-ups seed) now actually gets written by `ensure_tree()` — M2/M3 had left `HEARTBEAT.md` intentionally empty pending H1; the promotion happens only when the file is missing OR empty, so both a fresh install and the two prior sessions' already-deployed empty placeholder get seeded exactly once, and any non-empty content (owner or agent edits) from then on is never touched again; `read_heartbeat()` added. `zilla/harness.py`: one new protocol line in the owner memory block — "keep an eye on / remind / follow up on something recurring" → add to `HEARTBEAT.md`. `bot.py main()`: wires `heartbeat.ensure_heartbeat_schedule(...)` and a try/except-wrapped `gc_orphaned_conv_dirs` startup sweep (logs count removed, never blocks startup on failure). New `test_heartbeat.py` (61 tests, all of PLAN.md §6/H1's "Accept" criteria): empty-file skip (zero AI calls, verified via a `fake_run` spy that must never be called) and the busy-lock case (skip without blocking, no queueing); actionable-file fire with the placeholder prompt provably replaced by the real time-stamped beat prompt; `HEARTBEAT_OK` suppressed vs. any other response delivered — all against the REAL heartbeat title/schedule end-to-end through `core._run_and_record`, not a generic system=1 fixture (test_memory_m3.py already covers the generic quiet-run gate); schedule idempotency across 3 simulated restarts + `heartbeat_interval=0` pause/resume including "0 from a clean store creates nothing"; template seeding never clobbers an edit, and an emptied-out file gets exactly one re-seed; `reconcile_startup`'s catchup-override matrix (beat skips, distillation still catches up, a user schedule's own `_catchup` key is ignored); brain-dir GC's referenced/age matrix + missing-`BRAIN_DIR` no-op. 778 green (717 + 61, zero regressions). Verified after the test run: no `.git` inside the real `Memory/` tree, no stray real `zilla.db`. **Deliberately NOT done as part of this session (live-only, owner's call, same category as M2/M3's live-smoke deferrals):** the live bot has not been restarted, so `heartbeat_interval` is unset and the beat schedule has not actually been seeded or fired against the real `Memory/HEARTBEAT.md`/Telegram yet — next `zilla stop && zilla start` will seed it at the 30-minute default and the first real beat will follow within 30 minutes of that restart. |
| 2026-07-18 | **H2 COMPLETE** (PLAN.md §6/H2, all 3 steps, one commit `d088c70`): new `zilla/health.py` — deterministic probes (`probe_disk` ≥500MB free, `probe_db_writable`, `probe_backend_path` for agy/claude binaries on PATH, `probe_agy_login` via cached `agy_reachable()`, `probe_claude_login` via a REAL `claude -p "ping" --output-format json` subprocess call hard-capped at 1×/6h since claude has no cheap logged-in signal the way agy's cached `agy models` call gives us — `backends.claude_identity()`'s `claude auth status` can say `loggedIn=True` for a session that no longer actually generates); `run_probes(active_backend, db_path)` always runs disk/db/both-binary-paths and probes login freshness for only the currently-active backend (probing the idle one would burn a real claude ping for no benefit until R2's fallback chain needs it); per-kind `ALERT_COOLDOWN` (6h) state machine (`should_alert`/`mark_alerted`/`clear_alert`/`is_alerted`) — fires once, silent while the same kind stays broken, a recovery clears the cooldown so the next NEW failure alerts promptly; `recovery_instructions(kind)` is the honest-ceiling text (detect + precise plain-language steps only — open a terminal, run `agy`/`claude`, sign in, then `/doctor`; **no scripted OAuth flow, no keychain token injection, no speculative login automation of any kind**, exactly the owner-confirmed ceiling PLAN.md's H2 spec calls for); `beat_flag_lines()` feeds H1's beat prompt one `"System flag: {kind} — already DM'd owner."` line per still-unresolved probe, so the agent never re-raises something the health loop already DM'd. `zilla/core.py`: probes run on their OWN 5-minute asyncio timer (`_HEALTH_TICK`), independent of `heartbeat_interval` (beat=0/off must never silence probes) — `health_probes_enabled`/`_health_task` follow the exact same opt-in pattern `memory_autocommit_enabled` established (default `False` in `__init__`, only `bot.py`'s real `main()` flips it `True`, `start()`/`stop()` spawn/cancel it exactly like `_sched_task`/`_bridge_task`), so no test-constructed `ZillaCore` — even one that calls `start()` — ever spawns a real `claude -p ping` subprocess on a timer; `_health_tick()` self-heals a failing disk probe first via the existing `gc_orphaned_conv_dirs` brain-dir GC (`max_age_days=1`, more aggressive than the startup sweep's 7-day window) before ever alerting — anything that self-heal fixes clears silently, anything it can't (or any other failing probe) broadcasts exactly one `Alert` with `recovery_instructions()` appended and logs `health_alert`. `zilla/heartbeat.py`: `build_beat_prompt()` gained `flags=`, `prepare_beat()` now prepends `health.beat_flag_lines()` ahead of the usual "It is {now}…" text. `bot.py main()`: `core.health_probes_enabled = True`, same one-line opt-in as M3's autocommit flag. New `test_health.py` (57 checks): every probe exercised with injected failures — disk (real `disk_usage` against an absurd threshold), db_writable (nonexistent dir), backend_path (missing binary), agy_login (`config.agy_reachable` monkeypatched true/false), claude_login (`subprocess.run` monkeypatched for success/is_error/timeout/missing-binary — **no real `claude` subprocess is ever invoked by a test**, and the 6h TTL cache is verified by asserting the fake only gets called once across two probe calls); the full cooldown state machine including the per-kind-independence case; `recovery_instructions` coverage for all 6 known kinds + the unknown-kind fallback; `beat_flag_lines()` formatting/sorting/clear-on-recovery; `heartbeat.build_beat_prompt(flags=...)` prefix placement; `core.py` integration — `health_probes_enabled` off by default, `start()`/`stop()` only create/tear down `_health_task` when explicitly turned on (with a stubbed `_health_tick` so the lifecycle test never runs a real probe round), and `_health_tick()` exercised directly against a monkeypatched `zilla.health.run_probes` (patched on the `health` module object itself, since `core.py` does `from zilla import health as _health` locally inside each method rather than importing it at module scope) for: alert-once-then-cooldown-silent, recovery-clears-and-a-fresh-failure-realerts-immediately, a self-healing disk failure that never alerts, and a disk failure self-heal can't fix that still does. 835 green (778 + 57, zero regressions). Verified after the test run: no `.git` inside the real `Memory/` tree, no stray real `zilla.db`. **Deliberately NOT done as part of this session (live-only, owner's call, same deferral category as M2/M3/H1's live-smoke items):** no live smoke of a full re-login round-trip against the actual running bot (kill the real agy/claude login, confirm the DM fires with correct instructions, log back in, confirm the alert clears) — that needs the owner's actual CLI sessions and is not something to trigger unattended. |
| 2026-07-18 night | **Branch reconciliation** (owner flagged staleness mid-session): the execution branch `claude/zilla-harness-review-0v96bs` had 7 unpushed commits (M4/H1/H2/H3) and a PLAN.md that had fallen ~630 lines behind `main`, which had independently absorbed the planning branch plus new owner decisions (Phase F/K/U/H4/B inserted, R1 expanded into "triage router + effort controller", a quick-fix spec, slash-command-per-skill). Pushed the 7 commits, merged `origin/main` in (`0280984`; PLAN.md merged clean, HANDOFF.md had 2 conflicting hunks resolved: built reality — M4/H1/H2/H3 DONE — wins, main's newer plan content wins, duplicate H1-H3 "genuinely new" rows dropped from the checklist), verified `origin/main`/`origin/claude/zilla-harness-review-0v96bs`/`origin/claude/python-cli-bot-planning-80x8a3` were all identical ancestors, pushed the merge to `main`, then executed the owner-decreed one-time chore: deleted both stale branches from origin (`git push origin --delete`), confirmed only `main` remains (`git ls-remote --heads origin`), switched local checkout to `main`. Full test gate reran clean at 858 green post-merge, zero regressions. **Then shipped the pre-F1 quick fix** (commit `4149092`): `bot.py` `_cb_misc` menu_close now `await query.message.delete()` (falls back to stripping the reply markup if delete raises, never the old "✓ Closed" text edit); `handle_callback`'s outer exception path no longer attempts a doomed second `query.answer()` — it edits the message with a calm one-line failure notice, falling back to a new `context.bot.send_message` if the edit also fails. New `test_quickfix.py` (10 checks, all of the spec's Accept criteria except live smoke): menu_close deletes + sends no text, falls back correctly when delete() raises; a `_cb_*` helper raising surfaces a visible non-silent failure line with no stack trace, and falls back to a new message when editing also fails. 868 green (858 + 10, zero regressions). **Not done (live-only, owner's call):** tap Close in the real chat; force a real callback error and confirm the failure line appears. |
| 2026-07-18 | **H3 COMPLETE** (PLAN.md §6/H3, both steps, one commit `da58805`): `install.py` gained a new `--service` flag (Linux-only; on macOS/Windows it prints a clear message and exits 0 rather than doing nothing silently or erroring — "nothing Windows breaks" per the accept criteria) that writes + enables `~/.config/systemd/user/zilla.service` via two new functions: `systemd_unit_content(py_path, base_dir)` (pure, golden-testable) and `write_service()` (I/O: writes the unit, `systemctl --user daemon-reload` + `enable --now`, prints a `loginctl enable-linger $USER` hint rather than running it — a login/session-policy change that shouldn't happen silently on the owner's behalf). `Restart=on-failure`, deliberately NOT `Restart=always` like the pre-H3 Linux autostart branch used to write: `run_background.py` already exits 0 cleanly on an intentional `zilla stop` (its own `zilla.stop`-file check), so `on-failure` respects that and only restarts systemd's copy on an actual crash — layered on top of `run_background.py`'s own pre-existing ~7s internal restart loop for `bot.py` itself, not replacing it. `setup_autostart()`'s Linux branch now calls the same `write_service()` instead of duplicating unit-writing logic (single source of truth caught at the future-cost-check step); `disable_autostart()` reuses the same `SYSTEMD_UNIT_PATH` constant. `zilla/doctor.py`: new `check_systemd_service()` — Linux-only (`applicable=False` elsewhere, since macOS uses the LaunchAgent and Windows the Startup shortcut, neither of which has a systemd unit to report on), reads `systemctl --user is-active`/`is-enabled`, never raises on a missing `systemctl` binary or a timeout; folded into `environment_report()`/`format_report()` so `zilla doctor` reports service state on Linux. New `test_service.py` (23 checks): exact golden text of the unit file (`ExecStart`/`WorkingDirectory`/`Restart=on-failure`/`WantedBy=default.target`); `write_service()` exercised against an isolated tmp systemd dir (module-level `SYSTEMD_UNIT_DIR`/`SYSTEMD_UNIT_PATH` swapped out for the duration of each test, restored after) with `subprocess.run` mocked for success/missing-binary/nonzero-exit — **no real `systemctl` is ever invoked and the real `~/.config/systemd/user/zilla.service` is never touched by a test**; `check_systemd_service()`'s output-parsing matrix (active+enabled, inactive+disabled, unit-not-installed, missing systemctl, timeout) exercised via a monkeypatched `platform_compat.IS_LINUX=True` since this dev machine is macOS (the real, unmocked `applicable=False` case is also asserted directly). 858 green (835 + 23, zero regressions). Verified after the test run: no stray `zilla.db` from tests (the real one is the live bot's, gitignored, pre-existing), no real systemd unit written to `~/.config/systemd/user/`. **Deliberately NOT done as part of this session (live-only, owner's call, same deferral category as every prior phase's live-smoke items):** the actual "reboot → bot up, missed schedules caught up" round-trip needs a real Linux box with the service enabled — that machine doesn't exist yet (dev is the owner's MacBook; the Ubuntu laptop deployment is still ahead per §3's "Deployment posture"). `reconcile_startup`'s catch-up logic itself is unrelated to H3 and already covered live-independent by `test_heartbeat.py`/`test_fixes.py`. |
| 2026-07-18 | **F1 COMPLETE** (PLAN.md §17/F1, `feat(F1)` commit): `zilla/config.py` gained `ZILLA_HOME` (env-overridable, default `~/Zilla`) and the storage constitution's four roots — `Media/{Inbox,Kept}`, `Outbox/`, `Runtime/{logs,zilla.db,zilla.pid,zilla_bot_instance.lock,cache/mcp,Bridge}`, `Memory/` — replacing the old `AGI_BRAIN_DIR`/repo-root `zilla.db`/repo-root `Memory` constants; `ensure_dirs()` rewritten to the new tree (Memory/ stays `memory.ensure_tree()`'s own job, never raced). `zilla/migrate.py` gained `migrate_zilla_home()` — idempotent, non-destructive (`_move_once` never clobbers an existing destination, never deletes a source on failure): moves legacy `~/AGI-Brain`'s Inbox/Outbox/Bridge AND the repo-root `Memory/`+`zilla.db` (+`-wal`/`-shm`/`.bak`/`.bak.1`) that M1-M4 already created there before F1 existed — **documented deviation from F1's literal spec text** (which assumed AGI-Brain still held Memory/state; M1-M4 shipped first and anchored them at the repo root instead) — then replaces `~/AGI-Brain` with a symlink to `~/Zilla` IF nothing unexpected is left inside it (a stray file blocks the symlink step rather than being silently deleted). Wired as `config.run_zilla_home_migration()`, called first-thing in both `bot.py main()` and `zilla/cli.py main()` (covers every CLI subcommand: doctor/start/stop/status/config/bare) and in `install.py main()` (covers `python install.py --doctor`/interactive install directly) — **all three**, because doctor/settings reads (`get_backend()`/`get_model()` → `store.get_store(SETTINGS_FILE)`, `SETTINGS_FILE = DB_FILE`) lazily create the new-layout file on first touch, which would make ZILLA_HOME "already exist" and cause the migration to silently no-op forever if any one of these entrypoints ran before it. Path fences re-anchored across `bot.py` (`safe_send_file`'s realpath allowlist), `harness.py` (trust_log now shares `config.LOG_DIR` instead of computing its own divergent repo-root `logs/`), `bot_instructions.md`/`interactive.py`/`cli_engine.py`/`media.py`/`memory.py`/`formatter.py`/`cli.py`/`install.py` (`is_running`/`read_pid`/doctor's `db_path`) — a stale `AGI_BRAIN_DIR` import left in `zilla/backends.py` (unused, would have been an import-time crash) was caught and removed by the path-audit sweep. `zilla/doctor.py` gained a `home` field (path + exists) in `environment_report()`/`format_report()`. New `test_zilla_home.py` (25 checks): full migration round-trip, no-op-when-ZILLA_HOME-exists, `_move_once`'s never-clobber contract, AGI-Brain-left-alone-on-stray-leftovers, no-legacy-sources-is-noop, doctor shows home before/after creation, and a path-audit grep gate (no production file outside `config.py`/`migrate.py` may still compose an `AGI_BRAIN_DIR`/`AGI-Brain` reference). `test_zilla_cli.py`'s `test_format_report_smoke` fixture updated for the new required `home` key. 894 green (868 + 25 new + 1 updated). **Near-miss caught and reversed before any commit:** an ad-hoc isolation smoke-test only overrode `HOME` via env var while running from the real repo checkout — but `_LEGACY_MEMORY_DIR`/`_LEGACY_DB_FILE` are `BASE_DIR`-relative (repo root), not `HOME_DIR`-relative, so the real repo-root `Memory/`+`zilla.db` got genuinely `shutil.move`'d into a `/tmp` sandbox. Caught immediately (nothing was deleted — `shutil.move` only removes a source after its destination copy succeeds), verified byte-identical via `md5`, moved both back to the repo root, confirmed `git status`/`git check-ignore` showed no diff. **Lesson for future sessions: testing this migration code needs full monkeypatching of `config`'s module attributes (`ZILLA_HOME`/`_LEGACY_*`), never just an env-var `HOME` override** — `test_zilla_home.py` calls `migrate_zilla_home()` directly with explicit kwargs for exactly this reason, never through the env-var-sensitive wrapper. Live cutover of the owner's actual `~/AGI-Brain`/repo `Memory/`/`zilla.db` deliberately NOT done by me — happens automatically, safely, idempotently on the owner's next `zilla start` or any `zilla`/`python install.py --doctor` invocation (same live-deferral pattern as every prior phase). |

| 2026-07-18 | **F2 COMPLETE** (PLAN.md §17/F2, `feat(F2)` commit): new `zilla/backend_registry.py` — every backend self-describes ONE `BackendAdapter` (`name`/`label`/`login_cmd`/`model_flag`/`hint`/`binary()`/`identity()`/`models()`/`dispatch()`), registered once at module load (agy, claude); `status_all()` returns the shared `{installed, path, ok, detail}` shape every UI surface now reads. All adapter functions do their real imports INSIDE the closure (never at module load) so this module can never circularly import `cli_engine.py`/`backends.py`/`config.py`, which read the registry back. Every previously hard-coded agy/claude branch now derives from the registry: `cli_engine.backend_status()`/`_dispatch_turn()` delegate to `adapter.identity()`/`adapter.dispatch()`; `doctor.py`'s `environment_report()`/`format_report()` loop over `status_all()` instead of two copy-pasted print blocks; `keyboards.py`'s `kb_model()`/`kb_settings()` build a "Use X" button per `installed_backends()` instead of one binary toggle button; `bot.py`'s `_cb_model`/`_cb_settings` callbacks became `model_use_<name>`/`set_backend_<name>` (validated against `backend_registry.get()`) replacing the old binary `model_switch_backend`/`set_toggle_backend`; `_backend_panel()`/`_model_note()` genericized off the adapter's normalized fields/`hint` instead of `if backend == "claude": ... else:`; `zilla/configmenu.py`'s `BACKEND_CHOICES` now `= backend_names()` (module-level, since adapters register at their own import time) instead of a literal list that included the not-yet-built `opencode`. Second half of F2 — the unified slash-command registry — replaced `bot.py`'s separate `_BASE_COMMANDS`/`_OWNER_COMMANDS` lists and 19 individual `add_handler(CommandHandler(...))` calls with one `COMMAND_REGISTRY: list[_CommandSpec]` (`name`/`description`/`handler`/`scope∈{default,owner,hidden}`/`aliases`), read by both `_register_commands()` (builds the two `BotCommand` menu lists) and the handler-setup loop (`for spec in COMMAND_REGISTRY: for name in (spec.name, *spec.aliases): app.add_handler(...)`) — a structural fix, not just a data fix, since there is now only one `CommandHandler(` call site in the whole file, so the two lists cannot drift apart again. This also fixed a real pre-existing bug found during the audit: `/start`, `/memory`, and the `/schedules` alias had working handlers but NO menu entry at all (now explicit: `start`→`hidden`, `memory`→`owner` alongside `adduser`/`removeuser`/`listusers`, `schedules`→ an alias of `schedule` rather than its own dangling handler). New tests in `test_zilla_cli.py` (+56 checks): `backend_registry` — agy/claude both present, `get()` unknown→`None` and is case/whitespace-tolerant, `status_all()` shape + coverage, every adapter has a label and a list-returning `models()`; `COMMAND_REGISTRY` — no duplicate name/alias, every scope valid, every handler callable, the four owner-gated commands are `scope="owner"` (regression guard for the exact bug just fixed), and a grep-gate proving bot.py contains zero literal `CommandHandler("name", ...)` call sites outside the one registry-driven loop. `test_format_report_smoke`'s fixture updated from the old ad-hoc `{"reachable": bool}` shape (which the new code silently no-oped against without erroring — a real gap, caught while auditing, not by the test failing) to the real `{installed, path, ok, detail}` shape, with assertions that now actually check the per-backend detail text renders. 950 green (894 + 56, zero regressions). Config-menu note: `opencode` (R3, not yet built) intentionally no longer appears in `BACKEND_CHOICES` or its menu text — per F2's own design intent ("a future adapter... shows up here with no edit"), it will reappear automatically the moment R3 registers it, rather than being hand-listed as a placeholder. |
| 2026-07-18 | **F3 COMPLETE** (PLAN.md §17/F3): started by discovering F3's own spec text was wrong about its dependencies — it says "sweep behavior itself is H1.4b", but a targeted grep found H1 only ever shipped H1.4a (`gc_orphaned_conv_dirs`); the retention sweep never existed. Built it here as a prerequisite, then the two things F3 actually asks for. **(0) Retention sweep (the H1.4b gap):** `zilla/config.py` gained `MEDIA_RETENTION_DAYS` (env default 30) + `get_media_retention_days()` (same `get_setting`-wrapping pattern as `get_idle_kill_after`). `zilla/media.py` gained `sweep_stale_media(retention_days, now=None)` — deletes Inbox/Outbox files older than the threshold via the EXISTING path-validated `delete_inbox_file`/`delete_outbox_file` (no new delete logic to audit); `retention_days=0` is a hard no-op; `Media/Kept/` is exempt by construction, not by a special-case check — `get_inbox_items()`/`get_outbox_items()` never scan it, so there is nothing to accidentally sweep. `zilla/core.py` gained a THIRD opt-in background loop (`media_sweep_enabled`/`_media_sweep_task`/`_media_sweep_loop`/`_media_sweep_tick`, hourly), following the exact `health_probes_enabled` pattern (default `False`, only `bot.py`'s real `main()` flips it `True`, `start()`/`stop()` spawn/cancel it identically) so no test-constructed `ZillaCore` ever deletes real files on a timer; the tick re-reads `get_media_retention_days()` fresh every hour so a `/settings` change takes effect without a restart. **(1) Owner settings, two surfaces:** Telegram `/settings → 🗄️ Storage` (`keyboards.kb_settings_storage()`, owner-gated same as the backend-switch row) — four buttons, values only (Off/30/60/90), a ✅ marks the current one, never free text; `bot.py`'s `_cb_settings` gained `set_storage`/`set_retention_<n>` branches (both already covered by the existing `data.startswith("set_")` dispatch rule — zero `handle_callback` changes needed). `zilla/configmenu.py` gained terminal menu item 9 (`_menu_retention`, `RETENTION_CHOICES`/`RETENTION_VALUES`) for parity with every other F2-era setting. **(2) Importance recognition, two paths, PLAN.md's "same graduation":** model-driven — `zilla/harness.py`'s `_memory_block()` gained one new owner-only protocol bullet (mirroring the existing HEARTBEAT.md line) telling the agent that when the owner calls a just-sent file important, copy it to `Media/Kept/` and journal one line. Deterministic — `zilla/media.py` gained `keep_file(path)` (path-validated like `delete_inbox_file`; COPIES, never moves, matching the model path's verb exactly; a same-name collision in Kept gets a numeric suffix, never an overwrite) plus `keep_token(path)`/`resolve_keep_token(token)` — a short sha1-based callback_data identifier, chosen over the codebase's usual `{category}_{index}` list-position convention because this button sits on a single fresh upload ack, not a browsed/paginated list, so there's no natural index and a hash is simpler than inventing one; `keyboards.kb_keep(path)` is the single "⭐ Keep" button, attached via `reply_markup=` to the five bare "saved, no caption/no analysis" acknowledgment replies in `handle_voice`/`handle_audio`/`handle_photo`/`handle_document`/`handle_video` (the AI-response paths are untouched — this button is deterministic-ack-only, per PLAN's "no model judgment" framing). `bot.py`'s `_cb_inbox` gained an `ibx_keep_` branch (already covered by the existing `data.startswith("ibx_")` dispatch rule) that resolves the token, calls `keep_file`, journals via `memory.append_journal`, and strips the button on success. New tests: `test_fixes.py` (+8 test functions) — `keep_file` copies without moving the original, refuses paths outside Inbox, collision-suffixes rather than overwrites; `keep_token`/`resolve_keep_token` round-trip; `sweep_stale_media` deletes only what's past the cutoff, is a hard no-op at 0, and — the actual accept-criteria assertion — a kept copy survives an ancient mtime while its Inbox original (same age) does NOT, proving the Kept exemption is real and not just "nothing happened to get old enough"; `media_retention_days` default-and-persist via `config.get_media_retention_days()`. `test_zilla_cli.py` (+4 test functions) — `configmenu` round-trip through menu item 9; `kb_settings_storage()` renders exactly one ✅ on the current selection with all four `set_retention_*` callbacks present; `kb_keep()`'s callback_data matches `media.keep_token()` and stays well under Telegram's 64-byte limit; a structural grep-gate confirming the three new branches exist in `bot.py` and are actually reachable from `handle_callback`'s dispatch rules (same style as F2's `CommandHandler` grep-gate). **980 green — a full FRESH count across all 15 `test_*.py` files** (not a delta off the old "950", because this session discovered the tracked "9-file gate" list in this doc's own summary section was stale: H1/H2/H3/H3-quickfix/F1 each added a test file — `test_harness.py`/`test_health.py`/`test_heartbeat.py`/`test_service.py`/`test_zilla_home.py` — that was never folded into "the gate" name up top; corrected the summary section above to name and count all 15, and to say so explicitly for the next session). Zero regressions across all 15. |
| 2026-07-18 | **F4 COMPLETE** (PLAN.md §17/F4, "the heartbeat-noise fix"): the ACTUAL bug behind the owner's screenshotted noise was in `core._run_and_record_system` — it broadcast a full `ScheduledResult` (rendered by `bot.py` as "⏰ Scheduled — {title}" + the ENTIRE raw response) any time a system job's reply wasn't EXACTLY the literal string `HEARTBEAT_OK`, so any mildly-interesting heartbeat finding produced a noisy full-response DM. **(1) `/schedules` is owner-schedules-only:** `zilla/schedules.py`'s `ScheduleManager.list()` gained `include_system: bool = False` — a READ-TIME filter (system rows were already correctly flagged `system=1` at creation by pre-existing M4/H1 code, so no data migration was needed; the two internal callers that must still see system rows, `ensure_system_schedule`/`ensure_heartbeat_schedule`, now explicitly pass `include_system=True`) — plus a new `list_system(user_id)` for system-only reads. **(2) `/health → System jobs` panel:** `keyboards.py` gained `kb_health()` (adds a "System jobs" entry) and `kb_sysjobs(items)` (status marker + last-run + a per-job `sysjob_toggle_<id>` pause/resume button, NEVER a delete button — `ScheduleManager.remove()` already refused system rows since F1); `bot.py`'s `_cb_misc` gained `menu_sysjobs`/`sysjob_toggle_` branches (no dispatch-table change needed, both already fall through to `_cb_misc`) plus a new `_sysjobs_panel_text()` helper, and `menu_health` now renders `kb_health()` instead of the old generic `kb_back()`. **(3) Silent-output contract:** `zilla/core.py` gained `_OWNER_ALERT_RE` (`^OWNER_ALERT:\s*(.+)$`, multiline) and `_maybe_alert_owner_from_system_job(sid, response)`; `_run_and_record_system` no longer broadcasts `ScheduledResult` under any circumstance — a system job's full output goes to the log only, and the ONLY DM path is an extracted `OWNER_ALERT:` line, cooldown-gated by reusing H2's existing generic `zilla.health.should_alert`/`mark_alerted` machinery under key `f"schedule_alert:{sid}"` (one alert per schedule per 6h cooldown window, same as a health probe). `zilla/heartbeat.py`'s `build_beat_prompt()` and `bot.py`'s `DISTILLATION_PROMPT` both now teach the agent the `OWNER_ALERT: <one calm sentence>` convention as the only way to reach the owner from a system job. **(4) Migration:** satisfied entirely by (1)'s read-time filter — no separate migration code needed, confirmed via `test_schedule_list_hides_system_jobs_by_default`. New tests: `test_heartbeat.py` — renamed and inverted `test_run_and_record_system_fires_with_injected_prompt_and_delivers` → `..._and_stays_silent` (F4 changes real behavior, not just a rename) plus 3 new tests (OWNER_ALERT line delivers as a bare `Alert` with only that line, not the whole response; repeated OWNER_ALERT for the same schedule is cooldown-gated to one DM; the silence contract applies to any `system=1` job, not just the heartbeat by title). `test_fixes.py` — `test_schedule_list_hides_system_jobs_by_default`, `test_ensure_system_schedule_still_finds_existing_across_restart`. `test_zilla_cli.py` — `kb_health()` links to System jobs, `kb_sysjobs()` renders status/toggle/no-delete, structural grep-gate confirming `bot.py`'s new branches are wired and the sysjobs panel is driven by `list_system()` not the owner-facing `list()`. **Regression found and fixed mid-session:** the new `list()` default broke two PRE-EXISTING tests that located the heartbeat/distillation schedule via a bare `.list(OWNER)` call expecting system rows included — `test_ensure_heartbeat_schedule_idempotent_and_toggle` (test_heartbeat.py) and `test_distillation_schedule_seeded_exactly_once` (test_memory_m4.py) — both fixed by adding `include_system=True` to those specific lookup calls; `bot.py`'s 5 real `/schedules`-surface call sites were correctly left on the new owner-only default (that's the point of F4.1). Also fixed a cosmetic truncation bug caught by the new `kb_sysjobs` test: the paused-marker test wanted the full word "distillation" in the label but `title[:24]` cut "Nightly memory distillation" mid-word — widened to `title[:40]` (only affects `kb_sysjobs`, `kb_schedules`'s separate `[:24]` truncation for the owner-facing `/schedules` list is untouched). **1000 green — full 15-file gate, zero regressions** (980 + 20 new F4 tests). |

### Notes (only what a future session needs)

- **Quick fix spec (owner-reported 2026-07-18 pm, do this FIRST, before
  F1) — DONE 2026-07-18 night, `test_quickfix.py` (10 checks), 868 green.
  Live smoke (tap Close in the real chat; force a real callback error)
  NOT done — owner's-call live verification, same deferral category as
  every prior phase's live-smoke items:**
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
