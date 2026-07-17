# ZILLA — HANDOFF

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

## 6. THE PLAN

Phases are dependency-ordered. Do them in order. Each phase lists Goal,
Steps, and Acceptance criteria — a step is done only when its acceptance
criteria are verified by actually running things.

### PHASE 0 — VERIFY REALITY ON THIS MACHINE (blocks everything)

**Goal:** replace assumptions with the installed truth. No building yet.

Steps:
1. Capture `agy --help`, `claude --help`, `opencode --help` (and
   `opencode run --help` or equivalent). Record: model selection flags,
   print/headless mode, conversation persistence flags, `--add-dir` or
   workspace flags, output formats, approval/permission flags,
   `--print-timeout` default.
2. Test whether agy reads `GEMINI.md` / `AGENTS.md` from its working dir
   (put a distinctive instruction in one, ask a question, see if it obeys).
3. Re-test Trap #2 (sandbox): in the most restrictive permission mode each
   CLI offers, run a headless turn that tries to write a file to a temp
   dir. Record whether the write happened. SAFE probes only.
4. Record login state of each CLI, `agy models` output, opencode's model
   list and how a model is chosen per run.
5. Run both test suites on macOS; note any platform-specific failures.

**Acceptance:** a findings table committed to `docs/dev/PHASE0_FINDINGS.md`;
any finding that changes this plan is raised to the owner BEFORE Phase 1.

### PHASE 1 — CORE EXTRACTION (the foundation)

**Goal:** an interface-agnostic core so the TUI and Telegram are both thin
frontends. The Telegram bot's behavior must not change.

Steps:
1. Design first (orchestrator): define the core API on paper — roughly:
   `core.handle_message(user, text, attachments) → stream of (progress |
   response | ask | files)`, plus session ops, settings ops, schedule ops,
   approval ops, health ops. Present to the owner before coding.
2. Create `zilla/` package; move pure modules in unchanged first
   (`sessions`, `users`, `schedules`, `schedule_parse`, `verify`,
   `autoharness`, `interactive`, `harness`, `config`, `platform_compat`,
   `cli_engine`, `backends`, `media`, `formatter`) with import shims so
   `bot.py` and tests keep passing at every commit.
3. Extract from `bot.py` into the core, one seam at a time: turn pipeline
   (locks, approval hold, run, verify, deliver), scheduler runtime, bridge
   watcher, health. `bot.py` shrinks to Telegram I/O + menus.
   **Scheduler seam additions** (cheap now, painful later — RESEARCH §7
   items 2–6): add schema fields `payload_type` (system-event = deliver
   canned text with ZERO model call / message / command), per-job `session`
   mode (isolated default / main / named), pinned `backend`+`model` at
   creation (fallback + one-time owner note if gone at fire time); retry
   backoff ladder 30s→60s→5m→15m→60m reset-on-success (keep Zilla's
   still-fires-next-occurrence); atomic `schedules.json` writes; outbound
   scheduled messages carry their session id so a reply can continue it;
   a scheduled run may NOT create schedules (recursion guard).
4. The core exposes the ask/answer bridge as events so ANY frontend
   (terminal included) can relay OTP/confirm prompts.

**Acceptance:** all 192 tests green plus new tests for the core seams; a
live Telegram round-trip works unchanged (text, voice, file, schedule,
approval, cancel); `bot.py` no longer contains engine/scheduler logic.

### PHASE 2 — THE `zilla` APP (entrypoint + TUI)

**Goal:** `zilla` is a real application.

Steps:
1. `zilla` console entrypoint (installable, e.g. `pipx install .` /
   `pip install -e .`): subcommands `config`, `doctor`, `start`, `stop`,
   `status`, `logs`. These wrap what `install.py --doctor`,
   `start.sh`/`stop.sh`, and the pid/lock files already do — promote, don't
   duplicate. `install.py` becomes a thin alias or is absorbed.
   Add `zilla doctor --security` (RESEARCH §5.3): file perms on home/config
   (600/700), secrets not in argv/logs, no unexpected listening sockets,
   WebBridge loopback-only, pending-skill gate intact, owner ID set;
   `--fix` auto-remediates the safe items.
