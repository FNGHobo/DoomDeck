#!/usr/bin/env bash
# run.sh - start DoomDeck.
#
# Uses the system PySide6 if there is one (on Arch: sudo pacman -S pyside6).
# Otherwise creates a private virtual environment in ./.venv next to this script
# and installs PySide6 into it, so nothing is installed system-wide and nothing is
# written to your home folder.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if python3 -c "import PySide6" 2>/dev/null; then
  exec python3 -m doomdeck "$@"
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "PySide6 not found - setting up ./.venv (one time, needs internet)..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet PySide6-Essentials
fi
exec .venv/bin/python -m doomdeck "$@"
