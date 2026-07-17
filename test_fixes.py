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


# ── Isolate config BEFORE importing it ──
# These tests exercise the agy model path and must not read or write the real
# repo state: force the agy backend, point the agy settings + the bot settings
# files at throwaway locations. (Without forcing the backend, a real .env with
# BACKEND=claude would route set_model into claude_model and pollute the live
# settings.json — exactly the bug that surfaced once a Mac .env existed.)
_tmpdir = tempfile.mkdtemp(prefix="zilla_test_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({
        "model": "Gemini 3.1 Pro (High)",
        "toolPermission": "always-proceed",
        "trustedWorkspaces": ["C:\\Users\\Isha"],
    }, f, indent=2)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"            # set before import; .env can't override (setdefault)

import config  # noqa: E402

# Redirect the bot's own settings KV to a throwaway file so set_setting / the
# claude_model path can't write the real settings.json during the run.
config.SETTINGS_FILE = os.path.join(_tmpdir, "bot_settings.json")
config._settings_cache = None
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


def test_agy_models_parse_and_fallback():
    # Parser pulls real "Name (Effort)" lines, ignores headers/blanks.
    raw = ("Gemini 3.5 Flash (Medium)\nGemini 3.1 Pro (High)\n"
           "Claude Opus 4.6 (Thinking)\nGPT-OSS 120B (Medium)\n\n  \nUsage: foo")
    parsed = config._parse_agy_models(raw)
    check("agy: parser extracts real models",
          parsed == ["Gemini 3.5 Flash (Medium)", "Gemini 3.1 Pro (High)",
                     "Claude Opus 4.6 (Thinking)", "GPT-OSS 120B (Medium)"],
          str(parsed))
    check("agy: parser drops the 'Usage' header line",
          all("Usage" not in p for p in parsed))
    # Live list falls back (not crashes, no fakes) when the binary can't be run.
    orig = config._run_agy_models
    try:
        config._run_agy_models = lambda timeout=8.0: None
        config._agy_models_cache.update(val=None, ts=0.0, live=False)
        live = config.agy_models_live(force=True)
        check("agy: falls back to offline real list",
              live == config.AGY_MODELS_FALLBACK, str(live[:2]))
        check("agy: reachable=False when binary unavailable",
              config.agy_reachable() is False)
        # When the binary DOES return data, that's used and reachable=True.
        config._run_agy_models = lambda timeout=8.0: "Gemini 3.1 Pro (Low)\nGemini 3.1 Pro (High)"
        live = config.agy_models_live(force=True)
        check("agy: uses live data when available",
              live == ["Gemini 3.1 Pro (Low)", "Gemini 3.1 Pro (High)"], str(live))
        check("agy: reachable=True with live data", config.agy_reachable() is True)
    finally:
        config._run_agy_models = orig
        config._agy_models_cache.update(val=None, ts=0.0, live=False)


def test_agy_label_compact():
    # Button labels stay short and readable for a phone.
    check("agy: label gemini", config._agy_label("Gemini 3.5 Flash (Medium)") == "3.5 Flash·Med",
          config._agy_label("Gemini 3.5 Flash (Medium)"))
    check("agy: label claude thinking",
          config._agy_label("Claude Opus 4.6 (Thinking)") == "Opus 4.6·Think",
          config._agy_label("Claude Opus 4.6 (Thinking)"))
    check("agy: no fake uniform efforts (fallback has per-model levels)",
          "Gemini 3.1 Pro (Medium)" not in config.AGY_MODELS_FALLBACK)


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


def test_auth_limited_role():
    a = _auth(owner=1000)
    a.add_user(4000, "Student", role="limited")
    check("limited: stored as limited", a.list_users()[4000]["role"] == "limited")
    check("limited: is authorized", a.is_authorized(4000))
    check("limited: is_limited True", a.is_limited(4000))
    check("limited: has chat cap", a.can(4000, "chat"))
    check("limited: lacks admin cap", not a.can(4000, "admin"))
    check("limited: lacks users cap", not a.can(4000, "users"))
    check("limited: can't change model even if admins allowed",
          not a.can_change_model(4000, True))
    check("limited: role_of == limited", a.role_of(4000) == "limited")
    check("limited: owner is not limited", not a.is_limited(1000))
    check("limited: stranger is not limited", not a.is_limited(9999))


