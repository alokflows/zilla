# ============================================================
#  TESTS — Phase K5: team relay (PLAN.md §6/K5 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/relay.py: marker parse/strip for both kinds (including a
#      message that legitimately contains "::"), malformed markers,
#      alias -> person page -> telegram_uid:: resolution and its two
#      failure modes, the explanatory lines, the confirm card.
#    - zilla/core.py: markers stripped off the owner-facing reply and
#      turned into a held RelayRequest; markers on a NON-owner turn
#      dropped entirely (injection guard); no confirm => nothing sent and
#      no schedule created, ever; confirm() creating a relay schedule with
#      uid=owner / chat_id=target / payload_type=system_event; the audit
#      trail recording only confirmed actions.
#    - bot.py: _cb_relay (owner gate, send goes to the TARGET's chat,
#      failure surfaces one calm line, double-tap says "expired"),
#      cmd_relay's owner gate + rendering, and the K5.5 inbound carve-out
#      (a known relay target's reply is REPORTED to the owner, never run;
#      a stranger still gets today's silent reject).
#
#  Run:  python test_memory_k5.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every test points zilla.memory.MEMORY_DIR / zilla.config.MEMORY_DIR at a
#  throwaway tmpdir and zilla.config.DB_FILE at a throwaway sqlite file
#  (same pattern test_memory_k1..k4 use) so a run never reads or writes the
#  real repo Memory/ tree or zilla.db.
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
_tmpdir = tempfile.mkdtemp(prefix="zilla_k5_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

# Tests must never write into the owner's real ~/Zilla (logs, media,
# Memory). config binds every path off ZILLA_HOME at import time, so this
# has to happen before the first zilla import in this file.
import os as _os, tempfile as _tf  # noqa: E402
_os.environ.setdefault("ZILLA_HOME", _tf.mkdtemp(prefix="zilla_test_home_"))
_os.makedirs(_os.path.join(_os.environ["ZILLA_HOME"], "Runtime", "logs"), exist_ok=True)
import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.memory as memory  # noqa: E402
import zilla.core as zcore  # noqa: E402
from zilla import graph  # noqa: E402
from zilla import relay  # noqa: E402
from zilla import store as _store  # noqa: E402
from zilla.core import ZillaCore, RelayRequest, Response, RELAY_MAX, RELAY_TTL  # noqa: E402
from zilla.schedules import ScheduleManager  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402

OWNER = 111
NON_OWNER = 999
PRIYA_UID = 424242

_PRIYA_PAGE = ("# Priya\nOperations lead at the shop.\n"
               "- type:: person\n- aliases:: Pri\n"
               f"- telegram_uid:: {PRIYA_UID}\n")
_RAHUL_PAGE = "# Rahul\nSupplier.\n- type:: person\n"


def _iso(tag: str):
    """A throwaway Memory/ tree + a clean graph in the shared test db."""
    tmp = tempfile.mkdtemp(prefix=f"zilla_k5_{tag}_")
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


def _people(db):
    _write_page("priya.md", _PRIYA_PAGE)
    _write_page("rahul.md", _RAHUL_PAGE)
    graph.reindex_graph(db, memory.MEMORY_DIR)


def _fresh_core(tag: str, subscribe=True):
    """A core with its own sessions/users/schedules, plus (optionally) a
    plain list collecting every broadcast event."""
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.json"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.json"), OWNER)
    sched = ScheduleManager(os.path.join(_tmpdir, f"schedules_{tag}.db"))
    core = ZillaCore(sessions=sessions, auth=auth, schedules=sched)
    events = []
    if subscribe:
        core._subscribers.append(_CollectingQueue(events))
    return core, events


class _CollectingQueue:
    """Stands in for the asyncio.Queue a real frontend subscribes with —
    _broadcast() only ever calls put_nowait()."""

    def __init__(self, sink):
        self._sink = sink

    def put_nowait(self, ev):
        self._sink.append(ev)


