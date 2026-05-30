# ============================================================
#  MEDIA — Audio Transcription + File Handling
# ============================================================
#  Merges audio_handler + file_handler + brain_manager into
#  one clean module. Handles all Telegram media.
# ============================================================

import os
import logging
from datetime import datetime

from config import (
    FFMPEG_PATH, INBOX_IMAGES, INBOX_AUDIO, INBOX_DOCUMENTS,
    TELEGRAM_MAX_SEND_FILE,
)

logger = logging.getLogger(__name__)

# ── Configure ffmpeg for pydub ────────────────────────────

_ffmpeg_dir = os.path.dirname(FFMPEG_PATH)
if os.path.exists(FFMPEG_PATH):
    os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

try:
    from pydub import AudioSegment
    PYDUB_OK = True
except ImportError:
    PYDUB_OK = False
    logger.warning("[MEDIA] pydub not installed — audio conversion disabled")

try:
    import speech_recognition as sr
    SR_OK = True
except ImportError:
    SR_OK = False
    logger.warning("[MEDIA] SpeechRecognition not installed — transcription disabled")


def is_audio_capable() -> bool:
    return PYDUB_OK and SR_OK


def get_audio_status() -> str:
    parts = []
    parts.append(f"pydub: {'OK' if PYDUB_OK else 'MISSING'}")
    parts.append(f"SR: {'OK' if SR_OK else 'MISSING'}")
    parts.append(f"ffmpeg: {'OK' if os.path.exists(FFMPEG_PATH) else 'MISSING'}")
    return " | ".join(parts)


# ── Audio Transcription ───────────────────────────────────

def convert_to_wav(input_path: str) -> str | None:
    """Convert any audio file to WAV for speech recognition."""
    if not PYDUB_OK:
        return None
    try:
        ext = os.path.splitext(input_path)[1].lower().lstrip(".")
        fmt_map = {
            "ogg": "ogg", "oga": "ogg", "opus": "ogg",
            "mp3": "mp3", "m4a": "mp4", "mp4": "mp4",
            "wav": "wav", "webm": "webm", "flac": "flac", "aac": "aac",
        }
        audio = AudioSegment.from_file(input_path, format=fmt_map.get(ext, ext))
        audio = audio.set_channels(1).set_frame_rate(16000)
        wav_path = os.path.splitext(input_path)[0] + ".wav"
        audio.export(wav_path, format="wav")
        return wav_path
    except Exception as e:
        logger.error(f"[MEDIA] Audio conversion failed: {e}")
        return None


def transcribe_audio(audio_path: str) -> str | None:
    """Transcribe audio using Google Speech Recognition (free, cloud)."""
    if not SR_OK:
        return None

    wav_path = audio_path
    if not audio_path.lower().endswith(".wav"):
        wav_path = convert_to_wav(audio_path)
        if not wav_path:
            return None

    try:
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio_data = recognizer.record(source)
        text = recognizer.recognize_google(audio_data, language="en-US")
        logger.info(f"[MEDIA] Transcribed {len(text)} chars")
        return text
    except sr.UnknownValueError:
        return "[Could not understand the audio. Try speaking more clearly.]"
    except sr.RequestError as e:
        return f"[Speech recognition service error: {e}]"
    except Exception as e:
        return f"[Transcription error: {e}]"
    finally:
        if wav_path != audio_path and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


# ── File Download ─────────────────────────────────────────

async def download_telegram_file(bot, file_id: str, dest_folder: str, filename: str) -> str:
    """Download a file from Telegram to the specified folder."""
    os.makedirs(dest_folder, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name, ext = os.path.splitext(os.path.basename(filename))
    filepath = os.path.join(dest_folder, f"{ts}_{name}{ext}")
    tg_file = await bot.get_file(file_id)
    await tg_file.download_to_drive(filepath)
    logger.info(f"[MEDIA] Downloaded: {filepath} ({os.path.getsize(filepath)} bytes)")
    return filepath


async def save_photo(bot, photo_sizes: list) -> str:
    best = photo_sizes[-1]
    return await download_telegram_file(
        bot, best.file_id, INBOX_IMAGES, f"photo_{best.file_unique_id}.jpg"
    )


async def save_voice(bot, voice) -> str:
    return await download_telegram_file(
        bot, voice.file_id, INBOX_AUDIO, f"voice_{voice.file_unique_id}.ogg"
    )


async def save_audio(bot, audio) -> str:
    filename = audio.file_name or f"audio_{audio.file_unique_id}.mp3"
    return await download_telegram_file(bot, audio.file_id, INBOX_AUDIO, filename)


async def save_document(bot, document) -> str:
    filename = document.file_name or f"doc_{document.file_unique_id}"
    return await download_telegram_file(bot, document.file_id, INBOX_DOCUMENTS, filename)


async def save_video(bot, video) -> str:
    return await download_telegram_file(
        bot, video.file_id, INBOX_DOCUMENTS, f"video_{video.file_unique_id}.mp4"
    )


# ── Text Extraction ───────────────────────────────────────

def extract_text(filepath: str) -> str | None:
    """Extract text from documents (PDF, DOCX, plain text)."""
    ext = os.path.splitext(filepath)[1].lower()
    plain_exts = {
        ".txt", ".md", ".csv", ".json", ".log", ".py", ".js",
        ".ts", ".html", ".xml", ".yaml", ".yml", ".toml",
    }
    if ext in plain_exts:
        return _read_plain(filepath)
    elif ext == ".pdf":
        return _extract_pdf(filepath)
    elif ext == ".docx":
        return _extract_docx(filepath)
    return None


def _read_plain(filepath: str) -> str | None:
    for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
        try:
            with open(filepath, "r", encoding=enc) as f:
                content = f.read(10000)
                has_more = bool(f.read(1))
            if content.strip():
                if has_more:
                    content += "\n\n... [truncated]"
                return content
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None


def _extract_pdf(filepath: str) -> str | None:
    try:
        import PyPDF2
    except ImportError:
        return None
    try:
        parts = []
        with open(filepath, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for i, page in enumerate(reader.pages[:20]):
                text = page.extract_text()
                if text and text.strip():
                    parts.append(f"--- Page {i+1} ---\n{text.strip()}")
        content = "\n\n".join(parts)
        return content[:15000] if content else None
    except Exception:
        return None


def _extract_docx(filepath: str) -> str | None:
    try:
        import docx
    except ImportError:
        return None
    try:
        doc = docx.Document(filepath)
        content = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return content[:15000] if content else None
    except Exception:
        return None


# ── Inbox Stats ───────────────────────────────────────────

def get_inbox_stats() -> dict:
    """Count files in inbox directories."""
    stats = {}
    for name, path in [("images", INBOX_IMAGES), ("audio", INBOX_AUDIO), ("documents", INBOX_DOCUMENTS)]:
        try:
            stats[name] = len([f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]) if os.path.isdir(path) else 0
        except Exception:
            stats[name] = 0
    return stats


def get_inbox_items() -> list[dict]:
    """List inbox items with metadata."""
    items = []
    for category, folder in [("images", INBOX_IMAGES), ("audio", INBOX_AUDIO), ("documents", INBOX_DOCUMENTS)]:
        if not os.path.isdir(folder):
            continue
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if os.path.isfile(fpath):
                items.append({"name": fname, "category": category, "size": os.path.getsize(fpath), "path": fpath})
    items.sort(key=lambda x: x["name"], reverse=True)
    return items


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"
