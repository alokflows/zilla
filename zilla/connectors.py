# ============================================================
#  CONNECTORS — manage the backends' own MCP configs (PLAN.md §12/C2)
# ============================================================
#  Reality, recorded in docs/dev/RESEARCH_CONNECTORS_2026-08-17.md and
#  re-verify before changing an adapter: connectors live at the BACKEND
#  level and differ per CLI.
#
#    claude   — the CLI owns the config. Zilla shells out to
#               `claude mcp add/remove --scope user` (never `local`: that
#               scope is keyed by the working directory, so a connector
#               would silently vanish when CLI_WORKING_DIR moves) and
#               reads the truth straight back out of ~/.claude.json's
#               top-level `mcpServers`.
#    agy      — no `mcp` subcommand; the file IS the interface:
#               ~/.gemini/config/mcp_config.json, {"mcpServers": {...}}.
#    opencode — ~/.config/opencode/opencode.jsonc under "mcp"; `enabled`
#               is REQUIRED on every entry, and it is the one backend
#               with a real enable/disable.
#
#  Zilla does not proxy or re-implement any of this. Every write is
#  verified by reading the config back (the same discipline as
#  config.set_model), because a guessed key or a silent no-op must never
#  look like success.
#
#  agy ALSO has native account-level connectors (Drive/Gmail/GitHub/
#  Slack/Jira) that are authorized by signing in inside agy — nothing
#  under ~/.gemini holds their state. Zilla can truthfully say they
#  exist and point the owner at the sign-in, but can never configure one
#  and must never claim it is connected.
#
#  Secrets discipline (P5): env/header VALUES live only inside the
#  backend's own config file. Every log line and every owner-facing
#  render goes through redact(); raised errors name the server, never
#  its contents. Nothing here ever writes to Memory/ or an export.
#
#  Pure except for the backend file/CLI I/O behind three narrow seams
#  (_run, and the path getters), which tests monkeypatch. Never raises
#  on malformed backend files — an unreadable config reads as empty,
#  closed, never open (P4).
# ============================================================

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse

logger = logging.getLogger(__name__)


class ConnectorError(Exception):
    """One plain-language reason an add/remove/toggle did not land."""


# ── canonical entry shape ─────────────────────────────────
#
#   {"name": str, "kind": "stdio"|"remote",
#    stdio : {"command": str, "args": [str], "env": {k: v}}
#    remote: {"url": str, "headers": {k: v}, "transport": "http"|"sse"}}

KIND_STDIO = "stdio"
KIND_REMOTE = "remote"

MAX_NAME = 64
MAX_ARGS = 32
MAX_ENV = 16
MAX_HEADERS = 16
MAX_SERVERS = 50          # sanity cap per backend

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,%d}$" % (MAX_NAME - 1))

# agy's native connectors, from the binary's own CONNECTOR_TYPE_ strings
# (research doc §4). Account-level: authorized by signing in inside agy,
# state lives on Google's side — reportable, never configurable.
# `words` feeds hint_backends' keyword match (C2 step 3): how an owner
# actually names it in a sentence.
NATIVE = {
    "agy": [
        {"id": "google_drive", "name": "Google Drive",
         "words": ("drive", "gdrive", "googledrive")},
        {"id": "gmail", "name": "Gmail",
         "words": ("gmail",)},
        {"id": "email", "name": "Email",
         "words": ("email",)},
        {"id": "github", "name": "GitHub",
         "words": ("github",)},
        {"id": "slack", "name": "Slack",
         "words": ("slack",)},
        {"id": "jira", "name": "Jira",
         "words": ("jira",)},
    ],
}

NATIVE_NOTE = ("Sign-in happens inside the app itself — I can't switch "
               "these on for you.")

BACKENDS = ("agy", "claude", "opencode")

# claude is managed through its CLI, so managing needs the binary; the
# other two are plain files we read and write ourselves.
CLI_MANAGED = ("claude",)


# ══════════════════════════════════════════════════════════
#  SEAMS  (tests monkeypatch these)
# ══════════════════════════════════════════════════════════

