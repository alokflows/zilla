# ============================================================
#  FORMATTER — Output Formatting for Telegram
# ============================================================
#  Formats CLI raw output for Telegram messages.
#
#  Handles:
#  - Cleaning raw markdown artifacts
#  - Converting headers to bold text
#  - Preserving code blocks
#  - Stripping JSON/debug artifacts
#  - Escaping special characters per parse mode
#  - Detecting file paths in output
#  - Truncating to Telegram's 4096-char limit
# ============================================================

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════

_MDV2_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!\\"

_CODE_BLOCK_RE = re.compile(r"(```[\w]*\n.*?```)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"(`[^`\n]+`)")
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_JSON_BLOCK_RE = re.compile(r"^\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*$", re.MULTILINE)
_DEBUG_LINE_RE = re.compile(
    r"^(?:\[DEBUG\].*|\[TRACE\].*|\[INFO\].*|DEBUG:.*|TRACE:.*|>>>.*)" r"$",
    re.MULTILINE | re.IGNORECASE,
)
_ESCAPED_UNICODE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\s\\/:*?\"<>|]+\\)*[^\s\\/:*?\"<>|]+\.[\w]+")
_HORIZONTAL_RULE_RE = re.compile(r"^[\s]*[-*_]{3,}[\s]*$", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_STAR_BULLET_RE = re.compile(r"^(\s*)\*\s+", re.MULTILINE)
_DASH_BULLET_RE = re.compile(r"^(\s*)-\s+", re.MULTILINE)


# ══════════════════════════════════════════════════════════
#  ESCAPE HELPERS
# ══════════════════════════════════════════════════════════

def escape_markdown_v2(text: str) -> str:
    escaped = []
    for char in text:
        if char in _MDV2_SPECIAL_CHARS:
            escaped.append(f"\\{char}")
        else:
            escaped.append(char)
    return "".join(escaped)


def escape_html(text: str) -> str:
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    return text


# ══════════════════════════════════════════════════════════
#  INTERNAL CLEANING
# ══════════════════════════════════════════════════════════

def _decode_escaped_unicode(text: str) -> str:
    def _replacer(match):
        try:
            return chr(int(match.group(1), 16))
        except (ValueError, OverflowError):
            return match.group(0)
    return _ESCAPED_UNICODE_RE.sub(_replacer, text)


def _strip_debug_artifacts(text: str) -> str:
    code_blocks = []

    def _save_code(match):
        code_blocks.append(match.group(0))
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

    text = _CODE_BLOCK_RE.sub(_save_code, text)
    text = _DEBUG_LINE_RE.sub("", text)
    text = _JSON_BLOCK_RE.sub("", text)
    for idx, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK{idx}\x00", block)
    return text


def _convert_headers(text: str) -> str:
    def _header_to_bold(match):
        return f"**{match.group(2).strip()}**"
    return _HEADER_RE.sub(_header_to_bold, text)


def _convert_horizontal_rules(text: str) -> str:
    return _HORIZONTAL_RULE_RE.sub("————————————", text)


def _normalize_bullets(text: str) -> str:
    code_blocks = []

    def _save_code(match):
        code_blocks.append(match.group(0))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = _CODE_BLOCK_RE.sub(_save_code, text)
    text = _STAR_BULLET_RE.sub(r"\1• ", text)
    text = _DASH_BULLET_RE.sub(r"\1• ", text)
    for idx, block in enumerate(code_blocks):
        text = text.replace(f"\x00CB{idx}\x00", block)
    return text


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def _clean_raw_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[\ud800-\udfff]", "", text)
    text = _decode_escaped_unicode(text)
    text = _strip_debug_artifacts(text)
    text = _convert_headers(text)
    text = _convert_horizontal_rules(text)
    text = _normalize_bullets(text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _collapse_blank_lines(text)
    return text.strip()


# ══════════════════════════════════════════════════════════
#  HTML FORMATTING
# ══════════════════════════════════════════════════════════

def _to_html(text: str) -> str:
    segments = []
    last_end = 0

    for match in _CODE_BLOCK_RE.finditer(text):
        before = text[last_end:match.start()]
        segments.append(("text", before))

        block = match.group(0)
        first_line_end = block.index("\n")
        lang = block[3:first_line_end].strip()
        code_body = block[first_line_end + 1:-3]

        segments.append(("code_block", code_body, lang or None))
        last_end = match.end()

    segments.append(("text", text[last_end:]))

    html_parts = []
    for seg in segments:
        if seg[0] == "code_block":
            code_body = escape_html(seg[1])
            lang = seg[2]
            if lang:
                html_parts.append(
                    f'<pre><code class="language-{escape_html(lang)}">'
                    f"{code_body}</code></pre>"
                )
            else:
                html_parts.append(f"<pre><code>{code_body}</code></pre>")
        else:
            html_parts.append(_format_inline_html(seg[1]))
    return "".join(html_parts)


def _format_inline_html(text: str) -> str:
    parts = _INLINE_CODE_RE.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            code_content = part[1:-1]
            result.append(f"<code>{escape_html(code_content)}</code>")
        else:
            chunk = escape_html(part)
            chunk = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", chunk)
            chunk = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", chunk)
            chunk = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', chunk)
            result.append(chunk)
    return "".join(result)


# ══════════════════════════════════════════════════════════
#  MARKDOWNV2 FORMATTING
# ══════════════════════════════════════════════════════════

def _to_markdown_v2(text: str) -> str:
    segments = []
    last_end = 0

    for match in _CODE_BLOCK_RE.finditer(text):
        before = text[last_end:match.start()]
        segments.append(("text", before))
        block = match.group(0)
        first_newline = block.index("\n")
        lang = block[3:first_newline].strip()
        code_body = block[first_newline + 1:-3]
        segments.append(("code_block", code_body, lang))
        last_end = match.end()

    segments.append(("text", text[last_end:]))

    mdv2_parts = []
    for seg in segments:
        if seg[0] == "code_block":
            lang = seg[2] or ""
            body = seg[1]
            mdv2_parts.append(f"```{lang}\n{body}```")
        else:
            mdv2_parts.append(_format_inline_mdv2(seg[1]))
    return "".join(mdv2_parts)


def _format_inline_mdv2(text: str) -> str:
    parts = _INLINE_CODE_RE.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            code_content = part[1:-1]
            result.append(f"`{code_content}`")
        else:
            result.append(_escape_mdv2_with_formatting(part))
    return "".join(result)


def _escape_mdv2_with_formatting(text: str) -> str:
    spans = []
    for m in _BOLD_RE.finditer(text):
        inner = escape_markdown_v2(m.group(1))
        spans.append((m.start(), m.end(), f"*{inner}*"))
    for m in _MD_LINK_RE.finditer(text):
        link_text = escape_markdown_v2(m.group(1))
        url = m.group(2).replace("\\", "\\\\").replace(")", "\\)")
        spans.append((m.start(), m.end(), f"[{link_text}]({url})"))

    spans.sort(key=lambda s: s[0])
    filtered = []
    last_end = -1
    for start, end, replacement in spans:
        if start >= last_end:
            filtered.append((start, end, replacement))
            last_end = end

    parts = []
    pos = 0
    for start, end, replacement in filtered:
        parts.append(escape_markdown_v2(text[pos:start]))
        parts.append(replacement)
        pos = end
    parts.append(escape_markdown_v2(text[pos:]))
    return "".join(parts)


# ══════════════════════════════════════════════════════════
#  FILE PATH DETECTION
# ══════════════════════════════════════════════════════════

def detect_file_paths(text: str) -> list[str]:
    """Extract Windows file paths from response text."""
    import os
    paths = []

    # Quoted paths
    for match in re.findall(r'["\']([A-Z]:[\\/][^"\']+?)["\']', text, re.IGNORECASE):
        paths.append(match)

    # Backtick paths
    for match in re.findall(r'`([A-Z]:[\\/][^`]+?)`', text, re.IGNORECASE):
        if match not in paths:
            paths.append(match)

    # "saved to/at" patterns
    for match in re.findall(
        r'(?:saved|created|written|output|generated|exported|stored|placed|available)'
        r'\s+(?:to|at|in|as)\s+([A-Z]:[\\/][^\s\n\r,;]+)', text, re.IGNORECASE
    ):
        clean = match.rstrip(".,;:)'\"]}*")
        if clean not in paths:
            paths.append(clean)

    # Unquoted paths
    for match in re.findall(r'[A-Z]:[\\/](?:[^\s<>"|?*\n`]+[\\/])*[^\s<>"|?*\n`]+', text, re.IGNORECASE):
        match = match.rstrip(".,;:)'\"]}*")
        if match not in paths:
            paths.append(match)

    # Validate
    valid = []
    for p in paths:
        normalized = os.path.normpath(p)
        if os.path.isfile(normalized) and normalized not in valid:
            valid.append(normalized)
    return valid


# ══════════════════════════════════════════════════════════
#  TRUNCATION
# ══════════════════════════════════════════════════════════

_TRUNCATION_INDICATOR = "\n\n… *(truncated)*"


def truncate_for_telegram(text: str, max_length: int = 4000) -> str:
    if len(text) <= max_length:
        return text

    budget = max_length - len(_TRUNCATION_INDICATOR)
    if budget <= 0:
        return text[:max_length]

    truncated = text[:budget]

    para_break = truncated.rfind("\n\n")
    if para_break > budget // 3:
        return truncated[:para_break].rstrip() + _TRUNCATION_INDICATOR

    line_break = truncated.rfind("\n")
    if line_break > budget // 3:
        return truncated[:line_break].rstrip() + _TRUNCATION_INDICATOR

    return truncated.rstrip() + _TRUNCATION_INDICATOR


# ══════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════

def format_for_telegram(text: str, parse_mode: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Format raw CLI output for Telegram. Returns (formatted_text, parse_mode)."""
    if not text or not text.strip():
        return ("No response.", None)

    cleaned = _clean_raw_text(text)
    if not cleaned:
        return ("No response.", None)

    cleaned = truncate_for_telegram(cleaned)

    has_formatting = bool(
        _BOLD_RE.search(cleaned)
        or _CODE_BLOCK_RE.search(cleaned)
        or _INLINE_CODE_RE.search(cleaned)
        or _MD_LINK_RE.search(cleaned)
    )

    if parse_mode == "MarkdownV2":
        try:
            return (_to_markdown_v2(cleaned), "MarkdownV2")
        except Exception:
            return (_strip_all_markdown(cleaned), None)

    elif parse_mode == "HTML":
        try:
            return (_to_html(cleaned), "HTML")
        except Exception:
            return (_strip_all_markdown(cleaned), None)

    else:
        if has_formatting:
            try:
                return (_to_html(cleaned), "HTML")
            except Exception:
                pass
        return (_strip_all_markdown(cleaned), None)


# ══════════════════════════════════════════════════════════
#  PLAIN TEXT FALLBACK
# ══════════════════════════════════════════════════════════

def _strip_all_markdown(text: str) -> str:
    text = re.sub(r"```[\w]*\n", "", text)
    text = text.replace("```", "")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text
