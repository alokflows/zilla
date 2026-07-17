# ⚡ Zilla

**A personal AI that lives on your own computer — and feels effortless everywhere you talk to it.**

Your knowledge stays in plain Markdown files on your own disk. The "brain" is rented from whichever free agentic AI CLI you have today — **Claude Code**, **agy** (Antigravity/Gemini), soon **opencode** — and is swappable tomorrow with a settings change, not a migration.

> **The knowledge is yours. The brain is rented.**
> A login expires, a quota tightens, a product shuts down — and Zilla shrugs. Your data never leaves your machine, and no model owns your memory.

Zilla is a **harness**, not another agent. The CLIs already reason, run tools, browse, and remember. Zilla's job is everything around that: shaping context, enforcing policy, keeping sessions and schedules straight, watching health, and giving you clean interfaces — a **full-screen terminal app** first, **Telegram** as a connector in your pocket.

---

## The feel we're building

Zilla is an unapologetic replica of the experience the best personal agents (OpenClaw, Hermes) deliver — rebuilt for CLI logins instead of API keys, with a terminal-first face. That experience, concretely:

- **It never feels dead.** The moment you send a message, something happens — an acknowledgment in under a second, then live progress ("⚙️ running command…", "🌐 browsing…") streaming into the chat while the agent works.
- **It never shows you an error it can fix itself.** If a task hits a missing tool — say a voice note arrives and there's no audio converter — the agent installs it, retries, and answers. You see the transcription, not the stack trace. Problems reach you only when a human is genuinely needed, in one plain sentence.
- **It reads its own answer before you do.** Every outbound response passes a deterministic review gate — empty output, error garbage, and rate-limit debris never reach your chat. One bounded corrective retry, then an honest plain-language stop. Never a silent loop.
- **Reminders behave like an alarm clock.** "Put a timer for 2 minutes" is created instantly, fires exactly on time, and costs zero AI calls at fire time. "Every day at 9am summarise my inbox" runs the full agent and messages you the result — and catches up if the machine was asleep.
- **It heals itself at 3am.** Background health checks fix what a program can fix, silently. If a CLI login expires, Zilla doesn't go dumb and stale — it DMs you the login link, you paste the token back into the chat, and it logs itself back in and carries on. One alert, exact recovery steps, no spam.
- **It knows you — and keeps that knowledge in your files.** A personal wiki of Markdown pages, built from conversation (a first-run interview, then a daily journal distilled into durable pages). Switch brains and it still knows you, because the memory was never inside the model.

---

## What works today (Telegram interface, in daily use)

- **Converse naturally** — text, voice notes, photos, documents.
- **Feels alive** — cancel any run mid-flight; long tasks show a working indicator.
- **Named sessions** — parallel conversations with separate memories (`/new`, `/sessions`, `/switch`).
- **Instant reminders & scheduled automation** — one-off reminders create with zero friction; recurring jobs confirm once, then run themselves; retry ladder + catch-up after downtime; failures degrade gracefully and never permanently disable a schedule.
- **Human-in-the-loop** — when a task needs an OTP or password, the agent pauses, asks you in chat, uses your reply once, and wipes it.
- **Approval mode** — a *limited* user can ask, but nothing runs until the owner taps ✅.
- **Backend & model switching** live from the settings menu — histories stay correctly separated per backend.
- **Cross-platform** — macOS, Linux, Windows; guided installer; `--doctor` self-check.

## Where it's headed (in active construction — see [`HANDOFF.md`](HANDOFF.md))

- **`zilla`** — a full-screen terminal app that looks like a real product the moment it opens: chat pane, live progress, settings, skills, and health screens; conversational onboarding ("connect to my Telegram" → it asks for the token and wires itself up).
- **Orchestration router** — a cheap triage pass on every message: small talk answers fast, complex work gets the full agent, and anything you *share* about your life is journaled into the wiki automatically.
- **Fallback chain** — primary backend errors out or hits a limit → the same request silently retries on your next backend. You get one clean answer.
- **Skills from chat** — "make that into a skill" produces a reusable skill; code-bearing skills wait for one owner approval tap before first run — enforced by Zilla's code, never by the model's judgment.
- **Assisted re-login, heartbeat, usage counters** — the self-healing layer above, completed.
- **Offline voice** — local Whisper alongside the current online transcription.

---

