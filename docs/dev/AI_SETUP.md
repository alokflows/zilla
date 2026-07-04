# 🤖 Zilla — Autonomous AI Setup

**You are an automated installer.** Follow these steps exactly, in order, running the
shell commands yourself. The ONLY things you need from the human are: the **bot token**
and their **Telegram numeric ID** (and optionally which backend). Ask for anything
missing, then do everything else without further questions. At the end, confirm the bot
sent its "online" message in Telegram.

---

## What you're installing
Zilla — a Telegram bot that pipes messages to an AI CLI (agy or Claude Code) and can run
scheduled jobs. Repo: `https://github.com/alokflows/zilla`

## Inputs to collect from the human (ask only if not already given)
1. **BOT_TOKEN** — from @BotFather (`/newbot`).
2. **OWNER_ID** — their numeric Telegram ID (from @userinfobot).
3. **BACKEND** — `agy` (default) or `claude`. If they don't care, use `agy`.

---

## Steps (run these commands; adapt the package manager to the OS)

### 1. Make sure git + Python 3.10+ exist
- Check: `git --version` and `python --version` (or `python3 --version`).
- If **git** is missing: Windows `winget install --id Git.Git -e --source winget` · macOS `brew install git` · Linux `sudo apt install -y git`.
- If **Python** is missing: Windows `winget install --id Python.Python.3.12 -e` · macOS `brew install python` · Linux `sudo apt install -y python3 python3-pip`.
- Re-open the shell if PATH changed.

### 2. Get the code (clone fresh, or update if present)
```
# pick a home location
cd ~            # Windows: cd %USERPROFILE%
git clone https://github.com/alokflows/zilla.git  ||  (cd zilla && git pull)
cd zilla
```

### 3. Make sure the chosen AI CLI is LOGGED IN
- If BACKEND = `agy`: confirm `agy` is installed and the human has signed in (run `agy` once interactively if not).
- If BACKEND = `claude`: confirm `claude` is installed and signed in.
- This login is required — the bot reuses it. If not logged in, tell the human to run the CLI once and sign in, then continue.

### 4. Run the installer NON-INTERACTIVELY with the collected values
```
python install.py --backend <BACKEND> --token <BOT_TOKEN> --owner <OWNER_ID>
```
(Use `python3` on macOS/Linux. This installs dependencies, writes `.env`, sets up
auto-start at login, and launches the bot in the background.)

### 5. Verify
- Run `python install.py --doctor` and make sure every line is ✅ (Python, dependencies,
  the CLI found + logged in, token set, owner id set).
- Tell the human to check Telegram — they should receive **"⚡ Zilla is online"**.
- If they don't get the message within ~30s, read the newest file in `logs/`, fix the
  issue (usually: CLI not logged in, or wrong token), and retry step 4.

### 6. Report
Tell the human: which backend is active, that auto-start is enabled, and that they can
now message their bot and send `/menu`. Done.

---

## Notes / guardrails
- Do **not** invent a token or ID — ask the human.
- Each person runs their **own** bot with their **own** token; never reuse someone else's.
- If a command needs elevated permission and you can't proceed, tell the human the exact
  command to run, then continue once it's done.
- One file is all the human needed: this one. Everything else you clone and run yourself.
