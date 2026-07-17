# HANDOFF — status board

**Planned by Fable (planning session, 2026-07-17) with the owner. The full
engineering blueprint is [`PLAN.md`](PLAN.md) — read it and execute, phase by
phase, in order.**

For any execution agent, every session:

1. Read `docs/dev/AI_CONTEXT.md` (system spec + invariants) — fully.
2. Read `PLAN.md` — your work order. Decisions there are settled; don't reopen.
3. Check the status board below; take the first unchecked item.
4. Run the test suite before touching anything (`python test_fixes.py`,
   `python test_interactive.py` — 176 passing at baseline).
5. Execute ONE sub-phase. Tests green + acceptance criteria met + live smoke
   logged (or explicitly marked shipped-untested) before you stop.
6. Update this board, `PLAN.md` §13 checkboxes, and `docs/dev/STATUS.md`
   (what landed, what's verified vs untested, anything the next agent must
   know). Commit small, phase-prefixed. Push.

## Status board

| Item | State | Notes |
|---|---|---|
| Planning (architecture, phases, blueprint) | ✅ done | Fable + owner, 2026-07-17 |
| M1 SQLite store + migration | ⬜ next | |
| M2 Memory layout + injection | ⬜ | |
| M3 FTS5 + nightly distillation | ⬜ | |
| M4 Memory git + quiet runs | ⬜ | |
| H1 Heartbeat loop | ⬜ | |
| H2 Health probes + assisted re-login | ⬜ | |
| H3 systemd service | ⬜ | |
| R1 Triage router | ⬜ | |
| R2 Fallback chain | ⬜ | |
| R3 opencode adapter | ⬜ | |
| S Skills from chat | ⬜ | |
| G1 Engine facade extraction | ⬜ | |
| T1 Terminal app | ⬜ | |
| V Offline voice | ⬜ | |
