# Operations: backups, restore, retention

State lives entirely in the database (SQLite files by default, or Postgres when
`ASCORE_DB` is set). Uploaded business docs live under `paths.uploads_dir`.

## Backups

**SQLite (default).** Each tenant is its own file: `ascore.db` (default tenant)
plus `ascore.<tenant>.db`, with `-wal`/`-shm` sidecars. Back up online:

```bash
BACKUP_DIR=/backups ./scripts/backup.sh        # sqlite3 .backup per DB (consistent under WAL)
```

For continuous, point-in-time backup, run **Litestream** against each DB file
(streams the WAL to S3/GCS):

```yaml
# litestream.yml
dbs:
  - path: /app/data/ascore.db
    replicas: [{ type: s3, bucket: my-bucket, path: ascore/ascore.db }]
```

**Postgres.** `backup.sh` runs `pg_dump --format=custom` when `ASCORE_DB` is set;
schedule it (cron / k8s CronJob) and/or use your managed-Postgres snapshots +
PITR.

## Restore drill (practice this before you need it)

1. Provision a scratch environment (empty volume / fresh Postgres DB).
2. `./scripts/restore.sh <backup-file> [target]` (stop the app first).
3. Start the app — `Registry.__init__` re-applies any pending migrations.
4. Verify: `GET /ready` → 200, `GET /api/agents` lists expected agents, open a
   recent scorecard. Record the wall-clock restore time as your RTO.

## Data retention & PII

Traces store agent inputs/outputs, which may contain client/PII data. Two
configurable controls (`retention` in config), applied by `ascore retention`:

```yaml
retention:
  trace_redact_days: 30   # strip inputs/outputs from traces older than 30d (keep timing/cost)
  trace_prune_days: 90    # delete traces older than 90d (scorecards keep their aggregates)
```

Run it on a schedule (the values are 0/off by default):

```bash
ascore retention --apply           # redact + prune per config
ascore retention                   # dry-run (prints what it would do)
```

Redaction is idempotent and keeps the trace row (structure, latency, cost) while
removing the sensitive payloads; pruning deletes whole trace rows. Scorecards
and per-criterion results are unaffected, so historical evaluation results
survive both. Schedule via cron or a Kubernetes CronJob invoking the CLI in the
app image.
