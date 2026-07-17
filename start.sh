#!/usr/bin/env bash
# One-command local start for Agenttic (UI pre-built — no Node needed).
# Usage:  ./start.sh          → http://localhost:8000 (or the port it prints)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "· Creating virtualenv and installing (first run only — a few minutes)…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -e .
fi

echo "· Starting Agenttic (serves the pre-built UI from ui/dist)…"
exec ./.venv/bin/agenttic ui
