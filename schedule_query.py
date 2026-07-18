#!/usr/bin/env python3
"""schedule_query.py — read-only, plain-language view of the owner's own
schedules (Phase F5). Same agent-callable CLI convention as memsearch.py.

Usage:  python schedule_query.py            # all of the owner's schedules
        python schedule_query.py <id>       # one schedule + run history

system=1 jobs (heartbeat, nightly distillation, any future Zilla-owned job)
are never shown here — Phase F4 already made those internal, and this tool
follows the same rule. Only the owner's own schedules are visible; another
user's rows are never returned by ScheduleManager.list()/get() for this
owner_id in the first place.
"""

from __future__ import annotations

import sys
from datetime import datetime

from zilla import config
from zilla.schedules import ScheduleManager, describe


def _fmt_ts(ts) -> str:
    if not ts:
        return "never"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def render_list(mgr: ScheduleManager, owner_id: int) -> str:
    items = mgr.list(owner_id)  # system=1 rows excluded by default (Phase F4)
    if not items:
        return "No schedules."
    lines = [f"{len(items)} schedule(s):"]
    for s in items:
        state = "enabled" if s.get("enabled") else "paused"
        lines.append(
            f"[{s['id']}] {s.get('title', '')} — {describe(s['kind'], s['spec'])} "
            f"· next {_fmt_ts(s.get('next_run'))} · {state}"
        )
    return "\n".join(lines)


def render_detail(mgr: ScheduleManager, owner_id: int, sid: str) -> str:
    s = mgr.get(sid)
    if not s or s.get("user_id") != owner_id or s.get("system"):
        return "No such schedule."
    state = "enabled" if s.get("enabled") else "paused"
    lines = [
        f"{s.get('title', '')} (id {s['id']})",
        f"  {describe(s['kind'], s['spec'])}",
        f"  next run: {_fmt_ts(s.get('next_run'))}",
        f"  status: {state}",
        f"  last run: {_fmt_ts(s.get('last_run'))}",
        f"  fail count: {s.get('fail_count', 0)}",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    mgr = ScheduleManager(config.SCHEDULES_FILE)
    owner_id = config.OWNER_CHAT_ID
    if len(argv) > 1 and argv[1].strip():
        print(render_detail(mgr, owner_id, argv[1].strip()))
    else:
        print(render_list(mgr, owner_id))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
