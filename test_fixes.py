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
