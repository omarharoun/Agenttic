#!/usr/bin/env bash
# Restore an Agenttic backup produced by backup.sh. Stop the app first.
#
#   Postgres:  AGENTTIC_DB=... ./scripts/restore.sh agenttic-20260615-120000.dump
#   SQLite:    ./scripts/restore.sh agenttic-20260615-120000.db [/path/to/agenttic.db]
#
# On next start the Registry re-applies any pending migrations automatically.
set -euo pipefail

SRC="${1:?usage: restore.sh <backup-file> [target]}"

if [[ -n "${AGENTTIC_DB:-}" ]]; then
  PG_URL="${AGENTTIC_DB#*+psycopg://}"; PG_URL="postgresql://$PG_URL"
  echo "Restoring Postgres from $SRC (drops & recreates objects)"
  pg_restore --clean --if-exists --dbname="$PG_URL" "$SRC"
else
  # default matches the shipped registry_db (agenttic.db); pass an explicit
  # second argument to restore into a legacy ascore.db instead.
  TARGET="${2:-./agenttic.db}"
  echo "Restoring SQLite $SRC -> $TARGET"
  cp "$SRC" "$TARGET"
  rm -f "$TARGET-wal" "$TARGET-shm"   # discard stale WAL sidecars
fi
echo "Restore complete. Start the app to re-run migrations."
