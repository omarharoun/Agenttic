"""CLI smoke test: app imports and registers the full command surface."""
from ascore.cli import app

def test_cli_commands_registered():
    names = {c.callback.__name__ for c in app.registered_commands}
    assert {"generate", "approve", "run", "calibrate",
            "regress", "report", "monitor"} <= names


def test_pilot_seed_command(tmp_path):
    from typer.testing import CliRunner
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l, generator: g}\n"
        "harness: {timeout_seconds: 1, max_steps: 1, max_parallel: 1, transport_retries: 0}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path / 'p.db'}, review_dir: r/, calibration_dir: c/}}\n")
    result = CliRunner().invoke(app, ["pilot", "--config", str(cfg), "--approve"])
    assert result.exit_code == 0, result.output
    assert "Seeded suite" in result.output
    from ascore.registry.sqlite_store import Registry
    suite, cases = Registry(tmp_path / "p.db").get_suite("pilot-support-triage")
    assert suite.approved and len(cases) == 10
    # idempotent re-run
    again = CliRunner().invoke(app, ["pilot", "--config", str(cfg)])
    assert again.exit_code == 0 and "already seeded" in again.output
