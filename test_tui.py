# ============================================================
#  TESTS — zilla.tui (Phase 2 step 3: the full-screen TUI)
# ============================================================
#  Deterministic, no-network tests for the Textual app, driven headlessly
#  via Textual's own test harness (App.run_test() / Pilot, size=(80, 24) —
#  the "normal 80x24 terminal" the quality bar asks for). The backend
#  (zilla.core's run_cli_async) is monkeypatched exactly like test_core.py
#  — these NEVER invoke a real CLI turn. The bridge/health probes are
#  exercised through the same real, tested code paths test_core.py uses
#  (interactive.write_ask + core._bridge_poll_once; monkeypatched
#  agy_reachable/agy_models_live/claude_identity), not by reaching into
#  Textual internals.
#
#  Run:  .venv/bin/python test_tui.py
#  Exit code 0 = all passed, 1 = something failed.
# ============================================================

import asyncio
import json
import os
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


# ── Isolate config BEFORE importing it (same pattern as test_core.py) ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_tui_test_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.SETTINGS_FILE = os.path.join(_tmpdir, "bot_settings.json")
config._settings_cache = None

import zilla.core as zcore  # noqa: E402
from zilla.core import ZillaCore  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402
import zilla.interactive as interactive  # noqa: E402

from zilla.tui.app import ZillaApp  # noqa: E402
from zilla.tui.logo import LOGO  # noqa: E402
from zilla.tui.widgets import ApprovalPrompt, ChatBubble  # noqa: E402

OWNER = 111


def _fresh_core(tag: str, **kw) -> ZillaCore:
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.json"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.json"), OWNER)
    return ZillaCore(sessions=sessions, auth=auth, **kw)


def _fresh_bridge_core(tag: str) -> tuple:
    bridge_dir = os.path.join(_tmpdir, f"bridge_{tag}")
    core = _fresh_core(tag, owner_chat_id=OWNER, bridge_dir=bridge_dir)
    return core, bridge_dir


def _app(core, user_id=OWNER, startup_hint=None) -> ZillaApp:
    return ZillaApp(core=core, user_id=user_id, startup_hint=startup_hint,
                    use_real_core=False)


class _patched_backend:
    """Monkeypatch zilla.core's run_cli_async / get_latest_step for one
    test — same technique test_core.py uses, so no real CLI ever runs."""

    def __init__(self, fake_run, latest_step=1):
        self.fake_run = fake_run
        self.latest_step = latest_step

    def __enter__(self):
        self._run, self._step = zcore.run_cli_async, zcore.get_latest_step
        zcore.run_cli_async = self.fake_run
        zcore.get_latest_step = lambda conv: self.latest_step
        return self

    def __exit__(self, *exc):
        zcore.run_cli_async, zcore.get_latest_step = self._run, self._step
        return False


class _patched_health:
    """Monkeypatch zilla.core's agy_reachable / agy_models_live /
    claude_identity for one test (mirrors test_core.py's _patched_health) —
    the Health screen must never shell out during tests."""

    def __enter__(self):
        self._orig = (zcore.agy_reachable, zcore.agy_models_live, zcore.claude_identity)
        zcore.agy_reachable = lambda: True
        zcore.agy_models_live = lambda force=False: ["Gemini 3.1 Pro (High)"]
        zcore.claude_identity = lambda force=False: {"loggedIn": True}
        return self

    def __exit__(self, *exc):
        zcore.agy_reachable, zcore.agy_models_live, zcore.claude_identity = self._orig
        return False


def _static_text(widget) -> str:
    """Read back what was passed to a Static widget — Textual 8.x renamed
    the old public `.renderable` to a name-mangled private attribute, so
    this tries both for resilience across versions."""
    for attr in ("renderable", "_Static__content"):
        if hasattr(widget, attr):
            return str(getattr(widget, attr))
    return str(widget.render())  # pragma: no cover - last resort


# ── 1. Home screen — ASCII logo + a visible, focused prompt box ────

def test_home_renders_logo():
    print("\n[1] Home screen — ASCII ZILLA logo + a visible, focused prompt box")
    core = _fresh_core("home")
    app = _app(core)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            logo = screen.query_one("#logo")
            prompt = screen.query_one("#home-prompt")
            return screen.__class__.__name__, _static_text(logo), prompt.has_focus

    screen_name, logo_text, prompt_focused = asyncio.run(run())
    check("home screen is the first screen shown", screen_name == "HomeScreen")
    check("logo renders the ZILLA wordmark", logo_text == LOGO, f"got {logo_text!r}")
    check("prompt box is focused on open", prompt_focused)


# ── 2. Home -> Chat: typing + Enter sends the first message ────────
# ── 2b. Message round-trip with the backend monkeypatched ──────────

def test_home_to_chat_round_trip():
    print("\n[2] Home prompt -> Chat: first message sent, monkeypatched backend replies")
    core = _fresh_core("roundtrip")
    app = _app(core)

    async def fake_run(prompt, conv_id, progress_callback=None,
                       cancel_event=None, skip_permissions=False, ctx=None):
        if progress_callback:
            progress_callback("Reading files…")
        return f"Echo: {prompt}", "conv-tui-1"

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            home = app.screen
            prompt = home.query_one("#home-prompt")
            prompt.value = "hello zilla"
            with _patched_backend(fake_run):
                await pilot.press("enter")
                # let the async turn worker run to completion
                for _ in range(20):
                    await pilot.pause()
                    bubbles = app.get_screen("chat").query(ChatBubble)
                    if len(bubbles) >= 2:
                        break
            chat = app.get_screen("chat")
            bubbles = list(chat.query(ChatBubble))
            return app.screen.__class__.__name__, [(b._role, b._text) for b in bubbles]

    screen_name, bubbles = asyncio.run(run())
    check("navigated to the chat screen", screen_name == "ChatScreen")
    check("exactly 2 bubbles (user + zilla)", len(bubbles) == 2, f"got {bubbles}")
    if len(bubbles) == 2:
        check("first bubble is the user's message",
              bubbles[0] == ("user", "hello zilla"), f"got {bubbles[0]}")
        check("second bubble is the backend's response",
              bubbles[1] == ("zilla", "Echo: hello zilla"), f"got {bubbles[1]}")


