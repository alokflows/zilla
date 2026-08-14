# ============================================================
#  TESTS — Phase S: skills from chat, ask-first (PLAN.md §11 "Accept:")
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/skills.py: the SKILL_PROPOSAL: marker parse/strip, slugify +
#      command-name collision suffixes, the folder read, the approval hash,
#      the state machine, and the owner-facing copy.
#    - store.skill_approvals: the row, the `enabled` switch, the idempotent
#      ALTER that adds it to a database created before Phase S.
#    - zilla/harness.py: the INDEX GATE — only approved, unmodified skills
#      are ever named to the model; owner-only; off in incognito and on a
#      fast turn; the legacy backend-native list is owner-only too.
#    - zilla/core.py Skills: propose → ✅ → write instruction on the next
#      turn → finalize (Markdown-only goes live, code waits for a second
#      tap), ❌ never re-proposed, the hash-mismatch auto-revoke + its
#      one-line notice, and the marker hold (owner-only, never incognito).
#    - the slash-command surface: approve → command appears, disable →
#      command disappears, with set_my_commands mocked.
#    - bot.py: /skills, the taps, the offer card, and running /<skill>.
#
#  Run:  .venv/bin/python test_skills.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Config is isolated to a tmpdir BEFORE zilla is imported, ZILLA_HOME is
#  pinned to a throwaway dir, and every test points MEMORY_DIR at its own
#  temporary tree — a run never touches the owner's real ~/Zilla.
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
_tmpdir = tempfile.mkdtemp(prefix="zilla_s_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

# Tests must never write into the owner's real ~/Zilla (logs, media,
# Memory). config binds every path off ZILLA_HOME at import time, so this
# has to happen before the first zilla import in this file.
os.environ.setdefault("ZILLA_HOME", tempfile.mkdtemp(prefix="zilla_test_home_"))
os.makedirs(os.path.join(os.environ["ZILLA_HOME"], "Runtime", "logs"), exist_ok=True)
import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.core as zcore  # noqa: E402
import zilla.harness as harness  # noqa: E402
import zilla.memory as memory  # noqa: E402
from zilla import skills as zskills  # noqa: E402
from zilla import store as _store  # noqa: E402
from zilla.core import SkillProposal, SkillsChanged, ZillaCore  # noqa: E402
from zilla.harness import TurnContext  # noqa: E402
from zilla.schedules import ScheduleManager  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402

OWNER = 111
NON_OWNER = 999


# ══════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════

class _CollectingQueue:
    """Stands in for the asyncio.Queue a real frontend subscribes with."""

    def __init__(self, sink):
        self._sink = sink

    def put_nowait(self, ev):
        self._sink.append(ev)


def _iso(tag: str):
    """A throwaway Memory/ tree and an empty skill_approvals table."""
    tmp = tempfile.mkdtemp(prefix=f"zilla_s_{tag}_")
    olds = (memory.MEMORY_DIR, config.MEMORY_DIR)
    memory.MEMORY_DIR = config.MEMORY_DIR = os.path.join(tmp, "Memory")
    memory.ensure_tree()
    db = _store.get_store(config.DB_FILE)
    db._write(lambda conn: conn.execute("DELETE FROM skill_approvals"))
    return tmp, olds, db


def _restore(tmp, olds):
    memory.MEMORY_DIR, config.MEMORY_DIR = olds
    shutil.rmtree(tmp, ignore_errors=True)


def _write_skill(slug, *, name=None, description="does a thing", body="Steps.",
                 script=None, mem_dir=None):
    """Create Memory/Skills/<slug>/ the way an approved write turn would."""
    mem_dir = mem_dir or memory.MEMORY_DIR
    folder = zskills.skill_dir(mem_dir, slug)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, zskills.SKILL_FILE), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {name or slug}\ndescription: {description}\n"
                f"created: 2026-08-14\nuses: 0\n---\n\n{body}\n")
    if script is not None:
        with open(os.path.join(folder, "run.py"), "w", encoding="utf-8") as f:
            f.write(script)
    return folder


def _fresh_core(tag: str, subscribe=True):
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.db"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.db"), OWNER)
    sched = ScheduleManager(os.path.join(_tmpdir, f"schedules_{tag}.db"))
    core = ZillaCore(sessions=sessions, auth=auth, schedules=sched)
    events = []
    if subscribe:
        core._subscribers.append(_CollectingQueue(events))
    return core, events


def _ctx(uid=OWNER, is_owner=True, incognito=False, fast_profile=False):
    return TurnContext(uid=uid, role="owner" if is_owner else "admin",
                       is_owner=is_owner, incognito=incognito,
                       fast_profile=fast_profile)


# ============================================================
#  1. skills.parse_markers / slugify — detect, strip, never leak
# ============================================================

