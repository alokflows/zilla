# ============================================================
#  TESTS — Phase C1: brain export / import (PLAN.md §12/C1 "Accept:")
# ============================================================
#  Everything runs in tmpdirs, with no network and no bot.
#
#    - the ACCEPT criterion: export → wipe (Memory tree AND database) →
#      import, and the entity graph answers identically — same nodes,
#      aliases, edges, same graph.neighbors() and find_path() results, same
#      full-text search hits. Nothing about the graph is carried in the
#      archive: it is recomputed from the Markdown, which is the P1 claim.
#    - the encrypted round trip through the real openssl (skipped, loudly,
#      if openssl isn't installed), a wrong passphrase failing in plain
#      language, and no plaintext archive left behind next to it.
#    - the snapshot excludes secrets: a credential-shaped setting never
#      enters the archive, `.env` becomes keys-only, and no byte of a real
#      token appears anywhere inside the tarball.
#    - what is deliberately NOT carried: skill approvals (importing a brain
#      must never arrive with code pre-authorized), sessions, the database.
#    - import safety: the Memory tree it replaces is moved aside, never
#      deleted; existing media is never overwritten; a tarball with a `..`
#      member is refused; junk input gets one plain sentence; importing the
#      same archive twice ends in the same state.
#    - the surfaces: `zilla export` / `zilla import` wiring, the passphrase
#      never reaching argv, and install.py's onboarding restore step.
#
#  Run:  .venv/bin/python test_brain.py
# ============================================================

