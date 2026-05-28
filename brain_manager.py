# ============================================================
#  BRAIN MANAGER — AGI-Brain Knowledge Management
# ============================================================
#  Karpathy-inspired file system for knowledge management.
#
#  Inbox → Process → Organize → Knowledge Base
#
#  Files dropped into Inbox get automatically processed:
#  - Audio → transcribed → saved as text
#  - Images → described → organized
#  - Documents → summarized → organized
# ============================================================

import os
import shutil
import logging
from datetime import datetime

from config import (
    AGI_BRAIN_DIR,
    AGI_INBOX_DIR,
    AGI_INBOX_IMAGES,
    AGI_INBOX_AUDIO,
    AGI_INBOX_DOCUMENTS,
    AGI_INBOX_TELEGRAM,
    AGI_KNOWLEDGE_DIR,
    AGI_KNOWLEDGE_NOTES,
    AGI_KNOWLEDGE_TRANSCRIPTS,
    AGI_KNOWLEDGE_SUMMARIES,
    AGI_KNOWLEDGE_RESEARCH,
    AGI_PROJECTS_DIR,
    AGI_LOGS_DIR,
)

logger = logging.getLogger(__name__)


def ensure_brain_structure():
    """Create the full AGI-Brain directory structure if it doesn't exist."""
    dirs = [
        AGI_INBOX_IMAGES,
        AGI_INBOX_AUDIO,
        AGI_INBOX_DOCUMENTS,
        AGI_INBOX_TELEGRAM,
        AGI_KNOWLEDGE_NOTES,
        AGI_KNOWLEDGE_TRANSCRIPTS,
        AGI_KNOWLEDGE_SUMMARIES,
        AGI_KNOWLEDGE_RESEARCH,
        AGI_PROJECTS_DIR,
        AGI_LOGS_DIR,
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    logger.info("[BRAIN] Directory structure verified.")


def save_to_inbox(data: bytes, filename: str, category: str = "telegram") -> str:
    """
    Save raw bytes to the appropriate inbox folder.
    Returns the full path to the saved file.
    """
    # Map category to folder
    folder_map = {
        "telegram": AGI_INBOX_TELEGRAM,
        "images": AGI_INBOX_IMAGES,
        "audio": AGI_INBOX_AUDIO,
        "documents": AGI_INBOX_DOCUMENTS,
    }
    folder = folder_map.get(category, AGI_INBOX_TELEGRAM)

    # Add timestamp to avoid collisions
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name, ext = os.path.splitext(filename)
    unique_name = f"{ts}_{name}{ext}"
    filepath = os.path.join(folder, unique_name)

    with open(filepath, "wb") as f:
        f.write(data)

    logger.info(f"[BRAIN] Saved to inbox: {filepath}")
    return filepath


def save_transcript(text: str, source_filename: str) -> str:
    """Save a transcription result to the Knowledge/transcripts folder."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = os.path.splitext(source_filename)[0]
    filepath = os.path.join(AGI_KNOWLEDGE_TRANSCRIPTS, f"{ts}_{name}.txt")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Transcript of: {source_filename}\n")
        f.write(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(text)

    logger.info(f"[BRAIN] Transcript saved: {filepath}")
    return filepath


def save_note(text: str, title: str = None) -> str:
    """Save a text note to the Knowledge/notes folder."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    title_slug = (title or "note").replace(" ", "_")[:50]
    filepath = os.path.join(AGI_KNOWLEDGE_NOTES, f"{ts}_{title_slug}.md")

    with open(filepath, "w", encoding="utf-8") as f:
        if title:
            f.write(f"# {title}\n\n")
        f.write(f"*Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write(text)

    logger.info(f"[BRAIN] Note saved: {filepath}")
    return filepath


def save_summary(text: str, source: str = "unknown") -> str:
    """Save an AI-generated summary to the Knowledge/summaries folder."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(AGI_KNOWLEDGE_SUMMARIES, f"{ts}_summary.md")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Summary\n")
        f.write(f"*Source: {source}*\n")
        f.write(f"*Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write(text)

    logger.info(f"[BRAIN] Summary saved: {filepath}")
    return filepath


def save_research(text: str, query: str) -> str:
    """Save web research results to the Knowledge/research folder."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    query_slug = query.replace(" ", "_")[:50]
    filepath = os.path.join(AGI_KNOWLEDGE_RESEARCH, f"{ts}_{query_slug}.md")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Research: {query}\n")
        f.write(f"*Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write(text)

    logger.info(f"[BRAIN] Research saved: {filepath}")
    return filepath


def get_brain_stats() -> dict:
    """Get statistics about the AGI-Brain."""
    stats = {
        "inbox": {"images": 0, "audio": 0, "documents": 0, "telegram": 0},
        "knowledge": {"notes": 0, "transcripts": 0, "summaries": 0, "research": 0},
        "projects": 0,
    }

    # Count inbox items
    for cat, folder in [
        ("images", AGI_INBOX_IMAGES),
        ("audio", AGI_INBOX_AUDIO),
        ("documents", AGI_INBOX_DOCUMENTS),
        ("telegram", AGI_INBOX_TELEGRAM),
    ]:
        try:
            stats["inbox"][cat] = len(
                [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
            )
        except Exception:
            pass

    # Count knowledge items
    for cat, folder in [
        ("notes", AGI_KNOWLEDGE_NOTES),
        ("transcripts", AGI_KNOWLEDGE_TRANSCRIPTS),
        ("summaries", AGI_KNOWLEDGE_SUMMARIES),
        ("research", AGI_KNOWLEDGE_RESEARCH),
    ]:
        try:
            stats["knowledge"][cat] = len(
                [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
            )
        except Exception:
            pass

    # Count projects
    try:
        stats["projects"] = len(
            [d for d in os.listdir(AGI_PROJECTS_DIR) if os.path.isdir(os.path.join(AGI_PROJECTS_DIR, d))]
        )
    except Exception:
        pass

    return stats


def get_inbox_pending() -> list[dict]:
    """List all pending items in the inbox."""
    pending = []
    for category, folder in [
        ("images", AGI_INBOX_IMAGES),
        ("audio", AGI_INBOX_AUDIO),
        ("documents", AGI_INBOX_DOCUMENTS),
        ("telegram", AGI_INBOX_TELEGRAM),
    ]:
        try:
            for f in os.listdir(folder):
                filepath = os.path.join(folder, f)
                if os.path.isfile(filepath):
                    pending.append({
                        "name": f,
                        "category": category,
                        "path": filepath,
                        "size": os.path.getsize(filepath),
                    })
        except Exception:
            pass

    return pending


def move_to_knowledge(source_path: str, dest_folder: str, new_name: str = None) -> str:
    """Move a processed file from inbox to knowledge base."""
    filename = new_name or os.path.basename(source_path)
    dest_path = os.path.join(dest_folder, filename)
    shutil.move(source_path, dest_path)
    logger.info(f"[BRAIN] Moved {source_path} → {dest_path}")
    return dest_path
