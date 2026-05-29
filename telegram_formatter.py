# ============================================================
#  TELEGRAM FORMATTER — Output Formatting for Telegram
# ============================================================
#  Formats agy's raw output for Telegram messages.
#
#  Telegram supports three parse modes:
#  - MarkdownV2  (strict escaping rules)
#  - HTML         (safe subset of HTML tags)
#  - None         (plain text, no formatting)
#
#  This module handles:
#  - Cleaning raw markdown artifacts from agy output
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

# Characters that MUST be escaped in MarkdownV2 (per Telegram docs)
_MDV2_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!"

# Regex for fenced code blocks (``` ... ```)
_CODE_BLOCK_RE = re.compile(
    r"(```[\w]*\n.*?```)",
    re.DOTALL,
)

# Regex for inline code (`...`)
_INLINE_CODE_RE = re.compile(r"(`[^`\n]+`)")

# Regex for markdown headers (## Header, ### Header, etc.)
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# Regex for bold markers (**text**)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# Regex for italic markers (*text* — single asterisks, not inside bold)
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")

# Regex for JSON-like debug blocks { ... } spanning multiple lines
_JSON_BLOCK_RE = re.compile(
    r"^\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*$",
    re.MULTILINE,
)

# Regex for debug / internal log lines
_DEBUG_LINE_RE = re.compile(
    r"^(?:"
    r"\[DEBUG\].*"
    r"|\[TRACE\].*"
    r"|\[INFO\].*"
    r"|DEBUG:.*"
    r"|TRACE:.*"
    r"|>>>.*"
    r")$",
    re.MULTILINE | re.IGNORECASE,
)

# Regex for escaped unicode sequences like \u00e9
_ESCAPED_UNICODE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")

# Regex for Windows absolute paths (e.g. C:\Users\Isha\file.txt)
_WIN_PATH_RE = re.compile(
    r"[A-Za-z]:\\(?:[^\s\\/:*?\"<>|]+\\)*[^\s\\/:*?\"<>|]+\.[\w]+"
)

# Regex for horizontal rules (---, ***, ___)
_HORIZONTAL_RULE_RE = re.compile(r"^[\s]*[-*_]{3,}[\s]*$", re.MULTILINE)

# Regex for markdown link syntax [text](url)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Regex for bullet points that use * instead of -
_STAR_BULLET_RE = re.compile(r"^(\s*)\*\s+", re.MULTILINE)


# ══════════════════════════════════════════════════════════
#  ESCAPE HELPERS
# ══════════════════════════════════════════════════════════

def escape_markdown_v2(text: str) -> str:
    """
    Escape special characters for Telegram MarkdownV2 mode.

    Escapes:  _ * [ ] ( ) ~ ` > # + - = | { } . !

    This performs a RAW escape — every special character gets a
    backslash.  Call this ONLY on plain-text segments that should
    NOT contain any intentional formatting.  Pre-formatted segments
    (bold, code, links) must be assembled separately and should NOT
    be run through this function.

    Args:
        text: Raw plain text to escape.

    Returns:
        Text with all MarkdownV2 special characters escaped.
    """
    escaped = []
    for char in text:
        if char in _MDV2_SPECIAL_CHARS:
            escaped.append(f"\\{char}")
        else:
            escaped.append(char)
    return "".join(escaped)


def escape_html(text: str) -> str:
    """
    Escape characters that are special in HTML.

    Converts:
        &  →  &amp;
        <  →  &lt;
        >  →  &gt;

    Args:
        text: Raw text to escape for HTML parse mode.

    Returns:
        HTML-safe text.
    """
    # Order matters: & must be replaced first
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


# ══════════════════════════════════════════════════════════
#  INTERNAL CLEANING HELPERS
# ══════════════════════════════════════════════════════════

