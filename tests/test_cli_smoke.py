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


def test_ui_binding_resolution():
    from ascore.cli import _resolve_ui_binding
    cfg = {"ui": {"host": "127.0.0.1", "port": 8700}}
    assert _resolve_ui_binding(cfg, "", 0, lan=False) == ("127.0.0.1", 8700)
    assert _resolve_ui_binding(cfg, "", 0, lan=True) == ("0.0.0.0", 8700)
    assert _resolve_ui_binding(cfg, "192.168.1.5", 9000, lan=False) == ("192.168.1.5", 9000)
    assert _resolve_ui_binding({}, "", 0, lan=False) == ("127.0.0.1", 8700)  # no ui section
    cfg_lan = {"ui": {"host": "0.0.0.0", "port": 8800}}  # persistent via config
    assert _resolve_ui_binding(cfg_lan, "", 0, lan=False) == ("0.0.0.0", 8800)


def _agent_cfg(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l, generator: g}\n"
        "harness: {timeout_seconds: 1, max_steps: 1, max_parallel: 1, transport_retries: 0}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path / 'p.db'}, review_dir: r/, calibration_dir: c/}}\n")
    return cfg


def test_cli_tenant_flag_isolates_data(tmp_path):
    from typer.testing import CliRunner
    cfg = _agent_cfg(tmp_path)
    run = CliRunner()
    # register an agent under tenant 'acme'
    a = run.invoke(app, ["--tenant", "acme", "agents", "add", "acme-bot",
                         "--config", str(cfg)])
    assert a.exit_code == 0, a.output
    # default tenant doesn't see it
    d = run.invoke(app, ["agents", "list", "--config", str(cfg)])
    assert "acme-bot" not in d.output
    # acme tenant does
    al = run.invoke(app, ["--tenant", "acme", "agents", "list", "--config", str(cfg)])
    assert "acme-bot" in al.output
    # and it lives in a sibling DB file
    assert (tmp_path / "p.acme.db").exists()


def test_agents_catalog_cli_roundtrip(tmp_path):
    from typer.testing import CliRunner
    cfg = _agent_cfg(tmp_path)
    run = CliRunner()
    add = run.invoke(app, ["agents", "add", "client-x", "--variant", "blackbox",
                           "--url", "http://x", "--config", str(cfg)])
    assert add.exit_code == 0 and "Registered" in add.output

    listed = run.invoke(app, ["agents", "list", "--config", str(cfg)])
    assert listed.exit_code == 0 and "client-x" in listed.output

    # bad connection details fail cleanly, not with a traceback
    bad = run.invoke(app, ["agents", "add", "b", "--variant", "blackbox",
                           "--config", str(cfg)])
    assert bad.exit_code != 0

    retire = run.invoke(app, ["agents", "retire", "client-x", "--config", str(cfg)])
    assert retire.exit_code == 0 and "Retired" in retire.output


def test_run_uses_named_agent_suite_options():
    """`ascore run` accepts --agent/--suite (README form), not positionals."""
    from typer.testing import CliRunner
    r = CliRunner().invoke(app, ["run", "--help"])
    assert r.exit_code == 0
    assert "--agent" in r.output and "--suite" in r.output
    # missing required options -> clear error, not a crash
    missing = CliRunner().invoke(app, ["run"])
    assert missing.exit_code != 0
    assert "--agent" in missing.output
