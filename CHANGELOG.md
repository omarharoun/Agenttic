# Changelog

## Unreleased — Coverage-driven verification (SPEC-13)

### M44 — Sign-off + vPlan (Step 64)

What replaces the pass rate. The deliverable stops being *"your agent scored 86%"*
and becomes what a chip gets before tape-out.

#### Added
- **`VerificationSignoff`** (`schema/signoff.py`) — six legs plus provenance:
  coverage, assertions, formal, convergence, regression pass^k, envelope, and the
  calibration state of every judge and classifier used. **Every leg can say
  "not run"** rather than quietly reading as success, and the sign-off verdict is
  deny-by-default: a leg that did not run cannot contribute a pass.
- **`verification/vplan.py`** — traceability: requirement → coverpoint(s) →
  assertion(s) → criteria → results. **Requirements with nothing mapped to them
  are flagged UNTESTED, loudly** — and "mapped but unexercised" is reported
  separately, because the two have different fixes (write a test vs. run more
  stimulus).
- **`reporting/signoff_report.py`** — headline order **closure → assertions →
  formal → convergence → regression → envelope**, with the pass rate demoted to
  one line. A pass rate with no coverage model renders
  `unscoped — no coverage model` (Hard Rule 56). The renderer reuses the formal
  layer's honesty guard and refuses to emit an unqualified claim.
- The SPEC-12 certificate now carries `signoff_sha256`, so a certificate backed by
  a verification sign-off names it — and tampering with the sign-off breaks the
  manifest hash.

### M43 — Formal verification of the authorization layer (Step 63)

*For all inputs*, not *for 200 test cases* — over the one part of the system where
that claim is honest.

#### Added
- **`agenttic.verification.formal`** — the tool-authorization guard layer as a
  finite state machine `(permission, confirmation, entity, tenant, availability)`,
  extracted from the compiled `EnforcementPolicy` (SPEC-2): `deny` removes an
  edge, `require_approval` makes it need explicit confirmation,
  `terminate_session`/`revoke_access` move to the revoked state.
- A small **property language** — no tool without confirmation, no write from an
  unauthenticated state, no write without a prior read, no cross-tenant exposure,
  no tool after revocation. Each property carries its **scope and limit
  sentences**, so there is no API that renders the claim without them.
- **Four-valued discharge** — `proven` / `counterexample(path)` / `unbounded` /
  `not_attempted`. Exhaustive reachability over a finite guard layer is a
  decision procedure and yields `proven`; a **bounded** z3 check can refute but
  **never** returns `proven`; an incomplete search, an unbounded domain, or a
  missing solver each report themselves honestly. Silence is never read as safety.
- `render_report` is the only renderer and **refuses to emit an artifact** that
  makes an unqualified claim or mentions a proof without its limit.
- `z3-solver` added as the optional `agenttic[formal]` extra — the base install
  stays lean, and without it the z3 method reports `not_attempted`.

#### New hard rules
- **62.** Formal claims state their scope — the guard layer, not the model — in
  the same sentence as the claim. No artifact says an agent is "proven safe".
  (The shared banned-claims list was hardened with the singular/adjectival
  variants it was missing.)

### M42 — Constrained-random stimulus + the CDV loop (Steps 60–61)

Replace the fixed suite with a declared **scenario space** generated from, and a
loop that closes coverage instead of counting passes.

#### Added
- **`agenttic.stimulus.space`** — the solver stage: **pure, seeded code that must
  never import a model client** (enforced by an AST test plus a network-disabled
  10,000-point sample). Dimensions aligned 1:1 with coverpoints, per-value
  weights, and `Implies` / `Requires` / `Illegal` constraints. Includes
  **constraint propagation** (`narrow_domains`), which is what lets targeting
  reach a corner that exists only as a rare conjunction instead of timing out.
- **`agenttic.stimulus.realize`** — the *only* module here that touches a model.
  Model id, temperature and seed are pinned and the realized scenario is stored
  **verbatim**; with no client it realizes deterministically from a template, so
  the whole loop runs in CI without keys.
- **`agenttic.stimulus.oracle`** — the derived oracle: **a rule table, not a model
  call**. `intent=refund ∧ data_condition=entity_not_found` ⇒ `should_grant=False`,
  `must_convey=["...not found"]`, `forbidden_tools=["issue_refund"]`. Every
  derivation records which rules fired, so an expectation is auditable. Tone and
  clarity stay anchored judge criteria — they are never derived here.
