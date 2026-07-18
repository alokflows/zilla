# ============================================================
#  TESTS — Phase K1: graph schema + indexer
#  (PLAN.md §6.K1 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/graph.py: parse_entity_page() golden tests (grammar incl.
#      ghost nodes, date intervals, alias multi-match, unknown verbs);
#      index_page()/reindex_graph()/rebuild() (rebuild-from-scratch ==
#      incremental result, ghost promotion, deletion/demotion);
#      neighbors()/find_path()/find_nodes() traversal (2-hop, path,
#      cycle safety).
#
#  Run:  python test_memory_k1.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every test points zilla.memory.MEMORY_DIR at a throwaway tmpdir and
#  zilla.config.DB_FILE at a throwaway sqlite file (same pattern
#  test_memory_m3.py uses) so a run never reads or writes the real repo
#  Memory/ tree or zilla.db.
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
_tmpdir = tempfile.mkdtemp(prefix="zilla_k1_cfg_")
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


def _iso() -> tuple[str, str, "object"]:
    """A fresh throwaway Memory/ dir + a fresh graph in the shared test db
    (graph_clear() between tests — the db file itself is shared per M3's
    pattern, but the graph tables must not leak node/edge state between
    tests since titles like 'Ramesh' repeat across them)."""
    tmp = tempfile.mkdtemp(prefix="zilla_k1_mem_")
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
    "- aliases:: Ramesh, my cousin, passport guy\n"
    "- phone:: +91 000\n"
    "## Relations\n"
    "- works_at:: [[Passport Office]] (since 2024-01)\n"
    "- family_of:: [[Suresh]]\n"
    "- worked_at:: [[XYZ Corp]] (2020 .. 2023-06)\n"
)


# ── 1. parse_entity_page() — golden test against the PLAN.md grammar ──

def test_parser_golden():
    print("\n[1] parse_entity_page() — golden grammar test")
    parsed = graph.parse_entity_page(RAMESH_PAGE)
    check("title", parsed["title"] == "Ramesh Kumar", parsed["title"])
    check("bio", parsed["bio"] == "Cousin; the person to call for anything passport-related.",
          parsed["bio"])
    check("type", parsed["type"] == "person", parsed["type"])
    check("aliases", parsed["aliases"] == ["Ramesh", "my cousin", "passport guy"],
          parsed["aliases"])
    rels = {(r["verb"], r["target"]): r for r in parsed["relations"]}
    check("3 relations parsed", len(parsed["relations"]) == 3, parsed["relations"])
    wa = rels.get(("works_at", "Passport Office"))
    check("works_at since-date", wa is not None and wa["valid_from"] == "2024-01"
          and wa["valid_to"] is None, wa)
    fo = rels.get(("family_of", "Suresh"))
    check("family_of no dates", fo is not None and fo["valid_from"] is None
          and fo["valid_to"] is None, fo)
    xy = rels.get(("worked_at", "XYZ Corp"))
    check("worked_at closed interval (superseded)",
          xy is not None and xy["valid_from"] == "2020" and xy["valid_to"] == "2023-06", xy)


def test_parser_unknown_verb_and_mentions():
    print("\n[2] parse_entity_page() — unknown verbs indexed, prose mentions captured")
    text = (
        "# Test Org\n"
        "An org that [[Ramesh Kumar]] mentioned once.\n"
        "- type:: org\n"
        "## Relations\n"
        "- collaborates_with_loosely:: [[Some Guy]]\n"
    )
    parsed = graph.parse_entity_page(text)
    check("unknown verb still parsed (normalized)",
          any(r["verb"] == "collaborates_with_loosely" for r in parsed["relations"]),
          parsed["relations"])
    check("bio wikilink captured as mention",
          any(m["target"] == "Ramesh Kumar" for m in parsed["mentions"]), parsed["mentions"])


def test_parser_verb_normalization():
    print("\n[3] parse_entity_page() — verb normalization to lower_snake")
    text = "# X\nbio\n## Relations\n- Works At:: [[Y]]\n"
    parsed = graph.parse_entity_page(text)
    check("'Works At' -> 'works_at'", parsed["relations"][0]["verb"] == "works_at",
          parsed["relations"])


# ── 4. indexer — ghost nodes, promotion, unknown verbs indexed ──

