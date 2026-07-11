#!/usr/bin/env bash
# Build every Agenttic distribution, validate it, and dry-run publish to
# TestPyPI (SPEC-8 Step 40, T40.3).
#
# Distributions built (SPEC-8 distribution model — see docs/SPEC2_DEVIATIONS.md):
#   * agenttic                 (repo root — public umbrella + internal ascore)
#   * agenttic-langgraph       (adapters/langgraph)
#   * agenttic-openai-agents   (adapters/openai_agents)
#
# What this does, always, with no credentials:
#   1. clean the dist/ dir
#   2. build sdist + wheel for each distribution
#   3. `twine check --strict` every artifact (metadata + README must render)
#   4. a DRY-RUN "publish": print the exact TestPyPI upload command and stop.
#
# The real upload is a deliberate HUMAN step (it needs credentials). It is NOT
# performed here and NOT in CI. To actually upload to TestPyPI, a maintainer runs
# this with `--publish` and a token in the environment:
#
#     TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-<testpypi-token> \
#         scripts/release/pypi.sh --publish
#
# and, only for the real index, `--publish --production` (guarded the same way).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST="$ROOT/dist"
PUBLISH=0
PRODUCTION=0
for arg in "$@"; do
  case "$arg" in
    --publish) PUBLISH=1 ;;
    --production) PRODUCTION=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# uv provides an isolated build backend; uvx runs twine without polluting envs.
# Fall back to `python -m build` / `python -m twine` if uv is unavailable.
if command -v uv >/dev/null 2>&1; then
  BUILD() { uv build --out-dir "$DIST" "$1" >&2; }
  TWINE() { uvx twine "$@"; }
else
  BUILD() { python -m build --outdir "$DIST" "$1" >&2; }
  TWINE() { python -m twine "$@"; }
fi

echo "==> Cleaning $DIST"
rm -rf "$DIST"
mkdir -p "$DIST"

echo "==> Building distributions"
BUILD "$ROOT"                          # agenttic (umbrella)
BUILD "$ROOT/adapters/langgraph"       # agenttic-langgraph
BUILD "$ROOT/adapters/openai_agents"   # agenttic-openai-agents

echo "==> Built artifacts:"
ls -1 "$DIST"

echo "==> twine check (strict — README/metadata must render on PyPI)"
TWINE check --strict "$DIST"/*

if [ "$PUBLISH" -ne 1 ]; then
  echo
  echo "==> DRY RUN complete — all distributions built and validated."
  echo "    No upload was performed (publishing is a credentialed human step)."
  echo "    To publish to TestPyPI:"
  echo "        TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-<token> \\"
  echo "            scripts/release/pypi.sh --publish"
  exit 0
fi

# --- Real upload (human step, requires credentials) ------------------------
if [ -z "${TWINE_PASSWORD:-}" ]; then
  echo "ERROR: --publish given but TWINE_PASSWORD (a token) is not set." >&2
  exit 3
fi
if [ "$PRODUCTION" -eq 1 ]; then
  echo "==> Uploading to PyPI (production)"
  TWINE upload "$DIST"/*
else
  echo "==> Uploading to TestPyPI"
  TWINE upload --repository testpypi "$DIST"/*
fi
