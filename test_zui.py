# ============================================================
#  TESTS — Phase U1/U2: the ZUI protocol (PLAN.md §7)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/zui.py: extract() (fences never survive, MAX_BLOCKS, bad JSON
#      dropped without costing the reply, a widget-only reply), per-kind
#      validation + every cap and whitelist (button verbs, javascript:/tg://
#      URLs, table/field/contact caps), render_card / render_table (numeric
#      right-alignment, the phone-width degrade, HTML escaping), graph
#      resolution (phone:: / latlon:: only — the model never supplies a
#      number), and ButtonStore's identity check / TTL / cap.
#    - bot.py: _deliver_zui_block (card via safe_send, contacts suppressed
#      for a non-owner and sent for the owner), _cb_zui (copy → monospace,
#      unknown/expired id → one calm line and no turn, say → exactly one
#      turn with the button's text, a foreign uid's tap → neither).
#    - zilla/harness.py: the protocol is actually taught to the model.
#
#  Run:  python test_zui.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Same isolation discipline as test_memory_k1..k5: zilla.memory.MEMORY_DIR /
#  zilla.config.MEMORY_DIR point at a throwaway tmpdir and zilla.config.DB_FILE
#  at a throwaway sqlite file, so a run never reads or writes the real repo
#  Memory/ tree or zilla.db.
# ============================================================

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


# ── Isolate config BEFORE anything touches the store ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_zui_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.memory as memory  # noqa: E402
from zilla import graph  # noqa: E402
from zilla import store as _store  # noqa: E402
from zilla import zui  # noqa: E402

OWNER = 111
NON_OWNER = 999

_RAMESH_PAGE = ("# Ramesh\nSupplier for the shop.\n"
                "- type:: person\n- aliases:: Ram\n- phone:: +91 98765 43210\n")
_SUNITA_PAGE = "# Sunita\nNeighbour.\n- type:: person\n"
_JUNKPHONE_PAGE = ("# Vikram\nDriver.\n- type:: person\n- phone:: call me\n")
_SHOP_PAGE = ("# Shop\nThe main outlet.\n- type:: place\n"
              "- latlon:: 17.38, 78.48\n- address:: Main Road, Hyderabad\n")
_BADPLACE_PAGE = "# Warehouse\nStore room.\n- type:: place\n- latlon:: somewhere nice\n"


def _iso(tag: str):
    """A throwaway Memory/ tree + a clean graph in the shared test db."""
    tmp = tempfile.mkdtemp(prefix=f"zilla_zui_{tag}_")
    old_mem, old_cfg = memory.MEMORY_DIR, config.MEMORY_DIR
    memory.MEMORY_DIR = config.MEMORY_DIR = os.path.join(tmp, "Memory")
    db = _store.get_store(config.DB_FILE)
    db.graph_clear()
    return tmp, (old_mem, old_cfg), db


def _restore(tmp, olds):
    memory.MEMORY_DIR, config.MEMORY_DIR = olds
    shutil.rmtree(tmp, ignore_errors=True)


