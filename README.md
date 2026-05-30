# 🦖 Zilla: The Personal AGI Telegram Bot (Version 2)
### Security Lockdown and PDF Delivery 

Welcome to **Zilla Version 2**! Zilla is your personal AI assistant that lives on your computer but can be controlled entirely from your phone via Telegram. 

Imagine you have a super-smart robot sitting at your desk. Zilla is that robot! You text Zilla on your phone, and Zilla works on your actual computer to get things done.

---

## ✨ What does Zilla do?

Zilla is not just a chat bot. It is an agent that can actively *do* things on your PC. 
Even if a child asked Zilla to do something, here is how simple it is:

### 1. 💬 Send Messages and Talk
Just open Telegram and type a message to Zilla. You can ask questions, brainstorm ideas, or ask it to research the web. Zilla uses its massive AI brain to read your message, think about it, and type back immediately.

### 2. 📸 Understand Photos & Documents
If you snap a photo of a document (like an ID card) or a picture of your homework, just send the image to Zilla in Telegram! 
- Zilla looks at the picture.
- It understands what is inside it.
- You can ask it to translate it, summarize it, or even convert it!

### 3. 📄 The Magic PDF Delivery 
If you tell Zilla: *"Take the photo of this ID card and make a nicely formatted PDF out of it."*
Zilla will literally create a real PDF file on your computer, save it, and then **automatically send that PDF right back to you in Telegram!** You don't have to search your computer for it. It magically pops up in your chat ready to download.

### 4. 🖥️ The Live Desktop Dashboard
If you are sitting at your computer, you can launch **Zilla's Live Dashboard**. It is a beautiful control panel that shows you:
- Your live chat history syncing in real-time. (If you send a photo on your phone, it instantly pops up on your computer screen!)
- The active AI session.
- System metrics like how many messages have been sent.

---

## 🔒 Iron-Clad Security Lockdown
Zilla is extremely secure. We built a giant digital fortress around it:
- **Stranger Danger:** If a random person finds your bot on Telegram and says "Hello", Zilla will treat them with **dead silence**. It won't even show a loading spinner. Zilla ONLY talks to you.
- **Sandboxed Files:** Zilla is only allowed to send files from specific safe folders on your computer. It can never accidentally leak sensitive system files.
- **Strict Authorization:** Even if an error happens in the code, Zilla is strictly forbidden from showing that error to anyone except the authorized owner.

---

## 🚀 How to Launch Zilla 

Starting Zilla is as easy as turning on a TV.

**Step 1:** Open your computer terminal (PowerShell or Command Prompt).
**Step 2:** Go to the folder where Zilla lives:
```bash
cd C:\Users\Isha\agy-telegram-bot-dev
```
**Step 3:** Turn Zilla on by typing:
```bash
python gui_app.py
```

A beautiful dashboard will appear. Just click the **"Start Bot"** button in the dashboard, and Zilla is awake and ready to serve you on Telegram!

---

## 🛠️ Setup for the Grown-Ups (Technical Settings)
To make Zilla yours, you need a secret key so Zilla knows it belongs to you.
Create a file named `.env` in the folder and put your keys inside like this:

```text
TELEGRAM_BOT_TOKEN="YOUR_SECRET_TELEGRAM_TOKEN"
TELEGRAM_OWNER_ID="YOUR_TELEGRAM_USER_ID_NUMBER"
```
*(Keep this file secret!)*

Enjoy your incredibly powerful, super-secure AI assistant!
