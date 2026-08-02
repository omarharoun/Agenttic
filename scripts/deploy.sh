#!/usr/bin/env bash
# Deploy the full Agenttic stack (app + Postgres + Redis) to a remote host over
# SSH. Idempotent: re-running redeploys the current tree and preserves the
# generated admin token + any Anthropic key already on the host.
#
#   ./scripts/deploy.sh [ssh-host]          # default host: node1
#
# Safe by default: the app port binds to 127.0.0.1 on the remote host (NOT the
# public IP) — front it with TLS (Caddy/nginx/ingress) before exposing it.
# To bind publicly anyway (authenticated but plaintext — discouraged):
#   AGENTTIC_BIND=0.0.0.0 ./scripts/deploy.sh node1
set -euo pipefail

HOST="${1:-node1}"
REMOTE_DIR="${REMOTE_DIR:-/opt/agenttic}"
BIND="${AGENTTIC_BIND:-127.0.0.1}"
PORT="${AGENTTIC_PORT:-8700}"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "1/8  Check SSH to $HOST"
ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" 'echo ok' >/dev/null || {
  echo "ERROR: cannot ssh $HOST non-interactively. Authorize your key first."; exit 1; }

say "2/8  Pre-flight disk check on $HOST (warn at >${DISK_THRESHOLD:-85}%)"
# node1 has filled to 100% during deploys (image + build-cache buildup). Warn
# early; a heavy build on a near-full disk can fail half-way. Non-fatal here.
ssh "$HOST" "bash -se -- '${DISK_THRESHOLD:-85}'" <<'REMOTE' || true
set -euo pipefail
THRESHOLD="$1"
USED="$(df -P / | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
AVAIL="$(df -Ph / | awk 'NR==2 {print $4}')"
echo "Root fs: ${USED}% used, ${AVAIL} free."
if [ "${USED:-0}" -ge "$THRESHOLD" ]; then
  echo "WARNING: disk at ${USED}% (>= ${THRESHOLD}%). Pre-pruning dangling images + build cache..."
  docker image prune -f   2>/dev/null || sg docker -c 'docker image prune -f'   || true
  docker builder prune -f 2>/dev/null || sg docker -c 'docker builder prune -f' || true
  df -Ph /
fi
REMOTE

say "3/8  Install Docker Engine + compose plugin (detect distro)"
ssh "$HOST" 'bash -se' <<'REMOTE'
set -euo pipefail
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "Docker + compose already present: $(docker --version)"
else
  . /etc/os-release
  echo "Distro: $ID $VERSION_ID — installing Docker via get.docker.com"
  curl -fsSL https://get.docker.com | sudo sh
fi
# add the login user to the docker group (effective on next login/session)
sudo usermod -aG docker "$USER" || true
sudo systemctl enable --now docker || true
REMOTE

say "4/8  Sync repo -> $HOST:$REMOTE_DIR"
ssh "$HOST" "sudo mkdir -p $REMOTE_DIR && sudo chown \$USER $REMOTE_DIR"
rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude 'ui/dist' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
  --exclude 'backups' --exclude 'uploads' --exclude '.env' \
  -e ssh "$LOCAL_DIR/" "$HOST:$REMOTE_DIR/"

# ---------------------------------------------------------------------------
# REFUSE to clobber a host-managed override.
#
# On 2026-07-31 this script overwrote /opt/agenttic/docker-compose.override.yml
# on node1. That file is HOST-ONLY, is not in the repo, and cannot be restored
# from git. It pinned `image: agenttic:deployed`, `pull_policy: never`,
# `build: !reset null` and a REPLACING ports entry. Losing it meant compose fell
# back to building on a VM with too little RAM to build (458MB -> OOM), and the
# base file's 0.0.0.0:8700 merged with 127.0.0.1:8700 so the container collided
# with itself. Production was down for about an hour.
#
# A host whose override pins an image is deploying by SHIPPING an image
# (docker save | ssh docker load), not by building from source here. This script
# is the build-on-host flow and does not belong on such a host.
# ---------------------------------------------------------------------------
say "4b/8  Refuse to overwrite a host-managed compose override"
if ssh "$HOST" "grep -qE '^[[:space:]]*(image:|pull_policy:|build:[[:space:]]*!reset)' $REMOTE_DIR/docker-compose.override.yml 2>/dev/null"; then
  cat >&2 <<'MSG'
