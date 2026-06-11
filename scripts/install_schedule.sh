#!/bin/bash
# Installs Prosperity AI Trader as a macOS launchd service.
# Runs every 15 minutes — auto-dispatch handles what to do based on time.
#
# Usage:
#   bash scripts/install_schedule.sh          # install
#   bash scripts/install_schedule.sh stop     # stop
#   bash scripts/install_schedule.sh uninstall # remove completely

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_NAME="com.prosperity.trader"
PLIST_SRC="$DIR/launchd/$PLIST_NAME.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

ACTION="${1:-install}"

check_prerequisites() {
    echo ""
    echo "Checking prerequisites..."

    # Check venv
    VENV_FOUND=false
    for VENV in venv .venv env; do
        if [ -f "$DIR/$VENV/bin/activate" ]; then
            VENV_FOUND=true
            echo "  ✓ Virtual environment found: $VENV"
            break
        fi
    done
    if [ "$VENV_FOUND" = false ]; then
        echo "  ✗ No virtual environment found."
        echo "    Create one first:  python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
        exit 1
    fi

    # Check .env
    if [ -f "$DIR/.env" ]; then
        echo "  ✓ .env file found"
    else
        echo "  ✗ .env file missing. Run:  cp .env.example .env  and fill in your API keys."
        exit 1
    fi

    # Check run.sh is executable
    chmod +x "$DIR/run.sh"
    echo "  ✓ run.sh is executable"
}

do_install() {
    echo ""
    echo "========================================"
    echo "  PROSPERITY TRADER — INSTALL SCHEDULE"
    echo "========================================"

    check_prerequisites

    # Substitute actual path into plist
    mkdir -p "$HOME/Library/LaunchAgents"
    sed "s|PLACEHOLDER_DIR|$DIR|g" "$PLIST_SRC" > "$PLIST_DEST"
    echo ""
    echo "  Plist installed at: $PLIST_DEST"

    # Unload if already running
    launchctl unload "$PLIST_DEST" 2>/dev/null

    # Load it
    launchctl load "$PLIST_DEST"
    if [ $? -eq 0 ]; then
        echo "  ✓ Service loaded — runs every 15 minutes"
    else
        echo "  ✗ Failed to load service. Check: launchctl error output above."
        exit 1
    fi

    echo ""
    echo "========================================"
    echo "  SCHEDULE ACTIVE"
    echo "========================================"
    echo ""
    echo "  The trader now runs automatically every 15 minutes."
    echo "  It figures out what to do based on the time:"
    echo ""
    echo "    5:00 - 9:30 AM ET   → Pre-market research"
    echo "    9:35 AM - 3:45 PM ET → Active trading"
    echo "    4:00 PM ET           → End of day + RCA"
    echo "    10:00 PM - 5:00 AM ET → Overnight learning"
    echo "    All other times      → Crypto monitoring"
    echo ""
    echo "  Logs:    $DIR/logs/trader_YYYY-MM-DD.log"
    echo "  Journal: python scripts/review.py --today"
    echo "  Status:  python main.py --phase status"
    echo ""
    echo "  To stop:     bash scripts/install_schedule.sh stop"
    echo "  To uninstall: bash scripts/install_schedule.sh uninstall"
    echo ""
}

do_stop() {
    echo "Stopping Prosperity Trader..."
    launchctl unload "$PLIST_DEST" 2>/dev/null
    echo "  ✓ Service stopped (plist stays installed — run 'install' to restart)"
}

do_uninstall() {
    echo "Uninstalling Prosperity Trader schedule..."
    launchctl unload "$PLIST_DEST" 2>/dev/null
    rm -f "$PLIST_DEST"
    echo "  ✓ Service removed from LaunchAgents"
    echo "  Your data, logs, and journal are untouched in $DIR/data and $DIR/logs"
}

do_status() {
    echo "Checking service status..."
    if launchctl list | grep -q "$PLIST_NAME"; then
        echo "  ✓ Service is RUNNING"
        launchctl list | grep "$PLIST_NAME"
    else
        echo "  ✗ Service is NOT running"
    fi
}

case "$ACTION" in
    install)   do_install ;;
    stop)      do_stop ;;
    uninstall) do_uninstall ;;
    status)    do_status ;;
    *)
        echo "Usage: $0 [install|stop|uninstall|status]"
        exit 1
        ;;
esac