def _decode_escaped_unicode(text: str) -> str:
    """Replace \\uXXXX sequences with actual characters."""
    def _replacer(match: re.Match) -> str:
        try:
            return chr(int(match.group(1), 16))
        except (ValueError, OverflowError):
            return match.group(0)
    return _ESCAPED_UNICODE_RE.sub(_replacer, text)


def _strip_debug_artifacts(text: str) -> str:
    """
    Remove JSON blobs and debug log lines that leak from agy internals.
    Preserves JSON inside code blocks.
    """
    # Pull out code blocks so we don't mangle them
    code_blocks: list[str] = []

    def _save_code(match: re.Match) -> str:
        code_blocks.append(match.group(0))
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

    text = _CODE_BLOCK_RE.sub(_save_code, text)

    # Strip debug lines
    text = _DEBUG_LINE_RE.sub("", text)

    # Strip standalone JSON objects (heuristic: braces on their own line)
    text = _JSON_BLOCK_RE.sub("", text)

    # Restore code blocks
    for idx, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK{idx}\x00", block)

    return text


def _convert_headers(text: str) -> str:
    """
    Convert Markdown headers (## Title) to bold text (**Title**).
    Telegram MarkdownV2 does not support # headers natively.
    """
    def _header_to_bold(match: re.Match) -> str:
        return f"**{match.group(2).strip()}**"
    return _HEADER_RE.sub(_header_to_bold, text)


def _convert_horizontal_rules(text: str) -> str:
    """Replace Markdown horizontal rules (---) with a unicode line."""
    return _HORIZONTAL_RULE_RE.sub("————————————", text)


def _normalize_bullets(text: str) -> str:
    """Convert * bullet points to - bullet points (avoids bold confusion)."""
    return _STAR_BULLET_RE.sub(r"\1• ", text)


def _collapse_blank_lines(text: str) -> str:
    """Collapse 3+ consecutive blank lines down to 2."""
    return re.sub(r"\n{3,}", "\n\n", text)


def _strip_trailing_whitespace(text: str) -> str:
    """Remove trailing whitespace from each line."""
    lines = text.split("\n")
    return "\n".join(line.rstrip() for line in lines)


def _clean_raw_text(text: str) -> str:
    """
    Run the full cleaning pipeline on raw agy output.

    Pipeline order:
    1. Decode escaped unicode
    2. Strip debug/JSON artifacts
    3. Convert headers → bold
    4. Convert horizontal rules
    5. Normalize bullet points
    6. Strip trailing whitespace per line
    7. Collapse excessive blank lines
    8. Final trim
    """
    text = _decode_escaped_unicode(text)
    text = _strip_debug_artifacts(text)
    text = _convert_headers(text)
    text = _convert_horizontal_rules(text)
    text = _normalize_bullets(text)
    text = _strip_trailing_whitespace(text)
    text = _collapse_blank_lines(text)
    text = text.strip()
    return text


# ══════════════════════════════════════════════════════════
#  HTML FORMATTING
# ══════════════════════════════════════════════════════════

def _to_html(text: str) -> str:
    """
    Convert cleaned markdown-ish text into Telegram-safe HTML.

    Handles:
    - Fenced code blocks  →  <pre><code>...</code></pre>
    - Inline code          →  <code>...</code>
    - Bold **text**        →  <b>text</b>
    - Italic *text*        →  <i>text</i>
    - Links [t](url)       →  <a href="url">t</a>
    - Everything else gets HTML-escaped
    """
    # --- Split on code blocks first (they must not be escaped) ---
    segments: list[str] = []
    last_end = 0

    for match in _CODE_BLOCK_RE.finditer(text):
        # Process the text before this code block
        before = text[last_end:match.start()]
        segments.append(("text", before))

        # Extract language hint and code content
        block = match.group(0)
        first_line_end = block.index("\n")
        lang = block[3:first_line_end].strip()
        code_body = block[first_line_end + 1:-3]  # strip ``` delimiters

        if lang:
            segments.append(("code_block", code_body, lang))
        else:
            segments.append(("code_block", code_body, None))

        last_end = match.end()

    # Remaining text after last code block
    segments.append(("text", text[last_end:]))

    # --- Render each segment ---
    html_parts: list[str] = []

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
            chunk = seg[1]
            # Process inline elements within this text chunk
            html_parts.append(_format_inline_html(chunk))

    return "".join(html_parts)


