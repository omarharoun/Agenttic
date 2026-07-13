"""T22.5 — Index import/export + Catalog boundaries (SPEC-2 M10)."""

from __future__ import annotations

import json
import tempfile

from fastapi.testclient import TestClient

from agenttic.interop.agent_index import (
    export_card,
    import_index_cards,
    load_index_cards,
    validate_export_round_trip,
)
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.agent_card import AgentCard, FieldValue
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.server.app import create_app
from agenttic.server.routes.leaderboard import _attach_certification_badges

VENDOR = "data/vendor/ai-agent-index/2025_annotations.json"


def test_import_count_matches_file():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        n = import_index_cards(reg)
        file_count = len(json.load(open(VENDOR)))
        assert n == file_count
        assert len(reg.list_cards(source="index_import")) == file_count


def test_imported_cards_are_documented_with_attribution():
    cards = load_index_cards()
    for card in cards[:5]:
        assert card.source == "index_import"
        assert card.attribution and "CC BY 4.0" in card.attribution
        for fv in card.fields.values():
            if fv.status == "value_present":
                assert fv.provenance == "documented"
                assert fv.citations  # citations preserved


def test_export_round_trip_against_generated_registry():
    for card in load_index_cards()[:5]:
        assert validate_export_round_trip(card)
    # csv export produces a header + one data row
    csv_out = export_card(load_index_cards()[0], "csv")
    assert csv_out.count("\n") >= 2


def test_leaderboard_excludes_index_agents(tmp_path):
    reg = Registry(tmp_path / "a.db")
    # an index card whose agent_id collides with a (hypothetical) scorecard
    reg.save_card(AgentCard(agent_id="index:Foo", source="index_import",
                            fields={"k": FieldValue.documented("k", "v", ["c"])},
                            attribution="CC BY 4.0"))
    reg.save_scorecard(Scorecard(
        scorecard_id="sc1", agent_id="index:Foo", suite_id="s", suite_version=1,
        rubric_id="r", rubric_version=1,
        run_scores=[RunScore(trace_id="t", test_id="c", passed=True,
                             criterion_scores=[CriterionScore(
                                 criterion_id="x", score=1.0, scorer="code")])],
        task_success_rate=1.0, mean_cost_usd=0.0, p95_latency_ms=0.0,
        visibility_tier="glass_box"))
    reg.save_scorecard(Scorecard(
        scorecard_id="sc2", agent_id="real-agent", suite_id="s", suite_version=1,
        rubric_id="r", rubric_version=1,
        run_scores=[RunScore(trace_id="t2", test_id="c", passed=True,
                             criterion_scores=[CriterionScore(
                                 criterion_id="x", score=1.0, scorer="code")])],
        task_success_rate=1.0, mean_cost_usd=0.0, p95_latency_ms=0.0,
        visibility_tier="glass_box"))

    CONFIG = f"""\
models: {{agent_default: a, judge_strong: j, judge_light: l}}
harness: {{timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}}
scoring: {{calibration_threshold: 0.8}}
live: {{sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}}
paths: {{registry_db: {tmp_path / 'a.db'}, review_dir: {tmp_path / 'r'}, calibration_dir: {tmp_path / 'c'}}}
auth: {{required: true, token: t}}
security: {{login_max_attempts: 5, login_lockout_seconds: 900}}
"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG)
    with TestClient(create_app(str(cfg), registry=reg)) as c:
        board = c.get("/api/leaderboard", headers={"Authorization": "Bearer t"}).json()
        ids = {a["agent_id"] for a in board["agents"]}
        assert "index:Foo" not in ids  # Catalog-only, excluded
        assert "real-agent" in ids


def test_public_card_static_snapshot_empty_registry(tmp_path):
    reg = Registry(tmp_path / "a.db")  # otherwise empty
    reg.save_card(AgentCard(agent_id="index:Solo", source="index_import",
                            fields={"product_overview.name_of_agent":
                                    FieldValue.documented(
                                        "product_overview.name_of_agent", "Solo",
                                        ["doi:x"])},
                            attribution="CC BY 4.0"))
    CONFIG = f"""\
models: {{agent_default: a, judge_strong: j, judge_light: l}}
harness: {{timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}}
scoring: {{calibration_threshold: 0.8}}
live: {{sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}}
paths: {{registry_db: {tmp_path / 'a.db'}, review_dir: {tmp_path / 'r'}, calibration_dir: {tmp_path / 'c'}}}
auth: {{required: false, token: ""}}
security: {{login_max_attempts: 5, login_lockout_seconds: 900}}
"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG)
    with TestClient(create_app(str(cfg), registry=reg)) as c:
        # public, no auth
        r = c.get("/cards/index:Solo")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"] == "index_import"
        assert body["attribution"] == "CC BY 4.0"
        assert "completeness" in body
        assert body["fields_by_provenance"]["documented"]
