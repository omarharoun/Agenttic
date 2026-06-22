"""Inspect interop: agenttic scorecards/traces ⇄ inspect_ai EvalLog.

Covers the round-trip contract (lossless on the mappable fields), the documented
lossy edges (foreign logs snap scores / recompute aggregates), the export
endpoint (valid JSON, auth-gated, tenant-scoped, 404), and the CLI. When
``inspect_ai`` is installed the produced log is also validated against the real
pydantic models; otherwise that one check is skipped (no runtime dependency).
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ascore.interop import INTEROP_VERSION, from_inspect_log, to_inspect_log
from ascore.registry.sqlite_store import Registry
from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.scorecard import CriterionScore, RunScore, Scorecard
from ascore.schema.testcase import TestCase
from ascore.schema.trace import Span, Trace
from ascore.server.app import create_app

T0 = datetime(2026, 6, 22, 10, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 22, 10, 0, 5, tzinfo=timezone.utc)

RUBRIC = Rubric(rubric_id="rub-1", version=2, criteria=[
    Criterion(criterion_id="correct", description="right answer", scorer="code",
              scale="binary", check_ref="final_output_matches_expected"),
    Criterion(criterion_id="safe", description="no harm", scorer="judge",
              scale="three_point", anchors={"pass": "safe", "fail": "harmful"}),
])


def _trace(i: int) -> Trace:
    return Trace(
        trace_id=f"tr-{i}", agent_id="agent-x", agent_config_hash="cfg123",
        test_case_id=f"tc-{i}", visibility="glass_box",
        final_output=f"answer {i}", total_cost_usd=0.012,
        total_latency_ms=5000.0, total_steps=2,
        spans=[
            Span(span_id=f"s{i}a", kind="llm_call", name="plan",
                 start_time=T0, end_time=T1, input={"prompt": "solve"},
                 output={"completion": "thinking..."}, tokens_in=100,
                 tokens_out=20, cost_usd=0.01),
            Span(span_id=f"s{i}b", parent_id=f"s{i}a", kind="tool_call",
                 name="calc", start_time=T0, end_time=T1, input={"x": 1},
                 output={"result": 42}, cost_usd=0.002,
                 attributes={"retry": False}),
        ])


def _case(i: int) -> TestCase:
    return TestCase(test_id=f"tc-{i}", suite_id="suite-a", version=3,
                    task_description=f"question {i}", input={"q": i},
                    expected={"answer": i}, tags=["happy_path"],
                    rubric_id="rub-1")


def _scorecard(sid="sc-1") -> Scorecard:
    runs = [RunScore(
        trace_id=f"tr-{i}", test_id=f"tc-{i}", passed=(i != 2),
        cost_usd=0.01 * (i + 1), scoring_cost_usd=0.003, latency_ms=100.0 * (i + 1),
        steps=i + 2, criterion_scores=[
            CriterionScore(criterion_id="correct", score=1.0 if i != 2 else 0.0,
                           scorer="code"),
            CriterionScore(criterion_id="safe", score=0.5, scorer="judge",
                           calibrated=False, judge_rationale="mostly safe",
                           cost_usd=0.003),
        ]) for i in range(3)]
    return Scorecard.aggregate(
        scorecard_id=sid, agent_id="agent-x", suite_id="suite-a", suite_version=3,
        rubric_id="rub-1", rubric_version=2, run_scores=runs,
        visibility_tier="glass_box")


def _full_export(sid="sc-1"):
    sc = _scorecard(sid)
    traces = [_trace(i) for i in range(3)]
    cases = [_case(i) for i in range(3)]
    return sc, traces, cases, to_inspect_log(sc, rubric=RUBRIC, traces=traces,
                                             testcases=cases)


class TestRoundTrip:
    def test_scorecard_rubric_trace_lossless(self):
        sc, traces, _, log = _full_export()
        back = from_inspect_log(log)
        assert back["scorecard"].model_dump() == sc.model_dump()
        assert back["rubric"].model_dump() == RUBRIC.model_dump()
        by_id = {t.trace_id: t for t in back["traces"]}
        for t in traces:  # every span, timing, IO, token count, attribute
            assert by_id[t.trace_id].model_dump() == t.model_dump()

    def test_export_is_json_serializable_evallog_shape(self):
        import json
        _, _, _, log = _full_export()
        assert json.loads(json.dumps(log))  # no non-serializable values
        assert log["version"] == 2
        assert log["eval"]["task"] == "suite-a"
        assert log["eval"]["model"] == "agent-x"        # agent under test -> model
        assert log["eval"]["task_version"] == 3
        assert log["eval"]["run_id"] == "sc-1"
        assert len(log["samples"]) == 3
        # one Inspect Score per criterion on each sample
        assert set(log["samples"][0]["scores"]) == {"correct", "safe"}
        # results carry per-criterion + overall metrics
        names = {s["name"] for s in log["results"]["scores"]}
        assert {"correct", "safe", "task_success_rate"} <= names

    def test_export_without_traces_or_rubric_still_valid(self):
        sc = _scorecard()
        log = to_inspect_log(sc)  # scores + aggregates only
        back = from_inspect_log(log)
        assert back["scorecard"].model_dump() == sc.model_dump()
        assert back["traces"] == []
        assert back["rubric"] is None

    def test_agenttic_metadata_namespaced(self):
        _, _, _, log = _full_export()
        assert log["eval"]["metadata"]["agenttic"]["exporter"] == "agenttic"
        assert log["results"]["metadata"]["agenttic"]["interop_version"] == \
            INTEROP_VERSION
        assert log["samples"][0]["metadata"]["agenttic"]["trace_id"] == "tr-0"


class TestNativeProjection:
    """The native (lossy) Inspect view is faithful even though the lossless
    record lives in metadata."""

    def test_messages_and_output_rendered_from_trace(self):
        _, _, _, log = _full_export()
        s0 = log["samples"][0]
        assert s0["output"]["completion"] == "answer 0"
        roles = [m["role"] for m in s0["messages"]]
        assert "assistant" in roles and "tool" in roles
        # token usage surfaced natively
        assert s0["model_usage"]["agent-x"]["input_tokens"] == 100

    def test_score_values_are_inspect_native(self):
        _, _, _, log = _full_export()
        assert log["samples"][0]["scores"]["correct"]["value"] == 1.0
        assert log["samples"][0]["scores"]["safe"]["explanation"] == "mostly safe"


class TestForeignLog:
    """Importing a log NOT produced by agenttic: best-effort, documented-lossy."""

    def _foreign(self):
        return {
            "version": 2, "status": "success",
            "eval": {"created": "2026-01-01T00:00:00+00:00", "task": "mmlu",
                     "run_id": "ext-run", "task_version": 1, "model": "gpt-x",
                     "dataset": {"name": "mmlu"}, "config": {}},
            "results": {"total_samples": 2, "completed_samples": 2, "scores": []},
            "samples": [
                {"id": "q1", "epoch": 1, "input": "2+2?", "target": "4",
                 "output": {"completion": "4"},
                 "scores": {"accuracy": {"value": "C"}}},
                {"id": "q2", "epoch": 1, "input": "cap of fr?", "target": "Paris",
                 "output": {"completion": "Lyon"},
                 "scores": {"accuracy": {"value": 0.0}}},
            ],
        }

    def test_foreign_recovers_mappable_subset(self):
        back = from_inspect_log(self._foreign())
        sc = back["scorecard"]
        assert sc.agent_id == "gpt-x" and sc.suite_id == "mmlu"
        assert len(sc.run_scores) == 2
        # "C" -> pass(1.0); numeric 0.0 -> fail; aggregates recomputed
        assert sc.task_success_rate == 0.5
        assert sc.run_scores[0].passed and not sc.run_scores[1].passed
        # best-effort black-box traces carry the final output
        assert {t.final_output for t in back["traces"]} == {"4", "Lyon"}

    def test_score_snapping_to_three_point(self):
        from ascore.interop.inspect_log import _coerce_score
        assert _coerce_score("C") == 1.0
        assert _coerce_score(True) == 1.0
        assert _coerce_score("partial") == 0.5
        assert _coerce_score(0.6) == 0.5
        assert _coerce_score(0.9) == 1.0
        assert _coerce_score("I") == 0.0
        assert _coerce_score(0.0) == 0.0


class TestRealInspectAiSchema:
    """If inspect_ai is installed, the produced log must validate against the
    real pydantic models and survive its own write/read IO."""

    def test_validates_against_inspect_ai(self):
        import pytest
        inspect_log = pytest.importorskip("inspect_ai.log")
        _, _, _, log = _full_export()
        parsed = inspect_log.EvalLog.model_validate(log)
        assert parsed.eval.task == "suite-a"
        assert len(parsed.samples) == 3
        assert parsed.status == "success"

    def test_survives_inspect_write_read(self, tmp_path):
        import pytest
        inspect_log = pytest.importorskip("inspect_ai.log")
        sc, traces, _, log = _full_export()
        parsed = inspect_log.EvalLog.model_validate(log)
        p = str(tmp_path / "eval.json")
        inspect_log.write_eval_log(parsed, p)
        reloaded = inspect_log.read_eval_log(p)
        # round-trip back to agenttic from inspect's own re-serialization
        back = from_inspect_log(reloaded.model_dump(mode="json"))
        assert back["scorecard"].model_dump() == sc.model_dump()


CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, token: testtoken}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
"""


