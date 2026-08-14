# ============================================================
#  TESTS — Phase H4: self-update with doctor-gated rollback
#  (PLAN.md §8/H4 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network, no-service tests for:
#    - zilla/update.py: the whole pipeline driven through an injected
#      command runner — happy path, "already up to date", a refused
#      non-checkout, a fetch/pull that aborts before anything changes,
#      and every rollback trigger (install, backup, migration, restart,
#      failed checks).
#    - the ACCEPT case: a simulated bad migration rolls back to the
#      recorded commit AND restores the real pre-update SQLite database
#      (taken with VACUUM INTO, restored with the shipped code).
#    - the doctor gate: a broken import always rolls back; an environment
#      problem that already existed BEFORE the update never does.
#    - update_available(): `git fetch --dry-run` capped at 1x/day, cached
#      in settings, and beat_flag_lines() reading that cache without ever
#      shelling out — so a heartbeat can mention an update, never install.
#    - zilla/cli.py: `zilla update` / `--check` / `--announce`.
#    - bot.py: /update is owner-only, shows a confirm card, and NOTHING
#      runs until the owner taps — the tap is the only path that spawns
#      the updater.
#
#  Live-only accept criterion NOT covered here (same deferral category as
#  every prior phase): "one full update on the Linux service" needs the
#  real box, a real remote, and a real systemd unit.
#
#  Run:  .venv/bin/python test_update.py
# ============================================================

import asyncio
import json
import os
import sys
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


# ── Isolate config BEFORE anything touches the store ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_h4_cfg_")
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

import zilla.store as _store  # noqa: E402
import zilla.update as zupdate  # noqa: E402

OWNER = 4242
NON_OWNER = 77

HEAD_OLD = "aaaaaaa111122223333444455556666777788889"
HEAD_NEW = "bbbbbbb111122223333444455556666777788889"

DOCTOR_OK = {"ok": True, "smoke_ok": True, "env_ok": True, "detail": ""}
DOCTOR_BROKEN_CODE = {"ok": False, "smoke_ok": False, "env_ok": True,
                      "detail": "ImportError: no module named zilla.zui"}
DOCTOR_BAD_ENV = {"ok": False, "smoke_ok": True, "env_ok": False,
                  "detail": "flac: not found"}


# ── fakes ───────────────────────────────────────────────────

class _Runner:
    """Stands in for the real subprocess runner: records every command and
    answers from a small scripted table. `fail` maps a step key to the
    (exit code, output) it should return instead."""

    def __init__(self, fail=None):
        self.calls: list[list[str]] = []
        self.fail = dict(fail or {})
        self.pulled = False

    @staticmethod
    def key(cmd):
        for word in ("rev-parse", "checkout", "pull"):
            if word in cmd:
                return word
        if "fetch" in cmd:
            return "fetch-dry" if "--dry-run" in cmd else "fetch"
        if "pip" in cmd:
            return "pip"
        return "other"

    @property
    def keys(self):
        return [self.key(c) for c in self.calls]

    def __call__(self, cmd, cwd, timeout):
        self.calls.append(list(cmd))
        key = self.key(cmd)
        if key in self.fail:
            return self.fail[key]
        if key == "rev-parse":
            return 0, (HEAD_NEW if self.pulled else HEAD_OLD)
        if key == "pull":
            self.pulled = True
            return 0, "Updating aaaaaaa..bbbbbbb"
        if key == "checkout":
            self.pulled = False
            return 0, "HEAD is now at aaaaaaa"
        return 0, ""


class _Calls:
    """A callable that returns scripted results and counts its calls."""

    def __init__(self, *results):
        self.results = list(results) or [{"ok": True, "detail": ""}]
        self.count = 0

    def __call__(self, *a, **kw):
        result = self.results[min(self.count, len(self.results) - 1)]
        self.count += 1
        return result


def _pipeline(runner=None, doctor=None, restart=None, migrate=None,
              backup=None, restore=None, db_path=None):
    """run_update with every outside-world step stubbed by default."""
    runner = runner or _Runner()
    return zupdate.run_update(
        repo_dir=_tmpdir, python=sys.executable,
        db_path=db_path or os.path.join(_tmpdir, "unused.db"),
        runner=runner,
        migrate=migrate or _Calls({"ok": True, "detail": ""}),
        backup=backup or (lambda: os.path.join(_tmpdir, "unused.db.pre-update")),
        restore=restore or (lambda path: None),
        restart=restart or _Calls({"ok": True, "detail": "restarted"}),
        doctor=doctor or _Calls(DOCTOR_OK),
    ), runner


