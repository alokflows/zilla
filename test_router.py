# ============================================================
#  TESTS — Phase R1: triage router + effort controller (PLAN.md §10 "Accept:")
# ============================================================
#  Deterministic, no-network tests for:
#    - router.classify(): a table of ≥ 30 messages → command/share/trivial/
#      normal, including everything that must NOT be caught by the narrow
#      patterns.
#    - router.resolve_effort(): the fixed priority — owner emphasis and an
#      explicit `!deep` beat the classifier absolutely, a trivial message is
#      fast, everything else is standard. The model is never asked.
#    - effort_map: validation at settings-WRITE time (an entry naming agy is
#      refused, with a reason), defaults per machine, and dispatch —
#      ctx.model reaching claude/opencode's `--model`.
#    - THE agy RULE: nothing in the routing path may write agy's model. The
#      agy settings file is byte-compared before and after a routed turn.
#    - core: a fast-profile turn does not touch the session's conv id, and a
#      fast turn that comes back empty is silently rerun as a normal one.
#
#  Run:  .venv/bin/python test_router.py
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
_tmpdir = tempfile.mkdtemp(prefix="zilla_r1_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)", "toolPermission": "ask"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.core as zcore  # noqa: E402
import zilla.memory as memory  # noqa: E402
from zilla import router  # noqa: E402
from zilla.core import Response, Progress, ZillaCore  # noqa: E402
from zilla.harness import TurnContext  # noqa: E402
from zilla.schedules import ScheduleManager  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402

OWNER = 111

# A stand-in binary so _installed() can be steered without a real CLI.
_FAKE_BIN = os.path.join(_tmpdir, "fake-cli")
with open(_FAKE_BIN, "w", encoding="utf-8") as f:
    f.write("#!/bin/sh\n")


def _with_backends(claude=True, opencode=False):
    """Set which backends look installed. Returns the previous pair."""
    old = (config.CLAUDE_PATH, config.OPENCODE_PATH)
    config.CLAUDE_PATH = _FAKE_BIN if claude else os.path.join(_tmpdir, "nope-claude")
    config.OPENCODE_PATH = _FAKE_BIN if opencode else os.path.join(_tmpdir, "nope-oc")
    return old


def _restore_backends(old):
    config.CLAUDE_PATH, config.OPENCODE_PATH = old


def _fresh_core(tag: str):
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.db"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.db"), OWNER)
    sched = ScheduleManager(os.path.join(_tmpdir, f"schedules_{tag}.db"))
    return ZillaCore(sessions=sessions, auth=auth, schedules=sched)


# ============================================================
#  1. The classifier table
# ============================================================

_CASES = [
    # (message, expected class)
    ("/start", router.COMMAND),
    ("/tasks", router.COMMAND),
    ("/bg research the market", router.COMMAND),
    ("  /help", router.COMMAND),
    ("hi", router.TRIVIAL),
    ("hello", router.TRIVIAL),
    ("hey there", router.TRIVIAL),
    ("good morning", router.TRIVIAL),
    ("thanks", router.TRIVIAL),
    ("thank you so much", router.TRIVIAL),
    ("ok", router.TRIVIAL),
    ("cool", router.TRIVIAL),
    ("got it", router.TRIVIAL),
    ("bye", router.TRIVIAL),
    ("yes", router.TRIVIAL),
    ("Thanks!", router.TRIVIAL),
    ("remember I prefer tea", router.SHARE),
    ("note down the gate code is 4417", router.SHARE),
    ("fyi the shop closes at 6", router.SHARE),
    ("for your reference: Priya handles ops", router.SHARE),
    ("what is the weather", router.NORMAL),
    ("draft a reply to the landlord", router.NORMAL),
    ("hi, can you check the invoice?", router.NORMAL),
    ("thanks — now do the other one", router.NORMAL),
    ("ok so what did we decide about the lease", router.NORMAL),
    ("remember?", router.NORMAL),
    ("do you remember the plan", router.NORMAL),
    ("yes or no: is the shop open", router.NORMAL),
    ("summarise this document", router.NORMAL),
    ("call Priya", router.NORMAL),
    ("", router.NORMAL),
    ("   ", router.NORMAL),
    ("hello world, build me a website", router.NORMAL),
    ("noted, but check the numbers again", router.NORMAL),
]