def _ctx(uid=OWNER, is_owner=True):
    from zilla.harness import TurnContext
    return TurnContext(uid=uid, role="owner" if is_owner else "user", is_owner=is_owner)


# ============================================================
#  1. relay.parse_markers — detect, strip, tolerate
# ============================================================

def _run_parse_tests():
    print("\n[1] relay.parse_markers — both kinds detected, always stripped, never raises")

    clean, actions = relay.parse_markers("Sure, I'll ask her.\n\nRELAY_SEND: Priya :: Please send the report")
    check("send marker parsed", len(actions) == 1 and actions[0]["kind"] == "send", actions)
    check("send alias + message split", actions and actions[0].get("alias") == "Priya"
          and actions[0].get("message") == "Please send the report", actions)
    check("marker never survives into the owner-facing text",
          "RELAY_SEND" not in clean and clean == "Sure, I'll ask her.", repr(clean))

    clean, actions = relay.parse_markers(
        'Done.\nRELAY_SCHEDULE: Rahul :: weekly :: {"days": [0], "hh": 9, "mm": 0} :: Send the stock count')
    check("schedule marker parsed", len(actions) == 1 and actions[0]["kind"] == "schedule", actions)
    a = actions[0] if actions else {}
    check("schedule kind/spec/text split",
          a.get("sched_kind") == "weekly" and a.get("spec") == {"days": [0], "hh": 9, "mm": 0}
          and a.get("text") == "Send the stock count", a)
    check("schedule marker stripped", "RELAY_SCHEDULE" not in clean, repr(clean))

    # A relayed message may legitimately contain "::" — only the FIRST
    # separator splits alias from message.
    _, actions = relay.parse_markers("RELAY_SEND: Priya :: note :: keep this bit")
    check("only the first '::' splits a send",
          actions and actions[0].get("message") == "note :: keep this bit", actions)

    for bad in ["RELAY_SEND: Priya",
                "RELAY_SEND:  :: hello",
                "RELAY_SCHEDULE: Priya :: daily :: not-json :: hi",
                "RELAY_SCHEDULE: Priya :: fortnightly :: {} :: hi",
                "RELAY_SCHEDULE: Priya :: daily :: hi"]:
        clean, actions = relay.parse_markers(f"Ok.\n{bad}")
        check(f"malformed marker reported, not raised: {bad[:38]}",
              len(actions) == 1 and actions[0].get("error") == "malformed", actions)
        check("malformed marker still stripped from the reply",
              "RELAY_" not in clean, repr(clean))

    text = "Nothing to relay here."
    clean, actions = relay.parse_markers(text)
    check("a normal reply is returned untouched", clean == text and actions == [])
    check("empty input is safe", relay.parse_markers("") == ("", []))

    many = "\n".join(f"RELAY_SEND: P{i} :: msg{i}" for i in range(8))
    _, actions = relay.parse_markers(many)
    check(f"actions capped at MAX_ACTIONS ({relay.MAX_ACTIONS})",
          len(actions) == relay.MAX_ACTIONS, len(actions))


# ============================================================
#  2. relay.resolve_target / failure lines / confirm card
# ============================================================

