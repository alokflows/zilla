# ============================================================
#  GRAPH — relational memory over Memory/Wiki/**.md (PLAN.md §6, Phase K1)
# ============================================================
#  Entities are wiki pages; typed relations are lines inside those pages;
#  this module derives a queryable graph in zilla.db (nodes/aliases/edges
#  — see store.py's schema). The graph is disposable and rebuildable —
#  the Markdown pages are the truth, never the reverse.
#
#  Entity page grammar (exact, PLAN.md §6):
#    line 1  = "# Title"                         -> node title
#    line 2  = bio line (free text)               -> node bio
#    lines before "## Relations" matching
#      "- key:: value"                            -> attributes
#        key == "type"    -> node type
#        key == "aliases" -> comma-separated alias list
#        anything else    -> not stored (page is the truth), but any
#                             [[Wiki-link]] inside the value still counts
#                             as an untyped "mentions" edge
#    "## Relations" heading marks the start of the relation block
#    lines after it matching "- verb:: [[Target]] (dates?)"
#        -> a typed edge; verb normalized to lower_snake; unknown verbs
#           are indexed, never rejected
#        (since YYYY[-MM[-DD]])   -> valid_from=that string, valid_to=None
#        (A .. B)                 -> closed interval: valid_from=A,
#                                     valid_to=B (a superseded fact)
#        no parens                -> both None (currently true, no known
#                                     start)
#    [[Wiki-links]] anywhere else in prose (bio, attribute values, stray
#    lines) also produce untyped "mentions" edges — Obsidian semantics.
#    A [[Target]] with no page yet becomes a ghost node (is_ghost=1,
#    path=NULL) — resolved/promoted the moment its own page appears,
#    order-independent (see resolve_or_create_ghost / index_page below).
# ============================================================

from __future__ import annotations

import os
import re
from collections import deque

WIKI_DIRNAME = "Wiki"

_HEADING_RELATIONS = re.compile(r"^##\s*Relations\s*$", re.IGNORECASE)
_ATTR_LINE = re.compile(r"^-\s*([A-Za-z_][A-Za-z0-9_ -]*?)::\s*(.*)$")
_REL_LINE = re.compile(
    r"^-\s*([A-Za-z_][A-Za-z0-9_ -]*?)::\s*\[\[([^\]]+)\]\]\s*(\(.*\))?\s*$"
)
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_SINCE = re.compile(r"^\(since\s+(.+?)\)$", re.IGNORECASE)
_INTERVAL = re.compile(r"^\((.+?)\s*\.\.\s*(.+?)\)$")


def _normalize_verb(verb: str) -> str:
    return re.sub(r"[\s\-]+", "_", verb.strip().lower())


def _parse_dates(paren: str | None) -> tuple[str | None, str | None]:
    """'(since 2024-01)' -> (from, None); '(2020 .. 2023-06)' -> (from, to);
    anything else (missing, unrecognized) -> (None, None) — never raises,
    a malformed date parenthetical just means "no known interval", never
    an indexing failure."""
    if not paren:
        return (None, None)
    s = paren.strip()
    m = _SINCE.match(s)
    if m:
        return (m.group(1).strip(), None)
    m = _INTERVAL.match(s)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return (None, None)


