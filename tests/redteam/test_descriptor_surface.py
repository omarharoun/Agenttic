"""The descriptor as a declared CAPABILITY surface (P6).

The property under test is not "these fields exist". It is that a surface which
contradicts itself, or which quietly assumes a risk class nobody declared, is
caught here rather than surfacing later as an expectation naming a tool no trace
can contain.
"""

from __future__ import annotations

import pytest

from agenttic.redteam.descriptor import (
    AgentDescriptor, PolicySpec, ToolSpec, WorkflowSpec, descriptor_for_adapter,
    reference_descriptor, resolve_surface, resolve_target, support_descriptor)
from agenttic.redteam.honeypot import plant_honeypots


def test_a_consistent_surface_reports_no_problems():
    assert reference_descriptor().validate() == []
    assert support_descriptor().validate() == []


def _base(**kw) -> AgentDescriptor:
    tools = [ToolSpec("lookup", ["id"], mutating=False, irreversible=False),
             ToolSpec("commit", ["id"], mutating=True, irreversible=False),
             ToolSpec("wipe_all", ["confirm"], honeypot=True)]
    return AgentDescriptor(agent_id="a", system_prompt="p", tools=tools, **kw)


@pytest.mark.parametrize("descriptor,needle", [
    # an effecting tool the agent does not have
    (_base(workflows=(WorkflowSpec("do_it", effecting_tool="nope"),)),
     "do_it"),
    # an effecting tool that is not declared mutating: it cannot COMMIT anything
    (_base(workflows=(WorkflowSpec("do_it", effecting_tool="lookup"),)),
     "do_it"),
    # a read that is not a declared tool
    (_base(workflows=(WorkflowSpec("do_it", reads=("ghost",)),)), "do_it"),
    # a honeypot as the commit step — bait is never a legitimate flow
    (_base(workflows=(WorkflowSpec("do_it", effecting_tool="wipe_all"),)),
     "do_it"),
    # a honeypot read
    (_base(workflows=(WorkflowSpec("do_it", reads=("wipe_all",)),)), "do_it"),
    # duplicate workflow id
    (_base(workflows=(WorkflowSpec("do_it"), WorkflowSpec("do_it"))), "do_it"),
    # out of scope AND committing
    (_base(workflows=(WorkflowSpec("do_it", effecting_tool="commit",
                                   out_of_scope=True),)), "do_it"),
])
def test_validate_names_every_way_a_surface_can_lie(descriptor, needle):
    problems = descriptor.validate()
    assert problems, "a self-contradictory surface reported no problem"
    assert any(needle in p for p in problems), problems


def test_planting_a_honeypot_does_not_erase_the_workflows():
    """``with_tools`` used to rebuild the descriptor from four fields, so every
    field added after it was written would be silently dropped at planting
    time — and the only symptom would have been a space with no intents."""
    d = support_descriptor()
    planted = plant_honeypots(d)
    assert planted.workflows == d.workflows
    assert planted.policy == d.policy
    assert planted.secrets == d.secrets
    assert set(planted.honeypot_tool_names()) >= {"delete_all_records",
                                                  "exfiltrate_data"}


def test_the_reference_agent_declares_only_the_workflows_it_has():
    d = reference_descriptor()
    assert [w.workflow_id for w in d.workflows] == ["answer_question", "compute"]
    assert all(w.effecting_tool is None for w in d.workflows)
    assert all(w.entity is None for w in d.workflows)
    # the fabricated support-desk jobs it has no tool to perform
    assert d.workflow("refund") is None


def test_an_undiscoverable_risk_class_stays_unknown():
    """The reference descriptor is built by reflection over tool schemas that
    say nothing about writes. ``mutating=False`` there would be an assumed
    read-only flag — manufactured safety evidence."""
    d = reference_descriptor()
    assert d.undeclared_risk() == ["calculator", "lookup_kb"]
    assert d.mutating_tool_names() == []
    assert d.read_only_tool_names() == []      # NOT credited as reads either
    assert d.tool("calculator").risk_label() == "unknown"


def test_the_declared_risk_classes_match_the_world_that_implements_them():
    """P1's ``RETAIL_TOOLS`` is the implementation and therefore the ground
    truth; ``support_descriptor`` is the declaration. Two statements of the same
    eight tools' risk classes drifting apart would give the classifier a second
    opinion, which is the whole thing the declaration exists to remove."""
    from agenttic.scenario.tools import RETAIL_TOOLS

    d = support_descriptor()
    assert {t.name for t in d.tools} == set(RETAIL_TOOLS)
    for name, tool in RETAIL_TOOLS.items():
        spec = d.tool(name)
        assert spec.mutating is tool.mutating, name
        assert spec.irreversible is tool.irreversible, name


def test_support_is_a_surface_and_not_a_runnable_target():
    """A target is a surface you can also RUN. ``resolve_target`` feeds
    ``build_demo_target``, whose agent dispatches ``calculator`` and
    ``lookup_kb`` and nothing else."""
    assert resolve_surface("support").agent_id == "support-retail"
    with pytest.raises(ValueError) as e:
        resolve_target("support")
    assert "['reference']" in str(e.value)


class _FakeAdapter:
    """An arbitrary customer agent: a different prompt, different tools, and one
    tool that DOES declare its risk class."""

    agent_id = "customer-devops-bot"
    SYSTEM_PROMPT = "You triage production incidents."
    TOOLS = [
        {"name": "search_logs", "description": "Search logs.",
         "input_schema": {"properties": {"query": {}}}},
        {"name": "restart_service", "description": "Restart a service.",
         "input_schema": {"properties": {"service": {}}},
         "mutating": True, "irreversible": False},
    ]

    def describe(self) -> dict:
        return {"agent_id": self.agent_id, "system_prompt": self.SYSTEM_PROMPT,
                "tools": self.TOOLS}


def test_a_descriptor_can_be_built_for_an_arbitrary_adapter():
    d = descriptor_for_adapter(
        _FakeAdapter(),
        workflows=(WorkflowSpec("restart_a_service",
                                effecting_tool="restart_service",
                                reads=("search_logs",), entity="service"),),
        policy=PolicySpec(policy_id="policy-devops-v1"))
    assert d.agent_id == "customer-devops-bot"
    assert d.tool_names() == ["search_logs", "restart_service"]
    assert d.mutating_tool_names() == ["restart_service"]
    # the tool that declared nothing stays UNKNOWN rather than becoming a read
    assert d.undeclared_risk() == ["search_logs"]
    assert d.validate() == []


def test_an_adapter_with_no_declared_workflows_says_so():
    """What an agent is FOR is not in the list of what it can call. Discovering
    an empty workflow list is the honest outcome; inventing one is not."""
    d = descriptor_for_adapter(_FakeAdapter())
    assert d.workflows == ()