2. `zilla config`: plain numbered-menu terminal settings editor covering
   the full settings table in §3. Reads/writes the SAME `.env` +
   `settings.json` the core uses. Works over SSH.
3. Bare `zilla`: full-screen TUI (Textual) — chat pane + input bar, driven
   by the same core API from Phase 1. Must support: chatting (with live
   progress), answering ask/OTP/confirm prompts inline, a settings screen,
   a skills list screen, a health screen.
4. Conversational onboarding in the TUI: first run with no config walks
   the user through setup; "connect to my Telegram" asks token + owner ID,
   validates, saves, starts the connector.
5. Telegram becomes a connector the core starts only when configured.

**Acceptance:** on a machine with zero prior config, `pipx install` →
`zilla` → onboard → chat with the AI in the terminal → enable Telegram →
same conversation continues from the phone. Doctor reports environment
detection results (OS, GUI, CLIs + login, ffmpeg, WebBridge).

### PHASE 3 — NEW HOME LAYOUT

**Goal:** one portable directory that IS the product.

Steps:
1. New layout (name it `~/Zilla` unless the owner objects):
   `wiki/  skills/  inbox/  outbox/  logs/  bridge/  config/`.
   All paths flow from `config.py` only.
2. Git-init the home on creation; auto-commit wiki/skill changes with
   simple messages (this is the knowledge safety net).
3. Migration shim: on startup, if `~/AGI-Brain` exists and the new home
   doesn't, offer to migrate (move files, keep a symlink or note).

**Acceptance:** fresh install creates the new home; existing install
migrates cleanly; grep shows no hardcoded `AGI-Brain` outside the shim.

### PHASE 4 — WIKI + FIRST-RUN INTERVIEW

**Goal:** the agent's persistent knowledge, owned by the user.

Steps:
1. `wiki/` = hierarchical Markdown + YAML frontmatter. The harness injects
   a one-line-per-page INDEX every turn (mirror `skills_summary()`);
   bodies are read on demand by the agent's own file tools. No vector DB.
   Injection char budgets per page (Hermes starting points: ~2,200 chars
   for durable agent memory, ~1,375 for the user profile) — truncate in
   context, never on disk. Add `wiki/journal/YYYY-MM-DD.md` daily notes:
   harness injects today+yesterday; distill-journal-into-pages happens
   during heartbeats (RESEARCH §7 items 10–14).
2. Preamble instruction: the agent creates/updates wiki pages autonomously
   when it learns something durable (people, processes, preferences,
   domain facts), and consults the index before asking the user something
   it should already know.
3. First-run interview: on a brand-new wiki, the agent interviews the
   owner (who they are, what they do, what they need) and writes
   `wiki/identity.md` + initial domain pages from the answers. This
   conversation IS the anti-hardcoding mechanism — no industry vocabulary
   ships in code.

**Acceptance:** fresh setup → interview happens → identity pages exist →
a later, separate conversation answers a question using wiki knowledge
without being told (verified live). Wiki survives switching backends.

### PHASE 5 — SKILLS, 100%

**Goal:** "make that into a skill" works end-to-end, safely.

Steps:
1. Preamble instruction: when the user asks to make something a skill, the
   agent authors a `SKILL.md` (+ code files if IT decides code is better —
   the agent chooses the form) into the active backend's skills dir.
   Authoring template (Hermes): When to Use / Procedure / Pitfalls /
   Verification. Autonomous-creation triggers (verbatim from Hermes):
   after a 5+-tool-call task succeeds; after errors led to a working path;
   after a user corrects the approach. Frontmatter gains OpenClaw-style
   gating (`requires.bins/env/config`, `os`) — skills whose gates fail are
   silently omitted from the index, viewer shows why (pairs with P6).