def _run_resolve_tests():
    print("\n[2] relay.resolve_target — alias -> person page -> telegram_uid::")
    tmp, olds, db = _iso("resolve")
    try:
        _people(db)

        t = relay.resolve_target(db, "Priya", memory.MEMORY_DIR)
        check("alias resolves to the page's telegram_uid",
              t["uid"] == PRIYA_UID and t["reason"] is None, t)
        check("resolved target carries the page title, not the alias",
              t["name"] == "Priya", t)

        t_alias = relay.resolve_target(db, "pri", memory.MEMORY_DIR)
        check("an alias:: entry resolves too, case-insensitively",
              t_alias["uid"] == PRIYA_UID and t_alias["name"] == "Priya", t_alias)

        t_nouid = relay.resolve_target(db, "Rahul", memory.MEMORY_DIR)
        check("known person without telegram_uid:: -> no_uid",
              t_nouid["uid"] is None and t_nouid["reason"] == "no_uid", t_nouid)
        check("no_uid line names the person and says what's missing",
              "Rahul" in relay.failure_line(t_nouid)
              and "telegram_uid" in relay.failure_line(t_nouid),
              relay.failure_line(t_nouid))

        t_none = relay.resolve_target(db, "Nobody", memory.MEMORY_DIR)
        check("unknown name -> no_node", t_none["uid"] is None and t_none["reason"] == "no_node", t_none)
        check("no_node line is plain language, no jargon",
              "Nobody" in relay.failure_line(t_none), relay.failure_line(t_none))

        _write_page("broken.md", "# Broken\nA person.\n- type:: person\n- telegram_uid:: not-a-number\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        t_bad = relay.resolve_target(db, "Broken", memory.MEMORY_DIR)
        check("a malformed telegram_uid:: degrades to no_uid, never raises",
              t_bad["uid"] is None and t_bad["reason"] == "no_uid", t_bad)

        # A page recorded in the graph whose file has since vanished.
        os.remove(os.path.join(memory.MEMORY_DIR, "Wiki", "priya.md"))
        t_gone = relay.resolve_target(db, "Priya", memory.MEMORY_DIR)
        check("a missing page file degrades to no_uid, never raises",
              t_gone["uid"] is None and t_gone["reason"] == "no_uid", t_gone)

        card = relay.confirm_card({"kind": "send", "alias": "Pri", "message": "Send the report"},
                                  {"name": "Priya", "alias": "Pri", "uid": PRIYA_UID})
        check("confirm card shows the RESOLVED name and the exact message",
              "Priya" in card and "Send the report" in card and "Pri ::" not in card, card)
        sched_card = relay.confirm_card(
            {"kind": "schedule", "sched_kind": "daily", "spec": {"hh": 9, "mm": 0}, "text": "Stock count"},
            {"name": "Rahul", "alias": "Rahul", "uid": 7})
        check("schedule confirm card shows the timing in plain language",
              "daily at 09:00" in sched_card and "Stock count" in sched_card, sched_card)
    finally:
        _restore(tmp, olds)


# ============================================================
#  3. find_person_by_uid — the K5.5 reverse lookup
# ============================================================

def _run_reverse_lookup_tests():
    print("\n[3] relay.find_person_by_uid — reverse lookup for inbound replies")
    tmp, olds, db = _iso("reverse")
    try:
        _people(db)
        node = relay.find_person_by_uid(db, PRIYA_UID, memory.MEMORY_DIR)
        check("a known relay target is found by their telegram_uid",
              node is not None and node.get("title") == "Priya", node)
        check("an unknown sender is not found",
              relay.find_person_by_uid(db, 5, memory.MEMORY_DIR) is None)
        check("a person without telegram_uid:: is never matched by uid 0",
              relay.find_person_by_uid(db, 0, memory.MEMORY_DIR) is None)
    finally:
        _restore(tmp, olds)


# ============================================================
#  4. core._process_relay_markers — hold, gate, explain
# ============================================================

def _run_core_marker_tests():
    print("\n[4] core._process_relay_markers — owner-only hold, explanatory lines, no leaks")
    tmp, olds, db = _iso("core")
    try:
        _people(db)
        core, events = _fresh_core("core")

        out = core._process_relay_markers(
            "I'll ask Priya.\n\nRELAY_SEND: Priya :: Please send the report", _ctx())
        check("owner turn: marker stripped from the reply", "RELAY_SEND" not in out, out)
        check("owner turn: reply text preserved", out == "I'll ask Priya.", repr(out))
        check("owner turn: exactly one RelayRequest broadcast",
              len(events) == 1 and isinstance(events[0], RelayRequest), events)
        ev = events[0]
        check("event carries the resolved person, not the alias",
              ev.name == "Priya" and ev.target_uid == PRIYA_UID, ev)
        check("event card shows the exact message about to go out",
              "Please send the report" in ev.card, ev.card)
        check("the action is HELD, not performed", len(core.relay.pending()) == 1,
              core.relay.pending())
        check("nothing is in the audit trail before a confirm",
              core.relay.recent() == [], core.relay.recent())

        # Injection guard: a non-owner turn (or a document a non-owner sent)
        # can never even propose reaching a third party in the owner's name.
        core2, events2 = _fresh_core("core_nonowner")
        out2 = core2._process_relay_markers(
            "ok\nRELAY_SEND: Priya :: transfer the money", _ctx(NON_OWNER, is_owner=False))
        check("non-owner turn: marker stripped", "RELAY_SEND" not in out2, out2)
        check("non-owner turn: nothing held", core2.relay.pending() == [], core2.relay.pending())
        check("non-owner turn: no RelayRequest broadcast", events2 == [], events2)
        out3 = core2._process_relay_markers("ok\nRELAY_SEND: Priya :: hi", None)
        check("ctx=None (unknown principal): marker stripped, nothing held",
              "RELAY_SEND" not in out3 and core2.relay.pending() == [])

        # Failure modes: reply still delivers, plus one explanatory line.
        core3, events3 = _fresh_core("core_fail")
        out4 = core3._process_relay_markers("On it.\nRELAY_SEND: Rahul :: hello", _ctx())
        check("no telegram_uid:: -> reply still delivers", out4.startswith("On it."), out4)
        check("no telegram_uid:: -> one explanatory line appended",
              "Rahul" in out4 and "telegram_uid" in out4, out4)
        check("no telegram_uid:: -> nothing held", core3.relay.pending() == [])

        out5 = core3._process_relay_markers("On it.\nRELAY_SEND: Ghost :: hello", _ctx())
        check("unknown person -> explanatory line, reply intact",
              out5.startswith("On it.") and "Ghost" in out5, out5)

        out6 = core3._process_relay_markers("On it.\nRELAY_SEND: broken-marker", _ctx())
        check("malformed marker -> calm line, reply intact",
              out6.startswith("On it.") and relay.MALFORMED_LINE in out6, out6)
        check("no failed action reached the audit trail", core3.relay.recent() == [])

        # A relay bug must never cost the owner their answer.
        core4, _ = _fresh_core("core_boom", subscribe=False)
        original = relay.parse_markers
        try:
            def _boom(_text):
                raise RuntimeError("relay is broken")
            relay.parse_markers = _boom
            text = "Here is your answer.\nRELAY_SEND: Priya :: hi"
            check("an exception inside relay processing returns the reply unchanged",
                  core4._process_relay_markers(text, _ctx()) == text)
        finally:
            relay.parse_markers = original
    finally:
        _restore(tmp, olds)


# ============================================================
#  5. Hold policy — TTL, cap, cancel
# ============================================================

def _run_hold_tests():
    print("\n[5] core.relay — TTL prune, cap, and cancel (no confirm => nothing happens)")
    tmp, olds, db = _iso("hold")
    try:
        _people(db)
        core, _ = _fresh_core("hold")
        target = relay.resolve_target(db, "Priya", memory.MEMORY_DIR)
        action = {"kind": "send", "alias": "Priya", "message": "hi"}

        rid = core.relay.submit(action, target, owner_uid=OWNER)
        check("submit returns an id", bool(rid))
        check("peek finds the held proposal", core.relay.peek(rid)["target_uid"] == PRIYA_UID)

        entry = core.relay.cancel(rid)
        check("cancel pops the proposal", entry is not None and core.relay.pending() == [])
        check("a canceled proposal is NOT in the audit trail — nothing happened",
              core.relay.recent() == [], core.relay.recent())
        check("cancel is idempotent (double-tap)", core.relay.cancel(rid) is None)
        check("confirm on a canceled id does nothing", core.relay.confirm(rid) is None)

        # TTL: an un-confirmed proposal expires; nothing is queued anywhere.
        rid2 = core.relay.submit(action, target, owner_uid=OWNER)
        core._pending_relays[rid2]["ts"] = time.time() - RELAY_TTL - 1
        core.relay.submit(action, target, owner_uid=OWNER)  # triggers the lazy prune
        check("an expired proposal is forgotten on the next submit",
              rid2 not in core._pending_relays, list(core._pending_relays))

        core._pending_relays.clear()
        ids = [core.relay.submit(action, target, owner_uid=OWNER) for _ in range(RELAY_MAX)]
        check(f"queue fills to RELAY_MAX ({RELAY_MAX})", all(ids) and len(ids) == RELAY_MAX)
        check("submit past the cap returns None instead of growing unbounded",
              core.relay.submit(action, target, owner_uid=OWNER) is None)
    finally:
        _restore(tmp, olds)


# ============================================================
#  6. RELAY_SCHEDULE round trip
# ============================================================

def _run_schedule_roundtrip_tests():
    print("\n[6] core.relay.confirm — relay schedule: uid=owner, chat_id=target, system_event")
    tmp, olds, db = _iso("sched")
    try:
        _people(db)
        core, events = _fresh_core("sched")

        core._process_relay_markers(
            'Sure.\nRELAY_SCHEDULE: Priya :: weekly :: {"days": [0], "hh": 9, "mm": 0} :: Send the stock count',
            _ctx())
        check("schedule proposal held, not created",
              len(core.relay.pending()) == 1 and core.schedules.list(OWNER) == [],
              core.schedules.list(OWNER))

        rid = core.relay.pending()[0]["id"]
        entry = core.relay.confirm(rid)
        check("confirm reports success", entry is not None and entry.get("ok") is True, entry)

        rows = core.schedules.list(OWNER)
        check("exactly one schedule created, under the OWNER's own list", len(rows) == 1, rows)
        row = rows[0] if rows else {}
        check("uid stays the owner's", row.get("user_id", row.get("uid")) == OWNER, row)
        check("chat_id is the TARGET's telegram_uid", row.get("chat_id") == PRIYA_UID, row)
        check("payload_type is system_event (verbatim, zero model call)",
              row.get("payload_type") == "system_event", row)
        check("prompt is the verbatim text to deliver",
              row.get("prompt") == "Send the stock count", row)
        check("title labels the target so /schedules shows '→ Priya'",
              str(row.get("title", "")).startswith("→ Priya"), row)

        log = core.relay.recent()
        check("confirmed schedule is in the audit trail",
              len(log) == 1 and log[0]["status"] == "scheduled", log)
        check("audit row names the target", log and log[0]["name"] == "Priya", log)
        check("audit row keeps a readable summary",
              log and "Send the stock count" in log[0]["summary"], log)

        # A schedule the manager refuses (a one-off in the past) is reported,
        # not crashed on, and lands in the log as a failure.
        core._process_relay_markers(
            'Ok.\nRELAY_SCHEDULE: Priya :: once :: {"run_at": 1} :: too late', _ctx())
        entry2 = core.relay.confirm(core.relay.pending()[0]["id"])
        check("an impossible schedule reports ok=False instead of raising",
              entry2 is not None and entry2.get("ok") is False, entry2)
        check("the failure is recorded in the audit trail",
              core.relay.recent()[0]["status"] == "failed", core.relay.recent()[:1])
        check("no extra schedule row was created", len(core.schedules.list(OWNER)) == 1)
    finally:
        _restore(tmp, olds)


# ============================================================
#  7. End-to-end: a full owner turn never leaks a marker
# ============================================================

def _run_turn_tests():
    print("\n[7] core.handle_message — a relay marker never reaches the owner's chat")
    tmp, olds, db = _iso("turn")
    try:
        _people(db)
        core, events = _fresh_core("turn")

        async def fake_run(prompt, conv_id, progress_callback=None, cancel_event=None,
                           skip_permissions=False, ctx=None):
            return ("I'll ask her now.\n\nRELAY_SEND: Priya :: Please send the report",
                    "conv-k5")

        async def run():
            out = []
            old_run, old_step = zcore.run_cli_async, zcore.get_latest_step
            zcore.run_cli_async = fake_run
            zcore.get_latest_step = lambda conv: 1
            try:
                async for ev in core.handle_message(OWNER, "tell Priya to send the report"):
                    out.append(ev)
            finally:
                zcore.run_cli_async, zcore.get_latest_step = old_run, old_step
            return out

        evs = asyncio.run(run())
        responses = [e for e in evs if isinstance(e, Response)]
        check("exactly one Response", len(responses) == 1, evs)
        check("the marker is gone from the delivered text",
              responses and "RELAY_SEND" not in responses[0].text, responses)
        check("the human half of the reply survives",
              responses and responses[0].text.strip() == "I'll ask her now.", responses)
        check("the turn held exactly one relay proposal",
              len(core.relay.pending()) == 1, core.relay.pending())
        check("a RelayRequest was broadcast for the frontend to render",
              any(isinstance(e, RelayRequest) for e in events), events)
    finally:
        _restore(tmp, olds)


# ============================================================
#  8. bot.py — _cb_relay, cmd_relay, inbound carve-out
# ============================================================

class _FakeMessage:
    def __init__(self):
        self.sent: list[str] = []
        self.text = None
        self.caption = None
        self.photo = None
        self.voice = None
        self.audio = None
        self.document = None
        self.video = None

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
    def __init__(self, fail=False):
        self.sent = []
        self._fail = fail

    async def send_message(self, chat_id, text, **kw):
        if self._fail:
            raise RuntimeError("chat not found")
        self.sent.append((chat_id, text))


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


def _run_bot_tests():
    print("\n[8] bot.py — confirm tap sends to the TARGET, /relay log, inbound report")
    tmp, olds, db = _iso("bot")
    import bot as _bot
    old_auth, old_core, old_owner = _bot.auth, _bot.core, _bot.OWNER_CHAT_ID
    old_bot_mem = _bot.MEMORY_DIR
    try:
        _people(db)
        core, _ = _fresh_core("bot")
        # bot.py binds MEMORY_DIR at import time — point it at this test's
        # throwaway tree (production's never moves under a running bot).
        _bot.MEMORY_DIR = memory.MEMORY_DIR
        _bot.auth = _FakeAuth()
        _bot.core = core
        _bot.OWNER_CHAT_ID = OWNER

        target = relay.resolve_target(db, "Priya", memory.MEMORY_DIR)
        send_action = {"kind": "send", "alias": "Priya", "message": "Please send the report"}

        # ── the ✅ tap: the message goes to the TARGET's chat, not the owner's
        rid = core.relay.submit(send_action, target, owner_uid=OWNER)
        q = _FakeQuery(f"relay_ok_{rid}")
        ctx = _FakeContext()
        asyncio.run(_bot._cb_relay(q, ctx, q.data, OWNER, OWNER))
        check("confirmed send goes to the target's chat, not the owner's",
              ctx.bot.sent == [(PRIYA_UID, "Please send the report")], ctx.bot.sent)
        check("the owner's card is edited to a plain confirmation",
              q.edited_texts and "Priya" in q.edited_texts[-1], q.edited_texts)
        check("a confirmed send is recorded in the audit trail",
              core.relay.recent() and core.relay.recent()[0]["status"] == "sent",
              core.relay.recent()[:1])
        check("the proposal is gone after the tap", core.relay.pending() == [])

        # ── double tap
        q2 = _FakeQuery(f"relay_ok_{rid}")
        ctx2 = _FakeContext()
        asyncio.run(_bot._cb_relay(q2, ctx2, q2.data, OWNER, OWNER))
        check("a second tap sends nothing", ctx2.bot.sent == [], ctx2.bot.sent)
        check("a second tap says it expired or was already handled",
              q2.edited_texts and "expired" in q2.edited_texts[-1].lower(), q2.edited_texts)

        # ── a NON-owner can never resolve a relay
        rid2 = core.relay.submit(send_action, target, owner_uid=OWNER)
        q3 = _FakeQuery(f"relay_ok_{rid2}")
        ctx3 = _FakeContext()
        asyncio.run(_bot._cb_relay(q3, ctx3, q3.data, NON_OWNER, NON_OWNER))
        check("a non-owner tap sends nothing", ctx3.bot.sent == [], ctx3.bot.sent)
        check("a non-owner tap leaves the proposal held",
              len(core.relay.pending()) == 1, core.relay.pending())

        # ── ❌ tap: nothing sent, nothing logged
        q4 = _FakeQuery(f"relay_no_{rid2}")
        ctx4 = _FakeContext()
        before = len(core.relay.recent())
        asyncio.run(_bot._cb_relay(q4, ctx4, q4.data, OWNER, OWNER))
        check("declining sends nothing", ctx4.bot.sent == [], ctx4.bot.sent)
        check("declining says so plainly",
              q4.edited_texts and "Priya" in q4.edited_texts[-1], q4.edited_texts)
        check("a declined relay is never written to the audit trail",
              len(core.relay.recent()) == before, core.relay.recent()[:2])

        # ── send failure (they never started a chat with the bot)
        rid3 = core.relay.submit(send_action, target, owner_uid=OWNER)
        q5 = _FakeQuery(f"relay_ok_{rid3}")
        ctx5 = _FakeContext(bot=_FakeBot(fail=True))
        asyncio.run(_bot._cb_relay(q5, ctx5, q5.data, OWNER, OWNER))
        check("a failed send surfaces one calm line, no stack trace",
              q5.edited_texts and "couldn't reach" in q5.edited_texts[-1], q5.edited_texts)
        check("a failed send is recorded as failed",
              core.relay.recent()[0]["status"] == "failed", core.relay.recent()[:1])

        # ── /relay log
        u = _FakeUpdate(NON_OWNER)
        asyncio.run(_bot.cmd_relay(u, _FakeContext()))
        check("/relay is owner-only", u.message.sent == ["Owner only."], u.message.sent)

        u2 = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_relay(u2, _FakeContext()))
        out = u2.message.sent[0] if u2.message.sent else ""
        check("/relay log lists the relay actions with their target",
              "Priya" in out and "Please send the report" in out, out)

        core_empty, _ = _fresh_core("bot_empty", subscribe=False)
        # An empty log means an empty TABLE — use a core whose store is the
        # same test db, so clear it first.
        _store.get_store(config.DB_FILE)._write(
            lambda conn: conn.execute("DELETE FROM relay_log"))
        u3 = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_relay(u3, _FakeContext()))
        check("an empty relay log explains the confirm-first rule",
              u3.message.sent and "✅" in u3.message.sent[0], u3.message.sent)

        # ── K5.5 inbound: a known relay target is REPORTED, never run
        msg = _FakeMessage()
        msg.text = "Sent it just now"
        inbound = _FakeUpdate(PRIYA_UID, message=msg)
        ctx6 = _FakeContext()
        handled = asyncio.run(_bot._report_relay_inbound(inbound, ctx6, PRIYA_UID))
        check("a known relay target's message is handled as a report", handled is True)
        check("the owner gets one plain line with the verbatim text",
              ctx6.bot.sent and ctx6.bot.sent[0][0] == OWNER
              and "Priya said: Sent it just now" in ctx6.bot.sent[0][1], ctx6.bot.sent)

        photo_msg = _FakeMessage()
        photo_msg.photo = ["file-id"]
        inbound2 = _FakeUpdate(PRIYA_UID, message=photo_msg)
        ctx7 = _FakeContext()
        asyncio.run(_bot._report_relay_inbound(inbound2, ctx7, PRIYA_UID))
        check("media from a relay target is named, never ingested",
              ctx7.bot.sent and "photo (not saved)" in ctx7.bot.sent[0][1], ctx7.bot.sent)

        stranger = _FakeUpdate(31337, message=_FakeMessage())
        ctx8 = _FakeContext()
        handled2 = asyncio.run(_bot._report_relay_inbound(stranger, ctx8, 31337))
        check("an unknown sender is not a relay target (silent reject stands)",
              handled2 is False and ctx8.bot.sent == [], ctx8.bot.sent)
    finally:
        _bot.auth, _bot.core, _bot.OWNER_CHAT_ID = old_auth, old_core, old_owner
        _bot.MEMORY_DIR = old_bot_mem
        _restore(tmp, olds)


