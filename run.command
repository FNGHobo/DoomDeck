#!/usr/bin/env bash
# run.command - start DoomDeck on macOS. Double-click it in Finder, or run it in Terminal.
#
# Uses a Python 3 that already has PySide6 if there is one. Otherwise it creates a private
# virtual environment in ./.venv next to this script and installs PySide6 into it, so
# nothing is installed system-wide and nothing is written outside this folder.
#
# If double-clicking says the file cannot be opened, make it executable once:
#   chmod +x run.command
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found. Install it with Homebrew (brew install python) or from"
  echo "https://www.python.org/downloads/ and run this again."
  exit 1
fi

if python3 -c "import PySide6" 2>/dev/null; then
  exec python3 -m doomdeck "$@"
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "PySide6 not found - setting up ./.venv (one time, needs internet)..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet PySide6-Essentials
fi
exec .venv/bin/python -m doomdeck "$@"