def parse_entity_page(text: str) -> dict:
    """Pure parser: entity page text -> {title, bio, type, aliases, attrs,
    relations: [{verb, target, valid_from, valid_to, line}], mentions:
    [{target, line}]}. `attrs` is every "- key:: value" line seen before
    "## Relations" (lowercased key -> raw value, including type/aliases
    themselves) — K3's gap detection needs to know which plain attributes
    (e.g. "contact::") a page does or doesn't carry; nothing else in the
    parse result records that. Never raises on unknown verbs or malformed
    dates — the indexer must never fail on owner/agent-authored Markdown."""
    lines = text.splitlines()
    title = lines[0].lstrip("#").strip() if lines and lines[0].strip() else ""
    bio = ""
    type_: str | None = None
    aliases: list[str] = []
    attrs: dict[str, str] = {}
    relations: list[dict] = []
    mentions: list[dict] = []
    in_relations = False

    if len(lines) > 1:
        bio = lines[1].strip()
        for m in _WIKILINK.finditer(bio):
            mentions.append({"target": m.group(1).strip(), "line": 2})

    for idx in range(2, len(lines)):
        stripped = lines[idx].strip()
        lineno = idx + 1
        if not stripped:
            continue
        if _HEADING_RELATIONS.match(stripped):
            in_relations = True
            continue
        if in_relations:
            m = _REL_LINE.match(stripped)
            if m:
                verb_raw, target, paren = m.groups()
                valid_from, valid_to = _parse_dates(paren)
                relations.append({
                    "verb": _normalize_verb(verb_raw),
                    "target": target.strip(),
                    "valid_from": valid_from, "valid_to": valid_to,
                    "line": lineno,
                })
                continue
            for m2 in _WIKILINK.finditer(stripped):
                mentions.append({"target": m2.group(1).strip(), "line": lineno})
            continue
        m = _ATTR_LINE.match(stripped)
        if m:
            key, value = m.groups()
            key_l = key.strip().lower()
            value = value.strip()
            attrs[key_l] = value
            if key_l == "type":
                type_ = value.lower()
            elif key_l == "aliases":
                aliases = [a.strip() for a in value.split(",") if a.strip()]
            else:
                for m2 in _WIKILINK.finditer(value):
                    mentions.append({"target": m2.group(1).strip(), "line": lineno})
            continue
        for m2 in _WIKILINK.finditer(stripped):
            mentions.append({"target": m2.group(1).strip(), "line": lineno})

    return {
        "title": title, "bio": bio, "type": type_, "aliases": aliases,
        "attrs": attrs, "relations": relations, "mentions": mentions,
    }


# ══════════════════════════════════════════════════════════
#  CURIOSITY GAP DETECTION — zero-AI, deterministic (PLAN.md §6.K3)
# ══════════════════════════════════════════════════════════
#  Two families: structural gaps on a REAL page's own declared type/attrs/
#  relations (checked every time that page is (re)indexed, in index_page),
#  and ghost-node gaps that only make sense graph-wide (checked once per
#  full reindex_graph pass, after every page has landed). Both funnel into
#  the same curiosity table via Store.curiosity_sync_node — the harness
#  (K3 step 2) never does detection itself, only reads what's already there.

GAP_NO_CONTACT = "no_contact"
GAP_NO_LOCATED_IN = "no_located_in"
GAP_GHOST_MULTI_REF = "ghost_multi_ref"

_GHOST_MULTI_REF_THRESHOLD = 2


def _structural_gaps(type_: str | None, attrs: dict, relations: list[dict]) -> list[str]:
    """Gaps derivable from one page's own parsed content: a person with no
    `contact::` attribute; an org/place with no `located_in::` relation."""
    t = (type_ or "").lower()
    gaps: list[str] = []
    if t == "person" and not attrs.get("contact"):
        gaps.append(GAP_NO_CONTACT)
    if t in ("org", "place") and not any(r["verb"] == "located_in" for r in relations):
        gaps.append(GAP_NO_LOCATED_IN)
    return gaps


def _sync_ghost_gaps(db) -> None:
    """Ghost nodes referenced from >= 2 distinct pages get GAP_GHOST_MULTI_REF;
    everything else (including ghosts with 0-1 references, and any node that
    was promoted/demoted since the last pass) gets that gap cleared. Must run
    AFTER the full page walk so promotions/demotions have already landed."""
    ghosts = [n for n in db.graph_nodes_all() if n["is_ghost"]]
    if not ghosts:
        return
    referrers: dict[int, set[int]] = {}
    for e in db.graph_edges_all(history=True):
        referrers.setdefault(e["dst"], set()).add(e["src"])
    for node in ghosts:
        refs = referrers.get(node["id"], set())
        gaps = [GAP_GHOST_MULTI_REF] if len(refs) >= _GHOST_MULTI_REF_THRESHOLD else []
        db.curiosity_sync_node(node["id"], gaps)


