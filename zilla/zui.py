"""
Phase U1 — THE ZUI PROTOCOL (PLAN.md §7/U1).

The agent should be able to answer with *interface*, not just text — cards,
tables, tappable next steps, real contact cards. Formatting is never
hard-coded per feature and never left to raw model output:

    the agent decides WHEN and WHAT · Zilla's code decides HOW and enforces LIMITS

The agent embeds at most two fenced blocks in a reply:

    ```zui
    {"kind": "buttons", "items": [
      {"label": "Book the ticket", "say": "book the 6pm ticket"},
      {"label": "Open site", "url": "https://example.com"}]}
    ```

This module is the deterministic half: extraction, schema validation, caps,
and rendering to plain text/HTML. It imports nothing from Telegram — a
frontend maps the validated blocks onto its own widgets (bot.py renders
`buttons` as an inline keyboard, `contacts` via `send_contact`, `location`
via `send_venue`).

Two rules hold everywhere in here:

1. **A bad block never costs the reply.** Invalid JSON, an unknown kind, a
   `javascript:` URL, an over-long table — the block is dropped, the text
   still delivers (P4).
2. **A block can only ever surface what Zilla already knows.** A phone
   number comes from the entity page's `phone::` attribute, never from the
   model's own typing; a `say` tap replays through the normal turn pipeline
   as the tapping owner, never as anyone else.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time

logger = logging.getLogger(__name__)

# ── Caps (PLAN.md §7/U1 step 3) ────────────────────────────
MAX_BLOCKS = 2
MAX_BUTTONS = 8
MAX_LABEL = 32
MAX_TABLE_COLS = 8
MAX_TABLE_ROWS = 20
MAX_FIELDS = 12
MAX_CONTACTS = 8
MAX_TEXT = 200          # any single title/subtitle/cell/value
MAX_SAY = 400           # a say-verb payload is a real user message

KINDS = ("card", "table", "contacts", "buttons", "location")
VERBS = ("say", "url", "copy")

# Phone-screen width, in monospace characters, before a table degrades to
# field-per-line. Telegram's mobile <pre> comfortably fits ~34.
TABLE_WIDTH_BUDGET = 34

_BLOCK_RE = re.compile(r"^[ \t]*```[ \t]*zui[ \t]*\n(.*?)^[ \t]*```[ \t]*$",
                       re.DOTALL | re.MULTILINE | re.IGNORECASE)


# ══════════════════════════════════════════════════════════
#  EXTRACTION + VALIDATION
# ══════════════════════════════════════════════════════════

def _clean_text(value, limit: int = MAX_TEXT) -> str:
    """One line, length-capped, control characters gone. Everything the
    model can put on a surface goes through here."""
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = "".join(ch for ch in text if ch >= " " or ch == "\t").strip()
    return text[:limit]


def _safe_url(value) -> str:
    """http/https only — the same guard formatter._safe_href applies to
    links in prose, applied to button targets (a tg:// or javascript: URL
    rendered as a tappable button is an account-hijack / spoofing vector)."""
    url = _clean_text(value, 500)
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return ""
    if any(c in url for c in ('"', "'", "<", ">", " ")):
        return ""
    return url


def _validate_buttons(obj: dict) -> dict | None:
    items = obj.get("items")
    if not isinstance(items, list):
        return None
    out = []
    for raw in items[:MAX_BUTTONS]:
        if not isinstance(raw, dict):
            continue
        label = _clean_text(raw.get("label"), MAX_LABEL)
        if not label:
            continue
        # Exactly one verb per button; unknown verbs are dropped entirely
        # (no silent fallback to something tappable).
        present = [v for v in VERBS if raw.get(v) not in (None, "")]
        if len(present) != 1:
            continue
        verb = present[0]
        if verb == "url":
            value = _safe_url(raw.get("url"))
        elif verb == "say":
            value = _clean_text(raw.get("say"), MAX_SAY)
        else:
            value = _clean_text(raw.get("copy"), MAX_SAY)
        if not value:
            continue
        out.append({"label": label, "verb": verb, "value": value})
    if not out:
        return None
    return {"kind": "buttons", "items": out}


def _validate_card(obj: dict) -> dict | None:
    title = _clean_text(obj.get("title"))
    if not title:
        return None
    fields = []
    raw_fields = obj.get("fields")
    if isinstance(raw_fields, list):
        for raw in raw_fields[:MAX_FIELDS]:
            if isinstance(raw, dict):
                label = _clean_text(raw.get("label"), MAX_LABEL)
                value = _clean_text(raw.get("value"))
            elif isinstance(raw, (list, tuple)) and len(raw) == 2:
                label, value = _clean_text(raw[0], MAX_LABEL), _clean_text(raw[1])
            else:
                continue
            if label or value:
                fields.append({"label": label, "value": value})
    return {"kind": "card", "title": title,
            "subtitle": _clean_text(obj.get("subtitle")),
            "fields": fields, "footer": _clean_text(obj.get("footer"))}


def _validate_table(obj: dict) -> dict | None:
    headers = obj.get("headers")
    rows = obj.get("rows")
    if not isinstance(headers, list) or not isinstance(rows, list):
        return None
    headers = [_clean_text(h, MAX_LABEL) for h in headers[:MAX_TABLE_COLS]]
    if not any(headers):
        return None
    width = len(headers)
    out_rows = []
    for raw in rows[:MAX_TABLE_ROWS]:
        if not isinstance(raw, (list, tuple)):
            continue
        cells = [_clean_text(c, 40) for c in list(raw)[:width]]
        cells += [""] * (width - len(cells))
        out_rows.append(cells)
    if not out_rows:
        return None
    return {"kind": "table", "headers": headers, "rows": out_rows,
            "title": _clean_text(obj.get("title"))}


def _validate_contacts(obj: dict) -> dict | None:
    """The model may only NAME people — it never supplies the number. The
    frontend resolves each name against the graph (resolve_contacts below);
    an unresolvable name simply doesn't become a card."""
    items = obj.get("items")
    if not isinstance(items, list):
        return None
    names = []
    for raw in items[:MAX_CONTACTS]:
        if isinstance(raw, dict):
            name = _clean_text(raw.get("name") or raw.get("ref"), MAX_LABEL)
        else:
            name = _clean_text(raw, MAX_LABEL)
        if name:
            names.append(name)
    if not names:
        return None
    return {"kind": "contacts", "items": names}


