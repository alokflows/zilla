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
   WebBridge loopback-only, pending-skill gate intact, owner ID set, token
   rotated; `--fix` auto-remediates the safe items.
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

> **Update this section and commit after EVERY completed step.** This is
> what lets a fresh session (or a different account) resume instantly.

**Current phase:** Phase 0 ✅ done → Phase 1 in progress (turn pipeline + scheduler runtime, PARTS A+B, extracted to `zilla/core.py`/`zilla/schedules.py`; bridge/approvals/health seams + live Telegram round-trip remain)
**Working branch:** `claude/zilla-harness-review-0v96bs`
**Last session:** 2026-07-16 late night (MacBook) — LIVE SMOKE STARTED with
owner's new token (`.env` now exists on this Mac, git-ignored; bot =
@Mangomangos_bot, owner id 8740189938). First live turn exposed a delivery
bug: CLI answered fine but the final `send_message` timed out once (owner on
hotspot) and `safe_send` swallowed it — reply silently vanished. FIXED:
`safe_send` now retries 4× with backoff; PTB network timeouts raised from 5s
defaults (connect 15 / read 30 / write 30 / pool 10). Verified live: real-CLI
pipeline PONG via `core.handle_message` + real DM delivered to owner. 298
tests green. Remaining smoke: text re-try, voice/photo/doc, cancel, schedule
fire — owner mid-test.

### Checklist