import json
import os
import shutil
import subprocess
import sys
import tarfile
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
_tmpdir = tempfile.mkdtemp(prefix="zilla_c1_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"
os.environ.setdefault("ZILLA_HOME", tempfile.mkdtemp(prefix="zilla_test_home_"))
os.makedirs(os.path.join(os.environ["ZILLA_HOME"], "Runtime", "logs"), exist_ok=True)

import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.brain as brain      # noqa: E402
import zilla.graph as graph      # noqa: E402
import zilla.memory as memory    # noqa: E402
import zilla.store as _store     # noqa: E402

OWNER = 4242
HELPER = 99

OPENSSL = shutil.which("openssl")

RAMESH_PAGE = (
    "# Ramesh Kumar\n"
    "Cousin; the person to call for anything passport-related.\n"
    "- type:: person\n"
    "- aliases:: Ramesh, my cousin\n"
    "- contact:: +91 000\n"
    "## Relations\n"
    "- works_at:: [[Passport Office]] (since 2024-01)\n"
    "- family_of:: [[Suresh]]\n"
)

SURESH_PAGE = (
    "# Suresh\n"
    "Ramesh's brother; runs the shop in [[Warangal]].\n"
    "- type:: person\n"
    "## Relations\n"
    "- located_in:: [[Warangal]]\n"
)

PROJECT_PAGE = (
    "# Oil press\n"
    "The cold-pressed oil line; [[Ramesh Kumar]] helps with paperwork.\n"
    "- type:: project\n"
)


# ── fixtures ────────────────────────────────────────────────

class Home:
    """One self-contained machine: its own Memory tree, Media tree, .env,
    database, and export/replaced folders."""

    def __init__(self, tag, seed=True):
        self.root = tempfile.mkdtemp(prefix=f"zilla_c1_{tag}_")
        self.mem_dir = os.path.join(self.root, "Memory")
        self.media_dir = os.path.join(self.root, "Media")
        self.export_dir = os.path.join(self.root, "Exports")
        self.replaced_dir = os.path.join(self.root, "Replaced")
        self.env_path = os.path.join(self.root, ".env")
        self.db_path = os.path.join(self.root, "zilla.db")
        os.makedirs(self.export_dir, exist_ok=True)
        # brain.rebuild_indexes goes through memory.reindex(), which reads the
        # store for config.DB_FILE — so a Home IS the current database while
        # it is in use (same contract every other module has).
        config.DB_FILE = self.db_path
        config.SETTINGS_FILE = self.db_path
        self.db = _store.get_store(self.db_path)
        if seed:
            self.seed()

    def use(self):
        config.DB_FILE = self.db_path
        config.SETTINGS_FILE = self.db_path
        return self

    def seed(self):
        memory.ensure_tree(self.mem_dir)
        wiki = os.path.join(self.mem_dir, "Wiki")
        self.write(os.path.join(wiki, "People", "ramesh.md"), RAMESH_PAGE)
        self.write(os.path.join(wiki, "People", "suresh.md"), SURESH_PAGE)
        self.write(os.path.join(wiki, "Projects", "oil-press.md"), PROJECT_PAGE)
        self.write(os.path.join(self.mem_dir, "MEMORY.md"),
                   "# Your memory\nThe owner runs a cold-pressed oil brand.\n")
        self.write(os.path.join(self.mem_dir, "Skills", "invoice", "SKILL.md"),
                   "# invoice\nMake an invoice from a list of items.\n")
        # a git history, so we can prove it travels
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "z@l"],
                    ["git", "config", "user.name", "Zilla"],
                    ["git", "add", "-A"], ["git", "commit", "-qm", "seed"]):
            subprocess.run(cmd, cwd=self.mem_dir, capture_output=True)

        self.write(os.path.join(self.media_dir, "Kept", "receipt.pdf"), "x" * 1024)
        # over the default export_media_max_mb (10) — the cap is the real one
        self.write(os.path.join(self.media_dir, "Kept", "huge.mp4"), "y" * (12 * 1024 * 1024))
        self.write(self.env_path,
                   'TELEGRAM_BOT_TOKEN="123456:SUPERSECRETTOKEN"\n'
                   'TELEGRAM_OWNER_ID="4242"\n'
                   "BACKEND=agy\n"
                   "# a comment\n")

        self.db.set_setting("voice_mode", "on")
        self.db.set_setting("max_bg_tasks", 3)
        self.db.set_setting("github_backup_token", "ghp_LEAKME")
        self.db.set_setting("last_update_check_ts", 1234.5)
        self.db.users_add(OWNER, "Krishna", "admin", "2026-08-01 10:00", None)
        self.db.users_add(HELPER, "Priya", "limited", "2026-08-02 11:00", OWNER)
        self.db.users_remove(HELPER, "2026-08-03 12:00")
        self.db.users_add(HELPER, "Priya", "limited", "2026-08-04 09:00", OWNER)
        self.db.schedules_insert({
            "id": "sch1", "uid": OWNER, "chat_id": OWNER, "kind": "daily",
            "spec": {"at": "09:00"}, "title": "Morning brief",
            "prompt": "what's on today", "enabled": 1, "created_at": "2026-08-01",
        })
        self.db.skill_approval_set("invoice", "deadbeef", "2026-08-05 10:00", OWNER)
        self.db.sessions_upsert(OWNER, "main", conv_id="conv-local-1")
        self.reindex()

    @staticmethod
    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def reindex(self):
        self.use()
        graph.rebuild(self.db, self.mem_dir)
        memory.reindex(base=self.mem_dir)

    # ── the comparable answer: what the graph actually says ──
    def graph_answer(self):
        self.use()
        nodes = sorted(
            (n["path"] or "", n["title"] or "", n["type"] or "", n["is_ghost"])
            for n in self.db.graph_nodes_all()
        )
        titles = {n["id"]: n["title"] for n in self.db.graph_nodes_all()}
        edges = sorted(
            (titles.get(e["src"]) or "", e["rel"], titles.get(e["dst"]) or "",
             e["valid_from"] or "", e["valid_to"] or "", e["provenance"] or "")
            for e in self.db.graph_edges_all(history=True)
        )
        aliases = sorted((a["alias"], titles.get(a["node_id"]) or "")
                         for a in self.db.graph_aliases_all())
        neighbours = graph.neighbors(self.db, "Ramesh", hops=2)
        path = graph.find_path(self.db, "Ramesh", "Warangal")
        return {
            "nodes": nodes, "edges": edges, "aliases": aliases,
            "neighbors": json.dumps(neighbours, sort_keys=True, default=str),
            "path": json.dumps(path, sort_keys=True, default=str),
            "search": [r["path"] for r in self.db.fts_search("pressed", limit=5)],
        }

    def export(self, **kw):
        self.use()
        kw.setdefault("db", self.db)
        kw.setdefault("mem_dir", self.mem_dir)
        kw.setdefault("media_dir", self.media_dir)
        kw.setdefault("env_path", self.env_path)
        dest = kw.pop("dest", os.path.join(self.export_dir, "brain.tar.gz"))
        return brain.export_brain(dest, **kw)

    def import_(self, src, **kw):
        self.use()
        kw.setdefault("db", self.db)
        kw.setdefault("mem_dir", self.mem_dir)
        kw.setdefault("media_dir", self.media_dir)
        kw.setdefault("env_path", self.env_path)
        kw.setdefault("replaced_dir", self.replaced_dir)
        return brain.import_brain(src, **kw)