# ============================================================
#  1. The happy path
# ============================================================

def test_happy_path():
    print("\n[1] a clean update — install, back up, migrate, restart, check")
    doctor = _Calls(DOCTOR_OK)
    restart = _Calls({"ok": True, "detail": "restarted"})
    result, runner = _pipeline(doctor=doctor, restart=restart)

    check("the update reports success", result["ok"] is True, result["message"])
    check("it never rolls back a good update", result["rolled_back"] is False)
    check("it records where it came from and where it went",
          result["from_commit"] == HEAD_OLD and result["to_commit"] == HEAD_NEW, result)
    check("the owner's line says it is updated and running",
          "Updated" in result["message"] and "checks out" in result["message"],
          result["message"])
    check("the commands run in the order the plan specifies",
          runner.keys == ["rev-parse", "fetch", "pull", "rev-parse", "pip"],
          runner.keys)
    check("the checks run before AND after the update (baseline + gate)",
          doctor.count == 2, doctor.count)
    check("the service is restarted exactly once", restart.count == 1, restart.count)
    names = [s.name for s in result["steps"]]
    check("every step is recorded for the log", "database" in names and "checks" in names, names)
    check("every recorded step passed",
          all(s.ok for s in result["steps"]), [s for s in result["steps"] if not s.ok])


def test_already_up_to_date():
    print("\n[2] nothing new — no install, no restart, no risk")
    runner = _Runner()
    runner.pulled = False

    class _NoChange(_Runner):
        def __call__(self, cmd, cwd, timeout):
            self.calls.append(list(cmd))
            if self.key(cmd) == "rev-parse":
                return 0, HEAD_OLD
            return 0, ""

    restart = _Calls({"ok": True, "detail": ""})
    migrate = _Calls({"ok": True, "detail": ""})
    result, r = _pipeline(runner=_NoChange(), restart=restart, migrate=migrate)
    check("an up-to-date Zilla reports success", result["ok"] is True, result)
    check("it says so in one plain line",
          result["message"] == "Already up to date.", result["message"])
    check("nothing was changed", result["changed"] is False)
    check("it never restarts when there was nothing to install", restart.count == 0)
    check("it never touches the database when there was nothing to install",
          migrate.count == 0)
    check("it never installs dependencies for a no-op", "pip" not in r.keys, r.keys)


def test_not_a_git_checkout():
    print("\n[3] a copy that wasn't installed from the repository")
    result, runner = _pipeline(runner=_Runner(fail={"rev-parse": (128, "not a git repository")}))
    check("it refuses rather than guessing", result["ok"] is False)
    check("it stops before doing anything at all", runner.keys == ["rev-parse"], runner.keys)
    check("the owner gets one plain sentence",
          "can't update this copy" in result["message"], result["message"])


def test_fetch_and_pull_failures_change_nothing():
    print("\n[4] a failed download or a dirty tree changes nothing")
    result, runner = _pipeline(runner=_Runner(fail={"fetch": (128, "could not resolve host")}))
    check("a failed download is not a failure to recover from",
          result["ok"] is False and result["rolled_back"] is False, result)
    check("nothing is installed after a failed download",
          "pip" not in runner.keys, runner.keys)
    check("the owner is told nothing changed",
          "nothing changed" in result["message"], result["message"])

    result2, runner2 = _pipeline(runner=_Runner(fail={"pull": (1, "local changes would be overwritten")}))
    check("an update that won't apply cleanly leaves everything alone",
          result2["ok"] is False and result2["rolled_back"] is False, result2)
    check("no rollback is attempted when nothing was applied",
          "checkout" not in runner2.keys, runner2.keys)
    check("the owner is told it was left as it was",
          "left everything as it was" in result2["message"], result2["message"])


# ============================================================
#  5. THE ACCEPT CASE — a bad migration rolls back commit AND data
# ============================================================

