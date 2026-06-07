# Zilla — Sessions, Memory & File Delivery (plain-English guide)

This explains how **sessions** work in Zilla (the Mango bot), why a chat can
sometimes feel like it's "remembering too much," and how generated files get
delivered. Written for everyday use — no code knowledge needed.

---

## 1. What a "session" actually is

- The Telegram bot is a **thin pipe**. It does NOT think — it forwards your
  message to the AI CLI (agy / Gemini, or Claude) and relays the answer back.
- A **session** = one ongoing **conversation** with the AI. Everything you say
  inside a session shares memory. The AI remembers the earlier messages in
  *that* session and keeps building on them.
- Each session maps to one CLI **conversation id** behind the scenes. Different
  sessions have **different** conversation ids, so they can't see each other.

Think of a session like a single WhatsApp chat thread: everything in the thread
is shared context; a brand-new thread starts blank.

---

## 2. The commands

| Command | What it does |
|---------|--------------|
| `/new` | Starts a **fresh** session. The next message begins with a blank memory. |
| `/sessions` | Lists your sessions; tap one to switch, or 🗑 to delete. |
| `/switch <name>` | Jump to another existing session (resumes its memory). |
| `/end` | Closes the current session. |
| `/menu` → 📁 Sessions | The same thing with buttons. |

`/new` is **verified to work**: it creates a new conversation id and the AI has
**zero** memory of anything from before (tested: it replies "no prior context").

---

## 3. The gotcha that caused confusion

**If you never hit `/new`, every message keeps going into the SAME session.**

That's normal — it's how you hold a continuous conversation. But if you run lots
of *unrelated* tasks back-to-back without `/new`, they all pile into one giant
conversation. When the AI then continues that bloated thread, it can start
**replaying or referencing old stuff**, which feels like "it's mixing things up"
or "vomiting the whole chat."

**Rule of thumb:**
- Same topic / follow-up question → just keep typing (stay in the session).
- New, unrelated task → send **`/new`** first. Clean slate, sharper answers.

This is not the bot being broken — it's just that one thread got too long.

---

## 4. Files the bot creates ("delivery")

- When you ask the bot to make a file, the AI writes it into
  `~/AGI-Brain/Outbox/` and the bot **attaches it to the chat** so you can
  download it on your phone.
- It auto-sends up to **10** files per reply, and only files it **just made**
  (anything older stays in the Outbox so old files don't get re-sent).
- Anything not auto-sent (or older files) you can pull yourself:
  **`/menu` → 📤 Outbox** → browse → 📤 to send, 🗑 to delete.
- Files **you** send to the bot land in the **Inbox** (`/menu` → 📥 Inbox).

So: **Inbox = stuff you sent in. Outbox = stuff the bot produced.**

---

## 5. If something looks wrong

- **Replies feel like they're dragging in old context** → send `/new`.
- **Same old files keep appearing** → shouldn't happen anymore (fixed in
  v4.6.1); if it does, the file is probably being re-mentioned in a long thread
  — `/new` clears it.
- **Want a specific earlier file** → `/menu` → 📤 Outbox, don't rely on the AI
  to re-send it.

---

*Last updated: 2026-06-07 (v4.6.1). Inbox/Outbox UI + fresh-file delivery added
in v4.6.0–4.6.1.*
