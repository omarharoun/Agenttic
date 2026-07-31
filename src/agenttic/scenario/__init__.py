"""Scenario — the world a scenario describes, instantiated and executable (P1).

Four modules, and the split is architectural:

* ``world`` — the domain store. Seeding (the first reader ``env_seed`` has ever
  had), snapshots, and the state diff. No policy, no gateway, no scoring.
* ``tools`` — the eight retail-support tools, each DECLARING ``mutating`` /
  ``irreversible`` as data rather than implying it in its spelling, plus the
  ``RETAIL_POLICY`` whose write-tool names match the tools that exist.
* ``faults`` — the fault plan (P4). What fails, on which call, and how: five
  kinds spelled exactly as the ``tool_condition`` bins, each producing evidence
  a reader can classify from the span rather than a label asserting it.
* ``env`` — :class:`~agenttic.scenario.env.ScenarioEnvironment`: evaluate at the
  enforcement gateway, then execute only if allowed — unless the plan stages a
  fault for that call — then stamp the result onto one span.

**One CLI path reaches this package, and the standard harness still does not.**
``agenttic cdv`` runs generated scenarios through this world (P5,
``scenario/runner.harness_executor``); ``agenttic run`` and every stored suite
still call ``adapter.run(tc.input)`` once per case and never come near it. So no
closure number on an existing SUITE run moves, and no coverpoint or bin is added.
What changes is what is *reachable*: ``action_risk`` had exactly one bin an
executable agent could hit, because the only tools the platform could run were a
calculator and a KB lookup, and the other three were reached solely by hand-built
``Span`` objects in the coverage tests. ``tool_condition`` was in the same
position and worse — five of its six bins could be *requested* and not one could
be *produced* — until P4's injector. Reachable and closed are different claims,
and this package makes only the first one.

Deliberately not here, so nobody reads them in: no simulated user (a confirmation
with no declared answer returns ``None``, never a fabricated ``True``), no
consumption of ``hidden_facts["data_condition"]``, and no reward computed from
``state_diff()``.

``injected_failures`` finally has an honest producer —
:attr:`ScenarioEnvironment.injected_failures
<agenttic.scenario.env.ScenarioEnvironment.injected_failures>`, which lists the
kinds that FIRED. Writing it back onto the realized scenario is the runner's
call, not this package's: the field is the scenario's record, and an environment
that reached into it would be making a claim about a run it cannot see the end
of.

Offline by construction: no network, no wall clock, no unseeded randomness.
"""

from agenttic.scenario.env import (  # noqa: F401
    EPOCH, SCENARIO_ENFORCEMENT_CFG, ScenarioEnvironment, ToolCall,
    install_scenario_enforcement,
)
from agenttic.scenario.faults import (  # noqa: F401
    FAULT_ATTR, FAULT_KINDS, FAULT_OBSERVABLE_ATTR, FaultPlan, FaultPlanError,
    FiredFault, PlannedFault, SkippedFault, apply_fault, plan_faults,
)
from agenttic.scenario.tools import (  # noqa: F401
    RETAIL_POLICY, RETAIL_TOOLS, RetailPolicy, ScenarioContext, ScenarioTool,
    tool_schemas,
)
from agenttic.scenario.world import (  # noqa: F401
    Customer, Order, RetailStore, seed_world,
)

__all__ = [
    "Customer", "Order", "RetailStore", "seed_world",
    "RETAIL_POLICY", "RETAIL_TOOLS", "RetailPolicy", "ScenarioContext",
    "ScenarioTool", "tool_schemas",
    "EPOCH", "SCENARIO_ENFORCEMENT_CFG", "ScenarioEnvironment", "ToolCall",
    "install_scenario_enforcement",
    "FAULT_ATTR", "FAULT_KINDS", "FAULT_OBSERVABLE_ATTR", "FaultPlan",
    "FaultPlanError", "FiredFault", "PlannedFault", "SkippedFault",
    "apply_fault", "plan_faults",
]
