"""Prompt-optimizer endpoints over HTTP: start a run (cost-warned), poll to
completion, and read the result with train/heldout scores. The agent + judge are
the same injected fakes as the rest of the API suite; the optimizer LLM is an
injected fake that returns a JSON candidate, so a full reflect→propose→evaluate
cycle runs end to end without real spend."""

import json
import time
import threading
import uuid
from types import SimpleNamespace as NS

import pytest

from tests.test_api import client as _api_client  # noqa: F401 (app+fakes fixture)


class OptimizerFakeClient:
    """A fake Anthropic client whose messages.create returns a JSON array of one
    candidate prompt — what the optimizer expects to parse."""
    def __init__(self):
        self.messages = NS(create=self._create)
        self._lock = threading.Lock()

    def _create(self, **kw):
        with self._lock:
            payload = json.dumps([
                {"prompt": "Always be concise and route precisely.",
                 "rationale": "targets the failing criterion"}])
            return NS(stop_reason="end_turn",
                      usage=NS(input_tokens=300, output_tokens=60),
                      content=[NS(type="text", text=payload)])


@pytest.fixture
def client(_api_client):
    """The shared API client, but with an 'optimizer' fake added to the app's
    injected clients so candidate proposal doesn't need a real key."""
    _api_client.app.state.clients["optimizer"] = OptimizerFakeClient()
    return _api_client


def _poll(client, run_id, timeout=25.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/optimize/runs/{run_id}").json()
        if last["status"] in ("succeeded", "failed"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"timeout; last={last}")


def _start(client, **over):
    body = {"agent_id": "router", "suite_id": "pilot-support-triage",
            "rounds": 1, "candidates_per_round": 1, "heldout_fraction": 0.3,
            "baseline_prompt": "Route the ticket."}
    body.update(over)
    return client.post("/api/optimize/runs", json=body)


class TestOptimizeApi:
    def test_start_warns_cost_and_runs_to_result(self, client):
        r = _start(client)
        assert r.status_code == 200, r.text
        body = r.json()
        # cost is surfaced up front (projected suite executions)
        assert body["projected_agent_runs"] > 0
        run = _poll(client, body["run_id"])
        assert run["status"] == "succeeded", run
        art = run["run"]
        # train + heldout reported (the overfitting guard) over a real split
        assert art["n_train"] >= 1 and art["n_heldout"] >= 1
        assert art["baseline_train_rate"] is not None
        assert art["baseline_heldout_rate"] is not None
        assert set(art["train_test_ids"]) & set(art["heldout_test_ids"]) == set()
        # at least one candidate was proposed + evaluated this round
        assert art["rounds"] and art["rounds"][0]["candidates"]
        assert art["methodology"].startswith("OPRO")

    def test_list_includes_run(self, client):
        run_id = _start(client).json()["run_id"]
        _poll(client, run_id)
        runs = client.get("/api/optimize/runs").json()["runs"]
        assert any(r["run_id"] == run_id and r["status"] == "succeeded"
                   for r in runs)

    def test_unknown_suite_404(self, client):
        assert _start(client, suite_id="ghost").status_code == 404

    def test_unapproved_suite_400(self, client):
        # seed a DRAFT suite and refuse to optimize against it
        from ascore.schema.testcase import TestCase, TestSuite
        sid = f"draft-{uuid.uuid4().hex[:6]}"
        cases = [TestCase(test_id="d-0", suite_id=sid, task_description="t",
                          input={"ticket": "refund please"}, rubric_id="r-triage")]
        client.reg.save_suite(TestSuite(suite_id=sid, business_context="x",
                                        test_ids=["d-0"]), cases)
        assert _start(client, suite_id=sid).status_code == 400

    def test_unknown_run_404(self, client):
        assert client.get("/api/optimize/runs/nope").status_code == 404
