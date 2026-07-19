# K5 (Team relay) — prerequisite research

> Produced 2026-07-19 by a research-only subagent, BEFORE any K5 code was
> written (session stopped here — owner hit their usage limit). This is a
> map of existing conventions to follow, not a spec — PLAN.md §6/K5 is
> still the spec. Saved so the next session doesn't have to re-derive it.

## 1. Deterministic marker pattern — `OWNER_ALERT:` is the only precedent

`BG_TASK:` / `SKILL_PROPOSAL:` are **not implemented anywhere yet** (they
belong to unbuilt Phases B1/S; PLAN.md only cites them as a "same family"
precedent for K5). K5 is the first real implementation of this marker
family on the **live chat** path.

The one marker implemented today: `OWNER_ALERT:` —
- `zilla/core.py:157` — `_OWNER_ALERT_RE = re.compile(r"^OWNER_ALERT:\s*(.+)$", re.MULTILINE)`
- `zilla/core.py:1154-1170` — `_maybe_alert_owner_from_system_job(sid, response)`:
  search, cooldown-gate via `health.should_alert`/`mark_alerted`, broadcast
  `Alert`. **Only ever runs on the system-job (scheduler) path**
  (`_run_and_record_system`, `core.py:1100`), never on live chat turns.

**No marker-stripping call site exists on the live `handle_message` path
today.** The live-turn pipeline's only outbound gate is `review.py`'s
`review()` (empty/limit/error/fabrication — no marker scanning). K5's
insertion point: `zilla/core.py:823-828`, right after
`result = review(text, response)` and before `yield Response(...)`, gated
on `ctx.is_owner` (see §7).

**No generic "pending action needs confirm" table in `store.py`.** Each
pending-action kind is bespoke and in-memory. Only `skill_approvals`
exists in `store.py:85-100`, and that's a *post-approval* audit record,
not a pending-confirm queue. K5 should follow the `Approvals` pattern
below (a new in-memory dict on `ZillaCore`), not a DB table.

## 2. Confirm-card / inline-button confirm flow — `core.Approvals` is the exact precedent

- `zilla/core.py:280-341` — class `Approvals`, wraps
  `ZillaCore._pending_approvals: dict[str, dict] = {}` (set at `core.py:385`):
  - `submit(uid, chat_id, prompt, name) -> str | None`: `rid = secrets.token_hex(6)`,
    stores `{uid, chat_id, prompt, name, ts}`, broadcasts `ApprovalRequest`.
  - `approve(rid)` — pop + run through `handle_message(skip_permissions=True, origin="approval")`.
  - `deny(rid)` — pop, no execution.
  - `_prune()` drops entries older than `APPROVAL_TTL=3600s` (`core.py:71`),
    called lazily on next `submit()` — no timer loop. `APPROVAL_MAX=50`.
- Event dataclass `ApprovalRequest` (`core.py:208-226`), broadcast via `self._broadcast(...)`.
- Telegram rendering: `bot.py:1466-1489` `_deliver_approval_request(ev)` —
  `InlineKeyboardMarkup([[Btn("✅ Approve & run", callback_data=f"appr_ok_{ev.id}"),
  Btn("❌ Deny", callback_data=f"appr_no_{ev.id}")]])`, DMs `OWNER_CHAT_ID`.
- Callback resolution: `bot.py:811-861` `_cb_approvals(...)` — owner-gated,
  branches on `appr_ok_`/`appr_no_` prefix, peeks `core.approvals.pending()`
  for the preview text, calls `.approve(rid)`/`.deny(rid)`. Expired/handled
  → `query.edit_message_text("⏳ That request expired or was already handled.")`.
  Wired into the router at `bot.py:2823`.
- A second, simpler precedent (`_kb_confirm_schedule()`, `bot.py:1228-1232`,
  static `callback_data="sched_confirm"/"sched_cancel"` + PTB
  `context.user_data["pending_schedule"]`) is **not usable for K5** — the
  pending relay action is born inside `core.py` (marker-parsed out of a
  model reply), which has no access to PTB's `context.user_data`. Use the
  `Approvals` id-keyed-dict-plus-broadcast-event pattern instead.

## 3. Alias resolution

- `zilla/store.py:666-677` — `Store.graph_alias_lookup(name: str) -> int | None`:
  exact match, aliases table first then node title, both `COLLATE NOCASE`.
  **This is the right primitive** for resolving one bare `<alias>` token
  from a marker (as opposed to `graph.alias_scan()`, which scans free text
  for multiple candidates — that's K2's job, not K5's).
  ```python
  node_id = db.graph_alias_lookup(alias)
  node = db.graph_node_get(node_id) if node_id is not None else None
  ```
- `zilla/store.py:574-589` — `graph_node_get(id)` / `_get_by_path(path)` /
  `_get_by_title(title)` all return a full node dict
  (`id, path, type, title, bio, is_ghost`) or `None`.
- K2's free-text-scan precedent (for the try/except discipline to mirror,
  not the lookup itself): `zilla/harness.py:476-493` `_graph_hits(...)`.