# ══════════════════════════════════════════════════════════
#  INDEXING — wired into memory.reindex()'s mtime-diff cycle
# ══════════════════════════════════════════════════════════

def _resolve_or_create_ghost(db, name: str) -> int:
    """Resolve a [[Target]] name to a node id — alias/title match first,
    else reuse an existing ghost with that title, else create a fresh
    ghost. Order-independent: whichever of two pages that reference each
    other is indexed first, the end state is identical."""
    node_id = db.graph_alias_lookup(name)
    if node_id is not None:
        return node_id
    return db.graph_node_insert(path=None, type=None, title=name, bio=None, is_ghost=True)


def index_page(db, path: str, text: str) -> int:
    """Parse one Wiki page and (re)index it: upsert/promote its own node,
    replace its alias set, and replace every edge it contributes
    (provenance-scoped, so this never touches another page's edges).
    Returns the node id. Idempotent and order-independent."""
    parsed = parse_entity_page(text)
    title = parsed["title"] or path

    existing = db.graph_node_get_by_path(path)
    if existing is not None:
        node_id = existing["id"]
        db.graph_node_update(node_id, type=parsed["type"], title=title,
                              bio=parsed["bio"], is_ghost=False)
    else:
        ghost = db.graph_node_get_by_title(title)
        if ghost is not None and ghost["is_ghost"] and ghost["path"] is None:
            node_id = ghost["id"]
            db.graph_node_promote(node_id, path=path, type=parsed["type"],
                                   title=title, bio=parsed["bio"])
        else:
            node_id = db.graph_node_insert(path=path, type=parsed["type"], title=title,
                                            bio=parsed["bio"], is_ghost=False)

    db.graph_aliases_set(node_id, parsed["aliases"])

    edges = []
    for rel in parsed["relations"]:
        target_id = _resolve_or_create_ghost(db, rel["target"])
        edges.append({
            "src": node_id, "rel": rel["verb"], "dst": target_id,
            "valid_from": rel["valid_from"], "valid_to": rel["valid_to"],
            "provenance": f"{path}:{rel['line']}",
        })
    for mention in parsed["mentions"]:
        target_id = _resolve_or_create_ghost(db, mention["target"])
        edges.append({
            "src": node_id, "rel": "mentions", "dst": target_id,
            "valid_from": None, "valid_to": None,
            "provenance": f"{path}:{mention['line']}",
        })
    db.graph_edges_replace_for_path(path, edges)
    db.curiosity_sync_node(node_id, _structural_gaps(parsed["type"], parsed["attrs"],
                                                       parsed["relations"]))
    return node_id


def remove_page(db, path: str) -> None:
    """A page vanished from disk: drop every edge it contributed; demote
    its node to a ghost if anything still points at it (never orphan
    another page's edges), else delete it outright."""
    node = db.graph_node_get_by_path(path)
    db.graph_edges_replace_for_path(path, [])
    if node is None:
        return
    if db.graph_edges_incoming_count(node["id"]) > 0:
        db.graph_node_demote_to_ghost(node["id"])
    else:
        db.graph_node_delete(node["id"])


