# ============================================================
#  TESTS — zilla.memory (Markdown knowledge tier) + zilla.harness
#  memory injection (PLAN.md §4/§5.M2)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/memory.py: tree creation/idempotency, template detection,
#      wiki index formatting + line cap, journal append.
#    - zilla/harness.py: TurnContext, and the owner-only "Your memory"
#      block build_preamble/wrap_prompt append — gating, first-run
#      interview line, soft/hard caps, the memsearch.py forward-compat
#      line, and concurrent two-principal isolation (no leakage of the
#      owner's memory into a non-owner's prompt under real concurrency).
#
#  Run:  python test_harness.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every test points zilla.memory.MEMORY_DIR at a throwaway tmpdir
#  (same pattern test_core.py/test_fixes.py use for MEMORY_DIR) so a
#  run never reads or writes the real repo Memory/ tree.
# ============================================================

import os
import sys
import shutil
import tempfile
import threading

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
# build_preamble()/operating_contract() read get_backend()/get_model(),
# which go through zilla.store.get_store(SETTINGS_FILE) — left pointed at
# the real DB_FILE (repo root zilla.db), any call here would create it as
# a side effect (same trap test_core.py/test_fixes.py already isolate
# against). Route it at a throwaway tmp DB instead.
_tmpdir = tempfile.mkdtemp(prefix="zilla_harness_cfg_")
import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.memory as memory  # noqa: E402
import zilla.harness as harness  # noqa: E402
from zilla.harness import TurnContext, build_preamble, wrap_prompt  # noqa: E402


def _iso_mem_dir():
    """A fresh throwaway Memory/ dir, and the previous memory.MEMORY_DIR to
    restore (tests must not leak isolation state into each other)."""
    tmp = tempfile.mkdtemp(prefix="zilla_harness_test_")
    old = memory.MEMORY_DIR
    memory.MEMORY_DIR = os.path.join(tmp, "Memory")
    return tmp, old


# ── 1. memory.ensure_tree — creates the full tree, idempotent ──