def _run_classify_tests():
    print(f"\n[1] router.classify — {len(_CASES)} cases, normal is the safe default")
    for msg, expected in _CASES:
        got = router.classify(msg)
        check(f"{msg!r:45} -> {expected}", got == expected, got)


# ============================================================
#  2. Effort priority
# ============================================================

def _run_effort_tests():
    print("\n[2] resolve_effort — the owner outranks the classifier, always")

    for msg in ("think hard about this", "think this through", "take your time",
                "please be thorough", "do it properly", "think really carefully",
                "let's do a deep dive on suppliers"):
        effort, _text, why = router.resolve_effort(msg, router.classify(msg))
        check(f"emphasis -> deep: {msg!r}", effort == router.DEEP and why == "emphasis",
              (effort, why))

    effort, text, why = router.resolve_effort("!deep hi", router.TRIVIAL)
    check("`!deep` on a one-word trivial message still goes deep",
          effort == router.DEEP and why == "prefix", (effort, why))
    check("the `!deep` marker is stripped before the model sees it",
          text == "hi", repr(text))

    effort, text, _ = router.resolve_effort("!deep", router.NORMAL)
    check("a bare `!deep` doesn't erase the message", text.strip() != "", repr(text))

    check("a trivial message is fast",
          router.resolve_effort("hi", router.TRIVIAL)[0] == router.FAST)
    check("everything else is standard",
          router.resolve_effort("draft the invoice", router.NORMAL)[0] == router.STANDARD)
    check("a share is standard, not fast",
          router.resolve_effort("remember I like tea", router.SHARE)[0] == router.STANDARD)

    # The words alone, mid-sentence, are not an instruction to Zilla.
    for msg in ("I carefully read the contract", "he took his time with it",
                "the deep end of the pool"):
        check(f"not an emphasis marker: {msg!r}",
              router.resolve_effort(msg, router.NORMAL)[0] == router.STANDARD)

    check("effort never comes from the model — resolve_effort is pure text",
          router.resolve_effort("please use your best model", router.NORMAL)[0]
          == router.STANDARD)


# ============================================================
#  3. effort_map validation (at settings-WRITE time)
# ============================================================

def _run_effort_map_tests():
    print("\n[3] effort_map — agy is refused at write time, with a reason")

    ok = router.validate_effort_map({"fast": "claude:haiku",
                                     "deep": {"backend": "claude", "model": "opus"}})
    check("both spellings normalize to the same shape",
          ok == {"fast": {"backend": "claude", "model": "haiku"},
                 "deep": {"backend": "claude", "model": "opus"}}, ok)
    check("empty is empty, not an error", router.validate_effort_map(None) == {}
          and router.validate_effort_map({}) == {})

    def _refused(value, needle):
        try:
            router.validate_effort_map(value)
            return False, "accepted"
        except ValueError as e:
            return needle in str(e).lower(), str(e)

    okk, msg = _refused({"fast": "agy:Gemini 3.1 Pro"}, "agy")
    check("an agy entry is refused", okk, msg)
    check("and the refusal explains why (one global setting, shared)",
          "global" in msg and "terminal" in msg, msg)
    check("an unknown backend is refused", _refused({"fast": "gpt:4"}, "backend")[0])
    check("a backend with no model is refused", _refused({"fast": "claude"}, "model")[0])
    check("an unknown effort name is refused", _refused({"turbo": "claude:haiku"}, "effort")[0])
    check("a nonsense value is refused", _refused({"fast": 7}, "backend:model")[0])
    check("a non-mapping is refused", _refused(["claude"], "mapping")[0])

    # The real gate: config.set_setting itself.
    try:
        config.set_setting("effort_map", {"deep": "agy:whatever"})
        wrote = True
    except ValueError:
        wrote = False
    check("set_setting('effort_map', agy) raises instead of storing it", not wrote)
    check("and nothing was stored", config.get_setting("effort_map", None) in (None, {}))

    config.set_setting("effort_map", {"fast": "claude:haiku"})
    check("a valid map is stored normalized",
          config.get_setting("effort_map") == {"fast": {"backend": "claude",
                                                        "model": "haiku"}},
          config.get_setting("effort_map"))
    config.set_setting("effort_map", None)


