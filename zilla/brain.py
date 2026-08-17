"""
Phase C1 — THE PORTABLE BRAIN: EXPORT / IMPORT (PLAN.md §12/C1).

This is the phase that proves P1 — *the knowledge is the user's, the brain is
rented*. One archive carries everything that is genuinely the owner's, and an
import on a bare machine reconstitutes the whole assistant from it:

    Memory/                 the knowledge, Markdown, with its own .git history
    System/state-snapshot.json   settings · schedules · users · curiosity
    System/manifest.json    what's inside + what was left out, and why
    .env.template           which keys this install needs — never their values
    Media/                  files under `export_media_max_mb` (default 10) each

**The database is not in the archive, on purpose.** `zilla.db` is operational
truth plus indexes: the FTS index and the entity graph are *derived* from the
Markdown, so they are rebuilt on import rather than shipped. What is NOT
derivable — the settings KV, schedules, the user list, and curiosity's
`asked_at` cooldown clock — is dumped to one JSON file. If the graph after an
import does not answer identically to the graph before the export, the claim
that Markdown is the source of truth was false; the round-trip test in
test_brain.py is exactly that assertion.

**Three things are deliberately left behind.**

  · *Secrets.* `.env` becomes a key-only template, and any snapshot setting
    whose key looks like a credential is dropped at export time — a brain
    archive travels over email and cloud storage, so it must be worthless to
    whoever finds it. `--encrypt` (AES-256 via openssl, passphrase on stdin,
    never in argv) is for owners who want the Markdown protected too.
  · *Skill approvals.* Skills themselves live in `Memory/Skills/` and travel;
    their approval rows do not. Importing a brain must never be a way to
    arrive on a new machine with code already authorized to run — the owner
    re-taps in `/skills`. Same reasoning as Phase S's index gate.
  · *Sessions.* Conversation ids belong to the backend on the machine that
    made them (§17/F1 step 3: conversations belong to brains, knowledge
    belongs to you), so they are meaningless after a move.

Every path is injectable and every outside effect (openssl, the clock) goes
through a parameter, so the whole round trip is testable in a tmpdir.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1
SNAPSHOT_REL = os.path.join("System", "state-snapshot.json")
MANIFEST_REL = os.path.join("System", "manifest.json")
ENV_TEMPLATE_REL = ".env.template"
ARCHIVE_ROOT = "zilla-brain"

DEFAULT_MEDIA_MAX_MB = 10
MEDIA_MAX_MB_SETTING = "export_media_max_mb"

OPENSSL_TIMEOUT = 300.0
ENCRYPTED_SUFFIX = ".enc"
# -pbkdf2 with an explicit iteration count: openssl's default key derivation
# is one MD5 pass, which is not a passphrase KDF. Recorded here because the
# decrypt side must use the identical arguments.
_OPENSSL_ARGS = ["enc", "-aes-256-cbc", "-md", "sha256", "-pbkdf2",
                 "-iter", "200000", "-salt"]

# A setting whose key matches this never enters an archive. Deliberately
# broad — a false positive costs the owner one re-entered value; a false
# negative puts a credential in a file that leaves the machine.
_SECRET_KEY_RE = re.compile(
    r"token|secret|password|passphrase|api[_-]?key|\bkey\b|credential|"
    r"\bpat\b|cookie|auth",
    re.IGNORECASE,
)

# Settings that describe THIS machine's state rather than the owner's
# preferences: importing them would carry a stale answer onto a new box.
_MACHINE_LOCAL_SETTINGS = ("last_update_check_ts", "update_available")


@dataclass(frozen=True)
class Step:
    """One stage of an export or import, in the order it ran. `name` is
    owner-safe English — never a command line, never a traceback."""
    name: str
    ok: bool
    detail: str = ""


# ── config access (lazy, so tests can point config at a tmpdir) ──

def _cfg():
    import zilla.config as config
    return config


def _db(db):
    if db is not None:
        return db
    from zilla import store as _store
    return _store.get_store(_cfg().DB_FILE)


def _media_max_bytes(db, media_max_mb: float | None) -> int:
    if media_max_mb is None:
        try:
            media_max_mb = db.get_setting(MEDIA_MAX_MB_SETTING, DEFAULT_MEDIA_MAX_MB)
        except Exception:
            media_max_mb = DEFAULT_MEDIA_MAX_MB
    try:
        mb = float(media_max_mb)
    except (TypeError, ValueError):
        mb = DEFAULT_MEDIA_MAX_MB
    return int(max(0.0, mb) * 1024 * 1024)


# ══════════════════════════════════════════════════════════
#  PURE PARTS — snapshot, env template, media selection
# ══════════════════════════════════════════════════════════

def env_template(text: str) -> str:
    """Turn a `.env` into a template: the KEYS it defines, none of the
    values. Comments are dropped too — they are the shipped `.env.example`'s
    job, and a comment in a live `.env` is as likely to be a note about a
    credential as it is to be documentation."""
    keys: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key and key not in keys:
            keys.append(key)
    if not keys:
        return "# Zilla — this install had no .env keys to carry over.\n"
    header = (
        "# Zilla — keys this install needs, values removed on export.\n"
        "# Fill each one in on the new machine (see .env.example for what\n"
        "# they mean), then start Zilla.\n"
    )
    return header + "".join(f"{k}=\n" for k in keys)


def env_template_missing_keys(template_text: str, current_env: dict) -> list[str]:
    """Keys the archive expects that the current `.env` hasn't got a value
    for — the one actionable line an import can give the owner."""
    wanted = [
        line.split("=", 1)[0].strip()
        for line in (template_text or "").splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    ]
    return [k for k in wanted if not (current_env or {}).get(k)]


def build_snapshot(db, *, now: float | None = None) -> dict:
    """Everything in the database that is NOT re-derivable from the Markdown,
    minus anything secret-shaped. Curiosity rows are keyed by the wiki page
    (or, for a ghost, its title) rather than by node id, because ids are
    rebuild artifacts and change on every import."""
    ts = now if now is not None else time.time()
    settings = {
        k: v for k, v in db.all_settings().items()
        if not _SECRET_KEY_RE.search(k) and k not in _MACHINE_LOCAL_SETTINGS
    }

    nodes = {n["id"]: n for n in db.graph_nodes_all()}
    curiosity = []
    for row in db.curiosity_all():
        node = nodes.get(row["node_id"])
        if node is None:
            continue
        curiosity.append({
            "path": node.get("path"),
            "title": node.get("title"),
            "gap": row["gap"],
            "asked_at": row["asked_at"],
        })

    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "exported_at": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
        "bot_version": getattr(_cfg(), "BOT_VERSION", ""),
        "schema_version": db.schema_version(),
        "settings": settings,
        "users": list(db.users_list().values()),
        "denied": [{"uid": uid} for uid in db.users_denied_list()],
        "schedules": db.schedules_all(),
        "curiosity": curiosity,
    }


def apply_snapshot(db, snap: dict) -> dict:
    """Write a snapshot's settings / users / schedules back into a database.
    Idempotent: importing the same archive twice ends in the same state.
    Curiosity is NOT applied here — it needs node ids, so it waits until the
    graph has been rebuilt (see `apply_curiosity`)."""
    counts = {"settings": 0, "users": 0, "denied": 0, "schedules": 0}

    for key, value in (snap.get("settings") or {}).items():
        if _SECRET_KEY_RE.search(key) or key in _MACHINE_LOCAL_SETTINGS:
            continue
        db.set_setting(key, value)
        counts["settings"] += 1

    for user in (snap.get("users") or []):
        try:
            uid = int(user.get("uid"))
        except (TypeError, ValueError):
            continue
        role = user.get("role") if user.get("role") in ("admin", "limited") else "limited"
        added = db.users_add(uid, user.get("name") or "", role,
                             user.get("added_at") or "", user.get("added_by"))
        if not added:
            db.users_set_role(uid, role)
        counts["users"] += 1

    for row in (snap.get("denied") or []):
        try:
            db.denied_add(int(row.get("uid")), row.get("denied_at") or "")
        except (TypeError, ValueError):
            continue
        counts["denied"] += 1

    for sched in (snap.get("schedules") or []):
        sid = sched.get("id")
        if not sid:
            continue
        fields = {k: v for k, v in sched.items() if k != "id"}
        if db.schedules_get(sid) is None:
            db.schedules_insert(dict(sched))
        else:
            db.schedules_update(sid, **fields)
        counts["schedules"] += 1

    return counts


def apply_curiosity(db, rows: list[dict]) -> int:
    """Restore the cooldown clock (`asked_at`) onto the freshly rebuilt
    graph. Only for gaps the rebuild actually detected — a snapshot never
    invents a gap, it only remembers when one was last asked about."""
    detected = {(r["node_id"], r["gap"]) for r in db.curiosity_all()}
    restored = 0
    for row in rows or []:
        if not row.get("asked_at"):
            continue
        node_id = None
        if row.get("path"):
            node = db.graph_node_get_by_path(row["path"])
            node_id = node["id"] if node else None
        if node_id is None and row.get("title"):
            node_id = db.graph_alias_lookup(row["title"])
        if node_id is None or (node_id, row["gap"]) not in detected:
            continue
        db.curiosity_mark_asked(node_id, row["gap"], row["asked_at"])
        restored += 1
    return restored


def select_media(media_dir: str, max_bytes: int) -> tuple[list[str], list[dict]]:
    """Split a Media tree into (carried, skipped). One file over the cap is
    left out and listed by name and size — the owner sees what didn't travel
    instead of discovering it missing later (C3 turns this list into
    Media/SKIPPED.md)."""
    carried: list[str] = []
    skipped: list[dict] = []
    if not os.path.isdir(media_dir):
        return carried, skipped
    for dirpath, _dirnames, filenames in os.walk(media_dir):
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, media_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > max_bytes:
                skipped.append({"path": rel, "bytes": size})
            else:
                carried.append(rel)
    return sorted(carried), sorted(skipped, key=lambda d: d["path"])


# ── plumbing ────────────────────────────────────────────────

def _openssl(args: list[str], passphrase: str, timeout: float = OPENSSL_TIMEOUT) -> tuple[int, str]:
    """Run openssl with the passphrase fed on stdin (`-pass stdin`), never as
    an argument — argv is world-readable in `ps`. Returns (rc, output) and
    never raises; a missing openssl is a normal, reportable failure."""
    try:
        proc = subprocess.run(["openssl", *args], input=(passphrase or "") + "\n",
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "openssl not found"
    except subprocess.TimeoutExpired:
        return 124, "openssl timed out"
    except Exception as e:  # pragma: no cover — defensive
        return 1, str(e)[:200]
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def encrypt_file(src: str, dest: str, passphrase: str, runner=_openssl) -> tuple[bool, str]:
    rc, out = runner([*_OPENSSL_ARGS, "-pass", "stdin", "-in", src, "-out", dest],
                     passphrase)
    return rc == 0, "" if rc == 0 else out


def decrypt_file(src: str, dest: str, passphrase: str, runner=_openssl) -> tuple[bool, str]:
    rc, out = runner([*_OPENSSL_ARGS, "-d", "-pass", "stdin", "-in", src, "-out", dest],
                     passphrase)
    if rc == 0:
        return True, ""
    # openssl's own wording here is "bad decrypt" — useless to the owner.
    return False, "wrong passphrase, or the file isn't a Zilla export"


def _safe_extract(tar: tarfile.TarFile, dest: str) -> None:
    """Extract an archive that came from outside this machine. Python 3.12+
    has the vetted `data` filter; on older interpreters, refuse absolute
    paths, `..` escapes, and anything that isn't a plain file or directory
    ourselves — a tarball is untrusted input."""
    try:
        tar.extractall(dest, filter="data")  # type: ignore[call-arg]
        return
    except TypeError:
        pass
    dest_abs = os.path.abspath(dest)
    members = []
    for member in tar.getmembers():
        if not (member.isfile() or member.isdir()):
            continue
        target = os.path.abspath(os.path.join(dest, member.name))
        if target != dest_abs and not target.startswith(dest_abs + os.sep):
            raise ValueError(f"unsafe path in archive: {member.name}")
        members.append(member)
    tar.extractall(dest, members=members)


def _copy_tree(src: str, dest: str) -> int:
    """Copy a directory, returning the file count. Nothing is filtered —
    `Memory/.git` travelling is the point: the owner keeps their history."""
    if not os.path.isdir(src):
        return 0
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return sum(len(files) for _root, _dirs, files in os.walk(dest))


def _default_export_path(now: float | None = None) -> str:
    stamp = datetime.fromtimestamp(now if now is not None else time.time())
    return os.path.join(_cfg().EXPORT_DIR,
                        f"zilla-brain-{stamp.strftime('%Y%m%d-%H%M%S')}.tar.gz")


def _resolve_dest(dest: str | None, now: float | None) -> str:
    if not dest:
        return _default_export_path(now)
    dest = os.path.expanduser(dest)
    if os.path.isdir(dest):
        return os.path.join(dest, os.path.basename(_default_export_path(now)))
    return dest


# ══════════════════════════════════════════════════════════
#  EXPORT
# ══════════════════════════════════════════════════════════

def export_brain(dest: str | None = None, *, encrypt: bool = False,
                 passphrase: str | None = None, db=None,
                 mem_dir: str | None = None, media_dir: str | None = None,
                 env_path: str | None = None, media_max_mb: float | None = None,
                 now: float | None = None, crypto=_openssl) -> dict:
    """Write one archive holding everything that is the owner's. Returns
    `{ok, path, encrypted, steps, counts, skipped}`; `path` is what to hand
    the owner. Never raises — a failure is a Step with `ok=False`."""
    config = _cfg()
    db = _db(db)
    mem_dir = mem_dir or config.MEMORY_DIR
    media_dir = media_dir or config.MEDIA_DIR
    env_path = env_path if env_path is not None else os.path.join(config.BASE_DIR, ".env")

    steps: list[Step] = []
    counts: dict = {}
    skipped: list[dict] = []
    dest = _resolve_dest(dest, now)
    stage_parent = tempfile.mkdtemp(prefix="zilla_export_")
    stage = os.path.join(stage_parent, ARCHIVE_ROOT)

    try:
        if not os.path.isdir(mem_dir):
            steps.append(Step("read the memory tree", False, "no Memory folder yet"))
            return {"ok": False, "path": "", "encrypted": False, "steps": steps,
                    "counts": counts, "skipped": skipped,
                    "message": "There's no memory to export yet."}

        os.makedirs(os.path.join(stage, "System"), exist_ok=True)
        counts["memory_files"] = _copy_tree(mem_dir, os.path.join(stage, "Memory"))
        steps.append(Step("copy the memory tree", True, f"{counts['memory_files']} files"))

        snap = build_snapshot(db, now=now)
        with open(os.path.join(stage, SNAPSHOT_REL), "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2, sort_keys=True)
        counts["settings"] = len(snap["settings"])
        counts["schedules"] = len(snap["schedules"])
        counts["users"] = len(snap["users"])
        counts["curiosity"] = len(snap["curiosity"])
        steps.append(Step("save settings, schedules and people", True,
                          f"{counts['schedules']} schedules, {counts['users']} people"))

        env_text = ""
        if env_path and os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    env_text = f.read()
            except OSError:
                env_text = ""
        with open(os.path.join(stage, ENV_TEMPLATE_REL), "w", encoding="utf-8") as f:
            f.write(env_template(env_text))
        steps.append(Step("list the settings keys (no passwords)", True))

        carried, skipped = select_media(media_dir, _media_max_bytes(db, media_max_mb))
        for rel in carried:
            src = os.path.join(media_dir, rel.replace("/", os.sep))
            target = os.path.join(stage, "Media", rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(src, target)
        counts["media_files"] = len(carried)
        counts["media_skipped"] = len(skipped)
        steps.append(Step("copy media", True,
                          f"{len(carried)} files"
                          + (f", {len(skipped)} too big" if skipped else "")))

        with open(os.path.join(stage, MANIFEST_REL), "w", encoding="utf-8") as f:
            json.dump({"snapshot_version": SNAPSHOT_VERSION,
                       "counts": counts, "skipped_media": skipped}, f, indent=2)

        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        with tarfile.open(dest, "w:gz") as tar:
            tar.add(stage, arcname=ARCHIVE_ROOT)
        steps.append(Step("pack the archive", True, os.path.basename(dest)))

        if encrypt:
            if not passphrase:
                _rm(dest)
                steps.append(Step("lock the archive", False, "no passphrase given"))
                return {"ok": False, "path": "", "encrypted": True, "steps": steps,
                        "counts": counts, "skipped": skipped,
                        "message": "Nothing was written — an encrypted export needs a passphrase."}
            enc_dest = dest + ENCRYPTED_SUFFIX
            ok, detail = encrypt_file(dest, enc_dest, passphrase, runner=crypto)
            if not ok:
                _rm(dest)
                _rm(enc_dest)
                steps.append(Step("lock the archive", False, detail))
                return {"ok": False, "path": "", "encrypted": True, "steps": steps,
                        "counts": counts, "skipped": skipped,
                        "message": "Couldn't lock the archive, so nothing was saved."}
            _rm(dest)  # the plaintext copy must not outlive the encrypted one
            dest = enc_dest
            steps.append(Step("lock the archive", True, "AES-256"))

        return {"ok": True, "path": dest, "encrypted": bool(encrypt), "steps": steps,
                "counts": counts, "skipped": skipped,
                "message": f"Your brain is saved: {dest}"}
    except Exception as e:
        logger.warning(f"[BRAIN] export failed: {e}")
        steps.append(Step("finish the export", False, str(e)[:200]))
        return {"ok": False, "path": "", "encrypted": bool(encrypt), "steps": steps,
                "counts": counts, "skipped": skipped,
                "message": "The export didn't finish — nothing was changed."}
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)


def _rm(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════
#  IMPORT
# ══════════════════════════════════════════════════════════

def _stage_source(src: str, passphrase: str | None, work: str,
                  crypto=_openssl) -> tuple[str | None, str]:
    """Resolve whatever the owner pointed at — a directory, a .tar.gz, or an
    encrypted .enc — into a directory holding the archive root. Returns
    (root or None, error in plain language)."""
    src = os.path.expanduser(src or "")
    if not src or not os.path.exists(src):
        return None, "I can't find that file."

    if os.path.isdir(src):
        return _find_root(src), ""

    tar_path = src
    if src.endswith(ENCRYPTED_SUFFIX):
        if not passphrase:
            return None, "That backup is locked — I need the passphrase."
        tar_path = os.path.join(work, "brain.tar.gz")
        ok, detail = decrypt_file(src, tar_path, passphrase, runner=crypto)
        if not ok:
            return None, detail

    extracted = os.path.join(work, "extracted")
    os.makedirs(extracted, exist_ok=True)
    try:
        with tarfile.open(tar_path, "r:*") as tar:
            _safe_extract(tar, extracted)
    except (tarfile.TarError, ValueError, OSError):
        return None, "That doesn't look like a Zilla backup."
    return _find_root(extracted), ""


def _find_root(path: str) -> str | None:
    """The directory that actually holds Memory/ — the archive root, or the
    single wrapper folder inside it."""
    if os.path.isdir(os.path.join(path, "Memory")):
        return path
    try:
        children = [os.path.join(path, n) for n in sorted(os.listdir(path))]
    except OSError:
        return None
    for child in children:
        if os.path.isdir(child) and os.path.isdir(os.path.join(child, "Memory")):
            return child
    return None


def import_brain(src: str, *, passphrase: str | None = None, db=None,
                 mem_dir: str | None = None, media_dir: str | None = None,
                 env_path: str | None = None, replaced_dir: str | None = None,
                 now: float | None = None, crypto=_openssl) -> dict:
    """Restore a brain from an archive or an unpacked directory: files first,
    then the snapshot, then the indexes rebuilt from those files. Returns
    `{ok, steps, counts, missing_env_keys, replaced, message}`.

    The Memory tree already on this machine is never deleted — it is moved
    into `Runtime/Replaced/` first, so a mistaken import is recoverable."""
    config = _cfg()
    db = _db(db)
    mem_dir = mem_dir or config.MEMORY_DIR
    media_dir = media_dir or config.MEDIA_DIR
    env_path = env_path if env_path is not None else os.path.join(config.BASE_DIR, ".env")
    replaced_dir = replaced_dir or config.REPLACED_DIR

    steps: list[Step] = []
    counts: dict = {}
    replaced = ""
    work = tempfile.mkdtemp(prefix="zilla_import_")
    try:
        root, error = _stage_source(src, passphrase, work, crypto=crypto)
        if root is None:
            steps.append(Step("open the backup", False, error))
            return {"ok": False, "steps": steps, "counts": counts,
                    "missing_env_keys": [], "replaced": "",
                    "message": error or "That doesn't look like a Zilla backup."}
        snap_path = os.path.join(root, SNAPSHOT_REL)
        if not os.path.exists(snap_path):
            steps.append(Step("open the backup", False, "no state snapshot inside"))
            return {"ok": False, "steps": steps, "counts": counts,
                    "missing_env_keys": [], "replaced": "",
                    "message": "That backup is incomplete — no settings file inside."}
        try:
            with open(snap_path, "r", encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, json.JSONDecodeError):
            steps.append(Step("open the backup", False, "unreadable state snapshot"))
            return {"ok": False, "steps": steps, "counts": counts,
                    "missing_env_keys": [], "replaced": "",
                    "message": "That backup's settings file is damaged."}
        steps.append(Step("open the backup", True,
                          f"made {snap.get('exported_at', 'at an unknown time')}"))

        # Files. The existing tree moves aside whole (with its .git) before
        # the archive's tree lands, so the two histories never interleave.
        if os.path.isdir(mem_dir) and os.listdir(mem_dir):
            stamp = datetime.fromtimestamp(
                now if now is not None else time.time()).strftime("%Y%m%d-%H%M%S")
            os.makedirs(replaced_dir, exist_ok=True)
            replaced = os.path.join(replaced_dir, f"Memory-{stamp}")
            shutil.move(mem_dir, replaced)
        os.makedirs(mem_dir, exist_ok=True)
        counts["memory_files"] = _copy_tree(os.path.join(root, "Memory"), mem_dir)
        steps.append(Step("restore the memory tree", True,
                          f"{counts['memory_files']} files"))

        counts["media_files"] = 0
        src_media = os.path.join(root, "Media")
        if os.path.isdir(src_media):
            for dirpath, _dirs, files in os.walk(src_media):
                for name in files:
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, src_media)
                    target = os.path.join(media_dir, rel)
                    if os.path.exists(target):
                        continue  # never overwrite a file already on this machine
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(full, target)
                    counts["media_files"] += 1
        steps.append(Step("restore media", True, f"{counts['media_files']} files"))

        counts.update(apply_snapshot(db, snap))
        steps.append(Step("restore settings, schedules and people", True,
                          f"{counts.get('schedules', 0)} schedules, "
                          f"{counts.get('users', 0)} people"))

        counts.update(rebuild_indexes(db, mem_dir))
        steps.append(Step("rebuild search and the graph", True,
                          f"{counts.get('wiki_pages', 0)} pages, "
                          f"{counts.get('entities', 0)} entities"))

        counts["curiosity"] = apply_curiosity(db, snap.get("curiosity") or [])

        missing = []
        template_path = os.path.join(root, ENV_TEMPLATE_REL)
        if os.path.exists(template_path):
            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    template_text = f.read()
            except OSError:
                template_text = ""
            missing = env_template_missing_keys(template_text, _read_env(env_path))

        return {"ok": True, "steps": steps, "counts": counts,
                "missing_env_keys": missing, "replaced": replaced,
                "message": _import_message(counts, missing)}
    except Exception as e:
        logger.warning(f"[BRAIN] import failed: {e}")
        steps.append(Step("finish the restore", False, str(e)[:200]))
        return {"ok": False, "steps": steps, "counts": counts,
                "missing_env_keys": [], "replaced": replaced,
                "message": "The restore didn't finish. Your old memory is safe"
                           + (f" in {replaced}." if replaced else ".")}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def rebuild_indexes(db, mem_dir: str) -> dict:
    """Rebuild everything the database only *derives* from the Markdown: the
    entity graph (wiped and re-parsed, so no node from the old machine
    survives) and the full-text index (ledger cleared first — see
    Store.fts_clear). This is the P1 proof: nothing here is imported, it is
    all recomputed from the owner's files."""
    from zilla import graph as _graph
    from zilla import memory as _memory

    pages = _graph.rebuild(db, mem_dir)
    db.fts_clear()
    docs = _memory.reindex(base=mem_dir)
    return {"wiki_pages": pages, "indexed_docs": docs,
            "entities": len(db.graph_nodes_all()),
            "relations": len(db.graph_edges_all())}


