# Attribution — Third-Party Data

## The 2025 AI Agent Index (CC BY 4.0)

Agenttic's agent-card **field taxonomy** and the **Catalog** of Index-imported
agents are derived from:

> **The 2025 AI Agent Index.** Zenodo. DOI: 10.5281/zenodo.19592546.
> https://zenodo.org/records/19592546 — licensed under
> [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

### How Agenttic uses it (honesty contract)
- The card field registry is **generated deterministically** from the dataset
  (`src/agenttic/cards/fields.py`) — never hand-transcribed.
- Imported agent cards carry `source = "index_import"`, **documented** provenance,
  and preserve their citations (dataset DOI + the agent's own website).
- Index-derived data is **Catalog-only**. It is **never** mixed into measured
  scores or score leaderboards (Hard Rule 17). Imported agents are explicitly
  excluded from `/api/leaderboard`.
- Every Index-imported card sets the CC BY attribution string, which is surfaced
  on the public card page and in card exports.

### Where attribution appears
- This file (`docs/ATTRIBUTION.md`), linked from the README.
- `data/vendor/ai-agent-index/LICENSE-NOTICE.md` (alongside the vendored files).
- Each imported card's `attribution` field → public card page + export metadata.

## Modifications
No substantive modifications are made to the underlying data on import; values are
copied verbatim into `documented` card fields with their citations preserved.
Empty/`N/A` values are represented as `none_found` (not fabricated).