def _run_target_tests():
    print("\n[4] target_for — defaults per machine, uninstalled means no target")
    old = _with_backends(claude=True)
    try:
        check("with claude present, fast is the cheapest claude model",
              router.target_for(router.FAST) == ("claude", "haiku"),
              router.target_for(router.FAST))
        check("and deep is the strongest",
              router.target_for(router.DEEP) == ("claude", "opus"),
              router.target_for(router.DEEP))
        check("standard is always the session's own backend",
              router.target_for(router.STANDARD) == (None, None))

        config.set_setting("effort_map", {"deep": "claude:sonnet"})
        check("an owner-set map wins over the default",
              router.target_for(router.DEEP) == ("claude", "sonnet"))
        check("an effort the map doesn't mention keeps its default target",
              router.target_for(router.FAST) == ("claude", "haiku"))

        config.set_setting("effort_map", {"fast": "off"})
        check("'off' is an explicit no-target — the session's backend runs it",
              router.target_for(router.FAST) == (None, None))

        # A map that went stale (hand-edited, or a backend removed) must not
        # break every turn.
        config.get_setting  # (read path only)
        from zilla import store as _store
        _store.get_store(config.DB_FILE).set_setting("effort_map", {"fast": "agy:x"})
        check("a stored map that is invalid degrades to the defaults, not a crash",
              router.target_for(router.FAST) == ("claude", "haiku"),
              router.target_for(router.FAST))
        config.set_setting("effort_map", None)
    finally:
        _restore_backends(old)

    old = _with_backends(claude=False, opencode=True)
    try:
        backend, model = router.target_for(router.FAST)
        check("with only opencode present, fast targets opencode",
              backend == "opencode" and bool(model), (backend, model))
        check("and there is no deep target to promise",
              router.target_for(router.DEEP) == (None, None))
    finally:
        _restore_backends(old)

    old = _with_backends(claude=False, opencode=False)
    try:
        check("agy-only machine: no fast target",
              router.target_for(router.FAST) == (None, None))
        d = router.decide("hi")
        check("…so a trivial message gets NO fast profile — a fresh agy conv "
              "costs the global new-conv lock and would be slower",
              d.effort == router.FAST and d.fast_profile is False, d)
    finally:
        _restore_backends(old)


def _run_decide_tests():
    print("\n[5] decide — one call carries class, effort, target and profile")
    old = _with_backends(claude=True)
    try:
        d = router.decide("hi")
        check("trivial + a target = the fast profile",
              d.klass == router.TRIVIAL and d.effort == router.FAST
              and d.fast_profile is True and d.backend == "claude", d)
        d = router.decide("!deep count the stock")
        check("`!deep` routes to the deep target",
              d.effort == router.DEEP and d.backend == "claude"
              and d.model == "opus", d)
        check("and the marker never reaches the model", d.text == "count the stock")
        d = router.decide("draft the invoice")
        check("an ordinary message runs on the session backend",
              d.effort == router.STANDARD and d.backend is None
              and d.fast_profile is False, d)
        check("the decision logs as flat fields",
              set(router.decide("hi").as_log()) ==
              {"class", "effort", "why", "target", "model"})
        check("a demoted decision is an ordinary turn",
              router.decide("hi").demoted().fast_profile is False)
    finally:
        _restore_backends(old)


# ============================================================
#  6. Dispatch — ctx.model reaches --model, and agy never does
# ============================================================

