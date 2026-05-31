# ⚡ Zilla — Your AI Helper Inside Telegram

> **Talk to a super-smart AI right inside your Telegram chat — by text, by voice, or by sending photos and files.**

Imagine you have a very clever robot friend who lives inside your phone's Telegram app.
You send it a message — like *"write me a story"* or *"summarize this PDF"* — and it does the work and sends the answer right back to you. **That robot friend is Zilla.** 🦖

> 📖 **Want step-by-step install + a full user manual?** Read **[MANUAL.md](MANUAL.md)** — everything explained like you're three, including how to connect a different AI.

---

## 🧸 Explain It Like I'm 3

Think of it like a **walkie-talkie to a genius**:

1. 📱 You **talk into Telegram** (type, send a voice note, or send a picture).
2. 📨 Zilla is the **messenger**. It carries your words to the genius.
3. 🧠 The **genius** is an AI program on a computer (called the "CLI"). It does all the thinking.
4. 📬 Zilla carries the answer **back to you** in the chat.

Zilla itself is **not** the brain. Zilla is the **mail carrier** between you and the brain. That's the whole secret. 📮

```
  YOU                ZILLA                  THE BRAIN
  📱  ───message───▶  📮  ───message───▶   🧠
  📱  ◀──answer────   📮  ◀──answer────    🧠
(Telegram)        (this bot)            (the AI program)
```

---

## ✨ What Can Zilla Do?

| You send...                          | Zilla does...                                          |
|--------------------------------------|--------------------------------------------------------|
| 💬 A text message                    | Sends it to the AI and replies with the answer         |
| 🎤 A voice note                      | Listens, turns it into words, then answers             |
| 📷 A photo **with a caption**        | Looks at the photo and answers your question about it  |
| 📄 A file (PDF, Word, etc.) **with a caption** | Reads the file and answers your question         |
| 📷 A photo or file **with no caption** | Just saves it in a safe folder for later               |
| 🌐 `/browse google.com`              | Opens a web page in a real browser (for admins)        |

If the AI makes a file for you (like a PDF report), Zilla **automatically sends that file** to you in the chat. 🎁

---

## 🔐 Who Is Allowed To Use It? (3 Levels)

Not everyone can use Zilla — only people you allow. There are **three levels of people**, like a video game:

| Level         | What they can do                                                        |
|---------------|-------------------------------------------------------------------------|
| 👤 **User**   | Chat, send voice, send photos and files                                 |
| 👑 **Admin**  | Everything a User can, **plus** change the AI brain, settings, browse   |
| 🦸 **Owner**  | Everything, **plus** add or remove people and change their level        |

There is **only one Owner** — that's you, the person who set it up.
The Owner can add friends from inside Telegram with a few button taps (no typing code!).

---

## 🛡️ Is It Safe?

Yes — Zilla is built to be careful:

- 🚪 **Locked door:** Only people on the allowed list can talk to it. Everyone else is ignored.
- 📁 **Safe folders only:** When sending you a file, Zilla can *only* reach into two specific folders. It can **never** grab your passwords or private files from anywhere else on the computer.
- 🔁 **Always checks the list:** Every single message is checked against the allowed-people list.
- 🤫 **Keeps secrets secret:** Your bot's secret password (the token) lives in a hidden `.env` file that is **never** uploaded to the internet.

---

## ⏱️ What If the AI Takes a Long Time?

Sometimes the AI has a big job and needs to think for a while. Zilla is patient but smart about it:

- ⌛ **It waits** as long as the AI is still working — no rushing it.
- 💬 For the first minute, you just see Telegram's normal "typing..." bubble.
- 📊 After a minute, Zilla shows a little **"Working… 1m 30s"** message with a **🛑 Cancel** button, and quietly updates it. No spam!
- 🛑 You can **stop it any time** by tapping Cancel or typing `/cancel`. Whatever the AI finished so far still gets sent to you.
- 😴 If the AI goes totally silent for too long (10 minutes by default), Zilla gently stops it so it doesn't run forever.

---

## 🗂️ Sessions — Like Separate Notebooks

A **session** is like a separate notebook for a separate topic. 📓

- Your *"homework"* notebook remembers your homework chat.
- Your *"recipes"* notebook remembers your cooking chat.

They don't mix up! You can make new notebooks, switch between them, and end them:

- `/new homework` → start a fresh notebook called "homework"
- `/sessions` → see all your notebooks
- `/switch recipes` → jump to your "recipes" notebook
- `/end` → close the current notebook

---

## 📋 All the Commands

Just **type any message** to talk to the AI — no command needed! These extra commands give you control:

