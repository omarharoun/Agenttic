# SPEC-9 — The Rubric Engine (build record)

The moat: a system that produces the right rubric for **any** agent automatically
and **proves it fits**, where "fits" is mechanical — the rubric *discriminates*
(separates good agents from bad). Per-client work shrinks toward a small audited
delta; the library compounds every engagement.

Package: `src/agenttic/rubric_engine/`. CLI: `agenttic evaluate <inputs>`.

## Steps → modules

| Step | Milestone | Module | What it does |
| --- | --- | --- | --- |
| 39 | M25 | `schema/archetype.py`, `rubric_engine/{cores,taxonomy,ethos}.py` | Archetype taxonomy: six authored cores (each a valid anchored `Rubric`), a tree with inheritance (`resolve_core`, child-wins recorded), and the ETHOS overlay. |
| 40 | M26 | `rubric_engine/classify.py` | Classify an agent into archetype(s) from a description (LLM, degrading offline to a keyword classifier over the same `signals`) + objective trace-shape features. Multi-archetype composes; below threshold → `custom`. |
| 41 | M26 | `rubric_engine/synthesize.py` | Synthesize the rubric as composed core + applicable ETHOS overlay + a small generated domain delta; emit `required_suite_features` and generate the **matched** suite (Hard Rule 41). |
| 42 | M27 | `rubric_engine/discrimination.py` | The fit gate: run a strong/weak/null reference panel at k≥4; require correct ranking + non-overlapping Wilson intervals on pass^k between the ends; flag panel-tying criteria non-discriminating. Deny-by-default. |
| 43 | M28 | `rubric_engine/library.py` | Four-source versioned library: authored cores, mined-from-engagements (human-gated), imported-benchmark exemplars, clustered novel archetypes. Provenance + discrimination track record + retire. |
| 44 | M28 | `rubric_engine/evaluate.py` + `agenttic evaluate` | One call: classify → synthesize → integrity gate → discrimination gate (auto-loop) → fit-verified draft awaiting approval → on approve, run. |

## Hard rules added (39–42)

39. No rubric ships without passing the discrimination gate (or an explicit,
    recorded human waiver). Enforced in `evaluate.approve_and_run`.
40. Every library criterion carries provenance + a live discrimination track
    record; criteria that stop discriminating are retired (`retire_candidates`).
41. Rubric and suite are a matched pair — `synthesize_suite` scaffolds a case for
    every required feature the generator didn't cover.
42. Archetype cores are versioned IP; mined additions are human-gated into cores,
    never auto-merged (`RubricLibrary.approve`).

## Honest deviations from the spec's stated prerequisites

The spec references three things that do **not** exist in-tree; each was
bootstrapped minimally and is flagged in the relevant module docstring:

- **The ETHOS pack** — no standalone pack exists. `rubric_engine/ethos.py`
  bootstraps a real overlay from the platform's proven safety `check_ref`s
  (harm-refusal, secret/PII leak, injection, plus gated fairness/escalation) with
  a maqāṣid-style severity, rather than inventing unscored criteria.
- **The Step-13 miner** — no miner exists. `library.propose_from_engagement`
  implements the "miner pointed at rubrics": it proposes the discriminating,
  stable delta criteria of an engagement into the core, human-gated.
- **A Terminal-bench importer** — absent. The existing `DatasetAdapter`
  importers (τ / BFCL / AgentHarm / SWE-bench / …) register as exemplars via
  `library.register_exemplar`.

## Tests (46, all offline)

`tests/test_archetype_taxonomy.py` (9) · `test_classify.py` (8) ·
`test_synthesize.py` (7) · `test_discrimination.py` (9, incl. a real strong/weak/null
panel run through the scoring engine on a code-only rubric) · `test_rubric_library.py`
(7) · `test_evaluate_flow.py` (6, incl. inputs → approved → scored end to end).

LLM-dependent stages (classify's semantic read, synthesize's domain delta) follow
the codebase convention: an injectable `client` / `generator` that defaults to real
Anthropic but degrades to a deterministic fallback, so the whole engine — and its
acceptance tests — run with no API key.
