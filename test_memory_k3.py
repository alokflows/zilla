# ============================================================
#  TESTS — Phase K3: curiosity loop (PLAN.md §6.K3 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/graph.py: parse_entity_page() attrs capture, _structural_gaps()
#      (person w/o contact, org/place w/o located_in), _sync_ghost_gaps()
#      (ghost referenced from >=2 pages), pending_curiosity() (relevance
#      gate to this turn's alias_scan hits, one pick, asked_at side effect,
#      cooldown).
#    - zilla/store.py: curiosity_sync_node() (preserves asked_at for a gap
#      still open, drops a gap once it's closed).
#    - zilla/harness.py: wrap_prompt() [curiosity] injection — owner-only,
#      relevance-gated, at most one per turn, cooldown suppresses a repeat.
#
#  Run:  python test_memory_k3.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every test points zilla.memory.MEMORY_DIR at a throwaway tmpdir and
#  zilla.config.DB_FILE at a throwaway sqlite file (same pattern
#  test_memory_k1.py/test_memory_k2.py use) so a run never reads or writes
#  the real repo Memory/ tree or zilla.db.
# ============================================================

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

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
_tmpdir = tempfile.mkdtemp(prefix="zilla_k3_cfg_")
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
from zilla.harness import TurnContext, wrap_prompt  # noqa: E402


def _iso():
    """A fresh throwaway Memory/ dir + a fresh graph in the shared test db."""
    tmp = tempfile.mkdtemp(prefix="zilla_k3_mem_")
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


# ── 1. parse_entity_page() — attrs captured ──

def test_parse_attrs():
    print("\n[1] parse_entity_page() — non-type/aliases attribute lines captured in attrs")
    parsed = graph.parse_entity_page(
        "# Ramesh Kumar\nCousin.\n- type:: person\n- contact:: +91-9000000000\n"
        "## Relations\n- family_of:: [[Suresh]]\n"
    )
    check("contact attr captured", parsed["attrs"].get("contact") == "+91-9000000000", parsed)
    check("type still parsed", parsed["type"] == "person", parsed)


# ── 2. structural gap: person with no contact ──

