# ZILLA — HANDOFF

**Read this file, then the ONE section of `PLAN.md` for the phase you're
about to build. Nothing else.** Everything historical lives in
[`docs/dev/HANDOFF_ARCHIVE.md`](docs/dev/HANDOFF_ARCHIVE.md) — search it when
you need to know *why*, never read it start to finish.

---

## What Zilla is

A non-technical person owns a powerful AI assistant on their own computer.
Their knowledge lives in portable Markdown on their own disk; the "brain" is
rented from whatever free AI CLI is available today and swappable tomorrow.

> **THE KNOWLEDGE IS THE USER'S. THE BRAIN IS RENTED.**

Zilla is a **harness, not an agent** — the CLIs (agy / claude / opencode)
already have tools, shell, files, sessions and skills. Zilla shapes the
context going in and the output coming back, and adds policy, memory,
scheduling and health. Before building any orchestration, check whether the
CLI already does it and Zilla just isn't configuring it.

Two frontends over one core: the terminal app (`zilla`) and Telegram as an
optional connector. Zero budget, permanently — CLI logins only, no API keys,
no paid dependencies.

---

## Where things are

| | |
|---|---|
| Working tree (the only one) | `~/Documents/Work/alokflows/zilla` (has `.venv`, `.env`) |
| Runtime data | `~/Zilla/Runtime` (`zilla.db`, logs) |
| Owner's memory | `Memory/` (Markdown + `Wiki/` entity pages, git-tracked by Zilla itself) |
| Repo | `alokflows/zilla`, branch `main` |
| Live bot | @Mangomangos_bot on the owner's MacBook |

Switch the GitHub account before any push or `gh` call
(`~/Documents/Work/AGENTS.md` is the machine contract; this folder is the
`alokflows` account):

```
gh auth switch --user alokflows
git push origin main
```

"Repository not found" means the wrong account is active — check with
`gh api user --jq .login`. Never put an AI attribution or a `Co-Authored-By`
trailer in a commit.

---

## Session protocol

1. Read this file. Read the current phase's section in `PLAN.md`.
2. `git log --oneline -5` — see what actually shipped last.
3. Run the full gate BEFORE touching anything:
   `for f in test_*.py; do echo "$f :: $(.venv/bin/python $f | tail -1)"; done`
   — every file must end `0 failed`. Always `.venv/bin/python`; plain
   `python3` has no `telegram` module.
4. Build the smallest increment that satisfies the phase's Accept criteria.
   Verify it by running it, not by reading it — the backends fail silently.
5. New pure logic gets tests. Full gate green again.
6. Tick the checklist below, add ONE line to the log below, commit
   (`feat(K5): …`), **push to `main` — every session, no exceptions.**

Serial only: one phase at a time, no parallel agent fan-out (it burns the
shared usage window with nothing to show).

---

## Progress

**Foundation (shipped, live, not reopened):** turn pipeline, scheduler,
bridge, approvals and health all extracted into `zilla/core.py` behind one
frontend-agnostic event vocabulary (`docs/dev/CORE_API.md`); `zilla` CLI
(`config`/`doctor`/`start`/`stop`/`status`/`logs`); full-screen Textual TUI;
the P1.5 triage router (`zilla/review.py` — smalltalk fast path, outbound
review gate, every route logged).