## Design principles

- **Self-hosted, single-owner.** Your machine, your CLI logins. No central server, no account with us, no telemetry, zero paid dependencies — no API keys, ever.
- **A harness, not another agent.** Never reimplement what the backend CLI already does. Route, configure, supervise.
- **Deterministic safety.** Every security decision (who may run what, what needs approval) is enforced by Zilla's own code. Untrusted text talks to the model; it never talks to the policy.
- **No exposed surfaces.** No web UI, no listening network ports, no skills marketplace auto-install — each of those has already burned other agent products (documented in `docs/dev/`). Any future socket: authenticated + loopback from day one.
- **Plain files.** `.env` + `settings.json` for config, human-readable JSON for state, Markdown for knowledge. Nothing is locked in.

---

## Quick start (Telegram interface)

**You need:** Python 3.10+ · one AI backend logged in ([Claude Code](https://docs.anthropic.com/en/docs/claude-code) or agy — run it once in a terminal and sign in) · a bot token from [@BotFather](https://t.me/BotFather) · your numeric ID from [@userinfobot](https://t.me/userinfobot).

| OS | Run |
|----|-----|
| Windows | `install.bat` |
| macOS | `install.command` |
| Linux | `bash install.sh` |

The installer detects your backend, asks for the two values, and starts the bot. Message your bot, send `/menu`, you're in. Something off? `python install.py --doctor` prints a full green/red health report.

```
./start.sh / ./stop.sh                        # macOS, Linux
START_BACKGROUND.bat / STOP_BACKGROUND.bat    # Windows
```

Day-to-day usage: **[MANUAL.md](MANUAL.md)** · Release notes: **[CHANGELOG.md](CHANGELOG.md)**

---

## Security model

Read this before adding anyone but yourself.

- The backend CLI executes real tools — files, shell — on the host. That capability **is the product**, and it means a full (admin) user can effectively run code on that computer through chat. Only grant admin to someone you'd trust with the keyboard.
- **Approval mode** exists for everyone else: limited users' requests wait for the owner's ✅, every time.
- Only the owner can manage users. Secrets are git-ignored and written owner-only; OTP/password replies are deleted from chat after use; unattended command schedules are owner-only at creation.
- The hard security boundary is the operating system, not the model. Exposed deployments run Zilla under a dedicated OS user with systemd hardening (guide ships with the Linux deployment phase).

---

## Architecture

```
You ── terminal app (next) · Telegram (today)
        │            events in, events out
        ▼
   ZillaCore ──  turn pipeline · sessions · scheduler · OTP bridge
        │        approvals · health · review gate      (zilla/ package)
        ▼
 Agentic CLI (claude / agy / opencode) ── reasoning · tools · memory
```

The core is interface-agnostic: every frontend speaks one small event vocabulary (`Progress`, `Response`, `Ask`, `ApprovalRequest`, `Alert`, `ScheduledResult`), so the terminal app and Telegram are thin renderers over the same brainstem.

| Component | Role |
|---|---|
| `zilla/core.py` | `ZillaCore`: turn pipeline, scheduler runtime, credential/OTP bridge, health — the product's engine |
| `bot.py` | Telegram connector: handlers, menus, delivery — a renderer, not a brain |
| `zilla/cli_engine.py` / `zilla/backends.py` | Backend contract; agy under a real PTY, Claude Code via JSON pipe |
| `zilla/harness.py` | Per-turn context: trust contract, style, skills index |
| `zilla/sessions.py` / `zilla/schedules.py` | Named conversations; the automation engine + natural-language schedule parser |
| `zilla/interactive.py` | Human-in-the-loop ask/answer file bridge |
| `zilla/users.py` | Owner / admin / limited tiers, approval queue |
| `zilla/config.py` | One source of truth: `.env`, `settings.json`, backend-aware model catalog |
| `zilla/platform_compat.py` | The only OS-specific code in the tree |

Engineering plan, invariants, live status: [`HANDOFF.md`](HANDOFF.md) and `docs/dev/`. **Tests:** 350+ across four suites, plain Python, no framework, all green before every commit.

---

## Project status

Actively developed, in daily use by its owner. The Telegram interface is stable; the terminal application and orchestration layer are under construction on the working branch. If something doesn't behave as documented, `python install.py --doctor` plus the newest file in `logs/` tells most of the story.