def _format_inline_html(text: str) -> str:
    """
    Format inline markdown elements to HTML in a plain-text segment.
    Handles inline code, bold, italic, and links.
    """
    # Split on inline code to protect it
    parts = _INLINE_CODE_RE.split(text)
    result: list[str] = []

    for i, part in enumerate(parts):
        if i % 2 == 1:
            # This is an inline code match — wrap in <code>
            code_content = part[1:-1]  # strip the backticks
            result.append(f"<code>{escape_html(code_content)}</code>")
        else:
            # Plain text — escape and apply formatting
            chunk = escape_html(part)

            # Bold: **text** → <b>text</b>
            chunk = re.sub(
                r"\*\*(.+?)\*\*",
                r"<b>\1</b>",
                chunk,
            )

            # Italic: *text* → <i>text</i> (not inside bold)
            chunk = re.sub(
                r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)",
                r"<i>\1</i>",
                chunk,
            )

            # Links: [text](url) → <a href="url">text</a>
            # Note: ( ) < > are already HTML-escaped at this point
            chunk = re.sub(
                r"\[([^\]]+)\]\(([^)]+)\)",
                r'<a href="\2">\1</a>',
                chunk,
            )

            result.append(chunk)

    return "".join(result)


# ══════════════════════════════════════════════════════════
#  MARKDOWNV2 FORMATTING
# ══════════════════════════════════════════════════════════

def _to_markdown_v2(text: str) -> str:
    """
    Convert cleaned text into Telegram MarkdownV2 format.

    Strategy:
    1. Extract code blocks and inline code (they have their own escaping rules)
    2. Escape all special chars in plain-text segments
    3. Re-apply bold / italic / link formatting with proper MarkdownV2 syntax
    4. Reassemble with code blocks intact
    """
    # --- Split on fenced code blocks ---
    segments: list[str] = []
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

    # --- Render each segment ---
    mdv2_parts: list[str] = []

    for seg in segments:
        if seg[0] == "code_block":
            # Code blocks in MarkdownV2: ```lang\n...\n```
            # Content inside ``` is NOT escaped (Telegram rule)
            lang = seg[2] or ""
            body = seg[1]
            mdv2_parts.append(f"```{lang}\n{body}```")
        else:
            chunk = seg[1]
            mdv2_parts.append(_format_inline_mdv2(chunk))

    return "".join(mdv2_parts)


def _format_inline_mdv2(text: str) -> str:
    """
    Format a plain-text segment for MarkdownV2.

    Extracts bold, italic, inline code, and links, escapes
    everything else, then reassembles.
    """
    # We process inline code first to protect it
    parts = _INLINE_CODE_RE.split(text)
    result: list[str] = []

    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Inline code — content is NOT escaped in MarkdownV2
            code_content = part[1:-1]
            result.append(f"`{code_content}`")
        else:
            # Plain text — extract formatting, escape the rest
            result.append(_escape_mdv2_with_formatting(part))

    return "".join(result)


