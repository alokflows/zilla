# ============================================================
#  GRAPH HTML VIEW — self-contained single-file export (PLAN.md §6.K4)
# ============================================================
#  render_graph_html() turns the SQLite graph (zilla/graph.py's nodes/
#  aliases/edges) into ONE offline-openable HTML file: inline CSS + JS,
#  no CDN, no network calls ever — must open on a phone with no
#  connectivity. All graph data ships as one embedded JSON blob; every
#  interaction (pan/zoom/filter/local-view/search/side-panel) runs
#  client-side against that blob, never a second server round-trip
#  (there is no server).
#
#  Node "attributes" in the PLAN.md spec ("side panel with bio,
#  attributes, current relations...") are limited here to what
#  zilla/store.py's `nodes` table actually persists (type, bio, aliases)
#  — arbitrary "- key:: value" attribute lines are NOT stored in the
#  graph tables by design (K1: "the Wiki pages are the truth"; only
#  type/aliases are extracted). The side panel shows type + bio +
#  aliases + relations, which is the honest superset of what a
#  disposable, rebuildable graph table can offer without re-reading the
#  Markdown file — a live server could do more, a static export can't.
#
#  Layout strategy (the "~60fps for <=2k nodes" requirement): running
#  full O(n^2) spring/repulsion physics every animation frame does not
#  scale to 2k nodes in plain JS. Instead the force simulation runs ONCE
#  per visible-node-set change (global load, local-view refocus, filter
#  change), chunked across animation frames with a spatial grid bucket
#  for repulsion (near-neighbors only, not all pairs) so the tab never
#  freezes, and settles quickly. Once settled, interaction (pan, zoom,
#  drag, hover, select) is pure canvas redraw with no physics in the
#  loop — that is what actually needs to hit 60fps, and does, because
#  it is just drawing <= a few thousand circles and lines per frame.
# ============================================================

from __future__ import annotations

import json

from zilla import graph as _graph

# Categorical palette (dataviz skill's validated default, fixed order —
# never cycled, never reassigned by filters). Only as many slots as this
# product has real node types for (WIKI_SUBDIRS) plus one "other"
# fallback; ghost nodes are NOT a type and never consume a slot — they
# render hollow/dashed in a dedicated muted style instead.
_TYPE_ORDER = ["person", "org", "place", "project", "system"]
_TYPE_COLORS_LIGHT = {
    "person": "#2a78d6", "org": "#008300", "place": "#e87ba4",
    "project": "#eda100", "system": "#1baf7a", "other": "#eb6834",
}
_TYPE_COLORS_DARK = {
    "person": "#3987e5", "org": "#008300", "place": "#d55181",
    "project": "#c98500", "system": "#199e70", "other": "#d95926",
}


def _build_snapshot(db) -> dict:
    """Pure data assembly: db -> {"nodes": [...], "edges": [...]}. Split
    out from render_graph_html so tests can check shape/counts without
    parsing HTML."""
    nodes = db.graph_nodes_all()
    edges = db.graph_edges_all(history=True)

    aliases_by_node: dict[int, list[str]] = {}
    for row in db.graph_aliases_all():
        aliases_by_node.setdefault(row["node_id"], []).append(row["alias"])

    degree: dict[int, int] = {}
    for e in edges:
        if e["valid_to"]:  # degree/sizing reflects CURRENT relations only
            continue
        degree[e["src"]] = degree.get(e["src"], 0) + 1
        degree[e["dst"]] = degree.get(e["dst"], 0) + 1

    node_list = [{
        "id": n["id"],
        "title": n["title"] or "(untitled)",
        "type": (n["type"] or "").lower() or None,
        "bio": n["bio"] or "",
        "ghost": bool(n["is_ghost"]),
        "aliases": sorted(aliases_by_node.get(n["id"], [])),
        "degree": degree.get(n["id"], 0),
    } for n in nodes]

    edge_list = [{
        "src": e["src"], "dst": e["dst"], "rel": e["rel"],
        "from": e["valid_from"], "to": e["valid_to"],
        "superseded": bool(e["valid_to"]),
    } for e in edges]

    return {"nodes": node_list, "edges": edge_list}


