# AGY Telegram Bot v8

> **Thin pipe to CLI** — the bot relays, the CLI thinks.

A lightweight Telegram bot that wraps the Antigravity CLI (or any text-in/text-out CLI). Chat from your phone, send media, switch sessions, browse the web — all through Telegram.

## Architecture

```
You (Telegram) ↔ bot.py ↔ cli_engine.py ↔ agy.exe (CLI)
                   ↕
              formatter.py (output formatting)
              media.py (voice/photo/docs)
              sessions.py (conversation management)
              users.py (multi-user auth)
              config.py (portable settings)
```

**7 files. ~2000 lines. That's it.**

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Telegram handlers, inline UI, single callback router |
| `cli_engine.py` | ConPTY wrapper for CLI + transcript reading |
| `config.py` | Portable env/paths config |
| `sessions.py` | Per-user session management |
| `media.py` | Audio transcription + file handling |
| `users.py` | Multi-user auth with `/adduser` |
| `formatter.py` | Telegram markdown formatting |

## Setup

1. Copy this folder anywhere
2. Create `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_OWNER_ID=your_telegram_id
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Run:
   ```
   python bot.py
   ```

## Running Persistently (Background)

**Option A: Double-click** `run_bot_hidden.vbs` — runs invisibly, auto-restarts on crash.

**Option B: Auto-start on login** — run `INSTALL_STARTUP.bat` (no admin needed).

To remove from startup: run `UNINSTALL_STARTUP.bat`.

## Commands

| Command | What it does |
|---------|-------------|
| Just type anything | Goes straight to CLI → response |
| `/menu` | Control panel with inline buttons |
| `/new <name>` | Create new session |
| `/sessions` | List all sessions |
| `/switch <name>` | Switch session |
| `/model` | Select AI model |
| `/browse <url>` | Open URL via Kimi WebBridge |
| `/browse screenshot` | Take browser screenshot |
| `/brain` | Inbox stats |
| `/adduser <id>` | Add authorized user (owner only) |
| `/ping` | Health check |

## Media

- **Voice** → transcribe → show transcription → send to CLI
- **Photo** → save to inbox → auto-analyze (or add caption for specific question)
- **Document** → save to inbox
- **Video** → save to inbox

## Kimi WebBridge

The bot integrates with [Kimi WebBridge](http://127.0.0.1:10086) for real browser control. Tell the CLI to "browse google.com" or use `/browse` commands directly.

## Portability

Nothing is hardcoded. Copy the folder, set `.env`, run. Works on any Windows machine with Python 3.10+ and the CLI installed.