def _escape_mdv2_with_formatting(text: str) -> str:
    """
    Escape a plain-text chunk for MarkdownV2 while preserving
    bold (**text**), italic (*text*), and links [text](url).

    We do this by:
    1. Finding all bold/italic/link spans
    2. Escaping the gaps between them
    3. Formatting the spans with proper MarkdownV2 syntax
    """
    # Collect all special spans with their positions
    spans: list[tuple[int, int, str]] = []  # (start, end, replacement)

    # Bold: **text**
    for m in _BOLD_RE.finditer(text):
        inner = escape_markdown_v2(m.group(1))
        spans.append((m.start(), m.end(), f"*{inner}*"))

    # Links: [text](url)
    for m in _MD_LINK_RE.finditer(text):
        link_text = escape_markdown_v2(m.group(1))
        # In MarkdownV2 links, the URL part has special escaping:
        # only ) and \ need escaping inside the parentheses
        url = m.group(2).replace("\\", "\\\\").replace(")", "\\)")
        spans.append((m.start(), m.end(), f"[{link_text}]({url})"))

    # Sort spans by start position
    spans.sort(key=lambda s: s[0])

    # Remove overlapping spans (keep the first one)
    filtered: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, replacement in spans:
        if start >= last_end:
            filtered.append((start, end, replacement))
            last_end = end

    # Build result by escaping gaps and inserting formatted spans
    parts: list[str] = []
    pos = 0
    for start, end, replacement in filtered:
        # Escape the gap before this span
        gap = text[pos:start]
        parts.append(escape_markdown_v2(gap))
        parts.append(replacement)
        pos = end

    # Escape the remaining tail
    parts.append(escape_markdown_v2(text[pos:]))

    return "".join(parts)


# ══════════════════════════════════════════════════════════
#  FILE PATH DETECTION
# ══════════════════════════════════════════════════════════

def detect_file_paths(text: str) -> list[str]:
    """
    Extract Windows absolute file paths from text.

    Looks for paths like C:\\Users\\Isha\\project\\file.py
    that agy mentions when it creates or modifies files.

    Args:
        text: Raw or cleaned agy output.

    Returns:
        De-duplicated list of absolute Windows file paths found.
    """
    matches = _WIN_PATH_RE.findall(text)

    # De-duplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for path in matches:
        normalized = path.replace("/", "\\")
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)

    if unique:
        logger.debug(f"[FORMATTER] Detected {len(unique)} file path(s): {unique}")

    return unique


# ══════════════════════════════════════════════════════════
#  TRUNCATION
# ══════════════════════════════════════════════════════════

_TRUNCATION_INDICATOR = "\n\n… *(truncated)*"


def truncate_for_telegram(text: str, max_length: int = 4000) -> str:
    """
    Intelligently truncate text for Telegram's 4096-character limit.

    Strategy:
    1. If text fits, return as-is.
    2. Try to break at a paragraph boundary (double newline).
    3. Fall back to breaking at a single newline.
    4. Last resort: hard cut at max_length.
    5. Append a '… (truncated)' indicator.

    Args:
        text:       The text to truncate.
        max_length: Maximum allowed length (default 4000 to leave
                    room for the truncation indicator and safety margin).

    Returns:
        Text that is guaranteed to be ≤ max_length characters
        (including the truncation indicator when applied).
    """
    if len(text) <= max_length:
        return text

    indicator = _TRUNCATION_INDICATOR
    budget = max_length - len(indicator)

    if budget <= 0:
        # Pathologically small max_length — just hard-cut
        return text[:max_length]

    truncated = text[:budget]

    # Strategy 1: Break at paragraph boundary (double newline)
    para_break = truncated.rfind("\n\n")
    if para_break > budget // 3:
        truncated = truncated[:para_break]
        logger.debug(
            f"[FORMATTER] Truncated at paragraph boundary "
            f"(pos {para_break}/{len(text)})"
        )
        return truncated.rstrip() + indicator

    # Strategy 2: Break at single newline
    line_break = truncated.rfind("\n")
    if line_break > budget // 3:
        truncated = truncated[:line_break]
        logger.debug(
            f"[FORMATTER] Truncated at line boundary "
            f"(pos {line_break}/{len(text)})"
        )
        return truncated.rstrip() + indicator

    # Strategy 3: Hard cut (last resort)
    logger.debug(
        f"[FORMATTER] Hard truncation at {budget}/{len(text)} chars"
    )
    return truncated.rstrip() + indicator


# ══════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════

