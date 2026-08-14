# ============================================================
#  TESTS — Phase R2: backend fallback chain (PLAN.md §10 "Accept:")
# ============================================================
#  Deterministic, no-network tests for:
#    - chain.order(): the owner's declared priority (agy → opencode →
#      claude), overridable by setting, always filtered to what's installed.
#    - chain.should_fallback(): TRIGGER DISCIPLINE. Error channels only —
#      and the required negative: a long, correct answer that happens to
#      contain "quota" and "429" must NOT be thrown away and re-asked.
#    - eligibility: a backend that is installed but logged out is skipped
#      (and the reason logged), never handed the turn.
#    - the walk through core.handle_message: one attempt per backend, one
#      clean answer with a footnote naming who produced it, the session's
#      own conversation untouched, usage.fallbacks bumped, and an honest
#      plain-language stop when the chain runs out.
#
#  Run:  .venv/bin/python test_chain.py
# ============================================================

import asyncio
import json
import os
import shutil
import sys
import tempfile

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


# ── Isolate config BEFORE zilla is imported ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_r2_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

# Tests must never write into the owner's real ~/Zilla (logs, media,
# Memory). config binds every path off ZILLA_HOME at import time, so this
# has to happen before the first zilla import in this file.
import os as _os, tempfile as _tf  # noqa: E402
_os.environ.setdefault("ZILLA_HOME", _tf.mkdtemp(prefix="zilla_test_home_"))
_os.makedirs(_os.path.join(_os.environ["ZILLA_HOME"], "Runtime", "logs"), exist_ok=True)
import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.core as zcore  # noqa: E402
from zilla import chain  # noqa: E402
from zilla import health  # noqa: E402
from zilla import store as _store  # noqa: E402
from zilla.core import Response, Progress, ZillaCore  # noqa: E402
from zilla.review import review  # noqa: E402
from zilla.schedules import ScheduleManager  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402

OWNER = 111

# A long, CORRECT answer that talks about rate limits. The whole point of
# the shape gate: this must never look like being rate-limited.
_PROSE_ABOUT_LIMITS = (
    "HTTP 429 means 'Too Many Requests'. The server is telling you that you "
    "have exceeded your quota for a given window, and it usually includes a "
    "Retry-After header saying how long to wait. In practice you handle it "
    "with exponential backoff: wait, retry, and widen the gap each time. "
    "Most providers publish separate quota buckets for requests per minute "
    "and tokens per minute, so a 429 can mean either one is exhausted. "
    "If you are seeing them constantly, the fix is usually batching rather "
    "than retrying harder — overloaded servers do not reward persistence."
)


def _fresh_core(tag: str):
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.db"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.db"), OWNER)
    sched = ScheduleManager(os.path.join(_tmpdir, f"schedules_{tag}.db"))
    return ZillaCore(sessions=sessions, auth=auth, schedules=sched)


def _all_installed(*names):
    return {b: (b in names) for b in chain.DEFAULT_ORDER}


# ============================================================
#  1. The chain order
# ============================================================

def _run_order_tests():
    print("\n[1] chain.order — the owner's priority, filtered to reality")
    config.set_setting("backend_chain", None)
    check("the default is the owner's declared priority",
          chain.order(_all_installed("agy", "opencode", "claude"))
          == ["agy", "opencode", "claude"])
    check("a backend that isn't installed is not in the chain",
          chain.order(_all_installed("agy", "claude")) == ["agy", "claude"])
    check("nothing installed is an empty chain, not a crash",
          chain.order(_all_installed()) == [])

    config.set_setting("backend_chain", ["claude", "agy"])
    check("the owner can reorder it",
          chain.order(_all_installed("agy", "opencode", "claude")) == ["claude", "agy"])
    config.set_setting("backend_chain", "claude, opencode")
    check("a comma/space string works too",
          chain.order(_all_installed("agy", "opencode", "claude")) == ["claude", "opencode"])
    config.set_setting("backend_chain", ["claude", "claude", "gpt5", "", "agy"])
    check("duplicates and unknown names are dropped",
          chain.order(_all_installed("agy", "opencode", "claude")) == ["claude", "agy"])
    config.set_setting("backend_chain", None)


# ============================================================
#  2. Trigger discipline — the heart of R2
# ============================================================