def _run_marker_tests():
    print("\n[1] SKILL_PROPOSAL: — detected, always stripped, capped")

    clean, props = zskills.parse_markers(
        "Done — that took a few steps.\n\n"
        "SKILL_PROPOSAL: Weekly stock report — pull the sheet, total it, message me")
    check("the proposal is parsed into name + description",
          len(props) == 1 and props[0]["name"] == "Weekly stock report"
          and props[0]["description"].startswith("pull the sheet"), props)
    check("the slug is derived deterministically",
          props and props[0]["slug"] == "weekly_stock_report", props)
    check("the marker never survives into the owner-facing text",
          "SKILL_PROPOSAL" not in clean and clean.startswith("Done"), repr(clean))

    check("a plain reply is returned untouched",
          zskills.parse_markers("no markers here") == ("no markers here", []))

    clean, props = zskills.parse_markers("ok\nSKILL_PROPOSAL:    ")
    check("an empty payload is dropped, not proposed", props == [], props)
    check("an empty marker line is still stripped",
          "SKILL_PROPOSAL" not in clean, repr(clean))

    clean, props = zskills.parse_markers("ok\nSKILL_PROPOSAL: !!! — nothing usable")
    check("a name with no letters or digits is malformed, never a skill",
          props == [{"error": "malformed"}], props)

    many = "ok\n" + "\n".join(f"SKILL_PROPOSAL: skill {i} — x" for i in range(4))
    clean, props = zskills.parse_markers(many)
    check(f"no more than MAX_PROPOSALS ({zskills.MAX_PROPOSALS}) are taken",
          len(props) == zskills.MAX_PROPOSALS, props)
    check("every marker line is stripped even past the cap",
          "SKILL_PROPOSAL" not in clean, repr(clean))

    _, props = zskills.parse_markers("SKILL_PROPOSAL: Just a name")
    check("a proposal with no description still parses",
          props and props[0]["slug"] == "just_a_name" and props[0]["description"] == "",
          props)

    for sep in ("–", "::", "-"):
        _, props = zskills.parse_markers(f"SKILL_PROPOSAL: Tidy up {sep} sorts files")
        check(f"a '{sep}' separator is tolerated",
              props and props[0]["name"] == "Tidy up"
              and props[0]["description"] == "sorts files", props)

    check("None/'' never raise", zskills.parse_markers("") == ("", [])
          and zskills.parse_markers(None) == (None, []))

    check("slugify collapses punctuation and spaces",
          zskills.slugify("  Weekly  Stock — Report!! ") == "weekly_stock_report",
          zskills.slugify("  Weekly  Stock — Report!! "))
    check("slugify is capped at Telegram's command length",
          len(zskills.slugify("x" * 80)) == zskills.MAX_SLUG)
    check("slugify returns '' when nothing is left",
          zskills.slugify("!!!") == "" and zskills.slugify(None) == "")


# ============================================================
#  2. Reading a skill folder + the approval hash
# ============================================================

def _run_disk_tests():
    print("\n[2] the folder on disk — frontmatter, files, the approval hash")
    tmp, olds, _db = _iso("disk")
    try:
        _write_skill("stock_report", name="Stock report", description="totals the sheet")
        skill = zskills.read_skill(memory.MEMORY_DIR, "stock_report")
        check("frontmatter gives the name and the one-liner",
              skill["name"] == "Stock report" and skill["description"] == "totals the sheet",
              skill)
        check("a Markdown-only skill is not code-type", skill["has_code"] is False)
        check("the hash is over real bytes", bool(skill["hash"]))

        first = skill["hash"]
        check("hashing twice gives the same answer",
              zskills.code_hash(memory.MEMORY_DIR, "stock_report") == first)

        _write_skill("stock_report", name="Stock report", description="totals the sheet",
                     body="Steps. Now with one more line.")
        check("editing SKILL.md changes the hash",
              zskills.code_hash(memory.MEMORY_DIR, "stock_report") != first)

        _write_skill("scripted", script="print('hi')\n")
        scripted = zskills.read_skill(memory.MEMORY_DIR, "scripted")
        check("a folder with anything besides SKILL.md is code-type",
              scripted["has_code"] is True, scripted["files"])
        with_script = scripted["hash"]
        with open(os.path.join(zskills.skill_dir(memory.MEMORY_DIR, "scripted"),
                               "run.py"), "w", encoding="utf-8") as f:
            f.write("print('changed')\n")
        check("editing a script changes the hash too",
              zskills.code_hash(memory.MEMORY_DIR, "scripted") != with_script)

        os.makedirs(zskills.skill_dir(memory.MEMORY_DIR, "half_written"), exist_ok=True)
        check("a folder with no SKILL.md is not a skill",
              zskills.read_skill(memory.MEMORY_DIR, "half_written") is None
              and zskills.code_hash(memory.MEMORY_DIR, "half_written") is None)
        check("a missing folder reads as nothing, never an error",
              zskills.read_skill(memory.MEMORY_DIR, "nope") is None
              and zskills.skill_files(memory.MEMORY_DIR, "nope") == [])

        listed = [s["slug"] for s in zskills.list_skills(memory.MEMORY_DIR)]
        check("list_skills sees the real ones only, slug-sorted",
              listed == ["scripted", "stock_report"], listed)

        check("broken frontmatter degrades, never raises",
              zskills.parse_frontmatter("no frontmatter") == {}
              and zskills.parse_frontmatter("---\nname: x") == {})
    finally:
        _restore(tmp, olds)


# ============================================================
#  3. The state machine and the audit gate
# ============================================================

