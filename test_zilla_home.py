# ============================================================
#  TESTS — Phase F1: ZILLA_HOME storage constitution (PLAN.md §17)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/migrate.py: migrate_zilla_home() — legacy ~/AGI-Brain
#      Inbox/Outbox/Bridge + repo-root Memory/zilla.db moved onto
#      ZILLA_HOME, idempotent, non-destructive, symlink left behind.
#    - zilla/doctor.py: environment_report()/format_report() show the
#      home path.
#    - a path-audit grep gate: no code composes an AGI-Brain path
#      anymore.
#
#  Run:  python test_zilla_home.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every test uses a throwaway tmpdir standing in for HOME_DIR/BASE_DIR/
#  ZILLA_HOME (same pattern as test_harness.py / test_memory_m3.py) so a
#  run never reads, writes, or moves the real repo's Memory/, zilla.db,
#  or ~/AGI-Brain / ~/Zilla. migrate_zilla_home() is called directly with
#  explicit kwargs rather than through config.run_zilla_home_migration(),
#  so no real module-level path is ever touched.
# ============================================================

import os
import shutil
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


_tmpdir = tempfile.mkdtemp(prefix="zilla_f1_test_")

import zilla.config as config  # noqa: E402

# doctor.environment_report() reads settings via store.get_store(SETTINGS_FILE)
# (get_backend/get_model) — point that at a throwaway db for this whole file,
# same isolation pattern as test_memory_m3.py, so it never opens the real
# ~/Zilla/Runtime/zilla.db.
config.DB_FILE = os.path.join(_tmpdir, "isolated_settings.db")
config.SETTINGS_FILE = config.DB_FILE

from zilla.migrate import migrate_zilla_home  # noqa: E402
import zilla.doctor as doctor  # noqa: E402


def _base():
    return os.path.join(_tmpdir, f"case_{os.urandom(4).hex()}")


def test_migration_moves_agi_brain_memory_and_db():
    base = _base()
    agi_brain = os.path.join(base, "AGI-Brain")
    os.makedirs(os.path.join(agi_brain, "Inbox", "images"), exist_ok=True)
    os.makedirs(os.path.join(agi_brain, "Outbox"), exist_ok=True)
    os.makedirs(os.path.join(agi_brain, "Bridge"), exist_ok=True)
    with open(os.path.join(agi_brain, "Inbox", "images", "photo.jpg"), "w") as f:
        f.write("img")
    with open(os.path.join(agi_brain, "Outbox", "report.pdf"), "w") as f:
        f.write("pdf")
    with open(os.path.join(agi_brain, "Bridge", "ask_1.json"), "w") as f:
        f.write("{}")

    memory_dir = os.path.join(base, "Memory")
    os.makedirs(os.path.join(memory_dir, "Journal"), exist_ok=True)
    with open(os.path.join(memory_dir, "MEMORY.md"), "w") as f:
        f.write("# hi")

    db_file = os.path.join(base, "zilla.db")
    with open(db_file, "w") as f:
        f.write("sqlitedata")
    with open(db_file + "-wal", "w") as f:
        f.write("wal")

    zilla_home = os.path.join(base, "Zilla")
    moved = migrate_zilla_home(
        zilla_home=zilla_home,
        legacy_agi_brain_dir=agi_brain,
        legacy_memory_dir=memory_dir,
        legacy_db_file=db_file,
    )

    check("migrate: reports inbox/outbox/bridge/memory/db all moved",
          all(moved[k] for k in ("inbox", "outbox", "bridge", "memory", "db")), moved)
    check("migrate: Inbox file landed under Media/Inbox",
          os.path.exists(os.path.join(zilla_home, "Media", "Inbox", "images", "photo.jpg")))
    check("migrate: Outbox file landed under Outbox/",
          os.path.exists(os.path.join(zilla_home, "Outbox", "report.pdf")))
    check("migrate: Bridge file landed under Runtime/Bridge",
          os.path.exists(os.path.join(zilla_home, "Runtime", "Bridge", "ask_1.json")))
    check("migrate: Memory/MEMORY.md landed under Zilla/Memory",
          os.path.exists(os.path.join(zilla_home, "Memory", "MEMORY.md")))
    check("migrate: zilla.db landed under Runtime/zilla.db",
          os.path.exists(os.path.join(zilla_home, "Runtime", "zilla.db")))
    check("migrate: zilla.db-wal moved alongside zilla.db",
          os.path.exists(os.path.join(zilla_home, "Runtime", "zilla.db-wal")))
    check("migrate: legacy AGI-Brain dir replaced with a symlink to ZILLA_HOME",
          os.path.islink(agi_brain) and os.path.realpath(agi_brain) == os.path.realpath(zilla_home))
    check("migrate: nothing left behind at old Memory location",
          not os.path.exists(memory_dir))
    check("migrate: nothing left behind at old db location",
          not os.path.exists(db_file))


def test_migration_is_noop_when_zilla_home_already_exists():
    base = _base()
    zilla_home = os.path.join(base, "Zilla")
    os.makedirs(zilla_home)
    memory_dir = os.path.join(base, "Memory")
    os.makedirs(memory_dir)
    with open(os.path.join(memory_dir, "MEMORY.md"), "w") as f:
        f.write("untouched")

    moved = migrate_zilla_home(
        zilla_home=zilla_home,
        legacy_agi_brain_dir=os.path.join(base, "AGI-Brain"),
        legacy_memory_dir=memory_dir,
        legacy_db_file=os.path.join(base, "zilla.db"),
    )
    check("migrate: no-op when ZILLA_HOME already exists", not any(moved.values()), moved)
    check("migrate: legacy Memory left in place on no-op", os.path.exists(memory_dir))