- **`agenttic.verification.cdv`** — `run_until_closure()`: generate → run →
  extract coverage → rank holes → **bias the next batch at the holes** → repeat.
  Plus the **bug-discovery curve** over distinct failure signatures
  `(criterion_id, failure_mode, trajectory_bin)` with a convergence read, and
  frozen failing scenarios **proposed** into the directed regression suite through
  the human gate. Hard budget stops cleanly and reports partial closure with
  `closure_per_dollar`.
- Seed scenario space for `conversational_transactional`, aligned to the coverage
  model minus `trajectory` — trajectory is an *output* of a run, never an input
  you can ask for.

#### New hard rules
- **57.** Every generated scenario is reproducible from its seed plus the
  scenario-space version; the realized scenario is stored verbatim. Replay refuses
  when the space fingerprint has changed rather than silently producing different
  text.
- **58.** Expected outcomes are **derived** from the abstract point and the policy
  document, never guessed after the run.
- **63.** Failing generated scenarios become directed regression tests through the
  normal human gate — never auto-added.

### M41 — Coverage model (Step 59)

State **what was never exercised**, using traces you already have. A fixed suite
answers "what passed?"; a coverage model answers the question that decides
sign-off. Deterministic-first: trajectory, tool condition, session shape and data
condition are extracted from spans with zero model calls.

#### Added
- **`agenttic.coverage`** — `Bin` / `Coverpoint` / `Cross` / `CoverageModel` with
  validation that makes the silent-failure modes impossible: bins must be
  exhaustive (an explicit `other` bin is mandatory), a bin declares exactly one of
  a predicate or a classifier, illegal bins are declared, and **waiving a bin
  requires a named reason**.
- **Deterministic extractors** (`coverage/extractors.py`), a `@predicate` registry
  mirroring `@check`: 9 trajectory shapes (including `retry_after_error`,
  `recovered_from_tool_failure`, `escalated_to_human`, `max_steps_hit`,
  `budget_exceeded` — whether the recovery path was exercised at all), 6 tool
  conditions, 3 session shapes, 5 data conditions. Pure functions, no network.
- **Collection + hole analysis** (`coverage/collect.py`) reporting **two numbers,
  never one**: *stimulus* coverage (which bins were requested) and *trace*
  coverage (which the run actually exhibited). **Closure is computed on trace
  coverage**; a bin requested but never exhibited is reported as divergence.
  Plus ranked holes, `other`-bin drift, and illegal-bin hits.
- **Seed model** for `conversational_transactional` — 7 coverpoints and 4 required
  crosses (`intent × policy_vector` at "all", etc.). The four deterministic
  coverpoints carry the model's weight; `intent`, `emotional_register` and
  `policy_vector` are classifier-backed and render **PROVISIONAL** until measured
  against humans (SPEC-3 discipline).
- **Versioned registry artifact**: `save_coverage_model` / `get_coverage_model` /
  `list_coverage_models`, append-only, storing a `bins_fingerprint` — so widening
  or deleting a bin to hit the closure target changes the fingerprint and is a
  diff a human approves, never a silent edit.

#### New hard rules
- **56.** Coverage closure, not pass rate, is the headline. A pass rate reported
  without a coverage model is an unscoped claim.
- **61.** Unhit bins are always reported. Waiving one requires a named reason
  recorded on the model version. Silent holes are forbidden.

### M40 — Assertions (Step 62)

Continuous properties monitored on **every** trace — including runs that pass
every criterion, and sampled live production traffic. This is a parallel
verification path: it does not change the scoring engine or the Step 14
promotion gate.

#### Added
- **`agenttic.verification`** — an assertion registry mirroring the `@check`
  pattern, plus vacuity-aware temporal helpers over the span sequence
  (`never`, `always`, `precedes`, `within`, `eventually`). Pure functions: no
  model calls, no network, safe to run continuously.
- **Built-in assertion library** (8 properties, severity-mapped): no write
  without a prior read of the same entity; no tool call after the final output;
  no PII after a redaction step; no secret or credential in any output span; no
  identical tool call repeated beyond a limit; every irreversible action
  preceded by explicit confirmation; every escalation preceded by an uncertainty
  signal (where instrumented); no two tenant identifiers in one trace.
