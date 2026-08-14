# ============================================================
#  TESTS — Phase U4: presence (PLAN.md §7/U4 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/presence.py: the startup decision (first install / update /
#      real downtime / routine restart), the card copy, and the
#      settings-backed state round trip.
#    - bot.py: _presence_startup edits the SAME pinned card on a routine
#      restart and sends no message; a first install posts and pins once;
#      real downtime sends exactly one message; a clean shutdown flips the
#      card to Offline; a deleted card is re-posted and re-pinned.
#
#  Run:  .venv/bin/python test_presence.py
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
_tmpdir = tempfile.mkdtemp(prefix="zilla_u4_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

from zilla import presence  # noqa: E402

OWNER = 111


def _reset_state():
    from zilla import store as _store
    db = _store.get_store(config.DB_FILE)
    for key in (presence.CARD_ID_KEY, presence.LAST_SEEN_KEY, presence.VERSION_KEY):
        db.set_setting(key, None)


# ============================================================
#  1. decide_startup — say something only when there is news
# ============================================================

def _run_decision_tests():
    print("\n[1] presence.decide_startup — silent restarts, informative news")
    now = 1_000_000.0

    first = presence.decide_startup(now=now, last_seen=None, stored_version=None,
                                    version="2.2.0", card_id=None)
    check("no card yet -> introduce itself and pin",
          first["pin"] is True and first["reason"] == "first_install"
          and "here" in first["message"], first)

    routine = presence.decide_startup(now=now, last_seen=now - 120,
                                      stored_version="2.2.0", version="2.2.0", card_id=5)
    check("routine restart sends NO message at all",
          routine["message"] is None and routine["reason"] == "routine_restart", routine)
    check("routine restart does not re-pin", routine["pin"] is False, routine)

    updated = presence.decide_startup(now=now, last_seen=now - 60,
                                      stored_version="2.1.0", version="2.2.0", card_id=5)
    check("a new version reports itself once",
          updated["reason"] == "updated" and "2.2.0" in updated["message"], updated)

    down = presence.decide_startup(now=now, last_seen=now - 3 * 3600,
                                   stored_version="2.2.0", version="2.2.0", card_id=5)
    check("real downtime explains the gap in hours",
          down["reason"] == "downtime" and "3 hours" in down["message"], down)

    short_gap = presence.decide_startup(now=now, last_seen=now - 20 * 60,
                                        stored_version="2.2.0", version="2.2.0", card_id=5)
    check("a gap under the threshold stays silent",
          short_gap["message"] is None, short_gap)

    tight = presence.decide_startup(now=now, last_seen=now - 20 * 60,
                                    stored_version="2.2.0", version="2.2.0", card_id=5,
                                    downtime_notify_min=10)
    check("the threshold is configurable", tight["reason"] == "downtime", tight)

    minutes = presence.decide_startup(now=now, last_seen=now - 70 * 60,
                                      stored_version="2.2.0", version="2.2.0", card_id=5)
    check("a gap just past an hour reads in minutes, not '1 hours'",
          "minutes" in minutes["message"], minutes)

    check("an update wins over a coincidental long gap",
          presence.decide_startup(now=now, last_seen=now - 5 * 3600,
                                  stored_version="2.1.0", version="2.2.0",
                                  card_id=5)["reason"] == "updated")


# ============================================================
#  2. card_text — STYLE.md copy
# ============================================================

def _run_copy_tests():
    print("\n[2] presence.card_text — glanceable, one glyph, no shouting")
    online = presence.card_text(online=True, backend="claude", version="2.2.0")
    check("online card names backend and version",
          online.startswith("● Online · claude · v2.2.0"), online)
    check("no exclamation marks anywhere in the copy", "!" not in online, online)

    with_beat = presence.card_text(online=True, backend="agy", version="2.2.0",
                                  last_beat=time.time())
    check("last-check line is added when a beat is known",
          "Last check" in with_beat, with_beat)

    offline = presence.card_text(online=False, backend="agy", version="2.2.0",
                                since=time.time())
    check("offline card says since when", offline.startswith("○ Offline since"), offline)
    check("unknown time degrades to a word, never a crash",
          "unknown" in presence.card_text(online=False, backend="agy", version="1"),
          presence.card_text(online=False, backend="agy", version="1"))

    for text in (online, with_beat, offline):
        check("copy stays within one glanceable card (<=2 lines)",
              len(text.splitlines()) <= 2, text)


# ============================================================
#  3. state round trip
# ============================================================

def _run_state_tests():
    print("\n[3] presence state — survives a restart, never raises")
    _reset_state()
    check("empty state reads as all-None",
          presence.read_state() == {"card_id": None, "last_seen": None, "version": None},
          presence.read_state())

    presence.write_state(card_id=4242, version="2.2.0")
    presence.touch_seen(now=1234.5)
    state = presence.read_state()
    check("card id persists as an int", state["card_id"] == 4242, state)
    check("version persists", state["version"] == "2.2.0", state)
    check("liveness stamp persists as a float", state["last_seen"] == 1234.5, state)

    from zilla import store as _store
    _store.get_store(config.DB_FILE).set_setting(presence.CARD_ID_KEY, "not-an-int")
    check("a corrupt card id reads as None instead of raising",
          presence.read_state()["card_id"] is None, presence.read_state())
    check("downtime_notify_min falls back to the default",
          presence.downtime_notify_min() == presence.DEFAULT_DOWNTIME_NOTIFY_MIN)


# ============================================================
#  4. bot.py wiring — edit in place, post only when needed
# ============================================================

class _FakeMessage:
    def __init__(self, message_id):
        self.message_id = message_id


class _FakeBot:
    """Records every call; edit_message_text fails when the card is gone."""

    def __init__(self, missing_card=False):
        self.sent = []
        self.edits = []
        self.pins = []
        self._missing = missing_card
        self._next_id = 900

    async def send_message(self, chat_id, text, **kw):
        self._next_id += 1
        self.sent.append((chat_id, text))
        return _FakeMessage(self._next_id)

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        if self._missing:
            raise RuntimeError("message to edit not found")
        self.edits.append((message_id, text))

    async def pin_chat_message(self, chat_id, message_id, **kw):
        self.pins.append(message_id)


class _FakeApp:
    def __init__(self, bot):
        self.bot = bot


def _run_bot_tests():
    print("\n[4] bot.py — one pinned card, edited in place; a message only when it matters")
    import bot as _bot
    old_owner, old_app = _bot.OWNER_CHAT_ID, _bot._application
    try:
        _bot.OWNER_CHAT_ID = OWNER

        # ── first install: post + pin, one greeting
        _reset_state()
        bot1 = _FakeBot()
        asyncio.run(_bot._presence_startup(_FakeApp(bot1)))
        check("first install posts the greeting and the card",
              len(bot1.sent) == 2 and "here" in bot1.sent[0][1], bot1.sent)
        check("the card is pinned exactly once", len(bot1.pins) == 1, bot1.pins)
        card_id = presence.read_state()["card_id"]
        check("the card id is remembered", card_id is not None, card_id)

        # ── routine restart: no message, card edited in place
        bot2 = _FakeBot()
        asyncio.run(_bot._presence_startup(_FakeApp(bot2)))
        check("a routine restart sends NO new message", bot2.sent == [], bot2.sent)
        check("a routine restart edits the SAME card",
              len(bot2.edits) == 1 and bot2.edits[0][0] == card_id, bot2.edits)
        check("a routine restart does not pin again", bot2.pins == [], bot2.pins)
        check("the card reads as online", "● Online" in bot2.edits[0][1], bot2.edits)

        # ── real downtime: exactly one message, still one card
        presence.write_state(last_seen=time.time() - 5 * 3600)
        bot3 = _FakeBot()
        asyncio.run(_bot._presence_startup(_FakeApp(bot3)))
        check("real downtime sends exactly one message", len(bot3.sent) == 1, bot3.sent)
        check("the downtime message explains the gap",
              "offline" in bot3.sent[0][1].lower(), bot3.sent)
        check("the card is still edited, not duplicated",
              len(bot3.edits) == 1 and bot3.edits[0][0] == card_id, bot3.edits)

        # ── clean shutdown: card flips to offline, no message
        bot4 = _FakeBot()
        asyncio.run(_bot._presence_shutdown(_FakeApp(bot4)))
        check("a clean shutdown flips the card to offline",
              bot4.edits and "○ Offline since" in bot4.edits[0][1], bot4.edits)
        check("a clean shutdown sends no message", bot4.sent == [], bot4.sent)

        # ── the owner deleted the card: post and pin a fresh one
        bot5 = _FakeBot(missing_card=True)
        asyncio.run(_bot._presence_startup(_FakeApp(bot5)))
        check("a deleted card is replaced", len(bot5.sent) == 1, bot5.sent)
        check("the replacement is pinned", len(bot5.pins) == 1, bot5.pins)
        check("the new card id is stored",
              presence.read_state()["card_id"] != card_id, presence.read_state())

        # ── no owner configured: presence is a silent no-op
        _bot.OWNER_CHAT_ID = 0
        bot6 = _FakeBot()
        asyncio.run(_bot._presence_startup(_FakeApp(bot6)))
        check("without an owner chat, presence does nothing",
              bot6.sent == [] and bot6.edits == [] and bot6.pins == [], bot6.sent)
    finally:
        _bot.OWNER_CHAT_ID, _bot._application = old_owner, old_app


def _run_startup_blast_test():
    print("\n[5] the old startup blast is gone")
    with open("bot.py", "r", encoding="utf-8") as f:
        src = f.read()
    check("bot.py no longer announces itself on every start",
          "Zilla is online" not in src)
    check("/status resolves to the same status view as /ping",
          'aliases=("status",)' in src)


if __name__ == "__main__":
    print("=" * 60)
    print("  PHASE U4 — PRESENCE TESTS")
    print("=" * 60)
    _run_decision_tests()
    _run_copy_tests()
    _run_state_tests()
    _run_bot_tests()
    _run_startup_blast_test()
    print("\n" + "=" * 60)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 60)
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
