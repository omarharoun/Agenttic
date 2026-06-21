"""Canonical, literature-anchored benchmarking metrics.

These are *standard* scorers — distinct from the LLM-generated rubric criteria —
that implement published agent-evaluation methodologies on agenttic's own seed
data. Each metric names the methodology it implements (see ``catalog.py``); we
implement the *methodology*, we do not reproduce a specific paper's numbers.
Adopting the actual public datasets (BFCL / tau-bench / AgentHarm / AgentDojo
cases) for direct comparability is an explicit NEXT phase.

Layers:
- ``canonical_checks``: deterministic per-trace checks (registered into the
  scoring CHECKS registry, so standard suites score through the normal pipeline).
- ``reliability`` / ``calibration`` / ``faithfulness``: run-level metrics.
- ``catalog``: the named metric definitions + Agenttic Index weighting.
- ``index``: roll component metric values into one normalized index.
- ``standard_suites``: the canonical tool-use + safety seed suites.
"""
