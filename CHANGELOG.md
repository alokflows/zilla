# 📜 Zilla — Changelog

All notable changes, newest first. Versions are git tags (e.g. `v2.2.0`).

---

## ⚡ v2.2.0 — Fast, multi-user, self-healing *(latest)*

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