# ── 3. Ask event renders inline and can be answered ─────────────────

def test_ask_event_renders_and_answers():
    print("\n[3] Ask event (bridge/OTP) renders inline and can be answered")
    core, bridge_dir = _fresh_bridge_core("ask")
    app = _app(core)

    ask = interactive.make_ask("otp", "Enter the code sent to your phone", chat_id=OWNER)
    interactive.write_ask(ask, bridge_dir=bridge_dir)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.action_goto("chat")
            await pilot.pause()
            chat = app.screen
            await core._bridge_poll_once()
            for _ in range(20):
                await pilot.pause()
                if chat._pending_ask is not None:
                    break
            inp = chat.query_one("#chat-input")
            pending_before = chat._pending_ask
            masked_while_pending = inp.password
            chat.answer_pending_ask("123456")
            await pilot.pause()
            return pending_before, masked_while_pending, chat._pending_ask, inp.password

    pending_before, masked, pending_after, masked_after = asyncio.run(run())
    check("Ask arrived and is pending", pending_before is not None and pending_before[0] == ask.id,
          f"got {pending_before}")
    check("otp kind masks the input", masked is True)
    check("answering clears the pending ask", pending_after is None)
    check("input unmasked again after answering", masked_after is False)
    check("the answer was actually written to the bridge",
          interactive.read_answer(ask.id, bridge_dir=bridge_dir) == "123456")


# ── 4. Screens switch via the F-key actions ─────────────────────────

def test_screens_switch():
    print("\n[4] Screens switch — Home / Chat / Settings / Skills / Health")
    core = _fresh_core("switch")
    app = _app(core)

    async def run():
        seen = []
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            seen.append(app.screen.__class__.__name__)
            for name in ("chat", "settings", "skills", "health", "home"):
                if name == "health":
                    with _patched_health():
                        app.action_goto(name)
                        await pilot.pause()
                else:
                    app.action_goto(name)
                    await pilot.pause()
                seen.append(app.screen.__class__.__name__)
        return seen

    seen = asyncio.run(run())
    check("visited every screen in order",
          seen == ["HomeScreen", "ChatScreen", "SettingsScreen", "SkillsScreen",
                   "HealthScreen", "HomeScreen"],
          f"got {seen}")


# ── 5. No config -> friendly hint, never a crash ─────────────────────

def test_no_core_shows_friendly_hint_not_a_crash():
    print("\n[5] No core / no config — friendly hint shown, app stays alive")
    hint = "No AI CLI found (agy or claude). Install one and log in, then relaunch."
    app = _app(core=None, startup_hint=hint)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            home_hint = _static_text(app.screen.query_one("#home-hint"))
            app.action_goto("chat")
            await pilot.pause()
            chat = app.screen
            bubbles = list(chat.query(ChatBubble))
            # sending a message with no core must not crash either
            chat.send_message("hi")
            await pilot.pause()
            bubbles_after = list(chat.query(ChatBubble))
            return home_hint, [(b._role, b._text) for b in bubbles], \
                [(b._role, b._text) for b in bubbles_after]

    home_hint, bubbles_on_mount, bubbles_after_send = asyncio.run(run())
    check("home screen shows the friendly hint", hint in home_hint, f"got {home_hint!r}")
    check("chat screen shows the hint instead of crashing on mount",
          any(role == "error" for role, _ in bubbles_on_mount), f"got {bubbles_on_mount}")
    check("sending a message with no core degrades to an error bubble, no crash",
          any(role == "error" for role, _ in bubbles_after_send), f"got {bubbles_after_send}")


# ── 6. ApprovalRequest renders inline with Approve/Deny ──────────────

def test_approval_request_renders():
    print("\n[6] ApprovalRequest (a limited user's held request) renders inline")
    core = _fresh_core("approval", owner_chat_id=OWNER)
    app = _app(core)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.action_goto("chat")
            await pilot.pause()
            rid = core.approvals.submit(uid=222, chat_id=222, prompt="buy 3 widgets", name="Bob")
            for _ in range(20):
                await pilot.pause()
                prompts = app.screen.query(ApprovalPrompt)
                if prompts:
                    break
            prompts = app.screen.query(ApprovalPrompt)
            return rid, [p.request_id for p in prompts]

    rid, ids = asyncio.run(run())
    check("approval request rendered inline", rid in ids, f"rid={rid} ids={ids}")


def main():
    tests = [
        test_home_renders_logo,
        test_home_to_chat_round_trip,
        test_ask_event_renders_and_answers,
        test_screens_switch,
        test_no_core_shows_friendly_hint_not_a_crash,
        test_approval_request_renders,
    ]
    print("Running zilla.tui tests...\n")
    for t in tests:
        try:
            t()
        except Exception as e:
            global _failed
            _failed += 1
            print(f"  ERROR {getattr(t, '__name__', t)}: {e!r}")
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
