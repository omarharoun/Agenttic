"""Standing up the world twice for one agent must work.

Every caller this environment exists for runs MANY scenarios against ONE agent:
``metrics/runner.run_standard`` repeats a whole suite k times for pass^k, and
``verification/cdv.run_until_closure`` realizes a fresh scenario per sample for
hundreds of samples per round. So "install the enforcement session" is not a
once-per-agent setup step, it is a per-scenario one.

``Registry.save_policy`` is append-only and refuses a duplicate ``policy_id``
outright (sqlite_store.py:1758). Keying the policy id on the agent alone
therefore made the SECOND call raise ``DuplicateVersionError`` — the environment
worked exactly once per agent per process and then died, which no unit test
caught because each one built its own registry.

The ruleset digest in the id closes both halves, and both halves are pinned here:
the same rules must be reusable, and a DIFFERENT ruleset must not silently be
served the first policy. That second half is the one with teeth — a scenario that
installs a deny rule being handed a previous scenario's permissive policy would
make the gateway look like it was enforcing while it was not.
"""

from __future__ import annotations

import pytest

from agenttic.scenario import ScenarioEnvironment, install_scenario_enforcement
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.enforcement import Rule
from agenttic.stimulus.realize import realize
from agenttic.stimulus.spaces.conversational_transactional import seed_space


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "scenario.db")


@pytest.fixture
def scenario():
    return realize({"intent": "refund", "emotional_register": "neutral",
                    "data_condition": "complete", "policy_vector": "compliant",
                    "tool_condition": "all_ok"},
                   seed=11, space=seed_space())


def _deny_refunds() -> Rule:
    """A real Lane-1 deny, in the shape ``enforce/lanes.py:_matches`` reads.

    The field is ``matcher`` and the tool key is ``tool`` — ``Rule`` ignores
    unknown kwargs, so a plausible-looking ``match={"tool_name": ...}``
    constructs cleanly, matches nothing, and produces a rule that silently
    permits everything. Worth naming, because a policy that looks installed and
    enforces nothing is the exact failure this whole layer exists to detect.
    """
    return Rule(rule_id="no-refunds", lane="lane1", action="deny",
                matcher={"tool": "issue_refund"})


class TestTheSameAgentCanRunManyScenarios:
    def test_installing_twice_for_one_agent_does_not_raise(self, reg):
        install_scenario_enforcement(reg, "agent-1")
        install_scenario_enforcement(reg, "agent-1")   # the call that used to die

    def test_the_second_session_is_a_working_session(self, reg, scenario):
        """Not raising is not enough — the reused policy has to still serve.

        The gateway re-verifies the policy content hash on every
        ``start_session`` (gateway.py:94), so a reuse path that returned a stale
        or rebuilt object would fail here rather than silently degrade.
        """
        install_scenario_enforcement(reg, "agent-1")
        gateway, session = install_scenario_enforcement(reg, "agent-1")

        env = ScenarioEnvironment(scenario, gateway=gateway,
                                  session_id=session.session_id)
        call = env.call("lookup_order",
                        {"order_id": scenario.env_seed["order_id"]})
        assert call.error is None
        assert call.decision is not None

    def test_k_repeats_of_one_scenario_all_stand_up(self, reg, scenario):
        """The pass^k shape: the same agent, the same rules, k times over."""
        for _ in range(5):
            gateway, session = install_scenario_enforcement(reg, "agent-k")
            env = ScenarioEnvironment(scenario, gateway=gateway,
                                      session_id=session.session_id)
            assert env.call("lookup_order",
                            {"order_id": scenario.env_seed["order_id"]}).error is None


class TestADifferentRulesetIsADifferentPolicy:
    def test_a_deny_rule_is_not_served_the_permissive_policy(self, reg, scenario):
        """The half that matters. Install permissive first, then restrictive.

        If the restrictive install were handed the stored permissive policy, the
        refund would go through and the gateway would look like it was enforcing
        a rule it had never loaded — enforcement theatre, which is precisely the
        distinction ``redteam/honeypot.py`` exists to draw.
        """
        install_scenario_enforcement(reg, "agent-2")            # permissive
        gateway, session = install_scenario_enforcement(
            reg, "agent-2", rules=[_deny_refunds()])            # restrictive

        env = ScenarioEnvironment(scenario, gateway=gateway,
                                  session_id=session.session_id)
        oid = scenario.env_seed["order_id"]
        env.call("lookup_order", {"order_id": oid})
        blocked = env.call("issue_refund", {"order_id": oid, "amount": 10.0})

        assert blocked.error is not None
        assert "BLOCKED_BY_HARNESS" in blocked.error
        # and the world is untouched — the gateway ruled BEFORE the tool ran
        assert env.state_diff() == {}

    def test_the_permissive_policy_still_permits_afterwards(self, reg, scenario):
        """The reverse order, so neither install can be poisoning the other."""
        install_scenario_enforcement(reg, "agent-3", rules=[_deny_refunds()])
        gateway, session = install_scenario_enforcement(reg, "agent-3")

        env = ScenarioEnvironment(scenario, gateway=gateway,
                                  session_id=session.session_id)
        oid = scenario.env_seed["order_id"]
        env.call("lookup_order", {"order_id": oid})
        allowed = env.call("issue_refund", {"order_id": oid, "amount": 10.0})

        assert allowed.error is None
        assert env.state_diff() != {}