def _members(archive):
    with tarfile.open(archive, "r:gz") as tar:
        return sorted(m.name for m in tar.getmembers())


# ============================================================
#  1. The pure parts
# ============================================================

def test_env_template_carries_keys_never_values():
    print("\n[1] .env → template: the keys, never the values")
    text = ('TELEGRAM_BOT_TOKEN="123456:SUPERSECRETTOKEN"\n'
            "# BACKEND is documented in .env.example\n"
            "BACKEND=agy\n"
            "\n"
            'CLI_PATH=/home/k/.local/bin/agy\n')
    out = brain.env_template(text)
    check("every key is listed", all(k in out for k in
          ("TELEGRAM_BOT_TOKEN", "BACKEND", "CLI_PATH")), out)
    check("no value survives", "SUPERSECRETTOKEN" not in out and "agy" not in out
          and "/home/k" not in out, out)
    check("comments are dropped too", "documented" not in out, out)
    check("an empty .env still produces a readable template",
          brain.env_template("").startswith("#"), brain.env_template(""))

    missing = brain.env_template_missing_keys(out, {"BACKEND": "agy"})
    check("it can say which keys the new machine still needs",
          missing == ["TELEGRAM_BOT_TOKEN", "CLI_PATH"], missing)


def test_media_selection_caps_per_file():
    print("\n[2] media selection — one cap per file, and the owner is told what stayed")
    home = Home("media")
    carried, skipped = brain.select_media(home.media_dir, 2 * 1024 * 1024)
    check("the small file travels", carried == ["Kept/receipt.pdf"], carried)
    check("the oversize file is named, not silently dropped",
          [s["path"] for s in skipped] == ["Kept/huge.mp4"], skipped)
    check("a missing Media folder is not an error",
          brain.select_media(os.path.join(home.root, "nope"), 10) == ([], []))


def test_snapshot_excludes_secrets_and_undeducible_state_only():
    print("\n[3] the snapshot: what isn't derivable from Markdown — and no secrets")
    home = Home("snap")
    snap = brain.build_snapshot(home.db)

    check("owner preferences are in", snap["settings"].get("voice_mode") == "on", snap["settings"])
    check("a credential-shaped setting is NOT",
          "github_backup_token" not in snap["settings"], snap["settings"])
    check("no secret value appears anywhere in the snapshot",
          "ghp_LEAKME" not in json.dumps(snap), snap["settings"])
    check("this machine's own update cache is not carried",
          "last_update_check_ts" not in snap["settings"], snap["settings"])
    check("schedules are in", [s["id"] for s in snap["schedules"]] == ["sch1"], snap["schedules"])
    check("people are in", sorted(u["uid"] for u in snap["users"]) == [HELPER, OWNER],
          snap["users"])
    check("the derived database is not in the snapshot",
          "nodes" not in snap and "edges" not in snap and "mem_fts" not in snap,
          list(snap))
    check("conversation ids stay with the machine that made them",
          "sessions" not in snap, list(snap))
    check("skill approvals are not carried",
          "skill_approvals" not in snap and "invoice" not in json.dumps(snap["settings"]),
          list(snap))
    check("curiosity is keyed by the wiki page, not by a rebuild-artefact id",
          all("path" in row and "node_id" not in row for row in snap["curiosity"]),
          snap["curiosity"][:2])


# ============================================================
#  2. Export
# ============================================================