def _run_middleware_tests():
    print("\n[9] bot.auth_middleware — a relay target is reported AND still stopped dead")
    tmp, olds, db = _iso("mw")
    import bot as _bot
    from telegram.ext import ApplicationHandlerStop
    old_auth, old_owner, old_bot_mem = _bot.auth, _bot.OWNER_CHAT_ID, _bot.MEMORY_DIR
    try:
        _people(db)
        _bot.OWNER_CHAT_ID = OWNER
        _bot.MEMORY_DIR = memory.MEMORY_DIR

        class _Auth:
            def reload(self):
                pass

            def is_authorized(self, uid):
                return uid == OWNER

            def is_owner(self, uid):
                return uid == OWNER

        _bot.auth = _Auth()

        msg = _FakeMessage()
        msg.text = "on my way"
        update = _FakeUpdate(PRIYA_UID, message=msg)
        ctx = _FakeContext()

        stopped = False
        try:
            asyncio.run(_bot.auth_middleware(update, ctx))
        except ApplicationHandlerStop:
            stopped = True
        check("a relay target's message still stops before every handler — "
              "it never becomes a turn", stopped is True)
        check("...but the owner is told what they said",
              ctx.bot.sent and "Priya said: on my way" in ctx.bot.sent[0][1], ctx.bot.sent)

        stranger = _FakeUpdate(31337, message=_FakeMessage())
        ctx2 = _FakeContext()
        stopped2 = False
        try:
            asyncio.run(_bot.auth_middleware(stranger, ctx2))
        except ApplicationHandlerStop:
            stopped2 = True
        check("a stranger is still silently rejected", stopped2 is True and ctx2.bot.sent == [],
              ctx2.bot.sent)
    finally:
        _bot.auth, _bot.OWNER_CHAT_ID, _bot.MEMORY_DIR = old_auth, old_owner, old_bot_mem
        _restore(tmp, olds)


