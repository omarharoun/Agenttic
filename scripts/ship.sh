#!/usr/bin/env bash
# Ship a locally-built image to the production droplet — the standard deploy.
#
#   podman build -t agenttic:deployed .   # build HERE, never on the droplet
#   ./scripts/ship.sh [user@host]         # default: root@64.23.179.172
#
# Why build-local: the droplet's 8.7G disk has hit 100% three times from
# on-box builds (see scripts/disk-guard.sh). This flow transfers the finished
# image (~150MB gz) instead, and the droplet only ever loads + runs it.
#
# Surgical by design — the droplet keeps its own state, this NEVER overwrites:
#   docker-compose.override.yml   (pins image: agenttic:deployed, port bind)
#   config.yaml (+ .bak*)         (hand-tuned prod config, superset of repo's)
#   .env                          (secrets: API token, Anthropic key, DB URL)
#   Caddyfile, data/, backups/, uploads/
set -euo pipefail

HOST="${1:-root@64.23.179.172}"
DIR="${REMOTE_DIR:-/opt/agenttic}"
IMAGE="${IMAGE:-agenttic:deployed}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -o BatchMode=yes)

say() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

say "0/6 preflight"
"${SSH[@]}" "$HOST" 'echo ok' >/dev/null || { echo "cannot ssh $HOST"; exit 1; }
podman image exists "$IMAGE" || {
  echo "no local image $IMAGE — build first: podman build -t $IMAGE ."; exit 1; }
"${SSH[@]}" "$HOST" "bash $DIR/scripts/disk-guard.sh" || true

say "1/6 ship image (podman save -> docker load)"
podman save --format docker-archive "$IMAGE" \
  | gzip \
  | "${SSH[@]}" "$HOST" \
      "gunzip | docker load && docker tag localhost/$IMAGE $IMAGE && docker rmi localhost/$IMAGE >/dev/null"

say "2/6 sync code (preserving droplet-local state)"
rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude 'ui/dist' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
  --exclude 'backups' --exclude 'uploads' --exclude '.env' \
  --exclude 'docker-compose.override.yml' --exclude 'config.yaml' \
  --exclude 'config.yaml.bak*' --exclude 'Caddyfile' --exclude 'data' \
  --exclude 'calibration' --exclude 'review' \
  "$REPO/" "$HOST:$DIR/"

say "3/6 migrate"
"${SSH[@]}" "$HOST" \
  "cd $DIR && docker compose --profile postgres --profile redis run --rm app ascore migrate --config /app/config.yaml"

say "4/6 swap the app container"
"${SSH[@]}" "$HOST" \
  "cd $DIR && docker compose --profile postgres --profile redis up -d --no-build --wait app && docker compose ps"

say "5/6 prune dangling layers + disk"
"${SSH[@]}" "$HOST" 'docker image prune -f | tail -1; df -h / | tail -1'

say "6/6 verify public surface"
for p in / /playground /api/public/demo-scan/preview; do
  printf '%-32s -> ' "$p"
  curl -s -o /dev/null -w '%{http_code}\n' --max-time 12 "https://agenttic.io$p"
done
