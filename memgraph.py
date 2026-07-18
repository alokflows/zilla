#!/usr/bin/env python3
"""memgraph.py — relational graph memory over Memory/Wiki/**.md (Phase K1).

Usage:
  python memgraph.py neighbors <name> [--hops N] [--history]
  python memgraph.py path <a> <b> [--history]
  python memgraph.py find <type> [--near <name>] [--hops N] [--history]

Prints a plain-text answer, exits 0 with a "not found"-style message when
there's nothing to report (never a stack trace). This is what harness.py's
memory block tells the owner-turn agent to run to answer "whom do I know
at/for X" or "how are these connected" — see PLAN.md §6.K2.
"""

from __future__ import annotations

import argparse
import sys

from zilla.config import DB_FILE
from zilla import graph, store as _store


def _fmt_dates(valid_from, valid_to) -> str:
    if valid_to:
        return f" ({valid_from or '?'} .. {valid_to}, superseded)"
    if valid_from:
        return f" (since {valid_from})"
    return ""


def _fmt_node(node: dict) -> str:
    label = node["title"] or "(untitled)"
    if node["is_ghost"]:
        return f"{label} [ghost — no page yet]"
    return label


def cmd_neighbors(db, args) -> int:
    result = graph.neighbors(db, args.name, hops=args.hops, history=args.history)
    if result is None:
        print(f"no entity found matching '{args.name}'")
        return 0
    start = result["node"]
    print(_fmt_node(start))
    if start["bio"]:
        print(f"  {start['bio']}")
    if not result["hits"]:
        print("  (no known relations)")
        return 0
    for hit in result["hits"]:
        arrow = "->" if hit["direction"] == "out" else "<-"
        dates = _fmt_dates(hit["valid_from"], hit["valid_to"])
        print(f"  [{hit['hop']}] {arrow} {hit['rel']} {arrow} {_fmt_node(hit['node'])}{dates}")
    return 0


def cmd_path(db, args) -> int:
    chain = graph.find_path(db, args.a, args.b, history=args.history)
    if chain is None:
        print(f"no path found between '{args.a}' and '{args.b}'")
        return 0
    parts = [_fmt_node(chain[0]["node"])]
    for step in chain[1:]:
        arrow = "->" if step["direction"] == "out" else "<-"
        parts.append(f"{arrow}[{step['rel']}]{arrow}")
        parts.append(_fmt_node(step["node"]))
    print(" ".join(parts))
    return 0


def cmd_find(db, args) -> int:
    nodes = graph.find_nodes(db, args.type, near=args.near, hops=args.hops, history=args.history)
    if not nodes:
        print("no results")
        return 0
    for node in nodes:
        print(_fmt_node(node))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="memgraph.py", add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)

    p_neighbors = sub.add_parser("neighbors")
    p_neighbors.add_argument("name")
    p_neighbors.add_argument("--hops", type=int, default=2)
    p_neighbors.add_argument("--history", action="store_true")

    p_path = sub.add_parser("path")
    p_path.add_argument("a")
    p_path.add_argument("b")
    p_path.add_argument("--history", action="store_true")

    p_find = sub.add_parser("find")
    p_find.add_argument("type")
    p_find.add_argument("--near", default=None)
    p_find.add_argument("--hops", type=int, default=2)
    p_find.add_argument("--history", action="store_true")

    try:
        args = parser.parse_args(argv[1:])
    except SystemExit:
        return 1

    db = _store.get_store(DB_FILE)
    if args.command == "neighbors":
        return cmd_neighbors(db, args)
    if args.command == "path":
        return cmd_path(db, args)
    if args.command == "find":
        return cmd_find(db, args)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