**Critical gap:** the `nodes` table (`store.py:94-99`) has **no `attrs`
column** — only `id, path, type, title, bio, is_ghost`. `parse_entity_page()`'s
`attrs` dict (which would carry `telegram_uid::`) is parsed transiently
inside `index_page()` (`graph.py:205-248`) and immediately discarded after
`_structural_gaps()` consumes it. **K5 must re-read and re-parse the page
file from disk** after resolving the alias to a node/path:
```python
from zilla.config import MEMORY_DIR
full_path = os.path.join(MEMORY_DIR, node["path"])
with open(full_path, encoding="utf-8") as f:
    parsed = graph.parse_entity_page(f.read())
uid_raw = parsed["attrs"].get("telegram_uid")
```

## 4. `ScheduleManager` — `zilla/schedules.py`

```python
def add(self, user_id: int, chat_id: int, prompt: str, kind: str, spec: dict,
        title: str = "", session_name: str | None = None,
        session: str | None = None, payload_type: str = "message",
        backend: str | None = None, model: str | None = None,
        is_owner: bool = False, now: float | None = None,
        system: bool = False) -> dict | None:
```
(`schedules.py:263-299`)

- `VALID_PAYLOAD_TYPES = ("message", "system_event", "command")`
  (`schedules.py:72`) — **`system_event` already exists**, exactly what
  PLAN.md's K5 spec calls for.
- `payload_type == "command"` requires `is_owner=True` or `add()` returns
  `None` (`schedules.py:273-276`) — enforced in code. `system_event` has
  no such gate.
- Returns `None` on invalid `kind`/`payload_type`, or no future
  `compute_next_run` result.
- Row shape: `{id, uid: user_id, chat_id, prompt, title, kind, spec,
  enabled:1, created_at, session, payload_type, backend, model,
  fail_count:0, system:0}`.
- Delivery per `payload_type`, `zilla/core.py:1069-1076` inside
  `_execute_schedule`:
  ```python
  if payload_type == "system_event":
      return True, s.get("prompt", ""), "", {"conv_id": None}   # verbatim, ZERO model call
  if payload_type == "command":
      return await self._execute_command_schedule(s)
  ```
  — exactly the "verbatim-text delivery, no re-generation drift" PLAN.md
  wants for `RELAY_SCHEDULE`.
- Delivery to `chat_id` (need not be the owner) already works generically:
  `_run_and_record` → `ScheduledResult` → `bot.py:1389-1428`
  `_deliver_scheduled_result(ev)` sends to `ev.chat_id` via `safe_send` —
  no extra plumbing needed for K5.5's "→ Priya" delivery.
- `.list(user_id, include_system=False)` (`schedules.py:333-344`) filters
  `system=1` out by default. Since a relay schedule keeps `uid=owner`
  (`RELAY_SCHEDULE` calls `add(user_id=owner, chat_id=<target uid>, ...)`),
  `mgr.list(owner_uid)` naturally includes it — rendering the "→ Priya"
  suffix needs a new `chat_id != user_id` check in bot.py (doesn't exist
  yet). `describe(kind, spec) -> str` (`schedules.py:178-196`) is the pure
  human-readable formatter, reused as-is.

## 5. `safe_send` — `bot.py:498-510`

```python
async def safe_send(bot, chat_id: int, text: str, parse_mode: str = None):
    for attempt in range(4):
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
            return
        except Exception as e:
            ...  # backoff 2*(attempt+1)s, log on final failure
```
**No allowlist/authorization check inside `safe_send` itself** — callers
validate. This matches K5's design: Telegram's own send API (target must
have started a chat with the bot) is the backstop, not Zilla code.
`RELAY_SEND` calls `safe_send(bot, resolved_chat_id, message)` directly; a
send failure surfaces as one calm logged line, mirroring
`_deliver_scheduled_result`'s `except Exception` (`bot.py:1428-1429`).

## 6. `auth_middleware` — `bot.py:581-590`

```python
async def auth_middleware(update, context):
    if not update.effective_user:
        raise ApplicationHandlerStop()
    auth.reload()
    uid = update.effective_user.id
    if not auth.is_authorized(uid):
        if update.callback_query:
            await update.callback_query.answer()
        logger.info(f"[AUTH] Denied: {uid}")
        raise ApplicationHandlerStop()
```
Registered `app.add_handler(TypeHandler(Update, auth_middleware), group=-1)`
(`bot.py:3168`) — runs before every other handler. "Reject" today = silent
drop, no reply ever sent to the unauthorized sender.

**K5.5's carve-out goes at line 586-590**, before
`raise ApplicationHandlerStop()`: check if `uid` matches a `telegram_uid::`
on a known person page. If matched, DM the owner the report line, then
still `raise ApplicationHandlerStop()` (the reply must never reach a real
handler). **No existing helper resolves a node BY telegram_uid value** —
attrs aren't indexed at all (see §3's gap) — this lookup is new code:
scan person-typed nodes, re-read each page's `attrs["telegram_uid"]`, or
(cheaper) add a small in-memory/DB index if this needs to be fast. Given
the person-page count is realistically small, a linear scan on each
inbound-from-a-stranger message is probably fine — don't over-engineer.

