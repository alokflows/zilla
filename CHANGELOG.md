# 📜 Zilla — Changelog

All notable changes, newest first. Versions are git tags (e.g. `v2.2.0`).

---

## 🎯 Installer picks the backend that's actually installed *(latest)*

Built for shared/office machines where only ONE backend is present and you have
no admin rights. Setup now follows reality instead of asking blindly:

- **Detects `agy` and `claude`** the same way the running bot does (PATH + the
  OS-specific install location) — so the installer and the bot always agree.
- **Only one installed → it's chosen automatically** and named back to you.
- **Both installed → it asks** which to use (1 or 2).
- **Neither installed → a clear message** telling you to install one and re-run,
  instead of silently writing a config that points at a missing CLI.
- `--doctor` uses the same detection, so it no longer misses an installed-but-
  not-on-PATH CLI. All 174 tests pass.

---

## 🧹 Code cleanup & structure pass

Behaviour-preserving tidy-up — the bot does exactly what it did before, but the
code is smaller and easier to work on (and safer to edit, including by AI).

- **`bot.py` slimmed from 2,804 → ~2,510 lines.**
- **New `keyboards.py`** holds all 14 inline-menu builders (pure UI: data in, buttons out). Verified by invoking every builder.
- **The 566-line button handler is now a thin dispatcher** routing to eight focused `_cb_*` helpers (one per feature). Proven identical by a characterization harness that compared all 46 button paths before/after.
- **One shared `_run_cli_turn` helper** replaces the ~18-line CLI-turn block that the text/voice/photo/document handlers each copied. Proven identical the same way.
- **Bug fix:** document text extraction now runs off the event loop (`asyncio.to_thread`) — a large PDF no longer freezes the bot for everyone.
- Removed the empty `SCRATCH.md`. All 174 tests pass.

---

## ⚡ Natural-language schedules understand spelled-out numbers

- **Fixed the "every three minutes" hang.** A request like *"Schedule every three minutes, send me a screenshot"* used to fall through the schedule parser (it only understood digits), so the whole message was handed to the agent — which spent many minutes running the task instead of creating a schedule. The parser now rewrites spelled-out numbers (`three` → `3`, `twenty five` → `25`, up to 99) before matching, so these become an instant **📅 Create this schedule?** confirmation. Once created, the recurring screenshot uses the fast bridge path, not the agent.
- Number rewriting is scoped so ordinary words are untouched (`a screenshot`, `someone`, `anyone` stay as-is). Covered by new tests in `test_fixes.py`.
- **On-demand screenshots are fast too.** A bare *"send me a screenshot"* / *"send display screenshot"* typed directly used to go through the agent (≈1 min). It now takes the same fast bridge path the `/browse screenshot` command and scheduled screenshots use, returning in seconds. It only triggers on short, single-intent requests (anything with `then`/`after`/`and`, or longer than 8 words, still goes to the agent), and if the bridge isn't reachable it silently falls back to the agent — so it's a pure speed-up, never a regression.

---

## 🧹 v4.1.1 — Audit & cleanup

- Audited the v4.1.0 changes: working tree clean, no leftover temp/probe files, 96/96 tests pass.
- Removed dead code left over from the cross-platform refactor (an unused `command` string + the now-unused `subprocess` import in `cli_engine`).
- Confirmed intact: the long-run **⏳ Working… [Cancel]** progress message, the idle-reaper delivery (now sourced from the clean transcript — you still get the partial answer, without the old screen-bleed), and the cancel flow. No behavior change beyond the cleanup.

---

## 🧪 v4.1.0 — Testing-round fixes (menus, inbox, flashing, cancel, backend)

Fixes from hands-on testing:

