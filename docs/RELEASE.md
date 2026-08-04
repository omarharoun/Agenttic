# Releasing to node1

How a release actually reaches production. This existed nowhere in the repo
until 2026-08-04, which is why the same deploy incident happened three times:
the procedure lived in one person's notes, so every safeguard had to be
rediscovered by tripping over it.

`scripts/deploy.sh` is **not** this procedure. It provisions a *fresh* host —
installs Docker, syncs source, builds on the box. On node1 it now refuses at
step 3b, and that refusal is correct. Do not override it.

---

## The four rules

1. **Never build on node1.** The VM OOMs in the UI stage — `vite-react-ssg
   build` exhausts node's heap and dies with exit 134, after burning ~740 MB of
   build cache on a disk that runs at ~88%.
2. **Never run `scripts/deploy.sh` against node1.** Its step 4 is
   `rsync -az --delete` against `/opt/agenttic`.
3. **Never touch `/opt/agenttic/config.yaml` or `.env`.** `.env` holds the
   signing keys. Regenerating either key invalidates every certificate already
   issued, and the old cert key `ed25519:ca33a367…` was lost exactly this way.
4. **Never let `docker compose up` run without `--profile postgres --profile
   redis`.** `COMPOSE_PROFILES` is *not* in the host `.env` (verified
   2026-08-04), so omitting the flags starts the app with no database or Redis.

---

## Pre-flight

```bash
pytest -q                       # must be green; 0 failed
cd ui && npm run verify         # only if ui/ changed
```

Then check the host — both of these, every time:

```bash
ssh root@node1 'ls -la /opt/agenttic/docker-compose.override.yml; df -h / | tail -1'
```

* **The override must exist.** It is host-only, not in git, and pins
  `image: agenttic:deployed`, `build: !reset null`, `pull_policy: never`,
  `ports: !override ["127.0.0.1:8700:8700"]`, and the postgres/redis
  dependencies. Every one of those lines is load-bearing. If it is missing,
  **stop** — restore it (§Recovering the override) before going further.
* **Disk needs ≳1.5 GB free.** If not: `ssh root@node1 'docker builder prune -f'`.

A local `pytest` that fails with `sqlite3.OperationalError: disk I/O error` is
usually the tmpfs **quota**, not the disk — `df` will show free space while
`quota -s` shows the cap reached. Sweep with
`find /tmp -maxdepth 1 -type f -name 'tmp*.db' -mmin +60 -delete`.

---

## Deploy

```bash
# 1. build locally
docker build -t agenttic:deploy-candidate .

# 2. tag a rollback point BEFORE anything changes
ssh root@node1 'docker tag agenttic:deployed agenttic:rollback-pre-<name>'

# 3. ship the image (never the source)
docker save agenttic:deploy-candidate | ssh root@node1 'docker load'

# 4. promote it
ssh root@node1 'docker tag agenttic:deploy-candidate agenttic:deployed'

# 5. VALIDATE THE MERGE BEFORE STARTING ANYTHING — this is the step that
#    catches a missing override, and it starts nothing
ssh root@node1 'cd /opt/agenttic && docker compose --profile postgres --profile redis config' \
  | grep -E 'image:|published:|host_ip:|build:'

# 6. go
ssh root@node1 'cd /opt/agenttic && docker compose --profile postgres --profile redis up -d --wait app'
```

Step 5 must show `image: agenttic:deployed`, `host_ip: 127.0.0.1`, **exactly one**
`published:`, and **no** `build:`. Anything else means the override is wrong or
gone — stop.

---

## Verify

```bash
ssh root@node1 '
  docker inspect agenttic-app-1 --format "{{slice .Image 7 19}} {{json .HostConfig.PortBindings}}"
  for p in / /engine /app/scenarios /status /pricing; do
    printf "%-18s -> " "$p"; curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8700$p"; done
  curl -s -o /dev/null -w "api(expect 401) -> %{http_code}\n" http://127.0.0.1:8700/api/scenario-runs
  curl -s http://127.0.0.1:8700/.well-known/agenttic-jwks.json
  curl -s http://127.0.0.1:8700/.well-known/agenttic-cert-keys.json
  docker logs --since 5m agenttic-app-1 2>&1 | grep -icE "error|traceback"
  df -h / | tail -1'
```

Expected: the image sha you just shipped; `127.0.0.1` only; pages 200; API 401;
**0** errors; and the signing identity **unchanged** —

| key | kid |
|---|---|
| passport (JWKS) | `6ebed4d56b5924de` |
| certificate | `ed25519:82b4729e01b44a4a` |

A changed kid means keys were regenerated. That is an incident, not a warning.

---

## Rollback

```bash
ssh root@node1 'cd /opt/agenttic \
  && docker tag agenttic:rollback-pre-<name> agenttic:deployed \
  && docker compose --profile postgres --profile redis up -d --wait app'
```

---

## Failure signatures

| What you see | What it means |
|---|---|
| `Image agenttic-app Building` | **The override is gone.** Compose fell back to `build: .`. Stop and restore it. |
| `FATAL ERROR: … JavaScript heap out of memory`, exit 134 | You are building on the VM. Same cause as above. |
| `address already in use` while the kernel says the port is free | `ports:` merged instead of replacing — the override lost its `!override`. The public `0.0.0.0` bind is live too. |
| Deploy "succeeds" but behaviour is stale | `docker compose up -d` does not rebuild. Compare the image *creation time* against your source, not just that two hashes agree. |

**Production stays up through all of these** — a running container keeps its own
config — so the damage is invisible until the *next* `compose up`. That is why
the override is checked in pre-flight, not after.

---

## Recovering the override

It is not in git and cannot be restored from it. Rebuild it from the running
container's own truth:

```bash
ssh root@node1 'docker inspect agenttic-app-1 \
  --format "{{json .HostConfig.PortBindings}} {{.Config.Image}}"'
```

Then write `/opt/agenttic/docker-compose.override.yml`:

```yaml
services:
  app:
    image: agenttic:deployed
    build: !reset null
    pull_policy: never
    ports: !override ["127.0.0.1:8700:8700"]
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
```

---

## Known drift (unresolved)

`/opt/agenttic/config.yaml` is the repo's **dev** `config.yaml`, so in production
`budgets.max_run_cost_usd`, `max_daily_cost_usd`, `security.rate_limit_per_minute`
and every scan/certify abuse ceiling are **0**, with `security.required: false`.
Env vars do not override them.

`config.prod.yaml` is **not** the fix: it is stale and lacks `billing` (with real
Stripe price IDs), `enforcement`, `incidents`, `cards`, `release`, `canaries`,
`oversight`, `passport`, `feeds`, and the `agents:` block. Copying it over
production would delete billing. `deploy.sh` refuses on this diff and is right to.

The real fix is to make one file a superset of the other. Until then, deploys use
the flow above, which never touches config.
