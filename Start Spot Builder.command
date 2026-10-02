#!/bin/bash
# Double-click to start the CTV Social Spot Builder.
# Opens it in your browser; keep this window open while you work, close it to stop.
cd "$(dirname "$0")" || exit 1
URL="http://localhost:${PORT:-8765}/"

# Already running (e.g. from another window)? Just open it.
if curl -fs "${URL}api/health" >/dev/null 2>&1; then
  echo "The builder is already running. Opening $URL"
  open "$URL"
  sleep 2
  exit 0
fi

bash start.sh
status=$?
if [ $status -ne 0 ] && [ $status -ne 130 ]; then  # 130 = stopped with Ctrl+C
  echo
  echo "The builder stopped with an error (see above). Press Return to close this window."
  read -r
fi
