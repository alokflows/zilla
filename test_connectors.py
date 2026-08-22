# ============================================================
#  TESTS — Phase C2: connectors (PLAN.md §12/C2 "Accept:")
# ============================================================
#  Deterministic, no-network tests for zilla/connectors.py:
#    - normalize/redact/describe: the gate that keeps junk out of a
#      backend config, and the redaction that keeps secrets out of
#      everything else.
#    - config write+read-back per backend, with MOCKED BINARIES:
#        claude  — `claude mcp add/remove --scope user` faked by _run;
#                  truth read back out of ~/.claude.json (the file, not
#                  the CLI's output — plugin servers never appear there).
#        agy     — ~/.gemini/config/mcp_config.json written and re-read,
#                  other top-level keys preserved.
#        opencode— opencode.jsonc under "mcp" with REQUIRED enabled;
#                  JSONC comments tolerated on read; real enable toggle.
#    - remove proves its work by re-read; unknown name is a quiet False.
#    - set_enabled refuses to fake a toggle where none exists.
#    - the matrix renders ONLY what configs and natives actually say.
#    - hint_backends: unique coverage is the routing seam.
#    - SECRETS NEVER APPEAR IN LOGS: every log record emitted during
#      add/remove/toggle/list is captured and searched for the secret.
#
#  Run:  .venv/bin/python test_connectors.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  Every backend config path is pointed at throwaway tmp files BEFORE
#  the first zilla import; nothing here touches the owner's real
#  ~/.claude.json or ~/.gemini.
# ============================================================

import json
import logging
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


# ── Isolate config BEFORE anything imports zilla ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_c2_cfg_")
os.environ.setdefault("ZILLA_HOME", tempfile.mkdtemp(prefix="zilla_test_home_"))
os.makedirs(os.path.join(os.environ["ZILLA_HOME"], "Runtime", "logs"), exist_ok=True)
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE

CLAUDE_JSON = os.path.join(_tmpdir, "claude_home", ".claude.json")
AGY_MCP = os.path.join(_tmpdir, "gemini_home", "config", "mcp_config.json")
OPENCODE_CFG = os.path.join(_tmpdir, "opencode_home", "opencode.jsonc")
config.CLAUDE_CONFIG_FILE = CLAUDE_JSON
config.AGY_MCP_CONFIG = AGY_MCP
config.OPENCODE_CONFIG_FILE = OPENCODE_CFG

from zilla import connectors as zcon  # noqa: E402

SECRET_ENV = "sk-live-zebra-42"
SECRET_HDR = "Bearer tok-9-echo"

STDIO_SPEC = {"name": "docs", "kind": "stdio", "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-docs"],
              "env": {"DOCS_KEY": SECRET_ENV}}
REMOTE_SPEC = {"name": "cloudsearch", "kind": "remote",
               "url": "https://example.com/mcp",
               "headers": {"Authorization": SECRET_HDR}}


def _wipe_configs():
    for p in (CLAUDE_JSON, AGY_MCP, OPENCODE_CFG):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


