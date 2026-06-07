"""Unit tests for interactive.py (credential/OTP relay core).

Run: ./.venv/bin/python test_interactive.py
Hermetic — uses a temp bridge dir, no network, no Telegram.
"""
import os
import tempfile
import time

import interactive as I

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}")


def main():
    d = tempfile.mkdtemp(prefix="zilla_bridge_")

    # make_ask validation
    try:
        I.make_ask("bogus", "hi", 123)
        check("make_ask rejects unknown kind", False)
    except ValueError:
        check("make_ask rejects unknown kind", True)

    a = I.make_ask("otp", "Enter the 6-digit code", 555)
    check("make_ask id is 16 hex", bool(I._ID_RE.match(a.id)))
    check("otp is flagged secret", a.is_secret is True)
    check("text is not secret", I.make_ask("text", "name?", 1).is_secret is False)

    # control chars stripped, prompt capped
    dirty = I.make_ask("text", "a\x00b\x1bc" + ("x" * 1000), 1)
    check("control chars stripped", "\x00" not in dirty.prompt and "\x1b" not in dirty.prompt)
    check("prompt length capped", len(dirty.prompt) <= I._MAX_PROMPT)

    # round trip: write ask -> pending -> answer -> read
    I.write_ask(a, d)
    pend = I.read_pending_asks(d)
    check("ask shows as pending", len(pend) == 1 and pend[0].id == a.id)

    I.write_answer(a.id, "123456", d)
    check("answer reads back", I.read_answer(a.id, d) == "123456")
    check("answered ask no longer pending", I.read_pending_asks(d) == [])

    # bad id handling
    try:
        I.write_answer("../etc/passwd", "x", d)
        check("write_answer rejects bad id", False)
    except ValueError:
        check("write_answer rejects bad id", True)
    check("read_answer bad id -> None", I.read_answer("not-hex", d) is None)

    # oversize answer rejected
    try:
        I.write_answer(a.id, "y" * (I._MAX_VALUE + 1), d)
        check("oversize answer rejected", False)
    except ValueError:
        check("oversize answer rejected", True)

    # clear removes both files
    I.clear_ask(a.id, d)
    check("clear removes ask", not os.path.exists(I._ask_path(d, a.id)))
    check("clear removes answer", not os.path.exists(I._answer_path(d, a.id)))

    # pending listing ignores junk filenames
    with open(os.path.join(d, "ask_zzz.json"), "w") as f:
        f.write("{}")
    check("non-hex ask id ignored", I.read_pending_asks(d) == [])

    # expire_stale
    old = I.make_ask("text", "old", 1)
    I.write_ask(old, d)
    # backdate the created time so it counts as stale
    import json
    with open(I._ask_path(d, old.id), "w") as f:
        json.dump({"id": old.id, "kind": "text", "prompt": "old",
                   "chat_id": 1, "created": time.time() - 99999}, f)
    cleared = I.expire_stale(max_age=1800, bridge_dir=d)
    check("expire_stale clears old ask", cleared == 1 and I.read_pending_asks(d) == [])

    print(f"\n{_pass} passed, {_fail} failed")
    return 1 if _fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
