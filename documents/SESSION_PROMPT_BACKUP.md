# 🔄 SESSION PROMPT BACKUP — Copy This Into a New Session
**Created:** 2026-05-30
**Purpose:** If the session hits a message limit, copy everything below into a new chat.

---

## CONTEXT
- **Project:** Zilla AI Agent Engine (Telegram Bot + Desktop GUI)
- **Path:** `C:\Users\Isha\agy-telegram-bot-dev`
- **Implementation Plan:** `C:\Users\Isha\agy-telegram-bot-dev\documents\IMPLEMENTATION_PLAN_v2.md`
- **Progress Tracker:** `C:\Users\Isha\agy-telegram-bot-dev\ZILLA_PROGRESS.md`

---

## FULL REQUEST (Verbatim)

### 1. CRITICAL PRIORITY: Security, Data Leaks & History Management

**Unauthorized Access:** The bot is currently responding to unauthorized users and new accounts. I granted someone access, removed it, and they were still able to interact with the bot. Given the bot has full system access, this is extremely hazardous.

**Data Leakage Bug (Severe Privacy Threat):** When an unauthorized person messages the bot, it dumps the entire conversation history, active sessions, and automatically sends them all previously generated PDF files.

**Crucial Example:** From my Owner account, I sent an ID card photo, and the bot made the PDF but failed to send it back to me. However, when I immediately tested the bot using an unauthorized account, the bot spit out everything to that random account, including the highly sensitive ID card PDF. This is a terrifying security risk and must be fixed instantly.

**Random Full History Dumps (Session Management Flaw):** I am shocked at how the bot handles chat history. Even in the middle of a normal, authorized chat, if I ask something, the bot inexplicably glitches and dumps the entire conversation history (from the start of the session) and all files directly into the active chat. Session and chat history management must be rewritten to operate securely. It should never blindly spit out the whole history or file directory like this.

**Fix Required:**
- Implement strict token and access verification before processing any message.
- If a user is unauthorized, the bot must completely ignore them (dead silence/no response).
- Fix the history/session management logic so it securely references past context without ever outputting the raw history or dumping files into the chat view.

### 2. Graphical User Interface (GUI) & Startup Options

I need a GUI built with four specific application states/options:

- **Option 1: Auto-Start & Trigger:** The app should automatically launch on system boot (like standard Windows apps) and immediately trigger the bot to start working.
- **Option 2: Normal GUI:** Standard visibility and operation.
- **Option 3: Hide to System Tray:** A hide button that removes the app from the screen/taskbar and keeps it running in the system tray.
- **Option 4: Stealth/Disappear Mode:** The app completely disappears from view. You need to implement a specific, hidden method (e.g., a shortcut or command) to reopen it.

### 3. Chat Interface & Session Management

- **Manual Refresh Button:** Add a manual refresh button to the chat UI.
- **Refresh Transparency:** Explicitly state the automatic refresh interval time. Currently, it stops showing new chats after a few messages or when media is sent.
- **Media Support:** Update the chat interface so that sent media (images/files) is fully visible inside the chat window.
- **Strict Session Separation:** The desktop chat and the Telegram ("Owner") chat are currently mixing together. They must be separated: Desktop chats show only in the Desktop session view, and Telegram chats show only in the Telegram session view.
- **Active Session UI:** The app must display active user flows, explicitly showing the active sessions and indicating which user is tied to which session.

### 4. PDF Generation & Delivery Fixes

- **Persistent Sending:** The bot successfully gathered data (e.g., 5 richest people) and sent a beautiful PDF. However, as mentioned above, when I sent an ID card image, it described it perfectly and saved the PDF, but failed to send it back, asking a question instead. You must make the "send" command a recurring, reliable process so it automatically sends the file to the authorized user every time without asking follow-up questions.
- **PDF Formatting Skills:** The bot is messing up document formatting (words are scattered everywhere). You need to explicitly inject "Doc formatting skills" into its logic so it knows exactly how to structure and align text inside the PDF.

---

## KEY FILES
| File | Purpose |
|------|---------|
| `bot.py` (1544 lines) | Core Telegram bot — handlers, auth, file sending |
| `gui_app.py` (3250 lines) | Main GUI (CustomTkinter) |
| `gui_main.py` (230 lines) | Legacy GUI entry point |
| `agy_runner.py` (704 lines) | ConPTY runner for agy.exe CLI |
| `sessions.py` (296 lines) | Session state management |
| `user_manager.py` (117 lines) | Authorized users CRUD |
| `chat_bus.py` (175 lines) | Thread-safe message bus |
| `config.py` (82 lines) | Configuration constants |
| `bot_instructions.md` | System instructions injected into AI |
| `telegram_formatter.py` (707 lines) | Message formatting for Telegram |
| `settings_manager.py` | Settings CRUD |

## IMPLEMENTATION PLAN STATUS
Check `documents/IMPLEMENTATION_PLAN_v2.md` for the detailed breakdown with checkboxes showing what's done and what's remaining.
