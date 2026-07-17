"""Settings screen — reads/writes the SAME .env + settings.json the core
reads (zilla/config.py — HANDOFF.md §3: "single source of truth ... never
two settings systems"). Covers the fields config.py actually supports
today: active backend, its model, voice mode, web mode. The Telegram token
and owner id are shown masked/read-only — editing secrets is onboarding's
job (HANDOFF.md Phase 2 step 4, a later step), not this screen's.

Note: HANDOFF.md's settings table lists a "backend priority order"
(agy/claude/opencode, ranked, with fallback). config.py only exposes a
single ACTIVE backend today (get_backend()/set_backend()) — the fallback
chain is Phase 8 (opencode backend). This screen honestly exposes what
exists rather than inventing a priority-list UI ahead of the data model.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Select, Static

from zilla import config

_VOICE_MODES = [("offline (local Whisper)", "offline"), ("online (Google)", "online")]
_WEB_MODES = [("headless (Playwright)", "headless"),
              ("my-browser (WebBridge)", "my-browser"),
              ("off", "off")]
_BACKENDS = [("agy", "agy"), ("claude", "claude")]


def _mask(secret: str) -> str:
    if not secret:
        return "not set"
    if len(secret) <= 4:
        return "•" * len(secret)
    return "•" * (len(secret) - 4) + secret[-4:]


class SettingsScreen(Screen):

    def __init__(self):
        super().__init__()
        self._loading = False  # guards refresh_values() from re-triggering on_select_changed

    def compose(self) -> ComposeResult:
        yield Static("Settings", classes="screen-title")
        with VerticalScroll():
            with Vertical(classes="settings-row"):
                yield Static("Backend", classes="settings-label")
                yield Select(_BACKENDS, id="set-backend", allow_blank=False)
            with Vertical(classes="settings-row"):
                yield Static("Model", classes="settings-label")
                yield Select([], id="set-model", allow_blank=True, prompt="(loading…)")
            with Vertical(classes="settings-row"):
                yield Static("Voice mode", classes="settings-label")
                yield Select(_VOICE_MODES, id="set-voice", allow_blank=False)
            with Vertical(classes="settings-row"):
                yield Static("Web mode", classes="settings-label")
                yield Select(_WEB_MODES, id="set-web", allow_blank=False)
            with Vertical(classes="settings-row"):
                yield Static("Telegram bot token", classes="settings-label")
                yield Static("", id="set-token")
            with Vertical(classes="settings-row"):
                yield Static("Telegram owner id", classes="settings-label")
                yield Static("", id="set-owner")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_values()

    def on_screen_resume(self) -> None:
        self.refresh_values()

    def refresh_values(self) -> None:
        self._loading = True
        try:
            backend = config.get_backend()
            self.query_one("#set-backend", Select).value = backend
            self._refresh_model_options(backend)
            self.query_one("#set-voice", Select).value = \
                config.get_setting("voice_mode", "online")
            self.query_one("#set-web", Select).value = \
                config.get_setting("web_mode", "headless")
            self.query_one("#set-token", Static).update(_mask(config.BOT_TOKEN))
            self.query_one("#set-owner", Static).update(
                str(config.OWNER_CHAT_ID) if config.OWNER_CHAT_ID else "not set")
        finally:
            self._loading = False

    def _refresh_model_options(self, backend: str) -> None:
        select = self.query_one("#set-model", Select)
        options = config.model_catalog()
        select.set_options(options)
        current = config.get_model()
        values = {v for _, v in options}
        select.value = current if current in values else Select.NULL

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._loading:
            return
        value = event.value
        if value is Select.NULL:
            return
        select_id = event.select.id
        if select_id == "set-backend":
            config.set_backend(value)
            self._loading = True
            try:
                self._refresh_model_options(value)
            finally:
                self._loading = False
        elif select_id == "set-model":
            config.set_model(value)
        elif select_id == "set-voice":
            config.set_setting("voice_mode", value)
        elif select_id == "set-web":
            config.set_setting("web_mode", value)
