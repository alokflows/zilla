"""Health screen — renders core.health_report() (docs/dev/CORE_API.md
health snapshot: backend/model, per-CLI reachability, disk, scheduler/
bridge attachment). The probes behind it can shell out (`agy models`,
`claude auth status`, each up to ~8s) so the report is always fetched on a
thread worker — the UI never freezes while it loads. Press 'r' to refresh
with force=True (bypasses the probes' cache)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static


def _human_bytes(n) -> str:
    if n is None:
        return "unknown"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class HealthScreen(Screen):

    BINDINGS = [Binding("r", "refresh_health", "Refresh")]

    def compose(self) -> ComposeResult:
        yield Static("Health", classes="screen-title")
        yield VerticalScroll(id="health-body")
        yield Footer()

    def on_screen_resume(self) -> None:
        # Textual posts ScreenResume on every switch_screen/push_screen,
        # including the very first time a freshly-created screen is shown
        # (in addition to on_mount firing) — relying on resume alone (and
        # dropping a separate on_mount hook) avoids load_health() firing
        # twice back-to-back, which raced two "health-loading" placeholder
        # widgets with the same id.
        self.load_health(force=False)

    def action_refresh_health(self) -> None:
        self.load_health(force=True)

    def load_health(self, force: bool) -> None:
        core = self.app.core
        body = self.query_one("#health-body", VerticalScroll)
        body.remove_children()
        if core is None:
            body.mount(Static(
                self.app.startup_hint or "Zilla's core did not start.",
                classes="bad"))
            return
        body.mount(Static("loading…", id="health-loading"))
        self.run_worker(lambda: self._fetch(core, force), thread=True,
                        exclusive=True, group="health")

    def _fetch(self, core, force: bool) -> None:
        try:
            report = core.health_report(force=force)
        except Exception as e:  # never crash the screen on a probe failure
            report = {"_error": str(e)}
        self.app.call_from_thread(self._render_report, report)

    def _render_report(self, report: dict) -> None:
        # Named _render_report, not _render — Widget._render() is a
        # reserved Textual internal (used during compositing); shadowing it
        # crashed the very first paint with
        # "TypeError: _render() missing 1 required positional argument".
        body = self.query_one("#health-body", VerticalScroll)
        body.remove_children()
        if "_error" in report:
            body.mount(Static(f"Health check failed: {report['_error']}", classes="bad"))
            return

        body.mount(Static("Backend", classes="health-section"))
        body.mount(Static(f"{report.get('backend')} — {report.get('model')}",
                          classes="health-row"))

        body.mount(Static("AI CLIs", classes="health-section"))
        for name, info in (report.get("clis") or {}).items():
            ok = bool(info.get("reachable"))
            css = "ok" if ok else "bad"
            state = "reachable" if ok else "not reachable"
            line = f"{name}: {state}"
            err = info.get("auth_error")
            if err:
                line += f" ({err})"
            body.mount(Static(line, classes=f"health-row {css}"))

        disk = report.get("disk") or {}
        body.mount(Static("Disk", classes="health-section"))
        body.mount(Static(
            f"{disk.get('path')} — {_human_bytes(disk.get('free_bytes'))} free "
            f"of {_human_bytes(disk.get('total_bytes'))}",
            classes="health-row"))

        sched = report.get("scheduler") or {}
        body.mount(Static("Scheduler", classes="health-section"))
        sched_css = "ok" if sched.get("attached") else "settings-label"
        body.mount(Static(
            f"attached: {sched.get('attached')}  ·  jobs: {sched.get('schedule_count', 0)}",
            classes=f"health-row {sched_css}"))

        bridge = report.get("bridge") or {}
        body.mount(Static("Bridge", classes="health-section"))
        bridge_css = "ok" if bridge.get("exists") else "bad"
        body.mount(Static(f"{bridge.get('dir')} — exists: {bridge.get('exists')}",
                          classes=f"health-row {bridge_css}"))
