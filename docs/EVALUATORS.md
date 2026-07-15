# Evaluator Plugin Interface

*Many evaluators in → one honest, signed, verifiable passport out.*

A single Agenttic passport can attest to the **union** of several evaluator
sources — Agenttic's own red-team generator, AISI's Inspect, and third-party
adapters shipped as separate packages — without blending their scores into a
dishonest single number. Every result carries its own provenance; every
dimension decomposes per source; every un-run dimension is `not_assessed`, never
an assumed pass.

## The one interface

Every evaluator implements the `EvaluatorAdapter` Protocol
(`agenttic/evaluators/base.py`):

```python
class EvaluatorAdapter(Protocol):
    id: str          # EvalResult.source
    version: str     # EvalResult.source_version
    license: str     # EvalResult.source_license (SPDX)
    def capabilities(self) -> Capabilities: ...
    def run(self, target: AgentTarget, config) -> list[EvalResult]: ...
```

`run()` **must not raise** on a single failing case: an agent mistake, a probe
that blows up, or an evaluator hiccup on one item becomes an
`EvalResult(outcome="error")` and the run continues. A crash loses data; an
error row keeps it.

`AgentTarget` reuses the existing `AgentAdapter` (how to run the agent and get a
`Trace`) plus the red-team `AgentDescriptor` (the agent's attack surface).
`AgentTarget.reference()` builds the built-in reference agent wired to a scripted
no-API-key client, so the whole pipeline runs offline.

## The normalized result

`agenttic/schema/eval_result.py` — a frozen `EvalResult`:

| group | fields |
|---|---|
| provenance (never optional) | `source`, `source_version`, `source_license` (SPDX) |
| what was tested | `dimension` (controlled vocab), `test_id`, `probe` |
| verdict | `outcome` ∈ `pass\|fail\|error\|not_assessed`, `score` (0..1 or `None`), `raw` (the evaluator's **original** output, verbatim) |
| evidence | `trace_ref`, `rationale`, `oracle` ∈ `deterministic\|judge\|vendor` |

## The controlled dimension vocabulary (Agenttic owns it)

Defined once, in `eval_result.py` (`DIMENSION_VOCAB_VERSION`):

| dimension | meaning |
|---|---|
| `injection_robustness` | resists prompt-injection / instruction hijack |
| `harmful_refusal` | refuses harmful/disallowed actions |
| `tool_safety` | no dangerous/forbidden/unauthorized tool use |
| `secret_disclosure` | does not leak secrets/credentials it holds |
| `faithfulness` | grounded in context/tools; no fabrication |

Each adapter maps its native categories onto this vocabulary through a
**versioned** table living in the adapter, keeping the native value in `raw`.

### Mapping tables

`agenttic-gen` (`MAPPING_VERSION = agenttic-gen-map/v1`):

| native criterion | dimension |
|---|---|
| `injection_robust` | `injection_robustness` |
| `no_secret_leak` | `secret_disclosure` |
| `tool_misuse_safety` | `tool_safety` |

`inspect_ai` (`INSPECT_MAP_VERSION = inspect-map/v1`):

| native category | dimension |
|---|---|
| `harmful_behavior`, `refusal`, `harmbench` | `harmful_refusal` |
| `faithfulness`, `hallucination`, `groundedness` | `faithfulness` |

The generator and Inspect cover **disjoint** dimensions — which is exactly why a
multi-evaluator passport is worth more than either alone.

## Orchestrator

`agenttic/evaluators/orchestrator.py` → `run_evaluation(target, adapters, *,
deployment_mode)` returns an `AggregateReport`:

- per **source** × **dimension** stats with a Wilson 95% interval (reusing
  `agenttic.stats.wilson_interval`) — a rate never travels without its interval;
- a **coverage** table: every (dimension, source) cell is `assessed` or
  `not_assessed`;
- **no naked blended number**: the only way to get a cross-source Index is
  `report.index_with_breakdown()`, which returns it *with* the per-source
  decomposition and coverage. There is no bare `.index` scalar.

## Signed union passport

`build_union_passport(report)` builds a certificate payload whose **signed
bytes** include the pinned `config_hash` and `agent_version`, every
`source`+`source_version`+`source_license`, the coverage table, the license
attribution table, the per-source index decomposition, and the license-gate
decisions. It reuses the existing Ed25519 signing path verbatim
(`certification/safety_cert.py`), so the signing key/kid is unchanged and
**older single-source dossiers keep verifying**.

## License gate

`agenttic/evaluators/license_gate.py`. First-party sources always run. Permissive
(MIT/Apache-2.0/BSD/ISC) runs free everywhere. Source-available (Elastic-2.0 /
SSPL / BSL) and network-copyleft (AGPL) are **refused in a `hosted`
deployment** and allowed (relaxed) when `self_hosted`. Unknown licenses fail
closed when hosted. Every decision is recorded and travels into the dossier.

## Registration

Built-ins are wired in code. Third parties ship an adapter as a separate
distribution and register a zero-arg factory under the `agenttic.evaluators`
entry-point group; `discover_adapters()` picks them up (de-duplicated by `id`).
Inspect support is the optional `agenttic[inspect]` extra — the base install
imports no `inspect_ai`.

See `examples/evaluator_union_passport.py` for a full offline run.
