# ============================================================
#  TESTS — model switching + session management
# ============================================================
#  Deterministic, no-network tests for the two things that were
#  reported broken:
#    1. Model selection must WRITE agy's real settings.json and
#       READ BACK the persisted value (no silent lying).
#    2. Sessions must create / switch / delete / isolate correctly,
#       and a "new" session must genuinely start fresh
#       (conversation_id is None until the CLI makes its own).
#
#  Run:  python test_fixes.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  These tests point config at a THROWAWAY agy settings file via
#  AGY_SETTINGS_FILE so your real ~/.gemini settings are never touched.
# ============================================================

import os
import sys
import json
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


# ── Point config at a throwaway agy settings file BEFORE importing it ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_test_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({
        "model": "Gemini 3.1 Pro (High)",
        "toolPermission": "always-proceed",
        "trustedWorkspaces": ["C:\\Users\\Isha"],
    }, f, indent=2)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy

import config  # noqa: E402
from sessions import SessionManager  # noqa: E402


# ════════════════════════════════════════════════════════════
#  MODEL
# ════════════════════════════════════════════════════════════

def test_model_read_reflects_real_file():
    check("model: get_model reads agy's real file",
          config.get_model() == "Gemini 3.1 Pro (High)",
          f"got {config.get_model()!r}")


def test_model_set_persists_and_reads_back():
    target = "Gemini 2.5 Flash (Medium)"
    stored = config.set_model(target)
    # The return value is the read-back from disk — the truth shown to the user.
    check("model: set_model returns persisted value", stored == target,
          f"got {stored!r}")
    check("model: get_model now reflects new value", config.get_model() == target)
    # And it's actually on disk in agy's file.
    on_disk = json.load(open(_fake_agy, encoding="utf-8"))
    check("model: written to agy settings.json on disk",
          on_disk.get("model") == target, f"got {on_disk.get('model')!r}")


def test_model_set_preserves_other_keys():
    config.set_model("Gemini 3 Flash (Low)")
    on_disk = json.load(open(_fake_agy, encoding="utf-8"))
    check("model: toolPermission preserved",
          on_disk.get("toolPermission") == "always-proceed")
    check("model: trustedWorkspaces preserved",
          on_disk.get("trustedWorkspaces") == ["C:\\Users\\Isha"])


def test_model_fallback_when_file_missing(tmp_path_holder):
    # Repoint config at a non-existent file; get_model must fall back, not crash.
    missing = os.path.join(_tmpdir, "does_not_exist.json")
    config.AGY_SETTINGS_FILE = missing
    try:
        check("model: fallback when file missing",
              config.get_model() == config._AGY_MODEL_FALLBACK,
              f"got {config.get_model()!r}")
    finally:
        config.AGY_SETTINGS_FILE = _fake_agy


def test_model_catalog_format():
    # 5 models x 3 efforts = 15 distinct, correctly-formatted strings.
    combos = [config.model_display(base, eff)
              for _, base in config.AGY_MODELS for eff in config.AGY_EFFORTS]
    check("model: catalog has 15 combos", len(combos) == 15, f"got {len(combos)}")
    check("model: combos are unique", len(set(combos)) == 15)
    check("model: format matches agy 'Name (Effort)'",
          "Gemini 3.1 Pro (High)" in combos and "Gemini 2.5 Flash (Low)" in combos,
          str(combos))
    # The confirmed-valid string the user's agy was already using must be offered.
    check("model: confirmed-valid string is selectable",
          "Gemini 3.1 Pro (High)" in combos)


# ════════════════════════════════════════════════════════════
#  SESSIONS
# ════════════════════════════════════════════════════════════

def _mgr():
    path = os.path.join(_tmpdir, f"sessions_{os.urandom(4).hex()}.json")
    return SessionManager(path)


