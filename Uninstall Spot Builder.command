#!/bin/bash
# Removes the background builder installed by "Install Spot Builder.command".
PORT="${PORT:-8765}"
APP="${SPOT_HOME:-$HOME/Library/Application Support/CTV Spot Builder}"
LABEL="com.inmarket.ctv-spot-builder"
[ "$PORT" != "8765" ] && LABEL="$LABEL.$PORT"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
case "$APP" in
  */"CTV Spot Builder") rm -rf "$APP" ;;  # only ever delete our own install folder
esac
rm -f "$HOME/Library/Logs/ctv-spot-builder.log"

echo "The CTV Social Spot Builder has been removed from this Mac."
[ -t 0 ] && read -r -p "Press Return to close this window."
exit 0
