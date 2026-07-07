# Changelog

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
