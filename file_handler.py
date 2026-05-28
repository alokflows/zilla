# ============================================================
#  FILE HANDLER — Image & Document Processing
# ============================================================
#  Handles photos, documents, and files sent via Telegram.
#
#  Photos:
#  - Saved to AGI-Brain/Inbox/images
#  - Can be sent to agy for analysis
#
#  Documents:
#  - Saved to AGI-Brain/Inbox/documents
#  - Text files can be summarized by agy
# ============================================================

import os
import logging
from datetime import datetime

from config import (
    AGI_INBOX_IMAGES,
    AGI_INBOX_DOCUMENTS,
    AGI_INBOX_TELEGRAM,
    TELEGRAM_MAX_LENGTH,
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