def test_export_writes_one_archive_with_the_agreed_layout():
    print("\n[4] export — one archive: Memory (with its git history), snapshot, template, media")
    home = Home("export")
    result = home.export()
    check("it reports success", result["ok"] is True, result)
    check("the file is where it says it is", os.path.exists(result["path"]), result["path"])

    names = _members(result["path"])
    def has(rel):
        return any(n == f"{brain.ARCHIVE_ROOT}/{rel}" for n in names)
    check("Memory/MEMORY.md is inside", has("Memory/MEMORY.md"), names[:8])
    check("the wiki pages are inside", has("Memory/Wiki/People/ramesh.md"), names[:8])
    check("the owner's git history travels with the memory",
          any("/Memory/.git/" in n for n in names), [n for n in names if ".git" in n][:3])
    check("the state snapshot is inside", has("System/state-snapshot.json"), names[:8])
    check("the env template is inside", has(".env.template"), names[:8])
    check("small media is inside", has("Media/Kept/receipt.pdf"), names[:8])
    check("oversize media is not", not has("Media/Kept/huge.mp4"), names[:8])
    check("no database file is inside", not any(n.endswith(".db") for n in names), names[:8])
    check("the owner is told what was too big",
          [s["path"] for s in result["skipped"]] == ["Kept/huge.mp4"], result["skipped"])

    with tarfile.open(result["path"], "r:gz") as tar:
        blob = b"".join(tar.extractfile(m).read() for m in tar.getmembers()
                        if m.isfile() and not m.name.endswith((".pdf", ".mp4")))
    check("not one byte of the bot token is in the archive",
          b"SUPERSECRETTOKEN" not in blob and b"ghp_LEAKME" not in blob)


def test_export_refuses_when_there_is_nothing_to_export():
    print("\n[5] export — no memory yet is a plain sentence, not a traceback")
    home = Home("empty", seed=False)
    result = home.export()
    check("it fails cleanly", result["ok"] is False, result)
    check("the message is owner-safe", "memory" in result["message"].lower()
          and "Traceback" not in result["message"], result["message"])


# ============================================================
#  3. THE ACCEPT CRITERION — round trip through a wiped machine
# ============================================================

def test_round_trip_rebuilds_an_identical_graph():
    print("\n[6] ACCEPT — export → wipe memory AND database → import → the graph "
          "answers identically")
    home = Home("round")
    before = home.graph_answer()
    check("the fixture graph is real (people, ghosts, relations)",
          len(before["nodes"]) >= 5 and len(before["edges"]) >= 4, before["nodes"])

    result = home.export()
    archive = result["path"]

    # The wipe: a genuinely new machine — no Memory, no database at all.
    fresh = Home("fresh", seed=False)
    imported = fresh.import_(archive)
    check("the import reports success", imported["ok"] is True, imported)

    after = fresh.graph_answer()
    check("same nodes", after["nodes"] == before["nodes"],
          f"{before['nodes']} != {after['nodes']}")
    check("same aliases", after["aliases"] == before["aliases"],
          f"{before['aliases']} != {after['aliases']}")
    check("same relations, provenance included", after["edges"] == before["edges"],
          f"{before['edges']} != {after['edges']}")
    check("neighbors('Ramesh') answers identically",
          after["neighbors"] == before["neighbors"])
    check("find_path('Ramesh' → 'Warangal') answers identically",
          after["path"] == before["path"], (before["path"], after["path"]))
    check("full-text search answers identically",
          after["search"] == before["search"] and after["search"] != [],
          (before["search"], after["search"]))

    check("the memory files are all there",
          os.path.exists(os.path.join(fresh.mem_dir, "Wiki", "People", "ramesh.md")))
    check("the git history came too",
          os.path.isdir(os.path.join(fresh.mem_dir, ".git")))
    check("the settings came back", fresh.db.get_setting("voice_mode") == "on")
    check("the schedule came back",
          (fresh.db.schedules_get("sch1") or {}).get("title") == "Morning brief",
          fresh.db.schedules_get("sch1"))
    check("the schedule's spec is usable JSON, not a string",
          (fresh.db.schedules_get("sch1") or {}).get("spec") == {"at": "09:00"},
          fresh.db.schedules_get("sch1"))
    check("the people came back with their roles",
          (fresh.db.users_get(OWNER) or {}).get("role") == "admin"
          and (fresh.db.users_get(HELPER) or {}).get("role") == "limited",
          fresh.db.users_list())
    check("the secret setting did not travel",
          fresh.db.get_setting("github_backup_token") is None)


