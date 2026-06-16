# Operations: public access, backups, restore, retention

## Public access via Cloudflare Tunnel (cloudflared)

The app binds to `127.0.0.1:8700` only — never a public port. Public access is
via a **Cloudflare Tunnel**: `cloudflared` runs on the host as a systemd
service, makes an **outbound-only** connection to Cloudflare, and routes a
hostname → `http://localhost:8700`. TLS is terminated at Cloudflare's edge; no
inbound ports are opened on the VM. (This replaces a Caddy/nginx reverse proxy.)

**Remote-managed (dashboard) tunnel — recommended, headless:**
1. Cloudflare **Zero Trust** → **Networks → Tunnels → Create a tunnel** →
   connector **Cloudflared** → name it (e.g. `agenttic-node1`).
2. Copy the **connector token** from the install command Cloudflare shows
   (the long string after `--token`).
3. On the host: `cloudflared service install <TOKEN>` (installs + starts the
   systemd service). Verify: `systemctl status cloudflared`.
4. In the tunnel's **Public Hostname** tab: add `agenttic.io` (and/or
   `www.agenttic.io`) → **Service: HTTP → `localhost:8700`**. Cloudflare creates
   the proxied DNS record and issues the cert automatically.

The app keeps its own bearer-token auth (`auth.required: true`) behind the
tunnel; optionally layer Cloudflare Access for SSO/identity.

**Locally-managed alternative** (needs a Cloudflare **API token** scoped
*Account: Cloudflare Tunnel: Edit* + *Zone: DNS: Edit* + *Zone: Zone: Read* on
the domain): `cloudflared tunnel create agenttic`, add ingress
`agenttic.io → http://localhost:8700` to the config, `cloudflared tunnel route
dns agenttic agenttic.io`, then install the service.

# Backups, restore, retention

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
