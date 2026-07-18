# ============================================================
#  TESTS — Phase K2: turn-time entity linking + neighborhood injection
#  (PLAN.md §6.K2 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/graph.py: alias_scan() (word boundary, longest match, cap,
#      ghost nodes included, empty text/no hits), local_card_lines()
#      (bio + edges, ghost marker, hops).
#    - zilla/harness.py: _graph_block()/wrap_prompt() injection golden
#      test — owner-only gating (same single gate as the M2 memory
#      block), `[via graph]` header, strongest-hit gets 2 hops, overall
#      line cap, silent no-op on no match / no ctx.
#
#  Run:  python test_memory_k2.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every test points zilla.memory.MEMORY_DIR at a throwaway tmpdir and
#  zilla.config.DB_FILE at a throwaway sqlite file (same pattern
#  test_memory_k1.py/test_harness.py use) so a run never reads or writes
#  the real repo Memory/ tree or zilla.db.
# ============================================================

import json
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


# ── Isolate config BEFORE anything touches the store ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_k2_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.memory as memory  # noqa: E402
from zilla import graph  # noqa: E402
from zilla import store as _store  # noqa: E402
from zilla.harness import TurnContext, wrap_prompt, build_preamble  # noqa: E402


def _iso():
    """A fresh throwaway Memory/ dir + a fresh graph in the shared test db."""
    tmp = tempfile.mkdtemp(prefix="zilla_k2_mem_")
    old = memory.MEMORY_DIR
    memory.MEMORY_DIR = os.path.join(tmp, "Memory")
    db = _store.get_store(config.DB_FILE)
    db.graph_clear()
    return tmp, old, db


