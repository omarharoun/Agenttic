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
./scripts/check-host-override.sh root@node1        # 0 ok · 1 drift · 2 missing
ssh root@node1 'df -h / | tail -1'
```

* **The override must exist and match the repo.** It pins
  `image: agenttic:deployed`, `build: !reset null`, `pull_policy: never`,
  `ports: !override ["127.0.0.1:8700:8700"]`, and the postgres/redis
  dependencies — every line load-bearing. The canonical copy is
  `deploy/hosts/node1/docker-compose.override.yml`; the installed copy is
  `/opt/agenttic/docker-compose.override.yml`. On **missing** or **drift**,
  stop and resolve it (§Recovering the override) before going further.
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

It **is** in git now — `deploy/hosts/node1/docker-compose.override.yml`. That was
the fix for having lost it three times; before 2026-08-04 it existed only on the
host and had to be reconstructed from a running container each time.

```bash
scp deploy/hosts/node1/docker-compose.override.yml \
    root@node1:/opt/agenttic/docker-compose.override.yml
./scripts/check-host-override.sh root@node1
```

`deploy.sh` also rsyncs the canonical copy onto the host, so a pristine one sits
beside the installed one and recovery works without a working laptop checkout:

```bash
ssh root@node1 'cd /opt/agenttic \
  && cp deploy/hosts/node1/docker-compose.override.yml docker-compose.override.yml'
```

If the check reports **drift**, do not guess which side is right — read the diff.
If the host is right, copy it back into the repo and commit; if the repo is
right, push it up.

---

## Config: resolved 2026-08-04

For months `/opt/agenttic/config.yaml` was the repo's **dev** config, so every
ceiling in production was `0` — meaning off, not low: no cap on a single run, no
daily cap, no per-tenant quota, no request rate limiting, and no ceiling on the
cost-bearing `/api/scan` and `/api/certify` endpoints. `0` is falsy at every call
site, which is exactly why it stayed invisible: it reads as a configured value.

`config.prod.yaml` could not be the fix — it had drifted so far it was missing
`billing` (with live Stripe price IDs), `enforcement`, `incidents`, `cards`,
`release`, `canaries`, `oversight`, `passport`, `feeds` and `agents:`. The
standard advice, "refresh the host from config.prod.yaml", would have deleted
billing from production. `deploy.sh` refusing on that diff was the only thing
preventing it.

It is now **generated from `config.yaml` plus the ceilings** and verified a
superset: no section missing, exactly 18 keys differ, all of them limits.

**Changing it needs a RESTART.** `docker compose up -d` will report `Running` and
do nothing — a bind-mounted file changing on disk gives compose no reason to
recreate the container, so the process keeps the config it started with:

```bash
ssh root@node1 'cd /opt/agenttic && cp -a config.yaml config.yaml.bak-<name>'
scp config.prod.yaml root@node1:/opt/agenttic/config.yaml
ssh root@node1 'docker restart agenttic-app-1'
```

Verify it took effect by reading what the RUNNING process loaded, not the file:

```bash
ssh root@node1 'docker exec agenttic-app-1 python -c "
import yaml; c=yaml.safe_load(open(\"/app/config.yaml\"))
print(c[\"security\"][\"rate_limit_per_minute\"], c[\"billing\"][\"enabled\"])"'
```

Rate limiting applies to **`/api` paths only** (`ratelimit.py` returns early for
anything else), so a burst against `/` or `/status` proves nothing. 135 requests
to `/api/scenario-runs` against the 120/min cap yields exactly 120 through and
15 × 429.

Roll back by copying the `.bak-` file over `config.yaml` and restarting again.
