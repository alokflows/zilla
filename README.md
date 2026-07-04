# ⚡ Zilla — Your AI, inside Telegram

> Talk to a powerful AI right inside your Telegram chat — by text, voice, photos, or files.
> It can also **run scheduled jobs** for you and message you the results automatically.

Zilla is a **bridge**. Your message goes into Telegram → Zilla hands it to an AI command-line tool on your computer (**agy** or **Claude Code**) → the AI does the work → Zilla sends the answer back to your chat.

- Works on **Windows, macOS, and Linux**.
- Runs on **either** backend: **agy** (Gemini) **or** **Claude Code** (Opus/Sonnet/Haiku) — switch anytime.
- **One-click installer.** Log into your AI tool, paste two values, done.

> 📜 What's new: see **[CHANGELOG.md](CHANGELOG.md)** (latest **v4.0.0** — cross-platform + Claude Code backend).

---

## 🧸 Explain it like I'm 3

1. 📱 You **type into Telegram**.
2. 📨 Zilla is the **messenger** that carries your words to the AI.
3. 🧠 The **AI** (agy *or* Claude Code) thinks and writes the answer.
4. 📨 Zilla brings the answer **back to your chat**.

That's it. You only ever touch Telegram.

---

## ✅ What you need first (prerequisites)

You need **four** things. Don't worry — steps for each are below.

1. **Python 3.10 or newer** — the language Zilla is written in.
2. **An AI backend CLI** — pick ONE (you can install both and switch later):
   - **agy** (antigravity CLI, Gemini), or
   - **Claude Code** (`claude`).
3. **A Telegram bot token** — a secret password for your bot, from **@BotFather**.
4. **Your Telegram numeric ID** — so the bot knows *you* are the owner, from **@userinfobot**.

### 1) Install Python
- **Windows:** download from <https://python.org> → run installer → **tick "Add Python to PATH"**.
- **macOS:** `brew install python` (or from python.org).
- **Linux (Ubuntu/Debian):** `sudo apt update && sudo apt install python3 python3-pip`.
- Check it works: open a terminal and run `python --version` (or `python3 --version`).

### 2) Install your AI backend and LOG IN (very important)
**Option A — agy (Gemini):**
- Install the antigravity CLI (`agy`).
- Run `agy` once in a terminal and **sign in** when it asks. Close it after.

