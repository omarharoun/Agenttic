# Self-hosting Agenttic

Agenttic runs entirely in your environment — your VPC, your Kubernetes cluster,
or a single VM. No data leaves your network unless you explicitly wire an egress
feature. This guide covers the two supported deployments (Docker Compose and
Helm) and the resources they need. For zero-egress installs see
[`AIRGAP.md`](AIRGAP.md).

## Option A — Docker Compose (single host)

```bash
cp deploy/.env.example deploy/.env      # set ASCORE_API_TOKEN (+ ASCORE_DB for BYO-Postgres)
docker compose -f deploy/docker-compose.yaml up
```

That stands up the server (scanner + certify + verify) on `:8700` against SQLite
in a named volume — no code edits. Bring your own Postgres by setting `ASCORE_DB`
in `deploy/.env`; run the bundled Postgres for evaluation with
`--profile bundled-db`. Add `--profile redis --profile worker` to scale out.

Verify it's live:

```bash
curl -sf http://localhost:8700/health && echo OK
```

## Option B — Helm (Kubernetes / VPC)

```bash
helm install agenttic deploy/helm/agenttic \
  --set-string secrets.apiToken=$(openssl rand -hex 24) \
  --set-string database.url='postgresql+psycopg://user:pass@pg.internal:5432/agenttic' \
  --set ingress.enabled=true --set ingress.host=agenttic.your-vpc.internal
```

The chart provisions a non-root Deployment (liveness/readiness probes), a PVC for
local state, a Service, an optional TLS Ingress, and a Secret holding the API
token, session secret, and database URL (never a ConfigMap). Supply your own
Secret with `--set secrets.existingSecret=<name>`.

### JWKS & verification

The passport signing JWKS is served by the app at
`/.well-known/agenttic-jwks.json` (configurable via `passport.jwksPath`). Point
verifiers at that URL; offline, the CLI/SDK verify against a JWKS file directly.

## Database

| Backend | When | How |
|---|---|---|
| SQLite (default) | single host, evaluation | data volume / PVC; zero config |
| Postgres (BYO) | production, HA | `ASCORE_DB=postgresql+psycopg://…` |

Migrations run automatically on boot. Back up the Postgres database (or the
SQLite volume) on your normal schedule; that volume is the entire state.

## Resource requirements

| Component | Requests | Limits | Notes |
|---|---|---|---|
| server | 250m CPU / 512Mi | 1 CPU / 1Gi | memory-bound during batch certification |
| Postgres (BYO) | per your standard | — | small; certification evidence is compact |
| Redis (optional) | 100m / 128Mi | — | only for multi-worker events + rate limiting |

Scale certification throughput horizontally with the `worker` replica (requires
Redis for the shared event bus). A single server handles interactive scanning
and CI-gate loads comfortably.

## Secrets checklist

- `ASCORE_API_TOKEN` — admin bootstrap; set `auth.required: true` in prod.
- `ASCORE_SESSION_SECRET` — signs session cookies.
- `ASCORE_DB` — Postgres URL (via Secret in Helm).
- Passport signing keys — generated into the data volume on first boot, or
  supplied out-of-band; they never appear in logs, events, or exports.

## Data residency

See [`AIRGAP.md`](AIRGAP.md#data-residency-statement) for the full statement a
security reviewer can hand to their team. In short: **all certification evidence,
traces, policies, and passports stay in your database and volumes.** The only
outbound calls are the ones you opt into (a remote LLM provider, external OTel
export, webhooks, SMTP) — each independently disableable, and all blocked at once
by air-gap mode.