def test_import_does_not_authorize_skills_or_carry_sessions():
    print("\n[7] a restored brain arrives with nothing pre-authorized")
    home = Home("skills")
    archive = home.export()["path"]
    fresh = Home("skills_fresh", seed=False)
    fresh.import_(archive)

    check("the skill's files travelled",
          os.path.exists(os.path.join(fresh.mem_dir, "Skills", "invoice", "SKILL.md")))
    check("but its approval did NOT — the owner re-taps",
          fresh.db.skill_approval_get("invoice") is None,
          fresh.db.skill_approvals_all())
    check("no session came across",
          fresh.db.sessions_get(OWNER, "main") is None, fresh.db.sessions_list(OWNER))


def test_curiosity_cooldown_survives_the_move():
    print("\n[8] curiosity's asked_at clock survives, remapped onto rebuilt ids")
    home = Home("curio")
    rows = home.db.curiosity_all()
    check("the fixture has a detected gap", len(rows) >= 1, rows)
    home.db.curiosity_mark_asked(rows[0]["node_id"], rows[0]["gap"], "2026-08-10 08:00")
    asked = {(r["gap"], r["asked_at"]) for r in home.db.curiosity_all() if r["asked_at"]}

    archive = home.export()["path"]
    fresh = Home("curio_fresh", seed=False)
    fresh.import_(archive)

    restored = {(r["gap"], r["asked_at"]) for r in fresh.db.curiosity_all() if r["asked_at"]}
    check("the same gap is still on cooldown, same timestamp", restored == asked,
          (asked, restored))
    check("no invented gaps — every row was detected from the files",
          len(fresh.db.curiosity_all()) == len(home.db.curiosity_all()),
          (len(home.db.curiosity_all()), len(fresh.db.curiosity_all())))


def test_import_twice_is_the_same_as_importing_once():
    print("\n[9] importing the same backup twice changes nothing the second time")
    home = Home("twice")
    archive = home.export()["path"]
    fresh = Home("twice_fresh", seed=False)
    fresh.import_(archive)
    once = fresh.graph_answer()
    counts_once = (len(fresh.db.users_list()), len(fresh.db.schedules_all()))
    second = fresh.import_(archive)
    check("the second import succeeds too", second["ok"] is True, second)
    check("the graph is unchanged", fresh.graph_answer() == once)
    check("no duplicated people or schedules",
          (len(fresh.db.users_list()), len(fresh.db.schedules_all())) == counts_once,
          counts_once)


# ============================================================
#  4. Encryption
# ============================================================

def test_encrypted_round_trip():
    print("\n[10] encrypted export → import, through the real openssl")
    if not OPENSSL:
        print("  SKIP  openssl is not installed on this machine")
        return
    home = Home("enc")
    result = home.export(dest=os.path.join(home.export_dir, "locked.tar.gz"),
                         encrypt=True, passphrase="correct horse battery")
    check("it reports success", result["ok"] is True, result)
    check("the file is the encrypted one", result["path"].endswith(brain.ENCRYPTED_SUFFIX),
          result["path"])
    check("the plaintext archive is not left lying next to it",
          not os.path.exists(os.path.join(home.export_dir, "locked.tar.gz")))
    with open(result["path"], "rb") as f:
        head = f.read(16)
    check("the file really is encrypted", head.startswith(b"Salted__"), head)

    fresh = Home("enc_fresh", seed=False)
    imported = fresh.import_(result["path"], passphrase="correct horse battery")
    check("the right passphrase restores everything", imported["ok"] is True, imported)
    check("the memory is really there",
          os.path.exists(os.path.join(fresh.mem_dir, "Wiki", "People", "ramesh.md")))

    wrong = Home("enc_wrong", seed=False)
    bad = wrong.import_(result["path"], passphrase="hunter2")
    check("a wrong passphrase fails", bad["ok"] is False, bad)
    check("and says so in plain language",
          "passphrase" in bad["message"].lower() and "openssl" not in bad["message"].lower(),
          bad["message"])
    check("a locked backup with no passphrase is refused before anything is touched",
          wrong.import_(result["path"])["ok"] is False)