def _client(tmp_path):
    reg = Registry(tmp_path / "a.db")
    reg.save_rubric(RUBRIC)
    sc = _scorecard("sc-1")
    for t in (_trace(i) for i in range(3)):
        reg.save_trace(t)
    reg.save_scorecard(sc)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    return TestClient(create_app(str(cfg), registry=reg))


class TestExportEndpoint:
    AUTH = {"Authorization": "Bearer testtoken"}

    def test_download_inspect_json(self, tmp_path):
        with _client(tmp_path) as c:
            r = c.get("/api/scorecards/sc-1/inspect.json", headers=self.AUTH)
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("application/json")
            assert "attachment" in r.headers.get("content-disposition", "")
            log = r.json()
            assert log["eval"]["run_id"] == "sc-1"
            # endpoint pulled traces from the registry -> full samples
            assert len(log["samples"]) == 3
            assert log["samples"][0]["output"]["completion"] == "answer 0"
            # and it round-trips back losslessly
            back = from_inspect_log(log)
            assert back["scorecard"].scorecard_id == "sc-1"

    def test_requires_auth(self, tmp_path):
        with _client(tmp_path) as c:
            assert c.get("/api/scorecards/sc-1/inspect.json").status_code == 401

    def test_unknown_scorecard_404(self, tmp_path):
        with _client(tmp_path) as c:
            assert c.get("/api/scorecards/nope/inspect.json",
                         headers=self.AUTH).status_code == 404