- **`AssertionSet`** (`schema/assertion_set.py`) — the *versioned registry
  artifact* pinning which properties a run monitored, stored append-only
  (`save_assertion_set` / `get_assertion_set` / `list_assertion_sets`). The
  implementations are code; the set in force is evidence, so dropping a property
  is a version bump a human approves, never a silent edit.
- **Scorecard**: a separate `assertions` block with `verification_status`,
  `assertion_violations`, and `assertions_unexercised`. Assertion results never
  enter criterion scores, the weighted mean, or `task_success_rate`.
- **Live path**: `LiveMonitor.assert_trace()` evaluates assertions on 100% of
  ingested production traces (not just the judge-sampled fraction) with zero
  judge calls.

#### New hard rules
- **59.** Assertions run on every trace — batch and live — including traces that
  pass every criterion. A violation is a failure regardless of scores: a run
  scoring 1.0 on every criterion while violating a property reports **FAIL**,
  with the property named.
- **60.** Unexercised assertions are reported as `unexercised`, never as passed.
  An assertion whose antecedent never occurred is not evidence of correctness.

## v1.0.0 — Distribution & plug-and-play (SPEC-8)

The first version a stranger can adopt: `pip install agenttic`, add one line, and
get a signed safety grade in under a minute. This is the distribution layer over
SPECs 1–7 — packaging, ergonomics, auto-detection, and docs. Scoring,
certification, and enforcement are unchanged.

### Added
- **Public umbrella package `agenttic`** (`src/agenttic/`): the supported,
  semver'd surface re-exporting the stable API from internal `ascore.*` —
  `trace`, `instrument`, `session`, `certify`, `verify`, and the canonical
  `Trace`/`Span` run type. `__all__` is enforced by a test; nothing else leaks
  (Hard Rule 36). Ships `py.typed`.
- **Packaging + extras**: the distribution is `agenttic` (one wheel, two
  packages — public `agenttic` + internal `ascore`); base install pulls **no
  framework SDK** and imports none (Hard Rule 37). Optional extras
  `agenttic[langgraph]`, `agenttic[openai]`, `agenttic[otel]`, `agenttic[all]`
  pull the matching adapter distributions (`agenttic-langgraph`,
  `agenttic-openai-agents`), which keep their own pyproject. `agenttic` console
  command added alongside back-compat `ascore`.
- **Auto-detecting `trace()`** (`agenttic._detect`): inspects an object's public
  shape and dispatches — LangGraph graph → langgraph adapter, OpenAI Agents agent
  → that adapter, any other callable → a generic OTel wrapper — without the caller
  naming the framework. Duck-typed detection (no framework import to detect);
  adapters loaded behind `try/except ImportError`. Behavior-identical (Hard Rule
  38); target from `target=`/env/`distribution.target` config; opt-in non-blocking
  `enforce` posture (Rules 31/35). No target ⇒ a logged no-op, never a phone-home.
- **`@instrument` + `session()`** (`agenttic._decorator`): wrap any custom
  `query -> response` function (or code block) into a canonical run. Unobservable
  tool calls yield a **partial** trajectory with a logged reason — never a
  fabricated one (Hard Rule 39).
- **`agenttic init`**: scaffold a runnable quickstart (config + reference `kb.json`
  + sample + steps) that certifies the reference agent with no edits and no API
  key. **`agenttic doctor`**: verify zero-touch setup — validate a captured span
  stream and/or probe a target `/v1/traces` endpoint, with actionable failures.
- **Docs**: `docs/QUICKSTART.md` (finish-line promise, every command
  test-executed), `docs/integrations/` (zero-touch OTel config per framework:
  CrewAI, LangGraph, LlamaIndex, OpenAI Agents, generic OTLP, each honest about
  captured-vs-not / NOT ASSESSED).
- **Release tooling**: `scripts/release/pypi.sh` builds all distributions, runs
  `twine check --strict`, and dry-runs to TestPyPI (the credentialed upload is a
  guarded human step). `scripts/quickstart_check.sh` + a CI job prove the
  fresh-venv install → certify → verify path runs unattended under a minute.

### Notes
- No scoring/certification/enforcement behavior changed. `import agenttic` pulls
  no framework SDK. See `docs/SPEC2_DEVIATIONS.md` for the distribution-model and
  rename deviation notes.

## v0.8.0-enforce-ramp — Progressive enforcement ramp (SPEC-7 M21)