def _run_state_tests():
    print("\n[3] the gate — unapproved is invisible, an edit revokes")
    tmp, olds, db = _iso("state")
    try:
        _write_skill("alpha", name="Alpha")
        skill = zskills.read_skill(memory.MEMORY_DIR, "alpha")

        check("a skill with no approval row is UNAPPROVED",
              zskills.state_of(skill, None) == zskills.UNAPPROVED)

        db.skill_approval_set("alpha", skill["hash"], "2026-08-14 10:00", OWNER)
        row = db.skill_approval_get("alpha")
        check("an approved, unmodified skill is OK",
              zskills.state_of(skill, row) == zskills.OK, row)

        approved, revoked = zskills.audit(db, memory.MEMORY_DIR)
        check("audit reports it as approved",
              [s["slug"] for s in approved] == ["alpha"] and revoked == [])

        _write_skill("alpha", name="Alpha", body="Different steps now.")
        edited = zskills.read_skill(memory.MEMORY_DIR, "alpha")
        check("an edited skill is CHANGED",
              zskills.state_of(edited, row) == zskills.CHANGED)
        approved, revoked = zskills.audit(db, memory.MEMORY_DIR)
        check("audit drops it from approved and reports it once",
              approved == [] and [s["slug"] for s in revoked] == ["alpha"])

        db.skill_approval_set_enabled("alpha", False)
        approved, revoked = zskills.audit(db, memory.MEMORY_DIR)
        check("an already switched-off skill is not reported again",
              approved == [] and revoked == [])

        db.skill_approval_set("alpha", edited["hash"], "2026-08-14 11:00", OWNER)
        approved, _ = zskills.audit(db, memory.MEMORY_DIR)
        check("re-approving the new bytes brings it back",
              [s["slug"] for s in approved] == ["alpha"])

        db.skill_approval_set_enabled("alpha", False)
        listed = {s["slug"]: s["state"] for s in zskills.listing(db, memory.MEMORY_DIR)}
        check("a switched-off skill reads as DISABLED",
              listed.get("alpha") == zskills.DISABLED, listed)

        class _BrokenDB:
            def skill_approvals_all(self):
                raise RuntimeError("db is gone")

        check("a store failure closes the gate rather than opening it",
              zskills.audit(_BrokenDB(), memory.MEMORY_DIR) == ([], []))
    finally:
        _restore(tmp, olds)


# ============================================================
#  4. store.skill_approvals
# ============================================================

def _run_store_tests():
    print("\n[4] store — the approval row and its on/off switch")
    tmp, olds, db = _iso("store")
    try:
        db.skill_approval_set("one", "hash-1", "2026-08-14 10:00", OWNER)
        row = db.skill_approval_get("one")
        check("the row round-trips", row and row["code_hash"] == "hash-1"
              and row["approved_by"] == OWNER, row)
        check("a new row is enabled", row["enabled"] == 1)

        db.skill_approval_set("one", "hash-2", "2026-08-14 11:00", OWNER)
        check("re-approving replaces the hash in place",
              db.skill_approval_get("one")["code_hash"] == "hash-2"
              and len(db.skill_approvals_all()) == 1)

        check("switching off is recorded",
              db.skill_approval_set_enabled("one", False)
              and db.skill_approval_get("one")["enabled"] == 0)
        check("switching an unknown slug off says so",
              db.skill_approval_set_enabled("nope", False) is False)

        db.skill_approval_set("two", "hash-3", "2026-08-14 12:00", OWNER)
        check("all rows come back slug-sorted",
              [r["slug"] for r in db.skill_approvals_all()] == ["one", "two"])
        check("deleting works", db.skill_approval_delete("two")
              and db.skill_approval_get("two") is None)
        check("deleting an unknown slug says so",
              db.skill_approval_delete("two") is False)

        # The idempotent ALTER: a database created BEFORE Phase S has no
        # `enabled` column, and opening it must add one rather than crash.
        legacy = os.path.join(_tmpdir, "legacy_skills.db")
        import sqlite3
        conn = sqlite3.connect(legacy)
        conn.execute("CREATE TABLE skill_approvals (slug TEXT PRIMARY KEY, "
                     "code_hash TEXT NOT NULL, approved_at TEXT NOT NULL, "
                     "approved_by INTEGER NOT NULL)")
        conn.execute("INSERT INTO skill_approvals VALUES ('old', 'h', 'then', 1)")
        conn.commit()
        conn.close()
        old_db = _store.Store(legacy)
        got = old_db.skill_approval_get("old")
        check("a pre-Phase-S database gains `enabled` on open, defaulting to on",
              got is not None and got.get("enabled") == 1, got)
        old_db.close()
    finally:
        _restore(tmp, olds)


# ============================================================
#  5. Harness injection — the index gate
# ============================================================

