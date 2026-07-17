#!/usr/bin/env bash
# Launch the Vite dev server with Node on PATH (Node lives in ~/.local, not the
# system PATH the preview runner inherits). Used by .claude/launch.json →
# frontend-vite. Serves the hot-reload UI on :5173, proxying /api to :8700.
set -euo pipefail
export PATH="$HOME/.local/node-current/bin:$PATH"
cd "$(dirname "$0")/ui"
exec npm run dev
