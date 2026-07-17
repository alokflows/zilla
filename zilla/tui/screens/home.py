"""Home screen — centered ASCII-art ZILLA logo + a visible prompt box.

Owner decree (HANDOFF.md Phase 2): "the terminal app should look like a
real product the moment it opens". Typing here and hitting Enter takes the
user straight into the chat with that first message already sent.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Middle
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from zilla.tui.logo import LOGO, TAGLINE


class HomeScreen(Screen):

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                yield Static(LOGO, id="logo")
            with Center():
                yield Static(TAGLINE, id="tagline")
            with Center():
                yield Static("", id="home-hint")
            with Center():
                yield Input(
                    placeholder="Ask Zilla anything… (Enter to start chatting)",
                    id="home-prompt",
                )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#home-prompt", Input).focus()
        hint = getattr(self.app, "startup_hint", None)
        if hint:
            self.query_one("#home-hint", Static).update(hint)

    def on_screen_resume(self) -> None:
        self.query_one("#home-prompt", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        await self.app.open_chat(text or None)
