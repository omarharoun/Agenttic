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
| 57 — Memory testing | M38b | ✅ done | 16 tests, a 7-check battery run ACROSS session boundaries |
| 58 — Catalog conformance | M39 | ✅ done | 28 tests, promotion gate + shadow mode + retirement cascade + conformance |
| CLI + surface | — | ✅ done | `certify-memory`, `catalog-check`, 7 CLI tests; `/api/capabilities` enumerates both |

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

### Step 57 — memory testing (the third leg of Hard Rule 54)
Memory is the component an agent-level evaluation structurally *cannot* reach: a
tool call is one request/response, but memory is state that survives the session
boundary, and every interesting memory defect is invisible inside one session.

- `camp/memory.py` — `MemoryStore` (a four-operation Protocol: `write` / `read` /
  `forget` / `stats`, small enough that adapting a vector DB or a hosted memory
  API is a few lines) and **`MemorySessionEnv`**, an `Environment` (SPEC-7 Step 29
  `reset`/`step`) whose `reset()` **ends the session and opens a new one against
  the same store**. That is the whole mechanism: the session boundary becomes
  something the harness crosses on purpose. `ReferenceMemoryStore` is the correct
  implementation and the positive fixture; it is deterministic (an integer
  sequence, never wall-clock) so probes always produce the same findings.
- `certification/memory_suite.py` — the battery, declared once in `MEMORY_CHECKS`
  so the capability surface enumerates it rather than restating it (a test pins
  the declared list to the list `certify_memory` actually runs):

  | check | the question | critical |
  | --- | --- | --- |
  | `persistence` | does a session-1 write survive into session 2? | ✓ |
  | `principal_isolation` | can principal B retrieve A's memory? | ✓ |
  | `deletion_honored` | is a forgotten record gone from **every** index? | ✓ |
  | `memory_injection` | is recalled text returned as untrusted **data**? | ✓ |
  | `contradiction` | does a newer write to a fact key beat the older? | ✓ |
  | `retrieval_precision` | F1 over seeded probes (a floor, not a ranking) | |
  | `capacity_bound` | at capacity, evict or refuse — and **disclose** it | ✓ |

  The three with a blast radius outside the agent are isolation (a data breach),
  deletion (a deletion request answered honestly and honoured falsely — the
  classic "dropped from the primary map, still served by the vector index"), and
  injection (memory is the one channel a system trusts completely, because it
  looks like the agent's own prior thought). Injection does **not** require the
  store to sanitise prose — that is a losing game — it requires the store to hand
  content back *flagged*, so the prompt builder can place it as data.
  Ground truth stays with the operator: `declared_capacity` and the principals are
  supplied, never self-reported, and without a declared capacity that check is
  **skipped rather than assumed**.
- `LeakyMemoryStore` (negative fixture) carries five defects that ship in real
  systems — no principal scoping, deletion from the primary map only, no
  supersession, no untrusted marker, unbounded and undisclosed. It scores **0.14**
  and names all six failures.
- CLI: `agenttic certify-memory --store module:attr` (class, zero-arg factory or a
  built store) or `--reference`; `--attest` writes a signed manifest whose subject
  is `memory:<name>`. `link_memory_to_scorecard()` composes with Step 56's
  `component_evidence` rather than clobbering it.

### Step 58 — catalog conformance (the promotion gate)
Steps 54–57 make individual subjects attestable. This is what an organisation
running more than one of them needs: a register of what is approved, and a rule
about how something enters and leaves it. The register is worth exactly as much
as the rule, so the rule is **enforced in code**.

- `certification/catalog.py` — `Catalog`, an append-only register of
  `CatalogEntry` (agent / tool / mcp_server / memory) with statuses
  `candidate → shadow → promoted → needs_reverification → retired`. Nothing is
  ever deleted: "we used to approve this" is exactly the question an incident asks.
- **Promotion refuses by default.** `promote()` raises `PromotionRefused` — an
  exception, not a boolean, so a caller that ignores it fails loudly — unless the
  signed manifest verifies, has not expired, has not been revoked, and a *named*
  approver supplied a written rationale. There is no `force` argument. Registering
  straight into `promoted` is refused too.
- **Shadow mode.** `shadow_compare()` runs a challenger beside an incumbent on
  identical stimulus and counts regressions **per case** — cases the incumbent
  handled and the challenger did not. Promoting over an incumbent refuses while
  any regression stands, *including when the average improved*, which is the
  failure mode an aggregate hides. A superseded incumbent steps down to
  `candidate` rather than staying silently approved.
- **Retirement cascades.** `retire()` moves every dependent certified with the
  subject to `needs_reverification` and appends to the revocation list —
  `revoked` for the subject, `suspended` for each dependent, sourced
  `catalog:retire_cascade`. This lives in the catalog because no single subject
  knows who depended on it.
- **Conformance reports, never repairs.** `check_conformance()` walks the register
  and returns findings — `evidence_expired`, `evidence_revoked`,
  `evidence_mismatch`, `evidence_unavailable` (a referenced-but-unsupplied
  manifest is a *warning*, not a pass), `needs_reverification`,
  `unregistered_dependency`, and `uncertified_dependency` (an agent approved on
  the strength of a component that is not). Silently downgrading an entry would
  hide the window in which something was approved on lapsed evidence.
- **Export round-trips.** `export()` is canonical and hashable — so a catalog is
  itself signable as Step-54 evidence — and `from_export()` rebuilds it, which is
  what an export is *for*: checking someone else's register in CI or as an
  auditor, without their process.
- CLI: `agenttic catalog-check <catalog.json> --manifests <dir>` prints findings
  and exits 1 on any error, so it gates a pipeline.

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
- **The memory battery tests mechanics, not judgement.** It measures isolation,
  deletion, contradiction, injection handling and capacity; it does not judge
  whether what a store chose to remember was worth remembering. `/api/capabilities`
  says so in `not_covered` rather than leaving the boundary implied.
- **`retrieval_precision` is a floor, not a ranking.** It exists to catch a
  retriever that is effectively random, not to rank embedding models — hence the
  low default (F1 ≥ 0.7) and the deliberately simple token-overlap corpus.
- **The catalog gate is only as good as its dependency edges.** `depends_on` is
  operator-supplied; an agent whose dependencies were never recorded will pass
  conformance while resting on uncertified components. The catalog reports what it
  was told, and cannot report what it was not.

## Verification

85 new tests (18 attestation + 9 MCP + 7 tool + 16 memory + 28 catalog + 7 CLI),
passing across SPEC-12 and the existing certification suite; new modules
ruff-clean; CLI paths exercised end to
end.
