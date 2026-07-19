"""Graph screen — local-graph explorer (PLAN.md §6.K4, TUI half): an
adjacency tree rooted at every known entity, lazily expandable via arrow
keys (each expansion fetches that node's own 1-hop neighbors), Enter
opens the selected node's Wiki page in the body pane. The full
force-directed visual is `/graph`'s HTML export (K4's other half) — a
terminal can't honestly do force layouts, so this screen stays a tree,
per PLAN.md's own text ("the full visual stays HTML")."""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Input, Static, Tree

from zilla import graph as _graph
from zilla import memory
from zilla.config import DB_FILE
from zilla.store import get_store

_ROOT_CAP = 200  # keep the initial tree paint fast even on a large graph


def _node_label(node: dict) -> str:
    label = node["title"] or "(untitled)"
    if node["is_ghost"]:
        label += " [ghost]"
    elif node.get("type"):
        label += f" ({node['type']})"
    return label


def _edge_label(hit: dict) -> str:
    arrow = "->" if hit["direction"] == "out" else "<-"
    dates = _graph.format_dates(hit["valid_from"], hit["valid_to"])
    return f"{arrow} {hit['rel']} {arrow} {_node_label(hit['node'])}{dates}"


class GraphScreen(Screen):

    def compose(self) -> ComposeResult:
        yield Static("Graph", classes="screen-title")
        yield Input(placeholder="Jump to a node by name…", id="graph-jump")
        with Horizontal(id="graph-body"):
            yield Tree("Graph", id="graph-tree")
            yield VerticalScroll(Static("", id="graph-page", markup=False), id="graph-page-wrap")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#graph-tree", Tree).show_root = False
        self.refresh_graph()

    def on_screen_resume(self) -> None:
        self.refresh_graph()

    def _db(self):
        try:
            return get_store(DB_FILE)
        except Exception:
            return None

    def refresh_graph(self) -> None:
        tree = self.query_one("#graph-tree", Tree)
        tree.clear()
        db = self._db()
        if db is None:
            tree.root.add_leaf("Zilla's core did not start.")
            return
        nodes = _graph.find_nodes(db)
        if not nodes:
            tree.root.add_leaf(
                "No graph data yet — mention people, places, or projects in "
                "chat and Zilla will start building this.")
            return
        for node in nodes[:_ROOT_CAP]:
            child = tree.root.add(_node_label(node), data={"node": node})
            child.allow_expand = True

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        data = node.data or {}
        if "node" not in data or node.children:
            return  # not a graph node, or already lazily populated
        db = self._db()
        if db is None:
            return
        result = _graph.neighbors(db, data["node"]["title"], hops=1) if data["node"]["title"] else None
        if not result or not result["hits"]:
            node.add_leaf("(no known relations)")
            return
        for hit in result["hits"]:
            leaf = node.add(_edge_label(hit), data={"node": hit["node"]})
            leaf.allow_expand = True

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data or {}
        node = data.get("node")
        if node:
            self._open_page(node)

    def _open_page(self, node: dict) -> None:
        body = self.query_one("#graph-page", Static)
        if node["is_ghost"]:
            body.update(f"# {node['title'] or '(untitled)'}\n\n[ghost — no page yet]")
            return
        path = node.get("path")
        if not path:
            body.update(f"# {node['title'] or '(untitled)'}\n\n{node.get('bio') or '(no page)'}")
            return
        full = os.path.join(memory.MEMORY_DIR, "Wiki", path)
        try:
            with open(full, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = node.get("bio") or "(page unreadable)"
        body.update(text)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "graph-jump":
            return
        name = event.value.strip()
        if not name:
            return
        db = self._db()
        if db is None:
            return
        node = _graph.resolve_name(db, name)
        if node is None:
            self.notify(f"No entity found matching '{name}'", severity="warning")
            return
        tree = self.query_one("#graph-tree", Tree)
        tree.clear()
        child = tree.root.add(_node_label(node), data={"node": node})
        child.allow_expand = True
        tree.select_node(child)
        child.expand()
        self._open_page(node)
        event.input.value = ""
