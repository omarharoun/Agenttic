# Changelog

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
