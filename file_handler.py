# ============================================================
#  FILE HANDLER — Image, Document & Video Processing
# ============================================================
#  Handles photos, documents, videos, and files sent via Telegram.
#
#  Photos:
#  - Saved to AGI-Brain/Inbox/images
#  - Can be sent to agy for analysis
#
#  Documents:
#  - Saved to AGI-Brain/Inbox/documents
#  - Text extracted from PDF, DOCX, TXT, MD, CSV
#  - Text content sent to agy for summarization
#
#  Videos:
#  - Saved to AGI-Brain/Inbox/telegram
#  - Audio extracted for transcription (if ffmpeg available)
# ============================================================

import os
import logging
from datetime import datetime

from config import (
    AGI_INBOX_IMAGES,
    AGI_INBOX_DOCUMENTS,
    AGI_INBOX_TELEGRAM,
    TELEGRAM_MAX_LENGTH,
    FFMPEG_PATH,
)

logger = logging.getLogger(__name__)


async def download_telegram_file(bot, file_id: str, dest_folder: str, filename: str) -> str:
    """
    Download a file from Telegram and save it to the specified folder.
    Returns the full path to the saved file.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name, ext = os.path.splitext(filename)
    unique_name = f"{ts}_{name}{ext}"
    filepath = os.path.join(dest_folder, unique_name)

    tg_file = await bot.get_file(file_id)
    await tg_file.download_to_drive(filepath)

    logger.info(f"[FILE] Downloaded: {filepath} ({os.path.getsize(filepath)} bytes)")
    return filepath


async def save_photo(bot, photo_sizes: list) -> str:
    """
    Save the largest available photo from Telegram.
    Returns the file path.
    """
    # Get the largest photo (last in the list)
    best_photo = photo_sizes[-1]
    filename = f"photo_{best_photo.file_unique_id}.jpg"
    return await download_telegram_file(bot, best_photo.file_id, AGI_INBOX_IMAGES, filename)


async def save_document(bot, document) -> str:
    """
    Save a document from Telegram.
    Returns the file path.
    """
    filename = document.file_name or f"doc_{document.file_unique_id}"
    return await download_telegram_file(bot, document.file_id, AGI_INBOX_DOCUMENTS, filename)


async def save_voice(bot, voice) -> str:
    """
    Save a voice message from Telegram.
    Returns the file path.
    """
    from config import AGI_INBOX_AUDIO
    filename = f"voice_{voice.file_unique_id}.ogg"
    return await download_telegram_file(bot, voice.file_id, AGI_INBOX_AUDIO, filename)


async def save_audio(bot, audio) -> str:
    """
    Save an audio file from Telegram.
    Returns the file path.
    """
    from config import AGI_INBOX_AUDIO
    filename = audio.file_name or f"audio_{audio.file_unique_id}.mp3"
    return await download_telegram_file(bot, audio.file_id, AGI_INBOX_AUDIO, filename)


async def save_video(bot, video) -> str:
    """
    Save a video from Telegram.
    Returns the file path.
    """
    filename = f"video_{video.file_unique_id}.mp4"
    return await download_telegram_file(bot, video.file_id, AGI_INBOX_TELEGRAM, filename)


# ══════════════════════════════════════════════════════════
#  DOCUMENT TEXT EXTRACTION
# ══════════════════════════════════════════════════════════

def extract_text_from_document(filepath: str) -> str | None:
    """
    Extract text content from a document file.
    Supports: .txt, .md, .csv, .json, .log, .py, .js (plain text)
              .pdf (via PyPDF2)
              .docx (via python-docx)

    Returns extracted text, or None if extraction fails/unsupported.
    """
    ext = os.path.splitext(filepath)[1].lower()

    # Plain text files — just read them
    plain_text_exts = {
        ".txt", ".md", ".csv", ".json", ".log", ".py", ".js",
        ".ts", ".html", ".xml", ".yaml", ".yml", ".toml", ".ini",
        ".cfg", ".sh", ".bat", ".ps1", ".sql", ".r", ".m",
    }

    if ext in plain_text_exts:
        return _read_plain_text(filepath)
    elif ext == ".pdf":
        return _extract_pdf(filepath)
    elif ext == ".docx":
        return _extract_docx(filepath)
    else:
        logger.info(f"[FILE] No text extraction for {ext} files")
        return None


def _read_plain_text(filepath: str) -> str | None:
    """Read a plain text file with encoding detection."""
    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
    for enc in encodings:
        try:
            with open(filepath, "r", encoding=enc) as f:
                content = f.read()
            if content.strip():
                # Limit to ~10000 chars to avoid overwhelming agy
                if len(content) > 10000:
                    content = content[:10000] + "\n\n... [truncated — full file available at path]"
                logger.info(f"[FILE] Read {len(content)} chars from {filepath}")
                return content
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None


def _extract_pdf(filepath: str) -> str | None:
    """Extract text from PDF using PyPDF2."""
    try:
        import PyPDF2
    except ImportError:
        logger.warning("[FILE] PyPDF2 not installed — PDF extraction disabled. Install: pip install PyPDF2")
        return None

    try:
        text_parts = []
        with open(filepath, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            total_pages = len(reader.pages)
            logger.info(f"[FILE] PDF has {total_pages} pages")

            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text and page_text.strip():
                    text_parts.append(f"--- Page {i + 1} ---\n{page_text.strip()}")

                # Limit to 20 pages to avoid huge extractions
                if i >= 19:
                    text_parts.append(f"\n... [truncated — {total_pages - 20} more pages]")
                    break

        if text_parts:
            content = "\n\n".join(text_parts)
            # Limit total length
            if len(content) > 15000:
                content = content[:15000] + "\n\n... [truncated — full document at path]"
            logger.info(f"[FILE] Extracted {len(content)} chars from PDF")
            return content
        else:
            logger.info("[FILE] PDF has no extractable text (may be image-based)")
            return None

    except Exception as e:
        logger.error(f"[FILE] PDF extraction failed: {e}", exc_info=True)
        return None


def _extract_docx(filepath: str) -> str | None:
    """Extract text from DOCX using python-docx."""
    try:
        import docx
    except ImportError:
        logger.warning("[FILE] python-docx not installed — DOCX extraction disabled. Install: pip install python-docx")
        return None

    try:
        doc = docx.Document(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

        if paragraphs:
            content = "\n\n".join(paragraphs)
            if len(content) > 15000:
                content = content[:15000] + "\n\n... [truncated — full document at path]"
            logger.info(f"[FILE] Extracted {len(content)} chars from DOCX")
            return content
        else:
            logger.info("[FILE] DOCX has no text content")
            return None

    except Exception as e:
        logger.error(f"[FILE] DOCX extraction failed: {e}", exc_info=True)
        return None


# ══════════════════════════════════════════════════════════
#  VIDEO PROCESSING
# ══════════════════════════════════════════════════════════

def extract_video_audio(video_path: str) -> str | None:
    """
    Extract audio track from a video file using ffmpeg.
    Returns path to the extracted audio WAV file, or None on failure.
    """
    if not os.path.exists(FFMPEG_PATH):
        logger.warning("[FILE] ffmpeg not available for video audio extraction")
        return None

    try:
        import subprocess
        audio_path = os.path.splitext(video_path)[0] + "_audio.wav"

        result = subprocess.run(
            [FFMPEG_PATH, "-i", video_path, "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", "-y", audio_path],
            capture_output=True,
            text=True,
            timeout=120,  # 2 min max
        )

        if result.returncode == 0 and os.path.exists(audio_path):
            size = os.path.getsize(audio_path)
            if size > 100:  # Sanity check — not empty
                logger.info(f"[FILE] Extracted audio: {audio_path} ({size} bytes)")
                return audio_path
            else:
                logger.info("[FILE] Video has no audio track")
                os.remove(audio_path)
                return None
        else:
            logger.warning(f"[FILE] ffmpeg audio extraction failed: {result.stderr[:200]}")
            return None

    except Exception as e:
        logger.error(f"[FILE] Video audio extraction failed: {e}", exc_info=True)
        return None


# ══════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════

def get_file_type(filepath: str) -> str:
    """Determine the type of a file by its extension."""
    ext = os.path.splitext(filepath)[1].lower()
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}
    audio_exts = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus", ".aac", ".wma"}
    video_exts = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".webm"}
    doc_exts = {".pdf", ".doc", ".docx", ".txt", ".md", ".csv", ".xlsx", ".pptx"}

    if ext in image_exts:
        return "image"
    elif ext in audio_exts:
        return "audio"
    elif ext in video_exts:
        return "video"
    elif ext in doc_exts:
        return "document"
    else:
        return "other"


def format_file_size(size_bytes: int) -> str:
    """Format bytes into human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