ERROR: the remote already has a HOST-MANAGED docker-compose.override.yml that
       pins an image (and is not in this repo). Overwriting it is what took
       production down on 2026-07-31.

       That host deploys by shipping a locally built image, not by building
       from source on the VM:

         docker build -t agenttic:deployed .
         docker save agenttic:deployed | gzip -1 | ssh <host> 'gunzip | docker load'
         ssh <host> 'cd /opt/agenttic && docker compose up -d --wait app'

       Re-run with ALLOW_OVERRIDE_CLOBBER=1 only if you are certain, and back
       the file up first.
MSG
  [ "${ALLOW_OVERRIDE_CLOBBER:-0}" = "1" ] || exit 1
  echo "ALLOW_OVERRIDE_CLOBBER=1 set — backing the file up and continuing."
  ssh "$HOST" "cp -n $REMOTE_DIR/docker-compose.override.yml $REMOTE_DIR/docker-compose.override.yml.bak-\$(date +%s) 2>/dev/null || true"
fi

say "5/8  Compose override (port bind + service ordering) + config"
ssh "$HOST" "bash -se -- '$REMOTE_DIR' '$BIND' '$PORT'" <<'REMOTE'
set -euo pipefail
DIR="$1"; BIND="$2"; PORT="$3"; cd "$DIR"

# --------------------------------------------------------------------------
# The mounted /app/config.yaml. This `cp -f` destroyed node1's HOST-APPENDED
# certification block on 2026-08-01, and nobody noticed for a day: the block
# carried the certificate signing key, so production silently fell back to the
# deterministic, publicly-known DEV key and published it as its issuer key —
# every certificate forgeable, and every certificate signed with the real key
# no longer verifiable. The signing key was not recoverable.
#
# It is the same failure as the compose-override clobber guarded above: this
# script writes files a host may legitimately own. So it now BACKS UP first
# (always, unconditionally — the override's .bak is the only reason that
# incident was recoverable) and REFUSES when the host's config differs from
# what we are about to write.
#
# Secrets belong in .env, which this script preserves, and NOT in config.yaml,
# which it overwrites. The keys were moved there on 2026-08-02.
# --------------------------------------------------------------------------
if [ -f config.yaml ]; then
  cp -a config.yaml "config.yaml.bak-$(date +%s)"
  # keep the 5 most recent backups; a host should not fill its disk with these
  ls -1t config.yaml.bak-* 2>/dev/null | tail -n +6 | xargs -r rm -f
fi
if [ -f config.yaml ] && ! cmp -s config.yaml config.prod.yaml; then
  if [ "${ALLOW_CONFIG_CLOBBER:-0}" != "1" ]; then
    cat >&2 <<'MSG'
ERROR: the host's config.yaml differs from config.prod.yaml. Something on this
       host has customised it — on node1 that was an appended `certification:`
       block holding the signing key, and overwriting it published a forgeable
       dev key as the issuer identity for a day.

       Diff them, fold anything host-specific into config.prod.yaml (or move
       secrets into .env, which this script preserves), then re-run.
       A timestamped backup was just written beside it.

       Re-run with ALLOW_CONFIG_CLOBBER=1 only if you are certain.
MSG
    exit 1
  fi
  echo "ALLOW_CONFIG_CLOBBER=1 set — overwriting a customised host config."
fi
cp -f config.prod.yaml config.yaml
cat > docker-compose.override.yml <<YAML
services:
  app:
    # !override REPLACES the base list. Without it compose MERGES \`ports\`, so the
    # app publishes BOTH 0.0.0.0:8700 (docker-compose.yml) and $BIND:$PORT — two
    # bindings for one port on one container. Two consequences, both real and both
    # observed on node1 on 2026-07-31:
    #   1. the container collides with itself and never starts
    #      ("failed to bind host port $BIND:$PORT: address already in use",
    #      while the kernel reports the port free and \`docker run -p\` succeeds);
    #   2. worse, the public 0.0.0.0 bind this file exists to PREVENT survives,
    #      so the safety promise in the header above was false whenever the
    #      container did start.
    ports: !override ["$BIND:$PORT:8700"]  # default 127.0.0.1 — front with TLS to expose
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
YAML
# Fail loudly if the merge still yields more than one published port.
PUBLISHED="$(docker compose --profile postgres --profile redis config 2>/dev/null | grep -c 'published:' || true)"
if [ "${PUBLISHED:-0}" -gt 1 ]; then
  echo "ERROR: $PUBLISHED published ports for one service — the override is not replacing the base list."
  echo "       (compose < 2.24 has no !override; upgrade it or remove ports: from docker-compose.yml)"
  exit 1