def test_encrypt_without_passphrase_writes_nothing():
    print("\n[11] --encrypt with no passphrase leaves no file at all")
    home = Home("enc_none")
    dest = os.path.join(home.export_dir, "nopass.tar.gz")
    result = home.export(dest=dest, encrypt=True, passphrase="")
    check("it fails", result["ok"] is False, result)
    check("no plaintext archive survives", not os.path.exists(dest))
    check("no encrypted archive either",
          not os.path.exists(dest + brain.ENCRYPTED_SUFFIX))


def test_passphrase_never_reaches_argv():
    print("\n[12] the passphrase goes on stdin — never into argv (ps is public)")
    seen = {}

    def fake_openssl(args, passphrase, timeout=0):
        seen["args"] = list(args)
        seen["passphrase"] = passphrase
        with open(args[args.index("-out") + 1], "wb") as f:
            f.write(b"pretend-ciphertext")
        return 0, ""

    home = Home("argv")
    result = home.export(dest=os.path.join(home.export_dir, "a.tar.gz"),
                         encrypt=True, passphrase="s3kr!t", crypto=fake_openssl)
    check("the export used the injected crypto", result["ok"] is True, result)
    check("the passphrase is not an argument",
          not any("s3kr!t" in a for a in seen.get("args", [])), seen.get("args"))
    check("openssl is told to read it from stdin", "-pass" in seen["args"]
          and seen["args"][seen["args"].index("-pass") + 1] == "stdin", seen["args"])
    check("it is AES-256 with a real KDF",
          "-aes-256-cbc" in seen["args"] and "-pbkdf2" in seen["args"], seen["args"])


# ============================================================
#  5. Import safety
# ============================================================

def test_the_memory_it_replaces_is_kept():
    print("\n[13] import never deletes the memory already on this machine")
    home = Home("keep")
    archive = home.export()["path"]
    other = Home("keep_other")
    other.write(os.path.join(other.mem_dir, "Wiki", "People", "local-only.md"),
                "# Local Only\nSomeone only this machine knows.\n")
    result = other.import_(archive)
    check("the import succeeded", result["ok"] is True, result)
    check("the old tree was moved aside, not deleted",
          result["replaced"] and os.path.exists(
              os.path.join(result["replaced"], "Wiki", "People", "local-only.md")),
          result["replaced"])
    check("the owner is told where it went",
          result["replaced"] in brain.format_steps(result), brain.format_steps(result))
    check("the imported tree replaced it (no stale page left behind)",
          not os.path.exists(os.path.join(other.mem_dir, "Wiki", "People", "local-only.md")))
    check("and the stale page is out of the search index too",
          other.db.fts_search("Someone only this machine", limit=3) == [],
          other.db.fts_search("Someone only this machine", limit=3))


def test_existing_media_is_never_overwritten():
    print("\n[14] import merges media and never overwrites a local file")
    home = Home("media_in")
    archive = home.export()["path"]
    other = Home("media_in_other", seed=False)
    other.write(os.path.join(other.media_dir, "Kept", "receipt.pdf"), "LOCAL")
    other.import_(archive)
    with open(os.path.join(other.media_dir, "Kept", "receipt.pdf"), encoding="utf-8") as f:
        check("the local file is intact", f.read() == "LOCAL")


def test_junk_input_is_one_plain_sentence():
    print("\n[15] junk input: one calm line, nothing touched")
    home = Home("junk")
    for src, label in ((os.path.join(home.root, "nope.tar.gz"), "a file that isn't there"),
                       (home.env_path, "something that isn't an archive"),
                       (home.root, "a folder with no Memory in it")):
        result = home.import_(src)
        check(f"refused: {label}", result["ok"] is False, result)
        check(f"…in plain language: {label}",
              "Traceback" not in result["message"] and len(result["message"]) < 120,
              result["message"])
    check("the memory tree is still intact after all that",
          os.path.exists(os.path.join(home.mem_dir, "Wiki", "People", "ramesh.md")))


