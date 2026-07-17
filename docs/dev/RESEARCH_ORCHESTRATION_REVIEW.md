# The "Effortless" Turn Pipeline — OpenClaw & Hermes vs Zilla, and the Response-Review Seam

Researched 2026-07-17. Deep-dive on ONE axis for the Zilla owner: the pipeline from **"model produced output" → "user sees message"**, plus **failure recovery**. Companion to `docs/dev/RESEARCH_OPENCLAW_HERMES.md` (read that first for the architecture map and the owner's ADOPT/SKIP decisions — this doc does not repeat them). Sources: docs.openclaw.ai, howtouseopenclaw.com (mirror of the agent-loop page), openclaw GitHub issues, hermes-agent.nousresearch.com/docs. **[V] = verified against a doc/source page. [I] = inferred.** Zilla claims cite `file:line` on branch `claude/zilla-harness-review-0v96bs`.

---

## 1. TL;DR

**What actually makes them effortless.** Neither OpenClaw nor Hermes has a reviewer LLM that reads the finished answer and decides whether to re-run it. The "it quietly fixed itself" feeling is produced by three *separate* mechanisms, none of which is a judge: (1) the model's **own agentic tool loop** — a shell/exec tool feeds stderr + exit codes back into the transcript as a tool result, and the model reacts in the same turn (sees "command not found", installs the converter, retries, transcribes — the OGG story lives entirely here) [V]; (2) a **system prompt that demands persistence** — OpenClaw's "Execution Bias": *"act in-turn on actionable requests, continue until done or blocked, recover from weak tool results, check mutable state live, and verify before finalizing"* [V]; (3) a **thin deterministic delivery filter** that runs between the loop and the channel — `NO_REPLY` suppression, messaging-tool de-duplication, and a fallback tool-error reply — which is "reading before delivering" but is pure string logic, never model judgment [V]. The user never sees the intermediate mess because delivery happens only after the loop finishes; the "self-heal" is in-loop, not a second pass.

**The 3-sentence version of what Zilla should build.** Zilla's "model" is a rented agentic CLI (agy / Claude Code) that *already* runs its own tool loop, so the OGG-style self-heal belongs in **(a) the CLI's loop, unlocked by a harness self-heal directive + exec permission — NOT a Zilla review pass**, because Zilla cannot re-drive the CLI's tools. Zilla's own gate should stay a **deterministic outbound check** (empty / limit / error-garbage — most of which already exists across `verify.py`, `cli_engine.detect_limit`, and `core._execute_message_schedule`) that triggers **at most ONE bounded corrective retry**, then stops in plain language — never a silent loop and never a model-judged security decision. So: harness preamble gets a self-heal clause; the scattered deterministic failure checks get unified into one `review()` seam called at the two delivery points (`core.handle_message`, `core._execute_message_schedule`); the P1.5 router adds a cheap triage pass in front — no LLM reviewer is built.

---

## 2. Findings per question

### Q1 — Is there literally a reviewer that inspects every outbound response before delivery?

**No — not a judge. Yes — a deterministic filter.** [V]

- OpenClaw's agent loop ends in an **output-filtering + dedup stage**, enumerated on the agent-loop page as exactly three transforms: `NO_REPLY` silent-token filtering, messaging-tool duplicate removal, and a fallback tool-error reply "if no valid payloads remain after filtering and a tool failed (unless a messaging tool already replied)." (https://docs.openclaw.ai/concepts/agent-loop, mirror https://www.howtouseopenclaw.com/en/concepts/agent-loop). These are string/payload rules, not a model re-reading the answer for intent satisfaction.
- The docs are explicit that quality is *not* enforced by a review pass: *"Safety guardrails in the system prompt are advisory, not enforcement"* — enforcement is tool policy + sandbox + approvals (https://docs.openclaw.ai/concepts/system-prompt). There is no "read the response, judge it, re-run" component anywhere in the loop.
- Hermes: the `AIAgent.run_conversation()` orchestrator "handles provider selection, prompt construction, tool execution, retries, fallback, callbacks, compression, and persistence" (https://hermes-agent.nousresearch.com/docs/developer-guide/architecture) — "retries/fallback" is **provider failover**, not answer review. The FAQ confirms **no reviewer/judge, no answer-verification workflow** exists (https://hermes-agent.nousresearch.com/docs/reference/faq). [V]

**Consequence for what we build:** the owner's mental model ("something reads the response before giving it and fixes it if wrong") is *partly* real but misattributed. The fixing happens *inside the model's turn* (the CLI loop), not in a post-hoc reviewer. The only true pre-delivery gate is deterministic. Zilla should therefore invest in (a) making the CLI loop self-heal, and (b) a deterministic gate — **not** an LLM reviewer, which neither product has and which would double the quota cost of every turn.

### Q2 — The self-heal story (OGG example): where does recovery happen?

**Inside the model's own agentic loop, fed by the exec tool — not in harness code.** [V] with one [I] on the OGG specifics.

- OpenClaw's loop runs an iterative model↔tool cycle; "tool results flow back into the session transcript" and "if a tool errors… a fallback tool error reply is emitted" only as a *last resort* if nothing else was produced (https://docs.openclaw.ai/concepts/agent-loop). Before that last resort, the errored tool result is just context the model reads and acts on. So a failed `ffmpeg`/converter call returns its stderr into the transcript, the model sees it, and continues. [V for the mechanism; I that the OGG case specifically resolves by the model installing the converter]
- The behavior is *instructed* by the system prompt's **Execution Bias**: *"continue until done or blocked, recover from weak tool results… verify before finalizing"* (https://docs.openclaw.ai/concepts/system-prompt). AGENTS.default adds caution rails around it — *"Before changing config or schedulers… inspect existing state first and preserve/merge"*, and a preflight "prefer existing tools/libraries before building custom" gate (https://docs.openclaw.ai/reference/AGENTS.default). So: persistence + recovery are prompted; destructive setup is hedged.
- **Critical caveat the owner should hear:** OpenClaw's own docs note it typically "runs in a restricted, non-root environment without administrative privileges, which prevents the agent from modifying your system or self-installing software" (skywork.ai skill-dependency guide, secondary [I]). So auto-install is **permission-gated** — it only "just works" where the agent is actually allowed to run the installer. The effortless demo the owner saw implies the agent had exec permission and a package manager it could use without sudo.
- Hermes: same shape — tools "use registry patterns and check_fn gating, not hard dependencies", tool calls are visible via callbacks, but the FAQ's missing-dependency guidance is *reactive/manual* ("ensure the server responds", "check logs"), with no documented auto-install (https://hermes-agent.nousresearch.com/docs/reference/faq). [V]

**Mapping to Zilla's three candidate locations (the crux):**
- **(a) The CLI's own loop** — *this is where the OGG self-heal must live for Zilla.* Claude Code and agy already run an internal run_command/exec loop and already see stderr + exit codes. Verified indirectly in Zilla: `cli_engine._TOOL_DISPLAY` maps `run_command`, `write_to_file`, etc. — the CLIs execute tools and Zilla merely watches the transcript (`cli_engine.py:302`). What Zilla does *not* yet do is (i) **instruct** the CLI to self-heal (install a missing dep, then retry) and (ii) reliably **permit** it. Permission today: agy gets `--dangerously-skip-permissions` and Claude gets its skip flag only when `skip_permissions` is set — derived from `auth.can(uid,"admin")` (`core.py:366-367`, `cli_engine.py:605-606`). So for the owner (admin) the exec permission is already there; the missing piece is the *instruction*.
- **(b) The harness preamble** — the right home for the self-heal *directive*. Today `harness._TRUST_CONTRACT` says *"FAILED is a signal to try another way, not a final answer"* (`harness.py:191`) — close, but it never says "if a tool/dependency is missing, install or set it up, then retry." That one sentence is the highest-leverage change in this whole doc.
- **(c) A Zilla-side review gate** — the *wrong* place for self-heal. Zilla cannot re-drive the CLI's tools from Python; a Zilla gate can only detect a bad outcome and re-ask the CLI (a whole new turn). Reserve (c) for deterministic detection + one corrective re-ask, exactly as `verify.py` already does for hallucination.

### Q3 — Deterministic output processing between model output and the channel

**OpenClaw [V]** (agent-loop + messages pages):
1. `NO_REPLY` (case-insensitive) silent-token filtering — stripped from outgoing payloads; if the turn has media (e.g. TTS audio), the silent text is stripped but the media is still delivered (https://docs.openclaw.ai/concepts/messages).
2. Messaging-tool **duplicate removal** — if the model already sent a message via a messaging tool, the duplicate confirmation in the final payload is dropped (agent-loop step 7).
3. **Fallback tool-error reply** — emitted only if no renderable payload remains and a tool errored, and only if a messaging tool hasn't already replied.
4. **Chunking** — "respects channel text limits and avoids splitting fenced code"; `minChars`/`maxChars` config; block streaming emits partial replies on `text_end` or `message_end`.
5. **Error-copy hygiene** — groups get a *silent* reply for generic failures ("avoid gateway error boilerplate"); DMs get "compact failure copy by default."
6. **Compaction-triggered retry** — on auto-compaction the runtime "can trigger a retry"; "in-memory buffers and tool summaries reset to avoid duplicate output."
7. **ackMaxChars drop** (heartbeat path) — after `HEARTBEAT_OK` is stripped, a remaining reply ≤300 chars is dropped entirely (prior research §3.1; https://docs.openclaw.ai/gateway/heartbeat).
- Known failure mode worth stealing the guard for: LLMs **append** `NO_REPLY` to real content, so it leaks (openclaw issues #30916, #8347); the filter must strip a trailing/embedded token, not only a bare-equals match.

**Hermes [V, thin]:** `/compress` context management; "missing files skipped silently" for shell init; tool calls surfaced via callbacks. No enumerated outbound transform list.

**Zilla today [V]** — the transforms already exist, but scattered across three modules and never unified:
- `cli_engine.strip_ansi` + `clean_response` — strip ANSI/CR, drop CLI "thinking" chatter lines (`cli_engine.py:87-112`).
- `cli_engine.sanitize_response` — drop transcript history lines, long dir listings, metadata lines, JSON debug blocks, the `Warning: conversation "…" not found` banner (`cli_engine.py:115-144`).
- Empty-response handling — `"No response from CLI. Try rephrasing."` on a normal exit with no answer (`cli_engine.py:774-775`).
- Non-normal exit headers — `🛑 Canceled…` / `⏱️ No activity…` / `⚠️ Stopped… (safety ceiling)` prefixes (`cli_engine.py:784-793`).
- Truncation — 10 000-char cap in `get_new_responses` (`cli_engine.py:229-231`); 4 000-char `truncate_for_telegram` (`formatter.py:354`).
- `detect_limit` — 18 rate-limit/quota/overload signals → short reason (`cli_engine.py:459-490`).
- `formatter._clean_raw_text` → header→bold, bullet normalize, HR convert, unicode-unescape, debug-artifact strip, blank-line collapse; then `_to_html`/`_to_markdown_v2` with `escape_html`/`escape_markdown_v2`, `_safe_href` URL guard (`formatter.py:126-284`). This is Zilla's **markdown-repair** layer — richer than OpenClaw's documented one.
- `split_message` — chunk at last newline before the limit, min half-length (`bot.py:316-329`). **Gap vs OpenClaw #4:** does not avoid splitting inside a fenced code block.
- **Zilla has no silent-token concept** (no `NO_REPLY`/`HEARTBEAT_OK` equivalent yet) — needed when P7 heartbeat lands (steal item #1 in the prior list).

### Q4 — Retry / fallback semantics on a failed or garbage turn

**Who detects, what counts as failure, how many retries, does the user see the mess?**
- **OpenClaw [V]:** the only automatic retry is *compaction-triggered* inside the runtime (buffer reset to avoid dup output); cron jobs have the 30s→60s→5m→15m→60m ladder (prior research §3.2). No answer-quality retry. User never sees intermediate output because streaming/partials are the *same* turn; a failed turn ends in the fallback error reply, not a re-run.
- **Hermes [V]:** `AIAgent` does provider **retries + fallback**, but the FAQ frames provider switching as manual (`hermes chat --provider …`). [I] the automatic "retries/fallback" is transient-error/timeout failover between endpoints, not garbage-answer detection.
- **Zilla today [V]:** three independent retry mechanisms already exist —
  1. **Answer-quality retry (the existing seed of review-before-delivery):** `verify.assess(prompt, response)` runs inline after every turn; on a fabrication-shape flag it fires **ONE** corrective re-ask that *continues the same conversation* so the CLI can fix its own prior answer (`cli_engine._run_blocking:842-866`, `verify.py:79-128`). Precision-tuned (numbers-dense + unsourced), logged to `trust_log.jsonl`. The user sees only the final, corrected answer — the intermediate flagged answer is never delivered.
  2. **Schedule retry ladder:** `_execute_message_schedule` classifies failure (empty / `detect_limit` / error-prefix), then `mark_failure` drives a retry ladder before the schedule advances; on give-up the owner is told in plain language and handed any partial output (`core.py:538-693`). **This is already exactly the "deterministic detect → bounded retry → plain-language stop" shape the design section generalizes.**
  3. **Delivery retry:** `safe_send` retries a failed Telegram send 4× with linear backoff (`bot.py:396-408`).
- **User-visible mess:** none in the live-chat path — delivery is a single `Response` event yielded *after* the lock releases and the (optional) corrective retry completes (`core.py:415-440`). Progress is time-driven typing only.

### Q5 — The "ack" so the app never feels dead

- **OpenClaw [V]:** typing indicators (per-channel `useIndicator`), **block streaming** of partial replies, and `tool` stream `start/update/end` events surfaced to the channel — the user watches tools fire in near-real-time. A stop-typing signal is sent when a run ends (incl. the `NO_REPLY` case — openclaw issue #8785). Driven from the gateway/agent-loop streaming layer.
- **Hermes [V]:** CLI spinner + gateway chat messages; "every tool call is visible to the user via callbacks" (architecture page).
- **Zilla today [V]:** `keep_typing` — native Telegram typing bubble 0–60s, then ONE editable "⏳ Working… {elapsed}" status message with a 🛑 Cancel button, edited (never re-sent) every 60s, deleted on completion (`bot.py:332-393`). Progress detail *is computed* — `TranscriptPoller` turns transcript steps into "🌐 Reading web page", "⚙️ Running command", etc. and pushes them as `Progress` events (`cli_engine.py:302-391`, `core.py:376-410`) — **but the Telegram seam consumes them silently** and shows only the time-driven bubble (`bot.py:295-298`: *"Progress events are consumed silently this seam"*). 
- **Gap:** OpenClaw streams the tool-by-tool activity to the user; Zilla builds the exact same signal and throws it away in the Telegram frontend. Surfacing `Progress` into the editable status line (cheap, no new plumbing) is the single biggest "feels alive" win available. TUI (P2) should render them directly.

### Q6 — Other load-bearing "effortless" ingredients Zilla lacks

1. **Block streaming / partial replies** — OpenClaw delivers text as it's produced; Zilla delivers once at end. Not worth building for the PTY backend (transcript is post-hoc), but the *progress surfacing* above recovers most of the felt benefit for free.
2. **An explicit self-heal directive in the system prompt** — OpenClaw's "recover from weak tool results, continue until done or blocked." Zilla's trust contract implies it ("FAILED is a signal to try another way") but never says *install the missing thing and retry*. (Design §4.)
3. **Silent-token vocabulary** (`NO_REPLY`/`HEARTBEAT_OK`) with append-tolerant stripping — prerequisite for P7 heartbeat not spamming. Zilla has none yet.
4. **Code-fence-aware chunking** — OpenClaw won't split a fenced block; `split_message` will.
5. **Exec permission as the true enabler** — the self-heal only *feels* effortless where the agent may run the installer. Zilla's `skip_permissions` (admin-derived) already grants this to the owner; the design must not let a *non-admin* path silently gain install rights (security stays deterministic).

---

## 3. Component-by-component comparison

| Pipeline stage | OpenClaw (source) | Hermes (source) | Zilla today (file:line) | Gap |
|---|---|---|---|---|
| Model↔tool loop (self-heal home) | pi-agent-core; tool results → transcript; model reacts in-turn (agent-loop) | `AIAgent.run_conversation` tool loop (architecture) | **The rented CLI's own loop** — Zilla watches transcript only (`cli_engine.py:302-391`) | Zilla can't re-drive tools; must *instruct* the CLI to self-heal |
| Self-heal instruction | System prompt "Execution Bias": recover from weak tool results, continue until done/blocked (system-prompt) | check_fn gating; no auto-install (faq) | `_TRUST_CONTRACT` "FAILED is a signal to try another way" (`harness.py:191`) | No explicit "install missing dep → retry" clause |
| Tool-error feedback | stderr/exit → transcript; fallback error reply last-resort (agent-loop) | tool calls visible via callbacks (architecture) | CLI-internal; Zilla sees only final transcript answer (`cli_engine.py:174-231`) | Parity via CLI; nothing for Zilla to add here |
| Answer-quality gate | none (advisory only) | none (faq) | `verify.assess` + ONE corrective retry (`cli_engine.py:842-866`, `verify.py`) | **Zilla is ahead** — this is the review seed |
| Deterministic output filter | NO_REPLY strip, messaging dedup, fallback error (agent-loop) | `/compress`; silent skips (faq) | `clean_response`/`sanitize_response`/`detect_limit`/exit headers/empty msg (`cli_engine.py:87-144,459-490,774-793`) | Scattered; no silent-token concept |
| Markdown repair | header/fence-aware chunking (messages) | — | `formatter._clean_raw_text`→HTML/MDv2 + escaping (`formatter.py:126-284`) | **Zilla richer**; chunking not fence-aware |
| Chunking | respects limit, no fence split (messages) | channel limits | `split_message` last-newline split (`bot.py:316-329`) | Splits code fences |
| Empty / failure copy | fallback error; DM compact / group silent (messages) | silent skips | `"No response… Try rephrasing"`; exit-reason headers (`cli_engine.py:774-793`) | Fine; unify into gate |
| Retry / fallback | compaction retry (buffers reset); cron ladder | provider failover (architecture) | verify 1× retry; schedule ladder; `safe_send` 4× (`core.py:538-693`, `bot.py:396`) | Fine; generalize the pattern |
| Ack / progress | typing + block-stream + tool start/update/end (agent-loop) | spinner + tool-call callbacks (architecture) | `keep_typing` editable status; `TranscriptPoller`→`Progress` **built but dropped in TG** (`bot.py:295,332-393`) | Surface Progress to the status line |
| Delivery target | per-channel visibility; stop-typing on end (issue #8785) | gateway | `send_response`/`_deliver_scheduled_result` + `safe_send` (`bot.py:626,1166,396`) | Parity |

---

## 4. DESIGN — the Zilla response-review seam

Owner-decreed orchestrator gate. **It is not an LLM judge.** It is a deterministic pre-delivery check plus a *bounded* self-heal, split across the harness (in-loop) and core (post-loop). Design principle carried from HANDOFF: **security decisions are never model-judged**; here that means the *permission* to run install/exec commands is decided by Zilla (role → `skip_permissions`), and only the *how-to-recover* is delegated to the CLI.

### 4.1 Two layers, matching where recovery actually happens

**Layer A — in-loop self-heal (harness directive, no new code path).** Add one clause to `harness._TRUST_CONTRACT` / a new `_SELF_HEAL` block injected every turn:

> SELF-HEAL: If a tool fails because something is missing or not set up — a binary not installed, a converter/codec absent, a package not present, a service not running — do not give up and do not report failure to the user. First try to fix it yourself with the tools you have (install the dependency, create the file/dir, start the service), then retry the original action. Only if the fix itself fails, or would be destructive/irreversible/require spending money, stop and tell the user in plain language exactly what is missing and what you tried. Fix silently; report only the outcome.

This is the OGG story for Zilla. It works *only* when the CLI has exec permission — which it does for the owner via `--dangerously-skip-permissions` (`cli_engine.py:605`) / Claude's skip flag, gated on `auth.can(uid,"admin")` (`core.py:366`). **Do not** widen that gate for the self-heal; a limited user's turn must not silently gain install rights. Pair with the AGENTS.default caution rail: instruct "inspect existing state before changing config/schedulers; prefer existing tools before building custom" so self-heal doesn't rampage.

**Layer B — deterministic outbound gate (`zilla/review.py`, new; unifies existing checks).** A single pure function called at the two delivery points:

```
review(user_message, response, *, exit_reason=None) -> ReviewResult
    verdict: "deliver" | "retry" | "stop"
    reason:  short machine tag (for trust_log)
    user_note: plain-language text when verdict == "stop"
    retry_prompt: the corrective re-ask when verdict == "retry"
```

Checks, evaluated in order, **all deterministic** (no model call):
1. **empty** — `not response.strip()` → `stop` ("I didn't get any output back — try rephrasing?"). (Today: `cli_engine.py:774`.)
2. **limit** — `detect_limit(response)` truthy → `stop` with the model-switch suggestion (bot already has this UX). (Today: `cli_engine.py:481`, `core.py:580`.)
3. **error-garbage** — `response` starts with `_SCHED_FAIL_PREFIXES` (`Error:` / `Claude error:` / `⏱️` / `⚠️ Stopped`) or `exit_reason != "normal"` → `stop`, preserve the header + any partial. (Today: `core.py:526,582`.)
4. **fabrication** — `verify.assess(user_message, response)` returns reasons → `retry` with `verify.correction_prompt(user_message)`, **once**. (Today: `cli_engine.py:847`.)
5. else → `deliver`.

`review()` is just the *consolidation* of logic that already exists in three files into one testable seam — low risk, and it gives P1.5 a single hook.

### 4.2 Where it hooks in

- **Live chat — `core.handle_message`:** today `verify` runs *inside* `cli_engine._run_blocking` (`cli_engine.py:824-875`). Keep the fabrication retry there (it needs to continue the same conversation, which the engine owns), but call `review()` in `handle_message` right before yielding the `Response` (`core.py:415`→`435`) for the empty/limit/error verdicts, so the *frontend-facing* classification lives in core with the rest of the turn pipeline. The `retry` verdict is already consumed one layer down; core handles only `deliver`/`stop`, attaching `user_note` to `Response.meta` so any frontend renders it uniformly.
- **Schedules — `core._execute_message_schedule`:** this **already implements the whole gate inline** (`core.py:577-586`: empty → limit → error-prefix classification, then `_run_and_record`'s ladder). Refactor it to call the same `review()` so live and scheduled turns share one definition of "did not really succeed." The retry ladder (`mark_failure`) stays schedule-only.

### 4.3 Interaction with the P1.5 orchestration router (cheap triage pass)

HANDOFF's P1.5 adds a cheap triage classifier in front of the turn. The review seam is its *symmetric back half*: triage decides **how** to run (backend/model/browser/complexity — already partly in `autoharness.classify`/`needs_browser`, `cli_engine.py:828-833`); `review()` decides **whether the result ships**. Keep them separate functions but let the router own both calls so there is one place that sees "planned X, got Y" — the natural home for a future (post-P10, opt-in) model-judged reviewer *if the owner ever wants one*. Until then the back half is 100% deterministic and adds **zero** model calls on the happy path (checks 1–3 are regex/string; check 4 already runs today).

### 4.4 Latency & cost

- Happy path: `review()` is pure string/regex — **sub-millisecond, no quota**. It replaces no existing latency; it consolidates checks already running.
- `retry` verdict: one extra CLI turn — same cost as today's hallucination retry, bounded to **exactly one**. Never loops.
- Self-heal (Layer A): cost is *inside* the CLI turn the user already paid for (extra tool calls, not extra Zilla turns) — the same place OpenClaw's cost lives. No Zilla-side multiplier.

### 4.5 Deterministic vs model-judged — the line

| Decision | Who | Why |
|---|---|---|
| Is the response empty / rate-limited / error-garbage? | **Zilla (deterministic)** | Cheap, exact, no quota |
| Does it look like fabricated data? | **Zilla (deterministic regex)** | `verify.assess` — precision-tuned, already shipped |
| *How* to recover a missing dependency | **CLI model (in-loop)** | Only the loop can re-drive tools; instructed by harness |
| May the agent run install/exec commands at all | **Zilla (deterministic, role-based)** | Security is never model-judged — `skip_permissions` from `auth.can` |
| Is the answer *semantically* correct / intent-satisfying | **Nobody** (deliver it) | Neither OpenClaw nor Hermes judges this; building an LLM judge doubles cost for marginal gain — explicitly out of scope until post-P10 |

---

## 5. Steal-list delta (continues numbering from RESEARCH_OPENCLAW_HERMES.md §7, which ended at 30)

Legend as before: **ADOPT** / **ADAPT** / **SKIP**. Phase = HANDOFF P1–P10 (P1.5 = orchestration router).

| # | Item (source) | Verdict | How, for CLI-not-API + zero budget | Phase |
|---|---|---|---|---|
| 31 | **Deterministic output filter as one seam** — NO_REPLY/dedup/fallback-error shape (OpenClaw agent-loop step 7) | **ADAPT** | Build `zilla/review.py`: unify `verify` + `detect_limit` + empty/exit-reason checks into one `review()` returning deliver/retry/stop. Call at `core.handle_message:415` and reuse in `_execute_message_schedule:577`. Pure, no model call. | P1.5 |
| 32 | **In-loop self-heal directive** — "recover from weak tool results, continue until done or blocked" (OpenClaw system-prompt Execution Bias) | **ADOPT** | Add `_SELF_HEAL` clause to the harness preamble (§4.1). This is the OGG story's actual home for Zilla — the CLI loop, not a review pass. | P0/harness |
| 33 | **AGENTS.default caution rails** — inspect state before changing config/schedulers; prefer existing tools before custom (OpenClaw AGENTS.default) | **ADOPT** | Bolt onto the self-heal clause so autonomous fixing stays non-destructive. Keeps self-heal from clobbering the owner's system. | P0/harness |
| 34 | **Silent-token vocabulary with append-tolerant stripping** — NO_REPLY leaks when appended (OpenClaw issues #30916/#8347/#8785) | **ADAPT** | When P7 heartbeat lands, define `HEARTBEAT_OK`/`NO_REPLY`; strip even when the model *appends* it to real text; on a bare token drop the message AND send stop-typing (issue #8785). Fits `review()` as a pre-check. | P7 |
| 35 | **Surface tool-by-tool progress** — tool start/update/end streamed (OpenClaw agent-loop) | **ADOPT** | Zilla already computes it (`TranscriptPoller`→`Progress`) then drops it (`bot.py:295`). Render the latest `Progress` into the editable "⏳ Working…" status line. Near-zero cost, biggest "feels alive" win. TUI renders directly. | P2 |
| 36 | **Fallback tool-error reply** — never end a turn with nothing when a tool failed (OpenClaw agent-loop) | **ADAPT** | Zilla's `"No response… Try rephrasing"` (`cli_engine.py:775`) is the analog; upgrade it to prefer the last tool error / partial from the transcript over the generic line, via `review()`'s `stop` note. | P1.5 |
| 37 | **Code-fence-aware chunking** (OpenClaw messages: "avoids splitting fenced code") | **ADOPT** | Teach `split_message` (`bot.py:316`) not to break inside a ``` block — split at the fence boundary or hard-wrap the block. Small, self-contained. | P2 |
| 38 | **Exec permission as the deterministic enabler of self-heal** (OpenClaw: restricted non-root env limits auto-install) | **ADOPT** | Document that self-heal only works where `skip_permissions` is granted, and that this stays **role-derived, never model-judged** (`core.py:366`). Non-admin turns must not silently gain install rights. | P1.5/P10 security |
| 39 | **Provider/answer separation of triage vs review** (OpenClaw loop shape; Hermes AIAgent) | **ADAPT** | P1.5 router owns both the front triage (`autoharness.classify`) and back `review()`; single place that sees "planned X → got Y". Leaves a clean seam for an *optional, post-P10* model reviewer without building one now. | P1.5 |
| 40 | **No LLM reviewer / judge** (verified absent in both) | **SKIP** (record the decision) | Neither product reads-and-judges the finished answer; an LLM judge doubles per-turn quota for marginal recall. Explicit non-goal until an owner need appears; the P1.5 seam (#39) is where it *would* attach. | — |

---

*Report generated 2026-07-17 by the research subagent. Only this file was created; no code or config was modified.*