def test_auth_set_role_toggle():
    a = _auth(owner=1000)
    a.add_user(4001, "Ada")                       # default admin
    check("toggle: starts admin", a.role_of(4001) == "admin")
    check("toggle: -> limited ok", a.set_role(4001, "limited"))
    check("toggle: now limited + no admin cap",
          a.is_limited(4001) and not a.can(4001, "admin"))
    check("toggle: -> admin ok", a.set_role(4001, "admin"))
    check("toggle: admin cap restored", a.can(4001, "admin"))
    check("toggle: invalid role rejected", not a.set_role(4001, "superuser"))
    check("toggle: unknown user rejected", not a.set_role(123456, "limited"))


def test_auth_limited_role_persists():
    path = os.path.join(_tmpdir, f"lim_{os.urandom(4).hex()}.json")
    a = AuthManager(path, owner_id=1000)
    a.add_user(4002, "Sam", role="limited")
    b = AuthManager(path, owner_id=1000)          # reload from disk
    check("limited: survives reload", b.is_limited(4002))


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
#  OUTBOX — browse/send/delete of agent-produced files (v4.6)
# ════════════════════════════════════════════════════════════

def _setup_outbox():
    base = os.path.join(_tmpdir, f"outbox_{os.urandom(4).hex()}")
    img = os.path.join(base, "images"); doc = os.path.join(base, "documents")
    for d in (img, doc):
        os.makedirs(d, exist_ok=True)
    open(os.path.join(img, "chart.png"), "w").close()
    open(os.path.join(doc, "report.pdf"), "w").close()
    open(os.path.join(doc, "sales.xlsx"), "w").close()
    open(os.path.join(doc, "clip.mp4"), "w").close()   # video lives under documents
    media.OUTBOX_IMAGES, media.OUTBOX_DOCUMENTS = img, doc
    return img, doc


def test_outbox_lists_and_classifies():
    _setup_outbox()
    counts = media.get_outbox_counts()
    check("outbox: counts per category",
          counts == {"images": 1, "video": 1, "documents": 2}, str(counts))
    vids = {i["name"] for i in media.get_outbox_items("video")}
    check("outbox: video split out of documents", vids == {"clip.mp4"}, str(vids))


def test_outbox_delete_is_path_scoped():
    _, doc = _setup_outbox()
    target = os.path.join(doc, "report.pdf")
    check("outbox-del: file exists before", os.path.exists(target))
    check("outbox-del: deletes a real outbox file", media.delete_outbox_file(target) is True)
    check("outbox-del: file gone after", not os.path.exists(target))
    outside = os.path.join(_tmpdir, "outbox_outside_secret.txt")
    open(outside, "w").close()
    check("outbox-del: refuses path outside outbox", media.delete_outbox_file(outside) is False)
    check("outbox-del: outside file untouched", os.path.exists(outside))


def test_detect_file_paths_posix():
    # The Windows-only extractor silently delivered ZERO files on macOS/Linux.
    # This guards the cross-platform delivery fix.
    from formatter import detect_file_paths
    base = os.path.join(_tmpdir, f"deliver_{os.urandom(4).hex()}")
    os.makedirs(base, exist_ok=True)
    f1 = os.path.join(base, "report.pdf"); open(f1, "w").close()
    f2 = os.path.join(base, "sales.xlsx"); open(f2, "w").close()
    msg = f"Here are your files:\n• {f1}\n• {f2}\nDone."
    got = detect_file_paths(msg)
    check("deliver: POSIX bullet paths detected", f1 in got and f2 in got, str(got))
    check("deliver: only existing files returned",
          detect_file_paths(f"see /no/such/file_{os.urandom(3).hex()}.pdf") == [])


# ════════════════════════════════════════════════════════════
#  SCHEDULES — next-run math, due selection, persistence, catch-up
# ════════════════════════════════════════════════════════════

from datetime import datetime, timedelta  # noqa: E402
import schedules as sched_mod  # noqa: E402
from schedules import ScheduleManager, compute_next_run, RETRY_LADDER  # noqa: E402
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
    # Created at 08:00 for a 09:00 daily slot -> next_run is already 09:00
    # today, which is before `now` (10:00): naturally past-due already.
    # catchup ON -> stays due
    sm.reconcile_startup(now=now, catchup=True)
    check("sched: catchup ON leaves past-due due", len(sm.due(now)) == 1)
    # catchup OFF -> advanced to future, not due
    sm.reconcile_startup(now=now, catchup=False)
    check("sched: catchup OFF advances past-due", sm.due(now) == [])
    check("sched: catchup OFF kept it enabled with future run",
          sm.get(s["id"])["enabled"] and sm.get(s["id"])["next_run"] > now)


