"""FastAPI surface: node-type catalog, workflow CRUD, execution lifecycle
(start → live progress → succeeded), the human-gate approve flow over HTTP,
resource browsing, uploads, and SSE replay. All LLM/agent calls faked.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app
from ascore.server.nodes import NODE_TYPES, NodeSpec
from tests.test_e2e_pipeline import ProfessionalToneJudgeClient, RoutingFakeClient
from tests.test_executor import eval_workflow, load_pilot

CONFIG_TEMPLATE = """\
models:
  agent_default: agent-model
  judge_strong: judge-model
  judge_light: judge-light
harness:
  timeout_seconds: 10
  max_parallel: 5
  transport_retries: 1
  max_steps: 10
scoring:
  calibration_threshold: 0.8
live:
  sample_rate: 0.05
  drift_threshold: 0.15
  drift_window_runs: 50
paths:
  registry_db: {db}
  review_dir: {review}
  calibration_dir: {calib}
ui:
  uploads_dir: {uploads}
"""


@pytest.fixture
def client(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG_TEMPLATE.format(
        db=tmp_path / "api.db", review=tmp_path / "review",
        calib=tmp_path / "calibration", uploads=tmp_path / "uploads"))
    reg = Registry(tmp_path / "api.db")
    load_pilot(reg)

    class ConstConfig(BaseModel):
        value: dict = {}

    async def run_const(ctx, cfg, inputs):
        return {"suite": cfg.value}

    NODE_TYPES["const_suite"] = NodeSpec(
        "const_suite", "Const Suite", "input", ConstConfig,
        {}, {"suite": "suite_ref"}, run_const)

    app = create_app(str(cfg_path), registry=reg, clients={
        "agent": RoutingFakeClient(), "judge": ProfessionalToneJudgeClient()})
    with TestClient(app) as c:
        c.reg = reg
        yield c
    NODE_TYPES.pop("const_suite", None)


def _make_unapproved_suite(reg, suite_id: str) -> None:
    """Two scoreable cases (same expected shape as the pilot) — DRAFT."""
    from ascore.schema.testcase import TestCase, TestSuite
    cases = [TestCase(
        test_id=f"g-{i}", suite_id=suite_id, task_description="t",
        input={"ticket": "I was charged twice, refund one charge"},
        expected={"final_output": "billing", "required_tools": ["lookup_kb"],
                  "max_steps": 6, "max_cost_usd": 0.05},
        rubric_id="r-triage") for i in range(2)]
    reg.save_suite(TestSuite(suite_id=suite_id, business_context="x",
                             test_ids=[c.test_id for c in cases]), cases)


def poll(client, execution_id, until, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ex = client.get(f"/api/executions/{execution_id}").json()
        if ex["status"] == until:
            return ex
        if ex["status"] in ("failed", "cancelled") and until != ex["status"]:
            raise AssertionError(f"execution ended {ex['status']}: {ex}")
        time.sleep(0.05)
    raise AssertionError(f"timeout waiting for {until}")


class TestCatalogAndCrud:
    def test_node_types_catalog(self, client):
        types = {t["type"]: t for t in client.get("/api/node-types").json()}
        for expected in ("business_doc", "generator", "human_gate", "agent",
                         "run_suite", "score", "scorecard", "report", "monitor"):
            assert expected in types
        agent = types["agent"]
        assert agent["outputs"] == {"agent": "agent_ref"}
        assert "variant" in agent["config_schema"]["properties"]

    def test_workflow_crud(self, client):
        wf = eval_workflow("pilot-support-triage").model_dump()
        r = client.post("/api/workflows", json=wf)
        assert r.status_code == 200 and r.json()["problems"] == []
        assert client.get("/api/workflows").json()[0]["workflow_id"] == "wf-eval"
        got = client.get("/api/workflows/wf-eval").json()
        assert len(got["workflow"]["nodes"]) == 5
        client.delete("/api/workflows/wf-eval")
        assert client.get("/api/workflows/wf-eval").status_code == 404


class TestExecutionLifecycle:
    def test_run_to_succeeded_with_scorecard(self, client):
        wf = eval_workflow("pilot-support-triage").model_dump()
        client.post("/api/workflows", json=wf)
        eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
        ex = poll(client, eid, "succeeded")
        assert set(ex["node_states"].values()) == {"succeeded"}
        sc = ex["node_outputs"]["card"]["scorecard"]
        assert sc["task_success_rate"] == pytest.approx(0.8)
        cards = client.get("/api/scorecards").json()
        assert cards[0]["scorecard_id"] == sc["scorecard_id"]
        report = client.get(f"/api/scorecards/{sc['scorecard_id']}/report")
        assert "Executive summary" in report.text
        traces = client.get("/api/traces?agent_id=ref-agent").json()
        assert len(traces) == 10
        full = client.get(f"/api/traces/{traces[0]['trace_id']}").json()
        assert full["spans"]

    def test_invalid_workflow_422(self, client):
        wf = eval_workflow("pilot-support-triage").model_dump()
        wf["nodes"][0]["type"] = "nonsense"
        client.post("/api/workflows", json=wf)
        r = client.post("/api/workflows/wf-eval/executions")
        assert r.status_code == 422
        assert any("unknown node type" in p
                   for p in r.json()["detail"]["problems"])

    def test_gate_approve_over_http(self, client):
        # the pilot is approved by the fixture; gate needs an unapproved suite
        _make_unapproved_suite(client.reg, "gated")
        wf = eval_workflow("gated", with_gate=True)
        for n in wf.nodes:
            if n.node_id == "src":
                n.config["value"] = {"suite_id": "gated", "version": 1,
                                     "approved": False}
        client.post("/api/workflows", json=wf.model_dump())
        eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
        poll(client, eid, "waiting_approval")
        r = client.post(f"/api/executions/{eid}/approve")
        assert r.json()["approved"]["suite_id"] == "gated"
        ex = poll(client, eid, "succeeded")
        assert ex["node_states"]["gate"] == "succeeded"
        # suite is now approved in the registry too
        suites = {s["suite_id"]: s for s in client.get("/api/suites").json()}
        assert suites["gated"]["approved"] is True

    def test_cancel(self, client):
        _make_unapproved_suite(client.reg, "gated")  # parks on the gate
        wf = eval_workflow("gated", with_gate=True)
        for n in wf.nodes:
            if n.node_id == "src":
                n.config["value"] = {"suite_id": "gated", "version": 1,
                                     "approved": False}
        client.post("/api/workflows", json=wf.model_dump())
        eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
        poll(client, eid, "waiting_approval")  # parked on the gate
        client.post(f"/api/executions/{eid}/cancel")
        assert client.get(f"/api/executions/{eid}").json()["status"] == "cancelled"


class TestSSE:
    def test_replay_stream_of_finished_execution(self, client):
        wf = eval_workflow("pilot-support-triage").model_dump()
        client.post("/api/workflows", json=wf)
        eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
        poll(client, eid, "succeeded")
        events = []
        with client.stream("GET", f"/api/executions/{eid}/events") as r:
            for line in r.iter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if "stream_end" in line:
                    break
        assert events[0] == "execution_started"
        assert "node_progress" in events
        assert events[-2] == "execution_succeeded"
        assert events[-1] == "stream_end"

    def test_replay_after_seq(self, client):
        wf = eval_workflow("pilot-support-triage").model_dump()
        client.post("/api/workflows", json=wf)
        eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
        poll(client, eid, "succeeded")
        seqs = []
        with client.stream("GET", f"/api/executions/{eid}/events?after=5") as r:
            for line in r.iter_lines():
                if line.startswith("data:") and '"seq"' in line:
                    seqs.append(json.loads(line.split(":", 1)[1])["seq"])
                if "stream_end" in line:
                    break
        assert seqs and min(seqs) == 6


class TestResources:
    def test_suites_and_rubrics(self, client):
        suites = client.get("/api/suites").json()
        assert suites[0]["suite_id"] == "pilot-support-triage"
        assert suites[0]["n_cases"] == 10
        detail = client.get("/api/suites/pilot-support-triage").json()
        assert len(detail["cases"]) == 10
        rubrics = client.get("/api/rubrics").json()
        assert rubrics[0]["rubric_id"] == "r-triage"
        assert client.get("/api/rubrics/r-triage").json()["criteria"]

    def test_review_404_when_missing(self, client):
        assert client.get("/api/suites/pilot-support-triage/review").status_code == 404

    def test_upload(self, client):
        r = client.post("/api/uploads",
                        files={"file": ("biz doc.txt", b"Support team triages tickets")})
        path = r.json()["file_path"]
        assert path.endswith("biz_doc.txt")
        assert "uploads" in path

    def test_monitor_endpoint(self, client):
        r = client.get("/api/monitor/ref-agent").json()
        assert r == {"agent_id": "ref-agent", "reeval_requests": []}


class TestImportDryRun:
    def test_dry_run_validates_without_saving(self, client):
        wf = eval_workflow("pilot-support-triage").model_dump()
        wf["workflow_id"] = "imported-wf"
        wf["nodes"][0]["type"] = "nonsense"  # broken import
        r = client.post("/api/workflows?dry_run=true", json=wf).json()
        assert r["saved"] is False
        assert any("unknown node type" in p for p in r["problems"])
        # nothing persisted
        assert client.get("/api/workflows/imported-wf").status_code == 404

    def test_valid_import_then_real_save(self, client):
        wf = eval_workflow("pilot-support-triage").model_dump()
        wf["workflow_id"] = "imported-wf"
        assert client.post("/api/workflows?dry_run=true",
                           json=wf).json()["problems"] == []
        r = client.post("/api/workflows", json=wf).json()
        assert r["saved"] is True
        assert client.get("/api/workflows/imported-wf").status_code == 200


class TestExecutionResults:
    def test_joined_scoreboard_with_predictions(self, client):
        wf = eval_workflow("pilot-support-triage").model_dump()
        client.post("/api/workflows", json=wf)
        eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
        poll(client, eid, "succeeded")
        r = client.get(f"/api/executions/{eid}/results").json()
        assert r["status"] == "succeeded"
        (sc,) = r["scorecards"]
        assert sc["task_success_rate"] == pytest.approx(0.8)
        assert "tone" in sc["per_criterion_means"]
        cases = r["cases"]
        assert len(cases) == 10
        by_id = {c["test_id"]: c for c in cases}
        # prediction (agent's actual answer) and expected ground truth present
        assert by_id["triage-000"]["prediction"] == "billing"
        assert by_id["triage-000"]["expected"]["final_output"] == "billing"
        assert by_id["triage-000"]["passed"] is True
        # the adversarial WRONGCASE misroutes: prediction != expected, failed
        wrong = by_id["triage-008"]
        assert wrong["passed"] is False
        assert wrong["prediction"] != wrong["expected"]["final_output"]
        # per-criterion detail with judge rationale
        tone = next(c for c in cases[0]["criteria"] if c["criterion_id"] == "tone")
        assert tone["scorer"] == "judge" and tone["rationale"]

    def test_results_for_unscored_execution_is_empty(self, client):
        _make_unapproved_suite(client.reg, "gated")
        wf = eval_workflow("gated", with_gate=True)
        for n in wf.nodes:
            if n.node_id == "src":
                n.config["value"] = {"suite_id": "gated", "version": 1,
                                     "approved": False}
        client.post("/api/workflows", json=wf.model_dump())
        eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
        poll(client, eid, "waiting_approval")
        r = client.get(f"/api/executions/{eid}/results").json()
        assert r == {"status": "waiting_approval", "scorecards": [], "cases": []}
        client.post(f"/api/executions/{eid}/cancel")


class TestFiAndPartialBatchApi:
    def test_fi_eval_node_in_catalog(self, client):
        types = {t["type"]: t for t in client.get("/api/node-types").json()}
        assert "fi_eval" in types
        fi = types["fi_eval"]
        assert fi["inputs"] == {"run": "run_ref"}
        assert fi["outputs"] == {"scored": "scored_run"}
        assert "metrics" in fi["config_schema"]["properties"]

    def test_results_surface_errored_cases(self, client):
        # a judge that always raises -> every case errors (partial batch)
        from types import SimpleNamespace as NS

        class BoomJudge:
            def __init__(self):
                self.messages = NS(create=self._c)
            def _c(self, **kw):
                raise RuntimeError("judge 500")

        # swap the injected judge for one that always raises, then run
        client.app.state.manager.clients["judge"] = BoomJudge()
        wf = eval_workflow("pilot-support-triage").model_dump()
        client.post("/api/workflows", json=wf)
        eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
        poll(client, eid, "succeeded")  # run completes; scoring all errored
        r = client.get(f"/api/executions/{eid}/results").json()
        (sc,) = r["scorecards"]
        assert len(sc["errored_test_ids"]) == 10
        assert sc["task_success_rate"] == 0.0
        assert all(c["scoring_error"] for c in r["cases"])
        assert "judge 500" in r["cases"][0]["scoring_error"]


class TestLeaderboardApi:
    def test_leaderboard_ranks_two_agents(self, client):
        # two agents with different routing behavior -> different Index
        from types import SimpleNamespace as NS
        import json as _json
        import uuid as _uuid

        class AlwaysGeneralClient:
            """Consults the KB but misroutes everything to 'general'."""
            def __init__(self):
                self.messages = NS(create=self._c)
            def _c(self, **kw):
                has_tool_result = any(
                    isinstance(m.get("content"), list) for m in kw["messages"])
                if not has_tool_result:
                    return NS(stop_reason="tool_use",
                              usage=NS(input_tokens=200, output_tokens=30),
                              content=[NS(type="tool_use", name="lookup_kb",
                                          input={"key": "routing_rules"},
                                          id=f"tu_{_uuid.uuid4().hex[:6]}")])
                return NS(stop_reason="end_turn",
                          usage=NS(input_tokens=260, output_tokens=8),
                          content=[NS(type="text", text="general")])

        wf = eval_workflow("pilot-support-triage").model_dump()
        agents = {"agent-good": RoutingFakeClient(),
                  "agent-meh": AlwaysGeneralClient()}
        for agent_id, agent_client in agents.items():
            for n in wf["nodes"]:
                if n["node_id"] == "agent":
                    n["config"]["agent_id"] = agent_id
            wf["workflow_id"] = f"wf-{agent_id}"
            client.app.state.manager.clients["agent"] = agent_client
            client.app.state.manager.clients["judge"] = ProfessionalToneJudgeClient()
            client.post("/api/workflows", json=wf)
            eid = client.post(
                f"/api/workflows/wf-{agent_id}/executions").json()["execution_id"]
            poll(client, eid, "succeeded")

        board = client.get("/api/leaderboard").json()
        names = [r["agent_id"] for r in board["agents"]]
        assert names[0] == "agent-good"  # higher Index ranks first
        assert board["agents"][0]["rank"] == 1
        assert board["agents"][0]["index"] > board["agents"][1]["index"]
        assert board["suites"] == ["pilot-support-triage"]
        assert all(r["coverage"] == 1 for r in board["agents"])

    def test_leaderboard_empty_when_no_runs(self, client):
        assert client.get("/api/leaderboard").json() == {
            "suites": [], "agents": [], "weights": {}}