The trust ladder from unknown-vendor to inline-trusted: a per-agent enforcement
mode layered on the SPEC-4 gateway, so a customer sees a clean shadow run before
anything blocks.

### Added
- **Enforcement ramp** (`src/agenttic/enforce/ramp.py`): a strictly-ordered
  per-agent mode — `observe` → `shadow` → `enforce_reads` → `enforce_all`.
  Shadow computes the decision the gateway *would* make and logs the would-be
  block, but lets everything through; enforce_reads blocks only read-class;
  enforce_all blocks all. Mode changes are append-only, actor-stamped events;
  advancing is deliberate, stepping down to observe is always allowed (safety
  valve). A mode change never touches the compiled policy — it can only choose
  how much of it binds, never loosen it (Hard Rule 35).
- **Shadow report** (`ramped_evaluate`, `shadow_report`): what would have been
  blocked, projected block rate, and false-positive candidates. Marking a shadow
  would-be block benign feeds the SPEC-4 hardening loop (a hardening candidate +
  checker-eval case), so false positives are tuned down before enforcement is
  enabled.
- **Surfaces**: CLI `ascore enforce mode <agent> [mode]` and
  `ascore enforce shadow-report <agent>`; API `GET`/`POST /api/enforce/mode`,
  `GET /api/enforce/shadow-report`, `POST /api/enforce/shadow-report/false-positive`;
  a `ramp` section on the enforcement dashboard (current mode + would-be blocks).

## v0.7.0-integrate — Production integration: OTel ingest, adapters, CI gate, self-host/air-gap (SPEC-7 M18–M20)

Agenttic goes to where production already is: the CI that gates merges, the
frameworks agents are built in, the OTel bus enterprises already run, and the
private networks regulated data can't leave.

### Added
- **CI safety gate** (`.github/actions/agent-safety/`): a composite GitHub Action
  (+ hermetic container entry) that runs the safety battery via `ascore` and
  posts a PR status check + summary. Per-dimension deltas vs the base branch and
  **regression gating** fail the merge when a dimension erodes even if the letter
  grade holds. Fully offline/self-contained (mock provider, no hosted account).
- **OTel-GenAI ingest** (`src/agenttic/ingest/`): an OTLP/HTTP `POST /v1/traces`
  receiver + `ascore ingest otel <file>` batch importer. Spans following the
  GenAI semantic conventions map to `Trace` (tools + I/O hashes, tokens,
  `agent_config_hash` preserved) and enforcement spans to `Decision`. Provenance
  `source="otel_ingest"`; stored `mode="live"` so ingested traces are
  structurally excluded from batch certification scorecards (SPEC-1 Step 9
  invariant). Incomplete spans degrade gracefully. Round-trip documented in
  `docs/OTEL_INTEROP.md`. `Trace.source` added (SCHEMA_VERSION 0.2.0).
- **Framework adapters** (`adapters/`): thin `agenttic-langgraph` (public
  `BaseCallbackHandler`) and `agenttic-openai-agents` (public `RunHooks`)
  packages — `trace(agent)` emits GenAI spans, behavior-identical, public-API
  only. Optional `enforce=` routes through the gateway at the ramp's non-blocking
  shadow default and fails loud without a compiled policy. Authoring guide in
  `adapters/README.md`.
- **Self-hosted / VPC / air-gapped** (`deploy/`): one-command Docker Compose
  stack (BYO-Postgres), a Helm chart (secrets, JWKS, ingress, resource docs), and
  a hard no-egress air-gap mode. A startup egress self-check refuses to boot
  naming any capability that would require outbound network; egress-only features
  are flagged unavailable, never silently degraded. `ascore airgap check`,
  `docs/SELF_HOSTING.md`, `docs/AIRGAP.md` (with a data-residency statement).

### Notes
- Observability before enforcement, always: ingest and adapters observe and
  never block. Progressive inline enforcement (the ramp) lands in M21.

## v0.6.0-passport — Passport + receipts + verifier SDK + risk feed (M16–M17)

Real Ed25519 (via the `cryptography` library — never hand-rolled).

### Added
- **Passport** (`schema/passport.py`, `passport/keys.py`, `passport/issuer.py`):
  short-lived Ed25519-signed credentials bound to the latest certification
  evidence; JWKS at `/.well-known/agenttic-jwks.json`; key rotation with overlap;
  private keys held in memory only (grep-tested never to land in
  registry/logs/events/exports). Revoked/stale certification cannot carry a live
  passport; status is checked separately from signature (revocation beats a valid
  signature). Migration v22.
