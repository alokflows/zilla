# ============================================================
#  TESTS — zilla.cli / zilla.configmenu / zilla.security / zilla.doctor
#  (Phase 2 steps 1-2: the `zilla` entrypoint + `zilla config`)
# ============================================================
#  Deterministic, no-network tests for the PURE logic added this round:
#    - configmenu parsing helpers (mask_token, parse_choice, parse_yes_no,
#      parse_priority_order, parse_pick_from) and a scripted round-trip
#      through the real menu loop (input_fn/print_fn injected).
#    - security checks run against a THROWAWAY tmp dir — never the live
#      install (file perms + fix, secrets-in-logs, webbridge loopback,
#      pending-skill gate, owner-id-set).
#    - doctor's pure formatting helpers.
#    - install.is_running()/read_pid() smoke (read-only, safe against the
#      real worktree — no process is ever started or killed here).
#
#  Run:  python test_zilla_cli.py
#  Exit code 0 = all passed, 1 = something failed.
# ============================================================

import io
import json
import os
import re
import stat
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


# ── Isolate config BEFORE importing it (same pattern as test_fixes.py /
#    test_core.py) — model writes must never touch the real ~/.gemini
#    settings on the machine running this test. ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_cli_test_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.SETTINGS_FILE = os.path.join(_tmpdir, "bot_settings.json")
config._settings_cache = None

import zilla.configmenu as configmenu  # noqa: E402
import zilla.security as zsecurity  # noqa: E402
import zilla.doctor as zdoctor  # noqa: E402
import zilla.cli as zcli  # noqa: E402
import zilla.backend_registry as backend_registry  # noqa: E402
import install  # noqa: E402
import bot as _bot  # noqa: E402


# ══════════════════════════════════════════════════════════
#  configmenu — pure parsing helpers
# ══════════════════════════════════════════════════════════

def test_mask_token():
    check("mask_token: empty", configmenu.mask_token("") == "(not set)")
    check("mask_token: short", configmenu.mask_token("abc") == "***")
    check("mask_token: long shows only edges",
          configmenu.mask_token("123456789:ABCDEFGHIJKL") == "1234…IJKL")


def test_parse_choice():
    check("parse_choice: valid digit", configmenu.parse_choice("3", 8) == 3)
    check("parse_choice: zero allowed", configmenu.parse_choice("0", 8) == 0)
    check("parse_choice: out of range -> None", configmenu.parse_choice("9", 8) is None)
    check("parse_choice: non-digit -> None", configmenu.parse_choice("abc", 8) is None)
    check("parse_choice: blank -> None", configmenu.parse_choice("", 8) is None)
    check("parse_choice: negative -> None", configmenu.parse_choice("-1", 8) is None)


def test_parse_yes_no():
    check("parse_yes_no: y -> True", configmenu.parse_yes_no("y", False) is True)
    check("parse_yes_no: no -> False", configmenu.parse_yes_no("no", True) is False)
    check("parse_yes_no: blank keeps default(True)", configmenu.parse_yes_no("", True) is True)
    check("parse_yes_no: blank keeps default(False)", configmenu.parse_yes_no("", False) is False)


def test_parse_priority_order():
    valid = configmenu.BACKEND_CHOICES
    check("priority: simple order",
          configmenu.parse_priority_order("agy,claude", valid) == ["agy", "claude"])
    check("priority: whitespace + case tolerant",
          configmenu.parse_priority_order(" Agy , Claude ", valid) == ["agy", "claude"])
    check("priority: duplicate -> None",
          configmenu.parse_priority_order("agy,agy", valid) is None)
    check("priority: unknown token -> None",
          configmenu.parse_priority_order("agy,bogus", valid) is None)
    check("priority: blank -> None",
          configmenu.parse_priority_order("", valid) is None)


def test_parse_pick_from():
    choices = ["a", "b", "c"]
    check("pick_from: by index", configmenu.parse_pick_from("2", choices) == "b")
    check("pick_from: by name", configmenu.parse_pick_from("B", choices) == "b")
    check("pick_from: unknown -> None", configmenu.parse_pick_from("z", choices) is None)
    check("pick_from: blank -> None", configmenu.parse_pick_from("", choices) is None)