| Command            | Who can use it | What it does                          |
|--------------------|----------------|---------------------------------------|
| *(just type)*      | Everyone       | Talk to the AI                        |
| `/menu`            | Everyone       | Open the button control panel         |
| `/cancel`          | Everyone       | Stop the AI if it's running           |
| `/new <name>`      | Everyone       | Start a new session (notebook)        |
| `/sessions`        | Everyone       | List your sessions                    |
| `/switch <name>`   | Everyone       | Switch to another session             |
| `/end`             | Everyone       | End the current session               |
| `/brain`           | Everyone       | See saved photos/files count          |
| `/ping`            | Everyone       | Check if Zilla is awake               |
| `/model`           | 👑 Admin+      | Pick which AI brain to use            |
| `/settings`        | 👑 Admin+      | Change bot settings                   |
| `/browse <url>`    | 👑 Admin+      | Open a web page in a browser          |
| `/adduser <id>`    | 🦸 Owner       | Add a new person                      |
| `/removeuser <id>` | 🦸 Owner       | Remove a person                       |
| `/listusers`       | 🦸 Owner       | See and manage all people             |

---

## 🚀 How To Set It Up

You need **Python** installed and an AI CLI program on your Windows computer.

**Step 1 — Get a Telegram bot token**
Message [@BotFather](https://t.me/BotFather) on Telegram, type `/newbot`, follow the steps, and copy the token it gives you.

**Step 2 — Get your Telegram ID**
Message [@userinfobot](https://t.me/userinfobot) on Telegram. It tells you your number ID.

**Step 3 — Make your settings file.**
Copy the template `.env.example` and name the copy `.env`, then open it and fill in your token and ID:
```env
TELEGRAM_BOT_TOKEN=paste_your_token_here
TELEGRAM_OWNER_ID=paste_your_id_here
```
You can leave everything else in the file alone — it figures itself out automatically.

**Step 4 — Install the helper libraries** (open a terminal in this folder):
```bash
pip install -r requirements.txt
```

**Step 5 — Start the bot:**
```bash
python bot.py
```

Now open Telegram, find your bot, and say hello! 👋

---

## 🖥️ Keep It Running Quietly

Want Zilla to run in the background without a black window popping up?

- **Run invisibly:** double-click `run_bot_hidden.vbs` — it runs silently and restarts itself if it crashes.
- **Start automatically when you log in:** double-click `install_startup.bat` (undo with `uninstall_startup.bat`).
- **Stop it:** double-click `Stop Zilla.vbs`.

---

## 🤝 Sharing With a Friend / Moving to Another Computer

Good news: Zilla **does not care** about your username or where the folder lives. It figures out the right paths on whatever computer it runs on. So moving it is easy — but there are **two things your friend must do**.

**1. Get the project onto their computer.** Best way:
```bash
git clone https://github.com/alokflows/zilla.git
```
> ⚠️ **Do NOT just zip your folder and send it.** Your own `.env` file holds your **secret bot token** — anyone who gets it can take over your bot. Cloning from GitHub is safe because `.env` is never uploaded. If you must zip, **delete `.env` first.**

**2. Install the "brain" (the AI CLI).** Zilla is only the messenger — it needs the AI program (`agy`) installed to actually think. Your friend installs that separately. If they put it in the normal place, Zilla finds it automatically. If they put it somewhere unusual, they just set `CLI_PATH` in their `.env`.

**Then your friend does the normal setup:**
- Install Python, then `pip install -r requirements.txt`
- Get **their own** bot token (@BotFather) and **their own** Telegram ID (@userinfobot)
- Copy `.env.example` → `.env` and fill in those two values
- Run `python bot.py`

That's it. Their bot, their token, their computer — same code. 🎉

> 💡 **Why their own token?** A bot token is like a house key for one specific bot. You each run your own copy of Zilla with your own key. Sharing the *code* is great; sharing the *key* is not.

---

## 🧩 What's Inside (For Curious Grown-Ups)

Zilla is built from a few small, tidy Python files — each does one job:

| File                  | Its one job                                                        |
|-----------------------|--------------------------------------------------------------------|
| `bot.py`              | Talks to Telegram — buttons, messages, permission checks           |
| `cli_engine.py`       | Runs the AI program and collects its answer                        |
| `config.py`           | Holds all the settings (reads your `.env`)                         |
| `sessions.py`         | Remembers each person's separate notebooks (sessions)              |
| `users.py`            | Knows who is allowed and what level they are                       |
| `media.py`            | Handles voice, photos, and files; turns speech into text           |
| `formatter.py`        | Makes the AI's answer look neat in Telegram                        |
| `winhide.py`          | Hides black console windows on Windows                             |
| `bot_instructions.md` | The rulebook the AI reads so it behaves like a Telegram assistant  |

**The big idea:** the bot is a *thin pipe*. It does **no thinking** — it just carries messages between you and the AI, and makes everything safe and tidy along the way. 🚰

---

*Made with ⚡ by Alok. Zilla relays — the AI thinks.*