fi
echo "App will bind ${BIND}:${PORT} on the host."
REMOTE

say "6/8  Secrets -> $HOST:$REMOTE_DIR/.env  (never committed)"
ssh "$HOST" "bash -se -- '$REMOTE_DIR'" <<'REMOTE'
set -euo pipefail
DIR="$1"; cd "$DIR"; touch .env; chmod 600 .env
get() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }

# admin token: preserve across redeploys, else generate a strong one
TOKEN="$(get AGENTTIC_API_TOKEN)"; [ -n "$TOKEN" ] || TOKEN="$(openssl rand -hex 32)"

# Anthropic key: reuse .env, else the host env, else leave a clear placeholder
AKEY="$(get ANTHROPIC_API_KEY)"; [ -n "$AKEY" ] && [ "$AKEY" != "REPLACE_ME" ] || AKEY="${ANTHROPIC_API_KEY:-REPLACE_ME}"

cat > .env <<ENV
# generated by deploy.sh — DO NOT COMMIT
AGENTTIC_API_TOKEN=$TOKEN
ANTHROPIC_API_KEY=$AKEY
# NB: the Postgres role/database are literally named `ascore` — they were
# initialised under the old name and Postgres only applies POSTGRES_USER/DB on
# FIRST init, so they persist in the volume regardless of compose defaults.
# This is a DATA identifier, not naming: changing it here would point the app
# at a database that does not exist. Renaming it is a separate, deliberate
# migration (ALTER DATABASE / ALTER ROLE), not a find-replace.
AGENTTIC_DB=postgresql+psycopg://ascore:ascore@postgres:5432/ascore
AGENTTIC_REDIS_URL=redis://redis:6379/0
ENV
echo "Wrote .env (token preserved/generated; ANTHROPIC_API_KEY=$([ "$AKEY" = REPLACE_ME ] && echo MISSING || echo set))"
REMOTE

say "7/8  Bring up Postgres + Redis, run migrations, then the app"
ssh "$HOST" "bash -se -- '$REMOTE_DIR'" <<'REMOTE'
set -euo pipefail
DIR="$1"; cd "$DIR"
# ONE way to run compose, chosen once. This used to be
# `sg docker -c "..." || dc ...`, which is how a deploy came to report success
# while changing nothing: as root (already able to reach the socket) the `sg`
# wrapper can exit 0 without the inner command having taken effect, and `||`
# then never runs the fallback. A deploy that cannot tell you it did nothing is
# worse than one that fails.
if docker compose version >/dev/null 2>&1; then
  dc() { docker compose --profile postgres --profile redis "$@"; }
else
  dc() { sg docker -c "cd $DIR && docker compose --profile postgres --profile redis $*"; }
fi

dc up -d --wait postgres redis
# run migrations against Postgres as a one-off (app also self-migrates on boot).
#
# `-T` AND `</dev/null` ARE BOTH LOAD-BEARING (-T alone is NOT enough:
# it disables the pseudo-TTY, it does not detach stdin). This whole block is fed to `bash -s` over ssh STDIN, and
# `docker compose run` attaches stdin by default — so without -T the migrate
# container CONSUMES THE REST OF THIS SCRIPT. Every line below it is eaten, the
# block exits 0, and the deploy reports success having never recreated the app.
# That is exactly what happened on node1 on 2026-07-31 and 2026-08-01: the build
# succeeded, migrations ran, /health answered from the OLD container, and the new
# image was never started. A deploy that silently skips its own remaining steps
# is the same defect as one that lies about them.
dc run --rm -T app agenttic migrate --config /app/config.yaml </dev/null
dc up -d --wait app
dc ps

