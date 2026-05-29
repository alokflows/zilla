# 🚀 AGY Telegram Bot (Version 1.0 - Stable)

The AGY Telegram Bot acts as a powerful "thin pipe" UI connecting you directly to your local Antigravity (AGY) agent engine. It allows you to operate a fully autonomous AI assistant locally from anywhere in the world via your mobile phone.

## ✨ What's New in Version 1.0 (The Big Update)

This major release transforms the bot from a basic text-relay script into a robust, state-aware AI assistant capable of advanced multitasking, background execution, and direct file management.

### 🛡️ Iron-Clad Stability
*   **The Single-Instance "Kill Switch":** Rebuilt the startup logic with aggressive OS-level file locking (`msvcrt`). This permanently prevents multiple ghost instances of the bot from running simultaneously and causing Telegram API conflicts.
*   **FORCE_KILL_BOT.bat:** A dedicated script has been added to forcefully sweep and terminate any background Python processes instantly, ensuring you always have a clean slate.

### 🧠 Deep UI Integration
*   **Live Settings Persistence:** The UI Settings menu now actively communicates with the local `SettingsManager`. Toggling "Auto Describe Photos" or changing the "Max Sub-Agents" count instantly persists across restarts.
*   **Dynamic Skills Menu:** The Skills menu now actively queries the local agent environment. It instantly displays installed native skills (like Kimi WebBridge).

### 📤 Automated File Delivery Queue
*   **Telegram File Sender Skill:** A dedicated skill was injected into the AI engine. Whenever you ask the agent to "send", "give", or "upload" a file, the AI is strictly instructed to output the absolute Windows file path.
*   **Auto-Regex Uploader:** The bot actively scans the agent's thoughts in real-time. If it detects a generated file path, the bot automatically grabs the file from your local hard drive and uploads it to your Telegram chat.
*   **Sub-Agent Queue System:** If you ask for 5 separate reports, the AI will use parallel sub-agents to generate them. The bot's internal queue system will capture up to 10 files at once and deliver them to your phone sequentially.

### 🌐 Browser Automation (Kimi WebBridge)
*   **Native Web Integration:** Because the bot acts as a direct pipe to the Antigravity engine, it automatically inherits the Kimi WebBridge capability. 
*   **The Hands and Legs:** The AI can now safely open a local Chrome browser window on your machine, navigate Wikipedia, scrape GitHub repositories, and execute complex web navigation tasks—all commanded remotely from your phone.

### 🏗️ Future-Proof Architecture (Version 2 Roadmap)
*   **Modular Workspaces:** We have successfully laid the architectural groundwork (`workspaces_manager.py`) for a highly modular external API system. This will soon allow seamless OAuth integration directly into Google Drive, Docs, and Calendar.
*   **GUI Desktop Application:** The next major milestone (Version 2) will transition this console-based script into a fully-fledged Windows Graphical Application.

---

## 🛠️ Setup & Security

**Security First:** All hardcoded credentials have been securely scrubbed from `config.py`. 
To run the bot, you must create a local `.env` file in this directory containing your keys. (This file is ignored by git to prevent accidental credential leaks).

**`.env` Format:**
```
TELEGRAM_BOT_TOKEN="YOUR_TOKEN_HERE"
TELEGRAM_OWNER_ID="YOUR_USER_ID"
```

Enjoy seamless, remote control over your local AGI!
