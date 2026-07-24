# Air-gapped operation

Agenttic supports a **hard no-egress** mode: the scanner, certification engine,
and OTel ingest run with zero outbound network. This is the enterprise gate for
regulated data that cannot leave the VPC. The promise is enforced in code, not
just documented (SPEC-7 Hard Rule 34).

## Turning it on

Set air-gap mode (either works):

```yaml
# config.yaml
airgap:
  enabled: true
  mock_llm: true                       # or: local_llm_base_url: http://vllm.internal:8000
  # allow: [otel_remote_export]        # explicit, logged escape hatch (discouraged)
```

or `AGENTTIC_AIRGAP=true` in the environment (the Helm chart sets this when
`airgap.enabled=true`).

## The startup self-check

On boot the app runs `assert_airgap_safe`. It audits every capability whose code
path could egress and, if air-gap mode is on and any are enabled, **refuses to
start and names the offender**:

```
air-gap mode is ON but 1 capability(ies) would require outbound network: remote_llm
  - remote_llm: LLM calls default to the Anthropic API (public). Set
    airgap.local_llm_base_url to a private inference endpoint, or airgap.mock_llm: true.
```

Run the same audit ahead of time from the CLI:

```bash
ascore airgap check     # exits non-zero if air-gap mode has offenders
```

### Capabilities audited

| Capability | Egress path | How to clear it |
|---|---|---|
| `remote_llm` | Anthropic API | `airgap.local_llm_base_url` or `airgap.mock_llm` |
| `otel_remote_export` | external OTLP collector | point at an in-cluster collector, or disable |
| `risk_webhooks` | public webhook URLs | intra-VPC targets only |
| `smtp_email` | external SMTP relay | in-cluster relay, or disable email |
| `upstream_index_import` | public Index | disable `interop.index_url` |

In-cluster/private hosts (loopback, RFC1918, `*.internal`, `*.svc`) are **not**
egress and are allowed.

## Network-layer enforcement

The self-check audits configuration; the compose overlay
(`deploy/airgap/docker-compose.airgap.yaml`) removes the escape route physically
by putting the stack on an `internal: true` Docker network (no gateway). Belt and
braces: even a bug that tried to egress has nowhere to go.

## What's unavailable offline (never silently degraded)

- **Hosted public verify pages** — the internet-facing verify UI. Offline,
  verification runs locally via the CLI/SDK against the JWKS; the cryptographic
  guarantee is identical.
- **Upstream Index browse/import** — the public discovery Index. Offline, your
  local registry is authoritative.

These are reported by `ascore airgap check` under "unavailable", so there is no
ambiguity about what you give up.

## Certification stays honest offline

Air-gap mode changes transport, not standards. NOT ASSESSED / `none_found`
semantics are unchanged; self-hosting does not lower the evidence bar. A grade
earned offline attests to exactly what was tested, same as online.

## Data-residency statement

> **What stays where.** Every artifact Agenttic produces or consumes —
> agent traces, scorecards, certification dossiers, enforcement policies and
> decisions, canaries, passports, and signing keys — is written only to the
> database and volumes you provision inside your network. Nothing is sent to
> Agenttic or any third party.
>
> **Outbound calls are opt-in and enumerable.** The only paths that can leave
> your network are: a remote LLM provider (if you configure one instead of a
> local/mock model), external OTel export (if you enable it to an off-cluster
> collector), risk webhooks (to URLs you configure), and SMTP email (to a relay
> you configure). Each is independently disableable. Air-gap mode blocks all of
> them at once and refuses to boot if any is still enabled.
>
> **Keys never leave, never log.** Passport signing keys live in your data
> volume and never appear in logs, events, or exports (regression-tested).
>
> **Verification is offline-capable.** Passports verify against a JWKS file with
> no network, so downstream relying parties inside your perimeter can verify
> without reaching the internet.

Hand this section to your security reviewer.
