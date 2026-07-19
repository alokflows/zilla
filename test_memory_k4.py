# ============================================================
#  TESTS — Phase K4: graph views (PLAN.md §6.K4 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/graph_html.py: _build_snapshot() shape (degree from CURRENT
#      edges only, ghost flag, aliases, superseded edges kept but excluded
#      from degree), render_graph_html() self-containment (no CDN/network
#      refs, valid embedded JSON, offline-openable), focus resolution
#      (alias resolves -> local view; unresolvable name -> silent fallback
#      to global view, never an error), golden_snapshot()/
#      render_from_snapshot() at 2000 nodes (the "~60fps for <=2k nodes"
#      accept criterion's data-validity half — the physics/interaction
#      half isn't testable headlessly, see the K4 live-smoke step).
#    - bot.py: cmd_graph owner-gate and file-generation + safe_send_file
#      wiring (the sending itself is monkeypatched — safe_send_file's own
#      allowlist logic isn't K4's to re-test).
#    - zilla/tui/screens/graph.py: GraphScreen tree lazy-expand and
#      enter-opens-page, beyond the screen-switch smoke already in
#      test_tui.py's test_screens_switch().
#
#  Run:  python test_memory_k4.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every test points zilla.memory.MEMORY_DIR at a throwaway tmpdir and
#  zilla.config.DB_FILE at a throwaway sqlite file (same pattern
#  test_memory_k1.py/test_memory_k2.py/test_memory_k3.py use) so a run
#  never reads or writes the real repo Memory/ tree or zilla.db.
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
_tmpdir = tempfile.mkdtemp(prefix="zilla_k4_cfg_")
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
from zilla import graph_html  # noqa: E402
from zilla import store as _store  # noqa: E402

OWNER = 111
NON_OWNER = 999


def _iso():
    """A fresh throwaway Memory/ dir + a fresh graph in the shared test db."""
    tmp = tempfile.mkdtemp(prefix="zilla_k4_mem_")
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


def _extract_payload(html_text: str) -> dict:
    start = html_text.index('<script id="graph-data" type="application/json">') + \
        len('<script id="graph-data" type="application/json">')
    end = html_text.index("</script>", start)
    return json.loads(html_text[start:end])


# ── 1. _build_snapshot() — degree/ghost/superseded shape ──

def test_build_snapshot_shape():
    print("\n[1] _build_snapshot() — degree from current edges only, ghost flag, superseded kept")
    tmp, old, db = _iso()
    try:
        _write_page(
            "ramesh.md",
            "# Ramesh\nCousin.\n- type:: person\n- aliases:: Ram\n"
            "## Relations\n- knows:: [[Suresh]] (since 2020-01-01)\n"
            "- worked_at:: [[Old Co]] (2018 .. 2019)\n",
        )
        graph.reindex_graph(db, memory.MEMORY_DIR)
        snap = graph_html._build_snapshot(db)

        ramesh = next(n for n in snap["nodes"] if n["title"] == "Ramesh")
        suresh = next(n for n in snap["nodes"] if n["title"] == "Suresh")
        oldco = next(n for n in snap["nodes"] if n["title"] == "Old Co")

        check("ramesh aliases captured", ramesh["aliases"] == ["Ram"], ramesh)
        check("ramesh is not a ghost", ramesh["ghost"] is False, ramesh)
        check("suresh is a ghost (unresolved [[Suresh]])", suresh["ghost"] is True, suresh)

        # degree counts CURRENT edges only: ramesh has 1 current (knows) +
        # 1 superseded (worked_at) -> degree 1, not 2.
        check("ramesh degree reflects only the current edge", ramesh["degree"] == 1, ramesh)
        check("old co (only reachable via the superseded edge) has degree 0", oldco["degree"] == 0, oldco)

        rels = {(e["rel"], e["superseded"]) for e in snap["edges"]}
        check("current 'knows' edge present, not marked superseded",
              ("knows", False) in rels, rels)
        check("superseded 'worked_at' edge present and marked superseded",
              ("worked_at", True) in rels, rels)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 2. render_graph_html() — self-contained, valid, offline-openable ──