def _run_trigger_tests():
    print("\n[2] should_fallback — error channels only")

    def _fires(user_msg, response, exit_reason=None):
        return chain.should_fallback(
            review(user_msg, response, exit_reason=exit_reason), response)

    fired, why = _fires("what is 2+2", "")
    check("an empty response fires the chain", fired and why == "empty", why)

    fired, why = _fires("do the thing", "Error: agy exited 1")
    check("a backend error fires it", fired and why == "error", why)

    fired, why = _fires("do the thing", "fine", exit_reason="crashed")
    check("a non-normal exit fires it", fired and why == "error", why)

    fired, why = _fires("hi", "⚠️ Stopped: rate limited")
    check("an error-shaped limit stub fires it", fired and why == "limit", why)

    fired, why = _fires("run it", "Error: 429 quota exceeded, try later")
    check("a short 429 error line fires it", fired, why)

    # THE required negative test.
    fired, why = _fires("what does HTTP 429 mean?", _PROSE_ABOUT_LIMITS)
    check("a LONG correct answer containing 'quota' and '429' does NOT fire "
          "the chain — it is the answer, not a failure",
          fired is False, (fired, why))
    check("…and review() no longer calls it a limit at all: the shape gate "
          "lives in the outbound gate, so the answer ships untouched",
          review("what does HTTP 429 mean?", _PROSE_ABOUT_LIMITS).verdict == "deliver")
    check("the chain still refuses a limit verdict handed to it in prose "
          "(defence in depth for other callers)",
          chain.should_fallback(
              type("R", (), {"verdict": "stop", "reason": "limit"})(),
              _PROSE_ABOUT_LIMITS) == (False, "limit_in_prose"))

    fired, why = _fires("what is 2+2", "4")
    check("a normal short answer never fires it", fired is False, why)

    long_ok = "Here is the plan.\n\n" + ("Step by step, carefully. " * 40)
    fired, why = _fires("plan it", long_ok)
    check("a long normal answer never fires it", fired is False, why)

    check("a canceled turn's own notice is not an error channel",
          _fires("stop", "🛑 Canceled by you.")[0] is False)


def _run_shape_tests():
    print("\n[3] is_error_shaped — short and structured, never prose")
    check("empty is error-shaped", chain.is_error_shaped(""))
    check("a fail prefix is", chain.is_error_shaped("Error: nope"))
    check("a short stub is", chain.is_error_shaped("429 Too Many Requests"))
    check("a long paragraph is not", chain.is_error_shaped(_PROSE_ABOUT_LIMITS) is False)
    check("multi-paragraph text is not",
          chain.is_error_shaped("Short.\n\nAlso short.") is False)
    check("a few sentences of real prose is not",
          chain.is_error_shaped("Yes. That works. I checked it. It is fine.") is False)


def _run_copy_tests():
    print("\n[4] the owner-facing pieces")
    p = chain.primer("find me three suppliers in Kochi")
    check("the primer carries the owner's own words",
          "find me three suppliers in Kochi" in p, p)
    check("and says it is context, not the task",
          p.startswith("Context:"), p)
    check("a huge message is trimmed",
          len(chain.primer("x" * 5000)) < chain.PRIMER_MAX_CHARS + 80)

    t = chain.with_footnote("The answer.", "claude")
    check("the footnote names who answered", t.endswith("↷ answered via claude"), t)
    check("the answer itself is untouched", t.startswith("The answer."), t)

    note = chain.exhausted_note(["agy", "claude"])
    check("the exhausted note names what was tried",
          "agy" in note and "claude" in note, note)
    check("it is plain language with no stack trace",
          "Traceback" not in note and "Error:" not in note, note)
    check("and it tells the owner nothing was lost",
          "nothing is lost" in note.lower(), note)


# ============================================================
#  5. Eligibility — logged out is not eligible
# ============================================================

def _run_eligibility_tests():
    print("\n[5] health.login_ok — a logged-out CLI never gets the turn")
    health.reset_cache()
    old = health._LOGIN_PROBES.copy()
    try:
        health._LOGIN_PROBES["claude"] = lambda force=False: {"ok": False,
                                                              "detail": "logged out"}
        health._LOGIN_PROBES["opencode"] = lambda force=False: {"ok": True,
                                                                "detail": "reachable"}
        r = health.login_ok("claude")
        check("a logged-out backend reports not ok, with a reason",
              r["ok"] is False and "logged out" in r["detail"], r)
        check("a live one reports ok", health.login_ok("opencode")["ok"] is True)
        check("an unknown backend is never eligible",
              health.login_ok("gpt5")["ok"] is False)

        # Freshness: a cached result inside max_age is reused; a stale one is
        # re-probed rather than trusted.
        calls = []

        def _counting(force=False):
            calls.append(force)
            return {"ok": True, "detail": "probed"}

        health._LOGIN_PROBES["agy"] = _counting
        health._cache.pop("agy_login", None)
        first = health.login_ok("agy", max_age=600)
        check("a missing probe result is probed on demand",
              first["ok"] and first["stale"] is True and len(calls) == 1, calls)
        # health._cached() stores under the probe's own key only when the
        # probe goes through it; seed the cache directly to test freshness.
        health._cache["agy_login"] = {"ok": True, "detail": "cached",
                                      "ts": __import__("time").time()}
        second = health.login_ok("agy", max_age=600)
        check("a fresh cached result is reused, not re-probed",
              second["stale"] is False and len(calls) == 1, calls)
        health._cache["agy_login"]["ts"] -= 10_000
        third = health.login_ok("agy", max_age=600)
        check("a stale result is re-probed on demand — never a dead chain",
              third["stale"] is True and len(calls) == 2, calls)
    finally:
        health._LOGIN_PROBES.clear()
        health._LOGIN_PROBES.update(old)
        health.reset_cache()


