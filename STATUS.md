# Zilla — Status & Future Plan
_Last updated: 2026-06-07 (session 2)_

A quick, honest snapshot: what works now, and what's next.

---

## ✅ What's DONE & verified

**Core (earlier sessions):** two-layer harness (`harness.py` + `autoharness.py`),
anti-hallucination gate (`verify.py`), self-healing scheduler, Health/Schedules menus,
structured trust log. Bot live as **@Mangomangos_bot ("Mango")**.

**This session:**
- **agy is now fully usable through Telegram** (was not before).
  - `/model` shows the **real, live** model list pulled straight from your Antigravity
    account (`agy models`) — the 8 actual models. Killed the old fake/invented list.
  - Switching models uses agy's real `--model` flag; switching backend works in `/settings`.
  - Honest status panel (logged-in state, current model).
  - Verified end-to-end: simple reply, an agentic grounded task (counted files = 18, correct),
    model + backend switching.
- **Embedded browser (Claude backend) fixed.** It was silently broken — falling back to a
  basic web-reader ~⅓ of the time. Now pinned + reliable, and only loads for web tasks
  (keeps simple replies fast).
- **Tests: 160 passing, 0 failing.** Git tree clean.

**Current live config:** backend = **agy**, model = **Gemini 3.1 Pro (High)**,
Claude account = lethercook9@gmail.com (Pro). Switch backend/model anytime in `/settings`.

---

## 🔎 agy speed — root cause found (decision: accept ~13s for now)

agy takes ~13s per message **not because of Telegram** — every one-shot `agy` call re-does a
**~5s cloud login handshake with Google** before answering (proven from agy's own logs).
Claude has no such per-call handshake, which is why it feels instant.

Your terminal feels fast because interactive mode does that handshake **once** and stays warm.
But agy's warm mode is a **full-screen TUI with no scriptable/headless interface** — it can't
be reliably driven by the bot. So a "warm" system isn't viable without fragile,
non-surgical hacks.

**Decision:** accept ~13s for now (your remote setup has no Claude, so routing fast messages
to Claude wouldn't help there). The bot already shows progress during the wait.

---

## 🗺️ Future plan (in priority order)

1. **agy's own browser.** agy can't browse yet (only the Claude backend can). Give agy its own
   browser via its MCP config, or check for native agy browser tools. _(your next priority)_

2. **Kimi WebBridge.** Make "use my live Brave logins" work — so the bot can act *as you* with
   your existing sessions. Brave is installed but the bridge on `127.0.0.1:10086` didn't wake;
   needs investigation (wrong profile? extension disabled? missing companion host?).

3. **Speed masking (cheap polish).** Instant "on it…" + live progress so the 13s *feels* shorter.
   Optional revisit of real warmth only if agy ships a headless/daemon mode.

4. **Capabilities backlog:**
   - Microsoft **markitdown** skill (PDF/anything → Markdown for fast doc reading).
   - **Skill auto-discovery + install** (search GitHub → ask you → download).
   - **App pipeline** as a general capability (build → test → iterate → drop APK in Telegram;
     full-stack via Google Sheets backend; "instant app").
   - agy **logout → auto re-auth relay** built into the bot (prototyped already).

---

## 🔧 How to operate

```bash
cd ~/Documents/zilla
# Restart the bot:
{ [ -f zilla.pid ] && kill "$(cat zilla.pid)" 2>/dev/null; }; pkill -9 -f "Documents/zilla.*bot.py"; sleep 2
./.venv/bin/python bot.py        # run in background
# Run tests (expect 160 passed):
./.venv/bin/python test_fixes.py 2>&1 | tail -2
```

- Always use `./.venv/bin/python` (the venv), not bare `python3`.
- `.env` holds the Telegram token (git-ignored). Backend/model live in bot settings; change via menus.
- Nothing committed yet (on `main`). Don't push without deciding to; branch first.
