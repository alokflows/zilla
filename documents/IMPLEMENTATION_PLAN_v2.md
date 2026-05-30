# 🛡️ ZILLA — Implementation Plan v2 (Security + GUI + Chat + PDF)
**Created:** 2026-05-30
**Status:** PLAN MODE — Validated, not yet coding

---

## 📊 EXECUTIVE SUMMARY

4 workstreams, 14 tasks, estimated across 3 priority tiers.
Each task includes: root cause analysis, exact files to change, and implementation approach.

| Priority | Workstream | Tasks | Risk |
|----------|-----------|-------|------|
| 🔴 P0 | Security & Data Leak Fixes | 5 | **CRITICAL** — full system access exposed |
| 🟡 P1 | GUI Startup / Tray / Stealth | 4 | Medium — UX feature |
| 🟡 P1 | Chat Interface & Sessions | 4 | Medium — UX/data integrity |
| 🟢 P2 | PDF Generation & Delivery | 3 | Low — reliability |

---

## 🔴 PHASE 1: SECURITY & DATA LEAKS (P0 — Do First)

### Task 1.1 — Fix Authorization Bypass After Revocation
**Bug:** User was removed from `authorized_users.json` but could still interact.
**Root Cause:** `is_authorized()` in `bot.py:221-231` calls `auth_manager._load_fresh()` which re-reads from disk — this SHOULD work. However, the `_load_fresh()` method (user_manager.py:58-61) is correct. The real issue is likely:
1. **In-memory caching in the Telegram library's `CallbackQueryHandler`** — callback queries from previously authorized users may still pass through because the `handle_callback()` function at line 1034 uses `is_authorized(update)` but the callback query's `effective_user` might differ from `effective_chat`.
2. **Race condition:** The JSON file is being read but the user was removed after the bot's last check. With `_load_fresh()` reading on every call, this shouldn't happen... unless the file wasn't properly saved.

**Fix Plan:**
- [ ] Add `_load_fresh()` call at the TOP of `is_authorized()` — confirmed it's already there (line 226). Verify it works by adding explicit logging.
- [ ] Add a **hardcoded early-exit guard** at the very beginning of every handler group that checks `user_id` against a deny-list in memory (belt and suspenders).
- [ ] Add `is_authorized()` check to the Telegram library's `pre_process` or use a **global filter/middleware** on the `Application` so no handler runs without auth.
- [ ] Add an `unauthorized_users` deny-list that persists revoked IDs. Even if `authorized_users.json` is empty, anyone who was previously removed stays blocked until manually cleared.
- [ ] Write a test: add user → verify authorized → remove user → verify immediately blocked.

**Files:** `bot.py`, `user_manager.py`

---

### Task 1.2 — Fix Data Leak to Unauthorized Users (CRITICAL)
**Bug:** Unauthorized user receives full conversation history, active sessions, and PDF files.
**Root Cause (Confirmed in code):**

The issue is a **perfect storm of 3 bugs**:

1. **`handle_message()` (bot.py:927-1022)** — The auth check at line 935 returns early, which is correct. BUT the `detect_file_paths()` function at line 1019 scans the `response` text for file paths and then `safe_send_file()` sends them. If the unauthorized user triggers any code path that doesn't hit the auth check first, files get sent.

2. **`agy_runner.py:654-670`** — When getting the "real response" from the transcript, `get_new_responses()` reads from the transcript of whatever `conversation_id` is stored in the session. If sessions are shared across users (see Task 1.3), an unauthorized user could trigger a response that includes another user's transcript content.

3. **The auto-file-send pipeline**: `detect_file_paths()` (bot.py:249-293) extracts ALL file paths from the response text → `safe_send_file()` (bot.py:175-218) sends them to whatever `chat_id` called the handler. If the history dump happens, ALL previously created files get sent.

**Fix Plan:**
- [ ] Add a **universal auth middleware** that runs BEFORE any handler. Use `Application.add_handler()` with a `MessageHandler` at group `-1` (highest priority) that blocks unauthorized users silently.
- [ ] Make `safe_send_file()` take a `user_id` parameter and verify authorization before sending.
- [ ] Add a `MAX_FILES_PER_RESPONSE = 3` hard cap in the file-sending loop (bot.py:1021).
- [ ] Add an `authorized_chat_ids` check inside `safe_send_file()` itself as a defense-in-depth measure.

**Files:** `bot.py`

---

