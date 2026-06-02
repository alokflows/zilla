#!/bin/bash
# Zilla installer (macOS) — double-click this file in Finder.
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 install.py
else
  echo "Python 3 is not installed. Install it from https://python.org then try again."
fi
echo
read -n 1 -s -r -p "Press any key to close…"