def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    """(rc, stdout, stderr) of one backend CLI call. Isolated so tests run
    adds/removes against mocked binaries. stderr text may name paths but
    is never logged with entry values."""
    import subprocess
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout or "", r.stderr or ""
    except Exception as e:
        return 127, "", str(e)


def _claude_json() -> str:
    from zilla.config import CLAUDE_CONFIG_FILE
    return CLAUDE_CONFIG_FILE


def _claude_bin() -> str | None:
    from zilla.config import CLAUDE_PATH
    return CLAUDE_PATH if CLAUDE_PATH and os.path.exists(CLAUDE_PATH) else None


def _agy_mcp_file() -> str:
    from zilla.config import AGY_MCP_CONFIG
    return AGY_MCP_CONFIG


def _opencode_config() -> str:
    from zilla.config import OPENCODE_CONFIG_FILE
    return OPENCODE_CONFIG_FILE


def _opencode_bin() -> str | None:
    from zilla.config import OPENCODE_PATH
    return OPENCODE_PATH if OPENCODE_PATH and os.path.exists(OPENCODE_PATH) else None


# ══════════════════════════════════════════════════════════
#  NORMALIZE + REDACT
# ══════════════════════════════════════════════════════════

def normalize_spec(raw: dict) -> dict:
    """Validate one owner-approved entry into canonical form, or raise
    ConnectorError with the reason. The gate keeps junk out of a backend
    config that the CLI will then really execute."""
    if not isinstance(raw, dict):
        raise ConnectorError("That connector didn't come through as expected.")
    # opencode's schema carries a real enable switch; an explicit value
    # travels with the spec, absence means on.
    enabled = bool(raw["enabled"]) if "enabled" in raw else None
    name = str(raw.get("name") or "").strip()
    if not _NAME_RE.match(name):
        raise ConnectorError(
            f"'{name[:40]}' isn't usable as a connector name — letters, "
            "numbers, dots, dashes and underscores only.")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind == KIND_STDIO:
        command = str(raw.get("command") or "").strip()
        if not command:
            raise ConnectorError(
                f"{name} needs the command to run — I don't have one.")
        args = [str(a) for a in (raw.get("args") or [])][:MAX_ARGS]
        env = _str_map(raw.get("env"), MAX_ENV)
        out = {"name": name, "kind": KIND_STDIO,
               "command": command, "args": args, "env": env}
    elif kind == KIND_REMOTE:
        url = str(raw.get("url") or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ConnectorError(f"{name} needs a full https address.")
        transport = str(raw.get("transport") or "http").strip().lower()
        if transport not in ("http", "sse"):
            raise ConnectorError(f"{name}: use http or sse as the type.")
        headers = _str_map(raw.get("headers"), MAX_HEADERS)
        out = {"name": name, "kind": KIND_REMOTE, "url": url,
               "headers": headers, "transport": transport}
    else:
        raise ConnectorError(f"{name}: I need to know if it runs locally or "
                             "lives online.")
    if enabled is not None:
        out["enabled"] = enabled
    return out


def _str_map(raw, cap: int) -> dict[str, str]:
    if raw in (None, {}, []):
        return {}
    if not isinstance(raw, dict):
        raise ConnectorError("Keys and values should come as name: value pairs.")
    out: dict[str, str] = {}
    for k, v in list(raw.items())[:cap]:
        k = str(k).strip()
        if k:
            out[k] = "" if v is None else str(v)
    return out


def redact(entry: dict) -> dict:
    """The display/log form: key names stay, values become bullets. Every
    log line and every screen renders THIS, never the raw entry."""
    if not isinstance(entry, dict):
        return {}
    out = dict(entry)
    if isinstance(entry.get("env"), dict):
        out["env"] = {k: "•••" for k in entry["env"]}
    if isinstance(entry.get("headers"), dict):
        out["headers"] = {k: "•••" for k in entry["headers"]}
    return out


def describe(entry: dict) -> str:
    """One short line saying what will run — the confirm card's body.
    Carries names and shapes, never secret values."""
    e = redact(entry)
    if e.get("kind") == KIND_REMOTE:
        bits = [f"online · {e.get('url', '')}"]
        if e.get("headers"):
            bits.append("keys: " + ", ".join(sorted(e["headers"])))
        return f"{e.get('name')} — " + " · ".join(bits)
    bits = [f"runs `{e.get('command', '')}`"]
    if e.get("args"):
        bits.append(" ".join(str(a) for a in e["args"][:6]))
    if e.get("env"):
        bits.append("keys: " + ", ".join(sorted(e["env"])))
    return f"{e.get('name')} — " + " · ".join(bits)


# ══════════════════════════════════════════════════════════
#  FILE HELPERS
# ══════════════════════════════════════════════════════════

def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: dict) -> bool:
    """Atomic write, preserving everything else already in the file;
    created 0600 (the file holds secrets once an env/headers value lands)."""
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except OSError as e:
        logger.error(f"[CONNECTORS] write failed for {os.path.basename(path)}: {e}")
        return False


