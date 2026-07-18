# ============================================================
#  MEDIA — Audio Transcription + File Handling
# ============================================================
#  Merges audio_handler + file_handler + brain_manager into
#  one clean module. Handles all Telegram media.
# ============================================================

import os
import logging
from datetime import datetime

from zilla.config import (
    FFMPEG_PATH, INBOX_IMAGES, INBOX_AUDIO, INBOX_DOCUMENTS,
    OUTBOX_DOCUMENTS, OUTBOX_IMAGES,
    TELEGRAM_MAX_SEND_FILE, get_setting,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_MEDIA_MB = 50


class MediaTooLargeError(Exception):
    """Raised by download_telegram_file when an incoming file exceeds the
    configured max_media_mb cap (STATUS.md audit finding: no media ingest
    size cap). Frontends should catch this specifically and show one
    friendly refusal rather than treating it as an unexpected error."""

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
    """Download a file from Telegram to the specified folder. Raises
    MediaTooLargeError (without downloading anything) if Telegram reports a
    size over the configured cap — setting 'max_media_mb', default 50."""
    tg_file = await bot.get_file(file_id)
    max_mb = get_setting("max_media_mb", DEFAULT_MAX_MEDIA_MB)
    if tg_file.file_size and tg_file.file_size > max_mb * 1024 * 1024:
        size_mb = tg_file.file_size / (1024 * 1024)
        raise MediaTooLargeError(
            f"That file is {size_mb:.1f} MB, over the {max_mb} MB limit — not downloading it."
        )
    os.makedirs(dest_folder, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name, ext = os.path.splitext(os.path.basename(filename))
    filepath = os.path.join(dest_folder, f"{ts}_{name}{ext}")
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

# Video files are downloaded into the documents folder; we split them out into
# their own category by extension so the Inbox can show 📷/🎵/🎬/📄 separately.
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"}

# The four inbox categories, in display order.
INBOX_CATEGORIES = ("images", "audio", "video", "documents")


def _classify(folder_category: str, fname: str) -> str:
    """Derive the display category for a file (splits video out of documents)."""
    if folder_category == "documents" and os.path.splitext(fname)[1].lower() in VIDEO_EXTS:
        return "video"
    return folder_category


def get_inbox_items(category: str | None = None) -> list[dict]:
    """
    List inbox items with metadata, newest first. Each item:
      {name, category, size, path, mtime}
    If `category` is given, only items in that category are returned.
    """
    items = []
    for folder_category, folder in [
        ("images", INBOX_IMAGES), ("audio", INBOX_AUDIO), ("documents", INBOX_DOCUMENTS),
    ]:
        if not os.path.isdir(folder):
            continue
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if not os.path.isfile(fpath):
                continue
            cat = _classify(folder_category, fname)
            if category and cat != category:
                continue
            try:
                mtime = os.path.getmtime(fpath)
                size = os.path.getsize(fpath)
            except OSError:
                continue
            items.append({"name": fname, "category": cat, "size": size,
                          "path": fpath, "mtime": mtime})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def delete_inbox_file(path: str) -> bool:
    """Delete a file ONLY if it lives inside an inbox folder (path-validated)."""
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    roots = [os.path.realpath(d) for d in (INBOX_IMAGES, INBOX_AUDIO, INBOX_DOCUMENTS)]
    if not any(real == r or real.startswith(r + os.sep) for r in roots):
        return False
    try:
        os.remove(real)
        return True
    except OSError:
        return False


def get_inbox_counts() -> dict:
    """Count files per display category: {images, audio, video, documents}."""
    counts = {c: 0 for c in INBOX_CATEGORIES}
    for item in get_inbox_items():
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return counts


def get_inbox_stats() -> dict:
    """Back-compat counts (images/audio/documents include video under documents)."""
    counts = get_inbox_counts()
    return {
        "images": counts["images"],
        "audio": counts["audio"],
        "documents": counts["documents"] + counts["video"],
    }


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


# ── Outbox (files the agent PRODUCES) ─────────────────────
#
# The CLI writes generated files (reports, sheets, charts, screenshots…) into
# ~/Zilla/Outbox/{documents,images}. The bot auto-delivers what it can,
# but the user also needs to browse/send/delete the rest from Telegram — same
# UX as the Inbox. These mirror the inbox helpers (video split out of docs;
# Outbox has no audio folder).

OUTBOX_CATEGORIES = ("images", "video", "documents")


def get_outbox_items(category: str | None = None) -> list[dict]:
    """List outbox items with metadata, newest first. Same shape as
    get_inbox_items: {name, category, size, path, mtime}."""
    items = []
    for folder_category, folder in [
        ("images", OUTBOX_IMAGES), ("documents", OUTBOX_DOCUMENTS),
    ]:
        if not os.path.isdir(folder):
            continue
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if not os.path.isfile(fpath):
                continue
            cat = _classify(folder_category, fname)
            if category and cat != category:
                continue
            try:
                mtime = os.path.getmtime(fpath)
                size = os.path.getsize(fpath)
            except OSError:
                continue
            items.append({"name": fname, "category": cat, "size": size,
                          "path": fpath, "mtime": mtime})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def delete_outbox_file(path: str) -> bool:
    """Delete a file ONLY if it lives inside an outbox folder (path-validated)."""
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    roots = [os.path.realpath(d) for d in (OUTBOX_IMAGES, OUTBOX_DOCUMENTS)]
    if not any(real == r or real.startswith(r + os.sep) for r in roots):
        return False
    try:
        os.remove(real)
        return True
    except OSError:
        return False


def get_outbox_counts() -> dict:
    """Count files per display category: {images, video, documents}."""
    counts = {c: 0 for c in OUTBOX_CATEGORIES}
    for item in get_outbox_items():
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return counts