def test_schedule_failure_retry():
    sm = _sm()
    now = _epoch(2026, 6, 2, 8, 0)
    s = sm.add(1, 1, "x", "daily", {"hh": 9, "mm": 0}, now=now)
    sid = s["id"]
    # Walk the full retry ladder (30s/60s/5m/15m/60m) → retry soon each time,
    # next_run = now + that rung's delay.
    for i, delay in enumerate(RETRY_LADDER, start=1):
        outcome, attempt = sm.mark_failure(sid, now=now)
        check(f"sched: failure {i} → retry", outcome == "retry" and attempt == i, f"{outcome},{attempt}")
        check(f"sched: retry {i} sets next_run to ladder delay {delay}s",
              abs(sm.get(sid)["next_run"] - (now + delay)) < 5)
    # One more failure exhausts the ladder → give up THIS occurrence, advance, reset.
    outcome, attempt = sm.mark_failure(sid, now=now)
    check("sched: exhausted ladder → gaveup",
          outcome == "gaveup" and attempt == len(RETRY_LADDER) + 1, f"{outcome},{attempt}")
    check("sched: gaveup resets fail_count", sm.get(sid).get("fail_count") == 0)
    check("sched: gaveup advances to a future run", sm.get(sid)["next_run"] > now)
    # Success resets the counter and advances normally.
    s2 = sm.add(2, 2, "y", "interval", {"seconds": 3600}, now=now)
    sm.mark_failure(s2["id"], now=now)
    sm.mark_success(s2["id"], now=now)
    check("sched: success resets fail_count", sm.get(s2["id"]).get("fail_count") == 0)
    check("sched: success advances ~interval",
          abs(sm.get(s2["id"])["next_run"] - (now + 3600)) < 5)
    # A one-off that keeps failing gives up AND disables (no future occurrence)
    # once the full ladder is exhausted.
    s3 = sm.add(3, 3, "z", "once", {"run_at": now + 60}, now=now)
    for _ in range(len(RETRY_LADDER)):
        sm.mark_failure(s3["id"], now=now + 61)  # walk the ladder, not yet exhausted
    o, _ = sm.mark_failure(s3["id"], now=now + 61)
    check("sched: failed one-off gives up + disables",
          o == "gaveup" and sm.get(s3["id"])["enabled"] is False)


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
    # Spoken cue variants (live-smoke failure 2026-07-17: "put a reminder for
    # 2 minutes" fell through to the agent, which slept 2 minutes and replied —
    # no schedule, no confirmation).
    p = parse_schedule("put a reminder for 2 minutes to drink water", fixed)
    check("parse: put-a-reminder for-N", p and p["kind"] == "once" and "drink water" in p["title"], str(p))
    p = parse_schedule("keep a reminder for me in 5 minutes check the stove", fixed)
    check("parse: keep-a-reminder", p and p["kind"] == "once" and "stove" in p["title"], str(p))
    p = parse_schedule("set a timer for 2 minutes", fixed)
    check("parse: bare timer gets default task", p and p["kind"] == "once" and p["title"] == "Time's up!", str(p))
    p = parse_schedule("set an alarm for 10 minutes to leave for class", fixed)
    check("parse: alarm cue", p and p["kind"] == "once" and "leave for class" in p["title"], str(p))
    check("parse: bare 'for 2 minutes' without cue is NOT a schedule",
          parse_schedule("for 2 minutes nothing happened", fixed) is None)
    # Reminder-style requests deliver their text verbatim at fire time
    # (payload_hint system_event, zero model call); "schedule <task>" still
    # means "run this task through the agent".
    p = parse_schedule("put a reminder for 2 minutes to drink water", fixed)
    check("parse: reminder hints system_event", p and p.get("payload_hint") == "system_event", str(p))
    p = parse_schedule("remind me in 2 minutes to drink water", fixed)
    check("parse: remind-me hints system_event", p and p.get("payload_hint") == "system_event", str(p))
    p = parse_schedule("schedule in 30 minutes summarise my inbox", fixed)
    check("parse: schedule-task hints message", p and p.get("payload_hint") == "message", str(p))
    # Spelled-out numbers must parse like digits (the "every three minutes"
    # failure: without this the whole message fell through to the agent and a
    # trivial request spun for many minutes instead of becoming a schedule).
    p = parse_schedule("Schedule every three minutes, send me a screenshot", fixed)
    check("parse: interval word 'three'",
          p and p["kind"] == "interval" and p["spec"]["seconds"] == 180
          and p["prompt"] == "send me a screenshot", str(p))
    p = parse_schedule("every twenty five minutes ping me", fixed)
    check("parse: interval word 'twenty five'",
          p and p["kind"] == "interval" and p["spec"]["seconds"] == 1500, str(p))
    p = parse_schedule("every five hours check the news", fixed)
    check("parse: interval word 'five hours'",
          p and p["kind"] == "interval" and p["spec"]["seconds"] == 18000, str(p))
    # Number-word rewriting must not maul ordinary words or real tasks.
    from schedule_parse import normalize_numbers
    check("parse: 'a screenshot' stays a word",
          normalize_numbers("send me a screenshot") == "send me a screenshot")
    check("parse: 'someone' not rewritten",
          normalize_numbers("ask someone anyone") == "ask someone anyone")


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


