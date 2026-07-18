# ============================================================
#  TESTS — pre-F1 quick fix (owner-reported 2026-07-18 pm, HANDOFF.md
#  "Notes" section)
# ============================================================
#  Deterministic, no-network tests for:
#    - bot.py _cb_misc "menu_close": deletes the message (no "✓ Closed"
#      text edit); falls back to stripping the reply markup if delete
#      raises, and never raises itself even if both fail.
#    - bot.py handle_callback: a _cb_* helper raising mid-way (after
#      query.answer() already succeeded, so a second answer() would
#      raise and be swallowed) now surfaces a visible one-line failure
#      to the chat instead of silence.
#
#  Run:  python test_quickfix.py
#  Exit code 0 = all passed, 1 = something failed.
# ============================================================

import asyncio
import json
import os
import tempfile

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


# ── Isolate config BEFORE importing bot (known trap — see project memory) ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_quickfix_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE

import bot as _bot  # noqa: E402

OWNER = 111


class _FakeMessage:
    def __init__(self, fail_delete=False):
        self.deleted = False
        self.markup_cleared = False
        self.edited_text = []
        self._fail_delete = fail_delete
        self.message_id = 42

    async def delete(self):
        if self._fail_delete:
            raise RuntimeError("delete not allowed")
        self.deleted = True


class _FakeQuery:
    def __init__(self, data, fail_delete=False, raise_in_helper=False):
        self.data = data
        self.message = _FakeMessage(fail_delete=fail_delete)
        self.answered = []
        self.edited_texts = []
        self.markup_edits = []
        self._raise_in_helper = raise_in_helper
        self._answer_calls = 0

    async def answer(self, *a, **kw):
        self._answer_calls += 1
        # Mirrors real Telegram behavior: a second answer() on the same
        # callback query raises.
        if self._answer_calls > 1:
            raise RuntimeError("Query is too old and response timeout expired or query id is invalid")
        self.answered.append((a, kw))

    async def edit_message_text(self, text, **kw):
        self.edited_texts.append(text)

    async def edit_message_reply_markup(self, reply_markup=None, **kw):
        self.markup_edits.append(reply_markup)
        self.message.markup_cleared = True


class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class _FakeUpdate:
    def __init__(self, query, uid, chat_id):
        self.callback_query = query
        self.effective_user = _FakeUser(uid)
        self.effective_chat = _FakeChat(chat_id)


class _FakeContext:
    def __init__(self):
        self.bot = _FakeBot()


# ── 1. menu_close deletes the message, no "Closed" text ──

def test_menu_close_deletes_message():
    print("\n[1] _cb_misc menu_close — deletes the message, sends no text")
    query = _FakeQuery("menu_close")
    asyncio.run(_bot._cb_misc(query, _FakeContext(), "menu_close", OWNER, OWNER))
    check("message was deleted", query.message.deleted is True)
    check("no edit_message_text call (no '✓ Closed' text)", query.edited_texts == [], query.edited_texts)
    check("no reply-markup fallback needed when delete succeeds",
          query.markup_edits == [], query.markup_edits)


def test_menu_close_falls_back_when_delete_fails():
    print("\n[2] _cb_misc menu_close — falls back to stripping markup if delete() raises")
    query = _FakeQuery("menu_close", fail_delete=True)
    asyncio.run(_bot._cb_misc(query, _FakeContext(), "menu_close", OWNER, OWNER))
    check("delete was attempted and failed (not left True)", query.message.deleted is False)
    check("fell back to clearing the reply markup", query.markup_edits == [None], query.markup_edits)
    check("still no '✓ Closed' text anywhere", query.edited_texts == [], query.edited_texts)


# ── 2. handle_callback surfaces a failure instead of silence ──

def test_callback_failure_is_visible_not_silent():
    print("\n[3] handle_callback — a _cb_* helper raising surfaces a visible failure line")
    query = _FakeQuery("menu_sessions")
    update = _FakeUpdate(query, OWNER, OWNER)
    context = _FakeContext()

    old_cb_sessions = _bot._cb_sessions

    async def _boom(*a, **kw):
        raise RuntimeError("simulated failure mid-helper")

    _bot._cb_sessions = _boom
    try:
        asyncio.run(_bot.handle_callback(update, context))
    finally:
        _bot._cb_sessions = old_cb_sessions

    check("first answer() succeeded (spent, not double-called by us)", query._answer_calls == 1, query._answer_calls)
    visible_somewhere = (
        any("didn't go through" in t for t in query.edited_texts)
        or any("didn't go through" in t for _, t in context.bot.sent)
    )
    check("a visible failure notice reached the chat (edit or new message)",
          visible_somewhere, (query.edited_texts, context.bot.sent))
    check("failure text is calm, one line, no stack trace",
          all("Traceback" not in t for t in query.edited_texts) and
          all("Traceback" not in t for _, t in context.bot.sent))


def test_callback_failure_falls_back_to_new_message_if_edit_fails():
    print("\n[4] handle_callback — if editing the message also fails, sends a new message instead")
    query = _FakeQuery("menu_sessions")

    async def _fail_edit(text, **kw):
        raise RuntimeError("can't edit, message too old")

    query.edit_message_text = _fail_edit
    update = _FakeUpdate(query, OWNER, OWNER)
    context = _FakeContext()

    old_cb_sessions = _bot._cb_sessions

    async def _boom(*a, **kw):
        raise RuntimeError("simulated failure mid-helper")

    _bot._cb_sessions = _boom
    try:
        asyncio.run(_bot.handle_callback(update, context))
    finally:
        _bot._cb_sessions = old_cb_sessions

    check("a new message was sent to the chat as the last-resort fallback",
          any("didn't go through" in t for _, t in context.bot.sent), context.bot.sent)


if __name__ == "__main__":
    tests = [
        test_menu_close_deletes_message,
        test_menu_close_falls_back_when_delete_fails,
        test_callback_failure_is_visible_not_silent,
        test_callback_failure_falls_back_to_new_message_if_edit_fails,
    ]
    for t in tests:
        t()

    print(f"\n{_passed} passed, {_failed} failed")
    import sys
    sys.exit(1 if _failed else 0)