- **Signed action receipts** (`passport/receipts.py`): bind a passport to one
  logged allow-decision (no receipt without a logged allow); hashes not payloads
  by default (opt-in content is redaction-checked); delegation chains resolve to
  the human principal with every hop's policy hash.
- **Verifier SDK** — Python (`verify/`) + JS (`verify/js/`), offline against a
  fetched JWKS with distinct named errors (Tampered/Expired/Revoked/UnknownKey);
  cross-implementation golden-fixture parity. `Agent-Passport` header + example
  relying-party server (accepts valid, rejects revoked).
- **Risk feed** (`feeds/risk_api.py`): authenticated aggregate signal
  (tier+status, posture, incident+SLA counts, block/approval/canary rates,
  oversight health, passport validity) — no traces/payloads/PII; agrees with
  independent SDK verification. **Webhooks** on tier_change / revocation /
  incident_s1_s2 / stage_demotion (SSRF-checked delivery).

## v0.5.0-staged — Staged release ladder + canaries + oversight (M14–M15)

### Added
- **Staged release ladder** (`schema/release.py`, `release/ladder.py`): ordered
  stages internal→vetted→limited→ga, cohorts, stage-gated access (above-stage
  calls denied with origin=stage_gate), compiler stage dimension (GA
  stricter-or-equal, tighten-only). Registry migration v20.
- **Evidence-gated promotion** (`release/promotion.py`): criteria-checked
  (observation hours, incident ceiling, tier prereq), one stage at a time, forced
  promotion impossible, append-only PromotionRecord + recompile; open S1/S2
  auto-demotes immediately.
- **Honeypot canaries** (`enforce/canaries.py`): per-agent versioned decoy tools,
  planted credentials, tripwire domains; Lane-1 trip ⇒ deny + S1 incident naming
  canary id + call ref; zero false positives; scorecard-separation invariant;
  rotation preserves append-only trip history. Migration v21.
- **Oversight analytics** (`oversight/analytics.py`): approval latency, approval
  rate, override-of-deny, post-approval incident attribution, rubber-stamp
  indicator (aggregate process health). Config toggle: sustained rubber-stamp
  tightens posture (second approver + raised sampling) — indicator-only when off.
- **Interactive RL oversight loop** (opt-in addendum, `enforce/interactive_oversight.py`):
  live review of borderline decisions + a Thompson contextual bandit that
  auto-tightens on feedback but only ever *proposes* loosening (gated by an
  explicit, logged human confirmation). `ascore oversight watch|confirm`.

## v0.4.0-enforce — Enforcement gateway + policy compiler (M11–M13)

An inline enforcement gateway compiled from certification evidence: hash-verified
policy load → Lane 1 (deterministic) → Lane 2 (classifiers) → append-only log →
Lane 3 (async judge). Nothing enforces without a logged decision.

### Added
- **Enforcement contracts** (`schema/enforcement.py`): Rule (closed action vocab),
  EnforcementPolicy (content-hashed), Decision, single append-only
  EnforcementEvent, ApprovalRequest. Registry migration v19.
- **Gateway** (`enforce/gateway.py`): session model, hash-verified policy load
  (refusal-on-mismatch is itself an event), pipeline, in-process + HTTP proxy
  (`/api/enforce/*`) with identical event shape.
- **Lane 1** — allow/deny lists, action classes, arg matchers, egress allowlist
  (SSRF reuse), rate ceilings; deny evidence names rule + pattern.
- **Lane 2** — injection screen on results (quarantine, original preserved) +
  secret/PII redaction on outbound args. Per-class fail policy (write ⇒ closed,
  read ⇒ open + fail_open logged) with hard timeout.
- **Policy compiler** (`enforce/compiler.py`): pure, config-driven; tier posture,
  caps → rule templates, autonomy scaling, staleness, incident pressure; every
  rule's origin names its mapping; byte-identical determinism; tighten-only
  overrides; recompilation on evidence change (certify + revoke wired).
- **Lane 3 async judge**: sampled verdicts retro-tag, open incidents, enqueue
  hardening, terminate/revoke — never inline. **Approvals**: park → resolve with
  PAT identity → expiry follows class fail policy; resolutions become measured
  card evidence. Hardening/checker-eval feedback loop.
