#!/bin/bash
# One-time install: double-click this. Afterwards just open http://localhost:8765
# (the builder starts by itself whenever you log in).
set -uo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8765}"
APP="${SPOT_HOME:-$HOME/Library/Application Support/CTV Spot Builder}"
LABEL="com.inmarket.ctv-spot-builder"
[ "$PORT" != "8765" ] && LABEL="$LABEL.$PORT"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/ctv-spot-builder.log"
URL="http://localhost:$PORT/"

fail() { echo; echo "$1"; echo; [ -t 0 ] && read -r -p "Press Return to close this window."; exit 1; }

echo "Installing the CTV Social Spot Builder…"

# 1. Python 3.10+
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>/dev/null; then
  open "https://www.python.org/downloads/macos/"
  fail "Python 3.10 or newer is needed. The download page just opened: install it, then double-click this installer again."
fi

# 2. Copy the app somewhere permanent (so cleaning up Downloads doesn't break it)
mkdir -p "$APP" || fail "Couldn't create $APP"
if [ "$SRC" != "$APP" ]; then
  rsync -a --delete --exclude ".venv" --exclude "cache" --exclude ".git" "$SRC/" "$APP/" || fail "Couldn't copy the builder files."
fi

# 3. Private Python environment + dependencies
echo "Setting up (about a minute the first time)…"
[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv" || fail "Couldn't set up Python."
"$APP/.venv/bin/pip" install --quiet --disable-pip-version-check --upgrade -r "$APP/requirements.txt" \
  || fail "Couldn't install the video tools. Check your internet connection and try again."

# The app macOS asks about for Full Disk Access (needed only for posts that require your Safari login)
PYAPP="$(python3 -c 'import os,sys; print(os.path.join(os.path.realpath(sys.base_prefix), "Resources", "Python.app"))')"
[ -d "$PYAPP" ] || PYAPP="$(python3 -c 'import os,sys; print(os.path.realpath(sys.executable))')"

# 4. Start at login, keep running
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP/.venv/bin/python</string>
    <string>$APP/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PORT</key><string>$PORT</string>
    <key>NO_BROWSER</key><string>1</string>
    <key>SPOT_BUILDER_SERVICE</key><string>1</string>
    <key>SPOT_BUILDER_FDA_APP</key><string>$PYAPP</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" || fail "macOS wouldn't start the builder in the background. Try restarting your Mac and running this again."

# 5. Wait for it, then open it
for _ in $(seq 1 30); do
  curl -fs "${URL}api/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fs "${URL}api/health" >/dev/null 2>&1 || fail "The builder didn't start. Details are in $LOG"

[ "${NO_BROWSER:-}" = "1" ] || open "$URL"
cat <<EOF

Done! The builder is open at $URL
Bookmark it. From now on it's always there, even after restarting your Mac.

Instagram/Facebook posts that need a login use your Safari login. If the builder
says macOS is blocking that: System Settings › Privacy & Security › Full Disk Access,
click +, and add:
  $PYAPP

To update: download the newest version and double-click this installer again.
To remove: double-click "Uninstall Spot Builder.command".
EOF
[ -t 0 ] && read -r -p "Press Return to close this window."
exit 0