def _run_dispatch_tests():
    print("\n[6] dispatch — the per-turn model reaches claude/opencode only")
    from zilla import backend_registry as reg
    from zilla import backends as _backends
    seen = {}

    def _fake_claude(prompt, conv, **kw):
        seen["claude"] = kw.get("model")
        return "ok", "conv-1"

    def _fake_opencode(prompt, conv, **kw):
        seen["opencode"] = kw.get("model")
        return "ok", "conv-2"

    old = (_backends.run_claude, _backends.run_opencode)
    try:
        _backends.run_claude, _backends.run_opencode = _fake_claude, _fake_opencode
        ctx = TurnContext(uid=OWNER, role="owner", is_owner=True,
                          effort="deep", backend="claude", model="opus")
        reg.get("claude").dispatch("p", None, None, None, False, ctx=ctx)
        check("claude gets the effort's model", seen.get("claude") == "opus", seen)

        reg.get("opencode").dispatch("p", None, None, None, False, ctx=ctx)
        check("opencode gets it too", seen.get("opencode") == "opus", seen)

        plain = TurnContext(uid=OWNER, role="owner", is_owner=True)
        reg.get("claude").dispatch("p", None, None, None, False, ctx=plain)
        check("with no override, the configured model is used",
              seen.get("claude") == config.get_model_for("claude"), seen)

        check("the agy adapter has no model flag at all — structurally, effort "
              "can never switch agy's model",
              reg.get("agy").model_flag is False)
        import inspect
        src = inspect.getsource(reg._agy_dispatch)
        check("and its dispatch passes no model", "model" not in src, src)
    finally:
        _backends.run_claude, _backends.run_opencode = old


def _agy_file_state():
    with open(_fake_agy, "rb") as f:
        return f.read()


def _run_agy_untouched_test():
    print("\n[7] THE agy rule — routing never writes agy's model file")
    old = _with_backends(claude=True)
    core = _fresh_core("agy")
    before = _agy_file_state()
    original = zcore.run_cli_async
    calls = []

    async def _fake(prompt, conv_id=None, progress_callback=None, cancel_event=None,
                    skip_permissions=False, ctx=None):
        calls.append(ctx)
        return "an answer", "conv-x"

    try:
        zcore.run_cli_async = _fake

        async def _turn(msg):
            events = []
            async for ev in core.handle_message(OWNER, msg):
                events.append(ev)
            return events

        events = asyncio.run(_turn("!deep plan the whole quarter"))
        check("the deep turn ran", any(isinstance(e, Response) for e in events))
        check("the owner is told it will think longer (P4)",
              any(isinstance(e, Progress) and "deep" in e.text.lower() for e in events),
              events)
        check("the turn carried the deep effort and its target",
              calls[-1].effort == "deep" and calls[-1].backend == "claude"
              and calls[-1].model == "opus", calls[-1])
        check("agy's settings file is byte-for-byte unchanged",
              _agy_file_state() == before)
        check("agy's model reads the same as before",
              config.get_model_for("agy") == "Gemini 3.1 Pro (High)")
    finally:
        zcore.run_cli_async = original
        _restore_backends(old)


# ============================================================
#  8. The fast profile through the real turn pipeline
# ============================================================

