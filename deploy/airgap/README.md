# Air-gapped build & run

Agenttic runs its scanner, certification engine, and OTel ingest with **zero
outbound network**. This directory hardens the self-host stack for a
disconnected host and turns on the startup egress self-check.

## Build on a connected host, ship the image

Air-gapped hosts can't pull from a registry, so build once where you have
internet and transfer the image:

```bash
# on a connected build host
docker build -f Dockerfile -t agenttic:0.7.0 .
docker save agenttic:0.7.0 postgres:16-alpine redis:7-alpine -o agenttic-airgap.tar

# copy agenttic-airgap.tar to the air-gapped host, then:
docker load -i agenttic-airgap.tar
```

Set `AGENTTIC_IMAGE=agenttic:0.7.0` in `deploy/.env` so compose uses the loaded
image instead of building.

## Run with egress physically blocked

```bash
docker compose -f deploy/docker-compose.yaml \
               -f deploy/airgap/docker-compose.airgap.yaml up
```

The `agenttic-airgap` network is `internal: true` (no gateway → no route off the
host). `ASCORE_AIRGAP=true` makes the app run `assert_airgap_safe` at boot; if
any enabled capability would require egress it **refuses to start and names the
offender**.

## Pre-flight the config

Before deploying, audit the config from the CLI (same gate the server runs):

```bash
ascore airgap check              # exits non-zero if air-gap mode has offenders
```

## Offline LLM

Certification needs a model. Offline you have two options, set in `config.yaml`:

```yaml
airgap:
  enabled: true
  local_llm_base_url: "http://vllm.internal:8000"   # a private inference server
  # or, for CI/self-tests with no model at all:
  mock_llm: true
```

Without one of these the self-check reports the `remote_llm` offender and the
server won't boot — by design.

## What's unavailable offline

Egress-only features are flagged **unavailable**, never silently degraded:

- Hosted public verify pages — verify locally via the CLI/SDK + JWKS instead.
- Upstream Index browse/import — the local registry is authoritative.

See [`docs/AIRGAP.md`](../../docs/AIRGAP.md) for the full data-residency
statement.