def _write_page(rel: str, text: str) -> str:
    full = os.path.join(memory.MEMORY_DIR, "Wiki", rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
    return f"Wiki/{rel}".replace(os.sep, "/")


def _entities(db):
    _write_page("ramesh.md", _RAMESH_PAGE)
    _write_page("sunita.md", _SUNITA_PAGE)
    _write_page("vikram.md", _JUNKPHONE_PAGE)
    _write_page("shop.md", _SHOP_PAGE)
    _write_page("warehouse.md", _BADPLACE_PAGE)
    graph.reindex_graph(db, memory.MEMORY_DIR)


def _fence(payload) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return f"```zui\n{body}\n```"


# ============================================================
#  1. extract — the fence never reaches the owner, a bad block
#     never costs the reply
# ============================================================

def _run_extract_tests():
    print("\n[1] zui.extract — fences stripped, caps enforced, text always delivers")

    buttons = {"kind": "buttons", "items": [{"label": "Go", "say": "go now"}]}

    clean, blocks, dropped = zui.extract(f"Here are your options.\n\n{_fence(buttons)}")
    check("one valid block extracted", len(blocks) == 1 and dropped == 0, (blocks, dropped))
    check("the fence never survives in the delivered text",
          "```" not in clean and "zui" not in clean.lower(), repr(clean))
    check("the human half of the reply is preserved",
          clean == "Here are your options.", repr(clean))
    check("the block is normalized to {label, verb, value}",
          blocks and blocks[0]["items"] == [{"label": "Go", "verb": "say", "value": "go now"}],
          blocks)

    card = {"kind": "card", "title": "Flight"}
    table = {"kind": "table", "headers": ["A"], "rows": [["1"]]}
    three = f"Text.\n{_fence(buttons)}\n{_fence(card)}\n{_fence(table)}"
    clean, blocks, dropped = zui.extract(three)
    check(f"a third block is dropped (MAX_BLOCKS={zui.MAX_BLOCKS})",
          len(blocks) == zui.MAX_BLOCKS and dropped == 1, (len(blocks), dropped))
    check("the dropped block's fence is stripped too",
          "```" not in clean and clean == "Text.", repr(clean))

    clean, blocks, dropped = zui.extract("Your answer.\n\n```zui\n{not json,}\n```")
    check("invalid JSON is dropped", blocks == [] and dropped == 1, (blocks, dropped))
    check("...and the text still delivers", clean == "Your answer.", repr(clean))

    clean, blocks, dropped = zui.extract(f"Nope.\n{_fence({'kind': 'wat', 'x': 1})}")
    check("an unknown kind is dropped, text intact",
          blocks == [] and dropped == 1 and clean == "Nope.", (blocks, clean))

    plain = "Just a normal answer.\n\nWith two paragraphs."
    check("a reply with no blocks is returned untouched",
          zui.extract(plain) == (plain, [], 0), zui.extract(plain))
    check("a reply with a non-zui code fence is untouched",
          zui.extract("```python\nprint(1)\n```") == ("```python\nprint(1)\n```", [], 0))
    check("empty input is safe", zui.extract("") == ("", [], 0))

    clean, blocks, dropped = zui.extract(_fence(card))
    check("a reply that is ONLY a block: clean text is empty",
          clean == "" and dropped == 0, repr(clean))
    check("...and the block survives", len(blocks) == 1 and blocks[0]["kind"] == "card", blocks)

    # An indented fence (the model bulleting its own reply) still gets caught.
    clean, blocks, _ = zui.extract("Options:\n  ```zui\n" + json.dumps(buttons) + "\n  ```")
    check("an indented fence is still extracted and stripped",
          len(blocks) == 1 and "```" not in clean, (blocks, repr(clean)))


# ============================================================
#  2. Validation per kind — schema, caps, whitelists
# ============================================================

def _run_validate_tests():
    print("\n[2] zui.validate — per-kind schema, every cap, both URL whitelists")

    # ── buttons
    many = {"kind": "buttons",
            "items": [{"label": f"B{i}", "say": f"s{i}"} for i in range(20)]}
    out = zui.validate(many)
    check(f"buttons truncated to MAX_BUTTONS ({zui.MAX_BUTTONS})",
          out and len(out["items"]) == zui.MAX_BUTTONS, out)

    long_label = {"kind": "buttons", "items": [{"label": "x" * 100, "say": "hi"}]}
    out = zui.validate(long_label)
    check(f"a label is truncated to MAX_LABEL ({zui.MAX_LABEL})",
          out and len(out["items"][0]["label"]) == zui.MAX_LABEL, out)

    out = zui.validate({"kind": "buttons", "items": [
        {"label": "Two verbs", "say": "hi", "url": "https://example.com"},
        {"label": "Fine", "say": "ok"}]})
    check("a button with two verbs is dropped, the good one survives",
          out and [i["label"] for i in out["items"]] == ["Fine"], out)

    out = zui.validate({"kind": "buttons", "items": [
        {"label": "Mystery", "run": "rm -rf /"},
        {"label": "Fine", "copy": "ABC123"}]})
    check("a button with an unknown verb is dropped entirely",
          out and [i["verb"] for i in out["items"]] == ["copy"], out)

    for bad_url in ["javascript:alert(1)", "tg://resolve?domain=evil",
                    "JavaScript:alert(1)", "data:text/html,<b>x", "//example.com",
                    "https://example.com/a b"]:
        out = zui.validate({"kind": "buttons",
                            "items": [{"label": "Tap", "url": bad_url}]})
        check(f"url rejected: {bad_url[:28]}", out is None, out)

    out = zui.validate({"kind": "buttons",
                        "items": [{"label": "Site", "url": "https://example.com/x?a=1"}]})
    check("an http(s) url survives",
          out and out["items"][0]["value"] == "https://example.com/x?a=1", out)

    check("empty items -> the whole buttons block is invalid",
          zui.validate({"kind": "buttons", "items": []}) is None)
    check("items missing -> invalid", zui.validate({"kind": "buttons"}) is None)
    check("a button with a label but no verb -> whole block invalid (nothing tappable)",
          zui.validate({"kind": "buttons", "items": [{"label": "Dead"}]}) is None)
    check("a button with a verb but no label is dropped",
          zui.validate({"kind": "buttons", "items": [{"say": "hi"}]}) is None)

    # ── card
    check("a card with no title is invalid",
          zui.validate({"kind": "card", "fields": [{"label": "a", "value": "b"}]}) is None)
    check("a card with a blank title is invalid",
          zui.validate({"kind": "card", "title": "   "}) is None)
    out = zui.validate({"kind": "card", "title": "Trip",
                        "fields": [{"label": f"L{i}", "value": f"V{i}"} for i in range(30)]})
    check(f"card fields capped at MAX_FIELDS ({zui.MAX_FIELDS})",
          out and len(out["fields"]) == zui.MAX_FIELDS, out)
    out = zui.validate({"kind": "card", "title": "Trip", "fields": "not a list"})
    check("a card with junk fields still renders as a titled card",
          out and out["fields"] == [], out)
    out = zui.validate({"kind": "card", "title": "T", "fields": [["Depart", "18:05"]]})
    check("a [label, value] pair is accepted as a field",
          out and out["fields"] == [{"label": "Depart", "value": "18:05"}], out)
    out = zui.validate({"kind": "card", "title": "A\nB", "subtitle": "x" * 400})
    check("a multi-line title is flattened to one line",
          out and "\n" not in out["title"], out)
    check(f"any single text value is capped at MAX_TEXT ({zui.MAX_TEXT})",
          out and len(out["subtitle"]) == zui.MAX_TEXT, len(out["subtitle"]))

    # ── table
    check("a table with no headers is invalid",
          zui.validate({"kind": "table", "rows": [["1"]]}) is None)
    check("a table with no rows is invalid",
          zui.validate({"kind": "table", "headers": ["A"]}) is None)
    check("a table with blank headers is invalid",
          zui.validate({"kind": "table", "headers": ["", ""], "rows": [["1", "2"]]}) is None)
    check("a table whose rows are not lists is invalid",
          zui.validate({"kind": "table", "headers": ["A"], "rows": ["nope"]}) is None)
    out = zui.validate({"kind": "table",
                        "headers": [f"H{i}" for i in range(20)],
                        "rows": [[f"c{i}" for i in range(20)]]})
    check(f"columns capped at MAX_TABLE_COLS ({zui.MAX_TABLE_COLS})",
          out and len(out["headers"]) == zui.MAX_TABLE_COLS
          and len(out["rows"][0]) == zui.MAX_TABLE_COLS, out)
    out = zui.validate({"kind": "table", "headers": ["A"],
                        "rows": [[str(i)] for i in range(50)]})
    check(f"rows capped at MAX_TABLE_ROWS ({zui.MAX_TABLE_ROWS})",
          out and len(out["rows"]) == zui.MAX_TABLE_ROWS, out)
    out = zui.validate({"kind": "table", "headers": ["A", "B", "C"], "rows": [["1"]]})
    check("a short row is padded to the header width",
          out and out["rows"][0] == ["1", "", ""], out)

    # ── contacts (names only — the model never supplies a number)
    out = zui.validate({"kind": "contacts",
                        "items": [{"name": "Ramesh", "phone": "+91 00000 00000"}]})
    check("a contacts block is names only", out and out["items"] == ["Ramesh"], out)
    check("a free-typed phone key never survives validation",
          out and "phone" not in json.dumps(out), out)
    out = zui.validate({"kind": "contacts", "items": ["Ramesh", "Sunita"]})
    check("bare strings are accepted as names", out and out["items"] == ["Ramesh", "Sunita"], out)
    out = zui.validate({"kind": "contacts",
                        "items": [{"name": f"P{i}"} for i in range(20)]})
    check(f"contacts capped at MAX_CONTACTS ({zui.MAX_CONTACTS})",
          out and len(out["items"]) == zui.MAX_CONTACTS, out)
    check("contacts with no resolvable names is invalid",
          zui.validate({"kind": "contacts", "items": [{}, ""]}) is None)

    # ── location
    check("out-of-range latitude is rejected",
          zui.validate({"kind": "location", "lat": 91, "lon": 0}) is None)
    check("out-of-range longitude is rejected",
          zui.validate({"kind": "location", "lat": 0, "lon": 181}) is None)
    check("non-numeric coordinates are rejected",
          zui.validate({"kind": "location", "lat": "here", "lon": "there"}) is None)
    out = zui.validate({"kind": "location", "lat": "17.38", "lon": "78.48",
                        "title": "Shop", "address": "Main Road"})
    check("numeric-string coordinates are coerced to floats",
          out and out["lat"] == 17.38 and out["lon"] == 78.48, out)
    out = zui.validate({"kind": "location", "place": "Shop"})
    check("a named place is accepted without coordinates",
          out and out["place"] == "Shop" and out["lat"] is None, out)
    check("a location with neither coordinates nor a place is invalid",
          zui.validate({"kind": "location", "title": "Somewhere"}) is None)

    # ── the guard rail itself
    check("validate never raises on junk input",
          zui.validate(None) is None and zui.validate("string") is None
          and zui.validate([1, 2]) is None and zui.validate({}) is None)
    check("kind matching is case/space tolerant",
          (zui.validate({"kind": " CARD ", "title": "T"}) or {}).get("kind") == "card")


# ============================================================
#  3. Rendering — golden shapes, alignment, escaping
# ============================================================

def _run_render_tests():
    print("\n[3] zui.render_card / render_table — shape, alignment, degrade, escaping")

    card = zui.validate({"kind": "card", "title": "Flight to Delhi",
                         "subtitle": "Tue 12 Aug",
                         "fields": [{"label": "Depart", "value": "18:05"},
                                    {"label": "Gate", "value": "4B"}],
                         "footer": "Prices change"})
    out = zui.render_card(card)
    check("card golden shape: bold title, subtitle, label — value, italic footer",
          out == ("<b>Flight to Delhi</b>\nTue 12 Aug\n\n"
                  "Depart — 18:05\nGate — 4B\n\n<i>Prices change</i>"), repr(out))

    bare = zui.render_card(zui.validate({"kind": "card", "title": "Just a title"}))
    check("a title-only card is one bold line, no stray blank lines",
          bare == "<b>Just a title</b>", repr(bare))

    esc = zui.render_card(zui.validate(
        {"kind": "card", "title": "R&D <b>bold</b>", "fields": [{"label": "a<b", "value": "x&y"}]}))
    check("model-supplied text is HTML-escaped in a card",
          "R&amp;D &lt;b&gt;bold&lt;/b&gt;" in esc and "a&lt;b — x&amp;y" in esc, repr(esc))
    check("no unescaped angle bracket survives outside our own tags",
          esc.count("<b>") == 1 and esc.count("</b>") == 1, repr(esc))

    table = zui.validate({"kind": "table", "headers": ["Item", "Qty"],
                          "rows": [["Rice", "120"], ["Dal", "8"]]})
    out = zui.render_table(table)
    check("a table renders inside <pre>", out.startswith("<pre>") and out.endswith("</pre>"), out)
    check("numeric columns are right-aligned, text columns left-aligned",
          "Rice  120" in out and "Dal     8" in out, repr(out))
    check("a separator row sits under the headers", "----  ---" in out, repr(out))

    titled = zui.render_table(zui.validate(
        {"kind": "table", "title": "Stock", "headers": ["A"], "rows": [["1"]]}))
    check("a table title is a bold line ABOVE the monospace block",
          titled.startswith("<b>Stock</b>\n<pre>"), repr(titled))

    esc_t = zui.render_table(zui.validate(
        {"kind": "table", "headers": ["Tag"], "rows": [["<b>&"]]}))
    check("model-supplied cells are HTML-escaped inside <pre>",
          "&lt;b&gt;&amp;" in esc_t and "<b>&" not in esc_t, repr(esc_t))

    wide = zui.validate({"kind": "table",
                         "headers": ["Item", "Description", "Price"],
                         "rows": [["Rice", "25kg premium sona masoori", "1200"],
                                  ["Dal", "toor, unpolished", "180"]]})
    deg = zui.render_table(wide)
    check("past the width budget a table degrades to field-per-line records",
          "<pre>" not in deg and "Item: Rice" in deg and "Price: 1200" in deg, repr(deg))
    check("degraded records are separated by a blank line",
          "\n\nItem: Dal" in deg, repr(deg))
    check("an explicit width budget forces the degrade",
          "<pre>" not in zui.render_table(table, width_budget=4))
    check("a generous width budget keeps the monospace table",
          "<pre>" in zui.render_table(wide, width_budget=200))

    check("render_text dispatches card and table",
          zui.render_text(card).startswith("<b>Flight")
          and "<pre>" in zui.render_text(table))
    for kind_block in (zui.validate({"kind": "buttons", "items": [{"label": "A", "say": "a"}]}),
                       zui.validate({"kind": "contacts", "items": ["Ramesh"]}),
                       zui.validate({"kind": "location", "place": "Shop"})):
        check(f"render_text returns '' for the pure widget {kind_block['kind']}",
              zui.render_text(kind_block) == "", kind_block["kind"])


# ============================================================
#  4. Graph resolution — Zilla supplies the data, never the model
# ============================================================

def _run_resolve_tests():
    print("\n[4] zui.resolve_contacts / resolve_location — phone:: and latlon:: only")
    tmp, olds, db = _iso("resolve")
    try:
        _entities(db)

        out = zui.resolve_contacts(db, ["Ramesh"], memory.MEMORY_DIR)
        check("a person page with phone:: resolves to a real number",
              out == [{"name": "Ramesh", "phone": "+91 98765 43210"}], out)
        check("an alias resolves to the same person",
              zui.resolve_contacts(db, ["ram"], memory.MEMORY_DIR) == out,
              zui.resolve_contacts(db, ["ram"], memory.MEMORY_DIR))
        check("a person with no phone:: resolves to nothing",
              zui.resolve_contacts(db, ["Sunita"], memory.MEMORY_DIR) == [], out)
        check("a junk phone value ('call me') resolves to nothing",
              zui.resolve_contacts(db, ["Vikram"], memory.MEMORY_DIR) == [])
        check("an unknown name resolves to nothing",
              zui.resolve_contacts(db, ["Nobody At All"], memory.MEMORY_DIR) == [])
        check("a mixed list yields only the resolvable people",
              zui.resolve_contacts(db, ["Ghost", "Ramesh", "Sunita"], memory.MEMORY_DIR)
              == [{"name": "Ramesh", "phone": "+91 98765 43210"}])

        block = zui.validate({"kind": "location", "place": "Shop"})
        place = zui.resolve_location(db, block, memory.MEMORY_DIR)
        check("a place page with latlon:: resolves to coordinates",
              place and place["lat"] == 17.38 and place["lon"] == 78.48, place)
        check("the page title and address:: caption the venue",
              place and place["title"] == "Shop"
              and place["address"] == "Main Road, Hyderabad", place)

        bad = zui.validate({"kind": "location", "place": "Warehouse"})
        check("a junk latlon:: resolves to None",
              zui.resolve_location(db, bad, memory.MEMORY_DIR) is None)
        check("an unknown place resolves to None",
              zui.resolve_location(db, zui.validate(
                  {"kind": "location", "place": "Atlantis"}), memory.MEMORY_DIR) is None)
        check("a person page with no latlon:: resolves to None",
              zui.resolve_location(db, zui.validate(
                  {"kind": "location", "place": "Sunita"}), memory.MEMORY_DIR) is None)

        explicit = zui.validate({"kind": "location", "lat": 12.5, "lon": 77.5,
                                 "title": "Pickup", "address": "Gate 2"})
        check("explicit coordinates pass straight through, no page needed",
              zui.resolve_location(db, explicit, memory.MEMORY_DIR)
              == {"title": "Pickup", "address": "Gate 2", "lat": 12.5, "lon": 77.5},
              zui.resolve_location(db, explicit, memory.MEMORY_DIR))
    finally:
        _restore(tmp, olds)


# ============================================================
#  5. ButtonStore — identity-checked taps, TTL, cap
# ============================================================

def _run_button_store_tests():
    print("\n[5] zui.ButtonStore — round trip, identity check, TTL, oldest-first eviction")

    store = zui.ButtonStore()
    bid = store.put(OWNER, "say", "book the 6pm ticket")
    entry = store.get(bid, OWNER)
    check("put/get round trip returns the verb and value",
          entry and entry["verb"] == "say" and entry["value"] == "book the 6pm ticket", entry)
    check("the entry remembers who it was addressed to", entry and entry["uid"] == OWNER, entry)
    check("a different uid's tap returns None",
          store.get(bid, NON_OWNER) is None, store.get(bid, NON_OWNER))
    check("a foreign tap does NOT consume the entry (the owner can still tap)",
          store.get(bid, OWNER) is not None)
    check("an unknown id returns None", store.get("deadbeef", OWNER) is None)
    check("get returns a copy, not the live entry",
          store.get(bid, OWNER) is not store._entries[bid])

    store._entries[bid]["ts"] -= zui.BUTTON_TTL + 1
    check("an expired entry returns None", store.get(bid, OWNER) is None)
    check("an expired entry is forgotten, not kept forever", len(store) == 0, len(store))

    capped = zui.ButtonStore(max_entries=3)
    ids = [capped.put(OWNER, "copy", f"v{i}") for i in range(3)]
    now = time.time()
    for offset, b in enumerate(ids):          # deterministic age ordering
        capped._entries[b]["ts"] = now - (10 - offset)
    check("the store fills to its cap", len(capped) == 3, len(capped))
    newest = capped.put(OWNER, "copy", "v3")
    check("the cap evicts the OLDEST entry", capped.get(ids[0], OWNER) is None)
    check("newer entries survive the eviction",
          capped.get(ids[1], OWNER) and capped.get(ids[2], OWNER)
          and capped.get(newest, OWNER))
    check("the store never grows past its cap", len(capped) == 3, len(capped))

    expiring = zui.ButtonStore(ttl=-1)
    check("a zero/negative TTL expires immediately",
          expiring.get(expiring.put(OWNER, "say", "hi"), OWNER) is None)


# ============================================================
#  6. bot.py — widget delivery and the tap handler
# ============================================================

class _FakeMessage:
    def __init__(self):
        self.sent: list[str] = []
        self.text = None

    async def reply_text(self, text, **kw):
        self.sent.append(text)


class _FakeQuery:
    def __init__(self, data):
        self.data = data
        self.edited_texts = []

    async def answer(self, *a, **kw):
        pass

    async def edit_message_text(self, text, **kw):
        self.edited_texts.append(text)


class _FakeBot:
    """Records every widget call instead of talking to Telegram."""

    def __init__(self):
        self.sent = []
        self.contacts = []
        self.venues = []
        self.keyboards = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        if kw.get("reply_markup") is not None:
            self.keyboards.append(kw["reply_markup"])

    async def send_contact(self, chat_id, phone_number, first_name, **kw):
        self.contacts.append((chat_id, phone_number, first_name))

    async def send_venue(self, chat_id, latitude, longitude, title, address, **kw):
        self.venues.append((chat_id, latitude, longitude, title, address))

    async def send_chat_action(self, *a, **kw):
        pass


class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class _FakeUpdate:
    def __init__(self, uid, chat_id=None, query=None, message=None):
        self.effective_user = _FakeUser(uid)
        self.effective_chat = _FakeChat(chat_id if chat_id is not None else uid)
        self.message = message if message is not None else _FakeMessage()
        self.effective_message = self.message
        self.callback_query = query


class _FakeContext:
    def __init__(self, args=None, bot=None):
        self.args = args or []
        self.bot = bot if bot is not None else _FakeBot()


class _FakeAuth:
    def is_owner(self, uid):
        return uid == OWNER


def _run_bot_delivery_tests():
    print("\n[6] bot._deliver_zui_block — native widget per kind, owner-only for graph data")
    tmp, olds, db = _iso("deliver")
    import bot as _bot
    old_auth, old_mem, old_dbf = _bot.auth, _bot.MEMORY_DIR, _bot.DB_FILE
    old_safe = _bot.safe_send
    try:
        _entities(db)
        # bot.py binds MEMORY_DIR/DB_FILE at import time — point them at this
        # test's throwaway tree (production's never moves under a running bot).
        _bot.MEMORY_DIR = memory.MEMORY_DIR
        _bot.DB_FILE = config.DB_FILE
        _bot.auth = _FakeAuth()

        sends = []

        async def fake_safe_send(bot, chat_id, text, parse_mode=None):
            sends.append((chat_id, text, parse_mode))

        _bot.safe_send = fake_safe_send

        # ── card
        ctx = _FakeContext()
        card = zui.validate({"kind": "card", "title": "Flight to Delhi",
                             "fields": [{"label": "Depart", "value": "18:05"}]})
        asyncio.run(_bot._deliver_zui_block(ctx, card, OWNER, OWNER))
        check("a card is sent as one HTML message",
              len(sends) == 1 and sends[0][2] == "HTML", sends)
        check("the card's rendered text goes out verbatim",
              sends and sends[0][1] == zui.render_card(card), sends)

        # ── buttons: an inline keyboard, callbacks bound to this user
        sends.clear()
        ctx2 = _FakeContext()
        block = zui.validate({"kind": "buttons", "items": [
            {"label": "Book it", "say": "book the 6pm ticket"},
            {"label": "Copy code", "copy": "ZL-4421"},
            {"label": "Open site", "url": "https://example.com"}]})
        asyncio.run(_bot._deliver_zui_block(ctx2, block, OWNER, OWNER))
        check("a buttons block sends one message with a keyboard",
              len(ctx2.bot.sent) == 1 and len(ctx2.bot.keyboards) == 1, ctx2.bot.sent)
        rows = ctx2.bot.keyboards[0].inline_keyboard if ctx2.bot.keyboards else []
        flat = [b for row in rows for b in row]
        check("every button is on the keyboard, two per row",
              len(flat) == 3 and [len(r) for r in rows] == [2, 1], rows)
        check("a url button is a native link, not a callback",
              any(b.url == "https://example.com" and b.callback_data is None for b in flat), flat)
        cbs = [b.callback_data for b in flat if b.callback_data]
        check("say/copy buttons carry a short zui_ callback id",
              len(cbs) == 2 and all(c.startswith("zui_") and len(c) <= 64 for c in cbs), cbs)
        check("the payload lives in the store, bound to this user",
              all(_bot._zui_buttons.get(c.removeprefix("zui_"), OWNER) for c in cbs), cbs)
        check("nobody else's tap can resolve those buttons",
              all(_bot._zui_buttons.get(c.removeprefix("zui_"), NON_OWNER) is None
                  for c in cbs), cbs)

        # ── contacts: owner-only, and the number comes from the page
        ctx3 = _FakeContext()
        contacts = zui.validate({"kind": "contacts",
                                 "items": [{"name": "Ramesh", "phone": "+1 555 0100"}]})
        asyncio.run(_bot._deliver_zui_block(ctx3, contacts, NON_OWNER, NON_OWNER))
        check("a contacts block for a NON-owner is suppressed entirely",
              ctx3.bot.contacts == [] and ctx3.bot.sent == [] and sends == [],
              (ctx3.bot.contacts, ctx3.bot.sent, sends))

        ctx4 = _FakeContext()
        asyncio.run(_bot._deliver_zui_block(ctx4, contacts, OWNER, OWNER))
        check("the owner gets one send_contact call",
              len(ctx4.bot.contacts) == 1, ctx4.bot.contacts)
        check("the number comes from the page's phone::, never from the model",
              ctx4.bot.contacts == [(OWNER, "+91 98765 43210", "Ramesh")], ctx4.bot.contacts)

        ctx5 = _FakeContext()
        asyncio.run(_bot._deliver_zui_block(
            ctx5, zui.validate({"kind": "contacts", "items": ["Sunita"]}), OWNER, OWNER))
        check("a name with no phone:: sends nothing at all — and no error message",
              ctx5.bot.contacts == [] and ctx5.bot.sent == [] and sends == [],
              (ctx5.bot.contacts, ctx5.bot.sent))

        # ── location: owner-only too
        ctx6 = _FakeContext()
        loc = zui.validate({"kind": "location", "place": "Shop"})
        asyncio.run(_bot._deliver_zui_block(ctx6, loc, NON_OWNER, NON_OWNER))
        check("a location block for a NON-owner is suppressed",
              ctx6.bot.venues == [], ctx6.bot.venues)

        ctx7 = _FakeContext()
        asyncio.run(_bot._deliver_zui_block(ctx7, loc, OWNER, OWNER))
        check("the owner gets a venue at the page's latlon::",
              ctx7.bot.venues == [(OWNER, 17.38, 78.48, "Shop", "Main Road, Hyderabad")],
              ctx7.bot.venues)

        ctx8 = _FakeContext()
        asyncio.run(_bot._deliver_zui_block(
            ctx8, zui.validate({"kind": "location", "place": "Warehouse"}), OWNER, OWNER))
        check("an unresolvable location sends nothing, no error to the owner",
              ctx8.bot.venues == [] and ctx8.bot.sent == [] and sends == [],
              (ctx8.bot.venues, ctx8.bot.sent))

        # ── a widget that blows up must never become an error message
        class _BoomBot(_FakeBot):
            async def send_contact(self, *a, **kw):
                raise RuntimeError("telegram is down")

        ctx9 = _FakeContext(bot=_BoomBot())
        asyncio.run(_bot._deliver_zui_block(ctx9, contacts, OWNER, OWNER))
        check("a failing widget is one logged line, not an owner-facing error",
              sends == [] and ctx9.bot.sent == [], (sends, ctx9.bot.sent))
    finally:
        _bot.auth, _bot.MEMORY_DIR, _bot.DB_FILE = old_auth, old_mem, old_dbf
        _bot.safe_send = old_safe
        _restore(tmp, olds)


def _run_bot_tap_tests():
    print("\n[7] bot._cb_zui — copy echoes, say runs ONE turn, a foreign/expired tap does neither")
    import bot as _bot
    old_safe, old_turn, old_resp = _bot.safe_send, _bot._relay_cli_turn, _bot.send_response
    old_typing = _bot.keep_typing
    try:
        sends = []

        async def fake_safe_send(bot, chat_id, text, parse_mode=None):
            sends.append((chat_id, text, parse_mode))

        turns = []

        async def fake_turn(update, uid, chat_id, prompt, **kw):
            turns.append((uid, chat_id, prompt))
            return "Booked."

        responses = []

        async def fake_send_response(update, context, response, user_id, chat_id):
            responses.append((response, user_id, chat_id))

        async def fake_keep_typing(bot, chat_id, stop_event, progress=None):
            await stop_event.wait()

        _bot.safe_send = fake_safe_send
        _bot._relay_cli_turn = fake_turn
        _bot.send_response = fake_send_response
        _bot.keep_typing = fake_keep_typing

        # ── copy
        bid = _bot._zui_buttons.put(OWNER, "copy", "ZL-4421 <tag> & co")
        ctx = _FakeContext()
        asyncio.run(_bot._cb_zui(_FakeQuery(f"zui_{bid}"), ctx, f"zui_{bid}", OWNER, OWNER))
        check("a copy tap sends the value as monospace HTML",
              len(sends) == 1 and sends[0][2] == "HTML"
              and sends[0][1].startswith("<code>") and sends[0][1].endswith("</code>"), sends)
        check("the copied value is HTML-escaped inside the <code> tag",
              sends and "&lt;tag&gt; &amp; co" in sends[0][1], sends)
        check("a copy tap never starts a turn", turns == [] and responses == [])

        # ── unknown / expired id
        sends.clear()
        ctx2 = _FakeContext()
        asyncio.run(_bot._cb_zui(_FakeQuery("zui_deadbeef"), ctx2, "zui_deadbeef", OWNER, OWNER))
        check("an unknown/expired id gets one calm line",
              len(sends) == 1 and "isn't available any more" in sends[0][1], sends)
        check("an unknown/expired id starts no turn", turns == [] and responses == [])

        # ── say: exactly one turn, with the button's text, as the tapping user
        sends.clear()
        bid2 = _bot._zui_buttons.put(OWNER, "say", "book the 6pm ticket")
        ctx3 = _FakeContext()
        asyncio.run(_bot._cb_zui(_FakeQuery(f"zui_{bid2}"), ctx3, f"zui_{bid2}", OWNER, OWNER))
        check("a say tap runs the turn pipeline exactly once", len(turns) == 1, turns)
        check("the turn carries the button's text, as the tapping user",
              turns == [(OWNER, OWNER, "book the 6pm ticket")], turns)
        check("the reply goes back through send_response exactly once",
              responses == [("Booked.", OWNER, OWNER)], responses)
        check("the tapped text is echoed so the chat reads like a conversation",
              any("book the 6pm ticket" in t for _, t, _ in sends), sends)

        # ── a tap from a DIFFERENT uid than the button was created for
        sends.clear()
        turns.clear()
        responses.clear()
        bid3 = _bot._zui_buttons.put(OWNER, "say", "transfer the money")
        ctx4 = _FakeContext()
        asyncio.run(_bot._cb_zui(_FakeQuery(f"zui_{bid3}"), ctx4, f"zui_{bid3}",
                                 NON_OWNER, NON_OWNER))
        check("a foreign uid's tap starts no turn", turns == [], turns)
        check("a foreign uid's tap sends no response", responses == [], responses)
        check("a foreign uid's tap gets the same calm line as an expired one",
              len(sends) == 1 and "isn't available any more" in sends[0][1], sends)
        check("the owner's button is still tappable after a foreign attempt",
              _bot._zui_buttons.get(bid3, OWNER) is not None)

        # ── a failing turn still answers the owner (P4)
        sends.clear()
        turns.clear()
        responses.clear()

        async def boom_turn(*a, **kw):
            raise RuntimeError("engine unavailable")

        _bot._relay_cli_turn = boom_turn
        bid4 = _bot._zui_buttons.put(OWNER, "say", "do the thing")
        ctx5 = _FakeContext()
        asyncio.run(_bot._cb_zui(_FakeQuery(f"zui_{bid4}"), ctx5, f"zui_{bid4}", OWNER, OWNER))
        check("a failed say-turn still sends one friendly reply, no stack trace",
              len(responses) == 1 and responses[0][0], responses)
    finally:
        _bot.safe_send, _bot._relay_cli_turn = old_safe, old_turn
        _bot.send_response, _bot.keep_typing = old_resp, old_typing


# ============================================================
#  8. harness — the model is actually taught the protocol
# ============================================================

def _run_preamble_tests():
    print("\n[8] harness — the ZUI protocol is in the always-on contract and the onboarding")
    tmp, olds, db = _iso("preamble")
    try:
        memory.ensure_tree()
        from zilla import harness as _harness

        contract = _harness.operating_contract()
        check("_ZUI_PROTOCOL is in the every-turn operating contract",
              _harness._ZUI_PROTOCOL in contract, contract[-300:])
        check("the contract names the fence the extractor looks for",
              "```zui" in contract)
        check("every kind the validator accepts is taught",
              all(k in contract for k in zui.KINDS), zui.KINDS)
        check("every verb the validator accepts is taught",
              all(v in contract for v in zui.VERBS), zui.VERBS)
        check("the 2-block cap is stated to the model", "at most 2" in contract)
        check("the model is told it may NOT supply a phone number itself",
              "never from you" in contract)

        onboarding = _harness.build_preamble(is_new=True)
        check("_ZUI_PROTOCOL is in the new-conversation onboarding block",
              _harness._ZUI_PROTOCOL in onboarding, onboarding[-300:])
        check("...and in a continued turn's compact block",
              _harness._ZUI_PROTOCOL in _harness.build_preamble(is_new=False))
    finally:
        _restore(tmp, olds)


if __name__ == "__main__":
    print("=" * 60)
    print("  PHASE U1/U2 — ZUI PROTOCOL TESTS")
    print("=" * 60)
    _run_extract_tests()
    _run_validate_tests()
    _run_render_tests()
    _run_resolve_tests()
    _run_button_store_tests()
    _run_bot_delivery_tests()
    _run_bot_tap_tests()
    _run_preamble_tests()
    print("\n" + "=" * 60)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 60)
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
