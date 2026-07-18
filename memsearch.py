#!/usr/bin/env python3
"""memsearch.py — full-text search over Memory/**/*.md (Phase M3).

Usage:  python memsearch.py "query"

Prints up to 8 ranked matches as `path:line` followed by a 2-line snippet,
plain text. Exits 0 with "no results" when the query finds nothing. This
is what harness.py's memory block tells the owner-turn agent to run when
it needs to recall something not already in MEMORY.md/the wiki index.
"""

from __future__ import annotations

import sys

from zilla import memory


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not argv[1].strip():
        print('usage: python memsearch.py "query"')
        return 1
    results = memory.search(argv[1])
    if not results:
        print("no results")
        return 0
    for path, line, snippet in results:
        print(f"{path}:{line}")
        for snippet_line in snippet.splitlines():
            print(f"  {snippet_line}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
