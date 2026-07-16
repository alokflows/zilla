# PHASE 0 FINDINGS — verified on the owner's MacBook, 2026-07-16

Machine: macOS (Darwin 25.5.0), Apple Silicon. Python: Homebrew `python3`.
Test suites: **192 + 16 = 208 passed, 0 failed** (no venv needed; suites
import pure modules directly).

## Installed CLIs & login state

| CLI | Version | Logged in? | Notes |
|---|---|---|---|
| `agy` | 1.1.2 | ✅ yes | `agy models` returns the live catalog (see below) |
| `claude` | 2.1.211 | ✅ yes | `claude auth status`: claude.ai **Pro subscription**, firstParty (no per-call billing; the `total_cost_usd` in `-p` JSON output is notional) |
| `opencode` | 1.18.2 | ⚠️ 0 credentials stored | **Works anyway** via free models (`opencode/*-free`); a live headless run succeeded with no login |

agy model catalog (live, account-scoped): Gemini 3.5 Flash (Medium/High/Low),
Gemini 3.1 Pro (Low/High), **Claude Sonnet 4.6 (Thinking)**, **Claude Opus 4.6
(Thinking)**, GPT-OSS 120B (Medium). agy now proxies Claude models too.

opencode free models: `opencode/big-pickle`, `deepseek-v4-flash-free`,
`hy3-free`, `mimo-v2.5-free`, `nemotron-3-ultra-free`, `north-mini-code-free`.

## Headless/print flags (step 1)

**agy 1.1.2**: `--print/-p`, `--prompt` (alias), `--prompt-interactive/-i`,
`--conversation <id>`, `--continue/-c`, `--model` (**validated — see Trap #1**),
`--add-dir` (repeatable), `--dangerously-skip-permissions`, `--sandbox`,
`--mode {accept-edits, plan}`, `--print-timeout` (default **5m0s**),
`--agent`, `--project` / `--new-project` (new: project grouping). Subcommands:
`models`, `agents`, `plugin`, `update`, `install`, `changelog`.

**claude 2.1.211**: `-p/--print`, `--output-format {text,json,stream-json}`,
`--resume <session_id>`, `--model <alias>`, `--add-dir`,
`--dangerously-skip-permissions`,
`--permission-mode {acceptEdits, auto, bypassPermissions, manual, dontAsk, plan}`,
`--allowedTools` / `--disallowedTools` / `--tools`, `--plugin-dir`.

**opencode 1.18.2**: headless = `opencode run [message]` with
`-m provider/model`, `--variant <effort>`, `-s/--session <id>`,
`-c/--continue`, `--fork`, `--format {default,json}`, `-f/--file <attach>`,
`--dir`, `--title`, `--auto` (auto-approve, "dangerous"). Also: `serve`/
`attach` (HTTP server mode), `export`/`import` (session JSON), `models`,
`auth`, `stats`. Sessions have ids → satisfies the backend contract
(headless run ✓, model flag ✓, conversation persistence ✓).

## Trap verdicts

### Trap #1 — agy model handling: RESOLVED (config.py note is outdated)
agy 1.1.2 has a real `--model` flag and **validates it**: an unknown string
produces `Error: invalid --model … not recognized` plus the available-model
list, **exit code 1**. No silent fallback on the CLI flag path.
`cli_engine.py`'s `--model` usage is correct; keep `set_model`'s
write-then-read-back anyway (harmless, still guards the settings-file path).

### Trap #2 — in-CLI sandbox: PARTIALLY REFUTED (differs per backend)
Probe: headless turn asking to write `pwned-probe` into a temp dir, in each
CLI's most restrictive mode, then check the filesystem.

| Backend | Mode probed | Result |
|---|---|---|
| claude | `-p`, default perms (no skip flag) | **BLOCKED.** Write tool AND Bash redirect denied (`permission_denials` in JSON); file not created. Claude's headless permission system is real now. |
| agy | `--sandbox --print` (no skip flag) | **Executed unattended** — no approval gate; the write happened. But it landed in agy's scratch workspace (`~/.gemini/antigravity-cli/scratch/`), not cwd. Location confinement appears workspace-based; enforcement strength unverified. |
| opencode | `run`, default (no `--auto`) | **Wrote to cwd immediately**, no approval. Headless default is permissive. |

Consequence: the OS-level boundary (Phase 10) remains the real defense for
agy/opencode. But claude now offers meaningful in-CLI restriction
(`--permission-mode`, `--allowedTools`) — usable for limited-tier users when
the claude backend is active.

### Trap #3 — agy auth expiry: probe primitive CONFIRMED, expiry untested
`agy models` returns real data when logged in (verified). Forced-expiry
behavior not testable today. `config.agy_reachable()` stands as the health
check for Phase 7.

### Trap #4 — opencode: VIABLE at zero budget
Runs headless with zero credentials via its free-model tier. Phase 8
integration is unblocked. Default model selection when `-m` is omitted is
opaque — always pass `-m` explicitly in `run_opencode()`.

## Instruction-file ingestion (step 2)

Probe: cwd containing `GEMINI.md` + `AGENTS.md` with a distinctive marker
instruction ("end every reply with MANGO-42"), then one headless turn.

| CLI | Reads it? | Evidence |
|---|---|---|
| agy | **NO** (neither file) | Marker absent from stdout AND from the conversation's `transcript.jsonl` |
| opencode | **YES — `AGENTS.md`** | Reply ended with the marker |
| claude | not probed | Known convention: reads `CLAUDE.md`; test if it ever matters |

Consequence: per-turn harness prompt injection stays the universal
instruction channel; `AGENTS.md` is an extra, opencode-only channel.

## Misc observations

- agy `--print` stdout produced a clean answer in these probes; that does NOT
  license switching off transcript extraction — the invariants in
  `docs/dev/AI_CONTEXT.md` stand (one clean sample refutes nothing).
- claude `-p` default (no `--model`) ran on **claude-opus-4-8** under the Pro
  subscription; fine for probes, but Zilla should keep passing its configured
  alias explicitly.
- Repo checkout on this machine lives at `~/Documents/repos/zilla`
  (older docs say `~/Documents/zilla` — stale path).

## Plan impact (raised to owner before Phase 1)

Nothing blocks Phase 1. Three findings improve the plan:
1. claude's real headless permission modes → limited-tier users can get an
   actual in-CLI restriction when the claude backend is active (strengthens
   Approval mode; note for Phase 1 core design + Phase 10).
2. opencode free tier works with no login → the P8 fallback chain has a
   genuinely free last resort.
3. agy `--model` is validated with a hard error → simpler error handling
   than the "silent fallback" defense the code was written around.