def test_migration_never_clobbers_existing_destination():
    """If Runtime/zilla.db somehow already exists (e.g. a partial prior
    run), the legacy db must be left in place, not silently discarded."""
    base = _base()
    zilla_home = os.path.join(base, "Zilla")
    # No top-level Zilla/ yet (so the migration's outer guard doesn't
    # no-op), but Runtime/zilla.db already present.
    os.makedirs(os.path.join(zilla_home, "Runtime"))
    # migrate_zilla_home's own top-level guard is "zilla_home isdir" —
    # to exercise the per-item _move_once guard we must not have created
    # zilla_home yet, so simulate via a helper call directly against
    # _move_once through the public function isn't possible without the
    # top-level dir existing. Instead verify the documented contract at
    # the _move_once level via the module import.
    from zilla.migrate import _move_once
    dst = os.path.join(zilla_home, "Runtime", "zilla.db")
    with open(dst, "w") as f:
        f.write("new-layout-data")
    src = os.path.join(base, "zilla.db")
    with open(src, "w") as f:
        f.write("legacy-data")

    result = _move_once(src, dst)
    check("_move_once: refuses to clobber an existing destination", result is False)
    check("_move_once: source untouched on refusal", os.path.exists(src))
    with open(dst) as f:
        check("_move_once: destination content untouched on refusal",
              f.read() == "new-layout-data")


def test_migration_leaves_agi_brain_alone_if_leftovers_remain():
    """A stray unexpected file inside ~/AGI-Brain must block the
    symlink-replacement step rather than silently deleting it."""
    base = _base()
    agi_brain = os.path.join(base, "AGI-Brain")
    os.makedirs(os.path.join(agi_brain, "Inbox"))
    os.makedirs(os.path.join(agi_brain, "Outbox"))
    os.makedirs(os.path.join(agi_brain, "Bridge"))
    with open(os.path.join(agi_brain, "mystery_file.txt"), "w") as f:
        f.write("don't delete me")

    zilla_home = os.path.join(base, "Zilla")
    migrate_zilla_home(
        zilla_home=zilla_home,
        legacy_agi_brain_dir=agi_brain,
        legacy_memory_dir=os.path.join(base, "Memory"),
        legacy_db_file=os.path.join(base, "zilla.db"),
    )
    check("migrate: AGI-Brain left as a real dir (not a symlink) when leftovers remain",
          os.path.isdir(agi_brain) and not os.path.islink(agi_brain))
    check("migrate: the stray file itself is untouched",
          os.path.exists(os.path.join(agi_brain, "mystery_file.txt")))


def test_migration_no_legacy_sources_is_noop():
    base = _base()
    zilla_home = os.path.join(base, "Zilla")
    moved = migrate_zilla_home(
        zilla_home=zilla_home,
        legacy_agi_brain_dir=os.path.join(base, "AGI-Brain"),
        legacy_memory_dir=os.path.join(base, "Memory"),
        legacy_db_file=os.path.join(base, "zilla.db"),
    )
    check("migrate: nothing to move -> all-false stats", not any(moved.values()), moved)
    check("migrate: ZILLA_HOME dir still created for a fresh install",
          os.path.isdir(zilla_home))


def test_doctor_reports_home():
    old_home = config.ZILLA_HOME
    old_doctor_home = doctor.ZILLA_HOME
    try:
        fake_home = os.path.join(_base(), "Zilla")
        config.ZILLA_HOME = fake_home
        doctor.ZILLA_HOME = fake_home

        report = doctor.environment_report()
        check("doctor: report includes a home.path key", report["home"]["path"] == fake_home)
        check("doctor: home.exists is False before creation", report["home"]["exists"] is False)

        text = doctor.format_report(report)
        check("doctor: formatted report mentions the Zilla home line", "Zilla home:" in text)
        check("doctor: formatted report shows the path", fake_home in text)

        os.makedirs(fake_home)
        report2 = doctor.environment_report()
        check("doctor: home.exists is True once created", report2["home"]["exists"] is True)
    finally:
        config.ZILLA_HOME = old_home
        doctor.ZILLA_HOME = old_doctor_home


def test_no_stray_agi_brain_path_composition_in_source():
    """Path-audit gate: outside of the documented legacy-migration
    plumbing (config.py's _LEGACY_AGI_BRAIN_DIR and migrate.py's
    migration function, both of which exist specifically to find and
    move the OLD layout), no production code should still compose an
    AGI-Brain path. Comments/docstrings are fine; this only flags
    executable-looking references."""
    repo_root = os.path.dirname(os.path.abspath(__file__))
    allowed_files = {
        os.path.join(repo_root, "zilla", "config.py"),
        os.path.join(repo_root, "zilla", "migrate.py"),
    }
    offenders = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in (
            ".git", ".venv", "__pycache__", "node_modules", "Memory",
        )]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            if fpath in allowed_files or fname.startswith("test_"):
                continue
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if "AGI_BRAIN_DIR" in line or "AGI-Brain" in line:
                        offenders.append(f"{fpath}:{lineno}: {stripped}")
    check("path-audit: no production code (outside config.py/migrate.py) "
          "composes an AGI-Brain path", not offenders, offenders)


if __name__ == "__main__":
    tests = [
        test_migration_moves_agi_brain_memory_and_db,
        test_migration_is_noop_when_zilla_home_already_exists,
        test_migration_never_clobbers_existing_destination,
        test_migration_leaves_agi_brain_alone_if_leftovers_remain,
        test_migration_no_legacy_sources_is_noop,
        test_doctor_reports_home,
        test_no_stray_agi_brain_path_composition_in_source,
    ]
    for t in tests:
        t()

    print(f"\n{_passed} passed, {_failed} failed")
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
