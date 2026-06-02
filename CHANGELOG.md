# 📜 Zilla — Changelog

All notable changes, newest first. Versions are git tags (e.g. `v2.2.0`).

---

## 🤖 v2.4.0 — Model switching that's actually real + session delete *(latest)*

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