def _run_fast_profile_tests():
    print("\n[8] a fast-profile turn leaves the session's conversation alone")
    old = _with_backends(claude=True)
    core = _fresh_core("fast")
    original = zcore.run_cli_async
    seen = []

    async def _fake(prompt, conv_id=None, progress_callback=None, cancel_event=None,
                    skip_permissions=False, ctx=None):
        seen.append({"conv_id": conv_id, "ctx": ctx})
        return "Hey!", "throwaway-conv"

    try:
        zcore.run_cli_async = _fake
        core.sessions.set_active_name("main", OWNER)
        core.sessions.set_conversation_id("real-conv", user_id=OWNER,
                                          session_name="main", backend="agy")

        # 'hi' is trivial — but the smalltalk one-shot would spawn a real
        # claude process, so point the classifier's fast path at the full
        # pipeline by using a message that is trivial only after the marker
        # is stripped is not possible; instead disable the one-shot.
        old_fast = zcore._run_fast_claude
        zcore._run_fast_claude = lambda prompt: None   # unreachable ⇒ full path

        async def _turn(msg):
            out = []
            async for ev in core.handle_message(OWNER, msg):
                out.append(ev)
            return out

        events = asyncio.run(_turn("hi"))
        reply = [e for e in events if isinstance(e, Response)][-1]
        check("the reply still arrives", reply.text == "Hey!", reply)
        check("the fast turn ran in a throwaway conversation, not the session's",
              seen[-1]["conv_id"] is None, seen[-1])
        check("it carried the fast profile",
              seen[-1]["ctx"].fast_profile is True
              and seen[-1]["ctx"].effort == "fast", seen[-1]["ctx"])
        check("THE conv id the session holds is untouched",
              core.sessions.get_conversation_id(user_id=OWNER, session_name="main")
              == "real-conv")
        check("and the response reports no conv id of its own",
              reply.meta.get("conv_id") is None, reply.meta)

        # Misclassification safety: an empty fast answer reruns as normal.
        answers = ["", "The real answer."]

        async def _empty_then_real(prompt, conv_id=None, progress_callback=None,
                                   cancel_event=None, skip_permissions=False, ctx=None):
            seen.append({"conv_id": conv_id, "ctx": ctx})
            return answers.pop(0), "conv-rerun"

        zcore.run_cli_async = _empty_then_real
        seen.clear()
        events = asyncio.run(_turn("hi"))
        reply = [e for e in events if isinstance(e, Response)][-1]
        check("an empty fast answer is silently rerun as a normal turn",
              len(seen) == 2 and reply.text == "The real answer.", (len(seen), reply.text))
        check("the rerun uses the session's own conversation",
              seen[1]["conv_id"] == "real-conv", seen[1])
        check("the rerun is a standard-effort turn with no override",
              seen[1]["ctx"].effort == "standard" and seen[1]["ctx"].backend is None
              and seen[1]["ctx"].fast_profile is False, seen[1]["ctx"])
        check("the owner is never told the cheap attempt happened",
              "rerun" not in reply.text.lower() and "fast" not in reply.text.lower())
        check("the rerun's conv id IS recorded — it was a real session turn",
              core.sessions.get_conversation_id(user_id=OWNER, session_name="main")
              == "conv-rerun")
        zcore._run_fast_claude = old_fast
    finally:
        zcore.run_cli_async = original
        _restore_backends(old)


def _run_lean_injection_test():
    print("\n[9] the fast profile is leaner — core MEMORY.md, no wiki index")
    tmp = tempfile.mkdtemp(prefix="zilla_r1_mem_")
    olds = (memory.MEMORY_DIR, config.MEMORY_DIR)
    memory.MEMORY_DIR = config.MEMORY_DIR = os.path.join(tmp, "Memory")
    try:
        from zilla import harness as _harness
        memory.ensure_tree()
        os.makedirs(os.path.join(memory.MEMORY_DIR, "Wiki", "People"), exist_ok=True)
        with open(os.path.join(memory.MEMORY_DIR, "Wiki", "People", "priya.md"),
                  "w", encoding="utf-8") as f:
            f.write("# Priya\nOperations lead.\n- type:: person\n")

        normal = _harness._memory_block(
            TurnContext(uid=OWNER, role="owner", is_owner=True))
        fast = _harness._memory_block(
            TurnContext(uid=OWNER, role="owner", is_owner=True, fast_profile=True))
        check("the normal block carries the wiki index", "Wiki index" in normal)
        check("the fast block does not", "Wiki index" not in fast, fast[:200])
        check("the fast block never claims the wiki is empty",
              "no wiki pages yet" not in fast)
        check("but the owner's core memory is still there",
              "Your memory" in fast and "Memory protocol" in fast)
        check("and it is genuinely smaller", len(fast) < len(normal))
    finally:
        memory.MEMORY_DIR, config.MEMORY_DIR = olds
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("  PHASE R1 — ROUTER + EFFORT CONTROLLER TESTS")
    print("=" * 60)
    _run_classify_tests()
    _run_effort_tests()
    _run_effort_map_tests()
    _run_target_tests()
    _run_decide_tests()
    _run_dispatch_tests()
    _run_agy_untouched_test()
    _run_fast_profile_tests()
    _run_lean_injection_test()
    print("\n" + "=" * 60)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 60)
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
