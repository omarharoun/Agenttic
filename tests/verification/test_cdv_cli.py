"""P5 — the operator surface. A loop nobody can start is a loop nobody runs.

`agenttic cdv --mock` is the whole method in one command: generate from the
space, run against the world, score, find the holes, aim the next batch at them,
report the bug-discovery curve and write the frozen regressions as PROPOSALS.
The `--mock` path takes no API key, so this is checkable in CI rather than
described in a README.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agenttic.cli import app
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.rubric import Rubric

RUBRIC = Rubric(rubric_id="r-cli", version=1, criteria=[
    {"criterion_id": "step_budget", "description": "within the step budget",
     "scorer": "code", "scale": "binary", "check_ref": "steps_under_limit"},
])


def _workspace(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models: {agent_default: scripted-support, judge_strong: j, "
        "judge_light: l, generator: g}\n"
        "harness: {timeout_seconds: 5, max_steps: 8, max_parallel: 2, "
        "transport_retries: 0}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "coverage: {closure_target: 0.95}\n"
        "cdv: {max_scenarios: 10, max_dollars: 1.0, max_rounds: 2, batch_size: 5}\n"
        f"paths: {{registry_db: {tmp_path / 'cdv.db'}, "
        f"review_dir: {tmp_path / 'review'}/, calibration_dir: c/}}\n")
    Registry(str(tmp_path / "cdv.db")).save_rubric(RUBRIC)
    return cfg


def test_cdv_is_registered():
    assert "cdv" in {c.callback.__name__ for c in app.registered_commands}


def test_cdv_mock_runs_the_loop_and_reports_the_curve(tmp_path):
    cfg = _workspace(tmp_path)
    result = CliRunner().invoke(app, ["cdv", "--agent", "dut", "--rubric", "r-cli",
                                      "--seed", "11", "--mock",
                                      "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "bug-discovery curve" in out
    # The two legs that have read "not run" on every sign-off the product has
    # ever issued.
    assert "4 · CONVERGENCE" in out
    assert "distinct failure signature(s) over" in out     # the leg is populated
    assert "closure/$" in out                              # so is the envelope
    assert "stimulus vs trace" in out
    assert "Scorecard" in out


def test_cdv_writes_proposals_not_a_suite(tmp_path):
    cfg = _workspace(tmp_path)
    result = CliRunner().invoke(app, ["cdv", "--agent", "dut", "--rubric", "r-cli",
                                      "--seed", "3", "--mock",
                                      "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    files = list((tmp_path / "review").glob("cdv-*.json"))
    assert files, "no frozen-regression proposals were written"
    payload = json.loads(files[0].read_text())
    assert all(r["approved"] is False for r in payload["regressions"])
    assert "human decision" in payload["note"]