2. Code-type skills are written to `skills/pending/` instead — and edits,
   patches, and deletes of existing code skills are staged the same way
   (Hermes granularity), not just creation. Zilla (not
   the model) detects pending skills and raises an approval request —
   reuse the Approval-mode UI in Telegram AND an equivalent prompt in the
   TUI. One owner tap moves it live. Instruction-only skills go live
   immediately. The distinction is determined by Zilla inspecting the
   skill's files (deterministic), not by asking the model.
3. Skills viewer: list (name + description + type + status) in both TUI
   and Telegram menu; per-skill enable/disable/delete.
4. Failure loop (harness-enforced): a failing skill gets
   `{code, error, intent}` handed back to the agent, one rewrite, one
   retry; a second failure stops, explains the bottleneck to the user in
   ONE plain sentence, and logs the full trace for the owner. Never loop
   silently.

**Acceptance:** live demo: user says "make that into a skill" → skill
exists → (if code) owner gets one approval tap → next relevant request
activates it (verified in the transcript). A deliberately broken skill
stops after exactly one retry with a plain-language explanation.

### PHASE 6 — ENVIRONMENT DETECTION + ADAPTATION

**Goal:** Zilla knows its machine and adapts silently.

Steps:
1. Detection module (extend `platform_compat.py`): OS + version,
   GUI/headless (e.g. DISPLAY/WAYLAND on Linux, always-GUI on macOS),
   installed CLIs + login state, ffmpeg, WebBridge reachability.
   Run at startup, cache, expose in `doctor` + health screens.
2. Adaptation policy: GUI → desktop-control instructions included in the
   preamble; headless → shell-only phrasing. Windows → stub errors.
3. Display/audio specifics resolved at runtime, never hardcoded; if a
   genuine ambiguity needs a user choice, ask ONCE and persist the answer
   to the wiki.

**Acceptance:** `zilla doctor` prints an accurate environment report on
macOS; simulated-headless run (unset DISPLAY on Linux CI or a test) flips
the adaptation; no display/audio identifiers appear hardcoded anywhere.

### PHASE 7 — SILENT SELF-HEALING HEALTH

**Goal:** the 3am problem dies quietly.

Steps:
1. Background health loop in the core (piggyback on the scheduler tick):
   periodically check CLI reachability + login (`agy_reachable(force=True)`,
   `claude_identity()`), disk space, WebBridge (only if web mode =
   my-browser), Telegram connectivity.
2. Self-heal what a program can: restart crashed connector, re-create
   missing dirs, clear stale bridge files, retry transient failures.
   Log every check + action to `trust_log.jsonl`. NO messages to anyone.
3. Alert the owner ONLY on human-required conditions (login expired, disk
   full, token revoked) — one message, plain language, with exact recovery
   steps (a runbook readable at 3am), and no repeat alert until the
   condition changes.
   **ASSISTED RE-LOGIN (owner decree 2026-07-17):** on CLI login expiry
   (agy, claude, later opencode) the bot must never go stale/dumb. The
   alert itself carries the login link/URL for that CLI; the owner replies
   with the token/code **in the same chat**; Zilla feeds it to the CLI's
   login flow and confirms recovery — the owner never has to touch the
   machine. Build it on the existing ask/answer bridge (`Ask` kind
   `password`/`text`, secrets masked + wiped after use — the plumbing
   already exists and is tested). Deterministic detection first
   (`agy_reachable()`, `claude_identity()` auth_error), then drive the
   CLI's own login command; never store the token anywhere.
4. Usage/quota counters (per backend+model per day, from trust_log) shown
   on demand in Health screens; anomaly = a one-time owner note, not spam.
