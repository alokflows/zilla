"""Chat screen — opencode-style scrolling chat pane + input bar at the
bottom, driven by zilla.core.ZillaCore.handle_message (the SAME turn
pipeline bot.py drives — docs/dev/CORE_API.md).

Two event paths, per CORE_API:
  - IN-TURN events stream from the handle_message() async generator while a
    message is being answered: Progress -> the status line, Response -> a
    markdown chat bubble.
  - OUT-OF-TURN (background) events arrive via core.subscribe(): Ask (a
    credential/OTP prompt from the bridge watcher) becomes an inline masked
    prompt in the input bar; ApprovalRequest (a held request from a
    "limited" user elsewhere) becomes an inline approve/deny card; Alert
    and ScheduledResult render as system bubbles.

handle_message() is itself an async generator built on asyncio locks/queues
(it must run on the event loop, not an OS thread — see zilla/core.py). This
screen drives it with Textual's native ASYNC worker
(`run_worker(coro, exclusive=True)`), which runs concurrently on the app's
own asyncio loop without blocking widget updates — the same non-blocking
guarantee a thread worker would give here, without needing a second event
loop.
"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from zilla.core import Alert, ApprovalRequest, Ask, Progress, Response, ScheduledResult
from zilla.tui.widgets import ApprovalPrompt, ChatBubble


class ChatScreen(Screen):

    def __init__(self):
        super().__init__()
        # (ask_id, is_secret) while the input bar owes an answer to a
        # bridge Ask instead of a normal chat message.
        self._pending_ask: tuple[str, bool] | None = None
        self._event_queue: asyncio.Queue | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-log")
        yield Static("", id="status-line")
        yield Input(placeholder="Message Zilla…", id="chat-input")
        yield Footer()

    def on_mount(self) -> None:
        core = self.app.core
        if core is not None:
            self._event_queue = asyncio.Queue()
            core.subscribe(self._event_queue)
            self.run_worker(self._drain_background_events(), exclusive=False,
                            group="bg-events", name="bg-events")
        elif getattr(self.app, "startup_hint", None):
            self._append(ChatBubble("error", self.app.startup_hint))

    def on_screen_resume(self) -> None:
        self.query_one("#chat-input", Input).focus()

    def on_unmount(self) -> None:
        core = self.app.core
        if core is not None and self._event_queue is not None:
            core.unsubscribe(self._event_queue)

    # ── sending ─────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        if self._pending_ask is not None:
            self.answer_pending_ask(text)
        else:
            self.send_message(text)

    def send_message(self, text: str) -> None:
        """Public — called both by the input bar and by HomeScreen's
        "type and Enter" first message."""
        if self.app.core is None:
            self._append(ChatBubble(
                "error", self.app.startup_hint or "Zilla's core did not start."))
            return
        self._append(ChatBubble("user", text))
        self.run_worker(self._drive_turn(text), exclusive=True, group="turn", name="turn")

    async def _drive_turn(self, text: str) -> None:
        core = self.app.core
        status = self.query_one("#status-line", Static)
        status.update("⏳ thinking…")
        try:
            async for ev in core.handle_message(self.app.user_id, text,
                                                 chat_key=self.app.user_id):
                if isinstance(ev, Progress):
                    status.update(f"⏳ {ev.text}")
                elif isinstance(ev, Response):
                    self._append(ChatBubble("zilla", ev.text or "(no output)", markdown=True))
        except Exception as e:  # never let a backend failure crash the app
            self._append(ChatBubble("error", str(e)))
        finally:
            status.update("")

    # ── inline ask (otp / password / text / confirm) ───────

    def prompt_ask(self, ask_id: str, kind: str, prompt: str, is_secret: bool) -> None:
        self._pending_ask = (ask_id, is_secret)
        self._append(ChatBubble("system", f"[{kind}] {prompt}"))
        inp = self.query_one("#chat-input", Input)
        inp.password = is_secret
        inp.placeholder = "Type your answer and press Enter…"

    def answer_pending_ask(self, text: str) -> None:
        ask_id, _is_secret = self._pending_ask
        self._pending_ask = None
        inp = self.query_one("#chat-input", Input)
        inp.password = False
        inp.placeholder = "Message Zilla…"
        self.app.core.answer_ask(ask_id, text)
        self._append(ChatBubble("system", "answer sent"))

    # ── background events (asks, approvals, alerts, schedules) ─

    async def _drain_background_events(self) -> None:
        while True:
            ev = await self._event_queue.get()
            self._handle_event(ev)

    def _handle_event(self, ev) -> None:
        if isinstance(ev, Ask):
            self.prompt_ask(ev.id, ev.kind, ev.prompt, ev.is_secret)
        elif isinstance(ev, ApprovalRequest):
            self._append(ApprovalPrompt(ev.id, ev.name or str(ev.user), ev.prompt))
        elif isinstance(ev, Alert):
            self._append(ChatBubble("error", f"{ev.text}\n{ev.runbook}".strip()))
        elif isinstance(ev, ScheduledResult):
            body = ev.response or ev.warning
            self._append(ChatBubble("system", f"⏰ Scheduled — {ev.title}\n{body}"))

    def on_button_pressed(self, event) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("approve-"):
            rid = button_id.removeprefix("approve-")
            self.run_worker(self._resolve_approval(rid, True), exclusive=False)
        elif button_id.startswith("deny-"):
            rid = button_id.removeprefix("deny-")
            self.run_worker(self._resolve_approval(rid, False), exclusive=False)

    async def _resolve_approval(self, rid: str, approve: bool) -> None:
        core = self.app.core
        if approve:
            result = await core.approvals.approve(rid)
            text = "approved and run" if result else "already resolved/expired"
        else:
            result = core.approvals.deny(rid)
            text = "denied" if result else "already resolved/expired"
        self._append(ChatBubble("system", f"Request {rid[:8]} — {text}"))

    # ── helpers ─────────────────────────────────────────────

    def _append(self, widget) -> None:
        log = self.query_one("#chat-log", VerticalScroll)
        log.mount(widget)
        log.scroll_end(animate=False)
