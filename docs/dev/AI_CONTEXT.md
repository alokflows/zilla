# ZILLA — System Context (AI ingest)

> Dense operational spec for a fresh agent. Read fully before mutating. Assumes competence; no pedagogy.

## Thesis
Zilla is a **stateless-relay control plane over an agentic CLI**. Telegram is the I/O surface; an external agent CLI (`agy` = antigravity/Gemini, or `claude` = Claude Code) is the cognition+actuation substrate. The bot owns *routing, session/identity, scheduling, lifecycle, delivery isolation*; the CLI owns *reasoning and tool execution*. Treat the bot as a thin, defensive multiplexer — push intelligence to the backend, keep the relay deterministic.

## Topology / dataflow
`Telegram update → auth_middleware (group=-1) → handler → per-user asyncio.Lock → run_cli_async → executor thread → backend.run() → response → transcript-isolated extraction → format_for_telegram → chunked send`. Long-poll (`run_polling`, allowed_updates filtered, drop_pending). `concurrent_updates(True)`: multiple coroutines per user can interleave → all CLI execution is serialized per-uid (see Invariant L).

## Module map (authority boundaries)
- `bot.py` — handlers, inline-keyboard state machine, scheduler runtime (`scheduler_loop`/`_run_scheduled`), delivery (`send_response`/`keep_typing`), lifecycle (`main`/`post_init`/`_register_commands`), WebBridge async shims (`bridge_status`/`bridge_command` via `asyncio.to_thread`).
- `backends.py` — `run_claude()` (pipe+JSON), `_parse_claude_json`, backend registry semantics. agy path lives in `cli_engine`.
- `cli_engine.py` — `run_cli()` (agy via ConPTY), `run_cli_async`→`_run_blocking` (backend dispatch), transcript reducers (`get_new_responses`, `_extract_file_paths`, `get_latest_step`, `TranscriptPoller`), new-conv detection (`_find_new_conv` + global `_new_conv_lock`), `detect_limit`.
- `platform_compat.py` — sole OS-divergence sink: `IS_WINDOWS/MAC/LINUX`, `acquire/release_instance_lock` (msvcrt↔fcntl), `apply_window_hiding`, `FlashSuppressor`, `PtyProcess` (winpty↔stdlib `pty`).
- `config.py` — `.env` loader, path resolution (`_find_exe`: PATH→platform default), backend selection (`get_backend/set_backend`), model layer (`get_model/set_model/model_catalog`, backend-dispatched), settings KV (`get_setting/set_setting`, cached on first load — NO mtime invalidation; external edits to settings.json are invisible until restart), `BOT_VERSION`.
- `sessions.py` — `SessionManager`: per-uid named sessions → CLI conversation ids, `conv_backend` tag, `last_seen_step`, `auto_title`. Atomic JSON.
- `schedules.py` — `ScheduleManager` + pure `compute_next_run(kind,spec,after)` (once/interval/daily/weekly), `due`, `touch_run`, `reconcile_startup`. `schedule_parse.py` — NL + command grammar → `{kind,spec,title,prompt}`.
- `media.py` — ingest (`save_*`), inbox model (`get_inbox_items(category)`, `get_inbox_counts`, `delete_inbox_file` [realpath-fenced]), `transcribe_audio`, `extract_text`.
- `formatter.py` — `format_for_telegram` (HTML/MarkdownV2 sanitization), `detect_file_paths`.
- `install.py` (+`install.bat/.command/.sh`) — interactive or non-interactive (`--token/--owner/--backend`) provisioning + `--doctor`. `run_background.py` — cross-platform restart supervisor (stop-flag `zilla.stop`). `winhide.py` — Windows Popen CREATE_NO_WINDOW patch.

## Backend abstraction
`BACKEND ∈ {agy, claude}` (live: `get_setting("backend") or env BACKEND`; togglable in `/settings`, model screen, no restart). Dispatch in `cli_engine._run_blocking`. Contract: `run(prompt, conversation_id, *, progress_callback, cancel_event, skip_permissions[, model]) -> (response:str, conversation_id|None)`.
- **agy**: TUI ⇒ requires PTY. Spawn `agy [--conversation <id>] [--dangerously-skip-permissions] --print-timeout <m>m --print <prompt>` under `PtyProcess`. New conv id is NOT pre-generated — agy mints its own dir under `BRAIN_DIR`; detect via snapshot-diff. Answer extracted from `transcript.jsonl`, not stdout.
- **claude**: `claude -p <prompt> --output-format json [--model X] [--resume <session_id>] [--dangerously-skip-permissions] --add-dir <cwd>`. Parse JSON → `result`,`session_id`(=conversation_id),`is_error`. Pipe-based ⇒ cross-platform, no PTY. Memory via `--resume`.

## Model layer (non-obvious)
- agy has **no** `--model` flag and ignores model env vars. Active model lives in `~/.gemini/antigravity-cli/settings.json:"model"` as a **display string** (e.g. `"Gemini 3.1 Pro (High)"`). agy **silently falls back** for unrecognized strings (no error) — never trust a write blindly; `set_model` writes atomically then **reads back**. agy's true catalog is fetched dynamically (`FetchAvailableModels`, account-scoped) and not cached to disk → bot ships a curated Gemini list + ✏️Custom escape hatch.
- claude model = alias (`opus/sonnet/haiku`) stored in bot settings (`claude_model`), passed via `--model`.
- `model_catalog()` and `get/set_model` dispatch on `get_backend()`.

