# 📖 Zilla — The Full User Manual

Everything you need, explained simply. Read the part you need:

1. [What Zilla is](#1-what-zilla-is)
2. [Install it (baby steps)](#2-install-it--baby-steps)
3. [Get your token and ID](#3-get-your-token-and-id)
4. [Turn it on](#4-turn-it-on)
5. [Run it quietly in the background](#5-run-it-quietly-in-the-background)
6. [Everyday use — talking to Zilla](#6-everyday-use--talking-to-zilla)
7. [Sessions — separate notebooks](#7-sessions--separate-notebooks)
8. [Owner & admin powers](#8-owner--admin-powers)
9. [Connecting a DIFFERENT AI (another CLI)](#9-connecting-a-different-ai-another-cli)
10. [Sharing with a friend](#10-sharing-with-a-friend)
11. [When something goes wrong](#11-when-something-goes-wrong)

---

## 1. What Zilla is

Zilla is a **messenger** that lives in your Telegram. You send it a message; it passes it to a clever **AI program** on your computer; the AI thinks; Zilla brings the answer back to you.

```
  YOU (Telegram)  ───▶  ZILLA (this bot)  ───▶  THE AI BRAIN (a CLI program)
  YOU (Telegram)  ◀───  ZILLA (this bot)  ◀───  THE AI BRAIN (a CLI program)
```

Zilla does **no thinking** of its own. It just carries messages and keeps everything safe and tidy.

---

## 2. Install it — baby steps

Do these **in order**, top to bottom. 🍼

### Step 1 — Install Python (the engine)
1. Go to 👉 **https://www.python.org/downloads/**
2. Click the big **"Download Python"** button.
3. Open the downloaded file.
4. ⚠️ **VERY IMPORTANT:** tick the box at the bottom that says **"Add python.exe to PATH"**, then click **Install Now**.
5. Wait, then click **Close**.

### Step 2 — Get the Zilla project
1. Go to 👉 **https://github.com/alokflows/zilla**
2. Click the green **"< > Code"** button → **"Download ZIP"**.
3. Open your **Downloads** folder, **right-click the zip → Extract All**.
4. You now have a folder called **zilla**. 📁

### Step 3 — Install the AI brain (the `agy` CLI)
Zilla needs the **`agy` AI program** to think. Install it the **same way it was first installed**. Zilla finds it automatically afterward.
> Without this, the bot turns on but can't answer anything. See [Section 9](#9-connecting-a-different-ai-another-cli) to use a different AI.

### Step 4 — Open a command window inside the folder
1. Open the **zilla** folder.
2. Click in the **address bar** at the top.
3. Type **`cmd`** and press **Enter**.
4. A black window appears. 🖤 That's normal — keep it open.

### Step 5 — Install Zilla's helper parts
In the black window, type and press Enter:
```
pip install -r requirements.txt
```
Wait until it goes quiet. ✅

Now do [Section 3](#3-get-your-token-and-id) to make your settings file.

---

## 3. Get your token and ID

Zilla needs two secret values. You put them in a file called `.env`.

### Make the `.env` file
1. In the **zilla** folder, find **`.env.example`**.
2. Copy it (Ctrl+C, Ctrl+V). You get a copy.
3. Rename the copy to exactly **`.env`** (just `.env`, nothing else).
4. Open `.env` with Notepad.

### Get your bot token 🤖
1. Open Telegram, search for **@BotFather**.
2. Send `/newbot` and follow the steps (give it a name).
3. BotFather sends you a long **token**. Copy it.
4. Paste it in `.env`:
   ```
   TELEGRAM_BOT_TOKEN="the-token-botfather-gave-you"
   ```

### Get your Telegram ID 🆔
1. In Telegram, search for **@userinfobot** and message it.
2. It replies with your **number ID**. Copy it.
3. Paste it in `.env`:
   ```
   TELEGRAM_OWNER_ID="your-number-id"
   ```

**Save** (Ctrl+S) and close. Leave everything else in the file alone — it sorts itself out.

---

## 4. Turn it on

In the black command window (inside the zilla folder), type:
```
python bot.py
```
🎉 Open Telegram, find your bot, and say **hi**. It should reply.

To stop it: click the black window and press **Ctrl + C**.

---

## 5. Run it quietly in the background

Once it works, you don't need the black window.

- **Run invisibly:** double-click **`run_bot_hidden.vbs`** — runs silently, restarts itself if it crashes.
- **Start automatically when you log in:** double-click **`install_startup.bat`** (undo with `uninstall_startup.bat`).
- **Stop it completely:** double-click **`Stop Zilla.vbs`**.

---

## 6. Everyday use — talking to Zilla

Just **type any message** — it goes straight to the AI. No command needed.

| You send…                              | Zilla does…                                         |
|----------------------------------------|-----------------------------------------------------|
| 💬 Text                                | Sends to AI, replies with the answer                |
| 🎤 Voice note                          | Turns your speech into words, then answers          |
| 📷 Photo **with a caption**            | Looks at the photo, answers your question           |
| 📄 File (PDF/Word/etc.) **with caption** | Reads the file, answers your question             |
| 📷 / 📄 **with NO caption**            | Just saves it to a safe folder                      |

If the AI makes a file for you (like a PDF), Zilla **sends it to you automatically**. 🎁

**Handy commands (anyone can use):**

| Command   | What it does                          |
|-----------|---------------------------------------|
| `/menu`   | Open the button control panel         |
| `/cancel` | Stop the AI if it's taking too long   |
| `/ping`   | Check Zilla is awake                  |
| `/brain`  | See how many saved photos/files       |
| `/help`   | Show all commands                     |

---

## 7. Sessions — separate notebooks

A **session** is like a separate notebook for a separate topic. They don't mix. 📓

| Command            | What it does                          |
|--------------------|---------------------------------------|
| `/new homework`    | Start a fresh notebook called "homework" |
| `/sessions`        | See all your notebooks                |
| `/switch recipes`  | Jump to your "recipes" notebook       |
| `/end`             | Close the current notebook            |

---

## 8. Owner & admin powers

There are **three levels** of people:

| Level         | Can do…                                                            |
|---------------|--------------------------------------------------------------------|
| 👤 **User**   | Chat, voice, photos, files                                         |
| 👑 **Admin**  | Everything a User can + change AI model, settings, `/browse`       |
| 🦸 **Owner**  | Everything + add/remove people and change their level (that's you) |

**Admin commands:**

| Command           | What it does                  |
|-------------------|-------------------------------|
| `/model`          | Pick which AI brain to use    |
| `/settings`       | Change bot settings           |
| `/browse <url>`   | Open a web page in a browser  |

**Owner commands (just you):**

| Command              | What it does            |
|----------------------|-------------------------|
| `/adduser <id>`      | Add a new person        |
| `/removeuser <id>`   | Remove a person         |
| `/listusers`         | See & manage everyone   |

Easiest way to add a friend: type `/menu` → tap **👥 Users** → **➕ Add User**, then follow the buttons (their ID → name → level). No typing code needed.

> To get a friend's Telegram ID, have them message **@userinfobot** and send you the number.

---

## 9. Connecting a DIFFERENT AI (another CLI)

Zilla talks to the AI by running it like a command and reading what it produces. There are **two cases**.

### 🟢 Easy case: same `agy` program, just a different location
If `agy` is installed somewhere unusual, open `.env`, find this line, remove the `#`, and point it at the right file:
```
CLI_PATH=C:\path\to\your\agy.exe
```
Save, restart the bot. Done. ✅

### 🟡 Harder case: a totally different AI program
Zilla currently expects the AI to be run like this:
```
agy.exe --conversation <id> --print-timeout <minutes>m --print "your message"
```
…and it reads the AI's answer from a transcript file the `agy` CLI writes.

A different AI program probably uses **different command words** (flags) and may not write that transcript file. So you (or an AI helping you) need to make two small edits in the file **`cli_engine.py`**:

1. **Change how the command is built.**
   Find the function `run_cli` (near the middle of the file) and the lines that build `cmd_parts` — roughly:
   ```python
   cmd_parts = [CLI_PATH]
   if conversation_id:
       cmd_parts.extend(["--conversation", conversation_id])
   ...
   cmd_parts.extend(["--print-timeout", f"{print_timeout_min}m", "--print", prompt])
   ```
   Replace the flags with whatever your new AI program uses to "take one message and print one answer."

2. **Tell it where the answer comes from.**
   - If your new program simply **prints the answer to the screen**, good news: Zilla already captures screen output as a fallback, so it often **just works** after step 1.
   - The "remember the conversation" and "live progress" features rely on the `agy` transcript file. If your new program doesn't write one, those extras won't work — but normal question-and-answer still will.

> 💡 **Simplest path:** open the project in any AI coding assistant and say:
> *"In `cli_engine.py`, change the `run_cli` command builder to launch **&lt;my CLI&gt;** with **&lt;its flags&gt;** instead of agy's `--print` flags."*
> That's the only file you need to touch to swap brains.

You can also point Zilla at a different transcript location with `BRAIN_DIR` in `.env` if your CLI writes one elsewhere.

---

## 10. Sharing with a friend

1. **Best way — they clone from GitHub:**
   ```
   git clone https://github.com/alokflows/zilla.git
   ```
   This safely **leaves out your private `.env`**.
   > ⚠️ Don't just zip your folder and send it — your `.env` holds your **secret bot token**. If you must zip, **delete `.env` first**.

2. They install the **`agy` AI brain** ([Step 3](#2-install-it--baby-steps)).
3. They get **their own** token + ID and make **their own** `.env` ([Section 3](#3-get-your-token-and-id)).
4. They run `python bot.py`.

> A bot token is like a house key for one specific bot. You each run your own copy with your own key. Share the *code*, never the *key*.

---

## 11. When something goes wrong

| Problem                                   | Try this                                                                 |
|-------------------------------------------|--------------------------------------------------------------------------|
| `'python' is not recognized`              | Python wasn't added to PATH. Reinstall Python and tick **"Add to PATH"**. |
| Bot starts but never answers              | The **`agy` AI brain isn't installed** or `CLI_PATH` is wrong. See [Section 9](#9-connecting-a-different-ai-another-cli). |
| Bot ignores my messages in Telegram       | Your `TELEGRAM_OWNER_ID` is wrong/missing in `.env`. Double-check it with @userinfobot. |
| "Another instance is already running"     | It's already on. Double-click **`Stop Zilla.vbs`** first, then start again. |
| Voice notes aren't transcribed            | `ffmpeg` isn't installed. Text and files still work fine without it.     |
| I changed `.env` but nothing changed      | **Stop and restart** the bot — it reads `.env` only when it starts.      |

To watch what's happening, run with the visible window: double-click **`START_BOT.bat`** (it shows logs). Logs are also saved in the **`logs`** folder.

---

*Zilla relays — the AI thinks. Made with ⚡ by Alok.*
