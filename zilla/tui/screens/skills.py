"""Skills screen — lists the ACTIVE backend's skills from the same source
harness.skills_summary() feeds into every turn's preamble (HANDOFF.md: "One
line per skill from SKILL.md frontmatter"). Never a second skills index."""

from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from zilla import harness
from zilla.tui.widgets import SkillRow

_SKILL_LINE = re.compile(r"^-\s+\*\*(.+?)\*\*:\s*(.*)$")


def _parse_skills(summary: str) -> list[tuple[str, str]]:
    """harness.skills_summary() lines look like '- **name**: description'."""
    rows = []
    for line in (summary or "").splitlines():
        m = _SKILL_LINE.match(line.strip())
        if m:
            rows.append((m.group(1), m.group(2)))
    return rows


class SkillsScreen(Screen):

    def compose(self) -> ComposeResult:
        yield Static("Skills", classes="screen-title")
        yield VerticalScroll(id="skills-list")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_skills()

    def on_screen_resume(self) -> None:
        self.refresh_skills()

    def refresh_skills(self) -> None:
        container = self.query_one("#skills-list", VerticalScroll)
        container.remove_children()
        rows = _parse_skills(harness.skills_summary())
        if not rows:
            container.mount(Static(
                "No skills found for the active backend.", classes="settings-label"))
            return
        for name, desc in rows:
            container.mount(SkillRow(name, desc))
