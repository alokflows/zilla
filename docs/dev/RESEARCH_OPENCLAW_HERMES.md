# OpenClaw + Hermes Agent — Research & Steal List for Zilla

Researched 2026-07-16. Sources: live docs at docs.openclaw.ai and hermes-agent.nousresearch.com/docs, both GitHub repos, plus security reporting (The Hacker News, Infosecurity Magazine, Giskard, SecurityScorecard/Censys data). **[V] = verified from repo/docs. [I] = inference or secondary reporting.**

---

## 1. TL;DR

1. Both targets confidently identified: **OpenClaw** (github.com/openclaw/openclaw, Peter Steinberger, ex-Clawdbot/Moltbot, ~247k stars) and **Hermes Agent** (github.com/NousResearch/hermes-agent, Nous Research, Feb 2026, ~216k stars).
2. Both are the same shape as Zilla: local gateway daemon + messaging connectors + workspace-of-Markdown-files as memory + skills + cron. The only structural difference is the brain: they call model **APIs**; Zilla shells out to logged-in **CLIs**. Zilla's HANDOFF plan is essentially a CLI-native replica of this architecture — the plan is validated, not contradicted.
3. The single most valuable steal is OpenClaw's **heartbeat**: periodic agent turn + `HEARTBEAT.md` checklist + magic `HEARTBEAT_OK` token that gets suppressed so "nothing to do" costs no notification — and a **light context mode** so it costs ~2–5K tokens instead of ~100K.
4. Second most valuable: their **cron job model** (payload types, per-job session mode, retry backoff ladder, delivery targets, agent-creates-jobs-via-tool) — directly upgrades Zilla's `schedules.py`.
5. Skills: both follow the **agentskills.io SKILL.md standard** Zilla already uses; steal OpenClaw's gating metadata (`requires.bins/env/config`) and Hermes' **staged skill writes** (identical to Zilla's planned `skills/pending/` approval gate — independent validation of the owner's design).
6. Security is where OpenClaw teaches by failure: ~40k internet-exposed gateways, a 1-click-RCE CVE in its web Control UI, and 341 malicious skills on its marketplace. **Zilla's biggest security feature is what it doesn't build**: no network gateway, no web UI, no auto-installed third-party skills.
7. Hermes' zero-budget-friendly tricks (pre-run scripts with `wakeAgent:false`, per-job model pinning, char-budgeted memory files) map beautifully onto Zilla's quota-scarce CLI world.

---

## 2. OpenClaw feature inventory

Docs root: https://docs.openclaw.ai/ · Repo: https://github.com/openclaw/openclaw · MIT license. All rows [V] unless noted.