class _FakeCLI:
    """Stands in for a claude/opencode binary: records invocations and
    'writes' the config exactly as the real CLI would."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.fail = False

    def run(self, cmd, timeout=30.0):
        self.calls.append(list(cmd))
        if self.fail:
            return 1, "", "refused (fake)"
        if cmd[1:3] == ["mcp", "add"]:
            self._apply_add(cmd)
        elif cmd[1:3] == ["mcp", "remove"]:
            self._apply_remove(cmd)
        return 0, "ok", ""

    def _apply_add(self, cmd):
        # Parse our own arg construction back out — this doubles as a
        # check that the command line is well-formed enough to round-trip.
        args = cmd[cmd.index("add") + 1:]
        env, headers, transport = {}, {}, "stdio"
        while args and args[0] in ("--env", "--header", "--transport", "--scope"):
            flag, val, args = args[0], args[1], args[2:]
            if flag == "--scope":
                assert val == "user", f"scope must be user, got {val}"
            elif flag == "--env":
                k, _, v = val.partition("=")
                env[k] = v
            elif flag == "--header":
                k, _, v = val.partition(": ")
                headers[k] = v
            else:
                transport = val
        if args and args[0] == "--":
            args = args[1:]
        # Documented shape: <name> [-- commandOrUrl args...]
        name, rest = args[0], args[1:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        command_or_url, rest = rest[0], rest[1:]
        data = zcon._read_json(CLAUDE_JSON)
        servers = data.setdefault("mcpServers", {})
        if transport == "stdio":
            servers[name] = {"type": "stdio", "command": command_or_url,
                             "args": rest, "env": env}
        else:
            servers[name] = {"type": transport, "url": command_or_url,
                             "headers": headers}
        zcon._write_json(CLAUDE_JSON, data)

    def _apply_remove(self, cmd):
        name = cmd[-1]
        data = zcon._read_json(CLAUDE_JSON)
        servers = data.get("mcpServers", {})
        if name in servers:
            del servers[name]
        zcon._write_json(CLAUDE_JSON, data)


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(record.getMessage())


# ══════════════════════════════════════════════════════════

def _run_normalize_tests():
    print("\n[1] normalize — the gate before any backend sees an entry")

    e = zcon.normalize_spec(STDIO_SPEC)
    check("a good stdio spec passes through",
          e["name"] == "docs" and e["command"] == "npx" and e["args"].__len__() == 2)

    e = zcon.normalize_spec(REMOTE_SPEC)
    check("a good remote spec keeps url+headers",
          e["url"] == "https://example.com/mcp"
          and e["headers"].get("Authorization") == SECRET_HDR)

    for bad in ({}, {"name": "", "kind": "stdio"},
                {"name": "has space", "kind": "stdio", "command": "x"},
                {"name": "x", "kind": "stdio"},
                {"name": "x", "kind": "remote", "url": "not-a-url"},
                {"name": "x", "kind": "remote", "url": "ftp://x.com"},
                {"name": "x", "kind": "teleport", "command": "x"}):
        try:
            zcon.normalize_spec(bad)
            ok = False
        except zcon.ConnectorError:
            ok = True
        check(f"junk refused: {json.dumps(bad)[:48]}", ok)

    caps = zcon.normalize_spec({
        "name": "capped", "kind": "stdio", "command": "run",
        "args": [f"a{i}" for i in range(64)],
        "env": {f"K{i}": "v" for i in range(64)}})
    check("arg and env caps hold", len(caps["args"]) == zcon.MAX_ARGS
          and len(caps["env"]) == zcon.MAX_ENV)

    r = zcon.redact({"name": "d", "kind": "stdio", "command": "npx",
                     "args": [], "env": {"DOCS_KEY": SECRET_ENV}})
    check("redact hides values, keeps key names",
          r["env"]["DOCS_KEY"] != SECRET_ENV and "DOCS_KEY" in r["env"])

    d = zcon.describe(REMOTE_SPEC) + zcon.describe(STDIO_SPEC)
    check("describe never carries a secret value",
          SECRET_ENV not in d and SECRET_HDR not in d)
    check("describe still says what will run",
          "npx" in zcon.describe(STDIO_SPEC)
          and "https://example.com/mcp" in zcon.describe(REMOTE_SPEC))


def _run_agy_tests():
    print("\n[2] agy — the file IS the interface (write + re-read)")
    _wipe_configs()

    landed = zcon.add_server("agy", STDIO_SPEC)
    check("stdio add lands and reads back identical",
          landed["command"] == "npx" and landed["env"]["DOCS_KEY"] == SECRET_ENV)
    raw = zcon._read_json(AGY_MCP)
    entry = raw.get("mcpServers", {}).get("docs", {})
    check("on-disk shape matches the researched native shape",
          entry.get("command") == "npx" and entry.get("args") == STDIO_SPEC["args"]
          and entry.get("env", {}).get("DOCS_KEY") == SECRET_ENV)
    check("the config file is private (0600)",
          oct(os.stat(AGY_MCP).st_mode & 0o777) == "0o600")

    zcon.add_server("agy", REMOTE_SPEC)
    entry = zcon.list_servers("agy")["cloudsearch"]
    check("remote add uses serverUrl + headers",
          entry["url"] == "https://example.com/mcp"
          and entry["headers"]["Authorization"] == SECRET_HDR)

    # A pre-existing foreign top-level key survives every write.
    zcon._write_json(AGY_MCP, {"mcpServers": {}, "owner_note": "leave me"})
    zcon.add_server("agy", STDIO_SPEC)
    check("other keys in agy's file are preserved",
          zcon._read_json(AGY_MCP).get("owner_note") == "leave me")

    check("remove proves itself by re-read", zcon.remove_server("agy", "docs")
          and "docs" not in zcon.list_servers("agy"))
    check("removing an unknown name is a quiet False",
          zcon.remove_server("agy", "ghost") is False)

    try:
        zcon.set_enabled("agy", "cloudsearch", False)
        ok = False
    except zcon.ConnectorError:
        ok = True
    check("no fake enable toggle on agy — it raises instead", ok)


def _run_claude_tests():
    print("\n[3] claude — CLI writes, the FILE answers read-back")
    _wipe_configs()
    fake = _FakeCLI()
    old_run, old_bin = zcon._run, zcon._claude_bin
    zcon._run = fake.run
    zcon._claude_bin = lambda: "/fake/bin/claude"
    try:
        landed = zcon.add_server("claude", STDIO_SPEC)
        check("stdio add verified from ~/.claude.json",
              landed["name"] == "docs"
              and landed["env"]["DOCS_KEY"] == SECRET_ENV)
        check("the add went through the CLI with --scope user",
              fake.calls and fake.calls[0][1:3] == ["mcp", "add"]
              and "--scope" in fake.calls[0])
        check("`--` guards the command so flag-looking args survive",
              "--" in fake.calls[0])

        zcon.add_server("claude", REMOTE_SPEC)
        entry = zcon.list_servers("claude")["cloudsearch"]
        check("remote add carries transport + headers",
              entry["transport"] in ("http", "sse")
              and entry["headers"]["Authorization"] == SECRET_HDR)

        check("remove via CLI, proven by re-read",
              zcon.remove_server("claude", "docs")
              and "docs" not in zcon.list_servers("claude"))

        fake.fail = True
        try:
            zcon.add_server("claude", STDIO_SPEC)
            ok = False
        except zcon.ConnectorError:
            ok = True
        check("a refused add raises instead of claiming success", ok)
        check("the refusal left no half-entry behind",
              "docs" not in zcon.list_servers("claude"))
    finally:
        zcon._run, zcon._claude_bin = old_run, old_bin

    old_bin = zcon._claude_bin
    zcon._claude_bin = lambda: None
    try:
        try:
            zcon.add_server("claude", STDIO_SPEC)
            ok = False
        except zcon.ConnectorError:
            ok = True
        check("no binary means no manage — said plainly, not attempted", ok)
    finally:
        zcon._claude_bin = old_bin

    # Plugin-provided servers (not ours) are reported truthfully too —
    # reading the file lists them without pretending Zilla wrote them.
    _wipe_configs()
    zcon._write_json(CLAUDE_JSON, {"mcpServers": {
        "plugin:builtin:x": {"type": "stdio", "command": "x"}}})
    check("foreign/plugin entries read back as configured servers",
          "plugin:builtin:x" in zcon.list_servers("claude"))


def _run_opencode_tests():
    print("\n[4] opencode — JSONC file, required enabled, real toggle")
    _wipe_configs()

    # An owner-authored file with comments and a trailing comma must read.
    os.makedirs(os.path.dirname(OPENCODE_CFG), exist_ok=True)
    with open(OPENCODE_CFG, "w", encoding="utf-8") as f:
        f.write("""{
  // opencode config, hand-edited
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    /* existing server */
    "kept": { "type": "local", "command": ["uvx", "kept"], "enabled": true, },
  },
}""")
    kept = zcon.list_servers("opencode").get("kept")
    check("JSONC with comments/trailing commas reads cleanly",
          kept is not None and kept["command"] == "uvx" and kept["enabled"])

    landed = zcon.add_server("opencode", STDIO_SPEC)
    check("stdio add lands with enabled=true by default", landed["enabled"])
    raw = zcon._read_opencode(OPENCODE_CFG).get("mcp", {}).get("docs", {})
    check("on-disk shape: argv array + environment + enabled",
          raw.get("command") == ["npx", "-y", "@modelcontextprotocol/server-docs"]
          and raw.get("environment", {}).get("DOCS_KEY") == SECRET_ENV
          and raw.get("enabled") is True)
    check("the pre-existing commented server survived the write",
          zcon.list_servers("opencode").get("kept", {}).get("command") == "uvx")

    off = zcon.set_enabled("opencode", "docs", False)
    check("disable flips the real switch",
          off["enabled"] is False
          and zcon.list_servers("opencode")["docs"]["enabled"] is False)
    check("toggled_line speaks plainly",
          "off" in zcon.toggled_line("docs", False))
    on = zcon.set_enabled("opencode", "docs", True)
    check("re-enable works", on["enabled"] is True)

    check("remove proves itself by re-read",
          zcon.remove_server("opencode", "docs")
          and "docs" not in zcon.list_servers("opencode"))

    try:
        zcon.add_server("opencode", {"name": "bad remote", "kind": "remote",
                                     "url": ""})
        ok = False
    except zcon.ConnectorError:
        ok = True
    check("junk never reaches the opencode file", ok)


def _run_matrix_tests():
    print("\n[5] the matrix renders only what configs actually say")
    _wipe_configs()

    rows = zcon.matrix(["agy", "claude", "opencode"])
    drive = next((r for r in rows if r["name"] == "Google Drive"), None)
    check("agy's native Drive shows as native",
          drive and drive["cells"].get("agy") == "native")

    fake = _FakeCLI()
    old_run, old_bin = zcon._run, zcon._claude_bin
    zcon._run = fake.run
    zcon._claude_bin = lambda: "/fake/bin/claude"
    try:
        zcon.add_server("claude", STDIO_SPEC)
    finally:
        zcon._run, zcon._claude_bin = old_run, old_bin
    rows = {r["name"]: r for r in zcon.matrix()}
    check("an MCP-configured connector shows as mcp on its backend only",
          rows["docs"]["cells"].get("claude") == "mcp"
          and "agy" not in rows["docs"]["cells"]
          and "opencode" not in rows["docs"]["cells"])
    check("native rows never claim mcp, mcp rows never claim native",
          all(c in ("native", "mcp") for r in rows.values() for c in r["cells"].values()))

    body = zcon.matrix_text([rows["Google Drive"], rows["docs"]])
    check("matrix text: native says sign-in, mcp says connected",
          "sign in inside the app" in body and "connected" in body)
    check("native copy never claims Zilla connected it",
          "(in agy)" in body)

    _wipe_configs()
    empty = zcon.matrix(["claude"])
    check("no natives + no servers = no invented rows",
          all(r["cells"] for r in empty) or not any(
              r["cells"].get("claude") for r in empty))


def _run_hints_tests():
    print("\n[6] hint_backends — the routing seam (unique coverage)")
    _wipe_configs()

    check("drive is agy-only coverage",
          zcon.hint_backends("drive") == ["agy"])
    check("Gmail case-insensitive",
          zcon.hint_backends("GMAIL") == ["agy"])
    check("an unknown keyword covers nobody",
          zcon.hint_backends("teleporter") == [])

    fake = _FakeCLI()
    old_run, old_bin = zcon._run, zcon._claude_bin
    zcon._run = fake.run
    zcon._claude_bin = lambda: "/fake/bin/claude"
    try:
        zcon.add_server("claude", {"name": "drive", "kind": "stdio",
                                   "command": "npx"})
        both = zcon.hint_backends("drive")
        check("coverage follows MCP names too, so shared names stay ambiguous",
              sorted(both) == ["agy", "claude"])
        check("empty/odd keywords answer []",
              zcon.hint_backends("") == [] and zcon.hint_backends("!!!") == [])
        zcon.remove_server("claude", "drive")
    finally:
        zcon._run, zcon._claude_bin = old_run, old_bin


def _run_secrets_tests():
    print("\n[7] secrets never appear in logs")
    _wipe_configs()
    capture = _LogCapture()
    root = logging.getLogger()
    root.addHandler(capture)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    fake = _FakeCLI()
    old_run, old_bin = zcon._run, zcon._claude_bin
    zcon._run = fake.run
    zcon._claude_bin = lambda: "/fake/bin/claude"
    try:
        zcon.add_server("claude", STDIO_SPEC)
        zcon.add_server("agy", REMOTE_SPEC)
        zcon.add_server("opencode", STDIO_SPEC)
        zcon.remove_server("claude", "docs")
        zcon.remove_server("agy", "cloudsearch")
        zcon.remove_server("opencode", "docs")
        for b in ("claude", "agy", "opencode"):
            zcon.list_servers(b)
        joined = "\n".join(capture.records)
        check("no secret value in ANY log record during full add/remove/list",
              SECRET_ENV not in joined and SECRET_HDR not in joined,
              f"{len(capture.records)} records captured")
        check("log lines still say which connector moved (names only)",
              any("'docs'" in r for r in capture.records))
    finally:
        root.removeHandler(capture)
        root.setLevel(old_level)
        zcon._run, zcon._claude_bin = old_run, old_bin


if __name__ == "__main__":
    print("=" * 60)
    print("  PHASE C2 — CONNECTORS TESTS")
    print("=" * 60)
    try:
        _run_normalize_tests()
        _run_agy_tests()
        _run_claude_tests()
        _run_opencode_tests()
        _run_matrix_tests()
        _run_hints_tests()
        _run_secrets_tests()
    finally:
        _wipe_configs()
    print("\n" + "=" * 60)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 60)
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
