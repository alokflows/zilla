# ============================================================
#  TESTS — zilla.review (P1.5 outbound gate + triage classifier)
# ============================================================
#  Pure-logic tests: review() and classify_route() never call a model,
#  never touch the network/filesystem, so these need no config isolation
#  (unlike test_fixes.py/test_core.py) — plain imports are enough.
#
#  Run:  python test_review.py
#  Exit code 0 = all passed, 1 = something failed.
# ============================================================

import sys
from datetime import datetime, timezone, timedelta

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


# Tests must never write into the owner's real ~/Zilla (logs, media,
# Memory). config binds every path off ZILLA_HOME at import time, so this
# has to happen before the first zilla import in this file.
import os as _os, tempfile as _tf  # noqa: E402
_os.environ.setdefault("ZILLA_HOME", _tf.mkdtemp(prefix="zilla_test_home_"))
_os.makedirs(_os.path.join(_os.environ["ZILLA_HOME"], "Runtime", "logs"), exist_ok=True)
from zilla.review import (review, classify_route, is_trivial, clock_answer,  # noqa: E402
                          ReviewResult, FAIL_PREFIXES)


# ── review() ──────────────────────────────────────────────

def test_review_empty():
    print("\n[1] review() — empty / generic-empty -> stop")
    r = review("hi", "")
    check("blank -> stop", r.verdict == "stop", r)
    check("blank -> reason empty", r.reason == "empty")

    r2 = review("hi", "   \n  ")
    check("whitespace-only -> stop", r2.verdict == "stop", r2)

    r3 = review("hi", "No response from CLI. Try rephrasing.")
    check("generic CLI empty -> stop", r3.verdict == "stop", r3)

    r4 = review("hi", "No response from Claude.")
    check("generic Claude empty -> stop", r4.verdict == "stop", r4)

    r5 = review("hi", "  No Response From Claude.  ")
    check("generic-empty case/whitespace insensitive -> stop", r5.verdict == "stop", r5)


def test_review_limit():
    print("\n[2] review() — rate-limit text -> stop")
    r = review("hi", "You hit the rate limit, slow down")
    check("limit -> stop", r.verdict == "stop", r)
    check("limit -> reason limit", r.reason == "limit")
    check("limit -> user_note non-empty", bool(r.user_note))


def test_review_error_prefix():
    print("\n[3] review() — known fail-prefix / non-normal exit -> stop")
    for prefix in FAIL_PREFIXES:
        r = review("hi", f"{prefix} something went wrong")
        check(f"prefix {prefix!r} -> stop", r.verdict == "stop", r)
        check(f"prefix {prefix!r} -> note is verbatim response",
              r.user_note == f"{prefix} something went wrong", r)

    r2 = review("hi", "a perfectly normal-looking answer", exit_reason="idle")
    check("non-normal exit_reason -> stop even with clean text", r2.verdict == "stop", r2)

    r3 = review("hi", "a perfectly normal-looking answer", exit_reason="normal")
    check("normal exit_reason -> not forced to stop", r3.verdict != "stop", r3)


def test_review_fabrication():
    print("\n[4] review() — fabrication heuristic -> retry (once)")
    # Mirrors zilla/verify.py's own "invented dataset with no sourcing" shape
    # (see test_fixes.py's verify: data-request intent detection case) — hedge
    # language on an unsourced data request is one of assess()'s flag shapes.
    user_msg = "get me the exact revenue numbers for last quarter"
    fabricated = "Revenue was approximately $4.2M, roughly 12% higher than last quarter."
    r = review(user_msg, fabricated)
    check("fabrication-shaped answer -> retry", r.verdict == "retry", r)
    check("retry carries a retry_prompt", bool(r.retry_prompt), r)


def test_review_deliver():
    print("\n[5] review() — clean answer -> deliver")
    r = review("what's 2+2", "4")
    check("clean short answer -> deliver", r.verdict == "deliver", r)
    check("deliver has no user_note", r.user_note == "")

    r2 = review("hi", "🛑 Canceled after 5s.")
    check("cancel header text is not treated as a failure", r2.verdict == "deliver", r2)


def test_review_is_pure_and_total():
    print("\n[6] review() — never raises, handles None-ish input")
    r = review("", "")
    check("empty user_message + empty response -> stop, no raise", r.verdict == "stop")
    r2 = review("hi", None)
    check("None response -> stop, no raise", r2.verdict == "stop")
    check("ReviewResult is the return type", isinstance(r2, ReviewResult))


# ── classify_route() ──────────────────────────────────────