def test_bad_migration_rolls_back_commit_and_db():
    print("\n[5] a bad migration — the recorded commit AND the database come back")
    db_path = os.path.join(_tmpdir, "pipeline.db")
    for stale in (db_path, db_path + zupdate.BACKUP_SUFFIX):
        if os.path.exists(stale):
            os.remove(stale)
    db = _store.get_store(db_path)
    db.set_setting("marker", "before")

    def bad_migrate():
        # A migration that gets partway — writes, then fails. Exactly the
        # case a plain "checkout the old commit" rollback would not fix.
        _store.get_store(db_path).set_setting("marker", "half-migrated")
        return {"ok": False, "detail": "sqlite3.OperationalError: no such column"}

    doctor = _Calls(DOCTOR_OK)
    restart = _Calls({"ok": True, "detail": "restarted"})
    result, runner = _pipeline(runner=_Runner(), doctor=doctor, restart=restart,
                               migrate=bad_migrate, db_path=db_path,
                               backup=lambda: zupdate.default_backup(db_path),
                               restore=lambda path: zupdate.default_restore(path, db_path))

    check("the update reports failure", result["ok"] is False, result)
    check("it rolled back", result["rolled_back"] is True, result)
    check("it names the database as the step that failed",
          result["failed_step"] == "database", result["failed_step"])
    checkouts = [c for c in runner.calls if "checkout" in c]
    check("the recorded commit — not just any commit — is checked out again",
          len(checkouts) == 1 and HEAD_OLD in checkouts[0], checkouts)
    check("the rolled-back version's dependencies are reinstalled",
          runner.keys.count("pip") == 2, runner.keys)
    check("the pre-update database is restored, not the half-migrated one",
          _store.get_store(db_path).get_setting("marker") == "before",
          _store.get_store(db_path).get_setting("marker"))
    check("the backup was taken with the shipped VACUUM INTO snapshot",
          os.path.exists(db_path + zupdate.BACKUP_SUFFIX))
    check("the owner gets one calm line, no error text",
          "put the previous version back" in result["message"]
          and "sqlite" not in result["message"].lower(), result["message"])
    check("it does not ask for a human when the rollback worked",
          result["needs_human"] is False, result)


# ============================================================
#  6. The doctor gate
# ============================================================

def test_doctor_gate_rolls_back_broken_code():
    print("\n[6] the doctor gate — broken code always rolls back")
    doctor = _Calls(DOCTOR_OK, DOCTOR_BROKEN_CODE, DOCTOR_OK)
    result, runner = _pipeline(doctor=doctor)
    check("failed checks roll the update back",
          result["ok"] is False and result["rolled_back"] is True, result)
    check("it names the checks as the step that failed",
          result["failed_step"] == "checks", result["failed_step"])
    check("the previous commit is restored", "checkout" in runner.keys, runner.keys)
    check("the owner's line mentions the checks, never an ImportError",
          "didn't pass its checks" in result["message"]
          and "ImportError" not in result["message"], result["message"])


def test_doctor_gate_ignores_a_pre_existing_environment_problem():
    print("\n[7] a problem that was already there never reverts a good release")
    # Baseline already failing (a missing flac, say) and still failing after.
    doctor = _Calls(DOCTOR_BAD_ENV)
    result, runner = _pipeline(doctor=doctor)
    check("a pre-existing environment problem does not roll back",
          result["ok"] is True and result["rolled_back"] is False, result)
    check("no rollback commands were issued", "checkout" not in runner.keys, runner.keys)

    # Same problem, but it appeared WITH the update — that is a regression.
    doctor2 = _Calls(DOCTOR_OK, DOCTOR_BAD_ENV, DOCTOR_OK)
    result2, runner2 = _pipeline(doctor=doctor2)
    check("an environment problem the update introduced does roll back",
          result2["rolled_back"] is True, result2)
    check("the previous commit is restored", "checkout" in runner2.keys, runner2.keys)


def test_install_backup_and_restart_failures_roll_back():
    print("\n[8] every other failure rolls back too")
    result, runner = _pipeline(runner=_Runner(fail={"pip": (1, "no matching distribution")}))
    check("a failed install rolls back",
          result["rolled_back"] is True and result["failed_step"] == "install", result)
    check("a failed install never runs the migrations",
          "database" not in [s.name for s in result["steps"]],
          [s.name for s in result["steps"]])

    def boom():
        raise OSError("disk full")

    result2, _ = _pipeline(backup=boom)
    check("a database that can't be backed up stops the update",
          result2["rolled_back"] is True and result2["failed_step"] == "back up data",
          result2)
    check("nothing is restored when no backup was ever taken",
          result2["backup"] is None, result2["backup"])

    restart = _Calls({"ok": False, "detail": "it did not come back up"},
                     {"ok": True, "detail": "restarted"})
    result3, runner3 = _pipeline(restart=restart)
    check("a version that won't start rolls back",
          result3["rolled_back"] is True and result3["failed_step"] == "restart", result3)
    check("the rollback restarts it again", restart.count == 2, restart.count)