| Feature | How it works | Source |
|---|---|---|
| **Gateway daemon** | One long-lived `openclaw gateway` process owns all messaging surfaces (WhatsApp via Baileys, Telegram via grammY, Slack, Discord, Signal, iMessage, WebChat…); exposes a typed WebSocket API; supervised by launchd/systemd for auto-restart ("always on"). | https://docs.openclaw.ai/concepts/architecture |
| **Channels** | 15+ connectors: WhatsApp, Telegram, Slack, Discord, Google Chat, Signal, iMessage, IRC, Teams, Matrix, Feishu, LINE, Mattermost, Zalo… all through the one gateway. | https://docs.openclaw.ai/channels |
| **Agent workspace** | `~/.openclaw/workspace` = the agent's home dir; bootstrap files injected at session start: `AGENTS.md` (behavior rules), `SOUL.md` (persona), `USER.md` (who the user is), `IDENTITY.md` (agent name/vibe/emoji), `TOOLS.md` (local tool conventions), `MEMORY.md`, optional `HEARTBEAT.md`, and one-time `BOOTSTRAP.md` ("first-run ritual… delete it after the ritual is complete"). Size budgets: `bootstrapMaxChars` 20,000/file, `bootstrapTotalMaxChars` 60,000 — oversize files are truncated in context but left intact on disk. Docs say: treat workspace as private memory, keep it in a **private git repo**. | https://docs.openclaw.ai/concepts/agent-workspace |
| **Memory model** | Three layers, all Markdown: `MEMORY.md` (curated durable facts, loaded every session), `memory/YYYY-MM-DD.md` daily notes (raw working notes; today+yesterday auto-load on `/new`/`/reset`), optional `DREAMS.md`. **Memory flush**: before context compaction, a silent turn reminds the agent to save important context to memory files. Agent distills daily notes → MEMORY.md over time (often during heartbeats). Docs advise capturing *when it is safe to act on a note*, not just the fact. | https://docs.openclaw.ai/concepts/memory |
| **Heartbeat** | Periodic agent turn (default 30m) with a fixed prompt; `HEARTBEAT_OK` reply suppressed. Full detail in §3. | https://docs.openclaw.ai/gateway/heartbeat |
| **Cron/scheduler** | SQLite-persisted jobs; 4 schedule types, 3 payload types, 4 session modes, retry ladders, channel delivery. Full detail in §3. | https://docs.openclaw.ai/automation/cron-jobs |
| **Skills + ClawHub** | agentskills.io-standard `SKILL.md` files; six-source precedence discovery; gating metadata; public registry (ClawHub) with trust scanning. Full detail in §4. | https://docs.openclaw.ai/tools/skills |
| **Multi-agent routing** | Route channels/accounts to isolated agents, each with its own workspace + sessions. | https://docs.openclaw.ai/concepts/multi-agent |
| **Nodes / device control** | macOS/iOS/Android/headless "nodes" connect to the gateway over WebSocket with `role: node` and explicit capability declarations; skills hosted on a node appear in the skill list while it's connected, vanish when it disconnects. | https://docs.openclaw.ai/nodes |
| **Voice** | Voice on macOS/iOS/Android via node apps. | https://github.com/openclaw/openclaw README |
| **Browser + Canvas** | First-class browser control tool; "live Canvas you control" (visual workspace). Docs warn browser control ≈ operator access (see §5). | repo README; https://docs.openclaw.ai/tools |
| **Web Control UI** | Browser control plane for the gateway. Source of their worst CVE (§5). | https://docs.openclaw.ai/web/control-ui |
| **Onboarding** | `npm install -g openclaw@latest && openclaw onboard --install-daemon` — wizard installs the daemon, then the agent runs the `BOOTSTRAP.md` "first-run ritual" in chat (names itself, writes IDENTITY.md/USER.md). | repo README; agent-workspace docs |
| **Config** | Single `~/.openclaw/openclaw.json` (JSON5) — agent model, channels, tools policy, skills, heartbeat, gateway auth — everything in one file. | https://docs.openclaw.ai/gateway/configuration |
| **Chat operators** | `/status`, `/new`, `/reset`, `/think <level>`, `/usage`; session tools `sessions_list`, `sessions_history`, `sessions_send`. | repo README |
| **Security tooling** | `openclaw security audit [--deep|--fix]`; DM pairing; fail-closed gateway auth. Full detail in §5. | https://docs.openclaw.ai/gateway/security |
| **Remote access** | Documented Tailscale-first remote access pattern. | https://docs.openclaw.ai/gateway/tailscale |

---

## 3. Heartbeat + scheduler semantics (the detail the owner asked for)

### 3.1 Heartbeat [V]

Source: https://docs.openclaw.ai/gateway/heartbeat

- **What**: a periodic agent turn "so the model can surface anything that needs attention without spamming you." Default **every 30 minutes** (auto-relaxed to 1h on some auth types — i.e. they already throttle by how scarce the brain is; directly relevant to CLI quotas).
- **Exact injected prompt**: *"Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK."*
- **HEARTBEAT.md**: optional user-editable checklist, "small, stable, and safe to consider every 30 minutes" — can be plain reminders or structured `tasks:` blocks with per-task intervals. Missing/empty file → heartbeat still runs, agent decides.
- **Cheap "nothing to do"**: agent replies `HEARTBEAT_OK`; the token is stripped, and if the remaining reply is ≤ `ackMaxChars` (default 300) the message is **dropped entirely** — periodic execution without notification spam.
- **Cheap in tokens**: `isolatedSession: true` skips full conversation history (**~100K → ~2–5K tokens per beat**); `lightContext: true` limits bootstrap files to just HEARTBEAT.md. Other knobs: `target: "last"` (where alerts deliver), `skipWhenBusy: true` (never interrupts an active turn), `includeReasoning: false`.
- **Per-channel visibility**: `showOk` / `showAlerts` / `useIndicator` per channel; all three false → the heartbeat run is skipped for that channel.
- **Heartbeat vs cron** (their own framing): heartbeat = recurring *attention sweep* in/near the main session, no job records; cron = *detached scheduled work* with run history. Two mechanisms, deliberately not merged.

### 3.2 Cron [V]

Source: https://docs.openclaw.ai/automation/cron-jobs