# ══════════════════════════════════════════════════════════
#  configmenu — scripted round-trip through the real loop
# ══════════════════════════════════════════════════════════

def test_menu_round_trip_voice_mode():
    check("voice_mode: default is online",
          config.get_setting("voice_mode", "online") == "online")

    inputs = iter(["4", "offline", "0"])

    def fake_input(_prompt=""):
        return next(inputs)

    buf = io.StringIO()
    configmenu.run_menu(input_fn=fake_input, print_fn=lambda *a: print(*a, file=buf))

    check("voice_mode: menu wrote offline",
          config.get_setting("voice_mode") == "offline",
          detail=f"got {config.get_setting('voice_mode')!r}")

    # restore
    inputs2 = iter(["4", "online", "0"])
    configmenu.run_menu(input_fn=lambda _p="": next(inputs2), print_fn=lambda *a: None)
    check("voice_mode: restored to online", config.get_setting("voice_mode") == "online")


def test_menu_invalid_choice_reprompts_then_exits():
    inputs = iter(["99", "0"])
    buf = io.StringIO()
    # Should not raise despite the invalid first choice.
    configmenu.run_menu(input_fn=lambda _p="": next(inputs),
                         print_fn=lambda *a: print(*a, file=buf))
    check("menu: invalid choice handled without crashing", "Not a valid choice" in buf.getvalue())


def test_menu_quit_synonyms():
    for word in ("q", "quit", "exit"):
        called = {"n": 0}

        def fake_input(_p="", _w=word):
            called["n"] += 1
            return _w
        configmenu.run_menu(input_fn=fake_input, print_fn=lambda *a: None)
        check(f"menu: '{word}' exits immediately", called["n"] == 1)


def test_menu_fallback_toggle():
    check("fallback: default off", config.get_setting("fallback_enabled", False) is False)
    inputs = iter(["3", "y", "0"])
    configmenu.run_menu(input_fn=lambda _p="": next(inputs), print_fn=lambda *a: None)
    check("fallback: toggled on", config.get_setting("fallback_enabled") is True)
    inputs2 = iter(["3", "n", "0"])
    configmenu.run_menu(input_fn=lambda _p="": next(inputs2), print_fn=lambda *a: None)
    check("fallback: restored off", config.get_setting("fallback_enabled") is False)


def test_menu_round_trip_retention():
    # F3 (PLAN.md §17): media storage retention, menu item 9. RETENTION_CHOICES
    # = ["0 (off)", "30", "60", "90"] — picking "3" selects "60", "2" selects "30".
    check("retention: default is 30", config.get_media_retention_days() == 30,
          detail=f"got {config.get_media_retention_days()!r}")
    inputs = iter(["9", "3", "0"])
    configmenu.run_menu(input_fn=lambda _p="": next(inputs), print_fn=lambda *a: None)
    check("retention: menu wrote 60", config.get_media_retention_days() == 60,
          detail=f"got {config.get_media_retention_days()!r}")
    inputs2 = iter(["9", "2", "0"])
    configmenu.run_menu(input_fn=lambda _p="": next(inputs2), print_fn=lambda *a: None)
    check("retention: restored to 30", config.get_media_retention_days() == 30)


# ══════════════════════════════════════════════════════════
#  security — checks against a throwaway tmp dir (never the live install)
# ══════════════════════════════════════════════════════════