def test_failed_rollback_asks_for_a_human():
    print("\n[9] a rollback that itself fails asks for a human, calmly")
    runner = _Runner(fail={"checkout": (1, "cannot checkout")})
    result, _ = _pipeline(runner=runner, doctor=_Calls(DOCTOR_OK, DOCTOR_BROKEN_CODE))
    check("it says a human is needed", result["needs_human"] is True, result)
    check("the owner's line is one plain sentence",
          "needs a hand on the computer" in result["message"], result["message"])


def test_owner_facing_lines_carry_no_internals():
    print("\n[10] every owner-facing line stays plain (docs/dev/STYLE.md R3, R5)")
    doctors = [_Calls(DOCTOR_OK), _Calls(DOCTOR_OK, DOCTOR_BROKEN_CODE)]
    messages = [_pipeline(doctor=d)[0]["message"] for d in doctors]
    messages.append(_pipeline(runner=_Runner(fail={"fetch": (1, "boom")}))[0]["message"])
    messages.append(_pipeline(runner=_Runner(fail={"rev-parse": (128, "")}))[0]["message"])
    banned = ("git", "pip", "sqlite", "commit", "traceback", "stderr", "!")
    for msg in messages:
        low = msg.lower()
        check(f"plain language: {msg[:44]}…",
              not any(word in low for word in banned), msg)
        check(f"one or two sentences: {msg[:44]}…", msg.count(".") <= 2, msg)


# ============================================================
#  11. "an update is available" — 1x/day, cached, never installs
# ============================================================

def test_update_available_is_checked_at_most_once_a_day():
    print("\n[11] the availability check — one fetch a day, cached in settings")
    config.set_setting(zupdate.CHECK_TS_KEY, 0)
    config.set_setting(zupdate.CHECK_RESULT_KEY, False)

    runner = _Runner()
    runner.fail["fetch-dry"] = (0, "  aaaaaaa..bbbbbbb  main -> origin/main")
    state = zupdate.update_available(repo_dir=_tmpdir, runner=runner, now=1_000_000.0)
    check("a ref that would move means an update is available",
          state["available"] is True and state["fresh"] is True, state)
    check("it asked git exactly once", runner.keys == ["fetch-dry"], runner.keys)

    state2 = zupdate.update_available(repo_dir=_tmpdir, runner=runner, now=1_000_060.0)
    check("a second look within the day uses the cache",
          state2["available"] is True and state2["fresh"] is False, state2)
    check("and shells out to nothing", runner.keys == ["fetch-dry"], runner.keys)

    state3 = zupdate.update_available(repo_dir=_tmpdir, runner=runner,
                                      now=1_000_000.0 + 25 * 3600)
    check("a day later it looks again", state3["fresh"] is True, state3)

    quiet = _Runner()
    quiet.fail["fetch-dry"] = (0, "")
    state4 = zupdate.update_available(repo_dir=_tmpdir, runner=quiet,
                                      now=2_000_000.0, force=True)
    check("no moving refs means no update", state4["available"] is False, state4)
    check("force bypasses the daily cache", quiet.keys == ["fetch-dry"], quiet.keys)

    broken = _Runner()
    broken.fail["fetch-dry"] = (128, "could not resolve host")
    state5 = zupdate.update_available(repo_dir=_tmpdir, runner=broken,
                                      now=3_000_000.0, force=True)
    check("no network is not an available update", state5["available"] is False, state5)


def test_beat_may_mention_an_update_but_never_installs_one():
    print("\n[12] the heartbeat may mention an update — cache only, no git")
    config.set_setting(zupdate.CHECK_RESULT_KEY, False)
    check("no update available -> the beat says nothing",
          zupdate.beat_flag_lines() == [], zupdate.beat_flag_lines())

    config.set_setting(zupdate.CHECK_RESULT_KEY, True)
    lines = zupdate.beat_flag_lines()
    check("an available update becomes exactly one beat line", len(lines) == 1, lines)
    check("the line points at the owner's own /update tap",
          "/update" in lines[0], lines)
    check("installing stays the owner's action, never the agent's",
          "the owner can install it" in lines[0], lines)

    from zilla import heartbeat as zheartbeat
    from datetime import datetime
    prompt = zheartbeat.build_beat_prompt(datetime(2026, 8, 14, 9, 0), None,
                                          "IST", flags=lines)
    check("the line is prepended to the beat prompt", prompt.startswith("System note:"),
          prompt[:60])
    config.set_setting(zupdate.CHECK_RESULT_KEY, False)


