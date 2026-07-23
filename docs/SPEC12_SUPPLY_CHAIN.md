# SPEC-12 — Certifying the Agent Supply Chain (build record)

The shift from *certifying agents* to *certifying the agent supply chain*. An
agent is only as trustworthy as the tools it calls, the memory it carries, and
the evidence binding those facts together — certifying the agent alone certifies
a façade.

## Status

| Step | Milestone | State | Notes |
| --- | --- | --- | --- |
| 54 — Attestation + ABOM + revocation | M36 | ✅ done | 18 tests, all 7 acceptance criteria |
| 55 — MCP server certification | M37 | ✅ done | 9 tests, all 5 acceptance criteria |
| 56 — Tool certification (component tier) | M38a | ✅ done | 7 tests, all 3 acceptance criteria |
| 57 — Memory testing | M38b | ⬜ not started | needs a memory/multi-session layer on `camp/environment.py` |
| 58 — Catalog conformance | M39 | ⬜ not started | catalog export, promotion record, shadow-mode challenger, retirement |

## What shipped

### Step 54 — sign the evidence, never the verdict (Hard Rule 51)
- `schema/attestation.py` — `EvidenceManifest` (subject + `agent_config_hash`,
  suite/rubric versions, judge configs with calibration state + α + human
  ceiling, k, integrity gates, contamination, scorecard hash, environment,
  issuer, scope/limits) with **canonical serialization**: fixed-precision floats
  and UTC datetimes layered over the existing `certification.hashing`
  canonicalizer, so evidence hashes identically across processes (proven in a
  subprocess) without introducing a third canonical form.
  Model validation **refuses** a manifest with no expiry, no config-hash binding,
  or a banned unbounded claim.
- `certification/attest.py` — two tiers: **local self-attestation** (Ed25519 key
  generated on first use in the user's config dir, 0600, offline — proves
  integrity, not neutrality) and **assurance** (the platform's published issuer
  key). The rendered certificate always states its tier (Hard Rule 55).
  `verify_manifest` recomputes every hash from stored evidence and reports a
  precise reason: altered scorecard, altered manifest body, bad signature,
  subject mismatch, expired, suspended/revoked.
- **Revocation list** — signed, append-only. None existed before (only per-object
  revocation). `suspend_on_drift()` wires the live monitor's re-eval requests to
  automatic suspension (Hard Rule 52).
- `certification/abom.py` — CycloneDX 1.6 Agent BOM: models + parameters, prompt
  **hashes** (never inlined), tools, MCP servers, suite/rubric, harness,
  dependencies. Validated with `jsonschema` (no new dependency), referenced from
  the manifest by hash.
- CLI: `agenttic attest` / `verify` / `abom`.

### Step 55 — MCP server certification (Hard Rule 54)
- `adapters/mcp_server.py` — a dependency-free MCP client (JSON-RPC 2.0) over
  **stdio and HTTP**, with protocol discovery. Deliberately no `mcp` SDK: the
  certifier must probe a *misbehaving* server without an SDK normalising the
  faults being detected.
- `certification/mcp_suite.py` — contract, **golden responses pinned per server
  version** (schema drift is caught as a breaking change), input fuzzing,
  authorization, error taxonomy, idempotency, rate limiting, side-effect
  disclosure, and the novel **tool-response injection** probe. Results attach to
  a Step-54 signed manifest naming the server + version.
- CLI: `agenttic certify-mcp`. The good fixture scores 1.00; the broken fixture
  scores 0.25 naming all six defects.

### Step 56 — tool certification, the component tier
- `certification/tool_suite.py` — framework-agnostic (`from_mcp` / `from_native`),
  reusing the Step 55 battery plus **description quality** (cross-model
  tool-selection accuracy: a vague description scores measurably lower than a
  rewritten one, converting a "model failure" into a named root cause) and
  **failure-mode handling** (rate limit / timeout / 5xx must surface a typed
  error). `link_to_agent_scorecard()` feeds component results into the agent-level
  failure catalogue, so a failing agent's report says whether a tool it used was
  already known-weak.

## Hard rules added (51–55)

51. Sign the evidence, never the verdict — no artifact asserts an agent is
    "safe"; every certificate carries scope and limits. (Enforced by model
    validation + `assert_no_banned_claims`, and tested.)
52. Certificates expire, and drift revokes them automatically.
53. A certificate is bound to an exact `agent_config_hash`; a changed subject
    invalidates it by construction.
54. Component certification (MCP servers, tools, memory) is attestable on its own
    terms.
55. Local self-attestation is never presented as third-party assurance; the tier
    is stated in the artifact.

## Honest notes

- **The spec's own caveat stands.** All of this makes the certificate *stronger*,
  none of it makes it *proven*. SPEC-8's real-model run and human judge
  calibration remain the gate between capability and evidence — a beautifully
  signed manifest attesting an uncalibrated judge is exactly the false confidence
  this platform exists to prevent. The manifest therefore records
  `calibration_state` per criterion so an uncalibrated judge is visible in the
  evidence rather than hidden by the signature.
- **Sigstore** (keyless Fulcio/Rekor signing for the assurance tier) is modelled
  in the schema (`transparency_log_url`) but not wired — it needs an external
  service and an issuer identity, so it is deliberately left as an integration
  point rather than faked.
- **Cross-model behaviour** (Step 55's last bullet) is implemented through the
  injectable `selector` in Step 56; running it against ≥2 *real* models needs API
  keys and is not exercised in CI.
- Steps 57–58 are not started; Step 57 needs a memory/multi-session layer built
  on `camp/environment.py` (the SPEC-7 Step 29 engine, which does exist).

## Verification

34 new tests (18 attestation + 9 MCP + 7 tool), 93 passing across SPEC-12 and the
existing certification suite; new modules ruff-clean; CLI paths exercised end to
end.