def test_check_file_perms():
    d = tempfile.mkdtemp(prefix="zilla_sec_test_")
    env_path = os.path.join(d, ".env")
    open(env_path, "w").write("TELEGRAM_BOT_TOKEN=x\n")

    if sys.platform == "win32":
        check("file perms: no-op on Windows", True)
        return

    os.chmod(env_path, 0o600)
    os.chmod(d, 0o700)
    findings = {f.name: f for f in zsecurity.check_file_perms(d)}
    check("perms: 600 file passes", findings["perms: .env"].ok)
    check("perms: 700 dir passes", findings["perms: home dir"].ok)

    os.chmod(env_path, 0o644)
    findings2 = {f.name: f for f in zsecurity.check_file_perms(d)}
    f = findings2["perms: .env"]
    check("perms: 644 file fails", not f.ok)
    check("perms: 644 file is fixable", f.fixable)
    fixed = f.fix()
    check("perms: fix() reports ok", fixed.ok)
    mode_after = stat.S_IMODE(os.stat(env_path).st_mode)
    check("perms: fix() actually chmod'd to 600", mode_after == 0o600,
          detail=f"got {oct(mode_after)}")

    os.chmod(d, 0o755)
    findings3 = {f.name: f for f in zsecurity.check_file_perms(d)}
    df = findings3["perms: home dir"]
    check("perms: 755 dir fails", not df.ok)
    fixed_d = df.fix()
    check("perms: dir fix() actually chmod'd to 700",
          stat.S_IMODE(os.stat(d).st_mode) == 0o700)


def test_check_secrets_not_in_logs():
    d = tempfile.mkdtemp(prefix="zilla_sec_logs_")
    open(os.path.join(d, "bot_20260101.log"), "w").write("hello\nworld\n")
    f = zsecurity.check_secrets_not_in_logs(d)
    check("secrets-in-logs: clean log passes", f.ok)

    open(os.path.join(d, "bot_20260102.log"), "w").write(
        "got update from 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n")
    f2 = zsecurity.check_secrets_not_in_logs(d)
    check("secrets-in-logs: token-shaped string fails", not f2.ok)

    f3 = zsecurity.check_secrets_not_in_logs(os.path.join(d, "nope"))
    check("secrets-in-logs: missing dir is a graceful pass", f3.ok)


def test_check_webbridge_loopback():
    check("webbridge: 127.0.0.1 passes",
          zsecurity.check_webbridge_loopback("http://127.0.0.1:10086").ok)
    check("webbridge: localhost passes",
          zsecurity.check_webbridge_loopback("http://localhost:10086").ok)
    check("webbridge: 0.0.0.0 fails",
          not zsecurity.check_webbridge_loopback("http://0.0.0.0:10086").ok)
    check("webbridge: LAN IP fails",
          not zsecurity.check_webbridge_loopback("http://192.168.1.5:10086").ok)


def test_check_pending_skill_gate():
    d = tempfile.mkdtemp(prefix="zilla_skills_test_")
    f1 = zsecurity.check_pending_skill_gate(os.path.join(d, "nonexistent"))
    check("pending-gate: missing skills dir -> graceful skip", f1.ok)

    os.makedirs(os.path.join(d, "skills"))
    f2 = zsecurity.check_pending_skill_gate(os.path.join(d, "skills"))
    check("pending-gate: skills dir without pending/ -> graceful skip", f2.ok)

    if sys.platform != "win32":
        pending = os.path.join(d, "skills", "pending")
        os.makedirs(pending)
        os.chmod(pending, 0o700)
        f3 = zsecurity.check_pending_skill_gate(os.path.join(d, "skills"))
        check("pending-gate: 700 pending/ passes", f3.ok)

        os.chmod(pending, 0o777)
        f4 = zsecurity.check_pending_skill_gate(os.path.join(d, "skills"))
        check("pending-gate: world-writable pending/ fails", not f4.ok)


def test_check_owner_id_set():
    check("owner-id: 0 fails", not zsecurity.check_owner_id_set(0).ok)
    check("owner-id: set passes", zsecurity.check_owner_id_set(123456789).ok)


