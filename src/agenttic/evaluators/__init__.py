"""Evaluator Plugin Interface — many evaluators in, one signed passport out.

Public surface:

* :class:`~agenttic.evaluators.base.EvaluatorAdapter` — the Protocol every
  evaluator plugs into, plus :class:`~agenttic.evaluators.base.AgentTarget` and
  :class:`~agenttic.evaluators.base.Capabilities`.
* :class:`~agenttic.evaluators.orchestrator.AggregateReport` and
  :func:`~agenttic.evaluators.orchestrator.run_evaluation` — the testing-ops
  product.
* :func:`~agenttic.evaluators.passport.build_union_passport` — sign the union.

**Base-import hygiene.** Importing this package pulls in NO evaluator SDK.
``inspect_ai`` is imported only inside
:mod:`agenttic.evaluators.inspect_adapter`, itself behind a ``try/except
ImportError``. The names below are exposed lazily via ``__getattr__`` so that
``import agenttic.evaluators`` (and, transitively, ``import agenttic``) never
imports an evaluator dependency until an adapter is actually constructed.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AgentTarget",
    "Capabilities",
    "EvaluatorAdapter",
    "AgenttixGenAdapter",
    "InspectAdapter",
    "run_evaluation",
    "discover_adapters",
    "AggregateReport",
    "build_union_passport",
    "UnionPassport",
]

# name -> (submodule, attribute)
_LAZY: dict[str, tuple[str, str]] = {
    "AgentTarget": (".base", "AgentTarget"),
    "Capabilities": (".base", "Capabilities"),
    "EvaluatorAdapter": (".base", "EvaluatorAdapter"),
    "AgenttixGenAdapter": (".agenttic_gen", "AgenttixGenAdapter"),
    "InspectAdapter": (".inspect_adapter", "InspectAdapter"),
    "run_evaluation": (".orchestrator", "run_evaluation"),
    "discover_adapters": (".orchestrator", "discover_adapters"),
    "AggregateReport": (".orchestrator", "AggregateReport"),
    "build_union_passport": (".passport", "build_union_passport"),
    "UnionPassport": (".passport", "UnionPassport"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(target[0], __name__)
    return getattr(mod, target[1])


def __dir__() -> list[str]:
    return sorted(__all__)