# ============================================================
#  10. The model is actually told how to relay
# ============================================================

def _run_preamble_tests():
    print("\n[10] harness — the relay protocol is taught on owner turns only")
    tmp, olds, db = _iso("preamble")
    try:
        _people(db)
        memory.ensure_tree()
        from zilla import harness as _harness
        owner_block = _harness._memory_block(_ctx())
        check("owner turn: the RELAY_SEND marker is taught",
              "RELAY_SEND:" in owner_block, owner_block[-400:])
        check("owner turn: the RELAY_SCHEDULE marker is taught",
              "RELAY_SCHEDULE:" in owner_block)
        check("owner turn: the model is told the owner must confirm first",
              "confirm" in owner_block.lower())
        check("non-owner turn: nothing about relaying is injected",
              _harness._memory_block(_ctx(NON_OWNER, is_owner=False)) == "")
    finally:
        _restore(tmp, olds)


if __name__ == "__main__":
    print("=" * 60)
    print("  PHASE K5 — TEAM RELAY TESTS")
    print("=" * 60)
    _run_parse_tests()
    _run_resolve_tests()
    _run_reverse_lookup_tests()
    _run_core_marker_tests()
    _run_hold_tests()
    _run_schedule_roundtrip_tests()
    _run_turn_tests()
    _run_bot_tests()
    _run_middleware_tests()
    _run_preamble_tests()
    print("\n" + "=" * 60)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 60)
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
