"""The testing-ops orchestrator — many evaluators in, one honest report out.

Discovers registered evaluator adapters, gates them by license for the current
deployment, runs the allowed ones against the agent-under-test, and aggregates
their normalized :class:`~agenttic.schema.eval_result.EvalResult` rows into a
report that is honest by construction:

* **Provenance is kept.** Rows are grouped by dimension but never blended across
  sources: every dimension shows a *per-source* breakdown.
* **Wilson per source.** The existing stats core
  (:func:`~agenttic.stats.wilson_interval`) is applied PER (source, dimension)
  and per source — a rate never travels without its interval.
* **Coverage is explicit.** Every (dimension, source) cell is ``assessed`` or
  ``not_assessed``; an unavailable or gated-out source contributes
  ``not_assessed``, never an assumed pass.
* **No naked blended number.** The only way to obtain a cross-source Index is
  :meth:`AggregateReport.index_with_breakdown`, which returns it *together with*
  its per-source decomposition and coverage. There is no bare ``.index`` scalar.

.. note::
   This module lives under ``agenttic.evaluators`` (not ``agenttic.ops``) because
   ``agenttic.ops`` is already a heavily-imported module; a package would shadow
   it. Same role — the testing-ops product — co-located with the adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agenttic.evaluators.base import AgentTarget, Capabilities, EvaluatorAdapter
from agenttic.evaluators.license_gate import (
    DeploymentMode,
    GateDecision,
    evaluate_gate,
)
from agenttic.schema.eval_result import (
    DIMENSION_VOCAB_VERSION,
    EvalResult,
)
from agenttic.stats import wilson_interval

# --------------------------------------------------------------------------- #
# Discovery.
# --------------------------------------------------------------------------- #

_ENTRY_POINT_GROUP = "agenttic.evaluators"


def _builtin_adapters() -> list[EvaluatorAdapter]:
    from agenttic.evaluators.agenttic_gen import AgenttixGenAdapter
    from agenttic.evaluators.inspect_adapter import InspectAdapter

    return [AgenttixGenAdapter(), InspectAdapter()]


def discover_adapters(*, include_builtins: bool = True,
                      include_entry_points: bool = True
                      ) -> list[EvaluatorAdapter]:
    """Discover evaluator adapters: built-ins + ``agenttic.evaluators`` plugins.

    Third parties ship adapters as separate packages that register a factory (a
    zero-arg callable returning an adapter instance) under the
    ``agenttic.evaluators`` entry-point group. A broken plugin is skipped, never
    fatal — one bad third-party package can't take down the orchestrator.
    """
    adapters: list[EvaluatorAdapter] = []
    seen_ids: set[str] = set()
    if include_builtins:
        for a in _builtin_adapters():
            adapters.append(a)
            seen_ids.add(a.id)
    if include_entry_points:
        try:
            from importlib.metadata import entry_points
            eps = entry_points(group=_ENTRY_POINT_GROUP)
        except Exception:  # noqa: BLE001 - discovery must never crash a run
            eps = []
        for ep in eps:
            try:
                factory = ep.load()
                adapter = factory() if callable(factory) else factory
                if getattr(adapter, "id", None) in seen_ids:
                    continue
                adapters.append(adapter)
                seen_ids.add(adapter.id)
            except Exception:  # noqa: BLE001 - skip broken plugins
                continue
    return adapters


# --------------------------------------------------------------------------- #
# Aggregate structures.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DimensionStat:
    """One source's measurement of one dimension — with its Wilson interval."""

    source: str
    dimension: str
    status: str                 # "assessed" | "not_assessed"
    n_assessed: int             # pass + fail rows (error/skip excluded)
    n_pass: int
    n_fail: int
    n_error: int
    pass_rate: float | None     # point estimate over assessed rows, or None
    wilson_low: float | None
    wilson_high: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "dimension": self.dimension,
            "status": self.status, "n_assessed": self.n_assessed,
            "n_pass": self.n_pass, "n_fail": self.n_fail,
            "n_error": self.n_error, "pass_rate": self.pass_rate,
            "wilson_low": self.wilson_low, "wilson_high": self.wilson_high,
        }


