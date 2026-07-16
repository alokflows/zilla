# ============================================================
#  SCHEDULE PARSE — natural-language → structured schedule
# ============================================================
#  Pure + testable. Returns a dict {kind, spec, title, prompt}
#  or None when the text isn't clearly a schedule request, so it
#  never hijacks a normal chat message.
#
#  Recognized forms (case-insensitive):
#    "every 5 hours <task>"  / "every 30 minutes <task>"
#    "every day at 9am <task>" / "daily at 18:30 <task>"
#    "at 09:00 <task>" / "at 6pm tomorrow <task>"
#    "in 30 minutes <task>" / "in 2 hours <task>"
#    "on mon,wed,fri at 09:00 <task>"
#    "remind me to <task> at 9am"
#  A leading "schedule" / "remind me" is also accepted as a cue.
# ============================================================

import re
from datetime import datetime, timedelta

_WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3,
    "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

# Spelled-out numbers → digits, so "every three minutes" parses exactly like
# "every 3 minutes". Without this the whole message falls through to the agent
# and a trivial request can spin for many minutes instead of becoming a schedule.
_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_ONES_ALT = "|".join(_ONES)
_TENS_ALT = "|".join(_TENS)
# Combos first ("twenty five" → 25), then bare tens, then bare ones — order
# matters so "twenty" inside "twenty five" isn't rewritten on its own.
_COMBO_RE = re.compile(rf"\b({_TENS_ALT})[\s-]+({_ONES_ALT})\b", re.IGNORECASE)
_TENS_RE = re.compile(rf"\b({_TENS_ALT})\b", re.IGNORECASE)
_ONES_RE = re.compile(rf"\b({_ONES_ALT})\b", re.IGNORECASE)


def normalize_numbers(text: str) -> str:
    """Rewrite spelled-out numbers (0–99) to digits, leaving the rest intact."""
    if not text:
        return text
    text = _COMBO_RE.sub(
        lambda m: str(_TENS[m.group(1).lower()] + _ONES[m.group(2).lower()]), text)
    text = _TENS_RE.sub(lambda m: str(_TENS[m.group(1).lower()]), text)
    text = _ONES_RE.sub(lambda m: str(_ONES[m.group(1).lower()]), text)
    return text


def _clean_task(text: str) -> str:
    t = text.strip().strip(",.;:").strip()
    # Strip a leading "to " left over from "remind me to ..."
    t = re.sub(r"^(to|that|me to)\s+", "", t, flags=re.IGNORECASE)
    return t.strip()


def _parse_hhmm(s: str) -> tuple[int, int] | None:
    """Parse '9', '9am', '6pm', '18:30', '9:5' → (hh, mm)."""
    s = s.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", s)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2)) if m.group(2) else 0
    ap = m.group(3)
    if ap == "pm" and hh < 12:
        hh += 12
    elif ap == "am" and hh == 12:
        hh = 0
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return hh, mm
    return None


def parse_schedule_command(argstr: str, now: datetime | None = None) -> dict | None:
    """
    Parse the explicit `/schedule ...` grammar (more forgiving than NL):
      once YYYY-MM-DD HH:MM <task>
      once HH:MM <task>            (today/tomorrow)
      daily HH:MM <task>
      every 5h|30m|2d <task>
      mon,wed,fri HH:MM <task>
    Falls back to the natural-language parser for anything else.
    """
    if not argstr or not argstr.strip():
        return None
    now = now or datetime.now()
    s = argstr.strip()
    head = s.split(None, 1)
    kw = head[0].lower()
    rest = head[1] if len(head) > 1 else ""

    if kw == "once" and rest:
        m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s+(.*)", rest)
        if m:
            try:
                dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
            except ValueError:
                return None
            task = _clean_task(m.group(3))
            if task and dt.timestamp() > now.timestamp():
                return {"kind": "once", "spec": {"run_at": dt.timestamp()},
                        "title": task, "prompt": task}
        # "once HH:MM <task>"
        return parse_schedule("at " + rest, now)

    if kw == "daily" and rest:
        return parse_schedule("daily at " + rest, now)

    if kw == "every" and rest:
        # accept shorthand 5h / 30m / 2d
        rest2 = re.sub(r"^(\d+)\s*([mhd])\b",
                       lambda x: f"{x.group(1)} " +
                       {"m": "minutes", "h": "hours", "d": "days"}[x.group(2)],
                       rest, flags=re.IGNORECASE)
        return parse_schedule("every " + rest2, now)

    # weekday list: "mon,wed,fri HH:MM <task>"
    m = re.match(r"([a-z]{3,9}(?:\s*,\s*[a-z]{3,9})*)\s+(\d{1,2}:\d{2})\s+(.*)",
                 s, flags=re.IGNORECASE)
    if m and all(d.strip().lower() in _WEEKDAYS
                 for d in m.group(1).split(",")):
        return parse_schedule(f"on {m.group(1)} at {m.group(2)} {m.group(3)}", now)

    return parse_schedule(s, now)


