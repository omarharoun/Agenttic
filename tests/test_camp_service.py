"""Service-layer + adapter-bridge tests: the camp orchestration, the gate
evaluation dict, and running a *real* Agenttic adapter under camp (via a fake
adapter, so no network) — including the honesty rule that an unparseable agent
reply is graded as a miss rather than excused."""

import json

from agenttic.adapters.base import AgentAdapter
from agenttic.camp import service
from agenttic.camp.adapter_agent import AdapterAgent, parse_action
from agenttic.camp.agent import HeuristicSupportAgent
from agenttic.schema.trace import Trace


def _trace(final_output: str) -> Trace:
    return Trace(
        trace_id="tr", agent_id="fake", agent_config_hash="x" * 16,
        spans=[], visibility="black_box", final_output=final_output)


class HeuristicAdapter(AgentAdapter):
    """A fake adapter that reproduces the baseline heuristic through the real
    adapter interface — proves the bridge drives an agent end to end."""

    agent_id = "heuristic-adapter"
    visibility = "black_box"

    def __init__(self):
        self._inner = HeuristicSupportAgent()

    def describe(self) -> dict:
        return {"kind": "heuristic-adapter"}

    def run(self, test_input: dict, *, test_case_id=None) -> Trace:
        action = self._inner.act(test_input)
        return _trace(json.dumps(action))


class GarbageAdapter(AgentAdapter):
    agent_id = "garbage"
    visibility = "black_box"

    def describe(self) -> dict:
        return {"kind": "garbage"}

    def run(self, test_input: dict, *, test_case_id=None) -> Trace:
        return _trace("I cannot answer that as JSON, sorry!")


def test_parse_action_handles_fences_and_prose():
    assert parse_action('{"action": "x"}') == {"action": "x"}
    assert parse_action('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_action("here you go: {\"a\": 2} done") == {"a": 2}
    assert parse_action("no json here") == {}
    assert parse_action("") == {}


def test_adapter_agent_bridges_to_real_adapter():
    obs = {"system": "sys", "message": "I was charged twice, please refund one."}
    action = AdapterAgent(HeuristicAdapter()).act(obs)
    assert action.get("category") == "billing"


def test_agent_mode_matches_mock_baseline_under_same_seed():
    # Same task + seed: driving the baseline via the adapter bridge must produce
    # the exact same graded outcome as the built-in mock agent.
    mock = service.run_single_camp(mode="mock", episodes=200, seed=3,
                                   threshold=0.8)
    byo = service.run_single_camp(mode="agent", episodes=200, seed=3,
                                  threshold=0.8, adapter=HeuristicAdapter())
    assert mock["report"]["passes"] == byo["report"]["passes"]
    assert byo["report"]["episodes"] == 200


def test_garbage_agent_is_graded_as_failure_not_excused():
    res = service.run_single_camp(mode="agent", episodes=50, seed=1,
                                  adapter=GarbageAdapter(), threshold=0.8)
    # Nothing parseable => zero passes, honest floor not met.
    assert res["report"]["passes"] == 0
    assert res["gate"]["floor_met"] is False


def test_gate_dict_floor_non_overridable():
    # 98% run, human "approves" -> still blocked because the floor isn't met.
    res = service.run_single_camp(mode="mock", episodes=500, seed=0,
                                  threshold=0.99)
    approved = service.evaluate_gate(res["report_obj"], approved_by="op@x.com")
    assert approved["human_approved"] is True
    assert approved["floor_met"] is False
    assert approved["promoted"] is False


def test_single_camp_promotes_when_floor_cleared_and_approved():
    res = service.run_single_camp(mode="mock", episodes=800, seed=0,
                                  threshold=0.70)
    assert res["report"]["meets_floor"] is True
    approved = service.evaluate_gate(res["report_obj"], approved_by="op@x.com")
    assert approved["promoted"] is True


def test_improve_service_returns_ratchet_and_review_queue():
    res = service.run_improve_camp(rounds=5, episodes_per_round=300,
                                   threshold=0.95, holdout=400, seed=1,
                                   approved_by="op@x.com")
    assert res["report"]["final_champion_gen"] >= 1
    assert len(res["rounds"]) >= 1
    # review queue is the champion's remaining holdout failures (may be empty if
    # it cleared everything, but the structure must be present)
    assert isinstance(res["review_queue"], list)


def test_improve_degenerate_does_not_promote():
    res = service.run_improve_camp(rounds=6, episodes_per_round=300,
                                   threshold=0.99, holdout=400, seed=1,
                                   degenerate=True, approved_by="op@x.com")
    assert res["gate"]["promoted"] is False
    assert "stall" in res["report"]["halted_reason"] or \
           "escalate" in res["report"]["halted_reason"]
