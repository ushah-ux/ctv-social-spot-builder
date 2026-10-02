#!/bin/bash
# One-command setup + launch for the CTV Social Spot Builder (macOS).
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1 || ! python3 -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>/dev/null; then
  echo "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/macos/ and run ./start.sh again."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting things up (about a minute)…"
  python3 -m venv .venv
fi
.venv/bin/pip install --quiet --disable-pip-version-check --upgrade -r requirements.txt

exec .venv/bin/python server.py
