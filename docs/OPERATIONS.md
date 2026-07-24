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

# Disk space & Docker hygiene

The live VM (`node1`) has filled to **100% disk twice during deploys** — Docker
accumulates dangling images and (mostly) **build cache** every time the app
image is rebuilt. A full disk fails the next build and can wedge Postgres. Two
defences: prune as a standard deploy step, and a disk guard on a timer.

## Safe prune (what's safe vs. what is NOT)

```bash
docker image prune -f       # remove ONLY dangling (untagged) images
docker builder prune -f     # remove the build cache (the big one — tens of GB)
docker system df            # see where space went (Images / Build Cache / Volumes)
```

`scripts/deploy.sh` runs both of these automatically — once **pre-flight** if the
disk is already over threshold, and once **post-ship** after the build. They are
safe because:

- They remove only **dangling images** and **build cache** — never the running
  app / `postgres:16-alpine` / `redis:7-alpine` images, and never anything an
  active container references.
- They **never touch named volumes**, so the data volumes `pg-data`,
  `redis-data`, and `ascore-data` (your Postgres data, Redis data, and SQLite
  DBs) are untouched.

**Do NOT run these on `node1`** unless you mean to:

```bash
docker volume prune         # DELETES unused volumes — can wipe pg-data/etc.
docker image prune -a       # removes ALL unused images (re-pull/rebuild needed)
docker system prune -a --volumes   # the nuclear option — destroys data volumes
```

If you must reclaim more, scope by age instead, e.g.
`docker image prune -af --filter "until=168h"` (images unused for 7d) — still
volume-safe, but it will force a re-pull/rebuild of anything older.

## Disk guard (warn before it fills)

`scripts/disk-guard.sh` warns (exit 1) when the root filesystem crosses a
threshold (default 85%) and prints `docker system df` for triage. It **only
reports — it never deletes**.

```bash
./scripts/disk-guard.sh                 # check /, warn at >85%
THRESHOLD=90 ./scripts/disk-guard.sh    # custom threshold
CHECK_PATH=/var/lib/docker ./scripts/disk-guard.sh   # if Docker is on its own fs
```

Enable it on `node1` with a **cron** entry (every 15 min; mail/log on warning):

```cron
*/15 * * * * /opt/agenttic/scripts/disk-guard.sh >> /var/log/agenttic-disk.log 2>&1
```

…or a **systemd timer** (no extra packages; the operator installs these — the
deploy does NOT auto-install them):

```ini
# /etc/systemd/system/agenttic-disk-guard.service
[Unit]
Description=Agenttic disk-space guard
[Service]
Type=oneshot
Environment=THRESHOLD=85
ExecStart=/opt/agenttic/scripts/disk-guard.sh
```
```ini
# /etc/systemd/system/agenttic-disk-guard.timer
[Unit]
Description=Run Agenttic disk-space guard every 15 min
[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
[Install]
WantedBy=timers.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agenttic-disk-guard.timer
systemctl list-timers agenttic-disk-guard.timer    # confirm it's scheduled
journalctl -u agenttic-disk-guard.service          # see warnings
```

# Uptime monitoring

Point your external uptime monitor (UptimeRobot, Cloudflare Health Checks,
BetterStack, …) at **`GET https://agenttic.io/health`** — liveness, returns
`{"status":"ok"}` with **200**, no auth required. Use **`/ready`** (also GET) for
readiness: it returns **200** when the default DB is reachable and **503**
otherwise — good for "is it actually serving" checks.

> ⚠️ **Use GET, not HEAD.** Both endpoints are registered as GET-only
> (`@app.get`), and FastAPI/Starlette does not auto-add a HEAD route, so an
> uptime check configured for **HEAD returns 405** and will read as DOWN.
> Configure the monitor's method as GET and expect HTTP 200.

# Backups, restore, retention

State lives entirely in the database (SQLite files by default, or Postgres when
`AGENTTIC_DB` is set). Uploaded business docs live under `paths.uploads_dir`.

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

**Postgres (this is what `node1` runs).** `deploy.sh` sets `AGENTTIC_DB`, so the
live store is the dockerized `postgres:16-alpine` on the `pg-data` volume.
`backup.sh` runs `pg_dump --format=custom` when `AGENTTIC_DB` is set. On `node1`
the simplest consistent dump runs `pg_dump` **inside the postgres container** (no
client install needed) and is safe while the app is live:

```bash
cd /opt/agenttic
# logical dump (custom format) straight from the running container
docker compose exec -T postgres \
  pg_dump -U ascore -Fc ascore > backups/ascore-$(date +%Y%m%d-%H%M%S).dump
# keep the last 14 dumps, drop older ones
ls -1t backups/ascore-*.dump | tail -n +15 | xargs -r rm --
```

Schedule it (cron / k8s CronJob). For a managed Postgres, prefer the provider's
snapshots + PITR instead.

**Volume snapshots (Postgres + Redis + SQLite data).** To capture the raw
volumes (`pg-data`, `redis-data`, `ascore-data`) — e.g. for a full-VM restore —
tar each named volume from a throwaway container. For a *consistent* Postgres
volume snapshot, **stop the app and Postgres first** (a live filesystem copy of
PG data can be torn — the `pg_dump` above is preferred for hot backups):

```bash
cd /opt/agenttic
docker compose stop app postgres          # for a consistent pg-data snapshot
for v in pg-data redis-data ascore-data; do
  docker run --rm -v agenttic_${v}:/data -v "$PWD/backups":/out alpine \
    tar czf /out/${v}-$(date +%Y%m%d-%H%M%S).tgz -C /data .
done
docker compose start postgres app
```

> The volume names are compose-prefixed (`agenttic_pg-data`, …). Confirm with
> `docker volume ls`. Store `backups/` off-box (rsync/S3) — a snapshot on the
> same full disk helps no one.

## Restore drill (practice this before you need it)

**Postgres logical dump (the usual case on `node1`):**

```bash
cd /opt/agenttic
docker compose stop app                                   # quiesce writers
# DROPs & recreates objects, then reloads (run from inside the container):
docker compose exec -T postgres \
  pg_restore -U ascore --clean --if-exists -d ascore < backups/ascore-XXXX.dump
docker compose start app                                  # re-applies migrations
```

(Off-host, with a Postgres client on PATH and `AGENTTIC_DB` exported, the same
dump restores via `./scripts/restore.sh ascore-XXXX.dump`.)

**Volume tarball restore (full data-volume rollback):**

```bash
cd /opt/agenttic
docker compose down                                       # stop everything
docker run --rm -v agenttic_pg-data:/data -v "$PWD/backups":/in alpine \
  sh -c 'rm -rf /data/* && tar xzf /in/pg-data-XXXX.tgz -C /data'
docker compose up -d                                      # back online
```

**Verify after any restore:** `GET /ready` → 200, `GET /api/agents` lists the
expected agents, open a recent scorecard. Record the wall-clock restore time as
your RTO.

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
