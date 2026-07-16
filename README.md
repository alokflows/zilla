# Zilla

**A self-hosted AI assistant that lives on your computer — your knowledge in plain files you own, the "brain" rented from whichever AI CLI you have today and swappable tomorrow.**

Zilla is a harness over agentic AI CLIs (**Claude Code**, **agy**/Antigravity, **opencode**). The CLIs already know how to reason, run tools, browse, and remember conversations — Zilla's job is everything around that: shaping context, enforcing policy, managing sessions and schedules, keeping the system healthy, and giving you clean interfaces to talk through.

> **The knowledge is yours. The brain is rented.**
> Access to any given model can vanish overnight — a login expires, a quota tightens, a product shuts down. Zilla is built so that surviving that is a settings change, not a migration. Your data never leaves your machine.

---

## Design principles

- **Self-hosted, single-owner.** Runs entirely on your computer with your own CLI logins. No central server, no account with us, no telemetry. Zero paid dependencies — CLI logins only, no API keys.
- **A harness, not another agent.** Zilla never reimplements what the backend CLI already does (tools, memory, skills, browsing). It routes, configures, and supervises.
- **Backend-agnostic.** Switch between Claude Code, agy, and (soon) opencode from the settings — mid-conversation histories stay correctly separated per backend.
- **Deterministic safety.** Every security decision (who may run what, what needs approval) is enforced by Zilla's own code, never delegated to a model's judgment.
- **Plain files.** Configuration is `.env` + `settings.json`. State is human-readable JSON. Nothing is locked in.

---

## What works today

The current interface is **Telegram** — a private bot that bridges your chat to the AI CLI on your machine:

- **Converse naturally** — text, voice notes, photos, and documents.
- **Named sessions** — parallel conversations with separate memories (`/new`, `/sessions`, `/switch`).
- **Scheduled automation** — "every day at 9am summarise my inbox" becomes a recurring job that messages you the result; missed jobs catch up on startup.
- **Human-in-the-loop** — when a task needs a credential or an OTP, the agent pauses, you're asked in chat, your reply is used once and wiped.
- **Approval mode** — add a second user as *limited*: they can ask, but nothing runs until the owner taps ✅ Approve.
- **Backend & model switching** from the settings menu, live catalogs included.
- **Cross-platform** — macOS, Linux, and Windows, with a guided installer and a `--doctor` self-check.

## Where it's headed

Zilla is being rebuilt as a **terminal-first application** (work in progress, tracked in [`HANDOFF.md`](HANDOFF.md)):

- **`zilla`** — a full-screen terminal UI: chat with the AI directly, settings, skills, and health screens. Telegram becomes an optional connector you enable in one sentence ("connect to my Telegram").
- **A personal wiki** — the assistant's persistent knowledge as portable Markdown on your disk, built up from conversation. It outlives every model swap.
- **Fallback chain** — if the primary backend errors out or hits a limit, the same request retries on the next backend in your priority order. You get one clean answer.
- **Silent self-healing** — background health checks that fix what a program can fix and alert you only when a human is genuinely needed, with plain-language recovery steps.
- **Skills from chat** — "make that into a skill" produces a reusable skill; code-bearing skills need one owner approval before first run.
- **Offline voice** — a local Whisper option alongside the current online transcription.

---

## Quick start (Telegram interface)

**Prerequisites**

1. **Python 3.10+**
2. **One AI backend, logged in** — [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`) or agy. Run it once in a terminal and sign in; Zilla reuses that login.
3. **A Telegram bot token** — from [@BotFather](https://t.me/BotFather) (`/newbot`).
4. **Your Telegram numeric ID** — from [@userinfobot](https://t.me/userinfobot).

**Install**

| OS | Run |
|----|-----|
| Windows | `install.bat` |
| macOS | `install.command` |
| Linux | `bash install.sh` |

The installer sets up dependencies, detects which backend is installed, asks for your token and ID, and starts the bot. Message your bot on Telegram, send `/menu`, and you're in.

Something off? `python install.py --doctor` prints a full green/red health report (Python, dependencies, CLI login, token, ID).

**Operate**

```
./start.sh / ./stop.sh          # macOS, Linux
START_BACKGROUND.bat / STOP_BACKGROUND.bat   # Windows
```

Full command reference and day-to-day usage: **[MANUAL.md](MANUAL.md)**. Release notes: **[CHANGELOG.md](CHANGELOG.md)**.

---

## Security model

Read this before adding anyone but yourself.

- The backend CLI executes real tools — files, shell — on the host machine. That capability is the product, and it means **a full (admin) user can effectively run code on that computer through chat**. Only grant admin to someone you'd trust with the keyboard.
- **Approval mode** exists for everyone else: limited users' requests are held until the owner approves each one.
- Only the owner can manage users. Secrets (`.env`, token) are git-ignored and written with owner-only permissions; OTP/password replies are wiped from chat after use.
- A leaked bot token can't control your computer, but rotate it anyway: @BotFather → `/revoke`, update `.env`, restart.
- The hard security boundary is the operating system, not the model. For exposed deployments, run Zilla under a dedicated OS user (hardening guide ships with the Linux deployment phase).

---

## Architecture

```
You (Telegram today, terminal UI next)
        │
        ▼
   Zilla core ── context shaping · policy · sessions · schedules · health
        │
        ▼
 Agentic CLI (claude / agy / opencode) ── reasoning · tools · memory
```

| Component | Role |
|---|---|
| `bot.py` | Telegram interface: handlers, menus, delivery |
| `cli_engine.py` / `backends.py` | Backend contract; runs agy under a real PTY, Claude Code via JSON pipe |
| `harness.py` | Per-turn context injection: trust contract, style, skills index |
| `sessions.py` / `schedules.py` | Named conversations; the automation engine + natural-language schedule parser |
| `interactive.py` | The human-in-the-loop ask/answer bridge (OTP, confirmations) |
| `users.py` | Owner / admin / limited tiers, approval queue |
| `config.py` | Single source of truth: `.env`, `settings.json`, backend-aware model catalog |
| `platform_compat.py` | The only OS-specific code (locks, PTY, window hiding) |
| `install.py` | Guided installer + `--doctor` |

Core modules are being consolidated into the `zilla/` package as part of the terminal-app rebuild — see [`HANDOFF.md`](HANDOFF.md) and `docs/dev/` for the engineering plan, invariants, and current status.

**Tests:** `python test_fixes.py` and `python test_interactive.py` (208 tests, no framework required).

---

## Project status

Actively developed. The Telegram interface is stable and in daily use; the terminal application is under construction on the current working branch. If you try Zilla and something doesn't behave as documented, the `--doctor` report plus the newest file in `logs/` tells most of the story.