def reindex_graph(db, mem_dir: str) -> int:
    """Scan Memory/Wiki/**.md, index every page (index_page is cheap and
    idempotent, so a full rescan is fine — the graph has no separate
    mtime-diff state of its own; it rides on the same files memory.py's
    FTS reindex already walks). Removes nodes for pages no longer on
    disk. Returns the count of pages indexed. Never raises."""
    wiki_dir = os.path.join(mem_dir, WIKI_DIRNAME)
    if not os.path.isdir(wiki_dir):
        return 0
    on_disk: set[str] = set()
    touched = 0
    for dirpath, _dirnames, filenames in os.walk(wiki_dir):
        for name in filenames:
            if not name.endswith(".md"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, wiki_dir).replace(os.sep, "/")
            on_disk.add(rel)
            try:
                with open(full, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue
            index_page(db, rel, text)
            touched += 1
    indexed_paths = {n["path"] for n in db.graph_nodes_all() if n["path"] is not None}
    for stale in indexed_paths - on_disk:
        remove_page(db, stale)
    _sync_ghost_gaps(db)
    return touched


def rebuild(db, mem_dir: str) -> int:
    """Full rebuild-from-scratch: wipe the graph, re-parse every page.
    Must equal reindex_graph()'s incremental end state — index_page's
    title-based ghost promotion is order-independent, so it does."""
    db.graph_clear()
    return reindex_graph(db, mem_dir)


# ══════════════════════════════════════════════════════════
#  TRAVERSAL — neighbors / path / find (memgraph.py's engine)
# ══════════════════════════════════════════════════════════
#  Loaded into Python and walked with plain BFS rather than a raw
#  recursive SQL CTE: at the scale this product targets (thousands of
#  nodes, PLAN.md §6.K4's 2k-node ceiling) one `graph_edges_all()` fetch
#  is trivially cheap, and BFS in Python is far easier to keep provably
#  cycle-safe and correctly deduplicated than a hand-rolled recursive
#  CTE — same query surface (neighbors/path/find), simpler to verify.

def resolve_name(db, name: str) -> dict | None:
    """A bare name (title or alias, case-insensitive) -> its node dict,
    or None if nothing matches. Never creates a ghost — that only
    happens during indexing of real relation/mention targets."""
    node_id = db.graph_alias_lookup(name)
    return db.graph_node_get(node_id) if node_id is not None else None


def _adjacency(db, *, history: bool) -> dict[int, list[dict]]:
    """node_id -> [{other, rel, direction, valid_from, valid_to}, ...],
    both directions of every edge (traversal is undirected; rel + direction
    preserve which way the typed fact actually points)."""
    adj: dict[int, list[dict]] = {}
    for e in db.graph_edges_all(history=history):
        adj.setdefault(e["src"], []).append({
            "other": e["dst"], "rel": e["rel"], "direction": "out",
            "valid_from": e["valid_from"], "valid_to": e["valid_to"],
        })
        adj.setdefault(e["dst"], []).append({
            "other": e["src"], "rel": e["rel"], "direction": "in",
            "valid_from": e["valid_from"], "valid_to": e["valid_to"],
        })
    return adj


def neighbors(db, name: str, *, hops: int = 2, history: bool = False) -> dict | None:
    """BFS out to `hops` from the node resolved from `name`. Returns
    {"node": <dict>, "hits": [{"node": <dict>, "hop": int, "rel": str,
    "direction": "out"|"in", "valid_from", "valid_to"}, ...]} sorted by
    hop then title, closest first. None if `name` resolves to nothing.
    Cycle-safe: a node already visited at a shorter (or equal) hop is
    never re-expanded."""
    start = resolve_name(db, name)
    if start is None:
        return None
    adj = _adjacency(db, history=history)
    visited = {start["id"]: 0}
    order: list[tuple[int, dict]] = []
    q = deque([(start["id"], 0)])
    while q:
        node_id, hop = q.popleft()
        if hop >= hops:
            continue
        for edge in adj.get(node_id, []):
            other = edge["other"]
            if other in visited:
                continue
            visited[other] = hop + 1
            order.append((hop + 1, {**edge, "from": node_id}))
            q.append((other, hop + 1))
    hits = []
    for hop, edge in order:
        node = db.graph_node_get(edge["other"])
        if node is None:
            continue
        hits.append({
            "node": node, "hop": hop, "rel": edge["rel"], "direction": edge["direction"],
            "valid_from": edge["valid_from"], "valid_to": edge["valid_to"],
        })
    hits.sort(key=lambda h: (h["hop"], (h["node"]["title"] or "").lower()))
    return {"node": start, "hits": hits}


def find_path(db, a: str, b: str, *, history: bool = False, max_hops: int = 8) -> list[dict] | None:
    """Shortest path (BFS, cycle-safe) between the nodes resolved from `a`
    and `b`. Returns a list of {"node": <dict>, "rel": str|None,
    "direction": str|None} from a to b inclusive (first hop has rel=None),
    or None if either name doesn't resolve or no path exists within
    max_hops."""
    start = resolve_name(db, a)
    end = resolve_name(db, b)
    if start is None or end is None:
        return None
    if start["id"] == end["id"]:
        return [{"node": start, "rel": None, "direction": None}]
    adj = _adjacency(db, history=history)
    visited = {start["id"]}
    parent: dict[int, tuple[int, str, str]] = {}
    q = deque([(start["id"], 0)])
    while q:
        node_id, hop = q.popleft()
        if hop >= max_hops:
            continue
        done = False
        for edge in adj.get(node_id, []):
            other = edge["other"]
            if other in visited:
                continue
            visited.add(other)
            parent[other] = (node_id, edge["rel"], edge["direction"])
            if other == end["id"]:
                done = True
                break
            q.append((other, hop + 1))
        if done:
            break
    if end["id"] not in parent:
        return None
    chain = [end["id"]]
    cur = end["id"]
    while cur != start["id"]:
        if cur not in parent:
            return None
        cur, _rel, _dir = parent[cur]
        chain.append(cur)
    chain.reverse()
    result = [{"node": start, "rel": None, "direction": None}]
    for node_id in chain[1:]:
        prev_id, rel, direction = parent[node_id]
        result.append({"node": db.graph_node_get(node_id), "rel": rel, "direction": direction})
    return result


def format_dates(valid_from: str | None, valid_to: str | None) -> str:
    """'(since 2024-01)' / '(2020 .. 2023-06, superseded)' / '' — shared by
    memgraph.py's CLI output and K2's turn-time local graph card."""
    if valid_to:
        return f" ({valid_from or '?'} .. {valid_to}, superseded)"
    if valid_from:
        return f" (since {valid_from})"
    return ""


def find_nodes(db, type_: str | None = None, *, near: str | None = None,
                hops: int = 2, history: bool = False) -> list[dict]:
    """Nodes matching `type_` (case-insensitive; None = any type),
    optionally restricted to the `near` node's neighborhood. Sorted by
    title. [] (never None) when nothing matches, including an
    unresolvable `near` name."""
    if near is not None:
        result = neighbors(db, near, hops=hops, history=history)
        if result is None:
            return []
        candidates = [hit["node"] for hit in result["hits"]]
    else:
        candidates = db.graph_nodes_all()
    if type_ is not None:
        candidates = [n for n in candidates if (n["type"] or "").lower() == type_.lower()]
    seen: set[int] = set()
    out = []
    for n in candidates:
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        out.append(n)
    out.sort(key=lambda n: (n["title"] or "").lower())
    return out


# ══════════════════════════════════════════════════════════
#  TURN-TIME LINKING  (harness.py's owner-turn injection, PLAN.md §6.K2)
# ══════════════════════════════════════════════════════════
#  Deterministic — zero AI calls in this path. A name is "known" only via
#  an exact alias or an exact node title (case-insensitive, word-bounded);
#  no fuzzy matching here (that is K3/curiosity territory, not this one).

def alias_scan(db, text: str, *, cap: int = 3) -> list[dict]:
    """Scan `text` (an owner message) for known entity names — every
    alias plus every node's own title is a candidate, longest name first
    so "Ramesh Kumar" wins over "Ramesh" when both would match the same
    span. Case-insensitive, word-bounded (`\\b`), first occurrence per
    candidate. Returns up to `cap` distinct node dicts, ordered strongest
    (longest matched name) first — the order `harness.py` uses to decide
    which single hit gets the deeper (2-hop) card. Never raises; [] on
    empty text or no hits."""
    if not text:
        return []
    seen_names: set[str] = set()
    candidates: list[tuple[str, int]] = []
    for row in db.graph_aliases_all():
        name = (row["alias"] or "").strip()
        key = name.lower()
        if not name or key in seen_names:
            continue
        seen_names.add(key)
        candidates.append((name, row["node_id"]))
    for node in db.graph_nodes_all():
        name = (node["title"] or "").strip()
        key = name.lower()
        if not name or key in seen_names:
            continue
        seen_names.add(key)
        candidates.append((name, node["id"]))
    candidates.sort(key=lambda c: (-len(c[0]), c[0].lower()))

    claimed: list[tuple[int, int]] = []
    hit_ids: list[int] = []
    for name, node_id in candidates:
        if len(hit_ids) >= cap or node_id in hit_ids:
            continue
        try:
            m = re.search(r"\b" + re.escape(name) + r"\b", text, re.IGNORECASE)
        except re.error:
            continue
        if not m:
            continue
        start, end = m.span()
        if any(start < c_end and end > c_start for c_start, c_end in claimed):
            continue
        claimed.append((start, end))
        hit_ids.append(node_id)

    hits = []
    for node_id in hit_ids:
        node = db.graph_node_get(node_id)
        if node is not None:
            hits.append(node)
    return hits


def local_card_lines(db, node: dict, *, hops: int = 1) -> list[str]:
    """Compact "local graph card" lines for one node: title (+ ghost
    marker) + bio line + current edges out to `hops` hops. This is the
    text that lets a nickname mention surface graph knowledge before the
    agent even reasons — never more than a handful of lines per node,
    the caller (harness.py) enforces the overall block's line cap."""
    label = node["title"] or "(untitled)"
    if node["is_ghost"]:
        label += " [ghost — no page yet]"
    lines = [f"- {label}"]
    if node.get("bio"):
        lines.append(f"    {node['bio']}")
    result = neighbors(db, node["title"], hops=hops, history=False) if node["title"] else None
    if result:
        for hit in result["hits"]:
            arrow = "->" if hit["direction"] == "out" else "<-"
            dates = format_dates(hit["valid_from"], hit["valid_to"])
            hit_label = hit["node"]["title"] or "(untitled)"
            lines.append(f"    {arrow} {hit['rel']} {arrow} {hit_label}{dates}")
    return lines


# ══════════════════════════════════════════════════════════
#  TURN-TIME CURIOSITY  (harness.py's owner-turn injection, PLAN.md §6.K3)
# ══════════════════════════════════════════════════════════
#  Enforcement is code, not model judgment: at most ONE gap is ever
#  surfaced per turn, and only for a node this turn's own alias_scan()
#  already activated (the same relevance gate K2 uses for the graph
#  card) — never a cold trawl of the whole curiosity table.

_GAP_QUESTION_TEXT = {
    GAP_NO_CONTACT: "we don't have their contact info saved yet",
    GAP_NO_LOCATED_IN: "we don't have its location saved yet",
    GAP_GHOST_MULTI_REF: "it's mentioned on multiple pages but has no page of its own yet",
}

_CURIOSITY_COOLDOWN_DAYS = 7


def pending_curiosity(db, hits: list[dict], *, now: str | None = None) -> dict | None:
    """Given this turn's alias_scan() hits (strongest match first), the one
    gap Zilla may permit the model to ask about — the first hit (in hit
    order) carrying an unasked-or-cooled-down gap. Marks it asked_at=now as
    a side effect, so the same gap won't surface again for
    `_CURIOSITY_COOLDOWN_DAYS` — the single mechanism that both keeps a
    conversation to one question and honors the 7-day cooldown for an
    unanswered one. Returns {"node", "gap", "question"} or None if no hit
    has anything pending. Never raises; a broken read just means no
    question gets asked this turn."""
    if not hits:
        return None
    try:
        from datetime import datetime, timedelta
        now = now or datetime.now().isoformat(timespec="seconds")
        cutoff = (
            datetime.fromisoformat(now) - timedelta(days=_CURIOSITY_COOLDOWN_DAYS)
        ).isoformat(timespec="seconds")
        node_ids = [h["id"] for h in hits]
        pending = db.curiosity_pending(node_ids, cooldown_before=cutoff)
        if not pending:
            return None
        by_node: dict[int, list[str]] = {}
        for row in pending:
            by_node.setdefault(row["node_id"], []).append(row["gap"])
        for node in hits:
            gaps = by_node.get(node["id"])
            if not gaps:
                continue
            gap = gaps[0]
            db.curiosity_mark_asked(node["id"], gap, now)
            return {
                "node": node, "gap": gap,
                "question": _GAP_QUESTION_TEXT.get(gap, "there's missing information"),
            }
        return None
    except Exception:
        return None
