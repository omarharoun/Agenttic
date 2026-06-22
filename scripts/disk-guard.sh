#!/usr/bin/env bash
# Lightweight disk-space guard for the node hosting Agenttic. Prints a warning
# (and exits non-zero) when the filesystem backing Docker crosses a threshold.
# node1 has hit 100% disk twice during deploys (Docker image + build-cache
# buildup), so run this on a timer and/or before every deploy.
#
#   ./scripts/disk-guard.sh                 # check / (root fs), warn at >85%
#   THRESHOLD=90 ./scripts/disk-guard.sh    # custom threshold (percent used)
#   CHECK_PATH=/var/lib/docker ./scripts/disk-guard.sh   # check Docker's fs
#
# Exit codes: 0 = below threshold, 1 = at/above threshold (warning emitted).
# This script ONLY reports — it never deletes anything. To reclaim space safely
# see `scripts/deploy.sh` (prune step) and docs/OPERATIONS.md ("Disk space").
set -euo pipefail

THRESHOLD="${THRESHOLD:-85}"
CHECK_PATH="${CHECK_PATH:-/}"

# Percent used (integer) for the filesystem that contains CHECK_PATH.
USED="$(df -P "$CHECK_PATH" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
AVAIL="$(df -Ph "$CHECK_PATH" | awk 'NR==2 {print $4}')"

if [[ -z "$USED" ]]; then
  echo "disk-guard: could not read disk usage for $CHECK_PATH" >&2
  exit 2
fi

if (( USED >= THRESHOLD )); then
  echo "WARNING: disk at ${USED}% on $(df -P "$CHECK_PATH" | awk 'NR==2 {print $6}') (${AVAIL} free) — threshold ${THRESHOLD}%." >&2
  echo "Reclaim safely (does NOT touch pg-data/redis-data/ascore-data volumes):" >&2
  echo "  docker image prune -f && docker builder prune -f" >&2
  # Show the biggest space consumers to speed up triage.
  if command -v docker >/dev/null 2>&1; then
    echo "Docker disk usage:" >&2
    docker system df >&2 || true
  fi
  exit 1
fi

echo "disk-guard: ${USED}% used on $CHECK_PATH (${AVAIL} free), below ${THRESHOLD}% threshold."