def test_classify_smalltalk():
    print("\n[7] classify_route() — conservative smalltalk whitelist")
    smalltalk = [
        "hi", "Hi", "HELLO", "hey", "hey there", "good morning", "gm",
        "thanks", "thank you!", "thanks so much.", "ty", "ok", "okay",
        "cool", "great", "got it", "noted", "bye", "see ya",
    ]
    for msg in smalltalk:
        check(f"{msg!r} -> smalltalk", classify_route(msg) == "smalltalk",
              classify_route(msg))


def test_classify_smalltalk_conservative_exclusions():
    print("\n[8] classify_route() — smalltalk excludes anything with context/questions")
    not_smalltalk = [
        "hi, can you check my email?",
        "hi what time is it",
        "thanks, but can you also do X",
        "ok do it",
        "hello there, remind me to call mom",
        "good morning! what's on my schedule today?",
        "",
        "   ",
        "this is a much longer message that just happens to start with hi but "
        "clearly needs the full agent turn to handle properly",
    ]
    for msg in not_smalltalk:
        check(f"{msg!r} -> NOT smalltalk", classify_route(msg) != "smalltalk",
              classify_route(msg))


def test_classify_share():
    print("\n[9] classify_route() — explicit share verbs only")
    share = [
        "remember to buy milk",
        "Remember: the wifi password is hunter2",
        "note down the meeting is at 5pm",
        "note that the sky is blue today",
        "fyi the server restarted",
        "for your reference here's the address",
    ]
    for msg in share:
        check(f"{msg!r} -> share", classify_route(msg) == "share", classify_route(msg))


def test_classify_share_conservative_exclusions():
    print("\n[10] classify_route() — share excludes questions / bare verb / mid-sentence")
    not_share = [
        "remember?",
        "remember",
        "do you remember what I told you?",
        "I need to remember this",
        "please note down my request",  # share verb must be the LEADING word
    ]
    for msg in not_share:
        check(f"{msg!r} -> NOT share", classify_route(msg) != "share", classify_route(msg))


def test_classify_full_default():
    print("\n[11] classify_route() — everything else defaults to 'full' (safe default)")
    full = [
        "what's the weather today?",
        "summarize this document for me",
        "can you help me debug this error",
        "schedule a reminder for 5pm",
        "how are you",  # a genuine question, not the closed-whitelist smalltalk
    ]
    for msg in full:
        check(f"{msg!r} -> full", classify_route(msg) == "full", classify_route(msg))


# ── R4b: is_trivial() — three matchers behind the shared guards ──

def test_trivial_exact_matcher():
    print("\n[12] is_trivial() — EXACT: the original whitelist, byte-identical")
    exact = [
        "hi", "HELLO", "Yo", "hey there", "good morning", "gm",
        "good night", "thanks!", "thank you.", "ty", "tysm",
        "much appreciated", "ok", "okay", "kk", "alright", "cool",
        "sounds good", "got it", "noted", "no worries", "np",
        "yes", "Nope", "bye", "see ya", "you're welcome",
    ]
    for msg in exact:
        check(f"{msg!r} -> trivial (exact)", is_trivial(msg) is True, msg)


def test_trivial_tokens_matcher():
    print("\n[13] is_trivial() — TOKENS: short acks from the closed vocabulary")
    token_hits = [
        "done",            # CEO's example — a bare new ack word
        "ok done",
        "ok cool",
        "great thanks",
        "hey man",
        "thanks guys",
        "night all",
        "bye then",
    ]
    for msg in token_hits:
        check(f"{msg!r} -> trivial (tokens)", is_trivial(msg) is True, msg)

    token_misses = [
        "yes sir",                    # unknown word ⇒ full path
        "ok where is the invoice",    # content words are never in the vocab
        "done for today",
        "yes and no",
        "cool story",
        "ok do it",                   # pinned since P1.5 — verbs never widen in
        "noted, but check the numbers again",
    ]
    for msg in token_misses:
        check(f"{msg!r} -> NOT trivial (unknown/content words)",
              is_trivial(msg) is False, msg)


def test_trivial_emoji_matcher():
    print("\n[14] is_trivial() — EMOJI: glyph-only messages, no alnum anywhere")
    emoji_hits = ["👍", "🙏🙏🙏", "🎉 🎉", "👍🏻", "❤️", ":)"]
    for msg in emoji_hits:
        check(f"{msg!r} -> trivial (emoji)", is_trivial(msg) is True, msg)

    emoji_misses = [
        "ok 👍",   # ANY alphanumeric anywhere disqualifies
        "2!",
        "a 🙏",
        "ok then 👍 ok then 👍 ok then 👍 ok then 👍",  # >40 normalized chars
    ]
    for msg in emoji_misses:
        check(f"{msg!r} -> NOT trivial", is_trivial(msg) is False, msg)


