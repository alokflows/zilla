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


from zilla.review import review, classify_route, ReviewResult, FAIL_PREFIXES  # noqa: E402


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
