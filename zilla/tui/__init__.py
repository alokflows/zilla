"""Zilla TUI — the full-screen terminal app (HANDOFF.md Phase 2 step 3).

A thin frontend over zilla.core.ZillaCore, same as bot.py — see
docs/dev/CORE_API.md for the event vocabulary this package renders
(Progress / Ask / Response / ApprovalRequest / Alert / ScheduledResult).

Entry points:
    zilla.tui.app.run()   — the function a `zilla` console script calls.
    python -m zilla.tui   — same, via __main__.py.
"""