def test_session_create_is_fresh():
    sm = _mgr()
    uid = 111
    assert sm.create_session("work", uid)
    # A brand-new session must have NO conversation bound — the CLI will create
    # its own dir on first message. This is what makes "new session" truly fresh.
    check("session: new session has no conversation_id",
          sm.get_conversation_id(uid, "work") is None)
    check("session: new session becomes active", sm.get_active_name(uid) == "work")
    check("session: last_seen_step starts at 0",
          sm.get_last_seen_step(uid) == 0)


def test_session_isolation_between_sessions():
    sm = _mgr()
    uid = 222
    sm.create_session("alpha", uid)
    sm.set_conversation_id("conv-alpha", uid, "alpha")
    sm.create_session("beta", uid)          # switches active to beta
    sm.set_conversation_id("conv-beta", uid, "beta")
    check("session: alpha keeps its own conv",
          sm.get_conversation_id(uid, "alpha") == "conv-alpha")
    check("session: beta keeps its own conv",
          sm.get_conversation_id(uid, "beta") == "conv-beta")
    # Switching back must restore alpha's conversation, not bleed beta's.
    sm.set_active_name("alpha", uid)
    check("session: switch restores correct active conv",
          sm.get_conversation_id(uid) == "conv-alpha")


def test_session_isolation_between_users():
    sm = _mgr()
    sm.create_session("shared", 1)
    sm.set_conversation_id("conv-u1", 1, "shared")
    sm.create_session("shared", 2)
    sm.set_conversation_id("conv-u2", 2, "shared")
    check("session: same name, different users isolated (u1)",
          sm.get_conversation_id(1, "shared") == "conv-u1")
    check("session: same name, different users isolated (u2)",
          sm.get_conversation_id(2, "shared") == "conv-u2")


def test_session_delete():
    sm = _mgr()
    uid = 333
    sm.create_session("keep", uid)
    sm.create_session("trash", uid)        # active = trash
    check("session: delete returns True for existing", sm.delete_session("trash", uid))
    check("session: deleted session is gone", "trash" not in sm.list_sessions(uid))
    check("session: active reassigned after deleting active",
          sm.get_active_name(uid) != "trash")
    check("session: delete returns False for missing",
          sm.delete_session("ghost", uid) is False)


def test_session_delete_does_not_touch_other_users():
    sm = _mgr()
    sm.create_session("s", 10)
    sm.create_session("s", 20)
    sm.delete_session("s", 10)
    check("session: deleting one user's session leaves the other's",
          "s" in sm.list_sessions(20) and "s" not in sm.list_sessions(10))


def test_session_persists_to_disk():
    path = os.path.join(_tmpdir, "persist.json")
    sm = SessionManager(path)
    sm.create_session("dur", 99)
    sm.set_conversation_id("conv-dur", 99, "dur")
    # Reload from disk in a fresh manager — state must survive.
    sm2 = SessionManager(path)
    check("session: survives reload from disk",
          sm2.get_conversation_id(99, "dur") == "conv-dur")


# ════════════════════════════════════════════════════════════
#  AUTH — two-role model (owner + admin) + owner-gated model
# ════════════════════════════════════════════════════════════

from users import AuthManager  # noqa: E402


def _auth(owner=1000):
    path = os.path.join(_tmpdir, f"users_{os.urandom(4).hex()}.json")
    return AuthManager(path, owner_id=owner)


def test_auth_authorized_user_is_admin():
    a = _auth()
    a.add_user(2000, "Bob")              # no role arg -> admin
    check("auth: added account is admin role",
          a.list_users()[2000]["role"] == "admin")
    check("auth: admin has chat cap", a.can(2000, "chat"))
    check("auth: admin has admin cap", a.can(2000, "admin"))
    check("auth: admin lacks users cap", not a.can(2000, "users"))


def test_auth_owner_has_everything():
    a = _auth(owner=1000)
    check("auth: owner has users cap", a.can(1000, "users"))
    check("auth: owner has admin cap", a.can(1000, "admin"))