- **No more stale-menu collisions.** Every menu now has a **✕ Close** button, and opening a new menu automatically **kills the previous menu's buttons** — so tapping an old menu in your chat history can't silently change your session anymore.
- **Inbox: delete files.** Each file row now has **🗑 Delete** (alongside 📤 Send) that removes the file from disk immediately (path-validated) and refreshes the list.
- **No more scary console flashing.** A Windows window-suppressor hides the console windows that agy (and its child tools / ConPTY) briefly popped during a run — including on `/browse`. Nothing flashes now.
- **Cancel no longer bleeds old replies.** Canceling mid-run used to dump the raw terminal screen, which still showed the *previous* answer. Cancel now returns a clean "🛑 Canceled" (plus only the current turn's output if any) — verified.
- **Backend switch is clean.** Conversation IDs are tagged with their backend; switching agy ⇄ claude now **starts a fresh conversation** instead of erroring on a mismatched ID.
- **Reach Claude faster.** The `/model` screen shows the active backend and has a **🧠 Use claude / Use agy** button to switch right there; the model list matches the active backend.

---

## 🌍 v4.0.0 — Cross-platform + pluggable backends + one-click installer

The "install anywhere, run on any AI" release.

### 🧠 Pluggable backends — agy **or** Claude Code
- The bot now runs on **either** the antigravity `agy` CLI (Gemini) **or** **Claude Code** (Opus/Sonnet/Haiku).
- Switch live from **/settings → 🧠 Backend**, or set `BACKEND=claude` in `.env`.
- Claude Code uses `claude -p --output-format json` (clean answer + session id, memory via `--resume`) — verified end-to-end. The model picker adapts per backend; ✏️ Custom still works.
- `backends.py` is heavily commented on how to switch/add a backend.

### 🌍 Cross-platform (Windows / macOS / Linux)
- New `platform_compat.py` isolates every OS-specific bit: single-instance lock (`msvcrt`↔`fcntl`), window-hiding (Windows-only), and a PTY abstraction (winpty on Windows, stdlib `pty` on Unix). Everything else is platform-agnostic.
- Windows is fully tested; macOS/Linux paths are implemented and validated by the installer's `--doctor` self-check on the target machine.

### 🚀 One-click installer
- **`install.bat`** (Windows), **`install.command`** (macOS), **`install.sh`** (Linux) → run `install.py`: installs dependencies, asks backend + bot token + your Telegram ID + autostart, reminds you to log into your CLI, writes `.env`, sets up per-OS auto-start, and starts the bot.
- **`python install.py --doctor`** checks Python, deps, your CLI + login, token, and ID.
- Cross-platform background supervisor (`run_background.py`) + `start.sh`/`stop.sh` for Unix.

### 📖 Docs
- Full **README** rewrite: beginner-proof, OS-by-OS, both backends, switching, sharing to a friend's PC, troubleshooting, security.

### Notes
- `pywinpty` is now Windows-only in `requirements.txt` (Unix uses the stdlib `pty`).
- v3.0.0 is tagged as the pre-refactor restore point.

---

## ⏰ v2.6.0 — Schedules, model-limit recovery, faster & leaner

### ⏰ Scheduling engine (the big one)
- Register unlimited recurring jobs that run automatically and DM you the result. Kinds: **once**, **every N minutes/hours**, **daily at HH:MM**, **weekly on chosen days**.
- **Catch-up:** if the bot/PC was off when a job was due, it runs the missed job once on startup and sends it to you immediately. Toggle this in **Settings → "Catch up missed schedules"** (default ON).
- Create them two ways: the **`/schedule`** command (e.g. `/schedule daily 09:00 summarise my inbox`, `/schedule every 5h check the news`, `/schedule mon,wed,fri 09:00 stand-up`) **or just say it** ("every day at 9am summarise my inbox") — the bot shows a confirm card before creating.
- Manage from the panel: pause/resume, ▶️ run-now, 🗑 delete. Schedules persist to disk and survive restarts.
- Runs go through the same per-user lock as chat, so a scheduled job never collides with a manual message.

### 🚦 Model-limit recovery
- When a model is rate-limited/quota-blocked (Claude can be down for hours), the bot detects it, tells you **which model is blocked**, and shows **model buttons right there** so you switch on the spot and resend. Works for chat and scheduled runs.

### ⚡ Faster & leaner
- **Fixed the real lag:** WebBridge/`/browse` used blocking network calls that **froze the whole bot** (typing, menus) for up to 30s. They now run off the event loop, so menus and the typing indicator stay instant.
- `get_model()` is cached (mtime-gated) so it no longer reads disk on every call.
- Removed dead code (unused `set_role`, a dead callback branch).

---

## 🔐 v2.5.0 — Trust-based roles, owner-gated model, slash-command menu, Inbox redesign

### 🧱 Why the security model changed
I tested it directly: **agy executes tools — file writes, shell commands — in headless mode no matter what** (`--dangerously-skip-permissions`, the `toolPermission` setting, and `--sandbox` all fail to block it). There's no way to safely sandbox an untrusted user inside agy. So the bot now uses a **trust-based model** instead of pretending to restrict.

### 👤 Two roles only: owner + admin
- The untrusted **"user" tier is gone**. Everyone the owner adds is an **admin** with full access (chat, sessions, media, files, agy). Old `"user"` entries auto-migrate to admin.
- **Only the owner manages people** (adds/removes admins). Adding someone is now one step — ID → name → done (no role prompt).
- A fix surfaced by tests: capability checks now correctly deny anyone who isn't the owner or a stored admin.

### 🎚️ Owner controls whether admins can change the model
- New **owner-only** Settings toggle: **"Admins can change model: ON/OFF"** (default ON). When OFF, admins lose the 🤖 Model button and `/model` replies that it's owner-disabled. The owner can always change it.

### ⌨️ Native slash-command menu
- Typing **`/`** now shows the full command list with descriptions (no more guessing). The owner additionally sees `adduser` / `removeuser` / `listusers`.

### 🤖 Honest model list
- Clarified on the model screen: the buttons are common **Gemini** options; agy fetches its **full model list from your Antigravity account**, so use **✏️ Custom** to enter any exact name from agy's own "Switch Model" screen (including a Claude model, if your account has it). We don't show fake buttons that would silently fall back.

### 📥 Inbox redesign
- Inbox no longer dumps a wall of text. It shows **category buttons** — 📷 Images, 🎵 Audio, 🎬 Video, 📄 Documents (video split out by extension) — each opening a **paginated list (10 per page, "More ➡️")**. Every file has a **📤 send button** that delivers it straight to your Telegram chat.

---

## 🤖 v2.4.0 — Model switching that's actually real + session delete

### 🐛 The model switcher was lying
- **Changing the model never did anything.** The bot stored your pick in its *own* `settings.json` and exported three guessed environment variables (`ANTIGRAVITY_MODEL`, `GEMINI_API_MODEL`, `MODEL`) — **agy reads none of them**, and has no `--model` flag at all. So every "✅ Model changed" was false.
- **Now it writes the file agy actually reads:** `~/.gemini/antigravity-cli/settings.json` → `"model"`, using agy's own display format (`Gemini 3.1 Pro (High)`), preserving all other keys.
- **No more guessing — it reads back the truth.** After you pick a model the bot re-reads agy's file and shows the value that's *actually on disk*, so the confirmation can't lie.
- **Real model list** (built from agy's own internal model keys) with **Low / Medium / High** thinking levels: Gemini 3.1 Pro, 3 Flash, 2.5 Pro, 2.5 Flash, 3.1 Flash Lite.
- **✏️ Custom…** option — paste the exact string from agy's own "Switch Model" screen if you want something not listed.

### 🗑️ Session delete button
- Every session in the list now has a **🗑 delete** button (with a confirm step). Previously the delete code existed but **no button ever triggered it** — you could only `/end` the active one.
- Switching, creating and deleting now refresh the list in place.

### ✅ Tested
- New `test_fixes.py` — 25 deterministic checks: model round-trips to agy's real file, other keys preserved, fallback on missing file, catalog format, and full session CRUD (create-is-fresh, switch isolation, per-user isolation, delete, disk persistence).
- Live end-to-end run confirmed: setting a model writes agy's file, agy loads it, a real turn runs and replies.

---

## 🛡️ v2.3.0 — No-admin background runner

- **New background method that needs no admin rights and no `.vbs`** (the old `.vbs` launchers failed on locked-down/corporate PCs).
- A tiny hidden Python **supervisor** (`run_background.pyw`, launched via `pythonw` = no window) runs the bot and **restarts it within ~10s** if it crashes.
- **`START_BACKGROUND.bat`** turns it on (hidden, self-healing, auto-start at login via a Startup-folder shortcut). **`STOP_BACKGROUND.bat`** turns it off.
- Removed the old `.vbs` launchers and the admin-only Task Scheduler installer.

---

## ⚡ v2.2.0 — Fast, multi-user, self-healing

The "make it actually fast and shareable" release.

### 🚀 Speed
- **Switched the default model to `gemini-2.5-flash`** — dramatically faster replies. (Change anytime in Telegram with `/model`.)
- **Trimmed the AI instruction prompt** from ~5 KB of "always use every tool / always make rich PDFs / try 3 approaches" down to a lean prompt, so simple questions get simple, quick answers instead of over-working.

### 🩹 No more 10-minute hangs
- **Smarter idle-reaper:** a stuck AI that just blinks a spinner used to look "busy" forever. Now only *real* progress (actual text or a new transcript step) counts as activity, so hung tasks are stopped in **~3 minutes** instead of 10–60.
- **New-conversation detection mid-run**, so a brand-new chat that stalls still gets caught.
- Idle timeout default lowered 10 min → **3 min**; Settings options are now **2 / 3 / 5 min**.

### 👥 Real multi-user
- **Concurrent processing** — multiple people (and the owner) are now served **at the same time**. Previously one person's long task blocked everyone else in a queue.

### 🪲 Bulletproof persistence
- The hidden launcher restarts the bot in **~7 seconds** on any crash.
- `install_startup.bat` sets up a **Task Scheduler watchdog** that restarts the launcher itself and starts Zilla at every login.
- You get a *"⚡ Zilla is online"* DM on every (re)start, so you always know it's alive.

---

## 🧹 v2.1.x — Cleanup & sharing

- **v2.1.1** — Fixed multi-user: enabled concurrent update processing.
- **v2.1.0** — Removed bloat (dead `selected_model.txt`, a duplicate launcher), untracked runtime state, hardened the restart loop, and added the watchdog installer.

---

## 📦 v2.0.0 — Portable & documented

- Added **`.env.example`** so the project is clone-and-go on any machine (paths auto-detect from the home folder).
- Added a full beginner **[MANUAL.md](MANUAL.md)** — install, setup, everyday use, connecting a different AI CLI, sharing, troubleshooting.
- Rewrote the **README** in plain, friendly language.
- Removed hardcoded user paths from the AI instructions.

---

## 🌱 v1.0.0 — First stable release

- Telegram ↔ AI CLI "thin pipe" bot: text, voice, photos, documents.
- Three-tier permissions (User / Admin / Owner) managed from inside Telegram.
- Per-user sessions, inline control panel, automatic file delivery, browser control via WebBridge.

---

> 📖 New here? Start with the **[README](README.md)**, then the **[MANUAL](MANUAL.md)**.
