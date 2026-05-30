# ============================================================
#  DESKTOP FORMATTER — Rich Output Formatting for Desktop GUI
# ============================================================
#  Formats AI responses for display in the Zilla desktop app.
#  Unlike telegram_formatter.py (which must respect Telegram's
#  strict MarkdownV2/HTML rules and 4096-char limit), the desktop
#  formatter can show:
#  - Full-length responses (no truncation)
#  - Rich markdown (headers, tables, code blocks)
#  - Interactive elements (copy buttons, file links)
#  - Embedded images and file previews
# ============================================================

import re
import os
from typing import Optional


class DesktopFormatter:
    """Formats AI responses for rich desktop display in Zilla GUI."""

    # Regex patterns
    _CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    _FILE_PATH_RE = re.compile(
        r'(?:^|\s)([A-Za-z]:\\[\\\w\-. ]+\.\w+)',
        re.MULTILINE,
    )
    _URL_RE = re.compile(r'https?://[^\s)>]+', re.IGNORECASE)
    _HEADER_RE = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    _BOLD_RE = re.compile(r'\*\*(.+?)\*\*')

    def format(self, raw_text: str, source: str = "desktop") -> dict:
        """
        Format raw AI response for desktop display.
        
        Returns a dict with structured content:
        {
            'text': str,           # Cleaned full text
            'source': str,         # 'desktop' or 'telegram'
            'has_code': bool,
            'code_blocks': [...],  # {language, code}
            'has_files': bool,
            'file_paths': [...],
            'urls': [...],
            'truncated': False,    # Desktop never truncates
        }
        """
        text = self._clean(raw_text)
        code_blocks = self._extract_code_blocks(raw_text)
        file_paths = self._extract_file_paths(raw_text)
        urls = self._extract_urls(raw_text)

        return {
            'text': text,
            'source': source,
            'has_code': len(code_blocks) > 0,
            'code_blocks': code_blocks,
            'has_files': len(file_paths) > 0,
            'file_paths': file_paths,
            'urls': urls,
            'truncated': False,
        }

    def format_text(self, raw_text: str) -> str:
        """Simple text formatting — clean and return."""
        return self._clean(raw_text)

    def _clean(self, text: str) -> str:
        """Clean raw text for desktop display."""
        if not text:
            return ""
        
        # Remove ANSI escape sequences
        text = re.sub(r'\x1b\[[0-9;]*m', '', text)
        
        # Remove chain-of-thought markers
        text = re.sub(r'<(thinking|antml:thinking)>.*?</(thinking|antml:thinking)>', '', text, flags=re.DOTALL)
        
        # Clean up excessive blank lines
        text = re.sub(r'\n{4,}', '\n\n\n', text)
        
        return text.strip()

    def _extract_code_blocks(self, text: str) -> list:
        """Extract fenced code blocks."""
        blocks = []
        for match in self._CODE_BLOCK_RE.finditer(text):
            blocks.append({
                'language': match.group(1) or 'text',
                'code': match.group(2).strip(),
            })
        return blocks

    def _extract_file_paths(self, text: str) -> list:
        """Extract Windows file paths from response."""
        paths = []
        for match in self._FILE_PATH_RE.finditer(text):
            path = match.group(1).strip()
            if os.path.exists(path):
                paths.append(path)
        return paths

    def _extract_urls(self, text: str) -> list:
        """Extract URLs from response."""
        return self._URL_RE.findall(text)


def format_for_desktop(text: str) -> str:
    """Quick formatting function for desktop display."""
    formatter = DesktopFormatter()
    return formatter.format_text(text)


def format_for_source(text: str, source: str) -> str:
    """
    Route formatting based on message source.
    
    - 'desktop' → Full rich text, no truncation
    - 'telegram' → Use telegram_formatter (imported lazily)
    """
    if source == "telegram":
        try:
            from telegram_formatter import format_for_telegram
            formatted, _ = format_for_telegram(text)
            return formatted
        except ImportError:
            pass
    
    return format_for_desktop(text)
