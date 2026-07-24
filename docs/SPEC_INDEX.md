# Spec Index — Agenttic

Cross-links and status for the platform spec surfaces.

## Contracts
- [SPEC.md](../SPEC.md) — SPEC-1: the 10-step evaluation platform (Hard Rules 1–8).
- [AGENTTIC-MASTER-PLAYBOOK.md](../AGENTTIC-MASTER-PLAYBOOK.md) — SPEC-2 → SPEC-6
  certification / enforcement / passport track (Hard Rules 9–30). Binding contract
  (the six `SPEC-*.md` files are absent by design).

## Build record
- [docs/SPEC2_BASELINE.md](SPEC2_BASELINE.md) — SPEC-1 surface survey + green baseline.
- [docs/SPEC2_DEVIATIONS.md](SPEC2_DEVIATIONS.md) — path adaptations ledger.
- [docs/INCIDENT_CROSSWALK.md](INCIDENT_CROSSWALK.md) — incident export → SB53/RAISE/EU CoP.
- [docs/REGULATORY_CROSSWALK.md](REGULATORY_CROSSWALK.md) — dossier artifacts → clause families.

## Milestone status

| Milestone | Scope | Status | Release tag |
| --- | --- | --- | --- |
| SPEC-1 | 10-step evaluation platform | ✅ shipped | — |
| P0 | Certification workspace + config surface | ✅ done | — |
| M4 | Certification schema + profiles | ✅ done | — |
| M5 | Elicitation + certify vertical slice | ✅ done | — |
| M6 | Attestation + incidents | ✅ done | — |
| M7 | Staleness + public verification | ✅ done | — |
| M8 | Certification release | ✅ done | `v0.2.0-cert` |
| M9 | Agent cards + autonomy | ✅ done | — |
| M10 | Autonomy policy + Index interop | ✅ done | `v0.3.0-cards` |
| M11–M13 | Enforcement gateway + compiler + lanes | ✅ done | `v0.4.0-enforce` |
| M14–M15 | Staged release ladder + canaries/oversight | ✅ done | `v0.5.0-staged` |
| — | Interactive RL oversight loop (opt-in addendum) | ✅ done | (in v0.5.0-staged) |
| M16–M17 | Passport + receipts + verifier SDK + risk feed | ✅ done | `v0.6.0-passport` |
| M25 | SPEC-9 Step 39 — archetype taxonomy + seed cores | ✅ done | — |
| M26 | SPEC-9 Steps 40–41 — classify + synthesize | ✅ done | — |
| M27 | SPEC-9 Step 42 — discrimination fit gate (the moat's proof) | ✅ done | — |
| M28 | SPEC-9 Steps 43–44 — library flywheel + `evaluate` one-call flow | ✅ done | — |
| M32 | SPEC-11 Step 50 — one token source of truth | ✅ done | — |
| M33 | SPEC-11 Step 51 — shared component library | ✅ done | — |
| M34 | SPEC-11 Step 52 — landing rebuilt as a real route | ✅ done | — |
| M35 | SPEC-11 Step 53 — production bar for the public surface | ✅ done | — |
| M36 | SPEC-12 Step 54 — attestation + ABOM + revocation | ✅ done | — |
| M37 | SPEC-12 Step 55 — MCP server certification | ✅ done | — |
| M38a | SPEC-12 Step 56 — tool certification (component tier) | ✅ done | — |
| M38b | SPEC-12 Step 57 — memory testing | ⬜ not started | — |
| M39 | SPEC-12 Step 58 — catalog conformance | ⬜ not started | — |
| M40 | SPEC-13 Step 62 — assertions on every trace | ✅ done | — |
| M41 | SPEC-13 Step 59 — coverage model | ✅ done | — |
| M42 | SPEC-13 Steps 60–61 — stimulus + CDV loop | ✅ done | — |
| M43 | SPEC-13 Step 63 — formal (authorization layer) | ✅ done | — |
| M44 | SPEC-13 Step 64 — sign-off + vPlan | ✅ done | — |

See [docs/SPEC9_RUBRIC_ENGINE.md](SPEC9_RUBRIC_ENGINE.md),
[docs/SPEC11_DESIGN_SYSTEM.md](SPEC11_DESIGN_SYSTEM.md) and
[docs/SPEC12_SUPPLY_CHAIN.md](SPEC12_SUPPLY_CHAIN.md) and
[docs/SPEC13_COVERAGE_DRIVEN.md](SPEC13_COVERAGE_DRIVEN.md) for the build records.

## Key entry points
- Profiles: `ascore profiles list|show` · `src/ascore/certification/profiles.py`
- Certify: `ascore certify` · `src/ascore/certification/certify.py`
- Dossiers: `ascore dossier verify|revoke|show` · `src/ascore/certification/dossier.py`
- Incidents: `ascore incidents …` · `src/ascore/live/incidents.py`
- Public verify: `GET /certification/{dossier_id}` · `src/ascore/server/routes/dossiers.py`
- Rubric engine: `agenttic evaluate <inputs>` · `src/agenttic/rubric_engine/` (classify → synthesize → discrimination gate → library)