def test_ensure_tree_creates_and_is_idempotent():
    print("\n[1] memory.ensure_tree — creates the tree, never clobbers on rerun")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        mem_dir = memory.MEMORY_DIR
        check("MEMORY.md created", os.path.isfile(os.path.join(mem_dir, "MEMORY.md")))
        check("HEARTBEAT.md created", os.path.isfile(os.path.join(mem_dir, "HEARTBEAT.md")))
        check("Journal/ dir created", os.path.isdir(os.path.join(mem_dir, "Journal")))
        check("Skills/ dir created", os.path.isdir(os.path.join(mem_dir, "Skills")))
        for sub in memory.WIKI_SUBDIRS:
            check(f"Wiki/{sub}/ created", os.path.isdir(os.path.join(mem_dir, "Wiki", sub)))
        check("starter Wiki pages seeded",
              os.path.isfile(os.path.join(mem_dir, "Wiki", "People", "owner.md")))

        # Diverge MEMORY.md (simulate the owner/agent having edited it), then
        # rerun ensure_tree — must NOT be clobbered.
        edited = "# Your memory\n\nAlok is the owner.\n"
        with open(os.path.join(mem_dir, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write(edited)
        memory.ensure_tree()
        with open(os.path.join(mem_dir, "MEMORY.md"), encoding="utf-8") as f:
            check("rerun does not clobber an edited MEMORY.md", f.read() == edited)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 2. memory.read_core / is_template ──

def test_read_core_and_is_template():
    print("\n[2] memory.read_core / is_template — template detection")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        check("is_template() true right after ensure_tree", memory.is_template())
        with open(os.path.join(memory.MEMORY_DIR, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("# Your memory\n\nAlok, beginner builder, phone-first.\n")
        check("is_template() false once diverged", not memory.is_template())
        check("read_core() reflects the edit", "beginner builder" in memory.read_core())
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 3. memory.wiki_index_text — format + line cap ──

def test_wiki_index_text_format_and_cap():
    print("\n[3] memory.wiki_index_text — 'path — summary' format, line cap")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        text = memory.wiki_index_text()
        check("starter pages listed", "Wiki/People/owner.md" in text, text)
        check("summary included", " — " in text, text)

        # Blow past the cap with synthetic pages and confirm truncation.
        people_dir = os.path.join(memory.MEMORY_DIR, "Wiki", "People")
        for i in range(150):
            with open(os.path.join(people_dir, f"p{i:03d}.md"), "w", encoding="utf-8") as f:
                f.write(f"# Person {i}\nSummary: test page {i}\n")
        capped = memory.wiki_index_text(max_index_lines=100)
        lines = capped.splitlines()
        check("capped at max_index_lines + 1 marker line", len(lines) == 101, len(lines))
        check("truncation marker present", lines[-1] == "[index truncated — consolidate pages]",
              lines[-1])
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 4. memory.append_journal ──

def test_append_journal_creates_and_appends():
    print("\n[4] memory.append_journal — creates today's file, appends timestamped lines")
    tmp, old = _iso_mem_dir()
    try:
        p1 = memory.append_journal("first fact")
        p2 = memory.append_journal("second fact")
        check("same file both times (same day)", p1 == p2, (p1, p2))
        with open(p1, encoding="utf-8") as f:
            content = f.read()
        check("first fact present", "first fact" in content, content)
        check("second fact present", "second fact" in content, content)
        check("timestamped bullet format", content.count(" — ") >= 2, content)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 5. TurnContext — plain dataclass, frozen ──

def test_turn_context_shape():
    print("\n[5] TurnContext — fields + frozen")
    ctx = TurnContext(uid=111, role="owner", is_owner=True, origin="user")
    check("uid", ctx.uid == 111)
    check("role", ctx.role == "owner")
    check("is_owner", ctx.is_owner is True)
    check("origin default reflected", ctx.origin == "user")
    try:
        ctx.uid = 222
        check("frozen — mutation raises", False)
    except Exception:
        check("frozen — mutation raises", True)


# ── 6. memory injection gating — None / non-owner / owner ──

def test_memory_injection_gating():
    print("\n[6] build_preamble/wrap_prompt — memory block only for is_owner=True")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        with open(os.path.join(memory.MEMORY_DIR, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("# Your memory\n\nAlok is the owner, beginner builder.\n")

        none_pre = build_preamble(is_new=False, ctx=None)
        check("ctx=None -> no memory block", "Your memory" not in none_pre, none_pre)

        limited_ctx = TurnContext(uid=999, role="limited", is_owner=False)
        limited_pre = build_preamble(is_new=False, ctx=limited_ctx)
        check("non-owner ctx -> no memory block", "Your memory" not in limited_pre, limited_pre)
        check("non-owner ctx -> owner fact NOT leaked",
              "beginner builder" not in limited_pre, limited_pre)

        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)
        owner_pre = build_preamble(is_new=False, ctx=owner_ctx)
        check("owner ctx -> memory block present", "## Your memory" in owner_pre, owner_pre)
        check("owner ctx -> MEMORY.md content present", "beginner builder" in owner_pre, owner_pre)
        check("owner ctx -> wiki index section present", "## Wiki index" in owner_pre, owner_pre)
        check("owner ctx -> memory protocol section present", "## Memory protocol" in owner_pre,
              owner_pre)

        # Also exercised through wrap_prompt (the real call site) on the
        # is_new=True path.
        wrapped = wrap_prompt("hello", is_new=True, ctx=owner_ctx)
        check("wrap_prompt is_new=True also injects for owner", "## Your memory" in wrapped)
        check("wrap_prompt still carries the user message boundary",
              "USER MESSAGE" in wrapped and wrapped.endswith("hello"))
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 7. first-run interview line — template vs. edited ──

def test_first_run_interview_line():
    print("\n[7] memory block — first-run interview line while MEMORY.md is still the template")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()  # MEMORY.md left as the seeded template
        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)
        pre = build_preamble(is_new=False, ctx=owner_ctx)
        check("interview line present while template", "interview the owner" in pre, pre)

        with open(os.path.join(memory.MEMORY_DIR, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("# Your memory\n\nFilled in now.\n")
        pre2 = build_preamble(is_new=False, ctx=owner_ctx)
        check("interview line gone once MEMORY.md diverges",
              "interview the owner" not in pre2, pre2)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 8. soft/hard caps ──

def test_memory_block_caps():
    print("\n[8] memory block — soft-cap log warning + hard-cap truncation marker")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        big = "# Your memory\n\n" + ("x" * 5000) + "\n"
        with open(os.path.join(memory.MEMORY_DIR, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write(big)
        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)

        warnings = []
        orig_warning = harness.logger.warning
        harness.logger.warning = lambda msg, *a, **kw: warnings.append(msg)
        try:
            pre = build_preamble(is_new=False, ctx=owner_ctx)
        finally:
            harness.logger.warning = orig_warning

        check("soft-cap warning logged for an oversized MEMORY.md",
              any("soft cap" in w for w in warnings), warnings)
        check("truncation marker present", "[truncated — trim me]" in pre, pre[-100:])
        # Memory block is appended last — everything from its header to the
        # end of the preamble is the capped block plus the marker.
        block_portion = pre[pre.index("## Your memory"):]
        check("hard cap enforced on the assembled memory block",
              len(block_portion) <= harness._MEMORY_HARD_CAP + len("\n[truncated — trim me]") + 10,
              len(block_portion))
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 9. memsearch.py forward-compat line ──

def test_memsearch_line_appears_only_when_script_exists():
    print("\n[9] memory block — memsearch.py line only when the script actually exists")
    tmp, old = _iso_mem_dir()
    old_here = harness._HERE
    harness._HERE = tmp
    try:
        memory.ensure_tree()
        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)

        pre_without = build_preamble(is_new=False, ctx=owner_ctx)
        check("no memsearch.py -> no memsearch instruction",
              "memsearch.py" not in pre_without, pre_without)

        with open(os.path.join(tmp, "memsearch.py"), "w", encoding="utf-8") as f:
            f.write("# stub\n")
        pre_with = build_preamble(is_new=False, ctx=owner_ctx)
        check("memsearch.py present -> instruction line included",
              "memsearch.py" in pre_with, pre_with)
    finally:
        harness._HERE = old_here
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 10. concurrent two-principal isolation ──

def test_concurrent_owner_and_non_owner_no_leakage():
    print("\n[10] concurrency — owner and non-owner turns interleaved never cross-contaminate")
    tmp, old = _iso_mem_dir()
    try:
        memory.ensure_tree()
        with open(os.path.join(memory.MEMORY_DIR, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("# Your memory\n\nSECRET_OWNER_FACT_42\n")

        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)
        limited_ctx = TurnContext(uid=222, role="limited", is_owner=False)
        results = []
        lock = threading.Lock()

        def worker(i):
            ctx = owner_ctx if i % 2 == 0 else limited_ctx
            pre = build_preamble(is_new=False, ctx=ctx)
            with lock:
                results.append((ctx.is_owner, pre))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        check("40 turns all completed", len(results) == 40, len(results))
        owner_ok = all("SECRET_OWNER_FACT_42" in pre for is_owner, pre in results if is_owner)
        non_owner_clean = all("SECRET_OWNER_FACT_42" not in pre
                              for is_owner, pre in results if not is_owner)
        check("every owner-tagged turn saw the memory fact", owner_ok)
        check("no non-owner-tagged turn ever saw the memory fact", non_owner_clean)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    tests = [
        test_ensure_tree_creates_and_is_idempotent,
        test_read_core_and_is_template,
        test_wiki_index_text_format_and_cap,
        test_append_journal_creates_and_appends,
        test_turn_context_shape,
        test_memory_injection_gating,
        test_first_run_interview_line,
        test_memory_block_caps,
        test_memsearch_line_appears_only_when_script_exists,
        test_concurrent_owner_and_non_owner_no_leakage,
    ]
    print("Running zilla.memory / zilla.harness memory-injection tests...\n")
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