- **Storage**: SQLite, survives restarts; "the Gateway must run continuously for schedules to fire" (same daemon-held-job-list model the Zilla owner already decided on).
- **Schedule types**: `at` (one-shot ISO or relative `20m`), `every` (`10m`/`1h`/`1d`), `cron` (5/6-field, optional IANA `--tz`, default = host tz), `on-exit` (fires when a watched command exits).
- **Payload types (exactly one per job)** — this taxonomy is the steal:
  1. `--system-event`: enqueue an event into the main session **without invoking the model** (free reminders);
  2. `--message`: a model-backed agent turn;
  3. `--command`: run a shell script on the gateway host (admin automation, distinct from agent tool-exec).
- **Session modes per job**: `main` (shared cron lane), `isolated` (fresh session per run — background reports), `current` (bound to the session that created it), `session:<custom-id>` (persistent named session for multi-run workflows).
- **Delivery**: `announce` (deliver result to a channel only if the agent didn't already message), `webhook` (POST result), `none`. Per-job `--channel`/`--to`.
- **Retry**: one-shot jobs — transient errors retry ×3 with 30s/60s/5m backoff; recurring jobs — consecutive-failure ladder 30s→60s→5m→15m→60m, resets on success; **permanent errors disable the job** (vs Zilla's give-up-but-fire-next-occurrence — Zilla's is friendlier; keep it, add the ladder).
- **Per-job overrides**: `--model`, `--thinking high`, `--wake now`, `--delete-after-run`, `--wait`.
- **CLI**: `cron create/list/get/show/runs/enable/disable/edit/run/remove`.

### 3.3 Hermes cron [V] — the zero-budget refinements

Source: https://hermes-agent.nousresearch.com/docs (cron feature page)

- Created three ways: `/cron add` in chat, `hermes cron create` CLI, or **natural conversation — the agent itself uses an internal `cronjob` tool** (this is exactly Zilla's planned schedule-request bridge, minus the owner-confirm tap Zilla adds on top).
- Storage: plain `~/.hermes/cron/jobs.json` with **atomic writes**; outputs archived to `~/.hermes/cron/output/{job_id}/{timestamp}.md`.
- Each job runs in a fresh agent session; **jobs cannot create cron jobs** (recursion/scheduling-loop guard).
- **Provider snapshot**: a job pins the provider/model configured at creation; if global defaults change later the job "fails safely with alerts rather than silently switching to paid services." (Zilla analog: pin backend+model per schedule; on backend removal, alert instead of silently falling through.)
- **Pre-run scripts with `wakeAgent:false`**: a bash/python check runs first; if data is unchanged, **no LLM call happens at all**. The single best quota-saving idea found in this research.
- **Job chaining** (`context_from` passes a previous job's output in) and **continuable jobs** (replying to a delivered cron message continues that conversation).

### 3.4 Hermes "periodic nudges" [V, thin]

Hermes advertises "agent-curated memory with periodic nudges" and a "background self-improvement review" after turns; the docs page does not specify interval or prompt. [I] It is a lighter-weight heartbeat aimed at memory/skill curation rather than user-facing alerts.

---

## 4. Skills ecosystem in detail

### 4.1 OpenClaw skills [V]

Source: https://docs.openclaw.ai/tools/skills · standard: https://agentskills.io

- **Format**: `SKILL.md` with YAML frontmatter; minimum `name` + `description`. Extra fields: `user-invocable` (exposes as slash command), `disable-model-invocation` (slash-command-only), `command-dispatch: "tool"` + `command-tool` (bypass the model entirely), `homepage`.
- **Gating metadata** (`metadata.openclaw`, JSON5): `requires.bins` (binaries on PATH), `requires.anyBins`, `requires.env`, `requires.config` (config paths must be truthy), `os` filter (`darwin`/`linux`/`win32`), `always`. A skill whose gates fail is simply not offered — broken skills never reach the prompt.
- **Discovery**: six sources, highest→lowest precedence: workspace `skills/` → project `.agents/skills` → personal `~/.agents/skills` → managed `~/.openclaw/skills` → bundled → configured extra dirs/plugins. Scans up to 6 levels deep for `SKILL.md`; name = frontmatter or dir name; highest-precedence duplicate wins.
- **Prompt cost is engineered**: eligible skills inject a compact XML block ≈ **24 tokens/skill** + name/description lengths; a `skills.limits.maxSkillsPromptChars` budget degrades gracefully (drop descriptions before dropping names). Same philosophy as Zilla's `harness.skills_summary()` one-liner index — Zilla is already doing the right thing.
- **Session snapshot + watcher**: skill list is snapshotted at session start; a file watcher (`skills.load.watch`, 250ms debounce) refreshes it mid-session on `SKILL.md` change.
- **Per-agent allowlists**: `agents.defaults.skills: [...]` / per-agent `skills: []`; docs explicitly warn this is *visibility* control, "not a host shell authorization boundary; pair it with sandboxing, OS-user isolation, exec deny/allowlists."
- **Env/API-key injection**: `skills.entries.<name>.apiKey/env/config` injected only for the run's duration, host-side only, "secrets remain out of prompts and logs."
- **ClawHub** (https://docs.openclaw.ai/clawhub): public registry; `openclaw skills install @owner/slug | git:owner/repo@ref | ./local`, `--global`, `skills update --all`; semver + changelogs + download/star counts; publishing gated only by "a GitHub account old enough to pass the upload gate"; `openclaw skills verify` checks a `clawhub.skill.verify.v1` **trust envelope** against VirusTotal + ClawScan + static analysis, non-zero exit on failure. [I] The verify/trust-envelope machinery appears to be the post-ClawHavoc response (see §5) — the age-gate alone demonstrably failed.

### 4.2 Hermes skills [V]

Source: https://hermes-agent.nousresearch.com/docs (skills feature page)

- Same agentskills.io `SKILL.md` (name/description/version/platforms/tags) with recommended body sections: **When to Use / Procedure / Pitfalls / Verification** — a genuinely good authoring template.
- **Autonomous creation triggers** (steal these verbatim for Zilla's P5 preamble): "After completing a complex task (5+ tool calls) successfully. When it hit errors or dead ends and found the working path." Plus: after a user corrects the agent's approach; after discovering a non-trivial workflow.
- Framing: "Memory stores small durable facts that should always be in context, while skills store longer procedures that should load only when relevant" — skills are *procedural memory*; skills self-improve during use (agent patches its own skill after a run).
- **Progressive disclosure**: Level 0 = `skills_list()` returning `{name, description, category}` only; full body loads on demand.
- **Staged writes** (the big one): "Every `skill_manage` write (create / edit / patch / delete / write_file / remove_file) is **staged** instead of committed" behind an optional approval gate — Zilla's `skills/pending/` + one-tap design, independently invented. Hub installs get scanned for "data exfiltration and prompt injection risks."
- Sharing: official optional skills, skills.sh, well-known endpoints, GitHub repos, user-published "taps" (curated skill repos, no infrastructure needed).

---

## 5. Security: practices, incidents, lessons

### 5.1 Documented practices [V]

Source: https://docs.openclaw.ai/gateway/security

- **Explicit trust model**: single trusted operator; "OpenClaw is **not** a hostile multi-tenant security boundary." Multiple trust domains ⇒ separate gateways, separate OS users/hosts.
- **DM `dmPolicy`**: `pairing` (default — unknown sender gets a time-limited code, owner approves via `openclaw pairing approve <channel> <code>`), `allowlist`, `open` (requires explicit `"*"`), `disabled`. Groups: mention-gating + per-group allowlists. Per-sender session isolation: `session.dmScope: "per-channel-peer"`.
- **Prompt injection stance** (quote): injection is "**not solved** by system prompt guardrails alone"; hard enforcement = tool policy + exec approvals + sandboxing + channel allowlists; "Treat links, attachments, and pasted instructions as hostile by default" even from trusted senders. This is exactly Zilla's working agreement ("security decisions are deterministic, never model-judged").
- **Sandboxing**: whole-gateway-in-Docker, or per-tool sandbox containers; workspace mount modes `none`/`ro`/`rw`; `tools.exec: { security: "deny", ask: "always" }`; `tools.elevated` disabled by default.
- **Gateway auth**: required by default (fail-closed), loopback-only bind by default; token/password/trusted-proxy; "never expose unauthenticated on 0.0.0.0."
- **Secrets**: config `600`, `~/.openclaw` `700`, full-disk encryption recommended; credentials never in workspace `.env`.
- **`openclaw security audit [--deep|--fix]`**: checks inbound policies, tool blast radius, file perms, network exposure, browser-control risk, plugin loading, policy drift; `--fix` auto-remediates safe items. Docs ship a copy-paste "hardened 60-second baseline" config and a 4-step incident-response runbook (contain → freeze → rotate → audit).

### 5.2 Public incidents [I — from security press, not the project's docs]

- **Mass exposure (late Jan 2026)**: Censys tracked ~1,000 → 21,000+ publicly exposed gateways in one week; SecurityScorecard later counted **40,214 internet-exposed instances, 35.4% flagged vulnerable**. Root cause: users binding the gateway/Control UI to public interfaces during the viral-adoption spike. (https://conscia.com/blog/the-openclaw-security-crisis/, https://www.sangfor.com/blog/cybersecurity/openclaw-ai-agent-security-risks-2026)
- **CVE-2026-25253, CVSS 8.8 — 1-click RCE**: the web Control UI trusted a `gatewayUrl` query parameter and auto-connected on load, sending the stored gateway token to an attacker's server; attacker then connects to the victim's gateway, flips sandbox/tool policy config, and executes code. (https://thehackernews.com/2026/02/openclaw-bug-enables-one-click-remote.html)
- **ClawHavoc — supply chain**: **341 malicious skills (~12% of ClawHub at the time)**, primarily delivering the Atomic macOS Stealer. The GitHub-account-age upload gate was insufficient. (Sangfor, Infosecurity Magazine)
- **Prompt-injection → RCE and credential leakage** demonstrated by Giskard against real deployments; six further vulnerabilities reported by researchers; a "ClawJacked" covert-hijack bug. (https://www.giskard.ai/knowledge/openclaw-security-vulnerabilities-include-data-leakage-and-prompt-injection-risks, https://www.infosecurity-magazine.com/news/researchers-six-new-openclaw/)
- **Mitigations they added** [I, inferred by comparing current docs to the incident timeline]: fail-closed gateway auth + loopback default, the `security audit` command with `--fix`, pairing-by-default DMs, ClawHub trust envelopes/verification (VirusTotal + ClawScan), the explicit non-goal statement about multi-tenant trust.

### 5.3 Lessons for Zilla

1. **The attack surface Zilla doesn't build is the win.** Every headline OpenClaw incident lived in components Zilla has no plans for: a network-listening gateway, a web Control UI, an open skills marketplace. Telegram long-polling (outbound-only) + local TUI + no listening ports = the entire exposed-gateway class of failure is structurally absent. Keep it that way; if a web UI is ever proposed, this CVE is the counterargument.
2. **Fail-closed and loopback-by-default** — if Zilla ever grows any socket (even the WebBridge health check), auth required + loopback bind from day one.
3. **Skills are a supply chain.** 12% of their marketplace was malware. Zilla's decisions (no marketplace; code skills need one deterministic owner tap; Zilla — not the model — classifies instruction vs code) are exactly the right posture. If skills are ever imported from outside, scan + verify before the approval card, and show the owner *what the skill can touch*.
4. **Prompt injection**: adopt their defense hierarchy (identity → scope → model quality) and their doctrine sentence — guardrail prompts are soft; only Zilla-enforced policy is hard. Zilla's Trap #2 finding (agy/opencode execute tools unattended headlessly) makes OS-level isolation (P10) the *only* real boundary for those backends — OpenClaw's docs independently confirm that judgment.
5. **Steal `security audit` as `zilla doctor --security`**: check config/home file permissions (600/700), token not in argv/logs, no unexpected listening ports, pending-skills gate intact, Telegram owner-ID set, WebBridge bound to localhost. Cheap to build, catches the exact misconfigurations that burned OpenClaw users.
6. **Publish the trust model in one paragraph** ("Zilla is one owner's assistant; it is not a boundary between mutually distrusting users") — their clearest doc habit.

### 5.4 Docs conventions worth copying [V]

- **Agent-facing workspace files with fixed names and one job each** (AGENTS.md = rules, SOUL.md = persona, USER.md = the human, IDENTITY.md = agent's self, TOOLS.md = local conventions, HEARTBEAT.md = periodic checklist, BOOTSTRAP.md = one-time ritual that deletes itself). Self-documenting, git-diffable, user-editable.
- **Explicit token budgets in docs** ("keep it short to avoid token burn", 20K/60K char caps, 24 tokens/skill) — they teach users the cost model instead of hiding it.
- Copy-paste **hardened baseline config** and a 3am-readable **incident runbook** in the security page — matches Zilla's P7 runbook requirement.
- One concept per page; CLI reference auto-generated; a "when to use heartbeat vs cron" decision page. Zilla's HANDOFF.md already follows the spirit; port the pattern into `docs/` as user docs appear.

---

## 6. Hermes agent findings

**Confident identification** (not ambiguous once searched): **Hermes Agent by Nous Research** — https://github.com/NousResearch/hermes-agent, docs https://hermes-agent.nousresearch.com/docs, released Feb 2026, MIT, ~216k stars. It is API-key based (Nous Portal OAuth / OpenRouter / OpenAI / any endpoint — `hermes model` to switch) and even ships `hermes claw migrate` to import an OpenClaw workspace — confirming these two are the same product category and the pair the owner meant. Candidates rejected: Nous Research's *Hermes model series* (LLM weights, not an agent product) and assorted small "Hermes" GitHub chatbots (no traction, wrong shape). [V]

Highlights not already covered above:

- **Layout**: everything under `~/.hermes/` (`memories/`, `skills/`, `cron/`); config via `hermes config get/set` + `hermes setup` wizard; single curl installer for Linux/macOS/WSL2/Termux. [V]
- **Memory**: `MEMORY.md` capped at **2,200 chars (~800 tokens)** for agent notes; `USER.md` capped at **1,375 chars (~500 tokens)** for the user profile; injected as a **frozen snapshot at session start to preserve the LLM prefix cache** (changes appear next session); agent curates via a `memory` tool (add/replace/remove) with exact-duplicate rejection. Session history searchable via **SQLite FTS5** with optional LLM summarization; optional external providers (Honcho user modeling, Mem0, etc.). [V]
- **Execution backends**: local, Docker, SSH, Singularity, Modal, Daytona — a pluggable "where does the shell run" seam. [V]
- **Gateway**: one process fronting Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Email, CLI; DM pairing; command allowlist with approval patterns. [V]
- **TUI**: full terminal UI with multiline editing and slash-command autocomplete — the same product shape Zilla P2 targets. [V]
- Subagent spawning, batch trajectory generation (training-data harvesting — Nous's actual business motive [I]), Home Assistant integration, voice memo transcription. [V]

---

## 7. THE STEAL LIST

Legend: **ADOPT** = take as-is · **ADAPT** = take with CLI-not-API / zero-budget changes · **SKIP** = don't. Phases per HANDOFF.md P1–P10. ⚑ = changes/extends the current HANDOFF plan (summarized after the table).

| # | Feature (source) | Verdict | How, given CLI-not-API + zero budget | Phase |
|---|---|---|---|---|
| 1 | **Heartbeat**: interval turn + HEARTBEAT.md + `HEARTBEAT_OK` suppression (OpenClaw) | **ADOPT** ⚑ | Health tick starts a **fresh, short CLI conversation** (never resume the main one — that's their `isolatedSession`, and it's what keeps a beat at ~2–5K tokens) containing only the heartbeat prompt + a wiki checklist page (`wiki/heartbeat.md`). Copy their prompt nearly verbatim incl. "Do not infer or repeat old tasks from prior chats." Zilla strips `HEARTBEAT_OK` and drops replies ≤300 chars. Quota-aware interval (default 30–60m, back off when `trust_log` shows the day's quota running hot), `skipWhenBusy` via the existing per-user lock. | P7 |
| 2 | **Cron payload taxonomy**: system-event / agent-message / command (OpenClaw) | **ADOPT** ⚑ | Add `payload_type` to `schedules.json`. `system-event` = deliver a canned reminder text with **zero CLI invocation** (free!); `message` = today's behavior; `command` = run a script, agent woken only if it fails/changes. Huge quota saver for "remind me" schedules that currently burn a model turn. | P1 (scheduler extraction) |
| 3 | **Per-job session mode**: isolated / main / named (OpenClaw) | **ADOPT** | `session` field per schedule: fresh conversation per run (default, prevents context pollution) vs named persistent session (weekly report that remembers last week) vs user's main session. Zilla's sessions.py already supports named sessions — just wire it. | P1 |
| 4 | **Retry backoff ladder** 30s/60s/5m/15m/60m, reset on success (OpenClaw) | **ADOPT** | Drop into `schedules.py`'s existing self-heal. Keep Zilla's friendlier "still fires next occurrence" instead of their disable-on-permanent-error. | P1 |
| 5 | **Agent creates schedules via tool** + no-recursion rule (Hermes `cronjob` tool) | **ADOPT** | This IS the owner's already-decided schedule-request bridge (file-pattern like OTP, one confirm tap). Steal Hermes' guard verbatim: **a scheduled run may not create schedules**. Also steal atomic writes for `schedules.json`. | P1/P5 (per HANDOFF note) |
| 6 | **Per-job backend/model pinning + fail-safe alert** (Hermes provider snapshot) | **ADOPT** ⚑ | Store backend+model on each schedule at creation; if that backend is gone at fire time, follow the normal fallback chain **and send the owner a one-time note** — never silently degrade a scheduled report. | P8 |
| 7 | **Pre-run script with `wakeAgent:false`** (Hermes) | **ADOPT** ⚑ | The best zero-budget idea found: a schedule can carry a cheap deterministic pre-check (script/grep); unchanged result ⇒ **no CLI turn at all**. Also use it for heartbeat: only wake the agent if the deterministic checks found something. | P7 |
| 8 | **Continuable scheduled messages** (Hermes) | **ADAPT** | Tag delivered schedule output with its session; a Telegram reply to it resumes that named session. Needs the P1 delivery seam to carry session ids. | P1 design, P2 UX |
| 9 | **HEARTBEAT.md-style user-editable checklist** (OpenClaw) | **ADOPT** | A wiki page the owner edits in plain Markdown = the heartbeat agenda. Keep their doc line: "small, stable, and safe to consider every 30 minutes." | P7 (page lives in P4 wiki) |
| 10 | **Bootstrap file suite** AGENTS/SOUL/USER/IDENTITY.md (OpenClaw) | **ADAPT** | Zilla's harness preamble + wiki cover this, but adopt the *separation*: `wiki/identity.md` (user) vs `wiki/agent.md` (persona/name) vs harness rules (code-owned). Adopt char budgets with truncate-in-context-not-on-disk. | P4 |
| 11 | **BOOTSTRAP.md one-time ritual, delete after** (OpenClaw) | **ADOPT** | Exactly Zilla's first-run interview. Implement as a first-run preamble file the agent consumes and Zilla deletes on completion — a deterministic "interview done" marker that survives crashes mid-onboarding. | P4 |
| 12 | **Daily notes `memory/YYYY-MM-DD.md` + distill-into-MEMORY.md** (OpenClaw) | **ADAPT** ⚑ | Add `wiki/journal/YYYY-MM-DD.md`; harness injects today+yesterday's journal (like their `/new` behavior); heartbeat occasionally instructs: distill journal → wiki pages, prune stale. Cheap, no vector DB, matches owner's grep-only stance. | P4 |
| 13 | **Memory flush before compaction** (OpenClaw) | **ADAPT** | Zilla doesn't control CLI compaction, but can approximate: when starting a NEW conversation (session rotation/limit), first send one silent "save durable facts to the wiki now" turn in the OLD conversation if it's still resumable. Best-effort; don't over-engineer. | P4 |
| 14 | **Memory char caps** (Hermes: MEMORY.md 2,200 chars, USER.md 1,375) | **ADOPT** | Apply per-page injection budgets to the wiki INDEX and to always-injected pages. Concrete numbers to start from. | P4 |
| 15 | **Skill gating metadata** `requires.bins/env/config`, `os` (OpenClaw) | **ADOPT** ⚑ | Add to Zilla's SKILL.md frontmatter conventions; `skills_summary()` silently omits skills whose gates fail (missing binary, wrong OS). Skills viewer shows *why* one is inactive. Pairs perfectly with P6 environment detection. | P5 (+P6) |
| 16 | **Staged skill writes + approval** (Hermes; = OpenClaw allowlists' spirit) | **ADOPT** | Independent validation of Zilla's `skills/pending/` one-tap design. Steal Hermes' granularity: edits/patches/deletes of existing code skills are staged too, not just creation. | P5 |
| 17 | **Skill authoring template** When to Use / Procedure / Pitfalls / Verification (Hermes) | **ADOPT** | Put these four sections in the P5 preamble instruction for skill authoring. | P5 |
| 18 | **Autonomous skill-creation triggers** (Hermes: success after 5+ tool calls; error→working-path; user correction) | **ADOPT** | Verbatim into the harness preamble, plus Zilla's explicit "make that into a skill" command. | P5 |
| 19 | **Prompt-budget engineering for the skills index** (OpenClaw: ~24 tok/skill, degrade descriptions first) | **ADOPT** | `skills_summary()` already one-lines; add a max-chars budget with drop-descriptions-before-names degradation. | P5 |
| 20 | **Skills marketplace (ClawHub)** | **SKIP** (for now) | 12% malware is the cautionary tale; zero budget = no infra to run scanning. If sharing ever matters: import-from-git only + show diff + owner approval + the Hermes-style scan. Skills stay agentskills.io-compatible so import is trivial later. | — (post-P10) |
| 21 | **DM pairing codes** (both) | **ADAPT** | Zilla has owner-ID allowlist + tiers. Add pairing as the *onboarding* path for new limited users: unknown Telegram sender → short-lived code → owner taps approve (reuse Approval-mode UI) → user lands in `limited` tier. Nicer than hand-editing IDs. | P2 (Telegram connector polish) |
| 22 | **`security audit --fix`** (OpenClaw) | **ADOPT** ⚑ | `zilla doctor --security`: file perms on home/config (600/700), secrets not in argv, no listening sockets, WebBridge on localhost only, pending-skill gate intact, owner ID set, token rotated (the known leak in HANDOFF notes). `--fix` chmods and warns. | P2 (doctor) / P10 (checklist grows) |
| 23 | **Trust-model statement + hardened-baseline + incident runbook docs** (OpenClaw) | **ADOPT** | One page in `docs/`: what Zilla protects against, what it doesn't, 3am recovery steps. Feeds P7's alert-runbook requirement. | P7/P10 |
| 24 | **Web Control UI** | **SKIP** | Their worst CVE (1-click RCE via `gatewayUrl`). Zilla's TUI-over-SSH/Tailscale replaces it with zero listening ports. | — |
| 25 | **Network gateway with WS API / multi-agent routing / nodes (device mesh)** | **SKIP** | Zilla is single-owner, single-host by design; the CLI brains already carry the tools. Revisit nodes only if a real second device need appears. | — |
| 26 | **Whole-gateway Docker sandbox / per-tool sandboxes** | **ADAPT** | Can't sandbox inside the CLIs (Trap #2: agy/opencode execute unattended). The equivalent boundary is P10's dedicated user + systemd hardening — OpenClaw's docs confirm OS-level isolation is the fallback when tool policy can't be enforced. Document it as *the* boundary, not an extra. | P10 |
| 27 | **FTS5 session search** (Hermes) | **ADAPT** (cheap) | No new deps needed: sqlite3+FTS5 is in Python's stdlib. But start with grep over transcripts/trust_log per owner's no-vector-DB stance; add FTS5 only if grep proves too slow. Not a phase blocker. | P4 (optional, later) |
| 28 | **`/status`, `/usage`, `/think` chat operators** (OpenClaw) | **ADAPT** | Zilla has menu equivalents; add `/status` + `/usage` (from trust_log quota counters) as text commands in both Telegram and TUI. | P2/P7 |
| 29 | **Multiple exec backends (SSH/Modal/…)** (Hermes) | **SKIP** | The CLI brain owns execution. Zilla's seam is backend choice, which already exists. | — |
| 30 | **Voice via node apps** (OpenClaw) / voice memo transcription (Hermes) | **SKIP** (already planned) | Zilla's P9 Whisper/Google plan already matches; nothing new to take. | P9 |

### ⚑ Flags — where this research changes/extends the HANDOFF plan

1. **P7 heartbeat is now fully specified** (HANDOFF's note "steal OpenClaw's heartbeat idea" → concrete semantics): fresh isolated conversation, ~2–5K-token context, exact prompt, `HEARTBEAT_OK` + ≤300-char suppression, quota-aware interval, deterministic pre-checks before waking the agent (#1, #7, #9). Add to P7 steps.
2. **P1 scheduler extraction should add three fields to the schedule schema now** (cheap during the refactor, painful later): `payload_type` (system-event/message/command), `session` mode, pinned `backend+model` (#2, #3, #6). Plus the backoff ladder (#4) and atomic writes (#5).
3. **P1 delivery seam**: carry session ids on outbound scheduled messages so replies can continue them (#8) — a design constraint on the core API, even if the UX ships later.
4. **P5 gains skill *gating metadata*** (#15) and staged-*edits*-not-just-creates (#16) — small extensions to the already-decided approval design.
5. **P2 `zilla doctor` gains a `--security` mode** (#22); P4 gains journal/daily-notes (#12) and char budgets (#14).
6. **Explicit non-goals to write into HANDOFF** (each backed by an OpenClaw incident): no web UI, no listening network gateway, no skill marketplace/auto-install (#20, #24, #25).

---

*Report generated 2026-07-16 by the research subagent. No Zilla files were modified.*
