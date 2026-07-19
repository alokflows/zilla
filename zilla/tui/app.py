"""Zilla TUI — the bare `zilla` command's full-screen terminal app
(HANDOFF.md Phase 2 step 3). A thin frontend over zilla.core.ZillaCore, the
same core bot.py drives — see docs/dev/CORE_API.md.

`run()` is the entry point a `zilla` console script calls (wired up by a
parallel executor's CLI work this round); `python -m zilla.tui` calls the
same function via __main__.py.
"""

from __future__ import annotations

import logging

from textual.app import App
from textual.binding import Binding

from zilla.tui.core_setup import build_core, cli_hint
from zilla.tui.screens.chat import ChatScreen
from zilla.tui.screens.graph import GraphScreen
from zilla.tui.screens.health import HealthScreen
from zilla.tui.screens.home import HomeScreen
from zilla.tui.screens.settings import SettingsScreen
from zilla.tui.screens.skills import SkillsScreen

logger = logging.getLogger(__name__)


class ZillaApp(App):
    """Home -> Chat -> Settings -> Skills -> Health -> Graph, switched with
    the F-keys; q/ctrl+c quits cleanly (stops the core's background tasks
    first, so no bridge-watcher/scheduler task is left dangling)."""

    CSS_PATH = "app.tcss"
    TITLE = "Zilla"

    SCREENS = {
        "home": HomeScreen,
        "chat": ChatScreen,
        "settings": SettingsScreen,
        "skills": SkillsScreen,
        "health": HealthScreen,
        "graph": GraphScreen,
    }

    BINDINGS = [
        Binding("f1", "goto('home')", "Home"),
        Binding("f2", "goto('chat')", "Chat"),
        Binding("f3", "goto('settings')", "Settings"),
        Binding("f4", "goto('skills')", "Skills"),
        Binding("f5", "goto('health')", "Health"),
        Binding("f6", "goto('graph')", "Graph"),
        Binding("q", "quit_app", "Quit", priority=True),
        Binding("ctrl+c", "quit_app", "Quit", priority=True, show=False),
    ]

    def __init__(self, *, core=None, user_id: int = 0, startup_hint: str | None = None,
                use_real_core: bool = True):
        """use_real_core=True (the default, and what `run()` uses) builds a
        ZillaCore against the real config files via build_core(). Tests pass
        use_real_core=False to inject a fixture core (or None, to exercise
        the no-core/no-config friendly-hint path) without touching real
        state — see test_tui.py."""
        super().__init__()
        if use_real_core:
            self.core, self.user_id, self.startup_hint = build_core()
            if self.core is not None and self.startup_hint is None:
                self.startup_hint = cli_hint()
        else:
            self.core, self.user_id, self.startup_hint = core, user_id, startup_hint
        self._core_started = False

    async def on_mount(self) -> None:
        if self.core is not None:
            try:
                await self.core.start()
                self._core_started = True
            except Exception as e:  # never crash on startup — see HANDOFF quality bar
                logger.error(f"[TUI] core.start() failed: {e}")
                self.startup_hint = f"Background services didn't start: {e}"
        self.push_screen("home")

    async def open_chat(self, initial_message: str | None) -> None:
        """Called by HomeScreen when the owner types a first message and
        hits Enter — switches to Chat and sends it as the first turn. Waits
        for the screen switch to finish mounting before sending, so
        send_message()'s query_one("#chat-log") never races an empty
        not-yet-composed ChatScreen."""
        await self.switch_screen("chat")
        if initial_message:
            chat = self.get_screen("chat")
            chat.send_message(initial_message)

    def action_goto(self, screen_name: str) -> None:
        self.switch_screen(screen_name)

    async def action_quit_app(self) -> None:
        if self.core is not None and self._core_started:
            try:
                await self.core.stop()
            except Exception as e:  # pragma: no cover - defensive
                logger.error(f"[TUI] core.stop() error during quit: {e}")
        self.exit()


def run() -> None:
    """Launch the Zilla TUI. Blocks until the user quits."""
    ZillaApp().run()


if __name__ == "__main__":
    run()