# ============================================================
#  13. The database backup/restore primitives, for real
# ============================================================

def test_backup_and_restore_round_trip():
    print("\n[13] VACUUM INTO snapshot and restore, on a real database")
    db_path = os.path.join(_tmpdir, "roundtrip.db")
    for stale in (db_path, db_path + zupdate.BACKUP_SUFFIX):
        if os.path.exists(stale):
            os.remove(stale)
    _store.get_store(db_path).set_setting("shape", "original")
    backup_path = zupdate.default_backup(db_path)
    check("the snapshot lands beside the database, on its own name",
          backup_path == db_path + zupdate.BACKUP_SUFFIX and os.path.exists(backup_path),
          backup_path)
    check("the nightly .bak generation is untouched",
          not os.path.exists(db_path + ".bak"))

    _store.get_store(db_path).set_setting("shape", "damaged")
    zupdate.default_restore(backup_path, db_path)
    check("no stale write-ahead log is left to replay over it",
          not os.path.exists(db_path + "-wal"))
    check("restoring brings the snapshot's contents back",
          _store.get_store(db_path).get_setting("shape") == "original",
          _store.get_store(db_path).get_setting("shape"))


# ============================================================
#  14. zilla update — the terminal entry point
# ============================================================

def test_cli_update_subcommand():
    print("\n[14] `zilla update` — check, run, announce")
    import zilla.cli as zcli
    old_run, old_avail, old_notify, old_migrate = (
        zupdate.run_update, zupdate.update_available, zupdate.notify,
        config.run_zilla_home_migration)
    config.run_zilla_home_migration = lambda *a, **kw: None
    try:
        zupdate.update_available = lambda **kw: {"available": True, "fresh": True}
        check("`zilla update --check` only looks", zcli.main(["update", "--check"]) == 0)

        calls = {}
        zupdate.run_update = lambda **kw: {"ok": True, "steps": [],
                                           "message": "Updated and running."}
        zupdate.notify = lambda chat_id, text, **kw: calls.setdefault("dm", (chat_id, text))
        check("`zilla update` reports success as exit 0",
              zcli.main(["update"]) == 0)
        check("without --announce nobody is messaged", "dm" not in calls, calls)

        check("`zilla update --announce` still exits 0",
              zcli.main(["update", "--announce", str(OWNER)]) == 0)
        check("with --announce the owner gets the one line",
              calls.get("dm") == (OWNER, "Updated and running."), calls)

        zupdate.run_update = lambda **kw: {"ok": False, "steps": [],
                                           "message": "The new version didn't pass."}
        check("a failed update exits non-zero", zcli.main(["update"]) == 1)
    finally:
        zupdate.run_update, zupdate.update_available, zupdate.notify = (
            old_run, old_avail, old_notify)
        config.run_zilla_home_migration = old_migrate


# ============================================================
#  15. bot.py — /update is owner-only and needs a tap
# ============================================================

class _FakeMessage:
    def __init__(self):
        self.sent = []
        self.markups = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)
        self.markups.append(kw.get("reply_markup"))


class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class _FakeUpdate:
    def __init__(self, uid):
        self.effective_user = _FakeUser(uid)
        self.effective_chat = _FakeChat(uid)
        self.message = _FakeMessage()
        self.effective_message = self.message


class _FakeQuery:
    def __init__(self, data):
        self.data = data
        self.edited_texts = []

    async def answer(self, *a, **kw):
        pass

    async def edit_message_text(self, text, **kw):
        self.edited_texts.append(text)


class _FakeContext:
    def __init__(self, args=None):
        self.args = args or []
        self.bot = None


class _FakeAuth:
    def is_owner(self, uid):
        return uid == OWNER


