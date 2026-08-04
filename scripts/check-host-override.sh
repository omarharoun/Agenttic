#!/usr/bin/env bash
# Does the host's compose override still match the copy in this repo?
#
#   ./scripts/check-host-override.sh [host]        # default: root@node1
#
# The override is what pins the shipped image, refuses to build on the VM, and
# keeps the app on loopback. It is not generated — it is a file that has been
# destroyed three times by tooling that syncs the repo over /opt/agenttic. Run
# this BEFORE a deploy: production stays up through every one of those failures
# (a running container keeps its own config), so the damage is invisible until
# the next `docker compose up`, which is far too late to discover it.
#
# Exit 0 match · 1 drift · 2 missing on the host · 3 no canonical copy here.
set -euo pipefail

HOST="${1:-root@node1}"
NAME="${HOST##*@}"
REMOTE_DIR="${REMOTE_DIR:-/opt/agenttic}"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CANON="$LOCAL_DIR/deploy/hosts/$NAME/docker-compose.override.yml"

if [ ! -f "$CANON" ]; then
  echo "FAIL: no canonical override tracked for '$NAME' (looked in $CANON)"
  echo "      If this host has one, copy it here and commit it — a file that"
  echo "      exists only on a server is one rsync away from gone."
  exit 3
fi

REMOTE="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" \
  "cat $REMOTE_DIR/docker-compose.override.yml 2>/dev/null" || true)"

if [ -z "$REMOTE" ]; then
  cat >&2 <<MSG
FAIL: $HOST has NO $REMOTE_DIR/docker-compose.override.yml

      Compose will fall back to \`build: .\` and try to build on the VM, publish
      0.0.0.0:8700 instead of loopback, and lose the postgres/redis ordering.
      DO NOT run \`docker compose up\` until it is restored:

        scp $CANON $HOST:$REMOTE_DIR/docker-compose.override.yml
MSG
  exit 2
fi

if ! diff -q <(printf '%s\n' "$REMOTE") "$CANON" >/dev/null 2>&1; then
  echo "DRIFT: $HOST's override differs from $CANON" >&2
  diff <(printf '%s\n' "$REMOTE") "$CANON" >&2 || true
  echo >&2
  echo "      Decide which is right. If the HOST is right, copy it back into the" >&2
  echo "      repo and commit; if the REPO is right, scp it up. Do not guess." >&2
  exit 1
fi

echo "OK: $HOST's compose override matches deploy/hosts/$NAME/ (byte for byte)"
