# ============================================================
#  CONFIGURATION — AGY Telegram Bot v7 (Dev)
# ============================================================

import os

# --- Load .env manually for zero-dependency ---
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                key, val = line.strip().split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"\'')

# --- Telegram Settings ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
ALLOWED_USER_ID = None  # None = open access for dev
OWNER_CHAT_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))

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

# --- Session State File (DEV — separate from prod!) ---
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.json")

# --- Settings File ---
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# --- Agent State File ---
AGENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents.json")

# --- Kimi Web Bridge ---
KIMI_BRIDGE_DIR = r"C:\Users\Isha\.kimi-webbridge"
KIMI_BRIDGE_BIN = os.path.join(KIMI_BRIDGE_DIR, "bin", "kimi-webbridge")
KIMI_BRIDGE_URL = "http://127.0.0.1:10086"

# --- Skills ---
SKILLS_DIR = r"C:\Users\Isha\.gemini\antigravity-cli\skills"

# --- Whisper (Audio Transcription) ---
WHISPER_MODEL = "base"  # tiny, base, small, medium
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "int8"

# --- Telegram Limits ---
TELEGRAM_MAX_LENGTH = 4000
TELEGRAM_MAX_SEND_FILE = 50 * 1024 * 1024    # 50 MB
TELEGRAM_MAX_DOWNLOAD_FILE = 20 * 1024 * 1024  # 20 MB

# --- Max Concurrent Agents ---
MAX_CONCURRENT_AGENTS = 5

# --- Bot Version ---
BOT_VERSION = "7.0"