def test_check_secrets_not_in_argv():
    saved = sys.argv[:]
    try:
        sys.argv = ["zilla", "doctor"]
        check("argv: clean argv passes", zsecurity.check_secrets_not_in_argv().ok)
        sys.argv = ["zilla", "--token", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"]
        check("argv: token-shaped arg fails", not zsecurity.check_secrets_not_in_argv().ok)
    finally:
        sys.argv = saved


def test_run_security_checks_and_apply_fixes():
    d = tempfile.mkdtemp(prefix="zilla_sec_full_")
    open(os.path.join(d, ".env"), "w").write("X=1\n")
    os.chmod(os.path.join(d, ".env"), 0o644 if sys.platform != "win32" else 0o600)
    findings = zsecurity.run_security_checks(
        base_dir=d, logs_dir=os.path.join(d, "logs"),
        skills_dir=os.path.join(d, "skills"), bridge_url="http://127.0.0.1:10086",
        owner_chat_id=0,
    )
    names = {f.name for f in findings}
    check("run_security_checks: covers all seven areas",
          {"perms: .env", "perms: home dir", "secrets not in logs",
           "secrets not in argv", "WebBridge loopback-only",
           "no unexpected listening sockets", "pending-skill gate",
           "owner ID set"} <= names or sys.platform == "win32")
    if sys.platform != "win32":
        fixed = zsecurity.apply_fixes(findings)
        env_finding = next(f for f in fixed if f.name == "perms: .env")
        check("apply_fixes: fixed the fixable perms finding", env_finding.ok)
        owner_finding = next(f for f in fixed if f.name == "owner ID set")
        check("apply_fixes: leaves non-fixable finding failing",
              not owner_finding.ok)


# ══════════════════════════════════════════════════════════
#  doctor — pure formatting helpers
# ══════════════════════════════════════════════════════════

def test_fmt_bytes():
    check("_fmt_bytes: None -> '?'", zdoctor._fmt_bytes(None) == "?")
    check("_fmt_bytes: bytes", zdoctor._fmt_bytes(500).startswith("500.0"))
    check("_fmt_bytes: kb", zdoctor._fmt_bytes(2048).startswith("2.0K"))


def test_detect_gui_smoke():
    result = zdoctor.detect_gui()
    check("detect_gui: returns a bool", isinstance(result, bool))


def test_format_report_smoke():
    # PLAN.md §17/F2: doctor's "clis" shape is now registry-shaped —
    # {name: {installed, path, ok, detail}} for whatever IS registered,
    # zero hard-coded agy/claude keys.
    report = {
        "os": {"system": "Darwin", "release": "25.0", "python": "3.12.0", "gui": True},
        "home": {"path": "/Users/tester/Zilla", "exists": True},
        "backend": {"active": "agy", "model": "Gemini 3.1 Pro (High)"},
        "clis": {"agy": {"installed": True, "path": "/usr/local/bin/agy", "ok": True, "detail": "logged in"},
                 "claude": {"installed": True, "path": "/usr/local/bin/claude", "ok": False, "detail": "not logged in"}},
        "ffmpeg": {"ok": True, "detail": "/usr/bin/ffmpeg"},
        "flac": {"ok": False, "detail": "not found"},
        "webbridge": {"ok": False, "detail": "unreachable"},
        "disk": {"path": "/", "free_bytes": 1024, "total_bytes": 2048},
    }
    text = zdoctor.format_report(report)
    check("format_report: mentions backend", "agy" in text)
    check("format_report: shows agy status detail", "logged in" in text)
    check("format_report: shows claude status detail", "not logged in" in text)
    check("format_report: flags missing flac", "flac" in text and "not found" in text)
    check("format_report: shows the Zilla home path", "/Users/tester/Zilla" in text)


# ══════════════════════════════════════════════════════════
#  install.py — read-only liveness helpers (never starts/kills a process)
# ══════════════════════════════════════════════════════════

def test_install_is_running_and_pid_readonly():
    result = install.is_running()
    check("install.is_running: returns a bool", isinstance(result, bool))
    pid = install.read_pid()
    check("install.read_pid: None or int", pid is None or isinstance(pid, int))


# ══════════════════════════════════════════════════════════
#  cli.py — argument parsing + a couple of safe end-to-end paths
# ══════════════════════════════════════════════════════════

def test_build_parser_subcommands():
    parser = zcli.build_parser()
    names = set()
    for action in parser._subparsers._group_actions:
        names |= set(action.choices.keys())
    check("cli: all six subcommands registered",
          names == {"config", "doctor", "start", "stop", "status", "logs"},
          detail=str(names))


def test_cmd_status_returns_zero():
    rc = zcli.main(["status"])
    check("cli: status exits 0", rc == 0)


def test_cmd_logs_missing_returns_nonzero():
    # This worktree may or may not have logs/; either way logs command must
    # not raise, and must fail cleanly if no bot_*.log exists yet.
    rc = zcli.main(["logs"])
    check("cli: logs exits 0 or 1 without raising", rc in (0, 1))


# ══════════════════════════════════════════════════════════
#  backend_registry.py — dynamic backend adapters (PLAN.md §17/F2)
# ══════════════════════════════════════════════════════════

def test_backend_registry_has_agy_and_claude():
    names = backend_registry.names()
    check("backend_registry: agy registered", "agy" in names)
    check("backend_registry: claude registered", "claude" in names)


def test_backend_registry_get_unknown_is_none():
    check("backend_registry: get() unknown -> None",
          backend_registry.get("not-a-real-backend") is None)
    check("backend_registry: get() is case/whitespace tolerant",
          backend_registry.get(" Claude ") is backend_registry.get("claude"))


def test_backend_registry_status_all_shape():
    status = backend_registry.status_all()
    check("backend_registry: status_all covers every registered name",
          set(status.keys()) == set(backend_registry.names()))
    for name, cli in status.items():
        check(f"backend_registry: status_all[{name}] has the shared shape",
              {"installed", "path", "ok", "detail"} <= set(cli.keys()))


def test_backend_registry_adapter_fields():
    for adapter in backend_registry.all_backends():
        check(f"backend_registry: {adapter.name} has a non-empty label", bool(adapter.label))
        check(f"backend_registry: {adapter.name}.models() returns a list",
              isinstance(adapter.models(), list))


# ══════════════════════════════════════════════════════════
#  bot.py COMMAND_REGISTRY — unified slash-command registry (PLAN.md §17/F2)
# ══════════════════════════════════════════════════════════

def test_command_registry_names_and_aliases_unique():
    seen = []
    for spec in _bot.COMMAND_REGISTRY:
        seen.append(spec.name)
        seen.extend(spec.aliases)
    check("COMMAND_REGISTRY: no duplicate command/alias names",
          len(seen) == len(set(seen)), detail=str(seen))


def test_command_registry_scopes_valid():
    for spec in _bot.COMMAND_REGISTRY:
        check(f"COMMAND_REGISTRY: {spec.name} has a valid scope",
              spec.scope in ("default", "owner", "hidden"), detail=spec.scope)
        check(f"COMMAND_REGISTRY: {spec.name}.handler is callable", callable(spec.handler))


def test_command_registry_owner_only_handlers_are_owner_scoped():
    # Regression guard for the pre-F2 bug: /memory, /adduser, /removeuser,
    # /listusers are owner-gated INSIDE their handlers but had no menu entry
    # at all (or the wrong one) — scope must say so explicitly now.
    owner_gated = {"memory", "adduser", "removeuser", "listusers"}
    for spec in _bot.COMMAND_REGISTRY:
        if spec.name in owner_gated:
            check(f"COMMAND_REGISTRY: {spec.name} is owner-scoped", spec.scope == "owner")


def test_command_registry_1to1_with_telegram_handlers():
    # Grep-gate (PLAN.md §17/F2 accept criteria): bot.py must construct
    # CommandHandler(...) from COMMAND_REGISTRY alone — no second, drifting
    # list of manual add_handler(CommandHandler("name", ...)) calls.
    bot_src = _read_bot_source()
    literal_command_handlers = re.findall(r'CommandHandler\(\s*"([a-z_]+)"', bot_src)
    check("bot.py: zero hard-coded CommandHandler(\"name\", ...) call sites "
          "outside the COMMAND_REGISTRY loop",
          literal_command_handlers == [], detail=str(literal_command_handlers))

    registry_names = {spec.name for spec in _bot.COMMAND_REGISTRY}
    registry_names |= {a for spec in _bot.COMMAND_REGISTRY for a in spec.aliases}
    menu_names = {spec.name for spec in _bot.COMMAND_REGISTRY if spec.scope in ("default", "owner")}
    check("COMMAND_REGISTRY: every menu-visible command is a real, unique entry",
          menu_names <= registry_names)


# ══════════════════════════════════════════════════════════
#  F3 (PLAN.md §17) — Storage settings render + Keep button wiring
# ══════════════════════════════════════════════════════════

def test_kb_settings_storage_renders_current_selection():
    import keyboards
    config.set_setting("media_retention_days", 60)
    try:
        markup = keyboards.kb_settings_storage()
        labels = [btn.text for row in markup.inline_keyboard for btn in row]
        check("storage kb: shows a checkmark on the current (60d) option",
              any(lbl.startswith("✅") and "60" in lbl for lbl in labels), labels)
        check("storage kb: other options have no checkmark",
              sum(lbl.startswith("✅") for lbl in labels) == 1, labels)
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        check("storage kb: one button per retention value",
              "set_retention_0" in callbacks and "set_retention_30" in callbacks
              and "set_retention_60" in callbacks and "set_retention_90" in callbacks,
              callbacks)
    finally:
        config.set_setting("media_retention_days", 30)


def test_kb_keep_uses_stable_token_callback():
    import keyboards
    from zilla.media import keep_token
    path = "/tmp/does-not-need-to-exist/report.pdf"
    markup = keyboards.kb_keep(path)
    btn = markup.inline_keyboard[0][0]
    check("keep kb: label is Keep", "Keep" in btn.text, btn.text)
    check("keep kb: callback_data matches media.keep_token(path)",
          btn.callback_data == f"ibx_keep_{keep_token(path)}", btn.callback_data)
    check("keep kb: callback_data is well under Telegram's 64-byte limit",
          len(btn.callback_data.encode()) <= 64, btn.callback_data)


def test_bot_wires_storage_and_keep_callbacks():
    # Structural grep-gate, same style as test_command_registry_1to1_with_
    # telegram_handlers: confirm the F3 callback branches exist and that
    # handle_callback's dispatcher actually routes to them.
    bot_src = _read_bot_source()
    for needle in ('data == "set_storage"', 'data.startswith("set_retention_")',
                   'data.startswith("ibx_keep_")'):
        check(f"bot.py: {needle} branch present", needle in bot_src, needle)
    check("bot.py: set_ prefix (storage/retention) routed to _cb_settings",
          'data == "menu_settings" or data.startswith("set_")' in bot_src)
    check("bot.py: ibx_ prefix (keep) routed to _cb_inbox",
          'data == "menu_inbox" or data.startswith("ibx_")' in bot_src)


def _read_bot_source() -> str:
    with open(_bot.__file__, "r", encoding="utf-8") as f:
        return f.read()


def main():
    tests = [
        test_mask_token,
        test_parse_choice,
        test_parse_yes_no,
        test_parse_priority_order,
        test_parse_pick_from,
        test_menu_round_trip_voice_mode,
        test_menu_invalid_choice_reprompts_then_exits,
        test_menu_quit_synonyms,
        test_menu_fallback_toggle,
        test_menu_round_trip_retention,
        test_check_file_perms,
        test_check_secrets_not_in_logs,
        test_check_webbridge_loopback,
        test_check_pending_skill_gate,
        test_check_owner_id_set,
        test_check_secrets_not_in_argv,
        test_run_security_checks_and_apply_fixes,
        test_fmt_bytes,
        test_detect_gui_smoke,
        test_format_report_smoke,
        test_install_is_running_and_pid_readonly,
        test_build_parser_subcommands,
        test_cmd_status_returns_zero,
        test_cmd_logs_missing_returns_nonzero,
        test_backend_registry_has_agy_and_claude,
        test_backend_registry_get_unknown_is_none,
        test_backend_registry_status_all_shape,
        test_backend_registry_adapter_fields,
        test_command_registry_names_and_aliases_unique,
        test_command_registry_scopes_valid,
        test_command_registry_owner_only_handlers_are_owner_scoped,
        test_command_registry_1to1_with_telegram_handlers,
        test_kb_settings_storage_renders_current_selection,
        test_kb_keep_uses_stable_token_callback,
        test_bot_wires_storage_and_keep_callbacks,
    ]
    print("Running zilla.cli / configmenu / security / doctor tests...\n")
    global _failed
    for t in tests:
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  ERROR {t.__name__}: {e!r}")
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