def test_auth_old_user_role_migrates():
    # Write a legacy file with a "user" role, then load it.
    path = os.path.join(_tmpdir, "legacy_users.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"3000": {"name": "Old", "role": "user"}}, f)
    a = AuthManager(path, owner_id=1000)
    check("auth: legacy 'user' role migrates to admin",
          a.list_users()[3000]["role"] == "admin" and a.can(3000, "admin"))


def test_can_change_model():
    a = _auth(owner=1000)
    a.add_user(2000, "Bob")
    check("model-gate: owner True when admins allowed", a.can_change_model(1000, True))
    check("model-gate: owner True even when admins disallowed", a.can_change_model(1000, False))
    check("model-gate: admin True when allowed", a.can_change_model(2000, True))
    check("model-gate: admin False when disallowed", not a.can_change_model(2000, False))
    check("model-gate: stranger False", not a.can_change_model(9999, True))


# ════════════════════════════════════════════════════════════
#  INBOX — classification, filtering, counts
# ════════════════════════════════════════════════════════════

import media  # noqa: E402


def _setup_inbox():
    base = os.path.join(_tmpdir, f"inbox_{os.urandom(4).hex()}")
    img = os.path.join(base, "images"); aud = os.path.join(base, "audio"); doc = os.path.join(base, "documents")
    for d in (img, aud, doc):
        os.makedirs(d, exist_ok=True)
    open(os.path.join(img, "p1.jpg"), "w").close()
    open(os.path.join(aud, "v1.ogg"), "w").close()
    open(os.path.join(doc, "report.pdf"), "w").close()
    open(os.path.join(doc, "clip.mp4"), "w").close()   # video, lives in documents folder
    open(os.path.join(doc, "movie.MKV"), "w").close()  # video, uppercase ext
    media.INBOX_IMAGES, media.INBOX_AUDIO, media.INBOX_DOCUMENTS = img, aud, doc
    return img, aud, doc


def test_inbox_classifies_video_by_extension():
    _setup_inbox()
    vids = {i["name"] for i in media.get_inbox_items("video")}
    docs = {i["name"] for i in media.get_inbox_items("documents")}
    check("inbox: videos split out of documents by ext",
          vids == {"clip.mp4", "movie.MKV"}, str(vids))
    check("inbox: documents excludes videos", docs == {"report.pdf"}, str(docs))


def test_inbox_counts():
    _setup_inbox()
    counts = media.get_inbox_counts()
    check("inbox: counts per category",
          counts == {"images": 1, "audio": 1, "video": 2, "documents": 1}, str(counts))
    # Back-compat stats fold video into documents.
    stats = media.get_inbox_stats()
    check("inbox: legacy stats fold video into documents",
          stats == {"images": 1, "audio": 1, "documents": 3}, str(stats))


def test_inbox_filter_returns_only_category():
    _setup_inbox()
    aud = media.get_inbox_items("audio")
    check("inbox: filter returns only that category",
          len(aud) == 1 and aud[0]["category"] == "audio")


def test_inbox_delete_file():
    img, aud, doc = _setup_inbox()
    target = os.path.join(doc, "report.pdf")
    check("inbox-del: file exists before", os.path.exists(target))
    check("inbox-del: deletes a real inbox file", media.delete_inbox_file(target) is True)
    check("inbox-del: file gone after", not os.path.exists(target))
    # Security: must REFUSE to delete a path outside the inbox folders.
    outside = os.path.join(_tmpdir, "outside_secret.txt")
    open(outside, "w").close()
    check("inbox-del: refuses path outside inbox", media.delete_inbox_file(outside) is False)
    check("inbox-del: outside file untouched", os.path.exists(outside))


# ════════════════════════════════════════════════════════════
#  SCHEDULES — next-run math, due selection, persistence, catch-up
# ════════════════════════════════════════════════════════════

from datetime import datetime, timedelta  # noqa: E402
import schedules as sched_mod  # noqa: E402
from schedules import ScheduleManager, compute_next_run  # noqa: E402
from schedule_parse import parse_schedule, parse_schedule_command  # noqa: E402
from cli_engine import detect_limit  # noqa: E402


def _epoch(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi).timestamp()


def test_next_run_daily():
    # before today's slot -> today; after -> tomorrow
    base = datetime(2026, 6, 2, 8, 0)
    nxt = compute_next_run("daily", {"hh": 9, "mm": 0}, base.timestamp())
    check("sched: daily before slot -> today 09:00",
          datetime.fromtimestamp(nxt) == datetime(2026, 6, 2, 9, 0))
    base2 = datetime(2026, 6, 2, 10, 0)
    nxt2 = compute_next_run("daily", {"hh": 9, "mm": 0}, base2.timestamp())
    check("sched: daily after slot -> tomorrow 09:00",
          datetime.fromtimestamp(nxt2) == datetime(2026, 6, 3, 9, 0))


def test_next_run_interval_and_once():
    after = _epoch(2026, 6, 2, 8, 0)
    check("sched: interval adds seconds",
          compute_next_run("interval", {"seconds": 18000}, after) == after + 18000)
    future = after + 100
    check("sched: once future returns run_at",
          compute_next_run("once", {"run_at": future}, after) == future)
    check("sched: once past returns None",
          compute_next_run("once", {"run_at": after - 100}, after) is None)


def test_next_run_weekly():
    # 2026-06-02 is a Tuesday (weekday 1). Want Wednesday (2) at 09:00 -> next day.
    base = datetime(2026, 6, 2, 8, 0)
    nxt = compute_next_run("weekly", {"days": [2], "hh": 9, "mm": 0}, base.timestamp())
    got = datetime.fromtimestamp(nxt)
    check("sched: weekly picks next chosen weekday",
          got == datetime(2026, 6, 3, 9, 0), str(got))


def _sm():
    return ScheduleManager(os.path.join(_tmpdir, f"sched_{os.urandom(4).hex()}.json"))


def test_schedule_add_due_touch():
    sm = _sm()
    now = _epoch(2026, 6, 2, 8, 0)
    s = sm.add(1, 1, "do x", "daily", {"hh": 9, "mm": 0}, now=now)
    first_next = s["next_run"]   # capture BEFORE touch_run mutates the live dict
    check("sched: add returns schedule with future next_run", s and first_next > now)
    check("sched: not due before next_run", sm.due(now) == [])
    check("sched: due at/after next_run", len(sm.due(first_next + 1)) == 1)
    sm.touch_run(s["id"], now=first_next + 1)
    check("sched: touch_run advances ~24h",
          abs(sm.get(s["id"])["next_run"] - (first_next + 86400)) < 5)
    check("sched: touch_run records last_run", sm.get(s["id"])["last_run"] is not None)


def test_schedule_once_disables_after_run():
    sm = _sm()
    now = _epoch(2026, 6, 2, 8, 0)
    s = sm.add(1, 1, "ping", "once", {"run_at": now + 60}, now=now)
    sm.touch_run(s["id"], now=now + 61)
    check("sched: once disabled after run", sm.get(s["id"])["enabled"] is False)
    check("sched: once has no next_run after run", sm.get(s["id"])["next_run"] is None)


def test_schedule_reconcile_catchup():
    now = _epoch(2026, 6, 2, 10, 0)
    # Build a schedule whose next_run is already in the past.
    sm = _sm()
    s = sm.add(1, 1, "x", "daily", {"hh": 9, "mm": 0}, now=_epoch(2026, 6, 2, 8, 0))
    # force past-due
    sm.get(s["id"])["next_run"] = now - 100
    # catchup ON -> stays due
    sm.reconcile_startup(now=now, catchup=True)
    check("sched: catchup ON leaves past-due due", len(sm.due(now)) == 1)
    # catchup OFF -> advanced to future, not due
    sm.reconcile_startup(now=now, catchup=False)
    check("sched: catchup OFF advances past-due", sm.due(now) == [])
    check("sched: catchup OFF kept it enabled with future run",
          sm.get(s["id"])["enabled"] and sm.get(s["id"])["next_run"] > now)


def test_schedule_remove_and_owner_scope():
    sm = _sm()
    s = sm.add(7, 7, "x", "interval", {"seconds": 3600})
    check("sched: other user can't remove", sm.remove(s["id"], 999) is False)
    check("sched: owner removes", sm.remove(s["id"], 7) is True)
    check("sched: list empty after remove", sm.list(7) == [])


# ════════════════════════════════════════════════════════════
#  NL PARSER + LIMIT DETECTION
# ════════════════════════════════════════════════════════════

def test_parse_schedule_forms():
    fixed = datetime(2026, 6, 2, 8, 0)
    p = parse_schedule("every 5 hours check the news", fixed)
    check("parse: interval 5h", p and p["kind"] == "interval" and p["spec"]["seconds"] == 18000, str(p))
    p = parse_schedule("daily at 9am summarise my inbox", fixed)
    check("parse: daily 9am", p and p["kind"] == "daily" and p["spec"] == {"hh": 9, "mm": 0}, str(p))
    p = parse_schedule("every day at 18:30 post update", fixed)
    check("parse: daily 18:30", p and p["kind"] == "daily" and p["spec"]["hh"] == 18 and p["spec"]["mm"] == 30, str(p))
    p = parse_schedule("on mon,wed at 09:00 standup", fixed)
    check("parse: weekly mon,wed", p and p["kind"] == "weekly" and p["spec"]["days"] == [0, 2], str(p))
    p = parse_schedule("in 30 minutes ping me", fixed)
    check("parse: once in 30m", p and p["kind"] == "once", str(p))
    p = parse_schedule("remind me to call mom at 9am", fixed)
    check("parse: remind-me cue", p and p["kind"] == "once" and "call mom" in p["title"], str(p))
    check("parse: plain text is not a schedule",
          parse_schedule("what is the weather today", fixed) is None)


def test_parse_schedule_command_forms():
    fixed = datetime(2026, 6, 2, 8, 0)
    p = parse_schedule_command("daily 09:00 summarise inbox", fixed)
    check("cmd: daily 09:00", p and p["kind"] == "daily" and p["spec"]["hh"] == 9, str(p))
    p = parse_schedule_command("every 5h check news", fixed)
    check("cmd: every 5h", p and p["kind"] == "interval" and p["spec"]["seconds"] == 18000, str(p))
    p = parse_schedule_command("once 2026-12-31 23:59 party time", fixed)
    check("cmd: once explicit date", p and p["kind"] == "once", str(p))
    p = parse_schedule_command("mon,fri 08:00 gym", fixed)
    check("cmd: weekday list", p and p["kind"] == "weekly" and p["spec"]["days"] == [0, 4], str(p))


def test_detect_limit():
    check("limit: rate limit", detect_limit("You hit the rate limit, slow down") is not None)
    check("limit: 429", detect_limit("HTTP 429 Too Many Requests") is not None)
    check("limit: resource exhausted", detect_limit("Error: RESOURCE_EXHAUSTED") is not None)
    check("limit: overloaded", detect_limit("The model is overloaded right now") is not None)
    check("limit: normal text -> None", detect_limit("Here is your answer, all good.") is None)
    check("limit: empty -> None", detect_limit("") is None)


# ════════════════════════════════════════════════════════════
#  BACKENDS + CROSS-PLATFORM
# ════════════════════════════════════════════════════════════

import backends  # noqa: E402
import platform_compat as pc  # noqa: E402


def test_claude_json_parsing():
    sample = ('{"type":"result","subtype":"success","is_error":false,'
              '"result":"PONG","session_id":"abc-123"}')
    resp, sid = backends._parse_claude_json(sample, None)
    check("claude: parses result", resp == "PONG", repr(resp))
    check("claude: parses session_id", sid == "abc-123", repr(sid))
    # error payload
    err = '{"type":"result","is_error":true,"result":"rate limit hit","session_id":"x"}'
    r2, s2 = backends._parse_claude_json(err, None)
    check("claude: error payload surfaces text", "rate limit" in r2.lower(), repr(r2))
    # non-JSON fallback keeps prior conv id and returns text
    r3, s3 = backends._parse_claude_json("just text, not json", "prev-id")
    check("claude: non-json fallback", "just text" in r3 and s3 == "prev-id")
    # empty
    r4, _ = backends._parse_claude_json("", None)
    check("claude: empty -> message", "No response" in r4)


def test_model_catalog_per_backend():
    orig = config.get_backend
    try:
        config.get_backend = lambda: "claude"
        cat = config.model_catalog()
        vals = [v for _, v in cat]
        check("catalog: claude has opus/sonnet/haiku",
              vals == ["opus", "sonnet", "haiku"], str(vals))
        config.get_backend = lambda: "agy"
        check("catalog: agy has 15 gemini combos", len(config.model_catalog()) == 15)
    finally:
        config.get_backend = orig


def test_platform_compat_lock():
    check("pc: running on Windows in this test env", pc.IS_WINDOWS is True)
    lockp = os.path.join(_tmpdir, f"lock_{os.urandom(3).hex()}.lock")
    h1 = pc.acquire_instance_lock(lockp)
    check("pc: first lock acquired", h1 is not None)
    h2 = pc.acquire_instance_lock(lockp)
    check("pc: second lock refused (single instance)", h2 is None)
    pc.acquire_instance_lock  # noqa
    pc.release_instance_lock(h1, lockp)
    h3 = pc.acquire_instance_lock(lockp)
    check("pc: lock reacquired after release", h3 is not None)
    pc.release_instance_lock(h3, lockp)


def test_platform_compat_imports_clean():
    # PtyProcess class exists and window-hiding is a safe no-op to call.
    check("pc: PtyProcess present", hasattr(pc, "PtyProcess"))
    try:
        pc.apply_window_hiding()
        check("pc: apply_window_hiding callable", True)
    except Exception as e:
        check("pc: apply_window_hiding callable", False, repr(e))


# ════════════════════════════════════════════════════════════

def main():
    tests = [
        test_model_read_reflects_real_file,
        test_model_set_persists_and_reads_back,
        test_model_set_preserves_other_keys,
        lambda: test_model_fallback_when_file_missing(None),
        test_model_catalog_format,
        test_session_create_is_fresh,
        test_session_isolation_between_sessions,
        test_session_isolation_between_users,
        test_session_delete,
        test_session_delete_does_not_touch_other_users,
        test_session_persists_to_disk,
        test_auth_authorized_user_is_admin,
        test_auth_owner_has_everything,
        test_auth_old_user_role_migrates,
        test_can_change_model,
        test_inbox_classifies_video_by_extension,
        test_inbox_counts,
        test_inbox_filter_returns_only_category,
        test_inbox_delete_file,
        test_next_run_daily,
        test_next_run_interval_and_once,
        test_next_run_weekly,
        test_schedule_add_due_touch,
        test_schedule_once_disables_after_run,
        test_schedule_reconcile_catchup,
        test_schedule_remove_and_owner_scope,
        test_parse_schedule_forms,
        test_parse_schedule_command_forms,
        test_detect_limit,
        test_claude_json_parsing,
        test_model_catalog_per_backend,
        test_platform_compat_lock,
        test_platform_compat_imports_clean,
    ]
    print("Running zilla fix tests...\n")
    for t in tests:
        try:
            t()
        except Exception as e:
            global _failed
            _failed += 1
            print(f"  ERROR {getattr(t, '__name__', t)}: {e!r}")
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