def test_verify_gate():
    from verify import assess, looks_like_data_request
    # The 1500-bookings failure mode: data request answered with a dense,
    # unsourced dataset → must flag.
    fab = ("get me the booking data for all customers",
           "1. A $1,250 2026-01-03\n2. B $890 2026-01-04\n3. C $4,300\n"
           "4. D $220\n5. E $1,910\n6. F $760\nTotal: 1500 bookings $1,284,300")
    check("verify: flags fabricated dataset", assess(*fab) is not None)
    # Honest failure → never flag (good behavior).
    check("verify: honest failure not flagged",
          assess("get the booking data", "I FAILED to fetch it — no data source available.") is None)
    # Creative task → not a data request → never flag.
    check("verify: creative not flagged",
          assess("write a poem about the sea", "The waves roll soft against the shore.") is None)
    # User supplied the data → grounded → never flag.
    check("verify: grounded (uploaded) not flagged",
          assess("how many rows in this file?", "This file has 1,512 rows across 6 columns.") is None)
    # Cited a source → never flag.
    check("verify: sourced answer not flagged",
          assess("how many bookings today?",
                 "According to the bookings API I queried, there were 1,247 today.") is None)
    # General knowledge with a stray number → not the fabrication shape.
    check("verify: general knowledge not flagged",
          assess("how many planets are there?", "There are 8 planets.") is None)
    check("verify: data-request intent detection",
          looks_like_data_request("how many orders did we get") and
          not looks_like_data_request("tell me a joke"))


def test_autoharness_classify():
    from autoharness import classify, plan_directive, SIMPLE, COMPLEX
    simple = ["hi", "what is 2+2", "translate hello to french", "define entropy",
              "who wrote hamlet", "convert 5km to miles"]
    cplx = ["build me a todo app", "research the best laptops under 1000",
            "get the sales data from the sheet", "make an apk that tracks water",
            "scrape the prices and put them in a spreadsheet",
            "first find the data then create a report"]
    for m in simple:
        check(f"auto: simple «{m[:18]}»", classify(m) == SIMPLE, classify(m))
    for m in cplx:
        check(f"auto: complex «{m[:18]}»", classify(m) == COMPLEX, classify(m))
    check("auto: simple → no directive", plan_directive("hi") == "")
    check("auto: complex → execution directive",
          "EXECUTION MODE" in plan_directive("build an app"))
    check("auto: empty → simple", classify("") == SIMPLE)


