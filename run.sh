#!/bin/bash
# Prosperity AI Trader — run wrapper
# Activates the virtual environment and runs main.py with auto-dispatch.
# Called by launchd every 15 minutes. Handles its own logging.

# Get the directory this script lives in (works even when called by launchd)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

# Log file with date rotation
LOG="$DIR/logs/trader_$(date +%Y-%m-%d).log"
mkdir -p "$DIR/logs"

# Activate virtual environment (supports common venv names)
for VENV in venv .venv env; do
    if [ -f "$DIR/$VENV/bin/activate" ]; then
        source "$DIR/$VENV/bin/activate"
        break
    fi
done

# Source .env so variables are available (launchd doesn't inherit shell env)
if [ -f "$DIR/.env" ]; then
    set -a
    source "$DIR/.env"
    set +a
fi

# Run with timestamp header in log
{
    echo ""
    echo "========================================"
    echo "  RUN: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "========================================"
    python "$DIR/main.py" "$@"
    echo "  EXIT CODE: $?"
} >> "$LOG" 2>&1