# ============================================================
#  6. The walk, through the real turn pipeline
# ============================================================

def _install(monkey: dict):
    """Point ZillaCore._installed_backends at a fixed set."""
    ZillaCore._installed_backends = staticmethod(lambda: dict(monkey))


def _run_walk_tests():
    print("\n[6] the chain walk — one attempt per backend, one clean answer")
    core = _fresh_core("walk")
    original = zcore.run_cli_async
    old_installed = ZillaCore._installed_backends
    old_probes = health._LOGIN_PROBES.copy()
    db = _store.get_store(config.DB_FILE)
    calls = []

    async def _fake(prompt, conv_id=None, progress_callback=None, cancel_event=None,
                    skip_permissions=False, ctx=None):
        backend = (ctx.backend if ctx and ctx.backend else "agy")
        calls.append({"backend": backend, "conv_id": conv_id, "prompt": prompt})
        if backend == "agy":
            return "Error: 429 rate limited", None
        if backend == "opencode":
            return "", None                      # eligible, but also fails
        return "Here is the real answer.", "claude-conv"

    try:
        zcore.run_cli_async = _fake
        health.reset_cache()
        health._LOGIN_PROBES.update({
            b: (lambda force=False: {"ok": True, "detail": "up"})
            for b in ("agy", "opencode", "claude")})
        _install({"agy": True, "opencode": True, "claude": True})
        core.sessions.set_active_name("main", OWNER)
        core.sessions.set_conversation_id("agy-conv", user_id=OWNER,
                                          session_name="main", backend="agy")

        async def _turn(msg="what is the plan"):
            out = []
            async for ev in core.handle_message(OWNER, msg):
                out.append(ev)
            return out

        events = asyncio.run(_turn())
        reply = [e for e in events if isinstance(e, Response)][-1]

        check("the chain walked each backend exactly once",
              [c["backend"] for c in calls] == ["agy", "opencode", "claude"],
              [c["backend"] for c in calls])
        check("the owner gets ONE answer, from the backend that worked",
              reply.text.startswith("Here is the real answer."), reply.text)
        check("with a footnote naming it", "↷ answered via claude" in reply.text,
              reply.text)
        check("the failed attempts are not shown to the owner",
              "429" not in reply.text and "Error" not in reply.text, reply.text)
        check("each rescue attempt ran in a FRESH conversation",
              all(c["conv_id"] is None for c in calls[1:]), calls)
        check("each rescue carried one primer line of context",
              all(c["prompt"].startswith("Context: the owner was just asking about:")
                  for c in calls[1:]), calls[1]["prompt"][:60])
        check("and the owner's actual message came with it",
              all("what is the plan" in c["prompt"] for c in calls[1:]))
        check("THE session's own conversation is untouched",
              core.sessions.get_conversation_id(user_id=OWNER, session_name="main")
              == "agy-conv")
        check("the session keeps its own backend tag",
              core.sessions.get_conv_backend(OWNER, "main") == "agy")
        check("the response still points at the session's own conversation, "
              "not the rescue's",
              reply.meta.get("conv_id") == "agy-conv", reply.meta)
        check("the owner is told what is happening while it walks (P4)",
              any(isinstance(e, Progress) and "trying" in e.text for e in events))

        usage = {u["backend"]: u for u in db.usage_for_day(
            __import__("datetime").datetime.now().strftime("%Y-%m-%d"))}
        check("every rescue attempt is counted as a fallback",
              usage.get("opencode", {}).get("fallbacks") == 1
              and usage.get("claude", {}).get("fallbacks") == 1, usage)
        check("the primary backend is not counted as a fallback of itself",
              usage.get("agy", {}).get("fallbacks", 0) == 0, usage)
    finally:
        zcore.run_cli_async = original
        ZillaCore._installed_backends = old_installed
        health._LOGIN_PROBES.clear()
        health._LOGIN_PROBES.update(old_probes)
        health.reset_cache()