# POST-CONDITION: the running app must be the image we just built. Without this
# the previous failure mode is invisible — the old container keeps serving, its
# /health answers, and every check below passes against code that was never
# deployed.
# STALENESS: the image must be NEWER than the source just synced. `up -d` does
# NOT rebuild an existing image, so a run can sync new source, skip the build
# entirely, and leave running==built — both stale, and the identity check below
# passes because it only compares those two to each other. Observed on node1 on
# 2026-08-01: source dated 15:46 served by an image built the previous day.
NEWEST_SRC="$(find src ui/src config.yaml config.prod.yaml Dockerfile -type f -newer /proc/self 2>/dev/null | head -1)"
IMG_EPOCH="$(date -d "$(docker inspect --format '{{.Created}}' agenttic-app:latest 2>/dev/null)" +%s 2>/dev/null || echo 0)"
SRC_EPOCH="$(find src ui/src config.yaml Dockerfile -type f -printf '%T@\n' 2>/dev/null | sort -rn | head -1 | cut -d. -f1)"
if [ "${IMG_EPOCH:-0}" -gt 0 ] && [ "${SRC_EPOCH:-0}" -gt "${IMG_EPOCH:-0}" ]; then
  echo "ERROR: agenttic-app:latest was built BEFORE the newest source file here."
  echo "       image: $(date -d @"$IMG_EPOCH" 2>/dev/null)   source: $(date -d @"$SRC_EPOCH" 2>/dev/null)"
  echo "       \`up -d\` does not rebuild an existing image. Force it (\`up -d --build\`)"
  echo "       or, on a host that pins an image, ship one: docker save | ssh docker load."
  exit 1
fi
BUILT="$(docker images -q agenttic-app:latest 2>/dev/null | head -1)"
RUNNING="$(docker inspect --format '{{.Image}}' "$(dc ps -q app)" 2>/dev/null | sed 's/^sha256://' | cut -c1-12)"
BUILT_SHORT="$(printf '%s' "${BUILT:-}" | sed 's/^sha256://' | cut -c1-12)"
if [ -n "$BUILT_SHORT" ] && [ "$RUNNING" != "$BUILT_SHORT" ]; then
  echo "ERROR: the running app image ($RUNNING) is NOT the image just built ($BUILT_SHORT)."
  echo "       The container was not recreated. Refusing to report a successful deploy."
  exit 1
fi
echo "Running image matches the build: ${RUNNING:-unknown}"

# Post-ship prune: reclaim the space the just-finished build left behind.
# SAFE — removes only dangling (untagged) images + the builder cache. It does
# NOT touch the running app/postgres/redis images, and NEVER touches named
# volumes (pg-data / redis-data / ascore-data), so no data is at risk. We do
# NOT run `docker volume prune` or `image prune -a` for that reason.
echo "Pruning dangling images + build cache (data volumes untouched)..."
docker image prune -f   2>/dev/null || sg docker -c 'docker image prune -f'   || true
docker builder prune -f 2>/dev/null || sg docker -c 'docker builder prune -f' || true
df -Ph / | awk 'NR<=2'
REMOTE

say "8/8  Verify /health and /ready"
ssh "$HOST" "bash -se -- '$BIND' '$PORT'" <<'REMOTE'
set -euo pipefail
BIND="$1"; PORT="$2"; H="http://127.0.0.1:$PORT"
# NOTE: /health and /ready are GET-only (FastAPI @app.get; no auto HEAD route),
# so a HEAD probe returns 405. curl here uses GET — uptime monitors must too.
for ep in health ready; do
  printf '%s -> ' "$ep"; curl -fsS "$H/$ep" && echo || echo "FAILED"
done
echo "Listening:"; ss -ltnp 2>/dev/null | grep ":$PORT" || true
REMOTE

say "Done. Admin token is in $HOST:$REMOTE_DIR/.env (AGENTTIC_API_TOKEN)."
echo "Disk after deploy:"; ssh "$HOST" 'df -Ph / | awk "NR<=2"' || true
echo "Tip: enable scripts/disk-guard.sh on a timer — see docs/OPERATIONS.md (Disk space)."
echo "Access (from the host / behind your tunnel or TLS proxy): http://127.0.0.1:$PORT"
echo "If ANTHROPIC_API_KEY shows MISSING, set it in $REMOTE_DIR/.env and: docker compose up -d app"