- [x] **P0** Verify reality (flags, GEMINI.md/AGENTS.md, sandbox test, logins, tests on macOS) → `docs/dev/PHASE0_FINDINGS.md`
- [x] **P1** Core extraction: design core API (owner-approved) → `docs/dev/CORE_API.md`
- [x] **P1** Move modules into `zilla/` package (tests green)
- [ ] **P1** Extract turn pipeline / scheduler / bridge / health from `bot.py`
- [ ] **P1.5** Orchestration router (OWNER DECREE 2026-07-17): a cheap first pass on EVERY incoming message decides complexity + intent BEFORE the heavy CLI turn — (a) small-talk/simple → answer fast (small model or short-circuit), complex → full CLI agent turn; (b) if the user is *sharing* something (a fact about their life, a preference, something they did) → immediately append it to the wiki journal (`wiki/journal/YYYY-MM-DD.md`), structure matures over time via heartbeat distillation (steal-list #12/#13). This also attacks LATENCY, the owner's other complaint: today every "hi" pays full CLI cold-start (~10s spin-up + model time). Also add an instant ack reaction (👀 or typing starts <1s) so the bot never feels dead. Design this seam into CORE_API alongside the bridge/approvals/health extraction.
- [ ] **P2** `zilla` entrypoint + `config`/`doctor`/`start`/`stop`/`status`/`logs`
- [ ] **P2** Full-screen TUI (chat + settings + skills + health; OWNER 2026-07-17: ASCII-art Zilla logo centered on the home screen + a visible prompt box — the terminal app should look like a real product the moment it opens)
- [ ] **P2** Conversational onboarding + Telegram-as-connector
- [ ] **P3** New Zilla home layout + git-init + migration shim
- [ ] **P4** Wiki: index injection + autonomous read/write instructions
- [ ] **P4** First-run interview → identity/domain pages
- [ ] **P5** Skill authoring via chat
- [ ] **P5** Code-skill approval gate (pending/ + one tap)
- [ ] **P5** Skills viewer + failure loop (one retry, then plain-language stop)
- [ ] **P6** Environment detection + adaptation
- [ ] **P7** Silent self-healing health + 3am alert + usage counters
- [ ] **P8** opencode backend + fallback chain
- [ ] **P9** Local Whisper + voice_mode setting
- [ ] **P10** Ubuntu deployment hardening (LAST — on the client machine)

### Session log

| Date | Session did | Warnings for next session |
|---|---|---|
| 2026-07-16 | Full codebase analysis; handoff written and pushed. | agy/opencode were NOT installed in that environment — nothing in "traps" is verified yet. Do Phase 0 first. Older repo docs conflict with this vision; this file wins. |
| 2026-07-16 (later, MacBook) | Phase 0 complete: all 3 CLIs probed live, 208 tests green, `docs/dev/PHASE0_FINDINGS.md` committed. Trap #1 refuted (agy `--model` validates, hard error). Trap #2 split: claude blocks headless writes without permission; agy+opencode execute unattended. agy does NOT read GEMINI.md/AGENTS.md; opencode DOES read AGENTS.md. opencode runs free with 0 credentials. claude = Pro subscription. | Nothing blocks Phase 1. Next: orchestrator designs the core API on paper and gets owner approval BEFORE coding (P1 step 1). Local checkout is `~/Documents/repos/zilla`. |
| 2026-07-16 (P1 step 2, MacBook) | Pure move (no logic edits): `platform_compat, config, users, sessions, schedules, schedule_parse, verify, autoharness, interactive, harness, cli_engine, backends, media, formatter` → `zilla/` via `git mv` (history preserved). Cross-module imports inside `zilla/` rewritten to `from zilla.<mod> import ...`. Root-level shim files (`import zilla.<mod> as _mod; sys.modules[__name__] = _mod`) alias the old names so `bot.py`, `keyboards.py`, `install.py`, and both test suites are unchanged. `config.BASE_DIR` and `harness._HERE` (both `__file__`-derived) bumped one directory level up — they now point one level deeper than before the move, fixed with a one-line comment each. 192+16 tests green; `zilla.cli_engine/backends/harness/media` import clean; `import bot`/`import keyboards` fail ONLY on missing `telegram` package (not installed in this env — expected, not an import-path bug); `import install` clean. | `bot.py` and `keyboards.py` still import the OLD top-level names (`from config import ...` etc.) via the shims — that's intentional for this step. Next P1 step extracts the turn pipeline out of `bot.py` into `core.handle_message`; once bot.py is rewritten to use `zilla.*` directly (or the shim strategy is revisited) the root-level shim files can be deleted. `telegram` package is not installed in system `python3` on this machine — install it (or use whatever venv the bot normally runs in) before attempting a live Telegram smoke test. |
| 2026-07-16 (P1 turn-pipeline seam, MacBook) | Extracted the TURN PIPELINE from `bot.py` into `zilla/core.py`: event dataclasses per CORE_API (`Progress`/`Ask`/`Response` live; `ApprovalRequest`/`Alert`/`ScheduledResult` placeholders) + `ZillaCore` with async-generator `handle_message(user_id, text, chat_key=, auto_title=, skip_permissions=)`, `cancel(key)`, `is_busy(uid)`, `get_user_lock(uid)`. Moved (not rewritten): per-user lock map, `_active_cancel`, `_conv_for_run`, the whole `_run_cli_turn` body (in-lock session pinning, `run_cli_async` w/ progress callback + cancel event, session writes threading `session_name`+`backend`). `bot.py` handlers (text/voice/photo/document + approval runner) now drive `_relay_cli_turn` → `core.handle_message`; cancel taps use `core.cancel()`. Scheduler/bridge/health stayed in `bot.py` (thin `_get_user_lock`/`_conv_for_run` delegates share the core's lock). Progress events are consumed silently in Telegram — the ⏳ Working UI remains time-driven `keep_typing`, so visible behavior is unchanged. New `test_core.py` (29 tests, backend monkeypatched): event sequence, bookkeeping, lock serialization, cancel, error hygiene. 192+16+29 = 237 green; `import bot` clean in a local `.venv` (git-ignored). | ⚠️ LIVE TELEGRAM ROUND-TRIP STILL PENDING — this machine has no `.env`/bot token, so the seam is verified only by tests + import. Owner must run a live smoke (text, voice, photo, doc, approval, cancel) before the P1 extraction item can be ticked. AI_CONTEXT.md §Module map/§L still describe the lock/cancel state as living in `bot.py` — update it as the remaining seams move (scheduler → step 3, bridge → step 4, approvals → step 5, health → step 6); only then delete the `_get_user_lock`/`_conv_for_run` delegates and the root-level shims. |
| 2026-07-16 (P1 scheduler seam, MacBook) | ⚠️ **ROW WRITTEN BY THE EXECUTOR BEFORE IT WAS KILLED MID-COMMIT-SPLIT — only PART A is actually in the tree.** Every PART B claim below (ladder, schema, new `mark_failure(sid, now=)` signature, 57-test suite green, 298 total) describes work that was REVERTED and must be re-implemented; treat it as the spec. Moved the SCHEDULER RUNTIME from `bot.py` into `zilla/core.py` (CORE_API migration step 3), two commits (PART A runtime, PART B schema). **PART A**: `_scheduler_loop`/`_execute_schedule`/`_run_and_record`/`run_schedule_now` now live on `ZillaCore`, started/stopped via `core.start()`/`core.stop()` (wired to PTB `post_init`/new `post_shutdown`). Scheduled `message`-payload runs share the SAME per-user lock as live chat (`core.get_user_lock`) — a schedule and a live turn for the same uid still never overlap. Delivery is a `ScheduledResult`/`Alert` event broadcast through a new `core.subscribe(sink: asyncio.Queue)` — bot.py subscribes once in `post_init` and renders the exact old `⏰ Scheduled — <title>` DM (`_deliver_scheduled_result`), plus a new owner-only `_deliver_alert`. `bot.py`'s `_get_user_lock`/`_conv_for_run` delegates are GONE (grep-confirmed no scheduler logic remains in `bot.py` — only the Telegram-specific screenshot-via-bridge fast path stayed, now wired through `core.schedule_pre_run`). **Discovered default behavior** (informed the schema defaults): every existing schedule in `schedules.json` today runs with `session_name=None` always → i.e. **every schedule is effectively "isolated"** (fresh conversation each run, nothing ever persisted) — no UI path has ever set `session_name`. **PART B schema** (all additive, defaults preserve exactly that discovered behavior): `payload_type` (`message` default/`system_event`/`command`, `command` owner-only enforced in `ScheduleManager.add()` not a comment), `session` (default `"isolated"`), pinned `backend`/`model` (default `None`=unpinned; mismatch at fire time runs on the CURRENT backend — the only thing `cli_engine` can do, no per-call override exists — and sends the owner ONE `Alert`, tracked via new `backend_pin_notified`), retry `RETRY_LADDER=(30,60,300,900,3600)` replacing the old flat `retry_delay`/`max_retries` (still never permanently disables — give-up resets + advances to the next normal occurrence), `ScheduledResult.session`/`conv_id`/`warning` fields (carried for a future continue-conversation UX; no reply-routing built), recursion guard `core.is_scheduled_run(uid)` checked by `bot.py`'s NL schedule-detection. Atomic writes in `ScheduleManager._save()` were ALREADY atomic (tmp+fsync+`os.replace`) — confirmed, not rebuilt. `compute_next_run` untouched. New `test_schedules_seam.py` (57 tests): pure logic (`resolve_session_mode`, `backend_pin_mismatch`, ladder shape, payload-type/owner gating) + runtime integration (system_event zero-CLI-call, command-payload subprocess incl. failure, ScheduledResult/Alert broadcast, give-up-keeps-firing, one-time backend-pin alert, `run_schedule_now` never advances, deauthorized-owner disables, `core.start()`/`stop()` lifecycle incl. no-op without a `ScheduleManager`). `test_fixes.py`'s `test_schedule_failure_retry` rewritten for the new `mark_failure(sid, now=)` signature (196, +4 from finer ladder assertions). Totals: 196+16+29+57 = 298 green. `import bot`/`import zilla.core` clean. One deliberate, documented deviation from byte-identical: the old ephemeral (~5s) `send_chat_action(TYPING)` ping before a scheduled CLI run is dropped — core.py has no Telegram bot handle and it's pure UI nicety, not delivered content. | Bridge and health seams (CORE_API steps 4/6) still remain — P1 extraction checklist item stays unticked. No UI yet lets a user SET `payload_type`/`session`/`backend`/`model` at creation (`/schedule` and NL-parse still only produce `kind`/`spec`/`title`/`prompt`) — these are schema-only, wired for a future UI. Live Telegram round-trip for scheduled jobs is still unverified on this machine (no `.env`/token here, same constraint as the prior seam) — owner should smoke-test a real schedule fire (success, a forced failure to see the ladder+warning, and the model-switch suggestion path) before trusting this in production. AI_CONTEXT.md module map now has a `zilla/core.py` entry (previously missing entirely) folding in both the turn-pipeline AND scheduler description; `bot.py`'s entry, Invariant I-CONV, and Invariant L were updated to stop naming `bot._get_user_lock`/`bot._conv_for_run` (both deleted) and point at `core.*` instead — this also closes out the "AI_CONTEXT still describes lock/cancel as living in bot.py" warning from the previous seam's row. |
| 2026-07-16 (night wrap-up, MacBook) | Session limits killed the scheduler-seam executor twice mid Part A/Part B split. Orchestrator salvage: **kept PART A** (scheduler runtime in `zilla/core.py`, reviewed line-by-line — includes a real security improvement: schedules owned by a de-authorized user are disabled at fire time, not silently kept running); reverted the orphaned `test_fixes.py` ladder rewrite (green again against HEAD `zilla/schedules.py`, old `mark_failure(sid, retry_delay, max_retries)` signature); **added the missing `core.is_scheduled_run(uid)`** (tracked via `_scheduled_running` set around `_execute_schedule`) that `bot.py:1782` already called — without it every admin text message would have crashed at runtime; committed `test_schedules_seam.py` as the PART B acceptance spec (RED by design — it imports `resolve_session_mode`/`backend_pin_mismatch`/`RETRY_LADDER` which don't exist yet; NOT part of the green trio). 237 green (192+16+29); `import bot` clean in `.venv`. | **RESUME HERE.** (1) Re-implement PART B in `zilla/schedules.py` (+ small core/bot wiring) until `test_schedules_seam.py` is green — the ⚠️ row above and AI_CONTEXT's Scheduler section are the exact spec; also restore the ladder version of `test_fixes.py::test_schedule_failure_retry` then. (2) `bot.py:1053` comment references `core._maybe_notify_backend_pin` — doesn't exist yet, Part B. (3) AI_CONTEXT.md currently DESCRIBES Part B as done (schedules.py entry, Scheduler ladder/schema paragraphs, test counts) — spec, not state; a "⚠️ Part B pending" note was added, remove it and fix counts when Part B lands. (4) Then bridge seam (CORE_API step 4) → approvals (5) → health stub (6). **FOR ALOK**: live Telegram smoke (text/voice/photo/doc/approval/cancel + a real schedule fire) needs your bot token — no `.env` on this Mac; and confirm the previously-leaked token was rotated. |
| 2026-07-16 (PART B re-implementation, MacBook) | Re-implemented PART B from the frozen `test_schedules_seam.py` spec — no test edits, only source. **`zilla/schedules.py`**: `VALID_PAYLOAD_TYPES = (message/system_event/command)`, `RETRY_LADDER = (30,60,300,900,3600)`; pure helpers `resolve_session_mode(sched)` (explicit `session` wins → legacy `session_name` maps to `named:<x>` → default `"isolated"`) and `backend_pin_mismatch(sched, backend, model)` (no pin / already-notified / model=None-legacy all → False, honoring the one-time-note gate); `ScheduleManager.add()` gained `session`/`payload_type`/`backend`/`model`/`is_owner` kwargs — `command` payload_type is refused unless `is_owner=True`, unknown `payload_type` refused, new schedules default `session="isolated"`, `backend=model=None`, `backend_pin_notified=False`; `mark_failure(sid, now=)` rewritten off the ladder (old flat `retry_delay`/`max_retries` params gone) — same never-permanently-disables contract; added `mark_backend_pin_notified(sid)`. **`zilla/core.py`**: `_execute_schedule` now dispatches by `payload_type` — `system_event` returns the stored prompt text verbatim with **zero** CLI call, `command` runs via `asyncio.create_subprocess_shell` (new `_execute_command_schedule`, also zero model call, stdout+stderr captured, nonzero exit = failure), `message` is the old CLI-turn path renamed `_execute_message_schedule` and now resolves session mode (`resolve_session_mode` → `_sname_for_mode`: `isolated`→fresh conv every run, `main`→the user's active session, `named:<x>`→that session) instead of the old bare `session_name` field. All three return the new `(ok, response, detail, meta)` 4-tuple (`meta` carries `conv_id`, and for message-payload also `session` + `pin_mismatch`). New `_maybe_notify_backend_pin(s)`: broadcasts one `Alert` and calls `mark_backend_pin_notified` — wired into `_run_and_record`/`run_schedule_now` whenever `meta["pin_mismatch"]` is set (bot.py's `_deliver_alert`, already present from the Part A salvage, needed no changes). `ScheduledResult` gained `session`/`conv_id` fields (carried through from `meta`, no reply-routing built on them, matches spec). `test_fixes.py::test_schedule_failure_retry` rewritten for `mark_failure(sid, now=)` walking all 5 ladder rungs before asserting give-up (192→196, +4 exact). Verified: `test_schedules_seam.py` 57/57, `test_fixes.py` 196/196, `test_interactive.py` 16/16, `test_core.py` 29/29 — **298 total green**; `import bot; import zilla.core` clean in `.venv`. No test files touched — every fix was source-side per the frozen-spec constraint. AI_CONTEXT.md: removed the "⚠️ Part B pending" note, Scheduler section now describes the dispatch-by-payload_type flow + `_maybe_notify_backend_pin` by name, verification counts corrected (196/16/29/57). | No UI still lets a user CHOOSE `payload_type`/`session` at creation (schema-only, as before — `command` payload type has no creation path at all yet outside direct `ScheduleManager.add(is_owner=True, payload_type="command")`, i.e. no `/schedule` grammar for it). `_execute_command_schedule` uses `asyncio.create_subprocess_shell` with the raw stored prompt string — this is intentionally unattended shell execution gated owner-only at creation; do not loosen that gate without adding sanitization. Next: bridge seam (CORE_API step 4) → approvals (5) → health stub (6), per the existing Phase 1 checklist. Live Telegram round-trip (including a real schedule fire on all three payload types) is STILL unverified on this machine — same no-`.env` constraint as prior sessions. |

### Notes / open concerns

- Owner mentioned rotating the Telegram bot token after an earlier leak
  (see `docs/dev/STATUS.md`) — confirm it was rotated before going live
  with the client.
- Orchestrator liberty: if Phase 0 findings contradict this plan, argue it
  with the owner BEFORE proceeding — do not silently comply with a stale
  plan.
- **Scheduling policy (owner decision, 2026-07-16):** Zilla's own scheduler
  (`schedules.json` + core ticker) is the ONLY scheduling authority — same
  architecture OpenClaw/Hermes-class agents use (daemon-held job list firing
  prompts at the agent). Two additions to the plan: (a) harness rule — the
  agent must NEVER create OS timers (cron/launchd/Task Scheduler); recurring
  work goes through Zilla (add when the harness preamble is next touched);
  (b) a schedule-request bridge (same file pattern as the OTP bridge): the
  agent writes a schedule request, Zilla shows the owner the normal confirm
  card, one tap stores it in `schedules.json` — fold into the P1 bridge
  extraction or P5. Also steal OpenClaw's "heartbeat" idea for Phase 7: the
  health tick can periodically hand the agent a tiny checklist review, not
  just probe logins. (Now formalized: P7 step 5 + the RESEARCH doc.)
- **Reference designs (owner decision, 2026-07-16):** OpenClaw + Hermes
  Agent are the explicit reference products — "exact replica is fine", with
  API keys swapped for CLI logins (the niche neither covers: they support
  Gemini CLI but not Antigravity/Claude Code). The vetted 30-item steal list
  with per-phase mapping lives in `docs/dev/RESEARCH_OPENCLAW_HERMES.md` §7
  — consult it at the START of each phase (P2 pairing-code onboarding, P4
  memory/journal/BOOTSTRAP ritual, P5 skills, P7 heartbeat, P8 pinning).