def format_for_telegram(
    text: str,
    parse_mode: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """
    Format raw agy output for sending via Telegram.

    This is the main entry point.  It cleans the text, then converts
    it to the requested parse_mode (or auto-detects the best one).

    Pipeline:
    1. Clean raw text  (headers, debug, unicode, whitespace)
    2. Convert to the target format:
       - 'MarkdownV2' → strict MarkdownV2 with escaped specials
       - 'HTML'        → safe HTML subset
       - None          → try HTML first (most reliable), fall back to plain
    3. Truncate if needed

    Args:
        text:       Raw agy output.
        parse_mode: Desired Telegram parse mode.
                    One of 'MarkdownV2', 'HTML', or None.
                    When None, we auto-select HTML (safest for rich text).

    Returns:
        (formatted_text, parse_mode) tuple.
        parse_mode will be 'MarkdownV2', 'HTML', or None (plain text).
    """
    if not text or not text.strip():
        return ("No response.", None)

    logger.debug(f"[FORMATTER] Input: {len(text)} chars, parse_mode={parse_mode}")

    # --- Step 1: Clean ---
    cleaned = _clean_raw_text(text)

    if not cleaned:
        return ("No response.", None)

    # --- Step 2: Detect if text has any formatting worth preserving ---
    has_formatting = bool(
        _BOLD_RE.search(cleaned)
        or _CODE_BLOCK_RE.search(cleaned)
        or _INLINE_CODE_RE.search(cleaned)
        or _MD_LINK_RE.search(cleaned)
    )

    # --- Step 3: Format based on parse_mode ---
    if parse_mode == "MarkdownV2":
        try:
            formatted = _to_markdown_v2(cleaned)
            formatted = truncate_for_telegram(formatted)
            logger.debug(
                f"[FORMATTER] Output: {len(formatted)} chars as MarkdownV2"
            )
            return (formatted, "MarkdownV2")
        except Exception as e:
            logger.warning(
                f"[FORMATTER] MarkdownV2 formatting failed, "
                f"falling back to plain: {e}"
            )
            plain = _strip_all_markdown(cleaned)
            return (truncate_for_telegram(plain), None)

    elif parse_mode == "HTML":
        try:
            formatted = _to_html(cleaned)
            formatted = truncate_for_telegram(formatted)
            logger.debug(
                f"[FORMATTER] Output: {len(formatted)} chars as HTML"
            )
            return (formatted, "HTML")
        except Exception as e:
            logger.warning(
                f"[FORMATTER] HTML formatting failed, "
                f"falling back to plain: {e}"
            )
            plain = _strip_all_markdown(cleaned)
            return (truncate_for_telegram(plain), None)

    else:
        # Auto-select: use HTML if there's formatting, plain text otherwise
        if has_formatting:
            try:
                formatted = _to_html(cleaned)
                formatted = truncate_for_telegram(formatted)
                logger.debug(
                    f"[FORMATTER] Output: {len(formatted)} chars as HTML (auto)"
                )
                return (formatted, "HTML")
            except Exception as e:
                logger.warning(
                    f"[FORMATTER] Auto HTML failed, falling back to plain: {e}"
                )

        # Plain text — strip markdown syntax
        plain = _strip_all_markdown(cleaned)
        plain = truncate_for_telegram(plain)
        logger.debug(
            f"[FORMATTER] Output: {len(plain)} chars as plain text"
        )
        return (plain, None)


# ══════════════════════════════════════════════════════════
#  PLAIN TEXT FALLBACK
# ══════════════════════════════════════════════════════════

def _strip_all_markdown(text: str) -> str:
    """
    Remove all markdown formatting to produce clean plain text.
    Used as a fallback when formatted output fails.
    """
    # Remove fenced code block delimiters but keep content
    text = re.sub(r"```[\w]*\n", "", text)
    text = text.replace("```", "")

    # Remove inline code backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Remove bold markers
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)

    # Remove italic markers
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)

    # Convert links to "text (url)" format
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)

    return text