5. **Heartbeat** (OpenClaw semantics, fully specified in RESEARCH §3.1):
   every 30–60m start a FRESH conversation (never resume the main one —
   that's what keeps a beat at ~2–5K tokens) whose context is only the
   heartbeat prompt + `wiki/heartbeat.md` (owner-editable checklist,
   "small, stable, safe to consider every 30 minutes"). Prompt near-verbatim
   theirs, incl. "Do not infer or repeat old tasks from prior chats. If
   nothing needs attention, reply HEARTBEAT_OK." Strip the token; drop
   replies ≤300 chars. Run deterministic pre-checks FIRST and only wake the
   agent if they found something (Hermes `wakeAgent:false`). Quota-aware
   interval: back off when trust_log shows the day running hot. Skip while
   the user's lock is busy.

**Acceptance:** kill the CLI login (log out) → within one health interval
the owner gets ONE plain-language alert with recovery steps, and no
further spam; restore login → a single "recovered" note; routine checks
produce zero messages.

### PHASE 8 — FALLBACK CHAIN + OPENCODE BACKEND

**Goal:** model access can vanish and the user never sees a hole.

Steps:
1. `run_opencode()` in `backends.py` honoring the existing backend
   contract (headless run, model flag, conversation persistence — per
   Phase 0 findings); register in `cli_engine._run_blocking`; add its
   models to `config.model_catalog()`.
2. Fallback policy in `_run_blocking`: if the primary backend's turn ends
   in error / empty output / `detect_limit()` hit, re-run the SAME turn on
   the next backend in the owner-configured priority. The user gets one
   clean answer; the owner gets a log event (and a health note only if it
   keeps happening). Never fall back on long-but-alive runs.
3. All backend/model choices live in settings; zero hardcoded model
   strings outside `config.py` fallback caches.

**Acceptance:** with the primary CLI broken on purpose (logged out or
renamed), a user message still gets a real answer via the fallback, the
switch is visible in trust_log, and the user-visible reply contains no
error garbage. Restore primary → next turn uses it again.

### PHASE 9 — VOICE (offline option)

**Goal:** voice notes work per the owner's privacy choice.

Steps:
1. Add local Whisper transcription (faster-whisper or whisper.cpp binding;
   small/base model — must run acceptably on an i5/16GB) alongside the
   existing Google path in `media.py`.
2. `voice_mode` setting decides; offline mode must never touch the
   network; if the chosen engine is unavailable, degrade to the other WITH
   a one-time owner note.

**Acceptance:** a voice note transcribes correctly in both modes; network
disabled + offline mode still works.

### PHASE 10 — DEPLOYMENT HARDENING (later, on the client's Ubuntu laptop)

**Goal:** the real security boundary, per Trap #2.

Steps: dedicated non-root `zilla` user; systemd unit with hardening
(`ProtectSystem=strict`, `ReadWritePaths=` the Zilla home, resource
limits); autostart; SSH/Tailscale access for the owner; deployment runbook
in `docs/`. Never run the agent as root — elevation is for building the
cage, not for the agent.

**Acceptance:** documented install on a fresh Ubuntu machine; the agent
cannot write outside its allowed paths (verified by probe); reboot →
everything comes back by itself.

---

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

**Current phase:** Phase 1 core extraction is **DONE** — turn pipeline,
scheduler runtime, credential/OTP bridge, Approval mode, and the
`health_report()` snapshot all live in `zilla/core.py` (CORE_API steps 2–6);
`bot.py` is a Telegram renderer. **NEXT SESSION STARTS AT: P1.5
orchestration router** — implement per the checklist item below +
`docs/dev/RESEARCH_ORCHESTRATION_REVIEW.md` (the `review()` seam design,
harness self-heal clause, and Progress-into-⏳-bubble are already specced
there; start from its "What to build" section).
**Working branch:** `claude/zilla-harness-review-0v96bs`
**Tests:** 204+16+102+57 = **379 green** — `.venv/bin/python test_fixes.py /
test_interactive.py / test_core.py / test_schedules_seam.py` (the last is a
frozen acceptance spec — never edit it) + `import bot; import zilla.core`.
**Bot:** live on the owner's MacBook (@Mangomangos_bot; `.env` exists here,
git-ignored). After changing `bot.py`: `pkill -9 -f "Python bot.py"`, restart
`.venv/bin/python bot.py`, confirm "Application started" in its log.

