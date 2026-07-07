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

## Key entry points
- Profiles: `ascore profiles list|show` · `src/ascore/certification/profiles.py`
- Certify: `ascore certify` · `src/ascore/certification/certify.py`
- Dossiers: `ascore dossier verify|revoke|show` · `src/ascore/certification/dossier.py`
- Incidents: `ascore incidents …` · `src/ascore/live/incidents.py`
- Public verify: `GET /certification/{dossier_id}` · `src/ascore/server/routes/dossiers.py`