**Option B — Claude Code (`claude`):**
- Install Claude Code (see Anthropic's docs).
- Run `claude` once in a terminal and **sign in** (it opens a browser). Close it after.

> 🔑 **The login is what makes it work.** Zilla cannot log in for you — you log into the CLI once on that computer, and Zilla reuses that login forever.

### 3) Get a bot token (@BotFather)
1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot`, pick a name and a username ending in `bot`.
3. BotFather replies with a **token** like `123456:ABC-DEF...`. Copy it. Keep it secret.

### 4) Get your Telegram ID (@userinfobot)
1. Open **@userinfobot** in Telegram and send any message.
2. It replies with your **numeric ID** (e.g. `8740189938`). Copy it.

---

## 🚀 Install (one click)

1. Download/clone this folder onto the computer that will run the bot.
2. Run the installer for your OS:

| OS | Do this |
|----|---------|
| **Windows** | **Double-click `install.bat`** |
| **macOS** | **Double-click `install.command`** (first time: right-click → Open) |
| **Linux** | In a terminal: `bash install.sh` |

3. The installer will:
   - install the Python pieces,
   - ask **which backend** (agy or Claude Code),
   - ask for your **bot token** and **your Telegram ID**,
   - ask **"auto-start at login? (y/n)"**,
   - remind you to **log into your CLI** if you haven't,
   - **start the bot**.

4. Open Telegram, message your bot — you should get a **"⚡ Zilla is online"** message. Send **`/menu`**. 🎉

> 🩺 **Something off?** Run `python install.py --doctor` — it checks Python, dependencies, your CLI + login, token, and ID, and prints a green/red report.

---

## 🔀 Choosing & switching the backend (agy ⇄ Claude Code)

Two easy ways:

- **From Telegram:** `/settings` → tap **🧠 Backend** to toggle agy ⇄ claude. Takes effect on your next message.
- **In the `.env` file:** set `BACKEND=claude` (or `BACKEND=agy`) and restart.

**Pick the model** for whichever backend is active: `/model` (or **🤖 Model** in `/menu`).
- agy shows Gemini models × Low/Med/High thinking.
- Claude Code shows **Opus / Sonnet / Haiku**.
- **✏️ Custom** lets you type any exact model name the CLI supports.

> Want a *different* `claude`/`agy` binary? Set `CLAUDE_PATH=` or `CLI_PATH=` in `.env`. (Also documented in `config.py` and `backends.py`.)

---

## 👥 Sharing it with a friend

There are two ways:

**A) Add them to YOUR bot (no install for them).**
- `/menu` → **👥 Users** → **➕ Add User** → paste their Telegram ID → name. They become an **admin**.
- ⚠️ Anyone you add can run commands/files on **your** computer (see Security). Only add people you trust.

**B) Give them their OWN bot on THEIR PC (recommended for a friend).**
- They install Python + their backend CLI (agy or Claude Code) and **log in**.
- They make their own bot token (@BotFather) and get their own ID (@userinfobot).
- They copy this folder and run the installer. Done — a fully independent bot on their machine.

---

## ⏰ Schedules (automation)

Make the bot do things on a timer and message you the result.

- Command: `/schedule`
  - `/schedule daily 09:00 summarise my inbox`
  - `/schedule every 5h check the news`
  - `/schedule once 2026-06-10 18:30 wish happy birthday`
  - `/schedule mon,wed,fri 09:00 stand-up notes`
- Or just say it: **"every day at 9am summarise my inbox"** → confirm card → done.
- Manage: pause/resume, ▶️ run-now, 🗑 delete from the `/schedule` panel.
- **Catch-up:** if the PC was off when a job was due, it runs once on startup. Toggle in `/settings` → **⏰ Catch up missed schedules**.

---

## 💬 Commands (also in the `/` menu)

| Command | What it does |
|---------|--------------|
| *(just type)* | Ask the AI anything |
| `/menu` | Buttons for everything |
| `/schedule` | Add / manage scheduled jobs |
| `/model` | Pick the AI model |
| `/settings` | Backend, schedules catch-up, etc. |
| `/new`, `/sessions`, `/switch`, `/end` | Separate conversations (memories) |
| `/brain` | Inbox stats |
| `/browse <url>` | Control a browser (WebBridge) |
| `/cancel` | Stop a running request |
| `/ping`, `/help` | Status / help |
| `/adduser`, `/removeuser`, `/listusers` | Owner only: manage admins |

---

## 🔄 Updating

```
git pull
python install.py --doctor   # confirm everything still green
```
Then restart (Windows: `STOP_BACKGROUND.bat` then `START_BACKGROUND.bat`; macOS/Linux: `./stop.sh` then `./start.sh`).

---

## 🛟 Troubleshooting

- **No "online" message / bot silent** → `python install.py --doctor`. Most often: CLI not logged in, or wrong token.
- **"not logged in" / model errors** → run your CLI (`agy` or `claude`) in a terminal and sign in again.
- **Menus feel slow** → make sure only ONE copy is running (the installer enforces a single instance).
- **Logs** are in the `logs/` folder (newest file).
- **Stop the bot:** Windows `STOP_BACKGROUND.bat`; macOS/Linux `./stop.sh`.
- **Start it:** Windows `START_BACKGROUND.bat`; macOS/Linux `./start.sh`.

---

## 🔐 Security — read this

Zilla runs your AI CLI with permissions to read/write files and run commands **on the computer it's installed on**. That power is the point (it can actually do tasks) — but it means:

- **An admin can effectively run code on that machine** through chat — the AI CLI executes tools on your behalf. **Only give full (admin) access to people you trust with that computer.**
- **Not sure you trust them that much? Use Approval mode.** Add them as a *limited* user and every request they send waits for you to tap ✅ Approve before anything runs. Good for a student/helper. (Pick the tier when adding them, or toggle it later in the Users panel.)
- Only the **owner** (you) can add/remove users.
- Your bot **token** and your **.env** are secrets — they're git-ignored; never share them.
- **If your token ever leaks** (posted a screenshot, pasted it somewhere public), revoke it: message **@BotFather → /revoke**, pick the bot, and put the new token in your `.env`, then restart. A leaked token doesn't let someone control your computer, but they could disrupt the bot or message you *as* it — so rotate it to be safe.
- Each person who wants their own safe setup should run their **own** bot on their **own** PC (Sharing → option B).

---

## 🗂️ How it's built (for the curious)

- `bot.py` — Telegram handlers, menus, scheduler loop.
- `backends.py` — chooses & runs the AI CLI (**agy** or **Claude Code**). *Comments explain how to switch/add a backend.*
- `platform_compat.py` — the only OS-specific code (lock, window-hiding, PTY) for Windows/macOS/Linux.
- `cli_engine.py` — runs the agy CLI via a pseudo-terminal; delegates to the chosen backend.
- `schedules.py` / `schedule_parse.py` — the automation engine + natural-language parser.
- `config.py` — all settings (reads `.env`); platform-aware paths; backend-aware model handling.
- `install.py` — the cross-platform installer + `--doctor` self-check.
- `test_fixes.py` — 90+ unit tests (`python test_fixes.py`).