def test_needs_browser():
    from autoharness import needs_browser, plan_directive
    # Web/interactive intent → attach the browser.
    web = ["browse to example.com and tell me the heading",
           "find me 3 cotton night suits online with links and prices",
           "order me a night suit", "log into my gmail", "sign into my account",
           "go to amazon.in and check prices", "what's the cheapest flight online",
           "click the submit button", "book me a table for two",
           "add it to my cart and checkout"]
    # Non-web work (incl. non-web COMPLEX tasks) → no browser, stays fast.
    noweb = ["hi", "what is 2+2", "explain recursion", "build me a todo app",
             "summarize this PDF", "write a python script to rename files",
             "search the codebase for the bug", "download the model weights"]
    for m in web:
        check(f"browser: yes «{m[:18]}»", needs_browser(m) is True, needs_browser(m))
    for m in noweb:
        check(f"browser: no «{m[:18]}»", needs_browser(m) is False, needs_browser(m))
    check("browser: empty → no", needs_browser("") is False)
    # Browser directive only appears when the browser is actually attached.
    check("browser: directive gated off",
          "WEB / BROWSER" not in plan_directive("build me a todo app"))
    check("browser: directive present when web",
          "WEB / BROWSER" in plan_directive("order me a night suit online"))


def test_mcp_configs_generated():
    import json as _json
    from config import MCP_BROWSER_CONFIG, MCP_NONE_CONFIG, PLAYWRIGHT_MCP_VERSION
    backends._ensure_mcp_configs()
    with open(MCP_BROWSER_CONFIG) as f:
        b = _json.load(f)
    with open(MCP_NONE_CONFIG) as f:
        n = _json.load(f)
    check("mcp: browser has playwright", "playwright" in b.get("mcpServers", {}))
    check("mcp: pinned exact version (not @latest)",
          f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}" in b["mcpServers"]["playwright"]["args"]
          and "latest" not in PLAYWRIGHT_MCP_VERSION)
    check("mcp: none config is empty", n.get("mcpServers") == {})


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
    orig_runner = config._run_agy_models
    try:
        config.get_backend = lambda: "claude"
        cat = config.model_catalog()
        vals = [v for _, v in cat]
        check("catalog: claude has opus/sonnet/haiku",
              vals == ["opus", "sonnet", "haiku"], str(vals))
        config.get_backend = lambda: "agy"
        # agy catalog = the REAL list (mocked here), as (compact_label, full_value).
        config._run_agy_models = lambda timeout=8.0: ("Gemini 3.5 Flash (Low)\n"
                                                      "Gemini 3.1 Pro (High)\n"
                                                      "Claude Opus 4.6 (Thinking)")
        config._agy_models_cache.update(val=None, ts=0.0, live=False)
        cat = config.model_catalog()
        vals = [v for _, v in cat]
        check("catalog: agy uses real live models",
              vals == ["Gemini 3.5 Flash (Low)", "Gemini 3.1 Pro (High)",
                       "Claude Opus 4.6 (Thinking)"], str(vals))
        check("catalog: agy labels are compact",
              [l for l, _ in cat] == ["3.5 Flash·Low", "3.1 Pro·High", "Opus 4.6·Think"],
              str([l for l, _ in cat]))
    finally:
        config.get_backend = orig
        config._run_agy_models = orig_runner
        config._agy_models_cache.update(val=None, ts=0.0, live=False)


def test_platform_compat_lock():
    # Exactly one of the OS flags must be true (portable: Win/macOS/Linux).
    check("pc: exactly one OS flag set",
          sum([pc.IS_WINDOWS, pc.IS_MAC, pc.IS_LINUX]) == 1,
          f"win={pc.IS_WINDOWS} mac={pc.IS_MAC} linux={pc.IS_LINUX}")
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
        test_agy_models_parse_and_fallback,
        test_agy_label_compact,
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
        test_auth_limited_role,
        test_auth_set_role_toggle,
        test_auth_limited_role_persists,
        test_inbox_classifies_video_by_extension,
        test_inbox_counts,
        test_inbox_filter_returns_only_category,
        test_inbox_delete_file,
        test_outbox_lists_and_classifies,
        test_outbox_delete_is_path_scoped,
        test_detect_file_paths_posix,
        test_next_run_daily,
        test_next_run_interval_and_once,
        test_next_run_weekly,
        test_schedule_add_due_touch,
        test_schedule_once_disables_after_run,
        test_schedule_reconcile_catchup,
        test_schedule_failure_retry,
        test_schedule_remove_and_owner_scope,
        test_parse_schedule_forms,
        test_parse_schedule_command_forms,
        test_detect_limit,
        test_autoharness_classify,
        test_needs_browser,
        test_mcp_configs_generated,
        test_verify_gate,
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