def test_indexer_ghost_node_created_and_promoted():
    print("\n[4] index_page() — ghost node created then promoted, order-independent")
    tmp, old, db = _iso()
    try:
        ramesh_path = _write_page("People/ramesh-kumar.md", RAMESH_PAGE)
        graph.index_page(db, ramesh_path, RAMESH_PAGE)

        ghost = db.graph_node_get_by_title("Passport Office")
        check("target with no page becomes a ghost",
              ghost is not None and ghost["is_ghost"] == 1 and ghost["path"] is None, ghost)

        office_page = (
            "# Passport Office\n"
            "The regional passport office.\n"
            "- type:: place\n"
            "## Relations\n"
            "- located_in:: [[Delhi]]\n"
        )
        office_path = _write_page("Places/passport-office.md", office_page)
        graph.index_page(db, office_path, office_page)

        promoted = db.graph_node_get_by_title("Passport Office")
        check("ghost promoted on real page arrival (same node id)",
              promoted is not None and promoted["id"] == ghost["id"]
              and promoted["is_ghost"] == 0 and promoted["path"] == office_path, promoted)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_indexer_promotion_order_independent():
    print("\n[5] index_page() — indexing the real page FIRST, then the referrer, "
          "converges to the same graph as the reverse order")
    tmp, old, db = _iso()
    try:
        office_page = "# Passport Office\nThe regional passport office.\n- type:: place\n"
        office_path = _write_page("Places/passport-office.md", office_page)
        graph.index_page(db, office_path, office_page)

        ramesh_path = _write_page("People/ramesh-kumar.md", RAMESH_PAGE)
        graph.index_page(db, ramesh_path, RAMESH_PAGE)

        node = db.graph_node_get_by_title("Passport Office")
        check("only one 'Passport Office' node exists",
              node is not None and node["is_ghost"] == 0, node)
        nodes_with_title = [n for n in db.graph_nodes_all() if n["title"] == "Passport Office"]
        check("no duplicate node created", len(nodes_with_title) == 1, nodes_with_title)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 6. rebuild-from-scratch == incremental ──

def test_rebuild_equals_incremental():
    print("\n[6] rebuild() — full rebuild-from-scratch equals the incremental result")
    tmp, old, db = _iso()
    try:
        memory.ensure_tree()
        ramesh_path = _write_page("People/ramesh-kumar.md", RAMESH_PAGE)
        office_page = "# Passport Office\nThe regional passport office.\n- type:: place\n"
        office_path = _write_page("Places/passport-office.md", office_page)

        graph.reindex_graph(db, memory.MEMORY_DIR)
        incremental_nodes = sorted(
            (n["title"], n["type"], n["is_ghost"]) for n in db.graph_nodes_all()
        )
        incremental_edges = sorted(
            (e["src"], e["rel"], e["dst"], e["valid_from"], e["valid_to"])
            for e in db.graph_edges_all(history=True)
        )

        graph.rebuild(db, memory.MEMORY_DIR)
        rebuilt_nodes = sorted(
            (n["title"], n["type"], n["is_ghost"]) for n in db.graph_nodes_all()
        )
        rebuilt_edges = sorted(
            (e["src"], e["rel"], e["dst"], e["valid_from"], e["valid_to"])
            for e in db.graph_edges_all(history=True)
        )
        # node ids may legitimately differ across a clear+rebuild; compare
        # by (title, type, is_ghost) sets and edge shape by (rel, dates)
        # counts rather than raw ids.
        check("same node set after rebuild", incremental_nodes == rebuilt_nodes,
              (incremental_nodes, rebuilt_nodes))
        check("same edge count after rebuild", len(incremental_edges) == len(rebuilt_edges),
              (len(incremental_edges), len(rebuilt_edges)))
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_reindex_graph_removes_deleted_page():
    print("\n[7] reindex_graph() — a deleted page's node is demoted/removed")
    tmp, old, db = _iso()
    try:
        memory.ensure_tree()
        friend_page = "# Friend\nJust a friend.\n- type:: person\n"
        _write_page("People/friend.md", friend_page)
        graph.reindex_graph(db, memory.MEMORY_DIR)
        check("node exists after first index",
              db.graph_node_get_by_title("Friend") is not None)

        os.remove(os.path.join(memory.MEMORY_DIR, "Wiki", "People", "friend.md"))
        graph.reindex_graph(db, memory.MEMORY_DIR)
        check("node removed after page deletion (no incoming edges)",
              db.graph_node_get_by_title("Friend") is None)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_reindex_graph_demotes_referenced_deleted_page():
    print("\n[8] reindex_graph() — a deleted but still-referenced page is demoted to a ghost")
    tmp, old, db = _iso()
    try:
        memory.ensure_tree()
        ramesh_path = _write_page("People/ramesh-kumar.md", RAMESH_PAGE)
        office_page = "# Passport Office\nThe regional passport office.\n- type:: place\n"
        _write_page("Places/passport-office.md", office_page)
        graph.reindex_graph(db, memory.MEMORY_DIR)
        office = db.graph_node_get_by_title("Passport Office")
        check("office indexed as real node", office is not None and office["is_ghost"] == 0)

        os.remove(os.path.join(memory.MEMORY_DIR, "Wiki", "Places", "passport-office.md"))
        graph.reindex_graph(db, memory.MEMORY_DIR)
        office_after = db.graph_node_get_by_title("Passport Office")
        check("still referenced by Ramesh -> demoted to ghost, not deleted",
              office_after is not None and office_after["is_ghost"] == 1
              and office_after["id"] == office["id"], office_after)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 9. traversal — neighbors (2-hop), path, cycle safety, find ──