@dataclass
class SourceReport:
    """Everything one evaluator contributed, self-contained (keeps provenance)."""

    source: str
    source_version: str
    source_license: str
    available: bool
    ran: bool                       # did it actually execute (allowed + available)?
    gate: GateDecision
    dimensions: dict[str, DimensionStat]  # per-dimension stat for this source
    n_results: int

    @property
    def assessed_dimensions(self) -> list[str]:
        return sorted(d for d, s in self.dimensions.items()
                      if s.status == "assessed")

    @property
    def source_index(self) -> float | None:
        """This source's index: the mean pass-rate across its assessed
        dimensions (equal weight). ``None`` if it assessed nothing — an
        un-assessed source has NO index, it does not default to 0 or 1."""
        rates = [s.pass_rate for s in self.dimensions.values()
                 if s.status == "assessed" and s.pass_rate is not None]
        return round(sum(rates) / len(rates), 4) if rates else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_version": self.source_version,
            "source_license": self.source_license,
            "available": self.available,
            "ran": self.ran,
            "gate": self.gate.to_dict(),
            "source_index": self.source_index,
            "assessed_dimensions": self.assessed_dimensions,
            "dimensions": {d: s.to_dict() for d, s in self.dimensions.items()},
            "n_results": self.n_results,
        }


@dataclass
class AggregateReport:
    """The union report: per-source + per-dimension breakdown, coverage, and a
    decomposable Index. Deliberately exposes NO bare blended scalar."""

    agent_id: str
    agent_version: str
    config_hash: str
    deployment_mode: DeploymentMode
    per_source: list[SourceReport]
    coverage: list[dict[str, Any]]         # {dimension, source, status}
    gate_decisions: list[GateDecision]
    results: list[EvalResult] = field(default_factory=list)
    dimension_vocab_version: str = DIMENSION_VOCAB_VERSION

    # -- the ONLY way to get a blended number: always with its breakdown ------ #
    def index_with_breakdown(self) -> tuple[float | None, dict[str, Any]]:
        """Return ``(overall_index, breakdown)``. The overall Index is the mean
        of the per-source indices (sources that assessed at least one dimension).
        You cannot get the number without the breakdown — that is the point."""
        per_source_index = {sr.source: sr.source_index for sr in self.per_source}
        scored = [v for v in per_source_index.values() if v is not None]
        overall = round(sum(scored) / len(scored), 4) if scored else None
        breakdown = {
            "per_source_index": per_source_index,
            "coverage_summary": self.coverage_summary(),
            "note": ("overall is a mean of per-source indices; it is meaningless "
                     "without the per-source breakdown and coverage shown here"),
        }
        return overall, breakdown

    def coverage_summary(self) -> dict[str, Any]:
        assessed = sum(1 for c in self.coverage if c["status"] == "assessed")
        total = len(self.coverage)
        by_source: dict[str, dict[str, int]] = {}
        for c in self.coverage:
            b = by_source.setdefault(c["source"], {"assessed": 0, "not_assessed": 0})
            b[c["status"]] += 1
        return {"assessed_cells": assessed, "total_cells": total,
                "by_source": by_source}

    def render_headline(self) -> str:
        """A one-line headline that ALWAYS decomposes to the per-source indices
        and coverage. Never a lone number."""
        overall, breakdown = self.index_with_breakdown()
        parts = []
        for src, idx in breakdown["per_source_index"].items():
            parts.append(f"{src}: {idx if idx is not None else 'not_assessed'}")
        cov = breakdown["coverage_summary"]
        head = "Union Safety Index"
        overall_s = "n/a" if overall is None else f"{overall}"
        return (f"{head}: {overall_s}  ["
                + " · ".join(parts)
                + f"]  coverage {cov['assessed_cells']}/{cov['total_cells']} "
                  "(dimension×source) assessed")

    def to_dict(self) -> dict[str, Any]:
        overall, breakdown = self.index_with_breakdown()
        return {
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "config_hash": self.config_hash,
            "deployment_mode": self.deployment_mode,
            "dimension_vocab_version": self.dimension_vocab_version,
            # A blended Index is present ONLY inside this decomposing structure.
            "index": {"overall": overall, **breakdown},
            "per_source": [sr.to_dict() for sr in self.per_source],
            "coverage": self.coverage,
            "gate_decisions": [g.to_dict() for g in self.gate_decisions],
            "n_results": len(self.results),
        }


