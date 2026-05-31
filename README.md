# Zilla Bot

> **Telegram as the control panel for any AI CLI.**

A minimal, production-grade Telegram bot that wraps the Antigravity CLI (or any text-in/text-out CLI). The bot does one thing: relay. The CLI thinks.

---

## Architecture

```
You (Telegram) ↔ bot.py ↔ cli_engine.py ↔ agy.exe (CLI)
                     ↕              ↕
               formatter.py    transcript.jsonl
               media.py        (authoritative response)
               sessions.py
               users.py
               config.py
```

**7 files. ~2 000 lines. That's it.**

The design is a strict separation of concerns:
- `bot.py` — Telegram I/O, inline keyboards, permission gates
- `cli_engine.py` — ConPTY subprocess, idle reaper, response extraction
- `config.py` — all configuration, reads from `.env`
- `sessions.py` — per-user CLI conversation mapping
- `users.py` — three-tier auth (owner / admin / user)
- `media.py` — voice transcription, file save/extract
- `formatter.py` — CLI output → Telegram-safe markdown

---

## Security Model

Three roles with hard capability gates:

| Capability | User | Admin | Owner |
|---|:---:|:---:|:---:|
| Chat (text, voice, photo, doc) | ✅ | ✅ | ✅ |
| Save media to Inbox | ✅ | ✅ | ✅ |
| Receive generated files | ❌ | ✅ | ✅ |
| Change AI model | ❌ | ✅ | ✅ |
| Change bot settings | ❌ | ✅ | ✅ |
| `/browse` (controls logged-in browser) | ❌ | ✅ | ✅ |
| Add / remove users, set roles | ❌ | ❌ | ✅ |

Additional hardening:
- File delivery uses `os.path.realpath` (symlink-safe) with an explicit allowlist — only `~/AGI-Brain` and the current conversation's output directory.
- `%TEMP%` is not in the allowlist — no credentials or browser artifacts can be exfiltrated.
- Auth is re-checked on every update (mtime-gated disk read — zero overhead when nothing changes).
- Bot only subscribes to `message`, `callback_query`, `edited_message` update types.

---

## Timeout Design

No wall-clock timeout. The bot waits for the CLI.

**Idle reaper:** the CLI is killed only if it produces *no output* **and** writes *no new transcript step* for `IDLE_KILL_AFTER` seconds (default 10 min, configurable). Any activity resets the clock.

**Catastrophic ceiling:** `MAX_TOTAL_RUNTIME` (default 1 hour) stops genuinely stuck processes regardless of activity.

**Cancel:** the user can send `/cancel` or tap the `[🛑 Cancel]` inline button that appears after 60 seconds. Whatever the CLI produced up to that point is delivered — nothing is discarded.

**Progress UX:** 0–60s = native Telegram typing bubble only. After 60s, one message is sent and *edited in place* every 60s with elapsed time and a Cancel button. No message spam.

---

## Document Intelligence

Send any file (PDF, DOCX, TXT, CSV, JSON…):
- **No caption** → saved to Inbox, confirmed.
- **With caption** → text extracted locally, sent to CLI with your question. e.g. caption "summarize in 3 bullets" → summary.

Same pattern for photos: no caption saves, caption analyzes.

---

## User Management

Fully interactive from Telegram. Owner opens `/menu → 👥 Users`:

```
👥 Users (2)
[user] Alice    [admin] Bob
[➕ Add User]   [◀ Menu]
```

Tap a user → see details → toggle role (User ↔ Admin) or remove. Adding a user is a 3-step inline flow (ID → name → role buttons) — no commands required.

---

## Setup

```bash
# 1. Clone / copy the folder
# 2. Create .env (see .env for all options):
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_OWNER_ID=your_telegram_id

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python bot.py
```

All paths are relative to your home directory or configurable in `.env`. Copy the folder to any machine, set `.env`, run.

---

## Running Persistently

**Option A — double-click** `run_bot_hidden.vbs` — runs invisibly, auto-restarts on crash.

**Option B — auto-start on login** — run `INSTALL_STARTUP.bat` (no admin needed). Remove with `UNINSTALL_STARTUP.bat`.

---

## Commands

| Command | Access | Description |
|---------|--------|-------------|
| Just type anything | all | Goes straight to CLI |
| `/menu` | all | Control panel (tiles adapt to your role) |
| `/cancel` | all | Cancel the running request |
| `/new <name>` | all | New session |
| `/sessions` | all | List sessions |
| `/switch <name>` | all | Switch session |
| `/end` | all | End current session |
| `/brain` | all | Inbox stats |
| `/model` | admin+ | Select AI model |
| `/settings` | admin+ | Bot settings |
| `/browse <url>` | admin+ | Browser control via WebBridge |
| `/adduser <id> [name]` | owner | Add user |
| `/removeuser <id>` | owner | Remove user |
| `/listusers` | owner | Manage users |
| `/ping` | all | Health check |