_JSONC_COMMENT = re.compile(r"(?s)(//[^\n]*)|(/\*.*?\*/)")


def _parse_jsonc(text: str) -> dict:
    """Read opencode's JSONC: comments allowed. String-aware comment strip,
    then a trailing-comma pass only if strict parsing fails."""
    def _keep_strings(m: re.Match) -> str:
        s = m.group(0)
        return s if s.startswith('"') else ""
    stripped = re.sub(r'(?s)"(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/',
                      _keep_strings, text)
    for attempt in (stripped, re.sub(r",(\s*[}\]])", r"\1", stripped)):
        try:
            data = json.loads(attempt)
            return data if isinstance(data, dict) else {}
        except ValueError:
            continue
    return {}


def _read_opencode(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _parse_jsonc(f.read())
    except OSError:
        return {}


# ══════════════════════════════════════════════════════════
#  PER-BACKEND READERS  (native shape → canonical)
# ══════════════════════════════════════════════════════════

def _claude_from_native(name: str, e: dict) -> dict | None:
    etype = str(e.get("type") or "stdio").lower()
    if etype in ("http", "sse"):
        return {"name": name, "kind": KIND_REMOTE, "url": str(e.get("url") or ""),
                "headers": dict(e.get("headers") or {}), "transport": etype}
    if isinstance(e.get("command"), str):
        return {"name": name, "kind": KIND_STDIO, "command": e["command"],
                "args": [str(a) for a in (e.get("args") or [])],
                "env": dict(e.get("env") or {})}
    return None


def _agy_from_native(name: str, e: dict) -> dict | None:
    if isinstance(e.get("serverUrl"), str):
        return {"name": name, "kind": KIND_REMOTE, "url": e["serverUrl"],
                "headers": dict(e.get("headers") or {}), "transport": "http"}
    if isinstance(e.get("command"), str):
        return {"name": name, "kind": KIND_STDIO, "command": e["command"],
                "args": [str(a) for a in (e.get("args") or [])],
                "env": dict(e.get("env") or {})}
    return None


def _opencode_from_native(name: str, e: dict) -> dict | None:
    etype = str(e.get("type") or "").lower()
    enabled = bool(e.get("enabled", False))
    if etype == "remote":
        base = {"name": name, "kind": KIND_REMOTE, "url": str(e.get("url") or ""),
                "headers": dict(e.get("headers") or {}), "transport": "http"}
    elif etype == "local":
        argv = [str(a) for a in (e.get("command") or [])]
        if not argv:
            return None
        base = {"name": name, "kind": KIND_STDIO, "command": argv[0],
                "args": argv[1:], "env": dict(e.get("environment") or {})}
    else:
        return None
    base["enabled"] = enabled
    return base


# ══════════════════════════════════════════════════════════
#  LIST  (the truthful read)
# ══════════════════════════════════════════════════════════

def list_servers(backend: str) -> dict[str, dict]:
    """name → canonical entry, exactly as each backend's own config holds
    it right now. An unreadable/absent config reads as {} — closed, never
    open. Values may contain secrets; render through redact()."""
    b = (backend or "").strip().lower()
    if b == "claude":
        servers = _read_json(_claude_json()).get("mcpServers")
        if not isinstance(servers, dict):
            return {}
        return {n: e for n, e in ((n, _claude_from_native(n, e))
                                  for n, e in servers.items()
                                  if isinstance(n, str))
                if e is not None}
    if b == "agy":
        servers = _read_json(_agy_mcp_file()).get("mcpServers")
        if not isinstance(servers, dict):
            return {}
        return {n: e for n, e in ((n, _agy_from_native(n, e))
                                  for n, e in servers.items()
                                  if isinstance(n, str))
                if e is not None}
    if b == "opencode":
        servers = _read_opencode(_opencode_config()).get("mcp")
        if not isinstance(servers, dict):
            return {}
        return {n: e for n, e in ((n, _opencode_from_native(n, e))
                                  for n, e in servers.items()
                                  if isinstance(n, str))
                if e is not None}
    return {}


def natives(backend: str) -> list[dict]:
    return [dict(n) for n in NATIVE.get((backend or "").strip().lower(), [])]


# ══════════════════════════════════════════════════════════
#  WRITE + READ-BACK VERIFY
# ══════════════════════════════════════════════════════════

def can_manage(backend: str) -> bool:
    """True when Zilla can actually manage this backend's MCP config here —
    claude needs its binary; file-managed backends always qualify."""
    b = (backend or "").strip().lower()
    if b in CLI_MANAGED:
        return _claude_bin() is not None
    return b in BACKENDS


def supports_toggle(backend: str) -> bool:
    """Only opencode's config schema carries `enabled`. For the others,
    disable means remove — Zilla says so rather than faking a toggle."""
    return (backend or "").strip().lower() == "opencode"


def add_server(backend: str, spec: dict) -> dict:
    """Write one server into the backend's own config, verify by reading
    back, and return what is now stored. Raises ConnectorError with the
    plain reason when anything refuses to land."""
    b = (backend or "").strip().lower()
    if b not in BACKENDS:
        raise ConnectorError(f"{b} isn't one of the AI tools I know.")
    if len(list_servers(b)) >= MAX_SERVERS:
        raise ConnectorError("There are a lot of connectors on this tool "
                             "already — remove one first.")
    entry = normalize_spec(spec)

    if b == "claude":
        binpath = _claude_bin()
        if not binpath:
            raise ConnectorError(
                "Claude isn't installed here, so I can't add connectors to it.")
        rc, _out, err = _run(_claude_add_cmd(binpath, entry))
        if rc != 0:
            logger.warning(f"[CONNECTORS] claude mcp add refused '{entry['name']}' (rc={rc})")
            raise ConnectorError(
                "Claude refused the new connector. Check /settings and try again.")
    else:
        stored = {n: e for n, e in list_servers(b).items() if n != entry["name"]}
        stored[entry["name"]] = entry
        if b == "agy":
            # The file is agy's — anything besides mcpServers stays as-is.
            data = _read_json(_agy_mcp_file())
            data["mcpServers"] = _to_agy(stored)
            ok = _write_json(_agy_mcp_file(), data)
        else:
            # opencode's schema REQUIRES enabled on every entry; absent
            # means on (adding is adding).
            entry.setdefault("enabled", True)
            stored[entry["name"]] = entry
            data = _read_opencode(_opencode_config())
            data["mcp"] = _to_opencode(stored)
            ok = _write_json(_opencode_config(), data)
        if not ok:
            raise ConnectorError(
                "I couldn't save the connector's settings file. Nothing changed.")

    # Read-back verification — the source of truth is the config itself.
    landed = list_servers(b).get(entry["name"])
    if landed is None or not _same(landed, entry, toggle=b == "opencode"):
        raise ConnectorError(
            f"{entry['name']} didn't stick in {b}'s settings. Nothing was saved.")
    log_event_added(b, entry["name"])
    return landed


def remove_server(backend: str, name: str) -> bool:
    """Take one server out of the backend's own config. True when a re-read
    proves it is gone; False (with the reason logged, never the contents)
    when it isn't ours to remove or the removal refused."""
    b = (backend or "").strip().lower()
    if b not in BACKENDS or not name:
        return False
    before = list_servers(b)
    if name not in before:
        return False
    if b == "claude":
        binpath = _claude_bin()
        if not binpath:
            raise ConnectorError(
                "Claude isn't installed here, so I can't touch its connectors.")
        rc, _out, err = _run([binpath, "mcp", "remove", "--scope", "user", name])
        if rc != 0:
            logger.warning(f"[CONNECTORS] claude mcp remove refused '{name}' (rc={rc})")
            raise ConnectorError(
                "Claude refused to remove it. Open /settings and try again.")
    else:
        remaining = {n: e for n, e in before.items() if n != name}
        if b == "agy":
            data = _read_json(_agy_mcp_file())
            data["mcpServers"] = _to_agy(remaining)
            ok = _write_json(_agy_mcp_file(), data)
        else:
            data = _read_opencode(_opencode_config())
            data["mcp"] = _to_opencode(remaining)
            ok = _write_json(_opencode_config(), data)
        if not ok:
            raise ConnectorError(
                "I couldn't save the settings file. Nothing changed.")
    gone = name not in list_servers(b)
    if gone:
        log_event_removed(b, name)
    return gone


def set_enabled(backend: str, name: str, enabled: bool) -> dict | None:
    """Flip opencode's real enable switch. Any other backend raises — the
    screen checks supports_toggle() and never offers the button there."""
    b = (backend or "").strip().lower()
    if not supports_toggle(b):
        raise ConnectorError(
            "This tool can only remove a connector, not pause it — "
            "remove it and add it back when you need it.")
    current = list_servers(b).get(name)
    if current is None:
        return None
    current["enabled"] = bool(enabled)
    add_server(b, current)
    log_event_toggled(b, name, enabled)
    return list_servers(b).get(name)


def _claude_add_cmd(binpath: str, e: dict) -> list[str]:
    cmd = [binpath, "mcp", "add", "--scope", "user"]
    if e["kind"] == KIND_REMOTE:
        cmd += ["--transport", e["transport"]]
        for k in sorted(e["headers"]):
            cmd += ["--header", f"{k}: {e['headers'][k]}"]
        cmd += [e["name"], e["url"]]
    else:
        for k in sorted(e["env"]):
            cmd += ["--env", f"{k}={e['env'][k]}"]
        # `--` so args that look like flags survive verbatim.
        cmd += [e["name"], "--", e["command"], *e["args"]]
    return cmd


def _to_agy(stored: dict[str, dict]) -> dict:
    out = {}
    for name, e in sorted(stored.items()):
        if e["kind"] == KIND_REMOTE:
            entry = {"serverUrl": e["url"]}
            if e["headers"]:
                entry["headers"] = e["headers"]
        else:
            entry = {"command": e["command"]}
            if e["args"]:
                entry["args"] = e["args"]
            if e["env"]:
                entry["env"] = e["env"]
        out[name] = entry
    return out


def _to_opencode(stored: dict[str, dict]) -> dict:
    out = {}
    for name, e in sorted(stored.items()):
        if e["kind"] == KIND_REMOTE:
            entry = {"type": "remote", "url": e["url"],
                     "enabled": bool(e.get("enabled", True))}
            if e["headers"]:
                entry["headers"] = e["headers"]
        else:
            entry = {"type": "local",
                     "command": [e["command"], *e["args"]],
                     "enabled": bool(e.get("enabled", True))}
            if e["env"]:
                entry["environment"] = e["env"]
        out[name] = entry
    return out


def _same(landed: dict, wanted: dict, toggle: bool = False) -> bool:
    keys = ["kind", "command", "args", "env", "url", "headers", "transport"]
    if toggle:
        keys.append("enabled")
    return all(landed.get(k) == wanted.get(k) for k in keys)


# Logging stays name-only: values never appear, even in debug lines.
def log_event_added(backend: str, name: str) -> None:
    logger.info(f"[CONNECTORS] added '{name}' on {backend}")


def log_event_removed(backend: str, name: str) -> None:
    logger.info(f"[CONNECTORS] removed '{name}' on {backend}")


def log_event_toggled(backend: str, name: str, enabled: bool) -> None:
    logger.info(f"[CONNECTORS] {'enabled' if enabled else 'disabled'} "
                f"'{name}' on {backend}")


# ══════════════════════════════════════════════════════════
#  THE MATRIX  (connector × backend, rendered truthfully)
# ══════════════════════════════════════════════════════════

def matrix(backends: list[str] | None = None) -> list[dict]:
    """One row per connector the owner could care about, one cell per
    backend: 'native' (sign in inside the app), 'mcp' (configured via its
    MCP config), '' (nothing here). Built ONLY from what is actually
    configured or natively offered — never invented."""
    names = [b for b in (backends or BACKENDS) if b in BACKENDS]
    configured = {b: list_servers(b) for b in names}
    rows: dict[str, dict] = {}

    for b in names:
        for n in natives(b):
            row = rows.setdefault(n["name"], {"name": n["name"], "cells": {}})
            row["cells"][b] = "native"
        for server_name in sorted(configured[b]):
            row = rows.setdefault(server_name, {"name": server_name, "cells": {}})
            if not row["cells"].get(b):
                row["cells"][b] = "mcp"

    out = []
    for name in sorted(rows):
        row = rows[name]
        # A native capability on a backend whose CLI isn't installed is
        # not available on this machine — say nothing rather than promise.
        row["cells"] = {b: c for b, c in row["cells"].items() if c}
        out.append(row)
    return out


def hint_backends(keyword: str, backends: list[str] | None = None) -> list[str]:
    """Which INSTALLED backends cover this keyword (a connector name, e.g.
    'drive', 'gmail', or an MCP server name)? C2 step 3's routing seam —
    unique coverage means the turn belongs there."""
    kw = re.sub(r"[^a-z0-9]", "", (keyword or "").strip().lower())
    if not kw:
        return []
    names = [b for b in (backends or BACKENDS) if b in BACKENDS]
    hit: list[str] = []
    for b in names:
        words = set()
        for n in natives(b):
            words.add(re.sub(r"[^a-z0-9]", "", n["id"]))
            words.update(n.get("words") or ())
        if kw and kw in words:
            hit.append(b)
            continue
        if kw in {re.sub(r"[^a-z0-9]", "", s.lower())
                  for s in list_servers(b)}:
            hit.append(b)
    return hit


# ══════════════════════════════════════════════════════════
#  COPY  (owner-facing; STYLE.md — calm, no jargon, R3)
# ============================================================

_CELL_WORD = {"native": "built in — sign in inside the app",
              "mcp": "connected"}


def matrix_text(rows: list[dict], notes: dict | None = None) -> str:
    """Body of the Connectors panel. `notes` maps backend → one line of
    honest status (e.g. 'not installed'). Native rows say where to sign
    in; they never claim to be connected."""
    parts: list[str] = []
    for row in rows:
        cells = row["cells"]
        if not cells:
            continue
        line_bits = []
        for b, cell in cells.items():
            word = _CELL_WORD.get(cell, cell)
            if b == "agy" and cell == "native":
                word += " (in agy)"
            line_bits.append(word)
        parts.append(f"• {row['name']} — " + "; ".join(line_bits))
    if not parts:
        parts.append("Nothing connected yet. Add one and I'll be able to "
                     "reach more of your accounts.")
    if notes:
        status = ["Status:"] + [f"• {b} — {notes[b]}" for b, note in notes.items()]
        parts.append("\n".join(status))
    return "\n\n".join(parts)


def added_line(entry: dict, backend: str) -> str:
    label = {"claude": "Claude", "agy": "agy", "opencode": "opencode"}.get(
        backend, backend)
    return (f"✅ {entry['name']} is connected on {label}. "
            "It'll be available on your next message.")


def removed_line(name: str) -> str:
    return f"🗑 {name} is removed. Its tools are gone from that app."


def toggled_line(name: str, enabled: bool) -> str:
    state = "on" if enabled else "off — I won't reach for it"
    return f"{'✅' if enabled else '⏸'} {name} is {state}."