def _validate_location(obj: dict) -> dict | None:
    """Either explicit coordinates, or a place entity to resolve from the
    graph (same rule as contacts: the model names, Zilla resolves)."""
    title = _clean_text(obj.get("title") or obj.get("name"))
    lat, lon = obj.get("lat"), obj.get("lon")
    if lat is not None and lon is not None:
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
            return None
        return {"kind": "location", "title": title or "Location",
                "address": _clean_text(obj.get("address")),
                "lat": lat_f, "lon": lon_f}
    place = _clean_text(obj.get("place") or obj.get("ref"), MAX_LABEL)
    if not place:
        return None
    return {"kind": "location", "title": title, "address": "",
            "place": place, "lat": None, "lon": None}


_VALIDATORS = {
    "card": _validate_card,
    "table": _validate_table,
    "contacts": _validate_contacts,
    "buttons": _validate_buttons,
    "location": _validate_location,
}


def validate(obj) -> dict | None:
    """One parsed JSON object → a normalized block, or None if it doesn't
    survive the schema, the caps, or the whitelists. Never raises."""
    if not isinstance(obj, dict):
        return None
    kind = str(obj.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return None
    try:
        return _VALIDATORS[kind](obj)
    except Exception as e:  # a malformed value must never break a reply
        logger.debug(f"[ZUI] {kind} block rejected: {e}")
        return None


def extract(text: str) -> tuple[str, list[dict], int]:
    """Split a reply into (clean_text, blocks, dropped).

    Every ```zui fence is removed from the text whether or not it validated
    — the owner never sees the raw protocol. `dropped` counts blocks that
    were stripped without becoming a widget (logged, not shown: a bad card
    is Zilla's problem, not something to explain to the owner mid-answer)."""
    if not text or "```" not in text or "zui" not in text.lower():
        return text, [], 0

    blocks: list[dict] = []
    dropped = 0

    def _take(match: re.Match) -> str:
        nonlocal dropped
        if len(blocks) >= MAX_BLOCKS:
            dropped += 1
            return ""
        try:
            parsed = json.loads(match.group(1))
        except (ValueError, TypeError):
            dropped += 1
            return ""
        block = validate(parsed)
        if block is None:
            dropped += 1
            return ""
        blocks.append(block)
        return ""

    clean = _BLOCK_RE.sub(_take, text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, blocks, dropped


# ══════════════════════════════════════════════════════════
#  RENDERING (text/HTML — frontend-agnostic)
# ══════════════════════════════════════════════════════════

def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_card(block: dict) -> str:
    """STYLE.md shape: bold title, plain subtitle, aligned label/value
    lines, italic footer. One block, no decoration for decoration's sake."""
    lines = [f"<b>{_esc(block['title'])}</b>"]
    if block.get("subtitle"):
        lines.append(_esc(block["subtitle"]))
    if block.get("fields"):
        lines.append("")
        for field in block["fields"]:
            label, value = _esc(field["label"]), _esc(field["value"])
            lines.append(f"{label} — {value}" if label and value else (label or value))
    if block.get("footer"):
        lines.append("")
        lines.append(f"<i>{_esc(block['footer'])}</i>")
    return "\n".join(lines)


def _is_numeric(cell: str) -> bool:
    stripped = cell.replace(",", "").replace("%", "").replace("₹", "").strip()
    if not stripped:
        return False
    try:
        float(stripped)
        return True
    except ValueError:
        return False


def render_table(block: dict, width_budget: int = TABLE_WIDTH_BUDGET) -> str:
    """Monospace table with per-column alignment (numbers right, text
    left). Past the phone-width budget it degrades to field-per-line
    records rather than wrapping into an unreadable mess."""
    headers, rows = block["headers"], block["rows"]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    total = sum(widths) + 2 * (len(widths) - 1)

    title = f"<b>{_esc(block['title'])}</b>\n" if block.get("title") else ""

    if total > width_budget:
        parts = []
        for row in rows:
            record = [f"{_esc(headers[i])}: {_esc(cell)}"
                      for i, cell in enumerate(row) if cell]
            if record:
                parts.append("\n".join(record))
        return title + "\n\n".join(parts)

    numeric = [all(_is_numeric(row[i]) for row in rows if row[i])
               and any(row[i] for row in rows) for i in range(len(headers))]

    def _fmt(cells: list[str]) -> str:
        out = []
        for i, cell in enumerate(cells):
            out.append(cell.rjust(widths[i]) if numeric[i] else cell.ljust(widths[i]))
        return "  ".join(out).rstrip()

    lines = [_fmt(headers), _fmt(["-" * w for w in widths])]
    lines += [_fmt(row) for row in rows]
    return title + "<pre>" + _esc("\n".join(lines)) + "</pre>"


def render_text(block: dict) -> str:
    """The text/HTML part of a block, if it has one. `buttons`, `contacts`
    and `location` are pure widgets — the frontend renders those itself."""
    if block["kind"] == "card":
        return render_card(block)
    if block["kind"] == "table":
        return render_table(block)
    return ""


# ══════════════════════════════════════════════════════════
#  GRAPH RESOLUTION  (contacts / location — Zilla supplies the data)
# ══════════════════════════════════════════════════════════

def _page_attrs(db, mem_dir: str, name: str) -> tuple[dict, str] | None:
    """(attrs, title) for a named graph entity, or None. Reuses K5's page
    reader — attrs live in the page, not the nodes table."""
    try:
        from zilla import relay as _relay
        node_id = db.graph_alias_lookup(name)
        node = db.graph_node_get(node_id) if node_id is not None else None
        if node is None:
            return None
        return _relay._page_attrs(mem_dir, node), (node.get("title") or name)
    except Exception as e:
        logger.debug(f"[ZUI] entity lookup failed for {name!r}: {e}")
        return None


_PHONE_OK = re.compile(r"^\+?[0-9][0-9 ()\-]{5,24}$")


def resolve_contacts(db, names: list[str], mem_dir: str) -> list[dict]:
    """Names → [{name, phone}] using each entity page's `phone::` attribute.
    A name with no page, or a page with no plausible phone number, simply
    yields nothing — the model never gets to invent a number to dial."""
    out = []
    for name in names:
        found = _page_attrs(db, mem_dir, name)
        if found is None:
            continue
        attrs, title = found
        phone = _clean_text(attrs.get("phone") or attrs.get("mobile"), 32)
        if not phone or not _PHONE_OK.match(phone):
            continue
        out.append({"name": title, "phone": phone})
    return out


def resolve_location(db, block: dict, mem_dir: str) -> dict | None:
    """A `location` block ready to send: explicit coordinates pass through;
    a named place resolves through its page's `latlon:: <lat>, <lon>`
    attribute (with `address::` as the caption when present)."""
    if block.get("lat") is not None:
        return {"title": block["title"], "address": block.get("address", ""),
                "lat": block["lat"], "lon": block["lon"]}
    found = _page_attrs(db, mem_dir, block.get("place", ""))
    if found is None:
        return None
    attrs, title = found
    raw = _clean_text(attrs.get("latlon") or attrs.get("coordinates"), 64)
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"title": block.get("title") or title,
            "address": _clean_text(attrs.get("address")), "lat": lat, "lon": lon}


# ══════════════════════════════════════════════════════════
#  BUTTON STORE  (identity-checked taps)
# ══════════════════════════════════════════════════════════
#
#  callback_data is capped at 64 bytes by Telegram, so a button carries a
#  short id and the payload lives here. Each entry remembers WHO the block
#  was addressed to: only that uid's tap resolves it (the same
#  callback-identity rule the approval and relay cards follow). An
#  instance, not module state, so tests get a clean store per case.

BUTTON_TTL = 86400.0    # a day — the owner may come back to an old reply
BUTTON_MAX = 500


class ButtonStore:
    def __init__(self, ttl: float = BUTTON_TTL, max_entries: int = BUTTON_MAX):
        self._ttl = ttl
        self._max = max_entries
        self._entries: dict[str, dict] = {}

    def _prune(self) -> None:
        now = time.time()
        for bid in [b for b, e in self._entries.items() if now - e["ts"] > self._ttl]:
            self._entries.pop(bid, None)
        # Oldest-first eviction if a very long-lived process fills the cap.
        while len(self._entries) >= self._max:
            oldest = min(self._entries, key=lambda b: self._entries[b]["ts"])
            self._entries.pop(oldest, None)

    def put(self, uid: int, verb: str, value: str) -> str:
        self._prune()
        bid = secrets.token_hex(4)
        self._entries[bid] = {"uid": uid, "verb": verb, "value": value,
                              "ts": time.time()}
        return bid

    def get(self, bid: str, uid: int) -> dict | None:
        """Resolve a tap. None if the id is unknown, expired, or the tap
        came from someone the block was not addressed to."""
        entry = self._entries.get(bid)
        if entry is None:
            return None
        if time.time() - entry["ts"] > self._ttl:
            self._entries.pop(bid, None)
            return None
        if entry["uid"] != uid:
            return None
        return dict(entry)

    def __len__(self) -> int:
        return len(self._entries)