### Task 1.3 — Fix Session Bleed Between Users
**Bug:** Desktop and Telegram sessions are mixing.
**Root Cause:** Sessions are keyed by `user_id` in `sessions.py`, but the desktop GUI uses `user_id=0` while Telegram uses the actual Telegram user ID. This is correct in theory. However:
1. In `gui_app.py`, the desktop chat sends messages through the same `agy_runner.py` pipeline, possibly sharing the same `conversation_id` as a Telegram session if the session naming collides.
2. The `SessionManager.get_conversation_id()` (sessions.py:116-122) checks `user_id` ownership, which should prevent cross-user session access. But if desktop (`user_id=0`) and Telegram (`user_id=OWNER_ID`) both access a session named "main", the desktop one owns it (user_id=0) and Telegram can't access it — so it creates a NEW session, but the name might collide.

**Fix Plan:**
- [ ] Enforce session name prefixing: `tg_{user_id}_{name}` for Telegram sessions, `desktop_{name}` for desktop sessions. This guarantees zero naming collisions.
- [ ] Add `source` field to sessions ("telegram" vs "desktop") and filter on it in list/get operations.
- [ ] Verify `gui_app.py`'s chat pipeline uses `user_id=0` consistently and never accidentally uses the owner's Telegram ID.

**Files:** `sessions.py`, `bot.py`, `gui_app.py`

---

### Task 1.4 — Fix Random History Dumps in Normal Chat
**Bug:** Even during authorized normal use, asking a question sometimes dumps the entire conversation history.
**Root Cause (Confirmed):**

The transcript-based response extraction in `agy_runner.py:161-214` (`get_new_responses()`) reads from `starting_step` onward. If `starting_step` is wrong (e.g., 0 or stale), it reads ALL responses from the entire conversation.

The `starting_step` is set by `sessions.get_last_seen_step()` (sessions.py:139-145). The flow is:
1. `handle_message()` at line 965: `starting_step = get_latest_step(conv_id) if conv_id else 0`
2. After agy finishes, line 990: `sessions.set_last_seen_step(new_step, user_id=user_id)`