def _build_traversal_graph(db):
    """A(person)-[works_at]->B(org)-[located_in]->C(place); D(person)-[knows]->A;
    plus a cycle A-[knows]->D->[knows]->A already closes one via the two
    edges above once both directions are added (undirected BFS)."""
    a = db.graph_node_insert(path="a", type="person", title="A", bio="", is_ghost=False)
    b = db.graph_node_insert(path="b", type="org", title="B", bio="", is_ghost=False)
    c = db.graph_node_insert(path="c", type="place", title="C", bio="", is_ghost=False)
    d = db.graph_node_insert(path="d", type="person", title="D", bio="", is_ghost=False)
    db.graph_edges_replace_for_path("a", [
        {"src": a, "rel": "works_at", "dst": b, "provenance": "a:1"},
    ])
    db.graph_edges_replace_for_path("b", [
        {"src": b, "rel": "located_in", "dst": c, "provenance": "b:1"},
    ])
    db.graph_edges_replace_for_path("d", [
        {"src": d, "rel": "knows", "dst": a, "provenance": "d:1"},
    ])
    return a, b, c, d


def test_neighbors_two_hop_and_cycle_safe():
    print("\n[9] neighbors() — 2-hop traversal, cycle-safe on a re-added edge")
    tmp, old, db = _iso()
    try:
        a, b, c, d = _build_traversal_graph(db)
        # add a redundant edge creating a genuine cycle: B knows D directly
        db.graph_edges_replace_for_path("b2", [
            {"src": b, "rel": "knows", "dst": d, "provenance": "b2:1"},
        ])
        result = graph.neighbors(db, "A", hops=2)
        check("resolves start node", result is not None and result["node"]["title"] == "A")
        titles_by_hop = {(h["node"]["title"], h["hop"]) for h in result["hits"]}
        check("B at hop 1", ("B", 1) in titles_by_hop, titles_by_hop)
        check("D at hop 1 (via knows)", ("D", 1) in titles_by_hop, titles_by_hop)
        check("C at hop 2 (via B)", ("C", 2) in titles_by_hop, titles_by_hop)
        check("no duplicate hits despite the B<->D<->A cycle",
              len(result["hits"]) == len({h["node"]["id"] for h in result["hits"]}),
              result["hits"])
        result1 = graph.neighbors(db, "A", hops=1)
        check("hops=1 excludes C", all(h["node"]["title"] != "C" for h in result1["hits"]),
              result1["hits"])
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_find_path():
    print("\n[10] find_path() — shortest path between two nodes")
    tmp, old, db = _iso()
    try:
        a, b, c, d = _build_traversal_graph(db)
        chain = graph.find_path(db, "A", "C")
        check("path found A -> B -> C", chain is not None and
              [step["node"]["title"] for step in chain] == ["A", "B", "C"], chain)
        no_path = graph.find_path(db, "A", "nonexistent-node-xyz")
        check("unresolvable target -> None", no_path is None)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_find_nodes_by_type_and_near():
    print("\n[11] find_nodes() — filter by type, optionally scoped to a neighborhood")
    tmp, old, db = _iso()
    try:
        a, b, c, d = _build_traversal_graph(db)
        persons = graph.find_nodes(db, "person")
        check("finds both persons", {n["title"] for n in persons} == {"A", "D"}, persons)
        near_a_orgs = graph.find_nodes(db, "org", near="A", hops=1)
        check("org near A", [n["title"] for n in near_a_orgs] == ["B"], near_a_orgs)
        check("unresolvable near-name -> []",
              graph.find_nodes(db, "person", near="nope") == [])
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_alias_multi_match():
    print("\n[12] graph_alias_lookup() — resolves via any of several aliases")
    tmp, old, db = _iso()
    try:
        node_id = db.graph_node_insert(path="p", type="person", title="Ramesh Kumar",
                                        bio="", is_ghost=False)
        db.graph_aliases_set(node_id, ["Ramesh", "my cousin", "passport guy"])
        check("resolves via primary alias", db.graph_alias_lookup("Ramesh") == node_id)
        check("resolves via multi-word alias", db.graph_alias_lookup("my cousin") == node_id)
        check("resolves via title itself, case-insensitive",
              db.graph_alias_lookup("ramesh kumar") == node_id)
        check("unresolvable alias -> None", db.graph_alias_lookup("nobody") is None)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    tests = [
        test_parser_golden,
        test_parser_unknown_verb_and_mentions,
        test_parser_verb_normalization,
        test_indexer_ghost_node_created_and_promoted,
        test_indexer_promotion_order_independent,
        test_rebuild_equals_incremental,
        test_reindex_graph_removes_deleted_page,
        test_reindex_graph_demotes_referenced_deleted_page,
        test_neighbors_two_hop_and_cycle_safe,
        test_find_path,
        test_find_nodes_by_type_and_near,
        test_alias_multi_match,
    ]
    for t in tests:
        t()

    print(f"\n{_passed} passed, {_failed} failed")
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
