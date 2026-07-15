"""Evaluator Plugin Interface — the one Protocol every evaluator plugs into.

The thesis: *many evaluators in → one honest, signed, verifiable passport out.*
That only works if every evaluator, whatever its native shape, speaks a single
interface. This module defines it:

* :class:`AgentTarget` — the agent-under-test handle. It reuses the existing
  :class:`~agenttic.adapters.base.AgentAdapter` (so "any agent, any framework"
  keeps working) plus the red-team :class:`~agenttic.redteam.descriptor.AgentDescriptor`
  (the agent's attack surface: tools, prompt, declared secrets).
* :class:`Capabilities` — what an evaluator can (and cannot) assess right now,
  reported *before* it runs, so the orchestrator can stamp un-run dimensions
  ``not_assessed`` instead of silently assuming a pass.
* :class:`EvaluatorAdapter` — the Protocol: stable ``id`` / ``version`` /
  ``license`` (SPDX), :meth:`capabilities`, and :meth:`run`.

**Arm's length rule.** Evaluators are dependencies we *call* (their API/CLI),
never vendor. The base install imports no evaluator SDK; each concrete adapter
imports its evaluator lazily (behind ``try/except ImportError`` where the
evaluator is an optional extra).

**No-crash rule (Hard Rule 5, extended).** ``run`` MUST NOT raise on a single
failing case. An agent mistake, a probe that blows up, an evaluator hiccup on
one item — each becomes an ``EvalResult(outcome="error")`` and the run
continues. A crash loses data; an error row keeps it.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agenttic.adapters.base import AgentAdapter
from agenttic.redteam.descriptor import AgentDescriptor
from agenttic.schema.eval_result import EvalResult, Oracle


@dataclass
class AgentTarget:
    """The agent under test, as an evaluator sees it.

    Bundles the existing driver (:class:`AgentAdapter` — how to *run* the agent
    and get a Trace) with the red-team :class:`AgentDescriptor` (the agent's
    declared attack surface — tools, system prompt, secrets). An evaluator that
    only needs to run the agent uses ``adapter``; one that needs to author
    attacks against the real surface uses ``descriptor``.
    """

    adapter: AgentAdapter
    descriptor: AgentDescriptor
    #: A human/semver label for the agent build. Distinct from ``config_hash``
    #: (which is the cryptographic pin) — this is what a person reads on a badge.
    agent_version: str = "unknown"

    @property
    def agent_id(self) -> str:
        return self.descriptor.agent_id

    @property
    def config_hash(self) -> str:
        """The exact-agent-version pin, from the adapter's own config hash."""
        return self.adapter.config_hash()

    @classmethod
    def reference(cls, *, kb_path: str | None = None,
                  agent_version: str = "reference-1.0.0") -> "AgentTarget":
        """The built-in reference agent wired to a scripted, no-API-key client.

        Reuses :func:`~agenttic.redteam.descriptor.reference_descriptor` and
        :func:`~agenttic.redteam.demo_target.build_demo_target`, so the whole
        end-to-end example runs offline with no credentials. In production you
        would build an :class:`AgentTarget` around a *real* adapter + descriptor
        instead.
        """
        from agenttic.redteam.demo_target import build_demo_target
        from agenttic.redteam.descriptor import reference_descriptor

        descriptor = reference_descriptor()
        if kb_path is None:
            # A tiny KB so the agent's lookup_kb tool has something to read. Kept
            # for the process lifetime (the reference target is a demo helper).
            tmp = Path(tempfile.mkdtemp(prefix="agenttic-ref-kb-"))
            kb = tmp / "kb.json"
            kb.write_text(json.dumps({"refund_policy": "30 days",
                                      "support_email": "help@example.com"}))
            kb_path = str(kb)
        adapter = build_demo_target(descriptor, kb_path=kb_path)
        return cls(adapter=adapter, descriptor=descriptor,
                   agent_version=agent_version)


@dataclass(frozen=True)
class Capabilities:
    """What an evaluator can assess, declared before it runs.

    ``available`` False means the evaluator cannot run here (SDK missing, no
    credential, license-gated out). The orchestrator then stamps every dimension
    in ``dimensions`` as ``not_assessed`` for this source — never an assumed
    pass. ``dimensions`` is the controlled-vocab set this evaluator *would*
    assess when available.
    """

    available: bool
    dimensions: tuple[str, ...]          # controlled Dimension ids it covers
    oracle: Oracle                       # the dominant oracle kind it uses
    requires_network: bool = False       # does a real run make network calls?
    unavailable_reason: str | None = None  # why available is False (if so)
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "dimensions": list(self.dimensions),
            "oracle": self.oracle,
            "requires_network": self.requires_network,
            "unavailable_reason": self.unavailable_reason,
            "notes": self.notes,
        }


@runtime_checkable
class EvaluatorAdapter(Protocol):
    """The single interface every evaluator plugs into.

    Concrete adapters set ``id`` / ``version`` / ``license`` as instance
    attributes (or class attributes) and implement :meth:`capabilities` and
    :meth:`run`. Third parties ship adapters as separate packages registered
    under the ``agenttic.evaluators`` entry-point group.
    """

    id: str          # stable evaluator id (the EvalResult.source value)
    version: str     # evaluator build/version (EvalResult.source_version)
    license: str     # SPDX id for the evaluator (EvalResult.source_license)

    def capabilities(self) -> Capabilities:
        """Report, without running, what this evaluator can assess right now."""
        ...

    def run(self, target: AgentTarget,
            config: dict[str, Any] | None = None) -> list[EvalResult]:
        """Assess ``target`` and return normalized rows.

        MUST NOT raise on a single failing case: a case that errors becomes an
        ``EvalResult(outcome="error")``. Returning ``[]`` is legitimate (e.g. the
        evaluator is unavailable) — the orchestrator handles coverage from
        :meth:`capabilities`.
        """
        ...