def render_graph_html(db, *, focus: str | None = None, default_hops: int = 2) -> str:
    """Build the complete, self-contained HTML document. `focus` (a node
    name/alias, e.g. from `/graph <name>`) opens directly in local view
    on that node if it resolves; None or unresolvable -> global view
    (unresolvable is silent-safe, never an error page — the owner still
    gets a usable graph, just not pre-focused)."""
    snapshot = _build_snapshot(db)
    focus_id = None
    if focus:
        node = _graph.resolve_name(db, focus)
        if node is not None:
            focus_id = node["id"]
    snapshot["focus"] = focus_id
    snapshot["hops"] = max(1, min(3, default_hops))
    snapshot["types"] = _TYPE_ORDER

    payload = json.dumps(snapshot, separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.replace("__GRAPH_DATA__", payload)


def golden_snapshot(node_count: int, edge_fanout: int = 2) -> dict:
    """Synthetic {nodes, edges} of a given size — used by the K4 golden
    test (valid/self-contained HTML) and the 2k-node perf smoke, without
    needing a real Store/db fixture wired up just to check HTML shape."""
    nodes = [{
        "id": i, "title": f"Node {i}", "type": _TYPE_ORDER[i % len(_TYPE_ORDER)],
        "bio": f"synthetic node {i}", "ghost": i % 17 == 0, "aliases": [],
        "degree": 0,
    } for i in range(node_count)]
    edges = []
    for i in range(node_count):
        for k in range(1, edge_fanout + 1):
            j = (i + k) % node_count
            if j == i:
                continue
            edges.append({"src": i, "dst": j, "rel": "mentions",
                         "from": None, "to": None, "superseded": False})
            nodes[i]["degree"] += 1
            nodes[j]["degree"] += 1
    return {"nodes": nodes, "edges": edges, "focus": None, "hops": 2, "types": _TYPE_ORDER}


def render_from_snapshot(snapshot: dict) -> str:
    """Same template, given an already-built snapshot dict (golden_snapshot
    or a hand-built fixture) rather than a live db — test-only entry point
    that shares the exact production template."""
    payload = json.dumps(snapshot, separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.replace("__GRAPH_DATA__", payload)


_TYPE_COLORS_JS = json.dumps({"light": _TYPE_COLORS_LIGHT, "dark": _TYPE_COLORS_DARK})

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Zilla Graph</title>
<style>
  :root {
    color-scheme: light;
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --ink-1: #0b0b0b; --ink-2: #52514e; --ink-muted: #898781;
    --grid: #e1e0d9; --baseline: #c3c2b7;
    --border: rgba(11,11,11,0.10); --panel-bg: #fcfcfbf2;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --surface-1: #1a1a19; --page: #0d0d0d;
      --ink-1: #ffffff; --ink-2: #c3c2b7; --ink-muted: #898781;
      --grid: #2c2c2a; --baseline: #383835;
      --border: rgba(255,255,255,0.10); --panel-bg: #1a1a19f2;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface-1: #1a1a19; --page: #0d0d0d;
    --ink-1: #ffffff; --ink-2: #c3c2b7; --ink-muted: #898781;
    --grid: #2c2c2a; --baseline: #383835;
    --border: rgba(255,255,255,0.10); --panel-bg: #1a1a19f2;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; overflow: hidden;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--page); color: var(--ink-1); }
  #app { display: flex; flex-direction: column; height: 100%; }
  #toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
    padding: 8px 10px; background: var(--surface-1); border-bottom: 1px solid var(--border); z-index: 5; }
  #toolbar input[type="search"], #toolbar select {
    font: inherit; padding: 5px 8px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--page); color: var(--ink-1); }
  #toolbar label { font-size: 13px; color: var(--ink-2); display: flex; align-items: center; gap: 4px; white-space: nowrap; }
  #toolbar button { font: inherit; padding: 5px 10px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--page); color: var(--ink-1); cursor: pointer; }
  #toolbar button.active { background: var(--ink-1); color: var(--page); }
  #hopswrap { display: none; align-items: center; gap: 6px; }
  #hopswrap.show { display: flex; }
  #count { margin-left: auto; font-size: 12px; color: var(--ink-muted); white-space: nowrap; }
  #legend { display: flex; flex-wrap: wrap; gap: 10px; padding: 6px 10px; font-size: 12px; color: var(--ink-2);
    background: var(--surface-1); border-bottom: 1px solid var(--border); }
  .legend-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; vertical-align: -1px; }
  .legend-dot.hollow { background: transparent; border: 2px dashed var(--ink-muted); }
  #main { position: relative; flex: 1; min-height: 0; }
  canvas { display: block; width: 100%; height: 100%; touch-action: none; cursor: grab; }
  canvas.dragging { cursor: grabbing; }
  #listview { position: absolute; inset: 0; overflow: auto; background: var(--page); padding: 10px; display: none; }
  #listview.show { display: block; }
  #listview details { border-bottom: 1px solid var(--grid); padding: 6px 0; }
  #listview summary { cursor: pointer; font-weight: 600; }
  #listview .rel-line { font-size: 13px; color: var(--ink-2); padding: 2px 0 2px 16px; }
  #panel { position: absolute; top: 0; right: 0; bottom: 0; width: min(340px, 92vw);
    background: var(--panel-bg); backdrop-filter: blur(6px); border-left: 1px solid var(--border);
    transform: translateX(100%); transition: transform .15s ease; overflow-y: auto; padding: 14px; }
  #panel.show { transform: translateX(0); }
  #panel h2 { margin: 0 0 4px; font-size: 18px; }
  #panel .ghost-tag { font-size: 11px; color: var(--ink-muted); border: 1px dashed var(--ink-muted);
    border-radius: 4px; padding: 1px 5px; margin-left: 6px; }
  #panel .type-tag { font-size: 11px; color: var(--ink-2); text-transform: uppercase; letter-spacing: .04em; }
  #panel .bio { margin: 8px 0; color: var(--ink-2); font-size: 14px; }
  #panel .aliases { font-size: 12px; color: var(--ink-muted); margin-bottom: 10px; }
  #panel h3 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--ink-muted);
    margin: 14px 0 6px; }
  #panel .rel { font-size: 13px; padding: 3px 0; cursor: pointer; color: var(--ink-1); }
  #panel .rel:hover { text-decoration: underline; }
  #panel .rel .dates { color: var(--ink-muted); font-size: 12px; }
  #panel-close { position: absolute; top: 10px; right: 10px; background: none; border: none;
    color: var(--ink-2); font-size: 18px; cursor: pointer; }
  #panel button.focus-btn { margin-top: 10px; font: inherit; padding: 6px 10px; border-radius: 6px;
    border: 1px solid var(--border); background: var(--ink-1); color: var(--page); cursor: pointer; }
  #loading { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    color: var(--ink-muted); font-size: 13px; pointer-events: none; }
  #empty { position: absolute; inset: 0; display: none; align-items: center; justify-content: center;
    color: var(--ink-muted); font-size: 14px; text-align: center; padding: 20px; }
  #empty.show { display: flex; }