def _write_page(rel: str, text: str) -> str:
    full = os.path.join(memory.MEMORY_DIR, "Wiki", rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
    return f"Wiki/{rel}".replace(os.sep, "/")


RAMESH_PAGE = (
    "# Ramesh Kumar\n"
    "Cousin; the person to call for anything passport-related.\n"
    "- type:: person\n"
    "- aliases:: Ramesh, my cousin\n"
    "## Relations\n"
    "- works_at:: [[Passport Office]] (since 2024-01)\n"
    "- family_of:: [[Suresh]]\n"
)


# ── 1. alias_scan() — word boundary ──

def test_alias_scan_word_boundary():
    print("\n[1] alias_scan() — case-insensitive, word-bounded")
    tmp, old, db = _iso()
    try:
        _write_page("ramesh-kumar.md", RAMESH_PAGE)
        graph.reindex_graph(db, memory.MEMORY_DIR)

        hits = graph.alias_scan(db, "call ramesh tomorrow")
        check("lowercase alias hits (case-insensitive)", len(hits) == 1, hits)
        check("hit is Ramesh Kumar", hits and hits[0]["title"] == "Ramesh Kumar", hits)

        hits2 = graph.alias_scan(db, "I met Rameshwaram last year")
        check("no false-positive on 'Rameshwaram' (word boundary)", hits2 == [], hits2)

        hits3 = graph.alias_scan(db, "nothing relevant here")
        check("no hits on unrelated text", hits3 == [], hits3)

        check("empty text -> []", graph.alias_scan(db, "") == [])
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 2. alias_scan() — longest match wins over a shorter overlapping name ──

def test_alias_scan_longest_match():
    print("\n[2] alias_scan() — longest match wins for overlapping names")
    tmp, old, db = _iso()
    try:
        _write_page("new-york.md", "# New York\nA city.\n- type:: place\n")
        _write_page(
            "new-york-city-project.md",
            "# New York City Project\nA client project.\n- type:: project\n",
        )
        graph.reindex_graph(db, memory.MEMORY_DIR)

        hits = graph.alias_scan(db, "flying to New York City Project next week")
        check("exactly one hit (no double count of overlapping span)", len(hits) == 1, hits)
        check("longest name wins", hits and hits[0]["title"] == "New York City Project", hits)

        hits2 = graph.alias_scan(db, "flying to New York next week")
        check("shorter name still matches on its own", len(hits2) == 1, hits2)
        check("shorter name resolves correctly", hits2 and hits2[0]["title"] == "New York", hits2)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 3. alias_scan() — cap ──

def test_alias_scan_cap():
    print("\n[3] alias_scan() — capped at `cap` distinct nodes")
    tmp, old, db = _iso()
    try:
        for i in range(5):
            _write_page(f"person-{i}.md", f"# Person{i}\nBio.\n- type:: person\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)

        text = "Person0 Person1 Person2 Person3 Person4 all in one message"
        hits = graph.alias_scan(db, text, cap=3)
        check("capped at 3", len(hits) == 3, hits)

        hits_default = graph.alias_scan(db, text)
        check("default cap is also 3", len(hits_default) == 3, hits_default)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 4. alias_scan() — ghost nodes are matchable by title ──

def test_alias_scan_matches_ghost_by_title():
    print("\n[4] alias_scan() — a ghost node (referenced, no page yet) is matchable")
    tmp, old, db = _iso()
    try:
        _write_page("ramesh-kumar.md", RAMESH_PAGE)
        graph.reindex_graph(db, memory.MEMORY_DIR)  # creates ghost "Suresh"

        hits = graph.alias_scan(db, "tell Suresh I said hi")
        check("ghost node matched by title", len(hits) == 1, hits)
        check("matched node is a ghost", hits and hits[0]["is_ghost"], hits)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 5. local_card_lines() — bio + edges, ghost marker ──

def test_local_card_lines():
    print("\n[5] local_card_lines() — bio + current edges, ghost marker")
    tmp, old, db = _iso()
    try:
        _write_page("ramesh-kumar.md", RAMESH_PAGE)
        graph.reindex_graph(db, memory.MEMORY_DIR)

        node = graph.resolve_name(db, "Ramesh Kumar")
        lines = graph.local_card_lines(db, node, hops=1)
        text = "\n".join(lines)
        check("title present", "Ramesh Kumar" in text, text)
        check("bio present", "Cousin" in text, text)
        check("works_at edge present", "works_at" in text and "Passport Office" in text, text)

        ghost = graph.resolve_name(db, "Suresh")
        ghost_lines = graph.local_card_lines(db, ghost, hops=1)
        ghost_text = "\n".join(ghost_lines)
        check("ghost marker present", "[ghost" in ghost_text, ghost_text)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 6. wrap_prompt() injection — owner-only gating, header, no-match no-op ──

def test_injection_gating_and_header():
    print("\n[6] wrap_prompt() — [via graph] card only for is_owner=True, on a hit")
    tmp, old, db = _iso()
    try:
        memory.ensure_tree()
        _write_page("ramesh-kumar.md", RAMESH_PAGE)
        memory.reindex()  # real call site's path: FTS + graph, same as a live turn

        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)
        limited_ctx = TurnContext(uid=999, role="limited", is_owner=False)

        owner_hit = wrap_prompt("can you call ramesh today", is_new=False, ctx=owner_ctx)
        check("owner turn, alias mentioned -> [via graph] present",
              "[via graph]" in owner_hit, owner_hit)
        check("owner turn -> bio surfaced", "Cousin" in owner_hit, owner_hit)
        check("owner turn -> edge surfaced", "Passport Office" in owner_hit, owner_hit)

        limited_hit = wrap_prompt("can you call ramesh today", is_new=False, ctx=limited_ctx)
        check("non-owner turn, same alias -> no [via graph] block",
              "[via graph]" not in limited_hit, limited_hit)
        check("non-owner turn -> no bio leak", "Cousin" not in limited_hit, limited_hit)

        none_hit = wrap_prompt("can you call ramesh today", is_new=False, ctx=None)
        check("ctx=None -> no [via graph] block", "[via graph]" not in none_hit, none_hit)

        owner_miss = wrap_prompt("what's the weather like", is_new=False, ctx=owner_ctx)
        check("owner turn, no known entity mentioned -> no [via graph] block",
              "[via graph]" not in owner_miss, owner_miss)

        # Also exercised through build_preamble directly is NOT applicable —
        # the graph card needs the raw user message, which only wrap_prompt
        # has; build_preamble alone must never carry it.
        pre = build_preamble(is_new=False, ctx=owner_ctx)
        check("build_preamble alone never carries the graph card (needs user_message)",
              "[via graph]" not in pre, pre)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 7. wrap_prompt() injection — strongest hit gets 2 hops, others 1 ──

def test_injection_strongest_hit_two_hops():
    print("\n[7] wrap_prompt() — the single strongest (longest-matched) hit gets a 2-hop card")
    tmp, old, db = _iso()
    try:
        memory.ensure_tree()
        # Ramesh Kumar -> works_at -> Passport Office -> (2nd hop) nothing yet;
        # give Passport Office its own outgoing edge so a 2-hop card differs
        # from a 1-hop one.
        _write_page("ramesh-kumar.md", RAMESH_PAGE)
        _write_page(
            "passport-office.md",
            "# Passport Office\nGovernment office.\n- type:: org\n"
            "## Relations\n- located_in:: [[Delhi]]\n",
        )
        memory.reindex()

        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)
        wrapped = wrap_prompt("ping Ramesh Kumar about the passport", is_new=False, ctx=owner_ctx)
        check("2-hop reaches Delhi via Ramesh Kumar's strongest-hit card",
              "Delhi" in wrapped, wrapped)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 8. wrap_prompt() injection — overall line cap never bloats the prompt ──

def test_injection_line_cap():
    print("\n[8] wrap_prompt() — graph card capped, never crashes on a busy graph")
    tmp, old, db = _iso()
    try:
        memory.ensure_tree()
        # One hub node with many outgoing edges -> more than the cap's worth
        # of lines for a single hit.
        lines = ["# Hub\nA very connected person.\n- type:: person\n- aliases:: Hub\n",
                 "## Relations\n"]
        for i in range(40):
            lines.append(f"- knows:: [[Contact{i}]]\n")
        _write_page("hub.md", "".join(lines))
        memory.reindex()

        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)
        wrapped = wrap_prompt("what's up with Hub", is_new=False, ctx=owner_ctx)
        # Isolate just the graph card block for a clean line count.
        card = wrapped.split("[via graph]", 1)[1].split("\n\nUSER MESSAGE", 1)[0]
        card_lines = card.strip("\n").split("\n")
        check("card line count respects the cap",
              len(card_lines) <= 25, len(card_lines))
        check("truncation marker present when clipped", "[truncated]" in wrapped, wrapped[:400])
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


TESTS = [
    test_alias_scan_word_boundary,
    test_alias_scan_longest_match,
    test_alias_scan_cap,
    test_alias_scan_matches_ghost_by_title,
    test_local_card_lines,
    test_injection_gating_and_header,
    test_injection_strongest_hit_two_hops,
    test_injection_line_cap,
]


def main():
    for t in TESTS:
        t()
    print(f"\n{_passed} passed, {_failed} failed")
    shutil.rmtree(_tmpdir, ignore_errors=True)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