## Conversation & delivery isolation (hard-won invariants)
- **I-CONV**: conversation ids are backend-specific (agy brain-dir vs claude session uuid). `sessions` tags each with `conv_backend`; `bot._conv_for_run` returns `None` when active backend ≠ tag ⇒ fresh conversation on switch.
- **I-STEP**: agy answer = final `PLANNER_RESPONSE` with `step_index > max(starting_step_floor, last USER_INPUT boundary)`. Dual guard prevents prior-turn bleed. `starting_step` captured pre-spawn inside the lock.
- **I-CANCEL**: on `canceled|idle|max_runtime`, **do not** fall back to raw PTY scrollback (it renders prior turns ⇒ bleed). Deliver transcript-only (clean current-turn partial) + status header. PTY-tail fallback is *normal-exit only*.
- **L (lock)**: `async with _get_user_lock(uid)` wraps every CLI run; `conv_id` re-read and `sname=get_active_name(uid)` pinned **inside** the lock; all session writes thread `session_name=sname` + `backend=get_backend()`. `_active_cancel[chat_id]` set inside lock; popped only if identity-matched.
- New-conv detection uses a **global** `_new_conv_lock` across snapshot→spawn→detect (shared `BRAIN_DIR`) to prevent cross-user dir misattribution; bounded acquire, released in `finally`.

## Reaper / progress
No wall-clock kill. Terminate only on: `cancel_event`, idle silence > `idle_kill_after` (settings; activity = new PTY bytes OR new transcript step), or `MAX_TOTAL_RUNTIME` ceiling. `keep_typing`: typing action 0–60s, then a single editable "⏳ Working… [🛑 Cancel]" message (`cancel_active`), deleted on completion.

## Scheduler
Custom asyncio loop (NO APScheduler / PTB JobQueue — APScheduler absent). `post_init`→`scheduler_loop`: `reconcile_startup(catchup=setting schedule_catchup default True)` then ~20s tick; `_run_scheduled` executes under the per-uid lock, DMs `⏰ Scheduled — <title>` + result, `touch_run` advances. Catch-up = run missed once on boot. Creation: `/schedule` (`parse_schedule_command`) or NL in chat (`parse_schedule`, admin-gated, confirm card). Persistence `schedules.json`.

## Identity / authz (trust model — read before "securing")
Two roles only: **owner** (env `TELEGRAM_OWNER_ID`) + **admin** (everyone in `authorized_users.json`; legacy `user` auto-migrates). `_CAPS`: chat/admin→{admin,owner}, users→{owner}. `can()` denies non-stored non-owner. Owner-gated `admins_can_change_model` (predicate `auth.can_change_model`). **Critical**: agy/claude execute tools in headless `--print` regardless of `--dangerously-skip-permissions`/`toolPermission`/`--sandbox` (empirically verified). ⇒ **no in-CLI sandbox is achievable**; any authorized principal ≈ RCE-as-host-user. Containment must be OS-level (separate account / container) or by not exposing the agentic backend. Trust-based by design.

## Cross-platform & UX hygiene
- `PtyProcess`: winpty(ConPTY) on Windows; `pty.openpty`+`start_new_session` on POSIX (`import pty` lazy — no `termios` on Win).
- `FlashSuppressor`: hides newly-spawned `ConsoleWindowClass` windows during agy runs (root cause: pythonw host ⇒ ConPTY conhost + agy's child tool consoles flash; winhide only covers our own Popen). Won't touch Windows Terminal (distinct class).
- Single-instance: `acquire_instance_lock` (msvcrt LK_NBLCK / fcntl LOCK_EX|NB). PID file `zilla.pid`.
- Console output is cp1252 on Win → emit ASCII OR `sys.stdout.reconfigure(utf-8, errors=replace)` (installer does).
- **Menu lifecycle**: opening a command-menu via `_open_menu` strips the previous menu's keyboard (`_active_menu[chat_id]`) ⇒ stale menus in history are inert. Every screen carries `✕ Close` (`menu_close`).
- `safe_send_file`: realpath allowlist = `AGI_BRAIN_DIR` (∋ Inbox/Outbox) + the live conversation dir; symlink-fenced.

## Config surface (.env)
Required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID`. Selective: `BACKEND`, `CLI_PATH`, `CLAUDE_PATH`, `CLI_WORKING_DIR`, `BRAIN_DIR`, `FFMPEG_PATH`, `IDLE_KILL_AFTER`, `MAX_TOTAL_RUNTIME`, `KIMI_BRIDGE_URL`, `AGY_SETTINGS_FILE`. Settings KV (`settings.json`): `backend`, `claude_model`, `admins_can_change_model`, `schedule_catchup`, `idle_kill_after`, `auto_describe_photos`.

## Verification
`python test_fixes.py` (96 deterministic, no-network; isolates state via `AGY_SETTINGS_FILE` env). `python install.py --doctor` (env/login/token/deps). Live smoke: agy+claude round-trip, resume continuity, cancel-no-bleed, schedule fire+catchup, model read-back, menu staleness, no console flash.

## Repo / release
`github.com/alokflows/zilla`, branch `main`. Tags: `v3.0.0` (pre-refactor checkpoint), `v4.0.0` (cross-platform+backends+installer), `v4.1.x` (UX fixes/cleanup). `BOT_VERSION` in `config.py`. Persistent agent memory in `~/.claude/projects/.../memory/` (project_agy_bot.md is canonical).

## Mutation guidance
Preserve I-CONV/I-STEP/I-CANCEL/L and the global new-conv lock — violating any reintroduces response bleed or memory corruption. Keep OS-specific code in `platform_compat`. Add a backend by implementing the run-contract + registering in `_run_blocking` + extending `model_catalog`. Don't claim a model/feature works without the read-back/transcript/live evidence — the backend lies silently.