def test_gap_no_contact():
    print("\n[2] person with no contact:: -> GAP_NO_CONTACT; with it -> no gap")
    tmp, old, db = _iso()
    try:
        _write_page("ramesh.md", "# Ramesh\nCousin.\n- type:: person\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        node = graph.resolve_name(db, "Ramesh")
        gaps = {r["gap"] for r in db.curiosity_all() if r["node_id"] == node["id"]}
        check("no_contact gap detected", gaps == {graph.GAP_NO_CONTACT}, gaps)

        # Owner fills in contact -> gap must clear on next reindex.
        _write_page("ramesh.md", "# Ramesh\nCousin.\n- type:: person\n- contact:: 555-1234\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        gaps2 = {r["gap"] for r in db.curiosity_all() if r["node_id"] == node["id"]}
        check("gap cleared once contact is added", gaps2 == set(), gaps2)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 3. structural gap: org/place with no located_in ──

def test_gap_no_located_in():
    print("\n[3] org/place with no located_in:: -> GAP_NO_LOCATED_IN; with it -> no gap")
    tmp, old, db = _iso()
    try:
        _write_page("office.md", "# Passport Office\nGovt office.\n- type:: org\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        node = graph.resolve_name(db, "Passport Office")
        gaps = {r["gap"] for r in db.curiosity_all() if r["node_id"] == node["id"]}
        check("no_located_in gap detected", gaps == {graph.GAP_NO_LOCATED_IN}, gaps)

        _write_page(
            "office.md",
            "# Passport Office\nGovt office.\n- type:: org\n"
            "## Relations\n- located_in:: [[Delhi]]\n",
        )
        graph.reindex_graph(db, memory.MEMORY_DIR)
        gaps2 = {r["gap"] for r in db.curiosity_all() if r["node_id"] == node["id"]}
        check("gap cleared once located_in is added", gaps2 == set(), gaps2)

        # A type outside {person, org, place} never gets either gap.
        _write_page("proj.md", "# SomeProject\nA project.\n- type:: project\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        pnode = graph.resolve_name(db, "SomeProject")
        pgaps = {r["gap"] for r in db.curiosity_all() if r["node_id"] == pnode["id"]}
        check("unrelated type gets no gaps", pgaps == set(), pgaps)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 4. ghost gap: referenced from >= 2 pages ──

def test_gap_ghost_multi_ref():
    print("\n[4] ghost node referenced from >=2 pages -> GAP_GHOST_MULTI_REF")
    tmp, old, db = _iso()
    try:
        _write_page("a.md", "# A\nBio.\n- type:: person\n## Relations\n- knows:: [[Suresh]]\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        ghost = graph.resolve_name(db, "Suresh")
        gaps = {r["gap"] for r in db.curiosity_all() if r["node_id"] == ghost["id"]}
        check("single reference -> no gap yet", gaps == set(), gaps)

        _write_page("b.md", "# B\nBio.\n- type:: person\n## Relations\n- knows:: [[Suresh]]\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        gaps2 = {r["gap"] for r in db.curiosity_all() if r["node_id"] == ghost["id"]}
        check("second reference -> ghost_multi_ref gap", gaps2 == {graph.GAP_GHOST_MULTI_REF}, gaps2)

        # Ghost gets a real page -> promoted, no longer eligible for the ghost gap.
        _write_page("suresh.md", "# Suresh\nA real person.\n- type:: person\n- contact:: x\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        promoted = graph.resolve_name(db, "Suresh")
        gaps3 = {r["gap"] for r in db.curiosity_all() if r["node_id"] == promoted["id"]}
        check("promoted node has no ghost gap", graph.GAP_GHOST_MULTI_REF not in gaps3, gaps3)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 5. curiosity_sync_node() preserves asked_at for a gap still open ──

def test_sync_preserves_asked_at():
    print("\n[5] curiosity_sync_node() keeps asked_at for a gap that's still open")
    tmp, old, db = _iso()
    try:
        _write_page("ramesh.md", "# Ramesh\nCousin.\n- type:: person\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        node = graph.resolve_name(db, "Ramesh")
        db.curiosity_mark_asked(node["id"], graph.GAP_NO_CONTACT, "2020-01-01T00:00:00")

        # Reindex again with the gap still present (no page change).
        graph.reindex_graph(db, memory.MEMORY_DIR)
        row = next(r for r in db.curiosity_all() if r["node_id"] == node["id"])
        check("asked_at preserved across a reindex where the gap persists",
              row["asked_at"] == "2020-01-01T00:00:00", row)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 6. pending_curiosity() — relevance gate ──

def test_pending_curiosity_relevance_gate():
    print("\n[6] pending_curiosity() — only a hit node's own gap is eligible")
    tmp, old, db = _iso()
    try:
        _write_page("ramesh.md", "# Ramesh\nCousin.\n- type:: person\n- aliases:: Ramesh\n")
        _write_page("suresh.md", "# Suresh\nFriend.\n- type:: person\n- aliases:: Suresh\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        ramesh = graph.resolve_name(db, "Ramesh")

        # Suresh has a gap too, but only Ramesh was "mentioned" (is in hits).
        pick = graph.pending_curiosity(db, [ramesh])
        check("pick is not None", pick is not None, pick)
        check("picked node is Ramesh", pick and pick["node"]["id"] == ramesh["id"], pick)
        check("gap is no_contact", pick and pick["gap"] == graph.GAP_NO_CONTACT, pick)

        check("empty hits -> None", graph.pending_curiosity(db, []) is None)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 7. pending_curiosity() — one pick, cooldown ──

def test_pending_curiosity_cooldown():
    print("\n[7] pending_curiosity() — marks asked_at, cooldown suppresses a repeat")
    tmp, old, db = _iso()
    try:
        _write_page("ramesh.md", "# Ramesh\nCousin.\n- type:: person\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        node = graph.resolve_name(db, "Ramesh")

        pick = graph.pending_curiosity(db, [node], now="2026-07-19T10:00:00")
        check("first ask returns the gap", pick is not None, pick)

        row = next(r for r in db.curiosity_all() if r["node_id"] == node["id"])
        check("asked_at stamped", row["asked_at"] == "2026-07-19T10:00:00", row)

        # Same day (well within the 7-day cooldown) -> nothing pending.
        pick2 = graph.pending_curiosity(db, [node], now="2026-07-19T10:05:00")
        check("re-ask same day -> None (cooldown)", pick2 is None, pick2)

        # 8 days later -> cooldown has expired, eligible again.
        pick3 = graph.pending_curiosity(db, [node], now="2026-07-27T10:05:00")
        check("re-ask 8 days later -> pending again", pick3 is not None, pick3)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 8. wrap_prompt() injection — owner-only gating, header, relevance ──

def test_injection_gating():
    print("\n[8] wrap_prompt() — [curiosity] only for owner turns mentioning a gapped entity")
    tmp, old, db = _iso()
    try:
        memory.ensure_tree()
        _write_page("ramesh.md", "# Ramesh\nCousin.\n- type:: person\n- aliases:: Ramesh\n")
        memory.reindex()

        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)
        limited_ctx = TurnContext(uid=999, role="limited", is_owner=False)

        limited_hit = wrap_prompt("call ramesh today", is_new=False, ctx=limited_ctx)
        check("non-owner turn -> no [curiosity] block", "[curiosity]" not in limited_hit, limited_hit)

        none_hit = wrap_prompt("call ramesh today", is_new=False, ctx=None)
        check("ctx=None -> no [curiosity] block", "[curiosity]" not in none_hit, none_hit)

        owner_miss = wrap_prompt("what's the weather", is_new=False, ctx=owner_ctx)
        check("owner turn, no entity mentioned -> no [curiosity] block",
              "[curiosity]" not in owner_miss, owner_miss)

        owner_hit = wrap_prompt("call ramesh today", is_new=False, ctx=owner_ctx)
        check("owner turn, gapped entity mentioned -> [curiosity] present",
              "[curiosity]" in owner_hit, owner_hit)
        check("question references the contact gap", "contact" in owner_hit, owner_hit)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 9. wrap_prompt() — at most one question, cooldown across turns ──

def test_injection_one_question_per_conversation():
    print("\n[9] wrap_prompt() — mentioning a gapped entity twice asks exactly once")
    tmp, old, db = _iso()
    try:
        memory.ensure_tree()
        _write_page("ramesh.md", "# Ramesh\nCousin.\n- type:: person\n- aliases:: Ramesh\n")
        memory.reindex()
        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)

        first = wrap_prompt("tell ramesh hi", is_new=False, ctx=owner_ctx)
        check("first mention -> [curiosity] present", "[curiosity]" in first, first)

        second = wrap_prompt("tell ramesh hi again", is_new=False, ctx=owner_ctx)
        check("second mention (cooldown active) -> no repeat [curiosity]",
              "[curiosity]" not in second, second)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 10. wrap_prompt() — multiple hits, only the strongest's gap surfaces ──

def test_injection_single_pick_among_multiple_hits():
    print("\n[10] wrap_prompt() — exactly one [curiosity] block even with multiple gapped hits")
    tmp, old, db = _iso()
    try:
        memory.ensure_tree()
        _write_page("ramesh.md", "# Ramesh\nCousin.\n- type:: person\n- aliases:: Ramesh\n")
        _write_page("suresh.md", "# Suresh\nFriend.\n- type:: person\n- aliases:: Suresh\n")
        memory.reindex()
        owner_ctx = TurnContext(uid=111, role="owner", is_owner=True)

        wrapped = wrap_prompt("ramesh and suresh should meet", is_new=False, ctx=owner_ctx)
        check("exactly one [curiosity] header", wrapped.count("[curiosity]") == 1, wrapped)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


TESTS = [
    test_parse_attrs,
    test_gap_no_contact,
    test_gap_no_located_in,
    test_gap_ghost_multi_ref,
    test_sync_preserves_asked_at,
    test_pending_curiosity_relevance_gate,
    test_pending_curiosity_cooldown,
    test_injection_gating,
    test_injection_one_question_per_conversation,
    test_injection_single_pick_among_multiple_hits,
]


def main():
    for t in TESTS:
        t()
    print(f"\n{_passed} passed, {_failed} failed")
    shutil.rmtree(_tmpdir, ignore_errors=True)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