def test_a_tarball_that_tries_to_escape_is_refused():
    print("\n[16] a tarball with a '..' member is refused — archives are untrusted input")
    home = Home("evil", seed=False)
    outside = os.path.join(home.root, "outside.txt")
    payload = os.path.join(home.root, "payload.txt")
    with open(payload, "w", encoding="utf-8") as f:
        f.write("pwned")
    evil = os.path.join(home.root, "evil.tar.gz")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname=f"{brain.ARCHIVE_ROOT}/Memory/MEMORY.md")
        tar.add(payload, arcname="../outside.txt")
    result = home.import_(evil)
    check("nothing was written outside the target", not os.path.exists(outside))
    check("the whole archive is refused, not partly applied",
          result["ok"] is False, result["message"])
    check("…in plain language", "Traceback" not in result["message"], result["message"])


def test_import_reports_the_env_keys_still_needed():
    print("\n[17] import tells the owner which .env keys the new machine still needs")
    home = Home("env_in")
    archive = home.export()["path"]
    fresh = Home("env_in_fresh", seed=False)
    fresh.write(fresh.env_path, "BACKEND=agy\n")
    result = fresh.import_(archive)
    check("the missing keys are named",
          result["missing_env_keys"] == ["TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID"],
          result["missing_env_keys"])
    check("and they reach the owner's message",
          "TELEGRAM_BOT_TOKEN" in result["message"], result["message"])
    check("no template value is invented into .env",
          open(fresh.env_path, encoding="utf-8").read() == "BACKEND=agy\n")


def test_a_directory_can_be_imported_directly():
    print("\n[18] an unpacked folder imports as-is (no re-tarring to restore)")
    home = Home("dir")
    archive = home.export()["path"]
    unpacked = os.path.join(home.root, "unpacked")
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(unpacked)
    fresh = Home("dir_fresh", seed=False)
    result = fresh.import_(os.path.join(unpacked, brain.ARCHIVE_ROOT))
    check("the folder imported", result["ok"] is True, result)
    check("the graph is there", len(fresh.db.graph_nodes_all()) >= 5,
          fresh.db.graph_nodes_all())


# ============================================================
#  6. The surfaces
# ============================================================

def test_cli_export_and_import_subcommands():
    print("\n[19] zilla export / zilla import")
    import zilla.cli as cli

    parser = cli.build_parser()
    args = parser.parse_args(["export", "--encrypt", "/tmp/x.tar.gz"])
    check("export takes an optional path and --encrypt",
          args.command == "export" and args.encrypt is True and args.path == "/tmp/x.tar.gz",
          args)
    check("export's path really is optional",
          parser.parse_args(["export"]).path is None)
    check("import requires the backup to restore",
          parser.parse_args(["import", "/tmp/x.tar.gz"]).path == "/tmp/x.tar.gz")

    calls = {}

    class _Brain:
        ENCRYPTED_SUFFIX = brain.ENCRYPTED_SUFFIX

        def export_brain(self, dest, **kw):
            calls["export"] = (dest, kw)
            return {"ok": True, "path": "/tmp/x.tar.gz", "steps": [],
                    "message": "saved"}

        def import_brain(self, src, **kw):
            calls["import"] = (src, kw)
            return {"ok": True, "steps": [], "message": "restored"}

        @staticmethod
        def format_steps(_r):
            return "  ✅ done"

    import zilla as _zilla_pkg
    real = sys.modules.get("zilla.brain")
    real_getpass = None
    fake = _Brain()
    try:
        # `import zilla.brain as brain` binds the package ATTRIBUTE, so both
        # the attribute and sys.modules have to point at the fake.
        sys.modules["zilla.brain"] = fake
        _zilla_pkg.brain = fake
        import getpass
        real_getpass = getpass.getpass
        getpass.getpass = lambda *_a, **_k: "pw"

        rc = cli.main(["export", "--encrypt", "/tmp/x.tar.gz"])
        check("`zilla export --encrypt` runs the export", rc == 0 and "export" in calls, calls)
        check("it passes the passphrase it prompted for, and never prints it",
              calls["export"][1].get("passphrase") == "pw"
              and calls["export"][1].get("encrypt") is True, calls["export"][1].keys())

        rc = cli.main(["import", "/tmp/x.tar.gz"])
        check("`zilla import` runs the import", rc == 0 and "import" in calls, calls)
        check("a plain .tar.gz is not asked for a passphrase",
              calls["import"][1].get("passphrase") is None, calls["import"][1])

        cli.main(["import", "/tmp/x.tar.gz" + brain.ENCRYPTED_SUFFIX])
        check("an .enc backup IS asked for one",
              calls["import"][1].get("passphrase") == "pw", calls["import"][1])
    finally:
        if real is not None:
            sys.modules["zilla.brain"] = real
            _zilla_pkg.brain = real
        if real_getpass is not None:
            import getpass
            getpass.getpass = real_getpass


