"""FastAPI surface: node-type catalog, workflow CRUD, execution lifecycle
(start → live progress → succeeded), the human-gate approve flow over HTTP,
resource browsing, uploads, and SSE replay. All LLM/agent calls faked.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agenttic.registry.sqlite_store import Registry
from agenttic.server.app import create_app
from agenttic.server.nodes import NODE_TYPES, NodeSpec
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
    from agenttic.schema.testcase import TestCase, TestSuite
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
        # The per-row "report" button on the Scorecards table opens this route
        # in-app; it must return 200 text (a non-2xx would be swallowed client-side).
        report = client.get(f"/api/scorecards/{sc['scorecard_id']}/report")
        assert report.status_code == 200
        assert report.headers["content-type"].startswith("text/plain")
        assert "Executive summary" in report.text
        assert report.text.strip()
        # The sibling "PDF" button hits a distinct route; both must resolve so
        # the two buttons never collide on the same path.
        pdf = client.get(f"/api/scorecards/{sc['scorecard_id']}/report.pdf")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        # A missing scorecard is a clean 404, not a 500.
        assert client.get("/api/scorecards/nope/report").status_code == 404
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


class TestCostEstimateApi:
    def test_estimate_for_suite(self, client):
        r = client.get("/api/estimate?suite_id=pilot-support-triage").json()
        est = r["estimate"]
        assert est["n_cases"] == 10
        assert est["agent_variant"] == "reference"
        assert est["projected_usd"] > 0
        assert est["n_judge_criteria"] >= 1  # the pilot rubric has a judge criterion
        assert "budget" in r and r["budget"]["projected_usd"] == est["projected_usd"]

    def test_estimate_unknown_suite_404(self, client):
        assert client.get("/api/estimate?suite_id=ghost").status_code == 404

    def test_estimate_declared_blackbox_agent(self, client):
        client.post("/api/agents/catalog", json={
            "agent_id": "client-bb", "variant": "blackbox",
            "url": "https://agents.example.com/x"})
        est = client.get("/api/estimate?suite_id=pilot-support-triage"
                         "&agent_id=client-bb").json()["estimate"]
        assert est["agent_variant"] == "blackbox"
        assert est["projected_agent_usd"] == 0.0  # unknown for black-box

    def test_estimate_declared_blackbox_with_cost(self, client):
        client.post("/api/agents/catalog", json={
            "agent_id": "client-bb2", "variant": "blackbox",
            "url": "https://agents.example.com/y", "cost_per_call_usd": 0.005})
        est = client.get("/api/estimate?suite_id=pilot-support-triage"
                         "&agent_id=client-bb2").json()["estimate"]
        # 0.005 * 10 cases of agent cost now estimated, not "unknown"
        assert est["projected_agent_usd"] == 0.05

    def test_quota_endpoint(self, client):
        r = client.get("/api/quota").json()
        assert r["tenant"] == "default"
        assert "spent_today_usd" in r and "remaining_daily_usd" in r

    def test_workflow_estimate(self, client):
        wf = eval_workflow("pilot-support-triage").model_dump()
        for n in wf["nodes"]:
            if n["type"] == "run_suite":
                n["config"]["suite_id"] = "pilot-support-triage"
        client.post("/api/workflows", json=wf)
        r = client.get("/api/workflows/wf-eval/estimate").json()
        assert r["estimate"]["n_cases"] == 10
        assert r["estimate"]["projected_usd"] > 0


class TestAgentsDiscovery:
    def test_lists_agents_observed_from_runs(self, client):
        # before any run, no observed agents (managed may warn w/o key)
        empty = client.get("/api/agents").json()
        assert empty["agents"] == []

        wf = eval_workflow("pilot-support-triage").model_dump()
        for n in wf["nodes"]:
            if n["node_id"] == "agent":
                n["config"]["agent_id"] = "discovered-agent"
        client.post("/api/workflows", json=wf)
        eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
        poll(client, eid, "succeeded")

        agents = {a["agent_id"]: a for a in client.get("/api/agents").json()["agents"]}
        a = agents["discovered-agent"]
        assert a["scored"] is True
        assert "scored" in a["sources"] and "traced" in a["sources"]
        assert a["n_scorecards"] == 1 and a["n_traces"] == 10
        assert a["suites"] == ["pilot-support-triage"]
        assert a["last_seen"]

    def test_managed_failure_is_a_warning_not_a_500(self, client):
        # no ANTHROPIC_API_KEY in test env -> managed enrichment degrades softly
        r = client.get("/api/agents")
        assert r.status_code == 200
        # warning may be set (no key) but the call still succeeds
        assert "agents" in r.json()


class TestDeclaredAgentCatalogApi:
    def test_register_list_get_retire(self, client):
        # register a black-box agent
        r = client.post("/api/agents/catalog", json={
            "agent_id": "client-x", "variant": "blackbox",
            "url": "http://client-x/agent", "description": "the client's bot"})
        assert r.status_code == 200 and r.json()["version"] == 1

        catalog = client.get("/api/agents/catalog").json()["agents"]
        assert [a["agent_id"] for a in catalog] == ["client-x"]
        assert catalog[0]["url"] == "http://client-x/agent"

        one = client.get("/api/agents/catalog/client-x").json()
        assert one["variant"] == "blackbox"

        # re-register bumps the version (append-only)
        v2 = client.post("/api/agents/catalog", json={
            "agent_id": "client-x", "variant": "blackbox",
            "url": "http://client-x/v2"}).json()
        assert v2["version"] == 2

        # retire hides it from the default catalog list
        assert client.delete("/api/agents/catalog/client-x").json() == {
            "retired": "client-x"}
        assert client.get("/api/agents/catalog").json()["agents"] == []
        assert len(client.get(
            "/api/agents/catalog?include_retired=true").json()["agents"]) == 1

    def test_invalid_variant_connection_is_422(self, client):
        r = client.post("/api/agents/catalog",
                        json={"agent_id": "b", "variant": "blackbox"})  # no url
        assert r.status_code == 422

    def test_ssrf_url_rejected_at_registration(self, client):
        # metadata / loopback / non-http schemes are refused with 422
        for url in ("http://169.254.169.254/latest/", "http://127.0.0.1/agent",
                    "file:///etc/passwd"):
            r = client.post("/api/agents/catalog", json={
                "agent_id": "evil", "variant": "blackbox", "url": url})
            assert r.status_code == 422, url
            assert "unsafe agent url" in str(r.json()["detail"])

    def test_unknown_agent_404(self, client):
        assert client.get("/api/agents/catalog/ghost").status_code == 404
        assert client.delete("/api/agents/catalog/ghost").status_code == 404

    def test_declared_agent_shows_in_discovery_before_any_run(self, client):
        client.post("/api/agents/catalog", json={
            "agent_id": "prod-agent", "variant": "reference",
            "model": "claude-x", "description": "prod"})
        agents = {a["agent_id"]: a
                  for a in client.get("/api/agents").json()["agents"]}
        a = agents["prod-agent"]
        assert a["declared"] is True and a["variant"] == "reference"
        assert "declared" in a["sources"] and a["scored"] is False

    def test_declared_then_run_unions_sources(self, client):
        client.post("/api/agents/catalog", json={
            "agent_id": "discovered-agent", "variant": "reference"})
        wf = eval_workflow("pilot-support-triage").model_dump()
        for n in wf["nodes"]:
            if n["node_id"] == "agent":
                n["config"]["agent_id"] = "discovered-agent"
        client.post("/api/workflows", json=wf)
        eid = client.post(
            "/api/workflows/wf-eval/executions").json()["execution_id"]
        poll(client, eid, "succeeded")

        a = {x["agent_id"]: x
             for x in client.get("/api/agents").json()["agents"]}["discovered-agent"]
        assert {"declared", "scored", "traced"} <= set(a["sources"])
        assert a["declared"] is True and a["scored"] is True

        # the leaderboard now carries the declared type
        board = client.get("/api/leaderboard").json()
        row = next(r for r in board["agents"]
                   if r["agent_id"] == "discovered-agent")
        assert row["agent_type"] == "reference"


class TestApiNeverFallsThroughToSpaHtml:
    """Regression for the intermittent "Unexpected Application Error!
    JSON.parse: unexpected character at line 1 column 1" crash: an unmatched
    ``/api/*`` (or ``/v1/*``) GET must return a JSON 404 — NOT the HTML SPA
    shell. When the UI is built and mounted, the catch-all route used to serve
    ``index.html`` (200 text/html) for any unmatched path, so the frontend's
    ``res.json()`` choked on ``<!DOCTYPE html>`` and crashed the whole app."""

    def _spa_client(self, tmp_path, monkeypatch):
        # A built UI so the SPA catch-all actually mounts (it's gated on
        # UI_DIST.is_dir()). index.html is the exact body that used to leak.
        ui_dist = tmp_path / "uidist"
        (ui_dist / "assets").mkdir(parents=True)
        (ui_dist / "index.html").write_text(
            "<!DOCTYPE html><html><head><title>Agenttic</title></head>"
            "<body><div id=root></div></body></html>")
        import agenttic.server.app as appmod
        monkeypatch.setattr(appmod, "UI_DIST", ui_dist)

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(CONFIG_TEMPLATE.format(
            db=tmp_path / "api.db", review=tmp_path / "review",
            calib=tmp_path / "calibration", uploads=tmp_path / "uploads"))
        reg = Registry(tmp_path / "api.db")
        load_pilot(reg)
        app = create_app(str(cfg_path), registry=reg, clients={
            "agent": RoutingFakeClient(), "judge": ProfessionalToneJudgeClient()})
        return TestClient(app)

    def test_unmatched_api_get_is_json_404_not_html(self, tmp_path, monkeypatch):
        with self._spa_client(tmp_path, monkeypatch) as c:
            r = c.get("/api/does-not-exist-xyz")
            assert r.status_code == 404
            assert "application/json" in r.headers["content-type"]
            assert not r.text.lstrip().startswith("<")  # never HTML
            r.json()  # body parses as JSON (would raise if it were HTML)
            assert r.json()["detail"]

            # /v1 (OTLP ingest surface) is guarded the same way
            v = c.get("/v1/nope")
            assert v.status_code == 404
            assert "application/json" in v.headers["content-type"]

    def test_spa_shell_still_served_for_app_routes(self, tmp_path, monkeypatch):
        # The fix must not break the SPA: a real app route still gets index.html.
        with self._spa_client(tmp_path, monkeypatch) as c:
            r = c.get("/app/canvas")
            assert r.status_code == 200
            assert "text/html" in r.headers["content-type"]
            assert "<!DOCTYPE html>" in r.text