def parse_schedule(text: str, now: datetime | None = None) -> dict | None:
    if not text or not text.strip():
        return None
    now = now or datetime.now()
    raw = normalize_numbers(text.strip())
    low = raw.lower()

    # Optional leading cue we can drop before matching the timing clause.
    # Covers spoken phrasings: "put/keep/set/add/create a reminder/timer/alarm".
    body = re.sub(
        r"^\s*(please\s+)?(can\s+you\s+)?"
        r"(schedule|remind\s+me|"
        r"(?:put|keep|set|add|create)\s+(?:a\s+|an\s+)?"
        r"(?:reminder|timer|alarm)(?:\s+for\s+me)?)\b[:,]?\s*",
        "", raw, flags=re.IGNORECASE).strip()
    had_cue = body != raw
    had_timer_cue = had_cue and bool(
        re.search(r"\b(reminder|timer|alarm)\b", raw[:len(raw) - len(body)],
                  flags=re.IGNORECASE))
    blow = body.lower()

    # 1) interval: "every N minute(s)/hour(s)/day(s)"
    m = re.match(r"every\s+(\d+)\s*(min(?:ute)?s?|hours?|hrs?|days?)\b(.*)",
                 blow, flags=re.IGNORECASE)
    if m:
        n = int(m.group(1)); unit = m.group(2).lower()
        per = 60 if unit.startswith("min") else 86400 if unit.startswith("day") else 3600
        task = _clean_task(body[m.end(2):])
        if n > 0 and task:
            return {"kind": "interval", "spec": {"seconds": n * per},
                    "title": task, "prompt": task}

    # 2) "every day / daily at <time> <task>"
    m = re.match(r"(?:every\s*day|daily|each\s*day)\s+at\s+([0-9:apm\s]+?)\s+(.*)",
                 blow, flags=re.IGNORECASE)
    if m:
        hhmm = _parse_hhmm(m.group(1))
        task = _clean_task(body[m.start(2):])
        if hhmm and task:
            return {"kind": "daily", "spec": {"hh": hhmm[0], "mm": hhmm[1]},
                    "title": task, "prompt": task}

    # 3) "on mon,wed,fri at <time> <task>"
    m = re.match(r"(?:on\s+|every\s+)([a-z, ]+?)\s+at\s+([0-9:apm\s]+?)\s+(.*)",
                 blow, flags=re.IGNORECASE)
    if m:
        day_tokens = re.split(r"[ ,]+", m.group(1).strip())
        days = [_WEEKDAYS[d] for d in day_tokens if d in _WEEKDAYS]
        hhmm = _parse_hhmm(m.group(2))
        task = _clean_task(body[m.start(3):])
        if days and hhmm and task:
            return {"kind": "weekly",
                    "spec": {"days": sorted(set(days)), "hh": hhmm[0], "mm": hhmm[1]},
                    "title": task, "prompt": task}

    # 4) "in/after/for N minutes/hours <task>"  → one-off
    #    ("for"/"after" only make sense once a cue like "set a timer" was
    #    stripped — a bare "for 2 minutes ..." is not a schedule request)
    m = re.match(r"(in|after|for)\s+(\d+)\s*(min(?:ute)?s?|hours?|hrs?)\b(.*)",
                 blow, flags=re.IGNORECASE)
    if m and (m.group(1).lower() == "in" or had_cue):
        n = int(m.group(2)); unit = m.group(3).lower()
        per = 60 if unit.startswith("min") else 3600
        task = _clean_task(body[m.end(3):])
        if not task and had_timer_cue:
            task = "Time's up!"
        if n > 0 and task:
            run_at = (now + timedelta(seconds=n * per)).timestamp()
            return {"kind": "once", "spec": {"run_at": run_at},
                    "title": task, "prompt": task}

    # 5) "at <time> [today|tomorrow] <task>"  → one-off
    m = re.match(r"at\s+([0-9:apm\s]+?)\b\s*(today|tomorrow)?\s+(.*)",
                 blow, flags=re.IGNORECASE)
    if m:
        hhmm = _parse_hhmm(m.group(1))
        when = (m.group(2) or "").lower()
        task = _clean_task(body[m.start(3):])
        if hhmm and task:
            target = now.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)
            if when == "tomorrow" or (when != "today" and target <= now):
                target += timedelta(days=1)
            return {"kind": "once", "spec": {"run_at": target.timestamp()},
                    "title": task, "prompt": task}

    # 6) trailing "... at <time>" (e.g. "remind me to call mom at 9am")
    m = re.search(r"\bat\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?)\s*$", body,
                  flags=re.IGNORECASE)
    if m and had_cue:
        hhmm = _parse_hhmm(m.group(1))
        task = _clean_task(body[:m.start()])
        if hhmm and task:
            target = now.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return {"kind": "once", "spec": {"run_at": target.timestamp()},
                    "title": task, "prompt": task}

    return None
