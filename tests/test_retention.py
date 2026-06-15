"""Trace retention: redaction + pruning by age."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from ascore.registry.sqlite_store import Registry, TraceRow
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace


def _trace(out="secret answer"):
    now = datetime.now(timezone.utc)
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 spans=[Span(span_id="s", kind="final_output", name="f",
                             start_time=now, end_time=now,
                             input={"ticket": "PII here"}, output={"text": out})],
                 visibility="glass_box", final_output=out,
                 schema_version=SCHEMA_VERSION)


def _age_traces(reg, days):
    """Backdate every trace's created_at by `days`."""
    old = datetime.now(timezone.utc) - timedelta(days=days)
    with Session(reg.engine) as s:
        for r in s.exec(__import__("sqlmodel").select(TraceRow)).all():
            r.created_at = old
            s.add(r)
        s.commit()


def test_redact_old_traces_strips_io(tmp_path):
    reg = Registry(tmp_path / "r.db")
    reg.save_trace(_trace())
    _age_traces(reg, 40)
    assert reg.redact_old_traces(30) == 1
    t = reg.traces("a")[0]
    assert t.final_output == "[redacted]"
    assert t.spans[0].input == {} and t.spans[0].output == {}
    # idempotent + age-gated: recent traces untouched
    reg.save_trace(_trace(out="fresh"))
    assert reg.redact_old_traces(30) == 1  # only the still-old one matches


def test_prune_old_traces(tmp_path):
    reg = Registry(tmp_path / "r.db")
    reg.save_trace(_trace())
    reg.save_trace(_trace())
    _age_traces(reg, 100)
    assert reg.prune_traces(90) == 2
    assert reg.traces("a") == []


def test_zero_is_noop(tmp_path):
    reg = Registry(tmp_path / "r.db")
    reg.save_trace(_trace())
    assert reg.redact_old_traces(0) == 0
    assert reg.prune_traces(0) == 0
    assert len(reg.traces("a")) == 1


def test_cli_retention_dry_run_and_apply(tmp_path):
    from typer.testing import CliRunner

    from ascore.cli import app
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l, generator: g}\n"
        "harness: {timeout_seconds: 1, max_steps: 1, max_parallel: 1, transport_retries: 0}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path / 'p.db'}, review_dir: r/, calibration_dir: c/}}\n"
        "retention: {trace_redact_days: 30, trace_prune_days: 90}\n")
    r = CliRunner().invoke(app, ["retention", "--config", str(cfg)])
    assert r.exit_code == 0 and "Dry run" in r.output
    r2 = CliRunner().invoke(app, ["retention", "--apply", "--config", str(cfg)])
    assert r2.exit_code == 0 and "Retention applied" in r2.output
