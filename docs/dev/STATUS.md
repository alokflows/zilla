# Zilla — Status & Roadmap
_Last updated: 2026-06-07 (session 3 — security hardening + autonomy keystone)_

Goal: a top-class, fast, trustworthy autonomous agent driven entirely from
Telegram — one command gets real things done (log in to services, order to an
address, run/control the whole computer), asking the human only for
credentials/OTPs.

The security-hardening work described below was merged to `main` via PR #1
(`4d5b5ce`). Current planning + execution branch:
`claude/python-cli-bot-planning-80x8a3` — see `PLAN.md` (blueprint) and
`HANDOFF.md` (status board).

---

## ✅ Done & verified this session

**Security audit → fixes (6 of the audit's critical/high findings).** Full report
in the session log. Committed in `4f3d246`:
- **Bot-token leak** — redaction filter strips the token from logs; `chmod 600`
  on `.env`/state/logs at startup. ACTION: **rotate the token via @BotFather**
  (the old one was exposed in plaintext logs and in audit context).
- **Schedule revocation** — de-authorized users' schedules no longer execute
  (was a persistent RCE backdoor); they're auto-disabled.
- **Link-href injection** — `tg://`/`javascript:`/`file:`/`data:` links from CLI
  output are stripped; only http/https render.
- **Response bleed (I-STEP)** — new-conv floor pinned to 0 instead of read from a
  live transcript.
- **Crash path** — `response` bound before the try (no NameError on shutdown).
- **Owner guard** — bot refuses to start if `TELEGRAM_OWNER_ID` is unset.
- **/browse** restricted to http/https; **set_toggle_photo** got its missing
  admin check.

**Autonomy keystone — human-in-the-loop credential/OTP relay.** Committed in
`b35dcc8`:
- `interactive.py` — pure file-bridge core (ask/answer JSON), **16 hermetic
  tests green**.
- The agent is taught the protocol every turn (harness); a `bridge_watcher` DMs
  the owner the prompt; the owner's reply is captured as the answer.
- This is what enables "log in with my number -> I paste the OTP -> it finishes
  the order" without exposing secrets in the prompt.

**Test status: 160 (existing) + 16 (relay) = 176 passing, 0 failing.** `bot.py`
imports clean; all touched modules compile.

---

## Needs a LIVE round-trip test (code shipped, not yet exercised end-to-end)

The bot is **not currently running** and there's no live CLI/Telegram in this
session, so the OTP relay has unit-test coverage but not an integration run.
Before trusting it for a real login/order, verify once:
1. Start the bot, ask it (in Telegram) to log into a test service.
2. Confirm the agent writes `AGI-Brain/Bridge/ask_*.json`, you get the DM,
   your reply lands as `answer_*.json`, and the agent continues.
Likely tuning after that: the idle-reaper must not kill the CLI while it's
polling for an answer (the agent should emit activity while waiting), and the
secret reply should be deleted from the Telegram chat for `otp`/`password` kinds.

---

## Remaining audit findings (tracked, not yet fixed)

Ordered by priority. None are new regressions — all pre-existing.
- **C5 (HIGH/CRIT): state locks only guard file I/O, not in-memory mutations**
  (`schedules.py`, `sessions.py`). Needs an `asyncio.Lock` around
  mutate+save. Deferred deliberately — a botched concurrency change is worse
  than the current low-probability interleave; do it with care + a test.
- **DST scheduling** (`schedules.py` naive datetime) — wrong fire time near DST.
- **`_active_cancel` keyed by chat_id** — cross-user cancel in group chats.
- **`os.fsync` on the event loop** in `_save()` — wrap in `to_thread`.
- **No media size cap** on Telegram ingest (`media.py`).
- **config settings/model caches** unlocked + return live mutable dict.
- Mediums/lows: deprecated `get_event_loop`, `TargetFile` isfile probe,
  install.py perms/newline, harness instruction cache never expires, ffmpeg
  format hint, etc. (see full report).

---

## Autonomy roadmap (the "gets shit done" vision)

Much of "control the whole computer" already works: the agentic CLI executes
shell/files with full host privileges, and the Claude backend has an embedded
Playwright browser. The gaps to the full vision:

1. **Credential/OTP relay** — shipped; needs the live test above.
2. **agy browser** — agy can't browse yet (only Claude can). Give agy a browser
   via its MCP config, or route web tasks to the Claude backend automatically.
3. **Order-to-an-address flow** — once (1)+(2) hold, this is a prompt + the
   relay. Add a stored "profile" (default address, etc.) the agent can read,
   and ALWAYS gate the final purchase behind a `confirm` relay ask.
4. **Telegram dashboards/projects** — a `/projects` surface backed by JSON
   files (like sessions/schedules), with status cards. Build on existing menu
   infra.
5. **Speed** — agy's ~13s/turn is a per-call Google handshake; route fast/simple
   turns to Claude when available, keep progress UI for the rest.
6. **Trust hardening** — finish C5 + the mediums before relying on it for money
   movements.

---

## Operate

```bash
cd ~/Documents/zilla
# restart:
{ [ -f zilla.pid ] && kill "$(cat zilla.pid)" 2>/dev/null; }; pkill -9 -f "Documents/zilla.*bot.py"; sleep 2
./.venv/bin/python bot.py
# tests (expect 160 + 16):
./.venv/bin/python test_fixes.py 2>&1 | tail -2
./.venv/bin/python test_interactive.py 2>&1 | tail -2
```
Always use `./.venv/bin/python`. Branch `security-hardening-and-autonomy` holds
all of this; merge to `main` / push after the live relay test + token rotation.