def test_installer_offers_to_restore():
    print("\n[20] the installer's onboarding restore step")
    import install

    home = Home("installer")
    archive = home.export()["path"]
    fresh = Home("installer_fresh", seed=False)
    config.MEMORY_DIR, real_mem = fresh.mem_dir, config.MEMORY_DIR
    config.MEDIA_DIR, real_media = fresh.media_dir, config.MEDIA_DIR
    config.REPLACED_DIR, real_repl = fresh.replaced_dir, config.REPLACED_DIR
    try:
        check("nothing happens when there's no backup to restore",
              install.restore_from_backup(None, interactive=False) is False)
        check("a --restore path restores without asking anything",
              install.restore_from_backup(archive, interactive=False) is True)
        check("the memory landed in the new machine's home",
              os.path.exists(os.path.join(fresh.mem_dir, "Wiki", "People", "ramesh.md")))
        check("a bad path fails without stopping the install",
              install.restore_from_backup(os.path.join(fresh.root, "nope.tar.gz"),
                                          interactive=False) is False)
    finally:
        config.MEMORY_DIR = real_mem
        config.MEDIA_DIR = real_media
        config.REPLACED_DIR = real_repl


def test_owner_facing_report_carries_no_internals():
    print("\n[21] the report the owner reads: ticks and one next step, no internals")
    home = Home("report")
    text = brain.format_steps(home.export())
    check("it shows the steps", "✅" in text, text)
    check("it names the file to keep", ".tar.gz" in text, text)
    check("it names what was too big to carry", "huge.mp4" in text, text)
    # (the export DOES print the archive's full path — that's the one thing
    # the owner needs. Everything else about the machine stays out.)
    for word in ("Traceback", "sqlite", "openssl", "tarfile", "Exception"):
        check(f"no internals: no '{word}'", word not in text, text)


def test_the_export_dir_lives_under_runtime():
    print("\n[22] the storage constitution — an export is a backup, so it lives in Runtime")
    check("EXPORT_DIR is under Runtime",
          os.path.normpath(config.EXPORT_DIR).startswith(
              os.path.normpath(config.RUNTIME_DIR) + os.sep), config.EXPORT_DIR)
    check("REPLACED_DIR is under Runtime",
          os.path.normpath(config.REPLACED_DIR).startswith(
              os.path.normpath(config.RUNTIME_DIR) + os.sep), config.REPLACED_DIR)
    check("a bare `zilla export` defaults into it",
          brain._default_export_path(0).startswith(config.EXPORT_DIR),
          brain._default_export_path(0))


if __name__ == "__main__":
    tests = [
        test_env_template_carries_keys_never_values,
        test_media_selection_caps_per_file,
        test_snapshot_excludes_secrets_and_undeducible_state_only,
        test_export_writes_one_archive_with_the_agreed_layout,
        test_export_refuses_when_there_is_nothing_to_export,
        test_round_trip_rebuilds_an_identical_graph,
        test_import_does_not_authorize_skills_or_carry_sessions,
        test_curiosity_cooldown_survives_the_move,
        test_import_twice_is_the_same_as_importing_once,
        test_encrypted_round_trip,
        test_encrypt_without_passphrase_writes_nothing,
        test_passphrase_never_reaches_argv,
        test_the_memory_it_replaces_is_kept,
        test_existing_media_is_never_overwritten,
        test_junk_input_is_one_plain_sentence,
        test_a_tarball_that_tries_to_escape_is_refused,
        test_import_reports_the_env_keys_still_needed,
        test_a_directory_can_be_imported_directly,
        test_cli_export_and_import_subcommands,
        test_installer_offers_to_restore,
        test_owner_facing_report_carries_no_internals,
        test_the_export_dir_lives_under_runtime,
    ]
    for t in tests:
        t()

    print("\n" + "=" * 60)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 60)
    sys.exit(1 if _failed else 0)