def _run_skip_and_exhaust_tests():
    print("\n[7] logged-out entries are skipped; an exhausted chain says so")
    core = _fresh_core("skip")
    original = zcore.run_cli_async
    old_installed = ZillaCore._installed_backends
    old_probes = health._LOGIN_PROBES.copy()
    calls = []

    async def _fake(prompt, conv_id=None, progress_callback=None, cancel_event=None,
                    skip_permissions=False, ctx=None):
        backend = (ctx.backend if ctx and ctx.backend else "agy")
        calls.append(backend)
        if backend == "claude":
            return "Rescued.", "c1"
        return "Error: backend fell over", None

    try:
        zcore.run_cli_async = _fake
        health.reset_cache()
        health._LOGIN_PROBES.update({
            "agy": lambda force=False: {"ok": True, "detail": "up"},
            "opencode": lambda force=False: {"ok": False, "detail": "not logged in"},
            "claude": lambda force=False: {"ok": True, "detail": "up"},
        })
        _install({"agy": True, "opencode": True, "claude": True})
        core.sessions.set_active_name("main", OWNER)

        async def _turn(msg="do the thing"):
            out = []
            async for ev in core.handle_message(OWNER, msg):
                out.append(ev)
            return out

        reply = [e for e in asyncio.run(_turn()) if isinstance(e, Response)][-1]
        check("the logged-out backend is skipped entirely",
              "opencode" not in calls, calls)
        check("and the next eligible one answers",
              calls == ["agy", "claude"] and reply.text.startswith("Rescued."),
              (calls, reply.text))

        # Now nothing works.
        calls.clear()

        async def _all_broken(prompt, conv_id=None, progress_callback=None,
                              cancel_event=None, skip_permissions=False, ctx=None):
            calls.append(ctx.backend if ctx and ctx.backend else "agy")
            return "Error: backend fell over", None

        zcore.run_cli_async = _all_broken
        reply = [e for e in asyncio.run(_turn()) if isinstance(e, Response)][-1]
        check("an exhausted chain tried every eligible backend once",
              calls == ["agy", "claude"], calls)
        check("and says so honestly, in plain language",
              "couldn't get an answer" in reply.text
              and "nothing is lost" in reply.text.lower(), reply.text)
        check("no stack trace reaches the owner",
              "Traceback" not in reply.text and "Error:" not in reply.text, reply.text)

        # Nothing else installed: the honest single-backend failure is kept,
        # not replaced with chain language about backends the owner has none of.
        calls.clear()
        _install({"agy": True, "opencode": False, "claude": False})
        reply = [e for e in asyncio.run(_turn()) if isinstance(e, Response)][-1]
        check("with no other backend, nothing is retried",
              calls == ["agy"], calls)
        check("and the owner gets the original failure, not chain talk",
              "couldn't get an answer from any" not in reply.text, reply.text)
    finally:
        zcore.run_cli_async = original
        ZillaCore._installed_backends = old_installed
        health._LOGIN_PROBES.clear()
        health._LOGIN_PROBES.update(old_probes)
        health.reset_cache()


def _run_no_false_trigger_test():
    print("\n[8] a good answer never walks the chain")
    core = _fresh_core("good")
    original = zcore.run_cli_async
    old_installed = ZillaCore._installed_backends
    calls = []

    async def _fake(prompt, conv_id=None, progress_callback=None, cancel_event=None,
                    skip_permissions=False, ctx=None):
        calls.append(ctx.backend if ctx and ctx.backend else "agy")
        return _PROSE_ABOUT_LIMITS, "agy-conv-2"

    try:
        zcore.run_cli_async = _fake
        _install({"agy": True, "opencode": True, "claude": True})
        core.sessions.set_active_name("main", OWNER)

        async def _turn():
            out = []
            async for ev in core.handle_message(OWNER, "what does HTTP 429 mean?"):
                out.append(ev)
            return out

        reply = [e for e in asyncio.run(_turn()) if isinstance(e, Response)][-1]
        check("the answer about 429s is delivered as-is",
              reply.text == _PROSE_ABOUT_LIMITS, reply.text[:80])
        check("no second backend was ever called", calls == ["agy"], calls)
        check("and no footnote was added", "↷" not in reply.text)
        check("the session's conversation advanced normally — this was a "
              "successful turn, not a rescue",
              core.sessions.get_conversation_id(user_id=OWNER, session_name="main")
              == "agy-conv-2")
    finally:
        zcore.run_cli_async = original
        ZillaCore._installed_backends = old_installed


if __name__ == "__main__":
    print("=" * 60)
    print("  PHASE R2 — FALLBACK CHAIN TESTS")
    print("=" * 60)
    _run_order_tests()
    _run_trigger_tests()
    _run_shape_tests()
    _run_copy_tests()
    _run_eligibility_tests()
    _run_walk_tests()
    _run_skip_and_exhaust_tests()
    _run_no_false_trigger_test()
    print("\n" + "=" * 60)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 60)
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