def _run_injection_tests():
    print("\n[5] harness — only approved skills are ever named to the model")
    tmp, olds, db = _iso("inject")
    try:
        _write_skill("alpha", name="Alpha", description="does alpha things")
        _write_skill("beta", name="Beta", description="does beta things")
        alpha = zskills.read_skill(memory.MEMORY_DIR, "alpha")
        db.skill_approval_set("alpha", alpha["hash"], "2026-08-14 10:00", OWNER)

        block = harness._skills_block(_ctx())
        check("an approved skill is named, with its file to read",
              "Alpha" in block and "Skills/alpha/SKILL.md" in block, block)
        check("an UNAPPROVED skill on disk is never named",
              "Beta" not in block and "beta" not in block, block)

        check("a non-owner turn gets no skill index at all",
              harness._skills_block(_ctx(NON_OWNER, is_owner=False)) == "")
        check("an incognito turn gets no skill index",
              harness._skills_block(_ctx(incognito=True)) == "")
        check("a fast turn stays lean — no index",
              harness._skills_block(_ctx(fast_profile=True)) == "")
        check("no ctx means no index", harness._skills_block(None) == "")

        db.skill_approval_set_enabled("alpha", False)
        check("switching a skill off takes it out of the prompt",
              "Alpha" not in harness._skills_block(_ctx()))

        db.skill_approval_set("alpha", alpha["hash"], "2026-08-14 11:00", OWNER)
        _write_skill("alpha", name="Alpha", description="does alpha things",
                     body="Edited behind Zilla's back.")
        check("a skill edited after approval drops out of the prompt",
              "Alpha" not in harness._skills_block(_ctx()))

        # Nothing approved at all ⇒ no heading, rather than an empty one.
        db._write(lambda conn: conn.execute("DELETE FROM skill_approvals"))
        check("with nothing approved the block is empty, not a bare heading",
              harness._skills_block(_ctx()) == "")

        # The proposal protocol rides the owner's memory block.
        owner_pre = harness.build_preamble(is_new=False, ctx=_ctx())
        check("the owner is taught the SKILL_PROPOSAL protocol",
              "SKILL_PROPOSAL:" in owner_pre)
        other_pre = harness.build_preamble(is_new=False,
                                           ctx=_ctx(NON_OWNER, is_owner=False))
        check("another principal is never taught it",
              "SKILL_PROPOSAL" not in other_pre)
        check("an incognito turn is never taught it",
              "SKILL_PROPOSAL" not in harness.build_preamble(
                  is_new=False, ctx=_ctx(incognito=True)))

        # PLAN.md §11 step 4: the LEGACY backend-native list becomes
        # owner-turn-only, riding the same scope guard as memory.
        legacy_dir = os.path.join(tmp, "backend_skills")
        os.makedirs(os.path.join(legacy_dir, "installed_one"), exist_ok=True)
        with open(os.path.join(legacy_dir, "installed_one", "SKILL.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\ndescription: came with the CLI\n---\n")
        old_get = config.get_skills_dir
        config.get_skills_dir = lambda backend=None: legacy_dir
        harness._skills_cache.update(key=None, val="", ts=0.0)
        try:
            owner_new = harness.build_preamble(is_new=True, ctx=_ctx())
            check("the owner still sees the CLI's own installed skills",
                  "installed_one" in owner_new)
            harness._skills_cache.update(key=None, val="", ts=0.0)
            other_new = harness.build_preamble(
                is_new=True, ctx=_ctx(NON_OWNER, is_owner=False))
            check("another principal does not",
                  "installed_one" not in other_new)
        finally:
            config.get_skills_dir = old_get
            harness._skills_cache.update(key=None, val="", ts=0.0)
    finally:
        _restore(tmp, olds)


# ============================================================
#  6. core.Skills — the ask-first lifecycle
# ============================================================

def _run_lifecycle_tests():
    print("\n[6] core.Skills — offer, ✅, write turn, and what goes live")
    tmp, olds, db = _iso("life")
    core, events = _fresh_core("life")
    try:
        pid = core.skills.propose(
            {"slug": "stock_report", "name": "Stock report",
             "description": "totals the sheet"}, OWNER, OWNER)
        check("the offer is held", pid and len(core.skills.pending()) == 1)
        proposal = [e for e in events if isinstance(e, SkillProposal)][-1]
        check("a SkillProposal is broadcast with the card",
              proposal.slug == "stock_report" and "Stock report" in proposal.card,
              proposal)
        check("nothing was written by the offer itself",
              zskills.list_skills(memory.MEMORY_DIR) == [])

        check("the same skill is not offered twice at once",
              core.skills.propose({"slug": "stock_report", "name": "Stock report"},
                                  OWNER, OWNER) is None)

        # ❌ — dropped, and never offered again.
        pid2 = core.skills.propose({"slug": "tidy_up", "name": "Tidy up"}, OWNER, OWNER)
        check("declining returns the offer", core.skills.decline(pid2) is not None)
        check("declining an unknown id is calm", core.skills.decline("nope") is None)
        check("a declined skill is never proposed again",
              core.skills.propose({"slug": "tidy_up", "name": "Tidy up"},
                                  OWNER, OWNER) is None)

        # ✅ — arms the write instruction for the NEXT turn only.
        entry = core.skills.accept(pid)
        check("accepting returns the entry", entry and entry["slug"] == "stock_report")
        check("a ✅ approves nothing yet",
              db.skill_approval_get("stock_report") is None)
        check("the write instruction is armed for the owner",
              core.skills.pending_write(OWNER) is not None)
        check("accepting the same offer twice is calm",
              core.skills.accept(pid) is None)

        decorated = core.skills.decorate("what's the stock?", _ctx())
        check("the next owner turn carries the write instruction",
              "SAVE THIS SKILL FIRST" in decorated
              and decorated.endswith("what's the stock?"), decorated[:80])
        check("the instruction names the exact file to write",
              zskills.skill_file(memory.MEMORY_DIR, "stock_report") in decorated)
        check("another principal's turn is never decorated",
              core.skills.decorate("hi", _ctx(NON_OWNER, is_owner=False)) == "hi")
        check("an incognito turn is never decorated",
              core.skills.decorate("hi", _ctx(incognito=True)) == "hi")

        # The agent wrote a Markdown-only skill: the ✅ already approved it.
        _write_skill("stock_report", name="Stock report", description="totals the sheet")
        note = core.skills.finalize(OWNER)
        check("the owner is told it is saved", "Saved" in note, note)
        row = db.skill_approval_get("stock_report")
        check("a Markdown-only skill goes live on the tap that authorized it",
              row is not None and row["enabled"] == 1, row)
        check("it is now in the index",
              "Stock report" in harness._skills_block(_ctx()))
        check("the instruction fires exactly once",
              core.skills.pending_write(OWNER) is None
              and core.skills.decorate("next", _ctx()) == "next")
        check("the command list changed", any(isinstance(e, SkillsChanged)
                                              for e in events))

        # A skill that arrives WITH code needs a second, explicit tap.
        pid3 = core.skills.propose({"slug": "backup_it", "name": "Backup it"},
                                   OWNER, OWNER)
        core.skills.accept(pid3)
        _write_skill("backup_it", name="Backup it", script="print('backup')\n")
        note = core.skills.finalize(OWNER)
        check("a code-carrying skill says it is waiting for the owner",
              "script" in note and "/skills" in note, note)
        check("and it is NOT approved",
              db.skill_approval_get("backup_it") is None)
        check("so it is not in the index either",
              "Backup it" not in harness._skills_block(_ctx()))

        approved = core.skills.approve("backup_it", OWNER)
        check("the owner's approve tap switches it on",
              approved and approved["state"] == zskills.OK, approved)
        check("now it is in the index",
              "Backup it" in harness._skills_block(_ctx()))
        check("approving a skill that isn't there is calm",
              core.skills.approve("ghost", OWNER) is None)

        # A ✅ the agent never honored.
        pid4 = core.skills.propose({"slug": "never_written", "name": "Never written"},
                                   OWNER, OWNER)
        core.skills.accept(pid4)
        note = core.skills.finalize(OWNER)
        check("nothing written means nothing saved, said plainly",
              "didn't manage" in note and "Nothing was saved" in note, note)
        check("finalize with nothing pending is silent",
              core.skills.finalize(OWNER) == "")

        # Auto-revoke: the bytes changed after approval.
        with open(os.path.join(zskills.skill_dir(memory.MEMORY_DIR, "backup_it"),
                               "run.py"), "w", encoding="utf-8") as f:
            f.write("print('something else entirely')\n")
        revoked = core.skills.audit()
        check("editing an approved script revokes it",
              [s["slug"] for s in revoked] == ["backup_it"], revoked)
        check("it is switched off in the table",
              db.skill_approval_get("backup_it")["enabled"] == 0)
        check("and out of the prompt",
              "Backup it" not in harness._skills_block(_ctx()))
        check("the notice explains why in one line",
              "edited" in zskills.revoked_line({"name": "Backup it"}))
        check("the same edit is not reported twice", core.skills.audit() == [])

        off = core.skills.disable("stock_report")
        check("switching a skill off reports it",
              off and off["state"] == zskills.DISABLED, off)
        check("switching off an unknown skill is calm",
              core.skills.disable("ghost") is None)
    finally:
        _restore(tmp, olds)


# ============================================================
#  7. core — the marker hold on a real turn's reply
# ============================================================

def _run_marker_hold_tests():
    print("\n[7] core — the marker is honored on OWNER turns only")
    tmp, olds, db = _iso("hold")
    core, events = _fresh_core("hold")
    try:
        reply = ("Here's the report.\n\n"
                 "SKILL_PROPOSAL: Weekly report — pull, total, send")
        out = core._process_skill_markers(reply, _ctx(), OWNER)
        check("the owner never sees the raw protocol", "SKILL_PROPOSAL" not in out, out)
        check("the answer itself still delivers", out.startswith("Here's the report."))
        check("an offer is held", len(core.skills.pending()) == 1)
        check("a SkillProposal reached the frontend",
              any(isinstance(e, SkillProposal) for e in events))

        core._pending_skills.clear()
        events.clear()
        out = core._process_skill_markers(reply, _ctx(NON_OWNER, is_owner=False), OWNER)
        check("another principal's marker is stripped and dropped",
              "SKILL_PROPOSAL" not in out and core.skills.pending() == []
              and not any(isinstance(e, SkillProposal) for e in events), out)

        out = core._process_skill_markers(reply, _ctx(incognito=True), OWNER)
        check("a private turn leaves nothing behind, not even an offer",
              "SKILL_PROPOSAL" not in out and core.skills.pending() == [])

        out = core._process_skill_markers("ok\nSKILL_PROPOSAL: ### — nope", _ctx(), OWNER)
        check("a malformed marker becomes one calm line",
              zskills.MALFORMED_LINE in out and "SKILL_PROPOSAL" not in out, out)

        check("a reply with no marker at all is untouched",
              core._process_skill_markers("just an answer", _ctx(), OWNER)
              == "just an answer")

        # The revoke notice rides the reply the owner is already reading.
        _write_skill("edited_one", name="Edited one")
        skill = zskills.read_skill(memory.MEMORY_DIR, "edited_one")
        db.skill_approval_set("edited_one", skill["hash"], "2026-08-14 10:00", OWNER)
        _write_skill("edited_one", name="Edited one", body="Changed.")
        out = core._process_skill_markers("Answer.", _ctx(), OWNER)
        check("an auto-revoke is reported on the next owner reply",
              out.startswith("Answer.") and "Edited one" in out, out)

        check("a broken marker pass never costs the owner their answer",
              core._process_skill_markers("Answer.", object(), OWNER) == "Answer.")
    finally:
        _restore(tmp, olds)


# ============================================================
#  8. Slash commands — slugify, collisions, appear/disappear
# ============================================================

def _run_command_tests():
    print("\n[8] every approved skill is a command — and only an approved one")
    tmp, olds, db = _iso("cmd")
    core, _events = _fresh_core("cmd")
    try:
        check("a free name is used as-is",
              zskills.command_name("stock_report", set()) == "stock_report")
        check("a collision with a built-in command gets _2",
              zskills.command_name("memory", {"memory"}) == "memory_2")
        check("a second collision gets _3",
              zskills.command_name("memory", {"memory", "memory_2"}) == "memory_3")
        check("an unusable slug gets no command", zskills.command_name("!!!", set()) == "")

        _write_skill("memory", name="Memory helper", description="my own memory routine")
        _write_skill("stock_report", name="Stock report", description="totals the sheet")
        for slug in ("memory", "stock_report"):
            skill = zskills.read_skill(memory.MEMORY_DIR, slug)
            db.skill_approval_set(slug, skill["hash"], "2026-08-14 10:00", OWNER)

        taken = {"memory", "help", "menu"}
        cmds = {e["slug"]: e["command"] for e in core.skills.commands(taken)}
        check("a skill can never shadow a built-in command",
              cmds["memory"] == "memory_2", cmds)
        check("a free one keeps its own name", cmds["stock_report"] == "stock_report")

        prompt = core.skills.prompt_for_command("stock_report", taken)
        check("the command resolves to the skill's wording",
              prompt and "Stock report" in prompt, prompt)
        check("an unknown command resolves to nothing",
              core.skills.prompt_for_command("nope", taken) is None)

        _write_skill("with_prompt", name="With prompt")
        folder = zskills.skill_dir(memory.MEMORY_DIR, "with_prompt")
        with open(os.path.join(folder, zskills.SKILL_FILE), "w", encoding="utf-8") as f:
            f.write("---\nname: With prompt\ndescription: d\n"
                    "prompt: run the weekly close\n---\n\nSteps.\n")
        skill = zskills.read_skill(memory.MEMORY_DIR, "with_prompt")
        check("a skill may state its own wording",
              zskills.prompt_for(skill) == "run the weekly close")

        db.skill_approval_set("with_prompt", skill["hash"], "2026-08-14 10:00", OWNER)
        check("approve → the command appears",
              "with_prompt" in {e["command"] for e in core.skills.commands(taken)})
        core.skills.disable("with_prompt")
        check("switch off → the command disappears",
              "with_prompt" not in {e["command"] for e in core.skills.commands(taken)})
        check("and typing it resolves to nothing",
              core.skills.prompt_for_command("with_prompt", taken) is None)

        # An unapproved skill on disk is never callable (P5).
        _write_skill("sneaky", name="Sneaky", description="wrote itself")
        check("an unapproved skill has no command",
              "sneaky" not in {e["command"] for e in core.skills.commands(taken)})
        check("and cannot be run by typing it",
              core.skills.prompt_for_command("sneaky", taken) is None)
    finally:
        _restore(tmp, olds)


# ============================================================
#  9. bot.py — /skills, the taps, the offer card, /<skill>
# ============================================================

class _FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.sent = []
        self.message_id = 1

    async def reply_text(self, text, **kw):
        self.sent.append(text)
        return self

    async def delete(self):
        return True


class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeChat:
    def __init__(self, cid):
        self.id = cid


class _FakeUpdate:
    def __init__(self, uid, chat_id=None, text=""):
        self.effective_user = _FakeUser(uid)
        self.effective_chat = _FakeChat(chat_id if chat_id is not None else uid)
        self.message = _FakeMessage(text)
        self.effective_message = self.message
        self.callback_query = None


class _FakeQuery:
    def __init__(self, data):
        self.data = data
        self.edited_texts = []
        self.message = _FakeMessage()

    async def answer(self, *a, **kw):
        return True

    async def edit_message_text(self, text, **kw):
        self.edited_texts.append(text)
        return True


class _FakeTgBot:
    def __init__(self):
        self.sent = []
        self.command_calls = []

    async def send_message(self, chat_id=None, text=None, **kw):
        self.sent.append((chat_id, text))
        return _FakeMessage()

    async def set_my_commands(self, commands, scope=None):
        self.command_calls.append([c.command for c in commands])
        return True


class _FakeApplication:
    def __init__(self, bot):
        self.bot = bot
        self.handlers = []

    def add_handler(self, handler, group=0):
        self.handlers.append(handler)


class _FakeContext:
    def __init__(self, args=None, bot=None):
        self.args = args or []
        self.bot = bot if bot is not None else _FakeTgBot()
        self.user_data = {}


class _FakeAuth:
    def is_owner(self, uid):
        return uid == OWNER

    def can(self, uid, role):
        return uid == OWNER

    def role_of(self, uid):
        return "owner" if uid == OWNER else "admin"

    def is_authorized(self, uid):
        return uid == OWNER


def _run_bot_tests():
    print("\n[9] bot.py — /skills, the taps, the offer, and running /<skill>")
    tmp, olds, db = _iso("bot")
    import bot as _bot
    core, events = _fresh_core("bot")
    old = (_bot.auth, _bot.core, _bot.OWNER_CHAT_ID, _bot.sessions,
           _bot.MEMORY_DIR, _bot._application, _bot._run_text_turn)
    try:
        _bot.auth = _FakeAuth()
        _bot.core = core
        _bot.sessions = core.sessions
        _bot.OWNER_CHAT_ID = OWNER
        _bot.MEMORY_DIR = memory.MEMORY_DIR
        tg = _FakeTgBot()
        _bot._application = _FakeApplication(tg)
        _bot._skill_handlers.clear()

        # /skills with nothing saved yet.
        u = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_skills(u, _FakeContext()))
        check("/skills says so when nothing is saved",
              u.message.sent and "Nothing saved yet" in u.message.sent[0],
              u.message.sent)

        u2 = _FakeUpdate(NON_OWNER)
        asyncio.run(_bot.cmd_skills(u2, _FakeContext()))
        check("/skills is owner-only",
              u2.message.sent == ["Owner only."], u2.message.sent)

        # An offer, declined and accepted.
        core._process_skill_markers("ok\nSKILL_PROPOSAL: Tidy files — sorts the inbox",
                                    _ctx(), OWNER)
        offer = [e for e in events if isinstance(e, SkillProposal)][-1]
        asyncio.run(_bot._deliver_skill_proposal(offer))
        check("the offer card is DMd with the skill's name in it",
              tg.sent and "Tidy files" in tg.sent[-1][1], tg.sent)

        q = _FakeQuery(f"skp_no_{offer.id}")
        asyncio.run(_bot._cb_skills(q, _FakeContext(), q.data, OWNER, OWNER))
        check("declining says nothing was saved",
              q.edited_texts and "nothing saved" in q.edited_texts[0], q.edited_texts)

        core._process_skill_markers("ok\nSKILL_PROPOSAL: Stock report — totals it",
                                    _ctx(), OWNER)
        offer2 = [e for e in events if isinstance(e, SkillProposal)][-1]
        q2 = _FakeQuery(f"skp_ok_{offer2.id}")
        asyncio.run(_bot._cb_skills(q2, _FakeContext(), q2.data, OWNER, OWNER))
        check("accepting promises to write it on the next message",
              q2.edited_texts and "next message" in q2.edited_texts[0], q2.edited_texts)
        q3 = _FakeQuery(f"skp_ok_{offer2.id}")
        asyncio.run(_bot._cb_skills(q3, _FakeContext(), q3.data, OWNER, OWNER))
        check("a double-tap says it was already handled",
              q3.edited_texts and "expired" in q3.edited_texts[0], q3.edited_texts)

        q4 = _FakeQuery(f"skp_ok_{offer2.id}")
        asyncio.run(_bot._cb_skills(q4, _FakeContext(), q4.data, NON_OWNER, NON_OWNER))
        check("a non-owner tap does nothing at all", q4.edited_texts == [])

        # A saved, code-carrying skill: the panel, the detail, the switch.
        _write_skill("backup_it", name="Backup it", description="copies the folder",
                     script="print('x')\n")
        u3 = _FakeUpdate(OWNER)
        asyncio.run(_bot.cmd_skills(u3, _FakeContext()))
        check("/skills lists it as waiting for the owner",
              u3.message.sent and "Backup it" in u3.message.sent[0]
              and "waiting for you" in u3.message.sent[0], u3.message.sent)

        q5 = _FakeQuery("skill_view_backup_it")
        asyncio.run(_bot._cb_skills(q5, _FakeContext(), q5.data, OWNER, OWNER))
        check("opening it shows that it carries code",
              q5.edited_texts and "run.py" in q5.edited_texts[0], q5.edited_texts)

        tg.command_calls.clear()
        q6 = _FakeQuery("skill_ok_backup_it")
        asyncio.run(_bot._cb_skills(q6, _FakeContext(), q6.data, OWNER, OWNER))
        check("switching it on says so with its command",
              q6.edited_texts and "/backup_it" in q6.edited_texts[0], q6.edited_texts)
        check("approve → the command is registered with Telegram",
              tg.command_calls and any("backup_it" in call for call in tg.command_calls),
              tg.command_calls)
        check("a handler was added for it",
              any(getattr(h, "commands", None) == frozenset({"backup_it"})
                  for h in _bot._application.handlers)
              or "backup_it" in _bot._skill_handlers, _bot._skill_handlers)

        tg.command_calls.clear()
        q7 = _FakeQuery("skill_off_backup_it")
        asyncio.run(_bot._cb_skills(q7, _FakeContext(), q7.data, OWNER, OWNER))
        check("switching it off says it won't be used",
              q7.edited_texts and "won't use it" in q7.edited_texts[0], q7.edited_texts)
        check("revoke → the command is gone from Telegram's list",
              tg.command_calls and not any("backup_it" in call
                                           for call in tg.command_calls),
              tg.command_calls)

        q8 = _FakeQuery("skill_view_ghost")
        asyncio.run(_bot._cb_skills(q8, _FakeContext(), q8.data, OWNER, OWNER))
        check("opening a skill that's gone is one calm line",
              q8.edited_texts and "isn't there any more" in q8.edited_texts[0],
              q8.edited_texts)

        # Running /<skill> — the SAME pipeline as typed text.
        ran = []

        async def _fake_turn(update, context, uid, chat_id, text):
            ran.append(text)

        _bot._run_text_turn = _fake_turn
        db.skill_approval_set(
            "backup_it", zskills.code_hash(memory.MEMORY_DIR, "backup_it"),
            "2026-08-14 10:00", OWNER)
        u4 = _FakeUpdate(OWNER, text="/backup_it now")
        asyncio.run(_bot.cmd_skill_run(u4, _FakeContext(args=["now"])))
        check("typing the command runs the skill's wording as a normal turn",
              ran and "Backup it" in ran[0] and ran[0].endswith("now"), ran)

        core.skills.disable("backup_it")
        ran.clear()
        u5 = _FakeUpdate(OWNER, text="/backup_it")
        asyncio.run(_bot.cmd_skill_run(u5, _FakeContext()))
        check("a switched-off skill stops running immediately",
              ran == [] and u5.message.sent
              and "isn't switched on" in u5.message.sent[0], u5.message.sent)

        u6 = _FakeUpdate(NON_OWNER, text="/backup_it")
        asyncio.run(_bot.cmd_skill_run(u6, _FakeContext()))
        check("nobody but the owner can run a skill command",
              u6.message.sent == [], u6.message.sent)

        names = {spec.name for spec in _bot.COMMAND_REGISTRY}
        check("/skills is a real registered command", "skills" in names)
        check("and it is owner-scoped",
              all(spec.scope == "owner" for spec in _bot.COMMAND_REGISTRY
                  if spec.name == "skills"))
    finally:
        (_bot.auth, _bot.core, _bot.OWNER_CHAT_ID, _bot.sessions,
         _bot.MEMORY_DIR, _bot._application, _bot._run_text_turn) = old
        _restore(tmp, olds)