def test_trivial_hard_negatives():
    print("\n[15] is_trivial() — hard negatives: questions and state never widen")
    hard_negatives = [
        # A question mark anywhere kills it, even on an ack-shaped message.
        "ok?", "done?", "👍?", "yes?", "hi, can you check my email?",
        # State questions — with or without '?', they must hit the full path.
        "what did I say",
        "when is it",
        "remind me ok",
        "schedule tomorrow",
        "tell me when you are done",
        "what did we decide about the lease",
        "hi what time is it",
        # Length guard: a 40+ char ack-shaped string is not small talk.
        "ok " * 14,
        # Empty is nothing, not a greeting.
        "", "   ",
    ]
    for msg in hard_negatives:
        check(f"{msg!r} -> NOT trivial", is_trivial(msg) is False, msg)


def test_router_sees_the_widening():
    print("\n[16] router.classify / resolve_effort — widening can't outrank the owner")
    from zilla import router
    for msg in ("done", "ok cool", "👍", "hey man"):
        check(f"router.classify({msg!r}) -> TRIVIAL",
              router.classify(msg) == router.TRIVIAL, router.classify(msg))
    for msg in ("ok?", "what did I say", "remind me ok", "ok where is my invoice?"):
        check(f"router.classify({msg!r}) stays NORMAL",
              router.classify(msg) == router.NORMAL, router.classify(msg))

    # Emphasis and `!deep` beat the widened classifier, absolutely.
    effort, _text, why = router.resolve_effort("ok, think hard about this",
                                               router.TRIVIAL)
    check("emphasis on an ack-shaped message still goes deep",
          effort == router.DEEP and why == "emphasis", (effort, why))
    effort, text, why = router.resolve_effort("!deep 👍", router.NORMAL)
    check("`!deep` on an emoji message still goes deep",
          effort == router.DEEP and why == "prefix", (effort, why))
    check("…and the marker is stripped, leaving the emoji",
          text == "👍", repr(text))


# ── R4c: the zero-model clock/date route ──

def test_clock_classification():
    print("\n[17] classify_route() — a bare clock/date question -> 'clock'")
    hits = [
        "what time is it",
        "what time is it?",
        "What Time Is It?",
        "what time is it.",           # trailing period normalizes away
        "whats the time",
        "what is the time",
        "what's the date",
        "what is todays date",
        "what day is it",
        "what day is it today?",
        "what's the day",
        "what date is it now",
        "what time is it right now",
    ]
    for msg in hits:
        check(f"{msg!r} -> clock", classify_route(msg) == "clock",
              classify_route(msg))

    misses = [
        # extra words break the anchor — those need a real turn
        "what time is it in London",
        "what time is the meeting",
        "what day is it tomorrow",
        "tell me the time",
        "time please",
        # an explicit share still wins over clock
        "remember what time it is",
        # state questions are untouched
        "what did I say about the lease",
        "when is it",
    ]
    for msg in misses:
        check(f"{msg!r} -> NOT clock", classify_route(msg) != "clock",
              classify_route(msg))


def test_clock_answer_is_plain_and_pure():
    print("\n[18] clock_answer() — one plain sentence, pure in its inputs")
    ist = timezone(timedelta(hours=5, minutes=30))  # fixed offset: hermetic
    now = datetime(2026, 8, 22, 11, 15, tzinfo=timezone.utc)
    ans = clock_answer(now, zone=ist)
    check("answers time, day and date in ONE plain line",
          ans == "It's 4:45 pm, Saturday 22 August 2026.", ans)
    check("same inputs, same answer — no clock reads inside",
          clock_answer(now, zone=ist) == ans)

    midnight = clock_answer(datetime(2026, 1, 1, 18, 30, tzinfo=timezone.utc),
                            zone=ist)
    check("midnight reads am with no leading zero, correct day rollover",
          midnight == "It's 12:00 am, Friday 2 January 2026.", midnight)

    local = clock_answer(datetime.now(timezone.utc))
    check("zone=None resolves the local zone and still answers plainly",
          isinstance(local, str) and local.startswith("It's "), local)


def main():
    tests = [
        test_review_empty,
        test_review_limit,
        test_review_error_prefix,
        test_review_fabrication,
        test_review_deliver,
        test_review_is_pure_and_total,
        test_classify_smalltalk,
        test_classify_smalltalk_conservative_exclusions,
        test_classify_share,
        test_classify_share_conservative_exclusions,
        test_classify_full_default,
        test_trivial_exact_matcher,
        test_trivial_tokens_matcher,
        test_trivial_emoji_matcher,
        test_trivial_hard_negatives,
        test_router_sees_the_widening,
        test_clock_classification,
        test_clock_answer_is_plain_and_pure,
    ]
    print("Running zilla.review tests...\n")
    for t in tests:
        try:
            t()
        except Exception as e:
            global _failed
            _failed += 1
            print(f"  ERROR {getattr(t, '__name__', t)}: {e!r}")
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
