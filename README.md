# AGI Telegram Bot (Thin Pipe)

This repository contains the source code for a Telegram Bot that acts as a "Thin Pipe" interface to the Antigravity CLI, turning it into an accessible, persistent AGI Brain.

## Overview
Instead of building complex conversation logic into the bot itself, this bot is designed with a specific philosophy: **The bot does zero thinking.** It takes user input from Telegram (text, voice, images, documents), standardizes it, and pipes it directly to the Antigravity CLI engine. The AI handles all the reasoning, web searching, and task execution, and the bot simply relays the result back to Telegram.

## Core Features
1. **Direct Pass-Through (`bot.py` & `agy_runner.py`)**:
   - Any text message sent to the bot is forwarded directly to the Antigravity CLI. 
   - State and conversation IDs are mapped to Telegram sessions using `sessions.py`, allowing continuous, context-aware conversations.

2. **Voice Transcription (`audio_handler.py`)**:
   - Accepts direct Telegram Voice Notes (`.ogg`).
   - Uses `ffmpeg` and `pydub` to convert the audio into `16kHz` mono `.wav` files.
   - Transcribes the audio locally using Google Speech Recognition.
   - The resulting text transcript is piped directly into the AI as if the user typed it.

3. **Media & Document Handling (`file_handler.py`)**:
   - Photos, Documents, and Videos sent to the bot are automatically downloaded.
   - Files are fed into the AI, allowing it to perform visual analysis on photos or summarize documents.

4. **Karpathy-Inspired AGI-Brain (`brain_manager.py`)**:
   - Automatically maintains a structured local file system (the "Brain") in the user's directory (`C:\Users\Isha\AGI-Brain`).
   - Contains an `Inbox/` (for raw incoming media and telegram drops) and a `Knowledge/` base (for transcripts, notes, and research summaries).
   - Serves as the AI's long-term memory and working directory.

## Setup Requirements
1. **Python Dependencies**:
   - `python-telegram-bot`
   - `pydub`
   - `SpeechRecognition`
2. **External Tools**:
   - `ffmpeg` must be present in the Tools directory (`AGI-Brain\Tools\ffmpeg\ffmpeg.exe`) for audio conversion to work.
3. **Environment**:
   - Configured via `config.py` with your `BOT_TOKEN` and allowed user ID.

## Running the Bot
Run the bot locally using:
```bash
python bot.py
```
The bot will verify the brain directory structure on startup and begin polling for Telegram updates.
