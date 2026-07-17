# ZILLA CORE API — design (P1 step 1)

Status: APPROVED by owner, 2026-07-16.

## Shape

One package, `zilla/`. One class, `ZillaCore`, owning everything that is not
interface I/O: locks, turn pipeline, sessions, scheduler, bridge, approvals,
health. Frontends (Telegram bot, TUI, later anything) are thin translators
between their medium and this API. `platform_compat`, `config`, `sessions`,
`users`, `schedules`, `harness`, `cli_engine`, `backends`, `media`,
`interactive`, `verify`, `autoharness`, `formatter` move under `zilla/`
unchanged (import shims keep old paths alive during migration).

## Events (the one vocabulary every frontend speaks)

Turns and background activity all express themselves as a small set of
dataclasses:

| Event | Meaning | Telegram renders as | TUI renders as |
|---|---|---|---|
| `Progress(text)` | agent is working; latest step | the editable ⏳ message | status line |
| `Ask(id, kind, prompt)` | agent needs a human (otp/password/text/confirm) | DM with force-reply | inline prompt |
| `Response(text, files, meta)` | final answer (+ extracted file paths) | chunked HTML message | chat bubble |
| `ApprovalRequest(id, user, prompt)` | limited user waiting | ✅/❌ buttons to owner | prompt to owner |
| `Alert(text, runbook)` | human-required health problem | one plain DM | banner |
| `ScheduledResult(title, response)` | a schedule fired | ⏰ DM | notification line |

## The API (async, frontend-agnostic)

```python
core = ZillaCore(config)            # no I/O yet
await core.start()                  # scheduler loop, bridge watcher, health loop
await core.stop()

# THE turn pipeline (today buried in bot.py):
async for event in core.handle_message(user_id, text, attachments=()):
    ...                             # yields Progress/Ask/Response; owns per-user
                                    # lock, approval hold, harness, run, verify

core.answer_ask(ask_id, text)       # any frontend can answer an Ask
core.cancel(user_id)                # replaces _active_cancel poking

core.approvals.pending() / .approve(id) / .deny(id)
core.sessions.list/create/switch/rename/delete(user_id, ...)
core.schedules.list/create/delete/toggle(user_id, ...)
core.settings.get/set(key)          # same .env + settings.json — one source of truth
core.users …                        # wraps users.py as-is
core.health.report()                # doctor data; health loop emits Alert events

core.subscribe(sink)                # background events (Ask from schedules,
                                    # Alert, ScheduledResult, ApprovalRequest)
                                    # → every connected frontend's async queue
```

Two delivery paths, deliberately: events *inside a turn* stream from the
`handle_message` generator (natural for both a Telegram handler and a TUI);
events *outside any turn* (scheduler results, health alerts, bridge asks)
go through `subscribe()`. This is the piece `bot.py` currently hardwires to
Telegram DMs.

## Invariants carried over unchanged

Per-user `asyncio.Lock` around every CLI run; conv id re-read inside the
lock; I-CONV / I-STEP / I-CANCEL; global new-conv lock; activity-based
reaper. The refactor moves this code, it does not rewrite it.

## Migration order (strangler — bot must work at every commit)

1. Create `zilla/` package; move pure modules with import shims. Tests green.
2. Extract the turn pipeline from `bot.py` into `core.handle_message`;
   `bot.py`'s handlers become ~10-line translators. Live Telegram check.
3. Extract scheduler runtime → core + `ScheduledResult` events.
4. Extract bridge watcher → core `Ask` events; Telegram just renders them.
5. Extract approval flow → `core.approvals` + events.
6. Health loop lands in core as a stub (filled in Phase 7).

Each step: tests green, one live Telegram round-trip, commit.