# --------------------------------------------------------------------------- #
# The run.
# --------------------------------------------------------------------------- #


def _dimension_stat(source: str, dimension: str,
                    rows: list[EvalResult]) -> DimensionStat:
    n_pass = sum(1 for r in rows if r.outcome == "pass")
    n_fail = sum(1 for r in rows if r.outcome == "fail")
    n_error = sum(1 for r in rows if r.outcome == "error")
    n_assessed = n_pass + n_fail
    if n_assessed == 0:
        return DimensionStat(source, dimension, "not_assessed", 0, n_pass,
                             n_fail, n_error, None, None, None)
    low, high = wilson_interval(n_pass, n_assessed)
    return DimensionStat(
        source, dimension, "assessed", n_assessed, n_pass, n_fail, n_error,
        round(n_pass / n_assessed, 4), round(low, 4), round(high, 4))


def run_evaluation(
    target: AgentTarget,
    adapters: list[EvaluatorAdapter] | None = None,
    *,
    config: dict[str, Any] | None = None,
    deployment_mode: DeploymentMode = "self_hosted",
) -> AggregateReport:
    """Run every allowed evaluator against ``target`` and aggregate the union.

    ``adapters`` defaults to :func:`discover_adapters`. ``deployment_mode`` drives
    the license gate (``self_hosted`` relaxes; ``hosted`` refuses source-available
    / AGPL). A single adapter raising does NOT abort the run — it is recorded as a
    not-ran source (evaluator infra failure is data too).
    """
    cfg = config or {}
    adapters = adapters if adapters is not None else discover_adapters()

    per_source: list[SourceReport] = []
    coverage: list[dict[str, Any]] = []
    gate_decisions: list[GateDecision] = []
    all_results: list[EvalResult] = []

    for adapter in adapters:
        first_party = bool(getattr(adapter, "first_party", False))
        try:
            caps = adapter.capabilities()
        except Exception:  # noqa: BLE001 - a broken capabilities() is not fatal
            caps = Capabilities(available=False, dimensions=(), oracle="vendor",
                                unavailable_reason="capabilities() raised")

        gate = evaluate_gate(source=adapter.id,
                             source_license=getattr(adapter, "license", "unknown"),
                             first_party=first_party,
                             deployment_mode=deployment_mode)
        gate_decisions.append(gate)

        rows: list[EvalResult] = []
        ran = False
        if gate.allowed and caps.available:
            try:
                rows = adapter.run(target, cfg) or []
                ran = True
            except Exception:  # noqa: BLE001 - evaluator infra failure ≠ agent pass
                rows = []
                ran = False
        all_results.extend(rows)

        # Per-dimension stats for THIS source (provenance kept — never merged).
        rows_by_dim: dict[str, list[EvalResult]] = {}
        for r in rows:
            rows_by_dim.setdefault(r.dimension, []).append(r)

        # Coverage set = declared capability dimensions ∪ any dimension seen.
        cover_dims = set(caps.dimensions) | set(rows_by_dim)
        dim_stats: dict[str, DimensionStat] = {}
        for dim in sorted(cover_dims):
            stat = _dimension_stat(adapter.id, dim, rows_by_dim.get(dim, []))
            # A gated-out or unavailable source is not_assessed regardless.
            if not ran:
                stat = DimensionStat(adapter.id, dim, "not_assessed", 0, 0, 0, 0,
                                     None, None, None)
            dim_stats[dim] = stat
            coverage.append({"dimension": dim, "source": adapter.id,
                             "status": stat.status})

        per_source.append(SourceReport(
            source=adapter.id,
            source_version=getattr(adapter, "version", "unknown"),
            source_license=getattr(adapter, "license", "unknown"),
            available=caps.available,
            ran=ran,
            gate=gate,
            dimensions=dim_stats,
            n_results=len(rows),
        ))

    return AggregateReport(
        agent_id=target.agent_id,
        agent_version=target.agent_version,
        config_hash=target.config_hash,
        deployment_mode=deployment_mode,
        per_source=per_source,
        coverage=coverage,
        gate_decisions=gate_decisions,
        results=all_results,
    )