def test_render_graph_html_self_contained():
    print("\n[2] render_graph_html() — self-contained (no CDN/network refs), valid embedded JSON")
    tmp, old, db = _iso()
    try:
        _write_page("a.md", "# A\nBio.\n- type:: person\n## Relations\n- knows:: [[B]]\n")
        _write_page("b.md", "# B\nBio.\n- type:: person\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)

        html_text = graph_html.render_graph_html(db)
        check("starts with doctype", html_text.startswith("<!doctype html"), html_text[:40])
        check("no http:// reference", "http://" not in html_text)
        check("no https:// reference", "https://" not in html_text)
        check("no CDN-style external script tag", "<script src=" not in html_text)

        payload = _extract_payload(html_text)
        check("embedded JSON has both nodes", len(payload["nodes"]) == 2, payload["nodes"])
        check("embedded JSON has the one current edge", len(payload["edges"]) == 1, payload["edges"])
        check("no focus by default -> global view", payload["focus"] is None, payload)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 3. render_graph_html() — focus resolves via alias; unresolvable falls back silently ──

def test_render_graph_html_focus():
    print("\n[3] render_graph_html(focus=...) — alias resolves to local view; unresolvable -> silent global fallback")
    tmp, old, db = _iso()
    try:
        _write_page("ramesh.md", "# Ramesh\nCousin.\n- type:: person\n- aliases:: Ram\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)
        node = graph.resolve_name(db, "Ramesh")

        hit = graph_html.render_graph_html(db, focus="Ram")
        payload = _extract_payload(hit)
        check("alias 'Ram' resolves focus to Ramesh's node id", payload["focus"] == node["id"], payload)

        miss = graph_html.render_graph_html(db, focus="Nonexistent Person XYZ")
        payload2 = _extract_payload(miss)
        check("unresolvable focus name -> focus is null (global view), not an error",
              payload2["focus"] is None, payload2)
        check("unresolvable focus still returns a full valid document",
              miss.startswith("<!doctype html"), miss[:40])
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ── 4. golden_snapshot()/render_from_snapshot() — 2000-node validity/perf ──

def test_golden_snapshot_2000_nodes():
    print("\n[4] golden_snapshot(2000) + render_from_snapshot() — valid HTML, correct counts, builds fast")
    snap = graph_html.golden_snapshot(2000, edge_fanout=3)
    check("2000 nodes generated", len(snap["nodes"]) == 2000, len(snap["nodes"]))
    check("6000 edges generated (fanout 3)", len(snap["edges"]) == 6000, len(snap["edges"]))

    t0 = time.monotonic()
    html_text = graph_html.render_from_snapshot(snap)
    elapsed = time.monotonic() - t0
    check("renders well under a second (pure string assembly, no physics server-side)",
          elapsed < 2.0, elapsed)

    payload = _extract_payload(html_text)
    check("embedded JSON round-trips node count", len(payload["nodes"]) == 2000, len(payload["nodes"]))
    check("embedded JSON round-trips edge count", len(payload["edges"]) == 6000, len(payload["edges"]))
    check("no CDN/network reference even at this size", "http://" not in html_text and "https://" not in html_text)


TESTS_PART1 = [
    test_build_snapshot_shape,
    test_render_graph_html_self_contained,
    test_render_graph_html_focus,
    test_golden_snapshot_2000_nodes,
]


def main():
    for t in TESTS_PART1:
        t()

    # ── 5. bot.cmd_graph — owner-gate + file generation + safe_send_file wiring ──
    _run_cmd_graph_tests()

    # ── 6. GraphScreen — lazy-expand + enter-opens-page ──
    _run_tui_graph_tests()

    print(f"\n{_passed} passed, {_failed} failed")
    shutil.rmtree(_tmpdir, ignore_errors=True)
    return 0 if _failed == 0 else 1


# ============================================================
#  5. bot.cmd_graph
# ============================================================

class _FakeMessage:
    def __init__(self):
        self.sent: list[str] = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)


class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class _FakeUpdate:
    def __init__(self, uid, chat_id=None):
        self.effective_user = _FakeUser(uid)
        self.effective_chat = _FakeChat(chat_id if chat_id is not None else uid)
        self.message = _FakeMessage()


class _FakeContext:
    def __init__(self, args=None):
        self.args = args or []
        self.bot = object()  # cmd_graph passes this straight through to the (monkeypatched) safe_send_file


def _run_cmd_graph_tests():
    print("\n[5] bot.cmd_graph — owner-only, writes a self-contained HTML file, calls safe_send_file")
    tmp, old, db = _iso()
    outbox = tempfile.mkdtemp(prefix="zilla_k4_outbox_")
    try:
        _write_page("ramesh.md", "# Ramesh\nCousin.\n- type:: person\n- aliases:: Ram\n")
        graph.reindex_graph(db, memory.MEMORY_DIR)

        # bot.py is heavy to import for real — exercise the already-imported
        # module's cmd_graph against a fake auth/OUTBOX/safe_send_file
        # (same technique test_memory_m4.py uses for cmd_memory).
        import bot as _bot

        class _FakeAuth:
            def is_owner(self, uid):
                return uid == OWNER

        sent_calls = []

        async def _fake_safe_send_file(bot_obj, chat_id, filepath, caption=None, conv_id=None, user_id=None):
            sent_calls.append({"chat_id": chat_id, "filepath": filepath, "caption": caption, "user_id": user_id})
            return True

        old_auth, old_outbox, old_send = _bot.auth, _bot.OUTBOX_DOCUMENTS, _bot.safe_send_file
        _bot.auth = _FakeAuth()
        _bot.OUTBOX_DOCUMENTS = outbox
        _bot.safe_send_file = _fake_safe_send_file
        try:
            # Non-owner is refused, no file generated.
            u = _FakeUpdate(NON_OWNER)
            asyncio.run(_bot.cmd_graph(u, _FakeContext()))
            check("non-owner gets refused", u.message.sent == ["Owner only."], u.message.sent)
            check("no file generated for a non-owner", sent_calls == [], sent_calls)

            # Owner, no focus -> global-view file generated and sent.
            u2 = _FakeUpdate(OWNER)
            asyncio.run(_bot.cmd_graph(u2, _FakeContext()))
            check("owner call triggers exactly one safe_send_file", len(sent_calls) == 1, sent_calls)
            call = sent_calls[-1]
            check("sent file lives under OUTBOX_DOCUMENTS", call["filepath"].startswith(outbox), call)
            check("sent file exists on disk", os.path.exists(call["filepath"]), call)
            with open(call["filepath"], "r", encoding="utf-8") as f:
                text = f.read()
            check("generated file is a full self-contained HTML doc", text.startswith("<!doctype html"), text[:40])
            check("caption has no focus suffix for a plain /graph", call["caption"] == "🕸️ Graph", call)

            # Owner, with a resolvable focus name -> caption reflects it.
            u3 = _FakeUpdate(OWNER)
            asyncio.run(_bot.cmd_graph(u3, _FakeContext(["Ramesh"])))
            check("second owner call triggers a second safe_send_file", len(sent_calls) == 2, sent_calls)
            check("focused caption mentions the requested name",
                  sent_calls[-1]["caption"] == "🕸️ Graph — local view: Ramesh", sent_calls[-1])
        finally:
            _bot.auth, _bot.OUTBOX_DOCUMENTS, _bot.safe_send_file = old_auth, old_outbox, old_send
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(outbox, ignore_errors=True)


# ============================================================
#  6. GraphScreen — lazy-expand + enter-opens-page
# ============================================================

def _run_tui_graph_tests():
    print("\n[6] GraphScreen — tree lazy-expand fetches 1-hop neighbors, Enter opens the Wiki page")
    tmp, old, db = _iso()
    try:
        _write_page(
            "ramesh.md",
            "# Ramesh\nCousin who lives nearby.\n- type:: person\n"
            "## Relations\n- knows:: [[Suresh]] (since 2021)\n",
        )
        graph.reindex_graph(db, memory.MEMORY_DIR)

        import zilla.tui.app as tui_app
        from zilla.tui.screens.graph import GraphScreen

        # GraphScreen's module-level `from zilla.config import DB_FILE` was
        # bound whenever zilla.tui.screens.graph first imported (test_tui.py
        # already isolates config.DB_FILE before that happens in a normal
        # test run) — but this file imports it fresh here, so pin its
        # module-level DB_FILE explicitly to this test's throwaway db too,
        # in case this module is ever imported before config.DB_FILE is set.
        import zilla.tui.screens.graph as graph_screen_mod
        graph_screen_mod.DB_FILE = config.DB_FILE

        from zilla.core import ZillaCore
        from zilla.sessions import SessionManager
        from zilla.users import AuthManager

        sessions = SessionManager(os.path.join(tmp, "sessions.json"))
        auth_mgr = AuthManager(os.path.join(tmp, "users.json"), OWNER)
        core = ZillaCore(sessions=sessions, auth=auth_mgr)
        app = tui_app.ZillaApp(core=core, user_id=OWNER, startup_hint=None, use_real_core=False)

        async def run():
            from textual.widgets import Tree, Static
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.action_goto("graph")
                await pilot.pause()
                screen = app.get_screen("graph")
                tree = screen.query_one("#graph-tree", Tree)
                root_children = list(tree.root.children)
                ramesh_node = next(c for c in root_children if "Ramesh" in str(c.label))

                # Expand -> lazy-loads 1-hop neighbors (Suresh via "knows").
                ramesh_node.expand()
                await pilot.pause()
                child_labels = [str(c.label) for c in ramesh_node.children]

                # Select -> opens the Wiki page in the body pane.
                tree.select_node(ramesh_node)
                await pilot.pause()
                page = screen.query_one("#graph-page", Static)
                page_text = None
                for attr in ("renderable", "_Static__content"):
                    if hasattr(page, attr):
                        page_text = str(getattr(page, attr))
                        break
                return child_labels, page_text

        child_labels, page_text = asyncio.run(run())
        check("lazy-expand populated exactly one neighbor (Suresh via knows)",
              len(child_labels) == 1, child_labels)
        check("the neighbor edge label names the relation and target",
              child_labels and "knows" in child_labels[0] and "Suresh" in child_labels[0], child_labels)
        check("selecting Ramesh opens its Wiki page content in the body pane",
              page_text is not None and "Cousin who lives nearby" in page_text, page_text)
    finally:
        memory.MEMORY_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