</style>
</head>
<body>
<div id="app">
  <div id="toolbar">
    <button id="btn-global">Global view</button>
    <div id="hopswrap">
      <label>Hops <input id="hops" type="range" min="1" max="3" value="2" style="width:70px"></label>
      <span id="hops-val">2</span>
    </div>
    <input id="search" type="search" placeholder="Search nodes…">
    <select id="typefilter" multiple size="1" title="Filter by type (ctrl/cmd-click for multiple)"></select>
    <label><input id="orphans" type="checkbox" checked> Show orphans</label>
    <button id="btn-list">List view</button>
    <span id="count"></span>
  </div>
  <div id="legend"></div>
  <div id="main">
    <div id="loading">Laying out graph…</div>
    <div id="empty">No graph data yet — mention people, places, or projects in chat and Zilla will start building this.</div>
    <canvas id="canvas"></canvas>
    <div id="listview"></div>
    <div id="panel">
      <button id="panel-close">✕</button>
      <div id="panel-body"></div>
    </div>
  </div>
</div>
<script id="graph-data" type="application/json">__GRAPH_DATA__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("graph-data").textContent);
  var TYPE_COLORS = __TYPE_COLORS__;
  var TYPE_ORDER = DATA.types || [];

  var nodesById = {};
  DATA.nodes.forEach(function (n) { nodesById[n.id] = n; });

  var state = {
    mode: DATA.focus ? "local" : "global",
    focus: DATA.focus || null,
    hops: DATA.hops || 2,
    search: "",
    typeFilter: null,      // null = all
    showOrphans: true,
    selected: null,
    view: "canvas",        // "canvas" | "list"
  };

  var pan = { x: 0, y: 0, scale: 1 };
  var positions = {};      // id -> {x,y,vx,vy,fixed}
  var visibleNodes = [];
  var visibleEdges = [];
  var simRunning = false;

  function isDark() {
    var attr = document.documentElement.getAttribute("data-theme");
    if (attr === "dark") return true;
    if (attr === "light") return false;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function colorFor(type, ghost) {
    if (ghost) return null; // hollow, handled separately
    var t = TYPE_ORDER.indexOf(type) === -1 ? "other" : type;
    var pal = TYPE_COLORS[isDark() ? "dark" : "light"];
    return pal[t] || pal.other;
  }

  // ── current (non-superseded) adjacency, for BFS / layout / rendering ──
  var currentAdj = {};
  DATA.edges.forEach(function (e) {
    if (e.superseded) return;
    (currentAdj[e.src] = currentAdj[e.src] || []).push({ other: e.dst, rel: e.rel, dir: "out" });
    (currentAdj[e.dst] = currentAdj[e.dst] || []).push({ other: e.src, rel: e.rel, dir: "in" });
  });

  function bfsWithin(startId, hops) {
    var visited = {}; visited[startId] = 0;
    var q = [[startId, 0]], out = [startId];
    while (q.length) {
      var pair = q.shift(), id = pair[0], hop = pair[1];
      if (hop >= hops) continue;
      (currentAdj[id] || []).forEach(function (edge) {
        if (visited[edge.other] !== undefined) return;
        visited[edge.other] = hop + 1;
        out.push(edge.other);
        q.push([edge.other, hop + 1]);
      });
    }
    return out;
  }

  // ── recompute the visible node/edge set from state ──
  function recompute() {
    var idSet;
    if (state.mode === "local" && state.focus != null && nodesById[state.focus]) {
      idSet = bfsWithin(state.focus, state.hops);
    } else {
      idSet = DATA.nodes.map(function (n) { return n.id; });
    }
    var q = state.search.trim().toLowerCase();
    if (q) {
      idSet = idSet.filter(function (id) {
        var n = nodesById[id];
        if (!n) return false;
        if (n.title.toLowerCase().indexOf(q) !== -1) return true;
        return (n.aliases || []).some(function (a) { return a.toLowerCase().indexOf(q) !== -1; });
      });
    }
    if (state.typeFilter && state.typeFilter.length) {
      var allow = {};
      state.typeFilter.forEach(function (t) { allow[t] = true; });
      idSet = idSet.filter(function (id) {
        var n = nodesById[id];
        var t = n.type && TYPE_ORDER.indexOf(n.type) !== -1 ? n.type : (n.ghost ? "ghost" : "other");
        return allow[t];
      });
    }
    var idIn = {}; idSet.forEach(function (id) { idIn[id] = true; });
    var edges = DATA.edges.filter(function (e) {
      return !e.superseded && idIn[e.src] && idIn[e.dst];
    });
    if (!state.showOrphans) {
      var deg = {};
      edges.forEach(function (e) { deg[e.src] = (deg[e.src] || 0) + 1; deg[e.dst] = (deg[e.dst] || 0) + 1; });
      idSet = idSet.filter(function (id) { return deg[id] > 0; });
      idIn = {}; idSet.forEach(function (id) { idIn[id] = true; });
      edges = edges.filter(function (e) { return idIn[e.src] && idIn[e.dst]; });
    }
    visibleNodes = idSet.map(function (id) { return nodesById[id]; }).filter(Boolean);
    visibleEdges = edges;
    document.getElementById("count").textContent =
      visibleNodes.length + " node" + (visibleNodes.length === 1 ? "" : "s") +
      ", " + visibleEdges.length + " edge" + (visibleEdges.length === 1 ? "" : "s");
    document.getElementById("empty").classList.toggle("show", DATA.nodes.length === 0);
    layoutAndRender();
    if (state.view === "list") renderList();
  }

  // ── force layout: grid-bucketed repulsion + spring + centering,
  //    chunked across animation frames so it never blocks the tab ──
  function layoutAndRender() {
    var canvas = document.getElementById("canvas");
    var w = canvas.clientWidth || 800, h = canvas.clientHeight || 600;
    visibleNodes.forEach(function (n) {
      if (!positions[n.id]) {
        var angle = Math.random() * Math.PI * 2, r = Math.min(w, h) * 0.3;
        positions[n.id] = {
          x: w / 2 + Math.cos(angle) * r, y: h / 2 + Math.sin(angle) * r,
          vx: 0, vy: 0, fixed: false,
        };
      }
    });
    var loading = document.getElementById("loading");
    if (visibleNodes.length === 0) { loading.style.display = "none"; draw(); return; }
    loading.style.display = "flex";
    simRunning = true;
    var iterations = Math.max(40, Math.min(220, Math.round(30000 / Math.max(1, visibleNodes.length))));
    var i = 0;
    var CELL = 60;

    function grid() {
      var g = {};
      visibleNodes.forEach(function (n) {
        var p = positions[n.id];
        var key = (Math.floor(p.x / CELL)) + "," + (Math.floor(p.y / CELL));
        (g[key] = g[key] || []).push(n.id);
      });
      return g;
    }

    function step() {
      var g = grid();
      var repulseK = 1800, springK = 0.02, idealLen = 70, damping = 0.82;
      visibleNodes.forEach(function (n) {
        var p = positions[n.id];
        if (p.fixed) return;
        var cx = Math.floor(p.x / CELL), cy = Math.floor(p.y / CELL);
        var fx = 0, fy = 0;
        for (var dx = -1; dx <= 1; dx++) {
          for (var dy = -1; dy <= 1; dy++) {
            var neighbors = g[(cx + dx) + "," + (cy + dy)];
            if (!neighbors) continue;
            neighbors.forEach(function (oid) {
              if (oid === n.id) return;
              var o = positions[oid];
              var ddx = p.x - o.x, ddy = p.y - o.y;
              var dist2 = ddx * ddx + ddy * ddy + 0.01;
              var dist = Math.sqrt(dist2);
              if (dist > CELL * 1.5) return;
              var f = repulseK / dist2;
              fx += (ddx / dist) * f; fy += (ddy / dist) * f;
            });
          }
        }
        p.vx = (p.vx + fx) * damping; p.vy = (p.vy + fy) * damping;
      });
      visibleEdges.forEach(function (e) {
        var a = positions[e.src], b = positions[e.dst];
        if (!a || !b) return;
        var ddx = b.x - a.x, ddy = b.y - a.y;
        var dist = Math.sqrt(ddx * ddx + ddy * ddy) || 0.01;
        var f = springK * (dist - idealLen);
        var fx = (ddx / dist) * f, fy = (ddy / dist) * f;
        if (!a.fixed) { a.vx += fx; a.vy += fy; }
        if (!b.fixed) { b.vx -= fx; b.vy -= fy; }
      });
      visibleNodes.forEach(function (n) {
        var p = positions[n.id];
        if (p.fixed) return;
        p.vx += (w / 2 - p.x) * 0.002; p.vy += (h / 2 - p.y) * 0.002;
        p.x += p.vx; p.y += p.vy;
      });
    }

    function frame() {
      var chunk = 4, did = 0;
      while (did < chunk && i < iterations) { step(); i++; did++; }
      draw();
      if (i < iterations) {
        requestAnimationFrame(frame);
      } else {
        simRunning = false;
        loading.style.display = "none";
      }
    }
    requestAnimationFrame(frame);
  }

  // ── canvas drawing (no physics here — just transform + draw) ──
  var canvas = document.getElementById("canvas");
  var ctx = canvas.getContext("2d");

  function resize() {
    var dpr = window.devicePixelRatio || 1;
    canvas.width = canvas.clientWidth * dpr;
    canvas.height = canvas.clientHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }
  window.addEventListener("resize", resize);

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function draw() {
    var w = canvas.clientWidth, h = canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(pan.x, pan.y);
    ctx.scale(pan.scale, pan.scale);

    ctx.strokeStyle = css("--grid");
    ctx.lineWidth = 1.2 / pan.scale;
    visibleEdges.forEach(function (e) {
      var a = positions[e.src], b = positions[e.dst];
      if (!a || !b) return;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    });

    var showLabels = visibleNodes.length <= 80;
    visibleNodes.forEach(function (n) {
      var p = positions[n.id];
      if (!p) return;
      var r = Math.max(5, Math.min(24, 5 + Math.sqrt(n.degree || 0) * 4));
      var col = colorFor(n.type, n.ghost);
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      if (n.ghost) {
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = css("--ink-muted");
        ctx.lineWidth = 2 / pan.scale;
        ctx.stroke();
        ctx.setLineDash([]);
      } else {
        ctx.fillStyle = col;
        ctx.fill();
      }
      if (state.selected === n.id) {
        ctx.lineWidth = 2.5 / pan.scale;
        ctx.strokeStyle = css("--ink-1");
        ctx.stroke();
      }
      if (showLabels || state.selected === n.id || r > 14) {
        ctx.fillStyle = css("--ink-1");
        ctx.font = (12 / pan.scale) + "px system-ui, sans-serif";
        ctx.fillText(n.title, p.x + r + 4, p.y + 4);
      }
    });
    ctx.restore();
  }

  // ── pan / zoom / drag / tap ──
  var dragging = null, panning = false, lastX = 0, lastY = 0, moved = false;

  function toWorld(clientX, clientY) {
    var rect = canvas.getBoundingClientRect();
    return {
      x: (clientX - rect.left - pan.x) / pan.scale,
      y: (clientY - rect.top - pan.y) / pan.scale,
    };
  }

  function hitTest(wx, wy) {
    for (var i = visibleNodes.length - 1; i >= 0; i--) {
      var n = visibleNodes[i], p = positions[n.id];
      if (!p) continue;
      var r = Math.max(5, Math.min(24, 5 + Math.sqrt(n.degree || 0) * 4)) + 4;
      var dx = wx - p.x, dy = wy - p.y;
      if (dx * dx + dy * dy <= r * r) return n;
    }
    return null;
  }

  canvas.addEventListener("pointerdown", function (ev) {
    var w = toWorld(ev.clientX, ev.clientY);
    var hit = hitTest(w.x, w.y);
    moved = false;
    if (hit) {
      dragging = hit.id; positions[hit.id].fixed = true;
    } else {
      panning = true; canvas.classList.add("dragging");
    }
    lastX = ev.clientX; lastY = ev.clientY;
    canvas.setPointerCapture(ev.pointerId);
  });
  canvas.addEventListener("pointermove", function (ev) {
    if (!dragging && !panning) return;
    var dx = ev.clientX - lastX, dy = ev.clientY - lastY;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;
    if (dragging) {
      var p = positions[dragging];
      p.x += dx / pan.scale; p.y += dy / pan.scale;
    } else if (panning) {
      pan.x += dx; pan.y += dy;
    }
    lastX = ev.clientX; lastY = ev.clientY;
    draw();
  });
  canvas.addEventListener("pointerup", function (ev) {
    if (dragging && !moved) selectNode(dragging);
    if (!dragging && !moved) { closePanel(); }
    dragging = null; panning = false;
    canvas.classList.remove("dragging");
  });
  canvas.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    var factor = ev.deltaY < 0 ? 1.1 : 0.9;
    var rect = canvas.getBoundingClientRect();
    var mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    pan.x = mx - (mx - pan.x) * factor;
    pan.y = my - (my - pan.y) * factor;
    pan.scale = Math.max(0.15, Math.min(4, pan.scale * factor));
    draw();
  }, { passive: false });

  // ── side panel ──
  function relLine(node, dir, rel, other, dates, superseded) {
    var arrow = dir === "out" ? "→" : "←";
    var div = document.createElement("div");
    div.className = "rel";
    div.textContent = arrow + " " + rel + " " + arrow + " " + (other ? other.title : "?") + (dates ? " " : "");
    if (dates) {
      var span = document.createElement("span");
      span.className = "dates"; span.textContent = dates;
      div.appendChild(span);
    }
    if (other) {
      div.addEventListener("click", function () { selectNode(other.id); focusOn(other.id); });
    }
    return div;
  }

  function fmtDates(from, to) {
    if (to) return "(" + (from || "?") + " .. " + to + ", superseded)";
    if (from) return "(since " + from + ")";
    return "";
  }

  function selectNode(id) {
    var n = nodesById[id];
    if (!n) return;
    state.selected = id;
    draw();
    var body = document.getElementById("panel-body");
    body.innerHTML = "";
    var h2 = document.createElement("h2");
    h2.textContent = n.title;
    if (n.ghost) {
      var tag = document.createElement("span");
      tag.className = "ghost-tag"; tag.textContent = "ghost — no page yet";
      h2.appendChild(tag);
    }
    body.appendChild(h2);
    if (n.type) {
      var tt = document.createElement("div");
      tt.className = "type-tag"; tt.textContent = n.type;
      body.appendChild(tt);
    }
    if (n.bio) {
      var bio = document.createElement("div");
      bio.className = "bio"; bio.textContent = n.bio;
      body.appendChild(bio);
    }
    if (n.aliases && n.aliases.length) {
      var al = document.createElement("div");
      al.className = "aliases"; al.textContent = "aka " + n.aliases.join(", ");
      body.appendChild(al);
    }
    var current = DATA.edges.filter(function (e) { return !e.superseded && (e.src === id || e.dst === id); });
    var past = DATA.edges.filter(function (e) { return e.superseded && (e.src === id || e.dst === id); });
    var h3 = document.createElement("h3"); h3.textContent = "Current relations"; body.appendChild(h3);
    if (!current.length) {
      var none = document.createElement("div"); none.className = "rel"; none.style.color = "var(--ink-muted)";
      none.textContent = "(none known)"; body.appendChild(none);
    }
    current.forEach(function (e) {
      var dir = e.src === id ? "out" : "in";
      var other = nodesById[dir === "out" ? e.dst : e.src];
      body.appendChild(relLine(n, dir, e.rel, other, fmtDates(e.from, e.to), false));
    });
    if (past.length) {
      var details = document.createElement("details");
      var summary = document.createElement("summary");
      summary.textContent = "Superseded (" + past.length + ")";
      details.appendChild(summary);
      past.forEach(function (e) {
        var dir = e.src === id ? "out" : "in";
        var other = nodesById[dir === "out" ? e.dst : e.src];
        details.appendChild(relLine(n, dir, e.rel, other, fmtDates(e.from, e.to), true));
      });
      body.appendChild(details);
    }
    var focusBtn = document.createElement("button");
    focusBtn.className = "focus-btn"; focusBtn.textContent = "Focus local view here";
    focusBtn.addEventListener("click", function () { focusOn(id); });
    body.appendChild(focusBtn);

    document.getElementById("panel").classList.add("show");
  }

  function closePanel() {
    state.selected = null;
    document.getElementById("panel").classList.remove("show");
    draw();
  }
  document.getElementById("panel-close").addEventListener("click", closePanel);

  function focusOn(id) {
    state.mode = "local"; state.focus = id;
    document.getElementById("hopswrap").classList.add("show");
    positions = {}; // fresh layout centered on the new focus
    recompute();
  }

  document.getElementById("btn-global").addEventListener("click", function () {
    state.mode = "global"; state.focus = null;
    document.getElementById("hopswrap").classList.remove("show");
    positions = {};
    recompute();
  });

  document.getElementById("hops").addEventListener("input", function (ev) {
    state.hops = parseInt(ev.target.value, 10);
    document.getElementById("hops-val").textContent = state.hops;
    recompute();
  });

  document.getElementById("search").addEventListener("input", function (ev) {
    state.search = ev.target.value;
    recompute();
  });

  var typeSelect = document.getElementById("typefilter");
  (TYPE_ORDER.concat(["other", "ghost"])).forEach(function (t) {
    var opt = document.createElement("option");
    opt.value = t; opt.textContent = t;
    typeSelect.appendChild(opt);
  });
  typeSelect.addEventListener("change", function () {
    var vals = Array.prototype.slice.call(typeSelect.selectedOptions).map(function (o) { return o.value; });
    state.typeFilter = vals.length ? vals : null;
    recompute();
  });

  document.getElementById("orphans").addEventListener("change", function (ev) {
    state.showOrphans = ev.target.checked;
    recompute();
  });

  document.getElementById("btn-list").addEventListener("click", function () {
    state.view = state.view === "list" ? "canvas" : "list";
    document.getElementById("listview").classList.toggle("show", state.view === "list");
    document.getElementById("btn-list").classList.toggle("active", state.view === "list");
    if (state.view === "list") renderList();
  });

  function renderList() {
    var el = document.getElementById("listview");
    el.innerHTML = "";
    visibleNodes.forEach(function (n) {
      var det = document.createElement("details");
      var sum = document.createElement("summary");
      sum.textContent = n.title + (n.ghost ? " (ghost)" : "") + (n.type ? " — " + n.type : "");
      det.appendChild(sum);
      var rels = DATA.edges.filter(function (e) {
        return !e.superseded && (e.src === n.id || e.dst === n.id);
      });
      if (!rels.length) {
        var none = document.createElement("div");
        none.className = "rel-line"; none.textContent = "(no known relations)";
        det.appendChild(none);
      }
      rels.forEach(function (e) {
        var dir = e.src === n.id ? "out" : "in";
        var other = nodesById[dir === "out" ? e.dst : e.src];
        var line = document.createElement("div");
        line.className = "rel-line";
        line.textContent = (dir === "out" ? "→ " : "← ") + e.rel + " " + (dir === "out" ? "→ " : "← ") +
          (other ? other.title : "?") + " " + fmtDates(e.from, e.to);
        det.appendChild(line);
      });
      el.appendChild(det);
    });
  }

  function renderLegend() {
    var el = document.getElementById("legend");
    el.innerHTML = "";
    var pal = TYPE_COLORS[isDark() ? "dark" : "light"];
    TYPE_ORDER.concat(["other"]).forEach(function (t) {
      var span = document.createElement("span");
      var dot = document.createElement("span");
      dot.className = "legend-dot"; dot.style.background = pal[t] || pal.other;
      span.appendChild(dot);
      span.appendChild(document.createTextNode(t));
      el.appendChild(span);
    });
    var ghostSpan = document.createElement("span");
    var ghostDot = document.createElement("span");
    ghostDot.className = "legend-dot hollow";
    ghostSpan.appendChild(ghostDot);
    ghostSpan.appendChild(document.createTextNode("ghost (no page yet)"));
    el.appendChild(ghostSpan);
  }

  if (state.mode === "local") document.getElementById("hopswrap").classList.add("show");
  document.getElementById("hops").value = state.hops;
  document.getElementById("hops-val").textContent = state.hops;
  renderLegend();
  resize();
  recompute();
})();
</script>
</body>
</html>
""".replace("__TYPE_COLORS__", _TYPE_COLORS_JS)
