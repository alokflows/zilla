#!/bin/bash
# Zilla installer (Linux). Run:  ./install.sh   (or: bash install.sh)
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 install.py
else
  echo "Python 3 is not installed. Install it (e.g. sudo apt install python3 python3-pip) then try again."
fi
