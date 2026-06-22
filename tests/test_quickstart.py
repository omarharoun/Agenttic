"""Quickstart convenience endpoint: the generated from-requirement workflow is
a valid canonical graph, and the endpoint surfaces the BYO-key requirement
clearly (400) when the tenant has no Anthropic key."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app
from ascore.server.routes.quickstart import FromRequirementBody, _build_workflow
from ascore.server.workflow_schema import validate_workflow


def test_build_workflow_is_valid_canonical_graph():
    wf = _build_workflow(FromRequirementBody(
        requirement="Agents must refuse to delete production data.",
        agent_id="dut", system_prompt="be careful"))
    assert validate_workflow(wf) == []          # structurally valid, no cycles
    types = {n.type for n in wf.nodes}
    assert {"business_doc", "generator", "human_gate", "agent", "run_suite",
            "score", "scorecard", "report"} <= types
    # the requirement text is wired into the business_doc node
    doc = next(n for n in wf.nodes if n.type == "business_doc")
    assert doc.config["text"].startswith("Agents must refuse")


CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {token: "adm"}
"""


@pytest.fixture
def client(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    # NOTE: no injected clients -> the run path needs a real tenant key
    with TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db"))) as c:
        yield c


def test_quickstart_requires_anthropic_key(client):
    r = client.post("/api/quickstart/from-requirement",
                    headers={"Authorization": "Bearer adm"},
                    json={"requirement": "refuse harmful actions"})
    assert r.status_code == 400
    assert "Anthropic" in r.json()["detail"]


def test_quickstart_rejects_empty_requirement(client):
    r = client.post("/api/quickstart/from-requirement",
                    headers={"Authorization": "Bearer adm"},
                    json={"requirement": "   "})
    assert r.status_code == 422
