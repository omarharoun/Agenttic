# Agenttic

Agent verification: generate a benchmark from a requirement, run an agent
against it, score it, and issue evidence someone else can check.

## Hard rules — never violate

- Never edit or delete tests to make them pass
- Never change scoring-engine behaviour or the Step 14 promotion gate
- Colours, type, spacing come from tokens only — no raw hex in components
- Shared components (ProvenanceBadge, ScoreValue, ScorecardCard) have
  exactly one implementation
- Never fabricate social proof, metrics, or figures
- Motion encodes state; no ornamental animation
- Stop and ask rather than weakening an acceptance criterion

## Verify

- Backend: `pytest -q`
- UI: `cd ui && npm run verify`
