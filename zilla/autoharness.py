# ============================================================
#  AUTOHARNESS — Layer 2 (task-aware execution planning)
# ============================================================
#  Zilla is a relay: the backend CLI (agy / claude) already has
#  its own agentic loop. Re-orchestrating it from Python would
#  mean extra CLI round-trips = slower. So AutoHarness does the
#  smart, FAST thing instead: it CLASSIFIES the task and, for
#  complex ones, injects a senior-engineer execution directive so
#  the CLI plans → executes → self-heals inside its single run.
#
#    SIMPLE  (greet, define, translate, calculate, quick Q)
#            → no directive. Lean prompt = fastest possible answer.
#
#    COMPLEX (build/app, research, fetch real data, multi-step,
#             files, automation, browser, deploy)
#            → inject PLAN / TOOLS / STEPS+fallbacks / VERIFY /
#              SELF-HEAL directive. The CLI runs it autonomously
#              and only surfaces the plan if it gets stuck.
#
#  Pure + regex-based: classification is microseconds, so it adds
#  no measurable latency. NOTHING here is task-specific — "make an
#  APK", "use Google Sheets as a backend via the browser", etc. are
#  all just complex tasks the directive + CLI figure out. No
#  hardcoded task types, by design.
# ============================================================

from __future__ import annotations

import re

SIMPLE = "simple"
COMPLEX = "complex"

# Signals that a task needs tools / creation / external action / multi-step work.
_COMPLEX = re.compile(
    r"\b("
    r"build|create|make(?:\s+me)?|develop|generate|design|implement|code|program|"
    r"app|application|apk|website|web\s?app|web\s?site|software|tool|script|bot|"
    r"research|investigate|find(?:\s+out|\s+all|\s+the)?|search|look\s?up|"
    r"fetch|pull|scrape|crawl|download|upload|deploy|publish|host|"
    r"automat\w+|integrat\w+|analyz\w+|summari[sz]e|compile|convert\s+the|"
    r"report|spreadsheet|sheet|csv|excel|database|api|workflow|pipeline|"
    r"screenshot|browse|navigate|fill\s+out|log\s?in|sign\s?in|book|order|"
    r"send\s+(?:an?\s+)?(?:email|message)|scrap\w+|data\b|dataset|records?"
    r")\b",
    re.IGNORECASE,
)

# Multi-step phrasing → almost always complex.
_MULTISTEP = re.compile(
    r"\b(then|after that|and then|step[\s-]?by[\s-]?step|firstly|"
    r"once (?:you|that)|followed by)\b",
    re.IGNORECASE,
)

# Long enough that it's probably a real task even without a keyword.
_LONG_WORDS = 28


def classify(user_message: str) -> str:
    """Return SIMPLE or COMPLEX. Fast, conservative toward SIMPLE so quick
    questions stay snappy; only genuine work triggers the planning directive."""
    m = (user_message or "").strip()
    if not m:
        return SIMPLE
    if _COMPLEX.search(m) or _MULTISTEP.search(m):
        return COMPLEX
    if len(m.split()) > _LONG_WORDS:
        return COMPLEX
    return SIMPLE


# The senior-engineer execution directive injected for COMPLEX tasks. Kept tight
# (perf): it's only added when actually needed.
_PLAN_DIRECTIVE = """\
EXECUTION MODE — complex task. Work like a senior engineer, autonomously:
1. PLAN (silently, first): GOAL (what 'done' concretely means) · TOOLS you'll use ·
   STEPS (numbered, each with a fallback) · VERIFY (how you'll prove each step and
   the final result are REAL, not assumed).
2. EXECUTE the whole plan yourself. Don't ask permission for routine steps — do them.
   Create real files/sheets/apps and put them where they belong; deliver absolute paths.
3. SELF-HEAL: a failed step is a signal, not a stop. Try the fallback, then a different
   approach (another tool, another source). Exhaust real options before giving up.
4. Honour the TRUST CONTRACT: never fabricate to fill a gap. If you genuinely cannot
   finish, state exactly what you tried, what failed, and what you need.
5. Deliver only the final result + files. Show your plan ONLY if you got stuck or need
   a decision from the user."""