The bug: if `get_latest_step()` returns 0 (e.g., transcript file doesn't exist yet for a new conversation), then `get_new_responses()` reads ALL steps. The safety check at line 197 logs a warning and takes only the last response, but by then the damage is done — the raw output from the PTY (line 644: `response = clean_response(raw_output)`) may contain the full dump before it gets overwritten by the transcript response.

**But wait** — line 654-670 shows that the transcript response REPLACES the raw output. So the dump would only happen if:
1. `detected_id` is None (transcript not found), OR
2. `get_new_responses()` returns empty string, so the raw PTY output is used

In case (1), the raw PTY output IS the full dump because agy replays the whole conversation context.

**Fix Plan:**
- [ ] In `run_agy_pty()`, if this is a CONTINUING conversation (`conversation_id` is not None), always wait for and use the transcript response. Never fall back to raw PTY output for continuing conversations.
- [ ] Add a `MAX_RAW_OUTPUT_LEN = 5000` cap on the PTY output to prevent massive dumps.
- [ ] If `get_new_responses()` returns empty for a continuing conversation, return a generic error message like "Response processing failed. Please try again." instead of dumping raw output.
- [ ] Add response content scanning: if the response contains more than 5 file paths, or more than 3000 chars of what looks like conversation history (repeated "User:" / "Assistant:" patterns), SUPPRESS it and return a sanitized version.

**Files:** `agy_runner.py`, `bot.py`

---

### Task 1.5 — Add Global Auth Middleware (Defense in Depth)
**Purpose:** Even if individual handler checks fail, no unauthorized message ever reaches any handler.

**Fix Plan:**
- [ ] Create a `pre_handler` function registered at handler group `-1` that:
  1. Checks `update.effective_user.id` against auth
  2. If unauthorized: logs the attempt, does NOT respond, raises `ApplicationHandlerStop` to prevent further handler processing
- [ ] Move `auth_manager._load_fresh()` into this single middleware instead of calling it in every handler
- [ ] Add rate limiting: if the same unauthorized user sends > 5 messages in 60 seconds, temporarily add them to an in-memory deny list

**Files:** `bot.py`

---

## 🟡 PHASE 2: GUI STARTUP / TRAY / STEALTH (P1)

### Task 2.1 — Auto-Start on Windows Boot
**Current state:** `install_startup.bat` exists but isn't integrated into GUI.

**Plan:**
- [ ] Add a settings toggle in `gui_app.py` Settings page: "Launch on Windows startup"
- [ ] When enabled: create a shortcut in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` pointing to `run_bot_hidden.vbs` (or a new `zilla_autostart.vbs`)
- [ ] When disabled: remove the shortcut
- [ ] On startup, auto-trigger bot start (skip the manual "Start" button press)

**Files:** `gui_app.py`, `settings_manager.py`, new `startup_manager.py`

---

### Task 2.2 — System Tray Integration
**Plan:**
- [ ] Use `pystray` library (lightweight, Windows-native) to create a system tray icon
- [ ] Tray icon shows: Zilla logo/emoji, tooltip with status ("Online" / "Offline")
- [ ] Tray right-click menu: "Show Window", "Hide Window", "Restart Bot", "Stop Bot", "Exit"
- [ ] "Hide" button on the GUI title bar minimizes to tray (removes from taskbar)
- [ ] Double-click tray icon restores window
- [ ] When minimized to tray, bot continues running normally

**Files:** `gui_app.py`, new `tray_manager.py`, `requirements.txt` (add `pystray`, `Pillow`)

---

### Task 2.3 — Stealth/Disappear Mode
**Plan:**
- [ ] In stealth mode: window is withdrawn (not just minimized), AND tray icon is hidden
- [ ] Process still runs, bot still works
- [ ] Re-open method: A global hotkey (e.g., `Ctrl+Alt+Z`) registered via `pynput` or `keyboard` library
- [ ] Alternative: A named pipe or local socket that a small "revealer" script can signal
- [ ] Settings option: "Stealth Mode Hotkey" — customizable key combination
- [ ] When hotkey pressed: window re-appears, tray icon re-appears

**Files:** `gui_app.py`, `tray_manager.py`, `requirements.txt` (add `keyboard`)

---

### Task 2.4 — Normal GUI Mode (Already Exists — Polish)
**Plan:**
- [ ] Verify the current `gui_app.py` works correctly in normal windowed mode
- [ ] Ensure all 4 modes are selectable from Settings
- [ ] Add a mode indicator in the status bar: "Mode: Normal / Tray / Stealth"
- [ ] Ensure window state persists across restarts (remember last mode)

**Files:** `gui_app.py`, `settings_manager.py`

---

## 🟡 PHASE 3: CHAT INTERFACE & SESSION MANAGEMENT (P1)

### Task 3.1 — Manual Refresh Button + Refresh Interval Display
**Current state:** GUI polls `chat_bus` on a timer, but interval is not shown. Stops after a few messages.

**Plan:**
- [ ] Add a 🔄 refresh button in the chat header bar
- [ ] Display the auto-refresh interval prominently: "Auto-refresh: every 2s" (or whatever the interval is)
- [ ] Debug why chat stops updating after a few messages — likely the polling `after()` timer gets cancelled or the chat bus `deque` overflows
- [ ] Increase `max_messages` in `ChatBus` from 500 to 2000
- [ ] Ensure the refresh timer restarts after errors

**Files:** `gui_app.py`, `chat_bus.py`

---

### Task 3.2 — Media Visibility in Chat Window
**Current state:** Sent media (images/files) appear as text-only messages in the GUI chat.

**Plan:**
- [ ] Add `file_path` and `media_type` fields to `ChatMessage` dataclass
- [ ] When a photo is sent via Telegram, capture the local file path in the chat bus message
- [ ] In the GUI chat renderer: if `media_type == "image"`, render a thumbnail using `CTkImage` (PIL)
- [ ] For files: render a clickable file card with icon, name, and size
- [ ] For PDFs specifically: show a PDF icon + file name + "Open" button

**Files:** `chat_bus.py`, `gui_app.py`, `bot.py` (to post media events to chat bus)

---

### Task 3.3 — Strict Session Separation (Desktop vs Telegram)
**Current state:** Both channels may show interleaved messages in the GUI.

**Plan:**
- [ ] Add source-based filtering in the GUI chat view: "Desktop" tab and "Telegram" tab
- [ ] Add tab buttons at the top of the chat area: `[Desktop] [Telegram] [All]`
- [ ] Filter `chat_bus.get_all()` by `source` field
- [ ] Sessions list in sidebar should also show source indicator: 🖥️ for Desktop, 📱 for Telegram
- [ ] Never mix user_id=0 (desktop) messages with Telegram user messages in the same view

**Files:** `gui_app.py`, `chat_bus.py`

---

### Task 3.4 — Active Sessions Dashboard
**Plan:**
- [ ] Add a live "Active Sessions" panel in the Dashboard view
- [ ] Show: session name, user (Desktop/Telegram username), message count, last active time, status (active/idle)
- [ ] Highlight the currently active session with accent color
- [ ] Click a session to switch to it and view its chat history
- [ ] Refresh this panel every 5 seconds

**Files:** `gui_app.py`

---

## 🟢 PHASE 4: PDF GENERATION & DELIVERY (P2)

### Task 4.1 — Persistent Auto-Send After File Creation
**Bug:** Bot creates PDF, describes it, but asks a follow-up question instead of sending.
**Root Cause:** The AI (agy) sometimes generates a response that describes the file but doesn't include the file path in the format that `detect_file_paths()` (bot.py:249) can detect. Or it asks "Would you like me to send it?" instead of just including the path.

**Fix Plan:**
- [ ] Enhance `bot_instructions.md` with even stronger file delivery instructions: "ALWAYS state the file path. NEVER ask before sending. ALWAYS send first."
- [ ] In `agy_runner.py:_extract_file_paths_from_transcript()`, already extracts file paths from tool calls — ensure this works for PDFs created via `write_to_file` or `run_command` tools.
- [ ] Add a post-response hook in `handle_message()`: after getting the response, ALSO scan the transcript tool calls for file paths and send them, even if they're not in the text response.
- [ ] Add a **dedicated file extraction step**: after agy finishes, always call `_extract_file_paths_from_transcript()` and send any new files, regardless of whether the response mentions them.

**Files:** `bot.py`, `agy_runner.py`, `bot_instructions.md`

---

### Task 4.2 — PDF Formatting Skills Injection
**Bug:** PDFs have scattered/misaligned text.
**Root Cause:** agy uses whatever formatting it decides on. No explicit "document formatting skill" is loaded.

**Fix Plan:**
- [ ] Create a new skill at `C:\Users\Isha\.gemini\antigravity-cli\skills\pdf-formatting\SKILL.md` with detailed PDF formatting rules (margins, fonts, spacing, alignment, tables, images).
- [ ] Alternatively/additionally, enhance `bot_instructions.md` "Document Formatting Rules" section with more specific instructions (this is already partially there at lines 27-38, but needs strengthening).
- [ ] Add specific instructions about using `reportlab` properly: canvas coordinates, paragraph styles, table formatting.
- [ ] Add example templates in the skill for common document types: reports, summaries, image-based documents.

**Files:** `bot_instructions.md`, new skill folder at `skills/pdf-formatting/`

---

### Task 4.3 — File Send Queue with Retry
**Plan:**
- [ ] Create a `send_queue` in `bot.py` — a list of `(chat_id, file_path, retry_count)` tuples
- [ ] After each agy response, enqueue all detected files
- [ ] Process the queue with retry logic (max 3 attempts, exponential backoff)
- [ ] If a file fails to send (network error, file locked), retry after 5s/15s/30s
- [ ] Log all send attempts and failures

**Files:** `bot.py`, possibly new `file_send_queue.py`

---

## 📋 IMPLEMENTATION ORDER

```
Phase 1 (Security) — Must be first, everything else is secondary
  1.5 → 1.1 → 1.2 → 1.4 → 1.3

Phase 2 (GUI) — After security is locked down
  2.1 → 2.2 → 2.3 → 2.4

Phase 3 (Chat) — Can partially overlap with Phase 2
  3.1 → 3.2 → 3.3 → 3.4

Phase 4 (PDF) — Last, least critical
  4.1 → 4.2 → 4.3
```

---

## ⚠️ RISKS & OPEN QUESTIONS

1. **`pystray` on Windows:** Needs `Pillow` for icon rendering — adding dependencies. Will verify compatibility with CustomTkinter's event loop.
2. **Stealth mode hotkey:** The `keyboard` library requires admin privileges on some Windows configs. May need a fallback (e.g., local named pipe + tiny CLI tool).
3. **Session name migration:** Changing session naming from "main" to "desktop_main" / "tg_12345_main" requires a one-time migration of existing sessions.json. Will add migration logic similar to the v7→v8 migration already in `sessions.py`.
4. **GUI thread safety:** The chat bus is thread-safe, but media rendering (loading images) in the GUI must happen on the GUI thread. Need to be careful with `after()` scheduling.

---

## 🎯 SUCCESS CRITERIA

- [ ] Unauthorized user sends message → absolute silence, zero data exposed
- [ ] User revoked → immediately blocked on next message
- [ ] Normal chat never dumps history or random files
- [ ] GUI starts on boot, runs in tray, has stealth mode with hotkey
- [ ] Desktop and Telegram chats fully separated in GUI
- [ ] PDFs always auto-sent, properly formatted
- [ ] Refresh button works, interval displayed
- [ ] Media visible in chat window