# ============================================================
#  10. Copy — owner-facing, plain language
# ============================================================

def _run_copy_tests():
    print("\n[10] copy — plain language, one action, no jargon")

    card = zskills.proposal_card("Weekly report", "pull, total, send")
    check("the offer card names the skill and what it does",
          "Weekly report" in card and "pull, total, send" in card, card)

    check("saving says how to run it",
          "/weekly" in zskills.saved_line({"name": "Weekly"}, "weekly"))
    check("saving without a command still reads properly",
          zskills.saved_line({"name": "Weekly"}, "") == "🧩 Saved — Weekly.")
    check("a code-carrying skill explains the second tap",
          "script" in zskills.needs_approval_line({"name": "Backup"}))
    check("switching off says what stops",
          "command is gone" in zskills.disabled_line({"name": "Backup"}))

    menu = zskills.menu_text(
        [{"name": "Weekly", "slug": "weekly", "state": zskills.OK}],
        ["installed_one", "installed_two"])
    check("the menu labels the two sources distinctly",
          "Mine" in menu and "Installed in the AI itself" in menu, menu)
    check("the owner's own skills show their state", "Weekly — on" in menu, menu)
    check("an empty menu still says something useful",
          "Nothing saved yet" in zskills.menu_text([], []))

    detail = zskills.detail_text({"name": "Backup", "slug": "backup",
                                  "description": "copies things",
                                  "state": zskills.UNAPPROVED,
                                  "files": ["SKILL.md", "run.py"], "path": "/x/SKILL.md"})
    check("the detail names the code it carries", "run.py" in detail, detail)
    check("and says it is waiting", "waiting for you" in detail, detail)

    check("the index lists name, one-liner and the file to read",
          "**Weekly**" in zskills.index_text(
              [{"name": "Weekly", "slug": "weekly", "description": "d"}]))
    check("an empty index is empty, never a bare heading",
          zskills.index_text([]) == "")

    instruction = zskills.write_instruction(
        {"slug": "weekly", "name": "Weekly", "description": "d"}, "/mem")
    check("the write instruction names the file and the frontmatter",
          "SKILL.md" in instruction and "name: Weekly" in instruction, instruction)


if __name__ == "__main__":
    print("=" * 60)
    print("  PHASE S — SKILLS FROM CHAT TESTS")
    print("=" * 60)
    _run_marker_tests()
    _run_disk_tests()
    _run_state_tests()
    _run_store_tests()
    _run_injection_tests()
    _run_lifecycle_tests()
    _run_marker_hold_tests()
    _run_command_tests()
    _run_bot_tests()
    _run_copy_tests()
    print("\n" + "=" * 60)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 60)
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