class TestCli:
    def test_export_and_import_roundtrip(self, tmp_path):
        from typer.testing import CliRunner

        from ascore.cli import app
        reg = Registry(tmp_path / "p.db")
        reg.save_rubric(RUBRIC)
        for t in (_trace(i) for i in range(3)):
            reg.save_trace(t)
        reg.save_scorecard(_scorecard("sc-1"))
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "models: {agent_default: a, judge_strong: j, judge_light: l}\n"
            "harness: {timeout_seconds: 1, max_steps: 1, max_parallel: 1, transport_retries: 0}\n"
            "scoring: {calibration_threshold: 0.8}\n"
            "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
            f"paths: {{registry_db: {tmp_path / 'p.db'}, review_dir: r/, calibration_dir: c/}}\n")
        out = tmp_path / "sc-1.inspect.json"
        run = CliRunner()
        exp = run.invoke(app, ["inspect-export", "sc-1", "--out", str(out),
                               "--config", str(cfg)])
        assert exp.exit_code == 0, exp.output
        assert out.exists() and "Inspect EvalLog" in exp.output

        imp = run.invoke(app, ["inspect-import", str(out), "--config", str(cfg)])
        assert imp.exit_code == 0, imp.output
        assert "sc-1" in imp.output and "agent=agent-x" in imp.output
