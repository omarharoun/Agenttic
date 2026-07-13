"""SPEC-8 T44.3 — each integration page's config block is validated against the
OTLP endpoint with a captured span fixture, and no page overstates coverage.

For every framework page in docs/integrations/ there is a golden captured-span
fixture (mimicking that framework's exporter output — only service.name, no
agenttic.* hints). Each fixture is POSTed through the *real* /v1/traces endpoint
and must land as a well-formed, live-provenanced Trace with the llm/tool steps
captured. A reviewer-checklist test asserts every page states capture honestly
and ties to the NOT-ASSESSED contract without overstating.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agenttic.registry.sqlite_store import Registry
from agenttic.server.app import create_app

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs" / "integrations"
FIX = Path(__file__).resolve().parent / "fixtures" / "integrations"

# page-slug -> the service.name the golden fixture carries
FRAMEWORKS = {
    "generic-otlp": "my-agent",
    "crewai": "crewai-crew",
    "langgraph": "langgraph-agent",
    "llamaindex": "llamaindex-agent",
    "openai-agents": "openai-agent",
}


def _app(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l}\n"
        "harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path / 'a.db'}, review_dir: {tmp_path / 'r'}, "
        f"calibration_dir: {tmp_path / 'c'}}}\n"
        "auth: {required: true, token: t}\n"
        "security: {login_max_attempts: 5, login_lockout_seconds: 900}\n")
    reg = Registry(db_path=str(tmp_path / "a.db"))
    return create_app(str(cfg), registry=reg), reg


@pytest.mark.parametrize("slug,service", list(FRAMEWORKS.items()))
def test_golden_fixture_ingests_through_real_endpoint(slug, service, tmp_path):
    payload = json.loads((FIX / f"{slug}.json").read_text())
    app, reg = _app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/v1/traces", headers={"Authorization": "Bearer t"},
                   json=payload)
        assert r.status_code == 200, r.text
        # full success — every span mapped, none rejected
        assert r.json() == {"partialSuccess": {}}

    # a well-formed, live-provenanced Trace with the llm + tool steps captured
    saved = _only_trace(reg, payload)
    assert saved.source == "otel_ingest"
    assert saved.agent_id == service          # fell back to service.name
    kinds = [s.kind for s in saved.spans]
    assert "llm_call" in kinds and "tool_call" in kinds


def _only_trace(reg, payload):
    # the fixture has one trace id; pull it back out to verify it saved
    tid = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"]
    return reg.get_trace(tid)


@pytest.mark.parametrize("slug", list(FRAMEWORKS.keys()))
def test_each_page_exists_with_config_pointing_at_ingest(slug):
    page = DOCS / f"{slug}.md"
    assert page.exists(), page
    text = page.read_text()
    assert "/v1/traces" in text, f"{slug}: config block must point at /v1/traces"


@pytest.mark.parametrize("slug", list(FRAMEWORKS.keys()))
def test_each_page_states_capture_honestly(slug):
    """Reviewer-checklist: every page has a captured-vs-not statement, ties to
    NOT ASSESSED, and does not overstate (no 'certified'/'guaranteed' claims from
    live spans)."""
    text = DOCS / f"{slug}.md"
    body = text.read_text()
    assert "Captured" in body and "Not captured" in body
    assert "NOT ASSESSED" in body
    lower = body.lower()
    for overstatement in ("guaranteed safe", "certified tier", "fully assessed",
                          "certifies your agent"):
        assert overstatement not in lower, f"{slug} overstates: {overstatement!r}"


def test_integrations_index_lists_every_framework():
    index = (DOCS / "README.md").read_text()
    for slug in FRAMEWORKS:
        assert f"{slug}.md" in index, f"index missing {slug}"
