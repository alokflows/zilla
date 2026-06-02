#!/bin/bash
# Start Zilla in the background (macOS / Linux).
cd "$(dirname "$0")" || exit 1
rm -f zilla.stop
nohup python3 run_background.py >/dev/null 2>&1 &
echo "Zilla started in the background. Stop it with ./stop.sh"