def test_bot_update_command_and_tap():
    print("\n[15] /update — owner only, confirm card, nothing runs until the tap")
    import bot as _bot
    old_auth, old_avail, old_spawn = _bot.auth, zupdate.update_available, _bot._spawn_update
    spawned = []
    try:
        _bot.auth = _FakeAuth()
        zupdate.update_available = lambda **kw: {"available": True, "fresh": False}
        _bot._spawn_update = lambda chat_id: (spawned.append(chat_id), True)[1]

        u = _FakeUpdate(NON_OWNER)
        asyncio.run(_bot.cmd_update(u, _FakeContext()))
        check("/update is owner-only", u.message.sent == ["Owner only."], u.message.sent)
        check("a non-owner never starts an updater", spawned == [], spawned)

        u2 = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_update(u2, _FakeContext()))
        card = u2.message.sent[0] if u2.message.sent else ""
        check("the owner gets a confirm card, not an update",
              "Update" in card and spawned == [], (card, spawned))
        check("the card says what will happen if it goes wrong",
              "put the old version back" in card, card)
        check("the card has no exclamation marks (STYLE R2)", "!" not in card, card)
        buttons = [b.text for row in u2.message.markups[0].inline_keyboard for b in row]
        check("two buttons, the action first (STYLE R14/R17)",
              buttons == ["Update now", "Cancel"], buttons)

        q = _FakeQuery("upd_no")
        asyncio.run(_bot._cb_update(q, _FakeContext(), q.data, OWNER, OWNER))
        check("declining starts nothing", spawned == [], spawned)
        check("declining says so plainly",
              q.edited_texts == ["Left as it is."], q.edited_texts)

        q2 = _FakeQuery("upd_go")
        asyncio.run(_bot._cb_update(q2, _FakeContext(), q2.data, NON_OWNER, NON_OWNER))
        check("a non-owner tap starts nothing", spawned == [], spawned)

        q3 = _FakeQuery("upd_go")
        asyncio.run(_bot._cb_update(q3, _FakeContext(), q3.data, OWNER, OWNER))
        check("the owner's tap is the only thing that starts an update",
              spawned == [OWNER], spawned)
        check("the card becomes one calm line",
              q3.edited_texts and "Updating now" in q3.edited_texts[-1], q3.edited_texts)

        _bot._spawn_update = lambda chat_id: False
        q4 = _FakeQuery("upd_go")
        asyncio.run(_bot._cb_update(q4, _FakeContext(), q4.data, OWNER, OWNER))
        check("an updater that won't start is one calm sentence",
              q4.edited_texts and "couldn't start" in q4.edited_texts[-1], q4.edited_texts)

        spec = next((s for s in _bot.COMMAND_REGISTRY if s.name == "update"), None)
        check("/update is in the command registry, owner-scoped",
              spec is not None and spec.scope == "owner", spec)
        check("its menu description is plain (STYLE R3)",
              spec is not None and "!" not in spec.description
              and spec.description[0].isupper(), spec)
    finally:
        _bot.auth, _bot._spawn_update = old_auth, old_spawn
        zupdate.update_available = old_avail


def test_spawn_uses_the_running_interpreter():
    print("\n[16] the updater runs out-of-process, under the same interpreter")
    import bot as _bot
    import subprocess
    seen = {}

    class _FakePopen:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
            seen["kw"] = kw

    old = subprocess.Popen
    subprocess.Popen = _FakePopen
    try:
        ok = _bot._spawn_update(OWNER)
    finally:
        subprocess.Popen = old
    check("spawning reports success", ok is True)
    check("it runs `zilla update` out of process, so the restart can't kill it",
          seen["cmd"][1:4] == ["-m", "zilla.cli", "update"], seen.get("cmd"))
    check("it uses the interpreter the bot itself is running under",
          seen["cmd"][0] == sys.executable, seen.get("cmd"))
    check("it tells the updater which chat to report back to",
          seen["cmd"][-2:] == ["--announce", str(OWNER)], seen.get("cmd"))
    check("it is detached from the bot process",
          seen["kw"].get("start_new_session") or seen["kw"].get("creationflags"),
          seen.get("kw"))


if __name__ == "__main__":
    tests = [
        test_happy_path,
        test_already_up_to_date,
        test_not_a_git_checkout,
        test_fetch_and_pull_failures_change_nothing,
        test_bad_migration_rolls_back_commit_and_db,
        test_doctor_gate_rolls_back_broken_code,
        test_doctor_gate_ignores_a_pre_existing_environment_problem,
        test_install_backup_and_restart_failures_roll_back,
        test_failed_rollback_asks_for_a_human,
        test_owner_facing_lines_carry_no_internals,
        test_update_available_is_checked_at_most_once_a_day,
        test_beat_may_mention_an_update_but_never_installs_one,
        test_backup_and_restore_round_trip,
        test_cli_update_subcommand,
        test_bot_update_command_and_tap,
        test_spawn_uses_the_running_interpreter,
    ]
    for t in tests:
        t()

    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