| Phase | What it gave Zilla |
|---|---|
| **M1-M4** | SQLite+WAL store (one `zilla.db`, migrated from 5 JSON files) · `Memory/` Markdown tier injected owner-only per turn · FTS5 search (`memsearch.py`) + Zilla git-committing its own memory · nightly distillation + `/memory` |
| **H1-H3** | Heartbeat loop · health probes with silent self-heal and plain-language alerts only when a human must act · systemd service for Linux |
| **F1-F5** | One clean `ZILLA_HOME` layout · dynamic backend + slash-command registry (no hard-coded buttons) · media importance/retention · system jobs invisible and silent · conversational schedule answers (`schedule_query.py`) |
| **K1-K4** | Relational memory: entity pages → `nodes`/`aliases`/`edges` graph (`memgraph.py`) · turn-time entity linking, so mentioning a person surfaces what Zilla knows · curiosity loop (asks one good question about a real gap) · `/graph` self-contained HTML map + TUI graph screen |
| **K5** | Team relay: "tell Priya to send the report" / "remind Rahul every Monday at 9" → resolved against the graph, owner taps ✅, then it goes. `/relay` audit log. A relay target's reply is reported to the owner and never becomes a turn |
| **U1-U4** | Generative UI: the model emits a fenced ```zui block and Telegram renders it natively — buttons/card/table/contacts/location (`zilla/zui.py`) · the protocol is taught in the preamble, never hard-coded · one design system (`docs/dev/STYLE.md`) applied across every menu · presence replaces the startup blast: one pinned status card edited in place, a new message only for a first install, an update, or real downtime (`zilla/presence.py`) |
| **R3** | opencode as a third backend |

**Tests: 1432 green across 22 files** (as of 2026-08-14), plus the import
smoke: `import bot; import zilla.core; import zilla.cli; import zilla.tui.app;
import schedule_query; import zilla.graph; import zilla.graph_html; import
memgraph; import zilla.relay; import zilla.zui; import zilla.presence`. `test_schedules_seam.py` is a frozen
acceptance spec — never edit it. Recount fresh per file rather than trusting
any grand total.

### Checklist — build in this order (PLAN.md §13)

- [x] M1-M4 · H1-H3 · F1-F5 · K1-K4 · K5 · R3 · U1-U4
- [ ] **H4** Self-update with doctor-gated rollback (PLAN §8)
- [ ] **B1-B2** Background task lane + `/tasks`; incognito sessions (PLAN §9)
- [ ] **R1** Triage router refinement — mostly shipped as `zilla/review.py`;
      confirm against PLAN's spec, don't rebuild
- [ ] **R2** Fallback chain (error / empty / limit only — never on long runtime)
- [ ] **S** Skills from chat, one owner approval tap before code-type skills run
- [ ] **C1-C3** Brain export/import · connectors screen · cloud backup +
      bootstrap-from-cloud (PLAN §12)
- [ ] **G1** Engine facade: Unix-socket IPC daemon-attach. The riskiest
      refactor in the plan — do it alone
- [ ] **T1** Terminal app completion: Sessions/Schedules/Memory screens,
      daemon-attach, conversational onboarding
- [ ] **V1-V3** Offline transcription (faster-whisper, already installed) ·
      local TTS replies (Piper) · owner-trained wake word

---

## Standing rules

- The bot keeps working at every commit — the owner demos it.
- Owner-facing text: short, plain language, point-wise, no jargon, no stack
  traces. They are usually on a phone.
- Memory and the graph are the OWNER's — every injection is gated on
  `ctx.is_owner`. Never widen that gate.
- Security decisions are deterministic, enforced by Zilla, never judged by
  the model. Untrusted text talks to the model, so anything irreversible,
  destructive or outward-facing needs an owner tap.
- Preserve the `docs/dev/AI_CONTEXT.md` invariants (I-CONV / I-STEP /
  I-CANCEL, per-user lock, global new-conv lock) — they are what stops
  responses bleeding between conversations.
- OS-specific code lives only in `platform_compat.py`.
- No hardcoded models or paths outside `config.py`. No industry vocabulary in
  core prompts or code. Secrets never in argv.
- No web UI, no listening network gateway, no skills marketplace — each was
  a real security incident elsewhere (`docs/dev/RESEARCH_OPENCLAW_HERMES.md` §5).
  Any socket Zilla ever opens: loopback bind + auth from day one.
- Zilla's scheduler is the only scheduling authority; the agent never creates
  OS timers.

## Worth knowing

- **agy** answers are read from agy's own `transcript.jsonl`, not stdout, and
  it runs under a real PTY. That's solved — don't "fix" it.
- **agy silently ignores an unknown model string**, so the read-back
  verification in `config.py` is load-bearing.
- **Headless CLI runs execute tools regardless of permission flags** — the
  real boundary is OS-level (dedicated user + systemd hardening on the Linux
  deployment; never run the agent as root).
- **CLI logins expire quietly** ("the 3am problem"); H2 detects it and asks
  the owner to re-login with exact steps. No speculative login automation.
- **`cli_engine.py`'s thread pool is global (4 workers)** — a 5th
  simultaneous request queues with no visible feedback. Worth surfacing if
  relay traffic ever makes it noticeable.
- **Every `admin` is effectively unsandboxed shell access** on the host, so
  "adding people" is a trust decision, not a seat count. Delegation is what
  the owner actually wanted, and that's K5.
- **Latency is the owner's #1 complaint** — a full CLI call per turn (17s to
  ~2m30s). The triage router is the lever; keep widening the deterministic
  fast paths.
- **Schedule-triggered turns get no memory injection** — wiring it means
  touching the frozen `test_schedules_seam.py`, so it waits for a phase that
  needs it plus owner sign-off.
- A `MacBook` asleep or out of battery looks exactly like a bug from the
  chat side. H3's always-on Linux deployment is the structural fix.

## Cleanup backlog (small, do them when you're already in the file)

Exact caller lists in `docs/dev/AUDIT_PONYTAIL_2026-07-19.md`:
14 legacy 4-line import shims at the repo root can go once `bot.py`,
`keyboards.py`, `test_fixes.py` and `test_interactive.py` import `zilla.<name>`
directly · `WIKI_DIRNAME` is defined in both `zilla/memory.py` and
`zilla/graph.py` · `SESSIONS_FILE`/`SETTINGS_FILE`/`USERS_FILE`/`SCHEDULES_FILE`
all alias `DB_FILE` since M1 and could collapse.

Left over from the U3 style pass (all live in `bot.py`, which was busy that
session — see `docs/dev/STYLE.md` rule numbers): panel titles still use `═══`
divider rows and shouty headings (R10) · the inbox panel's redundant `📤`
button shares its callback with the filename button, so drop the button and
the "or 📤" copy together · `kb_schedules` uses two rows per schedule, so 5+
schedules scroll (R18, wants pagination) · `kb_user_detail` has no `✕ Close`
(R15) · `zilla/zui.py`'s own renderers were never style-audited.

## Live smokes still to do (need the owner's own devices/people)

- K5 relay end to end: a second real person on Telegram — watch the confirm
  card, the send landing in their chat, and one inbound reply reported back.
- The `Memory/` + `zilla.db` migration on the owner's real `zilla start`
  (wired and idempotent, just never run against production yet).
- Menu Close and the callback-failure notice in the real chat.
- U-phase in the real chat: ask for something that earns a card/table, tap a
  ZUI button (say / copy / url), and confirm the pinned card gets edited —
  not re-posted — across a restart.

---

## Session log (one line each — details in git log and the archive)

| Date | What shipped |
|---|---|
| 2026-08-14 | **U1-U4 generative UI** — `zilla/zui.py` (```zui block → buttons/card/table/contacts/location, caps + owner gate + `ButtonStore`), `_ZUI_PROTOCOL` taught in `harness.py`, `docs/dev/STYLE.md` (22 numbered rules) applied across `keyboards.py`, `zilla/presence.py` + pinned status card replacing the startup blast, `/status` alias. `test_zui.py` (137) + `test_presence.py` (41). 1432 green. |
| 2026-08-14 | **K5 team relay** — `zilla/relay.py` (marker parse/strip, alias→`telegram_uid::` resolution), `relay_log` table, `RelayRequest` + `core.relay` hold/confirm/audit, owner-only marker processing on the turn pipeline, confirm card + `/relay` + inbound-report carve-out in `bot.py`, `test_memory_k5.py` (107 checks). Ticked R3 (shipped 2026-07-19). 1254 green. |
| 2026-08-14 | HANDOFF split: this short brief at the root, full history moved to `docs/dev/HANDOFF_ARCHIVE.md`. |
| ≤2026-07-19 | Foundation, M1-M4, H1-H3, F1-F5, K1-K4, R3 — one line per session in the archive. |