## 7. Owner-only gating — `TurnContext.is_owner`

```python
@dataclass(frozen=True)
class TurnContext:
    uid: int
    role: str
    is_owner: bool
    origin: str = "user"
```
(`harness.py:62-77`), constructed once per turn at `core.py:774-777`.

Both M2's `_memory_block` (`harness.py:329`) and K2's `_graph_hits`
(`harness.py:482`) use the same idiom: `if ctx is None or not ctx.is_owner:
return ...`. K5's marker detection runs in `core.py` on the *response*
(not `harness.py` on the prompt) — the `ctx` local var from `core.py:774`
is already in scope at the review-call site (`core.py:825`), so
`ctx.is_owner` is checkable with zero new plumbing.

## 8. `parse_entity_page()` attrs — `zilla/graph.py:74-141`

```python
_ATTR_LINE = re.compile(r"^-\s*([A-Za-z_][A-Za-z0-9_ -]*?)::\s*(.*)$")
...
attrs[key_l] = value   # <-- always a raw string, never type-coerced
```
No existing attr key is type-coerced anywhere (`attrs.get("contact")` is
only ever used as a truthiness check). `telegram_uid::` needs K5's own
`int(...)` coercion with `try/except ValueError`, matching `_parse_dates`'s
"never raises on malformed owner-authored Markdown" discipline
(`graph.py:57-71`).

## 9. `/relay log` shaping — `bot.py`'s `COMMAND_REGISTRY`, NOT a standalone script

Two conventions exist, for two different audiences:

- **Repo-root scripts** (`schedule_query.py`, `memgraph.py`, `memsearch.py`)
  are *agent*-callable — invoked by the model mid-turn as a subprocess,
  taught via `harness.py`'s injected preamble. Plain-text output only.
- **Owner-facing Telegram commands** (`/schedule`, `/memory`, `/graph`)
  live in `bot.py`'s single `COMMAND_REGISTRY: list[_CommandSpec]`
  (`bot.py:2941-2970`), each entry `name/description/handler/scope/aliases`,
  registered generically in one loop (`bot.py:3170-3174`) — the structural
  guarantee (grep-gated by tests) that no second `CommandHandler(...)` call
  site can ever drift from the registry.

`/relay log` is owner-typed, not model-queried — it's the second kind.
Add:
```python
_CommandSpec("relay", "Relay log (last 20 relay actions)", cmd_relay, scope="owner"),
```
plus `cmd_relay(update, context)` following `cmd_memory`/`cmd_graph`'s
owner-gate shape (literal `"Owner only."` reply on non-owner, confirmed at
`test_memory_k4.py:296`).

**Test enforcement to satisfy:** `test_zilla_cli.py:419-467`'s
`COMMAND_REGISTRY` grep-gates — no duplicate names/aliases (line 422),
valid `scope` (line 431), owner-only handlers must be `scope="owner"`
(line 438), 1:1 with real `CommandHandler` registrations (line 448).

## 10. Test fixture conventions to reuse

- `test_memory_k4.py:234-262` — plain-command fixtures (no callback):
  `_FakeMessage` (`reply_text` appends to `.sent`), `_FakeUser`, `_FakeChat`,
  `_FakeUpdate`, `_FakeContext(args=...)`. Pattern (`test_memory_k4.py:265-322`):
  `import bot as _bot`, monkeypatch `_bot.auth` with a tiny `_FakeAuth`
  (`is_owner(uid)` only), monkeypatch the I/O call under test,
  `asyncio.run(_bot.cmd_x(update, context))`, assert, restore in `finally`.
  Config isolation happens **before any zilla import**, at module top
  (`test_memory_k4.py:52-63`): throwaway `AGY_SETTINGS_FILE`/`BACKEND` env,
  then `config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")`.
- `test_quickfix.py:54-121` — callback-query fixtures (needed for K5's
  confirm-tap tests): `_FakeMessage` (`.deleted`/`.edited_text`/`delete()`),
  `_FakeQuery` (`.data`, `.answer()` raises on a second call — mirrors real
  Telegram double-answer behavior — `edit_message_text`,
  `edit_message_reply_markup`), `_FakeBot` (`send_message` appends to
  `.sent`), `_FakeUpdate(query, uid, chat_id)`, `_FakeContext` (`.bot`).
- `test_memory_k4.py:74-89` — `_iso()`/`_write_page()`/
  `graph.reindex_graph(db, ...)` helpers for building `telegram_uid::`-bearing
  person pages to resolve against in tests.

For K5: reuse the `test_memory_k4.py` config-isolation preamble +
plain-command fixtures for `cmd_relay`, `test_quickfix.py`'s
`_FakeQuery`/`_FakeBot` for the ✅/❌ confirm-tap round trip.
