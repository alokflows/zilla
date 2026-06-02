#!/bin/bash
# Stop Zilla (macOS / Linux).
cd "$(dirname "$0")" || exit 1
echo stop > zilla.stop
pkill -f run_background.py 2>/dev/null
pkill -f "$(pwd)/bot.py" 2>/dev/null
# If installed as a service, stop that too (ignore errors).
systemctl --user stop zilla.service 2>/dev/null
echo "Zilla stopped."
