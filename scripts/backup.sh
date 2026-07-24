#!/usr/bin/env bash
# Back up Agenttic state. Picks pg_dump for Postgres (AGENTTIC_DB set) or an
# online SQLite .backup of every tenant DB otherwise. Writes to $BACKUP_DIR
# (default ./backups). Safe to run while the app is live.
#
#   BACKUP_DIR=/backups ./scripts/backup.sh
#
# For continuous SQLite backup use Litestream instead (see docs/OPERATIONS.md).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

if [[ -n "${AGENTTIC_DB:-}" ]]; then
  echo "Postgres backup -> $BACKUP_DIR/ascore-$STAMP.dump"
  # AGENTTIC_DB looks like postgresql+psycopg://user:pass@host:5432/db
  PG_URL="${AGENTTIC_DB#*+psycopg://}"; PG_URL="postgresql://$PG_URL"
  pg_dump --format=custom --dbname="$PG_URL" \
    --file="$BACKUP_DIR/ascore-$STAMP.dump"
else
  DATA_DIR="${AGENTTIC_DATA_DIR:-.}"
  shopt -s nullglob
  for db in "$DATA_DIR"/ascore*.db; do
    name="$(basename "$db" .db)"
    echo "SQLite backup $db -> $BACKUP_DIR/$name-$STAMP.db"
    sqlite3 "$db" ".backup '$BACKUP_DIR/$name-$STAMP.db'"
  done
fi
echo "Backup complete: $BACKUP_DIR"
