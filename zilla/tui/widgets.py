"""Reusable chat widgets: ChatBubble (a rendered turn) and ApprovalPrompt
(an inline approve/deny card for core's ApprovalRequest events — see
docs/dev/CORE_API.md)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Markdown, Static

_LABELS = {
    "user": "You",
    "zilla": "Zilla",
    "system": "System",
    "error": "Error",
}


class ChatBubble(Vertical):
    """One message in the chat log. `role` picks the label + CSS accent;
    `markdown=True` renders `text` as markdown (used for the backend's
    Response text — it may contain code blocks / formatting)."""

    def __init__(self, role: str, text: str, *, markdown: bool = False):
        super().__init__(classes=f"bubble bubble-{role}")
        self._role = role
        self._text = text
        self._markdown = markdown

    def compose(self) -> ComposeResult:
        yield Static(_LABELS.get(self._role, self._role.title()), classes="bubble-label")
        if self._markdown:
            yield Markdown(self._text, classes="bubble-body")
        else:
            yield Static(self._text, classes="bubble-body", markup=False)


class SkillRow(Vertical):
    """One row in the Skills screen — name + description, same source
    harness.skills_summary() feeds into every backend's turn preamble."""

    def __init__(self, name: str, description: str):
        super().__init__(classes="skill-row")
        self._name = name
        self._description = description

    def compose(self) -> ComposeResult:
        yield Static(self._name, classes="skill-name")
        yield Static(self._description or "(no description)", classes="bubble-body", markup=False)


class ApprovalPrompt(Vertical):
    """An ApprovalRequest rendered inline with Approve/Deny buttons. The
    button ids carry the request id (approve-<id> / deny-<id>) so the chat
    screen's on_button_pressed can route them without extra state."""

    def __init__(self, request_id: str, name: str, prompt: str):
        super().__init__(classes="bubble bubble-approval")
        self.request_id = request_id
        self._name = name
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield Static(f"Approval needed — {self._name}", classes="bubble-label")
        yield Static(self._prompt, classes="bubble-body", markup=False)
        with Horizontal(classes="approval-buttons"):
            yield Button("Approve", id=f"approve-{self.request_id}", variant="success", compact=True)
            yield Button("Deny", id=f"deny-{self.request_id}", variant="error", compact=True)
