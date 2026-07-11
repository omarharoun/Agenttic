#!/usr/bin/env bash
# SPEC-8 T43.2 — the finish-line promise, proven unattended.
#
# A stranger with a fresh machine can:  pip install → one command → a signed
# safety grade in under a minute, no API key.  This script does exactly that in
# a throwaway venv and times the user-facing path (init → certify → verify):
#
#   1. fresh venv
#   2. install `agenttic` (base, NO extras — proves a framework-free install)
#   3. agenttic init            (scaffold a runnable quickstart)
#   4. agenttic certify --mock  (offline; no API key)  → dossier.json
#   5. agenttic dossier verify  (recompute hashes offline)
#
# The init→certify→verify segment must finish within QUICKSTART_BUDGET_SECONDS
# (default 60 — the "under a minute" promise); the script fails if it doesn't.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUDGET="${QUICKSTART_BUDGET_SECONDS:-60}"
WORK="$(mktemp -d)"
VENV="$WORK/venv"
PROJ="$WORK/proj"
mkdir -p "$PROJ"
trap 'rm -rf "$WORK"' EXIT

echo "==> [1/5] fresh venv: $VENV"
if command -v uv >/dev/null 2>&1; then
  uv venv "$VENV" >/dev/null
  PY="$VENV/bin/python"
  PIP() { uv pip install --python "$PY" "$@"; }
else
  python3 -m venv "$VENV"
  PY="$VENV/bin/python"
  "$PY" -m pip install -q --upgrade pip
  PIP() { "$PY" -m pip install -q "$@"; }
fi

echo "==> [2/5] install agenttic (base, no extras)"
INSTALL_START="$(date +%s)"
PIP "$ROOT"
INSTALL_ELAPSED=$(( $(date +%s) - INSTALL_START ))
echo "    installed in ${INSTALL_ELAPSED}s"

# Prove the base install imported no framework SDK.
"$PY" -c "import sys, agenttic; \
  bad=[m for m in ('langgraph','langchain','langchain_core','agents') if m in sys.modules]; \
  assert not bad, bad; print('    import agenttic clean, version', agenttic.__version__)"

echo "==> [3-5/5] init → certify --mock → verify (budget ${BUDGET}s)"
cd "$PROJ"
START="$(date +%s)"
"$PY" -m ascore init
"$PY" -m ascore certify --mock --out dossier.json
"$PY" -m ascore dossier verify dossier.json
ELAPSED=$(( $(date +%s) - START ))

echo "==> init→certify→verify completed in ${ELAPSED}s (budget ${BUDGET}s)"
if [ "$ELAPSED" -gt "$BUDGET" ]; then
  echo "FAIL: over the under-a-minute budget" >&2
  exit 1
fi
echo "QUICKSTART OK — a signed grade in ${ELAPSED}s, no API key, no manual steps."