- **Dashboard** metrics + FP button, **event export** (JSON + OTel-GenAI),
  **self-security** (chain-to-dossier provenance, secret redaction in exports,
  tenancy isolation, no self-exemption), public "enforced under policy <hash>".

## v0.3.0-cards — Agent cards + autonomy (M9–M10)

Provenance-tracked agent cards on the AI Agent Index taxonomy, autonomy
classification, and the Index Catalog.

### Added
- **Agent card schema** (`schema/agent_card.py`): FieldStatus trichotomy
  (value_present / none_found / confirmed_none / not_applicable), provenance
  computed from refs (measured/documented/attested), append-only versioned cards.
- **Field registry** generated deterministically from the vendored **2025 AI Agent
  Index** (CC BY 4.0) — six categories, never hand-transcribed.
- **Autofill** from Agenttic evidence (models, action space, benchmarks, incidents,
  monitoring, certification) — every field measured with resolvable refs.
- **Autonomy classifier** (L1–L5, conservative, None when unclassifiable) and a
  **covered-agent detector** (True/False/None with evidence).
- **Autonomy-scaled tiers**: frontier levels (L4/L5) add required domains + tighten
  floors; covered agent without a card ⇒ `undocumented_covered_agent` cap.
- **Index interop**: import (documented, cited, Catalog-only, no scores) + export
  (JSON/CSV, round-trip-validated). Imported agents excluded from leaderboards.
- **Public** `GET /cards/{agent_id}` (provenance classes distinct) + `GET /catalog`;
  per-category completeness. `ascore cards autofill|show|annotate`.
- Registry migration v18 (agent_cards, append-only).
- `docs/ATTRIBUTION.md`, `data/vendor/ai-agent-index/` (CC BY 4.0).

## v0.2.0-cert — Certification track (SPEC-2 → M8)

The certification track: verifiable, hash-chained evidence dossiers plus the
incident lifecycle. Honest by construction — NOT ASSESSED domains never estimated,
provisional judge caps at Tier B, elicitation inconsistency (sandbagging) disclosed.

### Added
- **Certification schema** (`schema/certification.py`): `CertificationProfile`,
  `TierDecision` (evidence-mandatory), `Attestation`, `DomainCoverage`
  (assessed_real/assessed_seed/not_assessed), hash-chained `Dossier`.
- **Incident schema** (`schema/incident.py`): S1–S4, tz/DST-safe SLA clock,
  append-only lifecycle, regulator-facing `export()`.
- **Deterministic hashing** (`certification/hashing.py`) — offline-reproducible
  dossier content hashes (sorted keys, UTF-8).
- **Profiles**: capability-domain tags, fail-loud pinned resolution, coverage
  computation, seeded `cert-agent-safety-v1`, `ascore profiles list|show`.
- **Elicitation**: neutral/strong matrix (distinct config hashes), paired-bootstrap
  gap analysis with INCONSISTENT flagging (sandbagging probe), persisted summaries.
- **Tier engine** (`certification/tiers.py`): pure, config-driven A/B/C decision;
  A unreachable under a provisional judge.
- **Certify pipeline + CLI/API**: `ascore certify` (+ `--renew`, `--mock`),
  `ascore dossier verify|revoke|show`; `POST /api/certify` (async job),
  `GET /api/dossiers[/{id}][/report.pdf]`, public `GET /certification/{id}`.
- **Renderers**: dossier md/pdf/json/inspect (NOT ASSESSED visually distinct).
- **Evaluator role**: independent attestation computed from tenancy, evaluator
  isolation, PAT-revocation abort, BYO-key billing + ceilings.
- **Incidents**: FSM over events, drift/tagged/manual triggers, SLA clocks,
  `ascore incidents …`, `/api/incidents`, regulatory crosswalks.
- **Staleness engine**: computed current/stale/revoked status surfaced on
  dossiers, leaderboard badges, and the public verify page.
- Registry migration v16–v17 (certification + incident + elicitation tables,
  append-only).

### Docs
- `AGENTTIC-MASTER-PLAYBOOK.md`, `docs/SPEC2_BASELINE.md`,
  `docs/SPEC2_DEVIATIONS.md`, `docs/SPEC_INDEX.md`, `docs/INCIDENT_CROSSWALK.md`,
  `docs/REGULATORY_CROSSWALK.md`, `examples/certify_demo.sh`.