### Checklist


- [x] **P0** Verify reality (flags, GEMINI.md/AGENTS.md, sandbox test, logins, tests on macOS) → `docs/dev/PHASE0_FINDINGS.md`
- [x] **P1** Core extraction: design core API (owner-approved) → `docs/dev/CORE_API.md`
- [x] **P1** Move modules into `zilla/` package (tests green)
- [x] **P1** Extract turn pipeline / scheduler / bridge / approvals / health-stub from `bot.py` (FOR ALOK: live Telegram smoke of Approval mode — have a limited user send a request, tap ✅ and ❌ once each)
- [ ] **P1.5** Orchestration router (OWNER DECREE 2026-07-17): a cheap first pass on EVERY incoming message decides complexity + intent BEFORE the heavy CLI turn — (a) small-talk/simple → answer fast (small model or short-circuit), complex → full CLI agent turn; (b) if the user is *sharing* something (a fact about their life, a preference, something they did) → immediately append it to the wiki journal (`wiki/journal/YYYY-MM-DD.md`), structure matures over time via heartbeat distillation (steal-list #12/#13). This also attacks LATENCY, the owner's other complaint: today every "hi" pays full CLI cold-start (~10s spin-up + model time). Also add an instant ack reaction (👀 or typing starts <1s) so the bot never feels dead. Design this seam into CORE_API alongside the bridge/approvals/health extraction. (c) EXPANDED (owner decree 2026-07-17, the "effortless" mandate): an orchestrator RESPONSE-REVIEW gate — every outbound response is inspected BEFORE delivery to the user (deterministic checks first: empty/error-garbage/limit; then bounded self-heal — on a tool/dependency failure, fix it (e.g. install the missing converter) and retry ONCE, never deliver error garbage, never loop silently). Owner's reference story: OpenClaw hit a missing OGG converter, installed it, transcribed, answered — zero errors shown. Spec: `docs/dev/RESEARCH_ORCHESTRATION_REVIEW.md` (deep-dive comparison of OpenClaw/Hermes source vs Zilla's pipeline).
- [ ] **P2** `zilla` entrypoint + `config`/`doctor`/`start`/`stop`/`status`/`logs`
- [x] **P2** Full-screen TUI (chat + settings + skills + health; ASCII logo home + prompt box) — `zilla/tui/`, `python -m zilla.tui`; Pilot-tested headless (FOR ALOK: launch it once in a real terminal). TUI runs standalone with `schedules=None` (no double-fire); Telegram-as-connector unification is the later P2 step.
- [ ] **P2** Conversational onboarding + Telegram-as-connector
- [ ] **P3** New Zilla home layout + git-init + migration shim
- [ ] **P4** Wiki: index injection + autonomous read/write instructions
- [ ] **P4** First-run interview → identity/domain pages
- [ ] **P5** Skill authoring via chat
- [ ] **P5** Code-skill approval gate (pending/ + one tap)
- [ ] **P5** Skills viewer + failure loop (one retry, then plain-language stop)
- [ ] **P6** Environment detection + adaptation
- [ ] **P7** Silent self-healing health + 3am alert (incl. ASSISTED RE-LOGIN — see Phase 7 step 3) + usage counters
- [ ] **P8** opencode backend + fallback chain
- [ ] **P9** Local Whisper + voice_mode setting
- [ ] **P10** Ubuntu deployment hardening (LAST — on the client machine)

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

### Notes (only what a future session needs)

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
- **Loop protocol (owner decree 2026-07-17): GOD MODE.** Each round fans out
  ALL unchecked items that don't conflict on files to parallel Sonnet
  executors in isolated git worktrees (+ Opus agents for research items) —
  never one-at-a-time. The orchestrator reviews every diff, merges the
  worktrees SERIALLY with the full test gate between merges (that is what
  keeps parallel safe), restarts the bot when bot.py changed, pushes.