def _read_env(env_path: str) -> dict:
    data: dict = {}
    if not env_path or not os.path.exists(env_path):
        return data
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return data


def _import_message(counts: dict, missing: list[str]) -> str:
    parts = [f"Restored: {counts.get('memory_files', 0)} memory files, "
             f"{counts.get('entities', 0)} people and things, "
             f"{counts.get('schedules', 0)} reminders."]
    if missing:
        parts.append("Still needed in .env: " + ", ".join(missing[:6]) + ".")
    return " ".join(parts)


# ── owner-facing report ─────────────────────────────────────

def format_steps(result: dict) -> str:
    """The whole outcome in a few lines: a tick per step, then the one
    sentence that says what to do next."""
    lines = []
    for step in result.get("steps", []):
        mark = "✅" if step.ok else "❌"
        lines.append(f"  {mark} {step.name}" + (f" — {step.detail}" if step.detail else ""))
    skipped = result.get("skipped") or []
    if skipped:
        lines.append("  Too big to carry:")
        for item in skipped[:5]:
            lines.append(f"    • {item['path']} ({item['bytes'] // (1024 * 1024)} MB)")
        if len(skipped) > 5:
            lines.append(f"    • …and {len(skipped) - 5} more")
    if result.get("replaced"):
        lines.append(f"  Your previous memory is kept at {result['replaced']}")
    if result.get("message"):
        lines.append("")
        lines.append(f"  {result['message']}")
    return "\n".join(lines)
