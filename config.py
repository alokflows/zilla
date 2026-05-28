# ============================================================
#  CONFIGURATION FILE — agy Telegram Bot v4 (AGI Brain)
# ============================================================

import os

# --- Telegram Settings ---
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
ALLOWED_USER_ID = 123456789  # Replace with your actual Telegram User ID

# --- agy CLI Settings ---
AGY_PATH = r"C:\Users\Isha\AppData\Local\agy\bin\agy.exe"
AGY_WORKING_DIR = r"C:\Users\Isha"
AGY_TIMEOUT = 600  # 10 minutes max per request

# --- Brain Directory (where agy stores conversations) ---
BRAIN_DIR = r"C:\Users\Isha\.gemini\antigravity-cli\brain"

# --- AGI Brain (Knowledge Management System) ---
AGI_BRAIN_DIR = r"C:\Users\Isha\AGI-Brain"
AGI_INBOX_DIR = os.path.join(AGI_BRAIN_DIR, "Inbox")
AGI_INBOX_IMAGES = os.path.join(AGI_INBOX_DIR, "images")
AGI_INBOX_AUDIO = os.path.join(AGI_INBOX_DIR, "audio")
AGI_INBOX_DOCUMENTS = os.path.join(AGI_INBOX_DIR, "documents")
AGI_INBOX_TELEGRAM = os.path.join(AGI_INBOX_DIR, "telegram")
AGI_KNOWLEDGE_DIR = os.path.join(AGI_BRAIN_DIR, "Knowledge")
AGI_KNOWLEDGE_NOTES = os.path.join(AGI_KNOWLEDGE_DIR, "notes")
AGI_KNOWLEDGE_TRANSCRIPTS = os.path.join(AGI_KNOWLEDGE_DIR, "transcripts")
AGI_KNOWLEDGE_SUMMARIES = os.path.join(AGI_KNOWLEDGE_DIR, "summaries")
AGI_KNOWLEDGE_RESEARCH = os.path.join(AGI_KNOWLEDGE_DIR, "research")
AGI_PROJECTS_DIR = os.path.join(AGI_BRAIN_DIR, "Projects")
AGI_TOOLS_DIR = os.path.join(AGI_BRAIN_DIR, "Tools")
AGI_LOGS_DIR = os.path.join(AGI_BRAIN_DIR, "Logs")

# --- Portable ffmpeg ---
FFMPEG_PATH = os.path.join(AGI_TOOLS_DIR, "ffmpeg", "ffmpeg.exe")

# --- Session State File ---
STATE_FILE = r"C:\Users\Isha\agy-telegram-bot\sessions.json"

# --- Kimi Web Bridge ---
KIMI_BRIDGE_DIR = r"C:\Users\Isha\.kimi-webbridge"
KIMI_CONFIG_DIR = r"C:\Users\Isha\.kimi"

# --- Message Settings ---
TELEGRAM_MAX_LENGTH = 4000
