# AGY Telegram Bot — Master Task Brief
## Full Requirements for Bug Fixes, Security, GUI & Features
### Created: 2026-05-29 | Session Continuity Document

---

> **Purpose**: If the AI coding session hits a message limit, copy this entire file and paste it into a new session to continue exactly where you left off. It contains the full scope, priorities, and technical context.

---

## 🔴 1. CRITICAL PRIORITY: Security, Data Leaks & History Management

### 1.1 — Unauthorized Access (CRITICAL)
- **Bug**: The bot currently responds to unauthorized users and new accounts.
- **Reproduction**: Access was granted to a user, then removed, but the user could still interact with the bot.
- **Impact**: The bot has full system access — this is extremely hazardous.
- **Fix Required**:
  - Implement strict token and access verification **before** processing any message.
  - If a user is unauthorized, the bot must **completely ignore them** (dead silence / no response).
  - Authorization checks must be **real-time** (no caching of removed users).

### 1.2 — Data Leakage Bug (SEVERE PRIVACY THREAT)
- **Bug**: When an unauthorized person messages the bot, it **dumps the entire conversation history**, active sessions, and **automatically sends all previously generated PDF files** to that unauthorized user.
- **Crucial Example**: From the Owner account, an ID card photo was sent. The bot made the PDF but **failed to send it back to the Owner**. When immediately tested from an unauthorized account, the bot sent **everything** — including the highly sensitive ID card PDF — to the random unauthorized user.
- **Impact**: This is a terrifying security risk. Sensitive personal documents are being leaked to strangers.
- **Fix Required**:
  - History and files must **never** be sent to unauthorized users.
  - File delivery must be scoped to the **originating user's session only**.
  - All file-sending code paths must have authorization gates.

### 1.3 — Random Full History Dumps (SESSION MANAGEMENT FLAW)
- **Bug**: Even during a normal, authorized chat, the bot inexplicably glitches and dumps the entire conversation history (from session start) and **all files** directly into the active chat.
- **Impact**: Destroys chat usability and exposes all historical context inline.
- **Fix Required**:
  - Session and chat history management must be **rewritten to operate securely**.
  - The bot should securely reference past context internally but **never output raw history** or dump files into the chat view.
  - History must be used as AI context, not as chat output.

---

## 🟡 2. Graphical User Interface (GUI) & Startup Options

### Four Application States Required:

| Option | Name | Description |
|--------|------|-------------|
| 1 | **Auto-Start & Trigger** | App automatically launches on system boot (like standard Windows apps) and immediately triggers the bot to start working. |
| 2 | **Normal GUI** | Standard visibility and operation. |
| 3 | **Hide to System Tray** | A hide button that removes the app from the screen/taskbar and keeps it running in the system tray. |
| 4 | **Stealth/Disappear Mode** | The app completely disappears from view. A specific hidden method (e.g., a keyboard shortcut or command) is needed to reopen it. |

---

## 🟠 3. Chat Interface & Session Management

### 3.1 — Manual Refresh Button
- Add a manual refresh button to the chat UI.

### 3.2 — Refresh Transparency
- Explicitly display the automatic refresh interval time.
- **Current Bug**: The chat stops showing new messages after a few messages or when media is sent.

### 3.3 — Media Support
- Update the chat interface so that sent media (images/files) is **fully visible** inside the chat window (rendered inline, not just as filenames).

### 3.4 — Strict Session Separation
- **Current Bug**: Desktop chats and Telegram ("Owner") chats are mixing together.
- **Fix**: Desktop chats show **only** in the Desktop session view. Telegram chats show **only** in the Telegram session view. Complete separation.

### 3.5 — Active Session UI
- The app must display active user flows, explicitly showing:
  - Active sessions
  - Which user is tied to which session
  - Session source (Desktop vs Telegram)

---

## 🟢 4. PDF Generation & Delivery Fixes

### 4.1 — Persistent/Reliable Sending
- **Bug**: The bot gathered data (e.g., 5 richest people) and sent beautiful PDFs successfully. However, when an ID card image was sent, the bot described it perfectly and saved the PDF, but **failed to send it back**, asking a follow-up question instead.
- **Fix**: The "send" command must be a **recurring, reliable process** — it should automatically send the generated file to the authorized user every time **without asking follow-up questions**.

### 4.2 — PDF Formatting Skills
- **Bug**: The bot is messing up document formatting — words are scattered everywhere.
- **Fix**: Explicitly inject "Doc formatting skills" into the bot's logic so it knows exactly how to structure and align text inside the PDF.

---

## 📋 Implementation Priority Order

1. **🔴 Security & Data Leak Fixes** (Items 1.1, 1.2, 1.3) — Immediate
2. **🟢 PDF Delivery & Formatting** (Items 4.1, 4.2) — High
3. **🟠 Chat Interface & Sessions** (Items 3.1–3.5) — Medium-High
4. **🟡 GUI States & Startup** (Items 2.1–2.4) — Medium

---

## 🗂️ Project Technical Context

- **Project Path**: `C:\Users\Isha\agy-telegram-bot-dev`
- **Main Files**:
  - `bot.py` — Core Telegram bot logic (57KB)
  - `gui_app.py` — GUI application (130KB)
  - `user_manager.py` — User authorization
  - `sessions.py` — Session management
  - `file_handler.py` — File/PDF handling
  - `config.py` — Configuration
  - `chat_bus.py` — Chat routing
  - `agent_manager.py` — AI agent logic
  - `settings_manager.py` — Settings
  - `telegram_formatter.py` — Telegram message formatting
  - `desktop_formatter.py` — Desktop message formatting
  - `ui_buttons.py` — UI controls

- **Key Config Files**:
  - `.env` — Environment variables (bot token, owner ID)
  - `authorized_users.json` — Authorized user list
  - `sessions.json` — Active sessions
  - `settings.json` — App settings
  - `bot_instructions.md` — Bot system prompt

---

## 📸 Screenshots Reference

Three screenshots were provided showing:
1. The bot successfully generating 5 richest people PDFs and sending them via Telegram
2. The bot analyzing a volunteer ID card image and describing it in detail
3. The bot generating the volunteer badge PDF but only saving it locally (not sending)

---

## 🔄 Session Continuity Instructions

If starting a new AI session:
1. Paste this entire document as context
2. Reference the project path: `C:\Users\Isha\agy-telegram-bot-dev`
3. Check `documents/IMPLEMENTATION_PLAN.md` for the detailed technical plan (if created)
4. Check git log for any changes already made
5. Continue from the next uncompleted priority item