# Browser guidance — injected ONLY when the embedded browser is actually attached
# for this turn (see needs_browser). Telling the model it "has a browser" on a turn
# where the tools aren't loaded would just make it waste a step calling a missing tool.
_BROWSER_DIRECTIVE = """\
WEB / BROWSER: You have a real headless browser (Playwright tools: navigate, read the
page's accessibility tree, click, type, fill). For ANY web action, USE it and report only
what the page actually returned — never guess prices, stock, or results. (For quick
read-only look-ups, WebSearch/WebFetch are also fine.)
INTERACTIVE TASKS (shopping, bookings, anything with choices): ask ONE clarifying
question at a time (e.g. size, brand, budget, offers) and END your turn to wait — the next
message continues with full context. Never assume these details; gather them. Present
choices as a numbered list with links (and images when available) so the user can simply
reply with a number to pick.
LOGINS (policy: ASK BEFORE ANY LOGIN): browse and read freely, but before logging into
ANY site, stop and ask the user — they will approve, provide the credential/phone, say
"use my cookies", or hand you an OTP. Relay OTP/2FA prompts to the user; never invent
them. Reuse an already-signed-in browser session when one exists."""


def plan_directive(user_message: str) -> str:
    """The execution directive to inject for this task, or '' for simple ones.
    The browser block is appended only when the browser is attached this turn, so
    the directive always matches the tools the model actually has."""
    blocks = []
    if classify(user_message) == COMPLEX:
        blocks.append(_PLAN_DIRECTIVE)
    if needs_browser(user_message):
        blocks.append(_BROWSER_DIRECTIVE)
    return "\n\n".join(blocks)


# ── Browser gating ──────────────────────────────────────────────────────────
# The embedded Playwright browser costs ~2s of startup on EVERY turn it's loaded
# (and was flaky on top of that). Most COMPLEX tasks don't need it — "build an
# app", "summarise this PDF", "write a script" never touch a live page. So we
# attach the browser only when the message shows genuine interactive-web intent:
# driving a page (navigate/click/fill/login) or commerce (order/buy/book/price).
# Pure read-only look-ups stay on the fast built-in WebSearch/WebFetch.

# A URL or a bare domain (foo.com / amazon.in) → almost certainly a web action.
_URL = re.compile(r"https?://|\bwww\.|\b[a-z0-9-]+\.(?:com|in|org|net|io|co|app|dev|gov|edu|shop|store)\b",
                  re.IGNORECASE)

# Strong, unambiguous browser/automation/commerce verbs.
_BROWSER = re.compile(
    r"\b("
    r"browse|navigate|open\s+(?:the\s+|a\s+|this\s+)?(?:site|page|url|website|link|tab)|"
    r"go\s+to\s+(?:the\s+)?(?:site|page|url|website|http|www)|"
    r"click|fill\s+(?:in|out|the)|type\s+into|submit\s+the|"
    r"log\s?in(?:to)?|sign\s?in(?:to)?|sign\s?up|register\s+(?:on|at|for)|"
    r"order|buy|purchase|checkout|check\s?out|add\s+to\s+cart|cart|"
    r"shop(?:ping)?|book\s+(?:me|a|an|the)|reserve|"
    r"add\s+to\s+(?:wishlist|basket)|place\s+(?:an?\s+)?order"
    r")\b",
    re.IGNORECASE,
)

# Weaker commerce/availability words — only count when paired with web context.
_COMMERCE = re.compile(
    r"\b(price|prices|cheapest|deal|deals|discount|offer|offers|"
    r"in\s+stock|available|availability|product|products|listing)\b",
    re.IGNORECASE,
)
_WEB_CTX = re.compile(r"\b(online|web|internet|on\s+(?:amazon|flipkart|myntra|ebay|the\s+web))\b",
                      re.IGNORECASE)


def needs_browser(user_message: str) -> bool:
    """True if this turn should get the embedded browser. Conservative: keeps
    simple turns and non-web complex tasks (apps, scripts, docs) fast."""
    m = (user_message or "").strip()
    if not m:
        return False
    if _URL.search(m) or _BROWSER.search(m):
        return True
    # "find me the cheapest X online" → commerce intent + a web cue.
    if _COMMERCE.search(m) and _WEB_CTX.search(m):
        return True
    return False
